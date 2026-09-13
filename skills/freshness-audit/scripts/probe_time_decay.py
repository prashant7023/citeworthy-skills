#!/usr/bin/env python3
"""
probe_time_decay.py -- TIME-* checks: is the fact still true, and can a machine tell?

Stage 4a. Retrieval systems weight recency heavily, because a confidently stated
stale fact is the failure mode users punish hardest. Two distinct problems live
here and are often confused:

  undatable  -- the content may be current, but nothing on the page says so, so a
                recency-weighted retriever cannot prefer it and defaults to
                assuming it is old
  stale      -- the content demonstrably refers to a world that has moved on

A third, subtler one: *dishonest* freshness signals (every sitemap lastmod set to
today, a dateModified that bumps on every deploy). Those get discounted once
detected, which devalues the honest signals alongside them.

Pure analyzer: reads the evidence bundle, performs no network I/O.
"""
import argparse
import re
from collections import Counter
from datetime import datetime, timezone

from evidence import load_bundle, html_pages, best_view, body_text, escalate, Findings

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}

ISO = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")
LONG = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+([0-3]?\d),?\s+((?:19|20)\d{2})\b", re.I)
DMY = re.compile(r"\b([0-3]?\d)\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?,?\s+((?:19|20)\d{2})\b", re.I)

# Phrases that make a page's currency a load-bearing claim.
# "Latest", "current" and "today" are marketing vocabulary on nearly every product page;
# these phrases instead stake a fact on a point in time.
TIME_SENSITIVE = re.compile(
    r"\b(as of|this year|last updated|up[- ]to[- ]date|"
    r"currently (?:priced|available|supports?|offers?)|"
    r"latest (?:version|release|update|figures|data|rates?|prices?)|20\d\d roadmap|"
    r"(?:rates?|prices?|fees?) (?:effective|valid) (?:from|until))\b", re.I)
VERSION_CLAIM = re.compile(r"\b(?:v(?:ersion)?\s?\d+(?:\.\d+)*|release \d)", re.I)


def parse_dates(text):
    """Extract dates from prose as (year, month, day) tuples."""
    out = []
    for match in ISO.finditer(text):
        try:
            out.append(datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)),
                                tzinfo=timezone.utc))
        except ValueError:
            pass
    for match in LONG.finditer(text):
        try:
            out.append(datetime(int(match.group(3)), MONTHS[match.group(1).lower()[:3]],
                                int(match.group(2)), tzinfo=timezone.utc))
        except (ValueError, KeyError):
            pass
    for match in DMY.finditer(text):
        try:
            out.append(datetime(int(match.group(3)), MONTHS[match.group(2).lower()[:3]],
                                int(match.group(1)), tzinfo=timezone.utc))
        except (ValueError, KeyError):
            pass
    return out


def schema_dates(view):
    """Dates declared in JSON-LD (datePublished / dateModified / uploadDate)."""
    found = {}

    def walk(node):
        if isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            for key in ("datePublished", "dateModified", "uploadDate", "datePosted"):
                value = node.get(key)
                if isinstance(value, str):
                    parsed = parse_dates(value[:40])
                    if parsed:
                        found.setdefault(key, []).append(parsed[0])
            for value in node.values():
                if isinstance(value, (dict, list)):
                    walk(value)
    walk(view.get("jsonld", []))
    return found


def months_between(a, b):
    return (b.year - a.year) * 12 + (b.month - a.month)


