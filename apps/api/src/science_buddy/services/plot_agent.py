import csv
import hashlib
import re
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

import matplotlib
import numpy as np

matplotlib.use("Agg")
from matplotlib import font_manager
from matplotlib import pyplot as plt
from matplotlib.axes import Axes
from matplotlib.figure import Figure
from PIL import Image

ChartType = Literal["bar", "line", "scatter", "distribution", "heatmap"]
ColumnType = Literal["numeric", "categorical", "temporal"]
DataLayout = Literal["auto", "long", "wide", "summary"]
SummaryStatistic = Literal["mean_sd", "mean_sem", "mean_ci95"]
ReplicateUnit = Literal["biological", "technical"]
PairingMode = Literal["independent", "paired"]
_NUMERIC_PATTERN = re.compile(r"^[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?%?$")
_SEPARATOR_PATTERN = re.compile(r"^:?-+:?$")
_ERROR_MARKERS = ("sd", "se", "sem", "ci", "error", "误差", "标准差", "标准误")
_REPLICATE_MARKERS = (
    "replicate",
    "repeat",
    "rep",
    "重复",
    "复孔",
    "生物学重复",
    "技术重复",
)
_REPLICATE_HEADER_PATTERN = re.compile(
    r"^(?:rep(?:licate)?|repeat|r|重复|复孔)[\s_.-]*\d+$",
    re.IGNORECASE,
)
_TIME_MARKERS = (
    "date", "time", "day", "week", "month", "year",
    "日期", "时间", "天", "周", "月", "年",
)
_RENDER_LOCK = threading.Lock()


class PlotAgentError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ParsedTable:
    headers: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    column_types: dict[str, ColumnType]
    source_hash: str


@dataclass(frozen=True, slots=True)
class PlotRequest:
    chart_type: Literal["auto", "bar", "line", "scatter", "distribution", "heatmap"]
    title: str | None = None
    caption: str | None = None
    x_column: str | None = None
    y_columns: tuple[str, ...] = ()
    group_column: str | None = None
    error_column: str | None = None
    font_size: int = 10
    palette: str = "journal"
    line_width: float = 1.8
    point_size: float = 32
    figure_width: float = 7.2
    figure_height: float = 4.6
    dpi: int = 300
    show_grid: bool = False
    legend_position: str = "best"
    data_layout: DataLayout = "auto"
    condition_column: str | None = None
    value_column: str | None = None
    replicate_column: str | None = None
    replicate_columns: tuple[str, ...] = ()
    summary_stat: SummaryStatistic = "mean_sd"
    show_all_points: bool = True
    show_sample_size: bool = True
    replicate_unit: ReplicateUnit = "biological"
    pairing_mode: PairingMode = "independent"


@dataclass(frozen=True, slots=True)
class ReplicateData:
    layout: Literal["long", "wide"]
    condition_column: str
    value_label: str
    conditions: tuple[str, ...]
    values: tuple[tuple[float, ...], ...]
    replicate_ids: tuple[tuple[str, ...], ...]
    source_row_indices: tuple[tuple[int, ...], ...]
    replicate_column: str | None
    replicate_columns: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReplicateSummary:
    condition: str
    n: int
    mean: float
    sd: float | None
    sem: float | None
    ci95_low: float | None
    ci95_high: float | None


@dataclass(frozen=True, slots=True)
class PlotArtifact:
    path: Path
    chart_type: ChartType
    rationale: str
    detected_columns: dict[str, ColumnType]
    warnings: tuple[str, ...]
    source_table_hash: str
    render_spec: dict[str, object]
    width: int
    height: int
    byte_size: int


def _split_row(line: str) -> list[str]:
    text = line.strip()
    if not text.startswith("|") or not text.endswith("|"):
        raise PlotAgentError("Markdown table rows must start and end with '|'")
    reader = csv.reader([text[1:-1]], delimiter="|", skipinitialspace=True)
    return [value.strip().replace("\\|", "|") for value in next(reader)]


