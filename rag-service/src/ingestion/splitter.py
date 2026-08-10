from __future__ import annotations

import hashlib
import re
from typing import Any

# 语义边界（按优先级）：段落 > 行 > 句子（中文标点为主）
_PARAGRAPH_RE = re.compile(r"\n\s*\n")
_LINE_RE = re.compile(r"\n")
_SENTENCE_RE = re.compile(r"(?<=[。！？；!?；…])")
_FAQ_MARKER_RE = re.compile(
    r"(?:^|(?<=[。！？!?]))[ \t]*(?:#{1,6}[ \t]*)?"
    r"(?:[一二三四五六七八九十百]+、[ \t]*)?"
    r"FAQ(?=$|\s|\d+\s*[、.．])",
    re.IGNORECASE | re.MULTILINE,
)
_FAQ_CANDIDATE_RE = re.compile(r"(?<!\d)\d+\s*[、.．]")
_FAQ_ITEM_RE = re.compile(
    r"(?<!\d)\d+\s*[、.．]\s*.*?[？?]\s*答[：:]",
    re.DOTALL,
)
_FAQ_SECTION_RE = re.compile(
    r"(?:^|(?<=[。！？!?]))[ \t]*(?:"
    r"#{1,6}[ \t]+\S[^\r\n]*|"
    r"第[一二三四五六七八九十百\d]+[章节篇部][^\r\n]*|"
    r"[一二三四五六七八九十百]+、[^\r\n]+"
    r")",
    re.MULTILINE,
)

# 目标块大小允许的浮动比例：凑边界时允许略超
_SIZE_TOLERANCE = 0.15

# token 计数（tiktoken cl100k_base，与 RAGFlow 一致）：
# 中文约 1.5-2 token/字，英文约 1 token/词
try:  # pragma: no cover
    import tiktoken

    _ENCODER = tiktoken.get_encoding("cl100k_base")
except Exception:  # pragma: no cover - 无 tiktoken 时回退字符估算
    _ENCODER = None


def _token_count(text: str) -> int:
    if _ENCODER is not None:
        try:
            return len(_ENCODER.encode(text))
        except Exception:
            pass
    # 回退估算：中文 1.5 token/字，英文单词 1 token/词
    cjk = len(re.findall(r"[一-鿿　-〿＀-￯]", text))
    rest = len(text) - cjk
    return cjk * 2 + rest // 4 + 1


def split_text(
    doc_id: int,
    text: str,
    *,
    chunk_size: int = 256,
    overlap: int = 50,
) -> list[dict[str, Any]]:
    """按语义边界递归切分文本（chunk_size 单位为 token，见 _token_count）。

    切分优先级：段落（\\n\\n）> 行（\\n）> 句子（。！？；）> 兜底硬切。
    - 优先在句子边界停，不劈开句子；单句超长时才硬切
    - 块尾追加下一个切点的开头 overlap token，保持上下文衔接
    - 表格（连续 | 行）作为不可分割单元，整表/按行组切（保表头）
    - 空块 / 纯空白块丢弃
    """
    units = _extract_faq_units(text)
    if not units:
        return []

    chunks: list[dict[str, Any]] = []
    buffer: list[str] = []
    buffer_tokens = 0
    # 已消费单元数：overlap 取下一个未消费单元的开头
    consumed = 0

    def next_unit_head() -> str:
        nxt = units[consumed] if consumed < len(units) else ""
        # 取 overlap token 对应的字符前缀（英文 1 token/字符，中文 2 token/字符）
        if not nxt:
            return ""
        count = 0
        i = 0
        while i < len(nxt) and count < overlap:
            count += 1 if ord(nxt[i]) < 128 else 2
            i += 1
        return nxt[:i]

    def flush() -> None:
        nonlocal buffer, buffer_tokens
        content = "".join(buffer).strip()
        buffer, buffer_tokens = [], 0
        if not content:
            return
        # overlap 仅对普通文本块追加（表格不追加/不追加到表格）
        if overlap > 0 and not _looks_like_table(content) and not _looks_like_table(units[consumed] if consumed < len(units) else ""):
            content = content + next_unit_head()
        chunks.append(_make_chunk(doc_id, content, len(chunks)))

    for unit in units:
        unit_tokens = _token_count(unit)
        # 表格单元：独立成 chunk；超长时按行组切（保表头）
        if _looks_like_table(unit):
            flush()
            if unit_tokens > chunk_size * (1 + _SIZE_TOLERANCE):
                for piece in _split_long_table(unit, chunk_size):
                    content = piece.strip()
                    if content:
                        chunks.append(_make_chunk(doc_id, content, len(chunks)))
            else:
                buffer.append(unit)
                buffer_tokens = unit_tokens
                flush()
            consumed += 1
            continue
        if _looks_like_faq_unit(unit):
            flush()
            pieces = [unit] if len(unit) <= 500 else _split_long_faq(unit, 500)
            for piece in pieces:
                chunks.append(_make_chunk(doc_id, piece.strip(), len(chunks)))
            consumed += 1
            continue
        # 单单元超长（> 容忍上限）：flush 现有缓冲，单元自身按句/硬切细分
        if unit_tokens > chunk_size * (1 + _SIZE_TOLERANCE):
            flush()
            for piece in _split_long_unit(unit, chunk_size, overlap):
                content = piece.strip()
                if content:
                    chunks.append(_make_chunk(doc_id, content, len(chunks)))
            consumed += 1
            continue

        if buffer_tokens + unit_tokens > chunk_size and buffer:
            flush()
        buffer.append(unit)
        buffer_tokens += unit_tokens
        consumed += 1

    flush()
    return chunks


