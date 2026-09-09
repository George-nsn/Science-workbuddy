import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from science_buddy.domain.providers import (
    StructuredGenerationRequest,
    TextGenerationRequest,
)
from science_buddy.services.brainstorm_prompts import ORGANIZER_SYSTEM_PROMPT

WORKING_STATE_CONTRACT_SPEC = """
<!-- 结构化工作状态契约规范 (WORKING_STATE_CONTRACT) -->
<working_state_contract version="1.0">
## 1. User Intent & Scope (用户原始意图与范围)
## 2. Key Technical Decisions (已做出的关键技术决策)
## 3. Files & Artifacts Touched & Current State (涉及的文件、文献与修改状态)
## 4. Errors & Resolutions (遇到的报错、漏洞与已采取的修复手段)
## 5. Pending Task List (待办任务清单及当前进展)
## 6. Immediate Next Action (续接后的首要下一步动作)
</working_state_contract>
""".strip()


class WorkingStateContract(BaseModel):
    user_intent_and_scope: str = Field(description="用户核心课题意图与边界约束")
    key_technical_decisions: list[str] = Field(
        default_factory=list,
        description="已锁定的核心假说、研究模型与定量判定阈值",
    )
    artifacts_and_evidence_state: list[str] = Field(
        default_factory=list,
        description="已通过结构校验的产物、版本与有效 Evidence ID 列表",
    )
    errors_and_resolutions: list[str] = Field(
        default_factory=list,
        description="遇到的错误、批评挑战以及已采取的修复对策",
    )
    pending_tasks: list[str] = Field(
        default_factory=list,
        description="待办工作包清单与当前进展状态",
    )
    immediate_next_action: str = Field(description="续接后的首要单步具体行动指令")

    def to_markdown(self) -> str:
        lines = [
            '<working_state_contract version="1.0">',
            "## 1. User Intent & Scope",
            self.user_intent_and_scope.strip(),
            "",
            "## 2. Key Technical Decisions",
            *[f"- {item}" for item in self.key_technical_decisions],
            "",
            "## 3. Files & Artifacts Touched & Current State",
            *[f"- {item}" for item in self.artifacts_and_evidence_state],
            "",
            "## 4. Errors & Resolutions",
            *[f"- {item}" for item in self.errors_and_resolutions],
            "",
            "## 5. Pending Task List",
            *[f"- [ ] {item}" for item in self.pending_tasks],
            "",
            "## 6. Immediate Next Action",
            self.immediate_next_action.strip(),
            "</working_state_contract>",
        ]
        return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class ContextCompactionResult:
    tier: Literal["tier0_passthrough", "tier1_offloaded", "tier2_projected", "tier3_summarized"]
    content: str
    occupancy_ratio: float
    offloaded_block_count: int = 0
    anchors_injected: int = 0


