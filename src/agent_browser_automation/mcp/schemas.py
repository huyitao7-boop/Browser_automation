from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent_browser_automation.shared.errors import ErrorDetail
from agent_browser_automation.shared.tracing import TRACE_ID_PATTERN
from agent_browser_automation.testing.models import TestCase
from agent_browser_automation.workflow.action import Assertion, TargetSpec
from agent_browser_automation.workflow.types import WorkflowDefinition


class RequestContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: str = Field(min_length=1, max_length=128)
    trace_id: str | None = Field(
        default=None, min_length=1, max_length=128, pattern=TRACE_ID_PATTERN.pattern
    )


class DiscoveryRequest(RequestContext):
    pass


class WorkflowSearchRequest(RequestContext):
    capability: str | None = Field(default=None, min_length=1, max_length=256)
    origin: str | None = None


class BrowserSessionStartRequest(RequestContext):
    url: str
    trusted_redirect_origins: tuple[str, ...] = ()


class BrowserSessionRequest(RequestContext):
    session_id: str = Field(min_length=1, max_length=128)


class BrowserNavigateRequest(BrowserSessionRequest):
    url: str
    expected_revision: int | None = Field(default=None, ge=0)


class BrowserScrollRequest(BrowserSessionRequest):
    direction: Literal["up", "down"]
    expected_revision: int = Field(ge=0)


class BrowserAssertRequest(BrowserSessionRequest):
    assertion: Assertion
    expected_revision: int | None = Field(default=None, ge=0)


# Public exploration primitives.  They are intentionally separate from the
# business-level workflow_run tool and retain the same session/revision guards.
class BrowserClickRequest(BrowserSessionRequest):
    target: TargetSpec
    expected_revision: int = Field(ge=0)


class BrowserFillTextRequest(BrowserSessionRequest):
    target: TargetSpec
    value: str = Field(max_length=10_000)
    expected_revision: int = Field(ge=0)


class BrowserSelectRequest(BrowserSessionRequest):
    target: TargetSpec
    value: str = Field(min_length=1, max_length=10_000)
    expected_revision: int = Field(ge=0)


class BrowserSubmitRequest(BrowserSessionRequest):
    target: TargetSpec
    expected_revision: int = Field(ge=0)


class BrowserSimpleSubmitRequest(BrowserSessionRequest):
    target: TargetSpec
    expected_revision: int = Field(ge=0)


class RunEvidenceRequest(RequestContext):
    run_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")


class TestCaseRequest(RequestContext):
    testcase: TestCase


class WorkflowDraftRequest(RequestContext):
    creator_id: str = Field(min_length=1, max_length=128)
    workflow: WorkflowDefinition


class WorkflowGenerateRequest(RequestContext):
    session_id: str = Field(min_length=1, max_length=128)
    workflow_id: str = Field(min_length=1, max_length=256)
    capability: str = Field(min_length=1, max_length=256)
    creator_id: str = Field(min_length=1, max_length=128)
    input_schema: dict[str, object] = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        }
    )


class WorkflowDraftIdRequest(RequestContext):
    workflow_id: str = Field(min_length=1, max_length=256)


class WorkflowVerifyRequest(WorkflowDraftIdRequest):
    inputs: dict[str, Any] = Field(default_factory=dict)


class WorkflowReviewRequest(WorkflowDraftIdRequest):
    draft_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    reviewer_id: str = Field(min_length=1, max_length=128)
    approved: bool
    summary: str = Field(min_length=1, max_length=4000)


class WorkflowRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    workflow_id: str | None = None
    capability: str | None = None
    inputs: dict[str, Any] = Field(default_factory=dict)
    request_id: str
    trace_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=TRACE_ID_PATTERN.pattern,
    )

    @model_validator(mode="after")
    def require_selector(self) -> "WorkflowRunRequest":
        if self.workflow_id is None and self.capability is None:
            raise ValueError("workflow_id or capability is required")
        return self


class NextAction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: str
    message: str
    choices: list[dict[str, Any]] = Field(default_factory=lambda: [])


class ToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["passed", "failed", "needs-input", "needs-review"]
    phase: Literal[
        "validation",
        "matching",
        "safety",
        "authentication",
        "inspection",
        "startup",
        "healthcheck",
        "recovery",
        "compilation",
        "diagnosis",
        "publishing",
        "execution",
        "verification",
        "cleanup",
    ]
    trace_id: str
    request_id: str
    run_id: str | None = None
    data: dict[str, Any] | None = None
    error: ErrorDetail | None = None
    next_action: NextAction | None = None
    artifact_directory: str | None = None
    evidence_manifest: str | None = None
