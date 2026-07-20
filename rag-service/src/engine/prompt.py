from __future__ import annotations

from dataclasses import dataclass

from src.retrieval.module import RetrievedChunk

SYSTEM_PROMPT = "你是一个知识库问答助手。只能根据提供的来源回答问题，无法确认的信息请明确告知用户。"

UNTRUSTED_TEMPLATE = """<untrusted-source id="{id}">
标题: {title}
页码: {page}
内容: {content}
</untrusted-source>"""


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
            parts.append(
                UNTRUSTED_TEMPLATE.format(
                    id=i + 1,
                    title=s.title,
                    page=s.page or "N/A",
                    content=s.content,
                )
            )
        context = "\n\n".join(parts)
        messages.append({"role": "user", "content": f"{context}\n\n用户问题: {question}"})
    else:
        messages.append({"role": "user", "content": question})

    return messages
