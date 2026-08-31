"""Hamrah Mechanic ("همراه مکانیک") car price estimation adapter.

APPROACH: hybrid UI + direct API call
----------------------------------------
Hamrah Mechanic doesn't publish a documented public API, but its site is
Next.js, and confirmed via live Network-tab inspection: once a brand/model
/year/trim is selected and the "محاسبه قیمت" button is clicked, the page
does a client-side route change to `/carprice/{brand}/{model}/{year}/{typeId}/`,
which Next.js resolves by fetching
`/_next/data/{buildId}/carprice/{brand}/{model}/{year}/{typeId}.json?kilometer=...&clr=...&bodycondition=...&replacedparts=...`
and returns structured JSON directly (see `pageProps.price.{price,priceUp,priceDown}`).

So this module still uses Playwright to open the car picker and select
brand/model/year/trim (there's no separate confirmed search API for that
part), but instead of also filling the mileage/body-status/color UI and
scraping the result back out of the DOM - both of which were the source of
nearly every bug this project has hit (stuck modals, disabled tabs, click
timing, result-wait timing) - it reads the resolved `{brand}/{model}/{year}
/{typeId}` from the post-click URL, builds the JSON data-URL itself with
its own precise query parameters (computed straight from CarSpec, no DOM
interaction needed), fetches it via `page.request.get()` (same browser
session/cookies), and parses the response directly. Far fewer moving
parts, and none of the fragile pieces.

WHY [class*="..."] INSTEAD OF FULL CLASS NAMES
-------------------------------------------------
This site is built with Next.js using CSS Modules, so class names look like
`detailRow_car-detail__car-name__fhOg7` - the `__fhOg7` suffix is a content
hash that gets regenerated on every site rebuild, but the prefix before it
(`detailRow_car-detail__car-name`) stays stable. The (few) selectors below
that still target the picker UI use that stable prefix instead of the full
class, so they survive Hamrah Mechanic's next deploy even though the
hashes will have changed.

BODY STATUS MAPPING
-----------------------
Divar's "وضعیت بدنه" field is a closed list of ten standard categories
(e.g. "رنگ‌شدگی، در ۲ ناحیه", "دوررنگ", "تصادفی" - see
DIVAR_BODY_STATUS_MAP below), but it never says *which specific panels*
were painted or replaced - only how extensive the damage is. Hamrah
Mechanic's API wants specific part identifiers (confirmed real list, from
a live API response's `bodyParts`: Hood, Trunk, DoorFrontLeft,
DoorBackLeft, DoorFrontRight, DoorBackRight, FenderFrontLeft,
FenderBackLeft, FenderFrontRight, FenderBackRight, Roof - notably no
bumpers), so DIVAR_BODY_STATUS_MAP approximates "N areas affected" by
listing the N most commonly-affected panels. This is a reasonable
approximation for price purposes (the count of affected panels matters
more than exactly which ones) but isn't pixel-perfect. Listings marked
"اوراقی" (scrapped) skip estimation entirely - see the top of estimate().

If page loads keep timing out, see the matching note in divar_client.py -
the same `PAGE_TIMEOUT_MS` / `PAGE_GOTO_RETRIES` / `SCRAPER_PROXY_URL`
settings apply here too, since Hamrah Mechanic is also an Iran-hosted site.
"""

import asyncio
import logging
import re
from dataclasses import dataclass
from urllib.parse import urlencode

from playwright.async_api import Locator, Page, TimeoutError as PlaywrightTimeoutError, async_playwright

from app.config import settings
from app.services.text_utils import normalize_digits

logger = logging.getLogger(__name__)

BASE_URL = "https://www.hamrah-mechanic.com/carprice/"
DATA_URL_TEMPLATE = (
    "https://www.hamrah-mechanic.com/_next/data/{build_id}/carprice/{brand}/{model}/{year}/{type_id}.json"
)
# Matches the resolved car page URL Next.js routes to after a car is fully
# selected and submit is clicked, e.g.
# https://www.hamrah-mechanic.com/carprice/audi/a3l/2025/2934/
RESOLVED_URL_PATTERN = re.compile(r"/carprice/([^/]+)/([^/]+)/([^/]+)/([^/]+)/?(?:\?|$)")

