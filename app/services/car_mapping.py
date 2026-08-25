"""Divar-to-Hamrah-Mechanic car brand/model name mapping.

Divar and Hamrah Mechanic name the same car differently often enough that
free-text fuzzy matching against Hamrah Mechanic's search box is
unreliable (see the fallback in price_estimator.py, which is still used
for anything not covered here). This module wraps a verified mapping table
(app/data/car_brand_mapping.json, ~933 entries covering common brands/
models) exported from a manually-reviewed spreadsheet, so brand/model
selection can use Hamrah Mechanic's own exact naming directly instead of
guessing.

Only rows with status "تطبیق یافت شد" (confidently matched) are used for
lookups - the other ~500 rows in the source spreadsheet are marked "نیاز به
بررسی" (needs review) or "یافت نشد" (not found) and are intentionally
excluded to avoid feeding a wrong mapping into Hamrah Mechanic's form.

To extend coverage: edit the spreadsheet, re-export to
app/data/car_brand_mapping.json (same shape as below), and the "needs
review" rows can be promoted once verified.
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "car_brand_mapping.json"
CONFIDENT_STATUS = "تطبیق یافت شد"
NEEDS_REVIEW_STATUS = "نیاز به بررسی"


@dataclass
class MappingEntry:
    divar_brand: str
    divar_model: str
    divar_english_name: str | None
    divar_trims: list[str]
    hamrah_brand: str
    hamrah_model_fa: str
    hamrah_model_en: str | None
    car_model_id: int | None
    hamrah_trims: list[str]
    hamrah_year_range: str | None
    match_score: float | None
    status: str


def _normalize(text: str) -> str:
    """Collapse half-space (ZWNJ) and whitespace variations so matching
    isn't thrown off by "کوییک دنده‌ای" vs "کوییک دنده ای" vs extra spaces.
    """
    return " ".join(text.replace("\u200c", " ").split())


def _load_entries() -> list[MappingEntry]:
    if not DATA_PATH.exists():
        logger.warning("Car brand mapping file not found at %s - lookups will always miss", DATA_PATH)
        return []
    with open(DATA_PATH, encoding="utf-8") as f:
        raw = json.load(f)
    return [
        MappingEntry(
            divar_brand=row["divar_brand"],
            divar_model=row["divar_model"],
            divar_english_name=row.get("divar_english_name"),
            divar_trims=row.get("divar_trims") or [],
            hamrah_brand=row["hamrah_brand"],
            hamrah_model_fa=row["hamrah_model_fa"],
            hamrah_model_en=row.get("hamrah_model_en"),
            car_model_id=row.get("car_model_id"),
            hamrah_trims=row.get("hamrah_trims") or [],
            hamrah_year_range=row.get("hamrah_year_range"),
            match_score=row.get("match_score"),
            status=row["status"],
        )
        for row in raw
        if row.get("hamrah_brand") and row.get("hamrah_model_fa")
    ]


_ENTRIES: list[MappingEntry] = _load_entries()
_CONFIDENT_ENTRIES: list[MappingEntry] = [e for e in _ENTRIES if e.status == CONFIDENT_STATUS]
_NEEDS_REVIEW_ENTRIES: list[MappingEntry] = [e for e in _ENTRIES if e.status == NEEDS_REVIEW_STATUS]


def _build_lookup_pool(entries: list[MappingEntry]) -> list[tuple[str, MappingEntry]]:
    # Longest model text first so a more specific match (e.g. "کوییک
    # دنده‌ای") is tried before a shorter, looser one (e.g. "کوییک") that
    # would also technically match.
    return sorted(
        ((_normalize(e.divar_model), e) for e in entries),
        key=lambda pair: -len(pair[0]),
    )


_CONFIDENT_POOL = _build_lookup_pool(_CONFIDENT_ENTRIES)
_NEEDS_REVIEW_POOL = _build_lookup_pool(_NEEDS_REVIEW_ENTRIES)


def _search_pool(text: str, pool: list[tuple[str, MappingEntry]]) -> MappingEntry | None:
    for model_text, entry in pool:
        if model_text and model_text in text:
            return entry
    return None


def find_hamrah_names(
    divar_brand_model_text: str | None, allow_needs_review: bool = True
) -> MappingEntry | None:
    """Given Divar's raw (often messy) "برند و مدل" field text - e.g.
    "کوییک دنده‌ای R" or "پژو 206 تیپ 2" - returns the best-matching
    MappingEntry whose Divar model name is the longest match contained in
    that text, or None if nothing in the table matches.

    Tries confidently-matched rows (status "تطبیق یافت شد") first. If
    `allow_needs_review` is True (the default) and no confident match was
    found, falls back to "نیاز به بررسی" (needs-review) rows - these come
    from automated similarity scoring rather than a full manual check, but
    are still far more informed than free-text guessing. The returned
    entry's `.status` tells you which tier it came from, so callers that
    want to log/flag lower-confidence matches can check it.
    """
    if not divar_brand_model_text:
        return None
    text = _normalize(divar_brand_model_text)

    match = _search_pool(text, _CONFIDENT_POOL)
    if match or not allow_needs_review:
        return match
    return _search_pool(text, _NEEDS_REVIEW_POOL)


def entry_count() -> dict[str, int]:
    """Small introspection helper, mostly for logging/debugging at startup."""
    counts: dict[str, int] = {}
    for e in _ENTRIES:
        counts[e.status] = counts.get(e.status, 0) + 1
    return counts
