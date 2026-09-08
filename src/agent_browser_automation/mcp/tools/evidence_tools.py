from collections.abc import Callable
from typing import Any, cast

from mcp.server.fastmcp import FastMCP

from agent_browser_automation.diagnosis.engine import DiagnosisEngine
from agent_browser_automation.evidence.store import EvidenceStore
from agent_browser_automation.mcp.schemas import RunEvidenceRequest, ToolResult
from agent_browser_automation.shared.errors import AutomationException
from agent_browser_automation.shared.redaction import DEFAULT_REDACTOR
from agent_browser_automation.shared.tracing import ensure_trace_id


class EvidenceToolService:
    def __init__(self, evidence: EvidenceStore, diagnosis: DiagnosisEngine) -> None:
        self.evidence = evidence
        self.diagnosis = diagnosis

    def manifest(self, request: RunEvidenceRequest) -> ToolResult:
        return self._run(
            request,
            "inspection",
            lambda: {"manifest": self.evidence.manifest(request.run_id).model_dump(mode="json")},
        )

    def failure(self, request: RunEvidenceRequest) -> ToolResult:
        return self._run(
            request,
            "inspection",
            lambda: {"summary": self.evidence.failure_summary(request.run_id)},
        )

    def diagnose(self, request: RunEvidenceRequest) -> ToolResult:
        return self._run(
            request,
            "diagnosis",
            lambda: {
                "diagnosis": self.diagnosis.analyze(request.run_id).model_dump(mode="json")
            },
        )

    @staticmethod
    def _run(
        request: RunEvidenceRequest,
        phase: str,
        operation: Callable[[], dict[str, object]],
    ) -> ToolResult:
        trace_id = ensure_trace_id(request.trace_id)
        try:
            data = DEFAULT_REDACTOR.redact(operation())
        except AutomationException as exc:
            return ToolResult(
                status="failed",
                phase="diagnosis" if phase == "diagnosis" else "inspection",
                trace_id=trace_id,
                request_id=request.request_id,
                run_id=request.run_id,
                error=exc.error,
            )
        assert isinstance(data, dict)
        typed_data = cast(dict[str, Any], data)
        return ToolResult(
            status="passed",
            phase="diagnosis" if phase == "diagnosis" else "inspection",
            trace_id=trace_id,
            request_id=request.request_id,
            run_id=request.run_id,
            data=typed_data,
        )


def register_evidence_tools(mcp: FastMCP, service: EvidenceToolService) -> None:
    @mcp.tool()
    async def evidence_get_manifest(request: RunEvidenceRequest) -> ToolResult:
        """Read the redacted Evidence Manifest for one run."""
        return service.manifest(request)

    @mcp.tool()
    async def evidence_failure_summary(request: RunEvidenceRequest) -> ToolResult:
        """Read a bounded, redacted failure problem summary for one run."""
        return service.failure(request)

    @mcp.tool()
    async def diagnosis_analyze(request: RunEvidenceRequest) -> ToolResult:
        """Return evidence-cited deterministic cause candidates and confidence."""
        return service.diagnose(request)

    _ = evidence_get_manifest, evidence_failure_summary, diagnosis_analyze


def register_task_evidence_tools(mcp: FastMCP, service: EvidenceToolService) -> None:
    """Register the single bounded failure report needed by a task agent."""

    @mcp.tool()
    async def evidence_failure_summary(request: RunEvidenceRequest) -> ToolResult:
        """Read a bounded, redacted failure summary for one run."""
        return service.failure(request)

    _ = evidence_failure_summary
