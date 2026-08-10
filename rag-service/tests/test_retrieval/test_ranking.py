from __future__ import annotations

import pytest

from src.retrieval.module import RetrievedChunk
from src.retrieval.ranking import hybrid_score, lexical_score


def test_lexical_score_rewards_normalized_containment() -> None:
    assert lexical_score("半托管商家承责？", "规则。半托管商家承责？答：由商家承担。") == 1.0


def test_lexical_score_uses_bigram_dice_for_partial_overlap() -> None:
    score = lexical_score("包装破损责任", "商品包装破损由商家承担")
    assert 0.0 < score < 1.0


def test_hybrid_score_uses_fixed_weights() -> None:
    assert hybrid_score(0.2, 1.0) == pytest.approx(0.4)


def test_retrieved_chunk_orders_by_rank_score_with_score_fallback() -> None:
    base = RetrievedChunk("c1", "content", 1, "title", None, 0.3)
    reranked = RetrievedChunk("c2", "content", 1, "title", None, 0.3, rank_score=0.7)

    assert base.ordering_score == 0.3
    assert reranked.ordering_score == 0.7
