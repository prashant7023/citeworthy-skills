# citeworthy

An Agent Skill Marketplace that points a general AI agent at any website and produces an
audit of **why AI assistants fail to find, read, quote or correctly describe the brand**,
and **why visitors who do arrive don't stay** — with evidence, severity, and prioritized
fixes.

Recommend-only. Read-only. Nothing in this marketplace ever modifies a live site.

```bash
python skills/cw-audit-conductor/scripts/conduct_audit.py example.com \
  --workspace ./ws --markdown
```

Typical run: **9–25 seconds**, one polite crawl, no dependencies beyond Python 3.9+.

---

## The idea

A brand appears in an AI answer only if five things succeed **in order**. Each is a
pillar, each is a skill, and each gates the next:

```
   ┌─────────┐   ┌────────┐   ┌──────────┐   ┌────────┐   ┌────────┐
   │ ACCESS  │──▶│ PARSE  │──▶│ EXTRACT  │──▶│ TRUST  │──▶│ ENGAGE │
   └─────────┘   └────────┘   └──────────┘   └────────┘   └────────┘
    Can a bot     Can a       Can it lift    Does the      Does the
    reach it?     machine     a specific     wider web     visitor
                  read it?    fact?          agree?        stay?
```

Failure at any stage makes every later stage irrelevant — a perfectly written page behind
`Disallow: /` is invisible, and a perfectly crawlable page whose only pricing lives in a
PNG cannot be quoted. **This ordering is the decomposition, the composition logic, and the
prioritisation rule all at once.**

## The skills

| Skill | Pillar | What it answers |
|---|---|---|
| **`cw-audit-conductor`** ⭐ | — | *Entrypoint.* Runs the others in causal order, applies four cross-skill rules, emits and validates the single report. |
| `cw-reach-gate` | Access | Can a crawler reach the site? Also produces the shared evidence bundle. |
| `cw-render-gap` | Parse | Can a machine read what a human sees? |
| `cw-schema-truth` | Extract | Are the facts machine-readable — and do they agree with the page? |
| `cw-quotability` | Extract | Are the facts *quotable* as prose? |
| `cw-entity-consensus` | Trust | Does the wider web identify and corroborate the brand? |
| `cw-time-decay` | Trust | Are the facts still true, and can a machine tell? |
| `cw-arrival-experience` | Engage | Does a visitor referred by an assistant actually stay? |

**91 checks** across seven skills. Full table: `skills/cw-audit-conductor/references/finding-catalog.md`.

## How the entrypoint composes them

Six analyzers producing six lists would be a pile, not a marketplace. The entrypoint adds
four things that are statements about the *relationship between* findings — which is
precisely why they cannot live inside any individual skill:

**1. Gating.** A critical access failure marks every downstream finding `blocked_by`.
Those fixes are still correct; they just cannot take effect while the gate is closed. A
report that lists "add FAQ schema" as item three while the site disallows `OAI-SearchBot`
has misled its reader about what to do on Monday.

**2. Confounding — the main false-positive control.** If the crawler could not execute
JavaScript *and* the site looks client-rendered, then "no H1" may describe **our blind
spot**, not the site. Those findings are retained, demoted to `low` confidence, labelled
`confounded_by`, and given the exact command to verify them. Asserting them destroys
credibility; dropping them hides real defects. Labelling them is the honest third option.

**3. Linking.** A missing definitional sentence is simultaneously an extractability
defect, a corroboration defect and an engagement defect. Three skills each correctly
report their own symptom; only the entrypoint sees that one rewrite closes all three. The
decomposition therefore produces a **shorter** action list than a monolith would.

**4. Prioritising.** `severity × confidence × pillar stage`, so earlier-stage fixes that
unblock everything downstream rank first.

Details and rationale: `skills/cw-audit-conductor/references/composition-rules.md`.

## Architecture: crawl once, analyze many

`cw-reach-gate` performs the **only** network I/O in the marketplace and writes an
evidence bundle. Every other skill is a pure function of that bundle.

```
<workspace>/
  manifest.json        crawl metadata, robots analysis, per-agent verdicts, page index
  pages/<id>.json      normalised parsed page (raw + rendered views)
  pages/<id>.raw.html  verbatim server HTML — every evidence claim is re-checkable
  findings/<skill>.json
  report.json          ← the deliverable
  report.md            human-readable companion
```

This buys four properties the rubric cares about directly:

- **Deterministic** — same bundle, same findings, every time (asserted by the test suite).
- **Fast** — one crawl for eight skills; analysis is local and takes seconds.
- **Polite** — a site sees one bounded, rate-limited, robots-respecting crawl.
- **Auditable** — the raw HTML is on disk, so any evidence claim can be re-verified.
- **Testable offline** — analyzers run against fixture bundles with no network at all.

## Output

Always includes the required schema, and extends it:

