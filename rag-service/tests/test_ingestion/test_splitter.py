from __future__ import annotations

from src.ingestion.splitter import split_text


def test_chunk_ids_are_stable() -> None:
    first = split_text(7, "alpha beta", chunk_size=5, overlap=2)
    second = split_text(7, "alpha beta", chunk_size=5, overlap=2)
    assert first and second
    assert first[0]["chunk_id"] == second[0]["chunk_id"]


def test_does_not_split_sentences_when_fitting() -> None:
    """句号结尾的短文本不应被劈开（整句成一个 chunk）。"""
    text = "这是第一句。这是第二句！这是第三句？"
    chunks = split_text(9, text, chunk_size=512, overlap=0)
    assert len(chunks) == 1
    assert chunks[0]["content"].startswith("这是第一句。")


def test_splits_at_sentence_boundary() -> None:
    """多句超过 chunk_size 时在句号处断开，不劈句子。"""
    text = "第一句内容。" + "第二句内容。" * 20
    chunks = split_text(9, text, chunk_size=30, overlap=0)
    assert len(chunks) > 1
    for c in chunks:
        content = c["content"]
        # 每个块由完整句子拼接（不含半个句子）
        assert content.endswith("。")


def test_split_respects_paragraph_boundary() -> None:
    """段落边界优先：段落之间不混切。"""
    text = "第一段。" * 10 + "\n\n" + "第二段。" * 10
    chunks = split_text(9, text, chunk_size=50, overlap=0)
    joined = "".join(c["content"] for c in chunks)
    assert "第一段。" in joined and "第二段。" in joined


def test_overlap_appends_next_unit_head() -> None:
    """overlap 在块尾追加下一单元开头，保持上下文衔接。"""
    text = "第一句。第二句。第三句。"
    chunks = split_text(9, text, chunk_size=8, overlap=3)
    assert len(chunks) >= 2
    # 相邻 chunk 有内容重叠（上下文衔接）
    assert "第二" in chunks[0]["content"] and "第二" in chunks[1]["content"]


def test_long_sentence_falls_back_to_hard_split() -> None:
    """无标点超长文本仍能切分（兜底硬切）。"""
    text = "无标点" * 500
    chunks = split_text(9, text, chunk_size=50, overlap=5)
    assert len(chunks) > 1
    # 每块 token 数受 chunk_size + overlap 约束（中文约 2 token/字）
    for c in chunks:
        assert len(c["content"]) <= 50 * 2 + 5 * 2 + 10


def test_blank_text_returns_empty() -> None:
    assert split_text(9, "   \n\n  ", chunk_size=512, overlap=0) == []


def test_md_table_stays_whole_when_fitting() -> None:
    """小表格整表一个 chunk，表头+分隔行+数据行完整。"""
    md = "| 项目 | 预算 |\n|------|------|\n| 开发 | 50 |\n| 运维 | 20 |"
    chunks = split_text(9, md, chunk_size=512, overlap=0)
    assert len(chunks) == 1
    content = chunks[0]["content"]
    assert content.startswith("| 项目 | 预算 |")
    assert "| 开发 | 50 |" in content
    assert "| 运维 | 20 |" in content


def test_md_table_split_keeps_header_per_group() -> None:
    """大表格按行切分时，每个 chunk 都保留表头+分隔行。"""
    md = "| 项目 | 金额 |\n|------|------|\n" + "".join(f"| 项目{i} | {i}0 |\n" for i in range(20))
    chunks = split_text(9, md, chunk_size=60, overlap=0)
    assert len(chunks) > 1
    for c in chunks:
        lines = c["content"].split("\n")
        assert lines[0].startswith("| 项目 |")
        assert "------" in lines[1]
        # 数据行不被劈开
        assert all(ln.endswith("|") for ln in lines[2:] if ln.strip())


def test_md_table_not_polluted_by_overlap() -> None:
    """表格 chunk 不追加下一单元开头（避免污染表格结构）。"""
    md = "| 项目 | 预算 |\n|------|------|\n| 开发 | 50 |\n\n后续正文。"
    chunks = split_text(9, md, chunk_size=200, overlap=10)
    table_chunk = next(c for c in chunks if c["content"].startswith("| 项目 |"))
    # 表格 chunk 不应含正文内容
    assert "后续正文" not in table_chunk["content"]
    assert table_chunk["content"].endswith("| 开发 | 50 |")
