"""EXPLORATORY step 1 of the "map Divar <-> Hamrah Mechanic" plan.

Before writing a script that walks the ENTIRE Hamrah Mechanic catalog
unattended, this one only processes the first few brands (see LIMIT below)
so you can watch it in a visible browser and confirm the navigation logic
actually works the way we're assuming:

  1. Open the car picker, empty query -> should show a list of top-level
     BRANDS.
  2. Type just the brand name -> filter down -> click the brand's own
     entry -> does the result list now show that brand's MODELS? Or did
     clicking it "confirm" a full car selection and move on (e.g. the
     "سال ساخت" tab becomes active/enabled)?

Usage:
    python explore_hamrah_catalog.py
"""

import asyncio
import os

os.environ["HEADLESS_BROWSER"] = "false"

from playwright.async_api import async_playwright  # noqa: E402

BASE_URL = "https://www.hamrah-mechanic.com/carprice/"
CAR_PICKER_INPUT = 'input[name="car"]'
BRAND_MODEL_INPUT = 'input[name="brand-model"]'
RESULT_ITEM = '[class*="car-detail__car-name"]'
BRAND_MODEL_TAB = "برند و مدل"
YEAR_TAB = "سال ساخت"

LIMIT = 5  # only process this many top-level brands for this exploratory run


async def get_result_texts(page, exclude=("همه برند ها",)) -> list[str]:
    items = page.locator(RESULT_ITEM)
    count = await items.count()
    texts = []
    for i in range(count):
        try:
            t = (await items.nth(i).inner_text(timeout=1500)).strip()
        except Exception:
            continue
        if t and t not in exclude:
            texts.append(t)
    return texts


async def wait_for_real_results(page, tries: int = 6, delay_ms: int = 400) -> list[str]:
    """The model list renders a beat after the brand click (and a
    "همه برند ها" back-link can appear before the real models do), so poll
    a few times instead of reading immediately.
    """
    for _ in range(tries):
        texts = await get_result_texts(page)
        if texts:
            return texts
        await page.wait_for_timeout(delay_ms)
    return []


async def main() -> None:
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

        print("Clearing the search box to see the default top-level list...")
        await page.locator(BRAND_MODEL_INPUT).fill("")
        await page.wait_for_timeout(1000)
        top_level = await get_result_texts(page)
        print(f"\n== {len(top_level)} top-level entries (should be brands) ==")
        for t in top_level:
            print(f"  - {t}")

        for brand in top_level[:LIMIT]:
            print(f"\n=== Trying brand: {brand!r} ===")
            await page.locator(BRAND_MODEL_INPUT).fill(brand)
            await page.wait_for_timeout(800)

            option = page.locator(RESULT_ITEM).filter(has_text=brand)
            if not await option.count():
                print(f"  couldn't re-find {brand!r} after typing it - skipping")
                continue
            try:
                await option.first.click(timeout=3000)
            except Exception as exc:
                print(f"  couldn't click {brand!r}: {exc}")
                continue

            after_click = await wait_for_real_results(page)
            print(f"  result list after clicking {brand!r} ({len(after_click)} items):")
            for t in after_click:
                print(f"    - {t}")

            year_tab = page.get_by_role("tab", name=YEAR_TAB)
            year_enabled = False
            if await year_tab.count():
                aria_disabled = await year_tab.first.get_attribute("aria-disabled")
                year_enabled = aria_disabled != "true"
            print(f"  '{YEAR_TAB}' tab enabled after this click? {year_enabled}")
            print(
                "  -> if year_enabled is True, clicking the brand alone already "
                "confirmed a full car (probably not what we want). If the result "
                "list above shows model names instead, clicking drilled into models "
                "(what we're hoping for)."
            )

            # Reset for the next brand: click the "همه برند ها" back-link if
            # present (should return to the full 94-brand list); reload the
            # picker from scratch as a fallback if it's not there.
            back_link = page.get_by_text("همه برند ها", exact=True)
            if await back_link.count():
                try:
                    await back_link.first.click(timeout=3000)
                    await page.wait_for_timeout(600)
                    continue
                except Exception:
                    pass

            tab = page.get_by_role("tab", name=BRAND_MODEL_TAB)
            if await tab.count():
                await tab.first.click()
                await page.wait_for_timeout(300)
            await page.locator(BRAND_MODEL_INPUT).fill("")
            await page.wait_for_timeout(800)

        print("\nDone with exploratory pass. Browser stays open - inspect manually, then press Enter to close.")
        input()
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
