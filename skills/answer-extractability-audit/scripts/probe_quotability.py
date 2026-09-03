#!/usr/bin/env python3
"""
probe_quotability.py -- QUOTE-* checks: can a machine QUOTE a specific fact?

Stage 3b, and the half of extractability that markup cannot fix. A page can be
fully crawlable, fully server-rendered and carry perfect JSON-LD, and still never
be cited -- because nowhere in its prose is there a sentence a machine can lift
and present as an answer.

The mental model is retrieval-by-chunk: a retriever splits the page into passages,
embeds them, and returns one passage. That passage is shown to the user largely on
its own. So a passage must be:

  self-contained  -- names its subject rather than saying "we" / "our platform"
  declarative     -- states the fact, rather than implying it through a slogan
  bounded         -- short enough and headed, so the chunk boundary lands cleanly
  specific        -- carries the number, unit, currency or date the question needs

Every check below measures one of those four properties.

Pure analyzer: reads the evidence bundle, performs no network I/O.
"""
import argparse
import re
from collections import Counter

from evidence import load_bundle, html_pages, best_view, body_text, brand_tokens, escalate, Findings

SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'(])")
# "X is a/an/the ..." -- the canonical definitional shape a machine can lift verbatim.
DEFINITION = re.compile(
    r"\b(?:is|are|was|were)\s+(?:a|an|the)\b|"
    r"\b(?:is|are)\s+(?:the\s+)?(?:leading|world|first|only|largest)\b|"
    r"\b(?:provides?|offers?|builds?|makes?|develops?|delivers?|helps?|enables?|"
    r"specialis[ez]es?\s+in|manufactures?|sells?|designs?)\b", re.I)
VAGUE_HERO = re.compile(
    r"^\W*(?:welcome|hello|hi\b|home\b|unlock|unleash|empower|transform|reimagine|elevate|"
    r"discover|imagine|the future of|beyond|redefin|next[- ]generation|revolutionis|revolutioniz|"
    r"where .{0,30} meets)", re.I)
FIRST_PERSON = re.compile(r"\b(we|our|us|ourselves|my|i)\b", re.I)
ACRONYM = re.compile(r"\b([A-Z]{2,6})s?\b")
NUMERIC_CLAIM = re.compile(
    r"\b(\d{1,3}(?:[.,]\d+)?)\s*(%|percent|x\b|times\b)|"
    r"\b(?:up to|over|more than|less than|save|reduce|increase|faster|cheaper)\s+\d", re.I)
UNIT_CONTEXT = re.compile(
    r"\b(?:than|versus|vs\.?|compared (?:to|with)|baseline|before|previously|per\s+\w+)\b", re.I)
GENERIC_ANCHOR = re.compile(
    r"^\W*(?:click here|here|read more|learn more|more|this|link|see more|find out more|"
    r"continue|details|go|view)\W*$", re.I)
STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "you", "your", "our", "are", "from", "have",
    "has", "was", "were", "will", "can", "all", "any", "but", "not", "who", "how", "what",
    "when", "where", "why", "get", "use", "using", "more", "than", "into", "out", "about",
    "their", "them", "they", "his", "her", "its", "been", "being", "also", "just", "than",
}

# Fact classes an assistant is asked about constantly. Absence of a class is only
# reported when the site is plausibly expected to have it (see gating below).
FACT_PATTERNS = {
    "pricing": (re.compile(r"[$£€¥₹]\s?\d|(?:\d+\s?(?:USD|EUR|GBP|INR))|"
                           r"\bper (?:month|year|user|seat|licen[cs]e)\b|\bfree (?:tier|plan)\b", re.I),
                "what it costs"),
    "contact": (re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}|"
                           r"\+\d[\d\s().\-]{7,}|\bcontact us\b", re.I),
                "how to reach the company"),
    "location": (re.compile(r"\b(?:headquarter(?:s|ed)|based in|located in|our office|"
                            r"\d{1,5}\s+[A-Z][a-z]+\s+(?:Street|St|Road|Rd|Avenue|Ave|Lane|"
                            r"Boulevard|Blvd|Drive|Dr)\b)", re.I),
                 "where the company is"),
    "founding": (re.compile(r"\b(?:founded|established|since|started|launched|incorporated)\s+"
                            r"(?:in\s+)?(?:19|20)\d{2}\b", re.I),
                 "when it was founded"),
    "offering": (re.compile(r"\b(?:we|our company|the company)?\s*(?:provide|offer|specialis|specializ|"
                            r"build|make|sell|design|deliver)\w*\b", re.I),
                 "what it actually sells"),
}


