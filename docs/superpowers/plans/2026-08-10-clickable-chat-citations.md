# Clickable Chat Citations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render valid source markers in chat answers as clickable buttons, normalize the two observed malformed marker shapes, and constrain new model answers to canonical `[n]` citations.

**Architecture:** The backend system prompt defines the canonical output contract. A focused frontend Markdown-it inline plugin converts only valid citation syntax in ordinary inline text into controlled button tokens; `ChatView` delegates clicks to the existing references drawer. Citation validity is always checked against the message's actual references array.

**Tech Stack:** Python 3, pytest, Vue 3, TypeScript, markdown-it 15, Vitest, Vite.

## Global Constraints

- Canonical citations are `[n]`; multiple citations are adjacent, for example `[1][2]`.
- Normalize only the observed malformed forms: `[1#` to `[1]` and `[1##2#` to `[1][2]`.
- Only 1-based numbers present in the current message's `references` array become buttons.
- Do not rewrite persisted historical message content.
- Do not transform fenced code, inline code, Markdown links, link destinations, or unrelated bracketed text.
- Generated button attributes must contain only locally computed integer indices and fixed strings.
- Do not change retrieval, reranking, context expansion, or reference API ordering.

---

## File Structure

- Create `web/src/utils/chatCitations.ts`: owns citation recognition, Markdown token generation, controlled HTML rendering, and delegated-click index validation.
- Create `web/src/utils/chatCitations.test.ts`: unit tests syntax normalization, Markdown exclusions, bounds checks, and delegated-click validation.
- Modify `web/src/views/ChatView.vue`: uses the renderer, delegates inline citation clicks, opens the existing drawer at the selected reference, and styles inline buttons.
- Modify `rag-service/src/engine/prompt.py`: defines the model-facing citation contract.
- Modify `rag-service/tests/test_engine/test_prompt.py`: locks the prompt contract and source numbering behavior.

### Task 1: Backend Citation Contract

**Files:**
- Modify: `rag-service/src/engine/prompt.py:7`
- Modify: `rag-service/tests/test_engine/test_prompt.py`

**Interfaces:**
- Consumes: sequential `<untrusted-source id="n">` values already produced by `build_messages`.
- Produces: `SYSTEM_PROMPT: str` requiring `[n]` and `[1][2]` output without `#` markers or nonexistent IDs.

- [ ] **Step 1: Write failing prompt-contract tests**

Add tests that assert the system message contains exact rules for `[n]`, `[1][2]`, valid source IDs, and forbidden `#`, while a two-source context still contains IDs `1` and `2` in order:

```python
def test_system_prompt_requires_canonical_citations(self) -> None:
    from src.engine.prompt import build_messages

    system = build_messages("question", [], [])[0]["content"]
    assert "[n]" in system
    assert "[1][2]" in system
    assert "不得使用 #" in system
    assert "来源 id" in system


def test_source_ids_match_reference_order(self) -> None:
    from src.engine.prompt import build_messages
    from src.retrieval.module import RetrievedChunk

    sources = [
        RetrievedChunk("c1", "first", 1, "a.txt", None, 0.9),
        RetrievedChunk("c2", "second", 2, "b.txt", None, 0.8),
    ]
    content = build_messages("question", [], sources)[-1]["content"]
    assert content.index('<untrusted-source id="1">') < content.index(
        '<untrusted-source id="2">'
    )
```

- [ ] **Step 2: Run the focused test and confirm RED**

Run: `python -m pytest tests/test_engine/test_prompt.py -q`

Working directory: `rag-service`

Expected: the canonical-citation test fails because `SYSTEM_PROMPT` does not yet contain these rules.

- [ ] **Step 3: Implement the prompt contract**

Replace the one-line prompt with a readable concatenated string containing these exact constraints:

```python
SYSTEM_PROMPT = (
    "你是一个知识库问答助手。只能根据提供的来源回答问题，无法确认的信息请明确告知用户。"
    "回答中的事实必须使用来源 id 标注，单个来源写作 [n]，多个来源连续写作 [1][2]。"
    "n 必须是当前提供的来源 id；不得使用 #、脚注定义或不存在的编号。"
)
```

