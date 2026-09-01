---
name: cw-audit-conductor
description: >-
  Audit any website for the problems that stop AI assistants finding, reading,
  quoting and correctly describing a brand, and that stop visitors engaging once
  they arrive. Runs a bounded read-only crawl, then composes six focused
  sub-skills (crawl access, render parity, structured data, answer
  extractability, entity corroboration, freshness, engagement) into one report of
  evidence-backed findings with severity and prioritized fixes. Use when asked
  why a brand is invisible, stale, misrepresented or uncited in ChatGPT, Claude,
  Perplexity, Copilot or AI Overviews; why an AI-referred visitor bounces; or for
  any "AI SEO", "GEO", "AEO", "LLM visibility" or "AI readiness" review of a site.
license: MIT
allowed-tools: Bash, Read, Write, Glob, Grep, WebSearch, WebFetch
---

# Brand Citeworthy Audit — Orchestrator (entrypoint)

The entrypoint of the `citeworthy` marketplace. It receives the audit
request, runs the sub-skills, and emits the single audit report. It is the only
skill that writes the final report.

## When to use

Use when someone asks any of:

- "Why doesn't ChatGPT/Claude/Perplexity mention us?" or "…why does it describe us wrong?"
- "Audit this site for AI search visibility / GEO / AEO / LLM readiness."
- "People find us but don't convert — is the site the problem?"
- "Is our site ready to be cited as a source?"

Do **not** use for: keyword-rank tracking, paid-media audits, or full accessibility
conformance audits. This skill covers accessibility only where the same defect also
blocks machine extraction.

## Mental model (why these checks and not others)

A brand appears in an AI answer only if **five things succeed in order**. Each is a
pillar, each is one sub-skill, and each gates the next:

| # | Pillar | Question | Skill |
|---|--------|----------|-------|
| 1 | Access | Can a crawler reach the page at all? | `cw-reach-gate` |
| 2 | Parse | Can a machine read what a human sees? | `cw-render-gap` |
| 3 | Extract | Can a machine lift a specific fact and quote it? | `cw-schema-truth`, `cw-quotability` |
| 4 | Trust | Does the wider web corroborate that fact? | `cw-entity-consensus`, `cw-time-decay` |
| 5 | Engage | Does the arriving visitor stay? | `cw-arrival-experience` |

Failure at any stage makes the stages after it irrelevant — a perfectly written page
behind a `Disallow: /` is invisible. **This ordering is the composition logic**: it
decides what gates what, and how findings are ranked.

## Inputs

| Input | Required | Default | Notes |
|-------|----------|---------|-------|
| `url` | yes | — | Domain or URL, e.g. `example.com`. Redirects are resolved before auditing. |
| `max_pages` | no | 25 | Crawl breadth. 10–15 for a fast pass, 40 for a large site. |
| `max_depth` | no | 3 | Clicks from the entry page. |
| `budget_seconds` | no | 150 | Network budget. Total runtime stays under 5 minutes. |
| `render` | no | `auto` | `auto` uses Playwright when installed; `off` forces heuristic mode. |
| `probe_offsite` | no | true | Whether to run the off-site corroboration search probes. |

## Procedure

Run these steps in order. Steps 2–7 read only the evidence bundle written by step 1,
so the whole audit costs exactly **one** polite crawl.

1. **Preflight.** Normalise the URL. Create a workspace directory. Confirm the run is
   read-only: no authenticated areas, no forms submitted, no rate abuse, robots.txt
   respected. State the scope to the user (`N` pages, depth `D`, budget `B`s).

2. **Access + evidence bundle** — invoke `cw-reach-gate`:
   ```bash
   python skills/cw-reach-gate/scripts/harvest_site.py "<url>" \
     --workspace "<ws>" --max-pages 25 --max-depth 3 --budget-seconds 150 --render auto
   ```
   This writes `manifest.json`, `pages/*.json`, `pages/*.raw.html` and the `REACH-*`
   findings. **If this step fails, stop** — every later step depends on the bundle.

3. **Parse** — `python skills/cw-render-gap/scripts/probe_render_gap.py --workspace "<ws>"`

4. **Extract** — run both, they cover different layers (markup vs. prose):
   - `python skills/cw-schema-truth/scripts/probe_schema_truth.py --workspace "<ws>"`
   - `python skills/cw-quotability/scripts/probe_quotability.py --workspace "<ws>"`

   `cw-quotability` then asks you to make one judgment the scripts deliberately do not:
   read the homepage's first screen and decide whether a stranger could say what the
   brand is. Follow `skills/cw-quotability/references/judgment-rubric.md`. It is the only
   non-deterministic step in the pipeline, it is quote-backed and `-J` suffixed, and the
   audit is still valid if you skip it.

