from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent_browser_automation.shared.run_context import RiskLevel
from agent_browser_automation.shared.secrets import SecretRef


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class LocatorStrategy(Model):
    kind: Literal["role", "label", "placeholder", "testid", "text", "css"]
    role: str | None = None
    name: str | None = None
    value: str | None = None

    @model_validator(mode="after")
    def validate_strategy(self) -> "LocatorStrategy":
        if self.kind == "role":
            if not self.role:
                raise ValueError("role strategy requires role")
        elif not self.value:
            raise ValueError(f"{self.kind} strategy requires value")
        return self


class TargetSpec(Model):
    region: Literal["page", "main", "sidebar", "dialog", "overlay", "toast"] = "page"
    strategies: tuple[LocatorStrategy, ...]
    expected_control: str | None = None

    @model_validator(mode="after")
    def require_strategy(self) -> "TargetSpec":
        if not self.strategies:
            raise ValueError("target requires at least one strategy")
        return self


class Assertion(Model):
    type: Literal[
        "url_matches",
        "url_not_matches",
        "title_contains",
        "text_visible",
        "target_visible",
        "target_not_visible",
    ]
    value: str | None = None
    target: TargetSpec | None = None
    target_ref: str | None = Field(default=None, alias="targetRef")
    stability_ms: int = Field(default=0, ge=0, le=30_000)

    @model_validator(mode="after")
    def validate_assertion(self) -> "Assertion":
        if self.type in {
            "url_matches",
            "url_not_matches",
            "title_contains",
            "text_visible",
        } and self.value is None:
            raise ValueError(f"{self.type} requires value")
        if self.type in {"target_visible", "target_not_visible"}:
            if self.target is None and self.target_ref is None:
                raise ValueError(f"{self.type} requires target or targetRef")
        if self.stability_ms and self.type not in {
            "url_not_matches",
            "target_not_visible",
        }:
            raise ValueError("stability_ms is only supported by negative assertions")
        return self


class NavigateAction(Model):
    type: Literal["navigate"] = "navigate"
    url: str
    expected_revision: int | None = None


class ObserveAction(Model):
    type: Literal["observe"] = "observe"
    expected_revision: int | None = None


class ScrollAction(Model):
    """Move the viewport by one fixed, selector-free exploration step."""

    type: Literal["scroll"] = "scroll"
    direction: Literal["up", "down"]
    expected_revision: int | None = None


class ClickAction(Model):
    type: Literal["click"] = "click"
    target: TargetSpec | None = None
    target_ref: str | None = Field(default=None, alias="targetRef")
    risk: RiskLevel
    expected_revision: int | None = None

    @model_validator(mode="after")
    def require_target(self) -> "ClickAction":
        if self.target is None and self.target_ref is None:
            raise ValueError("click requires target or targetRef")
        return self


class ExploreClickAction(Model):
    type: Literal["explore_click"] = "explore_click"
    target: TargetSpec
    risk: RiskLevel
    expected_revision: int | None = None


class AdvanceAction(Model):
    """Advance a browser wizard step within an active demo test-write grant."""

    type: Literal["advance"] = "advance"
    target: TargetSpec
    risk: RiskLevel
    expected_revision: int | None = None


class FillAction(Model):
    type: Literal["fill"] = "fill"
    target: TargetSpec | None = None
    target_ref: str | None = Field(default=None, alias="targetRef")
    secret_ref: SecretRef
    risk: RiskLevel
    expected_revision: int | None = None

    @model_validator(mode="after")
    def require_target(self) -> "FillAction":
        if self.target is None and self.target_ref is None:
            raise ValueError("fill requires target or targetRef")
        return self


class FillTextAction(Model):
    type: Literal["fill_text"] = "fill_text"
    target: TargetSpec | None = None
    target_ref: str | None = Field(default=None, alias="targetRef")
    value: str = Field(max_length=10_000)
    risk: RiskLevel
    expected_revision: int | None = None

    @model_validator(mode="after")
    def require_target(self) -> "FillTextAction":
        if self.target is None and self.target_ref is None:
            raise ValueError("fill_text requires target or targetRef")
        return self


class SelectRadioAction(Model):
    type: Literal["select_radio"] = "select_radio"
    target: TargetSpec
    risk: RiskLevel
    expected_revision: int | None = None


class SelectComboboxAction(Model):
    """Choose one option from a standard ARIA combobox."""

    type: Literal["select_combobox"] = "select_combobox"
    target: TargetSpec
    value: str = Field(min_length=1, max_length=10_000)
    risk: RiskLevel
    expected_revision: int | None = None


class SelectAction(Model):
    """Choose one ordinary option from a native HTML select control."""

    type: Literal["select"] = "select"
    target: TargetSpec | None = None
    target_ref: str | None = Field(default=None, alias="targetRef")
    value: str = Field(min_length=1, max_length=10_000)
    risk: RiskLevel
    expected_revision: int | None = None

    @model_validator(mode="after")
    def require_target(self) -> "SelectAction":
        if self.target is None and self.target_ref is None:
            raise ValueError("select requires target or targetRef")
        return self


class WriteIntent(Model):
    operation: Literal["create", "update", "delete"]
    capability: str = Field(min_length=1, max_length=256)
    resource_kind: str = Field(min_length=1, max_length=128)
    resource_name: str = Field(min_length=1, max_length=256)
    idempotency_key: str = Field(min_length=8, max_length=256)


class SubmitAction(Model):
    type: Literal["submit"] = "submit"
    target: TargetSpec | None = None
    target_ref: str | None = Field(default=None, alias="targetRef")
    intent: WriteIntent
    verification: Assertion
    risk: RiskLevel
    demo: bool = False
    expected_revision: int | None = None

    @model_validator(mode="after")
    def require_target(self) -> "SubmitAction":
        if self.target is None and self.target_ref is None:
            raise ValueError("submit requires target or targetRef")
        return self


class SimpleSubmitAction(Model):
    """Submit a form in the loopback-only simple browser profile."""

    type: Literal["simple_submit"] = "simple_submit"
    target: TargetSpec
    risk: RiskLevel = RiskLevel.READONLY
    expected_revision: int | None = None


class AssertAction(Model):
    type: Literal["assert"] = "assert"
    assertion: Assertion
    expected_revision: int | None = None


WebAction = Annotated[
    NavigateAction
    | ObserveAction
    | ScrollAction
    | ClickAction
    | ExploreClickAction
    | AdvanceAction
    | FillAction
    | FillTextAction
    | SelectRadioAction
    | SelectComboboxAction
    | SelectAction
    | SubmitAction
    | SimpleSubmitAction
    | AssertAction,
    Field(discriminator="type"),
]


READONLY_ACTION_TYPES = frozenset({"navigate", "observe", "scroll", "assert"})
