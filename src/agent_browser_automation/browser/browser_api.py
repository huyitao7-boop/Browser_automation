from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from agent_browser_automation.shared.redaction import Redactor
from agent_browser_automation.shared.resource_ledger import (
    ResourceLedgerListener,
    ResourceRecord,
)
from agent_browser_automation.shared.run_context import RunContext
from agent_browser_automation.workflow.action import WebAction
from agent_browser_automation.workflow.result import (
    ActionResult,
    BrowserEvidence,
    PageObservation,
)

if TYPE_CHECKING:
    from .api_operations import ApiCleanupPlan, JsonCreateOperation
    from .cleanup import CleanupPlan


class BrowserAPI(Protocol):
    redactor: Redactor

    async def start(self) -> None: ...

    async def close(self) -> None: ...

    async def execute(self, action: WebAction, context: RunContext) -> ActionResult: ...

    async def observe(self) -> PageObservation: ...

    async def evidence(self) -> BrowserEvidence: ...

    async def screenshot(self, path: Path) -> None: ...


@runtime_checkable
class AuthenticationBrowserAPI(BrowserAPI, Protocol):
    async def recover_authentication(self, context: RunContext) -> ActionResult: ...


@runtime_checkable
class ResourceLedgerBrowserAPI(BrowserAPI, Protocol):
    def bind_resource_ledger(self, listener: ResourceLedgerListener) -> None: ...


@runtime_checkable
class CleanupBrowserAPI(BrowserAPI, Protocol):
    async def cleanup_resource(
        self, plan: "CleanupPlan", record: ResourceRecord, context: RunContext
    ) -> ActionResult: ...


@runtime_checkable
class JsonCreateBrowserAPI(ResourceLedgerBrowserAPI, Protocol):
    async def create_json_resource(
        self,
        operation: "JsonCreateOperation",
        payload: dict[str, object],
        resource_name: str,
        resource_locator: str,
        idempotency_key: str,
        context: RunContext,
    ) -> ActionResult: ...


@runtime_checkable
class ApiCleanupBrowserAPI(BrowserAPI, Protocol):
    async def cleanup_api_resource(
        self, plan: "ApiCleanupPlan", record: ResourceRecord, context: RunContext
    ) -> ActionResult: ...
