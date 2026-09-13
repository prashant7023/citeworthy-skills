#!/usr/bin/env python3
"""
probe_schema_truth.py -- MARK-* checks: is the fact machine-readable AND consistent?

Stage 3a. Structured data is the only channel where a site states a fact in a
form a machine cannot misread. This skill checks three separate things, which
are commonly conflated:

  1. presence   -- is there any markup at all, and of the right type for the page
  2. validity   -- does it parse, and does it carry the properties consumers need
  3. agreement  -- does it match what the page visibly says

(3) is the one most audits skip and the one that does real damage: markup that
contradicts the visible page teaches an assistant a wrong fact with high
confidence, which is worse than no markup at all.

Pure analyzer: reads the evidence bundle, performs no network I/O.
"""
import argparse
import json
import re
from urllib import parse

from evidence import load_bundle, html_pages, best_view, escalate, Findings

# Page type -> (expected schema types, why it matters for an assistant answer)
EXPECTED = {
    "homepage": (["Organization", "LocalBusiness", "Corporation", "NGO", "WebSite",
                  "OnlineStore", "EducationalOrganization", "GovernmentOrganization"],
                 "identifies who the brand is, so answers can attribute facts to the right entity"),
    "product": (["Product", "ProductGroup", "SoftwareApplication", "Vehicle", "Book", "Course"],
                "supplies name, price, availability and rating for product answers"),
    "pricing": (["Product", "Offer", "AggregateOffer", "Service", "SoftwareApplication",
                 "PriceSpecification"],
                "lets an assistant quote an exact, current price with a currency"),
    "article": (["Article", "BlogPosting", "NewsArticle", "TechArticle", "Report"],
                "supplies author, publish date and headline, which drive freshness and trust"),
    "faq": (["FAQPage", "QAPage"],
            "pairs each question with its answer explicitly (rich results for FAQ markup are now "
            "limited to a few site categories, so the value is machine clarity, not a SERP feature)"),
    "contact": (["Organization", "LocalBusiness", "ContactPage", "Place", "PostalAddress"],
                "supplies address, phone and hours for 'how do I reach them' answers"),
    "about": (["Organization", "AboutPage", "Corporation", "LocalBusiness"],
              "anchors the entity description assistants repeat back"),
    "careers": (["JobPosting"], "makes roles eligible for job-answer surfaces"),
    "local": (["LocalBusiness", "Restaurant", "Store", "Place"],
              "supplies hours, address and geo for local answers"),
    "docs": (["TechArticle", "HowTo", "APIReference", "Article"],
             "marks procedural content so steps can be quoted in order"),
}

# type -> (required properties, recommended properties)
PROPERTY_RULES = {
    "Organization": (["name", "url"], ["logo", "description", "sameAs", "address", "contactPoint", "@id"]),
    "LocalBusiness": (["name", "address"], ["telephone", "openingHours", "openingHoursSpecification",
                                            "geo", "priceRange", "url", "sameAs"]),
    "Product": (["name"], ["description", "image", "offers", "brand", "sku", "aggregateRating", "review"]),
    "Offer": (["price", "priceCurrency"], ["availability", "url", "priceValidUntil", "itemCondition"]),
    "Article": (["headline"], ["author", "datePublished", "dateModified", "image", "publisher"]),
    "BlogPosting": (["headline"], ["author", "datePublished", "dateModified", "image", "publisher"]),
    "NewsArticle": (["headline"], ["author", "datePublished", "dateModified", "image", "publisher"]),
    "FAQPage": (["mainEntity"], []),
    "JobPosting": (["title", "datePosted", "hiringOrganization"], ["jobLocation", "baseSalary",
                                                                   "employmentType", "validThrough"]),
    "WebSite": (["name", "url"], ["potentialAction", "publisher"]),
    "Event": (["name", "startDate", "location"], ["endDate", "offers", "organizer", "eventStatus"]),
    "SoftwareApplication": (["name"], ["offers", "applicationCategory", "operatingSystem",
                                       "aggregateRating"]),
    "BreadcrumbList": (["itemListElement"], []),
}

