---
name: audit-orchestrator
description: >-
  Evaluate or audit a website for brand visibility in AI assistants. Answers "evaluate
  this website", "audit this site", "why doesn't ChatGPT/Claude/Perplexity mention us",
  "why does it describe us wrong", "is our site ready to be cited", and "people find us
  but don't convert". Diagnoses both halves of the problem: off-site discoverability
  (crawlability, JS-render gaps, missing or contradictory structured data, facts locked
  in non-text, stale or uncorroborated claims, entity ambiguity) and on-site engagement
  (cold arrivals on inner pages, no orientation, no next step). Composes seven focused
  sub-skills into one report of evidence-backed findings, each with a severity and a
  prioritized fix. Use for any brand-visibility, AI-discoverability, GEO, AEO, "AI SEO"
  or LLM-citability review of a site. Recommend-only: never modifies the site.
license: MIT
allowed-tools: Bash, Read, Write, Glob, Grep, WebSearch, WebFetch
---

# Brand AI-Readiness Audit — Orchestrator (entrypoint)

The entrypoint of the `brand-ai-readiness-audit` marketplace. It receives the audit
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
| 1 | Access | Can a crawler reach the page at all? | `crawl-access-audit` |
| 2 | Parse | Can a machine read what a human sees? | `render-extraction-audit` |
| 3 | Extract | Can a machine lift a specific fact and quote it? | `structured-data-audit`, `answer-extractability-audit` |
| 4 | Trust | Does the wider web corroborate that fact? | `entity-corroboration-audit`, `freshness-audit` |
| 5 | Engage | Does the arriving visitor stay? | `engagement-audit` |

Failure at any stage makes the stages after it irrelevant — a perfectly written page
behind a `Disallow: /` is invisible. **This ordering is the composition logic**: it
decides what gates what, and how findings are ranked.

## Inputs

| Input | Required | Default | Notes |
|-------|----------|---------|-------|
| `url` | yes | — | Domain or URL, e.g. `example.com`. Redirects are resolved before auditing. |
| `max_pages` | no | 14 | Crawl breadth. The default is tuned so the whole audit, including your own thinking time, fits the five-minute budget. |
| `max_depth` | no | 3 | Clicks from the entry page. |
| `budget_seconds` | no | 90 | Network budget only. Your inference latency counts against the same five minutes, so leave headroom. |
| `render` | no | `auto` | `auto` uses a local renderer if one exists, else heuristics. Evaluation sandboxes have no renderer; see Step 2b. |
| `probe_offsite` | no | true | Whether to run the off-site corroboration search probes. |

## Procedure

You are invoked by a free-form request, not a command line. "Evaluate this website",
"why doesn't ChatGPT mention us", "audit example.com for AI visibility" all land here.
Extract the domain from whatever the user said and begin.

**Budget.** The whole audit must finish in five minutes *including your own thinking
time*. That makes agent turns the scarce resource, not CPU. Prefer Path A below: it is
one turn instead of eight.

### Step 1 — Scope and confirm

Resolve the domain. State the scope in one line before starting (pages, depth, and that
this is read-only). If the user named a specific worry ("our pricing never shows up"),
say which pillar will answer it.

### Step 2 — Gather evidence

Take **Path A** if you can run scripts. Fall back to **Path B** if you cannot — a
sandbox without a Python runtime, a script that errors, or a harness that offers only
web tools. Both paths produce the same report; Path A is faster and its numbers are
exact.

**Path A — bundled scripts (preferred, ~1 turn)**

```bash
python skills/audit-orchestrator/scripts/conduct_audit.py "<domain>"   --workspace "<ws>" --max-pages 14 --budget-seconds 90 --markdown
```

This crawls once, runs all six analyzers over the shared evidence bundle, composes the
report and validates it. Read `<ws>/report.json` and present it. If it exits non-zero,
read the error and switch to Path B rather than reporting nothing.

