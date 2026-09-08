import asyncio
from fnmatch import fnmatch
from typing import Literal

from playwright.async_api import Page

from agent_browser_automation.shared.classifications import ErrorClassification
from agent_browser_automation.shared.errors import AutomationException
from agent_browser_automation.shared.redaction import DEFAULT_REDACTOR, Redactor
from agent_browser_automation.workflow.action import TargetSpec

from ..target_resolver import TargetResolver
from .models import AuthObservation, AuthPolicy, AuthState

TargetPresence = Literal["absent", "present", "ambiguous"]


class AuthObserver:
    def __init__(
        self,
        resolver: TargetResolver | None = None,
        redactor: Redactor = DEFAULT_REDACTOR,
    ) -> None:
        self.resolver = resolver or TargetResolver()
        self.redactor = redactor

    async def inspect(
        self,
        page: Page,
        policy: AuthPolicy,
        previous_state: AuthState,
    ) -> AuthObservation:
        observation = await self._inspect_once(page, policy, previous_state)
        if (
            observation.state == AuthState.INTERRUPTED
            and observation.reason == "authenticated marker disappeared"
            and policy.stability_ms
        ):
            await asyncio.sleep(policy.stability_ms / 1000)
            observation = await self._inspect_once(page, policy, previous_state)
        return observation

    async def _inspect_once(
        self,
        page: Page,
        policy: AuthPolicy,
        previous_state: AuthState,
    ) -> AuthObservation:
        for pattern in policy.signed_out_url_patterns:
            if fnmatch(page.url, pattern):
                return self._required(
                    page,
                    policy,
                    previous_state,
                    "signed-out URL matched",
                    f"url:{pattern}",
                )

        for index, target in enumerate(policy.signed_out_targets):
            presence = await self._presence(page, target)
            if presence != "absent":
                return self._required(
                    page,
                    policy,
                    previous_state,
                    "signed-out target is visible",
                    f"signed-out-target:{index}:{presence}",
                )

        for index, target in enumerate(policy.authenticated_targets):
            if await self._presence(page, target) == "present":
                return AuthObservation(
                    state=AuthState.AUTHENTICATED,
                    reason="authenticated target is visible",
                    policy_id=policy.id,
                    url=self.redactor.sanitize_url(page.url),
                    matched_signal=f"authenticated-target:{index}",
                )

        if (
            previous_state == AuthState.AUTHENTICATED
            and policy.authenticated_targets
            and policy.freeze_on_marker_loss
        ):
            return AuthObservation(
                state=AuthState.INTERRUPTED,
                reason="authenticated marker disappeared",
                policy_id=policy.id,
                url=self.redactor.sanitize_url(page.url),
                matched_signal="authenticated-target:missing",
            )

        return AuthObservation(
            state=AuthState.UNKNOWN,
            reason="available page facts do not determine authentication state",
            policy_id=policy.id,
            url=self.redactor.sanitize_url(page.url),
        )

    async def _presence(self, page: Page, target: TargetSpec) -> TargetPresence:
        try:
            await self.resolver.resolve(page, target)
        except AutomationException as exc:
            if exc.error.classification == ErrorClassification.TARGET_NOT_FOUND:
                return "absent"
            if exc.error.classification == ErrorClassification.TARGET_AMBIGUOUS:
                return "ambiguous"
            raise
        return "present"

    def _required(
        self,
        page: Page,
        policy: AuthPolicy,
        previous_state: AuthState,
        reason: str,
        matched_signal: str,
    ) -> AuthObservation:
        state = (
            AuthState.INTERRUPTED
            if previous_state == AuthState.AUTHENTICATED
            else AuthState.AUTHENTICATION_REQUIRED
        )
        return AuthObservation(
            state=state,
            reason=reason,
            policy_id=policy.id,
            url=self.redactor.sanitize_url(page.url),
            matched_signal=matched_signal,
        )
