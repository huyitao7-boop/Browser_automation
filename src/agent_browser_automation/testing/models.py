from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TestCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,127}$")
    goal: str = Field(min_length=1, max_length=2000)
    capability: str | None = Field(default=None, min_length=1, max_length=256)
    workflow_id: str | None = Field(default=None, min_length=1, max_length=256)
    inputs: dict[str, Any] = Field(default_factory=dict)
    expected_status: Literal["passed"] = "passed"

    @model_validator(mode="after")
    def require_selector(self) -> "TestCase":
        if self.capability is None and self.workflow_id is None:
            raise ValueError("test case requires capability or workflow_id")
        return self


class CompiledTestPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    testcase_id: str
    workflow_id: str
    capability: str
    inputs: dict[str, Any]
    expected_status: Literal["passed"]