def _number(value: str) -> float | None:
    text = value.strip().replace(",", "")
    if not text or not _NUMERIC_PATTERN.fullmatch(text):
        return None
    percent = text.endswith("%")
    if percent:
        text = text[:-1]
    try:
        number = float(text)
    except ValueError:
        return None
    return number / 100 if percent else number


def _is_temporal(header: str, values: list[str]) -> bool:
    lowered = header.casefold()
    if any(marker in lowered for marker in _TIME_MARKERS):
        return True
    parsed = 0
    for value in values:
        try:
            datetime.fromisoformat(value)
            parsed += 1
        except ValueError:
            continue
    return bool(values) and parsed / len(values) >= 0.8


def parse_markdown_table(markdown: str) -> ParsedTable:
    lines = [line.strip() for line in markdown.strip().splitlines() if line.strip()]
    if len(lines) < 3:
        raise PlotAgentError("Markdown table needs a header, separator, and data row")
    headers = _split_row(lines[0])
    separator = _split_row(lines[1])
    if len(headers) < 2 or len(headers) > 24:
        raise PlotAgentError("A plot table must contain 2-24 columns")
    if len(separator) != len(headers) or any(
        not _SEPARATOR_PATTERN.fullmatch(value.replace(" ", ""))
        for value in separator
    ):
        raise PlotAgentError("The second Markdown table row must be a separator row")
    if len(set(headers)) != len(headers) or any(not value for value in headers):
        raise PlotAgentError("Table headers must be non-empty and unique")
    raw_rows = [_split_row(line) for line in lines[2:]]
    if not raw_rows or len(raw_rows) > 5000:
        raise PlotAgentError("A plot table must contain 1-5000 data rows")
    if any(len(row) != len(headers) for row in raw_rows):
        raise PlotAgentError("All Markdown table rows must have the same column count")
    columns = {header: [row[index] for row in raw_rows] for index, header in enumerate(headers)}
    column_types: dict[str, ColumnType] = {}
    for header, values in columns.items():
        non_empty = [value for value in values if value.strip()]
        numeric_count = sum(_number(value) is not None for value in non_empty)
        if _is_temporal(header, non_empty):
            column_types[header] = "temporal"
        elif non_empty and numeric_count / len(non_empty) >= 0.8:
            column_types[header] = "numeric"
        else:
            column_types[header] = "categorical"
    normalized = "\n".join("|".join(row) for row in (headers, *raw_rows))
    return ParsedTable(
        headers=tuple(headers),
        rows=tuple(tuple(value for value in row) for row in raw_rows),
        column_types=column_types,
        source_hash=hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
    )


def _select_chart(
    table: ParsedTable,
    request: PlotRequest,
    replicate_data: ReplicateData | None = None,
) -> tuple[ChartType, str]:
    if request.chart_type != "auto":
        return request.chart_type, "用户指定图形类型。"
    if replicate_data is not None:
        if table.column_types[replicate_data.condition_column] == "temporal":
            return "line", "检测到按时间组织的重复实验原始值，使用均值趋势、误差和原始点。"
        return "bar", "检测到重复实验原始值，使用汇总柱、误差和全部重复点。"
    numeric = [name for name in table.headers if table.column_types[name] == "numeric"]
    temporal = [name for name in table.headers if table.column_types[name] == "temporal"]
    categorical = [name for name in table.headers if table.column_types[name] == "categorical"]
    if temporal and numeric:
        return "line", "检测到时间范围列和定量列，使用时间序列折线图。"
    if len(numeric) >= 3 and len(categorical) <= 1:
        return "heatmap", "检测到样本标签和多个定量指标，使用标准化热图。"
    if len(numeric) >= 2 and not categorical:
        return "scatter", "检测到两个定量变量，使用散点图展示关系。"
    if categorical and numeric:
        unique_groups = len({row[table.headers.index(categorical[0])] for row in table.rows})
        if unique_groups < len(table.rows) and len(table.rows) >= 8:
            return "bar", "检测到重复分组观测，使用均值、误差和全部原始点。"
        return "bar", "检测到分类标签和定量指标，使用分组柱状图。"
    raise PlotAgentError("The table needs at least one numeric column for visualization")


