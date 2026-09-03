---
name: structured-data-audit
description: >-
  Audit schema.org structured data for presence, validity and agreement with the
  visible page. Detects absent markup, JSON-LD that fails to parse, no Organization
  identity anchor, no sameAs links, wrong or missing types for the page type
  (Product/Offer, Article, FAQPage, LocalBusiness, JobPosting, BreadcrumbList),
  omitted required and high-value properties, duplicate titles, missing meta
  descriptions, and markup whose price or name contradicts the rendered content.
  Use when assistants state wrong prices or attributes about a brand, or when
  entity recognition and rich results fail.
license: MIT
allowed-tools: Bash, Read
---

# Structured Data Audit (pillar 3a: machine-readable facts)

Stage 3a. Structured data is the only channel where a page states a fact in a form a
machine cannot misread.

## When to use

- As step 4 of `audit-orchestrator`, alongside `answer-extractability-audit`, which
  covers the prose layer this skill deliberately does not touch.
- Standalone when an assistant quotes a wrong price, misattributes a product, or fails
  to recognise the company as an entity.

## Inputs

`--workspace` (required) — an existing evidence bundle. No network I/O.

## The three questions, kept separate

1. **Presence** — is there markup, and of the type this page's content warrants?
2. **Validity** — does it parse, and does it carry the properties consumers require?
3. **Agreement** — does it match what the page visibly says?

(3) is the one most audits skip and the one that does real damage. Absent markup means a
fact must be inferred from prose. *Contradictory* markup means a wrong fact is asserted
with machine-grade confidence, so an assistant repeats the wrong number without hedging,
and persistent mismatch is treated as a spam signal. Wrong markup is worse than none.

## Procedure

1. Load the bundle; flatten every JSON-LD tree (including `@graph`) into an addressable
   node list tagged with its source URL.
2. **Presence** — `MARK-001` no structured data anywhere; `MARK-002` markup on under half
   of pages.
3. **Validity** — `MARK-003` blocks that fail to parse, cited with the parser's exact
   message, line, column and surrounding snippet. A block that does not parse is
   discarded whole, so every fact inside it is silently lost while looking present in
   the page source.
4. **Identity** — `MARK-004` no Organization/LocalBusiness entity anywhere; `MARK-005` an
   Organization with no `sameAs` link to a recognised identity authority.
5. **Type fit** — `MARK-006-<pagetype>` compares each page's inferred type against the
   types that page warrants, with a stated rationale per type. Suppressed when `MARK-001`
   or `MARK-004` already covers the same root cause, so one defect yields one finding
   rather than three.
6. **Properties** — `MARK-007` missing *required* properties (the node is typically
   rejected wholesale, taking its valid siblings with it); `MARK-008` missing high-value
   recommended ones (author, datePublished, offers.availability, description, logo).
7. **Agreement** — `MARK-009` markup price or name/headline that does not appear in the
   rendered text. Fires only when the page displays a competing value, so a page that
   simply shows no price is never accused of drift.
8. **Hygiene** — `MARK-010` duplicate titles across pages; `MARK-011` missing meta
   descriptions; `MARK-012` no BreadcrumbList on pages two or more clicks deep.
9. Write `findings/structured-data-audit.json`.

```bash
python scripts/probe_schema_truth.py --workspace ./ws
```

## Evidence standard

Node counts, the exact property path (`Offer.price`), the parser's verbatim error, the
conflicting values side by side, and the affected URLs.

## Severity

`high` — no markup at all, no identity entity, invalid JSON-LD, or markup contradicting
the page. `medium` — coverage gaps, missing required properties, wrong type for the page,
duplicate titles. `low` — missing recommended properties, breadcrumbs, descriptions.
Escalated by prevalence and homepage involvement.

## Output

`findings/structured-data-audit.json`, pillar `extract`.

## References

- `references/schema-requirements.md` — page type to expected types, required and
  recommended properties, and why each matters to an answer
