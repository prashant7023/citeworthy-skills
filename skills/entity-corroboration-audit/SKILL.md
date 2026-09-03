---
name: entity-corroboration-audit
description: >-
  Check whether the wider web identifies and corroborates the brand as a distinct
  entity. On-site: missing sameAs anchors to authoritative profiles, self-
  descriptions that differ on every surface, inconsistent contact details,
  anonymous content with no author, and authority claims with no citable source.
  Off-site, via search probes: whether the brand's own domain answers its own
  defining question, whether the web describes it as the brand describes itself,
  name collisions with other entities, absence of independent coverage, and
  competitors owning its pricing and alternatives queries. Use when a brand is
  invisible, confused with a namesake, or misrepresented by AI assistants.
license: MIT
allowed-tools: Bash, Read, Write, WebSearch, WebFetch
---

# Entity & Corroboration Audit (pillar 4a: does the web agree?)

Stage 4b. Every other skill examines one domain talking about itself. This one covers
what a site cannot fix by editing itself: whether the wider web resolves the same
entity, describes it consistently, and corroborates its claims.

A fact asserted in exactly one place is fragile. The same fact repeated across
unrelated sources is what a model will confidently repeat back.

## When to use

- As steps 5–6 of `audit-orchestrator`.
- Standalone when assistants confuse the brand with a similarly-named company, describe
  it in terms the brand does not recognise, or cite competitors for its own questions.

## Inputs

`--workspace` (required), `--phase` (`onsite` | `merge` | `both`).

## Two-phase design, and why

Only half of this can be done offline, so the phases are split at that seam:

- **Phase 1 (`onsite`)** — deterministic, no network. Audits the identity signals the
  site controls, then writes `probe_plan.json`: six exact queries with a typed result
  schema.
- **Phase 2 (`merge`)** — consumes `probe_results.json`, written by the agent from real
  search results, and derives the corroboration findings.

This keeps the script deterministic and puts the agent's non-deterministic work behind a
typed contract, so the same probe results always yield the same findings. **If phase 2
never runs, the audit degrades honestly**: it reports the on-site half and records that
off-site corroboration was *not verified*, rather than inventing a verdict. Unevaluated
is never reported as passing.

## Procedure

### Phase 1 — on-site identity (no network)

1. Resolve the declared brand name from Organization markup, falling back to the domain.
2. **`ENTITY-001` identity anchors** — collect outbound links and `sameAs` entries pointing
   at recognised authorities (Wikipedia, Wikidata, LinkedIn, Crunchbase, GitHub, app
   stores, review platforms, business registries). Under two anchors fires: entity
   resolution works by triangulation, and a brand with no anchors exists only as a
   domain name.
3. **`ENTITY-002` description drift** — compare the homepage meta description,
   `og:description` and `Organization.description` by token overlap. Under 35% fires.
   Machines gain confidence from repetition; a brand described differently on every
   surface never accumulates enough support for any one description.
4. **`ENTITY-003` contact consistency** — more than three distinct phone numbers site-wide.
5. **`ENTITY-004` authorship** — under half of article pages name an author in text or
   markup. Anonymous content is weighted below attributed content.
6. **`ENTITY-005` unsupported claims** — superlatives and quantified claims ("award-winning",
   "trusted by 10,000+", "ISO certified") on pages linking to no external source.
7. Write `probe_plan.json`.

### Phase 2 — off-site probes (agent-executed)

8. The agent reads `probe_plan.json` and runs each query with **WebSearch**, recording
   results **verbatim** into `probe_results.json` per the plan's `result_schema`.
   **Record only what the results show.** Never fill a field from prior knowledge; leave
   it `null` if the search did not establish it. A fabricated probe result silently
   corrupts every finding derived from it.

   | Probe | Query | Establishes |
   |-------|-------|-------------|
   | P1 | `what is <brand>` | Does the brand own its own defining question, and does the returned description match its own? |
   | P2 | `<brand> reviews` | Is there any independent third-party discussion? |
   | P3 | `"<brand>"` | How many distinct entities share the name, and which dominates? |
   | P4 | `<brand> pricing` | Does the brand or a third party answer "what does it cost"? |
   | P5 | `<brand> alternatives` | Who owns the comparison conversation? |
   | P6 | `site:wikipedia.org OR site:wikidata.org <brand>` | Is the entity in the public knowledge graphs? |

9. Re-run with `--phase merge` to derive: `ENTITY-006` own domain absent for its own
   defining question (critical); `ENTITY-007` the web's description disagrees with the
   brand's own (misrepresentation, not invisibility); `ENTITY-008` name collision;
   `ENTITY-009` no independent sources; `ENTITY-010` no knowledge-graph entity (proactive);
   `ENTITY-011-pricing` / `ENTITY-011-alternatives` third parties owning commercial queries.

```bash
python scripts/probe_entity_consensus.py --workspace ./ws --phase onsite
# agent runs the probes, writes probe_results.json
python scripts/probe_entity_consensus.py --workspace ./ws --phase merge
```

## Evidence standard

On-site findings cite counts, the anchors found, and the competing description texts
side by side. Off-site findings cite the probe id, the exact query, and the domains
actually returned. Every off-site finding carries `evidence_source` naming its probe, so
a reader can re-run the query and check it.

## Severity

`critical` — the brand's own domain does not surface for its own defining question.
`high` — misrepresentation, name collision, no independent coverage, no anchors.
`medium` — description drift, anonymous content, unsupported claims.
`low` — contact inconsistency, knowledge-graph absence.

## Output

`findings/entity-corroboration-audit.json`, pillar `trust`, plus `probe_plan.json`.

## Guardrails

Search only. Never contact third parties, never edit an external profile, never attempt
to create a Wikipedia article (the report explicitly advises against it — it is a
conflict of interest and reliably backfires). All outputs are recommendations.

## References

- `references/corroboration-protocol.md` — probe definitions, recording rules, and the
  anti-fabrication contract
