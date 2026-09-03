# AI crawler reference

The distinction this file exists to make: **being absent from a training corpus and being
uncitable in a live answer are different problems with different causes.** Conflating them
is the most common error in AI-visibility audits, and it produces both false alarms
(reporting a deliberate `GPTBot` block as critical) and false clears (missing that
`OAI-SearchBot` is blocked while `GPTBot` is allowed).

## Class A — Retrieval agents (blocking these destroys citability)

These fetch pages at answer time, or populate the search indexes that answers are grounded
in. If one is blocked, the brand cannot be cited by that assistant no matter how good its
content is.

| User-agent | Operator | Role |
|---|---|---|
| `OAI-SearchBot` | OpenAI | Builds the ChatGPT search index. Blocking it removes the site from ChatGPT's citable set. |
| `ChatGPT-User` | OpenAI | Fetches a page live when a user or a ChatGPT action requests it. |
| `Claude-User` | Anthropic | Fetches a page live on a user's behalf. |
| `Claude-SearchBot` | Anthropic | Indexes pages to support Claude's search results. |
| `PerplexityBot` | Perplexity | Builds Perplexity's index; citations come from it. |
| `Perplexity-User` | Perplexity | Live user-initiated fetch. |
| `Googlebot` | Google | The web index behind AI Overviews and Gemini grounding. |
| `Bingbot` | Microsoft | The index behind Copilot. |
| `Applebot` | Apple | Feeds Siri and Apple Intelligence surfaces. |
| `DuckAssistBot` | DuckDuckGo | Feeds DuckDuckGo's assistant answers. |
| `MistralAI-User` | Mistral | Live fetch for Le Chat. |

Blocking any of these is reported as `REACH-001`, **critical**.

## Class B — Training-corpus agents (blocking these is a policy choice)

These collect content for future model training. Blocking them affects whether a model
*remembers* the brand from pretraining. It has no effect on live citation.

| User-agent | Operator | Role |
|---|---|---|
| `GPTBot` | OpenAI | Training corpus. |
| `ClaudeBot` | Anthropic | Training corpus. |
| `CCBot` | Common Crawl | Feeds many organisations' training corpora. |
| `Google-Extended` | Google | Not a crawler — a robots token controlling Gemini training/grounding use. |
| `Applebot-Extended` | Apple | Robots token controlling Apple model-training use. |
| `meta-externalagent` | Meta | Training corpus. |
| `Amazonbot` | Amazon | Assistant corpus. |
| `Bytespider` | ByteDance | Training corpus. |

Blocking these is reported as `REACH-002`, **low / informational**, framed as a trade-off to
confirm rather than a defect to fix. Many organisations block them deliberately for
content-licensing reasons, and the audit respects that.

## The recommended posture

For most brands that want to be found and cited:

```
# Answer-time agents: allow. These produce citations.
User-agent: OAI-SearchBot
User-agent: ChatGPT-User
User-agent: Claude-User
User-agent: Claude-SearchBot
User-agent: PerplexityBot
Allow: /
Disallow: /account
Disallow: /checkout
Disallow: /admin

# Training corpora: block only if that is a deliberate policy.
User-agent: GPTBot
User-agent: CCBot
User-agent: Google-Extended
Disallow: /

User-agent: *
Allow: /
Disallow: /account
Disallow: /checkout
Disallow: /admin

Sitemap: https://example.com/sitemap.xml
```

## Robots matching rules the parser implements

Because getting these wrong produces confident wrong answers about who is blocked:

- **Group selection is by longest matching user-agent prefix**, case-insensitive, falling
  back to `*` only when no specific group matches. A site with a `User-agent: *` block and
  a `User-agent: GPTBot` allow does *not* block GPTBot.
- **Within a group, the longest matching path pattern wins.** On a tie, `Allow` beats
  `Disallow`.
- `*` is a wildcard, `$` anchors the end of the path.
- **Consecutive `User-agent` lines with no intervening rule share one group.** This is why
  the parser tracks `seen_rule` — mis-handling it merges groups that should be separate.
- An empty `Disallow:` means allow everything.
- Directives that fail to parse are skipped by real crawlers, so the intended policy is not
  the enforced policy. That is `REACH-003`.

## Two traps worth reporting

1. **`Disallow` does not mean "do not index".** A disallowed page can still be indexed from
   external links — and because the crawler is forbidden from fetching it, it can never
   read a `noindex` on that page. To remove a page from indexes, allow the fetch and serve
   `noindex`. Reported in `REACH-018`'s remediation steps.

2. **`nosnippet` forbids quotation without forbidding indexing.** A page can rank and still
   never be quoted, which looks exactly like an invisibility problem while being a
   permissions problem. That is `REACH-010`, and it is why the check reads both the meta
   robots tag and the `X-Robots-Tag` response header.
