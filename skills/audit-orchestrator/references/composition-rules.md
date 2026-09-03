# Composition rules

Four rules the entrypoint applies when merging sub-skill findings. Each is a statement
about the *relationship between* findings, which is why none of them can live inside an
individual skill. This file is the argument that the decomposition is real rather than
cosmetic: these rules only exist because the work was split.

Implemented in `scripts/compose_report.py`.

---

## Rule 1 — Gating

**Trigger.** A `critical` finding with check id `REACH-001` (retrieval agents disallowed),
`REACH-008` (homepage fails for a bot user-agent) or `REACH-011` (no HTTPS).

**Effect.** Every finding outside the gate set receives `blocked_by: "<gate check id>"`,
and the report headline is replaced with the gate.

**Why.** These three failures make the site unreachable. The content findings are still
true and still worth fixing, but fixing them changes nothing while the gate is closed. A
report that lists "add FAQ schema" as item three when the site disallows `OAI-SearchBot`
has misled the reader about what to do on Monday morning. Naming the dependency is more
useful than reordering alone, because it explains *why* the order is what it is.

---

## Rule 2 — Confounding (the main false-positive control)

**Trigger.** No renderer was available (`manifest.render.available == false`) **and**
`READ-001` or `READ-002` fired at `high` or `critical` — meaning the site looks
client-rendered and we could not execute its JavaScript.

**Effect.** Every content-dependent finding (`QUOTE-*`, `MARK-*`, `TIME-*`, and the
content-reading `ENG-*`/`ENT-*` checks) is:
- demoted one severity step,
- set to `confidence: "low"`,
- given `confounded_by: "<RND check id>"`,
- given a `verification` field naming the exact command to confirm it.

**Why.** If the page's content is injected by JavaScript and our crawler could not run
JavaScript, then "no H1" and "no definitional sentence" may describe *our blind spot*
rather than the site. Two wrong responses are available and both are worse than this one:
asserting them as defects destroys the report's credibility when the client opens a
browser and sees the H1; dropping them silently hides real problems on the sites where
they are genuine. Retaining them, demoted and explicitly labelled, is the honest option.

The rule is self-limiting where a renderer exists: the gap is then measured rather than
inferred and the rule never fires. **Evaluation sandboxes generally have no renderer**, so
assume it will fire on any client-rendered site. Before accepting that, try fetching one
affected page with the harness's own web tool — those tools often return post-JavaScript
HTML, which turns an inferred gap into a measured one and lets the findings stand at full
confidence.

---

## Rule 3 — Linking root causes

**Trigger.** Two or more findings from a predefined group are present.

**Groups.**

| Root cause | Member checks |
|---|---|
| The brand has no single, quotable self-definition | `QUOTE-001`, `QUOTE-002`, `ENTITY-002`, `ENTITY-006`, `ENTITY-007`, `MARK-004` |
| Content exists but machines cannot read it | `READ-001`, `READ-002`, `READ-003`, `READ-005`, `READ-006`, `READ-007` |
| Pages do not orient a visitor arriving from an AI citation | `STAY-001`, `MARK-012`, `QUOTE-007`, `STAY-006` |
| Nothing on the site can be dated, so it is assumed stale | `TIME-001`, `TIME-002`, `TIME-005`, `TIME-007` |

**Effect.** A `root_causes` entry is emitted and each member gains `related_check_ids`.

**Why.** These groups deliberately cross skill boundaries. A missing definitional
sentence is simultaneously an extractability defect, a corroboration defect and an
engagement defect — three skills each correctly report their own symptom, and only the
entrypoint can see that one rewrite closes all three. Without this rule the decomposition
would produce a longer list; with it, the decomposition produces a *shorter* action list
than a monolithic skill would.

---

## Rule 4 — Prioritisation

**Formula.** `impact = severity_rank x confidence_weight x pillar_stage`

| Component | Values |
|---|---|
| `severity_rank` | critical 4, high 3, medium 2, low 1 |
| `confidence_weight` | high 1.0, medium 0.75, low 0.5 |
| `pillar_stage` | access 1.30, parse 1.20, extract 1.10, trust 1.00, engage 1.00 |

**Why the stage multiplier.** The pillars are causally ordered: a fix at an earlier stage
unblocks everything after it, so at equal severity an access defect is worth more than an
engagement defect. The multiplier is small (1.3 vs 1.0) so it breaks ties within a
severity band without ever floating a `low` access item above a `critical` engagement one.

**Why confidence is a multiplier and not a filter.** A `low`-confidence finding is still
information; it just should not outrank a measured one. Filtering would discard it.

Findings are then numbered `F-001…` in ranked order, and grouped into four remediation
phases (unblock → machine-readable → trust and retention → polish and proactive), with
each item appearing in exactly one phase.

---

## What composition does *not* do

- It never invents a finding that no skill produced.
- It never raises a severity. Escalation by prevalence happens inside each skill, where
  the page-level evidence lives; the entrypoint only ever demotes.
- It never marks an unevaluated check as passing. Anything not run appears in
  `limitations`.
