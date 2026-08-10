# Bounded Hybrid Retrieval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep FAQ question-answer pairs intact, recover exact Chinese questions with hybrid ranking, and enrich only the two strongest hits with adjacent chunks under strict context limits.

**Architecture:** Ingestion first recognizes FAQ QA units before the existing semantic splitter. Retrieval over-fetches vector candidates and applies a separate lexical/vector rank score while preserving cosine as the public similarity. After global multi-KB truncation, a Chroma-specific context expander adds real-scored adjacent chunks and packs the final prompt context to fixed chunk/token limits.

**Tech Stack:** Python 3.12, FastAPI, ChromaDB cosine collections, SQLAlchemy asyncio, pytest, tiktoken.

## Global Constraints

- `RetrievedChunk.score` and every API `score` remain raw cosine similarity; thresholds continue to compare cosine only.
- Vector candidate count is `max(top_k * 4, 20)`.
- Hybrid score is `0.75 * cosine + 0.25 * lexical`.
- Lexical score is `1.0` for normalized query containment; otherwise use character-bigram Dice similarity after NFKC/lowercase/punctuation removal.
- Multi-KB results are globally sorted by `rank_score` (falling back to `score`) and globally truncated to `top_k`.
- Expand only the first 2 global core hits with same-KB, same-document `index - 1` and `index + 1` chunks.
- Final context contains at most 8 unique chunks and approximately 2,000 tokens.
- Neighbor cosine is computed from the stored neighbor embedding against the real query embedding; neighbors may be below the hit threshold.
- Existing collections without `chunk_index` metadata must fall back to parsing `doc:index:hash[#offset]` IDs.
- No new external dependency, search service, database migration, or frontend behavior change.
- Existing uncommitted application work was checkpointed at `d50307b`; each task commits only its scoped changes on `feat/bounded-hybrid-retrieval`.

---

### Task 1: FAQ question-answer semantic chunks

**Files:**
- Modify: `rag-service/src/ingestion/splitter.py`
- Test: `rag-service/tests/test_ingestion/test_splitter.py`

**Interfaces:**
- Consumes: existing `split_text(doc_id, text, *, chunk_size, overlap) -> list[dict[str, Any]]`.
- Produces: the same return structure, with FAQ `N、问题？答：答案` preserved as one chunk when at most 500 characters.

- [ ] **Step 1: Add failing FAQ regression tests**

```python
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
```

- [ ] **Step 2: Run the focused tests and confirm they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_ingestion/test_splitter.py -q`

Expected: the new FAQ preservation and repeated-prefix assertions fail against the generic splitter.

- [ ] **Step 3: Implement FAQ-tail recognition**

Add `_FAQ_MARKER_RE`, `_FAQ_ITEM_RE`, `_extract_faq_units`, `_looks_like_faq_unit`, and `_split_long_faq`. Only scan text after an explicit `FAQ` marker. A valid item requires a numeric item prefix, a question mark, and `答：`/`答:`. Return the pre-FAQ text to the existing `_to_units` path and append each extracted QA item as an indivisible unit.

In `split_text`, handle FAQ units before generic over-size units:

```python
if _looks_like_faq_unit(unit):
    flush()
    pieces = [unit] if len(unit) <= 500 else _split_long_faq(unit, 500)
    for piece in pieces:
        chunks.append(_make_chunk(doc_id, piece.strip(), len(chunks)))
    consumed += 1
    continue
```

`_split_long_faq` must keep `question + "答："` at the start of every sentence-packed answer piece and never exceed 500 characters.

- [ ] **Step 4: Run splitter tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_ingestion/test_splitter.py -q`

Expected: all splitter tests pass.

- [ ] **Step 5: Commit**

```powershell
git add rag-service/src/ingestion/splitter.py rag-service/tests/test_ingestion/test_splitter.py
git commit -m "feat: preserve FAQ question answer chunks"
```

---

### Task 2: Hybrid candidate ranking without changing similarity semantics

**Files:**
- Create: `rag-service/src/retrieval/ranking.py`
- Modify: `rag-service/src/retrieval/module.py`
- Modify: `rag-service/src/retrieval/chroma.py`
- Test: `rag-service/tests/test_retrieval/test_ranking.py`
- Test: `rag-service/tests/test_retrieval/test_chroma.py`

**Interfaces:**
- Produces: `lexical_score(query: str, content: str) -> float` and `hybrid_score(cosine: float, lexical: float) -> float`.
- Extends: `RetrievedChunk` with `rank_score: float | None = None`, `chunk_index: int | None = None`, and `is_neighbor: bool = False`.
- Produces: `RetrievedChunk.ordering_score` property returning `rank_score` when present, otherwise `score`.

- [ ] **Step 1: Add failing ranking tests**

```python
def test_lexical_score_rewards_normalized_containment() -> None:
    assert lexical_score("半托管商家承责？", "规则。半托管商家承责？答：由商家承担。") == 1.0


def test_lexical_score_uses_bigram_dice_for_partial_overlap() -> None:
    score = lexical_score("包装破损责任", "商品包装破损由商家承担")
    assert 0.0 < score < 1.0


def test_hybrid_score_uses_fixed_weights() -> None:
    assert hybrid_score(0.2, 1.0) == pytest.approx(0.4)
```