def _numeric_values(table: ParsedTable, column: str) -> np.ndarray:
    if column not in table.headers:
        raise PlotAgentError(f"Column '{column}' does not exist")
    values = [_number(row[table.headers.index(column)]) for row in table.rows]
    if any(value is None for value in values):
        raise PlotAgentError(f"Column '{column}' contains non-numeric values")
    return np.asarray(values, dtype=float)


def _professional_style(font_size: int) -> None:
    available = {value.name for value in font_manager.fontManager.ttflist}
    font_family = next(
        (
            value
            for value in (
                "Microsoft YaHei",
                "SimHei",
                "Noto Sans CJK SC",
                "Arial Unicode MS",
                "DejaVu Sans",
            )
            if value in available
        ),
        "DejaVu Sans",
    )
    plt.rcParams.update(
        {
            "font.family": font_family,
            "axes.unicode_minus": False,
            "font.size": font_size,
            "axes.titlesize": font_size + 2,
            "axes.labelsize": font_size,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.linewidth": 0.8,
            "axes.grid": False,
            "xtick.direction": "out",
            "ytick.direction": "out",
            "legend.frameon": False,
            "figure.facecolor": "white",
            "savefig.facecolor": "white",
            "savefig.bbox": None,
        }
    )


def _resolved_columns(table: ParsedTable, request: PlotRequest) -> tuple[str, list[str]]:
    numeric = [name for name in table.headers if table.column_types[name] == "numeric"]
    non_numeric = [name for name in table.headers if table.column_types[name] != "numeric"]
    x_column = (
        request.condition_column
        or request.x_column
        or (non_numeric[0] if non_numeric else table.headers[0])
    )
    y_columns = (
        [request.value_column]
        if request.value_column
        else list(request.y_columns)
    ) or [
        name
        for name in numeric
        if name != request.error_column
        and name != request.replicate_column
        and not _is_replicate_identifier_header(name)
        and not any(marker in name.casefold() for marker in _ERROR_MARKERS)
    ]
    if not y_columns:
        raise PlotAgentError("No numeric response column could be selected")
    return x_column, y_columns[:8]


def _detected_error_column(table: ParsedTable, request: PlotRequest) -> str | None:
    return request.error_column or next(
        (
            name
            for name in table.headers
            if table.column_types[name] == "numeric"
            and any(marker in name.casefold() for marker in _ERROR_MARKERS)
        ),
        None,
    )


def _replicate_id_column(
    table: ParsedTable,
    request: PlotRequest,
    condition_column: str,
) -> str | None:
    if request.replicate_column:
        if request.replicate_column not in table.headers:
            raise PlotAgentError(
                f"Column '{request.replicate_column}' does not exist"
            )
        return request.replicate_column
    return next(
        (
            name
            for name in table.headers
            if name != condition_column
            and _is_replicate_identifier_header(name)
        ),
        None,
    )


def _looks_like_replicate_column(name: str) -> bool:
    return bool(_REPLICATE_HEADER_PATTERN.fullmatch(name.strip()))


def _is_replicate_identifier_header(name: str) -> bool:
    lowered = name.strip().casefold()
    return (
        lowered in _REPLICATE_MARKERS
        or lowered.startswith("replicate ")
        or lowered.startswith("repeat ")
        or "重复编号" in lowered
        or "重复id" in lowered
    )


