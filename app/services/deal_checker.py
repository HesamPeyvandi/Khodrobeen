import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import SeenListing
from app.services.divar_client import DivarScraper, ListingDetail
from app.services.price_estimator import CarSpec, HamrahMechanicEstimator

logger = logging.getLogger(__name__)


def _split_brand_model(brand_model_text: str | None) -> tuple[str | None, str | None]:
    """Best-effort split of Divar's combined "brand model" spec field.
    Divar usually shows something like "پژو 206" or "پراید 131" as one
    string; the first token is treated as brand and the remainder as model.
    Adjust this if you find it mis-splitting common cases.
    """
    if not brand_model_text:
        return None, None
    parts = brand_model_text.strip().split(maxsplit=1)
    if len(parts) == 1:
        return parts[0], None
    return parts[0], parts[1]


def _already_seen(session: Session, token: str) -> bool:
    return (
        session.scalar(select(SeenListing).where(SeenListing.token == token))
        is not None
    )


async def process_city_category(
    session: Session,
    scraper: DivarScraper,
    estimator: HamrahMechanicEstimator,
    city_slug: str,
    category_slug: str,
) -> list[SeenListing]:
    """Scans one city/category pair, estimates prices for any listing not
    seen before, and returns the newly-recorded SeenListing rows (both deals
    and non-deals, so the caller can decide what to do with each).
    """
    new_records: list[SeenListing] = []
    summaries = await scraper.list_new_listings(city_slug, category_slug)

    for summary in summaries:
        if _already_seen(session, summary.token):
            continue

        detail = await scraper.get_listing_detail(summary.url)
        if detail is None:
            continue

        record = await _evaluate_listing(estimator, city_slug, category_slug, detail)
        session.add(record)
        session.commit()
        new_records.append(record)

    return new_records


async def _evaluate_listing(
    estimator: HamrahMechanicEstimator,
    city_slug: str,
    category_slug: str,
    detail: ListingDetail,
) -> SeenListing:
    brand, model = _split_brand_model(detail.brand_model)
    estimated_price: float | None = None

    if brand and model:
        spec = CarSpec(
            brand=brand,
            model=model,
            year=detail.year,
            trim=None,
            mileage_km=detail.mileage_km,
            color=detail.color,
            body_status=detail.body_status,
        )
        try:
            result = await estimator.estimate(spec)
            if result.success:
                estimated_price = result.estimated_price_toman
        except Exception:
            logger.exception("Price estimation failed for listing %s", detail.url)

    is_deal = (
        detail.price_toman is not None
        and estimated_price is not None
        and detail.price_toman < estimated_price
    )

    return SeenListing(
        token=detail.token,
        city_slug=city_slug,
        category_slug=category_slug,
        title=detail.title,
        url=detail.url,
        divar_price_toman=detail.price_toman,
        estimated_price_toman=estimated_price,
        is_deal=is_deal,
    )