def sentences(text, limit=400):
    return [s.strip() for s in SENTENCE.split(text)[:limit] if len(s.strip()) > 15]


def main():
    ap = argparse.ArgumentParser(description="Answer-extractability (quotability) analyzer")
    ap.add_argument("--workspace", required=True)
    args = ap.parse_args()

    manifest, pages = load_bundle(args.workspace)
    docs = html_pages(pages)
    find = Findings("answer-extractability-audit", "extract")
    for cid in [f"QUOTE-{n:03d}" for n in range(1, 14)]:
        find.check(cid)

    if not docs:
        find.note("No successfully-fetched HTML pages in the bundle; extractability checks skipped.")
        find.write(args.workspace, 0)
        print("answer-extractability-audit: no analysable pages")
        return

    total = len(docs)
    start_url = manifest.get("start_url")
    brand = brand_tokens(manifest)
    brand_label = manifest.get("site_root", manifest.get("site", "the brand")).split(".")[0]

    by_url = {p["url"]: p for p in docs}
    home = by_url.get(start_url) or next(
        (p for p in docs if p.get("page_type") == "homepage"), docs[0])
    home_view, _ = best_view(home)
    home_text = body_text(home_view)
    site_text_all = " ".join(body_text(best_view(p)[0]) for p in docs)

    # -- QUOTE-001: no definitional sentence -----------------------------------
    identity_pages = [p for p in docs if p.get("page_type") in ("homepage", "about")] or [home]
    definitional = []
    for page in identity_pages:
        view, _ = best_view(page)
        text = body_text(view)
        head = " ".join(sentences(text)[:25])
        for sentence in sentences(head):
            lowered = sentence.lower()
            names_brand = any(tok in lowered for tok in brand) or \
                any(h["text"].strip().lower() in lowered
                    for h in view.get("headings", []) if h["level"] == 1 and h["text"].strip())
            if names_brand and DEFINITION.search(sentence) and len(sentence.split()) >= 6:
                definitional.append({"url": page["url"], "sentence": sentence[:220]})
                break
    if not definitional:
        first_h1 = next((h["text"] for h in home_view.get("headings", []) if h["level"] == 1), "")
        find.add("QUOTE-001", "No plain-language sentence states what the brand actually is", "critical",
                 f"Scanned the opening ~25 sentences of {len(identity_pages)} identity page(s) "
                 f"({', '.join(p['url'] for p in identity_pages[:3])}) for a sentence that both names "
                 f"the brand and defines it ('<Brand> is a ...', '<Brand> provides ...'). None was "
                 f"found. The homepage H1 currently reads: '{(first_h1 or '(no H1)')[:120]}'. When a "
                 "user asks 'what is <brand>?', an assistant needs one liftable sentence to quote; "
                 "with none available it either paraphrases loosely, pulls the description from a "
                 "third-party directory, or declines to answer.",
                 "Add one explicit definitional sentence near the top of the homepage and About page.",
                 [f"Write it in the form: '{brand_label.capitalize()} is a <category> that "
                  "<does what> for <who>.' -- name the brand, the category and the audience in one "
                  "sentence.",
                  "Place it in the first 100 words of the homepage, in body text (not only in an "
                  "image, a slogan or the <title>).",
                  "Repeat the same sentence verbatim on the About page, in the meta description and "
                  "in the Organization JSON-LD `description` -- consistency across surfaces is what "
                  "makes a machine confident enough to repeat it.",
                  "Use the same wording on your LinkedIn, Crunchbase and social bios so the claim is "
                  "corroborated off-site as well."],
                 effort="low", owner="content",
                 affected_urls=[p["url"] for p in identity_pages[:5]])
    else:
        find.note(f"Definitional sentence found: \"{definitional[0]['sentence']}\" "
                  f"({definitional[0]['url']})")

    # -- QUOTE-002: hero is a slogan with no concrete category -----------------
    hero = (home_view.get("first_screen_text") or "")[:600]
    hero_h1 = next((h["text"] for h in home_view.get("headings", []) if h["level"] == 1), "")
    if hero_h1 and VAGUE_HERO.search(hero_h1) and not DEFINITION.search(hero[:400]):
        find.add("QUOTE-002", "The homepage opens with a slogan instead of a statement of what this is",
                 "high",
                 f"The homepage H1 is '{hero_h1[:120]}', which matches a generic-slogan pattern, and "
                 f"the first screen of text contains no defining verb ('is a', 'provides', 'builds'). "
                 f"First screen reads: '{hero[:200]}...'. Both a machine building an answer and a "
                 "visitor arriving from one need the category in the first thing they read; an "
                 "aspirational slogan gives neither.",
                 "Rewrite the H1 to name the category and audience, and keep the slogan as a subhead.",
                 ["Make the H1 concrete: '<Category> for <audience>' beats 'Unlock your potential'.",
                  "Put the aspirational line in the subhead where it still does its emotional work.",
                  "Add a one-sentence definition immediately under the H1.",
                  "Test the result by reading only the first screen and asking: could a stranger say "
                  "what this company sells and who it is for?"],
                 effort="low", owner="content", affected_urls=[home["url"]])

    # -- QUOTE-003: fact-class coverage ----------------------------------------
    # Gate each class so we do not demand pricing from a charity or hours from a SaaS.
    page_types = {p.get("page_type") for p in docs}
    expected_classes = {"offering"}
    if page_types & {"pricing", "product"} or "pricing" in site_text_all.lower():
        expected_classes.add("pricing")
    if page_types & {"contact", "about", "homepage", "local"}:
        expected_classes |= {"contact"}
    if page_types & {"about", "homepage"}:
        expected_classes |= {"founding"}
    if page_types & {"contact", "local"}:
        expected_classes.add("location")

    missing_facts = []
    for name in sorted(expected_classes):
        pattern, human = FACT_PATTERNS[name]
        if not pattern.search(site_text_all):
            missing_facts.append((name, human))
    if missing_facts:
        listed = "; ".join(f"{n} ({h})" for n, h in missing_facts)
        find.add("QUOTE-003", "Facts assistants are routinely asked for are not stated in text anywhere",
                 "high" if len(missing_facts) >= 2 else "medium",
                 f"Across {total} crawled pages ({len(site_text_all.split())} words of body text), no "
                 f"plain-text statement was found for: {listed}. These are the questions users ask "
                 "assistants about a brand most often. When the fact is absent from the site, the "
                 "assistant answers from a third-party directory (often stale or wrong) or says it "
                 "does not know.",
                 "Publish each missing fact as plain text on a page dedicated to it.",
                 ["Create or extend a canonical page per fact class: pricing on /pricing, contact "
                  "details on /contact, company facts on /about.",
                  "State each fact as a complete sentence with the entity named "
                  "(\"<Brand> was founded in 2014 in Berlin.\").",
                  "Mirror each fact into structured data (Offer.price, "
                  "Organization.address/foundingDate) so it is machine-readable twice.",
                  "Avoid putting these facts only in a PDF, an image, a contact form or a chat widget."],
                 effort="medium", owner="content",
                 metrics={"missing_fact_classes": [n for n, _ in missing_facts]})

    # -- QUOTE-004: chunk-hostile prose ----------------------------------------
    wall_pages, long_paras = [], []
    for page in docs:
        view, _ = best_view(page)
        text = body_text(view)
        words = len(text.split())
        if words < 250:
            continue
        heads = [h for h in view.get("headings", []) if h["level"] in (2, 3) and h["text"].strip()]
        if words >= 600 and len(heads) < max(2, words // 500):
            wall_pages.append({"url": page["url"], "words": words, "subheads": len(heads)})
        blocks = [b for b in re.split(r"\n+", text) if len(b.split()) > 0]
        overlong = [b for b in blocks if len(b.split()) > 160]
        if overlong:
            long_paras.append({"url": page["url"], "count": len(overlong),
                               "longest": max(len(b.split()) for b in overlong)})
    if wall_pages:
        sample = "; ".join(f"{w['url']} ({w['words']} words, {w['subheads']} subheading(s))"
                           for w in wall_pages[:3])
        find.add("QUOTE-004", "Long pages have too few subheadings to chunk cleanly",
                 escalate("medium", len(wall_pages) / total),
                 f"{len(wall_pages)}/{total} pages carry 600+ words with fewer subheadings than one "
                 f"per ~500 words. {sample}. Retrievers cut long text at arbitrary boundaries when "
                 "there is no heading structure to cut on, so the returned passage often starts "
                 "mid-argument and gets discarded as low quality.",
                 "Add a descriptive H2/H3 roughly every 200-300 words.",
                 ["Break long sections with headings that state the point of the section, not "
                  "'Overview' or 'Introduction'.",
                  "Phrase headings the way a user would ask the question, so the heading itself "
                  "matches the query.",
                  "Keep the first sentence under each heading a complete, standalone answer to it.",
                  "Add a table of contents on long pages -- it also helps human scanning."],
                 effort="medium", owner="content",
                 affected_urls=[w["url"] for w in wall_pages[:20]])
    if long_paras and len(long_paras) >= max(2, total * 0.3):
        worst = max(long_paras, key=lambda p: p["longest"])
        find.add("QUOTE-005", "Paragraphs are too long to survive as retrieved passages", "low",
                 f"{len(long_paras)}/{total} pages contain paragraphs over 160 words "
                 f"(longest: {worst['longest']} words on {worst['url']}). Oversized blocks get split "
                 "mid-idea or truncated, and the surviving fragment often loses the qualifier that "
                 "made the claim accurate.",
                 "Split long paragraphs into 40-80 word units, one idea each.",
                 ["Break each long block after its first complete idea.",
                  "Lead every paragraph with its conclusion, so a truncated chunk still carries the point.",
                  "Convert enumerations buried in prose into real <ul>/<ol> lists."],
                 effort="low", owner="content",
                 affected_urls=[p["url"] for p in long_paras[:20]])

    # -- QUOTE-006: no question-shaped content ---------------------------------
    question_headings = [h["text"] for p in docs
                         for h in best_view(p)[0].get("headings", [])
                         if h["text"].strip().endswith("?")]
    has_faq_page = any(p.get("page_type") == "faq" for p in docs)
    if not question_headings and not has_faq_page and total >= 3:
        find.add("QUOTE-006", "No question-and-answer content anywhere on the site", "medium",
                 f"Across {total} crawled pages there are no headings phrased as questions and no page "
                 "classified as an FAQ. Assistants answer questions; content already shaped as "
                 "question-then-direct-answer matches the query closely and can be quoted with no "
                 "reshaping, which makes it disproportionately likely to be selected.",
                 "Publish an FAQ built from the questions customers actually ask, with direct answers.",
                 ["Collect real questions from sales calls, support tickets and site search logs.",
                  "Make each question an H2 phrased exactly as a user would type it.",
                  "Answer in the first 1-2 sentences under the heading, directly and completely, "
                  "before adding any elaboration.",
                  "Mark the page up with FAQPage JSON-LD so the pairing is explicit.",
                  "Put FAQs on the relevant product/pricing pages too, not only on one central page."],
                 effort="medium", owner="content")

    # -- QUOTE-007: chunks that never name their subject ------------------------
    orphan_sections, checked_sections = 0, 0
    worst_examples = []
    for page in docs:
        view, _ = best_view(page)
        for section in view.get("sections", []):
            if section.get("word_count", 0) < 40:
                continue
            checked_sections += 1
            blob = ((section.get("heading") or "") + " " + section.get("text", "")).lower()
            names_brand = any(tok in blob for tok in brand)
            if not names_brand and FIRST_PERSON.search(blob):
                orphan_sections += 1
                if len(worst_examples) < 3:
                    worst_examples.append({
                        "url": page["url"],
                        "heading": (section.get("heading") or "(no heading)")[:70],
                        "opening": section.get("text", "")[:130]})
    if checked_sections >= 6 and orphan_sections / checked_sections > 0.7:
        sample = "; ".join(f"{e['url']} section '{e['heading']}' opens \"{e['opening']}...\""
                           for e in worst_examples)
        find.add("QUOTE-007", "Content sections never name the brand, so quoted passages lose attribution",
                 "high",
                 f"{orphan_sections}/{checked_sections} substantive sections "
                 f"({int(100 * orphan_sections / checked_sections)}%) refer to the company only as "
                 f"'we'/'our' and never name it. {sample}. A retrieved passage is shown largely on its "
                 "own: if it says 'our platform reduces costs by 40%' without saying whose platform, "
                 "the claim cannot be attributed and is dropped, or worse, attributed to whoever else "
                 "is in the answer.",
                 "Name the brand explicitly at least once in every substantive section.",
                 [f"Replace the first 'we/our' of each section with the brand name -- "
                  f"'{brand_label.capitalize()} reduces import time by 40%' rather than "
                  f"'our platform reduces import time by 40%'.",
                  "Do the same in headings where it reads naturally, and in image alt text.",
                  "Write each section so it still makes sense read alone, with no preceding paragraph.",
                  "Do not overdo it -- once per section is enough; keyword stuffing reads as spam to "
                  "both humans and ranking systems."],
                 effort="low", owner="content",
                 metrics={"sections_checked": checked_sections, "sections_unattributed": orphan_sections})

    # -- QUOTE-008: boilerplate drowns the content -----------------------------
    boilerplate = []
    for page in docs:
        view, _ = best_view(page)
        total_words = view.get("word_count", 0)
        main_words = view.get("main_word_count", 0)
        if total_words >= 300 and view.get("has_main") and main_words / max(1, total_words) < 0.35:
            boilerplate.append({"url": page["url"], "ratio": round(main_words / total_words, 2),
                                "main": main_words, "total": total_words})
    if boilerplate:
        worst = min(boilerplate, key=lambda b: b["ratio"])
        find.add("QUOTE-008", "Navigation and footer text outweighs the unique content",
                 escalate("medium", len(boilerplate) / total),
                 f"{len(boilerplate)}/{total} pages devote under 35% of their text to <main>. Worst: "
                 f"{worst['url']} at {int(worst['ratio'] * 100)}% ({worst['main']} of "
                 f"{worst['total']} words). Every retrieved chunk from such a page is mostly menu "
                 "labels and legal boilerplate, which dilutes its relevance score and wastes the "
                 "passage on text that is identical across the whole site.",
                 "Reduce repeated chrome and expand the unique content on thin pages.",
                 ["Collapse mega-menus and long footers behind semantic <nav>/<footer> landmarks so "
                  "extractors can discount them.",
                  "Add genuine, page-specific content to the thinnest pages, or consolidate them.",
                  "Move repeated marketing blocks (testimonials, badge walls) below the unique content."],
                 effort="medium", owner="content",
                 affected_urls=[b["url"] for b in boilerplate[:20]])

    # -- QUOTE-009: inconsistent naming of the same thing ----------------------
    heading_terms = Counter()
    for page in docs:
        view, _ = best_view(page)
        for heading in view.get("headings", []):
            for word in re.findall(r"[A-Za-z][A-Za-z0-9\-]{3,}", heading["text"]):
                low = word.lower()
                if low not in STOPWORDS and low not in brand:
                    heading_terms[low] += 1
    near_duplicates = []
    terms = [t for t, c in heading_terms.items() if c >= 2]
    for i, first in enumerate(terms):
        for second in terms[i + 1:]:
            if first != second and (first.startswith(second) or second.startswith(first)) \
                    and abs(len(first) - len(second)) <= 3:
                near_duplicates.append((first, second))
    if len(near_duplicates) >= 3:
        sample = ", ".join(f"'{a}'/'{b}'" for a, b in near_duplicates[:4])
        find.add("QUOTE-009", "The same concepts appear under inconsistent names", "low",
                 f"{len(near_duplicates)} near-duplicate term pair(s) appear across headings: {sample}. "
                 "Inconsistent naming splits the evidence for a concept across several weak variants "
                 "instead of concentrating it in one strong one, so no single term is clearly "
                 "associated with the brand.",
                 "Standardise on one name per concept and use it everywhere.",
                 ["Write a short internal terminology list: one canonical name per product and feature.",
                  "Update headings, navigation, structured data and marketing copy to match it.",
                  "Where an alternative name has real search demand, mention it once as an explicit "
                  "alias (\"X (also called Y)\") rather than alternating between them."],
                 effort="medium", owner="content")

    # -- QUOTE-010: unexpanded acronyms ----------------------------------------
    acronym_counts = Counter()
    for page in docs:
        view, _ = best_view(page)
        text = body_text(view)
        for match in ACRONYM.finditer(text):
            token = match.group(1)
            if token in {"USA", "UK", "EU", "CEO", "CTO", "CFO", "FAQ", "API", "PDF", "URL",
                         "HTML", "CSS", "SEO", "USD", "EUR", "GBP", "AI", "IT", "HR", "OK"}:
                continue
            if not re.search(r"\(\s*" + re.escape(token) + r"\s*\)", text) and \
                    not re.search(re.escape(token) + r"\s*\(", text):
                acronym_counts[token] += 1
    frequent = [t for t, c in acronym_counts.items() if c >= 4]
    if len(frequent) >= 3:
        find.add("QUOTE-010", "Domain acronyms are used without ever being expanded", "low",
                 f"{len(frequent)} acronym(s) appear 4+ times with no expansion anywhere in the "
                 f"crawled text: {', '.join(sorted(frequent)[:6])}. An unexpanded acronym cannot be "
                 "matched to the words a user actually types, so the content misses the queries it "
                 "should win, and a retrieved chunk containing it is unintelligible on its own.",
                 "Expand every domain acronym on first use, per page.",
                 ["Write the full term followed by the acronym in parentheses on first use: "
                  "'Customer Data Platform (CDP)'.",
                  "Do this per page -- a reader arriving from an AI answer never saw your other pages.",
                  "Add a glossary page defining each term, and link it from the relevant content."],
                 effort="low", owner="content")

    # -- QUOTE-011: unquotable numeric claims ----------------------------------
    bare_claims = []
    for page in docs:
        view, _ = best_view(page)
        for sentence in sentences(body_text(view), 300):
            if NUMERIC_CLAIM.search(sentence) and not UNIT_CONTEXT.search(sentence):
                bare_claims.append({"url": page["url"], "sentence": sentence[:160]})
    if len(bare_claims) >= 3:
        sample = "; ".join(f'"{c["sentence"]}" ({c["url"]})' for c in bare_claims[:3])
        find.add("QUOTE-011", "Performance claims lack the baseline that makes them quotable", "low",
                 f"{len(bare_claims)} numeric claim(s) state a figure with no comparison point, "
                 f"timeframe or unit of measure. Examples: {sample}. A claim like '50% faster' with no "
                 "'faster than what' cannot be repeated responsibly, so cautious answer generators "
                 "drop it -- the strongest proof points are the ones being discarded.",
                 "Rewrite each claim to include the baseline, the timeframe and the source.",
                 ["State the comparison explicitly: '40% faster than our previous release (v3.2, "
                  "measured on a 10k-row import)'.",
                  "Attribute third-party figures to the named source with a year and a link.",
                  "Add the measurement method for benchmark claims so they can be verified.",
                  "Date every statistic -- an undated number ages into a liability."],
                 effort="low", owner="content",
                 affected_urls=sorted({c["url"] for c in bare_claims})[:20])

    # -- QUOTE-012: uninformative link text ------------------------------------
    generic_links, total_links = 0, 0
    for page in docs:
        view, _ = best_view(page)
        for link in view.get("links", []):
            if not link.get("abs"):
                continue
            total_links += 1
            label = (link.get("text") or link.get("aria_label") or "").strip()
            if GENERIC_ANCHOR.match(label) or not label:
                generic_links += 1
    if total_links >= 40 and generic_links / total_links > 0.25:
        find.add("QUOTE-012", "A quarter of internal links carry no descriptive anchor text", "low",
                 f"{generic_links}/{total_links} crawled links "
                 f"({int(100 * generic_links / total_links)}%) use empty or generic anchor text "
                 "('click here', 'read more', 'learn more'). Anchor text is one of the strongest "
                 "signals of what the destination page is about; generic labels give the target page "
                 "no topical support and leave screen-reader and machine consumers with nothing.",
                 "Rewrite anchors to describe the destination.",
                 ["Replace 'learn more' with the destination's subject "
                  "('see the Pro plan pricing').",
                  "Keep anchor text under ~8 words and make it unique per destination.",
                  "Never label two different destinations with the same anchor text on one page."],
                 effort="low", owner="content")

    # -- QUOTE-013: no stated boundaries, so an assistant guesses ------------
    # Asked "does X support Y?", a model answers from a page that only ever says
    # what X *does*. With nothing stating what it does NOT do, the plausible-sounding
    # guess is the answer -- and the brand gets blamed for the hallucination.
    # Explicit boundaries and spec tables are the cheapest way to bound that.
    #
    # Guarded hard, because the naive form of this check fires on every page that
    # lacks a <table>: it only runs on pages where the question actually arises
    # (product, pricing, docs) AND that make positive capability claims worth bounding.
    CAPABILITY_CLAIM = re.compile(
        r"\b(?:supports?|integrat\w+|compatible with|works with|includes?|features?|"
        r"connects? to|syncs? with|available (?:for|on)|built for)\b", re.I)
    BOUNDARY = re.compile(
        r"\b(?:does not|doesn't|not compatible|not supported|not available|"
        r"unsupported|excludes?|excluding|limitations?|not included|"
        r"what (?:it|this) is not|out of scope|we do not|cannot)\b", re.I)

    bounded_types = {"product", "pricing", "docs"}
    candidates, unbounded = [], []
    for page in docs:
        if page.get("page_type") not in bounded_types:
            continue
        view, _ = best_view(page)
        text = body_text(view)
        if len(text.split()) < 200:
            continue
        claims = len(CAPABILITY_CLAIM.findall(text))
        if claims < 3:
            continue                       # not a page that makes capability promises
        candidates.append(page["url"])
        has_table = view.get("counts", {}).get("table", 0) > 0
        has_boundary = bool(BOUNDARY.search(text))
        if not has_table and not has_boundary:
            unbounded.append({"url": page["url"], "claims": claims})

    if candidates and len(unbounded) >= max(2, len(candidates) * 0.5):
        worst = max(unbounded, key=lambda u: u["claims"])
        find.add("QUOTE-013",
                 "Pages state what the product does but never what it does not",
                 escalate("medium", len(unbounded) / max(1, len(candidates))),
                 f"{len(unbounded)}/{len(candidates)} product, pricing or documentation pages make "
                 f"capability claims (\"supports\", \"integrates with\", \"works with\") without any "
                 f"specification table or explicit boundary statement. Worst: {worst['url']} with "
                 f"{worst['claims']} capability claims and no stated limits. When a user asks an "
                 "assistant whether this product does something it does not do, there is nothing on "
                 "the page to contradict a plausible guess -- so the assistant invents a capability "
                 "and the brand inherits the support ticket.",
                 "State the boundaries explicitly, and put the specifics in a real table.",
                 ["Add a specification table with the concrete values (limits, tiers, supported "
                  "versions, regions) -- a table is unambiguous in a way prose is not.",
                  "Add a short, plainly-worded limits section: what it does not do, what it is not "
                  "compatible with, who it is not for.",
                  "Name the nearest alternative for the cases you exclude; it builds trust and it "
                  "keeps the assistant from guessing you cover them.",
                  "Mirror the same values into structured data so the boundary is machine-readable."],
                 effort="medium", owner="content",
                 affected_urls=[u["url"] for u in unbounded[:20]],
                 metrics={"pages_with_claims": len(candidates),
                          "pages_without_boundaries": len(unbounded)})

    find.write(args.workspace, total)
    print(f"answer-extractability-audit: {len(find.items)} finding(s) across {total} page(s)")


if __name__ == "__main__":
    main()
