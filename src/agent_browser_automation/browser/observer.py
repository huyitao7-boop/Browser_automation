from collections import deque

from playwright.async_api import ConsoleMessage, Error, Frame, Page, Request, Response

from agent_browser_automation.shared.redaction import DEFAULT_REDACTOR, Redactor
from agent_browser_automation.workflow.result import (
    BrowserConsoleEvent,
    BrowserEvidence,
    BrowserNavigationEvent,
    BrowserNetworkEvent,
    BrowserPageErrorEvent,
)


class BrowserObserver:
    def __init__(self, limit: int = 200, redactor: Redactor = DEFAULT_REDACTOR) -> None:
        if limit < 1:
            raise ValueError("observer limit must be positive")
        self.console_events: deque[BrowserConsoleEvent] = deque(maxlen=limit)
        self.network_events: deque[BrowserNetworkEvent] = deque(maxlen=limit)
        self.page_errors: deque[BrowserPageErrorEvent] = deque(maxlen=limit)
        self.navigations: deque[BrowserNavigationEvent] = deque(maxlen=limit)
        self.http_events: deque[BrowserNetworkEvent] = deque(maxlen=limit)
        self.console_total = 0
        self.network_total = 0
        self.page_error_total = 0
        self.navigation_total = 0
        self.http_total = 0
        self.redactor = redactor

    def attach(self, page: Page) -> None:
        page.on("console", self._on_console)
        page.on("requestfailed", self._on_request_failed)
        page.on("response", self._on_response)
        page.on("pageerror", self._on_page_error)
        page.on("framenavigated", self._on_frame_navigated)

    def _on_page_error(self, error: Error) -> None:
        self.page_error_total += 1
        self.page_errors.append(
            BrowserPageErrorEvent(message=self.redactor.redact_text(str(error), 2_000))
        )

    def _on_frame_navigated(self, frame: Frame) -> None:
        if frame.parent_frame is not None:
            return
        self.navigation_total += 1
        self.navigations.append(
            BrowserNavigationEvent(url=self.redactor.sanitize_url(frame.url))
        )

    def _on_console(self, message: ConsoleMessage) -> None:
        self.record_console(message.type, message.text)

    def record_console(self, message_type: str, text: str) -> None:
        self.console_total += 1
        self.console_events.append(
            BrowserConsoleEvent(
                type=message_type, text=self.redactor.redact_text(text, 2_000)
            )
        )

    def _on_request_failed(self, request: Request) -> None:
        self.record_request_failed(request.method, request.url, request.failure)

    def record_request_failed(
        self, method: str, url: str, failure: str | None
    ) -> None:
        self.network_total += 1
        self.network_events.append(
            BrowserNetworkEvent(
                kind="requestfailed",
                method=method,
                url=self.redactor.sanitize_url(url),
                failure=self.redactor.redact_text(failure, 1_000) if failure else None,
            )
        )

    def _on_response(self, response: Response) -> None:
        self.record_response(response.request.method, response.url, response.status)

    def record_response(self, method: str, url: str, status: int) -> None:
        self.http_total += 1
        event = BrowserNetworkEvent(
            kind="response",
            method=method,
            url=self.redactor.sanitize_url(url),
            status=status,
        )
        self.http_events.append(event)
        if status >= 400:
            self.network_total += 1
            self.network_events.append(event)

    def snapshot(self) -> BrowserEvidence:
        return BrowserEvidence(
            console_total=self.console_total,
            network_total=self.network_total,
            console_truncated=self.console_total > len(self.console_events),
            network_truncated=self.network_total > len(self.network_events),
            console_events=tuple(self.console_events),
            network_events=tuple(self.network_events),
            page_error_total=self.page_error_total,
            navigation_total=self.navigation_total,
            page_error_truncated=self.page_error_total > len(self.page_errors),
            navigation_truncated=self.navigation_total > len(self.navigations),
            page_errors=tuple(self.page_errors),
            navigations=tuple(self.navigations),
            http_total=self.http_total,
            http_truncated=self.http_total > len(self.http_events),
            http_events=tuple(self.http_events),
        )
