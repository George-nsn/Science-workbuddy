import re
from dataclasses import dataclass, replace
from typing import Any

_HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_WORD_PATTERN = re.compile(r"[a-zA-Z0-9_./+-]{2,}|[\u4e00-\u9fff]+")
_SENTENCE_BOUNDARY = re.compile(r"(?<=[。！？!?；;.!])\s*")
_STABLE_HEADING_PATTERN = re.compile(
    r"研究问题|科学问题|假说|假设|研究目标|目标|用户偏好|约束|安全|伦理|"
    r"决策|结论|纳入|排除|终点|统计|质控|证据缺口|"
    r"research question|hypothesis|objective|constraint|preference|safety|ethic|"
    r"decision|endpoint|statistical|quality control|evidence gap",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class ContextModule:
    ordinal: int
    path: tuple[str, ...]
    chunk_index: int
    content: str
    stable: bool


@dataclass(frozen=True, slots=True)
class ContextSelection:
    modules: tuple[ContextModule, ...]
    original_characters: int
    total_modules: int
    char_budget: int
    query_terms: tuple[str, ...]

    @property
    def selected_characters(self) -> int:
        return sum(len(module.content) for module in self.modules)

    def to_payload(self) -> dict[str, Any]:
        return {
            "strategy": "markdown_section_rag_v1",
            "original_characters": self.original_characters,
            "selected_characters": self.selected_characters,
            "char_budget": self.char_budget,
            "selected_module_count": len(self.modules),
            "omitted_module_count": max(0, self.total_modules - len(self.modules)),
            "query_terms": list(self.query_terms),
            "modules": [
                {
                    "ordinal": module.ordinal,
                    "section_path": list(module.path),
                    "chunk_index": module.chunk_index,
                    "content": module.content,
                    "stable": module.stable,
                }
                for module in self.modules
            ],
        }


def _tokens(value: str) -> set[str]:
    values: set[str] = set()
    for match in _WORD_PATTERN.findall(value.casefold()):
        if re.fullmatch(r"[\u4e00-\u9fff]+", match):
            if len(match) == 1:
                values.add(match)
            else:
                values.update(match[index : index + 2] for index in range(len(match) - 1))
        else:
            values.add(match)
    return values


def _split_oversized_block(value: str, max_chars: int) -> list[str]:
    if len(value) <= max_chars:
        return [value]
    sentences = [item.strip() for item in _SENTENCE_BOUNDARY.split(value) if item.strip()]
    if len(sentences) <= 1:
        return [value[index : index + max_chars] for index in range(0, len(value), max_chars)]
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        if len(sentence) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(
                sentence[index : index + max_chars]
                for index in range(0, len(sentence), max_chars)
            )
            continue
        candidate = f"{current} {sentence}".strip()
        if current and len(candidate) > max_chars:
            chunks.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def _chunk_section(value: str, max_chars: int) -> list[str]:
    blocks = [block.strip() for block in re.split(r"\n\s*\n", value) if block.strip()]
    expanded = [
        chunk
        for block in blocks
        for chunk in _split_oversized_block(block, max_chars)
    ]
    chunks: list[str] = []
    current = ""
    for block in expanded:
        candidate = f"{current}\n\n{block}".strip()
        if current and len(candidate) > max_chars:
            chunks.append(current)
            current = block
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def split_markdown_modules(value: str, *, max_module_chars: int = 4800) -> list[ContextModule]:
    text = value.strip()
    if not text:
        return []
    sections: list[tuple[tuple[str, ...], str]] = []
    heading_stack: dict[int, str] = {}
    current_path: tuple[str, ...] = ("文档概述",)
    current_lines: list[str] = []

    def flush() -> None:
        content = "\n".join(current_lines).strip()
        if content:
            sections.append((current_path, content))

    for line in text.splitlines():
        heading = _HEADING_PATTERN.match(line)
        if heading:
            flush()
            current_lines = [line]
            level = len(heading.group(1))
            heading_stack[level] = heading.group(2).strip()
            for deeper in range(level + 1, 7):
                heading_stack.pop(deeper, None)
            current_path = tuple(
                heading_stack[item] for item in sorted(heading_stack) if item <= level
            )
        else:
            current_lines.append(line)
    flush()

    modules: list[ContextModule] = []
    ordinal = 0
    for path, content in sections:
        chunks = _chunk_section(content, max_module_chars)
        stable = bool(_STABLE_HEADING_PATTERN.search(" > ".join(path)))
        for chunk_index, chunk in enumerate(chunks):
            modules.append(
                ContextModule(
                    ordinal=ordinal,
                    path=path,
                    chunk_index=chunk_index,
                    content=chunk,
                    stable=stable,
                )
            )
            ordinal += 1
    return modules


def select_markdown_context(
    value: str,
    *,
    query: str,
    char_budget: int,
    max_module_chars: int = 4800,
) -> ContextSelection:
    modules = split_markdown_modules(value, max_module_chars=max_module_chars)
    query_terms = _tokens(query)
    if not modules:
        return ContextSelection((), len(value), 0, char_budget, tuple(sorted(query_terms)))

    total = len(modules)
    scored: list[tuple[float, ContextModule]] = []
    for module in modules:
        path_text = " > ".join(module.path)
        path_overlap = len(query_terms & _tokens(path_text))
        content_overlap = len(query_terms & _tokens(module.content))
        first_in_section = module.chunk_index == 0
        score = (
            path_overlap * 16
            + content_overlap * 5
            + (10 if module.stable else 0)
            + (3 if first_in_section else 0)
            + (module.ordinal / max(total, 1))
        )
        scored.append((score, module))

    ranked = sorted(
        scored,
        key=lambda item: (
            item[1].ordinal == 0,
            item[1].stable,
            item[0],
            -item[1].ordinal,
        ),
        reverse=True,
    )
    selected: list[ContextModule] = []
    used = 0
    selected_paths: set[tuple[str, ...]] = set()

    for pass_index in (0, 1):
        for _, module in ranked:
            if module in selected:
                continue
            if pass_index == 0 and module.path in selected_paths and not module.stable:
                continue
            remaining = char_budget - used
            if remaining <= 0:
                break
            size = len(module.content)
            if size <= remaining:
                content = module.content
            else:
                # Keep a bounded prefix instead of dropping the module entirely:
                # the budget is always spent on the best-ranked content.
                prefix = _split_oversized_block(module.content, remaining)[0]
                if not prefix.strip():
                    continue
                content = prefix
            selected.append(replace(module, content=content))
            selected_paths.add(module.path)
            used += len(content)
            if used >= char_budget:
                break
        if used >= char_budget:
            break

    selected.sort(key=lambda module: module.ordinal)
    return ContextSelection(
        modules=tuple(selected),
        original_characters=len(value),
        total_modules=total,
        char_budget=char_budget,
        query_terms=tuple(sorted(query_terms)),
    )
