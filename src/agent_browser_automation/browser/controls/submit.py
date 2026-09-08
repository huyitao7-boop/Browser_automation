from typing import NoReturn

from playwright.async_api import Locator

from agent_browser_automation.shared.classifications import ErrorClassification
from agent_browser_automation.shared.errors import automation_error

from .base import ControlFacts


class TestWriteSubmitHandler:
    """Handles explicit form submit controls after policy authorization."""

    async def supports(self, locator: Locator) -> bool:
        tag_name = str(await locator.evaluate("element => element.tagName.toLowerCase()"))
        control_type = (await locator.get_attribute("type") or "submit").lower()
        return tag_name in {"button", "input"} and control_type == "submit"

    async def inspect(self, locator: Locator) -> ControlFacts:
        tag_name = str(await locator.evaluate("element => element.tagName.toLowerCase()"))
        control_type = (await locator.get_attribute("type") or "submit").lower()
        aria_label = await locator.get_attribute("aria-label")
        title = await locator.get_attribute("title")
        value = await locator.get_attribute("value")
        text = (await locator.inner_text()).strip()
        accessible_name = next(
            (item.strip() for item in (aria_label, text, title, value) if item and item.strip()),
            "",
        )
        return ControlFacts(
            tag_name=tag_name,
            role=await locator.get_attribute("role"),
            control_type=control_type,
            accessible_name=accessible_name[:500],
            disabled=await locator.is_disabled(),
        )

    def validate(self, facts: ControlFacts, expected_control: str | None) -> None:
        if expected_control is not None and expected_control.lower() != "button":
            self._deny("submit target does not match expected_control", facts)
        if facts.disabled:
            self._deny("disabled controls cannot be submitted", facts)
        if not facts.accessible_name:
            self._deny("submit target has no accessible name", facts)

    async def submit(self, locator: Locator) -> None:
        await locator.click()

    @staticmethod
    def _deny(reason: str, facts: ControlFacts) -> NoReturn:
        raise automation_error(
            ErrorClassification.INTERACTION_DENIED,
            reason,
            tag_name=facts.tag_name,
            control_type=facts.control_type,
        )
