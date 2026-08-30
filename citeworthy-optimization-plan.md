# Citeworthy — Manual Testing & Optimization Plan

**Goal:** Prove your 91 checks actually catch real problems on real sites, find the
problems they *don't* catch yet, and fix both — without overfitting to the sites you
tested on (the contest grades on sites you've never seen).

Do the phases in order. Don't skip Phase 1 — testing before you have ground truth just
tells you the tool ran, not that it's right.

---

## Phase 1 — Get ground truth (before touching your tool)

**What to do:** For each site on your test list (below), open ChatGPT, Perplexity, and
Claude and ask it directly:

1. "What does [brand] do?"
2. "What does [brand] cost?" or "What are [brand]'s pricing plans?"
3. "Who founded/owns [brand]?"
4. "Is [brand] legitimate — what do people say about it?"

**What to record**, per site per question:
- Did it answer at all, or say "I don't have information on that"?
- Was the answer correct?
- Did it cite the brand's own site, or a third party (Reddit, review site, competitor)?
- Did it confuse the brand with something/someone else?

This is your **ground truth** — the real-world symptom. Everything after this is about
explaining *why* that symptom happened.

---

## Phase 2 — Find the cause by hand

For every wrong/missing/misattributed answer from Phase 1, manually check:

- **View source vs. rendered page** — is the fact you asked about actually in the raw
  HTML, or only visible after JavaScript runs? (`curl <url>` vs. opening it in a browser)
- **robots.txt** — visit `<site>/robots.txt` yourself. Is anything relevant disallowed?
- **Structured data** — run the page through Google's Rich Results Test or
  schema.org's validator. Does it exist? Is it valid? Does it match what's on the page?
- **Who else talks about this brand** — search the brand name *without* `site:` — is
  there independent coverage, or does only the brand's own site mention itself?
- **Freshness** — is there a visible date anywhere? Is it old?

Write down, in plain language, the actual root cause you found.

---

## Phase 3 — Run citeworthy on the same sites

Run your tool against every site from Phase 1. For each root cause you found by hand,
check:

- ✅ **Caught it** — the tool flagged the same issue, with reasonable evidence.
- ❌ **Missed it** — the tool said nothing, or said the check passed. This is a real
  gap — go add a check for it.
- ⚠️ **False positive** — the tool flagged something that manual inspection shows isn't
  actually a problem. This is worse than a miss for scoring — fix or guard the check.

Use the tracking table at the bottom of this file to log every case.

---

## Phase 4 — Fix gaps, without overfitting

For every ❌ or ⚠️ from Phase 3:

1. Write the check as a **pattern**, not a fact about that one site. Bad: "flag if
   homepage H1 is 'Unlock your potential'." Good: "flag if no page opens with a
   sentence that both names the brand and states what it is."
2. Add it to your finding catalog with an id, severity, and the evidence it needs.
3. Re-run the tool on that site to confirm it's now caught.
4. **Re-run the tool on a site you have NOT used for calibration.** If a fix only
   works on the site that inspired it, it's overfit — rewrite it more generally.

---

## Phase 5 — Generalization check (do this last)

Pick 3–4 sites you have **never looked at before** — not from your test list, not
used in Phases 1–4. Run the full tool cold. Check:

- Did it produce sensible findings without you having to interpret/fix anything?
- Any confident false positives?
- Did the report read clearly to someone who's never seen your tool before?

If this phase surfaces new gaps, you're not done — but resist the urge to keep tuning
against these specific sites too. Rotate in fresh ones if you need another round.

---

## Phase 6 — Final polish

- [ ] Every finding cites a measured quantity and a check id (no vague claims)
- [ ] `limitations` section is honest about anything not verified
- [ ] Report reads clearly top-to-bottom for a non-technical person
- [ ] Tool runs clean, under 5 minutes, on a site it's never seen
- [ ] README clearly explains what each skill does and how the entrypoint composes them

---

## Test-site list (real URLs)

Test **2+ sites per category** — one large/well-resourced, one small/independent.
**Don't assume the small one fails and the large one succeeds** — that assumption is
exactly what Phase 1 is supposed to test, not confirm. A few of these (the small
independent brands) were pulled from current "best small-brand website" write-ups
during research for this list; the rest are large, stable, well-known sites.

### E-commerce
- Large/major: `nike.com`, `patagonia.com`, `glossier.com`, `crateandbarrel.com`
- Small/independent: `bitetoothpastebits.com` (sustainable oral care, DTC)
- Small/independent: `blackstarpastry.com` (Sydney bakery, famous single product)
- Small/independent: `notebooktherapy.com` (East-Asian stationery, Shopify)
- Small/independent: `goayo.com` (light-therapy wearable, DTC health device)
- **Add your own:** a tiny Etsy/Shopify store in a niche you know personally

### SaaS / B2B software
- Large/well-documented: `stripe.com`, `notion.so`, `hubspot.com`, `helpscout.com`
- Smaller/newer: `sendpotion.com` (AI video personalization for sales)
- **Add your own:** a small SaaS tool you or a friend actually uses that you suspect is
  "invisible" to AI assistants

### Local business / services
- Multi-location chain: `chick-fil-a.com`, `greatclips.com`
- Boutique/independent: `thescottresort.com` (independent boutique hotel — also fits
  Travel/hospitality below)
- **Add your own — this is the most valuable category to fill in yourself:** pick an
  actual single-location restaurant, dentist, plumber, or salon in your own city. Truly
  local, low-resource sites are exactly what generic example lists (including this one)
  under-represent, and they're where AI-discoverability problems are most common.

### Publisher / content / blog
- Large: `nytimes.com`, `theverge.com`, `seriouseats.com`
- Niche/independent: any Substack or personal blog in a topic you follow — pick one
  with a small, dedicated readership so you can judge if AI tools have "heard of it"

### Nonprofit / .org
- Large/international: `redcross.org`, `doctorswithoutborders.org`, `wikipedia.org`
- Cultural/institutional (smaller): `franshalsmuseum.nl` (Dutch museum — good test of
  entity disambiguation, since "Frans Hals" is also just a historical painter's name)
- **Add your own:** a small local charity or community organization near you

### Education / institutional
- Large: `harvard.edu`, `khanacademy.org`, `coursera.org`
- **Add your own:** a specific university department page (not the homepage) — these
  are often much worse maintained than the main site

### Travel / hospitality
- Large chain: `marriott.com`, `airbnb.com`
- Independent boutique: `thescottresort.com` (Scottsdale, AZ)
- **Add your own:** a small independent tour operator or B&B

### Finance / professional services
- Large: a major bank or well-known fintech (`stripe.com` again fits here too)
- **Add your own:** a local accounting firm, law firm, or independent financial
  advisor's site — these are almost always thin on structured data and a great source
  of real findings

---

## Tracking template

Copy this table and fill it in as you go through Phase 1–3.

| Site | Question asked | AI's answer (correct/wrong/missing) | Root cause found manually | Did citeworthy catch it? | Notes |
|---|---|---|---|---|---|
| | | | | ✅ / ❌ / ⚠️ | |

---

## Reminders

- **Ground truth first, tool second.** Never trust a finding you haven't independently verified by hand at least once per check type.
- **Rotate your test sites.** Sites used to build a check should not be the only sites used to confirm it works.
- **A missed problem and a false positive are both failures** — the rubric penalizes both equally ("few misses and few false positives").
- **Keep it read-only.** Everything above is passive inspection and querying public AI assistants — no login, no scraping behind auth, no site modification.
