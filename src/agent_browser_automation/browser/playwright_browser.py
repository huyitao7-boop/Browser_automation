import asyncio
import re
from collections.abc import Mapping
from fnmatch import fnmatch
from pathlib import Path
from typing import Literal, cast
from urllib.parse import quote, urljoin, urlsplit

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Locator

from agent_browser_automation.shared.classifications import ErrorClassification
from agent_browser_automation.shared.errors import AutomationException, automation_error
from agent_browser_automation.shared.redaction import DEFAULT_REDACTOR, Redactor
from agent_browser_automation.shared.resource_ledger import (
    ResourceLedger,
    ResourceLedgerListener,
    ResourceRecord,
    ResourceState,
)
from agent_browser_automation.shared.run_context import (
    RiskLevel,
    RunContext,
    WriteAuthorization,
    normalized_origin,
)
from agent_browser_automation.shared.secrets import (
    SecretProvider,
    SecretRef,
    SecretResolver,
    UnavailableSecretProvider,
)
from agent_browser_automation.workflow.action import (
    AdvanceAction,
    AssertAction,
    Assertion,
    ClickAction,
    ExploreClickAction,
    FillAction,
    FillTextAction,
    NavigateAction,
    ObserveAction,
    ScrollAction,
    SelectAction,
    SelectComboboxAction,
    SelectRadioAction,
    SimpleSubmitAction,
    SubmitAction,
    TargetSpec,
    WebAction,
)
from agent_browser_automation.workflow.result import (
    ActionResult,
    BrowserEvidence,
    InteractiveControl,
    PageObservation,
)

from .api_operations import ApiCleanupPlan, JsonCreateOperation
from .auth import (
    AuthCredentialField,
    AuthObservation,
    AuthObserver,
    AuthPolicy,
    AuthRecoveryPlan,
    AuthState,
)
from .cleanup import CleanupPlan
from .controls import ControlHandlerRegistry
from .controls.delete import CleanupDeleteHandler
from .session import BrowserSession
from .target_resolver import TargetResolver


