from typing import NoReturn

from playwright.async_api import Locator

from agent_browser_automation.shared.classifications import ErrorClassification
from agent_browser_automation.shared.errors import automation_error

from .base import ControlFacts


class NativeRadioHandler:
    """Select one visible native or ARIA radio option without submitting a form."""

    async def supports(self, locator: Locator) -> bool:
        return (
            (await locator.get_attribute("type") or "").lower() == "radio"
            or (await locator.get_attribute("role") or "").lower() == "radio"
        )

    async def inspect(self, locator: Locator) -> ControlFacts:
        return ControlFacts(
            tag_name=str(await locator.evaluate("element => element.tagName.toLowerCase()")),
            role=await locator.get_attribute("role"),
            control_type=await locator.get_attribute("type"),
            accessible_name=(
                await locator.get_attribute("aria-label") or await locator.inner_text()
            ).strip()[:500],
            disabled=not await locator.is_enabled(),
        )

    def validate(self, facts: ControlFacts, expected_control: str | None) -> None:
        if expected_control != "radio":
            self._deny("radio target must declare expected_control=radio", facts)
        if facts.disabled:
            self._deny("disabled radio controls cannot be selected", facts)

    async def select(self, locator: Locator) -> None:
        await locator.check()

    @staticmethod
    def _deny(reason: str, facts: ControlFacts) -> NoReturn:
        raise automation_error(
            ErrorClassification.INTERACTION_DENIED,
            reason,
            tag_name=facts.tag_name,
        )
