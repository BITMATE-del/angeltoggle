import re


def normalize_korean_phone(value):
    """
    Duplicate/storage canonical form for Korean mobile/phone numbers.
    Examples:
      010-1234-5678      -> 01012345678
      +82 10 1234 5678   -> 01012345678
      821012345678        -> 01012345678
    """
    if value is None:
        return None

    raw = str(value).strip()

    # Excel sometimes gives numeric-looking values as 1012345678.0
    if raw.endswith(".0") and raw[:-2].isdigit():
        raw = raw[:-2]

    digits = re.sub(r"\D", "", raw)
    if not digits:
        return None

    if digits.startswith("0082"):
        digits = digits[4:]
        if not digits.startswith("0"):
            digits = "0" + digits
    elif digits.startswith("82"):
        digits = digits[2:]
        if not digits.startswith("0"):
            digits = "0" + digits

    # Korean mobile numbers should normally become 010xxxxxxxx.
    # Keep landline/other Korean numbers valid as long as they are plausible.
    if not digits.startswith("0"):
        return None

    if len(digits) < 9 or len(digits) > 11:
        return None

    return digits


def to_telegram_e164(value):
    """
    Telegram API canonical phone form.
    Examples:
      01012345678         -> +821012345678
      +82 10 1234 5678   -> +821012345678
    """
    domestic = normalize_korean_phone(value)
    if not domestic:
        return None
    return "+82" + domestic[1:]


def format_korean_international(value):
    """
    Human-readable form requested for contact display.
    01012345678 -> +82 10 1234 5678
    """
    domestic = normalize_korean_phone(value)
    if not domestic:
        return ""

    local = domestic[1:]
    if domestic.startswith("010") and len(domestic) == 11:
        return f"+82 {local[:2]} {local[2:6]} {local[6:]}"
    return "+82 " + local
