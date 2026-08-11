from __future__ import annotations

from dataclasses import dataclass

from src.retrieval.module import RetrievedChunk

SYSTEM_PROMPT = (
    "你是一个知识库问答助手。只能根据提供的来源回答问题，无法确认的信息请明确告知用户。"
    "回答中的事实必须使用来源 id 标注，并且只使用 ASCII 方括号：单个来源写作 [n]，"
    "多个来源连续写作 [1][2]。n 必须是当前提供的来源 id；"
    "不得使用 #、中文方头括号【】、脚注定义或不存在的编号。"
)

UNTRUSTED_TEMPLATE = """<untrusted-source id="{id}">
标题: {title}
页码: {page}
{kb_info}内容: {content}
</untrusted-source>"""

REWRITE_SYSTEM_PROMPT = """你负责把多轮对话中的最新问题改写成可独立理解的检索问题。
结合对话历史补全代词、省略的实体和必要上下文，但不得回答问题，不得添加历史中不存在的信息。
只输出改写后的问题正文，不要输出解释、标签、引号或 Markdown。"""


def build_rewrite_messages(
    question: str,
    history: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Build a focused prompt that turns a follow-up into a standalone query."""
    return [
        {"role": "system", "content": REWRITE_SYSTEM_PROMPT},
        *history,
        {"role": "user", "content": question},
    ]


def build_messages(
    question: str,
    history: list[dict[str, str]],
    sources: list[RetrievedChunk],
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(history)

    if sources:
        parts = []
        for i, s in enumerate(sources):
            # kb_name 为 None 时输出与旧版逐字符一致
            kb_info = f"知识库: {s.kb_name}\n" if s.kb_name else ""
            parts.append(
                UNTRUSTED_TEMPLATE.format(
                    id=i + 1,
                    title=s.title,
                    page=s.page or "N/A",
                    kb_info=kb_info,
                    content=s.content,
                )
            )
        context = "\n\n".join(parts)
        messages.append({"role": "user", "content": f"{context}\n\n用户问题: {question}"})
    else:
        messages.append({"role": "user", "content": question})

    return messages
