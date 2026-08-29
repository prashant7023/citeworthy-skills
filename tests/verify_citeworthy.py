#!/usr/bin/env python3
"""
verify_citeworthy.py -- offline test suite. No network required.

Two things are tested, and the second matters as much as the first:

  positive  -- a deliberately broken fixture site triggers the checks it should
  negative  -- a deliberately healthy fixture site triggers (almost) nothing

The negative suite is the false-positive guard. A checker that fires on
everything is worthless, and the only way to keep it honest is to keep a clean
fixture that must stay clean.

Also validates marketplace structure, agentskills.io frontmatter compliance, and
the report schema.

Run:  python tests/verify_citeworthy.py
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS = os.path.join(ROOT, "skills")
PY = sys.executable

PASS, FAIL = [], []


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append((name, detail))
    print(("  PASS  " if condition else "  FAIL  ") + name + (f"  [{detail}]" if detail and not condition else ""))


# --------------------------------------------------------------------------- fixtures

BROKEN_HTML = """<!doctype html><html><head>
<title>Home</title>
<meta name="robots" content="nosnippet">
<script type="application/ld+json">{"@type":"Product","name":"Widget",,}</script>
</head><body>
<div><span>Welcome</span></div>
<div>Unlock your potential with our platform.</div>
<img src="/img/pricing.png" alt="">
<p>We help teams. Our platform is 50% faster. Our CDP and ETL and RPA experts handle
your CDP needs; the CDP drives ETL and CDP workflows through ETL and RPA pipelines,
so RPA and ETL and RPA stay aligned across every CDP deployment.</p>
LINK_SOUP
<p>&copy; 2019 Example</p>
</body></html>""".replace(
    "LINK_SOUP",
    "".join(f'<a href="/p{i}">click here</a>' for i in range(45)))

HEALTHY_HTML = """<!doctype html><html lang="en"><head>
<title>Acme Scheduling — Construction project software</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="Acme is scheduling software for construction subcontractors, replacing spreadsheets for teams of 10 to 200.">
<link rel="canonical" href="https://acme.example/">
<script type="application/ld+json">
{"@context":"https://schema.org","@graph":[
 {"@type":"Organization","@id":"https://acme.example/#org","name":"Acme","url":"https://acme.example/",
  "description":"Acme is scheduling software for construction subcontractors.",
  "logo":"https://acme.example/logo.png",
  "sameAs":["https://www.linkedin.com/company/acme","https://www.crunchbase.com/organization/acme",
            "https://www.wikidata.org/wiki/Q1"]},
 {"@type":"WebSite","@id":"https://acme.example/#site","name":"Acme","url":"https://acme.example/",
  "publisher":{"@id":"https://acme.example/#org"}}]}
