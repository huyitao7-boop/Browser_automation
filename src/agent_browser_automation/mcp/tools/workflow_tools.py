import asyncio
from collections.abc import Callable
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from agent_browser_automation.browser.browser_api import BrowserAPI
from agent_browser_automation.shared.classifications import ErrorClassification
from agent_browser_automation.shared.errors import (
    AutomationException,
    ErrorDetail,
    automation_error,
)
from agent_browser_automation.shared.redaction import DEFAULT_REDACTOR, Redactor
from agent_browser_automation.shared.run_context import RiskLevel, RunContext
from agent_browser_automation.shared.tracing import create_run_id, ensure_trace_id
from agent_browser_automation.workflow.matcher import (
    AmbiguousWorkflowError,
    WorkflowMatcher,
)
from agent_browser_automation.workflow.runner import WorkflowRunner

from ..schemas import NextAction, ToolResult, WorkflowRunRequest, WorkflowSearchRequest


class WorkflowService:
    def __init__(
        self,
        matcher: WorkflowMatcher,
        browser_factory: Callable[[], BrowserAPI],
        artifact_root: Path,
        action_timeout_ms: int,
        navigation_timeout_ms: int,
        max_concurrent_runs: int = 4,
        redactor: Redactor = DEFAULT_REDACTOR,
    ) -> None:
        self.matcher = matcher
        self.browser_factory = browser_factory
        self.artifact_root = Path(artifact_root)
        self.action_timeout_ms = action_timeout_ms
        self.navigation_timeout_ms = navigation_timeout_ms
        self._run_slots = asyncio.Semaphore(max_concurrent_runs)
        self.redactor = redactor

    async def run(self, request: WorkflowRunRequest) -> ToolResult:
        trace_id = ensure_trace_id(request.trace_id)
        try:
            workflow = self.matcher.match(
                workflow_id=request.workflow_id,
                capability=request.capability,
                inputs=request.inputs,
            )
        except AmbiguousWorkflowError as exc:
            return ToolResult(
                status="needs-input",
                phase="matching",
                trace_id=trace_id,
                request_id=request.request_id,
                error=self._redact_error(
                    automation_error(
                        ErrorClassification.WORKFLOW_AMBIGUOUS,
                        "multiple published workflows matched the request",
                        candidate_count=len(exc.candidates),
                    ).error
                ),
                next_action=NextAction(
                    type="select-workflow",
                    message="Specify workflow_id to choose one published workflow.",
                    choices=[
                        {
                            "workflow_id": item.id,
                            "version": item.version,
                            "capability": item.capability,
                        }
                        for item in exc.candidates
                    ],
                ),
            )
        except AutomationException as exc:
            phase = (
                "validation"
                if exc.error.classification == ErrorClassification.VALIDATION_ERROR
                else "matching"
            )
            return ToolResult(
                status="failed",
                phase=phase,
                trace_id=trace_id,
                request_id=request.request_id,
                error=self._redact_error(exc.error),
            )

        run_id = create_run_id()
        context = RunContext(
            trace_id=trace_id,
            run_id=run_id,
            request_id=request.request_id,
            workflow_id=workflow.id,
            allowed_origins=workflow.allowed_origins,
            max_risk=RiskLevel.READONLY,
            action_timeout_ms=self.action_timeout_ms,
            navigation_timeout_ms=self.navigation_timeout_ms,
        )
        browser = self.browser_factory()
        run_redactor = browser.redactor
        runner = WorkflowRunner(browser, self.artifact_root, run_redactor)
        async with self._run_slots:
            result = await runner.run(workflow, request.inputs, context)
        phase = (
            "verification"
            if result.status == "passed"
            else "cleanup"
            if result.status == "needs-review"
            else "execution"
        )
        return ToolResult(
            status=result.status,
            phase=phase,
            trace_id=trace_id,
            request_id=request.request_id,
            run_id=run_id,
            data=run_redactor.redact(result.model_dump(mode="json", exclude={"error"})),
            error=(
                self._redact_error(result.error, run_redactor)
                if result.error is not None
                else None
            ),
            artifact_directory=result.artifact_directory,
            evidence_manifest=result.evidence_manifest,
        )

    async def search(self, request: WorkflowSearchRequest) -> ToolResult:
        trace_id = ensure_trace_id(request.trace_id)
        try:
            workflows = self.matcher.store.search(
                capability=request.capability, origin=request.origin
            )
        except (AutomationException, ValueError) as exc:
            error = (
                exc.error
                if isinstance(exc, AutomationException)
                else automation_error(
                    ErrorClassification.VALIDATION_ERROR, str(exc)
                ).error
            )
            return ToolResult(
                status="failed",
                phase="validation",
                trace_id=trace_id,
                request_id=request.request_id,
                error=self._redact_error(error),
            )
        return ToolResult(
            status="passed",
            phase="matching",
            trace_id=trace_id,
            request_id=request.request_id,
            data={
                "workflows": [
                    {
                        "workflow_id": item.id,
                        "version": item.version,
                        "capability": item.capability,
                        "risk": item.risk.value,
                        "input_schema": item.input_schema,
                        "allowed_origins": list(item.allowed_origins),
                        "step_ids": [step.id for step in item.steps],
                    }
                    for item in workflows
                ]
            },
        )

    def _redact_error(
        self, error: ErrorDetail, redactor: Redactor | None = None
    ) -> ErrorDetail:
        selected = redactor or self.redactor
        return ErrorDetail.model_validate(
            selected.redact(error.model_dump(mode="json"))
        )


def register_workflow_tools(mcp: FastMCP, service: WorkflowService) -> None:
    @mcp.tool()
    async def workflow_search(request: WorkflowSearchRequest) -> ToolResult:
        """Search published readonly workflows by capability or origin."""
        return await service.search(request)

    @mcp.tool()
    async def workflow_run(request: WorkflowRunRequest) -> ToolResult:
        """Run one uniquely matched, published, readonly web workflow."""
        return await service.run(request)

    @mcp.tool()
    async def workflow_continue(request: WorkflowRunRequest) -> ToolResult:
        """Continue a prior needs-input decision using an explicit workflow selector."""
        return await service.run(request)

    _ = workflow_search, workflow_run, workflow_continue


def register_task_workflow_tools(mcp: FastMCP, service: WorkflowService) -> None:
    """Register only the business-level workflow entrypoints."""

    @mcp.tool()
    async def workflow_run(request: WorkflowRunRequest) -> ToolResult:
        """Match and run one published business workflow."""
        return await service.run(request)

    @mcp.tool()
    async def workflow_continue(request: WorkflowRunRequest) -> ToolResult:
        """Continue a prior matching decision with an explicit workflow selector."""
        return await service.run(request)

    _ = workflow_run, workflow_continue
