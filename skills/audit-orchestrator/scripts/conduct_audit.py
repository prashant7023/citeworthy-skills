#!/usr/bin/env python3
"""
conduct_audit.py -- one-command pipeline runner for the whole marketplace.

The orchestrator SKILL.md describes the agent-driven path, where the agent invokes
each skill in turn and can interleave its own web-search work. This script is the
deterministic equivalent for CI, for reproducing a past audit from a saved bundle,
and for the case where the agent just wants the whole thing run in one call.

Both paths execute the same scripts in the same order and produce byte-identical
findings from identical inputs.

Stage order is causal, not arbitrary:
    crawl (fetches once) -> read -> extract -> trust -> engage -> compose

Every stage after the crawl is offline, so the network cost of the entire audit is
one polite crawl.
"""
import argparse
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS = os.path.abspath(os.path.join(HERE, "..", ".."))

# (skill folder, script, extra args, required?) -- order is the pipeline order.
STAGES = [
    ("render-extraction-audit", "probe_render_gap.py", [], True),
    ("structured-data-audit", "probe_schema_truth.py", [], True),
    ("answer-extractability-audit", "probe_quotability.py", [], True),
    ("freshness-audit", "probe_time_decay.py", [], True),
    ("entity-corroboration-audit", "probe_entity_consensus.py", ["--phase", "onsite"], True),
    ("engagement-audit", "probe_arrival.py", [], True),
]


def run(cmd, cwd=None, timeout=300):
    started = time.time()
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                          timeout=timeout, encoding="utf-8", errors="replace")
    return {"cmd": " ".join(cmd[1:]), "returncode": proc.returncode,
            "stdout": (proc.stdout or "").strip()[-2000:],
            "stderr": (proc.stderr or "").strip()[-2000:],
            "seconds": round(time.time() - started, 1)}


def main():
    ap = argparse.ArgumentParser(description="Run the full brand AI-readiness audit pipeline")
    ap.add_argument("url", help="Site to audit, e.g. example.com or https://example.com/")
    ap.add_argument("--workspace", required=True, help="Directory for the evidence bundle and report")
    ap.add_argument("--max-pages", type=int, default=14)
    ap.add_argument("--max-depth", type=int, default=3)
    ap.add_argument("--budget-seconds", type=int, default=90,
                help="Crawl budget for the network stage. Agent inference latency counts "
                     "against the same five-minute ceiling, so this leaves headroom.")
    ap.add_argument("--render", choices=["auto", "off"], default="auto")
    ap.add_argument("--markdown", action="store_true", help="Also write report.md")
    ap.add_argument("--merge-probes", action="store_true",
                    help="Run the corroboration merge phase (requires probe_results.json)")
    args = ap.parse_args()

    workspace = os.path.abspath(args.workspace)
    os.makedirs(workspace, exist_ok=True)
    python = sys.executable
    log, started = [], time.time()

    # -- stage 1: crawl (the only stage that touches the network) -----------
    crawl_dir = os.path.join(SKILLS, "crawl-access-audit", "scripts")
    result = run([python, os.path.join(crawl_dir, "harvest_site.py"), args.url,
                  "--workspace", workspace,
                  "--max-pages", str(args.max_pages),
                  "--max-depth", str(args.max_depth),
                  "--budget-seconds", str(args.budget_seconds),
                  "--render", args.render],
                 cwd=crawl_dir, timeout=args.budget_seconds + 120)
    log.append({"stage": "crawl-access-audit", **result})
    if result["returncode"] != 0:
        print(json.dumps({"error": "crawl failed", "detail": result}, indent=2))
        sys.exit(1)

    # -- stages 2-6: offline analyzers --------------------------------------
    for skill, script, extra, required in STAGES:
        script_dir = os.path.join(SKILLS, skill, "scripts")
        result = run([python, os.path.join(script_dir, script),
                      "--workspace", workspace] + extra,
                     cwd=script_dir, timeout=180)
        log.append({"stage": skill, **result})
        if result["returncode"] != 0 and required:
            # A failing analyzer must not lose the rest of the audit; record and continue.
            log[-1]["degraded"] = True

    if args.merge_probes:
        script_dir = os.path.join(SKILLS, "entity-corroboration-audit", "scripts")
        result = run([python, os.path.join(script_dir, "probe_entity_consensus.py"),
                      "--workspace", workspace, "--phase", "merge"],
                     cwd=script_dir, timeout=180)
        log.append({"stage": "entity-corroboration-audit (merge)", **result})

    # -- stage 7: compose ----------------------------------------------------
    aggregate = [python, os.path.join(HERE, "compose_report.py"), "--workspace", workspace]
    if args.markdown:
        aggregate += ["--markdown", os.path.join(workspace, "report.md")]
    result = run(aggregate, cwd=HERE, timeout=120)
    log.append({"stage": "audit-orchestrator", **result})
    if result["returncode"] != 0:
        print(json.dumps({"error": "aggregation failed", "detail": result}, indent=2))
        sys.exit(1)

    # -- stage 8: validate ---------------------------------------------------
    validate = run([python, os.path.join(HERE, "verify_report.py"),
                    os.path.join(workspace, "report.json")], cwd=HERE, timeout=60)
    log.append({"stage": "validate", **validate})

    with open(os.path.join(workspace, "run_log.json"), "w", encoding="utf-8") as fh:
        json.dump({"url": args.url, "total_seconds": round(time.time() - started, 1),
                   "stages": log}, fh, indent=2)

    degraded = [entry["stage"] for entry in log if entry.get("degraded")]
    print(json.dumps({
        "report": os.path.join(workspace, "report.json"),
        "markdown": os.path.join(workspace, "report.md") if args.markdown else None,
        "total_seconds": round(time.time() - started, 1),
        "schema_valid": validate["returncode"] == 0,
        "degraded_stages": degraded,
    }, indent=2))
    sys.exit(0 if validate["returncode"] == 0 else 2)


if __name__ == "__main__":
    main()
