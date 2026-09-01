# Render checks (READ-*)

| Check | Fires when | Severity | Guard against false positives |
|---|---|---|---|
| `READ-001` | `raw_words / rendered_words < 0.60` | high; critical below 0.25 | Requires a renderer AND 120+ rendered words. The ratio is meaningless on a thin page, so thin pages are skipped rather than flagged. |
| `READ-002` | No renderer, under 150 words of server text, plus an empty app shell or a framework bundle | high, `confidence: medium` | Only ever fires in heuristic mode; carries an explicit `verification` command. Never claims a measured gap. |
| `READ-003` | Title, H1, JSON-LD or price present in the rendered DOM but absent from raw HTML | high | Requires a renderer, so both sides are observed. |
| `READ-004` | Non-tracking iframe on a page with under 400 words of its own text | medium | Excludes an explicit vendor list: GTM, Analytics, DoubleClick, Facebook pixel, Hotjar, Intercom, Drift, reCAPTCHA, Cookiebot, OneTrust. |
| `READ-005` | Content-named image (pricing/spec/menu/chart/comparison) with alt under 25 chars, on a page under 350 words | high | Both conditions required. A text-rich page with unlabelled decorative images is a different defect (`STAY-012`), not this one. |
| `READ-006` | Links to PDF/Office documents | medium at 3+, else low | Reports the linking page and anchor text so the value of each can be judged. |
| `READ-007` | Video embed, under 500 words, no transcript or caption marker | medium | All three conditions required. |
| `READ-008` | 3+ scripts, under 150 words, no meaningful `<noscript>` | low, `confidence: low` | Only in heuristic mode. Framed as a symptom of `READ-001`/`READ-002`, and the remediation says so — a `<noscript>` block is a backstop, never a fix for client-side rendering. |
| `READ-009` | No non-empty `<h1>` | medium, escalating | — |
| `READ-010` | No `<main>`/`<article>` on 60%+ of pages | medium | Requires 2+ pages. |
| `READ-011` | Heading levels skip on 40%+ of pages | low | Requires 2+ pages. Only flags forward skips (h2 to h4), never decreases. |
| `READ-012` | URL contains a `#!/` or `#/` route | high | — |

## Why heuristic mode is deliberately conservative

The temptation with no renderer is to assume a low word count means client-side rendering.
That is wrong often enough to matter: brochure pages, image galleries, redirect stubs and
landing pages are all legitimately short.

So `READ-002` requires a *second, independent* signal — an empty framework mount point or a
framework bundle — before firing, and even then reports at reduced confidence with the
verification command attached.

The orchestrator then propagates that uncertainty across the whole marketplace: when this
check fires in heuristic mode, every content-dependent finding from every skill is demoted
and labelled, because they were all computed from the same possibly-incomplete HTML. See
`composition-rules.md`, rule 2. This is the single most important false-positive control in
the system, and it works precisely because this skill reports its own limits honestly
instead of guessing a number.

## Installing a renderer

```bash
pip install playwright && playwright install chromium
```

With a renderer available: `READ-001` and `READ-003` measure the gap directly, `READ-002` and
`READ-008` never fire, and every content check across the marketplace runs against the
rendered DOM at full confidence.

## The distinction that drives every fix here

Content can be *present* in three increasingly bad ways:

1. **In the server HTML** — readable by everything. This is the target.
2. **In the rendered DOM only** — readable by renderers, invisible to plain fetchers. Some
   retrieval agents render, several do not, and none guarantee it.
3. **In a non-text format** (image, video, PDF-as-scan, iframe) — invisible to everything,
   including renderers.

`READ-001`/`READ-002`/`READ-003` address the second case; the fix is SSR or pre-rendering.
`READ-004` through `READ-007` address the third; the fix is republishing the fact as text.
Confusing the two sends a team to fix rendering when the actual problem is a price list
saved as a PNG.
