"""Run locally to WATCH the scrape -> estimate pipeline in a visible browser.

Usage (from the project root, with your venv active):
    python local_test_watch.py [city_slug] [category_slug]

Example:
    python local_test_watch.py tehran car

This forces HEADLESS_BROWSER=false (regardless of your .env), opens Chromium
visibly, grabs the first not-yet-seen listing from Divar for the given
city/category, prints its parsed details, then drives Hamrah Mechanic's form
with those specs and prints the estimated price. It does NOT touch the
database or Telegram - just the two Playwright-driven services.
"""

import asyncio
import os
import sys

# Must happen before `app.config` is imported anywhere, since settings are
# read from the environment once at import time.
os.environ["HEADLESS_BROWSER"] = "false"

from app.services.deal_checker import _split_brand_model  # noqa: E402
from app.services.divar_client import DivarScraper  # noqa: E402
from app.services.price_estimator import CarSpec, HamrahMechanicEstimator  # noqa: E402


async def main() -> None:
    city_slug = sys.argv[1] if len(sys.argv) > 1 else "tehran"
    category_slug = sys.argv[2] if len(sys.argv) > 2 else "car"

    print(f"== Step 1: listing search results for {city_slug}/{category_slug} ==")
    async with DivarScraper() as scraper:
        summaries = await scraper.list_new_listings(city_slug, category_slug, limit=5)

        if not summaries:
            print("No listings found. Check that the city_slug/category_slug are valid "
                  "Divar slugs, or that the page actually loaded (watch the browser window).")
            return

        for i, s in enumerate(summaries):
            print(f"  [{i}] {s.title}  ->  {s.url}")

        first = summaries[0]
        print(f"\n== Step 2: opening detail page for listing [0]: {first.url} ==")
        fetch_result = await scraper.get_listing_detail(first.url)

        if fetch_result.detail is None:
            print(f"Failed to read the detail page: {fetch_result.error}")
            return
        detail = fetch_result.detail

        print("  title:", detail.title)
        print("  price_toman:", detail.price_toman)
        print("  brand_model:", detail.brand_model)
        print("  year:", detail.year)
        print("  mileage_km:", detail.mileage_km)
        print("  color:", detail.color)
        print("  body_status:", detail.body_status)
        print("  raw_specs (debug):", detail.raw_specs)

    brand, model = _split_brand_model(detail.brand_model)
    if not brand or not model:
        print("\nCouldn't split brand/model from the listing - can't run the estimate step.")
        return

    spec = CarSpec(
        brand=brand,
        model=model,
        year=detail.year,
        trim=None,
        mileage_km=detail.mileage_km,
        color=detail.color,
        body_status=detail.body_status,
    )

    print(f"\n== Step 3: driving Hamrah Mechanic's form with spec: {spec} ==")
    async with HamrahMechanicEstimator() as estimator:
        result = await estimator.estimate(spec)

    print("\n== Result ==")
    print("  success:", result.success)
    print("  estimated_price_toman:", result.estimated_price_toman)
    print("  min_price_toman:", result.min_price_toman)
    print("  max_price_toman:", result.max_price_toman)
    print("  raw_text:", result.raw_text)
    print("  error:", result.error)


if __name__ == "__main__":
    asyncio.run(main())
