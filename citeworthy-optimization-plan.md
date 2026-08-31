# Citeworthy — Manual Testing & Optimization Plan

**Goal:** Prove the 91 checks catch real problems on real sites, find the problems they
*don't* catch yet, and fix both — without overfitting to the sites you tested on (the
contest grades on sites you have never seen).

Do the phases in order. Don't skip Phase 1 — testing before you have ground truth only
tells you the tool *ran*, not that it's *right*.

> **Status note.** Phases 0 and 2.5 below are already done; their real results are
> recorded inline so you can start from them rather than re-deriving them. Everything
> marked ☐ is still yours to do.

---

## Phase 0 — Set up so the later phases mean something ☑ partly done

Two things silently degrade every later phase if skipped.

### 0a. Install a renderer ☐ **do this first**

```bash
pip install playwright && playwright install chromium
```

Without it: `READ-001` and `READ-003` cannot run at all, `READ-002` runs only as a
labelled heuristic, and — most importantly — the entrypoint's **confounding rule**
demotes every content finding to `low` confidence on any JS-heavy site. You would spend
Phase 3 reading a report that is hedging on purpose. One command removes all of that.

### 0b. Pre-flight the test list for crawlability ☑ done

A site that blocks bots can only ever produce access findings, so it tells you nothing
about the other four pillars. Probe before you invest:

```bash
curl -sL -o /dev/null -m 12 -w "%{http_code}\n" \
  -A "BrandAIReadinessAudit/1.0 (+read-only site audit; respects robots.txt)" \
  "https://SITE/"
```

**Result on this plan's own list (28 sites):**

| Outcome | Sites |
|---|---|
| Blocked — access findings only | `crateandbarrel.com` 403, `greatclips.com` 403, `marriott.com` 403, `redcross.org` 403, `patagonia.com` 404, `chick-fil-a.com` timeout |
| **Soft-blocked** — returns 200, serves identical chrome on every URL | `nike.com` |
| Crawlable — full signal | the remaining 21 |

So **7 of 28 (25%)** cannot exercise the parse/extract/trust/engage checks. Keep them —
they are excellent tests of whether the tool *admits* it could not read a site — but do
not count on them for content coverage, and don't schedule deep manual analysis on them.

---

## Phase 1 — Get ground truth (before touching the tool) ☐

For each site, ask ChatGPT, Perplexity and Claude:

1. "What does [brand] do?"
2. "What does [brand] cost?" / "What are [brand]'s pricing plans?"
3. "Who founded/owns [brand]?"
4. "Is [brand] legitimate — what do people say about it?"

**Record per site per question:**

