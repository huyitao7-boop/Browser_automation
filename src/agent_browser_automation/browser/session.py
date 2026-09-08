import asyncio
from collections import deque
from pathlib import Path

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from agent_browser_automation.shared.redaction import Redactor, SensitiveValueRegistry
from agent_browser_automation.workflow.result import BrowserAuthEvent

from .auth import AuthObservation, AuthState
from .observer import BrowserObserver


class BrowserSession:
    def __init__(
        self,
        executable_path: Path | None,
        headless: bool,
        redactor: Redactor | None = None,
    ) -> None:
        self.executable_path = executable_path
        self.headless = headless
        self.redactor = redactor or Redactor(SensitiveValueRegistry())
        self.action_lock = asyncio.Lock()
        self.revision = 0
        self.playwright: Playwright | None = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self.observer = BrowserObserver(redactor=self.redactor)
        self.auth_state = AuthState.UNKNOWN
        self.auth_frozen = False
        self.auth_reason: str | None = None
        self.auth_events: deque[BrowserAuthEvent] = deque(maxlen=200)
        self.auth_total = 0

    async def start(self) -> None:
        if self.page is not None:
            return
        self.playwright = await async_playwright().start()
        launch_args: dict[str, object] = {"headless": self.headless}
        if self.executable_path is not None:
            launch_args["executable_path"] = str(self.executable_path)
        self.browser = await self.playwright.chromium.launch(**launch_args)  # type: ignore[arg-type]
        self.context = await self.browser.new_context()
        self.page = await self.context.new_page()
        self.observer.attach(self.page)

    async def close(self) -> None:
        try:
            if self.context is not None:
                await self.context.close()
            if self.browser is not None:
                await self.browser.close()
        finally:
            if self.playwright is not None:
                await self.playwright.stop()
            self.page = None
            self.context = None
            self.browser = None
            self.playwright = None

    def require_page(self) -> Page:
        if self.page is None:
            raise RuntimeError("browser session has not been started")
        return self.page

    def record_auth(self, observation: AuthObservation) -> None:
        previous = self.auth_state
        self.auth_state = observation.state
        self.auth_total += 1
        self.auth_events.append(
            BrowserAuthEvent(
                state_before=previous.value,
                state_after=observation.state.value,
                reason=observation.reason,
                policy_id=observation.policy_id,
                url=observation.url,
                matched_signal=observation.matched_signal,
            )
        )
        if observation.state in {
            AuthState.AUTHENTICATION_REQUIRED,
            AuthState.INTERRUPTED,
        }:
            self.auth_frozen = True
            self.auth_reason = observation.reason

    def complete_auth_recovery(self, observation: AuthObservation) -> None:
        if observation.state != AuthState.AUTHENTICATED:
            raise ValueError("authentication recovery requires an authenticated observation")
        previous = self.auth_state
        self.auth_state = AuthState.AUTHENTICATED
        self.auth_frozen = False
        self.auth_reason = None
        self.auth_total += 1
        self.auth_events.append(
            BrowserAuthEvent(
                state_before=previous.value,
                state_after=AuthState.AUTHENTICATED.value,
                reason="explicit authentication recovery succeeded",
                policy_id=observation.policy_id,
                url=observation.url,
                matched_signal=observation.matched_signal,
            )
        )