def _resolve_replicate_data(
    table: ParsedTable,
    request: PlotRequest,
    x_column: str,
    y_columns: list[str],
    error_column: str | None,
) -> ReplicateData | None:
    if request.data_layout == "summary":
        return None
    if x_column not in table.headers:
        raise PlotAgentError(f"Column '{x_column}' does not exist")
    condition_index = table.headers.index(x_column)
    condition_values = [row[condition_index] for row in table.rows]
    duplicated_conditions = len(set(condition_values)) < len(condition_values)

    explicit_long = request.data_layout == "long"
    auto_long = (
        request.data_layout == "auto"
        and error_column is None
        and duplicated_conditions
        and len(y_columns) == 1
    )
    if explicit_long or auto_long:
        value_column = request.value_column or (y_columns[0] if y_columns else None)
        if value_column is None:
            raise PlotAgentError("Long replicate data needs a numeric value column")
        values = _numeric_values(table, value_column)
        replicate_column = _replicate_id_column(table, request, x_column)
        replicate_index = (
            table.headers.index(replicate_column) if replicate_column else None
        )
        conditions = list(dict.fromkeys(condition_values))
        grouped_values: list[tuple[float, ...]] = []
        grouped_ids: list[tuple[str, ...]] = []
        grouped_rows: list[tuple[int, ...]] = []
        for condition in conditions:
            row_indices = tuple(
                index
                for index, value in enumerate(condition_values)
                if value == condition
            )
            grouped_rows.append(row_indices)
            grouped_values.append(tuple(float(values[index]) for index in row_indices))
            grouped_ids.append(
                tuple(
                    table.rows[index][replicate_index]
                    if replicate_index is not None
                    else str(position + 1)
                    for position, index in enumerate(row_indices)
                )
            )
        return ReplicateData(
            layout="long",
            condition_column=x_column,
            value_label=value_column,
            conditions=tuple(conditions),
            values=tuple(grouped_values),
            replicate_ids=tuple(grouped_ids),
            source_row_indices=tuple(grouped_rows),
            replicate_column=replicate_column,
            replicate_columns=(),
        )

    explicit_wide = request.data_layout == "wide"
    replicate_columns = list(request.replicate_columns)
    if not replicate_columns:
        replicate_columns = [
            name
            for name in y_columns
            if table.column_types.get(name) == "numeric"
        ]
    auto_wide = (
        request.data_layout == "auto"
        and error_column is None
        and len(replicate_columns) >= 2
        and all(_looks_like_replicate_column(name) for name in replicate_columns)
    )
    if explicit_wide or auto_wide:
        if len(replicate_columns) < 2:
            raise PlotAgentError("Wide replicate data needs at least two replicate columns")
        if any(
            name not in table.headers or table.column_types[name] != "numeric"
            for name in replicate_columns
        ):
            raise PlotAgentError("All selected replicate columns must be numeric")
        conditions = list(dict.fromkeys(condition_values))
        grouped_values = []
        grouped_ids = []
        grouped_rows = []
        numeric_columns = {
            name: _numeric_values(table, name) for name in replicate_columns
        }
        for condition in conditions:
            row_indices = tuple(
                index
                for index, value in enumerate(condition_values)
                if value == condition
            )
            values_for_condition: list[float] = []
            ids_for_condition: list[str] = []
            for row_position, row_index in enumerate(row_indices):
                for column in replicate_columns:
                    values_for_condition.append(float(numeric_columns[column][row_index]))
                    ids_for_condition.append(f"{row_position + 1}:{column}")
            grouped_values.append(tuple(values_for_condition))
            grouped_ids.append(tuple(ids_for_condition))
            grouped_rows.append(row_indices)
        return ReplicateData(
            layout="wide",
            condition_column=x_column,
            value_label=request.value_column or "测量值",
            conditions=tuple(conditions),
            values=tuple(grouped_values),
            replicate_ids=tuple(grouped_ids),
            source_row_indices=tuple(grouped_rows),
            replicate_column=None,
            replicate_columns=tuple(replicate_columns),
        )
    return None


_T_CRITICAL_95 = {
    1: 12.706,
    2: 4.303,
    3: 3.182,
    4: 2.776,
    5: 2.571,
    6: 2.447,
    7: 2.365,
    8: 2.306,
    9: 2.262,
    10: 2.228,
    11: 2.201,
    12: 2.179,
    13: 2.160,
    14: 2.145,
    15: 2.131,
    16: 2.120,
    17: 2.110,
    18: 2.101,
    19: 2.093,
    20: 2.086,
    21: 2.080,
    22: 2.074,
    23: 2.069,
    24: 2.064,
    25: 2.060,
    26: 2.056,
    27: 2.052,
    28: 2.048,
    29: 2.045,
    30: 2.042,
}


