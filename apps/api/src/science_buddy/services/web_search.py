import re
from dataclasses import dataclass
from ipaddress import ip_address
from urllib.parse import urlparse

import httpx
from cryptography.fernet import InvalidToken
from sqlalchemy.ext.asyncio import AsyncSession

from science_buddy.config import Settings
from science_buddy.infrastructure.models import WebSearchConfiguration
from science_buddy.services.models import _model_key_cipher


class WebSearchConfigurationError(RuntimeError):
    pass


class WebSearchError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class WebSearchSettings:
    provider: str
    base_url: str
    api_key: str
    max_results: int
    search_depth: str
    source: str


@dataclass(frozen=True, slots=True)
class WebSearchResult:
    title: str
    url: str
    snippet: str
    score: float | None
    published_date: str | None


def encrypt_web_search_key(value: str, settings: Settings) -> str:
    key = value.strip()
    if not key:
        raise WebSearchConfigurationError("Tavily API Key is required")
    return _model_key_cipher(settings).encrypt(key.encode("utf-8")).decode("ascii")


def decrypt_web_search_key(value: str, settings: Settings) -> str:
    try:
        return _model_key_cipher(settings).decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeDecodeError) as exc:
        raise WebSearchConfigurationError(
            "Stored Tavily API Key cannot be decrypted; save the configuration again"
        ) from exc


def validate_web_search_base_url(value: str | None) -> str:
    base_url = (value or "https://api.tavily.com").strip().rstrip("/")
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise WebSearchConfigurationError("Search Base URL must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise WebSearchConfigurationError(
            "Search Base URL must not contain credentials, query parameters, or fragments"
        )
    if parsed.scheme == "http":
        hostname = parsed.hostname or ""
        try:
            loopback = ip_address(hostname).is_loopback
        except ValueError:
            loopback = hostname.casefold() == "localhost"
        if not loopback:
            raise WebSearchConfigurationError(
                "Unencrypted HTTP search endpoints are only allowed on localhost"
            )
    return base_url


def web_search_environment_configured(settings: Settings) -> bool:
    return settings.web_search_provider == "tavily" and bool(settings.web_search_api_key)


async def resolve_web_search_settings(
    session: AsyncSession,
    settings: Settings,
) -> WebSearchSettings | None:
    if web_search_environment_configured(settings):
        if settings.web_search_api_key is None:
            raise WebSearchConfigurationError("Tavily API Key is required")
        return WebSearchSettings(
            provider="tavily",
            base_url=validate_web_search_base_url(settings.web_search_base_url),
            api_key=settings.web_search_api_key.get_secret_value(),
            max_results=settings.web_search_max_results,
            search_depth=settings.web_search_depth,
            source="environment",
        )
    stored = await session.get(WebSearchConfiguration, 1)
    if stored is None:
        return None
    return WebSearchSettings(
        provider=stored.provider,
        base_url=validate_web_search_base_url(stored.base_url),
        api_key=decrypt_web_search_key(stored.api_key_encrypted, settings),
        max_results=stored.max_results,
        search_depth=stored.search_depth,
        source="local",
    )


def _safe_detail(response: httpx.Response, api_key: str) -> str | None:
    detail: str | None = None
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        for key in ("detail", "message", "error"):
            value = payload.get(key)
            if isinstance(value, str):
                detail = value
                break
    if not detail:
        return None
    detail = detail.replace(api_key, "[REDACTED]")
    detail = re.sub(
        r"(?i)(?:bearer\s+|api[_ -]?key[=: ]+)[A-Za-z0-9._-]{8,}",
        "[REDACTED]",
        detail,
    )
    return " ".join(detail.split())[:500]


class TavilySearchService:
    def __init__(self, client: httpx.AsyncClient, settings: WebSearchSettings) -> None:
        self._client = client
        self._settings = settings

    async def search(
        self,
        query: str,
        *,
        max_results: int | None = None,
    ) -> list[WebSearchResult]:
        cleaned_query = " ".join(query.split())[:500]
        if len(cleaned_query) < 2:
            raise WebSearchConfigurationError(
                "Search query must contain at least 2 characters"
            )
        limit = min(max_results or self._settings.max_results, 10)
        try:
            response = await self._client.post(
                f"{self._settings.base_url}/search",
                json={
                    "api_key": self._settings.api_key,
                    "query": cleaned_query,
                    "search_depth": self._settings.search_depth,
                    "max_results": limit,
                    "include_answer": False,
                    "include_raw_content": False,
                },
            )
        except httpx.TimeoutException as exc:
            raise WebSearchError("Tavily search timed out") from exc
        except httpx.HTTPError as exc:
            raise WebSearchError("Tavily search connection failed") from exc
        if response.is_error:
            detail = _safe_detail(response, self._settings.api_key)
            suffix = f": {detail}" if detail else ""
            raise WebSearchError(
                f"Tavily search failed (HTTP {response.status_code}){suffix}"
            )
        try:
            values = response.json().get("results", [])
        except (AttributeError, ValueError) as exc:
            raise WebSearchError("Tavily returned an invalid response") from exc
        if not isinstance(values, list):
            raise WebSearchError("Tavily returned an invalid results list")
        results: list[WebSearchResult] = []
        for value in values[:limit]:
            if not isinstance(value, dict):
                continue
            url = str(value.get("url") or "").strip()
            title = " ".join(str(value.get("title") or "Untitled result").split())[:300]
            snippet = " ".join(str(value.get("content") or "").split())[:1500]
            parsed = urlparse(url)
            if parsed.scheme != "https" or not parsed.netloc or not snippet:
                continue
            score = value.get("score")
            results.append(
                WebSearchResult(
                    title=title,
                    url=url,
                    snippet=snippet,
                    score=float(score) if isinstance(score, int | float) else None,
                    published_date=(
                        str(value["published_date"])[:32]
                        if value.get("published_date")
                        else None
                    ),
                )
            )
        return results


async def probe_web_search(settings: WebSearchSettings) -> None:
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        await TavilySearchService(client, settings).search(
            "biomedical research methods",
            max_results=1,
        )
