from typing import NoReturn

from playwright.async_api import Locator

from agent_browser_automation.shared.classifications import ErrorClassification
from agent_browser_automation.shared.errors import automation_error

from .base import ControlFacts


class AriaComboboxHandler:
    """Opens a standard ARIA combobox; option selection stays in the runtime."""

    async def supports(self, locator: Locator) -> bool:
        return (await locator.get_attribute("role") or "").lower() == "combobox"

    async def inspect(self, locator: Locator) -> ControlFacts:
        return ControlFacts(
            tag_name=str(await locator.evaluate("element => element.tagName.toLowerCase()")),
            role=await locator.get_attribute("role"),
            accessible_name=(
                await locator.get_attribute("aria-label")
                or await locator.get_attribute("title")
                or ""
            )[:500],
            disabled=not await locator.is_enabled(),
        )

    def validate(self, facts: ControlFacts, expected_control: str | None) -> None:
        if expected_control != "select":
            self._deny("combobox target must declare expected_control=select", facts)
        if facts.disabled:
            self._deny("disabled combobox controls cannot be selected", facts)

    async def open(self, locator: Locator) -> None:
        await locator.click()

    @staticmethod
    def _deny(reason: str, facts: ControlFacts) -> NoReturn:
        raise automation_error(
            ErrorClassification.INTERACTION_DENIED,
            reason,
            tag_name=facts.tag_name,
            accessible_name=facts.accessible_name,
        )
