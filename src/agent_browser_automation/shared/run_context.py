from enum import StrEnum
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class RiskLevel(StrEnum):
    READONLY = "readonly"
    AUTHENTICATION = "authentication"
    TEST_WRITE = "test-write"
    DELETE = "delete"
    PROHIBITED = "prohibited"


class WriteAuthorization(BaseModel):
    """A trusted, environment-derived grant for one bounded test mutation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    environment_id: str = Field(min_length=1, max_length=128)
    policy_id: str = Field(min_length=1, max_length=256)
    capability: str = Field(min_length=1, max_length=256)
    operation: Literal["create", "update", "delete"]
    resource_name: str = Field(min_length=1, max_length=256)
    authenticated: bool
    allowed_setup_controls: tuple["AllowedControl", ...] = ()
    allowed_submit_controls: tuple["AllowedControl", ...] = ()
    demo_allow_observed_controls: bool = False


class AllowedControl(BaseModel):
    """Environment-declared semantic control usable before the single submit."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    expected_control: str = Field(min_length=1, max_length=64)
    accessible_name: str | None = Field(default=None, min_length=1, max_length=500)
    test_id: str | None = Field(default=None, min_length=1, max_length=256)

    @model_validator(mode="after")
    def require_exact_identity(self) -> "AllowedControl":
        if (self.accessible_name is None) == (self.test_id is None):
            raise ValueError("allowed control requires exactly one name or test id")
        return self


def normalized_origin(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("origin must be an absolute http(s) URL")
    default_port = 80 if parsed.scheme == "http" else 443
    port = parsed.port or default_port
    return f"{parsed.scheme}://{parsed.hostname.lower()}:{port}"


class RunContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    trace_id: str
    run_id: str
    request_id: str
    workflow_id: str
    allowed_origins: tuple[str, ...]
    max_risk: RiskLevel = RiskLevel.READONLY
    allow_local_form_actions: bool = False
    # Direct browser sessions do not use Environment or write grants.  This is
    # an execution-mode flag; page identity is still bound to the Session.
    allow_session_interactions: bool = False
    write_authorization: WriteAuthorization | None = None
    action_timeout_ms: int = Field(default=15_000, ge=100, le=120_000)
    navigation_timeout_ms: int = Field(default=30_000, ge=100, le=180_000)

    @field_validator("allowed_origins")
    @classmethod
    def validate_origins(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if not values:
            raise ValueError("at least one allowed origin is required")
        return tuple(dict.fromkeys(normalized_origin(value) for value in values))
