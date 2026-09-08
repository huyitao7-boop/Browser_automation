import re
from typing import NoReturn

from playwright.async_api import Locator

from agent_browser_automation.shared.classifications import ErrorClassification
from agent_browser_automation.shared.errors import automation_error

_DELETE_LABEL = re.compile(r"\b(delete|remove)\b|删除|移除", re.IGNORECASE)


class CleanupDeleteHandler:
    async def validate(self, locator: Locator, expected_control: str | None) -> None:
        tag_name = str(await locator.evaluate("element => element.tagName.toLowerCase()"))
        control_type = (await locator.get_attribute("type") or "button").lower()
        aria_label = await locator.get_attribute("aria-label")
        title = await locator.get_attribute("title")
        value = await locator.get_attribute("value")
        text = (await locator.inner_text()).strip()
        name = next(
            (item.strip() for item in (aria_label, text, title, value) if item and item.strip()),
            "",
        )
        if expected_control not in {None, "button"}:
            self._deny("cleanup target must declare a button", tag_name, name)
        if tag_name not in {"button", "input"} or control_type not in {
            "button",
            "submit",
        }:
            self._deny("cleanup target is not a button", tag_name, name)
        if await locator.is_disabled():
            self._deny("disabled cleanup target cannot be clicked", tag_name, name)
        if not _DELETE_LABEL.search(name):
            self._deny("cleanup target does not have delete semantics", tag_name, name)

    @staticmethod
    def _deny(reason: str, tag_name: str, name: str) -> NoReturn:
        raise automation_error(
            ErrorClassification.INTERACTION_DENIED,
            reason,
            tag_name=tag_name,
            accessible_name=name[:500],
        )
