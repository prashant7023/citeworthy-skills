#!/usr/bin/env python3
"""
probe_entity_consensus.py -- ENT-* checks: is the brand a resolvable entity the web agrees on?

Stage 4b. Everything upstream concerns one domain talking about itself. This
skill covers the part a site cannot fix by editing itself: whether the wider web
identifies the same entity, describes it consistently, and corroborates its
claims. A fact asserted in exactly one place is fragile; the same fact repeated
by independent sources is what a model will confidently repeat back.

Two-phase design, because only half of this can be done offline:

  phase 1 (--phase onsite)  Deterministic, no network. Audits the identity
                            signals the site itself controls, and emits
                            probe_plan.json: the exact queries an agent must run.
  phase 2 (--phase merge)   Consumes probe_results.json (written by the agent
                            from real search results) and derives the
                            corroboration findings.

Splitting it this way keeps the script deterministic and keeps the agent's
non-deterministic work behind a typed contract, so the same probe results always
produce the same findings. If phase 2 never runs, the audit degrades honestly:
it reports the on-site half and records that off-site corroboration was not
verified, rather than inventing a verdict.
"""
import argparse
import json
import os
import re
from urllib import parse

from evidence import load_bundle, html_pages, best_view, body_text, Findings

AUTHORITY = {
    "wikipedia.org": "Wikipedia", "wikidata.org": "Wikidata", "linkedin.com": "LinkedIn",
    "crunchbase.com": "Crunchbase", "github.com": "GitHub", "apps.apple.com": "App Store",
    "play.google.com": "Google Play", "x.com": "X", "twitter.com": "X",
    "facebook.com": "Facebook", "instagram.com": "Instagram", "youtube.com": "YouTube",
    "g.page": "Google Business", "trustpilot.com": "Trustpilot", "g2.com": "G2",
    "capterra.com": "Capterra", "glassdoor.com": "Glassdoor", "producthunt.com": "Product Hunt",
    "companieshouse.gov.uk": "Companies House", "opencorporates.com": "OpenCorporates",
}
PHONE = re.compile(r"(?:\+\d{1,3}[\s.\-]?)?(?:\(\d{2,4}\)[\s.\-]?)?\d{3,4}[\s.\-]\d{3,4}(?:[\s.\-]\d{2,4})?")
AUTHOR_HINT = re.compile(
    r"\b(?:by|written by|author|posted by|reviewed by)\s+[A-Z][a-z]+\s+[A-Z][a-z]+", re.I)
SINGLE_SOURCE_CLAIM = re.compile(
    r"\b(?:award[- ]winning|industry[- ]leading|market leader|#\s?1|number one|best[- ]in[- ]class|"
    r"world[- ]class|trusted by (?:over )?[\d,]+|[\d,]+\+? (?:customers|clients|users|companies)|"
    r"raised \$[\d.]+|iso\s?\d+|soc\s?2|certified)\b", re.I)


def flatten(node, out=None):
    if out is None:
        out = []
    if isinstance(node, list):
        for item in node:
            flatten(item, out)
    elif isinstance(node, dict):
        if "@graph" in node:
            flatten(node["@graph"], out)
        if "@type" in node or "type" in node:
            out.append(node)
        for value in node.values():
            if isinstance(value, (dict, list)):
                flatten(value, out)
    return out


def types_of(node):
    raw = node.get("@type") or node.get("type") or []
    if isinstance(raw, str):
        raw = [raw]
    return [str(t).split("/")[-1] for t in raw if t]


def norm_desc(text):
    return re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower()).split()


def jaccard(a, b):
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


# --------------------------------------------------------------------- phase 1

