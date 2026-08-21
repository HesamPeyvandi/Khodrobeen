import re

_PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
_ARABIC_DIGITS = "٠١٢٣٤٥٦٧٨٩"
_ASCII_DIGITS = "0123456789"

_DIGIT_TRANSLATION = str.maketrans(
    _PERSIAN_DIGITS + _ARABIC_DIGITS,
    _ASCII_DIGITS + _ASCII_DIGITS,
)


def normalize_digits(text: str) -> str:
    return text.translate(_DIGIT_TRANSLATION)


def extract_number(text: str | None) -> float | None:
    """Pulls the first integer/float out of a Persian-formatted string like
    '۱۲۵,۰۰۰,۰۰۰ تومان' or 'کارکرد: ۸۵,۰۰۰ کیلومتر' -> 125000000 / 85000.
    Returns None if no digits are found (covers "توافقی" / "نامشخص" cases).
    """
    if not text:
        return None
    normalized = normalize_digits(text).replace(",", "").replace("،", "")
    match = re.search(r"\d+(\.\d+)?", normalized)
    if not match:
        return None
    return float(match.group())


def clean_whitespace(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()
