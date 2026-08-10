# Structured JSON Ingestion Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add safe, reusable, versioned, streaming JSON/JSONL mapping and ingestion while preserving the existing TXT/Markdown path.

**Architecture:** A new `src.structured` deep module owns path parsing, profiling, mapping, transforms, artifacts, and chunk construction. JSON uploads pause at `awaiting_mapping`; confirmed mappings enqueue the existing worker, which writes a staged Chroma run and atomically exposes it through `Document.active_ingest_run_id`. TXT/Markdown continue through the current parser path.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, SQLAlchemy asyncio/MySQL, Alembic, ijson, ChromaDB, pytest.

## Global Constraints

- Accept only `.json` and `.jsonl` in the structured path; retain `.txt`, `.md`, and `.markdown` behavior.
- Upload limit remains 100 MB and upload/parse operations must not read the full file into memory.
- Supported paths are `$`, object fields, array indices, `[*]`, and relative child paths; no recursive descent, filters, functions, or uploaded code.
- Mapping suggestions require explicit user confirmation before ingestion.
- Used mapping versions are immutable.
- Preserve the original upload, `records.jsonl`, `mapping-errors.jsonl`, `manifest.json`, and each record's raw JSON.
- Syntax errors fail the document; record errors fail at `max(1, min(ceil(total_records * 0.01), 100))`.
- Remove the worker's 500-character secondary split; one `ChunkBuilder` owns all model-limit handling.
- Retrieval visibility requires both `index_state=active` and `ingest_run_id == Document.active_ingest_run_id`.
- This plan must complete before `2026-08-10-independent-fts-hybrid-reranking.md`.

---

### Task 1: Streaming upload and structured-ingestion schema

**Files:**
- Modify: `rag-service/pyproject.toml`
- Modify: `rag-service/src/ingestion/storage.py`
- Modify: `rag-service/src/db/models.py`
- Create: `rag-service/src/db/migrations/versions/0005_structured_json_ingestion.py`
- Modify: `rag-service/tests/test_ingestion/test_storage.py`
- Modify: `rag-service/tests/test_db/test_migration.py`

**Interfaces:**
- Produces: `atomic_save_upload(root_dir, temp_dir, upload, filename, *, max_size_bytes) -> tuple[str, int, str]`.
- Produces: `MappingTemplate`, `MappingTemplateVersion`, `DocumentMapping`, and `IngestRun` ORM models.
- Extends: `Document.active_ingest_run_id`, `processed_path`, and `mapping_errors_path`.

- [ ] **Step 1: Add the incremental parser dependency**

Add `"ijson>=3.4,<4"` to `[project].dependencies`. The implementation will use the documented binary-file APIs:

```python
ijson.items(source, "items.item")
ijson.items(source, "", multiple_values=True)
```

- [ ] **Step 2: Write failing streaming-storage tests**

Add a fake async upload whose `read(size)` records requested sizes. Verify a 2 MiB payload is saved in multiple reads, returns `(relative_path, byte_count, sha256)`, and a file over the configured limit raises `AppError("FILE_TOO_LARGE", ...)` without leaving the temporary file.

```python
result = await atomic_save_upload(root, temp, upload, "records.json", max_size_bytes=3 * 1024 * 1024)
assert result[1] == len(payload)
assert result[2] == hashlib.sha256(payload).hexdigest()
assert len(upload.read_sizes) > 1
assert max(upload.read_sizes) <= 1024 * 1024
```

- [ ] **Step 3: Implement chunked upload persistence**

Write to a unique file below `temp_dir`, update SHA-256 while reading 1 MiB chunks, enforce the limit before every write, then use `os.replace` into the existing upload-root layout. On failure, unlink only the resolved temporary file.

```python
async def atomic_save_upload(..., *, max_size_bytes: int) -> tuple[str, int, str]:
    digest = hashlib.sha256()
    size = 0
    with temp_path.open("wb") as target:
        while chunk := await upload.read(1024 * 1024):
            size += len(chunk)
            if size > max_size_bytes:
                raise AppError("FILE_TOO_LARGE", "文件大小超过限制")
            digest.update(chunk)
            target.write(chunk)
    os.replace(temp_path, final_path)
    return final_path.relative_to(root_dir).as_posix(), size, digest.hexdigest()
```