</script>
</head><body>
<header><nav><a href="/pricing">Pricing</a><a href="/about">About</a><a href="/contact">Contact</a>
<a href="/blog">Blog</a><a href="/faq">FAQ</a><a href="/reviews">Customer case studies</a>
<a href="/privacy">Privacy policy</a></nav></header>
<main>
<h1>Scheduling software for construction subcontractors</h1>
<p>Acme is scheduling software for construction subcontractors. Acme replaces
spreadsheet-based scheduling for teams of 10 to 200 people. Acme was founded in 2014
and is based in Berlin.</p>
<h2>What does Acme cost?</h2>
<p>Acme costs $49 per user per month on the Pro plan. Acme bills annually and offers a
30-day trial. Published 2026-06-01. Last updated 2026-08-01.</p>
<h2>How long does Acme take to set up?</h2>
<p>Acme takes about two weeks to implement for a team of 50. Acme imports existing
schedules from Excel, which is 40% faster than manual entry compared with a
spreadsheet baseline measured in June 2026.</p>
<p>Contact Acme at hello@acme.example or start a free trial today.</p>
<img src="/team.jpg" alt="The Acme engineering team in the Berlin office">
<a href="/pricing">See the Pro plan pricing</a>
<a href="/about">Read about Acme's founding</a>
<a href="/faq">Browse the Acme FAQ</a>
<a href="/contact">Contact the Acme team</a>
<a href="/blog">Read the Acme engineering blog</a>
<a href="https://www.linkedin.com/company/acme">Acme on LinkedIn</a>
</main>
<footer><p>&copy; 2026 Acme</p></footer>
</body></html>"""


def build_bundle(workspace, pages, render_available=False, sitemap_lastmods=None,
                 robots_blocked=None):
    """Construct an evidence bundle without touching the network."""
    sys.path.insert(0, os.path.join(SKILLS, "cw-reach-gate", "scripts"))
    import harvest_site as crawler

    os.makedirs(os.path.join(workspace, "pages"), exist_ok=True)
    os.makedirs(os.path.join(workspace, "findings"), exist_ok=True)
    index = []
    for url, html, page_type in pages:
        pid = crawler.page_id(url)
        parsed = crawler.parse_html(html, url)
        inferred, tags = crawler.classify(url, parsed)
        record = {
            "id": pid, "url": url, "final_url": url, "status": 200, "redirects": [],
            "headers": {"content-type": "text/html"}, "content_type": "text/html",
            "declared_charset": "utf-8", "fetch_ms": 120, "bytes": len(html),
            "depth": 0 if page_type == "homepage" else 1, "non_html": False,
            "page_type": page_type or inferred, "page_tags": tags,
            "raw": parsed, "rendered": None, "render_available": render_available,
            "network_error": None,
        }
        with open(os.path.join(workspace, "pages", f"{pid}.json"), "w", encoding="utf-8") as fh:
            json.dump(record, fh)
        with open(os.path.join(workspace, "pages", f"{pid}.raw.html"), "w", encoding="utf-8") as fh:
            fh.write(html)
        index.append({"id": pid, "url": url, "status": 200,
                      "page_type": record["page_type"], "depth": record["depth"],
                      "word_count": parsed["word_count"], "render_available": render_available})

    manifest = {
        "schema_version": "1.0", "site": "acme.example", "site_root": "acme.example",
        "start_url": pages[0][0],
        "crawled_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "finished_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "duration_seconds": 1.0, "user_agent": "test", "limits": {},
        "pages": index, "page_count": len(index), "ok_page_count": len(index),
        "page_types": {p["page_type"]: 1 for p in index},
        "robots": {"url": "https://acme.example/robots.txt", "status": 200, "present": True,
                   "sitemaps_declared": ["https://acme.example/sitemap.xml"],
                   "parse_errors": [], "agent_verdicts": {},
                   "blocked_retrieval_agents": robots_blocked or [],
                   "blocked_training_agents": [], "raw_excerpt": ""},
        "sitemaps": [{"url": "https://acme.example/sitemap.xml", "status": 200, "ok": True}],
        "sitemap_url_count": len(index), "sitemap_lastmods": sitemap_lastmods or [],
        "sitemap_urls_sample": [p["url"] for p in index],
        "llms_txt": {"present": True, "status": 200}, "document_links": [],
        "render": {"available": render_available,
                   "reason": "test fixture" if render_available else "no renderer in test"},
        "blocked_by_robots": [], "skipped_private_urls": [], "notes": [],
    }
    with open(os.path.join(workspace, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh)
    return workspace


def run_analyzers(workspace, skip=()):
    stages = [
        ("cw-render-gap", "probe_render_gap.py", []),
        ("cw-schema-truth", "probe_schema_truth.py", []),
        ("cw-quotability", "probe_quotability.py", []),
        ("cw-time-decay", "probe_time_decay.py", []),
        ("cw-entity-consensus", "probe_entity_consensus.py", ["--phase", "onsite"]),
        ("cw-arrival-experience", "probe_arrival.py", []),
    ]
    for skill, script, extra in stages:
        if skill in skip:
            continue
        directory = os.path.join(SKILLS, skill, "scripts")
        proc = subprocess.run([PY, os.path.join(directory, script), "--workspace", workspace] + extra,
                              cwd=directory, capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        if proc.returncode != 0:
            check(f"analyzer {skill} runs", False, proc.stderr.strip()[-300:])


def fired(workspace):
    ids = set()
    directory = os.path.join(workspace, "findings")
    for name in os.listdir(directory):
        with open(os.path.join(directory, name), encoding="utf-8") as fh:
            for item in json.load(fh).get("findings", []):
                ids.add(item["check_id"])
    return ids


# --------------------------------------------------------------------------- tests

def test_structure():
    print("\n[1] Marketplace structure")
    manifest_path = os.path.join(ROOT, "marketplace.json")
    check("marketplace.json exists", os.path.exists(manifest_path))
    with open(manifest_path, encoding="utf-8") as fh:
        manifest = json.load(fh)
    for field in ("name", "version", "skills"):
        check(f"marketplace.json has {field}", field in manifest)
    entrypoints = [s for s in manifest["skills"] if s.get("entrypoint")]
    check("exactly one entrypoint", len(entrypoints) == 1, f"found {len(entrypoints)}")
    for skill in manifest["skills"]:
        path = os.path.join(ROOT, skill["path"])
        check(f"path exists: {skill['path']}", os.path.isdir(path))
        check(f"SKILL.md exists: {skill['id']}",
              os.path.exists(os.path.join(path, "SKILL.md")))
    listed = {s["path"] for s in manifest["skills"]}
    on_disk = {f"skills/{d}" for d in os.listdir(SKILLS)
               if os.path.isdir(os.path.join(SKILLS, d))}
    check("every skill folder is listed in the manifest", on_disk <= listed,
          f"unlisted: {on_disk - listed}")


def test_frontmatter():
    print("\n[2] agentskills.io frontmatter compliance")
    for folder in sorted(os.listdir(SKILLS)):
        path = os.path.join(SKILLS, folder, "SKILL.md")
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        match = re.match(r"^---\n(.*?)\n---\n", text, re.S)
        check(f"{folder}: has YAML frontmatter", bool(match))
        if not match:
            continue
        block = match.group(1)
        name = re.search(r"^name:\s*(.+)$", block, re.M)
        check(f"{folder}: has name", bool(name))
        if name:
            value = name.group(1).strip()
            check(f"{folder}: name matches folder", value == folder, f"{value} != {folder}")
            check(f"{folder}: name is valid slug",
                  bool(re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", value)) and len(value) <= 64)
        desc = re.search(r"^description:\s*(.+?)(?=^\w+:|\Z)", block, re.M | re.S)
        check(f"{folder}: has description", bool(desc))
        if desc:
            body = re.sub(r"\s+", " ", desc.group(1).replace(">-", "")).strip()
            check(f"{folder}: description non-trivial and within 1024 chars",
                  40 < len(body) <= 1024, f"len={len(body)}")
        check(f"{folder}: has license", bool(re.search(r"^license:", block, re.M)))
        for section in ("## When to use", "## Inputs", "## Procedure", "## Output"):
            check(f"{folder}: has '{section}'", section in text)


def test_broken_site_detection():
    print("\n[3] Positive: a broken fixture triggers the right checks")
    workspace = tempfile.mkdtemp(prefix="ws-broken-")
    try:
        build_bundle(workspace, [
            ("https://acme.example/", BROKEN_HTML, "homepage"),
            ("https://acme.example/blog/post", BROKEN_HTML, "article"),
        ], sitemap_lastmods=["2026-01-01"] * 12)
        run_analyzers(workspace)
        ids = fired(workspace)
        expected = {
            "MARK-003": "invalid JSON-LD is detected",
            "MARK-001": "absent structured data is detected",
            "QUOTE-001": "missing definitional sentence is detected",
            "QUOTE-010": "unexpanded acronyms are detected",
            "QUOTE-012": "generic anchor text is detected",
            "READ-009": "missing H1 is detected",
            "READ-010": "missing semantic landmarks are detected",
            "TIME-004": "stale copyright year is detected",
            "TIME-006": "identical sitemap lastmods are detected",
            "ENTITY-001": "missing identity anchors are detected",
            "STAY-004": "missing viewport is detected",
            "STAY-013": "missing lang attribute is detected",
        }
        for check_id, label in expected.items():
            check(label, check_id in ids, f"{check_id} did not fire; fired={sorted(ids)}")

        # Suppression: one root cause must yield one finding, not several restatements
        # of the same defect. Both of these are deliberate, so assert them explicitly.
        check("MARK-004 is suppressed when MARK-001 already covers absent markup",
              "MARK-004" not in ids)
        check("MARK-006-* is suppressed when no markup exists anywhere",
              not any(i.startswith("MARK-006") for i in ids), str(sorted(ids)))
        check("QUOTE-002 does not fire when there is no H1 to judge (READ-009 covers it)",
              "QUOTE-002" not in ids)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_healthy_site_quiet():
    print("\n[4] Negative: a healthy fixture stays quiet (false-positive guard)")
    workspace = tempfile.mkdtemp(prefix="ws-healthy-")
    try:
        build_bundle(workspace, [
            ("https://acme.example/", HEALTHY_HTML, "homepage"),
            ("https://acme.example/pricing", HEALTHY_HTML, "pricing"),
        ], sitemap_lastmods=["2026-06-01", "2026-07-14", "2026-08-02"])
        run_analyzers(workspace)
        ids = fired(workspace)
        must_not_fire = {
            "QUOTE-001": "does not claim a missing definition when one exists",
            "QUOTE-002": "does not flag a concrete H1 as a slogan",
            "QUOTE-012": "does not flag descriptive anchor text",
            "MARK-001": "does not claim absent markup when JSON-LD is present",
            "MARK-003": "does not report valid JSON-LD as broken",
            "MARK-004": "does not claim a missing Organization when one exists",
            "MARK-005": "does not claim missing sameAs when anchors are present",
            "READ-009": "does not report a missing H1 when one exists",
            "READ-010": "does not report missing landmarks when <main> is used",
            "STAY-004": "does not report a viewport problem when one is declared",
            "STAY-013": "does not report a missing lang when declared",
            "ENTITY-001": "does not report missing anchors when sameAs is populated",
            "TIME-004": "does not report a current copyright year as stale",
        }
        for check_id, label in must_not_fire.items():
            check(label, check_id not in ids, f"{check_id} FALSE POSITIVE")
        check("healthy fixture stays under 6 findings", len(ids) < 6,
              f"{len(ids)} fired: {sorted(ids)}")
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


DRIFT_REAL = """<!doctype html><html lang="en"><head><title>Widget Pro — Acme</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Product","name":"Widget Pro",
 "offers":{"@type":"Offer","price":"49.00","priceCurrency":"USD"}}