def _replicate_summaries(data: ReplicateData) -> tuple[ReplicateSummary, ...]:
    summaries: list[ReplicateSummary] = []
    for condition, raw_values in zip(data.conditions, data.values, strict=True):
        values = np.asarray(raw_values, dtype=float)
        n = len(values)
        mean = float(values.mean())
        if n < 2:
            summaries.append(
                ReplicateSummary(condition, n, mean, None, None, None, None)
            )
            continue
        sd = float(values.std(ddof=1))
        sem = sd / float(np.sqrt(n))
        critical = _T_CRITICAL_95.get(n - 1, 1.96)
        half_width = critical * sem
        summaries.append(
            ReplicateSummary(
                condition,
                n,
                mean,
                sd,
                sem,
                mean - half_width,
                mean + half_width,
            )
        )
    return tuple(summaries)


def _summary_errors(
    summaries: tuple[ReplicateSummary, ...],
    statistic: SummaryStatistic,
) -> np.ndarray:
    errors: list[float] = []
    for summary in summaries:
        if statistic == "mean_sd":
            errors.append(summary.sd or 0.0)
        elif statistic == "mean_sem":
            errors.append(summary.sem or 0.0)
        else:
            errors.append(
                0.0
                if summary.ci95_high is None
                else summary.ci95_high - summary.mean
            )
    return np.asarray(errors, dtype=float)


def _summary_payload(summary: ReplicateSummary) -> dict[str, object]:
    return {
        "condition": summary.condition,
        "n": summary.n,
        "mean": summary.mean,
        "sd": summary.sd,
        "sem": summary.sem,
        "ci95_low": summary.ci95_low,
        "ci95_high": summary.ci95_high,
    }


def _draw_replicate_points(
    *,
    axis: Axes,
    data: ReplicateData,
    positions: np.ndarray,
    request: PlotRequest,
    colors: list[str],
) -> None:
    if not request.show_all_points:
        return
    generator = np.random.default_rng(42)
    for index, raw_values in enumerate(data.values):
        values = np.asarray(raw_values, dtype=float)
        jitter = generator.normal(positions[index], 0.055, size=len(values))
        axis.scatter(
            jitter,
            values,
            s=max(8, request.point_size * 0.65),
            alpha=0.78,
            color=colors[index % len(colors)],
            edgecolors="white",
            linewidths=0.5,
            zorder=4,
        )


def _draw_paired_lines(
    *,
    axis: Axes,
    data: ReplicateData,
    positions: np.ndarray,
    request: PlotRequest,
) -> None:
    if request.pairing_mode != "paired":
        return
    if data.layout == "long" and data.replicate_column is None:
        raise PlotAgentError(
            "Paired long-format data needs an explicit replicate/subject identifier column"
        )
    observations: dict[str, dict[int, float]] = {}
    for condition_index, (ids, values) in enumerate(
        zip(data.replicate_ids, data.values, strict=True)
    ):
        for replicate_id, value in zip(ids, values, strict=True):
            observations.setdefault(replicate_id, {})[condition_index] = value
    for observed in observations.values():
        ordered = sorted(observed)
        if len(ordered) < 2:
            continue
        axis.plot(
            [positions[index] for index in ordered],
            [observed[index] for index in ordered],
            color="#7A8580",
            alpha=0.35,
            linewidth=max(0.5, request.line_width * 0.45),
            zorder=2,
        )