SELECTORS = {
    "car_picker_input": 'input[name="car"]',
    "brand_model_input": 'input[name="brand-model"]',
    # Shared by brand/model, year and trim result lists alike.
    "picker_result_item": '[class*="car-detail__car-name"]',
    # Same "تایید" confirm-button pattern used to close the car picker modal.
    "body_status_confirm_button": 'button:has-text("تایید")',
    "submit_button": 'button[type="submit"]:has-text("محاسبه قیمت")',
}

TAB_NAMES = {
    "brand_model": "برند و مدل",
    "year": "سال ساخت",
    "trim": "تیپ",
}

# Confirmed directly from a live Hamrah Mechanic API response's `bodyParts`
# list - English identifiers used as-is in the `bodycondition` /
# `replacedparts` query params. Notably: no bumpers. Ordered by how often
# each is the one actually painted/replaced on a used car (front-facing
# panels first), used when we only know a *count* of affected areas (from
# Divar's category below) but not which specific panels.
BODY_PART_NAMES = [
    "Hood",
    "DoorFrontLeft",
    "DoorFrontRight",
    "FenderFrontLeft",
    "FenderFrontRight",
    "DoorBackLeft",
    "DoorBackRight",
    "FenderBackLeft",
    "FenderBackRight",
    "Trunk",
    "Roof",
]

