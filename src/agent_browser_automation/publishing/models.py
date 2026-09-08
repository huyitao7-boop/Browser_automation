from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class VerificationRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    draft_hash: str
    passed_runs: int = Field(ge=0)
    inputs: dict[str, Any]
    run_ids: tuple[str, ...] = ()


class ReviewRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    draft_hash: str
    reviewer_id: str = Field(min_length=1, max_length=128)
    approved: bool
    summary: str = Field(min_length=1, max_length=4000)


class DraftState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    draft_hash: str
    creator_id: str
    validated: bool = False
    verification: VerificationRecord | None = None
    review: ReviewRecord | None = None
