import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from uuid import uuid4

from mcp.server.fastmcp import FastMCP

from agent_browser_automation.browser.browser_api import BrowserAPI
from agent_browser_automation.mcp.schemas import (
    BrowserAssertRequest,
    BrowserClickRequest,
    BrowserFillTextRequest,
    BrowserNavigateRequest,
    BrowserScrollRequest,
    BrowserSelectRequest,
    BrowserSessionRequest,
    BrowserSessionStartRequest,
    BrowserSubmitRequest,
    ToolResult,
)
from agent_browser_automation.shared.classifications import ErrorClassification
from agent_browser_automation.shared.errors import AutomationException, automation_error
from agent_browser_automation.shared.run_context import RiskLevel, RunContext, normalized_origin
from agent_browser_automation.shared.tracing import create_run_id, ensure_trace_id
from agent_browser_automation.workflow.action import (
    AssertAction,
    ClickAction,
    FillTextAction,
    NavigateAction,
    ObserveAction,
    ScrollAction,
    SelectAction,
    SimpleSubmitAction,
    WebAction,
)
from agent_browser_automation.workflow.explorer import ReadonlyExplorer
from agent_browser_automation.workflow.result import ActionResult, PageObservation
from agent_browser_automation.workflow.trajectory_recorder import TrajectoryEvent, TrajectoryRecorder