- Did it answer, or say it doesn't know?
- Was the answer correct?
- Did it cite the brand's own site, or a third party (Reddit, review site, competitor)?
- Did it confuse the brand with something else?
- **Which model, which date, and was browsing/search ON or OFF?**
- **Did the answer come from live retrieval or from memory?** (Ask "what sources did you
  use?" — an answer with no sources is training recall.)

### Why those last two fields matter

Assistant answers are **non-deterministic and personalized** — the Round-2 appendix
(section E) says so explicitly. A single query is a *sample*, not ground truth. Two
consequences:

- **Ask twice, in separate sessions.** If the two answers disagree, that variance is
  itself the finding, and you must not log a tool "miss" against it.
- **Training recall is not a site problem.** If an assistant describes a brand correctly
  from memory with no sources, fixing the site changes nothing in the short term — and
  logging that as a citeworthy "miss" would send you chasing a check you don't need.
  Conversely, a brand that is *only* known from memory and never retrieved live is
  exactly the `ENTITY-*` failure mode.

### Triage — the honest scale problem

28 sites × 4 questions × 3 assistants ≈ **336 manual queries** before any tool work.
That is not finishable under deadline. Do it in this order and stop when time runs out:

1. **6 sites, 4 questions, 1 assistant** (~24 queries) — one large + one small from three
   different categories. This alone surfaces most gaps.
2. Add the second assistant on only the sites where answers were *wrong* (not missing).
3. Add the remaining sites only if a category is still unrepresented.

---

## Phase 2 — Find the cause by hand ☐

For every wrong/missing/misattributed answer from Phase 1:

- **View source vs. rendered** — is the fact in the raw HTML, or only after JS?
  `curl -s <url> | grep -c "<the fact>"` vs. the browser.
- **robots.txt** — read the *whole* file, to the last line. Note any duplicated
  `User-agent` group: RFC 9309 requires crawlers to merge them, so a blanket
  `Disallow: /` appended at the bottom silently overrides everything above it.
  *(This is not hypothetical — it is exactly what `snitch.co.in` does at lines 168–169,
  and Python's stdlib `robotparser` reports that site as crawlable because it stops at
  the first matching group.)*
- **Structured data** — Google Rich Results Test or the schema.org validator. Does it
  exist, parse, and **match the visible page**?
- **Who else talks about this brand** — search the name *without* `site:`. Independent
  coverage, or only the brand's own domain?
- **Freshness** — is there a visible date? Is it old?

Write the root cause in plain language before looking at the tool's output. Looking first
biases you toward agreeing with it.

---

## Phase 2.5 — Reuse Phase 1's searches to exercise the entity checks ☐ **high leverage**

Phase 1 already has you doing the search work by hand. Route it into the tool and you
exercise six checks for free — checks that had **never run on a real site**:

```bash
# 1. writes probe_plan.json containing 6 exact queries
python skills/cw-entity-consensus/scripts/probe_entity_consensus.py \
  --workspace ./ws --phase onsite

# 2. answer those queries by hand, record VERBATIM into ws/probe_results.json
#    (schema is in probe_plan.json -> result_schema; leave any field you did not
#     establish as null -- never guess, it makes the finding circular)

# 3. derive ENTITY-006..011
python skills/cw-entity-consensus/scripts/probe_entity_consensus.py \
  --workspace ./ws --phase merge
```

Without this step the tool honestly reports those six as *unevaluated* in `limitations` —
which is correct behaviour, but it means the entire trust-by-corroboration pillar goes
untested on real data.

---

## Phase 3 — Run citeworthy on the same sites ☐

```bash
python skills/cw-audit-conductor/scripts/conduct_audit.py SITE \
  --workspace ./ws --max-pages 14 --markdown --merge-probes
```

For each root cause found by hand:

- ✅ **Caught** — flagged the same issue with reasonable evidence.
- ❌ **Missed** — said nothing, or the check passed. A real gap.
- ⚠️ **False positive** — flagged something manual inspection shows is fine.

**Before logging a ❌, check `limitations` and `checks_run`.** The tool distinguishes
*"checked and clean"* from *"could not check"*. A check listed in `limitations` as
unevaluated is not a miss — it is a gap in your run setup (usually a missing renderer or
skipped Phase 2.5).

---

## Phase 4 — Fix gaps without overfitting ☐

For every ❌ or ⚠️:

1. Write the check as a **pattern**, not a fact about one site.
   Bad: "flag if homepage H1 is 'Unlock your potential'."
   Good: "flag if no page opens with a sentence that both names the brand and states
   what it is."
2. Add it to the finding catalog with an id, severity, and the evidence it requires.
3. **Add a guard clause** — write down the conditions under which it must *not* fire, and
   a minimum sample size. An unguarded check is a future false positive.
4. **Add a regression test to `tests/verify_citeworthy.py`.** Non-negotiable: a bulk
   rename once silently disabled the entrypoint's confounding rule — the marketplace's
   main false-positive control — and *not one test failed*. A fix with no test will rot.
5. **Add a liveness fixture to `tests/probe_check_liveness.py`** so the new check is
   proven able to fire at all.
6. Re-run on the site that inspired the fix, **then on a site you did not calibrate
   against.** If it only works on the first, it is overfit — generalize it.

---

## Phase 5 — Coverage & liveness audit ☑ done, re-run after Phase 4

Real-site testing cannot tell you about a check that never fires — silence is ambiguous
between *"correctly quiet"* and *"broken"*. Two commands settle it:

```bash
python tests/verify_citeworthy.py        # 228 assertions: behaviour + false-positive guards
python tests/probe_check_liveness.py     # every check must fire on a triggering fixture
```

**Current state — 91 checks:**

| Evidence of liveness | Count |
|---|---|
| Fired on a real site across 19 audits | 63 |
| Proven live on a purpose-built fixture | 26 |
| Covered by unit tests | 5 |
| **Total proven able to fire** | **86 / 91 (95%)** |

**The 5 not yet proven** are all crawl-level checks in `harvest_site.py` that need a live
HTTP response, so no offline fixture reaches them. Verify each opportunistically when a
real site exhibits it, or by pointing the tool at a local test server:

| Check | Detects | Cheapest way to confirm |
|---|---|---|
| `REACH-003` | robots.txt syntax errors | The underlying parser *is* unit-tested; only the finding wrapper is unproven |
| `REACH-009` | internally-linked pages returning 4xx/5xx | Raise `--max-pages` on a large site; broken links appear at depth |
| `REACH-010` | `nosnippet` / `max-snippet:0` | Common on paywalled news — try a publisher site |
| `REACH-011` | no HTTPS | Nearly extinct; find any legacy `http://`-only site |
| `REACH-015` | server responses over 2500 ms | Try a small self-hosted or shared-hosting site |

Anything that reports **NOT LIVE** is dead code, not strictness — fix it before shipping.

---

## Phase 6 — Generalization check (do this last) ☐

Pick 3–4 sites you have **never looked at** — not on this list, not used in Phases 1–5.
Run cold, and judge:

- Sensible findings without you interpreting or fixing anything?
- Any *confident* false positives? (a `high`/`critical` at `high` confidence that is wrong
  is far more damaging than a hedged `low`)
- Does the report read clearly to someone who has never seen the tool?
- Does the score match your own gut assessment of the site? If a site you consider healthy
  scores 40, the scoring model is miscalibrated even if each finding is individually true.

Resist tuning against these sites too. Rotate in fresh ones for another round.

---

## Phase 7 — Final polish ☐

- [x] Every finding cites a measured quantity and a check id — enforced by
      `verify_report.py`, which fails closed
- [x] `limitations` is honest about anything not verified; a failed or soft-blocked crawl
      is refused a score entirely
- [x] Runs well under 5 minutes (measured: 7–42 s per site)
- [x] README explains each skill and how the entrypoint composes them
- [ ] Report reads clearly top-to-bottom for a non-technical person — **get one actual
      non-technical person to read `report.md` and tell you what they'd do first**
- [ ] Delete the stale `brand-ai-readiness-audit/` copy (drifted: 89 checks vs 91) —
      it is file-locked; close it in your editor first
- [ ] Final `zip` from a clean tree; confirm no `__pycache__` and exactly one entrypoint

---

## Test-site list

Test **2+ sites per category** — one large/well-resourced, one small/independent.
**Don't assume the small one fails and the large one succeeds** — that assumption is what
Phase 1 tests, not confirms. (Nike scores worse than a Sydney bakery would, because size
buys marketing, not machine-readability.)

Crawlability from Phase 0b is marked: ✅ full signal · 🚫 blocked · ⚠️ soft-blocked.

### E-commerce
- Large: `nike.com` ⚠️ · `patagonia.com` 🚫 · `glossier.com` ✅ · `crateandbarrel.com` 🚫
- Small/independent: `bitetoothpastebits.com` ✅ · `blackstarpastry.com` ✅ ·
  `notebooktherapy.com` ✅ · `goayo.com` ✅
- Already audited: `wearcomet.com`, `snitch.co.in` (the latter blocks all crawlers —
  see Phase 2)
- **Add your own:** a tiny Etsy/Shopify store in a niche you know personally

### SaaS / B2B software
- Large: `stripe.com` ✅ · `notion.so` ✅ · `hubspot.com` ✅ · `helpscout.com` ✅
- Smaller: `sendpotion.com` ✅
- **Add your own:** a small SaaS tool you use that you suspect is invisible to assistants

### Local business / services
- Chains: `chick-fil-a.com` 🚫 · `greatclips.com` 🚫
- Boutique: `thescottresort.com` ✅
- **Add your own — the most valuable category to fill in yourself:** a single-location
  restaurant, dentist, plumber or salon in your city. Low-resource local sites are
  under-represented in every generic list (including this one) and are where
  AI-discoverability problems are most common and most fixable.

### Publisher / content
- Large: `nytimes.com` ✅ · `theverge.com` ✅ · `seriouseats.com` ✅
- Niche: any Substack or personal blog you follow with a small readership
- *Publisher sites are the best place to trip `REACH-010` (nosnippet) — see Phase 5.*

### Nonprofit / .org
- Large: `redcross.org` 🚫 · `doctorswithoutborders.org` ✅ · `wikipedia.org` ✅
- Smaller: `franshalsmuseum.nl` ✅ — good entity-disambiguation test, since "Frans Hals"
  is also a painter's name; expect `ENTITY-008`
- **Add your own:** a small local charity

### Education / institutional
- Large: `harvard.edu` ✅ · `khanacademy.org` ✅ · `coursera.org` ✅
- **Add your own:** a specific university *department* page — usually far worse
  maintained than the main site

### Travel / hospitality
- Large: `marriott.com` 🚫 · `airbnb.com` ✅
- Independent: `thescottresort.com` ✅
- **Add your own:** a small independent tour operator or B&B

### Finance / professional services
- **Add your own:** a local accounting firm, law firm or independent financial adviser —
  almost always thin on structured data, and a reliable source of real findings

---

## Tracking template

| Site | Question | AI answer (correct/wrong/missing) | Model + date + browsing on? | Source cited | Root cause found by hand | citeworthy verdict | **check_id** | Action |
|---|---|---|---|---|---|---|---|---|
| | | | | | | ✅ / ❌ / ⚠️ | | |

The `check_id` column is what makes a row actionable: ✅ records which check earned its
place, ❌ names the check that should have existed, ⚠️ names the check to guard.

---

## Reminders

- **Ground truth first, tool second.** Never trust a finding you have not verified by hand
  at least once per check type.
- **Rotate test sites.** Sites used to build a check must not be the only sites used to
  confirm it works.
- **Misses and false positives are equally penalized** — the rubric says "few misses and
  few false positives".
- **A confident false positive is the worst outcome.** Hedged low-confidence findings
  carry their own caveat; a `critical` that is wrong discredits the whole report.
- **Check `limitations` before logging a miss.** Unevaluated ≠ passed ≠ missed.
- **Every fix gets a test.** Both suites, every time.
- **Read-only throughout.** Passive inspection and public AI assistants only — no login,
  no auth-gated scraping, no site modification.
