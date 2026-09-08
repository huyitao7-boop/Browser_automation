from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from threading import RLock
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .classifications import ErrorClassification
from .errors import automation_error


class ResourceState(StrEnum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    VERIFIED = "verified"
    NEEDS_CLEANUP = "needs-cleanup"
    CLEANUP_RUNNING = "cleanup-running"
    CLEANED = "cleaned"
    CLEANUP_NOT_NEEDED = "cleanup-not-needed"
    CLEANUP_FAILED = "cleanup-failed"


UNRESOLVED_CLEANUP_STATES = frozenset(
    {
        ResourceState.NEEDS_CLEANUP,
        ResourceState.CLEANUP_RUNNING,
        ResourceState.CLEANUP_FAILED,
    }
)


class ResourceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    idempotency_key: str
    operation: Literal["create", "update", "delete"]
    capability: str
    resource_kind: str
    resource_name: str
    resource_locator: str | None = None
    environment_id: str
    policy_id: str
    state: ResourceState
    created_at: datetime
    updated_at: datetime
    cleanup_attempts: int = 0
    last_cleanup_run_id: str | None = None
    last_cleanup_error: str | None = None


class ResourceLedgerArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    run_id: str = Field(min_length=1, max_length=128)
    records: tuple[ResourceRecord, ...]
    pending_cleanup_count: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_pending_count(self) -> "ResourceLedgerArtifact":
        actual = sum(
            item.state in UNRESOLVED_CLEANUP_STATES for item in self.records
        )
        if self.pending_cleanup_count != actual:
            raise ValueError("resource-ledger pending cleanup count is inconsistent")
        return self


ResourceLedgerListener = Callable[[tuple[ResourceRecord, ...]], None]


class ResourceLedger:
    """Session-local mutation ledger; duplicate keys never execute twice."""

    def __init__(self, listener: ResourceLedgerListener | None = None) -> None:
        self._records: dict[str, ResourceRecord] = {}
        self._lock = RLock()
        self._listener = listener

    def set_listener(self, listener: ResourceLedgerListener) -> None:
        with self._lock:
            self._listener = listener

    def begin(
        self,
        *,
        idempotency_key: str,
        operation: Literal["create", "update", "delete"],
        capability: str,
        resource_kind: str,
        resource_name: str,
        resource_locator: str | None = None,
        environment_id: str,
        policy_id: str,
    ) -> ResourceRecord:
        with self._lock:
            if idempotency_key in self._records:
                raise automation_error(
                    ErrorClassification.POLICY_DENIED,
                    "write idempotency key has already been used",
                    idempotency_key=idempotency_key,
                )
            now = datetime.now(UTC)
            record = ResourceRecord(
                idempotency_key=idempotency_key,
                operation=operation,
                capability=capability,
                resource_kind=resource_kind,
                resource_name=resource_name,
                resource_locator=resource_locator,
                environment_id=environment_id,
                policy_id=policy_id,
                state=ResourceState.PENDING,
                created_at=now,
                updated_at=now,
            )
            self._records[idempotency_key] = record
            try:
                self._notify()
            except Exception:
                del self._records[idempotency_key]
                raise
            return record

    def transition(
        self,
        idempotency_key: str,
        state: ResourceState,
        **updates: object,
    ) -> ResourceRecord:
        with self._lock:
            record = self._records[idempotency_key]
            updated = record.model_copy(
                update={"state": state, "updated_at": datetime.now(UTC), **updates}
            )
            self._records[idempotency_key] = updated
            self._notify()
            return updated

    def snapshot(self) -> tuple[ResourceRecord, ...]:
        with self._lock:
            return tuple(self._records.values())

    def _notify(self) -> None:
        if self._listener is not None:
            self._listener(tuple(self._records.values()))
