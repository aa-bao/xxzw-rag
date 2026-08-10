# JSON Mapping Wizard and Quality Rollout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the user-facing JSON mapping wizard, reusable template management, observable ingestion states, and measured rollout against generic and Knowledge Planet fixtures.

**Architecture:** Vue components keep only transient wizard state while every profile and preview comes from backend APIs. The existing document page routes JSON files into a six-step wizard and continues polling after confirmed ingestion. A versioned Knowledge Planet mapping is included only as a regression fixture, and retrieval configurations are promoted through an offline comparison harness.

**Tech Stack:** Vue 3, TypeScript 5.7, Element Plus, Vitest, Vue Test Utils, FastAPI APIs from the first two plans, pytest.

## Global Constraints

- Complete `2026-08-10-structured-json-ingestion-backend.md` and `2026-08-10-independent-fts-hybrid-reranking.md` first.
- Mapping suggestions never start ingestion without explicit confirmation.
- Preview displays raw record, readable content, embedding text, lexical fields, metadata, and warnings from the backend.
- Mapping roles are exactly `id`, `title`, `content`, `keyword`, `filter`, `timestamp`, `display`, `ignore`.
- Used mapping versions are immutable and breaking schema changes require reconfirmation.
- Frontend never evaluates JSONPath or transform code; it submits declarations to the backend.
- Preserve the existing TXT/Markdown multi-file upload flow.
- JSON/JSONL structured upload handles one file per wizard session.
- Media URLs are retained as display metadata; OCR and video retrieval remain recorded backlog work.
- Quality gates: Top-5 source hit ≥90%, exact-term Recall@5 ≥95%, parent-child citation ≥95%, refusal ≥90%, zero cross-KB leakage.

---

### Task 1: Structured API contracts and wizard state

**Files:**
- Create: `web/src/api/structured.ts`
- Create: `web/src/types/structured.ts`
- Create: `web/src/components/json-mapping/useJsonMappingWizard.ts`
- Create: `web/src/components/json-mapping/useJsonMappingWizard.test.ts`

**Interfaces:**
- Produces: typed clients for profile, preview, templates, ingest, and errors.
- Produces: `useJsonMappingWizard(kbId)` with six numbered steps and guarded transitions.

- [ ] **Step 1: Define exact frontend contracts**

Mirror backend snake_case payloads. Core types include:

```typescript
export type FieldRole = 'id' | 'title' | 'content' | 'keyword' | 'filter' | 'timestamp' | 'display' | 'ignore'

export interface FieldMapping {
  path: string
  role: FieldRole
  name: string
  transforms: Array<{ name: string; args?: Record<string, unknown> }>
  required: boolean
}

export interface MappingDefinition {
  source_format: 'json' | 'jsonl'
  record_types: RecordTypeMapping[]
}
```

Define profile candidates, compatibility result, preview rows, template/version summaries, ingest response, and mapping errors without `any`.

- [ ] **Step 2: Implement API functions**

Use the shared client:

```typescript
export async function profileJson(kbId: number, docId: number): Promise<SourceProfile> {
  return (await api.post<SourceProfile>(`/kb/${kbId}/json/profile`, { doc_id: docId })).data
}
```

Implement `previewJson`, `listMappingTemplates`, `createMappingTemplateVersion`, `startJsonIngest`, and `mappingErrorsUrl` with the approved endpoints.

- [ ] **Step 3: Write failing state-machine tests**

Assert initial step is upload; profile success advances to structure; mapping cannot advance without one title/content; preview cannot run while profile is absent; changing a breaking schema invalidates confirmation; ingest success records `doc_id` and `job_id` then resets transient raw preview data.

- [ ] **Step 4: Implement the composable**

Use a discriminated state union rather than unrelated booleans:

```typescript
type WizardState =
  | { step: 'upload'; file: File | null }
  | { step: 'structure'; docId: number; profile: SourceProfile }
  | { step: 'fields'; docId: number; profile: SourceProfile; mapping: MappingDefinition }
  | { step: 'relations'; docId: number; mapping: MappingDefinition }
  | { step: 'preview'; docId: number; mapping: MappingDefinition; rows: PreviewRow[] }
  | { step: 'confirm'; docId: number; mapping: MappingDefinition; rows: PreviewRow[] }
```