PRICE_NUM = re.compile(r"[\d][\d,]*(?:\.\d+)?")
AUTHORITATIVE_SAMEAS = re.compile(
    r"(wikipedia\.org|wikidata\.org|linkedin\.com|crunchbase\.com|github\.com|"
    r"apps\.apple\.com|play\.google\.com|x\.com|twitter\.com|facebook\.com|"
    r"instagram\.com|youtube\.com|g\.page|maps\.google)", re.I)


def flatten(node, out=None):
    """Yield every schema.org node in a JSON-LD tree, including @graph members."""
    if out is None:
        out = []
    if isinstance(node, list):
        for item in node:
            flatten(item, out)
    elif isinstance(node, dict):
        if "@graph" in node:
            flatten(node["@graph"], out)
        if any(k in node for k in ("@type", "@id")) or "type" in node:
            out.append(node)
        for value in node.values():
            if isinstance(value, (dict, list)):
                flatten(value, out)
    return out


def types_of(node):
    raw = node.get("@type") or node.get("type") or []
    if isinstance(raw, str):
        raw = [raw]
    return [str(t).split("/")[-1].split("#")[-1] for t in raw if t]


def prop(node, name):
    return node.get(name) if isinstance(node, dict) else None


def normalise_label(text):
    """Compare labels the way a human would judge 'same title'.

    Titles routinely carry a site-name suffix ("Page - Brand", "Page | Brand")
    that the H1 omits. Treating that as a contradiction is the single most common
    false positive in schema-drift checking, so the suffix and punctuation are
    stripped before comparison.
    """
    text = re.split(r"\s+[|–—-]\s+", (text or "").strip())[0]
    return re.sub(r"[^a-z0-9 ]+", " ", text.lower()).strip()


def as_text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return str(value.get("name") or value.get("@value") or value.get("url") or "")
    if isinstance(value, list) and value:
        return as_text(value[0])
    return "" if value is None else str(value)


