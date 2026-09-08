from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .classifications import ErrorClassification


class ErrorDetail(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    classification: ErrorClassification
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class AutomationException(Exception):
    def __init__(self, error: ErrorDetail) -> None:
        super().__init__(error.message)
        self.error = error


def automation_error(
    classification: ErrorClassification,
    message: str,
    **details: Any,
) -> AutomationException:
    return AutomationException(
        ErrorDetail(
            code=classification.value,
            classification=classification,
            message=message,
            details=details,
        )
    )
