import json
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from agent_browser_automation.shared.evidence_manifest import (
    EvidenceArtifact,
    EvidenceManifest,
)
from agent_browser_automation.shared.redaction import DEFAULT_REDACTOR
from agent_browser_automation.shared.tracing import create_run_id


class OperationEvidenceWriter:
    """Persists non-browser Tool results in the same reviewable artifact shape."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def write(
        self,
        result: BaseModel,
        *,
        trace_id: str,
        request_id: str,
        operation: str,
        passed: bool,
    ) -> tuple[str, str, str]:
        run_id = create_run_id()
        directory = self.root / run_id
        directory.mkdir(parents=True, exist_ok=False)
        result_path = directory / "result.json"
        manifest_path = directory / "manifest.json"
        safe = DEFAULT_REDACTOR.redact(
            result.model_dump(mode="json", exclude_none=True)
        )
        result_path.write_text(
            json.dumps(safe, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        now = datetime.now(UTC)
        manifest = EvidenceManifest(
            trace_id=trace_id,
            request_id=request_id,
            run_id=run_id,
            workflow_id=operation,
            status="passed" if passed else "failed",
            started_at=now,
            finished_at=now,
            artifacts=(
                EvidenceArtifact(
                    kind="operation_result",
                    path="result.json",
                    content_type="application/json",
                    redacted=True,
                ),
            ),
        )
        manifest_path.write_text(
            json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return run_id, str(directory), str(manifest_path)
