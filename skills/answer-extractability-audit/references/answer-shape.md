# Answer shape: what makes prose quotable

The checks in this skill all derive from one model. Writing it down is what keeps them
generalisable — the checks are consequences of the model, not a list of things noticed on
particular sites.

## The model

A retrieval system does not "read a page". It splits the page into passages, embeds each,
matches the query against them, and returns **one passage**, which is then shown to the
user largely stripped of its surroundings. So the unit that has to work is not the page —
it is the passage.

A passage earns its place in an answer when it is:

| Property | Failure mode | Checks |
|---|---|---|
| **Self-contained** | Says "we" and never names the subject, so the claim cannot be attributed once detached | `QUOTE-007`, `QUOTE-010` |
| **Declarative** | Implies the fact through a slogan instead of stating it | `QUOTE-001`, `QUOTE-002` |
| **Bounded** | Too long or unheaded, so the chunk boundary lands mid-argument | `QUOTE-004`, `QUOTE-005`, `QUOTE-008` |
| **Specific** | Carries a number with no unit, baseline or date, so it cannot be repeated responsibly | `QUOTE-003`, `QUOTE-011` |

Plus one consistency property across passages: the same concept named the same way
(`QUOTE-009`), and destinations described by their anchors (`QUOTE-012`).

## Rewrite patterns

### QUOTE-001 — the definitional sentence

This is the single highest-leverage sentence on any website. It is what gets quoted when
someone asks "what is X?".

> **Before:** "Welcome. We help teams do their best work."
>
> **After:** "Acme is a project-management tool for construction subcontractors. It
> replaces spreadsheet-based scheduling for teams of 10–200 people."

Form: `<Brand> is a <category> that <does what> for <who>.`

Place it in the first 100 words of the homepage, repeat it verbatim on the About page, in
the meta description, in `Organization.description`, and on every off-site profile. The
verbatim repetition is the point — corroboration across surfaces is what makes a machine
confident enough to repeat it rather than paraphrase.

### QUOTE-002 — the hero

> **Before (H1):** "Unlock your potential."
> **After (H1):** "Scheduling software for construction subcontractors"
> **After (subhead):** "Unlock your potential." — the slogan still does its emotional work.

The check requires *both* a slogan-pattern H1 *and* no defining verb in the first screen,
so a slogan sitting above a clear definition never fires.

### QUOTE-007 — attribution in every section

> **Before:** "Our platform reduces import time by 40%."
> **After:** "Acme reduces import time by 40% compared with manual CSV upload."

Once per section is enough. This is the fix people most often overdo; repeating the brand
in every sentence reads as spam to humans and ranking systems alike.

### QUOTE-011 — claims that survive quotation

> **Before:** "50% faster."
> **After:** "50% faster than our v3.2 release, measured on a 10,000-row import (March 2026)."

A cautious answer generator drops the first and can quote the second. The strongest proof
points are usually the ones being discarded for lack of a baseline.

### QUOTE-004 / QUOTE-006 — headings as questions

Headings are both the chunk boundary and the chunk label. A heading phrased as the user's
question matches the query directly and makes the passage under it a ready-made answer.

> **Before (H2):** "Overview"
> **After (H2):** "How long does implementation take?"

Answer in the first one or two sentences under the heading, completely, before any
elaboration. A retrieved passage that starts with a direct answer survives truncation.

## Guard conditions (why these checks do not over-fire)

Every check here is gated, because the most valuable property of a content check is
knowing when *not* to fire:

- **`QUOTE-003` fact coverage is gated by site type.** Pricing is only expected when the site
  has pricing or product pages, or mentions pricing at all; location only when contact or
  local pages exist; contact details only when a Contact, About or local page was crawled;
  founding only when an About page was crawled. A charity is never asked for a price list,
  and a 14-page sample that never reached /about is never told the site lacks About facts.
- **`QUOTE-001` reads only same-host identity pages.** An About page on another subdomain (a
  regional branch, a language edition) is not the brand's own. Without an About page in
  the sample the finding is `high` at medium confidence, never `critical`.
- **`QUOTE-007` needs at least 6 sections sampled** and fires only above 70%, so a short site
  with two unattributed sections is not accused of a systemic problem.
- **`QUOTE-004` scales with length** (one subheading per ~500 words) rather than using a
  fixed count, so a 700-word page and a 4,000-word page are judged by the same standard.
- **`QUOTE-008` requires the page to actually use `<main>`.** Without a landmark there is no
  reliable boilerplate boundary, so the check abstains rather than guessing.
- **`QUOTE-010` excludes universally-understood acronyms** (API, FAQ, CEO, PDF, URL, AI, …)
  and requires 4+ uses before flagging. It also skips two-letter tokens, words that appear
  elsewhere in lower case (uppercase styling such as ALL or NEW), the brand's own name, and
  ISO currency codes.
- **`QUOTE-009` counts only spelling variants of one term** (`e-mail`/`email`,
  `JavaScript`/`Javascript`), each variant used at least twice. Plurals and inflections
  (`model`/`models`, `build`/`building`) are grammar, not inconsistency.
- **`QUOTE-011` counts only comparative relative claims:** a percentage or multiplier plus
  a comparison word, with no baseline, year or source. "Trusted by 98% of the Fortune 500"
  and "over 11 days" are not claims needing a baseline.
- **`QUOTE-005` measures real `<p>` elements.** Splitting page text on line breaks turned
  layouts built from inline elements into multi-thousand-word "paragraphs".
- **`QUOTE-005` and `QUOTE-012` require a minimum corpus** (multiple pages, 40+ links) so small
  sites do not trip thresholds designed for large ones.

## What this skill deliberately does not check

Readability scores, keyword density, and word-count minimums. All three are proxies that
correlate with quality on average and mislead on individual pages — a 200-word page that
answers a question completely outperforms a 2,000-word page that circles it. The checks
here measure structural properties that have a direct mechanical consequence for
retrieval, not stylistic ones.
