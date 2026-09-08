from typing import NoReturn

from playwright.async_api import Locator

from agent_browser_automation.shared.classifications import ErrorClassification
from agent_browser_automation.shared.errors import automation_error

from .base import ControlFacts


class RichTextHandler:
    """Handles a visible, ordinary contenteditable editor during test writes."""

    async def supports(self, locator: Locator) -> bool:
        value = await locator.get_attribute("contenteditable")
        return value is not None and value.lower() != "false"

    async def inspect(self, locator: Locator) -> ControlFacts:
        return ControlFacts(
            tag_name=str(await locator.evaluate("element => element.tagName.toLowerCase()")),
            role=await locator.get_attribute("role"),
            accessible_name=(
                await locator.get_attribute("aria-label")
                or await locator.get_attribute("data-placeholder")
                or ""
            )[:500],
            disabled=not await locator.is_enabled(),
            editable=await locator.is_editable(),
        )

    def validate(self, facts: ControlFacts, expected_control: str | None) -> None:
        if expected_control != "richtextbox":
            self._deny("rich-text target must declare expected_control=richtextbox", facts)
        if facts.disabled or not facts.editable:
            self._deny("rich-text target must be enabled and editable", facts)

    async def fill(self, locator: Locator, value: str) -> None:
        await locator.fill(value)

    @staticmethod
    def _deny(reason: str, facts: ControlFacts) -> NoReturn:
        raise automation_error(
            ErrorClassification.INTERACTION_DENIED,
            reason,
            tag_name=facts.tag_name,
        )