Add a Chroma test with 20 candidates where the exact question candidate has cosine `0.23` and starts at vector rank 10. Assert `collection.query` receives `n_results=20`, the exact candidate enters the returned top 5, `candidate.score == 0.23`, and `candidate.rank_score == pytest.approx(0.4225)`.

- [ ] **Step 2: Run focused retrieval tests and confirm failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_retrieval/test_ranking.py tests/test_retrieval/test_chroma.py -q`

Expected: imports/fields fail before implementation.

- [ ] **Step 3: Implement isolated lexical and hybrid scoring**

Normalize with `unicodedata.normalize("NFKC", text).lower()` and retain only `str.isalnum()` characters. Return `0.0` for an empty normalized query/content. Return `1.0` when query is a substring. Otherwise compute set-based character-bigram Dice `2 * |A∩B| / (|A|+|B|)`; single-character inputs use exact equality.

- [ ] **Step 4: Over-fetch and rerank Chroma candidates**

In `retrieve`, use `candidate_k = max(top_k * 4, 20)` for `collection.query`. Convert chunks, attach KB data, filter by raw cosine threshold, assign `rank_score=hybrid_score(c.score, lexical_score(query, c.content))`, sort by `ordering_score` descending, and return `[:top_k]`.

Update `retrieve_multi` to stable-sort by `ordering_score`, not raw `score`.

- [ ] **Step 5: Run retrieval tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_retrieval/test_ranking.py tests/test_retrieval/test_chroma.py -q`

Expected: all focused tests pass, including existing threshold and multi-KB tests.

- [ ] **Step 6: Commit**

```powershell
git add rag-service/src/retrieval/ranking.py rag-service/src/retrieval/module.py rag-service/src/retrieval/chroma.py rag-service/tests/test_retrieval/test_ranking.py rag-service/tests/test_retrieval/test_chroma.py
git commit -m "feat: rerank retrieval with lexical signals"
```

---

### Task 3: Persist chunk indices and add bounded adjacent context

**Files:**
- Create: `rag-service/src/retrieval/context.py`
- Modify: `rag-service/src/ingestion/worker.py`
- Modify: `rag-service/src/retrieval/chroma.py`
- Modify: `rag-service/src/retrieval/module.py`
- Test: `rag-service/tests/test_ingestion/test_worker.py`
- Test: `rag-service/tests/test_retrieval/test_context.py`
- Test: `rag-service/tests/test_retrieval/test_chroma.py`

**Interfaces:**
- Produces: `pack_context(chunks: list[RetrievedChunk], *, max_chunks: int = 8, max_tokens: int = 2000) -> list[RetrievedChunk]`.
- Produces: `RetrievalModule.expand_context(query, owner_user_id, chunks, *, seed_count=2, max_chunks=8, max_tokens=2000) -> list[RetrievedChunk]`.
- Chroma override loads adjacent chunks and returns packed windows.

- [ ] **Step 1: Add failing context-packing and metadata tests**

Test that `pack_context` preserves order, removes duplicate `chunk_id`s, stops at 8 chunks, and refuses a chunk that would cross 2,000 tokens after at least one chunk has been accepted.

Extend the worker ingestion test to assert every Chroma metadata payload contains the splitter's numeric `chunk_index`.

Add a Chroma expansion test with core chunks at `(doc 10, index 3)`, `(doc 20, index 7)`, and three lower-ranked cores. Fake `collection.get` responses must include indices `2/3/4` and `6/7/8`. Assert only the first two seeds expand, IDs are deduplicated, document boundaries are respected, and neighbor `score` equals cosine computed from its stored embedding.

- [ ] **Step 2: Run focused tests and confirm failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_retrieval/test_context.py tests/test_retrieval/test_chroma.py tests/test_ingestion/test_worker.py -q`

Expected: missing packer/expander and missing `chunk_index` assertions fail.

- [ ] **Step 3: Carry chunk indices through ingestion**

Preserve `c["index"]` in every `embed_items` entry. For hard-split `#offset` pieces, keep the same semantic `chunk_index`. Add `chunk_index` to `upsert_chunks` metadata.

- [ ] **Step 4: Implement context packing**

Use tiktoken `cl100k_base` when available and the existing Chinese/ASCII estimate as fallback. Deduplicate by `chunk_id`. Always allow the first non-empty chunk; subsequent chunks must satisfy both limits.

The base `RetrievalModule.expand_context` returns `pack_context(chunks, ...)`, preserving compatibility for fakes.

- [ ] **Step 5: Implement Chroma neighbor expansion**

For the first two global seeds, authorize and resolve each `kb_id`, then call `collection.get(where={"doc_id": seed.doc_id}, include=["documents", "metadatas", "embeddings"])`. Resolve index from `metadata["chunk_index"]`, falling back to the second colon-separated chunk-ID component. Select only `seed_index - 1`, `seed_index`, and `seed_index + 1` in document order. Compute actual neighbor cosine against one freshly generated query embedding, set `is_neighbor=True` only for non-core IDs, and retain core `rank_score` ordering. On any neighbor failure, log a warning and pack the original cores.