5. **Trust** —
   - `python skills/cw-time-decay/scripts/probe_time_decay.py --workspace "<ws>"`
   - `python skills/cw-entity-consensus/scripts/probe_entity_consensus.py --workspace "<ws>" --phase onsite`

6. **Off-site probes** (skip if `probe_offsite` is false, and say so in the report).
   Read `<ws>/probe_plan.json`. It contains 6 exact queries. For each, run **WebSearch**
   and record results **verbatim** into `<ws>/probe_results.json` using the
   `result_schema` in the plan. Record only what the results actually show — never fill
   a field from prior knowledge; leave it `null` if the search did not establish it.
   Then merge:
   `python skills/cw-entity-consensus/scripts/probe_entity_consensus.py --workspace "<ws>" --phase merge`

7. **Engage** — `python skills/cw-arrival-experience/scripts/probe_arrival.py --workspace "<ws>"`

8. **Compose the report** —
   ```bash
   python skills/cw-audit-conductor/scripts/compose_report.py \
     --workspace "<ws>" --markdown "<ws>/report.md"
   ```
   Composition applies four cross-skill rules that no single skill can apply alone —
   see `references/composition-rules.md`:
   - **Gating** — a critical access failure marks every downstream finding `blocked_by`,
     because those fixes cannot take effect until the gate is cleared.
   - **Confounding** — if the crawler could not run JavaScript *and* the site looks
     client-rendered, content findings are demoted to `low` confidence and flagged for
     browser verification rather than asserted as defects. This is the primary
     false-positive control.
   - **Linking** — findings sharing a root cause are grouped so one fix closes several.
   - **Prioritising** — ranked by `severity × confidence × pillar stage`, so
     earlier-stage fixes that unblock others rank first.

9. **Validate — fail closed.**
   `python skills/cw-audit-conductor/scripts/verify_report.py "<ws>/report.json"`
   Exit code must be 0. It enforces the required schema *and* the evidence bar (every
   finding must cite a measured quantity and a check id). If it fails, fix the cause;
   never emit an unvalidated report.

10. **Deliver.** Present `report.json` as the deliverable. Lead with the headline, the
    score, and the phase-1 fixes. Never claim a check passed that did not run — the
    report's `limitations` array says what was not verified, and that must be repeated
    to the user.

### Shortcut

For a non-interactive run of steps 2–9 in one command:

```bash
python skills/cw-audit-conductor/scripts/conduct_audit.py "<url>" \
  --workspace "<ws>" --max-pages 25 --markdown
```
Add `--merge-probes` after writing `probe_results.json`. The agent-driven path and this
runner execute the same scripts in the same order and produce identical findings.

## Output

A single JSON report at `<ws>/report.json` (plus an optional Markdown companion).
It always contains at least the required schema:

```json
{
  "site": "example.com",
  "audited_at": "2026-09-20T14:32:00Z",
  "summary": { "total_findings": 6, "critical": 1, "high": 2, "medium": 3 },
  "findings": [
    {
      "id": "F-001",
      "title": "No JSON-LD structured data on product pages",
      "severity": "high",
      "evidence": "Crawled 12 product pages; 0/12 contain schema.org markup.",
      "suggested_action": { "summary": "Add Product/Offer JSON-LD to every product page.",
                            "priority": "high" }
    }
  ]
}
```

Extensions beyond the floor, all documented in `references/report-schema.json`:
`summary.citeworthy_score` and `pillar_scores`; per-finding `check_id`, `pillar`,
`confidence`, `affected_urls`, `blocked_by`, `confounded_by`, `related_check_ids`,
`source_skill`, and `suggested_action.steps` / `effort` / `owner`; plus top-level
`scope`, `method`, `limitations`, `root_causes`, `proactive_recommendations` and
`remediation_plan`.

## Guardrails

- **Recommend-only.** Nothing in this marketplace modifies a live site. Every output is
  a recommendation.
- **Read-only.** GET requests only. Never submit a form, never authenticate, never
  follow login/checkout/account URLs (the crawler filters them by path and subdomain).
- **Polite.** robots.txt is obeyed for our own user-agent, requests are delayed and
  concurrency-capped, and the crawl is bounded by pages, depth and time.
- **Honest.** A check that did not run is reported as unevaluated, never as passing.

## References

- `references/composition-rules.md` — the cross-skill rules and why each exists
- `references/severity-model.md` — how severity, confidence and scores are assigned
- `references/finding-catalog.md` — every check id across all skills, in one table
- `references/report-schema.json` — the full output contract
