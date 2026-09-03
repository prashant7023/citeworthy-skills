---
name: freshness-audit
description: >-
  Check whether a site's facts are current and whether a machine can tell. Detects
  time-sensitive pages with no date in text, markup or meta tags; content whose
  newest date is a year or more old; bulk staleness; stale copyright years; visible
  dates that contradict structured-data dates; sitemap lastmod values that are all
  identical (build timestamps masquerading as freshness); no changelog or news
  surface; and pages presenting a past year as current. Use when a brand is
  described with outdated facts, or loses recency-sensitive questions.
license: MIT
allowed-tools: Bash, Read
---

# Content Freshness Audit (pillar 4b: is it still true?)

Stage 4a. Retrieval systems weight recency heavily, because a confidently stated stale
fact is the failure mode users punish hardest.

## When to use

- As step 5 of `audit-orchestrator`.
- Standalone when assistants quote superseded prices, discontinued products or old
  version numbers, or when a site loses "current"/"latest"/"best 2026" questions.

## Inputs

`--workspace` (required), `--now` (optional ISO date, so tests are deterministic).
No network I/O.

## Three distinct problems, commonly confused

1. **Undatable** — the content may be perfectly current, but nothing says so. A
   recency-weighted retriever cannot prefer it and defaults to treating it as older than
   dated competitors. The fix is a date, not a rewrite.
2. **Stale** — the content demonstrably describes a world that has moved on. The fix is
   an edit or a retirement.
3. **Dishonest freshness** — every sitemap `lastmod` set to today, a `dateModified` that
   bumps on every deploy. Once detected, the signal is discounted, which devalues the
   honest signals alongside it. The fix is to stop emitting it.

Separating these matters because the remedy differs completely, and treating (1) as (2)
sends a team to rewrite content that was already correct.

## Procedure

1. Load the bundle. Extract dates from three independent sources per page: visible prose
   (ISO, "March 4, 2026", "4 March 2026"), structured data
   (`datePublished`/`dateModified`/`uploadDate`/`datePosted`), and meta/OpenGraph tags.
2. **`TIME-001` undated** — pages that are articles, docs, pricing or product pages, *or*
   whose text makes an explicit currency claim ("latest", "current", "now"), carrying no
   date in any of the three sources.
3. **`TIME-002` / `TIME-003` stale** — newest date anywhere 12+ months old (24+ is `high`);
   most dated pages 24+ months old.
4. **`TIME-004` stale copyright** — a footer year more than one year behind. Small, but it
   appears on every page and is a first-glance abandonment cue for humans and machines.
5. **`TIME-005` contradictions** — `dateModified` earlier than `datePublished`, or a
   visible date disagreeing with structured data by 12+ months. When the two disagree, a
   consumer discounts both, so a genuinely current page loses the benefit of being current.
6. **`TIME-006` fake freshness** — over 90% of sitemap `lastmod` values on one day.
7. **`TIME-007` no updating surface** — no blog, news, changelog, releases or press
   section anywhere, and none linked. Nothing changes, so crawlers return rarely and
   there is no dateable proof the business is active.
8. **`TIME-008` year-anchored** — present-tense currency language together with a year at
   least two years past in the title or a heading.
9. Write `findings/freshness-audit.json`.

```bash
python scripts/probe_time_decay.py --workspace ./ws
```

## Evidence standard

The actual dates found, their sources, the computed age in months, and the affected URLs.
Ages are computed against a single `now` captured once per run, so a report is
reproducible from a saved bundle by passing `--now`.

## Severity

`high` — the newest content anywhere is 24+ months old. `medium` — undated
time-sensitive pages, bulk staleness, date contradictions, no updating surface,
year-anchored pages. `low` — stale copyright, fake sitemap freshness. Escalated by
prevalence.

## Output

`findings/freshness-audit.json`, pillar `trust`.

## References

- `references/freshness-rules.md` — thresholds, date-source precedence, and the
  distinct fix for each of the three problem classes
