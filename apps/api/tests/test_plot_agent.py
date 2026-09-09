from pathlib import Path

import pytest

from science_buddy.services.plot_agent import (
    PlotAgentError,
    PlotRequest,
    parse_markdown_table,
    render_plot,
)


def test_plot_agent_detects_time_series_and_heatmap(tmp_path: Path) -> None:
    time_table = parse_markdown_table(
        "| 日期 | 对照 | 处理 |\n"
        "| --- | --- | --- |\n"
        "| 2026-01-01 | 1.0 | 1.2 |\n"
        "| 2026-01-02 | 1.4 | 1.8 |\n"
        "| 2026-01-03 | 1.6 | 2.2 |"
    )
    artifact = render_plot(
        time_table,
        PlotRequest(chart_type="auto", palette="nejm"),
        tmp_path / "line.png",
    )
    heatmap_table = parse_markdown_table(
        "| 样本 | GeneA | GeneB | GeneC |\n"
        "| --- | --- | --- | --- |\n"
        "| S1 | 1 | 2 | 3 |\n"
        "| S2 | 4 | 5 | 6 |"
    )
    heatmap = render_plot(
        heatmap_table,
        PlotRequest(chart_type="auto"),
        tmp_path / "heatmap.png",
    )

    assert time_table.column_types["日期"] == "temporal"
    assert artifact.chart_type == "line"
    assert artifact.path.read_bytes().startswith(b"\x89PNG")
    assert heatmap.chart_type == "heatmap"


def test_plot_agent_rejects_non_numeric_table() -> None:
    table = parse_markdown_table(
        "| 样本 | 结果 |\n| --- | --- |\n| A | 阳性 |\n| B | 阴性 |"
    )

    with pytest.raises(PlotAgentError, match="numeric"):
        render_plot(table, PlotRequest(chart_type="auto"), Path("ignored.png"))


def test_plot_agent_accepts_milkdown_compact_separator() -> None:
    table = parse_markdown_table(
        "| 组别 | 生物学重复 | 数值 |\n"
        "| -- | ----- | -- |\n"
        "| 对照 | B1 | 1.0 |"
    )

    assert table.headers == ("组别", "生物学重复", "数值")


def test_plot_agent_summarizes_long_biological_replicates(tmp_path: Path) -> None:
    table = parse_markdown_table(
        "| 组别 | 生物学重复 | 荧光强度 |\n"
        "| --- | --- | --- |\n"
        "| 对照 | B1 | 1.0 |\n"
        "| 对照 | B2 | 1.2 |\n"
        "| 对照 | B3 | 0.8 |\n"
        "| 处理 | B1 | 2.0 |\n"
        "| 处理 | B2 | 2.4 |\n"
        "| 处理 | B3 | 2.2 |"
    )

    artifact = render_plot(
        table,
        PlotRequest(
            chart_type="auto",
            data_layout="long",
            condition_column="组别",
            value_column="荧光强度",
            replicate_column="生物学重复",
            summary_stat="mean_sem",
            show_all_points=True,
            pairing_mode="paired",
        ),
        tmp_path / "long-replicates.png",
    )

    stats = artifact.render_spec["replicate_stats"]
    assert artifact.chart_type == "bar"
    assert artifact.render_spec["data_layout"] == "long"
    assert artifact.render_spec["show_all_points"] is True
    assert isinstance(stats, list)
    assert stats[0]["n"] == 3
    assert stats[0]["mean"] == pytest.approx(1.0)
    assert stats[0]["sd"] == pytest.approx(0.2)
    assert stats[0]["sem"] == pytest.approx(0.2 / (3**0.5))
    assert "text_elements" not in artifact.render_spec
    assert artifact.path.read_bytes().startswith(b"\x89PNG")


def test_plot_agent_summarizes_wide_replicates_and_warns_for_technical_unit(
    tmp_path: Path,
) -> None:
    table = parse_markdown_table(
        "| 组别 | Rep1 | Rep2 | Rep3 |\n"
        "| --- | --- | --- | --- |\n"
        "| 对照 | 1.0 | 1.2 | 0.8 |\n"
        "| 处理 | 2.0 | 2.4 | 2.2 |"
    )

    artifact = render_plot(
        table,
        PlotRequest(
            chart_type="bar",
            data_layout="wide",
            condition_column="组别",
            replicate_columns=("Rep1", "Rep2", "Rep3"),
            replicate_unit="technical",
            summary_stat="mean_ci95",
        ),
        tmp_path / "wide-replicates.png",
    )

    stats = artifact.render_spec["replicate_stats"]
    assert artifact.render_spec["replicate_columns"] == ["Rep1", "Rep2", "Rep3"]
    assert isinstance(stats, list)
    assert stats[1]["n"] == 3
    assert stats[1]["mean"] == pytest.approx(2.2)
    assert stats[1]["ci95_low"] is not None
    assert any("技术重复" in warning for warning in artifact.warnings)


def test_plot_agent_requires_ids_for_paired_long_data(tmp_path: Path) -> None:
    table = parse_markdown_table(
        "| 组别 | 数值 |\n"
        "| --- | --- |\n"
        "| 对照 | 1.0 |\n"
        "| 对照 | 1.2 |\n"
        "| 处理 | 2.0 |\n"
        "| 处理 | 2.2 |"
    )

    with pytest.raises(PlotAgentError, match="identifier"):
        render_plot(
            table,
            PlotRequest(
                chart_type="bar",
                data_layout="long",
                condition_column="组别",
                value_column="数值",
                pairing_mode="paired",
            ),
            tmp_path / "paired-without-id.png",
        )
