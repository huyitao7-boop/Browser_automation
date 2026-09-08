from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agent_browser_automation.shared.redaction import DEFAULT_REDACTOR, Redactor


class TrajectoryEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sequence: int = Field(ge=1)
    recorded_at: datetime
    action: dict[str, Any]
    result: dict[str, Any]


class TrajectoryRecorder:
    """Records redacted browser facts; it never chooses the next action."""

    def __init__(self, redactor: Redactor = DEFAULT_REDACTOR) -> None:
        self._events: list[TrajectoryEvent] = []
        self.redactor = redactor

    def append(self, action: BaseModel, result: BaseModel) -> None:
        self._events.append(
            TrajectoryEvent(
                sequence=len(self._events) + 1,
                recorded_at=datetime.now(UTC),
                action=self.redactor.redact(
                    action.model_dump(mode="json", by_alias=True, exclude_none=True)
                ),
                result=self.redactor.redact(
                    result.model_dump(mode="json", exclude_none=True)
                ),
            )
        )

    def snapshot(self) -> tuple[TrajectoryEvent, ...]:
        return tuple(self._events)