```jsonc
{
  "site": "example.com",
  "audited_at": "2026-08-27T14:32:00Z",
  "summary": {
    "total_findings": 16, "critical": 0, "high": 3, "medium": 5, "low": 8,
    "citeworthy_score": 72,
    "pillar_scores": {"access": 77, "parse": 95, "extract": 46, "trust": 78, "engage": 65},
    "headline": "3 high-severity issues limit how this site is found, read and cited."
  },
  "findings": [{
    "id": "F-001",
    "title": "No plain-language sentence states what the brand actually is",
    "severity": "critical",
    "evidence": "Scanned the opening ~25 sentences of 2 identity pages for a sentence that
                 both names the brand and defines it. None found. The homepage H1 reads:
                 'Unlock your potential.'",
    "suggested_action": {
      "summary": "Add one explicit definitional sentence near the top of the homepage.",
      "priority": "critical", "effort": "low", "owner": "content",
      "steps": ["Write it as '<Brand> is a <category> that <does what> for <who>.'", "..."]
    },
    // extensions:
    "check_id": "QUOTE-001", "pillar": "extract", "confidence": "high",
    "source_skill": "cw-quotability", "affected_urls": [...],
    "related_check_ids": ["ENTITY-002", "MARK-004"]
  }],
  "root_causes": [...],              // findings grouped by shared cause
  "proactive_recommendations": [...], // 7+ improvements beyond the defects found
  "remediation_plan": [...],          // 4 sequenced phases, each item once
  "limitations": [...],               // what was NOT verified — never reported as passing
  "scope": {...}, "method": {...}     // pages crawled, checks run, scoring formula
}
```

Full contract: `skills/cw-audit-conductor/references/report-schema.json`.

## Design commitments

**Evidence or it doesn't ship.** `verify_report.py` fails closed on the schema *and* on
an evidence-quality gate: every finding must cite a measured quantity and a traceable
check id. A plausible title with vague evidence is rejected by the build.

**Unevaluated is never "passing".** Every skill records `checks_run` whether or not a check
fires, so the report distinguishes *we checked and it's fine* from *we didn't check*.
Anything not verified lands in `limitations`.

**Guarded checks.** Every check has explicit conditions under which it must *not* fire —
minimum sample sizes, required corroborating signals, site-type gating (a charity is never
asked for a price list). The test suite keeps a deliberately healthy fixture that must stay
quiet.

**Trade-offs are not defects.** Blocking `GPTBot` (training) is reported as an
informational policy choice; blocking `OAI-SearchBot` (retrieval) is critical. Gated
pricing is reported as a trade-off with a stated cost, not a bug. Getting this wrong is
the fastest way to lose a reader's trust in every other finding.

**Fixes are mechanism-sound.** Each carries ordered steps, an effort estimate and an owner.
Where a fix is a symptom-patch, the text says so — a `<noscript>` block is described as a
backstop, never as a solution to client-side rendering.

## Running it

```bash
# full pipeline (crawl → 6 analyzers → compose → validate)
python skills/cw-audit-conductor/scripts/conduct_audit.py example.com --workspace ./ws --markdown

# with off-site corroboration: the agent writes probe_results.json, then
python skills/cw-audit-conductor/scripts/conduct_audit.py example.com --workspace ./ws --merge-probes

# any single skill, standalone, against an existing bundle
python skills/cw-quotability/scripts/probe_quotability.py --workspace ./ws

# tests (offline, no network)
python tests/test_marketplace.py
```

Options: `--max-pages` (25), `--max-depth` (3), `--budget-seconds` (150),
`--render auto|off`.

**Optional:** `pip install playwright && playwright install chromium`. With it, the
raw-vs-rendered gap is *measured* rather than inferred, and every content check runs at
full confidence. Without it, the audit still runs and says exactly what it could not
verify.

## Guardrails

- **Recommend-only** — audits and reports; never alters a site.
- **Read-only** — GET requests only. No forms, no POST, no authentication.
- **Never touches authenticated areas** — `/login`, `/checkout`, `/account`, `app.`,
  `dashboard.` and similar are filtered by path and subdomain before fetching.
- **Respects robots.txt** — for the auditor's own user-agent, with the site's
  `Crawl-delay` honoured.
- **Bounded** — hard caps on pages, depth, concurrency and wall-clock time.

Verified by the test suite: no write HTTP methods anywhere, and no network I/O in any
analyzer.

## Requirements

Python 3.9+. **No required dependencies** — standard library only. Playwright is optional.

## Repository layout

```
citeworthy/
├── marketplace.json            manifest: 8 skills, exactly one entrypoint
├── README.md
├── LICENSE                     MIT
├── tests/test_marketplace.py   188 offline assertions
└── skills/
    ├── cw-audit-conductor/  ⭐ entrypoint
    │   ├── SKILL.md
    │   ├── scripts/            compose_report.py, verify_report.py, conduct_audit.py
    │   └── references/         composition-rules, severity-model, finding-catalog, report-schema
    ├── cw-reach-gate/     SKILL.md, scripts/harvest_site.py, references/ (3)
    ├── cw-render-gap/
    ├── cw-schema-truth/
    ├── cw-quotability/
    ├── cw-entity-consensus/
    ├── cw-time-decay/
    └── cw-arrival-experience/
```

Every skill folder independently satisfies the agentskills.io spec: YAML frontmatter with
`name`, `description`, `license` and `allowed-tools`, and `When to use` / `Inputs` /
`Procedure` / `Output` sections, with detail pushed to `references/` and executable logic
to `scripts/` (progressive disclosure). Each analyzer vendors its own copy of `evidence.py`
so no skill imports across folder boundaries — deliberate, so any folder can be lifted out
and used alone.

## License

MIT — see `LICENSE`.
