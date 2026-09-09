from science_buddy.domain.providers import StructuredGenerationRequest
from science_buddy.services.resilience_hooks import (
    AdaptiveDualPassHook,
    PreFlightCompactionHook,
    StreamingASTContinuationHook,
    WorkingStateContract,
)


def test_working_state_contract_renders_all_six_sections() -> None:
    contract = WorkingStateContract(
        user_intent_and_scope="研究克雷伯菌噬菌体尾部蛋白进化",
        key_technical_decisions=[
            "确定以受体结合域作为先导点突变靶点",
            "决策门：IC50 < 100nM 且吸附率提高 30% 则进入动物模型",
        ],
        artifacts_and_evidence_state=[
            "方案当前为 V1 draft",
            "已绑定证据: [ev1.tail_fiber_1], [ev1.klebsiella_ref]",
        ],
        errors_and_resolutions=[
            "上轮因思维链过长触发 finish_reason=length，已自动提额自愈",
        ],
        pending_tasks=[
            "WP1: 先导突变库构建 (已完成)",
            "WP2: 正交宿主抗性筛选 (进行中)",
        ],
        immediate_next_action="生成 WP3 动物模型的详细步骤与质控参数",
    )
    md = contract.to_markdown()
    assert "<working_state_contract version=\"1.0\">" in md
    assert "## 1. User Intent & Scope" in md
    assert "## 2. Key Technical Decisions" in md
    assert "## 3. Files & Artifacts Touched & Current State" in md
    assert "## 4. Errors & Resolutions" in md
    assert "## 5. Pending Task List" in md
    assert "## 6. Immediate Next Action" in md
    assert "研究克雷伯菌噬菌体尾部蛋白进化" in md
    assert "[ev1.tail_fiber_1]" in md


def test_pre_flight_compaction_tiered_watermarks() -> None:
    # Tier 0: < 50%
    res0 = PreFlightCompactionHook.apply_tiered_compaction(
        raw_context="short context",
        current_tokens=2000,
        max_context_tokens=10000,
    )
    assert res0.tier == "tier0_passthrough"
    assert res0.content == "short context"

    # Tier 1: 50% - 70%
    long_payload = "A" * 6000
    res1 = PreFlightCompactionHook.apply_tiered_compaction(
        raw_context=long_payload,
        current_tokens=6000,
        max_context_tokens=10000,
    )
    assert res1.tier == "tier1_offloaded"
    assert "<content_offloaded_to_storage" in res1.content or len(res1.content) < len(long_payload)

    # Tier 2: 70% - 85%
    res2 = PreFlightCompactionHook.apply_tiered_compaction(
        raw_context="line 1\n\n\n\nline 2",
        current_tokens=7500,
        max_context_tokens=10000,
    )
    assert res2.tier == "tier2_projected"

    # Tier 3: >= 85% with <= 5 recency anchors
    anchors = [f"anchor_chunk_{i}" for i in range(1, 8)]
    res3 = PreFlightCompactionHook.apply_tiered_compaction(
        raw_context="huge context",
        current_tokens=9000,
        max_context_tokens=10000,
        recent_anchors=anchors,
    )
    assert res3.tier == "tier3_summarized"
    assert res3.anchors_injected == 5
    assert "<anchor_1>anchor_chunk_1</anchor_1>" in res3.content
    assert "<anchor_5>anchor_chunk_5</anchor_5>" in res3.content
    assert "<anchor_6>" not in res3.content


def test_pre_flight_output_reservation_by_model() -> None:
    assert PreFlightCompactionHook.calculate_output_reservation("deepseek", "max") == 65536
    assert PreFlightCompactionHook.calculate_output_reservation("deepseek", "deep") == 32768
    assert PreFlightCompactionHook.calculate_output_reservation("openai", "deep") == 24576
    assert PreFlightCompactionHook.calculate_output_reservation("anthropic", "balanced") == 8192


def test_streaming_ast_continuation_finds_last_closed_boundary() -> None:
    partial = (
        '<wp id="WP1">Valid WP1</wp>\n'
        '<wp id="WP2">Valid WP2</wp>\n'
        '<wp id="WP3">Cut off content'
    )
    valid_prefix, tag = StreamingASTContinuationHook.find_last_closed_boundary(partial)
    assert tag == "</wp>"
    assert valid_prefix == "<wp id=\"WP1\">Valid WP1</wp>\n<wp id=\"WP2\">Valid WP2</wp>"

    prompt = StreamingASTContinuationHook.build_continuation_prompt(valid_prefix, tag)
    assert "已成功接收前文有效输出" in prompt
    assert "【严禁重复前文已输出的内容】" in prompt

    stitched = StreamingASTContinuationHook.stitch_continuations([
        valid_prefix,
        "<wp id=\"WP3\">Finished WP3</wp>",
    ])
    assert "<wp id=\"WP1\">" in stitched
    assert "<wp id=\"WP3\">" in stitched


def test_adaptive_dual_pass_hook_decision_and_requests() -> None:
    assert AdaptiveDualPassHook.should_decouple("brainstorm.experiment_design") is True
    assert AdaptiveDualPassHook.should_decouple("brainstorm.method_agent") is True
    assert AdaptiveDualPassHook.should_decouple("brainstorm.scientific_question") is False
    assert AdaptiveDualPassHook.should_decouple("brainstorm.critic") is False

    req = StructuredGenerationRequest(
        system_instruction="Design experiment.",
        user_content="Research interest.",
        response_schema={"type": "object"},
        operation="brainstorm.experiment_design",
        depth="deep",
    )
    pass1 = AdaptiveDualPassHook.create_pass_1_request(req)
    assert pass1.operation == "brainstorm.experiment_design.free_draft"
    assert "Do NOT output JSON" in pass1.system_instruction

    pass2 = AdaptiveDualPassHook.create_pass_2_request(req, "# Draft Content")
    assert pass2.operation == "brainstorm.experiment_design.organizer"
    assert "<scientific_draft>" in pass2.user_content
    assert "# Draft Content" in pass2.user_content
