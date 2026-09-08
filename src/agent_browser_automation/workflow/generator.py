from typing import Any, cast

from pydantic import TypeAdapter

from agent_browser_automation.shared.run_context import RiskLevel

from .action import READONLY_ACTION_TYPES, WebAction
from .trajectory_recorder import TrajectoryEvent
from .types import WorkflowDefinition, WorkflowStep

ACTION_ADAPTER: TypeAdapter[Any] = TypeAdapter(WebAction)


class WorkflowGenerator:
    """Deterministically converts a factual readonly trajectory into a draft."""

    def generate(
        self,
        *,
        workflow_id: str,
        capability: str,
        allowed_origins: tuple[str, ...],
        events: tuple[TrajectoryEvent, ...],
        input_schema: dict[str, object] | None = None,
    ) -> WorkflowDefinition:
        steps: list[WorkflowStep] = []
        for event in events:
            raw = dict(event.action)
            raw.pop("expected_revision", None)
            action = cast(WebAction, ACTION_ADAPTER.validate_python(raw))
            if action.type not in READONLY_ACTION_TYPES:
                raise ValueError("trajectory contains a non-readonly action")
            steps.append(
                WorkflowStep(id=f"{action.type}-{event.sequence}", action=action)
            )
        return WorkflowDefinition(
            id=workflow_id,
            version=1,
            status="draft",
            capability=capability,
            risk=RiskLevel.READONLY,
            input_schema=input_schema
            or {"type": "object", "properties": {}, "additionalProperties": False},
            allowed_origins=allowed_origins,
            steps=tuple(steps),
        )
