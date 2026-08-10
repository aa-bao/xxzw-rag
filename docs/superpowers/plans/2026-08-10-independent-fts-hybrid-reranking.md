# Independent FTS Hybrid Retrieval and Reranking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add independent SQLite FTS5/BM25 retrieval, explicit metadata filtering, RRF fusion, and optional cloud reranking while retaining Chroma.

**Architecture:** Chroma and SQLite are independent recall lanes over the same stable chunk IDs and ingest runs. A `HybridRetrievalModule` authorizes the knowledge base, executes both lanes, fuses ranks, optionally reranks the top 30, then expands bounded parent/neighbor context. MySQL's active run manifest is the final visibility gate across both external indexes.

**Tech Stack:** Python 3.12, SQLite FTS5, ChromaDB, SQLAlchemy asyncio/MySQL, httpx, FastAPI, pytest, Docker Compose.

## Global Constraints

- Do not introduce Elasticsearch or another service.
- Dense and FTS lanes each retrieve Top 50 by default.
- FTS weights are title 5, keywords 3, content 1.
- Lexical normalization is NFKC + lowercase; preserve identifiers, numbers, and alphanumerics; Chinese emits unigram and bigram tokens.
- RRF uses `rank_constant=60` and passes at most 30 candidates to reranking.
- Dense similarity threshold applies only to dense candidates.
- `RetrievedChunk.score` remains raw cosine and becomes nullable for FTS-only results.
- Reranker failure, timeout, rate limit, or malformed output falls back to RRF ordering.
- Embedding and reranker provider/model/dimension are independent.
- Final context remains at most 8 chunks or approximately 2000 tokens.
- Complete `2026-08-10-structured-json-ingestion-backend.md` first.

---

### Task 1: SQLite index configuration and schema

**Files:**
- Modify: `rag-service/src/shared/config.py`
- Modify: `rag-service/config.yaml`
- Modify: `docker-compose.yml`
- Create: `rag-service/src/retrieval/fts.py`
- Create: `rag-service/tests/test_retrieval/test_fts.py`
- Modify: `rag-service/tests/test_shared_config.py`

**Interfaces:**
- Produces: `LexicalSettings(path: Path, dense_top_k=50, lexical_top_k=50, rrf_rank_constant=60)`.
- Produces: `FtsIndex.initialize()`, `stage_chunks`, `activate_run`, `delete_run`, `verify_run`, and `search`.

- [ ] **Step 1: Add failing configuration tests**

Assert the example config loads an FTS path and fixed defaults, rejects nonpositive lane sizes, and rejects `rrf_rank_constant < 1`.

```yaml
lexical:
  sqlite_path: ./data/fts/retrieval.db
  dense_top_k: 50
  lexical_top_k: 50
  rrf_rank_constant: 60
```

- [ ] **Step 2: Add SQLite schema tests**

Open a temporary database, call `initialize`, and assert tables `fts_chunks`, `chunk_meta`, and `chunk_filter` exist. Verify WAL mode, foreign keys, and indexes on `(collection_id, ingest_run_id, index_state)` and `(collection_id, field_name, normalized_value)`.

- [ ] **Step 3: Implement FtsIndex lifecycle**

