#!/usr/bin/env python3
"""
verify_report.py -- fail-closed schema and quality gate for the audit report.

Two jobs:

  contract  -- every required field from the brief is present and correctly typed
  quality   -- every finding meets the evidence bar this marketplace commits to

The quality gate exists because the schema alone cannot stop the failure mode that
matters most: a finding with a plausible title and vague, unfalsifiable evidence.
Each finding must cite a measured quantity and be traceable to a check id, or the
report does not ship. Stdlib only -- no jsonschema dependency.

Exit code 0 = valid, 1 = invalid (errors printed to stderr).
"""
import json
import re
import sys

SEVERITIES = {"critical", "high", "medium", "low"}
ISO_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")
FINDING_ID = re.compile(r"^F-\d{3,}$")
# Evidence must contain a real measurement: a count, a ratio, a percentage or a status code.
QUANTITY = re.compile(
    r"\b\d+\s*/\s*\d+\b"                            # n/N
    r"|\b\d+\s*%"                                   # percentages
    r"|status=\d+|->\s*\d{3}\b"                     # HTTP status codes
    # a count, optionally followed by up to two qualifier words, then a countable noun
    r"|\b\d+\s+(?:[\w()\-]+\s+){0,2}(?:ms|KB|MB|words?|pages?|months?|fields?|"
    r"links?|characters?|sentences?|sources?|domains?|entit(?:y|ies)|sections?|"
    r"images?|claims?|scripts?|blocks?|nodes?|queries|directives?|redirects?|"
    r"agents?|errors?|dates?|pairs?|acronyms?|terms?|forms?|URLs?|headings?)"
    r"(?:\(s\))?\b"
    r"|\b\d+\s+(?:distinct|malformed|crawled|independent|identity|recognised)\b")

# Remediation that games or deceives machines backfires: search engines penalise it and
# assistants discount it. Any recommendation matching these -- most likely from an
# agent-written judgment finding -- blocks the report. A sentence that says to avoid
# the tactic ("never hide text") is not a recommendation of it.
COUNTERPRODUCTIVE = {
    "keyword stuffing": re.compile(r"\bkeyword density\b|\brepeat (?:the |your )?(?:target |main )?"
                                   r"keywords?\b|\bstuff(?:ing)? (?:in )?keywords?\b", re.I),
    "hidden text or cloaking": re.compile(r"\bhidden text\b|\bhide (?:the |some )?text\b|"
                                          r"\bcloak(?:ing)?\b|\bserve (?:different|separate) content "
                                          r"to (?:bots|crawlers|ai)\b", re.I),
    "instructions aimed at AI models": re.compile(r"\binstruct (?:the )?(?:ai|llms?|models?|assistants?)"
                                                  r"\b|\bignore (?:all |any )?previous instructions\b|"
                                                  r"\bprompt[- ]inject", re.I),
    "removing dates or caveats": re.compile(r"\b(?:remove|strip|delete|hide) (?:all |the |any )?"
                                            r"(?:publication |published |visible |old )?"
                                            r"(?:dates?|disclaimers?|caveats?)\b", re.I),
    "fabricated social proof": re.compile(r"\b(?:fake|fabricated|invented) (?:reviews?|testimonials?|"
                                          r"ratings?)\b", re.I),
}
NEGATION = re.compile(r"\b(?:never|not|don't|do not|avoid|without|instead of|rather than|no)\b", re.I)


def counterproductive_advice(action):
    """Return (tactic, sentence) pairs where the action recommends a manipulative tactic."""
    texts = [str(action.get("summary", ""))] + [str(s) for s in action.get("steps") or []]
    hits = []
    for text in texts:
        for sentence in re.split(r"(?<=[.;!?])\s+", text):
            for tactic, pattern in COUNTERPRODUCTIVE.items():
                match = pattern.search(sentence)
                if match and not NEGATION.search(sentence[:match.start()]):
                    hits.append((tactic, sentence.strip()[:120]))
    return hits


def fail(errors, message):
    errors.append(message)


