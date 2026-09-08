import asyncio

from playwright.async_api import Locator, Page

from agent_browser_automation.shared.classifications import ErrorClassification
from agent_browser_automation.shared.errors import AutomationException, automation_error
from agent_browser_automation.workflow.action import LocatorStrategy, TargetSpec

from .regions import region_scope


class TargetResolver:
    async def resolve(self, page: Page, target: TargetSpec) -> Locator:
        scope = region_scope(page, target.region)
        for strategy in target.strategies:
            locator = self._locator(scope, strategy)
            visible = locator.filter(visible=True)
            count = await visible.count()
            if count == 1:
                return visible
            if count > 1:
                raise automation_error(
                    ErrorClassification.TARGET_AMBIGUOUS,
                    "target strategy matched multiple visible elements",
                    region=target.region,
                    strategy=strategy.model_dump(mode="json", exclude_none=True),
                    count=count,
                )
        raise automation_error(
            ErrorClassification.TARGET_NOT_FOUND,
            "no target strategy matched a visible element",
            region=target.region,
        )

    async def visible_count(self, page: Page, target: TargetSpec) -> int:
        scope = region_scope(page, target.region)
        total = 0
        for strategy in target.strategies:
            total += await self._locator(scope, strategy).filter(visible=True).count()
        return total

    async def wait_for_unique_visible(
        self, page: Page, target: TargetSpec, timeout_ms: int
    ) -> Locator:
        """Wait for a target to satisfy the same unique-visible rule as ``resolve``.

        A page can finish its initial document load before a client-side application
        renders its controls.  Waiting here preserves the resolver's safety
        invariant: a control is returned only when exactly one visible match exists.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_ms / 1_000
        while True:
            try:
                return await self.resolve(page, target)
            except AutomationException as exc:
                if exc.error.classification not in {
                    ErrorClassification.TARGET_NOT_FOUND,
                    ErrorClassification.TARGET_AMBIGUOUS,
                }:
                    raise
                if loop.time() >= deadline:
                    raise
                await asyncio.sleep(min(0.05, max(0.0, deadline - loop.time())))

    def _locator(self, scope: Page | Locator, strategy: LocatorStrategy) -> Locator:
        if strategy.kind == "role":
            return scope.get_by_role(strategy.role, name=strategy.name, exact=True)  # type: ignore[arg-type]
        assert strategy.value is not None
        if strategy.kind == "label":
            return scope.get_by_label(strategy.value, exact=True)
        if strategy.kind == "placeholder":
            return scope.get_by_placeholder(strategy.value, exact=True)
        if strategy.kind == "testid":
            return scope.get_by_test_id(strategy.value)
        if strategy.kind == "text":
            return scope.get_by_text(strategy.value, exact=True)
        self._validate_css(strategy.value)
        return scope.locator(strategy.value)

    @staticmethod
    def _validate_css(selector: str) -> None:
        lowered = selector.lower()
        forbidden = ("xpath=", "text=", ">>", ":has(")
        if any(value in lowered for value in forbidden):
            raise automation_error(
                ErrorClassification.UNSAFE_ACTION,
                "CSS fallback contains a forbidden selector feature",
            )
