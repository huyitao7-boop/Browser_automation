from typing import NoReturn

from playwright.async_api import Locator

from agent_browser_automation.shared.classifications import ErrorClassification
from agent_browser_automation.shared.errors import automation_error

from .base import ControlFacts


class NativeSelectHandler:
    """Handles native select elements; custom comboboxes remain explicitly unsupported."""

    async def supports(self, locator: Locator) -> bool:
        return str(await locator.evaluate("element => element.tagName.toLowerCase()")) == "select"

    async def inspect(self, locator: Locator) -> ControlFacts:
        aria_label = await locator.get_attribute("aria-label")
        name = await locator.get_attribute("name")
        return ControlFacts(
            tag_name="select",
            role=await locator.get_attribute("role"),
            accessible_name=next(
                (item.strip() for item in (aria_label, name) if item and item.strip()), ""
            )[:500],
            disabled=await locator.is_disabled(),
            editable=True,
        )

    def validate(self, facts: ControlFacts, expected_control: str | None) -> None:
        if expected_control is not None and expected_control.lower() != "select":
            self._deny("select target does not match expected_control", facts)
        if facts.disabled:
            self._deny("disabled controls cannot be selected", facts)

    async def select(self, locator: Locator, value: str) -> None:
        await locator.select_option(value=value)

    @staticmethod
    def _deny(reason: str, facts: ControlFacts) -> NoReturn:
        raise automation_error(
            ErrorClassification.INTERACTION_DENIED,
            reason,
            tag_name=facts.tag_name,
        )
