import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ListingStatus, SeenListing
from app.services.divar_client import DivarScraper, ListingDetail
from app.services.price_estimator import CarSpec, HamrahMechanicEstimator

logger = logging.getLogger(__name__)


# Iranian domestic models where Divar's "برند و مدل" field shows only the
# model name with no manufacturer prefix (e.g. "کوییک دنده‌ای R" instead of
# "سایپا کوییک ..."), so a naive first-word split would wrongly treat the
# model name itself as the brand. Keys are matched as a "starts with" check
# against the raw text (longest key first, so "پراید" doesn't shadow a
# longer more specific key if you add one later).
# NOTE: seeded with the most common Saipa/IKCO models - verify against what
# Hamrah Mechanic actually calls "برند" for these, and extend as you spot
# more mismatches in real scans.
DOMESTIC_MODEL_TO_BRAND: dict[str, str] = {
    "کوییک": "سایپا",
    "تیبا": "سایپا",
    "ساینا": "سایپا",
    "شاهین": "سایپا",
    "پراید": "سایپا",
    "دنا": "ایران خودرو",
    "سمند": "ایران خودرو",
    "رانا": "ایران خودرو",
    "تارا": "ایران خودرو",
    "آریسان": "ایران خودرو",
}


def _split_brand_model(brand_model_text: str | None) -> tuple[str | None, str | None]:
    """Best-effort split of Divar's combined "brand model" spec field.
    Divar usually shows something like "پژو 206" or "پراید 131" as one
    string; the first token is treated as brand and the remainder as model.
    For known domestic models Divar omits the manufacturer entirely (see
    DOMESTIC_MODEL_TO_BRAND above) - those are special-cased so the model
    name doesn't get mistaken for the brand.
    Adjust this if you find it mis-splitting common cases.
    """
    if not brand_model_text:
        return None, None
    text = brand_model_text.strip()

    for model_prefix, brand in sorted(
        DOMESTIC_MODEL_TO_BRAND.items(), key=lambda kv: -len(kv[0])
    ):
        if text.startswith(model_prefix):
            return brand, text

    parts = text.split(maxsplit=1)
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
    seen before, and returns the newly-recorded SeenListing rows (deals,
    non-deals, AND failures alike, so the caller can decide what to do with
    each). Every listing we attempt gets a row - including ones that failed
    to scrape or estimate - so a permanently-broken listing is never
    retried forever, and the admin panel can show what went wrong.
    """
    new_records: list[SeenListing] = []
    summaries = await scraper.list_new_listings(city_slug, category_slug)

    for summary in summaries:
        if _already_seen(session, summary.token):
            continue

        fetch_result = await scraper.get_listing_detail(summary.url)
        if fetch_result.detail is None:
            record = SeenListing(
                token=summary.token,
                city_slug=city_slug,
                category_slug=category_slug,
                title=summary.title,
                url=summary.url,
                status=ListingStatus.SCRAPE_FAILED,
                error_message=fetch_result.error or "unknown scrape error",
            )
            session.add(record)
            session.commit()
            new_records.append(record)
            continue

        record = await _evaluate_listing(estimator, city_slug, category_slug, fetch_result.detail)
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
    status = ListingStatus.OK
    error_message: str | None = None

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
            else:
                status = ListingStatus.SKIPPED if "اوراقی" in (result.error or "") else ListingStatus.ESTIMATE_FAILED
                error_message = result.error
        except Exception as exc:  # noqa: BLE001 - record the failure, don't crash the scan
            logger.exception("Price estimation failed for listing %s", detail.url)
            status = ListingStatus.ESTIMATE_FAILED
            error_message = f"{exc.__class__.__name__}: {exc}"
    else:
        status = ListingStatus.ESTIMATE_FAILED
        error_message = f"couldn't split brand/model from '{detail.brand_model}'"

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
        status=status,
        error_message=error_message,
    )
