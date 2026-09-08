from collections.abc import Callable

from mcp.server.fastmcp import FastMCP

from agent_browser_automation.cleanup_learning.generator import CleanupPlanGenerator
from agent_browser_automation.cleanup_learning.models import CleanupDraftState
from agent_browser_automation.cleanup_learning.service import CleanupPublishService
from agent_browser_automation.mcp.schemas import (
    CleanupDraftIdRequest,
    CleanupDraftRequest,
    CleanupGenerateRequest,
    CleanupReviewRequest,
    CleanupVerifyRequest,
    ToolResult,
)
from agent_browser_automation.shared.classifications import ErrorClassification
from agent_browser_automation.shared.errors import AutomationException, automation_error
from agent_browser_automation.shared.tracing import ensure_trace_id

from .browser_tools import BrowserSessionService


class CleanupLearningToolService:
    def __init__(
        self,
        publisher: CleanupPublishService,
        browsers: BrowserSessionService,
    ) -> None:
        self.publisher = publisher
        self.browsers = browsers
        self.generator = CleanupPlanGenerator()

    async def generate(self, request: CleanupGenerateRequest) -> ToolResult:
        trace_id = ensure_trace_id(request.trace_id)
        try:
            environment_id = self.browsers.recording_environment(request.session_id)
            if environment_id != request.environment_id:
                raise automation_error(
                    ErrorClassification.POLICY_DENIED,
                    "cleanup exploration session environment does not match",
                )
            _, events = self.browsers.recording(request.session_id)
            draft = self.generator.generate(
                environment_id=request.environment_id,
                plan_id=request.plan_id,
                capability=request.capability,
                resource_kind=request.resource_kind,
                resource_name=request.resource_name,
                resource_url=request.resource_url,
                identity_target=request.identity_target,
                delete_target=request.delete_target,
                confirmation_target=request.confirmation_target,
                confirmation_submit_target=request.confirmation_submit_target,
                absence_url=request.absence_url,
                absence_target=request.absence_target,
                events=events,
            )
            state = self.publisher.create(
                draft, request.creator_id, trajectory_bound=True
            )
        except (AutomationException, ValueError) as exc:
            return self._error(request, trace_id, exc)
        return ToolResult(
            status="passed",
            phase="publishing",
            trace_id=trace_id,
            request_id=request.request_id,
            data={
                "draft": draft.model_dump(mode="json", exclude_none=True),
                "state": state.model_dump(mode="json", exclude_none=True),
            },
        )

    async def create(self, request: CleanupDraftRequest) -> ToolResult:
        return self._sync(
            request,
            lambda: self.publisher.create(request.draft, request.creator_id),
        )

    async def validate(self, request: CleanupDraftIdRequest) -> ToolResult:
        return self._sync(
            request,
            lambda: self.publisher.validate(request.environment_id, request.plan_id),
        )

    async def verify(self, request: CleanupVerifyRequest) -> ToolResult:
        trace_id = ensure_trace_id(request.trace_id)
        try:
            state = await self.publisher.verify(
                request.environment_id,
                request.plan_id,
                tuple(
                    (item.run_id, item.idempotency_key, item.session_id)
                    for item in request.cycles
                ),
                request.request_id,
                trace_id,
            )
        except AutomationException as exc:
            return self._error(request, trace_id, exc)
        return self._state(request, trace_id, state)

    async def review(self, request: CleanupReviewRequest) -> ToolResult:
        return self._sync(
            request,
            lambda: self.publisher.review(
                request.environment_id,
                request.plan_id,
                request.draft_hash,
                request.reviewer_id,
                request.approved,
                request.summary,
            ),
        )

    async def publish(self, request: CleanupDraftIdRequest) -> ToolResult:
        trace_id = ensure_trace_id(request.trace_id)
        try:
            state, path = self.publisher.publish(
                request.environment_id, request.plan_id
            )
        except AutomationException as exc:
            return self._error(request, trace_id, exc)
        return ToolResult(
            status="passed",
            phase="publishing",
            trace_id=trace_id,
            request_id=request.request_id,
            data={
                "environment_id": request.environment_id,
                "plan_id": request.plan_id,
                "draft_hash": state.draft_hash,
                "published_path": str(path),
            },
        )

    def _sync(
        self,
        request: CleanupDraftRequest | CleanupDraftIdRequest | CleanupReviewRequest,
        operation: Callable[[], CleanupDraftState],
    ) -> ToolResult:
        trace_id = ensure_trace_id(request.trace_id)
        try:
            state = operation()
        except (AutomationException, ValueError) as exc:
            return self._error(request, trace_id, exc)
        return self._state(request, trace_id, state)

    @staticmethod
    def _state(
        request: CleanupDraftRequest | CleanupDraftIdRequest | CleanupReviewRequest,
        trace_id: str,
        state: CleanupDraftState,
    ) -> ToolResult:
        return ToolResult(
            status="passed",
            phase="publishing",
            trace_id=trace_id,
            request_id=request.request_id,
            data={"state": state.model_dump(mode="json", exclude_none=True)},
        )

    @staticmethod
    def _error(
        request: object, trace_id: str, exc: AutomationException | ValueError
    ) -> ToolResult:
        request_id = getattr(request, "request_id", "cleanup-learning")
        error = (
            exc.error
            if isinstance(exc, AutomationException)
            else automation_error(
                ErrorClassification.VALIDATION_ERROR, str(exc)
            ).error
        )
        return ToolResult(
            status="needs-review",
            phase="publishing",
            trace_id=trace_id,
            request_id=request_id,
            error=error,
        )


def register_cleanup_learning_tools(
    mcp: FastMCP, service: CleanupLearningToolService
) -> None:
    @mcp.tool()
    async def cleanup_draft_generate(request: CleanupGenerateRequest) -> ToolResult:
        """Generalize one recorded cleanup exploration into a hash-bound draft."""
        return await service.generate(request)

    @mcp.tool()
    async def cleanup_draft_create(request: CleanupDraftRequest) -> ToolResult:
        """Store a draft for inspection; only live-generated drafts can validate."""
        return await service.create(request)

    @mcp.tool()
    async def cleanup_validate(request: CleanupDraftIdRequest) -> ToolResult:
        """Validate origins, semantic targets, policy binding, and cleanup identity."""
        return await service.validate(request)

    @mcp.tool()
    async def cleanup_verify(request: CleanupVerifyRequest) -> ToolResult:
        """Delete and verify two distinct ledger-bound test resources with the draft."""
        return await service.verify(request)

    @mcp.tool()
    async def cleanup_review_submit(request: CleanupReviewRequest) -> ToolResult:
        """Submit an independent semantic review bound to the cleanup draft hash."""
        return await service.review(request)

    @mcp.tool()
    async def cleanup_publish(request: CleanupDraftIdRequest) -> ToolResult:
        """Atomically add a gated CleanupPlan to its Environment definition."""
        return await service.publish(request)

    _ = cleanup_draft_generate, cleanup_draft_create, cleanup_validate
    _ = cleanup_verify, cleanup_review_submit, cleanup_publish