To run the stages individually (useful when debugging one pillar), each analyzer takes
`--workspace` and is listed in `references/finding-catalog.md`.

**Path B — your own web tools (no scripts available)**

Scripts are an accelerator, not the method. The checks are documented so you can apply
them yourself:

1. Fetch `https://<domain>/robots.txt`. Read it **to the last line** and apply
   `skills/crawl-access-audit/references/ai-crawler-reference.md` — which agents are blocked,
   and critically, whether any `User-agent` token is declared more than once (RFC 9309
   merges duplicate groups, so a blanket `Disallow: /` appended at the bottom silently
   overrides everything above it).
2. Fetch the homepage plus 8–12 internal pages, favouring one of each type: pricing,
   product, about, contact, docs, a blog post. Note each page's status, title, H1,
   visible text, and whether JSON-LD is present.
3. Work through each skill's `references/*-checks.md` table against what you fetched.
   Each row states the threshold and, just as importantly, the conditions under which
   the check must **not** fire.
4. Record findings in the schema below. Every finding needs a measured quantity — "7 of
   12 pages", not "several pages".

Path B is slower and its counts are sampled rather than exhaustive. Say so in
`limitations`; do not present sampled counts as complete.

### Step 3 — The one judgment call

Whichever path you took, read the homepage's first screen yourself and apply
`skills/answer-extractability-audit/references/judgment-rubric.md`: could a stranger say what this
brand is, who it is for, and what to do next? A script matches definitional *sentence
shapes* and is wrong in both directions; comprehension is the actual question and you
are the right instrument for it. Emit any finding as `QUOTE-001-J`, quote-backed, at
`medium` confidence, and never overwrite the script's own `QUOTE-001`.

### Step 4 — Off-site corroboration

Read `<ws>/probe_plan.json` (Path A) or take the six queries from
`skills/entity-corroboration-audit/references/corroboration-protocol.md` (Path B). Run each
with your web-search tool and record what the results **actually show**, verbatim.
Never fill a field from prior knowledge — your own recollection of the brand is the
thing being measured, so using it makes the finding circular. Leave anything the search
did not establish as `null`, then merge:

```bash
python skills/entity-corroboration-audit/scripts/probe_entity_consensus.py   --workspace "<ws>" --phase merge
```

If you skip this step, say so — those six checks are then *unevaluated*, which is not
the same as passing.

### Step 5 — Compose

Path A already did this. Otherwise apply the four cross-skill rules yourself, from
`references/composition-rules.md`:

- **Gating** — a critical access failure marks every downstream finding `blocked_by`,
  because those fixes cannot take effect until the gate is cleared.
- **Confounding** — if you could not execute JavaScript *and* the site looks
  client-rendered, demote content findings to `low` confidence and label them rather
  than asserting them. This is the primary false-positive control.
- **Linking** — group findings that share a root cause so one fix closes several.
- **Prioritising** — rank by `severity × confidence × pillar stage`.

Withhold the score entirely when the crawl read almost nothing: zero usable pages, a
majority of identical bodies, or fewer than three pages totalling under 300 words. Near-
zero findings from a failed read must never be presented as a clean bill of health.

### Step 6 — Validate, then deliver

```bash
python skills/audit-orchestrator/scripts/verify_report.py "<ws>/report.json"
```

Exit code must be 0. It enforces the schema *and* the evidence bar: every finding must
cite a measured quantity and a check id. It also rejects any fix that recommends keyword
stuffing, hidden text, cloaking, instructions aimed at AI models, stripping dates or caveats,
or invented reviews. Those tactics backfire, and agent-written judgment findings are where
they creep in. On Path B, apply the same bar by hand.

Lead your answer with the headline, the score (or the reason there is none), and the
first three fixes. Repeat anything in `limitations` — never let a check that did not run
read as a check that passed.


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
`summary.ai_readiness_score` and `pillar_scores`; per-finding `check_id`, `pillar`,
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