- [ ] **Step 4: Write the migration assertions**

Extend migration tests to assert these tables and columns exist after upgrading to head:

```text
rag_mapping_template
rag_mapping_template_version
rag_document_mapping
rag_ingest_run
rag_document.active_ingest_run_id
rag_document.processed_path
rag_document.mapping_errors_path
```

- [ ] **Step 5: Add ORM models and migration 0005**

Use `BigInteger` numeric IDs for templates/versions, a 36-character UUID string for `IngestRun.id`, JSON mappings in `Text`, and these uniqueness rules:

```text
UNIQUE(mapping_template_id, version)
UNIQUE(document_id)
```

Expand `Document.status` to:

```text
uploaded, profiling, awaiting_mapping, previewing, queued, mapping, chunking,
embedding, indexing_lexical, indexing_dense, activating, done,
done_with_warnings, failed, deleting
```

Keep `DocumentJob.status` unchanged; expand its stage check to the nonterminal document stages. `DocumentMapping` stores `document_id`, `mapping_version_id`, `confirmed_by_user_id`, and `confirmed_at`. `IngestRun` stores `id`, `document_id`, `state`, counts, artifact paths, error text, and timestamps.

- [ ] **Step 6: Run and commit**

Run: `.venv/Scripts/python.exe -m pytest tests/test_ingestion/test_storage.py tests/test_db/test_migration.py -q`

Expected: all focused tests pass.

```powershell
git add rag-service/pyproject.toml rag-service/src/ingestion/storage.py rag-service/src/db/models.py rag-service/src/db/migrations/versions/0005_structured_json_ingestion.py rag-service/tests/test_ingestion/test_storage.py rag-service/tests/test_db/test_migration.py
git commit -m "feat: add structured ingestion persistence"
```

---

### Task 2: Safe path compiler and streaming record sources

**Files:**
- Create: `rag-service/src/structured/__init__.py`
- Create: `rag-service/src/structured/models.py`
- Create: `rag-service/src/structured/paths.py`
- Create: `rag-service/src/structured/stream.py`
- Create: `rag-service/tests/test_structured/test_paths.py`
- Create: `rag-service/tests/test_structured/test_stream.py`

**Interfaces:**
- Produces: `compile_path(value: str, *, relative: bool = False) -> CompiledPath`.
- Produces: `iter_source(path: Path, source_format: Literal["json", "jsonl"], record_path: CompiledPath) -> Iterator[SourceRecord]`.
- Produces: Pydantic models `FieldMapping`, `RecordTypeMapping`, `MappingDefinition`, `SourceRecord`, `MappedRecord`, and `IndexChunk`.

- [ ] **Step 1: Write path grammar tests**

```python
@pytest.mark.parametrize("path", ["$", "$.posts", "$.posts[*]", "$.items[0]", "comments[*]"])
def test_supported_paths_compile(path: str) -> None:
    assert compile_path(path, relative=not path.startswith("$")).tokens

@pytest.mark.parametrize("path", ["$..posts", "$.posts[?(@.x)]", "$.x()", "$.items[-1]"])
def test_unsafe_paths_are_rejected(path: str) -> None:
    with pytest.raises(PathSyntaxError):
        compile_path(path)
```

- [ ] **Step 2: Define immutable mapping and record contracts**

Use Pydantic `ConfigDict(frozen=True, extra="forbid")`. `FieldMapping.role` is exactly `id|title|content|keyword|filter|timestamp|display|ignore`. `MappedRecord` contains the fields from the approved design and `raw: dict[str, Any]`; `IndexChunk` contains separate embedding and lexical fields plus `ingest_run_id` and `index_state`.

- [ ] **Step 3: Implement the path compiler**

Tokenize fields, nonnegative indices, and array wildcards. Provide `resolve_one(value, path)` for scalar mappings and `resolve_many(value, path)` for child arrays. Reject a wildcard in `resolve_one` unless the caller explicitly joins it.