- [ ] **Step 4: Run focused and engine tests**

Run: `python -m pytest tests/test_engine/test_prompt.py tests/test_engine/test_query.py -q`

Expected: all selected tests pass with zero failures.

- [ ] **Step 5: Commit Task 1**

```powershell
git add -- rag-service/src/engine/prompt.py rag-service/tests/test_engine/test_prompt.py
git commit -m "fix(chat): constrain source citation format"
```

### Task 2: Frontend Citation Renderer and Interaction

**Files:**
- Create: `web/src/utils/chatCitations.ts`
- Create: `web/src/utils/chatCitations.test.ts`
- Modify: `web/src/views/ChatView.vue:94-112,248-259,312-317,964-991`

**Interfaces:**
- Produces: `renderChatMarkdown(content: string, referenceCount: number): string`.
- Produces: `citationIndexFromClick(event: MouseEvent, referenceCount: number): number | null`.
- Consumes: `ChatMessageInfo.references`, existing `openRefs`, and existing drawer state.

- [ ] **Step 1: Write failing renderer and click-validation tests**

Create `chatCitations.test.ts` with assertions for:

```typescript
import { describe, expect, it } from 'vitest'
import { citationIndexFromClick, renderChatMarkdown } from './chatCitations'

describe('renderChatMarkdown', () => {
  it('renders canonical and malformed citations as bounded buttons', () => {
    const html = renderChatMarkdown('A[1] B[1][2] C[1# D[1##2#', 2)
    expect(html.match(/data-reference-index="0"/g)).toHaveLength(4)
    expect(html.match(/data-reference-index="1"/g)).toHaveLength(2)
    expect(html).not.toContain('[1#')
    expect(html).not.toContain('##')
  })

  it('keeps missing references and protected Markdown as text', () => {
    const html = renderChatMarkdown('`[1]` [link [1]](https://example.com/[1]) [3]', 2)
    expect(html).toContain('<code>[1]</code>')
    expect(html).toContain('href="https://example.com/%5B1%5D"')
    expect(html).toContain('[3]')
    expect(html).not.toContain('data-reference-index="2"')
  })

  it('does not transform fenced code', () => {
    expect(renderChatMarkdown('```text\\n[1]\\n```', 1)).not.toContain(
      'data-reference-index',
    )
  })
})

describe('citationIndexFromClick', () => {
  it('returns a validated zero-based index only for a contained citation button', () => {
    const root = document.createElement('div')
    root.innerHTML = '<button data-reference-index="1"><span>source</span></button>'
    const child = root.querySelector('span')!
    expect(
      citationIndexFromClick(
        { target: child, currentTarget: root } as unknown as MouseEvent,
        2,
      ),
    ).toBe(1)
    expect(
      citationIndexFromClick(
        { target: child, currentTarget: root } as unknown as MouseEvent,
        1,
      ),
    ).toBeNull()
  })
})
```

- [ ] **Step 2: Run the focused test and confirm RED**

Run: `npm test -- --run src/utils/chatCitations.test.ts`

Working directory: `web`

Expected: FAIL because `chatCitations.ts` does not exist.

- [ ] **Step 3: Implement the Markdown-it citation module**

Create one configured Markdown-it instance. Register an inline rule before `emphasis` that:

1. Returns `false` unless the current character is `[` and `state.linkLevel === 0`.
2. Matches canonical `^\\[(\\d+)\\]` or malformed `^\\[(\\d+(?:##\\d+)*)#\\]?`.
3. Splits malformed IDs on `##`, converts them to integers, and rejects the entire match unless every ID is between `1` and `env.referenceCount`.
4. Pushes one custom `chat_citation` token per ID with zero-based `token.meta.referenceIndex`.
5. Advances `state.pos` by the complete match length.

Render each custom token using only the validated integer:

```typescript
return `<button type="button" class="chat-view__inline-cite" data-reference-index="${index}" aria-label="查看资料来源 ${index + 1}">[${index + 1}]</button>`
```

