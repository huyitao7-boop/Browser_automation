from typing import NoReturn

from playwright.async_api import Locator

from agent_browser_automation.shared.classifications import ErrorClassification
from agent_browser_automation.shared.errors import automation_error

from .base import ControlFacts

_TEXT_INPUT_TYPES = {"text", "email", "password", "search", "tel", "url"}


class TextInputHandler:
    """Handles editable text fields; values are redacted at the Session boundary."""

    async def supports(self, locator: Locator) -> bool:
        tag_name = str(await locator.evaluate("element => element.tagName.toLowerCase()"))
        input_type = (await locator.get_attribute("type") or "text").lower()
        return tag_name == "textarea" or (
            tag_name == "input" and input_type in _TEXT_INPUT_TYPES
        )

    async def inspect(self, locator: Locator) -> ControlFacts:
        tag_name = str(await locator.evaluate("element => element.tagName.toLowerCase()"))
        control_type = (await locator.get_attribute("type") or "text").lower()
        aria_label = await locator.get_attribute("aria-label")
        placeholder = await locator.get_attribute("placeholder")
        name = await locator.get_attribute("name")
        accessible_name = next(
            (item.strip() for item in (aria_label, placeholder, name) if item and item.strip()),
            "",
        )
        return ControlFacts(
            tag_name=tag_name,
            role=await locator.get_attribute("role"),
            control_type=control_type,
            accessible_name=accessible_name[:500],
            disabled=await locator.is_disabled(),
            editable=await locator.is_editable(),
        )

    def validate(self, facts: ControlFacts, expected_control: str | None) -> None:
        actual = "textbox"
        if expected_control is not None and expected_control.lower() != actual:
            self._deny("text target does not match expected_control", facts)
        if facts.disabled or not facts.editable:
            self._deny("text target must be enabled and editable", facts)
        if facts.control_type not in _TEXT_INPUT_TYPES and facts.tag_name != "textarea":
            self._deny("unsupported input type cannot receive text", facts)

    async def fill(self, locator: Locator, value: str) -> None:
        await locator.fill(value)

    @staticmethod
    def _deny(reason: str, facts: ControlFacts) -> NoReturn:
        raise automation_error(
            ErrorClassification.INTERACTION_DENIED,
            reason,
            tag_name=facts.tag_name,
            control_type=facts.control_type,
        )
