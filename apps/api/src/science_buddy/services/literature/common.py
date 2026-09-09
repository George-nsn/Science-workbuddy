import re
from datetime import date

_DOI_PREFIX = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", re.IGNORECASE)
_YEAR = re.compile(r"\b(19|20)\d{2}\b")


class LiteratureProviderError(RuntimeError):
    """Raised when an upstream literature service cannot satisfy a request."""


class LiteratureRecordNotFoundError(LiteratureProviderError):
    """Raised when an upstream identifier does not resolve to a record."""


def normalize_doi(value: str | None) -> str | None:
    if not value:
        return None
    normalized = _DOI_PREFIX.sub("", value.strip()).strip().lower()
    return normalized or None


def normalize_identifier(value: str) -> str:
    return value.strip().lower()


def parse_year(value: object) -> int | None:
    if isinstance(value, int) and 1800 <= value <= 2200:
        return value
    if not isinstance(value, str):
        return None
    match = _YEAR.search(value)
    return int(match.group(0)) if match else None


def safe_date(year: int | None, month: int = 1, day: int = 1) -> date | None:
    if year is None:
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return date(year, 1, 1)