- [ ] **Step 6: Run focused tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_retrieval/test_context.py tests/test_retrieval/test_chroma.py tests/test_ingestion/test_worker.py -q`

Expected: all focused tests pass.

- [ ] **Step 7: Commit**

```powershell
git add rag-service/src/retrieval/context.py rag-service/src/retrieval/module.py rag-service/src/retrieval/chroma.py rag-service/src/ingestion/worker.py rag-service/tests/test_retrieval/test_context.py rag-service/tests/test_retrieval/test_chroma.py rag-service/tests/test_ingestion/test_worker.py
git commit -m "feat: add bounded adjacent retrieval context"
```

---

### Task 4: Use expanded context in QueryEngine and references

**Files:**
- Modify: `rag-service/src/engine/query.py`
- Modify: `rag-service/src/api/router_chat.py`
- Modify: `rag-service/tests/test_engine/test_query.py`
- Modify: `rag-service/tests/test_retrieval/fakes.py`
- Modify: `rag-service/tests/test_api/test_chat.py`
- Modify: `web/src/api/chat.ts`

**Interfaces:**
- Consumes: `RetrievalModule.expand_context(...)` from Task 3.
- Produces: SSE reference items with optional `is_neighbor: bool`; raw `score` remains cosine.

- [ ] **Step 1: Add failing QueryEngine expansion tests**

Configure `FakeRetrieval` with five core chunks and two adjacent chunks. Assert `retrieve_multi` receives the full KB list, `expand_context` receives the user question and only globally truncated cores, `build_messages` contains the expanded chunks, persisted `Reference` rows equal the expanded context, `QueryLog.chunks_count` equals expanded length, and SSE reference items mark adjacent chunks with `is_neighbor: true`.

Add an API regression proving an empty core retrieval still returns `empty_response` without invoking expansion.

- [ ] **Step 2: Run focused engine/API tests and confirm failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_engine/test_query.py tests/test_api/test_chat.py -q`

Expected: expansion call and `is_neighbor` assertions fail.

- [ ] **Step 3: Integrate expansion after global retrieval**

Use separate variables:

```python
core_sources = await self._retrieval.retrieve_multi(...)
if not core_sources:
    # existing empty response
sources = await self._retrieval.expand_context(question, user_id, core_sources)
```

Build the prompt, persist references, and set `chunks_count` from `sources`. Include `"is_neighbor": s.is_neighbor` in immediate SSE items. Historical reference responses continue to default to `false` because no database column is added.

Add `"is_neighbor": False` to references serialized by the historical `get_messages` endpoint.

- [ ] **Step 4: Update fakes and frontend type**

Let `FakeRetrieval` expose `expanded_chunks`, `last_expand_query`, and `last_expand_core_ids`; its override returns configured expansion or delegates to the base packer. Add `is_neighbor?: boolean` to `ReferenceInfo` in `web/src/api/chat.ts`.

- [ ] **Step 5: Run focused tests and frontend type/build checks**

Run: `.venv/Scripts/python.exe -m pytest tests/test_engine/test_query.py tests/test_api/test_chat.py -q`

Run: `npx vue-tsc --noEmit && npm run build` from `web/`.

Expected: tests, type check, and build all pass.

- [ ] **Step 6: Commit**

```powershell
git add rag-service/src/engine/query.py rag-service/src/api/router_chat.py rag-service/tests/test_engine/test_query.py rag-service/tests/test_retrieval/fakes.py rag-service/tests/test_api/test_chat.py web/src/api/chat.ts
git commit -m "feat: feed bounded expanded context to answers"
```

---

### Task 5: Full verification and real FAQ regression

**Files:**
- Verify only; no production changes unless a failing regression identifies a scoped defect.

- [ ] **Step 1: Run the complete backend suite**

Run: `.venv/Scripts/python.exe -m pytest -q`

Expected: at least the 95 baseline tests plus all new tests pass with zero failures.

- [ ] **Step 2: Run frontend verification**

Run: `npx vue-tsc --noEmit`

Run: `npm run build`

Expected: both exit 0; existing bundle-size warnings are allowed.

- [ ] **Step 3: Reproduce the FAQ split without mutating production data**

Read document 558's retained source file, call the new `split_text` in a read-only script, and assert one generated chunk contains both `半托管商家承责的纠纷范围发生了哪些变化？` and `半托管JIT纠纷订单将被判定为商家包装问题`.

- [ ] **Step 4: Verify hybrid ranking against the existing collection without writing it**

Run a read-only retrieval for `半托管商家承责的纠纷范围发生了哪些变化？` against the current `kb_1_v1`. Assert chunk `558:23:*` enters top 5 through lexical reranking while its public `score` remains approximately `0.23`.

- [ ] **Step 5: Review branch diff and document reindex requirement**

Run: `git diff --check backup/pre-bounded-hybrid-retrieval-20260810..HEAD`

Run: `git status --short --branch`

Expected: no whitespace errors and no uncommitted implementation files. Report that knowledge base 1 must be explicitly reindexed before the new FAQ chunks replace old chunks.
