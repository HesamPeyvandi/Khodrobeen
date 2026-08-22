"""Divar scraping adapter.

Divar has no public API for reading third-party listings (their "Kenar"
platform is for posting/managing your own ads and chat-bots, not for
searching other users' ads), and https://divar.ir/robots.txt disallows
automated crawlers. This module talks to the public pages anyway, at a low
request rate, for personal/non-commercial monitoring - use it responsibly:
keep REQUEST_DELAY_SECONDS reasonable and don't run many parallel instances.

IMPORTANT - SELECTORS WILL NEED MAINTENANCE.
Divar's frontend markup changes periodically. All CSS selectors used to
read listing cards and detail pages live in the SELECTORS dict below. If
scraping starts returning empty results, open the affected page in a real
browser, use "Inspect element" on the relevant piece of data, and update
the matching selector here - the rest of the codebase never needs to change.

Divar's detail page shows specs in two different shapes, confirmed by
inspecting a live listing:
  1. Simple label:value rows (class `kt-unexpandable-row`) - e.g.
     "برند و مدل", "گیربکس", "نوع سوخت", "قیمت پایه".
  2. A grouped column table (class `kt-group-row`) - e.g. three headers
     "کارکرد / مدل (سال تولید) / رنگ" each paired by position with a
     value in a parallel row.
We use exact class-name matches (not `[class*=...]` "contains" selectors)
for the row containers themselves, since the substring version was also
matching nested child elements and corrupting the extraction.

IF DETAIL PAGES KEEP TIMING OUT
----------------------------------
Sites hosted in Iran (Divar included) are often placed behind an Iranian
CDN/anti-bot layer (Arvan Cloud is common) that can slow-walk or silently
drop traffic that looks automated - especially many sequential page loads
from a single non-Iranian IP, which is exactly what this scraper does when
deployed on a server outside Iran (e.g. Render). If you see repeated
`Timeout ... exceeded` errors on `get_listing_detail` but `list_new_listings`
works fine, this is the most likely cause. Three independent mitigations,
all controlled from `.env` (see `.env.example`):

  1. `PAGE_TIMEOUT_MS` - raise this (e.g. to 60000-90000) if pages are just
     slow rather than fully blocked.
  2. `PAGE_GOTO_RETRIES` - each navigation is retried this many times with
     a growing backoff before giving up.
  3. `SCRAPER_PROXY_URL` - route the scraper's browser traffic through a
     proxy with a good path into Iran. This is the most reliable fix if (1)
     and (2) don't help, since it addresses the actual network path instead
     of just waiting longer for a blocked/degraded connection.
"""

import asyncio
import logging
import random
import re
from dataclasses import dataclass, field

from playwright.async_api import Browser, Page, async_playwright

from app.config import settings
from app.services.text_utils import clean_whitespace, extract_number

logger = logging.getLogger(__name__)

BASE_URL = "https://divar.ir"

# Edit these if Divar changes its markup.
SELECTORS = {
    # Search results page (https://divar.ir/s/<city>/<category>)
    "listing_card_link": "article a[href*='/v/']",
    # Listing detail page (https://divar.ir/v/<slug>/<token>)
    "detail_title": "h1",
    "detail_price": ".kt-unexpandable-row :text('تومان')",
    # Shape 1: simple label:value rows
    "detail_unexpandable_row": ".kt-unexpandable-row",
    "detail_unexpandable_row_title": ".kt-unexpandable-row__title",
    "detail_unexpandable_row_value": ".kt-unexpandable-row__value, .kt-unexpandable-row__action",
    # Shape 2: grouped column table (headers and values line up by position)
    "detail_group_row": ".kt-group-row",
    "detail_group_row_header": ".kt-group-row-item__header",
    "detail_group_row_value": ".kt-group-row-item__value",
}

