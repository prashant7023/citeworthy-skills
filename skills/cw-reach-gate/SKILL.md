---
name: cw-reach-gate
description: >-
  Check whether AI crawlers and search agents can reach a site at all, and build
  the shared evidence bundle the rest of the audit reads. Detects robots.txt
  blocks on answer-time retrieval agents (OAI-SearchBot, ChatGPT-User, Claude-User,
  PerplexityBot) as distinct from training-corpus opt-outs, plus nosnippet and
  noindex directives, error status codes, missing or unadvertised sitemaps,
  canonical and duplicate-host problems, orphan pages, slow responses, redirect
  chains and missing HTTPS. Use as the first stage of an AI-readiness audit, or
  alone to answer "can bots even reach this site?".
license: MIT
allowed-tools: Bash, Read
---

# Crawl & Access Audit (pillar 1: reach)

Stage 1 of the pipeline, and the gate for everything after it. If a crawler cannot
fetch the page, nothing about its content matters.

This skill has two jobs: report `REACH-*` access findings, **and** produce the evidence
bundle every other skill in the marketplace consumes. Crawling once and analysing many
times is what keeps the whole audit under five minutes and under one site's worth of
polite traffic.

## When to use

- As step 1 of `cw-audit-conductor` (always).
- Standalone, to answer "are we blocking the AI crawlers?" or "why isn't this indexed?".

## Inputs

`url` (required), `--max-pages` (25), `--max-depth` (3), `--budget-seconds` (180),
`--concurrency` (4), `--delay-ms` (250), `--render` (`auto`|`off`), `--workspace` (required).

## The distinction this skill exists to make

Most AI-visibility audits report "you block GPTBot" as critical. That is usually wrong,
and getting it wrong destroys trust in the whole report. There are two different classes
of agent:

- **Retrieval agents** (`OAI-SearchBot`, `ChatGPT-User`, `Claude-User`, `Claude-SearchBot`,
  `PerplexityBot`, `Googlebot`, `Bingbot`, `Applebot`) fetch pages *at answer time* or
  populate the indexes answers are grounded in. Blocking these makes the brand
  **uncitable**. That is critical.
- **Training agents** (`GPTBot`, `ClaudeBot`, `CCBot`, `Google-Extended`,
  `Applebot-Extended`) only feed future model training. Blocking these is a legitimate,
  common, deliberate policy choice. It is reported as an informational trade-off, never
  as a defect.

A site can block every training crawler and still be cited perfectly. A site that blocks
one retrieval agent is invisible to that assistant no matter what else it does.

## Procedure

1. **Resolve the host.** Fetch the entry URL and follow redirects; derive the canonical
   host from the *final* URL. Auditing `example.com` when the site serves from
   `www.example.com` would otherwise probe robots.txt and sitemap.xml on the wrong host.
2. **HTTPS.** If the entry point is HTTP, probe HTTPS and check the certificate → `REACH-011`.
3. **robots.txt.** Fetch and parse it into per-user-agent groups (not a merged view — the
   whole point is knowing *which* agent is blocked). Evaluate the root path `/` for every
   agent in both classes using longest-match Allow/Disallow with wildcard support.
   → `REACH-001` (retrieval blocked, critical), `REACH-002` (training blocked, informational),
   `REACH-003` (syntax errors), `REACH-004` (unreachable/5xx — many crawlers read that as
   "disallow everything").
4. **llms.txt.** Probe `/llms.txt` → `REACH-019` (proactive, low).
5. **Sitemaps.** Try every `Sitemap:` directive, then `/sitemap.xml` and
   `/sitemap_index.xml`; follow one level of sitemap index. → `REACH-005` (none usable),
   `REACH-006` (exists but not advertised).
6. **Host canonicalisation.** Fetch both apex and `www`; if both return 200 without
   redirecting to one another → `REACH-007`.
7. **Crawl.** Breadth-first from the entry page, same registrable domain only, obeying
   robots for our own user-agent, with a polite delay (at least the site's `Crawl-delay`,
   capped at 2s), capped concurrency, and hard page/depth/time budgets. **Skip
   authenticated and transactional URLs** by path (`/login`, `/checkout`, `/account`, …)
   and by subdomain (`app.`, `dashboard.`, …). Top up from the sitemap if link-following
   runs dry.
8. **Per-page normalisation.** Parse each page into a flat record: title, meta, OpenGraph,
   canonical, headings, sections, body text, main-content text, above-the-fold text
   (excluding nav/header chrome), links, images, scripts, iframes, forms, JSON-LD
   (parsed, with byte-accurate parse errors), microdata, dates, prices, counts. Classify
   its page type deterministically (URL patterns first, on-page signals as tiebreak).
9. **Render (optional).** If Playwright is importable, render up to 5 pages and store the
   post-JS DOM beside the raw HTML. If not, record why. **Never fabricate a rendered
   view** — the render skill degrades to heuristics and says so.
10. **Access findings over the sample** — `REACH-008` homepage fails for a bot UA,
    `REACH-009` broken internal links, `REACH-010` nosnippet/max-snippet:0 (forbids
    quotation — indexed but never citable), `REACH-012` noindex on content pages,
    `REACH-013`/`REACH-014` canonical problems, `REACH-015` slow responses, `REACH-016` redirect
    chains, `REACH-017` orphan sitemap URLs, `REACH-018` disallowed content paths.
11. **Write the bundle**: `manifest.json`, `pages/<id>.json`, `pages/<id>.raw.html`,
    `pages/<id>.rendered.html`, `findings/cw-reach-gate.json`.

```bash
python scripts/harvest_site.py "https://example.com" --workspace ./ws --max-pages 25
```

## Evidence standard

Every finding cites the exact request and result: the URL fetched, the status code, the
matched robots group and rule, the counts (`n/N pages`), and a verbatim excerpt. A
finding that cannot name what was fetched and what came back is not emitted.

## Severity

`critical` — nothing can be reached or quoted site-wide (retrieval agents disallowed,
homepage fails for a bot UA, no HTTPS, most pages nosnippet).
`high` — a major section is unreachable or unindexable.
`medium` — discovery is degraded but content is reachable.
`low` — hygiene and proactive items.
Severity is then escalated one step if the defect covers ≥50% of crawled pages or hits
the homepage, and demoted one step if it covers ≤15%.

## Output

`findings/cw-reach-gate.json` — `{skill, pillar, generated_at, checks_run,
pages_analysed, findings[], notes[]}`. Each finding: `check_id`, `title`, `severity`,
`evidence`, `pillar`, `confidence`, `suggested_action{summary, priority, steps[], effort,
owner}`, and `affected_urls` where applicable.

Plus the evidence bundle, which is this skill's real product for the rest of the
marketplace. `references/evidence-bundle-format.md` documents every field.

## Guardrails

Read-only GET requests only. robots.txt obeyed. No authenticated areas, no forms, no
POST. Bounded by pages, depth and wall-clock time. `--ignore-robots` exists solely for
auditing a site you own and must never be used on a third party's site.

## References

- `references/ai-crawler-reference.md` — every agent, its class, and what blocking it costs
- `references/reach-checks.md` — the full `REACH-*` check table with thresholds
- `references/evidence-bundle-format.md` — the evidence-bundle contract
