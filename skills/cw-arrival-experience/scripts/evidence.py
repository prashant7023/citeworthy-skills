"""
evidence.py -- shared read-only accessor for the evidence bundle.

Every analyzer skill vendors an identical copy of this file. That is deliberate:
the agentskills.io format requires each skill folder to be independently valid
and portable, so no skill may import across folder boundaries. The file is small,
pure and has no dependencies, so the duplication costs nothing at runtime.

Analyzers NEVER touch the network. They only read what harvest_site.py already fetched,
which is what makes the audit deterministic and re-runnable on saved evidence.
"""
import json
import os
import re
from datetime import datetime, timezone

SEVERITY_ORDER = ["low", "medium", "high", "critical"]


def load_bundle(workspace):
    """Return (manifest, [page records]) for an evidence bundle."""
    workspace = os.path.abspath(workspace)
    with open(os.path.join(workspace, "manifest.json"), encoding="utf-8") as fh:
        manifest = json.load(fh)
    pages = []
    for entry in manifest.get("pages", []):
        path = os.path.join(workspace, "pages", f"{entry['id']}.json")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                pages.append(json.load(fh))
    return manifest, pages


def html_pages(pages):
    """Successfully-fetched HTML pages -- the only sound basis for content checks."""
    return [p for p in pages
            if p.get("status") == 200 and not p.get("non_html") and p.get("raw")]


def best_view(page):
    """The DOM an extractor would most plausibly see.

    Prefers the rendered DOM when a renderer was available, because judging
    content quality from raw HTML on a JS-heavy site produces false positives
    that belong to render-parity, not to the content checks.
    """
    if page.get("render_available") and page.get("rendered"):
        return page["rendered"], "rendered"
    return page["raw"], "raw"


def body_text(view):
    """Main-content text where the page marks it, else the full text.

    Falling back matters: many real sites never use <main>/<article>, and
    treating that as 'no content' is a classic false positive.
    """
    main = (view.get("main_text") or "").strip()
    if len(main.split()) >= 80:
        return main
    return view.get("text") or ""


def brand_tokens(manifest):
    """Candidate brand-name tokens derived from the domain, for entity checks."""
    root = (manifest.get("site_root") or "").split(".")[0]
    tokens = {root.lower()} if root else set()
    for part in re.split(r"[-_]", root or ""):
        if len(part) > 2:
            tokens.add(part.lower())
    return {t for t in tokens if len(t) > 2}


def escalate(severity, prevalence, homepage_hit=False):
    """Deterministic severity adjustment by blast radius.

    A defect on one page of forty is not the same problem as the same defect on
    every page, and a defect on the homepage is what an assistant hits first.
    """
    idx = SEVERITY_ORDER.index(severity)
    if prevalence >= 0.5 or homepage_hit:
        idx = min(idx + 1, len(SEVERITY_ORDER) - 1)
    elif prevalence <= 0.15:
        idx = max(idx - 1, 0)
    return SEVERITY_ORDER[idx]


class Findings:
    """Accumulator that enforces the finding contract every analyzer must meet."""

    def __init__(self, skill, pillar):
        self.skill, self.pillar = skill, pillar
        self.items, self.checks, self.notes = [], [], []

    def check(self, check_id):
        """Register a check as executed, whether or not it fires.

        Recording non-firing checks is what lets the final report distinguish
        'clean' from 'never looked', and keeps coverage auditable.
        """
        if check_id not in self.checks:
            self.checks.append(check_id)

    def add(self, check_id, title, severity, evidence, summary, steps,
            effort="medium", owner="web/dev", confidence="high", priority=None, **extra):
        self.check(check_id)
        item = {
            "check_id": check_id, "title": title, "severity": severity,
            "evidence": evidence, "pillar": extra.pop("pillar", self.pillar),
            "confidence": confidence,
            "suggested_action": {"summary": summary, "priority": priority or severity,
                                 "steps": steps, "effort": effort, "owner": owner},
        }
        item.update(extra)
        self.items.append(item)
        return item

    def note(self, text):
        self.notes.append(text)

    def write(self, workspace, pages_analysed=0):
        out = {
            "skill": self.skill, "pillar": self.pillar,
            "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "checks_run": self.checks, "pages_analysed": pages_analysed,
            "findings": self.items, "notes": self.notes,
        }
        target = os.path.join(os.path.abspath(workspace), "findings", f"{self.skill}.json")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as fh:
            json.dump(out, fh, ensure_ascii=False, indent=2)
        return out
