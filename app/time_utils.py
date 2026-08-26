from datetime import datetime, timedelta, timezone

TEHRAN_OFFSET = timedelta(hours=3, minutes=30)


def utcnow() -> datetime:
    """Naive UTC datetime - kept naive on purpose so it compares cleanly
    with the naive DateTime columns used throughout app/db/models.py.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_tehran(dt: datetime | None) -> datetime | None:
    """Converts a naive UTC datetime (as stored in the DB) to Iran local
    time for display purposes only - storage stays in UTC. Iran has used a
    fixed UTC+3:30 offset with no daylight-saving changes since 2022, so a
    plain fixed offset is accurate without needing a timezone database
    (zoneinfo's "Asia/Tehran" would need the `tzdata` package on Windows,
    which isn't worth the dependency for a single fixed offset).
    """
    if dt is None:
        return None
    return dt + TEHRAN_OFFSET
