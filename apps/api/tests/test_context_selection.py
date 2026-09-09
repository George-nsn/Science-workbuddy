from science_buddy.services.context_selection import (
    select_markdown_context,
    split_markdown_modules,
)


def test_markdown_context_selects_relevant_late_section_instead_of_prefix() -> None:
    document = "\n\n".join(
        [
            "# 研究总览\n固定研究目标与总体背景。",
            *[
                f"## 中间模块 {index}\n" + (f"普通内容 {index}。" * 500)
                for index in range(1, 8)
            ],
            "## 晚期关键统计分析\n必须使用预注册模型并报告效应量与置信区间。",
            "## 安全与伦理\n必须保留去标识化与审批约束。",
        ]
    )

    selection = select_markdown_context(
        document,
        query="请检查晚期关键统计分析和置信区间",
        char_budget=9000,
        max_module_chars=2500,
    )
    payload = selection.to_payload()
    paths = [" > ".join(module["section_path"]) for module in payload["modules"]]

    assert payload["strategy"] == "markdown_section_rag_v1"
    assert payload["selected_characters"] <= 9000
    assert payload["omitted_module_count"] > 0
    assert any("晚期关键统计分析" in path for path in paths)
    assert any("安全与伦理" in path for path in paths)
    assert any("研究总览" in path for path in paths)


def test_oversized_markdown_section_is_split_into_bounded_modules() -> None:
    document = "# 方法模块\n" + "。".join(f"步骤 {index}" for index in range(1000))

    modules = split_markdown_modules(document, max_module_chars=600)

    assert len(modules) > 2
    assert all(len(module.content) <= 600 for module in modules)
    assert all(module.path == ("方法模块",) for module in modules)
    assert [module.chunk_index for module in modules] == list(range(len(modules)))


def test_budget_is_spent_on_truncated_prefix_instead_of_dropping_module() -> None:
    document = "\n\n".join(
        [
            "# 研究总览\n固定研究目标与总体背景。",
            "## 关键结果\n" + ("关键结论内容。" * 2000),
        ]
    )

    selection = select_markdown_context(
        document,
        query="关键结果",
        char_budget=1200,
        max_module_chars=6000,
    )
    payload = selection.to_payload()

    assert payload["selected_characters"] <= 1200
    paths = [" > ".join(module["section_path"]) for module in payload["modules"]]
    assert any("关键结果" in path for path in paths)
    key_module = payload["modules"][
        [index for index, path in enumerate(paths) if "关键结果" in path][0]
    ]
    assert 0 < len(key_module["content"]) <= 1200