</script></head><body><main><h1>Widget Pro</h1>
<p>Widget Pro is a precision tool for machinists. Widget Pro costs $129.00 today.</p>
</main></body></html>"""

DRIFT_SUFFIX_ONLY = """<!doctype html><html lang="en"><head><title>All Company News - Acme</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"NewsArticle","headline":"All Company News - Acme",
 "author":{"@type":"Person","name":"Jane Doe"},"datePublished":"2026-07-01"}
</script></head><body><main>
<p>Acme publishes company news here. Acme posts updates every week for customers.</p>
</main></body></html>"""


def test_schema_drift_precision():
    """MARK-009 must catch genuine contradiction and ignore title-suffix noise.

    Regression guard for a real false positive found on nasa.gov, where markup
    headline 'All NASA News - NASA' exactly matched the <title> but the page had
    no H1 -- the check reported 'contradiction' when the true defect was a
    missing H1, already covered by READ-009.
    """
    print("\n[5] MARK-009 drift precision (regression: nasa.gov false positive)")

    positive = tempfile.mkdtemp(prefix="ws-drift-pos-")
    try:
        build_bundle(positive, [("https://acme.example/p/widget", DRIFT_REAL, "product")])
        run_analyzers(positive, skip=("cw-render-gap", "cw-quotability", "cw-time-decay",
                                      "cw-entity-consensus", "cw-arrival-experience"))
        check("catches genuine price drift (markup $49 vs page $129)",
              "MARK-009" in fired(positive), str(sorted(fired(positive))))
    finally:
        shutil.rmtree(positive, ignore_errors=True)

    negative = tempfile.mkdtemp(prefix="ws-drift-neg-")
    try:
        build_bundle(negative, [("https://acme.example/news/", DRIFT_SUFFIX_ONLY, "article")])
        run_analyzers(negative, skip=("cw-render-gap", "cw-quotability", "cw-time-decay",
                                      "cw-entity-consensus", "cw-arrival-experience"))
        check("does NOT flag drift when headline matches the title and there is no H1",
              "MARK-009" not in fired(negative), "FALSE POSITIVE reintroduced")
    finally:
        shutil.rmtree(negative, ignore_errors=True)


def test_composition_and_schema():
    print("\n[6] Composition rules and report schema")
    workspace = tempfile.mkdtemp(prefix="ws-compose-")
    try:
        build_bundle(workspace, [("https://acme.example/", BROKEN_HTML, "homepage")])
        run_analyzers(workspace)

        # Inject a critical access gate to exercise composition rule 1.
        gate = {"skill": "cw-reach-gate", "pillar": "access",
                "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "checks_run": ["REACH-001"], "pages_analysed": 1,
                "findings": [{
                    "check_id": "REACH-001", "severity": "critical", "pillar": "access",
                    "confidence": "high",
                    "title": "robots.txt blocks answer-time retrieval agents from the whole site",
                    "evidence": "GET https://acme.example/robots.txt -> 200. Root '/' disallowed "
                                "for 4 retrieval agents across 1/1 crawled pages.",
                    "suggested_action": {"summary": "Allow the retrieval agents in robots.txt.",
                                         "priority": "critical", "steps": ["Edit robots.txt."],
                                         "effort": "low", "owner": "web/dev"}}],
                "notes": []}
        with open(os.path.join(workspace, "findings", "cw-reach-gate.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(gate, fh)

        aggregate = os.path.join(SKILLS, "cw-audit-conductor", "scripts", "compose_report.py")
        proc = subprocess.run([PY, aggregate, "--workspace", workspace,
                               "--markdown", os.path.join(workspace, "report.md")],
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
        check("aggregator runs", proc.returncode == 0, proc.stderr.strip()[-300:])

        with open(os.path.join(workspace, "report.json"), encoding="utf-8") as fh:
            report = json.load(fh)

        for field in ("site", "audited_at", "summary", "findings"):
            check(f"report has required field '{field}'", field in report)
        for field in ("total_findings", "critical", "high", "medium"):
            check(f"summary has required field '{field}'", field in report["summary"])
        check("summary counts match findings",
              report["summary"]["total_findings"] == len(report["findings"]))
        check("finding ids are sequential F-NNN",
              all(f["id"] == f"F-{i:03d}" for i, f in enumerate(report["findings"], 1)))
        check("every finding has the required keys",
              all(all(k in f for k in ("id", "title", "severity", "evidence", "suggested_action"))
                  for f in report["findings"]))
        check("every suggested_action has summary and priority",
              all(all(k in f["suggested_action"] for k in ("summary", "priority"))
                  for f in report["findings"]))

        # Composition rule 1: gating.
        gated = [f for f in report["findings"] if f.get("blocked_by") == "REACH-001"]
        check("rule 1 — gating marks downstream findings blocked_by", len(gated) > 0)
        check("rule 1 — the gate itself is not marked blocked",
              all(f.get("blocked_by") is None for f in report["findings"]
                  if f["check_id"] == "REACH-001"))
        check("rule 1 — the gate is ranked first",
              report["findings"][0]["check_id"] == "REACH-001",
              report["findings"][0]["check_id"])

        # Composition rule 2: confounding is consistent.
        check("rule 2 — confounded findings are always low confidence",
              all(f.get("confidence") == "low" for f in report["findings"]
                  if f.get("confounded_by")))

        # Extensions.
        check("report includes pillar scores", "pillar_scores" in report["summary"])
        check("report includes proactive recommendations",
              len(report.get("proactive_recommendations", [])) >= 5)
        check("report includes a remediation plan",
              len(report.get("remediation_plan", [])) == 4)
        check("remediation plan assigns each item exactly once",
              len([i for p in report["remediation_plan"] for i in p["items"]])
              == len({i for p in report["remediation_plan"] for i in p["items"]}))
        check("report records limitations", isinstance(report.get("limitations"), list))
        check("markdown report written",
              os.path.exists(os.path.join(workspace, "report.md")))

        validate = os.path.join(SKILLS, "cw-audit-conductor", "scripts", "verify_report.py")
        proc = subprocess.run([PY, validate, os.path.join(workspace, "report.json")],
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
        check("report passes its own validator", proc.returncode == 0,
              proc.stderr.strip()[-400:])
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_inconclusive_crawl_is_not_scored():
    """A crawl that read nothing must not be reported as a healthy site.

    Regression guard for the worst bug found in real-site testing: patagonia.com
    404'd every URL, so almost no content findings fired, and the scoring formula
    read that silence as 98/100. Silence from a check that could not run must
    never be reported as a pass.
    """
    print("\n[7] Inconclusive crawl is refused a score (regression: patagonia.com 98/100)")
    workspace = tempfile.mkdtemp(prefix="ws-inconclusive-")
    try:
        build_bundle(workspace, [("https://acme.example/", HEALTHY_HTML, "homepage")])
        # Rewrite the manifest to look like a crawl where everything failed.
        path = os.path.join(workspace, "manifest.json")
        with open(path, encoding="utf-8") as fh:
            manifest = json.load(fh)
        manifest["ok_page_count"] = 0
        for page in manifest["pages"]:
            page["status"] = 404
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh)

        aggregate = os.path.join(SKILLS, "cw-audit-conductor", "scripts", "compose_report.py")
        proc = subprocess.run([PY, aggregate, "--workspace", workspace,
                               "--markdown", os.path.join(workspace, "report.md")],
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
        check("aggregator survives an empty crawl", proc.returncode == 0,
              proc.stderr.strip()[-300:])
        with open(os.path.join(workspace, "report.json"), encoding="utf-8") as fh:
            report = json.load(fh)

        check("no score is reported for an inconclusive crawl",
              report["summary"]["citeworthy_score"] is None,
              f"got {report['summary']['citeworthy_score']}")
        check("pillar scores are withheld too",
              all(v is None for v in report["summary"]["pillar_scores"].values()))
        check("headline states the audit was inconclusive",
              "inconclusive" in report["summary"]["headline"].lower())
        check("limitations lead with the inconclusive warning",
              report["limitations"] and
              report["limitations"][0].startswith("AUDIT INCONCLUSIVE"))
        check("markdown does not print 'None/100'",
              "None/100" not in open(os.path.join(workspace, "report.md"),
                                     encoding="utf-8").read())
        proc = subprocess.run([PY, os.path.join(SKILLS, "cw-audit-conductor", "scripts",
                                                "verify_report.py"),
                               os.path.join(workspace, "report.json")],
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
        check("an inconclusive report still passes schema validation",
              proc.returncode == 0, proc.stderr.strip()[-300:])
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_confounding_prefixes_are_current():
    """Composition rule 2 must cover the check ids that actually exist.

    Regression guard: a bulk rename left CONTENT_DEPENDENT_PREFIXES pointing at
    retired prefixes, which silently disabled the marketplace's main
    false-positive control without failing a single test.
    """
    print("\n[8] Confounding rule covers live check-id prefixes")
    source = open(os.path.join(SKILLS, "cw-audit-conductor", "scripts",
                               "compose_report.py"), encoding="utf-8").read()
    match = re.search(r"CONTENT_DEPENDENT_PREFIXES = \((.*?)\)", source, re.S)
    check("CONTENT_DEPENDENT_PREFIXES is defined", bool(match))
    if not match:
        return
    prefixes = set(re.findall(r'"([A-Z]+-\d*)"?', match.group(1)))

    catalog = open(os.path.join(SKILLS, "cw-audit-conductor", "references",
                                "finding-catalog.md"), encoding="utf-8").read()
    live = {cid.split("-")[0] for cid in re.findall(r"`([A-Z]+-\d{3})", catalog)}
    for prefix in prefixes:
        stem = prefix.split("-")[0]
        check(f"confounding prefix '{prefix}' matches a live check family",
              stem in live, f"{stem} not in {sorted(live)}")
    # The content-bearing families must all be represented.
    for required in ("QUOTE", "MARK", "TIME"):
        check(f"content family '{required}' is covered by the confounding rule",
              any(p.startswith(required) for p in prefixes))


def test_soft_block_is_not_scored():
    """A crawl where every URL returns identical boilerplate must not be scored.

    Regression guard for nike.com, where 9 of 10 URLs returned HTTP 200 with
    byte-identical navigation chrome. Status codes said the crawl succeeded; no
    page-specific content was ever read, yet the site scored 54.
    """
    print("\n[9] Soft-blocked crawl is refused a score (regression: nike.com 200-with-shell)")
    workspace = tempfile.mkdtemp(prefix="ws-softblock-")
    try:
        shell = ("<!doctype html><html lang=\"en\"><head><title>Shop</title>"
                 "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
                 "</head><body><nav>Home Help Orders Returns Sign In</nav>"
                 "<main><p>" + ("Navigation boilerplate repeated on every route. " * 30) +
                 "</p></main></body></html>")
        pages = [(f"https://acme.example/p/{i}", shell, "product") for i in range(6)]
        build_bundle(workspace, pages)
        # Recompute the manifest fields the crawler would have written.
        path = os.path.join(workspace, "manifest.json")
        with open(path, encoding="utf-8") as fh:
            manifest = json.load(fh)
        manifest["duplicate_body_ratio"] = 1.0
        manifest["distinct_text_bodies"] = 1
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh)

        aggregate = os.path.join(SKILLS, "cw-audit-conductor", "scripts", "compose_report.py")
        proc = subprocess.run([PY, aggregate, "--workspace", workspace],
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
        check("aggregator handles a soft-blocked crawl", proc.returncode == 0,
              proc.stderr.strip()[-300:])
        with open(os.path.join(workspace, "report.json"), encoding="utf-8") as fh:
            report = json.load(fh)
        check("no score for a soft-blocked crawl",
              report["summary"]["citeworthy_score"] is None,
              f"got {report['summary']['citeworthy_score']}")
        check("soft-block limitation explains identical bodies",
              any("byte-identical" in l for l in report["limitations"]))
        check("headline flags the audit as inconclusive",
              "inconclusive" in report["summary"]["headline"].lower())
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_duplicate_robots_groups_merge():
    """Duplicate User-agent groups must merge per RFC 9309 section 2.2.1.

    Regression guard for snitch.co.in, whose robots.txt declares `User-agent: *`
    twice -- once with specific rules and again 166 lines later with a blanket
    `Disallow: /`. Parsers that stop at the first matching group (Python's stdlib
    robotparser among them) report the site as crawlable. The crawlers that decide
    citability do not.
    """
    print("\n[10] Duplicate robots groups merge (regression: snitch.co.in)")
    sys.path.insert(0, os.path.join(SKILLS, "cw-reach-gate", "scripts"))
    import harvest_site as crawler

    text = ("User-agent: *\n"
            "Disallow: /admin\n"
            "Disallow: /cart\n"
            "\n"
            "User-agent: Nutch\n"
            "Disallow: /\n"
            "\n"
            "User-agent: *\n"
            "Disallow: /\n")
    policy = crawler.RobotsPolicy(text, 200, "https://x/robots.txt")

    check("blanket Disallow in a later duplicate group is honoured",
          policy.blanket_blocked("OAI-SearchBot")[0] is True)
    check("the duplicate group is recorded with its line numbers",
          "*" in policy.duplicate_groups and len(policy.duplicate_groups["*"]) == 2,
          str(policy.duplicate_groups))
    check("the blocking rule's line number is resolvable",
          policy.rule_line("OAI-SearchBot", "Disallow: /") == 9,
          str(policy.rule_line("OAI-SearchBot", "Disallow: /")))
    check("specific rules from the first group still apply after merging",
          policy.verdict("OAI-SearchBot", "/admin")[0] is False)
    check("an agent with its own group is unaffected by the * duplicate",
          policy.blanket_blocked("Nutch")[0] is True)

    # A file with no duplicates must not report one.
    clean = crawler.RobotsPolicy("User-agent: *\nDisallow: /admin\n", 200, "u")
    check("no duplicate reported for a well-formed file", not clean.duplicate_groups)
    check("a well-formed file is not reported as blocking the root",
          clean.blanket_blocked("OAI-SearchBot")[0] is False)


def test_page_classifier_precision():
    """Storefront chrome must not turn every page into a product page.

    Regression guard for wearcomet.com, where the Shopify cart drawer put a price
    and 'add to cart' into the global template, so all 13 crawled pages -- including
    /pages/about-us and /pages/order-tracking -- were classified 'product' and then
    charged with missing Product markup.
    """
    print("\n[11] Page classifier precision (regression: wearcomet.com Shopify chrome)")
    sys.path.insert(0, os.path.join(SKILLS, "cw-reach-gate", "scripts"))
    import harvest_site as crawler

    def build(html, url):
        return crawler.classify(url, crawler.parse_html(html, url))[0]

    # Global storefront chrome present on every page of the site.
    chrome = ("<nav>" + "".join(f'<a href="/c/{i}">Cat {i}</a>' for i in range(140)) + "</nav>"
              "<aside id=\"cart-drawer\"><p>Your cart</p><button>Add to cart</button>"
              "<span>Rs. 3,499</span></aside>")

    about = ("<!doctype html><html lang=\"en\"><head><title>About us</title></head><body>"
             + chrome + "<main><h1>About Comet</h1><p>Comet makes sneakers in India.</p>"
             "</main></body></html>")
    check("a static page with cart chrome is not a product page",
          build(about, "https://shop.example/pages/about-us") == "about",
          build(about, "https://shop.example/pages/about-us"))

    grid_prices = "".join(f"<span>Rs. {1000+i*100}</span>" for i in range(12))
    collection = ("<!doctype html><html lang=\"en\"><head><title>Sneakers</title></head><body>"
                  + chrome + "<main><h1>Men's sneakers</h1>" + grid_prices +
                  "<button>Add to cart</button></main></body></html>")
    check("a category grid is classified listing, not product",
          build(collection, "https://shop.example/collections/men-sneakers") == "listing",
          build(collection, "https://shop.example/collections/men-sneakers"))

    pdp = ("<!doctype html><html lang=\"en\"><head><title>Aeon V2</title></head><body>"
           + chrome + "<main><h1>Aeon V2 Sneaker</h1><span>Rs. 4,999</span>"
           "<button>Add to cart</button><p>Premium knit upper.</p></main></body></html>")
    check("a real product detail page is still classified product",
          build(pdp, "https://shop.example/products/aeon-v2") == "product",
          build(pdp, "https://shop.example/products/aeon-v2"))

    # URL signal must win even without any commerce markup at all.
    bare = ("<!doctype html><html lang=\"en\"><head><title>Item</title></head>"
            "<body><main><h1>Item</h1><p>Details.</p></main></body></html>")
    check("a /products/ URL is authoritative for product classification",
          build(bare, "https://shop.example/products/thing") == "product",
          build(bare, "https://shop.example/products/thing"))

    check("an ordinary static page with chrome falls back to generic",
          build("<!doctype html><html lang=\"en\"><head><title>Track</title></head><body>"
                + chrome + "<main><h1>Order tracking</h1><p>Enter your order id.</p>"
                "</main></body></html>",
                "https://shop.example/pages/order-tracking") in ("generic", "contact"),
          build("<!doctype html><html><body>" + chrome + "</body></html>",
                "https://shop.example/pages/order-tracking"))


def test_validator_rejects_bad_reports():
    print("\n[12] Validator fails closed")
    validate = os.path.join(SKILLS, "cw-audit-conductor", "scripts", "verify_report.py")

    def verdict(report):
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump(report, handle)
        handle.close()
        proc = subprocess.run([PY, validate, handle.name], capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        os.unlink(handle.name)
        return proc.returncode

    good = {
        "site": "a.com", "audited_at": "2026-08-27T10:00:00Z",
        "summary": {"total_findings": 1, "critical": 0, "high": 1, "medium": 0, "low": 0},
        "findings": [{"id": "F-001", "title": "A real problem here", "severity": "high",
                      "check_id": "QUOTE-001", "confidence": "high",
                      "evidence": "Checked 12/12 pages and found 0 matching sentences in the "
                                  "opening 25 sentences of each identity page.",
                      "suggested_action": {"summary": "Add a definitional sentence.",
                                           "priority": "high", "steps": ["Write it."]}}]}
    check("accepts a valid report", verdict(good) == 0)

    bad_time = json.loads(json.dumps(good)); bad_time["audited_at"] = "27/08/2026"
    check("rejects a non-ISO timestamp", verdict(bad_time) == 1)

    bad_count = json.loads(json.dumps(good)); bad_count["summary"]["total_findings"] = 99
    check("rejects mismatched summary counts", verdict(bad_count) == 1)

    bad_sev = json.loads(json.dumps(good)); bad_sev["findings"][0]["severity"] = "catastrophic"
    check("rejects an invalid severity", verdict(bad_sev) == 1)

    thin = json.loads(json.dumps(good)); thin["findings"][0]["evidence"] = "bad"
    check("rejects thin evidence", verdict(thin) == 1)

    no_action = json.loads(json.dumps(good)); del no_action["findings"][0]["suggested_action"]
    check("rejects a finding with no suggested_action", verdict(no_action) == 1)

    inconsistent = json.loads(json.dumps(good))
    inconsistent["findings"][0]["confounded_by"] = "READ-002"
    check("rejects a confounded finding that was not demoted", verdict(inconsistent) == 1)


def test_robots_parser():
    print("\n[13] Robots parser correctness")
    sys.path.insert(0, os.path.join(SKILLS, "cw-reach-gate", "scripts"))
    import harvest_site as crawler

    policy = crawler.RobotsPolicy("""
