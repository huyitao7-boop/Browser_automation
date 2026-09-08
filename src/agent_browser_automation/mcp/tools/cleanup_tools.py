import asyncio

from mcp.server.fastmcp import FastMCP

from agent_browser_automation.browser.api_operations import ApiCleanupPlan
from agent_browser_automation.environment.store import EnvironmentStore
from agent_browser_automation.evidence.store import EvidenceStore
from agent_browser_automation.mcp.schemas import (
    BrowserSessionRequest,
    ResourceCleanupRequest,
    ToolResult,
)
from agent_browser_automation.shared.classifications import ErrorClassification
from agent_browser_automation.shared.errors import AutomationException, automation_error
from agent_browser_automation.shared.resource_ledger import (
    ResourceRecord,
    ResourceState,
)
from agent_browser_automation.shared.tracing import create_run_id, ensure_trace_id

from .browser_tools import BrowserSessionService


class ResourceCleanupService:
    def __init__(
        self,
        evidence: EvidenceStore,
        environments: EnvironmentStore,
        browsers: BrowserSessionService,
    ) -> None:
        self.evidence = evidence
        self.environments = environments
        self.browsers = browsers
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}

    async def run(self, request: ResourceCleanupRequest) -> ToolResult:
        trace_id = ensure_trace_id(request.trace_id)
        cleanup_run_id = create_run_id()
        key = (request.run_id, request.idempotency_key)
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            try:
                artifact = self.evidence.resource_ledger(request.run_id)
                record = self._record(artifact.records, request.idempotency_key)
                environment = self.environments.get(record.environment_id)
                policy = environment.write_policy
                if policy is None or policy.id != record.policy_id:
                    raise automation_error(
                        ErrorClassification.POLICY_DENIED,
                        "resource ledger policy no longer matches the environment",
                    )
                if record.operation != "create":
                    raise automation_error(
                        ErrorClassification.POLICY_DENIED,
                        "compensation cleanup only accepts create records",
                    )
                if record.capability not in policy.allowed_capabilities or not any(
                    record.resource_name.startswith(prefix)
                    for prefix in policy.resource_name_prefixes
                ):
                    raise automation_error(
                        ErrorClassification.POLICY_DENIED,
                        "resource is outside the environment test-write allowlist",
                    )
                browser_plans = [
                    item
                    for item in environment.cleanup_plans
                    if item.capability == record.capability
                    and item.resource_kind == record.resource_kind
                ]
                api_plans = [
                    item
                    for item in environment.api_cleanup_plans
                    if item.capability == record.capability
                    and item.resource_kind == record.resource_kind
                ]
                plans = [*browser_plans, *api_plans]
                if len(plans) != 1:
                    raise automation_error(
                        ErrorClassification.POLICY_DENIED,
                        "environment has no unique trusted cleanup plan for the resource",
                    )
                plan = plans[0]
                if record.state not in {
                    ResourceState.VERIFIED,
                    ResourceState.NEEDS_CLEANUP,
                    ResourceState.CLEANUP_FAILED,
                }:
                    raise automation_error(
                        ErrorClassification.POLICY_DENIED,
                        "resource ledger state does not permit compensation cleanup",
                        state=record.state.value,
                    )
                if record.cleanup_attempts >= plan.max_attempts:
                    raise automation_error(
                        ErrorClassification.RECOVERY_EXHAUSTED,
                        "resource cleanup attempt limit is exhausted",
                        attempts=record.cleanup_attempts,
                    )
                attempts = record.cleanup_attempts + 1
                self.evidence.transition_resource(
                    request.run_id,
                    request.idempotency_key,
                    ResourceState.CLEANUP_RUNNING,
                    cleanup_attempts=attempts,
                    cleanup_run_id=cleanup_run_id,
                )
                try:
                    cleanup_request = BrowserSessionRequest(
                        session_id=request.session_id,
                        request_id=request.request_id,
                        trace_id=trace_id,
                    )
                    if isinstance(plan, ApiCleanupPlan):
                        result = await self.browsers.cleanup_api_resource(
                            cleanup_request,
                            plan,
                            record,
                            cleanup_run_id,
                        )
                    else:
                        result = await self.browsers.cleanup_resource(
                            cleanup_request,
                            plan,
                            record,
                            cleanup_run_id,
                        )
                except AutomationException as exc:
                    updated = self.evidence.transition_resource(
                        request.run_id,
                        request.idempotency_key,
                        ResourceState.CLEANUP_FAILED,
                        cleanup_attempts=attempts,
                        cleanup_run_id=cleanup_run_id,
                        cleanup_error=exc.error.classification.value,
                    )
                    return ToolResult(
                        status="needs-review",
                        phase="cleanup",
                        trace_id=trace_id,
                        request_id=request.request_id,
                        run_id=cleanup_run_id,
                        data={
                            "source_run_id": request.run_id,
                            "resource": updated.model_dump(mode="json"),
                        },
                        error=exc.error,
                    )
                if result.status == "passed":
                    state = (
                        ResourceState.CLEANUP_NOT_NEEDED
                        if result.observed_value == "cleanup-not-needed"
                        else ResourceState.CLEANED
                    )
                    updated = self.evidence.transition_resource(
                        request.run_id,
                        request.idempotency_key,
                        state,
                        cleanup_attempts=attempts,
                        cleanup_run_id=cleanup_run_id,
                    )
                    return ToolResult(
                        status="passed",
                        phase="cleanup",
                        trace_id=trace_id,
                        request_id=request.request_id,
                        run_id=cleanup_run_id,
                        data={
                            "source_run_id": request.run_id,
                            "resource": updated.model_dump(mode="json"),
                        },
                    )
                classification = (
                    result.error.classification.value
                    if result.error is not None
                    else ErrorClassification.INTERNAL_ERROR.value
                )
                updated = self.evidence.transition_resource(
                    request.run_id,
                    request.idempotency_key,
                    ResourceState.CLEANUP_FAILED,
                    cleanup_attempts=attempts,
                    cleanup_run_id=cleanup_run_id,
                    cleanup_error=classification,
                )
                return ToolResult(
                    status="needs-review",
                    phase="cleanup",
                    trace_id=trace_id,
                    request_id=request.request_id,
                    run_id=cleanup_run_id,
                    data={
                        "source_run_id": request.run_id,
                        "resource": updated.model_dump(mode="json"),
                    },
                    error=result.error,
                )
            except AutomationException as exc:
                return ToolResult(
                    status=(
                        "needs-review"
                        if exc.error.classification
                        == ErrorClassification.RECOVERY_EXHAUSTED
                        else "failed"
                    ),
                    phase="cleanup",
                    trace_id=trace_id,
                    request_id=request.request_id,
                    run_id=cleanup_run_id,
                    error=exc.error,
                )

    @staticmethod
    def _record(
        records: tuple[ResourceRecord, ...], idempotency_key: str
    ) -> ResourceRecord:
        matches = [item for item in records if item.idempotency_key == idempotency_key]
        if len(matches) != 1:
            raise automation_error(
                ErrorClassification.VALIDATION_ERROR,
                "resource ledger record was not found",
                idempotency_key=idempotency_key,
            )
        return matches[0]


def register_cleanup_tools(mcp: FastMCP, service: ResourceCleanupService) -> None:
    @mcp.tool()
    async def resource_cleanup_run(request: ResourceCleanupRequest) -> ToolResult:
        """Run one bounded compensation plan for a recorded test resource."""
        return await service.run(request)

    _ = resource_cleanup_run
