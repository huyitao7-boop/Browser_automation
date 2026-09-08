from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent_browser_automation.shared.secrets import SecretRef


class JsonCreateOperation(BaseModel):
    """Trusted Environment declaration for one JSON API create capability."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,127}$")
    capability: str = Field(min_length=1, max_length=256)
    resource_kind: str = Field(min_length=1, max_length=128)
    create_url: str
    get_by_name_url_template: str
    authorization_secret_ref: SecretRef | None = None

    @model_validator(mode="after")
    def validate_urls(self) -> "JsonCreateOperation":
        if "{resource_locator}" not in self.get_by_name_url_template:
            raise ValueError("JSON create verification URL requires {resource_locator}")
        for url in (self.create_url, self.get_by_name_url_template):
            if not url.startswith(("http://", "https://")):
                raise ValueError("JSON create URLs must be absolute http(s) URLs")
        return self


class ApiCleanupPlan(BaseModel):
    """Trusted API cleanup plan for a ledger-bound resource locator."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,127}$")
    capability: str = Field(min_length=1, max_length=256)
    resource_kind: str = Field(min_length=1, max_length=128)
    lookup_url_template: str
    delete_url_template: str
    authorization_secret_ref: SecretRef | None = None
    max_attempts: Literal[1, 2] = 1
    absence_stability_ms: int = Field(default=500, ge=100, le=10_000)

    @model_validator(mode="after")
    def validate_urls(self) -> "ApiCleanupPlan":
        if "{resource_locator}" not in self.lookup_url_template:
            raise ValueError("API cleanup lookup URL requires {resource_locator}")
        if "{resource_id}" not in self.delete_url_template:
            raise ValueError("API cleanup delete URL requires {resource_id}")
        for url in (self.lookup_url_template, self.delete_url_template):
            if not url.startswith(("http://", "https://")):
                raise ValueError("API cleanup URLs must be absolute http(s) URLs")
        return self