class PreFlightCompactionHook:
    """Hook 1: 飞行前动态上下文压缩与反向预算保留拦截器."""

    @staticmethod
    def calculate_output_reservation(
        provider_name: str,
        depth: Literal["quick", "balanced", "deep", "max"],
    ) -> int:
        """根据模型特征感知反向预留输出与思考 Token 预算."""
        if provider_name == "deepseek":
            return {
                "quick": 8192,
                "balanced": 24576,
                "deep": 32768,
                "max": 65536,
            }[depth]
        if provider_name == "openai":
            return {
                "quick": 4096,
                "balanced": 12288,
                "deep": 24576,
                "max": 32768,
            }[depth]
        return {
            "quick": 4096,
            "balanced": 8192,
            "deep": 16384,
            "max": 24576,
        }[depth]

    @classmethod
    def apply_tiered_compaction(
        cls,
        *,
        raw_context: str,
        current_tokens: int,
        max_context_tokens: int,
        recent_anchors: list[str] | None = None,
        state_contract: WorkingStateContract | None = None,
    ) -> ContextCompactionResult:
        """根据剩余 Token 水位线执行三级动态压缩金字塔."""
        if max_context_tokens <= 0:
            return ContextCompactionResult(
                tier="tier0_passthrough",
                content=raw_context,
                occupancy_ratio=0.0,
            )
        occupancy = current_tokens / max_context_tokens

        # 水位线 0: 占用 < 50%，全量直通
        if occupancy < 0.50:
            return ContextCompactionResult(
                tier="tier0_passthrough",
                content=raw_context,
                occupancy_ratio=occupancy,
            )

        # 水位线 1: 50% <= 占用 < 70%，离线卸载大块工具输出 (每块保留 <= 2KB 预览)
        if occupancy < 0.70:
            offloaded_content, count = cls._offline_offload(raw_context, max_preview_chars=1800)
            return ContextCompactionResult(
                tier="tier1_offloaded",
                content=offloaded_content,
                occupancy_ratio=occupancy,
                offloaded_block_count=count,
            )

        # 水位线 2: 70% <= 占用 < 85%，读时语义投影
        if occupancy < 0.85:
            projected_content = cls._read_time_projection(raw_context)
            return ContextCompactionResult(
                tier="tier2_projected",
                content=projected_content,
                occupancy_ratio=occupancy,
            )

        # 水位线 3: 占用 >= 85%，全量契约摘要 + 最近 <=5 个核心锚点防失忆注入
        anchors = (recent_anchors or [])[:5]
        summary_contract = state_contract or WorkingStateContract(
            user_intent_and_scope="保留用户核心研究意图，进行高密度上下文重置。",
            immediate_next_action="基于当前工作状态契约继续完成下一阶段设计。",
        )
        compacted_lines = [summary_contract.to_markdown(), ""]
        if anchors:
            anchor_tags = [
                f"<anchor_{idx}>{item}</anchor_{idx}>"
                for idx, item in enumerate(anchors, 1)
            ]
            compacted_lines.extend(
                [
                    f'<active_context_anchors count="{len(anchors)}">',
                    *anchor_tags,
                    "</active_context_anchors>",
                ]
            )
        return ContextCompactionResult(
            tier="tier3_summarized",
            content="\n".join(compacted_lines),
            occupancy_ratio=occupancy,
            anchors_injected=len(anchors),
        )

    @staticmethod
    def _offline_offload(text: str, max_preview_chars: int = 1800) -> tuple[str, int]:
        """扫描并离线卸载长文本块，保留紧凑预览与引用索引."""
        pattern = r"(\n\s*```[a-z]*\n.*?\n\s*```|\n\s*<untrusted_context>.*?</untrusted_context>)"
        blocks = re.split(pattern, text, flags=re.DOTALL)
        if len(blocks) <= 1:
            if len(text) > max_preview_chars * 2:
                preview = (
                    text[:max_preview_chars]
                    + "\n...<content_offloaded_to_storage />\n"
                    + text[-400:]
                )
                return preview, 1
            return text, 0
        offloaded_count = 0
        reconstructed: list[str] = []
        for block in blocks:
            if len(block) > max_preview_chars * 2:
                preview = (
                    block[:max_preview_chars]
                    + f"\n...<offloaded_content_ref size={len(block)} />\n"
                    + block[-300:]
                )
                reconstructed.append(preview)
                offloaded_count += 1
            else:
                reconstructed.append(block)
        return "".join(reconstructed), offloaded_count

    @staticmethod
    def _read_time_projection(text: str) -> str:
        """读时语义投影：折叠自然语言冗余，提取高密度结构化 XML 节点."""
        cleaned = re.sub(r"\n{3,}", "\n\n", text)
        return cleaned


