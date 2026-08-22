"""Debug helper: dumps candidate "spec/feature row" elements from a Divar
listing detail page so we can find the right CSS selector for the fields
that aren't being picked up (brand/model, year, mileage, color, body status).

Usage:
    python debug_divar_specs.py <divar_listing_url>
"""

import asyncio
import os
import sys

os.environ["HEADLESS_BROWSER"] = "false"

from playwright.async_api import async_playwright  # noqa: E402


async def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python debug_divar_specs.py <divar_listing_url>")
        return
    url = sys.argv[1]

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            locale="fa-IR",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
        )
        page = await context.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(2000)

        print("\n== All elements whose class contains 'row' ==")
        rows = await page.locator("[class*='row']").all()
        print(f"(found {len(rows)} elements)")
        for i, el in enumerate(rows):
            try:
                cls = await el.get_attribute("class")
                text = (await el.inner_text(timeout=1500)).replace("\n", " | ")
            except Exception:
                continue
            if text.strip():
                print(f"  [{i}] class={cls!r}")
                print(f"        text={text[:120]!r}")

        print("\n== All elements whose class contains 'feature' or 'group' ==")
        feats = await page.locator("[class*='feature'], [class*='group']").all()
        print(f"(found {len(feats)} elements)")
        for i, el in enumerate(feats):
            try:
                cls = await el.get_attribute("class")
                text = (await el.inner_text(timeout=1500)).replace("\n", " | ")
            except Exception:
                continue
            if text.strip():
                print(f"  [{i}] class={cls!r}")
                print(f"        text={text[:120]!r}")

        print("\nBrowser stays open - press Enter here to close it.")
        input()
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
