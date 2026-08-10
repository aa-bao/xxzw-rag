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


def test_faq_question_and_answer_stay_in_one_chunk() -> None:
    text = (
        "4、商品标签标识违规：违反规则将处罚。5、其他违规按规则处罚。"
        "# 九、FAQ"
        "1、半托管商家承责的纠纷范围发生了哪些变化？"
        "答：针对JIT模式履约的订单，破损问题由商家承担。"
        "2、如何减少破损问题的产生？答：请改善销售包装。"
    )
    chunks = split_text(558, text, chunk_size=64, overlap=0)
    contents = [chunk["content"] for chunk in chunks]
    assert any("1、半托管商家承责" in value and "破损问题由商家承担" in value for value in contents)
    assert not any(value.endswith("发生了哪些变化？") for value in contents)


def test_numbered_rules_without_faq_marker_are_not_qa_units() -> None:
    chunks = split_text(1, "1、规则一。2、规则二。答：补充说明。", chunk_size=20, overlap=0)
    assert all("规则一。2、规则二。答" not in chunk["content"] for chunk in chunks)


def test_long_faq_repeats_question_prefix() -> None:
    question = "1、很长的答案如何处理？"
    chunks = split_text(1, f"# FAQ{question}答：{'答案内容。' * 150}", chunk_size=256, overlap=0)
    faq_chunks = [chunk["content"] for chunk in chunks if question in chunk["content"]]
    assert len(faq_chunks) >= 2
    assert all(value.startswith(question) for value in faq_chunks)
    assert all(len(value) <= 500 for value in faq_chunks)


def test_faq_word_in_ordinary_sentence_does_not_enable_qa_units() -> None:
    text = "正文提到FAQ功能。1、这是什么？答：普通说明。"
    chunks = split_text(1, text, chunk_size=512, overlap=0)
    assert "".join(chunk["content"] for chunk in chunks) == text


def test_text_between_faq_marker_and_first_item_is_preserved() -> None:
    text = "# FAQ\n以下内容来自客服整理。\n1、如何处理？答：按流程处理。"
    chunks = split_text(1, text, chunk_size=512, overlap=0)
    contents = [chunk["content"] for chunk in chunks]
    assert any("以下内容来自客服整理。" in value for value in contents)
    assert any("1、如何处理？答：按流程处理。" in value for value in contents)


def test_faq_marker_without_valid_item_falls_back_to_normal_splitting() -> None:
    text = "# FAQ\n这是说明，没有编号问答。"
    chunks = split_text(1, text, chunk_size=512, overlap=0)
    assert "".join(chunk["content"] for chunk in chunks) == "# FAQ这是说明，没有编号问答。"


def test_faq_stops_before_following_markdown_section() -> None:
    text = "# FAQ\n1、如何处理？答：按流程处理。\n# 后续章节\n后续正文。"
    chunks = split_text(1, text, chunk_size=512, overlap=0)
    contents = [chunk["content"] for chunk in chunks]
    faq_chunk = next(value for value in contents if value.startswith("1、如何处理？"))
    assert "后续章节" not in faq_chunk
    assert any("# 后续章节" in value and "后续正文。" in value for value in contents)


def test_numbered_answer_steps_do_not_start_a_new_faq_item() -> None:
    text = (
        "# FAQ"
        "1、如何操作？答：步骤如下：1、先准备。2、再提交。"
        "2、如何撤销？答：联系管理员。"
    )
    chunks = split_text(1, text, chunk_size=64, overlap=0)
    contents = [chunk["content"] for chunk in chunks]
    first = next(value for value in contents if value.startswith("1、如何操作？"))
    second = next(value for value in contents if value.startswith("2、如何撤销？"))
    assert "1、先准备。2、再提交。" in first
    assert "如何撤销" not in first
    assert second == "2、如何撤销？答：联系管理员。"


def test_faq_followed_by_version_digits_is_not_a_marker() -> None:
    text = "FAQ2024版本说明。1、这是什么功能？答：这是普通正文中的问答说明。"
    chunks = split_text(1, text, chunk_size=20, overlap=0)
    contents = [chunk["content"] for chunk in chunks]
    assert any("FAQ2024版本说明。" in value for value in contents)
    assert not any("1、这是什么功能？" in value and "普通正文中的问答说明" in value for value in contents)


def test_faq_stops_before_following_numbered_section() -> None:
    text = "# FAQ\n1、如何处理？答：按流程处理。\n十、附则\n附则正文。"
    chunks = split_text(1, text, chunk_size=512, overlap=0)
    contents = [chunk["content"] for chunk in chunks]
    faq_chunk = next(value for value in contents if value.startswith("1、如何处理？"))
    assert "附则" not in faq_chunk
    assert any("十、附则" in value and "附则正文。" in value for value in contents)