- [ ] **Step 4: Write streaming source tests**

Cover root arrays, `$.posts[*]`, root JSON objects, and JSONL. Assert `source_pointer` values such as `/0`, `/posts/1`, and `/line/2`. Truncated JSON and malformed JSONL must raise `SourceSyntaxError`.

- [ ] **Step 5: Implement ijson-backed iteration**

Convert the safe path to ijson prefixes (`$.posts[*]` → `posts.item`, `$[*]` → `item`). Open files in binary mode. For JSONL use:

```python
for line_number, value in enumerate(ijson.items(handle, "", multiple_values=True), start=1):
    yield SourceRecord(value=value, source_pointer=f"/line/{line_number}")
```

Wrap `ijson.JSONError` and `ijson.IncompleteJSONError` as `SourceSyntaxError` while retaining the source pointer or byte location in the message.

- [ ] **Step 6: Run and commit**

Run: `.venv/Scripts/python.exe -m pytest tests/test_structured/test_paths.py tests/test_structured/test_stream.py -q`

Expected: all focused tests pass.

```powershell
git add rag-service/src/structured rag-service/tests/test_structured
git commit -m "feat: stream safe JSON record paths"
```

---

### Task 3: Deterministic profiler and versioned mapping service

**Files:**
- Create: `rag-service/src/structured/profiler.py`
- Create: `rag-service/src/structured/suggestions.py`
- Create: `rag-service/src/structured/mapping.py`
- Create: `rag-service/tests/test_structured/test_profiler.py`
- Create: `rag-service/tests/test_structured/test_suggestions.py`
- Create: `rag-service/tests/test_structured/test_mapping.py`

**Interfaces:**
- Produces: `profile_source(path, source_format, *, sample_limit=1000) -> SourceProfile`.
- Produces: `suggest_mapping(profile: SourceProfile) -> MappingSuggestion`.
- Produces: `enhance_suggestion(profile, deterministic, chat_client) -> MappingSuggestion` with deterministic fallback.
- Produces: `schema_fingerprint(profile: SourceProfile) -> str` and `compatibility(old, new) -> Compatibility`.
- Produces: `MappingService.create_version(...)` and `MappingService.bind_document(...)`.

- [ ] **Step 1: Write profiling and suggestion tests**

Use fixtures with unique IDs, long body text, repeated tags, timestamps, and nested comments. Assert deterministic roles:

```python
assert suggestion.field("id").role == "id"
assert suggestion.field("body").role == "content"
assert suggestion.field("date").role == "timestamp"
assert suggestion.record_types[0].children[0].record_path == "comments[*]"
```

Assert identical structures with different values have the same fingerprint; adding an optional field is compatible; removing `body` or changing `comments` from array to object is breaking.

- [ ] **Step 2: Implement bounded profiling**

Track path, observed container/scalar types, null rate, uniqueness estimate, min/max/average string length, datetime parse rate, and child-array frequency. Retain at most five redacted examples per path and at most 1000 sampled records. Keep the first 800 records and use deterministic reservoir sampling for 200 positions across the remaining stream; add a fixture whose schema-changing field appears near EOF. Do not include values in the fingerprint.

- [ ] **Step 3: Implement deterministic role suggestions**

Use ordered rules: high uniqueness plus ID-like name → `id`; parseable datetime → `timestamp`; long text → `content`; short repeated scalar/list → `keyword`; otherwise `display`. Return confidence and rule IDs such as `unique_identifier`, `long_text`, and `datetime_parse_rate`.

- [ ] **Step 4: Implement and test optional semantic suggestion enhancement**

Add suggestion-enhancement tests before persistence tests. The cloud prompt may contain only path/type/statistics and redacted examples capped at 200 characters. Validate the response against `MappingSuggestion`; timeout, invalid JSON, or an unsupported role returns the deterministic suggestion unchanged. The enhanced result remains a suggestion and cannot bind a document.

```python
enhanced = await enhance_suggestion(profile, deterministic, fake_chat)
assert fake_chat.last_payload["requires_user_confirmation"] is True
assert enhanced.requires_user_confirmation is True
```

