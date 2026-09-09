import asyncio
import math
from typing import Any

import httpx

from science_buddy.config import Settings


class OpenAlexJournalMetricsService:
    """Retrieve open journal metrics without presenting them as proprietary JIF/JCR data."""

    def __init__(self, client: httpx.AsyncClient, settings: Settings) -> None:
        self._client = client
        self._settings = settings
        self._cache: dict[str, dict[str, Any]] = {}

    def _auth_params(self) -> dict[str, str]:
        if not self._settings.openalex_api_key:
            return {}
        return {"api_key": self._settings.openalex_api_key.get_secret_value()}

    async def fetch(self, source_id: str) -> dict[str, Any]:
        normalized_id = source_id.rsplit("/", 1)[-1]
        if normalized_id in self._cache:
            return self._cache[normalized_id]
        response = await self._client.get(
            f"{self._settings.openalex_base_url.rstrip('/')}/sources/{normalized_id}",
            params=self._auth_params(),
        )
        response.raise_for_status()
        source = response.json()
        summary = source.get("summary_stats") or {}
        metric = summary.get("2yr_mean_citedness")
        impact = float(metric) if isinstance(metric, int | float) else None
        basis = self._dominant_topic(source)
        percentile = None
        quartile = None
        comparison_count = None
        if impact is not None and impact > 0:
            base_filters = ["type:journal"]
            if basis["topic_id"]:
                base_filters.append(f"topics.id:{basis['topic_id']}")
            positive_filter = ",".join(
                [*base_filters, "summary_stats.2yr_mean_citedness:>0"]
            )
            higher_filter = ",".join(
                [*base_filters, f"summary_stats.2yr_mean_citedness:>{impact}"]
            )
            comparison_count, higher_count = await asyncio.gather(
                self._count(positive_filter),
                self._count(higher_filter),
            )
            if comparison_count > 0:
                percentile = max(0.0, min(1.0, 1.0 - higher_count / comparison_count))
                quartile_number = min(4, max(1, int((1.0 - percentile) * 4) + 1))
                quartile = f"OA-Q{quartile_number}"
        impact_component = (
            min(1.0, math.log1p(max(impact, 0.0)) / math.log1p(20.0))
            if impact is not None
            else 0.0
        )
        importance_score = (
            round(0.6 * percentile + 0.4 * impact_component, 6)
            if percentile is not None
            else round(0.4 * impact_component, 6)
        )
        result = {
            "source": "openalex",
            "source_id": source.get("id"),
            "display_name": source.get("display_name"),
            "issn_l": source.get("issn_l"),
            "issn": source.get("issn") or [],
            "two_year_mean_citedness": impact,
            "h_index": summary.get("h_index"),
            "i10_index": summary.get("i10_index"),
            "quartile": quartile,
            "percentile": round(percentile, 6) if percentile is not None else None,
            "comparison_count": comparison_count,
            "quartile_basis": basis,
            "importance_score": importance_score,
            "updated_date": source.get("updated_date"),
            "metric_note": (
                "OpenAlex 2yr_mean_citedness and a same-topic percentile quartile; "
                "not Clarivate JIF, JCR, CAS, or SCImago SJR quartile."
            ),
        }
        self._cache[normalized_id] = result
        return result

    @staticmethod
    def _dominant_topic(source: dict[str, Any]) -> dict[str, str | None]:
        values = source.get("topics")
        if not isinstance(values, list) or not values or not isinstance(values[0], dict):
            return {"scope": "all_openalex_journals", "topic_id": None, "topic": None}
        topic = values[0]
        topic_id = str(topic.get("id") or "").rsplit("/", 1)[-1] or None
        return {
            "scope": "openalex_topic" if topic_id else "all_openalex_journals",
            "topic_id": topic_id,
            "topic": str(topic.get("display_name") or "") or None,
        }

    async def _count(self, filter_value: str) -> int:
        response = await self._client.get(
            f"{self._settings.openalex_base_url.rstrip('/')}/sources",
            params={
                "filter": filter_value,
                "per-page": "1",
                "select": "id",
                **self._auth_params(),
            },
        )
        response.raise_for_status()
        count = (response.json().get("meta") or {}).get("count")
        return int(count) if isinstance(count, int) else 0