def phase_onsite(args, manifest, docs, find):
    total = len(docs)
    host = manifest.get("site", "")
    root = manifest.get("site_root", host)
    brand_guess = root.split(".")[0]

    all_nodes = []
    for page in docs:
        view, _ = best_view(page)
        for node in flatten(view.get("jsonld", [])):
            node["_url"] = page["url"]
            all_nodes.append(node)

    org_nodes = [n for n in all_nodes
                 if {"Organization", "LocalBusiness", "Corporation", "NGO", "OnlineStore",
                     "EducationalOrganization", "Restaurant", "Store"} & set(types_of(n))]

    # Declared brand name: prefer structured data, fall back to the domain.
    declared_names = []
    for node in org_nodes:
        for field in ("name", "legalName", "alternateName"):
            value = node.get(field)
            if isinstance(value, str) and value.strip():
                declared_names.append(value.strip())
    brand_name = declared_names[0] if declared_names else brand_guess

    # -- ENTITY-001: outbound identity anchors ---------------------------------
    outbound = {}
    for page in docs:
        view, _ = best_view(page)
        for link in view.get("links", []):
            target = link.get("abs") or ""
            netloc = parse.urlparse(target).netloc.lower()
            for domain, label in AUTHORITY.items():
                if netloc.endswith(domain):
                    outbound.setdefault(label, target)
    same_as = []
    for node in org_nodes:
        value = node.get("sameAs")
        if isinstance(value, str):
            same_as.append(value)
        elif isinstance(value, list):
            same_as += [v for v in value if isinstance(v, str)]

    anchors = set(outbound)
    for url in same_as:
        netloc = parse.urlparse(url).netloc.lower()
        for domain, label in AUTHORITY.items():
            if netloc.endswith(domain):
                anchors.add(label)

    # Entity resolution is a weight of evidence, not a count of links. "Fewer than two
    # profiles = fail" punishes a single-location business whose Google Business Profile
    # is genuinely the right and sufficient anchor, while passing a startup that links
    # two social accounts nobody corroborates. Score the signals instead, show the
    # arithmetic in the finding, and abstain in the middle band.
    KNOWLEDGE_GRAPH = {"Wikipedia", "Wikidata"}
    PROFESSIONAL = {"LinkedIn", "Crunchbase", "Companies House", "OpenCorporates"}
    REVIEW = {"G2", "Capterra", "Trustpilot", "Glassdoor", "Product Hunt"}
    LOCAL = {"Google Business"}
    DEVELOPER = {"GitHub", "App Store", "Google Play"}

    score, breakdown = 0, []

    def award(points, label, condition):
        nonlocal score
        if condition:
            score += points
            breakdown.append(f"{label} +{points}")

    # The site states its own name in machine-readable form -- the anchor everything
    # else attaches to. Worth the most because without it there is nothing to resolve.
    award(25, "declared Organization name", bool(declared_names))
    award(20, "knowledge-graph entry", bool(anchors & KNOWLEDGE_GRAPH))
    award(20, "professional/registry profile", bool(anchors & PROFESSIONAL))
    award(15, "independent review platform", bool(anchors & REVIEW))
    award(15, "verified local listing", bool(anchors & LOCAL))
    award(10, "developer or app-store presence", bool(anchors & DEVELOPER))
    award(10, "social profiles", bool(anchors - KNOWLEDGE_GRAPH - PROFESSIONAL
                                      - REVIEW - LOCAL - DEVELOPER))

    # Name-collision risk is deliberately NOT guessed here. It is tempting to keep a
    # list of dictionary-word brand names and dock points for a match, but such a list
    # can only ever contain names its author thought of, so it fails on exactly the
    # unseen sites that matter and amounts to memorising examples rather than detecting
    # a pattern. Collision is measurable instead: ENTITY-008 reads how many distinct
    # real-world entities actually surface for the name (probe P3) and reports what was
    # observed. Where those probes did not run, this score says so rather than guessing.
    score = max(0, min(100, score))
    if not os.path.exists(os.path.join(os.path.abspath(args.workspace),
                                       "probe_results.json")):
        breakdown.append("name-collision risk not assessed (off-site probes not run)")
    detail = f"EntityScore {score}/100 [{', '.join(breakdown) or 'no signals'}]"

    if score < 40:
        find.add("ENTITY-001", "The brand is weakly resolvable as a real-world entity", "high",
                 f"{detail}. Anchors found across {total} crawled pages: "
                 f"{', '.join(sorted(anchors)) or 'none'}. An entity is resolved by "
                 "triangulation -- a system matches the site against known profiles to decide "
                 "which real-world organisation it is. Below 40 the evidence is too thin for that "
                 "to succeed, so the brand exists mainly as a domain name and is easily confused "
                 "with similarly-named entities.",
                 "Claim and link the profiles that act as identity anchors, and reference them in sameAs.",
                 ["Claim the profiles that matter for your category: LinkedIn and Crunchbase for "
                  "B2B, a Google Business Profile for anything with a physical location, G2 or "
                  "Capterra for software, Trustpilot for consumer.",
                  "Link them from the site footer and list all of them in Organization.sameAs.",
                  "Use the identical brand name and one-sentence description on every profile.",
                  "Ensure each profile links back to the canonical domain -- the claim has to be "
                  "verifiable in both directions.",
                  "For a company with genuine third-party coverage, pursue a Wikidata item: it is "
                  "the identity graph many systems resolve against, and it is editable by anyone "
                  "with sources."],
                 effort="medium", owner="marketing",
                 metrics={"entity_score": score, "breakdown": breakdown,
                          "anchors_found": sorted(anchors)})
    elif score < 60:
        find.add("ENTITY-001", "Entity resolution rests on a narrow set of signals", "low",
                 f"{detail}. Anchors: {', '.join(sorted(anchors)) or 'none'}. There is enough "
                 "evidence for a system to identify the brand, but little redundancy -- if the one "
                 "corroborating profile is stale or a namesake outranks it, resolution degrades. "
                 "Reported at low severity because this is a resilience gap, not a present defect.",
                 "Add one or two anchors from a category you do not yet cover.",
                 ["Identify which signal class is missing (knowledge graph, professional registry, "
                  "review platform, local listing) and claim the most relevant one.",
                  "Keep the name and description byte-identical to the site's own.",
                  "Re-check in a quarter; anchors decay when profiles go unmaintained."],
                 effort="low", owner="marketing", confidence="medium",
                 metrics={"entity_score": score, "breakdown": breakdown,
                          "anchors_found": sorted(anchors)})
    else:
        find.note(f"Entity resolution looks sound. {detail}. "
                  f"Anchors: {', '.join(sorted(anchors))}.")

    # -- ENTITY-002: description consistency across the site's own surfaces -----
    descriptions = {}
    for page in docs:
        view, _ = best_view(page)
        meta_desc = (view.get("meta", {}).get("description") or "").strip()
        og_desc = (view.get("og", {}).get("og:description") or "").strip()
        if meta_desc:
            descriptions.setdefault("meta description", set()).add(meta_desc[:200])
        if og_desc:
            descriptions.setdefault("og:description", set()).add(og_desc[:200])
    schema_desc = {str(n.get("description"))[:200] for n in org_nodes
                   if isinstance(n.get("description"), str) and n.get("description").strip()}
    home = next((p for p in docs if p["url"] == manifest.get("start_url")), docs[0])
    home_view, _ = best_view(home)
    home_meta = (home_view.get("meta", {}).get("description") or "").strip()
    home_og = (home_view.get("og", {}).get("og:description") or "").strip()

    variants = [d for d in {home_meta, home_og, *(schema_desc or set())} if d]
    if len(variants) >= 2:
        pairs = [(a, b, jaccard(norm_desc(a), norm_desc(b)))
                 for i, a in enumerate(variants) for b in variants[i + 1:]]
        worst = min(pairs, key=lambda p: p[2]) if pairs else None
        if worst and worst[2] < 0.35:
            find.add("ENTITY-002", "The brand describes itself differently on each surface", "medium",
                     f"The homepage's own self-descriptions overlap by only "
                     f"{int(worst[2] * 100)}% in wording. Variant A (meta/og): "
                     f"\"{worst[0][:140]}\". Variant B (structured data or the other tag): "
                     f"\"{worst[1][:140]}\". Machines gain confidence from repetition: when the same "
                     "entity is described in materially different terms on every surface, no single "
                     "description accumulates enough support to be repeated back confidently.",
                     "Write one canonical boilerplate description and use it verbatim everywhere.",
                     ["Agree a single 1-2 sentence description naming the category and the audience.",
                      "Use it verbatim in the meta description, og:description, "
                      "Organization.description and the About page's opening line.",
                      "Use the same text on LinkedIn, Crunchbase, app stores and press releases.",
                      "Vary marketing copy freely elsewhere -- but keep the definitional sentence fixed."],
                     effort="low", owner="marketing")

    # -- ENTITY-003: NAP consistency -------------------------------------------
    phones, emails = set(), set()
    for page in docs:
        view, _ = best_view(page)
        text = body_text(view)
        for match in PHONE.finditer(text):
            digits = re.sub(r"\D", "", match.group(0))
            if 7 <= len(digits) <= 15:
                phones.add(digits)
        emails |= set(view.get("emails", []))
    if len(phones) > 3:
        find.add("ENTITY-003", "Multiple inconsistent phone numbers appear across the site", "low",
                 f"{len(phones)} distinct phone numbers were found across {total} crawled pages. "
                 "Inconsistent name/address/phone data across a site and its off-site profiles is a "
                 "primary cause of entity fragmentation: local and knowledge systems treat the "
                 "variants as evidence of different organisations.",
                 "Standardise contact details to one canonical set, in one format, everywhere.",
                 ["Choose one primary phone number and write it in E.164 format everywhere.",
                  "Mark it up once in Organization/LocalBusiness structured data.",
                  "Update every off-site profile to match exactly, including formatting.",
                  "Route department-specific numbers through a clearly-labelled contact page."],
                 effort="low", owner="marketing")

    # -- ENTITY-004: authorship / expertise signals ----------------------------
    articles = [p for p in docs if p.get("page_type") == "article"]
    if articles:
        with_author = 0
        for page in articles:
            view, _ = best_view(page)
            has_schema_author = any(n.get("author") for n in flatten(view.get("jsonld", [])))
            if has_schema_author or AUTHOR_HINT.search(body_text(view)[:4000]):
                with_author += 1
        if with_author / len(articles) < 0.5:
            find.add("ENTITY-004", "Published content is anonymous", "medium",
                     f"{len(articles) - with_author}/{len(articles)} article pages name no author in "
                     "either visible text or structured data. Attribution to a named, credentialed "
                     "person is a core trust signal: anonymous content is weighted below equivalent "
                     "attributed content, particularly on health, finance, legal and safety topics.",
                     "Attribute every article to a named author with a real, linked biography.",
                     ["Add a visible byline plus an author bio block stating relevant credentials.",
                      "Create a Person page per author and link it from every byline.",
                      "Add author (as a Person with name, url and jobTitle) to Article JSON-LD.",
                      "Where content is reviewed by an expert, credit the reviewer separately."],
                     effort="medium", owner="content",
                     affected_urls=[p["url"] for p in articles[:20]])

    # -- ENTITY-005: unsupported superlative claims -----------------------------
    claims = []
    for page in docs:
        view, _ = best_view(page)
        text = body_text(view)
        for match in SINGLE_SOURCE_CLAIM.finditer(text):
            start = max(0, match.start() - 60)
            snippet = text[start:match.end() + 60].strip()
            near_link = any(l.get("abs") and parse.urlparse(l["abs"]).netloc
                            and root not in l["abs"] for l in view.get("links", []))
            claims.append({"url": page["url"], "claim": match.group(0),
                           "context": snippet[:160], "has_external_links": near_link})
    unsupported = [c for c in claims if not c["has_external_links"]]
    if len(unsupported) >= 2:
        sample = "; ".join(f'"{c["claim"]}" on {c["url"]}' for c in unsupported[:3])
        find.add("ENTITY-005", "Authority claims are made with no citable source", "medium",
                 f"{len(unsupported)} superlative or quantified claim(s) appear on pages that link to "
                 f"no external source. Examples: {sample}. A claim that exists only on the claimant's "
                 "own domain is single-sourced: cautious answer generators either omit it or attribute "
                 "it explicitly as the company's own marketing, which weakens rather than strengthens "
                 "the brand.",
                 "Attach a verifiable, independent source to every authority claim.",
                 ["Link each award, certification and ranking to the issuing body's own page.",
                  "Attribute customer counts and market claims to a dated, named source.",
                  "Replace unverifiable superlatives ('industry-leading') with specific, checkable "
                  "facts ('used by 4,200 clinics across 12 countries as of March 2026').",
                  "Publish the underlying evidence (case studies, methodology, audit reports) so "
                  "third parties can cite it -- that is how a claim escapes your own domain."],
                 effort="medium", owner="marketing",
                 affected_urls=sorted({c["url"] for c in unsupported})[:20])

    # -- probe plan: the exact off-site work the agent must perform ----------
    probe_plan = {
        "brand_name": brand_name,
        "site": host,
        "site_root": root,
        "declared_names": sorted(set(declared_names)),
        "known_anchors": sorted(anchors),
        "canonical_description": (home_meta or home_og or next(iter(schema_desc), ""))[:300],
        "instructions": (
            "Run every query below with a web-search tool. Record results verbatim -- do not "
            "summarise, infer or fill gaps from prior knowledge. Write the result to "
            "probe_results.json in this workspace, then re-run this script with --phase merge."),
        "probes": [
            {"id": "P1", "query": f"what is {brand_name}",
             "purpose": "Does the brand's own domain surface for its own defining question, and does "
                        "the description that comes back match the brand's own positioning?",
             "record": ["top_domains", "own_domain_present", "returned_description"]},
            {"id": "P2", "query": f"{brand_name} reviews",
             "purpose": "Is there independent third-party discussion, and on which platforms?",
             "record": ["top_domains", "independent_sources_count"]},
            {"id": "P3", "query": f"\"{brand_name}\"",
             "purpose": "Entity ambiguity: how many distinct real-world entities share this name, and "
                        "which one dominates the results?",
             "record": ["top_domains", "distinct_entities_observed", "dominant_entity"]},
            {"id": "P4", "query": f"{brand_name} pricing",
             "purpose": "When a user asks what it costs, does the answer come from the brand's own "
                        "page or from a third party (possibly with wrong figures)?",
             "record": ["top_domains", "own_domain_present"]},
            {"id": "P5", "query": f"{brand_name} alternatives",
             "purpose": "Which competitors own the comparison conversation, and does the brand appear "
                        "in its own category at all?",
             "record": ["top_domains", "own_domain_present", "competitors_named"]},
            {"id": "P6", "query": f"site:wikipedia.org OR site:wikidata.org {brand_name}",
             "purpose": "Is the entity present in the knowledge graphs many systems resolve against?",
             "record": ["own_entity_present", "entity_url"]},
        ],
        "result_schema": {
            "probes": [{"id": "P1", "query": "...", "top_domains": ["example.com"],
                        "own_domain_present": True, "returned_description": "...",
                        "distinct_entities_observed": 1, "dominant_entity": "...",
                        "independent_sources_count": 0, "competitors_named": [],
                        "own_entity_present": False, "entity_url": None,
                        "notes": "verbatim observations only"}]},
    }
    with open(os.path.join(os.path.abspath(args.workspace), "probe_plan.json"), "w",
              encoding="utf-8") as fh:
        json.dump(probe_plan, fh, ensure_ascii=False, indent=2)
    find.note(f"Probe plan written for brand '{brand_name}' ({len(probe_plan['probes'])} queries). "
              "Off-site corroboration findings require --phase merge with probe_results.json.")
    return brand_name


