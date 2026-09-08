from typing import NoReturn

from playwright.async_api import Locator

from agent_browser_automation.shared.classifications import ErrorClassification
from agent_browser_automation.shared.errors import automation_error
from agent_browser_automation.shared.run_context import AllowedControl

from .base import ControlFacts
from .click import MinimalClickHandler


class ExplorationClickHandler:
    def __init__(self) -> None:
        self._inspector = MinimalClickHandler()

    async def prepare(
        self,
        locator: Locator,
        expected_control: str | None,
        allowed_controls: tuple[AllowedControl, ...],
    ) -> ControlFacts:
        facts = await self._inspector.inspect(locator)
        if facts.role == "menuitem":
            actual = "menu_item"
        elif facts.tag_name == "a" and facts.href:
            actual = "link"
        elif facts.tag_name in {"button", "input"}:
            actual = "button"
        else:
            self._deny("exploration target is not a button or link", facts)
        if expected_control not in {"button", "link", "menu_item"} or expected_control != actual:
            self._deny("exploration target does not match expected_control", facts)
        if facts.disabled or not facts.accessible_name:
            self._deny("exploration target must be enabled and named", facts)
        if facts.is_form_submit:
            self._deny("form submit is not an exploration click", facts)
        if not any(
            item.expected_control == actual
            and (
                item.accessible_name == facts.accessible_name
                if item.accessible_name is not None
                else item.test_id == facts.test_id
            )
            for item in allowed_controls
        ):
            self._deny("target is not declared by the environment capability", facts)
        return facts

    @staticmethod
    def _deny(reason: str, facts: ControlFacts) -> NoReturn:
        raise automation_error(
            ErrorClassification.INTERACTION_DENIED,
            reason,
            accessible_name=facts.accessible_name,
            control_type=facts.control_type,
        )
