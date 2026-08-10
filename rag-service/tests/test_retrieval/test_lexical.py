from __future__ import annotations

from pathlib import Path

from src.retrieval.fts import FtsIndex, StagedChunk
from src.retrieval.lexical import analyze_lexical, build_match_query


def test_analyze_lexical_keeps_identifiers_and_cjk_ngrams() -> None:
    tokens = analyze_lexical("SKU-AB12，9610免税").split()
    assert "sku-ab12" in tokens
    assert "9610" in tokens
    assert {"免", "免税", "税"}.issubset(tokens)


def test_build_match_query_escapes_operators_without_quotes() -> None:
    query = build_match_query('站内 "付费" 第五讲')
    assert '"' not in query
    assert "站" in query
    assert "付费" in query
    assert "第五" in query


def test_analyze_lexical_nfkc_and_lowercase() -> None:
    tokens = analyze_lexical("ＡＢＣ ｃｄｅ ＦＵＬＬ").split()
    assert "abc" in tokens
    assert "cde" in tokens
    assert "full" in tokens


def test_analyze_lexical_deduplicates_with_stable_order() -> None:
    tokens = analyze_lexical("免税免税免税").split()
    # 每个 unigram 和相邻 bigram 各只出现一次，顺序与文本一致；
    # 免税/免税 交界处产生 bigram 税免
    assert tokens == ["免", "免税", "税", "税免"]


def test_analyze_lexical_emits_all_adjacent_bigrams() -> None:
    tokens = analyze_lexical("免税清单").split()
    assert {"免", "税", "清", "单", "免税", "税清", "清单"}.issubset(tokens)


def test_analyze_lexical_keeps_alphanumeric_mixed_runs() -> None:
    tokens = analyze_lexical("2026年春节").split()
    assert "2026" in tokens
    assert "年" in tokens
    assert "春节" in tokens


def test_analyze_lexical_drops_trailing_dash() -> None:
    tokens = analyze_lexical("sku-ab-").split()
    assert "sku-ab" in tokens


def test_build_match_query_splits_hyphen_identifiers() -> None:
    query = build_match_query("SKU-AB12 9610")
    assert "sku ab12" in query
    assert "9610" in query


def test_bm25_returns_exact_identifier_before_similar_prose(tmp_path: Path) -> None:
    index = FtsIndex(tmp_path / "retrieval.db")
    index.initialize()
    index.stage_chunks(
        [
            StagedChunk(
                chunk_id="exact",
                title="",
                keywords="",
                content=analyze_lexical("9610免税"),
                collection_id="col1",
                ingest_run_id="run1",
            ),
            StagedChunk(
                chunk_id="prose",
                title="",
                keywords="",
                content=analyze_lexical("免税政策解读"),
                collection_id="col1",
                ingest_run_id="run1",
            ),
            StagedChunk(
                chunk_id="unrelated",
                title="",
                keywords="",
                content=analyze_lexical("苹果香蕉"),
                collection_id="col1",
                ingest_run_id="run1",
            ),
        ]
    )
    index.activate_run("col1", "run1")

    hits = index.search(build_match_query("9610免税"), "col1", limit=5)
    assert [hit.chunk_id for hit in hits] == ["exact", "prose"]
    assert hits[0].lexical_rank == 1


def test_bm25_weights_title_above_keywords_above_content(tmp_path: Path) -> None:
    index = FtsIndex(tmp_path / "retrieval.db")
    index.initialize()
    index.stage_chunks(
        [
            StagedChunk(
                chunk_id="title-hit",
                title=analyze_lexical("免税"),
                keywords="",
                content="",
                collection_id="col1",
                ingest_run_id="run1",
            ),
            StagedChunk(
                chunk_id="keyword-hit",
                title="",
                keywords=analyze_lexical("免税"),
                content="",
                collection_id="col1",
                ingest_run_id="run1",
            ),
            StagedChunk(
                chunk_id="content-hit",
                title="",
                keywords="",
                content=analyze_lexical("免税"),
                collection_id="col1",
                ingest_run_id="run1",
            ),
        ]
    )
    index.activate_run("col1", "run1")

    hits = index.search("免税", "col1", limit=5)
    assert [hit.chunk_id for hit in hits] == ["title-hit", "keyword-hit", "content-hit"]
    assert hits[0].lexical_rank == 1
    assert hits[2].lexical_rank == 3
