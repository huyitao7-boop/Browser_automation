import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from .evidence_manifest import EvidenceArtifact, EvidenceManifest, EvidenceStatus
from .redaction import DEFAULT_REDACTOR, Redactor
from .resource_ledger import (
    UNRESOLVED_CLEANUP_STATES,
    ResourceLedgerArtifact,
    ResourceRecord,
)
from .run_context import RunContext
from .structured_logging import StructuredLogWriter


class ArtifactWriter:
    def __init__(
        self,
        root: Path,
        context: RunContext,
        redactor: Redactor = DEFAULT_REDACTOR,
    ) -> None:
        self.context = context
        self.redactor = redactor
        self.directory = root / context.run_id
        self.screenshot_directory = self.directory / "screenshots"
        self.screenshot_directory.mkdir(parents=True, exist_ok=False)
        self.manifest_path = self.directory / "manifest.json"
        self.log_path = self.directory / "run.log.jsonl"
        self.logger = StructuredLogWriter(self.log_path, context, redactor)
        self._steps: list[dict[str, Any]] = []
        self._manifest = EvidenceManifest(
            trace_id=context.trace_id,
            request_id=context.request_id,
            run_id=context.run_id,
            workflow_id=context.workflow_id,
            started_at=datetime.now(UTC),
            artifacts=(
                EvidenceArtifact(
                    kind="structured_log",
                    path=self.log_path.name,
                    content_type="application/x-ndjson",
                    redacted=True,
                ),
            ),
        )
        self._write_manifest()

    def append_step(self, step: BaseModel) -> None:
        self._steps.append(step.model_dump(mode="json", exclude_none=True))
        self._write_json("steps.json", self._steps)
        self._register(
            EvidenceArtifact(
                kind="step_results",
                path="steps.json",
                content_type="application/json",
                redacted=True,
            )
        )

    def write_result(self, result: BaseModel) -> None:
        self._write_json("result.json", result.model_dump(mode="json", exclude_none=True))
        self._register(
            EvidenceArtifact(
                kind="workflow_result",
                path="result.json",
                content_type="application/json",
                redacted=True,
            )
        )

    def write_failure(self, error: BaseModel) -> None:
        self._write_json("failure.json", error.model_dump(mode="json", exclude_none=True))
        self._register(
            EvidenceArtifact(
                kind="failure",
                path="failure.json",
                content_type="application/json",
                redacted=True,
            )
        )

    def write_browser_evidence(self, evidence: BaseModel) -> None:
        raw = evidence.model_dump(mode="json", exclude_none=True)
        self._write_json("browser-events.json", raw)
        truncated = bool(
            raw.get("console_truncated")
            or raw.get("network_truncated")
            or raw.get("page_error_truncated")
            or raw.get("navigation_truncated")
            or raw.get("http_truncated")
            or raw.get("auth_truncated")
        )
        self._register(
            EvidenceArtifact(
                kind="browser_events",
                path="browser-events.json",
                content_type="application/json",
                redacted=True,
                truncated=truncated,
            )
        )

    def write_resource_ledger(self, records: tuple[ResourceRecord, ...]) -> None:
        artifact = ResourceLedgerArtifact(
            run_id=self.context.run_id,
            records=records,
            pending_cleanup_count=sum(
                item.state in UNRESOLVED_CLEANUP_STATES for item in records
            ),
        )
        self._write_json("resource-ledger.json", artifact.model_dump(mode="json"))
        self._register(
            EvidenceArtifact(
                kind="resource_ledger",
                path="resource-ledger.json",
                content_type="application/json",
                redacted=True,
            )
        )

    def register_screenshot(self, path: Path, step_id: str | None) -> None:
        self._register(
            EvidenceArtifact(
                kind="screenshot",
                path=path.relative_to(self.directory).as_posix(),
                content_type="image/png",
                redacted=True,
                step_id=step_id,
            )
        )

    def finalize(self, status: EvidenceStatus) -> None:
        self._manifest = self._manifest.model_copy(
            update={"status": status, "finished_at": datetime.now(UTC)}
        )
        self._write_manifest()

    def _register(self, artifact: EvidenceArtifact) -> None:
        existing = {item.path: item for item in self._manifest.artifacts}
        existing[artifact.path] = artifact
        self._manifest = self._manifest.model_copy(
            update={"artifacts": tuple(existing.values())}
        )
        self._write_manifest()

    def _write_manifest(self) -> None:
        self._write_json(
            self.manifest_path.name,
            self._manifest.model_dump(mode="json", exclude_none=True),
        )

    def _write_json(self, name: str, value: Any) -> None:
        destination = self.directory / name
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        safe_value = self.redactor.redact(value)
        temporary.write_text(
            json.dumps(safe_value, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        temporary.replace(destination)
