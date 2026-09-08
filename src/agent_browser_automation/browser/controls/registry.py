from collections.abc import Sequence

from playwright.async_api import Locator

from agent_browser_automation.shared.classifications import ErrorClassification
from agent_browser_automation.shared.errors import automation_error
from agent_browser_automation.shared.run_context import AllowedControl

from .aria_combobox import AriaComboboxHandler
from .base import (
    ComboboxHandler,
    ControlFacts,
    ControlHandler,
    RadioHandler,
    SelectHandler,
    SubmitHandler,
    TextFillHandler,
)
from .click import MinimalClickHandler
from .declared_button_submit import DeclaredButtonSubmitHandler
from .explore_click import ExplorationClickHandler
from .native_select import NativeSelectHandler
from .radio import NativeRadioHandler
from .rich_text import RichTextHandler
from .submit import TestWriteSubmitHandler
from .text_input import TextInputHandler


class ControlHandlerRegistry:
    def __init__(
        self,
        handlers: Sequence[ControlHandler] | None = None,
        text_handlers: Sequence[TextFillHandler] | None = None,
        submit_handlers: Sequence[SubmitHandler] | None = None,
        select_handlers: Sequence[SelectHandler] | None = None,
        radio_handlers: Sequence[RadioHandler] | None = None,
        combobox_handlers: Sequence[ComboboxHandler] | None = None,
    ) -> None:
        self._handlers: tuple[ControlHandler, ...] = (
            tuple(handlers) if handlers is not None else (MinimalClickHandler(),)
        )
        self._text_handlers = (
            tuple(text_handlers)
            if text_handlers is not None
            else (TextInputHandler(), RichTextHandler())
        )
        self._submit_handlers = (
            tuple(submit_handlers)
            if submit_handlers is not None
            else (TestWriteSubmitHandler(),)
        )
        self._declared_button_submit = DeclaredButtonSubmitHandler()
        self._exploration_click = ExplorationClickHandler()
        self._select_handlers = (
            tuple(select_handlers) if select_handlers is not None else (NativeSelectHandler(),)
        )
        self._radio_handlers = (
            tuple(radio_handlers) if radio_handlers is not None else (NativeRadioHandler(),)
        )
        self._combobox_handlers = (
            tuple(combobox_handlers)
            if combobox_handlers is not None
            else (AriaComboboxHandler(),)
        )

    async def prepare_click(
        self, locator: Locator, expected_control: str | None
    ) -> tuple[ControlHandler, ControlFacts]:
        for handler in self._handlers:
            if await handler.supports(locator):
                facts = await handler.inspect(locator)
                handler.validate(facts, expected_control)
                return handler, facts
        raise automation_error(
            ErrorClassification.INTERACTION_DENIED,
            "no registered handler accepts the target control",
        )

    async def prepare_fill_text(
        self, locator: Locator, expected_control: str | None
    ) -> tuple[TextFillHandler, ControlFacts]:
        for handler in self._text_handlers:
            if await handler.supports(locator):
                facts = await handler.inspect(locator)
                handler.validate(facts, expected_control)
                return handler, facts
        raise automation_error(
            ErrorClassification.INTERACTION_DENIED,
            "no registered text handler accepts the target control",
        )

    async def prepare_submit(
        self, locator: Locator, expected_control: str | None
    ) -> tuple[SubmitHandler, ControlFacts]:
        for handler in self._submit_handlers:
            if await handler.supports(locator):
                facts = await handler.inspect(locator)
                handler.validate(facts, expected_control)
                return handler, facts
        raise automation_error(
            ErrorClassification.INTERACTION_DENIED,
            "no registered submit handler accepts the target control",
        )

    async def prepare_declared_button_submit(
        self,
        locator: Locator,
        expected_control: str | None,
        allowed_controls: tuple[AllowedControl, ...],
    ) -> tuple[DeclaredButtonSubmitHandler, ControlFacts]:
        if not await self._declared_button_submit.supports(locator):
            raise automation_error(
                ErrorClassification.INTERACTION_DENIED,
                "declared submit target is not a button or submit control",
            )
        facts = await self._declared_button_submit.inspect(locator)
        self._declared_button_submit.validate(facts, expected_control, allowed_controls)
        return self._declared_button_submit, facts

    async def prepare_select(
        self, locator: Locator, expected_control: str | None
    ) -> tuple[SelectHandler, ControlFacts]:
        for handler in self._select_handlers:
            if await handler.supports(locator):
                facts = await handler.inspect(locator)
                handler.validate(facts, expected_control)
                return handler, facts
        raise automation_error(
            ErrorClassification.INTERACTION_DENIED,
            "no registered select handler accepts the target control",
        )

    async def prepare_radio(
        self, locator: Locator, expected_control: str | None
    ) -> tuple[RadioHandler, ControlFacts]:
        for handler in self._radio_handlers:
            if await handler.supports(locator):
                facts = await handler.inspect(locator)
                handler.validate(facts, expected_control)
                return handler, facts
        raise automation_error(
            ErrorClassification.INTERACTION_DENIED,
            "no registered radio handler accepts the target control",
        )

    async def prepare_combobox(
        self, locator: Locator, expected_control: str | None
    ) -> tuple[ComboboxHandler, ControlFacts]:
        for handler in self._combobox_handlers:
            if await handler.supports(locator):
                facts = await handler.inspect(locator)
                handler.validate(facts, expected_control)
                return handler, facts
        raise automation_error(
            ErrorClassification.INTERACTION_DENIED,
            "no registered combobox handler accepts the target control",
        )

    async def prepare_exploration_click(
        self,
        locator: Locator,
        expected_control: str | None,
        allowed_controls: tuple[AllowedControl, ...],
    ) -> ControlFacts:
        return await self._exploration_click.prepare(locator, expected_control, allowed_controls)
