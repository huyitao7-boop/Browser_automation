import json
from pathlib import Path

from pydantic import ValidationError

from agent_browser_automation.shared.classifications import ErrorClassification
from agent_browser_automation.shared.errors import automation_error

from .types import WorkflowDefinition
from .validator import validate_workflow


class WorkflowStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def load_published(self) -> list[WorkflowDefinition]:
        directory = self.root / "published"
        if not directory.exists():
            return []
        workflows: list[WorkflowDefinition] = []
        for path in sorted(directory.glob("*.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                workflow = WorkflowDefinition.model_validate(raw)
                validate_workflow(workflow)
            except (OSError, json.JSONDecodeError, ValidationError) as exc:
                raise automation_error(
                    ErrorClassification.WORKFLOW_INVALID,
                    "published workflow could not be loaded",
                    path=str(path),
                    cause=str(exc)[:2_000],
                ) from exc
            workflows.append(workflow)
        return workflows

    def search(
        self, *, capability: str | None = None, origin: str | None = None
    ) -> list[WorkflowDefinition]:
        workflows = self.load_published()
        if capability is not None:
            workflows = [item for item in workflows if item.capability == capability]
        if origin is not None:
            from agent_browser_automation.shared.run_context import normalized_origin

            expected = normalized_origin(origin)
            workflows = [
                item
                for item in workflows
                if expected in {normalized_origin(value) for value in item.allowed_origins}
            ]
        return workflows
