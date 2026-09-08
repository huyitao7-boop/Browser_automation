from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from agent_browser_automation.shared.errors import ErrorDetail


class InteractiveControl(BaseModel):
    """A bounded, selector-free summary of one visible semantic page control."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    region: Literal["page", "main", "sidebar", "dialog", "overlay", "toast"]
    role: str = Field(min_length=1, max_length=64)
    accessible_name: str = Field(min_length=1, max_length=500)
    expected_control: Literal[
        "button",
        "link",
        "textbox",
        "richtextbox",
        "select",
        "radio",
        "menu_button",
        "menu_item",
        "clickable",
    ]
    enabled: bool
    match_count: int = Field(ge=1, le=200)
    test_id: str | None = Field(default=None, min_length=1, max_length=256)


class PageObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    revision: int
    url: str
    title: str
    text_excerpt: str = ""
    headings: tuple[str, ...] = ()
    landmarks: tuple[str, ...] = ()
    interactive_controls: tuple[InteractiveControl, ...] = ()
    interactive_controls_total: int = Field(default=0, ge=0)
    interactive_controls_truncated: bool = False
    auth_state: str | None = None
    auth_frozen: bool = False
    auth_reason: str | None = None


class BrowserConsoleEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: str
    text: str


class BrowserNetworkEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["requestfailed", "response"]
    method: str
    url: str
    status: int | None = None
    failure: str | None = None


class BrowserPageErrorEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    message: str


class BrowserNavigationEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    url: str


class BrowserAuthEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state_before: str
    state_after: str
    reason: str
    policy_id: str
    url: str
    matched_signal: str | None = None


class BrowserEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    console_total: int = Field(ge=0)
    network_total: int = Field(ge=0)
    console_truncated: bool
    network_truncated: bool
    console_events: tuple[BrowserConsoleEvent, ...] = ()
    network_events: tuple[BrowserNetworkEvent, ...] = ()
    page_error_total: int = Field(default=0, ge=0)
    navigation_total: int = Field(default=0, ge=0)
    page_error_truncated: bool = False
    navigation_truncated: bool = False
    page_errors: tuple[BrowserPageErrorEvent, ...] = ()
    navigations: tuple[BrowserNavigationEvent, ...] = ()
    http_total: int = Field(default=0, ge=0)
    http_truncated: bool = False
    http_events: tuple[BrowserNetworkEvent, ...] = ()
    auth_total: int = Field(default=0, ge=0)
    auth_truncated: bool = False
    auth_events: tuple[BrowserAuthEvent, ...] = ()


class ActionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["passed", "failed"]
    action_type: str
    revision_before: int
    revision_after: int
    current_url: str
    observed_value: Any | None = None
    observation: PageObservation | None = None
    error: ErrorDetail | None = None


class StepResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    step_id: str
    kind: Literal["precondition", "action", "postcondition"] = "action"
    status: Literal["passed", "failed"]
    action: dict[str, Any]
    result: ActionResult


class WorkflowResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["passed", "failed", "needs-review"]
    workflow_id: str
    run_id: str
    completed_steps: list[str] = Field(default_factory=list)
    failed_step: str | None = None
    data: dict[str, Any] | None = None
    error: ErrorDetail | None = None
    artifact_directory: str
    evidence_manifest: str