# Divar's "وضعیت بدنه" field is a closed list, not free text - every listing
# has exactly one of these ten values. Mapping each one to how many parts
# were painted vs. replaced is necessarily approximate since Divar doesn't
# say *which* panels, only how extensive the damage is.
DIVAR_BODY_STATUS_MAP: dict[str, dict] = {
    "سالم و بی‌خط و خش": {"painted": 0, "replaced": 0},
    "خط و خش جزیی": {"painted": 0, "replaced": 0},
    "صافکاری بی‌رنگ": {"painted": 0, "replaced": 0},  # bodywork done, but no paint applied
    "رنگ‌شدگی، در ۱ ناحیه": {"painted": 1, "replaced": 0},
    "رنگ‌شدگی، در ۲ ناحیه": {"painted": 2, "replaced": 0},
    "رنگ‌شدگی، در چند ناحیه": {"painted": 3, "replaced": 0},
    "دوررنگ": {"painted": len(BODY_PART_NAMES) // 2, "replaced": 0},
    "تمام‌رنگ": {"painted": len(BODY_PART_NAMES), "replaced": 0},
    "تصادفی": {"painted": 0, "replaced": 3},
}
SCRAPPED_BODY_STATUS = "اوراقی"

# Fallback keyword check for free-text body_status values that don't
# exactly match the closed list above (e.g. if Divar adds new categories).
HEALTHY_KEYWORDS = ["بدون رنگ", "سالم", "بی‌رنگ", "فاقد رنگ"]

# Confirmed from a live API response's `colors` list - Divar's Persian
# color text is matched against these (substring match) to get Hamrah
# Mechanic's internal color identifier for the `clr` query param.
COLOR_NAME_MAP: dict[str, str] = {
    "سفید": "ColorWhite",
    "مشکی": "ColorBlack",
    "قرمز": "ColorRed",
    "نقره‌ای": "ColorSilver",
    "نقره ای": "ColorSilver",
    "نوک مدادی": "ColorGray",
    "خاکستری": "ColorGray",
}
DEFAULT_COLOR_NAME = "ColorOthers"


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


def _resolve_color(color_text: str | None) -> str:
    """Maps Divar's Persian color text to Hamrah Mechanic's internal color
    identifier for the `clr` query param (confirmed list - see
    COLOR_NAME_MAP). Falls back to "ColorOthers" for anything unmatched,
    same as Hamrah Mechanic's own picker does for uncommon colors.
    """
    if not color_text:
        return DEFAULT_COLOR_NAME
    text = color_text.strip()
    for key, value in COLOR_NAME_MAP.items():
        if key in text:
            return value
    return DEFAULT_COLOR_NAME


def _resolve_body_parts(body_status_text: str | None) -> tuple[list[str], list[str]]:
    """Returns (painted_part_names, replaced_part_names) using Hamrah
    Mechanic's own English part identifiers, ready to join directly into
    the `bodycondition` / `replacedparts` query params. See
    DIVAR_BODY_STATUS_MAP for how Divar's closed-list category maps to a
    count of affected panels.
    """
    text = (body_status_text or "").strip()
    mapping = DIVAR_BODY_STATUS_MAP.get(text)
    if mapping is None:
        if not text or any(keyword in text for keyword in HEALTHY_KEYWORDS):
            return [], []
        logger.info(
            "Hamrah Mechanic: body_status '%s' didn't match Divar's standard list - "
            "treating as healthy (no parts marked)",
            text,
        )
        return [], []
    painted = BODY_PART_NAMES[: mapping["painted"]]
    replaced = BODY_PART_NAMES[: mapping["replaced"]]
    return painted, replaced


class HamrahMechanicEstimator:
    async def _safe_click(self, locator: Locator, timeout: int = 5000) -> tuple[bool, str | None]:
        """click() with a short timeout instead of Playwright's 30s default,
        so one unclickable element (covered, off-screen, mid-animation, or
        just gone) never eats a huge chunk of the request budget.

        Returns (success, error_detail). error_detail is Playwright's own
        diagnostic text on failure (e.g. "element is not enabled", "subtree
        intercepts pointer events") - this is much more useful for figuring
        out *why* than a generic guess, so callers should surface it rather
        than discard it.
        """
        try:
            await locator.click(timeout=timeout)
            return True, None
        except Exception as exc:
            return False, _last_diagnostic_line(str(exc))

    async def _capture_failure_diagnostics(self, page: Page, max_chars: int = 300) -> str:
        """Grabs diagnostic text about the page right now, for embedding
        directly in an EstimateResult's error message - this shows up in
        the admin panel's failures table (hover the truncated cell, or open
        the row directly, to see the full text) without needing to read
        server logs.

        Includes the current URL (reveals whether submit navigated
        somewhere unexpected) plus a text excerpt. Common Persian
        validation/error keywords are searched for first, since those are
        far more diagnostic than whatever happens to be at the very top of
        the page (often just header/nav boilerplate).
        """
        url = page.url
        try:
            body_text = await page.locator("body").inner_text(timeout=2000)
            normalized = " ".join(body_text.split())
        except Exception as exc:
            return f"url={url} | (couldn't read page text: {exc.__class__.__name__})"

        keywords = ("لطفا", "خطا", "الزامی", "ناموفق", "اشتباه", "نامعتبر")
        for keyword in keywords:
            idx = normalized.find(keyword)
            if idx != -1:
                start = max(0, idx - 30)
                snippet = normalized[start : start + max_chars]
                return f"url={url} | ...{snippet}"

        excerpt = normalized[:max_chars] if normalized else "(page body was empty)"
        return f"url={url} | {excerpt}"

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
        the confirm-button guess may not be the actual close mechanism for
        this specific modal - needs live inspection to confirm.
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
        except Exception:
            pass

        # Poll instead of a single fixed wait - closing can be animated,
        # so checking a few times a bit apart is more reliable than one
        # check right after a single fixed delay.
        for _ in range(4):
            await page.wait_for_timeout(300)
            if not await modal.count():
                return

        # Last resort: click the backdrop (#modal-root itself, near a
        # corner so we don't accidentally hit the modal content) to
        # dismiss it like a click-outside-to-close pattern.
        try:
            await page.locator("#modal-root").first.click(position={"x": 5, "y": 5}, timeout=2000)
        except Exception:
            pass

        for _ in range(4):
            await page.wait_for_timeout(300)
            if not await modal.count():
                return

        logger.warning(
            "Hamrah Mechanic: car picker modal still open after all close attempts - "
            "later fields will likely fail with 'subtree intercepts pointer events'"
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
        if (spec.body_status or "").strip() == SCRAPPED_BODY_STATUS:
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

            car_selected = await self._select_car(page, spec)  # ends with _close_car_picker_modal()
            if not car_selected:
                return EstimateResult(
                    estimated_price_toman=None,
                    min_price_toman=None,
                    max_price_toman=None,
                    raw_text=None,
                    success=False,
                    error=(
                        f"'{spec.brand} {spec.model}' not found in Hamrah Mechanic's "
                        "car database (search returned no results)"
                    ),
                )

            submit = page.locator(SELECTORS["submit_button"])
            if not await submit.count():
                page_excerpt = await self._capture_failure_diagnostics(page)
                return EstimateResult(
                    estimated_price_toman=None,
                    min_price_toman=None,
                    max_price_toman=None,
                    raw_text=None,
                    success=False,
                    error=f"submit button not found - page text: {page_excerpt}",
                )
            submit_clicked, submit_error = await self._safe_click(submit.first)
            if not submit_clicked and submit_error and "intercepts pointer events" in submit_error:
                # Known case: a leftover car-picker modal is still visually
                # covering the page even after _close_car_picker_modal()'s
                # attempts. The submit button itself is confirmed correct
                # (just blocked), so force-clicking through the stale
                # overlay is safe here - unlike a blind force-click
                # anywhere else, this only fires for this one confirmed
                # failure signature.
                logger.info("Hamrah Mechanic: submit blocked by leftover modal - forcing the click through it")
                try:
                    await submit.first.click(force=True, timeout=3000)
                    submit_clicked = True
                except Exception as exc:
                    submit_error = _last_diagnostic_line(str(exc))
            if not submit_clicked:
                return EstimateResult(
                    estimated_price_toman=None,
                    min_price_toman=None,
                    max_price_toman=None,
                    raw_text=None,
                    success=False,
                    error=f"submit button not clickable: {submit_error}",
                )

            # Clicking submit triggers a client-side Next.js route change to
            # /carprice/{brand}/{model}/{year}/{typeId}/ - wait for that
            # instead of a fixed sleep, then read brand/model/year/typeId
            # straight out of the resolved URL.
            try:
                await page.wait_for_url(RESOLVED_URL_PATTERN, timeout=10000)
            except Exception:
                pass  # fall through - the regex check below reports it clearly either way

            match = RESOLVED_URL_PATTERN.search(page.url)
            if not match:
                page_excerpt = await self._capture_failure_diagnostics(page)
                return EstimateResult(
                    estimated_price_toman=None,
                    min_price_toman=None,
                    max_price_toman=None,
                    raw_text=None,
                    success=False,
                    error=f"page didn't navigate to a resolved car URL after submit - {page_excerpt}",
                )
            brand_slug, model_slug, year_slug, type_id = match.groups()

            build_id = await page.evaluate("() => window.__NEXT_DATA__ && window.__NEXT_DATA__.buildId")
            if not build_id:
                return EstimateResult(
                    estimated_price_toman=None,
                    min_price_toman=None,
                    max_price_toman=None,
                    raw_text=None,
                    success=False,
                    error=f"couldn't read Next.js buildId from the page (url={page.url})",
                )

            painted_parts, replaced_parts = _resolve_body_parts(spec.body_status)
            query: dict[str, str] = {}
            if spec.mileage_km is not None:
                query["kilometer"] = str(int(spec.mileage_km))
            query["clr"] = _resolve_color(spec.color)
            if not painted_parts and not replaced_parts:
                query["bodycondition"] = "WithoutColor"
                query["body"] = "noColoredOrChanged"
            else:
                query["bodycondition"] = ",".join(painted_parts) if painted_parts else "WithoutColor"
                if replaced_parts:
                    query["replacedparts"] = ",".join(replaced_parts)
            query["brand"] = brand_slug
            query["model"] = model_slug
            query["year"] = year_slug
            query["typeId"] = type_id

            data_url = DATA_URL_TEMPLATE.format(
                build_id=build_id, brand=brand_slug, model=model_slug, year=year_slug, type_id=type_id
            )
            data_url = f"{data_url}?{urlencode(query)}"

            response = await page.request.get(data_url, headers={"x-nextjs-data": "1"})
            if response.status != 200:
                body_excerpt = (await response.text())[:300]
                return EstimateResult(
                    estimated_price_toman=None,
                    min_price_toman=None,
                    max_price_toman=None,
                    raw_text=None,
                    success=False,
                    error=f"price API returned HTTP {response.status}: {body_excerpt}",
                )

            payload = await response.json()
            price_info = (payload.get("pageProps") or {}).get("price") or {}
            if price_info.get("price") is None:
                return EstimateResult(
                    estimated_price_toman=None,
                    min_price_toman=None,
                    max_price_toman=None,
                    raw_text=None,
                    success=False,
                    error=f"price API response had no price field (url={data_url})",
                )

            return EstimateResult(
                estimated_price_toman=price_info.get("price"),
                min_price_toman=price_info.get("priceDown"),
                max_price_toman=price_info.get("priceUp"),
                raw_text=str(price_info.get("price")),
                success=True,
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

    async def _select_car(self, page: Page, spec: CarSpec) -> bool:
        """Returns whether a brand/model was actually selected. False means
        this car isn't in Hamrah Mechanic's database (or the search text
        needs a different form) - callers should stop and report that
        clearly rather than continuing on to click submit against an empty
        form, which just produces a confusing "didn't navigate anywhere"
        error instead of the real reason.
        """
        clicked, detail = await self._safe_click(
            page.locator(SELECTORS["car_picker_input"])
        )
        if not clicked:
            logger.warning("Hamrah Mechanic: car picker input not clickable - %s", detail)
            return False
        await page.wait_for_timeout(500)

        matched = await self._pick_brand_model(page, spec)
        if not matched:
            return False

        await self._pick_year_tab(page, spec.year)
        await self._pick_from_tab(page, TAB_NAMES["trim"], spec.trim)
        await self._close_car_picker_modal(page)
        return True

    async def _pick_brand_model(self, page: Page, spec: CarSpec) -> bool:
        tab = page.get_by_role("tab", name=TAB_NAMES["brand_model"])
        if await tab.count():
            clicked, detail = await self._safe_click(tab.first)
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
                "Hamrah Mechanic: no brand/model match found for '%s %s' - not in Hamrah "
                "Mechanic's database (or the search text needs a different form)",
                spec.brand,
                spec.model,
            )
        return matched

    async def _pick_year_tab(self, page: Page, desired_year: str | None) -> None:
        tab = page.get_by_role("tab", name=TAB_NAMES["year"])
        if not await tab.count():
            return

        aria_disabled = await tab.first.get_attribute("aria-disabled")
        if aria_disabled == "true":
            logger.info("Hamrah Mechanic: '%s' tab is disabled for this car - skipping", TAB_NAMES["year"])
            return

        clicked, detail = await self._safe_click(tab.first)
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

        aria_disabled = await tab.first.get_attribute("aria-disabled")
        if aria_disabled == "true":
            # This car genuinely has no data for this tab (e.g. no trim
            # variants) - clicking would just fail with "element is not
            # enabled" and, worse, leave the picker modal in a state where
            # it can't be confirmed/closed normally, which then blocks
            # every field after it. Skipping cleanly here is the fix.
            logger.info("Hamrah Mechanic: '%s' tab is disabled for this car - skipping", tab_label)
            return

        clicked, detail = await self._safe_click(tab.first)
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
