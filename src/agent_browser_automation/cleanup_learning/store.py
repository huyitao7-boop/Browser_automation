import hashlib
import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from agent_browser_automation.shared.classifications import ErrorClassification
from agent_browser_automation.shared.errors import automation_error

from .models import CleanupDraft, CleanupDraftState


def cleanup_draft_hash(draft: CleanupDraft) -> str:
    encoded = json.dumps(
        draft.model_dump(mode="json", exclude_none=True),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


class CleanupDraftStore:
    def __init__(self, environment_root: Path) -> None:
        self.environment_root = Path(environment_root)
        self.root = self.environment_root / "cleanup-drafts"
        self.root.mkdir(parents=True, exist_ok=True)

    def save(
        self, draft: CleanupDraft, creator_id: str, *, trajectory_bound: bool = False
    ) -> CleanupDraftState:
        self._write(self._draft_path(draft.environment_id, draft.plan.id), draft)
        state = CleanupDraftState(
            draft_hash=cleanup_draft_hash(draft),
            creator_id=creator_id,
            trajectory_bound=trajectory_bound,
        )
        self.save_state(draft.environment_id, draft.plan.id, state)
        return state

    def load(self, environment_id: str, plan_id: str) -> CleanupDraft:
        path = self._draft_path(environment_id, plan_id)
        if not path.is_file():
            raise automation_error(
                ErrorClassification.VALIDATION_ERROR,
                "cleanup draft was not found",
                environment_id=environment_id,
                plan_id=plan_id,
            )
        return CleanupDraft.model_validate_json(path.read_text(encoding="utf-8"))

    def load_state(self, environment_id: str, plan_id: str) -> CleanupDraftState:
        path = self._state_path(environment_id, plan_id)
        if not path.is_file():
            raise automation_error(
                ErrorClassification.VALIDATION_ERROR,
                "cleanup draft state was not found",
                environment_id=environment_id,
                plan_id=plan_id,
            )
        return CleanupDraftState.model_validate_json(path.read_text(encoding="utf-8"))

    def save_state(
        self, environment_id: str, plan_id: str, state: CleanupDraftState
    ) -> None:
        self._write(self._state_path(environment_id, plan_id), state)

    def _draft_path(self, environment_id: str, plan_id: str) -> Path:
        return self.root / f"{self._safe(environment_id)}__{self._safe(plan_id)}.json"

    def _state_path(self, environment_id: str, plan_id: str) -> Path:
        return self.root / f"{self._safe(environment_id)}__{self._safe(plan_id)}.state.json"

    @staticmethod
    def _safe(value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,256}", value):
            raise automation_error(
                ErrorClassification.VALIDATION_ERROR,
                "cleanup draft identifier is unsafe for storage",
            )
        return value

    @staticmethod
    def _write(path: Path, value: BaseModel | Any) -> None:
        payload = (
            value.model_dump(mode="json", exclude_none=True)
            if isinstance(value, BaseModel)
            else value
        )
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(path)
