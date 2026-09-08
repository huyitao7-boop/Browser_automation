from dataclasses import dataclass
from typing import Any

from agent_browser_automation.shared.classifications import ErrorClassification
from agent_browser_automation.shared.errors import automation_error

from .store import WorkflowStore
from .types import WorkflowDefinition
from .validator import validate_inputs


@dataclass(frozen=True)
class AmbiguousWorkflowError(Exception):
    candidates: tuple[WorkflowDefinition, ...]


class WorkflowMatcher:
    def __init__(self, store: WorkflowStore) -> None:
        self.store = store

    def match(
        self,
        *,
        workflow_id: str | None,
        capability: str | None,
        inputs: dict[str, Any],
    ) -> WorkflowDefinition:
        candidates = self.store.load_published()
        if workflow_id is not None:
            candidates = [item for item in candidates if item.id == workflow_id]
        elif capability is not None:
            candidates = [item for item in candidates if item.capability == capability]

        compatible: list[WorkflowDefinition] = []
        input_errors: list[str] = []
        for candidate in candidates:
            try:
                validate_inputs(candidate.input_schema, inputs)
                compatible.append(candidate)
            except Exception as exc:
                input_errors.append(str(exc))

        if not compatible:
            if candidates and input_errors:
                validate_inputs(candidates[0].input_schema, inputs)
            raise automation_error(
                ErrorClassification.WORKFLOW_NOT_FOUND,
                "no published workflow matched the request",
                workflow_id=workflow_id,
                capability=capability,
            )
        if len(compatible) > 1:
            raise AmbiguousWorkflowError(tuple(compatible))
        return compatible[0]
