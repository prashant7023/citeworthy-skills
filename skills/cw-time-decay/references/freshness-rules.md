# Freshness rules (FRS-*)

## Date-source precedence

Three independent sources are read per page and compared:

1. **Structured data** — `datePublished`, `dateModified`, `uploadDate`, `datePosted`.
   Most reliable, because it is unambiguous and explicitly typed.
2. **Visible prose** — ISO (`2026-03-04`), long (`March 4, 2026`) and D-M-Y
   (`4 March 2026`) formats.
3. **Meta / OpenGraph** — `article:published_time`, `article:modified_time`, `date`.

A page passes `TIME-001` if *any* source yields a date. Disagreement of 12+ months between
sources is `TIME-005`: when the visible and machine-readable dates conflict, a consumer
cannot tell which to trust and discounts both, so a genuinely current page loses the
benefit of being current.

Dates in the future or before 2005 are discarded as parse noise rather than reported —
version numbers, phone numbers and address fragments all produce spurious matches
otherwise.

## Thresholds

| Check | Threshold | Severity |
|---|---|---|
| `TIME-001` undated time-sensitive page | No date in any of the three sources | medium, escalating |
| `TIME-002` newest content old | 12+ months | medium; 24+ months high |
| `TIME-003` bulk staleness | 50%+ of dated pages 24+ months old | medium |
| `TIME-004` stale copyright | Footer year below current year minus 1 | low, escalating |
| `TIME-005` contradictory dates | dateModified before datePublished, or 12+ month disagreement | medium |
| `TIME-006` fake freshness | Over 90% of sitemap lastmods on one day (10+ entries) | low |
| `TIME-007` no updating surface | No blog/news/changelog/releases/press page or link | medium |
| `TIME-008` year-anchored | Currency language plus a year 2+ years past in title or heading | medium, escalating |

## The three problem classes, and why separating them matters

The remedy differs completely in each case, and conflating them wastes real effort.

**Undatable** (`TIME-001`) — the content may be perfectly accurate. Nothing needs rewriting;
it needs a date. Sending a content team to rewrite correct pages because an audit said
"stale" is a concrete, avoidable cost of merging these two ideas.

**Stale** (`TIME-002`, `TIME-003`, `TIME-008`) — the content describes a world that has moved
on. This needs an edit, a consolidation, or a retirement with a redirect — and only then a
date bump.

**Dishonest** (`TIME-006`, and `TIME-005` where `dateModified` tracks deploys) — the site
emits freshness signals that do not correspond to content changes. Crawlers detect the
pattern and stop trusting the field, which devalues the honest signals alongside it. The
fix is to *stop emitting* the signal: an absent `lastmod` is better than a discredited one.

This last one is counter-intuitive enough that the remediation states it explicitly, because
the instinct on being told "your freshness signals are wrong" is to make them fire more
often, which makes the problem worse.

## Which pages count as time-sensitive

A page qualifies if it is classified `article`, `docs`, `pricing` or `product`, **or** its
text contains explicit currency language (`latest`, `current`, `now available`, `as of`,
`up-to-date`, `this year`).

Evergreen pages making no currency claim are not required to carry a date — though the
remediation suggests a `Reviewed <month year>` stamp anyway, since it makes currency
claimable at almost no cost and costs nothing when the content has not changed.

## Determinism

`--now` overrides the current date, so a report can be reproduced exactly from a saved
bundle. Without it, `now` is captured once at the start of the run and reused for every
comparison, so no two checks within a run can disagree about what day it is.