class PlaywrightBrowser:
    def __init__(
        self,
        session: BrowserSession,
        redactor: Redactor | None = None,
        control_registry: ControlHandlerRegistry | None = None,
        auth_policy: AuthPolicy | None = None,
        auth_recovery: AuthRecoveryPlan | None = None,
        auth_observer: AuthObserver | None = None,
        secret_provider: SecretProvider | None = None,
        resource_ledger: ResourceLedger | None = None,
    ) -> None:
        self.session = session
        self.resolver = TargetResolver()
        self.redactor = redactor or session.redactor
        self.control_registry = control_registry or ControlHandlerRegistry()
        self.auth_policy = auth_policy
        self.auth_recovery = auth_recovery
        self.auth_observer = auth_observer or AuthObserver(self.resolver, self.redactor)
        self.secrets = SecretResolver(
            secret_provider or UnavailableSecretProvider(), self.redactor
        )
        self.resource_ledger = resource_ledger or ResourceLedger()
        self.cleanup_delete_handler = CleanupDeleteHandler()

    async def recover_authentication(self, context: RunContext) -> ActionResult:
        page = self.session.require_page()
        async with self.session.action_lock:
            before = self.session.revision
            try:
                policy = self.auth_policy
                recovery = self.auth_recovery
                if policy is None or recovery is None:
                    raise automation_error(
                        ErrorClassification.POLICY_DENIED,
                        "browser session has no trusted authentication recovery policy",
                    )
                if not self.session.auth_frozen:
                    raise automation_error(
                        ErrorClassification.INTERACTION_DENIED,
                        "authentication recovery is only allowed for a frozen session",
                        auth_state=self.session.auth_state.value,
                    )
                if context.max_risk != RiskLevel.AUTHENTICATION:
                    raise automation_error(
                        ErrorClassification.INTERACTION_DENIED,
                        "run context does not authorize authentication recovery",
                        max_risk=context.max_risk.value,
                    )

                page.set_default_timeout(context.action_timeout_ms)
                page.set_default_navigation_timeout(context.navigation_timeout_ms)
                self._check_origin(recovery.login_url, context)
                await page.goto(recovery.login_url, wait_until="domcontentloaded")
                self._check_origin(page.url, context)
                self.session.revision += 1

                observation = await self._inspect_auth()
                if observation is None:
                    raise automation_error(
                        ErrorClassification.POLICY_DENIED,
                        "authentication observer is unavailable for recovery",
                    )
                if observation.state != AuthState.AUTHENTICATED:
                    if recovery.entry_target is not None:
                        await self._submit_authentication(
                            recovery.entry_target, context
                        )
                    for credential in recovery.credentials:
                        await self._fill_auth_credential(credential)
                    await self._submit_authentication(recovery.submit_target, context)
                    observation = await self._wait_for_authenticated(recovery)

                if observation.state != AuthState.AUTHENTICATED:
                    raise automation_error(
                        ErrorClassification.AUTHENTICATION_REQUIRED,
                        "authentication recovery did not verify an authenticated state",
                        policy_id=policy.id,
                        auth_state=observation.state.value,
                        reason=observation.reason,
                    )
                self.session.complete_auth_recovery(observation)
                return ActionResult(
                    status="passed",
                    action_type="auth_recover",
                    revision_before=before,
                    revision_after=self.session.revision,
                    current_url=self.redactor.sanitize_url(page.url),
                    observation=await self.observe(),
                )
            except AutomationException as exc:
                return self._failed("auth_recover", before, page.url, exc)
            except PlaywrightError as exc:
                wrapped = automation_error(
                    ErrorClassification.BROWSER_ERROR,
                    "Playwright failed during authentication recovery",
                    playwright_message=self.redactor.redact_text(str(exc), 2_000),
                )
                return self._failed("auth_recover", before, page.url, wrapped)

    async def start(self) -> None:
        await self.session.start()

    def bind_resource_ledger(self, listener: ResourceLedgerListener) -> None:
        self.resource_ledger.set_listener(listener)

    async def create_json_resource(
        self,
        operation: JsonCreateOperation,
        payload: dict[str, object],
        resource_name: str,
        resource_locator: str,
        idempotency_key: str,
        context: RunContext,
    ) -> ActionResult:
        page = self.session.require_page()
        async with self.session.action_lock:
            before = self.session.revision
            authorization = self._require_test_write(RiskLevel.TEST_WRITE, context)
            if (
                authorization.capability != operation.capability
                or authorization.resource_name != resource_name
            ):
                return self._failed(
                    "json_create",
                    before,
                    page.url,
                    automation_error(
                        ErrorClassification.POLICY_DENIED,
                        "JSON create does not match the trusted authorization",
                    ),
                )
            self._require_interaction_unfrozen()
            await self._inspect_auth()
            self._require_interaction_unfrozen()
            verification_url = operation.get_by_name_url_template.replace(
                "{resource_locator}", quote(resource_locator, safe="")
            )
            self._check_origin(operation.create_url, context)
            self._check_origin(verification_url, context)
            try:
                headers = await self._api_authorization_headers(
                    operation.authorization_secret_ref
                )
            except AutomationException as exc:
                return self._failed("json_create", before, page.url, exc)
            self.resource_ledger.begin(
                idempotency_key=idempotency_key,
                operation="create",
                capability=operation.capability,
                resource_kind=operation.resource_kind,
                resource_name=resource_name,
                resource_locator=resource_locator,
                environment_id=authorization.environment_id,
                policy_id=authorization.policy_id,
            )
            try:
                browser_context = self.session.context
                if browser_context is None:
                    raise automation_error(
                        ErrorClassification.BROWSER_ERROR,
                        "browser context is unavailable for JSON create",
                    )
                response = await browser_context.request.post(
                    operation.create_url, data=payload, headers=headers
                )
                self.session.revision += 1
                if response.status not in {200, 201}:
                    raise automation_error(
                        ErrorClassification.BROWSER_ERROR,
                        "JSON create returned a non-success status",
                        status=response.status,
                    )
                self.resource_ledger.transition(
                    idempotency_key, ResourceState.SUBMITTED
                )
                verification = await browser_context.request.get(
                    verification_url, headers=headers
                )
                if verification.status != 200:
                    raise automation_error(
                        ErrorClassification.ASSERTION_FAILED,
                        "created resource was not found by its exact locator",
                        status=verification.status,
                    )
                raw_value = await verification.json()
                if not isinstance(raw_value, Mapping):
                    raise automation_error(
                        ErrorClassification.ASSERTION_FAILED,
                        "created resource verification did not return an object",
                    )
                raw = cast(Mapping[str, object], raw_value)
                actual_name = raw.get("name")
                actual_locator = raw.get("fullyQualifiedName")
                if actual_name != resource_name or actual_locator != resource_locator:
                    raise automation_error(
                        ErrorClassification.ASSERTION_FAILED,
                        "created resource identity does not match the request",
                    )
                await self._inspect_auth()
                self._require_interaction_unfrozen()
                record = self.resource_ledger.transition(
                    idempotency_key, ResourceState.VERIFIED
                )
                return ActionResult(
                    status="passed",
                    action_type="json_create",
                    revision_before=before,
                    revision_after=self.session.revision,
                    current_url=self.redactor.sanitize_url(page.url),
                    observed_value={
                        "id": (
                            raw.get("id")
                            if isinstance(raw.get("id"), str)
                            else None
                        ),
                        "name": actual_name,
                        "fullyQualifiedName": actual_locator,
                        "ledger_state": record.state.value,
                    },
                    observation=await self.observe(),
                )
            except AutomationException as exc:
                self.resource_ledger.transition(
                    idempotency_key, ResourceState.NEEDS_CLEANUP
                )
                return self._failed("json_create", before, page.url, exc)
            except (PlaywrightError, ValueError, TypeError) as exc:
                self.resource_ledger.transition(
                    idempotency_key, ResourceState.NEEDS_CLEANUP
                )
                return self._failed(
                    "json_create",
                    before,
                    page.url,
                    automation_error(
                        ErrorClassification.BROWSER_ERROR,
                        "JSON create request failed",
                        cause=self.redactor.redact_text(str(exc), 2_000),
                    ),
                )

    async def cleanup_api_resource(
        self, plan: ApiCleanupPlan, record: ResourceRecord, context: RunContext
    ) -> ActionResult:
        page = self.session.require_page()
        async with self.session.action_lock:
            before = self.session.revision
            try:
                self._require_interaction_unfrozen()
                await self._inspect_auth()
                self._require_interaction_unfrozen()
                if record.resource_locator is None:
                    raise automation_error(
                        ErrorClassification.POLICY_DENIED,
                        "API cleanup requires a ledger-bound resource locator",
                    )
                lookup_url = plan.lookup_url_template.replace(
                    "{resource_locator}", quote(record.resource_locator, safe="")
                )
                self._check_origin(lookup_url, context)
                browser_context = self.session.context
                if browser_context is None:
                    raise automation_error(
                        ErrorClassification.BROWSER_ERROR,
                        "browser context is unavailable for API cleanup",
                    )
                headers = await self._api_authorization_headers(
                    plan.authorization_secret_ref
                )
                lookup = await browser_context.request.get(lookup_url, headers=headers)
                if lookup.status == 404:
                    return ActionResult(
                        status="passed",
                        action_type="api_cleanup",
                        revision_before=before,
                        revision_after=self.session.revision,
                        current_url=self.redactor.sanitize_url(page.url),
                        observed_value="cleanup-not-needed",
                        observation=await self.observe(),
                    )
                if lookup.status != 200:
                    raise automation_error(
                        ErrorClassification.BROWSER_ERROR,
                        "API cleanup lookup returned an unsafe status",
                        status=lookup.status,
                    )
                raw_value = await lookup.json()
                if not isinstance(raw_value, Mapping):
                    raise automation_error(
                        ErrorClassification.ASSERTION_FAILED,
                        "API cleanup identity response is not an object",
                    )
                raw = cast(Mapping[str, object], raw_value)
                resource_id = raw.get("id")
                if (
                    not isinstance(resource_id, str)
                    or raw.get("fullyQualifiedName") != record.resource_locator
                    or raw.get("name") != record.resource_name
                ):
                    raise automation_error(
                        ErrorClassification.POLICY_DENIED,
                        "API cleanup resource identity does not match the ledger",
                    )
                delete_url = plan.delete_url_template.replace(
                    "{resource_id}", quote(resource_id, safe="")
                )
                self._check_origin(delete_url, context)
                deleted = await browser_context.request.delete(
                    delete_url, headers=headers
                )
                self.session.revision += 1
                if deleted.status not in {200, 204}:
                    raise automation_error(
                        ErrorClassification.BROWSER_ERROR,
                        "API cleanup delete returned a non-success status",
                        status=deleted.status,
                    )
                await asyncio.sleep(plan.absence_stability_ms / 1000)
                absent = await browser_context.request.get(lookup_url, headers=headers)
                if absent.status != 404:
                    raise automation_error(
                        ErrorClassification.ASSERTION_FAILED,
                        "API cleanup did not prove stable resource absence",
                        status=absent.status,
                    )
                await self._inspect_auth()
                self._require_interaction_unfrozen()
                return ActionResult(
                    status="passed",
                    action_type="api_cleanup",
                    revision_before=before,
                    revision_after=self.session.revision,
                    current_url=self.redactor.sanitize_url(page.url),
                    observed_value="cleaned",
                    observation=await self.observe(),
                )
            except AutomationException as exc:
                return self._failed("api_cleanup", before, page.url, exc)
            except (PlaywrightError, ValueError, TypeError) as exc:
                return self._failed(
                    "api_cleanup",
                    before,
                    page.url,
                    automation_error(
                        ErrorClassification.BROWSER_ERROR,
                        "API cleanup request failed",
                        cause=self.redactor.redact_text(str(exc), 2_000),
                    ),
                )

    async def _api_authorization_headers(
        self, secret_ref: SecretRef | None
    ) -> dict[str, str] | None:
        if secret_ref is None:
            return None
        secret = await self.secrets.resolve(secret_ref)
        return {"Authorization": f"Bearer {secret.reveal()}"}

    async def cleanup_resource(
        self, plan: CleanupPlan, record: ResourceRecord, context: RunContext
    ) -> ActionResult:
        page = self.session.require_page()
        async with self.session.action_lock:
            before = self.session.revision
            try:
                if context.max_risk != RiskLevel.DELETE:
                    raise automation_error(
                        ErrorClassification.INTERACTION_DENIED,
                        "cleanup requires an explicit delete-risk context",
                    )
                self._require_interaction_unfrozen()
                await self._inspect_auth()
                self._require_interaction_unfrozen()
                resource_url = plan.resource_url(record.resource_name)
                self._check_origin(resource_url, context)
                response = await page.goto(resource_url, wait_until="domcontentloaded")
                self._check_origin(page.url, context)
                self.session.revision += 1
                await self._inspect_auth()
                self._require_interaction_unfrozen()
                status = response.status if response is not None else None
                if status in {401, 403} or (status is not None and status >= 500):
                    raise automation_error(
                        ErrorClassification.BROWSER_ERROR,
                        "cleanup resource lookup returned an unsafe HTTP status",
                        status=status,
                    )
                identity = plan.resolve_target(
                    plan.identity_target, record.resource_name
                )
                if await self.resolver.visible_count(page, identity) == 0:
                    return ActionResult(
                        status="passed",
                        action_type="cleanup",
                        revision_before=before,
                        revision_after=self.session.revision,
                        current_url=self.redactor.sanitize_url(page.url),
                        observed_value="cleanup-not-needed",
                        observation=await self.observe(),
                    )
                await self.resolver.resolve(page, identity)
                delete_locator = await self.resolver.resolve(page, plan.delete_target)
                await self.cleanup_delete_handler.validate(
                    delete_locator, plan.delete_target.expected_control
                )
                await delete_locator.click()
                self.session.revision += 1
                if plan.confirmation_target is not None:
                    assert plan.confirmation_submit_target is not None
                    confirmation = await self.resolver.resolve(
                        page, plan.confirmation_target
                    )
                    handler, _ = await self.control_registry.prepare_fill_text(
                        confirmation, plan.confirmation_target.expected_control
                    )
                    await handler.fill(confirmation, record.resource_name)
                    self.session.revision += 1
                    submit = await self.resolver.resolve(
                        page, plan.confirmation_submit_target
                    )
                    submit_handler, _ = await self.control_registry.prepare_submit(
                        submit, plan.confirmation_submit_target.expected_control
                    )
                    await submit_handler.submit(submit)
                    self.session.revision += 1
                self._check_origin(page.url, context)
                await self._inspect_auth()
                self._require_interaction_unfrozen()
                self._check_origin(plan.absence_url, context)
                absence_response = await page.goto(
                    plan.absence_url, wait_until="domcontentloaded"
                )
                self.session.revision += 1
                self._check_origin(page.url, context)
                await self._inspect_auth()
                self._require_interaction_unfrozen()
                absence_status = (
                    absence_response.status if absence_response is not None else None
                )
                if absence_status is not None and absence_status >= 400:
                    raise automation_error(
                        ErrorClassification.BROWSER_ERROR,
                        "cleanup verification page returned an unsafe HTTP status",
                        status=absence_status,
                    )
                absence = plan.resolve_target(plan.absence_target, record.resource_name)
                await self._assert(
                    AssertAction(
                        assertion=Assertion(
                            type="target_not_visible",
                            target=absence,
                            stability_ms=plan.absence_stability_ms,
                        )
                    )
                )
                return ActionResult(
                    status="passed",
                    action_type="cleanup",
                    revision_before=before,
                    revision_after=self.session.revision,
                    current_url=self.redactor.sanitize_url(page.url),
                    observed_value="cleaned",
                    observation=await self.observe(),
                )
            except AutomationException as exc:
                return self._failed("cleanup", before, page.url, exc)
            except PlaywrightError as exc:
                wrapped = automation_error(
                    ErrorClassification.BROWSER_ERROR,
                    "Playwright failed during resource cleanup",
                    playwright_message=self.redactor.redact_text(str(exc), 2_000),
                )
                return self._failed("cleanup", before, page.url, wrapped)

    async def close(self) -> None:
        await self.session.close()

    async def execute(self, action: WebAction, context: RunContext) -> ActionResult:
        page = self.session.require_page()
        async with self.session.action_lock:
            before = self.session.revision
            was_frozen = self.session.auth_frozen
            try:
                if isinstance(
                    action,
                    (
                        ClickAction,
                        ExploreClickAction,
                        AdvanceAction,
                        FillAction,
                        FillTextAction,
                        SelectAction,
                        SelectComboboxAction,
                        SelectRadioAction,
                        SubmitAction,
                        SimpleSubmitAction,
                    ),
                ):
                    self._require_interaction_unfrozen()
                    await self._inspect_auth()
                    self._require_interaction_unfrozen()
                if action.expected_revision is not None and action.expected_revision != before:
                    raise automation_error(
                        ErrorClassification.STALE_REFERENCE,
                        "action revision does not match the current page revision",
                        expected=action.expected_revision,
                        actual=before,
                    )
                page.set_default_timeout(context.action_timeout_ms)
                page.set_default_navigation_timeout(context.navigation_timeout_ms)

                if isinstance(action, NavigateAction):
                    self._check_origin(action.url, context)
                    await page.goto(action.url, wait_until="domcontentloaded")
                    self._check_origin(page.url, context)
                    self.session.revision += 1
                elif isinstance(action, ObserveAction):
                    pass
                elif isinstance(action, ScrollAction):
                    self._check_origin(page.url, context)
                    await page.mouse.wheel(0, 600 if action.direction == "down" else -600)
                    self.session.revision += 1
                elif isinstance(action, AssertAction):
                    await self._assert(action)
                elif isinstance(action, ClickAction):
                    await self._click(action, context)
                elif isinstance(action, ExploreClickAction):
                    await self._explore_click(action, context)
                elif isinstance(action, AdvanceAction):
                    await self._advance(action, context)
                elif isinstance(action, FillTextAction):
                    await self._fill_text(action, context)
                elif isinstance(action, SelectAction):
                    await self._select(action, context)
                elif isinstance(action, SelectComboboxAction):
                    await self._select_combobox(action, context)
                elif isinstance(action, SelectRadioAction):
                    await self._select_radio(action, context)
                elif isinstance(action, SubmitAction):
                    record = await self._submit_test_write(action, context)
                    observation = await self._observe_with_timeout(context)
                    return ActionResult(
                        status="passed",
                        action_type=action.type,
                        revision_before=before,
                        revision_after=self.session.revision,
                        current_url=self.redactor.sanitize_url(page.url),
                        observed_value=record.model_dump(mode="json"),
                        observation=observation,
                    )
                elif isinstance(action, SimpleSubmitAction):
                    await self._simple_submit(action, context)
                else:
                    raise automation_error(
                        ErrorClassification.UNSAFE_ACTION,
                        "the minimal runner only permits readonly actions",
                        action_type=action.type,
                    )

                auth = await self._inspect_auth()
                if (
                    auth is not None
                    and auth.state
                    in {AuthState.AUTHENTICATION_REQUIRED, AuthState.INTERRUPTED}
                    and not was_frozen
                ):
                    raise self._authentication_required(auth)

                observation = await self._observe_with_timeout(context)
                return ActionResult(
                    status="passed",
                    action_type=action.type,
                    revision_before=before,
                    revision_after=self.session.revision,
                    current_url=self.redactor.sanitize_url(page.url),
                    observation=observation,
                )
            except AutomationException as exc:
                return self._failed(action.type, before, page.url, exc)
            except PlaywrightError as exc:
                wrapped = automation_error(
                    ErrorClassification.BROWSER_ERROR,
                    "Playwright failed to execute the browser action",
                    playwright_message=self.redactor.redact_text(str(exc), 2_000),
                )
                return self._failed(action.type, before, page.url, wrapped)

    async def _observe_with_timeout(self, context: RunContext) -> PageObservation:
        try:
            async with asyncio.timeout(context.action_timeout_ms / 1_000):
                return await self.observe()
        except TimeoutError:
            raise automation_error(
                ErrorClassification.BROWSER_ERROR,
                "page observation exceeded the configured action timeout",
                action_timeout_ms=context.action_timeout_ms,
            ) from None

    async def _inspect_auth(self) -> AuthObservation | None:
        if self.auth_policy is None:
            return None
        page = self.session.require_page()
        observation = await self.auth_observer.inspect(
            page, self.auth_policy, self.session.auth_state
        )
        self.session.record_auth(observation)
        return observation

    def _require_interaction_unfrozen(self) -> None:
        if not self.session.auth_frozen:
            return
        raise automation_error(
            ErrorClassification.AUTHENTICATION_REQUIRED,
            "browser session is frozen because authentication is required",
            auth_state=self.session.auth_state.value,
            reason=self.session.auth_reason,
            policy_id=self.auth_policy.id if self.auth_policy is not None else None,
        )

    @staticmethod
    def _authentication_required(observation: AuthObservation) -> AutomationException:
        return automation_error(
            ErrorClassification.AUTHENTICATION_REQUIRED,
            "browser authentication is required",
            auth_state=observation.state.value,
            reason=observation.reason,
            policy_id=observation.policy_id,
            matched_signal=observation.matched_signal,
        )

    async def _click(self, action: ClickAction, context: RunContext) -> None:
        page = self.session.require_page()
        self._check_origin(page.url, context)
        if action.risk == RiskLevel.READONLY:
            if context.max_risk != RiskLevel.READONLY:
                raise automation_error(
                    ErrorClassification.INTERACTION_DENIED,
                    "run context does not authorize readonly link navigation",
                    max_risk=context.max_risk.value,
                )
            if action.target is None:
                raise automation_error(
                    ErrorClassification.INTERACTION_DENIED,
                    "unresolved targetRef reached Browser Runtime",
                )
            locator = await self.resolver.resolve(page, action.target)
            handler, facts = await self.control_registry.prepare_click(
                locator, action.target.expected_control
            )
            if not context.allow_session_interactions and facts.href is None:
                raise automation_error(
                    ErrorClassification.INTERACTION_DENIED,
                    "readonly link navigation requires an href",
                )
            if facts.href is not None:
                self._check_origin(urljoin(page.url, facts.href), context)
            await handler.click(locator)
            self._check_origin(page.url, context)
            self.session.revision += 1
            return
        if action.risk != RiskLevel.AUTHENTICATION:
            raise automation_error(
                ErrorClassification.INTERACTION_DENIED,
                "the minimal click runtime only accepts authentication interactions",
                action_risk=action.risk.value,
            )
        if context.max_risk != RiskLevel.AUTHENTICATION:
            raise automation_error(
                ErrorClassification.INTERACTION_DENIED,
                "run context does not explicitly authorize authentication interaction",
                max_risk=context.max_risk.value,
            )
        if action.target is None:
            raise automation_error(
                ErrorClassification.WORKFLOW_INVALID,
                "unresolved targetRef reached Browser Runtime",
            )
        locator = await self.resolver.resolve(page, action.target)
        handler, facts = await self.control_registry.prepare_click(
            locator, action.target.expected_control
        )
        if facts.href is not None:
            self._check_origin(urljoin(page.url, facts.href), context)
        await handler.click(locator)
        self.session.revision += 1
        self._check_origin(page.url, context)

    async def _fill_auth_credential(self, credential: AuthCredentialField) -> None:
        page = self.session.require_page()
        locator = await self.resolver.resolve(page, credential.target)
        tag_name = str(
            await locator.evaluate("element => element.tagName.toLowerCase()")
        )
        input_type = (await locator.get_attribute("type") or "text").lower()
        allowed_types = (
            {"password"} if credential.kind == "password" else {"text", "email"}
        )
        if tag_name != "input" or input_type not in allowed_types:
            raise automation_error(
                ErrorClassification.INTERACTION_DENIED,
                "authentication credential target is not an allowed input",
                credential_kind=credential.kind,
                tag_name=tag_name,
                input_type=input_type,
            )
        if not await locator.is_editable():
            raise automation_error(
                ErrorClassification.INTERACTION_DENIED,
                "authentication credential target is not editable",
                credential_kind=credential.kind,
            )
        value = await self.secrets.resolve(credential.secret_ref)
        await locator.fill(value.reveal())
        self.session.revision += 1

    async def _fill_text(self, action: FillTextAction, context: RunContext) -> None:
        self._require_form_action(action.risk, context)
        if action.target is None:
            raise automation_error(
                ErrorClassification.WORKFLOW_INVALID,
                "unresolved targetRef reached Browser Runtime",
            )
        page = self.session.require_page()
        self._check_origin(page.url, context)
        locator = await self.resolver.resolve(page, action.target)
        handler, _ = await self.control_registry.prepare_fill_text(
            locator, action.target.expected_control
        )
        await handler.fill(locator, action.value)
        self.session.revision += 1

    async def _explore_click(
        self, action: ExploreClickAction, context: RunContext
    ) -> None:
        authorization = self._require_test_write(action.risk, context)
        page = self.session.require_page()
        self._check_origin(page.url, context)
        locator = await self.resolver.resolve(page, action.target)
        facts = await self.control_registry.prepare_exploration_click(
            locator,
            action.target.expected_control,
            authorization.allowed_setup_controls,
        )
        if facts.href is not None:
            self._check_origin(urljoin(page.url, facts.href), context)
        await locator.click()
        self.session.revision += 1
        self._check_origin(page.url, context)

    async def _advance(self, action: AdvanceAction, context: RunContext) -> None:
        self._require_test_write(action.risk, context)
        page = self.session.require_page()
        self._check_origin(page.url, context)
        locator = await self.resolver.resolve(page, action.target)
        await locator.click()
        self.session.revision += 1
        self._check_origin(page.url, context)

    async def _select(self, action: SelectAction, context: RunContext) -> None:
        self._require_form_action(action.risk, context)
        if action.target is None:
            raise automation_error(
                ErrorClassification.WORKFLOW_INVALID,
                "unresolved targetRef reached Browser Runtime",
            )
        page = self.session.require_page()
        self._check_origin(page.url, context)
        locator = await self.resolver.resolve(page, action.target)
        handler, _ = await self.control_registry.prepare_select(
            locator, action.target.expected_control
        )
        await handler.select(locator, action.value)
        self.session.revision += 1

    async def _select_radio(self, action: SelectRadioAction, context: RunContext) -> None:
        self._require_form_action(action.risk, context)
        page = self.session.require_page()
        self._check_origin(page.url, context)
        locator = await self.resolver.resolve(page, action.target)
        handler, _ = await self.control_registry.prepare_radio(
            locator, action.target.expected_control
        )
        await handler.select(locator)
        self.session.revision += 1

    async def _select_combobox(
        self, action: SelectComboboxAction, context: RunContext
    ) -> None:
        self._require_form_action(action.risk, context)
        page = self.session.require_page()
        self._check_origin(page.url, context)
        locator = await self.resolver.resolve(page, action.target)
        handler, _ = await self.control_registry.prepare_combobox(
            locator, action.target.expected_control
        )
        await handler.open(locator)
        options = page.get_by_role("option", name=action.value, exact=True).filter(visible=True)
        count = await options.count()
        if count != 1:
            classification = (
                ErrorClassification.TARGET_NOT_FOUND
                if count == 0
                else ErrorClassification.TARGET_AMBIGUOUS
            )
            raise automation_error(
                classification,
                "combobox option must have one visible exact match",
                option=action.value,
                count=count,
            )
        await options.click()
        self.session.revision += 1

    async def _submit_test_write(
        self, action: SubmitAction, context: RunContext
    ) -> ResourceRecord:
        authorization = self._require_test_write(action.risk, context)
        if (
            authorization.capability != action.intent.capability
            or authorization.operation != action.intent.operation
            or authorization.resource_name != action.intent.resource_name
        ):
            raise automation_error(
                ErrorClassification.POLICY_DENIED,
                "write intent does not match the trusted authorization",
                policy_id=authorization.policy_id,
            )
        if action.target is None:
            raise automation_error(
                ErrorClassification.WORKFLOW_INVALID,
                "unresolved targetRef reached Browser Runtime",
            )
        page = self.session.require_page()
        self._check_origin(page.url, context)
        locator = await self.resolver.resolve(page, action.target)
        if action.demo and authorization.demo_allow_observed_controls:
            handler, _ = await self.control_registry.prepare_submit(
                locator, action.target.expected_control
            )
        else:
            handler, _ = await self.control_registry.prepare_declared_button_submit(
                locator,
                action.target.expected_control,
                authorization.allowed_submit_controls,
            )
        form_action = await locator.evaluate(
            "element => element.form ? element.form.action : null"
        )
        if isinstance(form_action, str) and form_action:
            self._check_origin(form_action, context)
        self.resource_ledger.begin(
            **action.intent.model_dump(mode="python"),
            environment_id=authorization.environment_id,
            policy_id=authorization.policy_id,
        )
        try:
            await handler.submit(locator)
            self.session.revision += 1
            self._check_origin(page.url, context)
            self.resource_ledger.transition(
                action.intent.idempotency_key, ResourceState.SUBMITTED
            )
            await self._wait_for_assertion(action.verification, context.action_timeout_ms)
            return self.resource_ledger.transition(
                action.intent.idempotency_key, ResourceState.VERIFIED
            )
        except Exception:
            self.resource_ledger.transition(
                action.intent.idempotency_key, ResourceState.NEEDS_CLEANUP
            )
            raise

    async def _simple_submit(self, action: SimpleSubmitAction, context: RunContext) -> None:
        self._require_form_action(action.risk, context)
        page = self.session.require_page()
        self._check_origin(page.url, context)
        locator = await self.resolver.resolve(page, action.target)
        handler, _ = await self.control_registry.prepare_submit(
            locator, action.target.expected_control
        )
        form_action = await locator.evaluate(
            "element => element.form ? element.form.action : null"
        )
        if isinstance(form_action, str) and form_action:
            self._check_origin(form_action, context)
        await handler.submit(locator)
        # A click resolves before many client-side submit handlers have committed
        # their DOM update. Yield one rendered frame before returning control to
        # the caller, which will usually observe or assert the page immediately.
        await page.wait_for_timeout(50)
        self.session.revision += 1
        self._check_origin(page.url, context)

    def _require_form_action(self, risk: RiskLevel, context: RunContext) -> None:
        if (
            context.allow_session_interactions
            and risk == RiskLevel.READONLY
            and context.max_risk == RiskLevel.READONLY
        ):
            return
        if context.allow_local_form_actions:
            page = self.session.require_page()
            host = urlsplit(page.url).hostname
            if (
                risk == RiskLevel.READONLY
                and context.max_risk == RiskLevel.READONLY
                and host in {"127.0.0.1", "localhost", "::1"}
            ):
                return
            raise automation_error(
                ErrorClassification.INTERACTION_DENIED,
                "simple form actions are limited to the loopback test page",
            )
        self._require_test_write(risk, context)

    @staticmethod
    def _require_test_write(
        risk: RiskLevel, context: RunContext
    ) -> "WriteAuthorization":
        if risk not in {RiskLevel.TEST_WRITE, RiskLevel.DELETE} or context.max_risk != risk:
            raise automation_error(
                ErrorClassification.INTERACTION_DENIED,
                "test-write action requires an explicit test-write context",
                action_risk=risk.value,
                max_risk=context.max_risk.value,
            )
        authorization = context.write_authorization
        if authorization is None:
            raise automation_error(
                ErrorClassification.POLICY_DENIED,
                "test-write context has no trusted environment authorization",
            )
        if not authorization.authenticated:
            raise automation_error(
                ErrorClassification.AUTHENTICATION_REQUIRED,
                "test-write authorization has no authenticated-session attestation",
                policy_id=authorization.policy_id,
            )
        return authorization

    async def _submit_authentication(
        self, target: TargetSpec, context: RunContext
    ) -> None:
        page = self.session.require_page()
        self._check_origin(page.url, context)
        locator = await self.resolver.wait_for_unique_visible(
            page, target, context.action_timeout_ms
        )
        await self._validate_auth_submit(locator, context)
        await locator.click()
        self.session.revision += 1
        self._check_origin(page.url, context)

    async def _validate_auth_submit(
        self, locator: Locator, context: RunContext
    ) -> None:
        tag_name = str(
            await locator.evaluate("element => element.tagName.toLowerCase()")
        )
        control_type = (await locator.get_attribute("type") or "submit").lower()
        if tag_name not in {"button", "input"} or control_type not in {
            "button",
            "submit",
        }:
            raise automation_error(
                ErrorClassification.INTERACTION_DENIED,
                "authentication submit target is not a button",
                tag_name=tag_name,
                control_type=control_type,
            )
        if await locator.is_disabled():
            raise automation_error(
                ErrorClassification.INTERACTION_DENIED,
                "disabled authentication controls cannot be submitted",
            )
        aria_label = await locator.get_attribute("aria-label")
        title = await locator.get_attribute("title")
        value = await locator.get_attribute("value")
        text = (await locator.inner_text()).strip()
        accessible_name = next(
            (
                item.strip()
                for item in (aria_label, text, title, value)
                if item is not None and item.strip()
            ),
            "",
        )
        if not re.search(
            r"\b(sign[ -]?in|log[ -]?in|continue|authenticate)\b|登录|登入|继续",
            accessible_name,
            re.IGNORECASE,
        ):
            raise automation_error(
                ErrorClassification.INTERACTION_DENIED,
                "authentication submit target does not have login semantics",
                accessible_name=accessible_name[:500],
            )
        form_action = await locator.evaluate(
            "element => element.form ? element.form.action : null"
        )
        if isinstance(form_action, str) and form_action:
            self._check_origin(form_action, context)

    async def _wait_for_authenticated(
        self, recovery: AuthRecoveryPlan
    ) -> AuthObservation:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + recovery.verification_timeout_ms / 1_000
        observation = await self._inspect_auth()
        assert observation is not None
        while observation.state != AuthState.AUTHENTICATED and loop.time() < deadline:
            await asyncio.sleep(
                min(recovery.poll_interval_ms / 1_000, max(0.0, deadline - loop.time()))
            )
            observation = await self._inspect_auth()
            assert observation is not None
        return observation

    async def observe(self) -> PageObservation:
        page = self.session.require_page()
        title = await page.title()
        body = page.locator("body")
        text = await body.inner_text() if await body.count() else ""
        headings = await page.locator("h1, h2, h3").all_inner_texts()
        landmarks = await page.locator(
            "main, nav, aside, header, footer, [role=main], [role=navigation]"
        ).evaluate_all(
            "elements => elements.map((element) => "
            "element.getAttribute('role') || element.tagName.toLowerCase())"
        )
        raw_controls = cast(
            list[dict[str, object]],
            await page.locator(
                "a[href], button, input, textarea, select, [contenteditable], "
                "[role=button], [role=link], [role=textbox], [role=combobox], "
                "[role=radio], [role=menuitem], [aria-haspopup], [data-testid], "
                "[tabindex], [onclick], [style*='cursor']"
            ).evaluate_all(
                """elements => {
                  const namedBy = element => (element.getAttribute('aria-labelledby') || '')
                    .split(/\\s+/).filter(Boolean)
                    .map(id => document.getElementById(id)?.textContent?.trim() || '')
                    .filter(Boolean).join(' ');
                  const nameOf = element => {
                    const type = (element.getAttribute('type') || '').toLowerCase();
                    const labels = element.labels
                      ? Array.from(element.labels).map(label => label.textContent?.trim() || '')
                        .filter(Boolean).join(' ')
                      : '';
                    return (element.getAttribute('aria-label') || namedBy(element) || labels ||
                      element.getAttribute('title') ||
                      ((element.tagName === 'INPUT' && ['button', 'submit', 'reset'].includes(type))
                        ? (element.getAttribute('value') || '')
                        : '') || element.textContent?.trim() || '').replace(/\\s+/g, ' ').trim();
                  };
                  return elements.map(element => {
                    const tag = element.tagName.toLowerCase();
                    const explicitRole = element.getAttribute('role');
                    const type = (element.getAttribute('type') || '').toLowerCase();
                    const sensitive = type === 'password' || type === 'hidden' ||
                      element.getAttribute('data-sensitive') === 'true' ||
                      ['current-password', 'new-password']
                        .includes(element.getAttribute('autocomplete'));
                    const style = window.getComputedStyle(element);
                    const visible = !sensitive && style.display !== 'none' &&
                      style.visibility !== 'hidden' &&
                      style.opacity !== '0' && element.getClientRects().length > 0;
                    let expectedControl = null;
                    let role = explicitRole || '';
                    const testId = element.getAttribute('data-testid');
                    const name = nameOf(element);
                    if (explicitRole === 'menuitem') {
                      expectedControl = 'menu_item'; role ||= 'menuitem';
                    } else if (type === 'radio' || explicitRole === 'radio') {
                      expectedControl = 'radio'; role ||= 'radio';
                    } else if (element.getAttribute('contenteditable') &&
                      element.getAttribute('contenteditable') !== 'false') {
                      expectedControl = 'richtextbox'; role ||= 'textbox';
                    } else if (tag === 'a' && element.getAttribute('href')) {
                      expectedControl = 'link'; role ||= 'link';
                    } else if ((explicitRole === 'button' || tag === 'button') &&
                      element.getAttribute('aria-haspopup')) {
                      expectedControl = 'menu_button'; role ||= 'button';
                    } else if (tag === 'button' || explicitRole === 'button' ||
                      (tag === 'input' && ['button', 'submit', 'reset'].includes(type))) {
                      expectedControl = 'button'; role ||= 'button';
                    } else if (tag === 'select' || explicitRole === 'combobox') {
                      expectedControl = 'select'; role ||= 'combobox';
                    } else if (tag === 'textarea' || explicitRole === 'textbox' ||
                      (tag === 'input' && ![
                        'button', 'submit', 'reset', 'checkbox', 'radio'
                      ].includes(type))) {
                      expectedControl = 'textbox'; role ||= 'textbox';
                    } else if (testId || element.hasAttribute('tabindex') ||
                      element.hasAttribute('onclick') || style.cursor === 'pointer') {
                      expectedControl = 'clickable'; role ||= 'button';
                    }
                    let region = 'page';
                    if (element.closest('[role=dialog], dialog')) region = 'dialog';
                    else if (element.closest('main, [role=main]')) region = 'main';
                    else if (element.closest('aside')) region = 'sidebar';
                    else if (element.closest('[role=alert], [role=status]')) region = 'toast';
                    return {
                      region,
                      role,
                      accessible_name: name || testId || '',
                      test_id: testId,
                      expected_control: expectedControl,
                      enabled: !element.disabled &&
                        element.getAttribute('aria-disabled') !== 'true',
                      visible,
                    };
                  }).filter(item => item.visible && item.enabled &&
                    item.expected_control && item.accessible_name);
                }"""
            ),
        )
        keys = [
            (
                str(item["region"]),
                str(item["role"]),
                str(item["accessible_name"]),
                str(item["expected_control"]),
                str(item["test_id"] or ""),
            )
            for item in raw_controls
        ]
        counts: dict[tuple[str, str, str, str, str], int] = {}
        for key in keys:
            counts[key] = counts.get(key, 0) + 1
        interactive_controls_total = len(counts)
        seen: set[tuple[str, str, str, str, str]] = set()
        controls: list[InteractiveControl] = []
        for raw, key in zip(raw_controls, keys, strict=True):
            if key in seen or len(controls) >= 200:
                continue
            seen.add(key)
            controls.append(
                InteractiveControl(
                    region=cast(
                        Literal["page", "main", "sidebar", "dialog", "overlay", "toast"],
                        key[0],
                    ),
                    role=key[1],
                    accessible_name=self.redactor.redact_text(key[2], 500),
                    expected_control=cast(
                        Literal[
                            "button",
                            "link",
                            "textbox",
                            "richtextbox",
                            "select",
                            "radio",
                            "menu_button",
                            "menu_item",
                            "clickable",
                        ],
                        key[3],
                    ),
                    enabled=bool(raw["enabled"]),
                    match_count=counts[key],
                    test_id=(str(raw["test_id"]) if raw["test_id"] else None),
                )
            )
        return PageObservation(
            revision=self.session.revision,
            url=self.redactor.sanitize_url(page.url),
            title=title,
            text_excerpt=text[:4_000],
            headings=tuple(item[:500] for item in headings[:100]),
            landmarks=tuple(str(item)[:100] for item in landmarks[:100]),
            interactive_controls=tuple(controls),
            interactive_controls_total=interactive_controls_total,
            interactive_controls_truncated=interactive_controls_total > len(controls),
            auth_state=(
                self.session.auth_state.value if self.auth_policy is not None else None
            ),
            auth_frozen=self.session.auth_frozen,
            auth_reason=self.session.auth_reason,
        )

    async def evidence(self) -> BrowserEvidence:
        evidence = self.session.observer.snapshot()
        return evidence.model_copy(
            update={
                "auth_total": self.session.auth_total,
                "auth_truncated": self.session.auth_total
                > len(self.session.auth_events),
                "auth_events": tuple(self.session.auth_events),
            }
        )

    async def screenshot(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        page = self.session.require_page()
        sensitive = page.locator(
            'input[type="password"], input[type="hidden"], '
            '[data-sensitive="true"], [autocomplete="current-password"], '
            '[autocomplete="new-password"]'
        )
        await page.screenshot(path=str(path), full_page=True, mask=[sensitive])

    async def _assert(self, action: AssertAction) -> None:
        page = self.session.require_page()
        assertion = action.assertion
        if assertion.type == "url_matches":
            assert assertion.value is not None
            if not fnmatch(page.url, assertion.value):
                raise self._assertion_failed(assertion.type, assertion.value, page.url)
        elif assertion.type == "url_not_matches":
            assert assertion.value is not None
            if assertion.stability_ms:
                loop = asyncio.get_running_loop()
                deadline = loop.time() + assertion.stability_ms / 1_000
                while loop.time() < deadline:
                    if fnmatch(page.url, assertion.value):
                        raise self._assertion_failed(
                            assertion.type, assertion.value, page.url
                        )
                    await asyncio.sleep(min(0.05, max(0.0, deadline - loop.time())))
                return
            if fnmatch(page.url, assertion.value):
                raise self._assertion_failed(assertion.type, assertion.value, page.url)
        elif assertion.type == "title_contains":
            assert assertion.value is not None
            actual = await page.title()
            if assertion.value not in actual:
                raise self._assertion_failed(assertion.type, assertion.value, actual)
        elif assertion.type == "text_visible":
            assert assertion.value is not None
            matches = page.get_by_text(assertion.value, exact=True).filter(visible=True)
            if await matches.count() == 0:
                raise self._assertion_failed(assertion.type, assertion.value, None)
        elif assertion.type == "target_visible":
            if assertion.target is None:
                raise automation_error(
                    ErrorClassification.WORKFLOW_INVALID,
                    "unresolved targetRef reached Browser Runtime",
                )
            await self.resolver.resolve(page, assertion.target)
        else:
            if assertion.target is None:
                raise automation_error(
                    ErrorClassification.WORKFLOW_INVALID,
                    "unresolved targetRef reached Browser Runtime",
                )
            loop = asyncio.get_running_loop()
            deadline = loop.time() + assertion.stability_ms / 1_000
            while True:
                count = await self.resolver.visible_count(page, assertion.target)
                if count:
                    raise self._assertion_failed(assertion.type, "absent", str(count))
                if loop.time() >= deadline:
                    break
                await asyncio.sleep(min(0.05, max(0.0, deadline - loop.time())))

    async def _wait_for_assertion(self, assertion: Assertion, timeout_ms: int) -> None:
        """Wait for a positive post-submit page fact without masking real errors."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_ms / 1_000
        while True:
            try:
                await self._assert(AssertAction(assertion=assertion))
                return
            except AutomationException as exc:
                if (
                    exc.error.classification != ErrorClassification.ASSERTION_FAILED
                    or loop.time() >= deadline
                ):
                    raise
            await asyncio.sleep(min(0.05, max(0.0, deadline - loop.time())))

    def _assertion_failed(
        self, kind: str, expected: str, actual: str | None
    ) -> AutomationException:
        return automation_error(
            ErrorClassification.ASSERTION_FAILED,
            "browser assertion failed",
            assertion=kind,
            expected=self.redactor.redact_text(expected),
            actual=self.redactor.redact_text(actual) if actual is not None else None,
        )

    @staticmethod
    def _check_origin(url: str, context: RunContext) -> None:
        if urlsplit(url).scheme not in {"http", "https"}:
            raise automation_error(
                ErrorClassification.ORIGIN_NOT_ALLOWED,
                "only http(s) navigation is allowed",
                url=DEFAULT_REDACTOR.redact_text(url),
            )
        origin = normalized_origin(url)
        if origin not in context.allowed_origins:
            raise automation_error(
                ErrorClassification.ORIGIN_NOT_ALLOWED,
                "navigation origin is not allowed for this workflow",
                origin=origin,
            )

    def _failed(
        self,
        action_type: str,
        before: int,
        current_url: str,
        exc: AutomationException,
    ) -> ActionResult:
        return ActionResult(
            status="failed",
            action_type=action_type,
            revision_before=before,
            revision_after=self.session.revision,
            current_url=self.redactor.sanitize_url(current_url),
            error=exc.error,
        )
