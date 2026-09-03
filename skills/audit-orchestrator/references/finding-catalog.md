# Finding catalog

Every check in the marketplace, generated from the analyzer sources so it cannot drift
from the implementation. `Base severity` is the severity before the prevalence adjustment
described in `severity-model.md`; several checks compute it dynamically, shown as *derived*.

Check-id prefixes name the pipeline stage they guard: **REACH** (can a bot get in),
**READ** (can it parse), **MARK** (machine-readable facts), **QUOTE** (quotable prose),
**ENTITY** (does the web agree), **TIME** (is it still true), **STAY** (does the visitor remain).

**93 checks across 7 skills.** All are proven able to fire — see `tests/`.

## REACH-* — Access — can a crawler reach the page?

Pillar: `access` · Skill: `crawl-access-audit`

| Check | Base severity | Detects |
|---|---|---|
| `REACH-001` | critical | robots.txt blocks answer-time retrieval agents from the whole site |
| `REACH-002` | low | Training-corpus crawlers are blocked (deliberate trade-off, not a defect) |
| `REACH-003` | medium | robots.txt contains syntax errors |
| `REACH-004` | high | robots.txt is unreachable or returns an error |
| `REACH-005` | medium | No usable XML sitemap |
| `REACH-006` | low | Sitemap exists but is not advertised in robots.txt |
| `REACH-007` | medium | Both www and apex hosts serve 200 without redirecting to one canonical host |
| `REACH-008` | critical | Homepage does not return a successful response to a bot user-agent |
| `REACH-009` | high | Internally-linked pages return error status codes |
| `REACH-010` | critical | Pages forbid snippets, which forbids quotation |
| `REACH-011` | critical | Site is not reachable over HTTPS |
| `REACH-012` | derived | Content pages are marked noindex |
| `REACH-013` | high | Pages canonicalise to a different domain |
| `REACH-014` | medium | Most pages have no rel=canonical |
| `REACH-015` | medium | Server responses are slow enough to cost crawl budget and visitors |
| `REACH-016` | low | Long redirect chains on internal URLs |
| `REACH-017` | medium | Sitemap URLs are not reachable through internal links |
| `REACH-018` | high | Content paths are disallowed for all crawlers |
| `REACH-019` | low | No /llms.txt curated entry point for AI agents |
| `REACH-020` | medium | robots.txt declares the same user-agent in more than one group |
| `REACH-021` | critical | Different URLs return byte-identical page content |
| `REACH-022` | critical | A bot-protection service is serving challenge pages instead of content |

## READ-* — Parse — can a machine read what a human sees?

Pillar: `parse` · Skill: `render-extraction-audit`

| Check | Base severity | Detects |
|---|---|---|
| `READ-001` | derived | Most page content only exists after JavaScript runs |
| `READ-002` | high | Server HTML is a near-empty app shell (client-side rendering suspected) |
| `READ-003` | high | Identity facts (title, H1, price, structured data) are injected by JavaScript |
| `READ-004` | medium | Primary content is delivered inside third-party iframes |
| `READ-005` | high | Key facts appear to be published as images rather than text |
| `READ-006` | medium | Substantive content is distributed as downloadable documents |
| `READ-007` | medium | Video-led pages have no transcript or text equivalent |
| `READ-008` | low | No <noscript> fallback on script-dependent pages |
| `READ-009` | medium | Pages have no H1 heading |
| `READ-010` | medium | No semantic landmarks (<main>/<article>) to separate content from boilerplate |
| `READ-011` | low | Heading levels skip, breaking document structure |
| `READ-012` | high | Content is addressed by hash-based client-side routes |

## MARK-* — Extract (markup) — is the fact machine-readable and consistent?

Pillar: `extract` · Skill: `structured-data-audit`

| Check | Base severity | Detects |
|---|---|---|
| `MARK-001` | high | No structured data anywhere on the site |
| `MARK-002` | medium | Structured data covers only a minority of pages |
| `MARK-003` | high | JSON-LD blocks fail to parse |
| `MARK-004` | high | No Organization entity defines who the brand is |
| `MARK-005` | medium | Organization markup has no sameAs links to authoritative profiles |
| `MARK-006-{ptype}` | medium | {subject} no {gap['expected'][0]} markup |
| `MARK-007` | medium | Structured data omits properties that consumers require |
| `MARK-008` | low | High-value optional properties are absent from otherwise-valid markup |
| `MARK-009` | high | Structured data contradicts the visible page content |
| `MARK-010` | medium | Multiple pages share the same <title> |
| `MARK-011` | low | Most pages have no meta description |
| `MARK-012` | low | Deep pages carry no BreadcrumbList markup |

## QUOTE-* — Extract (prose) — can a machine quote a specific fact?

Pillar: `extract` · Skill: `answer-extractability-audit`

