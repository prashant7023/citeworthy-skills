# Evidence bundle format

The contract between `crawl-access-audit` and every other skill in the marketplace.

## Why a bundle exists

The alternative design — each skill fetching what it needs — would mean six skills
crawling the same site six times. That is slower, ruder to the site, and
non-deterministic: two skills fetching the same URL a minute apart can disagree, and the
report would have no single set of facts to point at.

The bundle inverts this. **One skill touches the network; every other skill is a pure
function of the bundle.** The consequences are worth stating because they are the
engineering argument for the whole decomposition:

- **Deterministic.** Re-running any analyzer on a saved bundle reproduces its findings
  exactly. Bugs are reproducible without re-crawling.
- **Fast.** The network cost of the entire audit is one polite crawl; analysis is
  local and takes seconds.
- **Auditable.** Every finding's evidence traces to a stored artefact — the raw HTML is
  on disk and can be re-read to check any claim.
- **Testable.** Analyzers can be tested against hand-built fixture bundles with no network
  at all. See `tests/`.

## Layout

```
<workspace>/
  manifest.json                 crawl metadata, robots analysis, sitemap analysis, page index
  pages/<id>.json               normalised parsed-page record (the primary input)
  pages/<id>.raw.html           raw server HTML, verbatim, capped at 1.5 MB
  pages/<id>.rendered.html      post-JavaScript DOM, when a renderer was available
  findings/<skill>.json         one file per skill that has run
  probe_plan.json               written by entity-corroboration-audit phase 1
  probe_results.json            written by the AGENT from real search results
  report.json / report.md       written by the entrypoint
  run_log.json                  per-stage timings and exit codes (conduct_audit.py only)
```

`<id>` is the first 16 hex characters of the SHA-1 of the URL: stable across runs, so the
same page maps to the same file every time.

## manifest.json

| Field | Meaning |
|---|---|
| `site`, `site_root`, `start_url` | Canonical host after redirect resolution, registrable domain, entry URL. |
| `crawled_at`, `finished_at`, `duration_seconds` | Timing. |
| `user_agent`, `limits` | Exactly what was sent and what bounds applied. |
| `pages[]` | Index: `{id, url, status, page_type, depth, word_count, render_available}`. |
| `page_count`, `ok_page_count`, `page_types` | Sample shape — how analyzers judge prevalence. |
| `robots` | `status`, `present`, `sitemaps_declared`, `parse_errors[]`, `raw_excerpt`, and `agent_verdicts` — a per-agent `{allowed_root, matched_group, rule, class}` map. |
| `sitemaps[]`, `sitemap_url_count`, `sitemap_lastmods[]`, `sitemap_urls_sample[]` | Sitemap analysis; `lastmods` feeds the fake-freshness check. |
| `llms_txt` | `{present, status}`. |
| `document_links[]` | Links to PDF/Office files, with source page and anchor text. |
| `render` | `{available, reason, engine, attempted_urls}` — **the honesty record.** Analyzers read this to decide measured vs. heuristic mode. |
| `blocked_by_robots[]` | URLs skipped, with the matching rule. |
| `skipped_private_urls[]` | Auth/transactional URLs deliberately not fetched. |
| `notes[]` | Human-readable caveats. |

## pages/&lt;id&gt;.json

```jsonc
{
  "id", "url", "final_url", "status", "redirects": [{from, to, status}],
  "headers": {}, "content_type", "declared_charset", "fetch_ms", "bytes",
  "depth", "non_html", "page_type", "page_tags": [],
  "raw":      { /* parsed-page record, from server HTML */ },
  "rendered": { /* same shape, from post-JS DOM, or null */ },
  "render_available": false,
  "network_error": null
}
```

### The parsed-page record (`raw` and `rendered` share this shape)

| Field | Notes |
|---|---|
| `title`, `lang`, `canonical`, `viewport`, `robots_meta` | Head-level identity. |
| `meta`, `og`, `twitter`, `hreflang` | Tag maps. |
| `headings[]` | `{level, text}` in document order. |
| `sections[]` | `{heading, level, text, word_count}` — the page split at h1–h3. **This is the unit the extractability checks reason about**, because it approximates a retrieval chunk. |
| `text`, `word_count` | Full visible text, script/style excluded. |
| `main_text`, `main_word_count`, `main_text_ratio` | Text inside `<main>`/`<article>`. Used for the boilerplate ratio; analyzers fall back to `text` when a page has no landmark, since many real sites do not use them. |
| `first_screen_text` | First ~60 text nodes **excluding `<nav>`, `<header>` and `<footer>`**, and excluding `aria-hidden`/`hidden` subtrees. Approximates above-the-fold content. The chrome exclusion matters: menu labels are identical on every page, so counting them makes a blank hero look informative. |
| `noscript_text` | `<noscript>` content, captured separately. |
| `links[]` | `{href, abs, text, rel, aria_label, target}`. `abs` is resolved and defragmented; empty for `javascript:`/`mailto:`/`tel:`/fragment links. `text` is entity-decoded and whitespace-collapsed. |
| `images[]` | `{src, alt, width, height, loading, in_first_screen}`. `alt` is `null` when the attribute is absent and `""` when present-but-empty — a distinction the accessibility check depends on. |
| `iframes[]`, `scripts[]`, `stylesheets[]`, `forms[]` | `scripts` records `async`/`defer`/`module`, which drives the render-blocking count. `forms` records field types, `required` and whether each field is labelled. |
| `jsonld[]`, `jsonld_raw_count`, `jsonld_errors[]` | Parsed JSON-LD, plus per-block parse errors with message, line, column and surrounding snippet. `raw_count` vs `len(jsonld)` reveals how many blocks were lost to syntax errors. |
| `microdata_types[]`, `rdfa_types[]` | So a site using microdata is not falsely reported as having no structured data. |
| `counts{}` | Tag counts (`table`, `li`, `p`, `video`, `input`, `dialog`, `script`, `img`, `a`, …). |
| `has_main`, `has_article`, `has_header`, `has_footer`, `has_skip_link` | Landmark flags. |
| `dates_in_text[]`, `copyright_years[]`, `years_mentioned[]` | Three date formats, plus footer copyright. |
| `emails[]`, `prices_in_text[]` | Contact and currency extraction, for fact-coverage and markup-drift checks. |
| `html_bytes`, `text_to_html_ratio` | Payload measures. |

## findings/&lt;skill&gt;.json

```jsonc
{
  "skill", "pillar", "generated_at",
  "checks_run": ["QUOTE-001", ...],   // every check attempted, whether or not it fired
  "pages_analysed": 25,
  "findings": [ /* see report-schema.json */ ],
  "notes": ["Definitional sentence found: \"...\" (https://...)"]
}
```

`checks_run` is what lets the final report distinguish **clean** from **never looked** —
the difference between "we checked and it is fine" and "we did not check". `notes` records
what satisfied a check that passed, so a reader can verify the check genuinely ran.

## Stability

`schema_version` in `manifest.json` is `1.0`. Analyzers read defensively (`.get()` with
defaults) so an older bundle never crashes a newer analyzer; it degrades to fewer checks.