def main():
    ap = argparse.ArgumentParser(description="Structured-data presence, validity and agreement analyzer")
    ap.add_argument("--workspace", required=True)
    args = ap.parse_args()

    manifest, pages = load_bundle(args.workspace)
    docs = html_pages(pages)
    find = Findings("structured-data-audit", "extract")
    for cid in [f"MARK-{n:03d}" for n in range(1, 13)]:
        find.check(cid)

    if not docs:
        find.note("No successfully-fetched HTML pages in the bundle; structured-data checks skipped.")
        find.write(args.workspace, 0)
        print("structured-data-audit: no analysable pages")
        return

    total = len(docs)
    start_url = manifest.get("start_url")

    # Index every node once.
    per_page, all_nodes = {}, []
    for page in docs:
        view, _ = best_view(page)
        nodes = flatten(view.get("jsonld", []))
        for node in nodes:
            node["_url"] = page["url"]
        per_page[page["url"]] = {"page": page, "view": view, "nodes": nodes,
                                 "types": {t for n in nodes for t in types_of(n)},
                                 "errors": view.get("jsonld_errors", []),
                                 "microdata": view.get("microdata_types", [])}
        all_nodes += nodes

    pages_with_any = [u for u, d in per_page.items()
                      if d["nodes"] or d["microdata"] or d["view"].get("rdfa_types")]

    # -- MARK-001: nothing at all ---------------------------------------------
    if not pages_with_any:
        find.add("MARK-001", "No structured data on any crawled page", "high",
                 f"0/{total} crawled pages contain JSON-LD, microdata or RDFa. Structured data states "
                 "facts without interpretation (who the company is, what a product costs, when an "
                 "article was written) and is how search systems tell this brand apart from a "
                 "namesake. Without it, every fact has to be inferred from prose, and the site "
                 "forgoes price, rating and breadcrumb search features.",
                 "Add schema.org JSON-LD, starting with Organization on the homepage and the "
                 "page-type-appropriate entity everywhere else.",
                 ["Add an Organization (or LocalBusiness) JSON-LD block to the homepage with name, url, "
                  "logo, description and sameAs links to your official profiles.",
                  "Add WebSite markup with a stable @id so all other entities can reference it.",
                  "Add the page-type entity to each template: Product+Offer on products, "
                  "Article on posts, FAQPage on FAQs, BreadcrumbList on every nested page.",
                  "Emit the JSON-LD server-side and validate with Google's Rich Results Test and "
                  "the schema.org validator before shipping."],
                 effort="medium", owner="web/dev",
                 metrics={"pages_with_markup": 0, "pages_checked": total})
    else:
        coverage = len(pages_with_any) / total
        if coverage < 0.5:
            missing = [u for u in per_page if u not in pages_with_any]
            find.add("MARK-002", "Structured data covers only a minority of pages", "medium",
                     f"{len(pages_with_any)}/{total} crawled pages carry structured data "
                     f"({int(coverage * 100)}%). Pages without it: {', '.join(missing[:3])}. Coverage "
                     "gaps mean the unmarked pages compete on prose alone while the marked ones do not.",
                     "Extend structured data to every page template, not just the homepage.",
                     ["Identify which templates emit markup and which do not.",
                      "Add the appropriate entity type to each remaining template.",
                      "Make markup part of the base layout so new pages inherit it automatically."],
                     effort="medium", owner="web/dev", affected_urls=missing[:20])

    # -- MARK-003: invalid JSON-LD --------------------------------------------
    broken = [(u, d["errors"]) for u, d in per_page.items() if d["errors"]]
    if broken:
        url, errs = broken[0]
        find.add("MARK-003", "JSON-LD blocks fail to parse", "high",
                 f"{len(broken)}/{total} pages contain a <script type=\"application/ld+json\"> block "
                 f"that is not valid JSON. Example: {url} -> {errs[0]['error']}; near "
                 f"'{(errs[0].get('snippet') or '')[:100]}'. A block that does not parse is discarded "
                 "in full, so every fact inside it is silently lost -- the markup looks present in the "
                 "page source but delivers nothing.",
                 "Fix the JSON syntax and validate every block in CI.",
                 ["Repair the flagged blocks (usual causes: unescaped quotes in a description, "
                  "trailing commas, raw newlines in strings, or a templating variable left unrendered).",
                  "Generate JSON-LD by serialising a real object (json.dumps / JSON.stringify) rather "
                  "than by string concatenation in a template.",
                  "Add a build-time test that JSON.parse succeeds on every ld+json block.",
                  "Re-validate with the schema.org validator after the fix."],
                 effort="low", owner="web/dev",
                 affected_urls=[u for u, _ in broken[:20]])

    # -- MARK-004: no Organization identity anchor ----------------------------
    org_nodes = [n for n in all_nodes if {"Organization", "LocalBusiness", "Corporation", "NGO",
                                          "OnlineStore", "EducationalOrganization",
                                          "GovernmentOrganization", "Restaurant", "Store"}
                 & set(types_of(n))]
    if not org_nodes and pages_with_any:
        find.add("MARK-004", "No Organization entity defines who the brand is", "high",
                 f"None of the {len(all_nodes)} structured-data nodes across {total} crawled pages "
                 "declares an Organization (or LocalBusiness) type. Assistants resolve a brand to an "
                 "entity before they answer about it; with no declared entity, the site provides "
                 "nothing to attach its facts to and is more easily confused with a similarly-named "
                 "organisation.",
                 "Publish one canonical Organization entity with a stable @id, referenced from every page.",
                 ["Add Organization JSON-LD on the homepage: name, alternateName, url, logo, "
                  "description, foundingDate, and sameAs.",
                  "Give it a stable @id (e.g. https://example.com/#organization) and reference that "
                  "@id from Article.publisher, Product.brand and WebSite.publisher on other pages.",
                  "Keep the `name` and `description` byte-identical to what you use on your "
                  "LinkedIn, Crunchbase and social profiles."],
                 effort="low", owner="web/dev")

    # -- MARK-005: sameAs identity anchors ------------------------------------
    if org_nodes:
        same_as = []
        for node in org_nodes:
            value = prop(node, "sameAs")
            if isinstance(value, str):
                same_as.append(value)
            elif isinstance(value, list):
                same_as += [as_text(v) for v in value]
        authoritative = [s for s in same_as if AUTHORITATIVE_SAMEAS.search(s or "")]
        if not authoritative:
            find.add("MARK-005", "Organization markup has no sameAs links to authoritative profiles",
                     "medium",
                     f"The Organization entity on {org_nodes[0].get('_url')} declares "
                     f"{len(same_as)} sameAs value(s), none pointing to a recognised identity "
                     "authority (Wikipedia, Wikidata, LinkedIn, Crunchbase, GitHub, app stores or "
                     "official social profiles). sameAs is how a site tells a machine 'this entity "
                     "and that well-known profile are the same thing' -- without it, the brand is an "
                     "island and is easily merged with a namesake.",
                     "Add sameAs links to every official profile you control, most authoritative first.",
                     ["List your LinkedIn company page, Crunchbase, Wikidata/Wikipedia entry (if any), "
                      "GitHub org, app-store listings and official social accounts in sameAs.",
                      "Use the exact canonical profile URLs -- no redirects, no tracking parameters.",
                      "Make sure each linked profile links back to the site and uses the same name "
                      "and description, so the identity claim is corroborated in both directions."],
                     effort="low", owner="marketing")

    # -- MARK-006: wrong/missing type for the page type -----------------------
    # When MARK-004 already reported that no Organization exists anywhere, the
    # per-page-type Organization gaps are the same defect restated. Suppress them
    # so one root cause yields one finding rather than three.
    no_markup_at_all = not pages_with_any          # MARK-001 already covers the whole site
    org_covered_by_sda004 = not org_nodes and pages_with_any
    type_gaps = {}
    for url, data in per_page.items():
        if no_markup_at_all:
            break
        ptype = data["page"].get("page_type")
        if ptype not in EXPECTED:
            continue
        expected, why = EXPECTED[ptype]
        if org_covered_by_sda004 and expected[0] in ("Organization",):
            continue
        micro = " ".join(data["microdata"])
        if not (set(expected) & data["types"]) and not any(e in micro for e in expected):
            type_gaps.setdefault(ptype, {"urls": [], "expected": expected, "why": why})
            type_gaps[ptype]["urls"].append(url)

    label = {"homepage": "The homepage carries", "faq": "FAQ pages carry",
             "docs": "Documentation pages carry", "about": "The About page carries",
             "contact": "The contact page carries", "local": "Location pages carry"}
    for ptype, gap in sorted(type_gaps.items(), key=lambda kv: -len(kv[1]["urls"])):
        prevalence = len(gap["urls"]) / total
        homepage_hit = start_url in gap["urls"]
        subject = label.get(ptype, f"{ptype.capitalize()} pages carry")
        find.add(f"MARK-006-{ptype}",
                 f"{subject} no {gap['expected'][0]} markup",
                 # Offer markup on a pricing page is a nice-to-have: search engines show no
                 # price feature for service pricing, so its absence is never above low.
                 "low" if ptype == "pricing" else escalate("medium", prevalence, homepage_hit),
                 f"{len(gap['urls'])} page(s) classified as '{ptype}' declare none of the expected "
                 f"types ({', '.join(gap['expected'][:4])}). Examples: "
                 f"{', '.join(gap['urls'][:3])}. This markup {gap['why']}, so its absence removes the "
                 "most reliable path to those facts.",
                 f"Add {gap['expected'][0]} JSON-LD to the {ptype} template.",
                 [f"Add a {gap['expected'][0]} block to every {ptype} page, populated from the same "
                  "data source that renders the visible page (never hand-written per page).",
                  "Include the required properties for the type and as many recommended ones as you "
                  "genuinely have.",
                  "Validate a sample URL in the Rich Results Test before rolling out.",
                  "Confirm the markup is present in the raw server HTML, not injected by JavaScript."],
                 effort="medium", owner="web/dev", affected_urls=gap["urls"][:20],
                 page_type=ptype)

    # -- MARK-007: missing required / recommended properties ------------------
    required_gaps, recommended_gaps = {}, {}
    for node in all_nodes:
        for tname in types_of(node):
            if tname not in PROPERTY_RULES:
                continue
            required, recommended = PROPERTY_RULES[tname]
            for field in required:
                if not node.get(field):
                    required_gaps.setdefault((tname, field), []).append(node.get("_url"))
            for field in recommended:
                if not node.get(field):
                    recommended_gaps.setdefault((tname, field), []).append(node.get("_url"))

    if required_gaps:
        detail = "; ".join(f"{t}.{f} missing on {len(set(urls))} page(s) (e.g. {sorted(set(urls))[0]})"
                           for (t, f), urls in sorted(required_gaps.items(),
                                                      key=lambda kv: -len(kv[1]))[:5])
        find.add("MARK-007", "Structured data omits properties that consumers require", "medium",
                 f"{len(required_gaps)} required-property gap(s) across the crawled sample: {detail}. "
                 "A node missing its required properties is typically rejected wholesale by the "
                 "consuming parser, so the surrounding valid properties are lost with it.",
                 "Populate the required properties for every declared type.",
                 ["Fill the missing required fields from the same data that renders the page.",
                  "If a value genuinely does not exist, remove the type rather than emitting a "
                  "half-populated node that will be discarded.",
                  "Add a build-time schema validation step so incomplete nodes fail CI."],
                 effort="low", owner="web/dev")

    high_value_recommended = {("Article", "author"), ("Article", "datePublished"),
                              ("BlogPosting", "author"), ("BlogPosting", "datePublished"),
                              ("NewsArticle", "author"), ("NewsArticle", "datePublished"),
                              ("Product", "offers"), ("Offer", "availability"),
                              ("Organization", "description"), ("Organization", "logo"),
                              ("LocalBusiness", "openingHoursSpecification")}
    notable = {k: v for k, v in recommended_gaps.items() if k in high_value_recommended}
    if notable:
        detail = "; ".join(f"{t}.{f} on {len(set(urls))} page(s)"
                           for (t, f), urls in sorted(notable.items(), key=lambda kv: -len(kv[1]))[:5])
        find.add("MARK-008", "High-value optional properties are absent from otherwise-valid markup",
                 "low",
                 f"Valid markup is present but omits properties that assistants lean on heavily: "
                 f"{detail}. These fields drive trust and freshness judgements (who wrote it, when, "
                 "is it in stock, what does the company do).",
                 "Add the author, date, availability and description properties to existing markup.",
                 ["Add author (as a Person with a name and, ideally, a url) and both datePublished "
                  "and dateModified to every Article.",
                  "Add offers.availability and offers.priceValidUntil to every Product.",
                  "Add a one-sentence description and a logo to the Organization entity."],
                 effort="low", owner="web/dev")

    # -- MARK-009: markup contradicts the visible page ------------------------
    drift = []
    for url, data in per_page.items():
        # A category or search page carries Product markup for every tile in the grid,
        # each with its own name and price. Comparing those against the page's own H1
        # reports one "contradiction" per tile when nothing is contradictory at all --
        # the markup describes items within the page, not the page itself. Drift is
        # only meaningful where the page has a single dominant entity.
        if data["page"].get("page_type") in ("listing", "homepage"):
            continue
        view = data["view"]
        visible_text = view.get("text", "")
        visible_prices = set(view.get("prices_in_text", []))
        for node in data["nodes"]:
            tset = set(types_of(node))
            # price drift
            offers = node.get("offers")
            offer_nodes = flatten(offers) if offers else []
            for offer in offer_nodes + ([node] if "Offer" in tset else []):
                price = offer.get("price") or offer.get("lowPrice")
                if price is None:
                    continue
                price_digits = PRICE_NUM.findall(str(price))
                if not price_digits:
                    continue
                needle = price_digits[0].replace(",", "")
                seen = any(needle in p.replace(",", "") for p in visible_prices) or \
                    needle in visible_text.replace(",", "")
                if not seen and visible_prices:
                    drift.append({"url": url, "kind": "price",
                                  "markup": str(price),
                                  "visible": ", ".join(sorted(visible_prices)[:3])})
            # name/headline drift.
            #
            # Drift means the page asserts a DIFFERENT value, not that we failed to
            # locate the value. So this only fires when the page actually presents a
            # competing label (an H1 or a <title>) and the markup matches neither.
            # A page with no H1 is a READ-009 finding, not a contradiction -- reporting
            # it here would double-count one defect as two and name the wrong fix.
            for field in ("name", "headline"):
                value = as_text(node.get(field))
                if not value or len(value) < 6:
                    continue
                if not ({"Article", "BlogPosting", "NewsArticle", "Product"} & tset):
                    continue
                h1s = [h["text"].strip() for h in view.get("headings", [])
                       if h["level"] == 1 and h["text"].strip()]
                title = (view.get("title") or "").strip()
                competing = [c for c in (h1s[:1] + [title]) if c]
                if not competing:
                    continue                    # nothing to contradict; abstain
                needle = normalise_label(value)
                if any(needle in normalise_label(c) or normalise_label(c) in needle
                       for c in competing):
                    continue                    # agrees with the H1 or the title
                if needle[:60] in normalise_label(visible_text):
                    continue                    # appears verbatim in the body copy
                drift.append({"url": url, "kind": field, "markup": value[:80],
                              "visible": competing[0][:80]})
    seen_drift = set()
    drift = [d for d in drift
             if not ((d["url"], d["kind"], d["markup"]) in seen_drift
                     or seen_drift.add((d["url"], d["kind"], d["markup"])))]
    if drift:
        first = drift[0]
        detail = "; ".join(f"{d['url']}: markup {d['kind']}='{d['markup']}' but page shows "
                           f"'{d['visible']}'" for d in drift[:3])
        find.add("MARK-009", "Structured data contradicts the visible page content", "high",
                 f"{len(drift)} disagreement(s) between markup and rendered content. {detail}. "
                 "Markup that disagrees with the page is worse than absent markup: it is stated with "
                 "machine-grade confidence, so an assistant will repeat the wrong number or title "
                 "without hedging, and search engines treat persistent mismatch as a spam signal.",
                 "Generate structured data from the same source of truth that renders the page.",
                 ["Populate JSON-LD from the same server-side model as the template -- never from a "
                  "hard-coded block, a stale CMS field or a separate feed.",
                  f"Reconcile the flagged mismatch on {first['url']} immediately.",
                  "Add a regression test asserting that the markup price/name equals the rendered "
                  "price/name for a sample of pages.",
                  "Re-check after any pricing or title change; drift usually appears at the next "
                  "content edit, not at launch."],
                 effort="medium", owner="web/dev",
                 affected_urls=sorted({d["url"] for d in drift})[:20])

    # -- MARK-010: title / description hygiene --------------------------------
    titles, no_desc, long_desc = {}, [], []
    for url, data in per_page.items():
        view = data["view"]
        title = (view.get("title") or "").strip()
        # URLs that declare one canonical, or differ only by query string, are one document
        # at two addresses; sharing a title is correct for them.
        target = parse.urlparse(view.get("canonical") or data["page"].get("final_url") or url)
        document = (target.netloc.lower(), (target.path or "/").rstrip("/") or "/")
        titles.setdefault(title, {}).setdefault(document, url)
        desc = (view.get("meta", {}).get("description") or "").strip()
        if not desc:
            no_desc.append(url)
        elif len(desc) > 320:
            long_desc.append(url)
    duplicates = {t: list(by_document.values()) for t, by_document in titles.items()
                  if t and len(by_document) > 1}
    if duplicates:
        title, urls = max(duplicates.items(), key=lambda kv: len(kv[1]))
        find.add("MARK-010", "Multiple pages share the same <title>",
                 escalate("medium", len(urls) / total),
                 f"{sum(len(u) for u in duplicates.values())} page(s) across {len(duplicates)} "
                 f"duplicate title group(s). Largest group: '{title[:80]}' used by {len(urls)} URLs "
                 f"({', '.join(urls[:3])}). The title is the primary label for a retrieved document; "
                 "identical titles make distinct pages indistinguishable at retrieval time and "
                 "invite the wrong one to be cited.",
                 "Give every page a unique, specific title describing that page alone.",
                 ["Template titles as `<specific page subject> | <brand>`.",
                  "Include the distinguishing attribute for near-duplicate pages "
                  "(location, model, size, year).",
                  "Audit for remaining duplicates after the change."],
                 effort="low", owner="content", affected_urls=[u for group in duplicates.values()
                                                               for u in group][:20])
    if no_desc and len(no_desc) >= max(2, total * 0.4):
        find.add("MARK-011", "Most pages have no meta description",
                 "low",
                 f"{len(no_desc)}/{total} pages declare no meta description. Examples: "
                 f"{', '.join(no_desc[:3])}. The description is a pre-written, human-authored summary "
                 "of the page -- when it is missing, the summary is improvised from body text, and "
                 "the brand loses control of how the page is framed in an answer.",
                 "Write a distinct 120-160 character meta description for every page.",
                 ["Lead with the specific answer the page provides, not a brand slogan.",
                  "Make each description unique and factual; include the entity name once.",
                  "Mirror the description into og:description so social and chat previews match."],
                 effort="low", owner="content", affected_urls=no_desc[:20])

    # -- MARK-012: breadcrumbs on deep pages ----------------------------------
    deep = [u for u, d in per_page.items() if d["page"].get("depth", 0) >= 2]
    deep_without_crumbs = [u for u in deep if "BreadcrumbList" not in per_page[u]["types"]]
    if deep and len(deep_without_crumbs) >= max(2, len(deep) * 0.7):
        find.add("MARK-012", "Deep pages carry no BreadcrumbList markup", "low",
                 f"{len(deep_without_crumbs)}/{len(deep)} pages two or more clicks from the homepage "
                 f"declare no BreadcrumbList. Examples: {', '.join(deep_without_crumbs[:3])}. "
                 "Breadcrumbs tell a machine where a page sits in the site's hierarchy, which is how a "
                 "deep page inherits the context of its section instead of being read in isolation.",
                 "Add BreadcrumbList JSON-LD (and a visible breadcrumb trail) to nested templates.",
                 ["Emit BreadcrumbList with an ordered itemListElement from the homepage down.",
                  "Render a matching visible breadcrumb -- it also fixes deep-link orientation for "
                  "human visitors arriving from an AI answer.",
                  "Keep breadcrumb URLs canonical and absolute."],
                 effort="low", owner="web/dev", affected_urls=deep_without_crumbs[:20])

    find.write(args.workspace, total)
    print(f"structured-data-audit: {len(find.items)} finding(s) across {total} page(s); "
          f"{len(all_nodes)} schema node(s) indexed")


if __name__ == "__main__":
    main()