- [ ] **Step 5: Write immutable-version persistence tests**

Assert the first version is `1`, a changed definition creates version `2`, and an attempt to update a used version raises `AppError("MAPPING_VERSION_IMMUTABLE", ...)`. Binding a document stores the confirmer and sets `Document.status="queued"` only after validation succeeds.

- [ ] **Step 6: Implement MappingService**

Canonicalize mapping JSON with sorted keys before hashing. Validate every path with `compile_path`, require one `content` or `title` field per indexed record type, and use a transaction to create `DocumentMapping` plus an ingest job.

- [ ] **Step 7: Run and commit**

Run: `.venv/Scripts/python.exe -m pytest tests/test_structured/test_profiler.py tests/test_structured/test_suggestions.py tests/test_structured/test_mapping.py -q`

Expected: all focused tests pass.

```powershell
git add rag-service/src/structured/profiler.py rag-service/src/structured/suggestions.py rag-service/src/structured/mapping.py rag-service/tests/test_structured/test_profiler.py rag-service/tests/test_structured/test_suggestions.py rag-service/tests/test_structured/test_mapping.py
git commit -m "feat: profile and version JSON mappings"
```

---

### Task 4: Mapping transforms, parent-child records, and artifacts

**Files:**
- Create: `rag-service/src/structured/transforms.py`
- Create: `rag-service/src/structured/artifacts.py`
- Create: `rag-service/tests/test_structured/test_transforms.py`
- Create: `rag-service/tests/test_structured/test_artifacts.py`

**Interfaces:**
- Produces: `map_record(source: SourceRecord, mapping: RecordTypeMapping, context: MappingContext) -> Iterator[MappedRecord]`.
- Produces: `ArtifactWriter.write_record`, `write_error`, and `finalize`.
- Produces: stable fallback IDs from source hash, JSON pointer, and record type.

- [ ] **Step 1: Add transform and relation tests**

Cover `trim`, newline normalization, `strip_html`, `join`, scalar conversion, `parse_datetime`, `deduplicate`, and `remove_child_echo`. For three comments where comment 2 has `referTo="A"`, assert the nearest prior sibling authored by A becomes its parent/context record.

```python
records = list(map_record(source, post_mapping, context))
assert records[0].parent_id is None
assert records[2].parent_id == records[1].record_id
assert records[0].content.count(records[1].content) == 0
```

- [ ] **Step 2: Implement a fixed transform registry**

Use a dictionary from declared names to pure functions. Never import a callable named by user input. Register exact transform names `trim`, `normalize_whitespace`, `normalize_newlines`, `strip_html`, `join`, `to_string`, `to_number`, `to_boolean`, `parse_datetime`, `deduplicate`, and `remove_child_echo`. Apply the declared conditions `required`, `min_length`, `max_length`, `not_empty`, `skip_if_only_emoji`, and `skip_if_matches` after transforms; classify results as `record_error` or `warning`.

- [ ] **Step 3: Implement record mapping and stable IDs**

Build title/content in declared field order. Normalize filters to JSON scalar values. Generate fallback IDs with:

```python
payload = f"{source_hash}\0{source_pointer}\0{record_type}".encode()
record_id = hashlib.sha256(payload).hexdigest()
```

Emit the parent before its children and preserve each raw object.

- [ ] **Step 4: Write artifact tests**

Assert `records.jsonl` and `mapping-errors.jsonl` are valid UTF-8 JSONL, `manifest.json` includes counts/hashes/mapping version, and an interrupted run leaves only its run directory without replacing the active artifact path.

- [ ] **Step 5: Implement atomic run artifacts**

Write below `<upload_root>/processed/<doc_id>/<ingest_run_id>/`. Flush records and errors line-by-line. `finalize` closes files, computes their hashes, writes the manifest through a temporary file, and returns relative paths for `IngestRun`.

- [ ] **Step 6: Run and commit**

Run: `.venv/Scripts/python.exe -m pytest tests/test_structured/test_transforms.py tests/test_structured/test_artifacts.py -q`

Expected: all focused tests pass.

