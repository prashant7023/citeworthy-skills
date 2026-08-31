#!/usr/bin/env python3
"""
probe_check_liveness.py -- prove every check CAN fire.

The companion to verify_citeworthy.py. That suite asks "does the tool behave
correctly?"; this one asks a different question the rubric cares about just as
much: **is any check dead?**

A check that never fires on any real site is ambiguous. It might be correctly
silent because the sites were healthy, or it might be broken -- over-guarded,
mis-wired after a refactor, or referencing a field that no longer exists. Silence
alone cannot tell you which, and a dead check is a guaranteed miss that no amount
of real-site testing will reveal.

So for each check, this builds a fixture deliberately designed to trigger it and
asserts that it does. Anything reported NOT LIVE is dead code, not strictness.

Offline. No network. Run:  python tests/probe_check_liveness.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS = os.path.join(ROOT, "skills")
PY = sys.executable
sys.path.insert(0, os.path.join(SKILLS, "cw-reach-gate", "scripts"))
import harvest_site as crawler  # noqa: E402

NAV = "<nav>" + "".join(f'<a href="/n{i}">Section {i}</a>' for i in range(8)) + "</nav>"
VIEWPORT = '<meta name="viewport" content="width=device-width, initial-scale=1">'

ANALYZERS = {
    "READ": ("cw-render-gap", "probe_render_gap.py", []),
    "MARK": ("cw-schema-truth", "probe_schema_truth.py", []),
    "QUOTE": ("cw-quotability", "probe_quotability.py", []),
    "TIME": ("cw-time-decay", "probe_time_decay.py", []),
    "STAY": ("cw-arrival-experience", "probe_arrival.py", []),
    "ENTITY": ("cw-entity-consensus", "probe_entity_consensus.py", ["--phase", "merge"]),
}


def build_bundle(workspace, pages, sitemap_lastmods=None, renderer=False):
    """pages: list of (url, raw_html, page_type, depth, rendered_html_or_None)."""
    os.makedirs(os.path.join(workspace, "pages"), exist_ok=True)
    os.makedirs(os.path.join(workspace, "findings"), exist_ok=True)
    index = []
    for url, html, ptype, depth, rendered in pages:
        pid = crawler.page_id(url)
        parsed = crawler.parse_html(html, url)
        rec = {
            "id": pid, "url": url, "final_url": url, "status": 200, "redirects": [],
            "headers": {"content-type": "text/html"}, "content_type": "text/html",
            "declared_charset": "utf-8", "fetch_ms": 100, "bytes": len(html),
            "depth": depth, "non_html": False, "page_type": ptype, "page_tags": [ptype],
            "raw": parsed,
            "rendered": crawler.parse_html(rendered, url) if rendered else None,
            "render_available": bool(rendered), "network_error": None,
        }
        with open(os.path.join(workspace, "pages", f"{pid}.json"), "w", encoding="utf-8") as fh:
            json.dump(rec, fh)
        with open(os.path.join(workspace, "pages", f"{pid}.raw.html"), "w", encoding="utf-8") as fh:
            fh.write(html)
        index.append({"id": pid, "url": url, "status": 200, "page_type": ptype,
                      "depth": depth, "word_count": parsed["word_count"],
                      "render_available": bool(rendered)})
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    manifest = {
        "schema_version": "1.0", "site": "acme.example", "site_root": "acme.example",
        "start_url": pages[0][0], "crawled_at": now, "finished_at": now,
        "duration_seconds": 1.0, "user_agent": "liveness", "limits": {},
        "pages": index, "page_count": len(index), "ok_page_count": len(index),
        "page_types": {p["page_type"]: 1 for p in index},
        "robots": {"url": "u", "status": 200, "present": True, "sitemaps_declared": [],
                   "parse_errors": [], "agent_verdicts": {},
                   "blocked_retrieval_agents": [], "blocked_training_agents": [],
                   "raw_excerpt": ""},
        "sitemaps": [], "sitemap_url_count": 0,
        "sitemap_lastmods": sitemap_lastmods or [], "sitemap_urls_sample": [],
        "llms_txt": {"present": True, "status": 200}, "document_links": [],
        "render": {"available": renderer,
                   "reason": "liveness fixture" if renderer else "no renderer in fixture"},
        "blocked_by_robots": [], "skipped_private_urls": [],
        "distinct_text_bodies": len(index), "duplicate_body_ratio": 0.0, "notes": [],
    }
    with open(os.path.join(workspace, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh)


def probe(check_id, pages, sitemap_lastmods=None, renderer=False, probe_results=None):
    ws = tempfile.mkdtemp(prefix="live-")
    try:
        build_bundle(ws, pages, sitemap_lastmods, renderer)
        family = check_id.split("-")[0]
        skill, script, extra = ANALYZERS[family]
        directory = os.path.join(SKILLS, skill, "scripts")

        if probe_results is not None:
            # The entity skill needs its plan written before results can be merged.
            subprocess.run([PY, os.path.join(directory, script), "--workspace", ws,
                            "--phase", "onsite"], cwd=directory, capture_output=True)
            with open(os.path.join(ws, "probe_results.json"), "w", encoding="utf-8") as fh:
                json.dump(probe_results, fh)

        proc = subprocess.run([PY, os.path.join(directory, script), "--workspace", ws] + extra,
                              cwd=directory, capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        if proc.returncode != 0:
            return "ERROR", proc.stderr.strip()[-200:]
        path = os.path.join(ws, "findings", f"{skill}.json")
        ids = [x["check_id"] for x in json.load(open(path, encoding="utf-8"))["findings"]]
        hit = any(i == check_id or i.startswith(check_id + "-") for i in ids)
        return ("LIVE" if hit else "NOT LIVE"), ", ".join(sorted(set(ids)))[:120]
    finally:
        shutil.rmtree(ws, ignore_errors=True)


def page(url, body, ptype="generic", depth=1, head="", rendered=None):
    html = (f"<!doctype html><html lang=en><head><title>Acme</title>{VIEWPORT}{head}"
            f"</head><body>{NAV}<main>{body}</main></body></html>")
    return (url, html, ptype, depth, rendered)


def filler(n=60):
    return "Acme provides freight scheduling for brokers. " * n


def build_cases():
    cases = []
    old = (datetime.now(timezone.utc) - timedelta(days=900)).date().isoformat()
    older = (datetime.now(timezone.utc) - timedelta(days=1200)).date().isoformat()

    # ---- READ: parse layer ------------------------------------------------
    shell = ('<!doctype html><html lang=en><head><title></title></head><body>'
             '<div id="root"></div><script src="/_next/static/main-a1b2c3d4.js"></script>'
             '</body></html>')
    rich = ("<!doctype html><html lang=en><head><title>Acme scheduling</title></head>"
            "<body><main><h1>Acme scheduling</h1><p>" + filler(80) +
            "</p></main></body></html>")
    cases.append(("READ-001", [(f"https://acme.example/p{i}", shell, "generic", 1, rich)
                               for i in range(3)], None, True, None))
    cases.append(("READ-002", [(f"https://acme.example/p{i}", shell, "generic", 1, None)
                               for i in range(3)], None, False, None))
    cases.append(("READ-003", [(f"https://acme.example/p{i}", shell, "generic", 1, rich)
                               for i in range(3)], None, True, None))
    cases.append(("READ-005", [page("https://acme.example/plans",
        '<h1>Plans</h1><img src="/img/pricing-table.png" alt="">'
        '<img src="/img/feature-comparison.png" alt=""><p>See the chart.</p>',
        "pricing")], None, False, None))
    cases.append(("READ-009", [page("https://acme.example/",
        "<p>" + filler() + "</p>", "homepage", 0)], None, False, None))
    cases.append(("READ-012", [page("https://acme.example/#!/dashboard",
        f"<h1>Acme</h1><p>{filler()}</p>", "generic")], None, False, None))

    # ---- MARK: markup layer ------------------------------------------------
    org = ('<script type="application/ld+json">{"@context":"https://schema.org",'
           '"@type":"Organization","name":"Acme","url":"https://acme.example/"}</script>')
    cases.append(("MARK-009", [page("https://acme.example/p/widget",
        f"<h1>Widget Pro</h1><p>Widget Pro costs $129.00 today. {filler(10)}</p>",
        "product", head='<script type="application/ld+json">'
        '{"@context":"https://schema.org","@type":"Product","name":"Widget Pro",'
        '"offers":{"@type":"Offer","price":"49.00","priceCurrency":"USD"}}</script>')],
        None, False, None))
    cases.append(("MARK-012",
        [page("https://acme.example/", f"<h1>Acme</h1><p>{filler(40)}</p>", "homepage", 0,
              head=org)] +
        [page(f"https://acme.example/docs/deep/page{i}", f"<h1>Acme doc</h1><p>{filler(40)}</p>",
              "docs", 2, head='<script type="application/ld+json">'
              '{"@context":"https://schema.org","@type":"TechArticle",'
              '"headline":"Acme doc"}</script>') for i in range(4)], None, False, None))

    # ---- QUOTE: prose layer ------------------------------------------------
    cases.append(("QUOTE-002", [page("https://acme.example/",
        "<h1>Unlock your potential</h1><p>Beyond limits. Beyond expectations. "
        "The journey starts here today for everyone who dares.</p>", "homepage", 0)],
        None, False, None))
    sections = "".join(
        f"<h2>Capability {i}</h2><p>" +
        ("We deliver outcomes for our clients and our platform supports our partners "
         "across our regions with our teams. " * 6) + "</p>" for i in range(7))
    cases.append(("QUOTE-007", [page("https://acme.example/",
        "<h1>Acme is a logistics platform for freight brokers.</h1>" + sections,
        "homepage", 0)], None, False, None))
    cases.append(("QUOTE-012", [page("https://acme.example/",
        f"<h1>Acme</h1><p>{filler(20)}</p>" +
        "".join(f'<a href="/x{i}">click here</a>' for i in range(45)), "homepage", 0)],
        None, False, None))

    # ---- TIME: freshness layer ---------------------------------------------
    cases.append(("TIME-002", [page(f"https://acme.example/blog/post{i}",
        f"<h1>Acme post</h1><p>Published {old}. {filler(20)}</p>", "article")
        for i in range(3)], None, False, None))
    cases.append(("TIME-003", [page(f"https://acme.example/blog/post{i}",
        f"<h1>Acme post</h1><p>Published {older}. {filler(20)}</p>", "article")
        for i in range(3)], None, False, None))
    cases.append(("TIME-005", [page("https://acme.example/blog/post",
        f"<h1>Acme post</h1><p>{filler(20)}</p>", "article",
        head='<script type="application/ld+json">{"@context":"https://schema.org",'
             '"@type":"Article","headline":"Acme post","datePublished":"2026-05-01",'
             '"dateModified":"2024-01-15"}</script>')], None, False, None))
    cases.append(("TIME-006", [page("https://acme.example/",
        f"<h1>Acme</h1><p>{filler(20)}</p>", "homepage", 0)],
        ["2026-01-01"] * 14, False, None))
    cases.append(("TIME-008", [page("https://acme.example/guide",
        "<h1>The latest Acme guide 2021</h1><p>This is the current, up-to-date guide "
        "for 2021. " + filler(30) + "</p>", "article")], None, False, None))

    # ---- STAY: engagement layer --------------------------------------------
    cases.append(("STAY-005", [(f"https://acme.example/p{i}",
        f'<!doctype html><html lang=en><head><title>Acme</title>{VIEWPORT}'
        '<script src="https://cdn.cookiebot.com/uc.js"></script>'
        '<script src="https://widget.intercom.io/widget.js"></script></head><body>'
        f"{NAV}<main><h1>Acme page</h1><dialog>Subscribe to our newsletter</dialog>"
        f"<p>{filler()}</p></main></body></html>", "generic", 1, None)
        for i in range(4)], None, False, None))
    cases.append(("STAY-012", [page("https://acme.example/",
        f"<h1>Acme gallery</h1><p>{filler()}</p>" +
        "".join(f'<img src="/i{i}.jpg">' for i in range(14)), "homepage", 0)],
        None, False, None))
    # STAY-007 needs three things at once: enough internal links to clear the STAY-006
    # dead-end branch, no descriptively-labelled links (so the shared NAV is omitted
    # here), and the finding's own 2-page minimum.
    generic_links = "".join(f'<a href="/topic{i}">read more</a>' for i in range(9))
    cases.append(("STAY-007", [(
        f"https://acme.example/p{i}",
        f"<!doctype html><html lang=en><head><title>Acme</title>{VIEWPORT}</head><body>"
        f"<main><h1>Acme page {i}</h1><p>{filler(30)}</p>{generic_links}</main>"
        "</body></html>", "generic", 1, None) for i in range(3)], None, False, None))
    cases.append(("STAY-014", [page("https://acme.example/contact",
        "<h1>Contact Acme</h1><p>" + filler(20) + "</p><form>" +
        "".join(f'<input type="text" name="f{i}">' for i in range(6)) + "</form>",
        "contact")], None, False, None))

    # ---- ENTITY: corroboration layer (needs recorded probe results) --------
    def results(**over):
        base = {"probes": [
            {"id": "P1", "query": "what is acme", "top_domains": ["g2.com", "reddit.com"],
             "own_domain_present": False, "returned_description": "A cartoon company."},
            {"id": "P2", "query": "acme reviews", "top_domains": [],
             "independent_sources_count": 0},
            {"id": "P3", "query": '"acme"', "top_domains": ["looneytunes.com"],
             "distinct_entities_observed": 3, "dominant_entity": "Acme Corp (cartoon)",
             "own_domain_present": False},
            {"id": "P4", "query": "acme pricing", "top_domains": ["capterra.com"],
             "own_domain_present": False},
            {"id": "P5", "query": "acme alternatives", "top_domains": ["rival.com"],
             "own_domain_present": False, "competitors_named": ["Rival"]},
            {"id": "P6", "query": "site:wikipedia.org acme", "own_entity_present": False,
             "entity_url": None}]}
        base.update(over)
        return base

    entity_page = [page("https://acme.example/",
        "<h1>Acme</h1><p>Acme is a logistics platform for freight brokers. " + filler(20) +
        "</p>", "homepage", 0,
        head='<meta name="description" content="Acme is a logistics platform for '
             'freight brokers moving palletised cargo across Europe.">' + org)]
    for cid in ("ENTITY-006", "ENTITY-007", "ENTITY-008", "ENTITY-009",
                "ENTITY-010", "ENTITY-011"):
        cases.append((cid, entity_page, None, False, results()))
    return cases


def main():
    print("=" * 92)
    print("citeworthy :: check liveness probe -- can every check fire at all?")
    print("=" * 92)
    print(f"\n{'check':<13}{'result':<10}fired in that fixture run")
    print("-" * 92)
    dead = []
    for check_id, pages, lastmods, renderer, results in build_cases():
        status, detail = probe(check_id, pages, lastmods, renderer, results)
        print(f"{check_id:<13}{status:<10}{detail}")
        if status != "LIVE":
            dead.append((check_id, status, detail))
    total = len(build_cases())
    print("\n" + "=" * 92)
    if dead:
        print(f"NOT LIVE: {len(dead)}/{total} -- these did NOT fire on a fixture built to "
              "trigger them, so they are dead code rather than strict checks:")
        for cid, status, detail in dead:
            print(f"  {cid}  [{status}]  {detail}")
        print("=" * 92)
        sys.exit(1)
    print(f"All {total} probed checks are LIVE.")
    print("=" * 92)
    sys.exit(0)


if __name__ == "__main__":
    main()