@dataclass
class ManagedBrowserSession:
    browser: BrowserAPI
    origin: str
    trusted_origins: tuple[str, ...]
    run_id: str
    recorder: TrajectoryRecorder
    revision: int = 0
    latest_observation: PageObservation | None = None
    observation_ready: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class BrowserSessionService:
    """Own same-origin browser sessions without process, policy, or credential bindings."""

    def __init__(
        self,
        browser_factory: Callable[[], BrowserAPI],
        action_timeout_ms: int,
        navigation_timeout_ms: int,
        max_sessions: int = 4,
        max_exploration_steps: int = 20,
        max_exploration_failures: int = 3,
        artifact_root: Path | None = None,
    ) -> None:
        self.browser_factory = browser_factory
        self.action_timeout_ms = action_timeout_ms
        self.navigation_timeout_ms = navigation_timeout_ms
        self.max_sessions = max_sessions
        self._limits = max_exploration_steps, max_exploration_failures, artifact_root
        self.explorer = ReadonlyExplorer()
        self._sessions: dict[str, ManagedBrowserSession] = {}

    async def start(self, request: BrowserSessionStartRequest) -> ToolResult:
        trace_id = ensure_trace_id(request.trace_id)
        if len(self._sessions) >= self.max_sessions:
            return self._failure(request, trace_id, ErrorClassification.BROWSER_ERROR, "browser session quota is exhausted")
        try:
            session_id = uuid4().hex
            origin = normalized_origin(request.url)
            trusted_origins = tuple(
                dict.fromkeys(
                    (origin,)
                    + tuple(
                        normalized_origin(value)
                        for value in request.trusted_redirect_origins
                    )
                )
            )
            managed = ManagedBrowserSession(
                browser=self.browser_factory(),
                origin=origin,
                trusted_origins=trusted_origins,
                run_id=create_run_id(),
                recorder=TrajectoryRecorder(),
            )
            await managed.browser.start()
            self._sessions[session_id] = managed
            result = await self._execute(request, managed, NavigateAction(url=request.url, expected_revision=0))
            if result.status == "failed":
                await self._discard(session_id)
            return self._result(request, managed, result, "inspection", session_id=session_id)
        except (AutomationException, ValueError) as exc:
            error = exc.error if isinstance(exc, AutomationException) else automation_error(ErrorClassification.VALIDATION_ERROR, str(exc)).error
            return ToolResult(status="failed", phase="safety", trace_id=trace_id, request_id=request.request_id, error=error)

    async def navigate(self, request: BrowserNavigateRequest) -> ToolResult:
        return await self._run(request, NavigateAction(url=request.url, expected_revision=request.expected_revision))

    async def observe(self, request: BrowserSessionRequest) -> ToolResult:
        managed = self._sessions.get(request.session_id)
        if managed is None:
            return self._not_found(request)
        result = await self._execute(request, managed, ObserveAction(expected_revision=managed.revision))
        managed.observation_ready = result.status == "passed"
        return self._result(request, managed, result, "inspection", session_id=request.session_id)

    async def scroll(self, request: BrowserScrollRequest) -> ToolResult:
        return await self._run(request, ScrollAction(direction=request.direction, expected_revision=request.expected_revision), require_observation=True)

    async def click(self, request: BrowserClickRequest) -> ToolResult:
        return await self._run(request, ClickAction(target=request.target, risk=RiskLevel.READONLY, expected_revision=request.expected_revision), require_observation=True)

    async def fill_text(self, request: BrowserFillTextRequest) -> ToolResult:
        self._register_sensitive_value(request.session_id, request.value)
        return await self._run(
            request,
            FillTextAction(target=request.target, value=request.value, risk=RiskLevel.READONLY, expected_revision=request.expected_revision),
            require_observation=True,
        )

    async def select(self, request: BrowserSelectRequest) -> ToolResult:
        return await self._run(
            request,
            SelectAction(target=request.target, value=request.value, risk=RiskLevel.READONLY, expected_revision=request.expected_revision),
            require_observation=True,
        )

    async def submit(self, request: BrowserSubmitRequest) -> ToolResult:
        return await self._run(
            request,
            SimpleSubmitAction(target=request.target, expected_revision=request.expected_revision),
            require_observation=True,
        )

    async def assert_page(self, request: BrowserAssertRequest) -> ToolResult:
        managed = self._sessions.get(request.session_id)
        if managed is None:
            return self._not_found(request)
        expected_revision = managed.revision if request.expected_revision is None else request.expected_revision
        return await self._run(request, AssertAction(assertion=request.assertion, expected_revision=expected_revision))

    async def trajectory(self, request: BrowserSessionRequest) -> ToolResult:
        managed = self._sessions.get(request.session_id)
        if managed is None:
            return self._not_found(request)
        return ToolResult(status="passed", phase="inspection", trace_id=ensure_trace_id(request.trace_id), request_id=request.request_id, run_id=managed.run_id, data={"events": [event.model_dump(mode="json") for event in managed.recorder.snapshot()]})

    async def suggest(self, request: BrowserSessionRequest) -> ToolResult:
        managed = self._sessions.get(request.session_id)
        if managed is None:
            return self._not_found(request)
        if managed.latest_observation is None:
            return self._failure(request, ensure_trace_id(request.trace_id), ErrorClassification.POLICY_DENIED, "browser_suggest requires a current observation")
        suggestions = self.explorer.suggest(managed.latest_observation)
        return ToolResult(status="passed", phase="inspection", trace_id=ensure_trace_id(request.trace_id), request_id=request.request_id, run_id=managed.run_id, data={"suggestions": [item.model_dump(mode="json") for item in suggestions]})

    async def close(self, request: BrowserSessionRequest) -> ToolResult:
        await self._discard(request.session_id)
        return ToolResult(status="passed", phase="execution", trace_id=ensure_trace_id(request.trace_id), request_id=request.request_id)

    async def close_all(self) -> None:
        for session_id in tuple(self._sessions):
            await self._discard(session_id)

    def recording(self, session_id: str) -> tuple[str, tuple[TrajectoryEvent, ...]]:
        managed = self._sessions.get(session_id)
        if managed is None:
            raise automation_error(ErrorClassification.VALIDATION_ERROR, "browser session was not found", session_id=session_id)
        return managed.origin, managed.recorder.snapshot()

    async def _run(self, request: BrowserSessionRequest, action: WebAction, *, require_observation: bool = False) -> ToolResult:
        managed = self._sessions.get(request.session_id)
        if managed is None:
            return self._not_found(request)
        async with managed.lock:
            if require_observation and not self._has_current_observation(managed, getattr(action, "expected_revision")):
                return self._failure(request, ensure_trace_id(request.trace_id), ErrorClassification.STALE_REFERENCE, "each action requires a fresh observation at the supplied revision")
            managed.observation_ready = False
            result = await self._execute(request, managed, action)
            return self._result(request, managed, result, "execution", session_id=request.session_id)

    @staticmethod
    def _has_current_observation(managed: ManagedBrowserSession, expected_revision: int | None) -> bool:
        return bool(managed.observation_ready and managed.latest_observation and expected_revision == managed.latest_observation.revision)

    async def _execute(self, request: BrowserSessionRequest | BrowserSessionStartRequest, managed: ManagedBrowserSession, action: WebAction) -> ActionResult:
        result = await managed.browser.execute(action, self._context(request, managed))
        managed.recorder.append(action, result)
        managed.revision = result.revision_after
        if result.observation is not None:
            managed.latest_observation = result.observation
        return result

    def _context(self, request: BrowserSessionRequest | BrowserSessionStartRequest, managed: ManagedBrowserSession) -> RunContext:
        return RunContext(trace_id=ensure_trace_id(request.trace_id), request_id=request.request_id, run_id=managed.run_id, workflow_id="browser.exploration", allowed_origins=managed.trusted_origins, max_risk=RiskLevel.READONLY, allow_session_interactions=True, action_timeout_ms=self.action_timeout_ms, navigation_timeout_ms=self.navigation_timeout_ms)

    def _register_sensitive_value(self, session_id: str, value: str) -> None:
        managed = self._sessions.get(session_id)
        if managed is not None and len(value) >= 4:
            managed.browser.redactor.register_sensitive_value(value)

    def _result(self, request: BrowserSessionRequest | BrowserSessionStartRequest, managed: ManagedBrowserSession, result: ActionResult, phase: Literal["inspection", "execution"], **extra: object) -> ToolResult:
        data: dict[str, object] = {"action_result": managed.browser.redactor.redact(result.model_dump(mode="json", exclude={"error"})), "observation": managed.browser.redactor.redact(result.observation.model_dump(mode="json")) if result.observation else None, **extra}
        return ToolResult(status=result.status, phase=phase, trace_id=ensure_trace_id(request.trace_id), request_id=request.request_id, run_id=managed.run_id, data=data, error=result.error)

    def _not_found(self, request: BrowserSessionRequest) -> ToolResult:
        return self._failure(request, ensure_trace_id(request.trace_id), ErrorClassification.VALIDATION_ERROR, "browser session was not found")

    @staticmethod
    def _failure(request: BrowserSessionRequest | BrowserSessionStartRequest, trace_id: str, classification: ErrorClassification, message: str) -> ToolResult:
        return ToolResult(status="failed", phase="safety", trace_id=trace_id, request_id=request.request_id, error=automation_error(classification, message).error)

    async def _discard(self, session_id: str) -> None:
        managed = self._sessions.pop(session_id, None)
        if managed is not None:
            await managed.browser.close()
            managed.browser.redactor.clear_sensitive_values()