def _draw_replicate_summary(
    *,
    axis: Axes,
    data: ReplicateData,
    summaries: tuple[ReplicateSummary, ...],
    request: PlotRequest,
    colors: list[str],
    chart_type: Literal["bar", "line"],
) -> np.ndarray:
    positions = np.arange(len(summaries), dtype=float)
    means = np.asarray([summary.mean for summary in summaries], dtype=float)
    errors = _summary_errors(summaries, request.summary_stat)
    _draw_paired_lines(
        axis=axis,
        data=data,
        positions=positions,
        request=request,
    )
    if chart_type == "bar":
        axis.bar(
            positions,
            means,
            width=0.68,
            color=[colors[index % len(colors)] for index in range(len(summaries))],
            edgecolor="white",
            linewidth=min(1.5, request.line_width * 0.35),
            yerr=errors,
            capsize=4,
            zorder=3,
        )
    else:
        axis.errorbar(
            positions,
            means,
            yerr=errors,
            color=colors[0],
            linewidth=request.line_width,
            marker="o",
            markersize=max(4, np.sqrt(request.point_size) * 0.75),
            capsize=4,
            zorder=3,
        )
    _draw_replicate_points(
        axis=axis,
        data=data,
        positions=positions,
        request=request,
        colors=colors,
    )
    axis.set_xticks(
        positions,
        [summary.condition for summary in summaries],
        rotation=30,
        ha="right",
    )
    if request.show_sample_size:
        span = max(
            1e-9,
            float(np.max(means + errors) - np.min(means - errors)),
        )
        for index, summary in enumerate(summaries):
            prefix = "n" if request.replicate_unit == "biological" else "n_tech"
            axis.text(
                positions[index],
                means[index] + errors[index] + span * 0.05,
                f"{prefix}={summary.n}",
                ha="center",
                va="bottom",
                fontsize=max(7, request.font_size - 2),
                color="#526159",
            )
    return positions


def render_plot(table: ParsedTable, request: PlotRequest, target: Path) -> PlotArtifact:
    with _RENDER_LOCK:
        return _render_plot_locked(table, request, target)


