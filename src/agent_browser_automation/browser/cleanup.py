from typing import Literal
from urllib.parse import quote, urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent_browser_automation.workflow.action import LocatorStrategy, TargetSpec

RESOURCE_PLACEHOLDER = "${resource_name}"


class CleanupPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=256)
    capability: str = Field(min_length=1, max_length=256)
    resource_kind: str = Field(min_length=1, max_length=128)
    resource_url_template: str
    identity_target: TargetSpec
    delete_target: TargetSpec
    confirmation_target: TargetSpec | None = None
    confirmation_submit_target: TargetSpec | None = None
    absence_url: str
    absence_target: TargetSpec
    max_attempts: Literal[1, 2] = 1
    absence_stability_ms: int = Field(default=500, ge=100, le=10_000)

    @model_validator(mode="after")
    def validate_plan(self) -> "CleanupPlan":
        if "{resource_name}" not in self.resource_url_template:
            raise ValueError("cleanup resource URL requires {resource_name}")
        for url in (self.resource_url_template, self.absence_url):
            parsed = urlsplit(url.replace("{resource_name}", "resource"))
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError("cleanup URLs must be absolute http(s) URLs")
        if (self.confirmation_target is None) != (
            self.confirmation_submit_target is None
        ):
            raise ValueError("cleanup confirmation targets must be configured together")
        for target in (self.identity_target, self.absence_target):
            placeholders = [
                item
                for item in target.strategies
                if item.name == RESOURCE_PLACEHOLDER
                or item.value == RESOURCE_PLACEHOLDER
            ]
            if not placeholders:
                raise ValueError("cleanup identity targets must bind the resource name")
            if any(item.kind == "css" for item in placeholders):
                raise ValueError("resource names cannot be substituted into CSS selectors")
        return self

    def resource_url(self, resource_name: str) -> str:
        return self.resource_url_template.replace(
            "{resource_name}", quote(resource_name, safe="")
        )

    def resolve_target(self, target: TargetSpec, resource_name: str) -> TargetSpec:
        strategies = tuple(
            LocatorStrategy(
                kind=item.kind,
                role=item.role,
                name=(resource_name if item.name == RESOURCE_PLACEHOLDER else item.name),
                value=(
                    resource_name if item.value == RESOURCE_PLACEHOLDER else item.value
                ),
            )
            for item in target.strategies
        )
        return target.model_copy(update={"strategies": strategies})
