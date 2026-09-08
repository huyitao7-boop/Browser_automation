from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

EvidenceStatus = Literal["running", "passed", "failed", "needs-review"]


class EvidenceArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: str
    path: str
    content_type: str
    redacted: bool
    step_id: str | None = None
    truncated: bool | None = None


class EvidenceManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    trace_id: str
    request_id: str
    run_id: str
    workflow_id: str
    status: EvidenceStatus = "running"
    started_at: datetime
    finished_at: datetime | None = None
    artifacts: tuple[EvidenceArtifact, ...] = Field(default_factory=tuple)
