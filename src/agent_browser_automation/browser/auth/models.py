from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agent_browser_automation.shared.run_context import normalized_origin
from agent_browser_automation.shared.secrets import SecretRef
from agent_browser_automation.workflow.action import TargetSpec


class AuthState(StrEnum):
    UNKNOWN = "unknown"
    AUTHENTICATED = "authenticated"
    AUTHENTICATION_REQUIRED = "authentication-required"
    INTERRUPTED = "interrupted"


class AuthPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,255}$")
    signed_out_url_patterns: tuple[str, ...] = ()
    signed_out_targets: tuple[TargetSpec, ...] = ()
    authenticated_targets: tuple[TargetSpec, ...] = ()
    stability_ms: int = Field(default=500, ge=0, le=30_000)
    freeze_on_marker_loss: bool = True

    @model_validator(mode="after")
    def require_signal(self) -> "AuthPolicy":
        if not (
            self.signed_out_url_patterns
            or self.signed_out_targets
            or self.authenticated_targets
        ):
            raise ValueError("auth policy requires at least one authentication signal")
        return self


class AuthObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: AuthState
    reason: str
    policy_id: str
    url: str
    matched_signal: str | None = None


class AuthCredentialField(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["identifier", "password"]
    target: TargetSpec
    secret_ref: SecretRef

    @model_validator(mode="after")
    def require_expected_control(self) -> "AuthCredentialField":
        expected = self.target.expected_control
        required = "password" if self.kind == "password" else "textbox"
        if expected != required:
            raise ValueError(f"{self.kind} credential target must expect {required!r}")
        return self


class AuthRecoveryPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    login_url: str
    entry_target: TargetSpec | None = None
    credentials: tuple[AuthCredentialField, ...] = Field(min_length=1, max_length=4)
    submit_target: TargetSpec
    verification_timeout_ms: int = Field(default=10_000, ge=100, le=60_000)
    poll_interval_ms: int = Field(default=100, ge=50, le=2_000)

    @field_validator("login_url")
    @classmethod
    def require_http_login_url(cls, value: str) -> str:
        normalized_origin(value)
        return value

    @model_validator(mode="after")
    def require_safe_shape(self) -> "AuthRecoveryPlan":
        references = [item.secret_ref.value for item in self.credentials]
        if len(references) != len(set(references)):
            raise ValueError("authentication credential secret refs must be unique")
        if self.submit_target.expected_control != "button":
            raise ValueError("authentication submit target must expect 'button'")
        return self


class EnvironmentSecretBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    secret_ref: SecretRef
    environment_variable: str = Field(pattern=r"^[A-Z_][A-Z0-9_]{0,127}$")


class BrowserAuthenticationDefinition(BaseModel):
    """Trusted platform configuration loaded from an Environment definition."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    origin: str
    allowed_origins: tuple[str, ...] = Field(min_length=1, max_length=8)
    policy: AuthPolicy
    recovery: AuthRecoveryPlan
    secret_environment: tuple[EnvironmentSecretBinding, ...] = Field(min_length=1)
    operation_secret_environment: tuple[EnvironmentSecretBinding, ...] = ()

    @field_validator("origin")
    @classmethod
    def normalize_origin(cls, value: str) -> str:
        return normalized_origin(value)

    @field_validator("allowed_origins")
    @classmethod
    def normalize_allowed_origins(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(normalized_origin(value) for value in values))

    @model_validator(mode="after")
    def validate_recovery_bindings(self) -> "BrowserAuthenticationDefinition":
        if normalized_origin(self.recovery.login_url) != self.origin:
            raise ValueError("authentication login URL must use the configured origin")
        if self.origin not in self.allowed_origins:
            raise ValueError("authentication allowed origins must include the application origin")
        all_bindings = self.secret_environment + self.operation_secret_environment
        references = [item.secret_ref.value for item in all_bindings]
        variables = [item.environment_variable for item in all_bindings]
        if len(references) != len(set(references)):
            raise ValueError("authentication secret refs must be unique")
        if len(variables) != len(set(variables)):
            raise ValueError("authentication environment variables must be unique")
        required = {item.secret_ref.value for item in self.recovery.credentials}
        recovery_references = {
            item.secret_ref.value for item in self.secret_environment
        }
        if recovery_references != required:
            raise ValueError(
                "authentication secret bindings must exactly match recovery credentials"
            )
        return self

    def secret_bindings(self) -> dict[str, str]:
        return {
            item.secret_ref.value: item.environment_variable
            for item in self.secret_environment + self.operation_secret_environment
        }

    def recovery_secret_refs(self) -> tuple[str, ...]:
        return tuple(item.secret_ref.value for item in self.secret_environment)
