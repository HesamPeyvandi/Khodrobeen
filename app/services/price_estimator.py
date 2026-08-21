"""Hamrah Mechanic ("همراه مکانیک") car price estimation adapter.

Hamrah Mechanic doesn't publish a public API for its pricing tool, so this
module drives the real web form with Playwright: open the car picker, choose
brand -> model -> year -> trim, fill mileage / body-status / color, submit,
and read back the estimated price (plus its reported min/max range).

WHY [class*="..."] INSTEAD OF FULL CLASS NAMES
-------------------------------------------------
This site is built with Next.js using CSS Modules, so class names look like
`detailRow_car-detail__car-name__fhOg7` - the `__fhOg7` suffix is a content
hash that gets regenerated on every site rebuild, but the prefix before it
(`detailRow_car-detail__car-name`) stays stable. Every selector below matches
on that stable prefix with a `[class*="..."]` "contains" selector instead of
the full class, so the scraper survives Hamrah Mechanic's next deploy even
though the hashes will have changed.

Selectors were filled in from live inspection of the real form (see the
project's git history / chat log for the raw HTML each one came from).

ONE KNOWN LIMITATION
-----------------------
Divar's "وضعیت بدنه" field is a closed list of ten standard categories
(e.g. "رنگ‌شدگی، در ۲ ناحیه", "دوررنگ", "تصادفی" - see
DIVAR_BODY_STATUS_MAP below), but it never says *which specific panels*
were painted or replaced - only how extensive the damage is. Hamrah
Mechanic's dialog wants specific parts ticked (کاپوت, سپر جلو, etc.), so
DIVAR_BODY_STATUS_MAP approximates "N areas affected" by ticking the N
most commonly-affected panels (front bumper, hood, front fenders first) on
the matching tab. This is a reasonable approximation for price purposes
(the count of affected panels matters more than exactly which ones) but
isn't pixel-perfect. Listings marked "اوراقی" (scrapped) skip estimation
entirely - see the top of estimate().
"""

import asyncio
import logging
import re
from dataclasses import dataclass

from playwright.async_api import Locator, Page, async_playwright

from app.config import settings
from app.services.text_utils import extract_number

logger = logging.getLogger(__name__)

BASE_URL = "https://www.hamrah-mechanic.com/carprice/"

SELECTORS = {
    "car_picker_input": 'input[name="car"]',
    "brand_model_input": 'input[name="brand-model"]',
    "mileage_input": 'input[name="kilometer"]',
    "body_status_input": 'input[placeholder="تعیین وضعیت بدنه"]',
    "color_input": 'input[placeholder="انتخاب رنگ خودرو"]',
    # Shared by brand/model, year and trim result lists alike.
    "picker_result_item": '[class*="car-detail__car-name"]',
    "body_status_confirm_button": 'button:has-text("تایید")',
    "color_option": '[class*="selectCarColor_color-name"]',
    "color_confirm_button": 'button:has-text("تایید")',
    "submit_button": 'button[type="submit"]:has-text("محاسبه قیمت")',
    "result_price_main": '[class*="info-box__price__"]',
    "result_price_range": '[class*="info-box__row-price__"]',
}

TAB_NAMES = {
    "brand_model": "برند و مدل",
    "year": "سال ساخت",
    "trim": "تیپ",
}

BODY_STATUS_TABS = {
    "paint": "رنگ‌شدگی",
    "replaced": "تعویض‌شدگی",
}

# Persian labels for body parts as they appear in the Hamrah Mechanic
# checkbox list, ordered roughly by how often each is the one actually
# painted/replaced on a used car (front-facing panels first) - used when we
# only know a *count* of affected areas (from Divar's category below) but
# not which specific panels. The English `id` on each <input> (e.g.
# id="Hood") is also stable if you'd rather match on that instead of the
# Persian label text.
PART_PRIORITY = [
    "سپر جلو",
    "کاپوت",
    "گلگیر جلو راست",
    "گلگیر جلو چپ",
    "درب راست جلو",
    "درب چپ جلو",
    "سپر عقب",
    "صندوق عقب",
    "درب راست عقب",
    "درب چپ عقب",
    "سقف",
    "گلگیر عقب راست",
    "گلگیر عقب چپ",
]
KNOWN_BODY_PARTS = PART_PRIORITY  # kept as an alias for backwards compatibility

