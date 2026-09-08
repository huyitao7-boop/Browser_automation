from pydantic import BaseModel, ConfigDict, Field

from agent_browser_automation.evidence.store import EvidenceStore


class DiagnosisCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    cause: str
    confidence: float = Field(ge=0, le=1)
    evidence_refs: tuple[str, ...]


class DiagnosisReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    run_id: str
    candidates: tuple[DiagnosisCandidate, ...]


class DiagnosisEngine:
    def __init__(self, evidence: EvidenceStore) -> None:
        self.evidence = evidence

    def analyze(self, run_id: str) -> DiagnosisReport:
        failure, events = self.evidence.raw_summary_inputs(run_id)
        candidates: list[DiagnosisCandidate] = []
        classification = failure.get("classification")
        mapping = {
            "ASSERTION_FAILED": "page facts did not satisfy the workflow assertion",
            "ORIGIN_NOT_ALLOWED": "navigation left the workflow origin allowlist",
            "TARGET_NOT_FOUND": "the workflow target is absent or its locator is stale",
            "TARGET_AMBIGUOUS": "the target locator is no longer unique",
            "BROWSER_ERROR": "the browser runtime or page execution failed",
        }
        if classification in mapping:
            candidates.append(
                DiagnosisCandidate(
                    cause=mapping[str(classification)],
                    confidence=0.9,
                    evidence_refs=("failure.json",),
                )
            )
        if int(events.get("network_total", 0)) > 0:
            candidates.append(
                DiagnosisCandidate(
                    cause="failed requests or HTTP error responses may have degraded the page",
                    confidence=0.65,
                    evidence_refs=("browser-events.json",),
                )
            )
        if int(events.get("page_error_total", 0)) > 0:
            candidates.append(
                DiagnosisCandidate(
                    cause="uncaught page errors occurred during execution",
                    confidence=0.75,
                    evidence_refs=("browser-events.json",),
                )
            )
        if not candidates:
            candidates.append(
                DiagnosisCandidate(
                    cause="available evidence is insufficient for a specific diagnosis",
                    confidence=0.2,
                    evidence_refs=("manifest.json",),
                )
            )
        return DiagnosisReport(run_id=run_id, candidates=tuple(candidates))
