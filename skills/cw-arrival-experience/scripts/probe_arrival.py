#!/usr/bin/env python3
"""
probe_arrival.py -- ENG-* checks: does the visitor who arrives actually stay?

Stage 5, and the on-site half of the problem. The framing that makes these checks
different from a generic UX audit is the *shape of AI-referred traffic*:

  1. The visitor was already given an answer. They arrive to verify it or to act
     on it, with a specific intent and low patience for a re-pitch.
  2. They land on an inner page, not the homepage. Assistants cite the page that
     held the fact, so every page is an entry page and must orient a stranger.
  3. They have no prior session. Nothing is remembered about them, so anything
     the site assumes they already read or already told you is a dead end.

So the checks below measure orientation-on-arrival, cost-to-first-value, and
whether a next step exists -- not brand aesthetics.

Pure analyzer: reads the evidence bundle, performs no network I/O.
"""
import argparse
import re

from evidence import load_bundle, html_pages, best_view, body_text, brand_tokens, escalate, Findings

CTA = re.compile(
    r"\b(buy|shop|order|add to (?:cart|bag)|get started|start (?:free|now|your)|sign ?up|"
    r"create (?:an? )?account|book (?:a )?(?:demo|call|appointment)|request (?:a )?(?:demo|quote)|"
    r"contact (?:us|sales)|talk to|subscribe|download|try (?:it )?free|apply now|"
    r"schedule|get (?:a )?quote|join)\b", re.I)
GENERIC_CTA = re.compile(r"^\W*(?:learn more|read more|more|click here|see more|explore|discover)\W*$", re.I)
OVERLAY = re.compile(
    r"(cookie|consent|gdpr|newsletter|subscribe|popup|pop-up|modal|overlay|interstitial|"
    r"age[- ]?(?:gate|verif)|paywall|lightbox|exit[- ]intent)", re.I)
CONSENT_VENDOR = re.compile(
    r"(onetrust|cookiebot|cookieyes|quantcast|trustarc|usercentrics|iubenda|termly|"
    r"osano|didomi|klaro|cookiehub|complianz)", re.I)
CHAT_WIDGET = re.compile(r"(intercom|drift|zendesk|tawk|crisp|hubspot-messages|livechat|freshchat)", re.I)
TRUST_SIGNALS = {
    "pricing": re.compile(r"\b(pricing|price|plans?|cost|how much)\b", re.I),
    "contact": re.compile(r"\b(contact|get in touch|reach us|support)\b", re.I),
    "about": re.compile(r"\b(about|our story|who we are|company|team)\b", re.I),
    "proof": re.compile(r"\b(customers?|clients?|case stud(?:y|ies)|testimonial|review|"
                        r"trusted by|used by)\b", re.I),
    "policy": re.compile(r"\b(privacy|terms|security|refund|returns?|guarantee)\b", re.I),
}
BLOCKING_SCRIPT_LIMIT = 8


def viewport_ok(value):
    if not value:
        return False
    lowered = value.lower().replace(" ", "")
    if "width=device-width" not in lowered:
        return False
    # A locked viewport blocks zoom, which is an accessibility and usability defect.
    if "user-scalable=no" in lowered or re.search(r"maximum-scale=(?:1(?:\.0)?)\b", lowered):
        return False
    return True


