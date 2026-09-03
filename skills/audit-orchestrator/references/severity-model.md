# Severity, confidence and scoring model

One model, applied identically by every skill, so that a `high` from the engagement skill
means the same thing as a `high` from the access skill.

## Severity: what it measures

Severity answers **"how much of the brand's AI visibility or engagement does this
destroy?"** — not how hard it is to fix. Effort is tracked separately in
`suggested_action.effort` precisely so that severity stays a pure impact measure and the
two can be traded off by the reader.

| Severity | Definition | Examples |
|---|---|---|
| `critical` | The brand cannot be found, read, quoted or identified **at all**, site-wide. | Retrieval agents disallowed in robots.txt; homepage fails for a bot UA; under 25% of content server-rendered; no sentence anywhere says what the brand is; the brand's own domain does not surface for its own defining question. |
| `high` | A whole class of facts, or a major section, is unavailable or wrong. | No structured data anywhere; markup contradicting the visible page; nosnippet on content pages; the web describes the brand differently than it describes itself; inner pages do not orient a cold arrival. |
| `medium` | Discoverability or engagement is measurably degraded, but the fact is still obtainable. | No sitemap; missing canonicals; thin subheading structure; stacked interruptions; undated time-sensitive pages. |
| `low` | Hygiene, polish, or a proactive opportunity with no current defect. | Stale copyright year; missing `lang`; no `/llms.txt`; generic anchor text. |

## Prevalence adjustment

Applied inside each skill via `bundle.escalate()`, where the page-level evidence lives.

```
prevalence = affected_pages / crawled_pages

prevalence >= 0.50  OR homepage affected  ->  escalate one step
prevalence <= 0.15                        ->  demote one step
otherwise                                 ->  unchanged
```

The homepage clause exists because it is the page an assistant and a human are most
likely to hit first, so a defect there has outsized reach regardless of page count.

The `<= 0.15` demotion prevents the most common over-reporting failure: one page in ten
having a defect is a task, not a systemic problem, and calling it `high` trains readers
to ignore the severity field.

## Confidence: how much we trust the evidence

Severity says how bad it is *if true*. Confidence says how sure we are.

| Confidence | Meaning |
|---|---|
| `high` | Directly measured from fetched artefacts. Both sides of every comparison were observed. |
| `medium` | Inferred from a conservative signature with a documented guard condition — e.g. client-side rendering suspected from an app shell plus a framework bundle, with no renderer to confirm. |
| `low` | Retained but demoted by the entrypoint's confounding rule, because the audit's own blind spot could explain it. Always carries a `verification` field. |

Confidence is never used to hide a finding. It scales the ranking and the score, and it
is printed on every finding so the reader can weigh it.

## Scoring

Five pillar scores plus an overall, all deterministic:

```
pillar_score = max(0, 100 - sum(penalty[severity] x confidence_weight))

penalty:            critical 40 | high 22 | medium 10 | low 3
confidence_weight:  high 1.0    | medium 0.75 | low 0.5

overall = unweighted mean of the five pillar scores
```

Deliberate properties of this formula:

- **Additive, not multiplicative.** Three `medium` problems in one pillar should hurt more
  than one, and they do.
- **Floored at 0.** A pillar can be maximally broken; it cannot go negative and drag the
  overall score into meaninglessness.
- **Unweighted mean across pillars.** Weighting them would encode a claim about relative
  business value that varies by site. An even mean keeps the number honest and pushes the
  reader to the pillar breakdown, which is the actionable part.
- **Comparable across runs, not across sites.** The score is designed to track one site's
  progress between audits. Comparing two different sites' scores is not meaningful,
  because crawl samples differ — this limitation is stated in the report's `method` block.

## Priority vs. severity

`suggested_action.priority` defaults to the finding's severity but is demoted alongside it
under the confounding rule, so a low-confidence finding never carries a high priority.
The `remediation_plan` phases are the practical ordering: they combine severity, pillar
stage and dependency into four sequenced buckets a team can actually work through.