# Divar's "وضعیت بدنه" field is a closed list, not free text - every listing
# has exactly one of these ten values. Mapping each one to how many parts
# (and on which Hamrah Mechanic tab) to tick is necessarily approximate
# since Divar doesn't say *which* panels, only how extensive the damage is.
# "skip" means: don't bother estimating at all (see estimate()).
DIVAR_BODY_STATUS_MAP: dict[str, dict] = {
    "سالم و بی‌خط و خش": {"tab": None, "count": 0},
    "خط و خش جزیی": {"tab": None, "count": 0},
    "صافکاری بی‌رنگ": {"tab": None, "count": 0},  # bodywork done, but no paint applied
    "رنگ‌شدگی، در ۱ ناحیه": {"tab": "paint", "count": 1},
    "رنگ‌شدگی، در ۲ ناحیه": {"tab": "paint", "count": 2},
    "رنگ‌شدگی، در چند ناحیه": {"tab": "paint", "count": 3},
    "دوررنگ": {"tab": "paint", "count": len(PART_PRIORITY) // 2},
    "تمام‌رنگ": {"tab": "paint", "count": len(PART_PRIORITY)},
    "تصادفی": {"tab": "replaced", "count": 3},
    "اوراقی": {"tab": "skip", "count": 0},
}

# Fallback keyword check for free-text body_status values that don't
# exactly match the closed list above (e.g. if Divar adds new categories,
# or the value came from somewhere other than the standard field).
HEALTHY_KEYWORDS = ["بدون رنگ", "سالم", "بی‌رنگ", "فاقد رنگ"]


@dataclass
class CarSpec:
    brand: str
    model: str
    year: str | None = None
    trim: str | None = None
    mileage_km: float | None = None
    color: str | None = None
    body_status: str | None = None


@dataclass
class EstimateResult:
    estimated_price_toman: float | None
    min_price_toman: float | None
    max_price_toman: float | None
    raw_text: str | None
    success: bool
    error: str | None = None


class HamrahMechanicEstimator:
    def __init__(self) -> None:
        self._playwright = None
        self._browser = None

    async def __aenter__(self) -> "HamrahMechanicEstimator":
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=settings.headless_browser
        )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    async def estimate(self, spec: CarSpec) -> EstimateResult:
        if (spec.body_status or "").strip() == "اوراقی":
            return EstimateResult(
                estimated_price_toman=None,
                min_price_toman=None,
                max_price_toman=None,
                raw_text=None,
                success=False,
                error="listing marked as اوراقی (scrapped) - not worth a price estimate, skipped",
            )

        assert self._browser is not None
        context = await self._browser.new_context(locale="fa-IR")
        page = await context.new_page()
        try:
            await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(1500)  # let the React app hydrate

            await self._select_car(page, spec)
            await self._fill_mileage(page, spec)
            await self._fill_body_status(page, spec)
            await self._fill_color(page, spec)

            submit = page.locator(SELECTORS["submit_button"])
            if not await submit.count():
                return EstimateResult(
                    estimated_price_toman=None,
                    min_price_toman=None,
                    max_price_toman=None,
                    raw_text=None,
                    success=False,
                    error="submit button not found - required fields may be incomplete",
                )
            await submit.first.click()
            await page.wait_for_timeout(2500)  # estimate calculation + render

            price_el = page.locator(SELECTORS["result_price_main"])
            if not await price_el.count():
                return EstimateResult(
                    estimated_price_toman=None,
                    min_price_toman=None,
                    max_price_toman=None,
                    raw_text=None,
                    success=False,
                    error="result price element not found after submit",
                )

            raw_text = await price_el.first.inner_text()
            price = extract_number(raw_text)

            min_price = max_price = None
            range_els = await page.locator(SELECTORS["result_price_range"]).all()
            if len(range_els) >= 2:
                min_price = extract_number(await range_els[0].inner_text())
                max_price = extract_number(await range_els[1].inner_text())

            return EstimateResult(
                estimated_price_toman=price,
                min_price_toman=min_price,
                max_price_toman=max_price,
                raw_text=raw_text,
                success=price is not None,
            )
        except Exception as exc:  # noqa: BLE001 - report any failure upstream instead of crashing the scan
            logger.exception("Hamrah Mechanic estimate failed for %s %s", spec.brand, spec.model)
            return EstimateResult(
                estimated_price_toman=None,
                min_price_toman=None,
                max_price_toman=None,
                raw_text=None,
                success=False,
                error=str(exc),
            )
        finally:
            await page.close()
            await asyncio.sleep(settings.request_delay_seconds)

    # -- car picker (brand/model -> year -> trim) ---------------------------

    async def _select_car(self, page: Page, spec: CarSpec) -> None:
        await page.locator(SELECTORS["car_picker_input"]).click()
        await page.wait_for_timeout(500)

        await self._pick_brand_model(page, spec)
        await self._pick_from_tab(page, TAB_NAMES["year"], spec.year)
        await self._pick_from_tab(page, TAB_NAMES["trim"], spec.trim)

    async def _pick_brand_model(self, page: Page, spec: CarSpec) -> None:
        tab = page.get_by_role("tab", name=TAB_NAMES["brand_model"])
        if await tab.count():
            await tab.first.click()
            await page.wait_for_timeout(300)

        query = f"{spec.brand} {spec.model}".strip()
        await page.locator(SELECTORS["brand_model_input"]).fill(query)
        await page.wait_for_timeout(800)  # debounce + results render

        matched = await self._click_matching_result(page, spec.model)
        if not matched:
            matched = await self._click_matching_result(page, spec.brand)
        if not matched:
            logger.warning("Hamrah Mechanic: no brand/model match found for '%s'", query)

    async def _pick_from_tab(self, page: Page, tab_label: str, desired_value: str | None) -> None:
        tab = page.get_by_role("tab", name=tab_label)
        if not await tab.count():
            return
        await tab.first.click()
        await page.wait_for_timeout(400)

        if desired_value and await self._click_matching_result(page, desired_value):
            return
        if desired_value:
            logger.warning(
                "Hamrah Mechanic: value '%s' not found under tab '%s', picking first option instead",
                desired_value,
                tab_label,
            )

        options = page.locator(SELECTORS["picker_result_item"])
        if await options.count():
            await options.first.click()
            await page.wait_for_timeout(400)

    async def _click_matching_result(self, page: Page, text: str) -> bool:
        if not text:
            return False
        option: Locator = page.locator(SELECTORS["picker_result_item"]).filter(has_text=text)
        if await option.count():
            await option.first.click()
            await page.wait_for_timeout(400)
            return True
        return False

    # -- mileage --------------------------------------------------------------

    async def _fill_mileage(self, page: Page, spec: CarSpec) -> None:
        if spec.mileage_km is None:
            return
        field = page.locator(SELECTORS["mileage_input"])
        if await field.count():
            await field.first.fill(str(int(spec.mileage_km)))

    # -- body status ------------------------------------------------------------

    def _extract_mentioned_parts(self, body_status: str) -> list[str]:
        return [part for part in PART_PRIORITY if part in body_status]

    async def _fill_body_status(self, page: Page, spec: CarSpec) -> None:
        field = page.locator(SELECTORS["body_status_input"])
        if not await field.count():
            return
        await field.first.click()
        await page.wait_for_timeout(400)

        text = (spec.body_status or "").strip()
        parts_to_tick, tab_key = self._resolve_body_status(text)

        if parts_to_tick and tab_key:
            tab_label = BODY_STATUS_TABS[tab_key]
            tab = page.get_by_text(tab_label, exact=True)
            if await tab.count():
                await tab.first.click()
                await page.wait_for_timeout(300)
            for part in parts_to_tick:
                checkbox_label = page.get_by_text(part, exact=True)
                if await checkbox_label.count():
                    await checkbox_label.first.click()
                    await page.wait_for_timeout(200)

        confirm = page.locator(SELECTORS["body_status_confirm_button"])
        if await confirm.count():
            await confirm.first.click()
            await page.wait_for_timeout(300)
        else:
            await page.keyboard.press("Escape")

    def _resolve_body_status(self, text: str) -> tuple[list[str], str | None]:
        """Returns (parts_to_tick, tab_key) for the given Divar body-status
        text. Prefers an exact match against Divar's closed list
        (DIVAR_BODY_STATUS_MAP); falls back to a loose keyword/part-name
        scan for anything that doesn't match exactly (e.g. Divar adding a
        new category, or the value coming from a non-standard source).
        """
        if not text:
            return [], None

        mapping = DIVAR_BODY_STATUS_MAP.get(text)
        if mapping is not None:
            tab_key = mapping["tab"]
            count = mapping["count"]
            if tab_key in (None, "skip") or count == 0:
                return [], None
            return PART_PRIORITY[:count], tab_key

        if any(keyword in text for keyword in HEALTHY_KEYWORDS):
            return [], None

        mentioned = self._extract_mentioned_parts(text)
        if mentioned:
            logger.info(
                "Hamrah Mechanic: body_status '%s' didn't match Divar's standard list, "
                "using keyword fallback (%d part(s) matched)",
                text,
                len(mentioned),
            )
            return mentioned, "paint"

        logger.info(
            "Hamrah Mechanic: body_status '%s' matched nothing (standard list or keywords), "
            "leaving all checkboxes unticked",
            text,
        )
        return [], None

    # -- color --------------------------------------------------------------

    async def _fill_color(self, page: Page, spec: CarSpec) -> None:
        if not spec.color:
            return
        field = page.locator(SELECTORS["color_input"])
        if not await field.count():
            return
        await field.first.click()
        await page.wait_for_timeout(400)

        option = page.locator(SELECTORS["color_option"]).filter(has_text=spec.color)
        if await option.count():
            await option.first.click()
            await page.wait_for_timeout(400)

            confirm = page.locator(SELECTORS["color_confirm_button"])
            if await confirm.count():
                await confirm.first.click()
                await page.wait_for_timeout(300)
            else:
                await page.keyboard.press("Escape")
        else:
            logger.warning("Hamrah Mechanic: color '%s' not found in picker, leaving unselected", spec.color)
            await page.keyboard.press("Escape")


if __name__ == "__main__":
    # Quick manual smoke test:
    #   python -m app.services.price_estimator
    # Run with HEADLESS_BROWSER=false in .env the first few times so you can
    # watch it click through the form and confirm each step visually.
    async def _main() -> None:
        spec = CarSpec(
            brand="پژو",
            model="206",
            year="1401",
            trim="تیپ 2",
            mileage_km=85000,
            color="سفید",
            body_status="دوررنگ",
        )
        async with HamrahMechanicEstimator() as estimator:
            result = await estimator.estimate(spec)
            print(result)

    asyncio.run(_main())