Expose explicit methods per transition and a single `busy` operation name to prevent double submits.

- [ ] **Step 5: Run and commit**

Run from `web/`: `npm run test -- src/components/json-mapping/useJsonMappingWizard.test.ts`

Run from `web/`: `npx vue-tsc --noEmit`

Expected: tests and type check pass.

```powershell
git add web/src/api/structured.ts web/src/types/structured.ts web/src/components/json-mapping/useJsonMappingWizard.ts web/src/components/json-mapping/useJsonMappingWizard.test.ts
git commit -m "feat: add JSON mapping wizard state"
```

---

### Task 2: Upload and structure-detection steps

**Files:**
- Create: `web/src/components/json-mapping/JsonMappingWizard.vue`
- Create: `web/src/components/json-mapping/JsonUploadStep.vue`
- Create: `web/src/components/json-mapping/JsonStructureStep.vue`
- Create: `web/src/components/json-mapping/JsonMappingWizard.test.ts`
- Modify: `web/src/components/UploadDialog.vue`
- Modify: `web/src/views/KbDocsView.vue`

**Interfaces:**
- Consumes: `useJsonMappingWizard` and structured APIs from Task 1.
- Produces: one-file JSON/JSONL wizard launch while leaving existing text upload unchanged.

- [ ] **Step 1: Add routing behavior tests**

Mount the document view with API mocks. Selecting TXT/Markdown must call the existing `uploadDoc`; selecting JSON/JSONL must open `JsonMappingWizard` and must not enqueue the legacy three-way upload queue. Unsupported extensions show an inline error.

- [ ] **Step 2: Build the wizard shell**

Use a modal/drawer with a six-step header, close confirmation after upload, a persistent back button, and one primary action. Emit only:

```typescript
defineEmits<{
  (e: 'close'): void
  (e: 'ingested', value: { docId: number; jobId: number }): void
}>()
```

Do not duplicate API state in child components.

- [ ] **Step 3: Build streaming upload UX**

Accept `.json,.jsonl`, display the 100 MB limit before selection, upload through the existing document endpoint, then call profile. Show separate progress labels `上传文件` and `探查结构`; disable closing only during the active HTTP request.

- [ ] **Step 4: Build structure candidate selection**

Render candidate record paths with record count estimate, field count, nested-array count, confidence, and five redacted samples. Selecting a candidate regenerates the deterministic draft mapping; changing selection requires confirmation if field edits already exist.

- [ ] **Step 5: Run and commit**

Run from `web/`: `npm run test -- src/components/json-mapping/JsonMappingWizard.test.ts`

Run from `web/`: `npx vue-tsc --noEmit && npm run build`

Expected: component tests, type check, and build pass.

```powershell
git add web/src/components/json-mapping/JsonMappingWizard.vue web/src/components/json-mapping/JsonUploadStep.vue web/src/components/json-mapping/JsonStructureStep.vue web/src/components/json-mapping/JsonMappingWizard.test.ts web/src/components/UploadDialog.vue web/src/views/KbDocsView.vue
git commit -m "feat: add JSON upload and structure steps"
```

---

### Task 3: Field roles, transforms, parent-child, and relation editor

**Files:**
- Create: `web/src/components/json-mapping/JsonFieldMappingStep.vue`
- Create: `web/src/components/json-mapping/JsonRelationStep.vue`
- Create: `web/src/components/json-mapping/JsonFieldMappingStep.test.ts`
- Create: `web/src/components/json-mapping/JsonRelationStep.test.ts`

**Interfaces:**
- Produces: validated `MappingDefinition` containing field roles, fixed transforms, conditions, chunk policy, child types, and relation rule.

- [ ] **Step 1: Add field-editor tests**

Assert every detected field defaults to the backend suggestion, role changes update only that field, `keyword` is labeled “仅关键词检索”, `filter` is labeled “仅筛选”, and Continue is disabled until each indexed record type has title/content.

- [ ] **Step 2: Implement accessible field mapping**

Render one row per path with sample, inferred type, role select, required switch, and transform menu. Transform choices are fixed to `trim`, whitespace/newline normalization, `strip_html`, `join`, scalar conversions, `parse_datetime`, `deduplicate`, and `remove_child_echo`; show only transforms compatible with the observed type.

