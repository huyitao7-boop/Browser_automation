from collections.abc import Awaitable, Callable

from mcp.server.fastmcp import FastMCP

from agent_browser_automation.mcp.schemas import (
    ToolResult,
    WorkflowDraftIdRequest,
    WorkflowDraftRequest,
    WorkflowGenerateRequest,
    WorkflowReviewRequest,
    WorkflowVerifyRequest,
)
from agent_browser_automation.publishing.models import DraftState
from agent_browser_automation.publishing.service import PublishService
from agent_browser_automation.shared.classifications import ErrorClassification
from agent_browser_automation.shared.errors import AutomationException, automation_error
from agent_browser_automation.shared.tracing import ensure_trace_id
from agent_browser_automation.workflow.generator import WorkflowGenerator

from .browser_tools import BrowserSessionService


class PublishingToolService:
    def __init__(
        self,
        publisher: PublishService,
        browser_sessions: BrowserSessionService,
    ) -> None:
        self.publisher = publisher
        self.browser_sessions = browser_sessions
        self.generator = WorkflowGenerator()

    async def create(self, request: WorkflowDraftRequest) -> ToolResult:
        return self._sync(
            request,
            lambda: self.publisher.create(request.workflow, request.creator_id),
        )

    async def generate(self, request: WorkflowGenerateRequest) -> ToolResult:
        trace_id = ensure_trace_id(request.trace_id)
        try:
            origin, events = self.browser_sessions.recording(request.session_id)
            workflow = self.generator.generate(
                workflow_id=request.workflow_id,
                capability=request.capability,
                allowed_origins=(origin,),
                events=events,
                input_schema=request.input_schema,
            )
            state = self.publisher.create(workflow, request.creator_id)
        except (AutomationException, ValueError) as exc:
            error = (
                exc.error
                if isinstance(exc, AutomationException)
                else automation_error(
                    ErrorClassification.WORKFLOW_INVALID, str(exc)
                ).error
            )
            return ToolResult(
                status="needs-review",
                phase="publishing",
                trace_id=trace_id,
                request_id=request.request_id,
                error=error,
            )
        return ToolResult(
            status="passed",
            phase="publishing",
            trace_id=trace_id,
            request_id=request.request_id,
            data={
                "workflow": workflow.model_dump(mode="json", by_alias=True),
                "state": state.model_dump(mode="json"),
            },
        )

    async def validate(self, request: WorkflowDraftIdRequest) -> ToolResult:
        return self._sync(request, lambda: self.publisher.validate(request.workflow_id))

    async def verify(self, request: WorkflowVerifyRequest) -> ToolResult:
        return await self._async(
            request,
            lambda: self.publisher.verify(request.workflow_id, request.inputs),
        )

    async def review(self, request: WorkflowReviewRequest) -> ToolResult:
        return self._sync(
            request,
            lambda: self.publisher.review(
                request.workflow_id,
                request.draft_hash,
                request.reviewer_id,
                request.approved,
                request.summary,
            ),
        )

    async def publish(self, request: WorkflowDraftIdRequest) -> ToolResult:
        trace_id = ensure_trace_id(request.trace_id)
        try:
            state, path, smoke_run_id = await self.publisher.publish(request.workflow_id)
        except AutomationException as exc:
            return self._error(request, trace_id, exc)
        return ToolResult(
            status="passed",
            phase="publishing",
            trace_id=trace_id,
            request_id=request.request_id,
            run_id=smoke_run_id,
            data={
                "workflow_id": request.workflow_id,
                "draft_hash": state.draft_hash,
                "published_path": path,
                "smoke_run_id": smoke_run_id,
            },
        )

    def _sync(
        self,
        request: WorkflowDraftRequest | WorkflowDraftIdRequest | WorkflowReviewRequest,
        operation: Callable[[], DraftState],
    ) -> ToolResult:
        trace_id = ensure_trace_id(request.trace_id)
        try:
            state = operation()
        except AutomationException as exc:
            return self._error(request, trace_id, exc)
        return ToolResult(
            status="passed",
            phase="publishing",
            trace_id=trace_id,
            request_id=request.request_id,
            data={"state": state.model_dump(mode="json", exclude_none=True)},
        )

    async def _async(
        self,
        request: WorkflowVerifyRequest,
        operation: Callable[[], Awaitable[DraftState]],
    ) -> ToolResult:
        trace_id = ensure_trace_id(request.trace_id)
        try:
            state = await operation()
        except AutomationException as exc:
            return self._error(request, trace_id, exc)
        return ToolResult(
            status="passed",
            phase="publishing",
            trace_id=trace_id,
            request_id=request.request_id,
            data={"state": state.model_dump(mode="json", exclude_none=True)},
        )

    @staticmethod
    def _error(
        request: WorkflowDraftRequest | WorkflowDraftIdRequest | WorkflowReviewRequest,
        trace_id: str,
        exc: AutomationException,
    ) -> ToolResult:
        return ToolResult(
            status="needs-review",
            phase="publishing",
            trace_id=trace_id,
            request_id=request.request_id,
            error=exc.error,
        )


def register_publishing_tools(mcp: FastMCP, service: PublishingToolService) -> None:
    @mcp.tool()
    async def workflow_draft_generate(request: WorkflowGenerateRequest) -> ToolResult:
        """Generate a readonly draft from one recorded factual browser trajectory."""
        return await service.generate(request)

    @mcp.tool()
    async def workflow_draft_create(request: WorkflowDraftRequest) -> ToolResult:
        """Create a hash-bound readonly draft; this never publishes it."""
        return await service.create(request)

    @mcp.tool()
    async def workflow_validate(request: WorkflowDraftIdRequest) -> ToolResult:
        """Run deterministic readonly validation for the current draft hash."""
        return await service.validate(request)

    @mcp.tool()
    async def workflow_verify(request: WorkflowVerifyRequest) -> ToolResult:
        """Replay the validated draft twice in clean browser sessions."""
        return await service.verify(request)

    @mcp.tool()
    async def workflow_review_submit(request: WorkflowReviewRequest) -> ToolResult:
        """Submit an independent semantic review bound to the draft hash."""
        return await service.review(request)

    @mcp.tool()
    async def workflow_publish(request: WorkflowDraftIdRequest) -> ToolResult:
        """Publish only after policy gate checks, then smoke-test and rollback on failure."""
        return await service.publish(request)

    _ = workflow_draft_generate, workflow_draft_create, workflow_validate, workflow_verify
    _ = workflow_review_submit, workflow_publish
