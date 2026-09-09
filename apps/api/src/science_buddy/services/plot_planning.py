import json
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from science_buddy.domain.providers import ModelProvider, StructuredGenerationRequest
from science_buddy.services.models import ModelResponseError
from science_buddy.services.plot_agent import ParsedTable, PlotRequest


class PlotPlan(BaseModel):
    chart_type: Literal["bar", "line", "scatter", "distribution", "heatmap"]
    title: str = Field(max_length=240)
    caption: str = Field(default="", max_length=1000)
    x_column: str
    y_columns: list[str] = Field(min_length=1, max_length=8)
    group_column: str | None = None
    error_column: str | None = None
    palette: Literal["journal", "npg", "nejm", "lancet", "jama", "colorblind"] = (
        "journal"
    )
    font_size: int | None = Field(default=None, ge=7, le=28)
    line_width: float | None = Field(default=None, ge=0.5, le=6)
    point_size: float | None = Field(default=None, ge=8, le=160)
    figure_width: float | None = Field(default=None, ge=4, le=16)
    figure_height: float | None = Field(default=None, ge=3, le=12)
    show_grid: bool | None = None
    legend_position: Literal["best", "top", "bottom", "left", "right", "none"] | None = None
    rationale: str = Field(max_length=1200)


class PlotPlanningService:
    """LLM intent planner with no code execution and a strict plotting allowlist."""

    def __init__(self, model: ModelProvider) -> None:
        self._model = model

    async def plan(
        self,
        *,
        table: ParsedTable,
        intent: str,
        base_request: PlotRequest,
        model_depth: Literal["quick", "balanced", "deep", "max"] = "balanced",
        max_context_tokens: int = 32768,
    ) -> PlotPlan:
        preview = [dict(zip(table.headers, row, strict=True)) for row in table.rows[:30]]
        request = StructuredGenerationRequest(
                system_instruction=(
                    "You are a scientific plotting planner. Interpret the user's visualization "
                    "intent and return a declarative plot plan only. Never emit Python, R, shell, "
                    "HTML, or executable code. Use only supplied columns and one allowed chart "
                    "type/palette. Prefer publication-ready grammar-of-graphics conventions: "
                    "show raw observations for distributions, use uncertainty columns only when "
                    "present, avoid 3D and misleading axes, and keep labels concise."
                    " Treat biological and technical replicates differently: biological replicates "
                    "are independent experimental units, while technical replicates only estimate "
                    "measurement precision. Never invent replicate identities, sample sizes, error "
                    "values, pairing, or significance tests. Preserve explicit replicate settings "
                    "from the current request."
                ),
                user_content=json.dumps(
                    {
                        "intent": intent,
                        "headers": list(table.headers),
                        "column_types": table.column_types,
                        "rows": len(table.rows),
                        "preview": preview,
                        "current_request": {
                            "chart_type": base_request.chart_type,
                            "title": base_request.title,
                            "caption": base_request.caption,
                            "x_column": base_request.x_column,
                            "y_columns": list(base_request.y_columns),
                            "group_column": base_request.group_column,
                            "error_column": base_request.error_column,
                            "palette": base_request.palette,
                            "font_size": base_request.font_size,
                            "line_width": base_request.line_width,
                            "point_size": base_request.point_size,
                            "figure_width": base_request.figure_width,
                            "figure_height": base_request.figure_height,
                            "show_grid": base_request.show_grid,
                            "legend_position": base_request.legend_position,
                            "data_layout": base_request.data_layout,
                            "condition_column": base_request.condition_column,
                            "value_column": base_request.value_column,
                            "replicate_column": base_request.replicate_column,
                            "replicate_columns": list(base_request.replicate_columns),
                            "summary_stat": base_request.summary_stat,
                            "show_all_points": base_request.show_all_points,
                            "show_sample_size": base_request.show_sample_size,
                            "replicate_unit": base_request.replicate_unit,
                            "pairing_mode": base_request.pairing_mode,
                        },
                    },
                    ensure_ascii=False,
                ),
                response_schema=PlotPlan.model_json_schema(),
                operation="workbench.plot_agent.intent_planning",
                # Plot planning is a bounded column/style mapping task. For reasoning
                # providers, quick/low leaves enough completion budget for the JSON plan.
                depth="quick" if model_depth == "balanced" else model_depth,
                max_context_tokens=max_context_tokens,
                max_output_tokens=8192,
        )
        try:
            raw = await self._model.generate_structured(request)
            plan = PlotPlan.model_validate(raw)
        except (ModelResponseError, ValidationError) as exc:
            retry = request.model_copy(
                update={
                    "system_instruction": (
                        request.system_instruction
                        + "\n\nRETRY: Preserve the requested plot semantics and return one "
                        "complete concise JSON plan. Do not spend output on explanation "
                        "outside the rationale field."
                    ),
                    "operation": "workbench.plot_agent.intent_planning.retry",
                    "max_output_tokens": 16384,
                }
            )
            try:
                raw = await self._model.generate_structured(retry)
                plan = PlotPlan.model_validate(raw)
            except (ModelResponseError, ValidationError) as retry_exc:
                raise ModelResponseError(
                    f"绘图规划连续两次未返回完整结构：{str(retry_exc)[:500]}"
                ) from exc
        allowed = set(table.headers)
        selected = {
            plan.x_column,
            *plan.y_columns,
            *([plan.group_column] if plan.group_column else []),
            *([plan.error_column] if plan.error_column else []),
        }
        if selected - allowed:
            raise ValueError("Plot planner selected a column outside the supplied table")
        if any(table.column_types[value] != "numeric" for value in plan.y_columns):
            raise ValueError("Plot planner selected a non-numeric response column")
        return plan