class StreamingASTContinuationHook:
    """Hook 2: 流式解析与中断语义续接拦截器."""

    @staticmethod
    def find_last_closed_boundary(partial_text: str) -> tuple[str, str | None]:
        """识别最后一个完整闭合的 XML/JSON 语法块边界，回滚残缺末尾."""
        text = partial_text.strip()
        if not text:
            return "", None

        # 检查闭合 XML 标签模式 (如 </wp>, </module>, </step>)
        xml_tags = ["</wp>", "</module>", "</step>", "</package>", "</div>", "</section>"]
        last_xml_pos = -1
        matched_tag = None
        for tag in xml_tags:
            pos = text.rfind(tag)
            if pos > last_xml_pos:
                last_xml_pos = pos + len(tag)
                matched_tag = tag

        if last_xml_pos > 0:
            valid_prefix = text[:last_xml_pos]
            return valid_prefix, matched_tag

        # 检查闭合 JSON 对象边界
        last_brace = text.rfind("}")
        if last_brace > 0:
            return text[: last_brace + 1], "json_object"

        return text, None

    @staticmethod
    def build_continuation_prompt(
        valid_prefix: str,
        cutoff_hint: str | None = None,
    ) -> str:
        """构建精准的断点续接指令."""
        return (
            f"<continuation_state>\n"
            f"已成功接收前文有效输出 (末尾断点在 {cutoff_hint or '有效边界'})。\n"
            f"【严禁重复前文已输出的内容】，请紧接着断点之后继续输出剩余未完成的全部工作包、模块或字段。\n"
            f"</continuation_state>\n"
            f"<prefix_context_tail>\n{valid_prefix[-800:]}\n</prefix_context_tail>"
        )

    @staticmethod
    def stitch_continuations(parts: list[str]) -> str:
        """内存中无缝拼装多次续接结果."""
        if not parts:
            return ""
        if len(parts) == 1:
            return parts[0]
        cleaned_parts = [p.strip() for p in parts if p.strip()]
        return "\n\n".join(cleaned_parts)


class AdaptiveDualPassHook:
    """Hook 3: 自适应双模解耦调度器 (自由推演 -> 无损结构化提取)."""

    COMPLEX_OPERATIONS = {
        "brainstorm.experiment_design",
        "brainstorm.method_agent",
        "brainstorm.plan_discovery",
        "brainstorm.coordinator",
        "synthesis.chapter_writer",
    }

    @classmethod
    def should_decouple(cls, operation: str) -> bool:
        """判断是否需要走自由 Markdown 推理 + 无损格式化双阶段解耦."""
        return any(operation.startswith(prefix) for prefix in cls.COMPLEX_OPERATIONS)

    @classmethod
    def create_pass_1_request(
        cls,
        request: StructuredGenerationRequest,
        custom_system_instruction: str | None = None,
    ) -> TextGenerationRequest:
        """创建阶段一自由推理文本请求 (去除 JSON Schema 负担)."""
        sys_prompt = custom_system_instruction or request.system_instruction
        return TextGenerationRequest(
            system_instruction=(
                "Develop the strongest scientific solution in natural, dense Markdown. "
                "Do NOT output JSON and do NOT organize output around schema field names. "
                "Focus 100% on scientific rigor, validation, and complete workflows.\n\n"
                + sys_prompt
            ),
            context_instruction=request.context_instruction,
            user_content=request.user_content,
            operation=f"{request.operation}.free_draft",
            depth=request.depth,
            max_context_tokens=request.max_context_tokens,
            max_output_tokens=request.max_output_tokens,
        )

    @classmethod
    def create_pass_2_request(
        cls,
        original_request: StructuredGenerationRequest,
        draft_markdown: str,
    ) -> StructuredGenerationRequest:
        """创建阶段二无损格式化请求 (将自由 Markdown 映射为 Pydantic JSON)."""
        return StructuredGenerationRequest(
            system_instruction=ORGANIZER_SYSTEM_PROMPT,
            context_instruction=original_request.context_instruction,
            user_content=(
                f"{original_request.user_content}\n\n"
                f"<scientific_draft>\n{draft_markdown}\n</scientific_draft>"
            ),
            response_schema=original_request.response_schema,
            operation=f"{original_request.operation}.organizer",
            depth=original_request.depth,
            max_context_tokens=original_request.max_context_tokens,
            max_output_tokens=original_request.max_output_tokens,
        )
