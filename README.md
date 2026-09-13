# brand-ai-readiness-audit

An Agent Skill Marketplace that audits why AI assistants fail to find, read, quote or
correctly describe a brand — and why the visitors who do arrive don't stay. It returns
one report: findings with evidence and severity, plus prioritized fixes.

Recommend-only. Read-only. Nothing here ever modifies a live site.

## How it is invoked

Install the marketplace in an agent harness and ask in plain language — *"evaluate this
website"*, *"why doesn't ChatGPT mention us"*, *"audit example.com for AI visibility"*.
The entrypoint `audit-orchestrator` picks the request up and returns the report. There is
no interface to stand up and no server to connect to.

Scripts are an accelerator, not the method. Where a Python runtime exists the entrypoint
runs one bundled command — one agent turn instead of eight, which matters because the
five-minute budget includes inference latency. Where it does not, the same audit runs
from the skills' own reference tables using the harness's web tools. Both paths produce
the same report; the scripted one is faster and its counts are exhaustive rather than
sampled.

A local browser is not assumed. Without one, `render-extraction-audit` takes its second
sample from the agent's web-fetch tool, which usually returns post-JavaScript HTML —
exactly what the raw-vs-rendered comparison needs. Only if neither exists does it fall
back to a heuristic, and it labels those findings as heuristic rather than asserting them.

## The design

A brand reaches an AI answer only if five things succeed **in order**:

```
ACCESS  ──▶  PARSE  ──▶  EXTRACT  ──▶  TRUST  ──▶  ENGAGE
can a bot    can a       can it lift   does the    does the
reach it?    machine     a specific    wider web   visitor
             read it?    fact?         agree?      stay?
```

Failure at any stage makes the later stages irrelevant: a well-written page behind
`Disallow: /` is invisible, and a crawlable page whose only pricing lives in a PNG cannot
be quoted. That ordering is the decomposition, the composition logic and the
prioritization rule at once — it is not a taxonomy applied after the fact.

## The skills

| Skill | Pillar | What it does |
|---|---|---|
| **`audit-orchestrator`** | — | **Entrypoint.** Runs the others in causal order, applies the four cross-skill rules below, emits and validates the single report. |
| `crawl-access-audit` | Access | Checks whether crawlers can reach the site: robots.txt per-agent verdicts, whether the CDN actually honours them, status codes, sitemaps, canonicals, snippet directives. Also performs the one crawl and writes the shared evidence bundle. |
| `render-extraction-audit` | Parse | Compares raw server HTML against a rendered view to find content that only exists after JavaScript, plus facts locked in images, PDFs, video and iframes. |
| `structured-data-audit` | Extract | Checks schema.org markup for presence, validity, and — the part most audits skip — agreement with what the page visibly says. |
| `answer-extractability-audit` | Extract | Checks whether the prose contains sentences a retriever can lift verbatim: self-contained, declarative, bounded, specific. |
| `entity-corroboration-audit` | Trust | Audits on-site identity anchors, then probes search to test whether the wider web resolves and corroborates the same brand. |
| `freshness-audit` | Trust | Separates content that is undatable from content that is stale from freshness signals that are dishonest. |
| `engagement-audit` | Engage | Judges the site as an AI-referred visitor meets it: cold arrival on an inner page, specific intent, no session history. |

**94 checks** across the seven analyzer skills. Full table with thresholds and guards:
`skills/audit-orchestrator/references/finding-catalog.md`.

## How the entrypoint composes them

Seven analyzers producing seven lists would be a pile. The entrypoint adds four things
that are statements about the *relationship between* findings, which is why they cannot
live inside any individual skill:

1. **Gating.** A critical access failure marks every downstream finding `blocked_by`.
   Those fixes are still correct; they just cannot take effect while the gate is closed.
2. **Confounding** — the main false-positive control. If the crawler could not run
   JavaScript *and* the site looks client-rendered, "no H1" may describe our blind spot
   rather than the site. Such findings are kept but demoted to `low` confidence and
   labelled. Asserting them costs credibility; dropping them hides real defects.
3. **Linking.** A missing definitional sentence is at once an extractability, a
   corroboration and an engagement defect. Grouping them means the decomposition yields a
   *shorter* action list than a monolith would.
4. **Prioritizing.** `severity × confidence × pillar stage`, so fixes that unblock others
   rank first. Findings are then numbered and sorted into four remediation phases.

The entrypoint also **withholds the score** when the crawl read too little to justify one
— nothing fetched, near-identical pages, or fewer than three pages under 300 words total.
Near-zero findings from a failed read must never read as a clean bill of health.

Rationale and worked examples: `skills/audit-orchestrator/references/composition-rules.md`.

## Architecture

`crawl-access-audit` performs the **only** network I/O and writes an evidence bundle;
every other skill is a pure function of that bundle. One crawl serves eight skills, so the
audit is deterministic, fast, polite to the site, and re-checkable — the raw HTML stays on
disk, so any evidence claim can be verified after the fact.

```
<workspace>/
  manifest.json          crawl metadata, robots analysis, per-agent verdicts, page index
  pages/<id>.json        normalised parsed page (raw + rendered views)
  pages/<id>.raw.html    verbatim server HTML
  findings/<skill>.json
  report.json            the deliverable
  report.md              human-readable companion
```

## Output

`report.json` always contains the required schema — `site`, `audited_at`, a
counts-by-severity `summary`, and `findings[]` each with `id`, `title`, `severity`,
`evidence` and `suggested_action`. It extends that with `check_id`, `pillar`,
`confidence`, `affected_urls`, `blocked_by`, `confounded_by`, remediation `steps`,
`effort` and `owner`, plus top-level `scope`, `method`, `limitations`, `root_causes`,
`proactive_recommendations` and a phased `remediation_plan`.

Full contract: `skills/audit-orchestrator/references/report-schema.json`.

Three commitments the validator enforces. **Evidence or it doesn't ship** —
`verify_report.py` fails closed unless every finding cites a measured quantity and a
traceable check id. **No advice that backfires** — a report is rejected if any fix
recommends keyword stuffing, hidden text, cloaking, instructions aimed at AI models,
stripping dates or caveats, or invented reviews. **Unevaluated is never "passing"** — each skill records the checks it
ran whether or not they fired, so the report distinguishes *checked and clean* from *not
checked*, and anything unverified lands in `limitations`.

## Running it

```bash
# whole audit: crawl, analyze, compose, validate
python skills/audit-orchestrator/scripts/conduct_audit.py example.com --workspace ./ws --markdown

# any single skill against an existing bundle
python skills/answer-extractability-audit/scripts/probe_quotability.py --workspace ./ws

# validate any report against the required schema
python skills/audit-orchestrator/scripts/verify_report.py ./ws/report.json
```

Options: `--max-pages` (14), `--max-depth` (3), `--budget-seconds` (90),
`--render auto|off`, `--merge-probes`.

## Guardrails

Recommend-only — it audits and reports, never alters a site. GET requests only; no forms,
no authentication. Authenticated and transactional URLs (`/login`, `/checkout`,
`/account`, `app.`, `dashboard.`) are filtered before fetching. robots.txt is respected,
the site's own `Crawl-delay` is honoured, and the crawl is capped by pages, depth,
concurrency and wall-clock time. To check whether the CDN honours robots.txt, the homepage
is requested once with a browser User-Agent and once with each allowed AI search agent's
published User-Agent. No retries.

## Requirements

Python 3.9+, standard library only. No required dependencies, no external service, no
model weights. Playwright is optional and local-development only; nothing depends on it.

MIT licensed — see `LICENSE`.