SPEC_LABELS = {
    "brand_model": ("خودرو", "برند", "مدل"),
    "year": ("سال ساخت", "سال"),
    "mileage": ("کارکرد",),
    "color": ("رنگ",),
    "body_status": ("وضعیت بدنه",),
    "gearbox": ("گیربکس",),
}


@dataclass
class ListingSummary:
    token: str
    url: str
    title: str


@dataclass
class ListingDetail:
    token: str
    url: str
    title: str
    price_toman: float | None
    brand_model: str | None = None
    year: str | None = None
    mileage_km: float | None = None
    color: str | None = None
    body_status: str | None = None
    gearbox: str | None = None
    raw_specs: dict[str, str] = field(default_factory=dict)


@dataclass
class DetailFetchResult:
    """Wraps get_listing_detail's outcome so callers (deal_checker) can
    record *why* a listing failed, not just that it did - this is what
    powers the admin panel's error list and success-rate stat.
    """

    detail: ListingDetail | None
    error: str | None = None


def _token_from_url(url: str) -> str:
    # Divar detail URLs look like https://divar.ir/v/<slug>/<token>
    return url.rstrip("/").rsplit("/", 1)[-1]


def _jittered_delay(base_seconds: float) -> float:
    """Randomized delay (±30% of the configured base) so request timing
    doesn't look like a fixed-interval bot.
    """
    return random.uniform(base_seconds * 0.7, base_seconds * 1.3)


def _extract_first_year(value: str | None) -> str | None:
    """Divar's year field sometimes comes as a range like "۱۳۸۲ - ۲۰۰۳"
    (Persian year - Gregorian year, since one Persian year spans two
    Gregorian ones and Divar shows both for reference). Hamrah Mechanic's
    "سال ساخت" tab only lists the bare Persian year as its own option, so
    pull out just the first number and keep its original (Persian) digits,
    since that's what the tab's option labels use.
    """
    if not value:
        return None
    match = re.search(r"[0-9۰-۹]+", value)
    return match.group(0) if match else None


