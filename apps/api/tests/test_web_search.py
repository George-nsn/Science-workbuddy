import httpx
import pytest

from science_buddy.services.web_search import (
    TavilySearchService,
    WebSearchError,
    WebSearchSettings,
)


def settings() -> WebSearchSettings:
    return WebSearchSettings(
        provider="tavily",
        base_url="https://api.tavily.com",
        api_key="secret-key-value",
        max_results=5,
        search_depth="advanced",
        source="test",
    )


@pytest.mark.asyncio
async def test_tavily_search_clips_and_filters_results() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = __import__("json").loads(request.content)
        assert payload["include_raw_content"] is False
        assert payload["include_answer"] is False
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "Recent biomedical result",
                        "url": "https://example.org/research",
                        "content": "x" * 2000,
                        "score": 0.9,
                    },
                    {
                        "title": "Insecure",
                        "url": "http://example.org/insecure",
                        "content": "not accepted",
                    },
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await TavilySearchService(client, settings()).search("marker research")

    assert len(results) == 1
    assert len(results[0].snippet) == 1500


@pytest.mark.asyncio
async def test_tavily_error_redacts_api_key() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "bad api key secret-key-value"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(WebSearchError) as caught:
            await TavilySearchService(client, settings()).search("marker research")

    assert "secret-key-value" not in str(caught.value)
    assert "[REDACTED]" in str(caught.value)