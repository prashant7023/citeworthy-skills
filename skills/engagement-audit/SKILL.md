---
name: engagement-audit
description: >-
  Check whether a visitor referred by an AI assistant actually stays. Framed around
  AI-referral traffic: arrivals land on inner pages with a specific intent and no
  session history. Detects inner pages that fail to orient a cold arrival (no H1,
  brand unnamed, no breadcrumb), entry screens with no value proposition or call to
  action, render-blocking weight, broken or zoom-locked mobile viewports, stacked
  consent/chat/newsletter interruptions, dead-end pages with no onward path, heavy
  top-of-funnel forms, missing site search, missing verification surfaces (pricing,
  contact, about, proof, policies), gated pricing, and accessibility defects that
  also block extraction. Use when traffic arrives but does not convert or stay.
license: MIT
allowed-tools: Bash, Read
---

# Engagement Audit (pillar 5: does the visitor stay?)

Stage 5, and the on-site half of the problem. Discoverability gets a visitor to the
door; this decides whether the visit was worth anything.

## When to use

- As step 7 of `audit-orchestrator`.
- Standalone when analytics show arrivals from assistant referrers with high bounce and
  single-page sessions.

## Inputs

`--workspace` (required) — an existing evidence bundle. No network I/O.

## What makes these checks different from a generic UX audit

The shape of AI-referred traffic is specific, and it drives every check here:

1. **The visitor was already given an answer.** They arrive to *verify* it or to *act* on
   it, with a specific intent and low patience for being re-pitched.
2. **They land on an inner page, not the homepage.** Assistants cite the page that held
   the fact. Every page is therefore an entry page and must orient a stranger.
3. **They have no prior session.** Nothing is remembered about them, so anything the site
   assumes they already read — or already told you — is a dead end.

So this skill measures orientation-on-arrival, cost-to-first-value, and whether a next
step exists. It does not grade brand aesthetics, and it covers accessibility only where
the same defect also blocks machine extraction (missing alt text, missing `lang`,
unlabelled inputs) — those appear here because they cost engagement *and* discoverability.

## Procedure

1. Load the bundle; prefer the rendered view where one exists.
2. **`STAY-001` deep-link orientation** — for each inner page, check three signals: an H1
   naming the subject, the brand named in the title, `og:site_name` or first screen
   (accent- and spacing-insensitive), a breadcrumb trail. Failing
   two or more counts as disoriented; fires when half or more of inner pages fail. This
   is the check that most distinguishes AI-referral readiness from ordinary UX.
3. **`STAY-002` entry-screen clarity** — the first screen (with navigation and header
   chrome excluded, so menu labels never count as content) carrying under 20 words, or
   no action-verb CTA among any of the page's links.
4. **`STAY-003` weight** — over 1 MB of HTML or more than 8 render-blocking scripts in
   `<head>`. Only classic scripts there block first paint; `async`, `defer`, `module` and
   end-of-body scripts do not.
5. **`STAY-004` mobile viewport** — missing `width=device-width`, or `user-scalable=no` /
   `maximum-scale=1`, which block pinch-zoom. Most assistant referrals arrive on mobile.
6. **`STAY-005` stacked interruptions** — two or more of: consent platform, chat widget,
   `<dialog>` elements, overlay headings. One is normal; several before first content is
   a dismissal tax on a visitor who came for one fact.
7. **`STAY-006` / `STAY-007` onward path** — substantive pages with under 5 internal links,
   or with links but no action-verb CTA and fewer than 3 descriptively-labelled links.
8. **`STAY-008` form friction** — 7+ fields or 5+ required fields on a first-contact form.
9. **`STAY-009` site search** — absent on a site of 10+ pages. Also the best available
   source of the real questions to answer in content.
10. **`STAY-010` verification surfaces** — no pricing, contact, about, social proof or
    policy reference anywhere in text or link targets. A referred visitor is
    mid-verification; a missing surface ends that check with a negative answer.
11. **`STAY-011` gated pricing** — a pricing page with no currency figure, only
    contact-sales. Reported as a *trade-off with a stated cost*, not as a defect: "how
    much does it cost" is among the most common questions asked about any brand, and with
    no number an assistant answers with a competitor's published pricing instead.
12. **`STAY-012`–`STAY-014`** — missing alt text, missing `lang`, unlabelled inputs.
13. Write `findings/engagement-audit.json`.

```bash
python scripts/probe_arrival.py --workspace ./ws
```

## Evidence standard

Counts and ratios over the crawled sample, the actual captured first-screen text, the
specific orientation signals each page failed, and the affected URLs. Performance
findings cite measured HTML bytes, script counts and server response times from the
crawl — never an estimated score, and the report says to confirm with Lighthouse or CrUX
rather than implying this skill measured Core Web Vitals.

## Severity

Never `critical`: an engagement defect degrades a visit but cannot make a brand
unfindable, so the entrypoint caps this pillar at `high`.
`high` — no orientation on most inner pages, no entry-screen CTA, broken mobile viewport,
three or more verification surfaces missing. `medium` — weight, interruptions, dead ends,
form friction, gated pricing, missing alt text. `low` — generic CTAs, no site search,
missing `lang`, unlabelled inputs.

## Output

`findings/engagement-audit.json`, pillar `engage`.

## References

- `references/arrival-checks.md` — the AI-referral model, thresholds, and the fix
  for each check
