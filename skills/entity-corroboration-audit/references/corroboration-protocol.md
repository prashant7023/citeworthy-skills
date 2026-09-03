# Corroboration protocol

## The anti-fabrication contract

This is the only skill whose evidence comes from outside the evidence bundle, and therefore
the only one where an agent could invent a result. This contract exists to make that hard
to do by accident.

**Rules for the agent executing phase 2:**

1. Run each query in `probe_plan.json` with a real web-search tool.
2. Record what the results **actually show**, verbatim. Do not summarise, infer, or
   reconcile results against what you already believe about the brand.
3. If a search does not establish a field, leave it `null`. Never guess.
4. Do not substitute prior knowledge for a search. A model's recollection of a brand is
   *exactly the thing being measured* — using it as input makes the measurement circular
   and the finding worthless.
5. Record `top_domains` in the order returned.

A fabricated probe result silently corrupts every finding derived from it, and those
findings are among the most severe the marketplace can emit (`ENTITY-006` is critical). If
phase 2 cannot be run properly, **do not run it**. The audit degrades honestly by reporting
the on-site half and listing off-site corroboration under `limitations`. Unevaluated is
never reported as passing.

## The probes

| ID | Query | Establishes | Findings derived |
|---|---|---|---|
| P1 | `what is <brand>` | Does the brand's own domain surface for its own defining question, and does the returned description match its own positioning? | `ENTITY-006`, `ENTITY-007` |
| P2 | `<brand> reviews` | Is there independent third-party discussion? | `ENTITY-009` |
| P3 | `"<brand>"` | How many distinct entities share the name; which dominates? | `ENTITY-008` |
| P4 | `<brand> pricing` | Does the brand or a third party answer "what does it cost"? | `ENTITY-011-pricing` |
| P5 | `<brand> alternatives` | Who owns the comparison conversation? | `ENTITY-011-alternatives` |
| P6 | `site:wikipedia.org OR site:wikidata.org <brand>` | Is the entity in the public knowledge graphs? | `ENTITY-010` |

Six is a deliberate ceiling. It covers the four question shapes users actually ask about a
brand — what is it, is it any good, what does it cost, what else is there — plus identity
resolution, while staying inside the audit's time budget and not hammering a search backend.

## Result schema

```json
{"probes": [{
  "id": "P1",
  "query": "what is acme",
  "top_domains": ["g2.com", "linkedin.com", "acme.com"],
  "own_domain_present": true,
  "returned_description": "verbatim text from the result snippet",
  "distinct_entities_observed": 2,
  "dominant_entity": "Acme Corp (the cartoon company)",
  "independent_sources_count": 4,
  "competitors_named": ["Foo", "Bar"],
  "own_entity_present": false,
  "entity_url": null,
  "notes": "verbatim observations only"
}]}
```

Only the fields listed in each probe's `record` array are required for that probe; the rest
may be omitted or left `null`.

## Interpreting the findings

**`ENTITY-006` (own domain absent for its own defining question) is critical** because it
means the brand has no control over its own definition. Whatever a directory, a competitor
or an aggregator says is what an assistant repeats.

**`ENTITY-007` is misrepresentation, not invisibility** — a different and often more urgent
problem. The brand *is* being described; it is being described wrongly, and confidently. The
fix is not more content in general but the *same* description repeated across every surface
until it dominates, plus correction requests to the highest-ranking wrong sources.

**`ENTITY-008` (name collision)** is graded by whether the brand's own domain appears at all.
Sharing a name with a larger entity is survivable if the brand still surfaces; it is severe
if it does not. Note that a generic dictionary-word brand name will always show collisions —
the finding's remediation is about adding disambiguating signals, not about renaming.

**`ENTITY-009` (no independent coverage)** is the slowest finding to fix and the highest
leverage. Corroboration cannot be bought at scale; it is earned by publishing something
worth citing. The remediation is deliberately ordered from fastest (get listed on the review
platforms your category is already evaluated on) to slowest (publish original data others
have a reason to reference).

## On-site checks (phase 1) and their guards

| Check | Fires when | Guard |
|---|---|---|
| `ENTITY-001` | Weighted EntityScore below 60 (see below) | Grades the weight of evidence rather than counting links, so a single-location business whose Google Business Profile is the right anchor is not failed for having one. |
| `ENTITY-002` | Homepage self-descriptions overlap under 35% by token Jaccard | Requires 2+ distinct descriptions to compare; a site with one consistent description never fires. |
| `ENTITY-003` | More than 3 distinct phone numbers site-wide | Normalises to digits, so formatting variants of one number do not count as different. |
| `ENTITY-004` | Under 50% of article pages name an author | Only runs when article pages exist; checks both visible bylines and markup. |
| `ENTITY-005` | 2+ superlative or quantified claims on pages linking to no external source | Requires the *absence* of any outbound external link on the page, so a claim next to its citation never fires. |

### The EntityScore (ENTITY-001)

Entity resolution is a weight of evidence, not a count of links. A rule like "fewer than
two profiles = fail" punishes a local business with one correct anchor and passes a startup
linking two profiles nobody corroborates. Signals are scored, and the finding shows its
arithmetic so a reader can check it:

| Signal | Points |
|---|---|
| Organization name declared in structured data | +25 |
| Knowledge-graph entry (Wikipedia, Wikidata) | +20 |
| Professional or registry profile (LinkedIn, Crunchbase, Companies House) | +20 |
| Independent review platform (G2, Capterra, Trustpilot) | +15 |
| Verified local listing (Google Business Profile) | +15 |
| Developer or app-store presence (GitHub, App Store, Play) | +10 |
| Social profiles | +10 |

Bands: **under 40** fires `high` — too thin for resolution to succeed. **40–59** fires `low`
at `medium` confidence — resolvable, but with no redundancy if the one anchor goes stale.
**60+** raises nothing and records the score in `notes`.

Name-collision risk is deliberately **not** guessed here. A hardcoded list of dictionary-word
brand names can only contain names its author thought of, so it fails on exactly the unseen
sites that matter. Collision is measured instead by `ENTITY-008` from probe P3; where the
probes did not run, the breakdown says so rather than inventing a penalty.

## What this skill will not recommend

Writing your own Wikipedia article. It is a conflict of interest, it reliably backfires, and
the entry is usually deleted. The report says this explicitly and points instead to Wikidata
— a far lower bar, directly consumed by entity-resolution systems — but only where
independent sourcing genuinely exists to support an entry. Where it does not, the
recommendation is to build that coverage first (`ENTITY-009`) rather than to fake the
appearance of notability.