- [ ] **Step 3: Add hierarchy and relation tests**

Use a parent `post` plus child `comment`. Assert the UI configures `comments[*]`, parent ID inheritance, chunk policy, and the generic nearest-previous-sibling rule with source `referTo` and target `author`. Disabling the rule removes only `relation_rule`.

- [ ] **Step 4: Implement hierarchy editor**

Present a tree of record types. Each node exposes `semantic|atomic|parent-only|ignore`. Child nodes show their relative path and context behavior. Relation rules use field dropdowns only; users cannot enter expressions or scripts.

- [ ] **Step 5: Run and commit**

Run from `web/`: `npm run test -- src/components/json-mapping/JsonFieldMappingStep.test.ts src/components/json-mapping/JsonRelationStep.test.ts`

Expected: all focused component tests pass.

```powershell
git add web/src/components/json-mapping/JsonFieldMappingStep.vue web/src/components/json-mapping/JsonRelationStep.vue web/src/components/json-mapping/JsonFieldMappingStep.test.ts web/src/components/json-mapping/JsonRelationStep.test.ts
git commit -m "feat: edit JSON field and relation mappings"
```

---

### Task 4: Real preview, template confirmation, and ingestion

**Files:**
- Create: `web/src/components/json-mapping/JsonPreviewStep.vue`
- Create: `web/src/components/json-mapping/JsonConfirmStep.vue`
- Create: `web/src/components/json-mapping/JsonPreviewStep.test.ts`
- Modify: `web/src/components/json-mapping/JsonMappingWizard.vue`
- Modify: `web/src/components/json-mapping/JsonMappingWizard.test.ts`

**Interfaces:**
- Consumes: backend-generated `PreviewRow[]`.
- Produces: template/version creation or compatible-template reuse followed by explicit ingestion confirmation.

- [ ] **Step 1: Add preview tests**

Assert tabs display raw JSON, readable content, embedding text, title/keywords/content lexical fields, filters/timestamps/display metadata, and warnings. A backend preview error remains on the same step and displays its source pointer.

- [ ] **Step 2: Implement preview rendering**

Do not reproduce transforms in TypeScript. Call `previewJson` whenever mapping hash changes; cache only the latest successful response. Use a record selector and escaped `<pre>` blocks for raw JSON.

- [ ] **Step 3: Add confirmation tests**

Cover new template name, compatible existing version reuse, optional-field compatibility notice, breaking-change reconfirmation, immutable used version display, double-submit prevention, and successful `ingested` emission.

- [ ] **Step 4: Implement confirmation and ingest**

Show a summary of record types, vector/FTS/filter fields, chunk policies, expected record count, warning count, and selected template version. Require a checkbox reading `我已检查预览并确认按此映射入库`. On submit, create/reuse the template version, then call ingest with `doc_id` and `mapping_version_id`.

- [ ] **Step 5: Run and commit**

Run from `web/`: `npm run test -- src/components/json-mapping/JsonPreviewStep.test.ts src/components/json-mapping/JsonMappingWizard.test.ts`

Run from `web/`: `npx vue-tsc --noEmit && npm run build`

Expected: tests, type check, and production build pass.

```powershell
git add web/src/components/json-mapping/JsonPreviewStep.vue web/src/components/json-mapping/JsonConfirmStep.vue web/src/components/json-mapping/JsonPreviewStep.test.ts web/src/components/json-mapping/JsonMappingWizard.vue web/src/components/json-mapping/JsonMappingWizard.test.ts
git commit -m "feat: preview and confirm JSON ingestion"
```

---

### Task 5: Document states, mapping errors, and template management

**Files:**
- Modify: `web/src/api/docs.ts`
- Modify: `web/src/views/KbDocsView.vue`
- Create: `web/src/views/MappingTemplatesView.vue`
- Modify: `web/src/router/index.ts`
- Create: `web/src/views/MappingTemplatesView.test.ts`
- Modify: `web/src/components/json-mapping/JsonMappingWizard.test.ts`

**Interfaces:**
- Displays: all structured document states and `done_with_warnings`.
- Produces: mapping-error download and template/version inspection.

- [ ] **Step 1: Extend document state tests and types**

