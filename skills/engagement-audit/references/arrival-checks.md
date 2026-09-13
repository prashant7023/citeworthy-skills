# Engagement checks: the AI-referral model

## Why this is not a generic UX audit

A visitor referred by an AI assistant is not the visitor most site UX is designed for. Three
differences drive every check in this skill:

**1. They were already given an answer.** They are here to *verify* it or *act* on it. They
do not need to be sold the category — they need the specific fact confirmed and a way to
proceed. A hero that re-pitches the category is friction, not persuasion.

**2. They land on an inner page.** Assistants cite the page that held the fact, not the
homepage. So the homepage is no longer *the* entry page — every page is an entry page, and
every page must orient a stranger. This inverts the usual assumption that a visitor
arrives at the top and works down a funnel.

**3. They have no session history.** Nothing is remembered about them. Any content that
assumes they read a previous page, any form that asks for something implied by how they
arrived, any navigation that only makes sense in sequence — all dead ends.

## Check reference

| Check | Fires when | Severity | Why it costs engagement |
|---|---|---|---|
| `STAY-001` | ≥50% of inner pages fail 2 of 3 orientation signals (H1 naming the subject, brand named in the title, `og:site_name` or first screen, breadcrumb) | high (engagement findings are never critical) | The arriving stranger cannot tell whose site this is or where they are. The single most AI-specific check here. |
| `STAY-002` | Entry screen <20 non-nav words, or no action-verb CTA among any link | high | No visible next action; the default decision is to leave. |
| `STAY-003` | >1 MB HTML or >8 render-blocking scripts in `<head>` (classic scripts only; `async`, `defer` and `module` do not block) | medium | Every blocking script delays first paint; abandonment rises steeply per second. |
| `STAY-004` | No `width=device-width`, or `user-scalable=no`/`maximum-scale=1` | high | Desktop layout scaled to illegibility, and pinch-zoom disabled so it cannot be fixed. Most referrals are mobile. |
| `STAY-005` | ≥2 interruption layers (consent platform, chat widget, `<dialog>`, overlay heading) | medium | Each costs a dismissal before the visitor reaches the fact they came for. |
| `STAY-006` | Substantive page has <5 internal links | medium | The session ends at one page; the crawler also finds no path onward. |
| `STAY-007` | Links exist but no action CTA and <3 descriptive labels | low | "Learn more" tells neither a human nor a machine what they get. |
| `STAY-008` | Form with 7+ fields or 5+ required | medium | No relationship exists yet; every required field measurably reduces completion. |
| `STAY-009` | No search input or `/search` on a 10+ page site | low | A visitor close to but not exactly on their answer has no way to redirect themselves. |
| `STAY-010` | No pricing / contact / about / proof / policy reference anywhere | high if ≥3 missing | The visitor is mid-verification; a missing surface ends the check with a negative answer. |
| `STAY-011` | Pricing page with no currency figure | medium | Reported as a trade-off, not a defect — see below. |
| `STAY-012` | >40% of images lack an `alt` attribute (10+ images) | medium | Blocks screen readers, and discards free machine-readable text. |
| `STAY-013` | ≥50% of pages have no `lang` | low | Affects voice selection and language-targeting. |
| `STAY-014` | ≥5 unlabelled form fields | low | Unusable with a screen reader, ambiguous to autofill. |

## Judgement calls this skill makes deliberately

**Gated pricing (`STAY-011`) is a trade-off, not a defect.** Enterprise sales models have
real reasons to withhold pricing. The finding does not say "you are wrong"; it states the
specific cost — "how much does X cost" is among the most-asked questions about any brand,
and with no number on the page an assistant answers with a competitor's published pricing
or reports that pricing is undisclosed. Then it offers graduated options (a starting price,
a range, or publishing the pricing *model*). Reporting a legitimate business choice as a
bug is how an audit loses its reader.

**Accessibility appears only where it is also a discoverability defect.** Missing `alt`,
missing `lang` and unlabelled inputs are here because they block machine extraction as well
as assistive technology. Colour contrast, focus order and ARIA correctness are genuinely
important and genuinely out of scope — this is not a WCAG conformance audit, and pretending
otherwise would misrepresent its coverage. The report says so.

**Performance is measured, not scored.** The skill reports HTML bytes, render-blocking
script counts and observed server response times, all from the crawl. It does not compute
or estimate Core Web Vitals, because it never rendered the page with real timing. The
remediation steps say to confirm with Lighthouse or CrUX. Inventing an LCP number would be
the same error the render skill refuses to make.

## Guard conditions

- `STAY-001` excludes legal pages, where boilerplate orientation is normal and expected.
- `STAY-002` measures the first screen **excluding `<nav>`/`<header>`/`<footer>`**, so
  navigation labels never masquerade as hero content — this alone removed a large class of
  false negatives during calibration.
- `STAY-006` skips pages under 120 words, which are usually redirects, confirmations or
  index stubs rather than dead-end content.
- `STAY-005` requires two or more signals: a single consent banner is normal on any European
  site and is not a finding.
- `STAY-009` and `STAY-013` require a minimum sample size so a five-page brochure site is not
  judged by the standards of a content library.
- Auth and checkout URLs are never crawled at all, so login shells cannot be mistaken for
  thin dead-end pages.
