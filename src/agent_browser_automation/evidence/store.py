import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from agent_browser_automation.shared.classifications import ErrorClassification
from agent_browser_automation.shared.errors import automation_error
from agent_browser_automation.shared.evidence_manifest import EvidenceManifest
from agent_browser_automation.shared.resource_ledger import (
    UNRESOLVED_CLEANUP_STATES,
    ResourceLedgerArtifact,
    ResourceRecord,
    ResourceState,
)


class EvidenceStore:
    def __init__(self, artifact_root: Path) -> None:
        self.root = Path(artifact_root).resolve()

    def manifest(self, run_id: str) -> EvidenceManifest:
        return EvidenceManifest.model_validate(self._json(run_id, "manifest.json"))

    def failure_summary(self, run_id: str) -> dict[str, Any]:
        result = self._json(run_id, "result.json")
        failure = self._optional_json(run_id, "failure.json")
        events = self._optional_json(run_id, "browser-events.json")
        return {
            "run_id": run_id,
            "status": result.get("status"),
            "workflow_id": result.get("workflow_id"),
            "failed_step": result.get("failed_step"),
            "error": failure,
            "browser_event_counts": {
                key: events.get(key, 0)
                for key in (
                    "console_total",
                    "network_total",
                    "page_error_total",
                    "navigation_total",
                )
            },
            "evidence_refs": [
                "result.json",
                *( ["failure.json"] if failure else [] ),
                *( ["browser-events.json"] if events else [] ),
            ],
        }

    def resource_ledger(self, run_id: str) -> ResourceLedgerArtifact:
        value = self._json(run_id, "resource-ledger.json")
        try:
            artifact = ResourceLedgerArtifact.model_validate(value)
        except ValueError as exc:
            raise automation_error(
                ErrorClassification.WORKFLOW_INVALID,
                "resource ledger artifact is invalid",
                run_id=run_id,
            ) from exc
        if artifact.run_id != run_id:
            raise automation_error(
                ErrorClassification.WORKFLOW_INVALID,
                "resource ledger run id does not match its artifact directory",
                run_id=run_id,
            )
        return artifact

    def pending_resources(
        self, limit: int
    ) -> tuple[tuple[str, ResourceRecord], ...]:
        pending: list[tuple[str, ResourceRecord]] = []
        if not self.root.is_dir():
            return ()
        for directory in sorted(self.root.iterdir(), key=lambda item: item.name):
            if not directory.is_dir():
                continue
            ledger_path = directory / "resource-ledger.json"
            if not ledger_path.is_file():
                continue
            artifact = self.resource_ledger(directory.name)
            for record in artifact.records:
                if record.state in UNRESOLVED_CLEANUP_STATES:
                    pending.append((artifact.run_id, record))
                    if len(pending) >= limit:
                        return tuple(pending)
        return tuple(pending)

    def transition_resource(
        self,
        run_id: str,
        idempotency_key: str,
        state: ResourceState,
        *,
        cleanup_attempts: int | None = None,
        cleanup_run_id: str | None = None,
        cleanup_error: str | None = None,
    ) -> ResourceRecord:
        artifact = self.resource_ledger(run_id)
        matched: ResourceRecord | None = None
        records: list[ResourceRecord] = []
        for record in artifact.records:
            if record.idempotency_key != idempotency_key:
                records.append(record)
                continue
            matched = record.model_copy(
                update={
                    "state": state,
                    "updated_at": datetime.now(UTC),
                    "cleanup_attempts": (
                        cleanup_attempts
                        if cleanup_attempts is not None
                        else record.cleanup_attempts
                    ),
                    "last_cleanup_run_id": cleanup_run_id,
                    "last_cleanup_error": cleanup_error,
                }
            )
            records.append(matched)
        if matched is None:
            raise automation_error(
                ErrorClassification.VALIDATION_ERROR,
                "resource ledger record was not found",
                run_id=run_id,
                idempotency_key=idempotency_key,
            )
        allowed_transitions = {
            ResourceState.VERIFIED: {ResourceState.CLEANUP_RUNNING},
            ResourceState.NEEDS_CLEANUP: {ResourceState.CLEANUP_RUNNING},
            ResourceState.CLEANUP_FAILED: {ResourceState.CLEANUP_RUNNING},
            ResourceState.CLEANUP_RUNNING: {
                ResourceState.CLEANED,
                ResourceState.CLEANUP_NOT_NEEDED,
                ResourceState.CLEANUP_FAILED,
            },
        }
        original = next(
            item for item in artifact.records if item.idempotency_key == idempotency_key
        )
        if state not in allowed_transitions.get(original.state, set()):
            raise automation_error(
                ErrorClassification.POLICY_DENIED,
                "resource ledger cleanup state transition is not allowed",
                state_before=original.state.value,
                state_after=state.value,
            )
        updated = ResourceLedgerArtifact(
            run_id=run_id,
            records=tuple(records),
            pending_cleanup_count=sum(
                item.state in UNRESOLVED_CLEANUP_STATES for item in records
            ),
        )
        path = self._run_directory(run_id) / "resource-ledger.json"
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(updated.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(path)
        return matched

    def raw_summary_inputs(self, run_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        failure = self._optional_json(run_id, "failure.json")
        events = self._optional_json(run_id, "browser-events.json")
        return failure, events

    def _optional_json(self, run_id: str, name: str) -> dict[str, Any]:
        path = self._run_directory(run_id) / name
        return self._read_mapping(path) if path.is_file() else {}

    def _json(self, run_id: str, name: str) -> dict[str, Any]:
        path = self._run_directory(run_id) / name
        if not path.is_file():
            raise automation_error(
                ErrorClassification.VALIDATION_ERROR,
                "requested evidence artifact was not found",
                run_id=run_id,
                artifact=name,
            )
        return self._read_mapping(path)

    def _run_directory(self, run_id: str) -> Path:
        candidate = (self.root / run_id).resolve()
        if candidate.parent != self.root:
            raise automation_error(
                ErrorClassification.VALIDATION_ERROR,
                "run id escapes the artifact root",
            )
        return candidate

    @staticmethod
    def _read_mapping(path: Path) -> dict[str, Any]:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise automation_error(
                ErrorClassification.WORKFLOW_INVALID,
                "evidence artifact must contain a JSON object",
                path=str(path),
            )
        return cast(dict[str, Any], value)
