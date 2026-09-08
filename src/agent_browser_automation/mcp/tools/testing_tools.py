from mcp.server.fastmcp import FastMCP

from agent_browser_automation.mcp.schemas import (
    TestCaseRequest,
    ToolResult,
    WorkflowRunRequest,
)
from agent_browser_automation.shared.errors import AutomationException
from agent_browser_automation.shared.tracing import ensure_trace_id
from agent_browser_automation.testing.compiler import TestCaseCompiler

from .workflow_tools import WorkflowService


class TestCaseService:
    def __init__(
        self,
        compiler: TestCaseCompiler,
        workflow_service: WorkflowService,
    ) -> None:
        self.compiler = compiler
        self.workflow_service = workflow_service

    async def validate(self, request: TestCaseRequest) -> ToolResult:
        return self._compile_result(request, include_plan=False)

    async def compile(self, request: TestCaseRequest) -> ToolResult:
        return self._compile_result(request, include_plan=True)

    async def run(self, request: TestCaseRequest) -> ToolResult:
        trace_id = ensure_trace_id(request.trace_id)
        try:
            plan = self.compiler.compile(request.testcase)
        except AutomationException as exc:
            return ToolResult(
                status="failed",
                phase="compilation",
                trace_id=trace_id,
                request_id=request.request_id,
                error=exc.error,
            )
        return await self.workflow_service.run(
            WorkflowRunRequest(
                workflow_id=plan.workflow_id,
                inputs=plan.inputs,
                request_id=request.request_id,
                trace_id=trace_id,
            )
        )

    def _compile_result(
        self, request: TestCaseRequest, *, include_plan: bool
    ) -> ToolResult:
        trace_id = ensure_trace_id(request.trace_id)
        try:
            plan = self.compiler.compile(request.testcase)
        except AutomationException as exc:
            return ToolResult(
                status="failed",
                phase="validation",
                trace_id=trace_id,
                request_id=request.request_id,
                error=exc.error,
            )
        data: dict[str, object] = {"valid": True, "testcase_id": plan.testcase_id}
        if include_plan:
            data["plan"] = plan.model_dump(mode="json")
        return ToolResult(
            status="passed",
            phase="compilation" if include_plan else "validation",
            trace_id=trace_id,
            request_id=request.request_id,
            data=data,
        )


def register_testing_tools(mcp: FastMCP, service: TestCaseService) -> None:
    @mcp.tool()
    async def testcase_validate(request: TestCaseRequest) -> ToolResult:
        """Validate a structured readonly test case and its declarations."""
        return await service.validate(request)

    @mcp.tool()
    async def testcase_compile(request: TestCaseRequest) -> ToolResult:
        """Compile a test case through the unique WorkflowMatcher."""
        return await service.compile(request)

    @mcp.tool()
    async def test_run(request: TestCaseRequest) -> ToolResult:
        """Ensure an optional environment and execute a readonly test plan."""
        return await service.run(request)

    _ = testcase_validate, testcase_compile, test_run