Add the approved states to `DocStatus` and `DocStage`. Map them to labels such as `等待映射`, `生成记录`, `全文索引`, `向量索引`, and `正在激活`. `done_with_warnings` uses a warning color and remains viewable/reindexable.

- [ ] **Step 2: Update document polling and actions**

Continue polling every nonterminal state. For `awaiting_mapping`, show `继续配置`; for `done_with_warnings`, show `下载错误`; for failed structured documents, show both error text and a mapping-error download when present.

- [ ] **Step 3: Add template-management tests**

Assert templates list name, source format, current version, compatibility fingerprint, usage count, and timestamps. Expanding a version displays immutable mapping JSON and its field-role summary. Creating a version starts from a selected version but saves a new version number.

- [ ] **Step 4: Implement template view and route**

Add `/settings/mapping-templates` for account admins. The page may create names and new versions but cannot mutate a used version. Reuse the field/relation editors in edit mode and backend preview before save.

- [ ] **Step 5: Run and commit**

Run from `web/`: `npm run test -- src/views/MappingTemplatesView.test.ts src/components/json-mapping/JsonMappingWizard.test.ts`

Run from `web/`: `npx vue-tsc --noEmit && npm run build`

Expected: tests, type check, and build pass.

```powershell
git add web/src/api/docs.ts web/src/views/KbDocsView.vue web/src/views/MappingTemplatesView.vue web/src/router/index.ts web/src/views/MappingTemplatesView.test.ts web/src/components/json-mapping/JsonMappingWizard.test.ts
git commit -m "feat: manage JSON mappings and ingest states"
```

---

### Task 6: Knowledge Planet reference mapping and media backlog

**Files:**
- Create: `rag-service/tests/fixtures/structured/knowledge_planet_mapping.json`
- Create: `rag-service/tests/fixtures/structured/knowledge_planet_sample.json`
- Create: `rag-service/tests/test_structured/test_knowledge_planet_mapping.py`
- Create: `docs/backlog/structured-media-ingestion.md`

**Interfaces:**
- Produces: a generic mapping fixture, not runtime hard coding.
- Records: image/OCR and protected-video work separately from text ingestion.

- [ ] **Step 1: Add the exact reference mapping**

Use root `$` for one post file and `comments[*]` for children. Map:

```json
{
  "post": {
    "id": "topic_index",
    "title": "body",
    "content": "body",
    "timestamp": "date",
    "filters": ["author", "isOwner", "isCertified"],
    "display": ["images", "likeText"],
    "transforms": ["trim", "normalize_newlines", "remove_child_echo"]
  },
  "comment": {
    "path": "comments[*]",
    "content": "text",
    "timestamp": "time",
    "filters": ["author", "referTo"],
    "display": ["images", "emojis"],
    "relation": {"source": "referTo", "target": "author", "strategy": "nearest_previous_sibling"}
  }
}
```

The actual fixture must use the production `MappingDefinition` schema while retaining these semantics.

- [ ] **Step 2: Add reference regression assertions**

Use sanitized data shaped exactly like `topic_0000.json`. Assert one parent plus all children, fallback/explicit IDs are stable, reply relation links to the nearest earlier author, short replies carry parent/question context, image URLs remain display metadata, and no image URL enters embedding text.

- [ ] **Step 3: Validate against the crawler dataset read-only**

Point a local validation command at `E:/dev/project/knowledge-boll-crawler/data/raw/posts`. Assert 136 parents, 1163 children, no child text of at least 8 characters remains echoed in parent content, and the existing media counts are preserved as metadata. Do not copy the private raw dataset into the RAG repository.

- [ ] **Step 4: Record the media backlog**

Create a concise backlog document with two separate deliverables:

```text
1. Images: download authorization, deduplication, OCR/vision extraction, provenance, retry policy.
2. Videos: inspect signed/anti-hotlink URL lifecycle, authenticated acquisition feasibility, audio transcription, frame sampling, provenance, and legal/permission constraints.
```

State explicitly that video URLs observed through F12 may be signed or anti-hotlink protected and text ingestion does not attempt to bypass those controls.

- [ ] **Step 5: Run and commit**

Run: `.venv/Scripts/python.exe -m pytest tests/test_structured/test_knowledge_planet_mapping.py -q`

Expected: fixture regression passes.

