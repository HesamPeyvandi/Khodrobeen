"""Debug helper: opens Hamrah Mechanic's price estimator, types a
brand/model query into the picker, and dumps every result item's raw text,
class, and whether Playwright considers it "visible" and "enabled" - so we
can see exactly why clicking one of them wasn't working.

Usage:
    python debug_hamrah_picker.py "پژو"
    python debug_hamrah_picker.py "پژو 207i"
"""

import asyncio
import os
import sys

os.environ["HEADLESS_BROWSER"] = "false"

from playwright.async_api import async_playwright  # noqa: E402

BASE_URL = "https://www.hamrah-mechanic.com/carprice/"
CAR_PICKER_INPUT = 'input[name="car"]'
BRAND_MODEL_INPUT = 'input[name="brand-model"]'
RESULT_ITEM = '[class*="car-detail__car-name"]'
BRAND_MODEL_TAB = "برند و مدل"


async def main() -> None:
    query = sys.argv[1] if len(sys.argv) > 1 else "پژو"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(locale="fa-IR")
        page = await context.new_page()

        print(f"Opening {BASE_URL} ...")
        await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(1500)

        await page.locator(CAR_PICKER_INPUT).click()
        await page.wait_for_timeout(500)

        tab = page.get_by_role("tab", name=BRAND_MODEL_TAB)
        if await tab.count():
            await tab.first.click()
            await page.wait_for_timeout(300)

        print(f"Typing query: {query!r}")
        await page.locator(BRAND_MODEL_INPUT).fill(query)
        await page.wait_for_timeout(1000)

        results = page.locator(RESULT_ITEM)
        count = await results.count()
        print(f"\n== {count} result item(s) found ==")

        for i in range(count):
            item = results.nth(i)
            try:
                text = await item.inner_text(timeout=1500)
            except Exception as exc:
                text = f"<error reading text: {exc}>"
            try:
                cls = await item.get_attribute("class")
            except Exception:
                cls = None
            try:
                visible = await item.is_visible()
            except Exception:
                visible = None
            try:
                enabled = await item.is_enabled()
            except Exception:
                enabled = None
            print(f"  [{i}] text={text!r}")
            print(f"        class={cls!r}")
            print(f"        visible={visible} enabled={enabled}")

        print("\nBrowser stays open - inspect it manually if you want, then press Enter here to close.")
        input()
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
