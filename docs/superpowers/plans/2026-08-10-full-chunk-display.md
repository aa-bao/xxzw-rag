# Full Chunk Display Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove multi-line truncation from the three confirmed chunk-content views so each bounded chunk is displayed in full by default.

**Architecture:** This is a CSS-only presentation change. Each Vue view already receives and renders the full `chunk.content`; removing the four WebKit clamp declarations lets the existing card or drawer layout grow naturally while preserving whitespace and word wrapping.

**Tech Stack:** Vue 3 single-file components, scoped CSS, TypeScript, Vite

## Global Constraints

- Modify only the three confirmed chunk-body selectors.
- Preserve `white-space: pre-wrap` and `word-break: break-word`.
- Keep truncation for titles, knowledge-base names, filenames, source labels, and chat references.
- Do not modify backend code, API types, data, pagination, or add expand/collapse state.

---

### Task 1: Show complete chunk bodies in all confirmed views

**Files:**
- Modify: `web/src/views/KbChunksView.vue:415-424`
- Modify: `web/src/views/KbDocsView.vue:1579-1588`
- Modify: `web/src/views/KbTestingView.vue:468-477`

**Interfaces:**
- Consumes: existing `chunk.content` and retrieval result content rendered by each template.
- Produces: naturally sized chunk-body elements with preserved whitespace and word wrapping.

- [ ] **Step 1: Run a source-level regression check and confirm RED**

Run from `web/`:

```powershell
@'
const fs = require('node:fs');
const checks = [
  ['src/views/KbChunksView.vue', '.chunk-card__content'],
  ['src/views/KbDocsView.vue', '.docs-drawer__chunk-content'],
  ['src/views/KbTestingView.vue', '.result-card__snippet'],
];
for (const [file, selector] of checks) {
  const source = fs.readFileSync(file, 'utf8');
  const start = source.indexOf(selector);
  const end = source.indexOf('\n}', start);
  const block = source.slice(start, end);
  if (/line-clamp|display:\s*-webkit-box|overflow:\s*hidden/.test(block)) {
    throw new Error(`${file} still truncates ${selector}`);
  }
  if (!/white-space:\s*pre-wrap/.test(block) || !/word-break:\s*break-word/.test(block)) {
    throw new Error(`${file} lost wrapping rules for ${selector}`);
  }
}
'@ | node
```

Expected: command exits non-zero and reports that `.chunk-card__content` still truncates content.

- [ ] **Step 2: Remove only the multi-line clamp declarations**

In each of the three selectors, remove:

```css
display: -webkit-box;
-webkit-line-clamp: 4; /* existing value is 4, 6, or 5 */
-webkit-box-orient: vertical;
overflow: hidden;
```

Keep each selector's existing typography plus:

```css
word-break: break-word;
white-space: pre-wrap;
```

- [ ] **Step 3: Re-run the source-level regression check and confirm GREEN**

Run the exact PowerShell/Node command from Step 1.

Expected: exit code 0 with no output.

- [ ] **Step 4: Run frontend type and production build verification**

Run from `web/`:

```powershell
npx vue-tsc --noEmit
npm run build
```

Expected: both commands exit 0. Existing bundle-size warnings are acceptable.

- [ ] **Step 5: Verify the running UI**

Open the existing local frontend and inspect a chunk longer than six lines in:

- the dedicated knowledge-chunk list;
- the document preview drawer's chunk tab;
- retrieval testing results.

Expected: all three show the complete body, preserve embedded newlines, grow vertically, and retain working page/drawer scrolling. Titles and source labels remain single-line truncated.

- [ ] **Step 6: Commit**

```powershell
git add web/src/views/KbChunksView.vue web/src/views/KbDocsView.vue web/src/views/KbTestingView.vue
git commit -m "fix: show complete knowledge chunks"
```

