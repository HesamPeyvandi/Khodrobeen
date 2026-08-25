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

If page loads keep timing out, see the matching note in divar_client.py -
the same `PAGE_TIMEOUT_MS` / `PAGE_GOTO_RETRIES` / `SCRAPER_PROXY_URL`
settings apply here too, since Hamrah Mechanic is also an Iran-hosted site.
"""

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from playwright.async_api import Locator, Page, TimeoutError as PlaywrightTimeoutError, async_playwright

from app.config import settings
from app.services.text_utils import extract_number, normalize_digits

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

# Dedicated checkbox Hamrah Mechanic shows for a car with no paint/panel
# damage at all - used instead of leaving every per-part checkbox unticked,
# for any Divar status that positively means "healthy" (see
# _resolve_body_status / HEALTHY_KEYWORDS below).
HEALTHY_BODY_STATUS_LABEL = "بدون رنگ و تعویض‌شدگی"

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


def _model_word_prefixes(model_words: list[str], max_words: int = 3) -> list[str]:
    """Generates decreasing-length prefixes of the model's words, most
    specific (multi-word) first - e.g. ["تیبا", "2", "(هاچبک)", "EX"] ->
    ["تیبا 2", "تیبا"]. Words that are just punctuation ("-") or start with
    a bracket are dropped, since Hamrah Mechanic's own model names are
    almost never that decorated and including them just wastes a query.
    """
    cleaned = [w for w in model_words if w not in ("-",) and not w.startswith("(")]
    prefixes = []
    for n in range(min(max_words, len(cleaned)), 0, -1):
        prefix = " ".join(cleaned[:n])
        if prefix not in prefixes:
            prefixes.append(prefix)
    return prefixes


def _strip_trim_suffix(word: str) -> str | None:
    """Given a token like '207i' or '405SLX', returns the leading numeric
    model code ('207', '405') if the token is digits followed by extra
    letters. Hamrah Mechanic's model list usually only has the bare number
    (e.g. "پژو 207") - suffixes like i/SLX/TU3 are trim details, picked
    separately on the "تیپ" tab, and never appear as their own model entry.
    Returns None if the word is already bare digits or has no leading digits.
    """
    match = re.match(r"(\d+)", word)
    if match and match.group(1) != word:
        return match.group(1)
    return None


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


_RETRY_BOOKKEEPING_PATTERNS = (
    re.compile(r"^retrying click action"),
    re.compile(r"^waiting \d+m?s$"),
    re.compile(r"^attempting click action$"),
)


def _last_diagnostic_line(error_text: str) -> str:
    """Playwright's error text is a multi-line "Call log" ending with
    whatever specific condition it was stuck on when it gave up (e.g.
    "element is not enabled", "subtree intercepts pointer events",
    "waiting for scheduled navigations to finish") - that's far more useful
    for diagnosis than the generic first line ("Timeout 30000ms exceeded").

    When Playwright retries the actionability check many times before
    giving up, the last lines are just its own retry bookkeeping
    ("retrying click action, attempt #13", "waiting 500ms") repeated every
    cycle - not informative on their own. Those are filtered out so we
    surface the actual repeated condition instead (e.g. "element is not
    visible") rather than just "it kept retrying".
    """
    lines = [line.strip(" -\t") for line in error_text.strip().splitlines() if line.strip()]
    if not lines:
        return "unknown error"
    substantive = [
        line for line in lines
        if not any(pattern.match(line) for pattern in _RETRY_BOOKKEEPING_PATTERNS)
    ]
    pool = substantive if substantive else lines
    tail = pool[-2:] if len(pool) >= 2 else pool
    return " | ".join(tail)


class HamrahMechanicEstimator:
    async def _safe_click(
        self,
        locator: Locator,
        timeout: int = 5000,
        *,
        page: Page | None = None,
        debug_name: str | None = None,
    ) -> tuple[bool, str | None]:
        """click() with a short timeout instead of Playwright's 30s default,
        so one unclickable element (covered, off-screen, mid-animation, or
        just gone) never eats a huge chunk of the request budget.

        Returns (success, error_detail). error_detail is Playwright's own
        diagnostic text on failure (e.g. "element is not enabled", "subtree
        intercepts pointer events") - this is much more useful for figuring
        out *why* than a generic guess, so callers should surface it rather
        than discard it.

        If DEBUG_SCREENSHOT_ON_CLICK_FAILURE is on and both `page` and
        `debug_name` are given, a failure also saves a screenshot to
        ./debug_screenshots/ - the fastest way to see *what* was actually
        covering the page (leftover modal, ad, cookie banner) without a
        live headed session, especially since this failure mode tends to
        affect every field on the page at once, not just one.
        """
        try:
            await locator.click(timeout=timeout)
            return True, None
        except Exception as exc:
            detail = _last_diagnostic_line(str(exc))
            if getattr(settings, "debug_screenshot_on_click_failure", False) and page is not None and debug_name is not None:
                await self._save_debug_screenshot(page, debug_name)
            return False, detail

    async def _save_debug_screenshot(self, page: Page, debug_name: str) -> None:
        try:
            out_dir = Path("debug_screenshots")
            out_dir.mkdir(exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            path = out_dir / f"{stamp}_{debug_name}.png"
            await page.screenshot(path=str(path), full_page=True)
            logger.info("Hamrah Mechanic: saved debug screenshot to %s", path)
        except Exception:
            logger.exception("Hamrah Mechanic: failed to save debug screenshot for '%s'", debug_name)

    async def _capture_failure_diagnostics(self, page: Page, debug_name: str, max_chars: int = 300) -> str:
        """Grabs a short excerpt of whatever text is actually visible on
        the page right now, for embedding directly in an EstimateResult's
        error message - this shows up in the admin panel's failures table
        without needing to enable screenshots or read server logs, and
        tells us the real reason (a validation message, an error banner, a
        still-loading spinner, etc.) instead of guessing. Also saves a
        screenshot if DEBUG_SCREENSHOT_ON_CLICK_FAILURE is on.
        """
        if getattr(settings, "debug_screenshot_on_click_failure", False):
            await self._save_debug_screenshot(page, debug_name)
        try:
            body_text = await page.locator("body").inner_text(timeout=2000)
            excerpt = " ".join(body_text.split())[:max_chars]
            return excerpt or "(page body was empty)"
        except Exception as exc:
            return f"(couldn't read page text: {exc.__class__.__name__})"

    async def _dismiss_overlays(self, page: Page) -> None:
        """Best-effort attempt to close common overlay patterns (cookie
        consent banners, promo/app-install popups, etc.) that can sit on
        top of the whole page and block every subsequent click. Safe to
        call even when nothing is actually there - every step here is a
        short, non-fatal, best-effort check.
        """
        for text in ("متوجه شدم", "قبول دارم", "باشه", "بستن", "قبول"):
            try:
                btn = page.get_by_text(text, exact=True)
                if await btn.count():
                    await btn.first.click(timeout=1500)
                    await page.wait_for_timeout(200)
            except Exception:
                pass
        try:
            await page.keyboard.press("Escape")
        except Exception:
            pass

    async def _close_car_picker_modal(self, page: Page) -> None:
        """The brand/model/year/trim picker opens inside a modal
        (`#modal-root .modal_container...`, confirmed from a live error:
        "modal_container ... subtree intercepts pointer events"). If it's
        never explicitly closed after picking a trim, it stays open on top
        of the mileage/body-status/color inputs below it and blocks every
        click to them - this was the root cause behind "not clickable"
        failures on nearly every field, not just the car picker itself.

        Tries, in order: the same "تایید" confirm button pattern used
        elsewhere on this site, then Escape, then clicking the modal
        backdrop directly. Each step is best-effort; if fields after this
        point are still failing with "subtree intercepts pointer events",
        check a debug screenshot (see DEBUG_SCREENSHOT_ON_CLICK_FAILURE)
        taken right after this function runs to see whether the modal is
        still open.
        """
        confirm = page.locator(SELECTORS["body_status_confirm_button"])  # same "تایید" pattern
        if await confirm.count():
            try:
                await confirm.first.click(timeout=3000)
                await page.wait_for_timeout(300)
            except Exception:
                pass

        modal = page.locator("#modal-root .modal_container__ltdkG, #modal-root [class*='modal_container']")
        if not await modal.count():
            return  # confirm button (or nothing) already closed it

        try:
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(300)
        except Exception:
            pass

        if not await modal.count():
            return

        # Last resort: click the backdrop (#modal-root itself, near a
        # corner so we don't accidentally hit the modal content) to
        # dismiss it like a click-outside-to-close pattern.
        try:
            await page.locator("#modal-root").first.click(position={"x": 5, "y": 5}, timeout=2000)
            await page.wait_for_timeout(300)
        except Exception:
            logger.warning(
                "Hamrah Mechanic: car picker modal may still be open after trim selection - "
                "later fields might fail with 'subtree intercepts pointer events'"
            )

    def __init__(self) -> None:
        self._playwright = None
        self._browser = None
        # Scoped to this instance's lifetime, which is exactly one scan
        # cycle (see app/scheduler/jobs.py) - so the breaker naturally
        # resets fresh every cycle rather than staying tripped forever.
        self._consecutive_navigation_failures = 0
        self._breaker_tripped = False

    async def __aenter__(self) -> "HamrahMechanicEstimator":
        self._playwright = await async_playwright().start()
        launch_kwargs: dict = {"headless": settings.headless_browser}
        if settings.scraper_proxy_url:
            launch_kwargs["proxy"] = {"server": settings.scraper_proxy_url}
        self._browser = await self._playwright.chromium.launch(**launch_kwargs)
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

        if self._breaker_tripped:
            return EstimateResult(
                estimated_price_toman=None,
                min_price_toman=None,
                max_price_toman=None,
                raw_text=None,
                success=False,
                error=(
                    "skipped - Hamrah Mechanic failed "
                    f"{settings.estimator_circuit_breaker_threshold}+ times in a row this cycle, "
                    "not retrying further this scan (see SCRAPER_PROXY_URL if this persists)"
                ),
            )

        assert self._browser is not None
        context = await self._browser.new_context(locale="fa-IR")
        page = await context.new_page()
        try:
            await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=settings.page_timeout_ms)
            self._consecutive_navigation_failures = 0  # reached the site - reset the breaker
            await page.wait_for_timeout(1500)  # let the React app hydrate
            await self._dismiss_overlays(page)

            await self._select_car(page, spec)
            await self._fill_mileage(page, spec)
            await self._fill_body_status(page, spec)
            await self._fill_color(page, spec)

            submit = page.locator(SELECTORS["submit_button"])
            if not await submit.count():
                page_excerpt = await self._capture_failure_diagnostics(page, "submit_button_missing")
                return EstimateResult(
                    estimated_price_toman=None,
                    min_price_toman=None,
                    max_price_toman=None,
                    raw_text=None,
                    success=False,
                    error=f"submit button not found - page text: {page_excerpt}",
                )
            submit_clicked, submit_error = await self._safe_click(submit.first, page=page, debug_name="submit_button")
            if not submit_clicked:
                return EstimateResult(
                    estimated_price_toman=None,
                    min_price_toman=None,
                    max_price_toman=None,
                    raw_text=None,
                    success=False,
                    error=f"submit button not clickable: {submit_error}",
                )
            # Wait adaptively for the result element to actually appear
            # instead of a fixed sleep - gives slow calculations more time
            # while not wasting time when it's fast.
            try:
                await page.wait_for_selector(SELECTORS["result_price_main"], timeout=8000)
            except Exception:
                pass  # fall through to the diagnostic capture below

            price_el = page.locator(SELECTORS["result_price_main"])
            if not await price_el.count():
                page_excerpt = await self._capture_failure_diagnostics(page, "result_price_missing")
                return EstimateResult(
                    estimated_price_toman=None,
                    min_price_toman=None,
                    max_price_toman=None,
                    raw_text=None,
                    success=False,
                    error=f"result price element not found after submit - page text: {page_excerpt}",
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
            if isinstance(exc, PlaywrightTimeoutError):
                self._consecutive_navigation_failures += 1
                if self._consecutive_navigation_failures >= settings.estimator_circuit_breaker_threshold:
                    self._breaker_tripped = True
                    logger.warning(
                        "Hamrah Mechanic failed %d times in a row - tripping circuit breaker for the "
                        "rest of this scan cycle",
                        self._consecutive_navigation_failures,
                    )
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
        clicked, detail = await self._safe_click(
            page.locator(SELECTORS["car_picker_input"]), page=page, debug_name="car_picker_input"
        )
        if not clicked:
            logger.warning("Hamrah Mechanic: car picker input not clickable - %s", detail)
            return
        await page.wait_for_timeout(500)

        await self._pick_brand_model(page, spec)
        await self._pick_year_tab(page, spec.year)
        await self._pick_from_tab(page, TAB_NAMES["trim"], spec.trim)
        await self._close_car_picker_modal(page)

    async def _pick_brand_model(self, page: Page, spec: CarSpec) -> None:
        tab = page.get_by_role("tab", name=TAB_NAMES["brand_model"])
        if await tab.count():
            clicked, detail = await self._safe_click(tab.first, page=page, debug_name="brand_model_tab")
            if not clicked:
                logger.warning("Hamrah Mechanic: '%s' tab found but not clickable - %s", TAB_NAMES["brand_model"], detail)
            await page.wait_for_timeout(300)

        # Divar's "برند و مدل" field often bundles trim/gearbox/engine details
        # onto the model (e.g. "کرولا اتوماتیک XLI - 1800cc", "تیبا 2
        # (هاچبک) EX", "207i دنده‌ای TU3"), but Hamrah Mechanic's own model
        # list only has clean model names - sometimes just the base name
        # ("کرولا"), sometimes base name + a distinguishing number/code
        # ("تیبا 2", "دنا پلاس") as its OWN separate entry. Trying only the
        # single first word conflates these: "تیبا" alone matches the
        # generic "تیبا" entry before Hamrah Mechanic even shows "تیبا 2",
        # picking the wrong (and differently priced) car.
        #
        # So instead we try decreasing-length word prefixes of the model
        # text - most specific multi-word combination first, falling back
        # to shorter ones - plus a digit-only version of the first word for
        # cases like "207i" -> "207" where the suffix is purely a trim code
        # glued onto a model number.
        #
        # Order matters overall: word-prefix candidates are tried BEFORE
        # anything brand-prefixed, since domestic manufacturer names (e.g.
        # "ایران خودرو") aren't reliably spelled the way Hamrah Mechanic's
        # search expects (spacing/ZWNJ differences) and can return some
        # unrelated non-empty result set. We only fall back to "click the
        # first result" as an absolute last resort, after every candidate
        # has failed to produce an exact text match - never mid-loop just
        # because *a* result showed up.
        model_words = spec.model.split() if spec.model else []
        word_prefixes = _model_word_prefixes(model_words)
        first_word = model_words[0] if model_words else None
        bare_model = _strip_trim_suffix(first_word) if first_word else None

        candidate_queries: list[str] = list(word_prefixes)
        if bare_model and bare_model not in candidate_queries:
            candidate_queries.append(bare_model)
        if spec.brand and spec.model:
            candidate_queries.append(f"{spec.brand} {spec.model}")
            if first_word:
                candidate_queries.append(f"{spec.brand} {first_word}")
            if bare_model:
                candidate_queries.append(f"{spec.brand} {bare_model}")
        if spec.brand:
            candidate_queries.append(spec.brand)

        # Text to try matching a result against, most specific first.
        match_candidates = list(word_prefixes)
        if bare_model and bare_model not in match_candidates:
            match_candidates.append(bare_model)

        matched = False
        for query in candidate_queries:
            query = query.strip()
            if not query:
                continue
            await page.locator(SELECTORS["brand_model_input"]).fill(query)
            await page.wait_for_timeout(800)  # debounce + results render

            results = page.locator(SELECTORS["picker_result_item"])
            if not await results.count():
                continue  # this query returned nothing - try the next one

            for candidate_text in match_candidates:
                if await self._click_matching_result(page, candidate_text):
                    matched = True
                    break
            if matched:
                break
            # This query returned results but none of them actually matched
            # our model text - don't settle for whatever's first, keep
            # trying other queries first.

        if not matched:
            # Absolute last resort: nothing matched anywhere. Re-run the
            # most specific query (a word prefix, not the broader
            # brand-prefixed ones) and take whatever it shows, so we at
            # least land on *a* car close to right rather than leaving the
            # picker empty.
            for query in candidate_queries:
                query = query.strip()
                if not query:
                    continue
                await page.locator(SELECTORS["brand_model_input"]).fill(query)
                await page.wait_for_timeout(800)
                results = page.locator(SELECTORS["picker_result_item"])
                if not await results.count():
                    continue
                try:
                    await results.first.scroll_into_view_if_needed(timeout=3000)
                    await results.first.click(timeout=3000)
                    await page.wait_for_timeout(400)
                    matched = True
                    logger.warning(
                        "Hamrah Mechanic: no exact match for '%s %s' - fell back to "
                        "the first result for query '%s'",
                        spec.brand, spec.model, query,
                    )
                except Exception:
                    continue
                break

        if not matched:
            logger.warning(
                "Hamrah Mechanic: no brand/model match found for '%s %s'",
                spec.brand,
                spec.model,
            )

    async def _pick_year_tab(self, page: Page, desired_year: str | None) -> None:
        tab = page.get_by_role("tab", name=TAB_NAMES["year"])
        if not await tab.count():
            return
        clicked, detail = await self._safe_click(tab.first, page=page, debug_name="year_tab")
        if not clicked:
            logger.warning("Hamrah Mechanic: '%s' tab found but not clickable - %s", TAB_NAMES["year"], detail)
            return
        await page.wait_for_timeout(400)

        if desired_year and await self._click_matching_result(page, desired_year):
            return

        # No exact match - this happens for brand-new model years that
        # Hamrah Mechanic's own database hasn't caught up with yet (e.g. a
        # car listed as "۱۴۰۵" before their year list has been updated).
        # Picking a genuinely random/oldest year here can leave the rest of
        # the form (trim options, etc.) inconsistent with the picked year
        # and break submission entirely - so pick whichever available year
        # is numerically closest to what we wanted instead of just "first".
        options = page.locator(SELECTORS["picker_result_item"])
        count = await options.count()
        if not count:
            return

        desired_num: int | None = None
        if desired_year:
            match = re.search(r"\d+", normalize_digits(desired_year))
            if match:
                desired_num = int(match.group(0))

        if desired_num is None:
            try:
                await options.first.scroll_into_view_if_needed(timeout=3000)
                await options.first.click(timeout=3000)
                await page.wait_for_timeout(400)
            except Exception:
                logger.warning("Hamrah Mechanic: couldn't click first option under tab '%s'", TAB_NAMES["year"])
            return

        best_index = None
        best_diff = None
        for i in range(count):
            try:
                text = await options.nth(i).inner_text(timeout=1500)
            except Exception:
                continue
            match = re.search(r"\d+", normalize_digits(text))
            if not match:
                continue
            diff = abs(int(match.group(0)) - desired_num)
            if best_diff is None or diff < best_diff:
                best_diff = diff
                best_index = i

        if best_index is None:
            logger.warning(
                "Hamrah Mechanic: no numeric year options found under tab '%s', picking first instead",
                TAB_NAMES["year"],
            )
            best_index = 0
        else:
            logger.warning(
                "Hamrah Mechanic: exact year '%s' not offered, picking closest available option instead",
                desired_year,
            )

        try:
            await options.nth(best_index).scroll_into_view_if_needed(timeout=3000)
            await options.nth(best_index).click(timeout=3000)
            await page.wait_for_timeout(400)
        except Exception:
            logger.warning("Hamrah Mechanic: closest year option wasn't clickable")

    async def _pick_from_tab(self, page: Page, tab_label: str, desired_value: str | None) -> None:
        tab = page.get_by_role("tab", name=tab_label)
        if not await tab.count():
            return
        clicked, detail = await self._safe_click(tab.first, page=page, debug_name=f"tab_{tab_label}")
        if not clicked:
            logger.warning("Hamrah Mechanic: '%s' tab found but not clickable - %s", tab_label, detail)
            return
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
            try:
                await options.first.scroll_into_view_if_needed(timeout=3000)
                await options.first.click(timeout=3000)
                await page.wait_for_timeout(400)
            except Exception:
                logger.warning(
                    "Hamrah Mechanic: couldn't click first option under tab '%s'", tab_label
                )

    async def _click_matching_result(self, page: Page, text: str) -> bool:
        if not text:
            return False
        option: Locator = page.locator(SELECTORS["picker_result_item"]).filter(has_text=text)
        if await option.count():
            try:
                await option.first.scroll_into_view_if_needed(timeout=3000)
                await option.first.click(timeout=3000)
            except Exception:
                logger.warning(
                    "Hamrah Mechanic: found a result matching '%s' but couldn't click it "
                    "(covered/off-screen/animating) - trying the next option",
                    text,
                )
                return False
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
        # For zero-mileage / brand-new cars, Hamrah Mechanic disables this
        # field entirely (there's nothing to report). Clicking a disabled
        # input never becomes "actionable", so Playwright would otherwise
        # wait out its full default timeout here on every new-car listing.
        if await field.first.is_disabled():
            logger.info("Hamrah Mechanic: body status field is disabled (new car) - skipping")
            return
        clicked, detail = await self._safe_click(field.first, page=page, debug_name="body_status_field")
        if not clicked:
            logger.warning("Hamrah Mechanic: body status field found but not clickable - %s", detail)
            return
        await page.wait_for_timeout(400)
        text = (spec.body_status or "").strip()
        parts_to_tick, tab_key, is_healthy = self._resolve_body_status(text)

        if parts_to_tick and tab_key:
            tab_label = BODY_STATUS_TABS[tab_key]
            tab = page.get_by_text(tab_label, exact=True)
            if await tab.count():
                sub_clicked, sub_detail = await self._safe_click(tab.first, page=page, debug_name="body_status_subtab")
                if sub_clicked:
                    await page.wait_for_timeout(300)
                else:
                    logger.warning("Hamrah Mechanic: body status sub-tab '%s' not clickable - %s", tab_label, sub_detail)
            for part in parts_to_tick:
                checkbox_label = page.get_by_text(part, exact=True)
                if await checkbox_label.count():
                    cb_clicked, cb_detail = await self._safe_click(checkbox_label.first, page=page, debug_name="body_part_checkbox")
                    if cb_clicked:
                        await page.wait_for_timeout(200)
                    else:
                        logger.warning("Hamrah Mechanic: body part checkbox '%s' not clickable - %s", part, cb_detail)
        elif is_healthy:
            # Divar positively reported no paint/panel damage - tick Hamrah
            # Mechanic's dedicated "healthy" checkbox instead of leaving
            # every per-part checkbox untouched (which reads as "unknown",
            # not "confirmed healthy").
            healthy_checkbox = page.get_by_text(HEALTHY_BODY_STATUS_LABEL, exact=True)
            if await healthy_checkbox.count():
                try:
                    await healthy_checkbox.first.scroll_into_view_if_needed(timeout=3000)
                except Exception:
                    pass  # not fatal - the click below will still be attempted
                hc_clicked, hc_detail = await self._safe_click(healthy_checkbox.first, page=page, debug_name="healthy_checkbox")
                if hc_clicked:
                    await page.wait_for_timeout(200)
                else:
                    logger.warning(
                        "Hamrah Mechanic: found '%s' checkbox but couldn't click it - %s",
                        HEALTHY_BODY_STATUS_LABEL,
                        hc_detail,
                    )
            else:
                logger.info(
                    "Hamrah Mechanic: healthy body status but couldn't find the "
                    "'%s' checkbox - leaving body status as-is",
                    HEALTHY_BODY_STATUS_LABEL,
                )

        confirm = page.locator(SELECTORS["body_status_confirm_button"])
        if await confirm.count():
            confirm_clicked, confirm_detail = await self._safe_click(confirm.first, page=page, debug_name="body_status_confirm")
            if confirm_clicked:
                await page.wait_for_timeout(300)
            else:
                logger.warning("Hamrah Mechanic: body status confirm button not clickable - %s", confirm_detail)
                await page.keyboard.press("Escape")
        else:
            await page.keyboard.press("Escape")

    def _resolve_body_status(self, text: str) -> tuple[list[str], str | None, bool]:
        """Returns (parts_to_tick, tab_key, is_healthy). Prefers an exact
        match against Divar's closed list (DIVAR_BODY_STATUS_MAP); falls
        back to a loose keyword/part-name scan for anything that doesn't
        match exactly (e.g. Divar adding a new category, or the value
        coming from a non-standard source). is_healthy distinguishes
        "Divar positively said no damage" (tick the dedicated healthy
        checkbox) from "couldn't tell" (leave the dialog untouched).
        """
        if not text:
            return [], None, False

        mapping = DIVAR_BODY_STATUS_MAP.get(text)
        if mapping is not None:
            tab_key = mapping["tab"]
            count = mapping["count"]
            if tab_key == "skip":
                return [], None, False
            if tab_key is None or count == 0:
                return [], None, True
            return PART_PRIORITY[:count], tab_key, False

        if any(keyword in text for keyword in HEALTHY_KEYWORDS):
            return [], None, True

        mentioned = self._extract_mentioned_parts(text)
        if mentioned:
            logger.info(
                "Hamrah Mechanic: body_status '%s' didn't match Divar's standard list, "
                "using keyword fallback (%d part(s) matched)",
                text,
                len(mentioned),
            )
            return mentioned, "paint", False

        logger.info(
            "Hamrah Mechanic: body_status '%s' matched nothing (standard list or keywords), "
            "leaving all checkboxes unticked",
            text,
        )
        return [], None, False

    # -- color --------------------------------------------------------------

    async def _fill_color(self, page: Page, spec: CarSpec) -> None:
        if not spec.color:
            return
        field = page.locator(SELECTORS["color_input"])
        if not await field.count():
            return
        clicked, detail = await self._safe_click(field.first, page=page, debug_name="color_field")
        if not clicked:
            logger.warning("Hamrah Mechanic: color field found but not clickable - %s", detail)
            return
        await page.wait_for_timeout(400)

        option = page.locator(SELECTORS["color_option"]).filter(has_text=spec.color)
        if await option.count():
            try:
                await option.first.scroll_into_view_if_needed(timeout=3000)
                await option.first.click(timeout=3000)
                await page.wait_for_timeout(400)
            except Exception:
                logger.warning(
                    "Hamrah Mechanic: found color '%s' but couldn't click it - "
                    "pressing Escape instead", spec.color
                )
                await page.keyboard.press("Escape")
                return

            # Some pickers close automatically on selection, others need an
            # explicit confirm click (same "تایید" button pattern as the
            # body-status dialog) - without it the picker stays open and
            # blocks every later step. Click it if it's there.
            confirm = page.locator(SELECTORS["body_status_confirm_button"])
            if await confirm.count():
                try:
                    await confirm.first.click(timeout=3000)
                    await page.wait_for_timeout(300)
                except Exception:
                    logger.warning(
                        "Hamrah Mechanic: color confirm button found but not "
                        "clickable - pressing Escape instead"
                    )
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
