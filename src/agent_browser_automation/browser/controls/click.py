import re
from typing import NoReturn

from playwright.async_api import Locator

from agent_browser_automation.shared.classifications import ErrorClassification
from agent_browser_automation.shared.errors import automation_error

from .base import ControlFacts

_HIGH_RISK_LABEL = re.compile(
    r"\b(submit|save|delete|remove|create|upload|download|publish|confirm|apply|"
    r"pay|purchase|buy|send|logout|log out|sign out)\b|"
    r"提交|保存|删除|移除|创建|上传|下载|发布|确认|申请|支付|购买|发送|退出",
    re.IGNORECASE,
)


class MinimalClickHandler:
    """Handles only semantically clear buttons and links with deny-by-default checks."""

    async def supports(self, locator: Locator) -> bool:
        tag_name = await locator.evaluate("element => element.tagName.toLowerCase()")
        return str(tag_name) in {"a", "button", "input"}

    async def inspect(self, locator: Locator) -> ControlFacts:
        tag_name = str(
            await locator.evaluate("element => element.tagName.toLowerCase()")
        )
        role = await locator.get_attribute("role")
        control_type = await locator.get_attribute("type")
        aria_label = await locator.get_attribute("aria-label")
        title = await locator.get_attribute("title")
        value = await locator.get_attribute("value")
        test_id = await locator.get_attribute("data-testid")
        text = (await locator.inner_text()).strip()
        accessible_name = next(
            (
                item.strip()
                for item in (aria_label, text, title, value, test_id)
                if item is not None and item.strip()
            ),
            "",
        )
        return ControlFacts(
            tag_name=tag_name,
            role=role.lower() if role else None,
            control_type=control_type.lower() if control_type else None,
            accessible_name=accessible_name[:500],
            href=await locator.get_attribute("href"),
            test_id=test_id,
            disabled=await locator.is_disabled(),
            is_form_submit=bool(
                await locator.evaluate(
                    """element => {
                      if (!(element instanceof HTMLButtonElement ||
                            element instanceof HTMLInputElement)) return false;
                      return element.type === 'submit' && element.form !== null;
                    }"""
                )
            ),
        )

    def validate(self, facts: ControlFacts, expected_control: str | None) -> None:
        actual_control = self._actual_control(facts)
        if actual_control is None:
            self._deny("target control semantics are not safely classifiable", facts)
        if expected_control is not None and expected_control.lower() != actual_control:
            self._deny("target control does not match expected_control", facts)
        if facts.disabled:
            self._deny("disabled controls cannot be clicked", facts)
        if not facts.accessible_name:
            self._deny("target has no accessible name", facts)
        if _HIGH_RISK_LABEL.search(facts.accessible_name):
            self._deny("target label indicates a high-risk interaction", facts)

    async def click(self, locator: Locator) -> None:
        await locator.click()

    @staticmethod
    def _actual_control(facts: ControlFacts) -> str | None:
        if facts.tag_name == "a" and facts.href:
            return "link"
        if facts.tag_name == "button" and facts.control_type == "button":
            return "button"
        if facts.tag_name == "input" and facts.control_type == "button":
            return "button"
        return None

    @staticmethod
    def _deny(reason: str, facts: ControlFacts) -> NoReturn:
        raise automation_error(
            ErrorClassification.INTERACTION_DENIED,
            reason,
            tag_name=facts.tag_name,
            control_type=facts.control_type,
            accessible_name=facts.accessible_name,
        )