# --------------------------------------------------------------------- phase 2

def phase_merge(args, manifest, find, brand_name):
    path = os.path.join(os.path.abspath(args.workspace), "probe_results.json")
    if not os.path.exists(path):
        find.note("No probe_results.json found: off-site corroboration was NOT verified in this run. "
                  "ENTITY-006 through ENTITY-009 are unevaluated, not passing.")
        return
    with open(path, encoding="utf-8") as fh:
        results = json.load(fh)
    probes = {p.get("id"): p for p in results.get("probes", [])}
    root = manifest.get("site_root", "")

    def domains(probe):
        return [d.lower() for d in (probe.get("top_domains") or [])]

    # -- ENTITY-006: the brand does not surface for its own defining question ---
    p1 = probes.get("P1")
    if p1:
        own_present = p1.get("own_domain_present")
        if own_present is False:
            find.add("ENTITY-006", "The brand's own site does not surface for its own defining question",
                     "critical",
                     f"Probe P1 (\"{p1.get('query')}\") returned {len(domains(p1))} source "
                     f"domain(s), 0 of which were the brand's own: "
                     f"{', '.join(domains(p1)[:6]) or '(none recorded)'} -- {root} was not among "
                     f"them. When an assistant answers 'what is "
                     f"{brand_name}?', it is grounding that answer entirely in third-party pages, so "
                     "the brand has no control over its own definition and inherits whatever those "
                     "pages happen to say.",
                     "Make one page the unambiguous canonical answer to 'what is <brand>', and get it "
                     "indexed.",
                     ["Publish a definitive About/overview page whose title and H1 are literally "
                      f"'What is {brand_name}?' or '{brand_name}: <category> for <audience>'.",
                      "Open it with the canonical definitional sentence, then the key facts "
                      "(founded, location, what it sells, who for).",
                      "Add Organization JSON-LD with a stable @id and full sameAs list.",
                      "Verify the page is indexed (Search Console URL inspection, Bing Webmaster "
                      "Tools) and internally linked from the homepage.",
                      "Update the third-party profiles that are currently answering this question so "
                      "they at least say the right thing while you build up your own."],
                     effort="medium", owner="marketing", pillar="trust",
                     evidence_source="probe P1")
        returned = (p1.get("returned_description") or "").strip()
        canonical = ""
        plan_path = os.path.join(os.path.abspath(args.workspace), "probe_plan.json")
        if os.path.exists(plan_path):
            with open(plan_path, encoding="utf-8") as fh:
                canonical = (json.load(fh).get("canonical_description") or "").strip()
        if returned and canonical and jaccard(norm_desc(returned), norm_desc(canonical)) < 0.25:
            find.add("ENTITY-007", "The web describes the brand differently than the brand describes itself",
                     "high",
                     f"Probe P1 returned this description: \"{returned[:200]}\". The site's own "
                     f"canonical description reads: \"{canonical[:200]}\". Token overlap between "
                     f"the two is {int(jaccard(norm_desc(returned), norm_desc(canonical)) * 100)}%. "
                     "This is misrepresentation rather than invisibility: assistants are "
                     "confidently repeating a framing the brand did not choose and may not endorse.",
                     "Flood the corroboration channels with the correct description until it dominates.",
                     ["Fix the definitional sentence on the site first (see the extractability findings).",
                      "Update every profile you control -- LinkedIn, Crunchbase, G2, app stores, "
                      "social bios -- to the identical wording.",
                      "Contact the highest-ranking third-party pages carrying the wrong description "
                      "and request a correction, supplying the canonical text.",
                      "Publish new, well-linked content that states the correct positioning "
                      "explicitly, so the correct version accumulates independent support.",
                      "Re-run this probe in 4-8 weeks to confirm the framing has shifted."],
                     effort="high", owner="marketing", pillar="trust",
                     evidence_source="probe P1")

    # -- ENTITY-008: name collision --------------------------------------------
    p3 = probes.get("P3")
    if p3:
        distinct = p3.get("distinct_entities_observed")
        dominant = (p3.get("dominant_entity") or "").strip()
        if isinstance(distinct, int) and distinct >= 2:
            find.add("ENTITY-008", "The brand name collides with other entities in search results",
                     "high" if not p3.get("own_domain_present") else "medium",
                     f"Probe P3 (exact-match \"{brand_name}\") surfaced {distinct} distinct real-world "
                     f"entities sharing the name; the dominant one is "
                     f"'{dominant or 'not recorded'}'. Sources returned: "
                     f"{', '.join(domains(p3)[:6])}. When several entities share a name, a system "
                     "needs a distinguishing signal to pick the right one -- without it, answers "
                     "about this brand get contaminated with facts about its namesakes.",
                     "Publish explicit disambiguating signals that tie the name to this specific entity.",
                     ["Always pair the brand name with its category in titles and headings "
                      f"('{brand_name} <category>'), especially on the homepage and About page.",
                      "Add Organization.alternateName, foundingDate, location and industry to "
                      "structured data -- these are the fields disambiguation actually uses.",
                      "Build the sameAs anchor set: profiles are the strongest disambiguators there are.",
                      "Where the collision is with a much larger entity, consider consistently using "
                      "a qualified form of the name in public material.",
                      "Pursue a Wikidata item with distinguishing properties, since it is what many "
                      "systems consult to separate namesakes."],
                     effort="medium", owner="marketing", pillar="trust",
                     evidence_source="probe P3")

    # -- ENTITY-009: no independent corroboration ------------------------------
    p2 = probes.get("P2")
    if p2:
        independent = p2.get("independent_sources_count")
        third_party = [d for d in domains(p2) if root and root not in d]
        if (isinstance(independent, int) and independent == 0) or \
                (independent is None and not third_party):
            find.add("ENTITY-009", "No independent sources discuss the brand", "high",
                     f"Probe P2 (\"{p2.get('query')}\") surfaced 0 independent third-party sources "
                     f"out of {len(domains(p2))} domain(s) returned "
                     f"({', '.join(domains(p2)[:6]) or 'none'}). Machines treat a "
                     "fact as trustworthy in proportion to how many unrelated places repeat it. A "
                     "brand discussed nowhere but its own domain has a single point of failure for "
                     "every claim it makes, and nothing to corroborate it against.",
                     "Build independent coverage deliberately -- this is the slowest fix and the "
                     "highest-leverage one.",
                     ["Get listed and reviewed on the platforms your category is evaluated on "
                      "(G2/Capterra for software, Trustpilot for consumer, industry directories).",
                      "Publish original data, research or tooling that others have a reason to cite; "
                      "citation-worthiness is earned with something worth citing.",
                      "Pursue coverage in trade publications and podcasts your buyers actually read.",
                      "Encourage customers to publish case studies on their own domains.",
                      "Ensure every mention uses the exact brand name and links the canonical domain, "
                      "so the coverage accrues to the right entity."],
                     effort="high", owner="marketing", pillar="trust",
                     evidence_source="probe P2")

    # -- ENTITY-010: knowledge-graph absence -----------------------------------
    p6 = probes.get("P6")
    if p6 and p6.get("own_entity_present") is False:
        find.add("ENTITY-010", "The brand has no entry in the public knowledge graphs", "low",
                 f"Probe P6 searched Wikipedia and Wikidata and found 0 entity pages for "
                 f"'{brand_name}'. Not a defect "
                 "for most organisations -- notability requirements are real and self-created entries "
                 "are removed -- but these graphs are what several systems resolve entities against, "
                 "so absence caps how confidently any system can identify the brand.",
                 "Where notability genuinely exists, pursue a sourced Wikidata item; otherwise "
                 "strengthen the substitutes.",
                 ["Assess honestly whether independent, substantial coverage exists -- if not, build "
                  "that first (see ENTITY-009) rather than attempting an entry that will be deleted.",
                  "A Wikidata item has a far lower bar than a Wikipedia article and is directly "
                  "consumed by entity-resolution systems: create one with properties sourced to "
                  "independent references.",
                  "Never write your own Wikipedia article; it is a conflict of interest and "
                  "reliably backfires.",
                  "In the meantime, treat LinkedIn, Crunchbase and Google Business Profile as the "
                  "working substitute anchor set."],
                 effort="high", owner="marketing", pillar="trust", proactive=True,
                 evidence_source="probe P6")

    # -- ENTITY-011: brand absent from its own commercial queries ---------------
    for pid, label, question in (("P4", "pricing", "what does it cost"),
                                 ("P5", "alternatives", "what are the alternatives")):
        probe = probes.get(pid)
        if probe and probe.get("own_domain_present") is False:
            competitors = probe.get("competitors_named") or []
            find.add(f"ENTITY-011-{label}",
                     f"Third parties, not the brand, answer '{label}' questions about it",
                     "high",
                     f"Probe {pid} (\"{probe.get('query')}\") returned {len(domains(probe))} "
                     f"source domain(s), 0 of them the brand's own: "
                     f"{', '.join(domains(probe)[:6]) or 'no sources'}"
                     + (f"; competitors named: {', '.join(competitors[:5])}" if competitors else "")
                     + f". When a user asks {question}, the answer is assembled from pages the brand "
                     "does not control -- which routinely means outdated figures and a framing chosen "
                     "by a competitor.",
                     f"Own the {label} question with a definitive, indexable page of your own.",
                     [f"Publish a dedicated {label} page with the facts stated plainly in text.",
                      "Answer the question in the first 100 words -- that passage is what gets quoted.",
                      "Add the matching structured data (Offer/AggregateOffer for pricing).",
                      "For comparison queries, publish honest comparison pages naming the real "
                      "alternatives; refusing to engage cedes the whole conversation.",
                      "Link the page prominently from the homepage and primary navigation."],
                     effort="medium", owner="marketing", pillar="trust",
                     evidence_source=f"probe {pid}")