def main():
    ap = argparse.ArgumentParser(description="On-site engagement analyzer (AI-referral framing)")
    ap.add_argument("--workspace", required=True)
    args = ap.parse_args()

    manifest, pages = load_bundle(args.workspace)
    docs = html_pages(pages)
    find = Findings("cw-arrival-experience", "engage")
    for cid in [f"STAY-{n:03d}" for n in range(1, 16)]:
        find.check(cid)

    if not docs:
        find.note("No successfully-fetched HTML pages in the bundle; engagement checks skipped.")
        find.write(args.workspace, 0)
        print("cw-arrival-experience: no analysable pages")
        return

    total = len(docs)
    start_url = manifest.get("start_url")
    brand = brand_tokens(manifest)
    site_text_all = " ".join(body_text(best_view(p)[0]) for p in docs).lower()
    all_link_text = " ".join(
        (l.get("text") or "") + " " + (l.get("abs") or "")
        for p in docs for l in best_view(p)[0].get("links", [])).lower()

    # -- STAY-001: deep-link orientation --------------------------------------
    # The check that most distinguishes AI-referral readiness from ordinary UX.
    inner = [p for p in docs if p["url"] != start_url and p.get("page_type") != "legal"]
    disoriented = []
    for page in inner:
        view, _ = best_view(page)
        text = body_text(view)
        h1 = next((h["text"].strip() for h in view.get("headings", [])
                   if h["level"] == 1 and h["text"].strip()), "")
        opening = (view.get("first_screen_text") or text)[:900].lower()
        names_brand = any(tok in opening for tok in brand)
        has_breadcrumb = "BreadcrumbList" in " ".join(
            str(view.get("jsonld", []))) or bool(re.search(
                r'(?:breadcrumb|aria-label=["\'][^"\']*breadcrumb)', str(view.get("links", []))[:4000], re.I))
        misses = []
        if not h1:
            misses.append("no H1")
        if not names_brand:
            misses.append("brand not named in the opening screen")
        if not has_breadcrumb:
            misses.append("no breadcrumb trail")
        if len(misses) >= 2:
            disoriented.append({"url": page["url"], "misses": misses, "h1": h1[:60]})
    if inner and len(disoriented) >= max(2, len(inner) * 0.5):
        sample = "; ".join(f"{d['url']} ({', '.join(d['misses'])})" for d in disoriented[:3])
        find.add("STAY-001", "Inner pages do not orient a visitor who lands on them cold",
                 escalate("high", len(disoriented) / max(1, len(inner))),
                 f"{len(disoriented)}/{len(inner)} inner pages fail two or more orientation checks "
                 f"(an H1 naming the page's subject, the brand name in the first screen, a breadcrumb "
                 f"trail). {sample}. Assistants cite the page that held the fact, not the homepage, so "
                 "every inner page is an entry page for a stranger with no session history. A visitor "
                 "who cannot tell whose site this is and where they are within it leaves immediately.",
                 "Make every page self-orienting: whose site, what page, where it sits, what is next.",
                 ["Add a visible breadcrumb trail to every page below the top level "
                  "(and mark it up with BreadcrumbList).",
                  "Ensure the brand name appears in the header and in the first screen of body text.",
                  "Give each page a specific H1 naming its subject, plus a one-line summary "
                  "underneath saying what the page covers.",
                  "Add a 'related' or 'next step' block so an arriving visitor has somewhere to go "
                  "other than back."],
                 effort="medium", owner="content/design",
                 affected_urls=[d["url"] for d in disoriented[:20]])

    # -- STAY-002: above-the-fold clarity on the entry page -------------------
    home = next((p for p in docs if p["url"] == start_url), docs[0])
    home_view, _ = best_view(home)
    first_screen = (home_view.get("first_screen_text") or "")[:900]
    home_ctas = [l for l in home_view.get("links", [])
                 if CTA.search((l.get("text") or "") + " " + (l.get("aria_label") or ""))]
    if len(first_screen.split()) < 20 or not home_ctas:
        total_links = len([l for l in home_view.get("links", []) if l.get("abs")])
        problems = []
        if len(first_screen.split()) < 20:
            problems.append(f"only {len(first_screen.split())} words of non-navigation text in the "
                            "opening screen")
        if not home_ctas:
            problems.append(f"no action-verb call to action among any of the {total_links} links on "
                            "the page (checked against 20 common CTA verb patterns)")
        find.add("STAY-002", "The entry page's first screen does not say what to do next",
                 "high",
                 f"On {home['url']} (measured on the server-rendered HTML): "
                 f"{', and '.join(problems)}. Opening text captured: "
                 f"'{first_screen[:180]}'. A visitor arriving with intent from an AI answer decides "
                 "within seconds whether this page serves that intent; with no explicit next action "
                 "in view, the decision defaults to leaving.",
                 "Put a clear value proposition and one primary action in the first screen.",
                 ["State what the company does and for whom in the first screen, as text.",
                  "Add one primary call to action with an explicit verb and object "
                  "('Start a free trial', 'Book a 20-minute demo') -- not 'Learn more'.",
                  "Keep exactly one primary action visually dominant; make everything else secondary.",
                  "Verify the text is real HTML, not baked into the hero image."],
                 effort="low", owner="content/design", affected_urls=[home["url"]])

    # -- STAY-003: weight and blocking resources ------------------------------
    heavy = []
    for page in docs:
        raw = page["raw"]
        blocking = [s for s in raw.get("scripts", []) if not s.get("async") and not s.get("defer")]
        html_kb = round(page.get("bytes", 0) / 1024)
        if html_kb > 500 or len(blocking) > BLOCKING_SCRIPT_LIMIT:
            heavy.append({"url": page["url"], "kb": html_kb, "blocking": len(blocking),
                          "scripts": raw.get("counts", {}).get("script", 0),
                          "ms": page.get("fetch_ms")})
    if heavy:
        worst = max(heavy, key=lambda h: h["blocking"] * 100 + h["kb"])
        find.add("STAY-003", "Pages ship enough render-blocking weight to delay first paint",
                 escalate("medium", len(heavy) / total),
                 f"{len(heavy)}/{total} pages exceed 500 KB of HTML or carry more than "
                 f"{BLOCKING_SCRIPT_LIMIT} render-blocking scripts. Worst: {worst['url']} "
                 f"({worst['kb']} KB HTML, {worst['blocking']} blocking script(s), "
                 f"{worst['scripts']} script tags total, {worst['ms']} ms server response). Each "
                 "blocking script delays the first meaningful paint, and abandonment rises steeply "
                 "with every additional second before content appears.",
                 "Defer non-critical JavaScript and cut the HTML payload.",
                 ["Add `defer` (or `async` where order does not matter) to every non-critical script.",
                  "Move third-party tags (analytics, chat, A/B testing) behind a tag manager that "
                  "loads them after first paint, and audit what each one is worth.",
                  "Inline only the critical CSS and load the rest asynchronously.",
                  "Compress and cache HTML at the edge; target under 200 KB of HTML per page.",
                  "Measure with Lighthouse or CrUX after the change rather than assuming."],
                 effort="medium", owner="web/dev",
                 affected_urls=[h["url"] for h in heavy[:20]])

    # -- STAY-004: mobile viewport ---------------------------------------------
    # Two genuinely different defects, reported separately. Conflating them produced a
    # finding on vercel.com whose evidence said "without width=device-width" about a page
    # that HAS width=device-width -- accurate detection, inaccurate explanation.
    no_viewport, zoom_locked = [], []
    for page in docs:
        value = best_view(page)[0].get("viewport")
        lowered = (value or "").lower().replace(" ", "")
        if not lowered or "width=device-width" not in lowered:
            no_viewport.append(page["url"])
        elif "user-scalable=no" in lowered or "maximum-scale=1" in lowered:
            zoom_locked.append((page["url"], value))

    if no_viewport and len(no_viewport) >= max(1, total * 0.3):
        find.add("STAY-004", "Pages declare no mobile viewport",
                 escalate("high", len(no_viewport) / total, start_url in no_viewport),
                 f"{len(no_viewport)}/{total} pages declare no viewport meta tag, or one without "
                 f"`width=device-width`. Example: {no_viewport[0]}. A phone then renders the desktop "
                 "layout scaled down to illegibility. The majority of assistant referrals arrive on "
                 "mobile.",
                 "Declare a standard, zoomable viewport on every page.",
                 ["Add `<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">` "
                  "to the base template.",
                  "Test the key templates at 360px wide and fix horizontal overflow.",
                  "Keep body text at 16px or larger and tap targets at least 44x44px."],
                 effort="low", owner="web/dev", affected_urls=no_viewport[:20])

    if zoom_locked and len(zoom_locked) >= max(1, total * 0.3):
        find.add("STAY-015", "The mobile viewport is configured to block pinch-zoom", "medium",
                 f"{len(zoom_locked)}/{total} pages set `user-scalable=no` or `maximum-scale=1`. "
                 f"Example: {zoom_locked[0][0]} -> '{zoom_locked[0][1]}'. The viewport is otherwise "
                 "correct, so the layout is responsive -- but a visitor who needs to enlarge small "
                 "text cannot, which fails WCAG 1.4.4 and costs exactly the users least able to "
                 "work around it.",
                 "Remove the zoom restriction and keep the rest of the viewport declaration.",
                 ["Drop `user-scalable=no` and `maximum-scale=1`, keeping "
                  "`width=device-width, initial-scale=1`.",
                  "If the restriction exists to stop accidental zoom on a map or canvas, scope it to "
                  "that element with CSS `touch-action` instead of disabling it page-wide.",
                  "Verify pinch-zoom works on a real device after the change."],
                 effort="low", owner="web/dev",
                 affected_urls=[u for u, _ in zoom_locked[:20]])

    # -- STAY-005: interruption on arrival -------------------------------------
    interrupters = []
    for page in docs:
        raw_scripts = " ".join(s.get("src", "") for s in page["raw"].get("scripts", []))
        signals = []
        if CONSENT_VENDOR.search(raw_scripts):
            signals.append("consent-management platform")
        if CHAT_WIDGET.search(raw_scripts):
            signals.append("chat widget")
        dialogs = page["raw"].get("counts", {}).get("dialog", 0)
        if dialogs:
            signals.append(f"{dialogs} <dialog> element(s)")
        if OVERLAY.search(" ".join(h["text"] for h in page["raw"].get("headings", []))):
            signals.append("overlay-related heading")
        if len(signals) >= 2:
            interrupters.append({"url": page["url"], "signals": signals})
    if interrupters and len(interrupters) >= max(2, total * 0.5):
        find.add("STAY-005", "Multiple interruption layers load before the content is usable",
                 "medium",
                 f"{len(interrupters)}/{total} pages load two or more interruption mechanisms. "
                 f"Example: {interrupters[0]['url']} -> {', '.join(interrupters[0]['signals'])}. "
                 "Stacked consent banners, chat bubbles and newsletter modals each cost a dismissal "
                 "before the visitor reaches the answer they came for; a visitor arriving from an AI "
                 "citation has a specific question and abandons rather than clearing three layers.",
                 "Sequence the interruptions: never show more than one, and never before first content.",
                 ["Show the consent notice only where legally required, and never full-screen on mobile.",
                  "Delay the newsletter/exit-intent modal to a real engagement threshold "
                  "(30+ seconds or 50% scroll), and never fire it on the first pageview.",
                  "Load the chat widget lazily, collapsed, and do not auto-open it.",
                  "Verify the primary content is readable and the primary CTA is clickable with every "
                  "overlay dismissed and with none dismissed."],
                 effort="medium", owner="web/dev",
                 affected_urls=[i["url"] for i in interrupters[:20]])

    # -- STAY-006: dead ends ---------------------------------------------------
    dead_ends, generic_only = [], []
    for page in docs:
        view, _ = best_view(page)
        if view.get("word_count", 0) < 120:
            continue
        internal = [l for l in view.get("links", [])
                    if l.get("abs") and manifest.get("site_root", "") in l["abs"]]
        actions = [l for l in internal
                   if CTA.search((l.get("text") or "") + " " + (l.get("aria_label") or ""))]
        labelled = [l for l in internal if (l.get("text") or "").strip()
                    and not GENERIC_CTA.match((l.get("text") or "").strip())]
        if len(internal) < 5:
            dead_ends.append({"url": page["url"], "internal_links": len(internal)})
        elif not actions and len(labelled) < 3:
            generic_only.append(page["url"])
    if dead_ends:
        find.add("STAY-006", "Content pages offer almost no onward path",
                 escalate("medium", len(dead_ends) / total),
                 f"{len(dead_ends)}/{total} substantive pages carry fewer than 5 internal links. "
                 f"Example: {dead_ends[0]['url']} ({dead_ends[0]['internal_links']} internal link(s)). "
                 "A visitor who arrives on a deep page, reads the answer and finds no relevant next "
                 "step leaves the site entirely -- the session ends at one page, and the crawler "
                 "likewise finds no path onward.",
                 "Add contextual next steps and related links to every content template.",
                 ["Add a 'related' block with 3-5 contextually relevant links to each content page.",
                  "Link naturally from body text to the pages that answer the obvious follow-up question.",
                  "Put one clear conversion action on every page, matched to that page's intent.",
                  "Ensure the primary navigation is present and usable on every template."],
                 effort="medium", owner="content",
                 affected_urls=[d["url"] for d in dead_ends[:20]])
    elif generic_only and len(generic_only) >= max(2, total * 0.4):
        find.add("STAY-007", "Pages link onward only with unlabelled or generic calls to action",
                 "low",
                 f"{len(generic_only)}/{total} pages have internal links but no action-verb CTA and "
                 f"fewer than 3 descriptively-labelled links. Examples: {', '.join(generic_only[:3])}. "
                 "'Learn more' does not tell a visitor what they get, so it converts poorly and "
                 "tells a machine nothing about the destination.",
                 "Rewrite calls to action to name the outcome.",
                 ["Replace generic labels with verb + object ('Compare the plans', 'Book a demo').",
                  "Make each CTA on a page distinct so the choice is meaningful.",
                  "Match the CTA to the page's intent -- a docs page should not push 'Buy now'."],
                 effort="low", owner="content", affected_urls=generic_only[:20])

    # -- STAY-008: form friction ------------------------------------------------
    heavy_forms = []
    for page in docs:
        for form in best_view(page)[0].get("forms", []):
            fields = form.get("fields", [])
            required = [f for f in fields if f.get("required")]
            if len(fields) >= 7 or len(required) >= 5:
                heavy_forms.append({"url": page["url"], "fields": len(fields),
                                    "required": len(required)})
    if heavy_forms:
        worst = max(heavy_forms, key=lambda f: f["fields"])
        find.add("STAY-008", "Top-of-funnel forms demand too much before giving anything",
                 "medium",
                 f"{len(heavy_forms)} form(s) request 7+ fields or 5+ required fields. Worst: "
                 f"{worst['url']} ({worst['fields']} fields, {worst['required']} required). A visitor "
                 "referred by an assistant has no relationship with the brand yet and has already "
                 "spent their patience getting here; every additional required field measurably "
                 "reduces completion.",
                 "Cut first-contact forms to the minimum, and defer the rest.",
                 ["Reduce the initial form to the fields you genuinely act on -- often just email.",
                  "Collect the rest progressively, after the first value exchange.",
                  "Enrich company/role data from the email domain instead of asking for it.",
                  "State plainly what happens next after submission, and how fast."],
                 effort="low", owner="marketing",
                 affected_urls=sorted({f["url"] for f in heavy_forms})[:20])

    # -- STAY-009: no site search on a content-heavy site ----------------------
    has_search = any(
        any(f.get("type") == "search" or "search" in (f.get("name") or "").lower()
            for f in form.get("fields", []))
        for page in docs for form in best_view(page)[0].get("forms", []))
    has_search = has_search or "/search" in all_link_text
    if not has_search and total >= 10:
        find.add("STAY-009", "No site search on a content-heavy site", "low",
                 f"No search input or /search destination was found across {total} crawled pages. "
                 "A visitor arriving on a page that is close to but not exactly their question has no "
                 "way to redirect themselves, so they return to the assistant instead of exploring. "
                 "Site-search logs are also the single best source of real user questions to answer "
                 "in content.",
                 "Add site search and mine its logs for content gaps.",
                 ["Add a search input to the header on every template.",
                  "Ensure zero-result queries show suggestions rather than a blank page.",
                  "Log queries and review them monthly -- they are the FAQ your customers actually have.",
                  "Add WebSite + SearchAction JSON-LD so the search surface is machine-discoverable."],
                 effort="medium", owner="web/dev")

    # -- STAY-010: verification surfaces a referred visitor looks for ----------
    missing_trust = []
    for name, pattern in TRUST_SIGNALS.items():
        if not pattern.search(all_link_text) and not pattern.search(site_text_all[:200000]):
            missing_trust.append(name)
    if missing_trust:
        find.add("STAY-010", "Pages a referred visitor uses to verify the brand are missing",
                 "high" if len(missing_trust) >= 3 else "medium",
                 f"No link or on-page reference was found for: {', '.join(missing_trust)} (checked "
                 f"across {total} pages and all internal link text). Someone sent here by an "
                 "assistant is mid-verification: they want to confirm the claim, see what it costs, "
                 "and check that a real company stands behind it. A missing verification surface ends "
                 "that check with a negative answer.",
                 "Publish the missing verification pages and link them from the global navigation.",
                 ["Publish a real pricing page -- even ranges or 'from $X' beat 'contact us', which "
                  "reads as expensive and cannot be quoted in an answer.",
                  "Publish contact details as text (address, email, phone), not only a form.",
                  "Publish an About page naming the team and the company's history.",
                  "Add named customer proof: logos with case studies, or reviews on independent "
                  "platforms you link to.",
                  "Link privacy, terms and security pages from the footer of every page."],
                 effort="medium", owner="content",
                 metrics={"missing_surfaces": missing_trust})

    # -- STAY-011: gated pricing ------------------------------------------------
    pricing_pages = [p for p in docs if p.get("page_type") == "pricing"]
    for page in pricing_pages:
        view, _ = best_view(page)
        if not view.get("prices_in_text") and re.search(
                r"contact (?:us|sales)|request (?:a )?quote|talk to sales|get (?:a )?quote",
                body_text(view), re.I):
            find.add("STAY-011", "The pricing page states no price", "medium",
                     f"{page['url']} is the pricing page but contains no currency figure -- only a "
                     "contact-sales path. This is a legitimate enterprise sales choice, but it has a "
                     "specific cost here: 'how much does X cost' is one of the most common questions "
                     "asked about any brand, and with no number on the page an assistant either "
                     "answers with a competitor's published pricing or reports that pricing is not "
                     "disclosed.",
                     "Publish at least an indicative price, band or starting point.",
                     ["Publish a starting price or a range per tier ('from $X per user per month').",
                      "If exact pricing is genuinely bespoke, publish the pricing *model* "
                      "(what drives the number) and a typical deal size.",
                      "Add Offer/AggregateOffer JSON-LD with whatever figures you do publish.",
                      "Add an FAQ entry answering 'how much does it cost' in plain text."],
                     effort="low", owner="marketing", affected_urls=[page["url"]])
            break

    # -- STAY-012: accessibility defects that also block extraction ------------
    no_alt_total, img_total, unlabelled_inputs, no_lang = 0, 0, 0, []
    for page in docs:
        view, _ = best_view(page)
        for image in view.get("images", []):
            img_total += 1
            if image.get("alt") is None:
                no_alt_total += 1
        for form in view.get("forms", []):
            unlabelled_inputs += sum(1 for f in form.get("fields", []) if not f.get("labelled"))
        if not (view.get("lang") or "").strip():
            no_lang.append(page["url"])
    if img_total >= 10 and no_alt_total / img_total > 0.4:
        find.add("STAY-012", "Most images have no alt attribute",
                 "medium",
                 f"{no_alt_total}/{img_total} images across {total} pages carry no alt attribute "
                 f"({int(100 * no_alt_total / img_total)}%). This blocks screen-reader users outright, "
                 "and it also discards free machine-readable text: alt is one of the few places where "
                 "the meaning of a visual can be stated in words a retriever can index.",
                 "Add descriptive alt text to informative images and empty alt to decorative ones.",
                 ["Write alt that states what the image communicates, not what it depicts "
                  "('Pro plan costs $49 per user per month', not 'pricing table').",
                  "Use alt=\"\" for purely decorative images so assistive tech skips them.",
                  "Never leave the attribute off entirely -- absent and empty mean different things.",
                  "Add an accessibility lint rule to CI."],
                 effort="medium", owner="content")
    if no_lang and len(no_lang) >= max(2, total * 0.5):
        find.add("STAY-013", "Pages declare no language", "low",
                 f"{len(no_lang)}/{total} pages have no lang attribute on <html>. Examples: "
                 f"{', '.join(no_lang[:3])}. Screen readers use it to choose a voice, and language "
                 "detection systems use it to decide which audience's results a page belongs in.",
                 "Set a lang attribute on <html> for every page.",
                 ["Add `<html lang=\"en\">` (or the correct BCP-47 tag) to the base template.",
                  "For multilingual sites, set it per locale and add hreflang alternates between them."],
                 effort="low", owner="web/dev", affected_urls=no_lang[:20])
    if unlabelled_inputs >= 5:
        find.add("STAY-014", "Form inputs have no accessible label", "low",
                 f"{unlabelled_inputs} form field(s) across the crawled sample carry no label, "
                 "aria-label, id or placeholder. An unlabelled field is unusable with a screen "
                 "reader and ambiguous to autofill, which raises abandonment on exactly the forms "
                 "that matter most.",
                 "Give every input a programmatically associated label.",
                 ["Pair each input with a <label for> referencing its id.",
                  "Use aria-label only where a visible label genuinely cannot exist.",
                  "Add autocomplete attributes so browsers can fill known values."],
                 effort="low", owner="web/dev")

    find.write(args.workspace, total)
    print(f"cw-arrival-experience: {len(find.items)} finding(s) across {total} page(s)")


if __name__ == "__main__":
    main()
