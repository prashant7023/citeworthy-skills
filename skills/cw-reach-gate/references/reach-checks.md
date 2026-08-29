# Access checks (ACC-*)

Thresholds and guard conditions for every check in `cw-reach-gate`. See
`ai-crawler-reference.md` for the agent classification these depend on.

| Check | Fires when | Severity | Guard against false positives |
|---|---|---|---|
| `REACH-001` | robots.txt disallows `/` for any Class-A retrieval agent | critical | Only fires when robots.txt returned 200 with a real body. An HTML soft-404 at /robots.txt is treated as absent. |
| `REACH-002` | Only Class-B training agents are blocked | low, `informational: true` | Framed as a policy choice to confirm, never a defect. |
| `REACH-003` | Malformed directive lines | medium | Only counts lines a real crawler would skip: missing `field: value`, or a rule before any `User-agent`. Comments and blanks ignored. |
| `REACH-004` | robots.txt returns 5xx or times out | high | 404 is explicitly *not* a finding — absence means "crawl everything". |
| `REACH-005` | No usable sitemap at any declared or conventional path | medium | Requires XML-looking content, so an HTML 200 page at /sitemap.xml does not count as a pass. |
| `REACH-006` | A sitemap responds but robots.txt declares none | low | Only when robots.txt itself returned 200. |
| `REACH-007` | Both apex and www return 200 without redirecting to one another | medium | Compares final hosts after redirects; same-host results never fire. |
| `REACH-008` | Homepage returns 0 or 5xx to a bot UA | critical | Uses the resolved post-redirect entry URL. |
| `REACH-009` | Internally-linked URLs return 4xx/5xx | high at 20%+ of sample, else medium | Only counts URLs actually linked from within the site. |
| `REACH-010` | `nosnippet` or `max-snippet:0` in meta robots or `X-Robots-Tag` | critical at 50%+ of pages, else high | Reads both the tag and the header, since either alone is binding. |
| `REACH-011` | HTTPS unreachable or certificate invalid | critical | Only probed when the entry URL was HTTP. |
| `REACH-012` | `noindex` on content pages | high | Excludes pages classified `legal` and `listing`, where noindex is often deliberate. |
| `REACH-013` | rel=canonical points to a different registrable domain | high | Compares registrable domains, so www/apex variants never fire. |
| `REACH-014` | 50%+ of 200-pages have no canonical | medium | Requires 2+ pages; suppressed entirely when `REACH-013` fired. |
| `REACH-015` | 30%+ of pages respond slower than 2500 ms | medium, pillar `engagement` | Reports the median of the slow set, not the worst case. |
| `REACH-016` | 3+ redirects on an internal URL | low | — |
| `REACH-017` | 30%+ of sampled sitemap URLs never appear as an internal link target | medium | Samples at most 200 sitemap URLs; requires 3+ orphans and a non-empty crawl. |
| `REACH-018` | Internally-linked content paths disallowed for all crawlers | high at 3+, else low | Records the exact matching rule for each URL. |
| `REACH-019` | No `/llms.txt` | low, `proactive: true` | Framed as an emerging, unenforced convention — an opportunity, not a defect. |

## Crawl politeness

- Delay between requests: `max(--delay-ms, min(site Crawl-delay, 2s))` — the site's own
  stated preference wins, capped so a hostile value cannot stall the audit.
- Concurrency capped at `--concurrency` (default 4).
- Hard bounds on pages, depth and wall-clock time; the crawl stops at whichever binds first.
- Response bodies capped at 3.5 MB.
- Authenticated and transactional URLs are never fetched, filtered by path (`/login`,
  `/checkout`, `/account`, `/admin`, `/cart`, …) and by subdomain (`app.`, `dashboard.`,
  `portal.`, …).
- GET only. No POST, no form submission, no cookie-authenticated request.

## Page-type classification

Used by the schema, freshness and engagement skills to decide what a page *should* have.
URL patterns are evaluated first because they are stable and cheap; on-page signals act as
a tiebreak. Types resolve in this priority order:

`homepage → pricing → product → faq → docs → article → about → contact → careers → legal
→ listing → local → generic`

Every matching label is also retained in `page_tags`, so a check can be conservative when a
page is genuinely ambiguous (a pricing page that is also a product page, for instance).
Classification never causes a finding on its own — it only decides which expectations
apply, so a misclassification degrades to a skipped check rather than a false positive.
