# Judgment rubric — where the agent reads, not the script

Everything else in this marketplace is deterministic: a script measures something and
the number is the evidence. This file covers the one place that approach has a known
ceiling, and hands that part to the agent running the skill.

## Why a rubric exists at all

`QUOTE-001` asks whether the site states, in plain language, what the brand is. The
script answers it by pattern-matching definitional shapes — *"X is a …"*, *"X provides
…"*, *"X builds …"*. That is fast, deterministic and reproducible, and it is **wrong in
both directions**:

- **False negative.** *"Every loaf we bake starts at 4am in our Marrickville kitchen."*
  A reader learns instantly that this is a bakery in Sydney. The regex sees no
  definitional verb and reports the brand as undefined.
- **False positive.** *"Acme is a leading provider of best-in-class solutions."* Matches
  the pattern perfectly and tells a reader nothing at all.

A regex is checking *sentence shape*. The real question is *comprehension*, and the
runtime for these skills is a language model — the one tool that can actually answer it.
Not using it here would be leaving the best instrument in the box.

## When to apply this

Only after the script has run, and only for `QUOTE-001` / `QUOTE-002`. Every other check
stays fully deterministic. This is a deliberate, bounded exception, not a licence to
replace measurement with opinion.

## The three questions

Read the homepage's first screen of body text from the evidence bundle
(`pages/<id>.json` → `raw.first_screen_text`, which already excludes nav and footer
chrome). Then answer, **using only what is on the page** — not prior knowledge, not the
domain name, not what you assume the company does:

1. **What is this?** Can you state in one sentence what the brand actually is or sells?
2. **Who is it for?** Is the audience or use case indicated, even implicitly
   ("for engineering teams", "for small restaurants")?
3. **What do I do next?** Is there an unambiguous next action visible near the top?

**Quote the exact text you based each answer on.** If you cannot quote it, the answer is
no — that is the whole point of the exercise.

## Turning the answers into a verdict

| Outcome | Action |
|---|---|
| All three answerable, and the script also passed | No finding. Record the quoted sentence in `notes`. |
| All three answerable, but the script raised `QUOTE-001` | **Overturn it.** Emit `QUOTE-001-J` at `low` severity noting the definition exists but in a non-standard form, and record the quote. |
| One or two unanswerable | Emit `QUOTE-001-J` at `medium`. |
| None answerable | Emit `QUOTE-001-J` at `high`. If the script also fired, keep the script's `critical` and add your quotes as corroboration. |

## Rules that keep this honest

**Never silently overwrite a script finding.** If you disagree with the script, emit a
separate finding with the `-J` suffix and say plainly that it contradicts the mechanical
result. A reader must be able to see both and judge.

**Every judgment finding carries `"confidence": "medium"` and
`"method": "agent-judgment"`.** It did not come from a measurement and must not look
like one. The report's scoring weights confidence, so a judgment call never outranks a
measured fact.

**Quote or drop it.** A judgment finding with no verbatim quote from the page is
inadmissible. This is the single rule that stops this section becoming a licence to
invent findings.

**Do not re-judge what the script measured well.** Alt-text coverage, heading levels,
status codes, schema validity — the script is better at all of them and does not vary
between runs. Judge comprehension only.

**Determinism note.** Because this step involves a model, two runs may word the finding
differently. That is why it is confined to two checks, always suffixed `-J`, always
`medium` confidence, and always quote-backed. Everything the report scores as a hard
fact still comes from the deterministic path, and the audit remains fully reproducible
with this step skipped.

## Worked example

Given first-screen text:

> "Every loaf we bake starts at 4am in our Marrickville kitchen. Order by Thursday for
> weekend pickup."

- What is this? **Yes** — a bakery. Quote: *"Every loaf we bake starts at 4am."*
- Who is it for? **Yes** — local customers collecting in person. Quote: *"for weekend
  pickup."*
- What next? **Yes** — order by Thursday.

The script raised `QUOTE-001` (no definitional verb). All three questions are answerable,
so emit `QUOTE-001-J` at `low`, note that the definition is present but implicit, and
recommend adding one explicit sentence for machine extraction while keeping the current
opening for humans.
