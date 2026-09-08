from pydantic import BaseModel, ConfigDict, Field

from agent_browser_automation.browser.cleanup import CleanupPlan
from agent_browser_automation.publishing.models import ReviewRecord


class CleanupDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    environment_id: str = Field(min_length=1, max_length=128)
    plan: CleanupPlan
    trajectory_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class CleanupVerificationRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    draft_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    passed_runs: int = Field(ge=0)
    source_run_ids: tuple[str, ...] = ()
    cleanup_run_ids: tuple[str, ...] = ()
    resource_names: tuple[str, ...] = ()


class CleanupDraftState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    draft_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    creator_id: str = Field(min_length=1, max_length=128)
    trajectory_bound: bool = False
    validated: bool = False
    verification: CleanupVerificationRecord | None = None
    review: ReviewRecord | None = None
