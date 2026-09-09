from collections.abc import AsyncIterator, Sequence
from typing import Any

import pytest

from science_buddy.domain.providers import StructuredGenerationRequest, TextGenerationRequest
from science_buddy.services.models import ModelResponseError
from science_buddy.services.plot_agent import PlotRequest, parse_markdown_table
from science_buddy.services.plot_planning import PlotPlanningService


class PlotModel:
    name = "plot-model"

    async def generate_structured(
        self, request: StructuredGenerationRequest
    ) -> dict[str, Any]:
        assert "Never emit Python, R, shell" in request.system_instruction
        assert request.depth == "quick"
        assert request.max_output_tokens == 8192
        return {
            "chart_type": "bar",
            "title": "Treatment response",
            "caption": "Mean with standard deviation",
            "x_column": "Group",
            "y_columns": ["Mean"],
            "error_column": "SD",
            "palette": "npg",
            "font_size": 12,
            "line_width": 2.4,
            "point_size": 40,
            "show_grid": True,
            "legend_position": "top",
            "rationale": "Compare group means with observed uncertainty.",
        }

    async def stream_text(
        self, _system: str, _messages: Sequence[str]
    ) -> AsyncIterator[str]:
        yield ""

    async def generate_text(self, _request: TextGenerationRequest) -> str:
        return ""


class RetryingPlotModel(PlotModel):
    def __init__(self) -> None:
        self.requests: list[StructuredGenerationRequest] = []

    async def generate_structured(
        self,
        request: StructuredGenerationRequest,
    ) -> dict[str, Any]:
        self.requests.append(request)
        if len(self.requests) == 1:
            raise ModelResponseError(
                "The model did not return valid JSON (finish_reason=length)"
            )
        return {
            "chart_type": "bar",
            "title": "Automatic plan",
            "x_column": "Group",
            "y_columns": ["Mean"],
            "error_column": "SD",
            "rationale": "Recovered after a larger output budget.",
        }


@pytest.mark.asyncio
async def test_llm_plot_planner_returns_allowlisted_declarative_plan() -> None:
    table = parse_markdown_table(
        "| Group | Mean | SD |\n"
        "| --- | --- | --- |\n"
        "| Control | 1.0 | 0.1 |\n"
        "| Treated | 2.0 | 0.2 |"
    )
    plan = await PlotPlanningService(PlotModel()).plan(
        table=table,
        intent="Compare treatment means and show uncertainty",
        base_request=PlotRequest(chart_type="auto"),
    )

    assert plan.chart_type == "bar"
    assert plan.y_columns == ["Mean"]
    assert plan.error_column == "SD"
    assert plan.palette == "npg"
    assert plan.font_size == 12
    assert plan.line_width == 2.4
    assert plan.legend_position == "top"


@pytest.mark.asyncio
async def test_llm_plot_planner_retries_truncated_output_with_larger_budget() -> None:
    table = parse_markdown_table(
        "| Group | Mean | SD |\n"
        "| --- | --- | --- |\n"
        "| Control | 1.0 | 0.1 |\n"
        "| Treated | 2.0 | 0.2 |"
    )
    model = RetryingPlotModel()

    plan = await PlotPlanningService(model).plan(
        table=table,
        intent="自动推荐图形",
        base_request=PlotRequest(chart_type="auto"),
    )

    assert plan.title == "Automatic plan"
    assert [request.max_output_tokens for request in model.requests] == [8192, 16384]
    assert model.requests[1].operation.endswith(".retry")