```powershell
git add rag-service/src/structured/transforms.py rag-service/src/structured/artifacts.py rag-service/tests/test_structured/test_transforms.py rag-service/tests/test_structured/test_artifacts.py
git commit -m "feat: map hierarchical JSON records"
```

---

### Task 5: Unified ChunkBuilder and Chroma run metadata

**Files:**
- Create: `rag-service/src/structured/chunks.py`
- Modify: `rag-service/src/ingestion/worker.py`
- Modify: `rag-service/src/retrieval/chroma.py`
- Create: `rag-service/tests/test_structured/test_chunks.py`
- Modify: `rag-service/tests/test_ingestion/test_worker.py`
- Modify: `rag-service/tests/test_retrieval/test_chroma.py`

**Interfaces:**
- Produces: `ChunkBuilder.build(record, policy, *, doc_id, ingest_run_id, chunk_size, overlap) -> Iterator[IndexChunk]`.
- Extends: `ChromaRetrieval.upsert_chunks` metadata with structured record/run fields.
- Produces: `set_run_state(collection_name, ingest_run_id, state)` and `delete_run(collection_name, ingest_run_id)`.

- [ ] **Step 1: Add failing chunk-policy tests**

Assert semantic, atomic, parent-only, and ignore behavior. An atomic record above the configured model-token ceiling must split on semantic boundaries, never arbitrary 500-character offsets. Every chunk ID must be stable across identical reruns and include the record ID and ordinal.

- [ ] **Step 2: Implement ChunkBuilder**

Delegate semantic splitting to `split_text`, but construct `embedding_text` from title and content, and lexical fields separately. Use a stable ID:

```python
chunk_id = f"{doc_id}:{record.record_id}:{ordinal}:{content_hash[:12]}"
```

For `parent-only`, return no child index chunks but keep children in artifacts for context lookup.

- [ ] **Step 3: Remove the worker hard split**

Delete `MAX_EMBED_CHARS` and the `#offset` loop. TXT/Markdown records are wrapped in an internal `MappedRecord(record_type="document")` and sent through `ChunkBuilder`, so both paths share model-limit enforcement.

- [ ] **Step 4: Persist structured Chroma metadata**

Write `doc_id`, `record_id`, `parent_id`, `record_type`, `mapping_version_id`, `source_pointer`, normalized timestamp, `content_hash`, `chunk_index`, `ingest_run_id`, and `index_state`. Add fake-collection tests proving run-scoped state updates and deletes target only the requested run.

- [ ] **Step 5: Run and commit**

Run: `.venv/Scripts/python.exe -m pytest tests/test_structured/test_chunks.py tests/test_ingestion/test_worker.py tests/test_retrieval/test_chroma.py -q`

Expected: all focused tests pass and no test expects `#500` hard-split IDs.

```powershell
git add rag-service/src/structured/chunks.py rag-service/src/ingestion/worker.py rag-service/src/retrieval/chroma.py rag-service/tests/test_structured/test_chunks.py rag-service/tests/test_ingestion/test_worker.py rag-service/tests/test_retrieval/test_chroma.py
git commit -m "feat: centralize structured chunk building"
```

---

### Task 6: Profile, preview, template, and ingest APIs

**Files:**
- Create: `rag-service/src/api/router_structured.py`
- Modify: `rag-service/src/api/app.py`
- Modify: `rag-service/src/api/router_docs.py`
- Create: `rag-service/tests/test_api/test_structured.py`
- Modify: `rag-service/tests/test_api/test_docs.py`

**Interfaces:**
- Produces: `POST /api/kb/{kb_id}/json/profile`.
- Produces: `POST /api/kb/{kb_id}/json/preview`.
- Produces: `GET|POST /api/mapping-templates` and `GET /api/mapping-templates/{id}/versions`.
- Produces: `POST /api/kb/{kb_id}/json/ingest`.
- Produces: `GET /api/docs/{doc_id}/mapping-errors`.

- [ ] **Step 1: Add authorization and lifecycle API tests**

Test admin ownership checks, JSON upload returning `awaiting_mapping` without a job, TXT upload still returning `pending` plus a job, profile shape, preview of five real mapped records, template version creation, ingest confirmation, and cross-owner access returning 404.

