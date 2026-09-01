#!/usr/bin/env python3
"""
probe_render_gap.py -- READ-* checks: can a machine READ what a human sees?

Stage 2. Answers the second of the three questions that gate visibility:
reach (access) -> READ (this skill) -> extract a fact (structured data + answers).

Pure analyzer: reads the evidence bundle, performs no network I/O.

Confidence contract
-------------------
When a real renderer ran, raw-vs-rendered deltas are measured and reported at
`high` confidence. When no renderer was available, this skill does NOT guess a
delta. It falls back to a conservative signature (SPA app-shell + framework
bundle + near-empty server HTML) and marks those findings `medium`/`low`
confidence so the orchestrator can flag them as needing browser verification.
Inventing a number would be worse than admitting the limit.
"""
import argparse
import os
import re
from urllib import parse

from evidence import (load_bundle, html_pages, best_view, escalate, Findings)

# Markers that a page is a client-rendered shell rather than server-rendered HTML.
SHELL_IDS = re.compile(
    r'<(?:div|main)[^>]*\bid=["\'](?:root|app|__next|__nuxt|ember-app|application|svelte)["\'][^>]*>\s*</(?:div|main)>',
    re.I)
FRAMEWORK_BUNDLE = re.compile(
    r"(?:/_next/static/|/_nuxt/|webpack|runtime[.-][\w]+\.js|main[.-][a-f0-9]{6,}\.js"
    r"|vendors?[.-][\w]+\.js|react|vue(?:\.runtime)?|angular|svelte|ember)", re.I)
HASH_ROUTE = re.compile(r"#!?/")
TEXT_IMAGE_NAME = re.compile(
    r"(pricing|price|plan|feature|spec|comparison|table|chart|infographic|menu|"
    r"testimonial|quote|faq|hero|banner|headline|text|slide|poster)", re.I)
TRANSCRIPT_HINT = re.compile(r"transcript|captions?|subtitle|summary of (?:the )?(?:video|episode)", re.I)
VIDEO_EMBED = re.compile(r"(youtube\.com/embed|youtu\.be|player\.vimeo|wistia|loom\.com|dailymotion)", re.I)


def read_raw_html(workspace, page):
    path = os.path.join(workspace, "pages", f"{page['id']}.raw.html")
    if os.path.exists(path):
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    return ""