def _register(mcp: FastMCP, service: BrowserSessionService, include_learning: bool) -> None:
    @mcp.tool()
    async def browser_session_start(request: BrowserSessionStartRequest) -> ToolResult:
        return await service.start(request)
    @mcp.tool()
    async def browser_navigate(request: BrowserNavigateRequest) -> ToolResult:
        return await service.navigate(request)
    @mcp.tool()
    async def browser_observe(request: BrowserSessionRequest) -> ToolResult:
        return await service.observe(request)
    @mcp.tool()
    async def browser_scroll(request: BrowserScrollRequest) -> ToolResult:
        return await service.scroll(request)
    @mcp.tool()
    async def browser_click(request: BrowserClickRequest) -> ToolResult:
        return await service.click(request)
    @mcp.tool()
    async def browser_fill_text(request: BrowserFillTextRequest) -> ToolResult:
        return await service.fill_text(request)
    @mcp.tool()
    async def browser_select(request: BrowserSelectRequest) -> ToolResult:
        return await service.select(request)
    @mcp.tool()
    async def browser_submit(request: BrowserSubmitRequest) -> ToolResult:
        return await service.submit(request)
    @mcp.tool()
    async def browser_assert(request: BrowserAssertRequest) -> ToolResult:
        return await service.assert_page(request)
    @mcp.tool()
    async def browser_session_close(request: BrowserSessionRequest) -> ToolResult:
        return await service.close(request)
    if include_learning:
        @mcp.tool()
        async def browser_trajectory(request: BrowserSessionRequest) -> ToolResult:
            return await service.trajectory(request)
        @mcp.tool()
        async def browser_suggest(request: BrowserSessionRequest) -> ToolResult:
            return await service.suggest(request)
        _ = browser_trajectory, browser_suggest

    _ = (
        browser_session_start,
        browser_navigate,
        browser_observe,
        browser_scroll,
        browser_click,
        browser_fill_text,
        browser_select,
        browser_submit,
        browser_assert,
        browser_session_close,
    )


def register_browser_tools(mcp: FastMCP, service: BrowserSessionService) -> None:
    _register(mcp, service, True)


def register_exploration_tools(mcp: FastMCP, service: BrowserSessionService) -> None:
    _register(mcp, service, True)


def register_minimal_browser_tools(mcp: FastMCP, service: BrowserSessionService) -> None:
    _register(mcp, service, False)


def register_demo_browser_tools(mcp: FastMCP, service: BrowserSessionService) -> None:
    _register(mcp, service, False)


def register_simple_browser_tools(mcp: FastMCP, service: BrowserSessionService) -> None:
    _register(mcp, service, False)
