---
name: render-extraction-audit
description: >-
  Detect content that a human sees but a machine cannot read: text that only
  exists after JavaScript runs, near-empty app shells, titles/H1s/prices injected
  client-side, facts locked inside images, PDFs, video or third-party iframes,
  missing semantic landmarks, broken heading hierarchies and hash-based routes.
  Compares raw server HTML against a rendered view of the same page, using a local
  browser if one exists, otherwise the agent's own web-fetch tool, and degrading to
  explicitly-labelled heuristics only when neither is available. Use when a page
  looks fine in a browser but is missing or empty in AI answers and search results.
license: MIT
allowed-tools: Bash, Read, WebFetch
---

# Render Parity Audit (pillar 2: read)

Stage 2. A page a crawler can reach is still invisible if the crawler cannot read it.
This skill measures the gap between what a browser shows and what a non-executing
fetcher receives.

## When to use

- As step 3 of `audit-orchestrator`.
- Standalone when a page is fetched but assistants describe it as empty, quote only
  navigation, or get the title wrong.

## Inputs

`--workspace` (required) — an evidence bundle already produced by `crawl-access-audit`.
This skill performs **no network I/O**; it only reads what was already fetched.

## The confidence contract (false-positive control)

This skill refuses to guess.

There are three tiers, and the skill always says which one it used.

- **Local renderer available** — the raw-vs-rendered word-count delta is *measured*.
  Findings are `high` confidence and cite both numbers.
- **No local renderer, but you have a web-fetch tool** — use it. Most harness fetch
  tools return post-JavaScript HTML, which is exactly the second sample this check
  needs. Fetch the two or three thinnest pages the crawl found, compare word counts
  against `raw.word_count` in the bundle, and report at `high` confidence noting the
  fetch tool as the render source. **Evaluation sandboxes typically have no local
  browser, so this is the normal path, not an exception.**
- **Neither** — no delta is invented. The skill falls back to a conservative signature
  (an empty app-shell element and/or a framework bundle, **plus** under 150 words of
  server-rendered text) and reports at `medium`/`low` confidence with an explicit
  `verification` instruction.

Guessing here would be worse than admitting the limit, because every content check
downstream inherits the assumption. The orchestrator reads this signal and demotes
content findings that may be artefacts of our own blind spot.

## Procedure

1. Load the bundle. Operate only on 200-status HTML pages.
2. **Measured gap** (`READ-001`) — for each rendered page with 120+ rendered words,
   compute `raw_words / rendered_words`. Below 0.60 is `high`; below 0.25 is `critical`.
   Pages under 120 rendered words are skipped: the ratio is meaningless on a thin page,
   and firing there is a classic false positive.
3. **Heuristic shell** (`READ-002`) — only when no renderer ran. Requires an empty
   `<div id="root|app|__next|__nuxt">` **or** a framework bundle, together with under
   150 words of server text.
4. **Identity injection** (`READ-003`) — title, H1, JSON-LD or price present in the
   rendered DOM but absent from raw HTML. These fields decide what a page *is*, so
   supplying them late gets the page indexed as untitled and unattributed.
5. **Iframed content** (`READ-004`) — a non-tracking iframe on a page holding under 400
   words of its own text. Content in an iframe is a separate document attributed to the
   embedded origin, so the host page earns no citation for it. Tracking, consent and
   chat frames are excluded by an explicit vendor list.
6. **Text as images** (`READ-005`) — images whose filenames indicate they carry content
   (pricing, spec, menu, comparison, chart) with alt text under 25 characters, on a page
   under 350 words. Both conditions are required: a text-rich page with unlabelled
   decorative images is not this defect.
7. **Documents and video** (`READ-006`, `READ-007`) — substantive facts distributed as
   PDF/Office downloads; video embeds on text-poor pages with no transcript marker.
8. **Fallbacks and structure** — `READ-008` no meaningful `<noscript>` on script-dependent
   pages (reported `low`, and framed as a symptom rather than the fix); `READ-009` no H1;
   `READ-010` no `<main>`/`<article>` landmarks, so boilerplate contaminates every chunk;
   `READ-011` skipped heading levels, which breaks the tree chunkers cut on.
9. **Hash routing** (`READ-012`) — fragment routes are never sent to the server, so every
   such view collapses to one document for a crawler.
10. Write `findings/render-extraction-audit.json`.

```bash
python scripts/probe_render_gap.py --workspace ./ws
```

## Evidence standard

Word counts on both sides, the affected URL, the specific missing element, and — in
heuristic mode — the reason no renderer was available plus the command to verify.

## Severity

`critical` under 25% server-rendered content; `high` under 60%, or identity fields
injected client-side; `medium` content in iframes, images or documents; `low` structural
and fallback issues. Then escalated one step at 50%+ prevalence or a homepage hit, and
demoted one step at 15% or less.

## Output

`findings/render-extraction-audit.json`, pillar `parse`. Findings carry `confidence` and,
in heuristic mode, a `verification` field naming the exact command to confirm them.

## References

- `references/render-checks.md` — thresholds, guard conditions and the fix for each check