```powershell
git add rag-service/tests/fixtures/structured/knowledge_planet_mapping.json rag-service/tests/fixtures/structured/knowledge_planet_sample.json rag-service/tests/test_structured/test_knowledge_planet_mapping.py docs/backlog/structured-media-ingestion.md
git commit -m "test: add hierarchical JSON reference mapping"
```

---

### Task 7: Retrieval evaluation and rollout gate

**Files:**
- Create: `rag-service/src/evaluation/retrieval.py`
- Create: `rag-service/scripts/evaluate_retrieval.py`
- Create: `rag-service/tests/fixtures/evaluation/schema.json`
- Create: `rag-service/tests/fixtures/evaluation/generic_json_eval.jsonl`
- Create: `rag-service/tests/test_evaluation/test_retrieval.py`
- Create: `docs/evaluation/generic-json-hybrid-baseline.md`

**Interfaces:**
- Produces: repeatable evaluation for dense-only, dense+FTS+RRF, and dense+FTS+RRF+reranker.
- Produces: machine-readable metrics and a checked-in baseline report without credentials or private source text.

- [ ] **Step 1: Define evaluation case schema and metric tests**

Each JSONL case contains:

```json
{"id":"exact-001","question":"...","answerable":true,"expected_doc_ids":[1],"expected_record_ids":["..."],"category":"exact_term"}
```

Add unit tests for source Recall@5, exact-term Recall@5, parent-child citation correctness, refusal rate, and leakage count using small fixed rankings.

- [ ] **Step 2: Implement the evaluation runner**

Accept `--mode dense|hybrid|reranked`, `--dataset`, `--kb-id`, and `--output`. Use application retrieval interfaces, never direct SQL/Chroma shortcuts. Emit per-case ranks plus aggregate JSON. Redact document content and model credentials from outputs.

- [ ] **Step 3: Build the acceptance dataset**

Create at least 180 cases: 100 answerable, 30 unanswerable, 30 exact-term, and 20 parent-child. Categories may overlap, but the file must contain at least 100 distinct answerable and 30 distinct unanswerable questions. Store only synthetic/public fixture text or hashes/IDs for private documents.

- [ ] **Step 4: Run the three-way comparison**

```powershell
.venv/Scripts/python.exe scripts/evaluate_retrieval.py --mode dense --dataset tests/fixtures/evaluation/generic_json_eval.jsonl --kb-id 1 --output artifacts/eval-dense.json
.venv/Scripts/python.exe scripts/evaluate_retrieval.py --mode hybrid --dataset tests/fixtures/evaluation/generic_json_eval.jsonl --kb-id 1 --output artifacts/eval-hybrid.json
.venv/Scripts/python.exe scripts/evaluate_retrieval.py --mode reranked --dataset tests/fixtures/evaluation/generic_json_eval.jsonl --kb-id 1 --output artifacts/eval-reranked.json
```

Promote hybrid only if it meets Top-5 ≥90%, exact-term Recall@5 ≥95%, parent-child ≥95%, refusal ≥90%, and leakage 0. Enable a reranker model by default only if it improves or preserves every gate and improves at least one target metric over hybrid RRF.

- [ ] **Step 5: Record model/cost/latency results**

Compare the configured cloud candidates on the identical top-30 inputs. Report model name, provider, p50/p95 latency, errors, reranker fallback rate, token/request cost where available, and all quality metrics. Do not infer a winner from provider alignment with the embedding model.

- [ ] **Step 6: Run full product verification and commit**

Run from `rag-service/`: `.venv/Scripts/python.exe -m pytest -q`

Run from `web/`: `npm run test -- --run && npx vue-tsc --noEmit && npm run build`

Run from repository root: `git diff --check`

Expected: all backend/frontend tests pass, type check/build exit 0, quality gates are documented, and no failed staging run is visible.

```powershell
git add rag-service/src/evaluation/retrieval.py rag-service/scripts/evaluate_retrieval.py rag-service/tests/fixtures/evaluation/schema.json rag-service/tests/fixtures/evaluation/generic_json_eval.jsonl rag-service/tests/test_evaluation/test_retrieval.py docs/evaluation/generic-json-hybrid-baseline.md
git commit -m "test: gate generic JSON hybrid retrieval quality"
```
