from datetime import datetime, timezone


def utcnow() -> datetime:
    """Naive UTC datetime - kept naive on purpose so it compares cleanly
    with the naive DateTime columns used throughout app/db/models.py.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)