def main():
    ap = argparse.ArgumentParser(description="Entity identity and cross-web corroboration analyzer")
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--phase", choices=["onsite", "merge", "both"], default="both")
    args = ap.parse_args()

    manifest, pages = load_bundle(args.workspace)
    docs = html_pages(pages)
    find = Findings("entity-corroboration-audit", "trust")
    for cid in [f"ENTITY-{n:03d}" for n in range(1, 12)]:
        find.check(cid)

    if not docs:
        find.note("No successfully-fetched HTML pages in the bundle; identity checks skipped.")
        find.write(args.workspace, 0)
        print("entity-corroboration-audit: no analysable pages")
        return

    brand_name = manifest.get("site_root", "").split(".")[0]
    if args.phase in ("onsite", "both"):
        brand_name = phase_onsite(args, manifest, docs, find)
    if args.phase in ("merge", "both"):
        # In merge-only mode, re-emit the on-site findings too so the file stays complete.
        if args.phase == "merge":
            brand_name = phase_onsite(args, manifest, docs, find)
        phase_merge(args, manifest, find, brand_name)

    find.write(args.workspace, len(docs))
    print(f"entity-corroboration-audit: {len(find.items)} finding(s); brand='{brand_name}'; "
          f"phase={args.phase}")


if __name__ == "__main__":
    main()