def validate(report):
    errors, warnings = [], []

    # ---- required top-level contract (the brief's floor) ------------------
    for field in ("site", "audited_at", "summary", "findings"):
        if field not in report:
            fail(errors, f"missing required top-level field: {field}")
    if errors:
        return errors, warnings

    if not isinstance(report["site"], str) or not report["site"].strip():
        fail(errors, "site must be a non-empty string")
    if not ISO_Z.match(str(report["audited_at"])):
        fail(errors, f"audited_at must be ISO-8601 UTC ending in Z, got {report['audited_at']!r}")

    summary = report["summary"]
    for field in ("total_findings", "critical", "high", "medium"):
        if field not in summary:
            fail(errors, f"summary missing required field: {field}")
        elif not isinstance(summary[field], int):
            fail(errors, f"summary.{field} must be an integer")

    findings = report["findings"]
    if not isinstance(findings, list):
        fail(errors, "findings must be an array")
        return errors, warnings

    if isinstance(summary.get("total_findings"), int) and summary["total_findings"] != len(findings):
        fail(errors, f"summary.total_findings ({summary['total_findings']}) != "
                     f"len(findings) ({len(findings)})")
    for level in ("critical", "high", "medium", "low"):
        if level in summary:
            actual = sum(1 for f in findings if f.get("severity") == level)
            if summary[level] != actual:
                fail(errors, f"summary.{level} ({summary[level]}) != actual count ({actual})")

    # ---- per-finding contract + quality gate ------------------------------
    seen_ids = set()
    for index, finding in enumerate(findings):
        where = f"findings[{index}]"
        for field in ("id", "title", "severity", "evidence", "suggested_action"):
            if field not in finding:
                fail(errors, f"{where} missing required field: {field}")
        if errors and where in str(errors[-1]):
            continue

        if not FINDING_ID.match(str(finding.get("id", ""))):
            fail(errors, f"{where}.id must match F-NNN, got {finding.get('id')!r}")
        if finding.get("id") in seen_ids:
            fail(errors, f"{where}.id is a duplicate: {finding.get('id')}")
        seen_ids.add(finding.get("id"))

        if finding.get("severity") not in SEVERITIES:
            fail(errors, f"{where}.severity must be one of {sorted(SEVERITIES)}, "
                         f"got {finding.get('severity')!r}")

        title = str(finding.get("title", ""))
        if len(title) < 10:
            fail(errors, f"{where}.title is too short to be actionable: {title!r}")

        evidence = str(finding.get("evidence", ""))
        if len(evidence) < 60:
            fail(errors, f"{where}.evidence is too thin ({len(evidence)} chars); "
                         "every finding must carry concrete, checkable evidence")
        elif not QUANTITY.search(evidence):
            warnings.append(f"{where} ({finding.get('id')}) evidence cites no measured quantity; "
                            "findings should quantify what was observed")

        action = finding.get("suggested_action")
        if not isinstance(action, dict):
            fail(errors, f"{where}.suggested_action must be an object")
            continue
        for field in ("summary", "priority"):
            if field not in action:
                fail(errors, f"{where}.suggested_action missing required field: {field}")
        if action.get("priority") not in SEVERITIES:
            fail(errors, f"{where}.suggested_action.priority must be one of {sorted(SEVERITIES)}, "
                         f"got {action.get('priority')!r}")
        if len(str(action.get("summary", ""))) < 15:
            fail(errors, f"{where}.suggested_action.summary is too vague to act on")
        if not action.get("steps"):
            warnings.append(f"{where} ({finding.get('id')}) has no concrete remediation steps")
        for tactic, sentence in counterproductive_advice(action):
            fail(errors, f"{where} ({finding.get('id')}) recommends {tactic}, which search engines "
                         f"penalise and assistants discount: {sentence!r}")

        # Marketplace-specific invariants: traceability and honest confidence.
        if "check_id" not in finding:
            warnings.append(f"{where} has no check_id, so it is not traceable to a named check")
        if finding.get("confidence") not in (None, "high", "medium", "low"):
            fail(errors, f"{where}.confidence must be high/medium/low")
        if finding.get("confounded_by") and finding.get("confidence") != "low":
            fail(errors, f"{where} is marked confounded_by={finding['confounded_by']} but its "
                         "confidence was not reduced -- confounded findings must be demoted")

    # ---- report-level sanity ----------------------------------------------
    if not report.get("findings") and not report.get("proactive_recommendations"):
        warnings.append("report contains neither findings nor proactive recommendations")
    ids = [f.get("id") for f in findings]
    if ids != sorted(ids, key=lambda x: int(str(x).split("-")[1]) if "-" in str(x) else 0):
        warnings.append("finding ids are not in ascending order")

    return errors, warnings


def main():
    if len(sys.argv) < 2:
        print("usage: verify_report.py <report.json>", file=sys.stderr)
        sys.exit(2)
    with open(sys.argv[1], encoding="utf-8") as fh:
        report = json.load(fh)
    errors, warnings = validate(report)

    for warning in warnings:
        print(f"WARN  {warning}", file=sys.stderr)
    for error in errors:
        print(f"ERROR {error}", file=sys.stderr)

    if errors:
        print(f"INVALID: {len(errors)} error(s), {len(warnings)} warning(s)", file=sys.stderr)
        sys.exit(1)
    print(f"VALID: {len(report.get('findings', []))} finding(s), {len(warnings)} warning(s)")
    sys.exit(0)


if __name__ == "__main__":
    main()