| Check | Base severity | Detects |
|---|---|---|
| `QUOTE-001` | critical | No plain-language sentence states what the brand actually is |
| `QUOTE-002` | high | The homepage opens with a slogan instead of a statement of what this is |
| `QUOTE-003` | high | Facts assistants are routinely asked for are not stated in text anywhere |
| `QUOTE-004` | medium | Long pages have too few subheadings to chunk cleanly |
| `QUOTE-005` | low | Paragraphs are too long to survive as retrieved passages |
| `QUOTE-006` | medium | No question-and-answer content anywhere on the site |
| `QUOTE-007` | high | Content sections never name the brand, so quoted passages lose attribution |
| `QUOTE-008` | medium | Navigation and footer text outweighs the unique content |
| `QUOTE-009` | low | The same concepts appear under inconsistent names |
| `QUOTE-010` | low | Domain acronyms are used without ever being expanded |
| `QUOTE-011` | low | Performance claims lack the baseline that makes them quotable |
| `QUOTE-012` | low | A quarter of internal links carry no descriptive anchor text |
| `QUOTE-013` | medium | Pages state what the product does but never what it does not |

## ENTITY-* — Trust (identity) — does the wider web agree?

Pillar: `trust` · Skill: `entity-corroboration-audit`

| Check | Base severity | Detects |
|---|---|---|
| `ENTITY-001` | high | The site links to almost no authoritative profiles of itself |
| `ENTITY-002` | medium | The brand describes itself differently on each surface |
| `ENTITY-003` | low | Multiple inconsistent phone numbers appear across the site |
| `ENTITY-004` | medium | Published content is anonymous |
| `ENTITY-005` | medium | Authority claims are made with no citable source |
| `ENTITY-006` | critical | The brand's own site does not surface for its own defining question |
| `ENTITY-007` | high | The web describes the brand differently than the brand describes itself |
| `ENTITY-008` | high | The brand name collides with other entities in search results |
| `ENTITY-009` | high | No independent sources discuss the brand |
| `ENTITY-010` | low | The brand has no entry in the public knowledge graphs |
| `ENTITY-011-{label}` | high | Third parties, not the brand, answer '{label}' questions about it |

## TIME-* — Trust (time) — is the fact still true?

Pillar: `trust` · Skill: `freshness-audit`

| Check | Base severity | Detects |
|---|---|---|
| `TIME-001` | medium | Time-sensitive pages publish no date at all |
| `TIME-002` | high | The most recent dated content is over a year old |
| `TIME-003` | medium | Most dated pages have not been touched in two years |
| `TIME-004` | low | The site-wide copyright year is out of date |
| `TIME-005` | medium | Dates on the page contradict the dates in the markup |
| `TIME-006` | low | Every sitemap entry claims the same modification date |
| `TIME-007` | medium | The site has no regularly-updated surface |
| `TIME-008` | medium | Pages present a past year as if it were current |

## STAY-* — Engage — does the arriving visitor stay?

Pillar: `engage` · Skill: `engagement-audit`

| Check | Base severity | Detects |
|---|---|---|
| `STAY-001` | high | Inner pages do not orient a visitor who lands on them cold |
| `STAY-002` | high | The entry page's first screen does not say what to do next |
| `STAY-003` | medium | Pages ship enough render-blocking weight to delay first paint |
| `STAY-004` | high | Pages declare no mobile viewport |
| `STAY-005` | medium | Multiple interruption layers load before the content is usable |
| `STAY-006` | medium | Content pages offer almost no onward path |
| `STAY-007` | low | Pages link onward only with unlabelled or generic calls to action |
| `STAY-008` | medium | Top-of-funnel forms demand too much before giving anything |
| `STAY-009` | low | No site search on a content-heavy site |
| `STAY-010` | high | Pages a referred visitor uses to verify the brand are missing |
| `STAY-011` | medium | The pricing page states no price |
| `STAY-012` | medium | Most images have no alt attribute |
| `STAY-013` | low | Pages declare no language |
| `STAY-014` | low | Form inputs have no accessible label |
| `STAY-015` | medium | The mobile viewport is configured to block pinch-zoom |

## Conventions

- Checks suffixed with a page type (`MARK-006-pricing`) are emitted once per affected page type.
- Every check is registered in `checks_run` whether or not it fires, so the report can
  distinguish *clean* from *never looked*.
- `REACH-002` is `low` by design: blocking training-corpus crawlers is a policy choice, not a
  defect. Only blocking answer-time retrieval agents (`REACH-001`) is critical.
- `REACH-008`, `REACH-021` and `REACH-022` each mark the whole audit inconclusive and suppress
  the score — a crawl that read nothing is never reported as a pass.
- `QUOTE-001-J` is the marketplace's only agent-judgment finding. It is quote-backed, always
  `medium` confidence, and never overwrites the script's `QUOTE-001`. See
  `answer-extractability-audit/references/judgment-rubric.md`.