def main():
    ap = argparse.ArgumentParser(description="Render-parity / machine-readability analyzer")
    ap.add_argument("--workspace", required=True)
    args = ap.parse_args()

    manifest, pages = load_bundle(args.workspace)
    docs = html_pages(pages)
    find = Findings("cw-render-gap", "parse")
    for cid in ["READ-001", "READ-002", "READ-003", "READ-004", "READ-005",
                "READ-006", "READ-007", "READ-008", "READ-009", "READ-010"]:
        find.check(cid)

    if not docs:
        find.note("No successfully-fetched HTML pages in the bundle; render checks skipped.")
        find.write(args.workspace, 0)
        print("cw-render-gap: no analysable pages")
        return

    total = len(docs)
    render_meta = manifest.get("render", {})
    renderer_ok = bool(render_meta.get("available"))
    rendered_docs = [p for p in docs if p.get("render_available") and p.get("rendered")]

    # -- READ-001 / READ-002: measured raw-vs-rendered gap ---------------------
    gaps = []
    for page in rendered_docs:
        raw_words = page["raw"].get("word_count", 0)
        ren_words = page["rendered"].get("word_count", 0)
        if ren_words < 120:                     # genuinely thin page; ratio is meaningless
            continue
        ratio = raw_words / max(1, ren_words)
        if ratio < 0.6:
            gaps.append({"url": page["url"], "raw": raw_words, "rendered": ren_words,
                         "ratio": round(ratio, 2)})

    if gaps:
        worst = min(gaps, key=lambda g: g["ratio"])
        prevalence = len(gaps) / max(1, len(rendered_docs))
        base = "critical" if worst["ratio"] < 0.25 else "high"
        sample = "; ".join(f"{g['url']} ({g['raw']} raw vs {g['rendered']} rendered words, "
                           f"{int(g['ratio'] * 100)}% visible)" for g in gaps[:3])
        find.add("READ-001", "Most page content only exists after JavaScript runs",
                 escalate(base, prevalence,
                          any(p["url"] == manifest.get("start_url") for p in rendered_docs
                              if p["url"] in [g["url"] for g in gaps])),
                 f"{len(gaps)}/{len(rendered_docs)} rendered pages serve substantially less text in the "
                 f"raw server response than in the rendered DOM. {sample}. Fetchers that do not execute "
                 "JavaScript -- which includes several answer-time retrieval agents and every "
                 "plain-HTTP reader -- see only the smaller version, so the missing text cannot be "
                 "indexed or quoted.",
                 "Server-render (SSR) or pre-render the primary content so it is present in the "
                 "initial HTML response.",
                 ["Move primary copy, headings and key facts into the server-rendered HTML "
                  "(Next.js SSR/SSG, Nuxt universal mode, Astro, or a pre-render step at build time).",
                  "Keep JavaScript for enhancement (personalisation, interactivity) -- never for the "
                  "core factual content of the page.",
                  "Verify with `curl -s <url> | grep -c '<your key sentence>'` -- the fact must appear "
                  "in the raw response, not just the browser.",
                  "Re-test the pages listed above until raw and rendered word counts are within ~20%."],
                 effort="high", owner="web/dev", confidence="high",
                 affected_urls=[g["url"] for g in gaps[:20]],
                 metrics={"pages_with_gap": len(gaps), "worst_ratio": worst["ratio"]})

    # Heuristic fallback when no renderer was available.
    if not renderer_ok:
        suspects = []
        for page in docs:
            raw_html = read_raw_html(args.workspace, page)
            words = page["raw"].get("word_count", 0)
            shell = bool(SHELL_IDS.search(raw_html))
            bundles = len([s for s in page["raw"].get("scripts", [])
                           if FRAMEWORK_BUNDLE.search(s.get("src", ""))])
            if words < 150 and (shell or bundles >= 1):
                suspects.append({"url": page["url"], "words": words,
                                 "empty_shell": shell, "framework_bundles": bundles})
        if suspects:
            prevalence = len(suspects) / total
            sample = "; ".join(f"{s['url']} ({s['words']} words of server-rendered text, "
                               f"empty app shell={s['empty_shell']}, "
                               f"{s['framework_bundles']} framework bundle(s))"
                               for s in suspects[:3])
            find.add("READ-002", "Server HTML is a near-empty app shell (client-side rendering suspected)",
                     escalate("high", prevalence),
                     f"{len(suspects)}/{total} crawled pages returned under 150 words of text in the "
                     f"raw server response while loading a JavaScript framework bundle. {sample}. "
                     f"No browser renderer was available during this audit "
                     f"({render_meta.get('reason')}), so the exact raw-vs-rendered gap was not measured "
                     "-- but a page whose server response carries no prose cannot be read by a "
                     "non-executing fetcher.",
                     "Confirm with a headless browser, then server-render or pre-render the content.",
                     ["Compare `curl -s <url>` against the browser DOM to confirm the gap.",
                      "If confirmed, enable SSR/SSG so primary content ships in the first response.",
                      "As a minimum interim step, pre-render the highest-value pages "
                      "(homepage, pricing, top products) to static HTML.",
                      "Re-run this audit with Playwright installed to measure the gap precisely."],
                     effort="high", owner="web/dev", confidence="medium",
                     affected_urls=[s["url"] for s in suspects[:20]],
                     verification="Install Playwright and re-run with --render auto to confirm.")
        else:
            find.note(f"No renderer available ({render_meta.get('reason')}). Raw HTML nonetheless "
                      "contained substantial text on every crawled page, so no client-side-rendering "
                      "problem is suspected.")

    # -- READ-003: key identity facts present only after render ---------------
    identity_gaps = []
    for page in rendered_docs:
        raw, ren = page["raw"], page["rendered"]
        missing = []
        if not raw.get("title") and ren.get("title"):
            missing.append("<title>")
        raw_h1 = [h["text"] for h in raw.get("headings", []) if h["level"] == 1]
        ren_h1 = [h["text"] for h in ren.get("headings", []) if h["level"] == 1]
        if not raw_h1 and ren_h1:
            missing.append(f"H1 ('{ren_h1[0][:60]}')")
        if not raw.get("jsonld") and ren.get("jsonld"):
            missing.append("JSON-LD structured data")
        if not raw.get("prices_in_text") and ren.get("prices_in_text"):
            missing.append(f"price ({ren['prices_in_text'][0]})")
        if missing:
            identity_gaps.append({"url": page["url"], "missing": missing})
    if identity_gaps:
        sample = "; ".join(f"{g['url']} lacks {', '.join(g['missing'])} in raw HTML"
                           for g in identity_gaps[:3])
        find.add("READ-003", "Identity facts (title, H1, price, structured data) are injected by JavaScript",
                 escalate("high", len(identity_gaps) / max(1, len(rendered_docs))),
                 f"{len(identity_gaps)}/{len(rendered_docs)} rendered pages are missing at least one "
                 f"identity-defining element from the raw server response. {sample}. These are exactly "
                 "the fields a retrieval system uses to decide what a page is about, so a page that "
                 "supplies them late may be indexed as untitled and unattributed.",
                 "Emit title, H1, canonical price and JSON-LD server-side, in the initial HTML response.",
                 ["Render <title>, <meta name=description>, the H1 and rel=canonical on the server "
                  "for every route -- never via client-side document.title assignment.",
                  "Emit JSON-LD in the server response; markup injected after load is unreliable "
                  "for non-executing consumers.",
                  "Render prices and availability server-side rather than fetching them from a "
                  "client-side pricing API."],
                 effort="medium", owner="web/dev", confidence="high",
                 affected_urls=[g["url"] for g in identity_gaps[:20]])

    # -- READ-004: content locked in iframes ----------------------------------
    iframe_pages = []
    for page in docs:
        view, _ = best_view(page)
        content_frames = [f for f in view.get("iframes", [])
                          if f.get("src") and not re.search(
                              r"(google(tagmanager|analytics)|doubleclick|facebook\.com/tr|"
                              r"hotjar|intercom|drift|recaptcha|consent|cookiebot|onetrust)",
                              f["src"], re.I)]
        if content_frames and view.get("word_count", 0) < 400:
            iframe_pages.append({"url": page["url"], "frames": [f["src"][:120] for f in content_frames[:3]],
                                 "words": view.get("word_count", 0)})
    if iframe_pages:
        sample = "; ".join(f"{p['url']} ({p['words']} own words, embeds {p['frames'][0]})"
                           for p in iframe_pages[:3])
        find.add("READ-004", "Primary content is delivered inside third-party iframes",
                 escalate("medium", len(iframe_pages) / total),
                 f"{len(iframe_pages)}/{total} pages carry a non-tracking iframe while holding under 400 "
                 f"words of their own text. {sample}. Content inside an iframe is a separate document: "
                 "it is attributed to the embedded origin, not to this page, so this page earns no "
                 "citation for it.",
                 "Move the substantive content into the host page's own HTML and keep the iframe for "
                 "interactive widgets only.",
                 ["Reproduce the key facts (schedules, listings, menus, documentation) as native HTML "
                  "in the parent page, even if the iframe stays for interaction.",
                  "Where a third-party tool owns the data, pull it server-side via API and render it "
                  "into your own markup.",
                  "Add a text summary above every retained embed so the page has quotable content."],
                 effort="medium", owner="web/dev", confidence="medium",
                 affected_urls=[p["url"] for p in iframe_pages[:20]])

    # -- READ-005: facts locked in images -------------------------------------
    image_text_pages = []
    for page in docs:
        view = page["raw"]
        images = view.get("images", [])
        if not images:
            continue
        no_alt = [i for i in images if not (i.get("alt") or "").strip()]
        texty = [i for i in images if TEXT_IMAGE_NAME.search(i.get("src") or "")
                 and len((i.get("alt") or "").strip()) < 25]
        words = view.get("word_count", 0)
        # Fire only on a genuine substitution signal: named-as-content images with
        # no descriptive alt, on a page that is otherwise text-poor.
        if texty and words < 350:
            image_text_pages.append({"url": page["url"], "words": words,
                                     "images": [i["src"][:100] for i in texty[:3]],
                                     "no_alt": len(no_alt), "total": len(images)})
    if image_text_pages:
        sample = "; ".join(f"{p['url']} ({p['words']} words of text, content-named image "
                           f"'{p['images'][0]}' with no descriptive alt)" for p in image_text_pages[:3])
        find.add("READ-005", "Key facts appear to be published as images rather than text",
                 escalate("high", len(image_text_pages) / total),
                 f"{len(image_text_pages)}/{total} pages are text-poor (<350 words) while carrying "
                 f"images whose filenames indicate they hold content (pricing, features, "
                 f"specifications, menus) and whose alt text is absent or under 25 characters. "
                 f"{sample}. Text baked into an image cannot be indexed, quoted or cited -- a human "
                 "reads it, a machine sees an opaque binary.",
                 "Re-publish the information as HTML text; keep the image as a visual companion.",
                 ["Transcribe pricing tables, spec sheets and menus into real HTML "
                  "<table>/<dl> markup.",
                  "Give every informative image descriptive alt text that states the fact, not the "
                  "filename (alt=\"Pro plan: $49 per user per month\", not alt=\"pricing\").",
                  "Where an infographic must stay, add a text summary or <figcaption> restating its "
                  "conclusion in prose.",
                  "Mirror the same numbers in JSON-LD (Offer, Product) so they are machine-readable twice."],
                 effort="medium", owner="content", confidence="medium",
                 affected_urls=[p["url"] for p in image_text_pages[:20]])

    # -- READ-006: facts locked in documents / video --------------------------
    doc_links = manifest.get("document_links", [])
    if doc_links:
        hosts = {}
        for link in doc_links:
            hosts.setdefault(parse.urlparse(link["href"]).path.split("/")[-1][:60], link)
        sample = "; ".join(f"'{l['text'] or l['href'].split('/')[-1]}' linked from {l['from']}"
                           for l in list(hosts.values())[:3])
        find.add("READ-006", "Substantive content is distributed as downloadable documents",
                 "medium" if len(doc_links) >= 3 else "low",
                 f"{len(doc_links)} link(s) to PDF/Office documents were found in the crawled sample. "
                 f"{sample}. Retrieval systems parse HTML far more reliably than binaries, and a "
                 "scanned or image-based PDF carries no extractable text at all -- so facts that live "
                 "only in these files are effectively unpublished.",
                 "Publish an HTML version of every document whose content matters for discovery.",
                 ["Create an HTML landing page per document that reproduces its key facts as text.",
                  "Link the file from that page rather than linking the binary directly from navigation.",
                  "Ensure any retained PDFs contain a real text layer (not a scan) and have a title.",
                  "Prioritise: spec sheets, price lists, whitepapers and reports people search for."],
                 effort="medium", owner="content", confidence="medium")

    video_pages = []
    for page in docs:
        raw = page["raw"]
        embeds = [f["src"] for f in raw.get("iframes", []) if VIDEO_EMBED.search(f.get("src") or "")]
        has_video = embeds or raw.get("counts", {}).get("video", 0) > 0
        if has_video and not TRANSCRIPT_HINT.search(raw.get("text", "")[:20000]) \
                and raw.get("word_count", 0) < 500:
            video_pages.append({"url": page["url"], "words": raw.get("word_count", 0)})
    if video_pages:
        find.add("READ-007", "Video-led pages have no transcript or text equivalent",
                 escalate("medium", len(video_pages) / total),
                 f"{len(video_pages)}/{total} pages embed video, carry under 500 words of text and "
                 f"contain no transcript/caption marker. Example: {video_pages[0]['url']} "
                 f"({video_pages[0]['words']} words). Whatever the video says is unreadable to a "
                 "retrieval system, so the page cannot be cited for any claim the video makes.",
                 "Publish a full text transcript alongside every substantive video.",
                 ["Add the transcript as visible HTML text on the same page (collapsed is fine -- "
                  "it must be in the DOM, not loaded on click).",
                  "Add VideoObject JSON-LD with `transcript`, `description` and `uploadDate`.",
                  "Lead with a short text summary of the video's key points above the player."],
                 effort="low", owner="content", confidence="medium",
                 affected_urls=[p["url"] for p in video_pages[:20]])

    # -- READ-008: noscript gap ------------------------------------------------
    noscript_bad = [p for p in docs
                    if p["raw"].get("word_count", 0) < 150
                    and len((p["raw"].get("noscript_text") or "").split()) < 10
                    and p["raw"].get("counts", {}).get("script", 0) >= 3]
    if noscript_bad and not renderer_ok:
        find.add("READ-008", "No <noscript> fallback on script-dependent pages", "low",
                 f"{len(noscript_bad)}/{total} pages load 3+ scripts, serve under 150 words of text and "
                 f"provide no meaningful <noscript> content. Example: {noscript_bad[0]['url']}. A "
                 "<noscript> block is not a fix for client-side rendering, but its absence removes the "
                 "last fallback for a non-executing reader.",
                 "Treat this as a symptom: fix the rendering, and add a <noscript> summary as a backstop.",
                 ["Prioritise server-rendering the content (see the render-parity findings above).",
                  "Add a <noscript> block containing the page's core message and primary links."],
                 effort="low", owner="web/dev", confidence="low",
                 affected_urls=[p["url"] for p in noscript_bad[:20]])

    # -- READ-009: non-semantic structure --------------------------------------
    no_landmark, bad_hierarchy, no_h1, multi_h1 = [], [], [], []
    for page in docs:
        view, _ = best_view(page)
        heads = view.get("headings", [])
        levels = [h["level"] for h in heads]
        if not view.get("has_main") and not view.get("has_article"):
            no_landmark.append(page["url"])
        h1s = [h for h in heads if h["level"] == 1 and h["text"].strip()]
        if not h1s:
            no_h1.append(page["url"])
        elif len(h1s) > 1:
            multi_h1.append(page["url"])
        for prev, cur in zip(levels, levels[1:]):
            if cur - prev > 1:
                bad_hierarchy.append(page["url"])
                break

    if no_h1:
        homepage_affected = manifest.get("start_url") in no_h1
        # State WHY the severity is what it is. Reporting "1/12 pages" at high severity
        # without explaining the homepage escalation makes the finding look miscalibrated,
        # and a reader who distrusts one severity discounts all of them.
        escalation_note = (" Severity is raised one step because the affected set includes the "
                           "homepage, which is the page an assistant and a first-time visitor "
                           "are most likely to hit."
                           if homepage_affected else "")
        find.add("READ-009", "Pages have no H1 heading",
                 escalate("medium", len(no_h1) / total, homepage_affected),
                 f"{len(no_h1)}/{total} crawled pages contain no non-empty <h1>. Examples: "
                 f"{', '.join(no_h1[:3])}.{escalation_note} The H1 is the strongest single signal of what a page is "
                 "about and is commonly used as the label for a retrieved chunk; without one, the "
                 "page is harder to classify and harder to attribute in an answer.",
                 "Give every page exactly one descriptive H1 that names the topic in plain language.",
                 ["Add a single <h1> per page stating the specific subject "
                  "(\"<Product> pricing\", not \"Welcome\").",
                  "Make the H1 match the page's title and search intent.",
                  "Do not use the site logo or navigation as the H1."],
                 effort="low", owner="content", affected_urls=no_h1[:20])

    if len(no_landmark) >= max(2, total * 0.6):
        find.add("READ-010", "No semantic landmarks (<main>/<article>) to separate content from boilerplate",
                 "medium",
                 f"{len(no_landmark)}/{total} pages use no <main> or <article> element. Extractors use "
                 "landmarks to tell the article apart from navigation, sidebars and footers; without "
                 "them, boilerplate is mixed into every retrieved chunk, diluting relevance and "
                 "causing navigation text to be quoted as if it were content.",
                 "Wrap the unique content of each page in <main> (and <article> for standalone pieces).",
                 ["Wrap the primary content region in a single <main> element per page.",
                  "Use <article> for self-contained items (blog posts, products, listings).",
                  "Move navigation into <nav>, and site-wide chrome into <header>/<footer>.",
                  "Keep the heading hierarchy inside <main> ordered h1 -> h2 -> h3 without skips."],
                 effort="low", owner="web/dev", affected_urls=no_landmark[:20])

    if bad_hierarchy and len(bad_hierarchy) >= max(2, total * 0.4):
        find.add("READ-011", "Heading levels skip, breaking document structure",
                 "low",
                 f"{len(bad_hierarchy)}/{total} pages skip heading levels (for example h2 followed "
                 f"directly by h4). Examples: {', '.join(bad_hierarchy[:3])}. Chunking algorithms use "
                 "the heading tree to decide where a passage starts and ends, so a broken tree "
                 "produces passages that begin mid-thought.",
                 "Use heading levels strictly in order to describe the real document outline.",
                 ["Fix skipped levels so headings descend one step at a time.",
                  "Choose heading level by structural depth, never by font size -- style with CSS.",
                  "Ensure every major section has a heading rather than a styled <div>."],
                 effort="low", owner="content", affected_urls=bad_hierarchy[:20])

    # -- READ-012: hash routing ------------------------------------------------
    hash_routed = [p["url"] for p in docs if HASH_ROUTE.search(p["url"])]
    if hash_routed:
        find.add("READ-012", "Content is addressed by hash-based client-side routes", "high",
                 f"{len(hash_routed)} crawled URL(s) use a fragment route (e.g. {hash_routed[0]}). "
                 "Fragments are never sent to the server, so every such route resolves to the same "
                 "document for a crawler: the individual views cannot be indexed, linked to, or cited "
                 "separately.",
                 "Migrate to real server-addressable paths (History API routing with SSR).",
                 ["Replace `#/route` URLs with real paths (`/route`) served by the server.",
                  "Ensure each path returns its own fully-rendered HTML with a unique title and canonical.",
                  "301-redirect the old fragment URLs where the framework allows it."],
                 effort="high", owner="web/dev", affected_urls=hash_routed[:20])

    find.write(args.workspace, total)
    print(f"cw-render-gap: {len(find.items)} finding(s) across {total} page(s); "
          f"renderer={'available' if renderer_ok else 'unavailable (heuristic mode)'}")


if __name__ == "__main__":
    main()