def main():
    ap = argparse.ArgumentParser(description="Content freshness and temporal-consistency analyzer")
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--now", default=None, help="ISO date to treat as 'today' (for deterministic tests)")
    args = ap.parse_args()

    now = datetime.fromisoformat(args.now).replace(tzinfo=timezone.utc) if args.now \
        else datetime.now(timezone.utc)
    manifest, pages = load_bundle(args.workspace)
    docs = html_pages(pages)
    find = Findings("freshness-audit", "trust")
    for cid in [f"TIME-{n:03d}" for n in range(1, 9)]:
        find.check(cid)

    if not docs:
        find.note("No successfully-fetched HTML pages in the bundle; freshness checks skipped.")
        find.write(args.workspace, 0)
        print("freshness-audit: no analysable pages")
        return

    total = len(docs)

    # -- TIME-001: time-sensitive pages carry no date -------------------------
    # Product, pricing and landing pages are evergreen by design: "the latest model" is
    # marketing, not a currency claim a retriever needs dated. Articles and docs are
    # dated by nature; other pages only when their prose stakes a claim on time.
    evergreen = ("homepage", "product", "pricing", "listing", "legal", "contact", "careers", "about")
    dateable = [p for p in docs if p.get("page_type") in ("article", "docs")
                or (p.get("page_type") not in evergreen
                    and TIME_SENSITIVE.search(body_text(best_view(p)[0])[:6000]))]
    undated = []
    for page in dateable:
        view, _ = best_view(page)
        has_visible = bool(view.get("dates_in_text"))
        has_schema = bool(schema_dates(view))
        has_meta = any(k in view.get("meta", {}) for k in
                       ("article:published_time", "article:modified_time", "date", "last-modified"))
        has_og = "article:published_time" in view.get("og", {})
        if not (has_visible or has_schema or has_meta or has_og):
            undated.append(page["url"])
    if undated:
        find.add("TIME-001", "Time-sensitive pages publish no date at all",
                 escalate("medium", len(undated) / max(1, len(dateable)), sample=len(dateable)),
                 f"{len(undated)}/{len(dateable)} pages that make currency claims (or are articles "
                 f"or docs) expose no date in visible text, meta tags or "
                 f"structured data. Examples: {', '.join(undated[:3])}. A retriever that cannot date a "
                 "page cannot prefer it for a recency-sensitive question, and typically treats "
                 "undated content as older than dated competitors.",
                 "Publish a visible last-updated date plus machine-readable date properties.",
                 ["Show 'Published <date>' and 'Last updated <date>' near the top of the content.",
                  "Emit datePublished and dateModified in the page's Article/Product JSON-LD.",
                  "Set the dates from real content-change events, not from the deploy timestamp.",
                  "For evergreen pages, still stamp a review date "
                  "('Reviewed <month year>') so currency is claimable."],
                 effort="low", owner="content", affected_urls=undated[:20])

    # -- TIME-002: newest content is old --------------------------------------
    article_pages = [p for p in docs if p.get("page_type") == "article"]
    newest = None
    per_page_latest = {}
    for page in docs:
        view, _ = best_view(page)
        candidates = parse_dates(" ".join(view.get("dates_in_text", [])))
        for values in schema_dates(view).values():
            candidates += values
        plausible = [d for d in candidates if 2005 <= d.year <= now.year + 1 and d <= now]
        if plausible:
            latest = max(plausible)
            per_page_latest[page["url"]] = latest
            newest = latest if newest is None else max(newest, latest)

    if article_pages and newest:
        age = months_between(newest, now)
        if age >= 12:
            find.add("TIME-002", "The most recent dated content is over a year old",
                     "high" if age >= 24 else "medium",
                     f"The newest date found anywhere in the crawled sample is "
                     f"{newest.date().isoformat()} ({age} months ago), across {len(article_pages)} "
                     "article page(s). A site whose freshest signal is a year stale is systematically "
                     "outranked for any question with a current-state reading, and readers who do "
                     "arrive read the dates and discount the whole site.",
                     "Restart a visible publishing cadence and refresh the highest-traffic pages first.",
                     ["Set a realistic cadence you can sustain (even monthly) and publish to it.",
                      "Refresh existing high-value pages with current figures, then update dateModified "
                      "honestly.",
                      "Add a changelog or 'what's new' page -- a single reliably-dated surface gives "
                      "the whole site a current signal.",
                      "Remove or clearly archive content that is no longer accurate."],
                     effort="high", owner="content",
                     metrics={"newest_date": newest.date().isoformat(), "age_months": age})

    stale_pages = [(u, d) for u, d in per_page_latest.items() if months_between(d, now) >= 24]
    if stale_pages and len(stale_pages) >= max(2, len(per_page_latest) * 0.5):
        oldest = min(stale_pages, key=lambda kv: kv[1])
        find.add("TIME-003", "Most dated pages have not been touched in two years",
                 "medium",
                 f"{len(stale_pages)}/{len(per_page_latest)} dated pages carry a latest date 24+ months "
                 f"old. Oldest: {oldest[0]} ({oldest[1].date().isoformat()}). Bulk staleness signals an "
                 "unmaintained site, which lowers the whole domain's standing as a source even for "
                 "the pages that are still accurate.",
                 "Run a content refresh pass over the stale set, updating or retiring each page.",
                 ["Triage stale pages into: refresh, consolidate, or retire with a 301.",
                  "Update figures, screenshots, product names and links on the pages you keep.",
                  "Only bump dateModified where the content genuinely changed."],
                 effort="high", owner="content",
                 affected_urls=[u for u, _ in stale_pages[:20]])

    # -- TIME-004: stale copyright -------------------------------------------
    stale_copyright = []
    for page in docs:
        view, _ = best_view(page)
        years = view.get("copyright_years", [])
        if years and max(years) < now.year - 1:
            stale_copyright.append((page["url"], max(years)))
    if stale_copyright:
        find.add("TIME-004", "The site-wide copyright year is out of date",
                 escalate("low", len(stale_copyright) / total),
                 f"{len(stale_copyright)}/{total} pages show a copyright year of "
                 f"{stale_copyright[0][1]} (current year: {now.year}). Example: {stale_copyright[0][0]}. "
                 "It is a small thing that appears on every page, and it is one of the first "
                 "abandonment cues a visitor reads -- and a cheap staleness signal for a machine.",
                 "Render the copyright year dynamically from the server clock.",
                 ["Replace the hard-coded year with a generated value in the footer template.",
                  "While there, verify the footer's address, phone number and social links are current."],
                 effort="low", owner="web/dev", affected_urls=[u for u, _ in stale_copyright[:20]])

    # -- TIME-005: contradictory dates ---------------------------------------
    contradictions = []
    for page in docs:
        view, _ = best_view(page)
        schema = schema_dates(view)
        published = schema.get("datePublished", [])
        modified = schema.get("dateModified", [])
        if published and modified and max(modified) < min(published):
            contradictions.append({"url": page["url"], "kind": "dateModified before datePublished",
                                   "detail": f"{max(modified).date()} < {min(published).date()}"})
        visible = [d for d in parse_dates(" ".join(view.get("dates_in_text", [])))
                   if d.year >= 2005 and d <= now]
        if visible and (published or modified):
            declared = max(published + modified)
            if abs(months_between(max(visible), declared)) >= 12:
                contradictions.append({
                    "url": page["url"], "kind": "visible date disagrees with structured data",
                    "detail": f"page shows {max(visible).date()}, markup declares {declared.date()}"})
    if contradictions:
        sample = "; ".join(f"{c['url']}: {c['kind']} ({c['detail']})" for c in contradictions[:3])
        find.add("TIME-005", "Dates on the page contradict the dates in the markup", "medium",
                 f"{len(contradictions)} temporal contradiction(s) found. {sample}. When the visible "
                 "date and the machine-readable date disagree, a consumer cannot tell which to trust "
                 "and typically discounts both -- so a page that is genuinely current loses the "
                 "benefit of being current.",
                 "Derive every displayed and marked-up date from one field in the content model.",
                 ["Render the visible date and the JSON-LD date from the same source value.",
                  "Ensure dateModified is always >= datePublished.",
                  "Use ISO-8601 with a timezone in structured data.",
                  "Add a test asserting the rendered date equals the marked-up date."],
                 effort="low", owner="web/dev",
                 affected_urls=sorted({c["url"] for c in contradictions})[:20])

    # -- TIME-006: fake freshness in the sitemap ------------------------------
    lastmods = manifest.get("sitemap_lastmods", [])
    if len(lastmods) >= 10:
        days = Counter(l[:10] for l in lastmods if len(l) >= 10)
        top_day, top_count = days.most_common(1)[0]
        share = top_count / len(lastmods)
        if share > 0.9:
            find.add("TIME-006", "Every sitemap entry claims the same modification date", "low",
                     f"{top_count}/{len(lastmods)} sitemap <lastmod> values are '{top_day}' "
                     f"({int(share * 100)}%). This is the signature of a build timestamp rather than "
                     "real content-change tracking. Crawlers detect the pattern, stop trusting the "
                     "field, and fall back to their own change estimates -- so genuinely updated pages "
                     "lose the priority the sitemap was supposed to give them.",
                     "Emit <lastmod> from real per-page content-modification timestamps.",
                     ["Store a content_updated_at per page and use it for <lastmod>.",
                      "Do not update it on deploys, template changes or unrelated edits.",
                      "Drop <lastmod> entirely rather than emitting a build timestamp -- an absent "
                      "field is better than a discredited one."],
                     effort="medium", owner="web/dev")

    # -- TIME-007: no updates surface ----------------------------------------
    has_news_surface = any(p.get("page_type") == "article" for p in docs) or \
        any(re.search(r"/(blog|news|changelog|releases?|updates?|press)\b", p["url"], re.I)
            for p in docs)
    linked_news = any(re.search(r"/(blog|news|changelog|releases?|updates?|press)\b",
                                link.get("abs", ""), re.I)
                      for p in docs for link in best_view(p)[0].get("links", []))
    if not has_news_surface and not linked_news and total >= 4:
        find.add("TIME-007", "The site has no regularly-updated surface", "medium",
                 f"No blog, news, changelog, releases or press section was found among {total} crawled "
                 "pages or in their internal links. Sites with no changing surface give crawlers no "
                 "reason to return often, so new information takes far longer to reach the indexes "
                 "assistants query -- and there is no dateable proof the business is still active.",
                 "Add one reliably-updated, dated surface and link it from the main navigation.",
                 ["Pick the lowest-effort format you will actually sustain: a changelog often beats "
                  "a blog nobody writes.",
                  "Date every entry and mark it up with Article/BlogPosting JSON-LD.",
                  "Link it from the primary navigation and include it in the sitemap.",
                  "Announce substantive updates there first, then link from the affected product pages."],
                 effort="medium", owner="content")

    # -- TIME-008: content anchored to a past year ---------------------------
    outdated_refs = []
    for page in docs:
        view, _ = best_view(page)
        text = body_text(view)
        title_and_heads = (view.get("title") or "") + " " + \
            " ".join(h["text"] for h in view.get("headings", []))
        years = [int(y) for y in re.findall(r"\b(20[0-4]\d)\b", title_and_heads)]
        past = [y for y in years if y <= now.year - 2]
        if past and TIME_SENSITIVE.search(text[:8000]):
            outdated_refs.append({"url": page["url"], "year": max(past),
                                  "where": "title/heading"})
    if outdated_refs:
        sample = "; ".join(f"{o['url']} (references {o['year']} in its {o['where']})"
                           for o in outdated_refs[:3])
        find.add("TIME-008", "Pages present a past year as if it were current",
                 escalate("medium", len(outdated_refs) / total),
                 f"{len(outdated_refs)}/{total} pages combine present-tense currency language "
                 f"('latest', 'current', 'now') with a year at least two years past in the title or a "
                 f"heading. {sample}. A page titled for an old year loses to a competitor titled for "
                 "the current one on every query where the user means 'now'.",
                 "Update or re-scope the year-anchored pages.",
                 ["For pages meant to stay current, remove the year from the title and add a visible "
                  "'last updated' date instead.",
                  "For genuinely annual content, publish a new page per year and link the previous "
                  "one as an archive.",
                  "Update the body figures at the same time -- retitling stale content is worse than "
                  "leaving it dated."],
                 effort="medium", owner="content",
                 affected_urls=[o["url"] for o in outdated_refs[:20]])

    find.write(args.workspace, total)
    print(f"freshness-audit: {len(find.items)} finding(s) across {total} page(s)")


if __name__ == "__main__":
    main()