- [ ] **Step 2: Make upload format-aware and streaming**

Use the filename suffix before parser validation. TXT/Markdown retain parser validation and immediate job creation. JSON/JSONL use `atomic_save_upload`, create a document in `uploaded`, then transition to `awaiting_mapping`; syntax validation occurs in profile so the request does not load the file.

- [ ] **Step 3: Implement structured endpoints**

Run CPU/file parsing through `asyncio.to_thread`. Preview must call the same `iter_source`, `map_record`, and `ChunkBuilder` used by the worker and return:

```json
{
  "raw": {},
  "content": "...",
  "embedding_text": "...",
  "lexical": {"title": "...", "content": "...", "keywords": []},
  "metadata": {},
  "warnings": []
}
```

Template create validates paths and stores a new immutable version. Ingest binds that version and creates exactly one pending ingest job.

- [ ] **Step 4: Expose errors safely**

Only the document owner/admin may read `mapping-errors.jsonl`. Return it as an attachment without resolving a path outside `upload.root_dir`; missing error artifacts return an empty JSONL response for successful runs.

- [ ] **Step 5: Run and commit**

Run: `.venv/Scripts/python.exe -m pytest tests/test_api/test_structured.py tests/test_api/test_docs.py -q`

Expected: all focused tests pass.

```powershell
git add rag-service/src/api/router_structured.py rag-service/src/api/app.py rag-service/src/api/router_docs.py rag-service/tests/test_api/test_structured.py rag-service/tests/test_api/test_docs.py
git commit -m "feat: expose JSON mapping workflow APIs"
```

---

### Task 7: Staged structured worker and atomic activation

**Files:**
- Modify: `rag-service/src/ingestion/worker.py`
- Modify: `rag-service/src/db/repositories.py`
- Modify: `rag-service/tests/test_ingestion/test_worker.py`
- Create: `rag-service/tests/test_ingestion/test_activation.py`

**Interfaces:**
- Consumes: bound `MappingTemplateVersion`, `ArtifactWriter`, `ChunkBuilder`, and Chroma run methods.
- Produces: `Document.active_ingest_run_id` as the final visibility gate.
- Produces: resumable cleanup of abandoned runs.

- [ ] **Step 1: Add worker state and failure-threshold tests**

Use 99 valid records plus one invalid required field and assert failure at the first error because the 100-record threshold is 1. Use 10,000 records with 99 errors and assert `done_with_warnings`; 100 errors must fail. Assert syntax errors fail the document and activate nothing.

- [ ] **Step 2: Add crash-window activation tests**

Inject failures after Chroma staging, after verification, and after external state becomes active. In every case assert `Document.active_ingest_run_id` still points to the old run. On a successful retry, assert the new ID is switched once and old-run cleanup is requested.

- [ ] **Step 3: Split worker orchestration by source type**

Keep `_process_text_document` for TXT/Markdown and add `_process_structured_document`. The structured path streams source records through mapping, artifacts, chunks, batched embeddings, and staged Chroma writes without collecting the whole document.

Use a bounded producer/consumer queue of at most 256 chunks. Update document stages at each approved transition and increment run counters in batches rather than per record.

- [ ] **Step 4: Implement activation protocol**

For this plan's Chroma-only stage:

```text
write Chroma staging
verify expected chunk IDs/count/hash
mark Chroma run active
transactionally set Document.active_ingest_run_id and final status
delete old Chroma run and old artifacts asynchronously
```

Retrieval changes in the next plan add FTS to the same protocol. Until then, Chroma queries include both `index_state="active"` and the current document run ID.

- [ ] **Step 5: Run backend regression and commit**

Run: `.venv/Scripts/python.exe -m pytest -q`

Expected: complete backend suite passes with zero failures.

```powershell
git add rag-service/src/ingestion/worker.py rag-service/src/db/repositories.py rag-service/tests/test_ingestion/test_worker.py rag-service/tests/test_ingestion/test_activation.py
git commit -m "feat: activate structured ingest runs atomically"
```
