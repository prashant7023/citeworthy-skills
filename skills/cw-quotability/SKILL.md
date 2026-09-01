---
name: cw-quotability
description: >-
  Check whether a page's prose contains sentences a machine can lift and quote as
  an answer. Detects a missing plain-language definition of what the brand is,
  slogan-only hero copy with no category, facts users constantly ask for (price,
  contact, location, founding, offering) that appear nowhere in text, walls of
  text with no subheadings, sections that say "we" and never name the brand,
  boilerplate outweighing content, inconsistent naming, unexpanded acronyms,
  numeric claims with no baseline, and generic anchor text. Use when a site is
  crawlable and marked up but assistants still cannot say what the brand does.
license: MIT
allowed-tools: Bash, Read
---

# Answer Extractability Audit (pillar 3b: quotable prose)

Stage 3b, and the half of extractability that markup cannot fix. A page can be fully
crawlable, fully server-rendered and carry perfect JSON-LD, and still never be cited —
because nowhere in its prose is there a sentence a machine can lift and present as an
answer.

## When to use

- As step 4 of `cw-audit-conductor`, alongside `cw-schema-truth`.
- Standalone when a brand is technically healthy but assistants describe it vaguely,
  generically, or in a competitor's terms.

## Inputs

`--workspace` (required) — an existing evidence bundle. No network I/O.

## The mental model: retrieval by chunk

A retriever splits a page into passages, embeds them, and returns *one passage*, which
is shown to the user largely on its own. So a passage must be:

- **self-contained** — names its subject rather than saying "we" / "our platform"
- **declarative** — states the fact rather than implying it through a slogan
- **bounded** — short enough and headed, so the chunk boundary lands cleanly
- **specific** — carries the number, unit, currency or date the question needs

Every check below measures exactly one of those four properties. That is why this skill
is separate from `cw-schema-truth`: the failure modes are different, and so are
the fixes (rewrite the copy vs. add markup).

## Procedure

1. Load the bundle; prefer the rendered view where one exists.
2. **Definition** (`QUOTE-001`, critical) — scan the opening ~25 sentences of the homepage
   and About page for a sentence that both names the brand and defines it
   ("X is a…", "X provides…"). Its absence means that when a user asks "what is X?",
   there is nothing to quote, so the answer is improvised or taken from a directory.
3. **Hero concreteness** (`QUOTE-002`) — fires only when the H1 matches a generic-slogan
   pattern **and** the first screen contains no defining verb. Both conditions are
   required, because a slogan above a clear definition is fine.
4. **Fact coverage** (`QUOTE-003`) — checks for pricing, contact, location, founding and
   offering as plain text. Each class is **gated by site type**, so a charity is never
   asked for pricing and a SaaS is never asked for opening hours.
5. **Chunkability** (`QUOTE-004`, `QUOTE-005`) — 600+ word pages with fewer than one
   subheading per ~500 words; paragraphs over 160 words. Without headings to cut on, a
   retriever splits mid-argument and the passage is discarded as low quality.
6. **Question shape** (`QUOTE-006`) — no question-phrased headings and no FAQ anywhere.
   Content already shaped as question-then-direct-answer matches a query closely and can
   be quoted with no reshaping.
7. **Attribution** (`QUOTE-007`) — the share of substantive sections that refer to the
   company only as "we"/"our" and never name it. Fires above 70% with at least 6 sections
   sampled. A passage saying "our platform cuts costs 40%" cannot be attributed once
   detached from the page, so it is dropped or credited to someone else.
8. **Signal-to-boilerplate** (`QUOTE-008`) — under 35% of a page's text inside `<main>`.
   Every chunk is then mostly menu labels identical across the whole site.
9. **Consistency and clarity** (`QUOTE-009` inconsistent naming, `QUOTE-010` unexpanded
   acronyms, `QUOTE-011` numeric claims with no baseline, `QUOTE-012` generic anchor text).
10. **Stated boundaries** (`QUOTE-013`) — product, pricing and docs pages that make three or
    more capability claims ("supports", "integrates with", "works with") while carrying no
    specification table and no explicit limits. Asked whether the product does something it
    does not, an assistant has nothing on the page to contradict a plausible guess, so it
    invents the capability and the brand inherits the support ticket.
11. **Comprehension judgment — you do this, not the script.** After the script finishes,
    read the homepage's `raw.first_screen_text` from the evidence bundle and apply
    [references/judgment-rubric.md](references/judgment-rubric.md): can a first-time reader
    say (a) what this is, (b) who it is for, (c) what to do next — quoting the exact text
    for each? The script matches definitional *sentence shapes*, which misses a bakery that
    opens "Every loaf we bake starts at 4am" and is fooled by "a leading provider of
    best-in-class solutions". Comprehension is the actual question and a language model is
    the right instrument for it. Emit any resulting finding as `QUOTE-001-J` with
    `"confidence": "medium"` and `"method": "agent-judgment"`, never overwriting the
    script's result — if you disagree with it, say so explicitly in the evidence. A
    judgment finding with no verbatim quote is inadmissible; drop it.
12. Write `findings/cw-quotability.json`.

```bash
python scripts/probe_quotability.py --workspace ./ws
```

## Evidence standard

Quoted sentences and headings verbatim, section counts and ratios, the H1 as it actually
reads, and the affected URLs. When a check passes, the found definitional sentence is
recorded in `notes` so a reader can confirm the check ran and what satisfied it.

## Severity

`critical` — no definitional sentence anywhere (the single highest-leverage defect in
this pillar). `high` — slogan-only hero, unattributed sections, missing fact classes.
`medium` — chunkability, boilerplate ratio. `low` — naming, acronyms, claims, anchors.

## Output

`findings/cw-quotability.json`, pillar `extract`.

## References

- `references/answer-shape.md` — the four properties, the checks, and rewrite patterns
  with before/after examples
- `references/judgment-rubric.md` — the one place the agent judges rather than measures,
  and the rules that keep that honest (quote-backed, `-J` suffixed, never overwriting)