class DivarScraper:
    def __init__(self) -> None:
        self._playwright = None
        self._browser: Browser | None = None

    async def __aenter__(self) -> "DivarScraper":
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

    async def _new_page(self) -> Page:
        assert self._browser is not None
        context = await self._browser.new_context(
            locale="fa-IR",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
        )
        return await context.new_page()

    async def _goto_with_retry(self, page: Page, url: str) -> None:
        """Navigates to `url`, retrying with a growing timeout/backoff on
        failure. Raises the last error if every attempt fails - callers
        already wrap this in try/except.
        """
        attempts = settings.page_goto_retries + 1
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                timeout = settings.page_timeout_ms * attempt  # grow timeout each retry
                await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
                return
            except Exception as exc:  # noqa: BLE001 - retry on any navigation failure
                last_error = exc
                if attempt < attempts:
                    backoff = 2 * attempt
                    logger.warning(
                        "Navigation attempt %d/%d failed for %s (%s) - retrying in %ds",
                        attempt,
                        attempts,
                        url,
                        exc.__class__.__name__,
                        backoff,
                    )
                    await asyncio.sleep(backoff)
        assert last_error is not None
        raise last_error

    async def list_new_listings(
        self, city_slug: str, category_slug: str, limit: int | None = None
    ) -> list[ListingSummary]:
        limit = limit or settings.max_listings_per_scan
        url = f"{BASE_URL}/s/{city_slug}/{category_slug}"
        page = await self._new_page()
        summaries: list[ListingSummary] = []
        try:
            await self._goto_with_retry(page, url)
            await page.wait_for_timeout(1500)  # let client-side rendering settle
            links = await page.locator(SELECTORS["listing_card_link"]).all()
            seen_urls: set[str] = set()
            for link in links:
                href = await link.get_attribute("href")
                if not href:
                    continue
                full_url = href if href.startswith("http") else f"{BASE_URL}{href}"
                if full_url in seen_urls:
                    continue
                seen_urls.add(full_url)

                title_text = clean_whitespace(await link.inner_text())
                summaries.append(
                    ListingSummary(
                        token=_token_from_url(full_url),
                        url=full_url,
                        title=title_text,
                    )
                )
                if len(summaries) >= limit:
                    break
        except Exception:
            logger.exception("Failed to list Divar listings for %s/%s", city_slug, category_slug)
        finally:
            await page.close()

        await asyncio.sleep(_jittered_delay(settings.request_delay_seconds))
        return summaries

    async def get_listing_detail(self, url: str) -> DetailFetchResult:
        page = await self._new_page()
        try:
            await self._goto_with_retry(page, url)
            await page.wait_for_timeout(1200)

            title = clean_whitespace(
                await page.locator(SELECTORS["detail_title"]).first.inner_text()
            ) if await page.locator(SELECTORS["detail_title"]).count() else ""

            price_text = ""
            if await page.locator(SELECTORS["detail_price"]).count():
                price_text = await page.locator(SELECTORS["detail_price"]).first.inner_text()
            price_toman = extract_number(price_text)

            raw_specs: dict[str, str] = {}

            # Shape 1: simple label:value rows (برند و مدل, گیربکس, نوع سوخت, ...)
            rows = await page.locator(SELECTORS["detail_unexpandable_row"]).all()
            for row in rows:
                try:
                    label = clean_whitespace(
                        await row.locator(
                            SELECTORS["detail_unexpandable_row_title"]
                        ).first.inner_text(timeout=2000)
                    )
                    value = clean_whitespace(
                        await row.locator(
                            SELECTORS["detail_unexpandable_row_value"]
                        ).first.inner_text(timeout=2000)
                    )
                except Exception:
                    # This row doesn't match the label/value shape - skip it fast
                    # instead of waiting out Playwright's default 30s timeout.
                    continue
                if label and value and label != value:
                    raw_specs[label] = value

            # Shape 2: grouped column table (کارکرد / مدل (سال تولید) / رنگ, ...)
            # Headers and values are separate flat lists that line up by position.
            groups = await page.locator(SELECTORS["detail_group_row"]).all()
            for group in groups:
                try:
                    headers = await group.locator(
                        SELECTORS["detail_group_row_header"]
                    ).all_inner_texts()
                    values = await group.locator(
                        SELECTORS["detail_group_row_value"]
                    ).all_inner_texts()
                except Exception:
                    continue
                for label, value in zip(headers, values):
                    label = clean_whitespace(label)
                    value = clean_whitespace(value)
                    if label and value and label != value:
                        raw_specs[label] = value

            detail = ListingDetail(
                token=_token_from_url(url),
                url=url,
                title=title,
                price_toman=price_toman,
                raw_specs=raw_specs,
            )
            _fill_known_specs(detail, raw_specs)
            return DetailFetchResult(detail=detail)
        except Exception as exc:  # noqa: BLE001 - report the failure upstream, don't crash the scan
            logger.exception("Failed to read Divar listing detail: %s", url)
            return DetailFetchResult(detail=None, error=f"{exc.__class__.__name__}: {exc}")
        finally:
            await page.close()
            await asyncio.sleep(_jittered_delay(settings.request_delay_seconds))


def _fill_known_specs(detail: ListingDetail, raw_specs: dict[str, str]) -> None:
    def find(*labels: str) -> str | None:
        for label, value in raw_specs.items():
            if any(target in label for target in labels):
                return value
        return None

    detail.brand_model = find(*SPEC_LABELS["brand_model"])
    detail.year = _extract_first_year(find(*SPEC_LABELS["year"]))
    detail.color = find(*SPEC_LABELS["color"])
    detail.body_status = find(*SPEC_LABELS["body_status"])
    detail.gearbox = find(*SPEC_LABELS["gearbox"])
    mileage_text = find(*SPEC_LABELS["mileage"])
    detail.mileage_km = extract_number(mileage_text)