def _extract_faq_units(text: str) -> list[str]:
    """仅在显式 FAQ 标记后提取完整的编号问答单元。"""
    marker = _FAQ_MARKER_RE.search(text)
    if marker is None:
        return _to_units(text)

    faq_tail = text[marker.end() :]
    section = _FAQ_SECTION_RE.search(faq_tail)
    faq_end = section.start() if section is not None else len(faq_tail)
    faq_region = faq_tail[:faq_end]
    candidates = list(_FAQ_CANDIDATE_RE.finditer(faq_region))
    item_starts: list[re.Match[str]] = []
    for index, candidate in enumerate(candidates):
        end = candidates[index + 1].start() if index + 1 < len(candidates) else len(faq_region)
        if _FAQ_ITEM_RE.match(faq_region[candidate.start() : end]):
            item_starts.append(candidate)
    if not item_starts:
        return _to_units(text)

    units = _to_units(text[: marker.start()])
    units.extend(_to_units(faq_region[: item_starts[0].start()]))
    for index, item_start in enumerate(item_starts):
        end = item_starts[index + 1].start() if index + 1 < len(item_starts) else len(faq_region)
        item = faq_region[item_start.start() : end].strip()
        if item:
            units.append(item)
    units.extend(_to_units(faq_tail[faq_end:]))
    return units


def _looks_like_faq_unit(text: str) -> bool:
    """编号、问号和答案标记齐全时才视为 FAQ 单元。"""
    return _FAQ_ITEM_RE.match(text.strip()) is not None


def _split_long_faq(unit: str, max_chars: int) -> list[str]:
    """按句子装箱长答案，并在每块前重复问题与答案标记。"""
    match = re.match(
        r"(?P<question>\d+\s*[、.．]\s*.*?[？?])\s*答[：:](?P<answer>.*)",
        unit.strip(),
        re.DOTALL,
    )
    if match is None:
        return [unit]

    prefix = f"{match.group('question').strip()}答："
    answer = match.group("answer").strip()
    capacity = max_chars - len(prefix)
    if capacity <= 0:
        return [unit[:max_chars]]

    sentences = [value for value in _SENTENCE_RE.split(answer) if value]
    answer_pieces: list[str] = []
    buffer = ""
    for sentence in sentences:
        while len(sentence) > capacity:
            if buffer:
                answer_pieces.append(buffer)
                buffer = ""
            answer_pieces.append(sentence[:capacity])
            sentence = sentence[capacity:]
        if buffer and len(buffer) + len(sentence) > capacity:
            answer_pieces.append(buffer)
            buffer = ""
        buffer += sentence
    if buffer or not answer_pieces:
        answer_pieces.append(buffer)
    return [prefix + piece for piece in answer_pieces]