Use one short-lived `sqlite3.Connection` per blocking operation, executed by callers through `asyncio.to_thread`. Initialize with:

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS fts_chunks USING fts5(
  chunk_id UNINDEXED, title, keywords, content,
  tokenize='unicode61 remove_diacritics 2'
);
```

Keep run and metadata fields in ordinary `chunk_meta`; use rowid linkage to the FTS table. Wrap each staging batch in one transaction. `verify_run` compares exact chunk-ID set, count, and aggregate content hash.

- [ ] **Step 4: Add persistent volume wiring**

Mount `./data/fts` at the configured service path. Do not add a container. Ensure the service user can create the SQLite file and WAL/SHM siblings.

- [ ] **Step 5: Run and commit**

Run: `.venv/Scripts/python.exe -m pytest tests/test_retrieval/test_fts.py tests/test_shared_config.py -q`

Expected: all focused tests pass.

```powershell
git add rag-service/src/shared/config.py rag-service/config.yaml docker-compose.yml rag-service/src/retrieval/fts.py rag-service/tests/test_retrieval/test_fts.py rag-service/tests/test_shared_config.py
git commit -m "feat: add persistent FTS index store"
```

---

### Task 2: Chinese lexical analysis and BM25 lane

**Files:**
- Create: `rag-service/src/retrieval/lexical.py`
- Modify: `rag-service/src/retrieval/fts.py`
- Create: `rag-service/tests/test_retrieval/test_lexical.py`
- Modify: `rag-service/tests/test_retrieval/test_fts.py`

**Interfaces:**
- Produces: `analyze_lexical(text: str) -> str` for indexed token strings.
- Produces: `build_match_query(text: str) -> str` with escaped FTS terms.
- Produces: `FtsHit(chunk_id, lexical_score, lexical_rank, metadata)`.

- [ ] **Step 1: Add tokenizer tests**

```python
tokens = analyze_lexical("SKU-AB12，9610免税").split()
assert "sku-ab12" in tokens
assert "9610" in tokens
assert {"免", "免税", "税"}.issubset(tokens)
assert '"' not in build_match_query('站内 "付费" 第五讲')
```

The exact output may contain deduplicated terms only once, but term order must be stable and both Chinese unigram/bigram plus complete identifiers must be present.

- [ ] **Step 2: Implement deterministic lexical analysis**

Normalize with NFKC and lowercase. Split Latin/number identifier runs without discarding the complete run. For every contiguous CJK run emit each character and adjacent two-character term. Escape FTS operators by quoting each produced token.

- [ ] **Step 3: Add ranking tests**

Index three chunks where only one contains exact `9610` and another is semantically similar prose. Assert FTS returns the exact identifier first. Assert title beats keyword, and keyword beats content when the same term frequency is used.

- [ ] **Step 4: Implement weighted BM25 search**

Query only rows whose metadata matches active collection/run and explicit filter candidates. Convert SQLite's lower-is-better BM25 to a higher-is-better diagnostic score while retaining rank:

```sql
SELECT m.chunk_id, bm25(fts_chunks, 0.0, 5.0, 3.0, 1.0) AS distance
FROM fts_chunks
JOIN chunk_meta m ON m.rowid = fts_chunks.rowid
WHERE fts_chunks MATCH ? AND m.collection_id = ?
ORDER BY distance ASC
LIMIT ?
```

Set `lexical_score = -distance`; fusion uses rank, not this raw value.

- [ ] **Step 5: Run and commit**

Run: `.venv/Scripts/python.exe -m pytest tests/test_retrieval/test_lexical.py tests/test_retrieval/test_fts.py -q`

Expected: all focused tests pass.

```powershell
git add rag-service/src/retrieval/lexical.py rag-service/src/retrieval/fts.py rag-service/tests/test_retrieval/test_lexical.py rag-service/tests/test_retrieval/test_fts.py
git commit -m "feat: add Chinese BM25 retrieval lane"
```

---

### Task 3: Explicit dynamic metadata filtering

**Files:**
- Create: `rag-service/src/retrieval/filters.py`
- Modify: `rag-service/src/retrieval/fts.py`
- Create: `rag-service/tests/test_retrieval/test_filters.py`
- Modify: `rag-service/tests/test_retrieval/test_fts.py`

**Interfaces:**
- Produces: `SearchFilter(field: str, op: Literal["eq", "in", "gte", "lte"], value: JsonScalar | list[JsonScalar])`.
- Produces: `SearchQuery(text: str, filters: tuple[SearchFilter, ...] = ())`.
- Produces: `normalize_filter_value(value) -> tuple[value_type, normalized_value]`.
- Produces: `FtsIndex.filter_chunk_ids(collection_id, active_run_ids, filters) -> set[str] | None`.

- [ ] **Step 1: Add filter validation tests**

Reject empty field names, nested objects, lists for non-`in`, and more than 20 filters. Assert Unicode strings normalize with NFKC, booleans remain distinct from integers, datetimes normalize to UTC ISO-8601, and numbers have canonical decimal strings.

- [ ] **Step 2: Implement typed normalization**

Store `value_type` as `string|number|boolean|datetime|null`. Equality and membership compare canonical values; range operators are allowed only for number/datetime and use typed columns rather than lexicographic text.

- [ ] **Step 3: Add multi-filter intersection tests**

Index chunks with `category`, `author`, and `created_at`. Assert filters are ANDed across fields and `in` is ORed within one field. Verify a user cannot inject SQL through field/value text because all values are bound parameters.

- [ ] **Step 4: Implement EAV filtering**

Insert one `chunk_filter` row per mapped filter. Build parameterized subqueries per filter and intersect `chunk_id`s. Return `None` for no filters and an empty set for no matches; FTS and dense lanes must short-circuit on an empty set.

- [ ] **Step 5: Run and commit**

Run: `.venv/Scripts/python.exe -m pytest tests/test_retrieval/test_filters.py tests/test_retrieval/test_fts.py -q`

Expected: all focused tests pass.

```powershell
git add rag-service/src/retrieval/filters.py rag-service/src/retrieval/fts.py rag-service/tests/test_retrieval/test_filters.py rag-service/tests/test_retrieval/test_fts.py
git commit -m "feat: filter structured retrieval metadata"
```

---

### Task 4: Independent dense lane and RRF fusion

**Files:**
- Create: `rag-service/src/retrieval/fusion.py`
- Create: `rag-service/src/retrieval/hybrid.py`
- Modify: `rag-service/src/retrieval/module.py`
- Modify: `rag-service/src/retrieval/chroma.py`
- Create: `rag-service/tests/test_retrieval/test_fusion.py`
- Create: `rag-service/tests/test_retrieval/test_hybrid.py`
- Modify: `rag-service/tests/test_retrieval/test_chroma.py`

**Interfaces:**
- Produces: `reciprocal_rank_fusion(lanes, *, rank_constant=60) -> list[RetrievedChunk]`.
- Produces: `HybridRetrievalModule.retrieve_query(query, owner_user_id, kb_id, top_k, similarity_threshold) -> list[RetrievedChunk]`.
- Extends: `RetrievedChunk` with nullable `score`, dense/lexical ranks/scores, `rrf_score`, `rerank_score`, and `retrieval_sources`.

- [ ] **Step 1: Add RRF unit tests**

```python
fused = reciprocal_rank_fusion({"dense": [a, b], "lexical": [b, c]}, rank_constant=60)
assert fused[0].chunk_id == b.chunk_id
assert fused[0].rrf_score == pytest.approx(1 / 62 + 1 / 61)
assert fused[0].retrieval_sources == ("dense", "lexical")
```

Assert a lexical-only chunk survives and has `score is None`.

- [ ] **Step 2: Separate Chroma dense retrieval**

Remove candidate-only `lexical_score` and `hybrid_score` from `ChromaRetrieval.retrieve`. Add `retrieve_dense(SearchQuery, ..., candidate_k=50, allowed_chunk_ids=None)` that applies cosine threshold only there and records `dense_rank`/`dense_score`.

Every Chroma query must constrain `index_state="active"`; returned candidates are discarded unless their `ingest_run_id` equals the authoritative active run loaded from MySQL.

- [ ] **Step 3: Implement pure RRF fusion**

Deduplicate by `(kb_id, chunk_id)`. Sum `1/(k+rank)` for every lane, preserve raw diagnostic scores, and use stable tie-breakers: RRF descending, best lane rank ascending, chunk ID ascending.

- [ ] **Step 4: Implement HybridRetrievalModule**

Authorize the KB once and load its active document run IDs. Execute dense and FTS lanes concurrently with `asyncio.gather`; one lane failing logs a warning and allows the other lane to continue. Apply explicit filters before or inside both lanes. Fuse and return the first `top_k` when reranking is disabled.

- [ ] **Step 5: Add compatibility wrapper**

Keep `RetrievalModule.retrieve(query: str, ...)` by wrapping the string in `SearchQuery(text=query)`. Update `retrieve_multi` to merge on `ordering_score`, where ordering is rerank score, then RRF, then dense score.

- [ ] **Step 6: Run and commit**

Run: `.venv/Scripts/python.exe -m pytest tests/test_retrieval/test_fusion.py tests/test_retrieval/test_hybrid.py tests/test_retrieval/test_chroma.py -q`

Expected: all focused tests pass, including a lexical-only exact-term recovery case.

```powershell
git add rag-service/src/retrieval/fusion.py rag-service/src/retrieval/hybrid.py rag-service/src/retrieval/module.py rag-service/src/retrieval/chroma.py rag-service/tests/test_retrieval/test_fusion.py rag-service/tests/test_retrieval/test_hybrid.py rag-service/tests/test_retrieval/test_chroma.py
git commit -m "feat: fuse independent dense and FTS recall"
```

---

### Task 5: Cross-index staging and activation

**Files:**
- Create: `rag-service/src/db/migrations/versions/0006_hybrid_retrieval_metadata.py`
- Modify: `rag-service/src/ingestion/worker.py`
- Modify: `rag-service/src/retrieval/fts.py`
- Modify: `rag-service/src/retrieval/chroma.py`
- Modify: `rag-service/src/api/router_kb.py`
- Modify: `rag-service/tests/test_ingestion/test_activation.py`
- Modify: `rag-service/tests/test_ingestion/test_worker.py`
- Modify: `rag-service/tests/test_api/test_kb_admin.py`
- Modify: `rag-service/tests/test_db/test_migration.py`

**Interfaces:**
- Extends: ingestion activation to Chroma, FTS, and filter sidecar.
- Produces: exact run verification and retry cleanup across both stores.

- [ ] **Step 1: Extend crash-matrix tests**

Inject failures after FTS staging, Chroma staging, verification, FTS activation, Chroma activation, and MySQL switch. Assert old runs remain queryable until the MySQL switch and partially active new runs remain invisible.

- [ ] **Step 2: Add migration 0006**

Add any query-log diagnostic columns required for `retrieval_mode`, `reranker_status`, and `degradation_reason`; keep them nullable for existing rows. Update migration-head assertions.

- [ ] **Step 3: Stage FTS alongside embeddings**

For every embedding batch, stage the same `IndexChunk` IDs into FTS and EAV. At the end compare expected IDs/hashes with both stores. A mismatch sets `IngestRun.state="failed"` and activates neither store.

- [ ] **Step 4: Implement the full activation protocol**

```text
mark FTS run active
mark Chroma run active
transactionally switch Document.active_ingest_run_id
mark document done or done_with_warnings
asynchronously delete the old run from FTS and Chroma
```

Both retrieval lanes additionally compare candidate run IDs against MySQL's authoritative map, making pre-switch external activation invisible.

- [ ] **Step 5: Preserve blue-green knowledge-base rebuilds**

Extend the existing rebuild endpoint to allocate a new collection generation and new document ingest runs without deleting the serving generation. Only after every document run verifies in both stores may one MySQL transaction switch `KnowledgeBase.active_collection` and the documents' active run IDs. A failed document marks the rebuild failed and retains the old collection/run map. Add API tests for successful switch, partial failure, and retry cleanup.

- [ ] **Step 6: Run and commit**

Run: `.venv/Scripts/python.exe -m pytest tests/test_ingestion/test_activation.py tests/test_ingestion/test_worker.py tests/test_api/test_kb_admin.py tests/test_db/test_migration.py -q`

Expected: all activation and migration tests pass.

```powershell
git add rag-service/src/db/migrations/versions/0006_hybrid_retrieval_metadata.py rag-service/src/ingestion/worker.py rag-service/src/retrieval/fts.py rag-service/src/retrieval/chroma.py rag-service/src/api/router_kb.py rag-service/tests/test_ingestion/test_activation.py rag-service/tests/test_ingestion/test_worker.py rag-service/tests/test_api/test_kb_admin.py rag-service/tests/test_db/test_migration.py
git commit -m "feat: activate dense and lexical indexes together"
```

---

### Task 6: Provider-neutral cloud reranker

**Files:**
- Create: `rag-service/src/models/rerank.py`
- Create: `rag-service/src/retrieval/rerank.py`
- Modify: `rag-service/src/models/client.py`
- Modify: `rag-service/src/shared/config.py`
- Modify: `rag-service/src/db/models.py`
- Create: `rag-service/src/db/migrations/versions/0007_reranker_settings.py`
- Modify: `rag-service/src/api/router_settings.py`
- Create: `rag-service/tests/test_models/test_rerank.py`
- Create: `rag-service/tests/test_retrieval/test_rerank.py`
- Modify: `rag-service/tests/test_api/test_settings.py`

**Interfaces:**
- Produces: `RerankClient.rerank(query, candidates, *, top_n) -> list[RerankResult]`.
- Produces: `RerankModule.rerank(query, candidates, top_n) -> list[RetrievedChunk]`.
- Adds settings: enabled, provider, base URL, API key, model, timeout seconds, batch size, top_n.

- [ ] **Step 1: Add contract and HTTP tests**

Mock the cloud endpoint and assert stable candidate IDs, query, model, and documents are sent; scalar scores are parsed and reordered. Reject duplicate IDs, missing scores, nonfinite scores, and unknown returned IDs.

```json
{
  "model": "configured-model",
  "query": "question",
  "documents": [{"id": "c1", "text": "..."}],
  "top_n": 30
}
```

- [ ] **Step 2: Implement provider adapters behind one interface**

Define a small adapter protocol for request/response shape. Keep credentials in `SecretStr` and never log request documents or keys. Use `httpx.AsyncClient` with configured timeout; map 429/5xx/timeouts/malformed responses to `RerankError(code, retryable)`.

- [ ] **Step 3: Add migration and settings API tests**

Add nullable reranker fields to `rag_model_setting`; API responses return `reranker_api_key_configured: bool` and never the key. Empty update keys preserve the stored secret, matching existing embedding-key behavior.

- [ ] **Step 4: Implement bounded reranking with fallback**

Pass only the first `min(30, configured_top_n, len(candidates))` fused candidates. On success assign `rerank_score` and append untouched tail candidates after reranked results. On any `RerankError`, return the original RRF list and a degradation status for logging.

- [ ] **Step 5: Run and commit**

Run: `.venv/Scripts/python.exe -m pytest tests/test_models/test_rerank.py tests/test_retrieval/test_rerank.py tests/test_api/test_settings.py tests/test_db/test_migration.py -q`

Expected: success, timeout, rate-limit, malformed-response, and secret-redaction tests pass.

```powershell
git add rag-service/src/models/rerank.py rag-service/src/retrieval/rerank.py rag-service/src/models/client.py rag-service/src/shared/config.py rag-service/src/db/models.py rag-service/src/db/migrations/versions/0007_reranker_settings.py rag-service/src/api/router_settings.py rag-service/tests/test_models/test_rerank.py rag-service/tests/test_retrieval/test_rerank.py rag-service/tests/test_api/test_settings.py rag-service/tests/test_db/test_migration.py
git commit -m "feat: add configurable cloud reranking"
```

---

### Task 7: Runtime wiring, score compatibility, and bounded context

**Files:**
- Modify: `rag-service/src/shared/runtime.py`
- Modify: `rag-service/src/api/app.py`
- Modify: `rag-service/src/engine/query.py`
- Modify: `rag-service/src/retrieval/context.py`
- Modify: `rag-service/src/api/router_kb.py`
- Modify: `rag-service/tests/test_engine/test_query.py`
- Modify: `rag-service/tests/test_retrieval/test_context.py`
- Modify: `rag-service/tests/test_api/test_kb.py`
- Modify: `web/src/api/retrieval.ts`

**Interfaces:**
- Makes: `HybridRetrievalModule` the application retrieval implementation.
- Preserves: public `score` as nullable cosine.
- Adds: diagnostic scores/ranks/sources to test-retrieval responses.

- [ ] **Step 1: Add runtime and response tests**

Assert the app constructs one shared `FtsIndex`, `ChromaRetrieval`, optional `RerankModule`, and `HybridRetrievalModule`. Test-retrieval JSON must serialize FTS-only results with `score: null` and diagnostic fields without changing historical chat reference payload requirements.

- [ ] **Step 2: Wire the hybrid module**

Initialize FTS schema during startup before the worker starts. Inject the same FTS instance into ingestion and retrieval. Close shared HTTP clients on shutdown.

- [ ] **Step 3: Expand parent/neighbor context after reranking**

For a child hit, fetch only its parent title/summary by `parent_id`; for long record chunks, fetch `chunk_index ± 1`. Do not load every child. Deduplicate by chunk ID and content hash, then call `pack_context(max_chunks=8, max_tokens=2000)`.

- [ ] **Step 4: Update query logging and frontend types**

Persist retrieval mode, reranker status, and degradation reason without storing query text beyond existing policy. In `web/src/api/retrieval.ts`, make `score: number | null` and add optional dense/lexical ranks/scores, RRF/rerank score, and retrieval sources.

- [ ] **Step 5: Run full verification and commit**

Run: `.venv/Scripts/python.exe -m pytest -q`

Run from `web/`: `npx vue-tsc --noEmit && npm run build`

Expected: backend suite, type check, and production build pass; existing bundle-size warning is allowed.

```powershell
git add rag-service/src/shared/runtime.py rag-service/src/api/app.py rag-service/src/engine/query.py rag-service/src/retrieval/context.py rag-service/src/api/router_kb.py rag-service/tests/test_engine/test_query.py rag-service/tests/test_retrieval/test_context.py rag-service/tests/test_api/test_kb.py web/src/api/retrieval.ts
git commit -m "feat: wire hybrid retrieval and bounded context"
```
