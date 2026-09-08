from typing import NoReturn

from playwright.async_api import Locator

from agent_browser_automation.shared.classifications import ErrorClassification
from agent_browser_automation.shared.errors import automation_error
from agent_browser_automation.shared.run_context import AllowedControl

from .base import ControlFacts


class DeclaredButtonSubmitHandler:
    """Submits one exact environment-declared button or submit control."""

    async def supports(self, locator: Locator) -> bool:
        tag = str(await locator.evaluate("element => element.tagName.toLowerCase()"))
        control_type = (await locator.get_attribute("type") or "button").lower()
        return tag in {"button", "input"} and control_type in {"button", "submit"}

    async def inspect(self, locator: Locator) -> ControlFacts:
        text = (await locator.inner_text()).strip()
        accessible_name = next(
            (
                item.strip()
                for item in (
                    await locator.get_attribute("aria-label"),
                    text,
                    await locator.get_attribute("title"),
                    await locator.get_attribute("value"),
                )
                if item and item.strip()
            ),
            "",
        )
        return ControlFacts(
            tag_name=str(await locator.evaluate("element => element.tagName.toLowerCase()")),
            role=await locator.get_attribute("role"),
            control_type=(await locator.get_attribute("type") or "button").lower(),
            accessible_name=accessible_name[:500],
            disabled=await locator.is_disabled(),
        )

    def validate(
        self,
        facts: ControlFacts,
        expected_control: str | None,
        allowed_controls: tuple[AllowedControl, ...],
    ) -> None:
        if expected_control != "button" or facts.disabled or not facts.accessible_name:
            self._deny("submit control must be enabled and named", facts)
        if not any(
            item.expected_control == "button"
            and item.accessible_name == facts.accessible_name
            for item in allowed_controls
        ):
            self._deny("submit control is not declared by the environment capability", facts)

    async def submit(self, locator: Locator) -> None:
        await locator.click()

    @staticmethod
    def _deny(reason: str, facts: ControlFacts) -> NoReturn:
        raise automation_error(
            ErrorClassification.INTERACTION_DENIED,
            reason,
            accessible_name=facts.accessible_name,
        )