def _to_units(text: str) -> list[str]:
    """把文本切成有序的语义单元：表格 > 段落 → 行 → 句子，保序展平。

    Markdown 表格（连续 | 行）作为不可分割单元处理，防止表头/数据行被劈开。
    """
    units: list[str] = []
    for paragraph in _PARAGRAPH_RE.split(text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        # 表格块：2+ 行以 | 开头（表头+分隔行+数据行）
        if _looks_like_table(paragraph):
            units.append(paragraph)
            continue
        for line in _LINE_RE.split(paragraph):
            line = line.strip()
            if not line:
                continue
            # 行内按句子切；无标点则整行作为一个单元
            sentences = [s.strip() for s in _SENTENCE_RE.split(line) if s.strip()]
            if len(sentences) > 1:
                units.extend(sentences)
            else:
                units.append(line)
    return units


def _looks_like_table(text: str) -> bool:
    """连续 | 分隔的行构成表格（表头 + 分隔行 + 至少一行数据）。"""
    lines = [ln for ln in _LINE_RE.split(text) if ln.strip()]
    if len(lines) < 2:
        return False
    pipe_lines = [ln for ln in lines if ln.strip().startswith("|") or ln.strip().endswith("|")]
    return len(pipe_lines) >= 2


def _split_long_unit(unit: str, chunk_size: int, overlap: int) -> list[str]:
    """超长单元：表格按行组切（保表头），其余优先按句子边界细分，仍超长才硬切。"""
    if _looks_like_table(unit):
        return _split_long_table(unit, chunk_size)
    # 句子细分（. 全角/半角 也纳入，兜底英文）
    sentences = [s.strip() for s in re.split(r"(?<=[。！？；!?;])", unit) if s.strip()]
    if len(sentences) == 1:
        # 无句子边界 → 硬切（按 token 数精确切，overlap 衔接）
        pieces: list[str] = []
        start = 0
        total = len(unit)
        while start < total:
            # 找从 start 起累计 chunk_size token 的字符边界
            end = start
            count = 0
            while end < total:
                ch = unit[end]
                count += 1 if ord(ch) < 128 else 2  # 英文 1 token，中文 2 token
                end += 1
                if count >= chunk_size:
                    break
            piece = unit[start:end]
            if end < total and overlap > 0:
                ov_end = end
                ov_count = 0
                while ov_end < total and ov_count < overlap:
                    ch = unit[ov_end]
                    ov_count += 1 if ord(ch) < 128 else 2
                    ov_end += 1
                piece = piece + unit[end:ov_end]
            pieces.append(piece)
            if end >= total:
                break
            start = end
        return pieces
    # 有句子边界：按句子重新走合并逻辑（防单句碎片）
    merged: list[str] = []
    buf = ""
    for s in sentences:
        if buf and _token_count(buf) + _token_count(s) > chunk_size:
            merged.append(buf)
            buf = ""
        buf += s
    if buf:
        merged.append(buf)
    return merged


def _split_long_table(table: str, chunk_size: int) -> list[str]:
    """超大表格：按行切分，每组保留表头+分隔行，不劈开数据行。"""
    lines = [ln for ln in _LINE_RE.split(table) if ln.strip()]
    if len(lines) < 3:
        return [table]
    header = lines[0]
    separator = lines[1] if lines[1].strip().startswith("|") and "-" in lines[1] else None
    data_lines = lines[2:] if separator else lines[1:]

    groups: list[str] = []
    current = [header]
    if separator:
        current.append(separator)
    current_tokens = _token_count(header) + (_token_count(separator) if separator else 0)

    for line in data_lines:
        line_tokens = _token_count(line)
        # 单行超 chunk_size：独立成块（保表头+该行），不劈行内内容
        if line_tokens > chunk_size:
            if len(current) > 2:
                groups.append("\n".join(current))
            current = [header]
            if separator:
                current.append(separator)
            current.append(line)
            groups.append("\n".join(current))
            current = [header]
            if separator:
                current.append(separator)
            current_tokens = _token_count(header) + (_token_count(separator) if separator else 0)
            continue
        if current_tokens + line_tokens > chunk_size and len(current) > 2:
            groups.append("\n".join(current))
            current = [header]
            if separator:
                current.append(separator)
            current_tokens = _token_count(header) + (_token_count(separator) if separator else 0)
        current.append(line)
        current_tokens += line_tokens

    if len(current) > 2:
        groups.append("\n".join(current))
    elif groups:
        # 最后一行不足一组：并入前组
        groups[-1] = groups[-1] + "\n" + "\n".join(current)
    else:
        groups.append("\n".join(current))
    return groups


def _make_chunk(doc_id: int, content: str, index: int) -> dict[str, Any]:
    content_hash = hashlib.sha256(content.encode()).hexdigest()
    return {
        "chunk_id": f"{doc_id}:{index}:{content_hash[:16]}",
        "content": content,
        "index": index,
    }
