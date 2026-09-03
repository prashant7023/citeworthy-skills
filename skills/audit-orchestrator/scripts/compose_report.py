#!/usr/bin/env python3
"""
compose_report.py -- compose every skill's findings into the single audit report.

This is where the marketplace stops being a pile of scripts. Composition does four
things no individual skill can do, because each is a statement about the
relationship *between* findings:

1. Gating. If retrieval agents cannot reach the site at all, then every content
   finding downstream is real but currently inert. The report says so explicitly
   and orders the gate first, instead of burying a robots.txt line under twenty
   copywriting suggestions.

2. Confounding (false-positive control). If the crawler could not execute
   JavaScript and the page turned out to be client-rendered, then "no H1" and
   "no definitional sentence" may be artefacts of our own blind spot rather than
   defects. Those findings are retained but demoted and explicitly labelled as
   needing browser verification. Silently reporting them as defects is how audits
   lose credibility; silently dropping them is how they miss real problems.

3. Linking. The same root cause surfaces in several pillars (a missing
   definitional sentence is an extractability defect, a corroboration defect and
   an engagement defect at once). The report links them so the reader fixes one
   thing, not three.

4. Prioritisation. Ranks by expected impact = severity x confidence x pillar
   stage, because a fix at an earlier stage of the pipeline unblocks everything
   after it.

Deterministic: same inputs always produce the same report. No network I/O.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SEVERITY_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1}
CONFIDENCE_WEIGHT = {"high": 1.0, "medium": 0.75, "low": 0.5}
# Earlier pipeline stages gate everything after them, so they rank higher at equal severity.
PILLAR_STAGE = {"access": 1.30, "parse": 1.20, "extract": 1.10, "trust": 1.00, "engage": 1.00}
PILLAR_LABEL = {
    "access": "Access -- can a crawler reach the page?",
    "parse": "Parse -- can a machine read what a human sees?",
    "extract": "Extract -- can a machine quote a specific fact?",
    "trust": "Trust -- does the wider web corroborate the facts?",
    "engage": "Engage -- does the arriving visitor stay?",
}
# Severity -> points deducted from a pillar's 100-point score, scaled by confidence.
SCORE_PENALTY = {"critical": 40, "high": 22, "medium": 10, "low": 3}

SKILL_ORDER = [
    "crawl-access-audit", "render-extraction-audit", "structured-data-audit",
    "answer-extractability-audit", "entity-corroboration-audit",
    "freshness-audit", "engagement-audit",
]

# Findings whose evidence depends on reading page content. If the audit could not
# see the content (client-side rendering + no renderer), these are unverified.
CONTENT_DEPENDENT_PREFIXES = ("QUOTE-", "MARK-", "TIME-", "STAY-001", "STAY-002",
                              "STAY-006", "STAY-010", "ENTITY-002", "ENTITY-004",
                              "ENTITY-005")

# Proactive recommendations: offered when the corresponding defect did NOT fire, so
# the report always contains forward-looking work, not only repairs.
PROACTIVE_LIBRARY = [
    {"id": "P-ANSWER-PAGES", "title": "Publish one canonical answer page per question you want to own",
     "pillar": "extract", "effort": "medium", "owner": "content",
     "rationale": "Retrieval returns passages, not sites. A page whose title, H1 and opening sentence "
                  "all match one real question is far more likely to be selected than a broad page "
                  "that mentions the topic among others.",
     "steps": ["List the 10-20 questions buyers actually ask before purchase.",
               "Give each its own URL, titled as the question, answered in the first 100 words.",
               "Add FAQPage or Article markup and link them from the relevant product pages."]},
    {"id": "P-COMPARISON", "title": "Publish honest comparison and alternatives pages",
     "pillar": "trust", "effort": "medium", "owner": "marketing",
     "rationale": "'X vs Y' and 'alternatives to X' are among the highest-intent questions asked of "
                  "assistants. If the brand publishes nothing, the answer is assembled entirely from "
                  "competitor and affiliate pages, which frame the brand on someone else's terms.",
     "steps": ["Write a comparison page per major alternative, naming it explicitly.",
               "Be genuinely even-handed, including where the alternative is the better fit -- "
                "one-sided pages are discounted by both readers and ranking systems.",
               "Include a specification table in real HTML so the facts are extractable."]},
    {"id": "P-ENTITY-HOME", "title": "Create a single canonical entity home page",
     "pillar": "trust", "effort": "low", "owner": "marketing",
     "rationale": "Entity resolution works best when one URL is unambiguously 'the page about this "
                  "organisation', carrying the canonical name, description, founding facts and every "
                  "sameAs anchor in one place.",
     "steps": ["Designate the About page as the entity home with a stable JSON-LD @id.",
               "State name, founding date, headquarters, size, category and offering as plain text.",
               "Reference that @id from every other entity on the site."]},
    {"id": "P-ORIGINAL-DATA", "title": "Publish original data others have a reason to cite",
     "pillar": "trust", "effort": "high", "owner": "marketing",
     "rationale": "Corroboration cannot be bought or asked for at scale; it is earned by publishing "
                  "something worth referencing. Original survey data, benchmarks and open tools are "
                  "the most reliably-cited assets a brand can produce.",
     "steps": ["Publish an annual data study from proprietary data you already hold.",
               "State the methodology and sample size so it is safe for others to cite.",
               "Give every statistic a stable anchor link and a clear reuse licence."]},
    {"id": "P-FACT-BLOCK", "title": "Add a machine-readable key-facts block to every major page",
     "pillar": "extract", "effort": "low", "owner": "content",
     "rationale": "A short, explicitly-labelled facts block (what, who for, price, availability, "
                  "location, last updated) gives extractors a dense, unambiguous target and gives "
                  "human skimmers the same answer in the same place.",
     "steps": ["Add a compact definition list near the top of key templates.",
               "Use real <dl>/<table> markup with explicit labels, never a styled image.",
               "Mirror the same values into the page's JSON-LD."]},
    {"id": "P-MONITORING", "title": "Monitor what assistants actually say about the brand",
     "pillar": "trust", "effort": "low", "owner": "marketing",
     "rationale": "AI answers are not in any analytics package. Without deliberate sampling, a brand "
                  "has no way to notice that assistants have started describing it wrongly.",
     "steps": ["Run a fixed set of 10 brand questions across the major assistants monthly.",
               "Log which sources each answer cites and how the brand is described.",
               "Track referral traffic from assistant domains separately in analytics.",
               "Re-run this audit quarterly and diff the findings."]},
]


def load_findings(workspace):
    directory = os.path.join(os.path.abspath(workspace), "findings")
    bundles = []
    if not os.path.isdir(directory):
        return bundles
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".json"):
            continue
        with open(os.path.join(directory, name), encoding="utf-8") as fh:
            try:
                bundles.append(json.load(fh))
            except json.JSONDecodeError:
                continue
    bundles.sort(key=lambda b: SKILL_ORDER.index(b["skill"])
                 if b.get("skill") in SKILL_ORDER else 99)
    return bundles


def impact(finding):
    return (SEVERITY_RANK.get(finding.get("severity"), 1)
            * CONFIDENCE_WEIGHT.get(finding.get("confidence", "high"), 1.0)
            * PILLAR_STAGE.get(finding.get("pillar"), 1.0))


def demote(severity):
    order = ["low", "medium", "high", "critical"]
    return order[max(0, order.index(severity) - 1)]


def main():
    ap = argparse.ArgumentParser(description="Compose sub-skill findings into the audit report")
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--out", default=None, help="report JSON path (default: <workspace>/report.json)")
    ap.add_argument("--markdown", default=None, help="also write a human-readable Markdown report")
    args = ap.parse_args()

    workspace = os.path.abspath(args.workspace)
    with open(os.path.join(workspace, "manifest.json"), encoding="utf-8") as fh:
        manifest = json.load(fh)
    bundles = load_findings(workspace)

    findings, checks_run, notes, skills_used = [], [], [], []
    for bundle in bundles:
        skills_used.append({"skill": bundle.get("skill"), "pillar": bundle.get("pillar"),
                            "findings": len(bundle.get("findings", [])),
                            "checks_run": len(bundle.get("checks_run", []))})
        checks_run += bundle.get("checks_run", [])
        for note in bundle.get("notes", []):
            notes.append({"skill": bundle.get("skill"), "note": note})
        for item in bundle.get("findings", []):
            item.setdefault("confidence", "high")
            item.setdefault("pillar", bundle.get("pillar", "extract"))
            item["source_skill"] = bundle.get("skill")
            findings.append(item)

    # ---- composition rule 1: gating ---------------------------------------
    gate_ids = {"REACH-001", "REACH-008", "REACH-011"}
    gates = [f for f in findings if f["check_id"] in gate_ids and f["severity"] == "critical"]
    gate_note = None
    if gates:
        gate_note = (
            "ACCESS IS GATED. " + gates[0]["title"] + " Until this is fixed, none of the other "
            "findings below can improve how the brand appears in AI assistants -- the content is "
            "correct to fix, but it cannot be seen at all in the meantime. Fix the gate first.")
        for finding in findings:
            if finding["check_id"] not in gate_ids:
                finding["blocked_by"] = gates[0]["check_id"]

    # ---- composition rule 2: confounding ----------------------------------
    render_meta = manifest.get("render", {})
    renderer_missing = not render_meta.get("available")
    csr_suspected = [f for f in findings if f["check_id"] in ("READ-001", "READ-002")
                     and f["severity"] in ("high", "critical")]
    confound_note = None
    if renderer_missing and csr_suspected:
        confound_note = (
            f"CONTENT CHECKS ARE UNVERIFIED. No browser renderer was available "
            f"({render_meta.get('reason')}) and the site appears to be client-rendered "
            f"({csr_suspected[0]['check_id']}). The content-dependent findings below were computed "
            "from server HTML that may itself be incomplete, so they are reported at reduced "
            "confidence and flagged for browser verification. They are candidates, not confirmed "
            "defects.")
        for finding in findings:
            if finding["check_id"].startswith(CONTENT_DEPENDENT_PREFIXES):
                finding["confidence"] = "low"
                finding["severity"] = demote(finding["severity"])
                finding["suggested_action"]["priority"] = demote(
                    finding["suggested_action"].get("priority", finding["severity"]))
                finding["confounded_by"] = csr_suspected[0]["check_id"]
                finding["verification"] = (
                    "Re-run this audit with a browser renderer installed "
                    "(pip install playwright && playwright install chromium) to confirm.")

    # ---- composition rule 3: linking related root causes -------------------
    related_groups = [
        {"name": "The brand has no single, quotable self-definition",
         "members": {"QUOTE-001", "QUOTE-002", "ENTITY-002", "ENTITY-006", "ENTITY-007", "MARK-004"}},
        {"name": "Content exists but machines cannot read it",
         "members": {"READ-001", "READ-002", "READ-003", "READ-005", "READ-006", "READ-007"}},
        {"name": "Pages do not orient a visitor arriving from an AI citation",
         "members": {"STAY-001", "MARK-012", "QUOTE-007", "STAY-006"}},
        {"name": "Nothing on the site can be dated, so it is assumed stale",
         "members": {"TIME-001", "TIME-002", "TIME-005", "TIME-007"}},
    ]
    present = {f["check_id"] for f in findings}
    root_causes = []
    for group in related_groups:
        hit = sorted(group["members"] & present)
        if len(hit) >= 2:
            root_causes.append({"root_cause": group["name"], "finding_check_ids": hit})
            for finding in findings:
                if finding["check_id"] in hit:
                    finding.setdefault("related_check_ids",
                                       [c for c in hit if c != finding["check_id"]])

    # ---- rank and assign stable ids ---------------------------------------
    findings.sort(key=lambda f: (-impact(f), f["pillar"], f["check_id"]))
    for index, finding in enumerate(findings, 1):
        finding["id"] = f"F-{index:03d}"

    # ---- pillar scores -----------------------------------------------------
    pillar_scores = {}
    for pillar in PILLAR_STAGE:
        score = 100.0
        for finding in findings:
            if finding["pillar"] != pillar:
                continue
            score -= SCORE_PENALTY.get(finding["severity"], 3) * \
                CONFIDENCE_WEIGHT.get(finding["confidence"], 1.0)
        pillar_scores[pillar] = max(0, round(score))
    overall = round(sum(pillar_scores.values()) / len(pillar_scores))

    # ---- proactive recommendations ----------------------------------------
    # Findings already flagged `proactive` are NOT echoed here: they carry their own
    # evidence and remediation as findings, and listing them twice would pad the report.
    proactive = [{**item, "type": "proactive_improvement"} for item in PROACTIVE_LIBRARY]

    # ---- remediation plan --------------------------------------------------
    # Phase 1's name is adaptive: calling minor sitemap hygiene "everything else depends
    # on it" would overstate it, and readers stop trusting phase labels that oversell.
    has_blocker = any(f["severity"] == "critical" or
                      (f["pillar"] == "access" and f["severity"] == "high")
                      for f in findings)
    phase_one_name = ("Unblock (do first -- everything else depends on it)" if has_blocker
                      else "Reachability groundwork (quick wins, no blockers found)")
    phases = [
        {"phase": 1, "name": phase_one_name,
         "criteria": "critical severity, or in the access pillar",
         "items": [f["id"] for f in findings
                   if f["severity"] == "critical" or f["pillar"] == "access"]},
        {"phase": 2, "name": "Make the content machine-readable",
         "criteria": "parse and extract pillars, high/medium severity",
         "items": [f["id"] for f in findings
                   if f["pillar"] in ("parse", "extract") and f["severity"] in ("high", "medium")]},
        {"phase": 3, "name": "Earn trust and keep the visitor",
         "criteria": "trust and engage pillars",
         "items": [f["id"] for f in findings
                   if f["pillar"] in ("trust", "engage") and f["severity"] in ("high", "medium")]},
        {"phase": 4, "name": "Polish and get ahead",
         "criteria": "low severity plus the proactive recommendations",
         "items": [f["id"] for f in findings if f["severity"] == "low"]
                  + [p["id"] for p in proactive]},
    ]
    seen = set()
    for phase in phases:                                    # each item appears once, earliest phase
        phase["items"] = [i for i in phase["items"]
                          if not (i in seen or seen.add(i))]

    counts = {level: sum(1 for f in findings if f["severity"] == level)
              for level in ("critical", "high", "medium", "low")}

    # ---- composition rule 5: refuse to score an audit that did not happen ----
    # A crawl that fetched nothing produces almost no findings, which the scoring
    # formula would otherwise read as "almost nothing wrong". Reporting 98/100 for a
    # site we never actually read is the single most damaging thing this tool could
    # do, so an inconclusive crawl is stated as such and the score is withheld.
    ok_pages = manifest.get("ok_page_count", 0)
    crawled = manifest.get("page_count", 0)
    # Two ways a crawl can fail to read a site: hard (nothing returned 200) and soft
    # (everything returned 200 but with identical boilerplate). Only checking status
    # codes catches the first, and the second scores just as misleadingly well.
    duplicate_ratio = manifest.get("duplicate_body_ratio", 0.0)
    distinct_bodies = manifest.get("distinct_text_bodies")
    soft_blocked = ok_pages >= 4 and duplicate_ratio >= 0.6

    # Third failure mode: the crawl succeeded but read almost nothing. A
    # client-rendered shell serves 200 OK with a few dozen words and no links, so
    # link-following finds no second page. Every prevalence denominator is then 1,
    # which reads as "100% of pages" and escalates severity on a sample far too
    # small to describe a site. A genuinely small brochure site is different: it
    # has real prose, so the word floor lets it through and keeps its score.
    sampled_words = sum(p.get("word_count", 0) for p in manifest.get("pages", [])
                        if p.get("status") == 200)
    thin_sample = ok_pages > 0 and ok_pages < 3 and sampled_words < 300

    inconclusive = (ok_pages == 0
                    or (crawled >= 3 and ok_pages / max(1, crawled) < 0.34)
                    or soft_blocked
                    or thin_sample)
    inconclusive_note = None
    if thin_sample:
        inconclusive_note = (
            f"AUDIT INCONCLUSIVE. The crawl obtained only {ok_pages} usable page(s) totalling "
            f"{sampled_words} words, which is too small a sample to describe a site. Findings "
            "below are computed over that one sample, so their prevalence figures ('1/1 pages') "
            "read as site-wide when they are not, and no score is reported. This is the "
            "signature of a client-rendered application whose server response carries no prose "
            "and no links for a crawler to follow. Re-run with a browser renderer "
            "(pip install playwright && playwright install chromium); if the site genuinely is "
            "a single page, the access and content findings still stand on their own terms.")
    elif soft_blocked:
        inconclusive_note = (
            f"AUDIT INCONCLUSIVE. {int(duplicate_ratio * 100)}% of the {ok_pages} pages that "
            f"returned HTTP 200 served byte-identical content ({distinct_bodies} distinct bodies "
            "in total), so the crawl never saw page-specific content. The content, "
            "structured-data, freshness and engagement checks below were computed from repeated "
            "boilerplate and are NOT a verdict on this site. No score is reported. Usual causes: "
            "a bot challenge or consent interstitial served under a 200 status, or client-side "
            "routing that returns the same shell for every path. Re-run with a browser renderer "
            "(pip install playwright && playwright install chromium), or from an environment the "
            "site does not challenge.")
    elif inconclusive:
        statuses = sorted({str(p.get("status")) for p in manifest.get("pages", [])})
        inconclusive_note = (
            f"AUDIT INCONCLUSIVE. Only {ok_pages} of {crawled} fetched URL(s) returned usable "
            f"HTML (status codes seen: {', '.join(statuses) or 'none'}). The content, structured-data, "
            "freshness and engagement checks had almost nothing to read, so their silence means "
            "'not assessed', NOT 'no problems found'. No score is reported. Usual causes: the site "
            "blocks non-browser user-agents at the CDN/WAF, the entry URL is wrong or redirects "
            "off-domain, or a geo/consent gate is returning an error to the crawler. Re-run against "
            "the exact canonical URL, and if it still fails, the access findings above are the "
            "real result.")

    # Withhold the score for BOTH failure modes. This sits outside the branches above
    # so a new inconclusive case can never be added without also suppressing the score.
    if inconclusive:
        overall = None
        pillar_scores = {k: None for k in pillar_scores}

    report = {
        "site": manifest.get("site"),
        "audited_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "summary": {
            "total_findings": len(findings),
            "critical": counts["critical"], "high": counts["high"],
            "medium": counts["medium"], "low": counts["low"],
            "ai_readiness_score": overall,
            "pillar_scores": pillar_scores,
            "headline": ("Audit inconclusive: the site could not be read by the crawler, so no "
                         "score is reported." if inconclusive else
                         gate_note.split(".")[0] + "." if gate_note else
                         (f"{counts['critical']} critical and {counts['high']} high-severity issues "
                          f"limit how this site is found, read and cited by AI assistants."
                          if counts["critical"] or counts["high"] else
                          "No critical or high-severity blockers found; see the proactive "
                          "recommendations for upside.")),
        },
        "scope": {
            "start_url": manifest.get("start_url"),
            "pages_crawled": manifest.get("page_count"),
            "pages_ok": manifest.get("ok_page_count"),
            "page_types": manifest.get("page_types"),
            "crawled_at": manifest.get("crawled_at"),
            "crawl_duration_seconds": manifest.get("duration_seconds"),
            "user_agent": manifest.get("user_agent"),
            "renderer": render_meta,
            "robots_respected": True,
            "read_only": True,
        },
        "method": {
            "skills": skills_used,
            "checks_run": sorted(set(checks_run)),
            "checks_run_count": len(set(checks_run)),
            "pillars": PILLAR_LABEL,
            "scoring": ("Each pillar starts at 100. Every finding deducts "
                        "critical 40 / high 22 / medium 10 / low 3, scaled by confidence "
                        "(high 1.0, medium 0.75, low 0.5). The overall score is the unweighted mean "
                        "of the five pillar scores."),
        },
        "limitations": [note for note in [inconclusive_note, gate_note, confound_note] if note]
                       + ([] if not renderer_missing else
                          ["No JavaScript renderer was available; render-parity findings are "
                           "heuristic. Install Playwright for measured results."])
                       + ([] if os.path.exists(os.path.join(workspace, "probe_results.json")) else
                          ["Off-site corroboration probes were not executed, so ENTITY-006 to ENTITY-011 "
                           "are unevaluated rather than passing."]),
        "root_causes": root_causes,
        "findings": findings,
        "proactive_recommendations": proactive,
        "remediation_plan": phases,
        "notes": notes,
    }

    out_path = args.out or os.path.join(workspace, "report.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)

    if args.markdown:
        with open(args.markdown, "w", encoding="utf-8") as fh:
            fh.write(render_markdown(report))

    print(json.dumps({"report": out_path, "findings": len(findings),
                      "score": overall, "summary": counts}, indent=2))


def render_markdown(report):
    """Human-readable companion to the JSON. A non-expert reads this one."""
    summary = report["summary"]
    lines = [
        f"# AI-Readiness Audit: {report['site']}", "",
        f"**Audited:** {report['audited_at']}  ",
        (f"**Overall AI-readiness score:** {summary['ai_readiness_score']}/100  "
         if summary.get("ai_readiness_score") is not None
         else "**Overall AI-readiness score:** not reported (audit inconclusive)  "),
        f"**Findings:** {summary['total_findings']} "
        f"({summary['critical']} critical, {summary['high']} high, "
        f"{summary['medium']} medium, {summary['low']} low)", "",
        f"> {summary['headline']}", "",
        "## Scores by pillar", "",
        "| Pillar | Score |", "| --- | --- |",
    ]
    for pillar, score in report["summary"]["pillar_scores"].items():
        shown = f"{score}/100" if score is not None else "not scored"
        lines.append(f"| {report['method']['pillars'][pillar]} | {shown} |")

    if report["limitations"]:
        lines += ["", "## Read this first", ""]
        lines += [f"- {item}" for item in report["limitations"]]

    if report["root_causes"]:
        lines += ["", "## Shared root causes", "",
                  "These findings are symptoms of the same underlying problem -- fix the cause once.", ""]
        for group in report["root_causes"]:
            lines.append(f"- **{group['root_cause']}** — {', '.join(group['finding_check_ids'])}")

    lines += ["", "## Fix in this order", ""]
    by_id = {f["id"]: f for f in report["findings"]}
    proactive_by_id = {p["id"]: p for p in report["proactive_recommendations"]}
    for phase in report["remediation_plan"]:
        if not phase["items"]:
            continue
        lines += [f"### Phase {phase['phase']}: {phase['name']}", ""]
        for item_id in phase["items"]:
            if item_id in by_id:
                finding = by_id[item_id]
                lines.append(f"- `{item_id}` **{finding['title']}** "
                             f"({finding['severity']}, effort: "
                             f"{finding['suggested_action'].get('effort', 'n/a')}) — "
                             f"{finding['suggested_action']['summary']}")
            elif item_id in proactive_by_id:
                item = proactive_by_id[item_id]
                lines.append(f"- `{item_id}` **{item['title']}** "
                             f"(proactive, effort: {item.get('effort', 'n/a')})")
        lines.append("")

    lines += ["## Findings in detail", ""]
    for finding in report["findings"]:
        lines += [
            f"### {finding['id']} — {finding['title']}", "",
            f"**Severity:** {finding['severity']} | **Pillar:** {finding['pillar']} | "
            f"**Confidence:** {finding['confidence']} | **Check:** `{finding['check_id']}` | "
            f"**Source skill:** `{finding.get('source_skill', 'n/a')}`", "",
            f"**Evidence.** {finding['evidence']}", "",
        ]
        if finding.get("blocked_by"):
            lines += [f"> Blocked by `{finding['blocked_by']}`: fixing this has no visible effect "
                      "until that access issue is resolved.", ""]
        if finding.get("confounded_by"):
            lines += [f"> Unverified: this may be an artefact of `{finding['confounded_by']}`. "
                      f"{finding.get('verification', '')}", ""]
        action = finding["suggested_action"]
        lines += [f"**Fix ({action.get('priority')} priority, {action.get('effort', 'n/a')} effort, "
                  f"owner: {action.get('owner', 'n/a')}).** {action['summary']}", ""]
        for step in action.get("steps", []):
            lines.append(f"1. {step}")
        if finding.get("affected_urls"):
            lines += ["", f"**Affected URLs ({len(finding['affected_urls'])} shown):**", ""]
            lines += [f"- {url}" for url in finding["affected_urls"][:10]]
        lines.append("")

    lines += ["## Proactive improvements", "",
              "Opportunities beyond the defects found -- worth doing even where nothing is broken.", ""]
    for item in report["proactive_recommendations"]:
        lines += [f"### {item['id']} — {item['title']}", "",
                  f"**Pillar:** {item['pillar']} | **Effort:** {item.get('effort', 'n/a')} | "
                  f"**Owner:** {item.get('owner', 'n/a')}", "",
                  f"{item.get('rationale', '')}", ""]
        for step in item.get("steps", []):
            lines.append(f"1. {step}")
        lines.append("")

    method = report["method"]
    lines += ["## Method", "",
              f"{method['checks_run_count']} checks executed across "
              f"{len(method['skills'])} skills over {report['scope']['pages_crawled']} crawled pages "
              f"({report['scope']['crawl_duration_seconds']}s). "
              f"Read-only; robots.txt respected.", "",
              f"*Scoring: {method['scoring']}*", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
