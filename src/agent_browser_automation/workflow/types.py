from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent_browser_automation.shared.run_context import RiskLevel

from .action import Assertion, TargetSpec, WebAction


class WorkflowStep(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    precondition: Assertion | None = None
    action: WebAction
    postcondition: Assertion | None = None


class WorkflowDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    version: int = Field(ge=1)
    status: Literal["draft", "published", "disabled"]
    capability: str
    risk: RiskLevel
    input_schema: dict[str, object]
    allowed_origins: tuple[str, ...]
    targets: dict[str, TargetSpec] = Field(default_factory=dict)
    steps: tuple[WorkflowStep, ...]

    @model_validator(mode="after")
    def validate_steps(self) -> "WorkflowDefinition":
        if not self.steps:
            raise ValueError("workflow must contain at least one step")
        ids = [step.id for step in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError("workflow step ids must be unique")
        return self