Export the renderer:

```typescript
export function renderChatMarkdown(content: string, referenceCount: number): string {
  const safeCount = Number.isInteger(referenceCount) && referenceCount > 0 ? referenceCount : 0
  return md.render(content || '', { referenceCount: safeCount })
}
```

Export delegated-click validation. It must require `target` and `currentTarget` to be `Element`, use `closest('button[data-reference-index]')`, require containment in `currentTarget`, parse the integer, and return it only when `0 <= index < referenceCount`; otherwise return `null`.

- [ ] **Step 4: Run focused tests and confirm GREEN**

Run: `npm test -- --run src/utils/chatCitations.test.ts`

Expected: all citation utility tests pass with zero failures.

- [ ] **Step 5: Wire the renderer and click delegation into ChatView**

Import the two utilities and replace message rendering with:

```vue
<div
  v-else
  class="chat-view__text chat-view__text--md"
  v-html="renderChatMarkdown(msg.content, msg.references.length)"
  @click="handleCitationClick($event, msg)"
></div>
```

Keep streaming content as ordinary Markdown with zero references:

```vue
<div
  class="chat-view__text chat-view__text--md"
  v-html="renderChatMarkdown(streamingContent, 0)"
></div>
```

Add the delegated handler:

```typescript
function handleCitationClick(event: MouseEvent, msg: ChatMessageInfo) {
  const index = citationIndexFromClick(event, msg.references.length)
  if (index !== null) openRefs(msg, index)
}
```

Make the selected reference the first drawer item while preserving correct labels:

```typescript
function openRefs(msg: ChatMessageInfo, index: number) {
  activeRefs.value = msg.references.slice(index)
  activeRefStart.value = index
  activeRefMessageKey.value = `${msg.role}-${msg.content.slice(0, 20)}-${index}`
  drawerOpen.value = true
}
```

Remove the local Markdown-it import, instance, and `renderMd`. Add scoped `:deep(.chat-view__inline-cite)` styles matching the existing citation color, with inline padding, border, rounded background, pointer cursor, visible `:focus-visible` outline, and hover background. Do not remove the existing bottom source buttons.

- [ ] **Step 6: Run frontend tests and build**

Run: `npm test -- --run`

Expected: all frontend tests pass with zero failures.

Run: `npm run build`

Expected: `vue-tsc`/Vite build exits 0 and emits `dist` assets.

- [ ] **Step 7: Manually verify the live interaction**

With the existing frontend and backend running, open `http://localhost:5173/chat`, load an answer containing `[1]`, click the inline button, and verify the drawer opens with source `[1]` first. Verify malformed fixture text through the renderer unit test rather than modifying stored chat data.

- [ ] **Step 8: Commit Task 2**

```powershell
git add -- web/src/utils/chatCitations.ts web/src/utils/chatCitations.test.ts web/src/views/ChatView.vue
git commit -m "feat(chat): add clickable source citations"
```

### Task 3: Final Cross-Layer Verification

**Files:**
- Verify only; no planned source changes.

**Interfaces:**
- Consumes: Task 1 prompt contract and Task 2 bounded renderer.
- Produces: evidence that the full branch meets the approved design.

- [ ] **Step 1: Run all relevant backend tests**

Run: `python -m pytest tests/test_engine/test_prompt.py tests/test_engine/test_query.py -q`

Working directory: `rag-service`

Expected: zero failures.

- [ ] **Step 2: Run all frontend tests and production build**

Run: `npm test -- --run`

Working directory: `web`

Expected: zero failures.

Run: `npm run build`

Expected: exit code 0.

- [ ] **Step 3: Inspect the final diff**

Run: `git diff --check backup/pre-clickable-citations-20260810..HEAD`

Expected: no whitespace errors. Confirm the diff changes only prompt constraints, citation parsing/tests, and ChatView wiring/styles.

- [ ] **Step 4: Request whole-branch review**

Review against `docs/superpowers/specs/2026-08-10-clickable-chat-citations-design.md` and this plan. Any Critical or Important finding must be fixed and reverified before delivery.
