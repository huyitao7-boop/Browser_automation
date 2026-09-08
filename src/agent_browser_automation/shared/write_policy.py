import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .classifications import ErrorClassification
from .errors import automation_error
from .run_context import AllowedControl, WriteAuthorization


class WritePolicy(BaseModel):
    """Trusted environment policy for non-production, bounded test writes."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=256)
    environment_type: Literal["development", "test"]
    allowed_capabilities: tuple[str, ...] = Field(min_length=1)
    allowed_operations: tuple[Literal["create", "update", "delete"], ...] = (
        "create",
    )
    allowed_resource_names: tuple[str, ...] = ()
    resource_name_prefixes: tuple[str, ...] = ()
    resource_name_patterns: tuple[str, ...] = ()
    require_authenticated_session: bool = True
    require_postcondition: bool = True

    @model_validator(mode="after")
    def validate_allowlists(self) -> "WritePolicy":
        if len(set(self.allowed_capabilities)) != len(self.allowed_capabilities):
            raise ValueError("write-policy capabilities must be unique")
        if len(set(self.allowed_operations)) != len(self.allowed_operations):
            raise ValueError("write-policy operations must be unique")
        if (
            not self.allowed_resource_names
            and not self.resource_name_prefixes
            and not self.resource_name_patterns
        ):
            raise ValueError("write-policy requires an exact name, prefix, or pattern")
        if len(set(self.allowed_resource_names)) != len(self.allowed_resource_names):
            raise ValueError("write-policy exact resource names must be unique")
        if any(not name.strip() for name in self.allowed_resource_names):
            raise ValueError("write-policy exact resource names must not be blank")
        if any(not prefix.strip() for prefix in self.resource_name_prefixes):
            raise ValueError("write-policy resource prefixes must not be blank")
        for pattern in self.resource_name_patterns:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError("write-policy resource name pattern is invalid") from exc
        return self

    def authorize(
        self,
        *,
        environment_id: str,
        capability: str,
        operation: Literal["create", "update", "delete"],
        resource_name: str,
        authenticated: bool,
        has_postcondition: bool,
        allowed_setup_controls: tuple[AllowedControl, ...] = (),
        allowed_submit_controls: tuple[AllowedControl, ...] = (),
        demo_allow_observed_controls: bool = False,
    ) -> WriteAuthorization:
        if capability not in self.allowed_capabilities:
            raise automation_error(
                ErrorClassification.POLICY_DENIED,
                "write capability is not allowed by the environment policy",
                policy_id=self.id,
                capability=capability,
            )
        if operation not in self.allowed_operations:
            raise automation_error(
                ErrorClassification.POLICY_DENIED,
                "write operation is not allowed by the environment policy",
                policy_id=self.id,
                operation=operation,
            )
        matches_exact = resource_name in self.allowed_resource_names
        matches_prefix = any(resource_name.startswith(item) for item in self.resource_name_prefixes)
        matches_pattern = any(
            re.fullmatch(pattern, resource_name) is not None
            for pattern in self.resource_name_patterns
        )
        if not matches_exact and not matches_prefix and not matches_pattern:
            raise automation_error(
                ErrorClassification.POLICY_DENIED,
                "resource name does not match an allowed test naming rule",
                policy_id=self.id,
            )
        if self.require_authenticated_session and not authenticated:
            raise automation_error(
                ErrorClassification.AUTHENTICATION_REQUIRED,
                "test writes require a verified authenticated session",
                policy_id=self.id,
            )
        if self.require_postcondition and not has_postcondition:
            raise automation_error(
                ErrorClassification.POLICY_DENIED,
                "test writes require an explicit post-write assertion",
                policy_id=self.id,
            )
        return WriteAuthorization(
            environment_id=environment_id,
            policy_id=self.id,
            capability=capability,
            operation=operation,
            resource_name=resource_name,
            authenticated=authenticated,
            allowed_setup_controls=allowed_setup_controls,
            allowed_submit_controls=allowed_submit_controls,
            demo_allow_observed_controls=demo_allow_observed_controls,
        )