User-agent: *
Disallow: /

User-agent: OAI-SearchBot
User-agent: PerplexityBot
Allow: /
Disallow: /private

User-agent: GPTBot
Disallow: /
""", 200, "https://x/robots.txt")

    check("wildcard group blocks an unlisted agent",
          policy.blanket_blocked("SomeRandomBot")[0] is True)
    check("a specific Allow group overrides the wildcard block",
          policy.blanket_blocked("OAI-SearchBot")[0] is False)
    check("consecutive User-agent lines share one group",
          policy.blanket_blocked("PerplexityBot")[0] is False)
    check("a training agent is correctly seen as blocked",
          policy.blanket_blocked("GPTBot")[0] is True)
    check("path-level Disallow is honoured",
          policy.verdict("OAI-SearchBot", "/private/x")[0] is False)
    check("path outside the Disallow is allowed",
          policy.verdict("OAI-SearchBot", "/public/x")[0] is True)
    check("prefix matching resolves versioned agent strings",
          policy.blanket_blocked("GPTBot/1.2")[0] is True)

    longest = crawler.RobotsPolicy(
        "User-agent: *\nDisallow: /a\nAllow: /a/b\n", 200, "https://x/robots.txt")
    check("longest match wins over a shorter Disallow",
          longest.verdict("Any", "/a/b/c")[0] is True)
    check("the shorter Disallow still applies elsewhere",
          longest.verdict("Any", "/a/z")[0] is False)

    malformed = crawler.RobotsPolicy("this is not robots syntax\nDisallow: /x\n", 200, "u")
    check("malformed lines are recorded", len(malformed.parse_errors) >= 1)


def test_determinism():
    print("\n[14] Determinism")
    workspace = tempfile.mkdtemp(prefix="ws-det-")
    try:
        build_bundle(workspace, [("https://acme.example/", BROKEN_HTML, "homepage")])
        run_analyzers(workspace)
        first = {}
        for name in os.listdir(os.path.join(workspace, "findings")):
            with open(os.path.join(workspace, "findings", name), encoding="utf-8") as fh:
                data = json.load(fh)
                first[name] = json.dumps([(f["check_id"], f["severity"], f["evidence"])
                                          for f in data["findings"]], sort_keys=True)
        run_analyzers(workspace)
        stable = True
        for name, snapshot in first.items():
            with open(os.path.join(workspace, "findings", name), encoding="utf-8") as fh:
                data = json.load(fh)
                again = json.dumps([(f["check_id"], f["severity"], f["evidence"])
                                    for f in data["findings"]], sort_keys=True)
                if again != snapshot:
                    stable = False
        check("re-running analyzers on the same bundle gives identical findings", stable)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_safety():
    print("\n[15] Safety guarantees")
    sources = []
    for base, _, files in os.walk(SKILLS):
        for name in files:
            if name.endswith(".py"):
                with open(os.path.join(base, name), encoding="utf-8") as fh:
                    sources.append((os.path.join(base, name), fh.read()))
    check("scripts exist", len(sources) > 0)

    offenders = [p for p, s in sources
                 if re.search(r'method\s*=\s*["\'](POST|PUT|DELETE|PATCH)', s)]
    check("no script issues write HTTP methods", not offenders, str(offenders))

    analyzers = [(p, s) for p, s in sources
                 if os.path.basename(p).startswith("probe_")]
    # Guard the guard: if the naming convention changes again, this test must fail
    # loudly rather than pass by matching nothing.
    check("safety scan actually found the analyzer scripts", len(analyzers) == 6,
          f"matched {len(analyzers)}, expected 6")
    networked = [p for p, s in analyzers
                 if re.search(r"urlopen|requests\.|httpx\.|socket\.socket", s)]
    check("analyzers perform no network I/O", not networked, str(networked))

    crawl_src = next(s for p, s in sources if p.endswith("harvest_site.py"))
    check("crawler filters authenticated paths", "PRIVATE_PATH" in crawl_src)
    check("crawler filters app subdomains", "PRIVATE_HOST" in crawl_src)
    check("crawler enforces a polite delay", "polite_delay" in crawl_src)
    check("crawler respects robots by default",
          "if not allowed and not args.ignore_robots" in crawl_src)


def main():
    print("=" * 72)
    print("citeworthy :: offline test suite")
    print("=" * 72)
    test_structure()
    test_frontmatter()
    test_broken_site_detection()
    test_healthy_site_quiet()
    test_schema_drift_precision()
    test_composition_and_schema()
    test_inconclusive_crawl_is_not_scored()
    test_confounding_prefixes_are_current()
    test_soft_block_is_not_scored()
    test_duplicate_robots_groups_merge()
    test_page_classifier_precision()
    test_validator_rejects_bad_reports()
    test_robots_parser()
    test_determinism()
    test_safety()
    print("\n" + "=" * 72)
    print(f"RESULT: {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("\nFailures:")
        for name, detail in FAIL:
            print(f"  - {name}  {detail}")
    print("=" * 72)
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