def _render_plot_locked(
    table: ParsedTable,
    request: PlotRequest,
    target: Path,
) -> PlotArtifact:
    x_column, y_columns = _resolved_columns(table, request)
    error_column = _detected_error_column(table, request)
    replicate_data = _resolve_replicate_data(
        table,
        request,
        x_column,
        y_columns,
        error_column,
    )
    chart_type, rationale = _select_chart(table, request, replicate_data)
    replicate_summaries = (
        _replicate_summaries(replicate_data) if replicate_data else ()
    )
    warnings: list[str] = []
    if replicate_data and any(summary.n < 2 for summary in replicate_summaries):
        warnings.append("至少一个实验组只有 1 个重复，无法估计 SD、SEM 或 95% CI。")
    if replicate_data and request.replicate_unit == "technical":
        warnings.append(
            "当前重复被标记为技术重复；它们用于评估测量精密度，不能替代独立生物学重复。"
        )
    if replicate_data and any(summary.n < 3 for summary in replicate_summaries):
        warnings.append("至少一个实验组少于 3 个重复；请谨慎解释离散度与区间估计。")
    _professional_style(request.font_size)
    figure: Figure
    figure, axis = plt.subplots(
        figsize=(request.figure_width, request.figure_height),
        dpi=min(request.dpi, 200),
    )
    palettes = {
        "journal": ["#3B6FB6", "#D95F4C", "#4A9A77", "#8B6BB8", "#C58A32", "#4D8499"],
        "npg": ["#E64B35", "#4DBBD5", "#00A087", "#3C5488", "#F39B7F", "#8491B4"],
        "nejm": ["#BC3C29", "#0072B5", "#E18727", "#20854E", "#7876B1", "#6F99AD"],
        "lancet": ["#00468B", "#ED0000", "#42B540", "#0099B4", "#925E9F", "#FDAF91"],
        "jama": ["#374E55", "#DF8F44", "#00A1D5", "#B24745", "#79AF97", "#6A6599"],
        "colorblind": ["#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00", "#56B4E9"],
    }
    colors = palettes.get(request.palette, palettes["journal"])
    x_index = table.headers.index(x_column)
    x_labels = [row[x_index] for row in table.rows]
    if chart_type == "bar":
        if replicate_data:
            _draw_replicate_summary(
                axis=axis,
                data=replicate_data,
                summaries=replicate_summaries,
                request=request,
                colors=colors,
                chart_type="bar",
            )
            y_columns = [replicate_data.value_label]
        else:
            positions = np.arange(len(table.rows), dtype=float)
            width = 0.78 / len(y_columns)
            errors = _numeric_values(table, error_column) if error_column else None
            for index, column in enumerate(y_columns):
                values = _numeric_values(table, column)
                offset = (index - (len(y_columns) - 1) / 2) * width
                axis.bar(
                    positions + offset,
                    values,
                    width=width,
                    label=column,
                    color=colors[index % len(colors)],
                    edgecolor="white",
                    linewidth=min(1.5, request.line_width * 0.35),
                    yerr=errors if len(y_columns) == 1 else None,
                    capsize=3,
                )
            axis.set_xticks(positions, x_labels, rotation=35, ha="right")
        axis.set_xlabel(x_column)
        axis.set_ylabel(" / ".join(y_columns))
    elif chart_type == "line":
        if replicate_data:
            _draw_replicate_summary(
                axis=axis,
                data=replicate_data,
                summaries=replicate_summaries,
                request=request,
                colors=colors,
                chart_type="line",
            )
            y_columns = [replicate_data.value_label]
        else:
            positions = np.arange(len(table.rows), dtype=float)
            for index, column in enumerate(y_columns):
                axis.plot(
                    positions,
                    _numeric_values(table, column),
                    marker="o",
                    markersize=4,
                    linewidth=request.line_width,
                    color=colors[index % len(colors)],
                    label=column,
                )
            axis.set_xticks(positions, x_labels, rotation=35, ha="right")
        axis.set_xlabel(x_column)
        axis.set_ylabel(" / ".join(y_columns))
    elif chart_type == "scatter":
        x_numeric = request.x_column or y_columns[0]
        y_column = y_columns[1] if len(y_columns) > 1 else y_columns[0]
        x_values = _numeric_values(table, x_numeric)
        y_values = _numeric_values(table, y_column)
        axis.scatter(
            x_values,
            y_values,
            s=request.point_size,
            alpha=0.78,
            color=colors[0],
            edgecolors="white",
            linewidths=0.5,
        )
        if len(x_values) >= 3 and np.ptp(x_values) > 0:
            coefficients = np.polyfit(x_values, y_values, 1)
            fitted = np.polyval(coefficients, x_values)
            order = np.argsort(x_values)
            axis.plot(
                x_values[order],
                fitted[order],
                color="#333333",
                linewidth=max(0.8, request.line_width * 0.7),
                linestyle="--",
            )
        axis.set_xlabel(x_numeric)
        axis.set_ylabel(y_column)
        x_column = x_numeric
        y_columns = [y_column]
    elif chart_type == "distribution":
        if replicate_data:
            groups = list(replicate_data.conditions)
            numeric_groups = [list(values) for values in replicate_data.values]
            y_columns = [replicate_data.value_label]
        else:
            groups = list(dict.fromkeys(x_labels))
            values_by_group = [
                [
                    _number(row[table.headers.index(y_columns[0])])
                    for row in table.rows
                    if row[x_index] == group
                ]
                for group in groups
            ]
            numeric_groups = [
                [float(value) for value in values if value is not None]
                for values in values_by_group
            ]
        axis.boxplot(
            numeric_groups,
            tick_labels=groups,
            patch_artist=True,
            showfliers=False,
        )
        if replicate_data:
            _draw_paired_lines(
                axis=axis,
                data=replicate_data,
                positions=np.arange(1, len(groups) + 1, dtype=float),
                request=request,
            )
        generator = np.random.default_rng(42)
        for index, group_values in enumerate(numeric_groups, start=1):
            jitter = generator.normal(index, 0.045, size=len(group_values))
            axis.scatter(
                jitter,
                group_values,
                s=max(8, request.point_size * 0.6),
                alpha=0.65,
                color=colors[(index - 1) % len(colors)],
                edgecolors="white",
                linewidths=0.4,
            )
        axis.tick_params(axis="x", rotation=30)
        axis.set_xlabel(x_column)
        axis.set_ylabel(y_columns[0])
    else:
        numeric_columns = y_columns if len(y_columns) >= 2 else [
            name for name in table.headers if table.column_types[name] == "numeric"
        ]
        matrix = np.column_stack([_numeric_values(table, name) for name in numeric_columns])
        means = matrix.mean(axis=0)
        std = matrix.std(axis=0)
        std[std == 0] = 1
        normalized = (matrix - means) / std
        image = axis.imshow(normalized, aspect="auto", cmap="RdBu_r", vmin=-2.5, vmax=2.5)
        axis.set_xticks(np.arange(len(numeric_columns)), numeric_columns, rotation=35, ha="right")
        axis.set_yticks(np.arange(len(table.rows)), x_labels)
        axis.set_xlabel("标准化定量指标")
        axis.set_ylabel(x_column)
        figure.colorbar(image, ax=axis, label="Z-score", fraction=0.04, pad=0.02)
        y_columns = numeric_columns
    if len(y_columns) > 1 and chart_type in {"bar", "line"}:
        legend_locations = {
            "best": "best",
            "top": "upper center",
            "bottom": "lower center",
            "left": "center left",
            "right": "center right",
        }
        if request.legend_position != "none":
            axis.legend(
                loc=legend_locations.get(request.legend_position, "best"),
                ncols=min(3, len(y_columns)),
            )
    axis.set_title(
        request.title or "科研数据可视化",
        loc="left",
        fontweight="bold",
    )
    if request.caption:
        figure.text(
            0.01, 0.01, request.caption,
            ha="left", va="bottom", fontsize=7, color="#555555",
        )
    axis.margins(x=0.03)
    if request.show_grid:
        axis.grid(axis="y", color="#D7DCE0", linewidth=0.6, alpha=0.75)
    figure.tight_layout(rect=(0, 0.04 if request.caption else 0, 1, 1))
    target.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(
        target,
        format="png",
        dpi=request.dpi,
        metadata={"Software": "Science Buddy Plot Agent"},
    )
    plt.close(figure)
    if len(table.rows) > 80 and chart_type in {"bar", "line"}:
        warnings.append("数据行较多，标签可能拥挤；建议筛选或改用热图。")
    with Image.open(target) as rendered:
        width, height = rendered.size
    render_spec: dict[str, object] = {
        "version": "workbench-plot-agent-v2",
        "chart_type": chart_type,
        "title": request.title,
        "caption": request.caption,
        "x_column": x_column,
        "y_columns": y_columns,
        "group_column": request.group_column,
        "error_column": error_column,
        "palette_name": request.palette,
        "palette": colors,
        "font_size": request.font_size,
        "line_width": request.line_width,
        "point_size": request.point_size,
        "figure_width": request.figure_width,
        "figure_height": request.figure_height,
        "dpi": request.dpi,
        "show_grid": request.show_grid,
        "legend_position": request.legend_position,
        "data_layout": replicate_data.layout if replicate_data else "summary",
        "condition_column": x_column,
        "value_column": replicate_data.value_label if replicate_data else None,
        "replicate_column": (
            replicate_data.replicate_column if replicate_data else None
        ),
        "replicate_columns": (
            list(replicate_data.replicate_columns) if replicate_data else []
        ),
        "summary_stat": request.summary_stat,
        "show_all_points": request.show_all_points,
        "show_sample_size": request.show_sample_size,
        "replicate_unit": request.replicate_unit,
        "pairing_mode": request.pairing_mode,
        "replicate_stats": [
            _summary_payload(summary) for summary in replicate_summaries
        ],
        "source_rows": len(table.rows),
        "source_columns": len(table.headers),
        "reference_gallery": "https://exts.ggplot2.tidyverse.org/gallery/",
    }
    return PlotArtifact(
        path=target,
        chart_type=chart_type,
        rationale=rationale,
        detected_columns=table.column_types,
        warnings=tuple(warnings),
        source_table_hash=table.source_hash,
        render_spec=render_spec,
        width=width,
        height=height,
        byte_size=target.stat().st_size,
    )
