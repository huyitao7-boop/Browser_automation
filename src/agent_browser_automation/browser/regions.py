from typing import Literal

from playwright.async_api import Locator, Page

RegionName = Literal["page", "main", "sidebar", "dialog", "overlay", "toast"]


def region_scope(page: Page, region: RegionName) -> Page | Locator:
    if region == "page":
        return page
    if region == "main":
        return page.get_by_role("main")
    if region == "sidebar":
        return page.locator('[role="navigation"], [role="complementary"], aside')
    if region == "dialog":
        return page.get_by_role("dialog").filter(visible=True)
    if region == "overlay":
        return page.locator('[role="listbox"], [role="menu"], [data-overlay-container]').filter(
            visible=True
        )
    return page.locator('[role="status"], [role="alert"]').filter(visible=True)
