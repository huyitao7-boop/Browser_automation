import hashlib
import json
import re
from pathlib import Path

from agent_browser_automation.shared.classifications import ErrorClassification
from agent_browser_automation.shared.errors import automation_error
from agent_browser_automation.workflow.types import WorkflowDefinition

from .models import DraftState


def workflow_hash(workflow: WorkflowDefinition) -> str:
    encoded = json.dumps(
        workflow.model_dump(mode="json", by_alias=True, exclude_none=True),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


class DraftStore:
    """Stores drafts and hash-bound reports; it has no publication operation."""

    def __init__(self, workflow_root: Path) -> None:
        self.root = Path(workflow_root)
        self.drafts = self.root / "drafts"
        self.drafts.mkdir(parents=True, exist_ok=True)

    def save(self, workflow: WorkflowDefinition, creator_id: str) -> DraftState:
        self._write_json(
            self._draft_path(workflow.id),
            workflow.model_dump(mode="json", by_alias=True, exclude_none=True),
        )
        state = DraftState(draft_hash=workflow_hash(workflow), creator_id=creator_id)
        self.save_state(workflow.id, state)
        return state

    def load(self, workflow_id: str) -> WorkflowDefinition:
        path = self._draft_path(workflow_id)
        if not path.is_file():
            raise automation_error(
                ErrorClassification.WORKFLOW_NOT_FOUND,
                "workflow draft was not found",
                workflow_id=workflow_id,
            )
        return WorkflowDefinition.model_validate(json.loads(path.read_text(encoding="utf-8")))

    def load_state(self, workflow_id: str) -> DraftState:
        path = self._state_path(workflow_id)
        if not path.is_file():
            raise automation_error(
                ErrorClassification.WORKFLOW_INVALID,
                "workflow draft state was not found",
                workflow_id=workflow_id,
            )
        return DraftState.model_validate(json.loads(path.read_text(encoding="utf-8")))

    def save_state(self, workflow_id: str, state: DraftState) -> None:
        self._write_json(
            self._state_path(workflow_id), state.model_dump(mode="json", exclude_none=True)
        )

    def _draft_path(self, workflow_id: str) -> Path:
        return self.drafts / f"{self._safe_id(workflow_id)}.json"

    def _state_path(self, workflow_id: str) -> Path:
        return self.drafts / f"{self._safe_id(workflow_id)}.state.json"

    @staticmethod
    def _safe_id(workflow_id: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,256}", workflow_id):
            raise automation_error(
                ErrorClassification.VALIDATION_ERROR,
                "workflow id is not safe for storage",
            )
        return workflow_id

    @staticmethod
    def _write_json(path: Path, value: object) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
