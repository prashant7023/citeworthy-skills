#!/usr/bin/env python3
"""
harvest_site.py -- Evidence-bundle producer + access-layer auditor.

Stage 1 of the pipeline. Fetches a bounded, robots-respecting sample of a site,
normalises every page into a machine-readable "parsed page" record, and writes a
single evidence bundle that every other skill in the marketplace reads.

Read-only. Never sends POST/PUT/DELETE. Never touches authenticated areas.
Stdlib only; optional Playwright is used for render parity when importable.

Outputs (under --workspace):
  manifest.json                     crawl metadata, robots analysis, sitemap analysis
  pages/<id>.json                   one normalised parsed-page record per fetched URL
  pages/<id>.raw.html               raw server HTML (verbatim, for citable evidence)
  pages/<id>.rendered.html          post-JS DOM when a renderer is available
  findings/crawl-access-audit.json  REACH-* findings
"""
import argparse
import gzip
import hashlib
import html as html_mod
import json
import os
import re
import socket
import ssl
import time
import zlib
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib import request, error, parse
import http.client

# Some large sites send more than http.client's default cap of 100 response headers,
# which would otherwise surface as a fetch error on a perfectly healthy page.
http.client._MAXHEADERS = 1000

UA_SELF = "BrandAIReadinessAudit/1.0 (+read-only site audit; respects robots.txt)"

# Agents whose access decides whether a brand can be *cited* at answer time,
# versus agents that only feed model training. Conflating the two is the most
# common false positive in AI-visibility audits, so they are graded separately.
RETRIEVAL_AGENTS = {
    "OAI-SearchBot": "ChatGPT search index (decides ChatGPT citability)",
    "ChatGPT-User": "ChatGPT live fetch on a user's behalf",
    "Claude-User": "Claude live fetch on a user's behalf",
    "Claude-SearchBot": "Claude search index",
    "PerplexityBot": "Perplexity search index",
    "Perplexity-User": "Perplexity live fetch on a user's behalf",
    "Googlebot": "Google index (feeds AI Overviews / Gemini grounding)",
    "Bingbot": "Bing index (feeds Copilot)",
    "Applebot": "Apple index (feeds Siri / Apple Intelligence)",
    "DuckAssistBot": "DuckDuckGo assistant",
    "MistralAI-User": "Le Chat live fetch",
}
TRAINING_AGENTS = {
    "GPTBot": "OpenAI model-training corpus",
    "ClaudeBot": "Anthropic model-training corpus",
    "CCBot": "Common Crawl (feeds many training corpora)",
    "Google-Extended": "Gemini training / grounding opt-out token",
    "Applebot-Extended": "Apple model-training opt-out token",
    "meta-externalagent": "Meta model-training corpus",
    "Amazonbot": "Amazon assistant corpus",
    "Bytespider": "ByteDance corpus",
}

# robots.txt states a policy; a CDN or WAF can enforce a different one. These are the
# published User-Agent strings of the AI search agents, used for one GET each against
# the homepage. Googlebot and Bingbot are deliberately absent: CDNs routinely verify
# those by reverse DNS and refuse impersonators, so a refusal would prove nothing.
EDGE_PROBE_AGENTS = {
    "OAI-SearchBot": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; "
                     "OAI-SearchBot/1.0; +https://openai.com/searchbot",
    "Claude-SearchBot": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; "
                        "Claude-SearchBot/1.0; +https://www.anthropic.com)",
    "PerplexityBot": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; "
                     "PerplexityBot/1.0; +https://perplexity.ai/perplexitybot)",
}
BROWSER_CONTROL_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
# 429 is excluded: it means "too fast", not "not you".
EDGE_REFUSAL_STATUS = {401, 403, 406, 503}
SNIPPET_LIMIT = re.compile(r"max-snippet\s*:\s*(-?\d+)")

SKIP_EXT = re.compile(
    r"\.(jpg|jpeg|png|gif|webp|avif|svg|ico|css|js|mjs|woff2?|ttf|eot|zip|gz|tar"
    r"|dmg|exe|mp4|webm|mp3|wav|xml|rss|atom)$", re.I)
DOC_EXT = re.compile(r"\.(pdf|docx?|pptx?|xlsx?|csv)$", re.I)
# Authenticated and transactional surfaces are never fetched. They are out of scope
# per the read-only guardrail, and they also pollute the content sample with login
# shells that look like thin pages but carry no public content.
PRIVATE_PATH = re.compile(
    r"/(?:login|signin|sign-in|log-in|register|signup|sign-up|account|profile|dashboard"
    r"|admin|checkout|cart|basket|billing|payment|order|my-?account|auth|oauth|logout"
    r"|password|reset|subscribe/confirm|wp-admin|wp-login)(?:[/?#]|$)", re.I)
PRIVATE_HOST = re.compile(r"^(?:app|dashboard|admin|my|account|portal|secure|login|auth|post|submit|upload)\.", re.I)
PRIORITY_PATH = re.compile(
    r"/(?:about|about-us|company|who-we-are|our-story|contact|contact-us|pricing|plans|faqs?)"
    r"(?:[/.?#]|$)", re.I)


def url_key(url):
    """Identity of a URL for de-duplication: host, path without trailing slash, query."""
    p = parse.urlparse(url or "")
    return (p.netloc.lower(), (p.path or "/").rstrip("/") or "/", p.query)

# Bot-protection challenge pages, by vendor. These return HTTP 200 with a body that
# is an interstitial, not content -- so status codes alone say the crawl succeeded.
# Naming the vendor is what makes the finding actionable: "Cloudflare is challenging
# AI crawlers" points at a specific dashboard, where "the site looks empty" does not.
CHALLENGE_VENDORS = {
    "Cloudflare": {
        "title": ["just a moment", "attention required", "checking your browser",
                  "please wait", "security check"],
        "body": ["cf-browser-verification", "challenges.cloudflare.com", "cf_chl",
                 "cloudflare ray id", "cf-turnstile", "_cf_chl_opt"],
        "headers": ["cf-ray", "cf-mitigated"],
    },
    "Akamai": {
        "title": ["access denied", "reference #"],
        "body": ["_abck", "ak_bmsc", "akamaighost", "_akamaiclientdata",
                 "errors.edgesuite.net"],
        "headers": ["x-akamai-transformed", "akamai-grn"],
    },
    "Imperva/Incapsula": {
        "title": ["request unsuccessful", "incapsula"],
        "body": ["_incapsula_resource", "incap_ses", "visid_incap"],
        "headers": ["x-iinfo", "x-cdn"],
    },
    "DataDome": {
        "title": ["blocked", "verification required"],
        "body": ["datadome", "dd_cookie_test", "captcha-delivery.com"],
        "headers": ["x-datadome", "x-dd-b"],
    },
    "PerimeterX/HUMAN": {
        "title": ["access to this page has been denied"],
        "body": ["_px", "perimeterx", "px-captcha", "human-security"],
        "headers": ["x-px"],
    },
    "AWS WAF": {
        "title": ["request blocked"],
        "body": ["awswaf", "aws-waf-token", "challenge.js"],
        "headers": ["x-amzn-waf-action"],
    },
    "Sucuri": {
        "title": ["sucuri website firewall"],
        "body": ["sucuri_cloudproxy", "cloudproxy"],
        "headers": ["x-sucuri-id"],
    },
}


def detect_challenge(html, headers, title, word_count):
    """Identify a bot-protection interstitial served under a 200 status.

    Requires a content signal (title or body marker) rather than headers alone --
    a huge share of the web sits behind Cloudflare perfectly happily, so the
    presence of `cf-ray` proves nothing on its own. Header hits only corroborate.
    """
    lowered_html = (html or "")[:20000].lower()
    lowered_title = (title or "").lower()
    header_keys = {k.lower() for k in (headers or {})}
    for vendor, sig in CHALLENGE_VENDORS.items():
        title_hit = any(p in lowered_title for p in sig["title"])
        body_hit = any(p in lowered_html for p in sig["body"])
        if not (title_hit or body_hit):
            continue
        # A real page that merely mentions a vendor script is not a challenge:
        # challenge interstitials are content-free.
        if word_count > 250 and not title_hit:
            continue
        header_hit = [h for h in sig["headers"] if h in header_keys]
        evidence = []
        if title_hit:
            evidence.append(f"title matched a {vendor} challenge phrase")
        if body_hit:
            evidence.append(f"body carried a {vendor} challenge marker")
        if header_hit:
            evidence.append(f"headers included {', '.join(header_hit)}")
        return {"vendor": vendor, "signals": evidence, "word_count": word_count}
    return None
# Paths a well-run site SHOULD exclude from crawling: search result permutations,
# faceted filters, user areas, alternate formats, honeypot traps. Blocking these is
# correct practice, so REACH-018 must not scold a site for doing the right thing.
NON_CONTENT_DISALLOW = re.compile(
    r"/(?:search|find|filter|facet|sort|query|browse-?by|user|users|account|login|signin|"
    r"register|signup|form|print|printable|api|feed|feeds|rss|atom|cgi-?bin|admin|tmp|temp|"
    r"test|ignore-?me|trap|honeypot|e-print|src|ps|dvi|cookies?|view|refs|cits|ftp|export|"
    r"download|cart|checkout|compare|wishlist|share|embed|amp|mobile|redirect|out|go)"
    r"(?:[/?#]|$)", re.I)

ISO_DATE = re.compile(r"\b(20\d{2})-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])\b")
LONG_DATE = re.compile(
    r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+([0-3]?\d),?\s+(19|20)\d{2}\b", re.I)
DMY_DATE = re.compile(
    r"\b([0-3]?\d)\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?,?\s+(19|20)\d{2}\b", re.I)
COPYRIGHT = re.compile(
    r"(?:©|&copy;|copyright)\s*(?:\(c\)\s*)?((?:19|20)\d{2})(?:\s*[-–—]\s*((?:19|20)\d{2}))?", re.I)
YEAR_MENTION = re.compile(r"\b(19[89]\d|20[0-4]\d)\b")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
PRICE_RE = re.compile(
    r"(?:[$£€¥₹]|USD|EUR|GBP|INR|AUD|CAD)\s?\d[\d,]*(?:\.\d{2})?"
    r"|\b\d[\d,]*(?:\.\d{2})?\s?(?:USD|EUR|GBP|INR)\b")

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr"}
BLOCK = {"p", "div", "section", "article", "header", "footer", "main", "aside",
         "nav", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr", "td", "th", "br",
         "hr", "blockquote", "pre", "figcaption", "dt", "dd", "form", "label", "option"}


# ----------------------------------------------------------------------- robots

class RobotsPolicy:
    """Per-user-agent robots.txt parser.

    urllib.robotparser collapses agent groups in ways that hide exactly the
    signal we need (which *specific* AI agent is blocked), so we parse groups
    ourselves and keep them addressable by name.
    """

    def __init__(self, text, status, url):
        self.raw, self.status, self.url = text or "", status, url
        self.groups, self.sitemaps, self.crawl_delay = {}, [], {}
        self.parse_errors = []
        self.duplicate_groups = {}      # agent -> [line numbers it was declared on]
        self._parse()

    def _parse(self):
        current, seen_rule = [], False
        # A leading byte-order mark is not part of the first directive; crawlers skip it.
        for lineno, raw_line in enumerate((self.raw or "").lstrip("﻿").splitlines(), 1):
            line = raw_line.split("#", 1)[0].strip()
            if not line:
                continue
            if ":" not in line:
                self.parse_errors.append({"line": lineno, "text": raw_line.strip()[:120],
                                          "reason": "no field:value separator"})
                continue
            field, value = line.split(":", 1)
            field, value = field.strip().lower(), value.strip()
            if field == "user-agent":
                if seen_rule:
                    current, seen_rule = [], False
                agent = value.lower()
                current.append(agent)
                self.groups.setdefault(agent, {"allow": [], "disallow": [], "lines": [],
                                               "rule_lines": {}})
                # A second declaration of the same product token starts a duplicate
                # group. RFC 9309 s2.2.1 and Google both require merging them, which is
                # what setdefault above does -- but it means a rule far down the file can
                # silently override everything above it, so record the declaration lines
                # to make that visible in the evidence.
                if lineno not in self.groups[agent]["lines"]:
                    self.groups[agent]["lines"].append(lineno)
                if len(self.groups[agent]["lines"]) > 1:
                    self.duplicate_groups.setdefault(agent, self.groups[agent]["lines"])
            elif field in ("allow", "disallow"):
                seen_rule = True
                if not current:
                    self.parse_errors.append({"line": lineno, "text": raw_line.strip()[:120],
                                              "reason": "rule before any User-agent group"})
                    continue
                for agent in current:
                    self.groups[agent][field].append(value)
                    self.groups[agent]["rule_lines"].setdefault(
                        (field, value), lineno)
            elif field == "sitemap":
                self.sitemaps.append(value)
            elif field == "crawl-delay":
                try:
                    for agent in current or ["*"]:
                        self.crawl_delay[agent] = float(value)
                except ValueError:
                    self.parse_errors.append({"line": lineno, "text": raw_line.strip()[:120],
                                              "reason": "non-numeric Crawl-delay"})

    @staticmethod
    def _match_len(pattern, path):
        """Google-style wildcard match; returns matched pattern length or -1."""
        if pattern == "":
            return -1
        regex = ["^"]
        for char in pattern:
            if char == "*":
                regex.append(".*")
            elif char == "$":
                regex.append("$")
            else:
                regex.append(re.escape(char))
        try:
            return len(pattern) if re.match("".join(regex), path) else -1
        except re.error:
            return -1

    def group_for(self, agent):
        agent = agent.lower()
        if agent in self.groups:
            return agent, self.groups[agent]
        for name in self.groups:                      # robots matching is prefix-based
            if name != "*" and agent.startswith(name):
                return name, self.groups[name]
        if "*" in self.groups:
            return "*", self.groups["*"]
        return None, None

    def verdict(self, agent, path="/"):
        """Return (allowed, matched_group, matched_rule) for a path."""
        name, group = self.group_for(agent)
        if group is None:
            return True, None, None
        best_allow = max((self._match_len(p, path) for p in group["allow"]), default=-1)
        best_disallow = max((self._match_len(p, path) for p in group["disallow"]), default=-1)
        if best_disallow < 0:
            return True, name, None
        if best_allow >= best_disallow:
            return True, name, "Allow"
        rule = max((p for p in group["disallow"] if self._match_len(p, path) == best_disallow),
                   key=len, default="")
        return False, name, "Disallow: " + (rule or "/")

    def blanket_blocked(self, agent):
        """True when the agent is denied the site root (the citability killer)."""
        allowed, name, rule = self.verdict(agent, "/")
        return (not allowed), name, rule

    def rule_line(self, agent, rule_text):
        """Line number of the Disallow rule that produced a verdict, if known."""
        name, group = self.group_for(agent)
        if not group or not rule_text:
            return None
        pattern = rule_text.split(":", 1)[-1].strip()
        return group.get("rule_lines", {}).get(("disallow", pattern))


# ------------------------------------------------------------------------ fetch

class _Redirects(request.HTTPRedirectHandler):
    def __init__(self):
        self.chain = []

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.chain.append({"from": req.full_url, "to": newurl, "status": code})
        if len(self.chain) > 10:
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url, ua=UA_SELF, timeout=15, max_bytes=3_500_000, method="GET"):
    """Single read-only fetch. Returns a dict; never raises."""
    tracker = _Redirects()
    ctx = ssl.create_default_context()
    opener = request.build_opener(tracker, request.HTTPSHandler(context=ctx))
    req = request.Request(url, method=method, headers={
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
    })
    out = {"url": url, "redirects": tracker.chain, "tls_error": None, "error": None}
    started = time.time()
    body = b""
    try:
        with opener.open(req, timeout=timeout) as resp:
            body = resp.read(max_bytes)
            enc = (resp.headers.get("Content-Encoding") or "").lower()
            if "gzip" in enc:
                try:
                    body = gzip.decompress(body)
                except Exception:
                    pass
            elif "deflate" in enc:
                try:
                    body = zlib.decompress(body, -zlib.MAX_WBITS)
                except Exception:
                    pass
            out.update(status=resp.status, final_url=resp.url, bytes=len(body),
                       headers={k.lower(): v for k, v in resp.headers.items()})
    except error.HTTPError as exc:
        try:
            body = exc.read(max_bytes)
        except Exception:
            body = b""
        out.update(status=exc.code, final_url=url, bytes=len(body),
                   headers={k.lower(): v for k, v in (exc.headers or {}).items()})
    except (ssl.SSLError, ssl.CertificateError) as exc:
        out.update(status=0, final_url=url, bytes=0, headers={}, tls_error=str(exc)[:200])
    except (error.URLError, socket.timeout, ConnectionError, OSError) as exc:
        out.update(status=0, final_url=url, bytes=0, headers={}, error=str(exc)[:200])
    except Exception as exc:                                   # never kill the crawl
        out.update(status=0, final_url=url, bytes=0, headers={}, error=repr(exc)[:200])

    out["elapsed_ms"] = int((time.time() - started) * 1000)
    ctype = out.get("headers", {}).get("content-type", "")
    out["content_type"] = ctype
    charset = "utf-8"
    match = re.search(r"charset=([\w\-]+)", ctype, re.I)
    if match:
        charset = match.group(1)
    try:
        out["text"] = body.decode(charset, errors="replace")
    except (LookupError, UnicodeDecodeError):
        out["text"] = body.decode("utf-8", errors="replace")
    out["declared_charset"] = charset
    out["raw_bytes"] = len(body)
    return out


# ------------------------------------------------------------------------ parse

class PageExtract(HTMLParser):
    """Normalises HTML into the flat record every downstream skill consumes."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title_parts, self.in_title = [], False
        self.meta, self.og, self.twitter = {}, {}, {}
        self.canonical = self.html_lang = self.viewport = None
        self.hreflang, self.links, self.images = [], [], []
        self.scripts, self.stylesheets, self.iframes = [], [], []
        self.jsonld, self.microdata_types, self.rdfa_types = [], [], []
        self.headings, self.sections = [], []
        self.forms, self._form = [], None
        self.text_parts, self.main_text_parts, self.noscript_parts = [], [], []
        self.first_nodes = []
        self.counts = dict.fromkeys(
            ["table", "ul", "ol", "li", "p", "video", "audio", "button", "input",
             "select", "textarea", "dialog", "svg", "picture", "source", "details",
             "img", "a", "script", "iframe", "form", "label", "nav", "figure"], 0)
        self.has_main = self.has_article = self.has_header = self.has_footer = False
        self.has_skip_link = self.body_seen = False
        self.inline_script_bytes = self.inline_style_bytes = 0
        self._skip = 0                                        # inside script/style/svg
        self._in_ld = False
        self._ld_buf = []
        self._in_noscript = 0
        self._main_depth = 0
        self._hidden_depth = 0
        self._chrome_depth = 0
        self._nosnippet_depth = 0
        self.visible_chars = self.nosnippet_chars = 0
        self.paragraph_words, self._paragraph_buf, self._in_paragraph = [], [], False
        self._heading_tag = None
        self._heading_buf = []
        self._cur_section = {"heading": None, "level": 0, "text_parts": []}
        self._stack = []

    # -- helpers ------------------------------------------------------------
    def _push_text(self, data):
        clean = data.strip()
        if not clean:
            return
        if self._in_noscript:
            self.noscript_parts.append(clean)
            return
        if self._hidden_depth == 0:
            self.visible_chars += len(clean)
            if self._nosnippet_depth:
                self.nosnippet_chars += len(clean)
            if self._in_paragraph:
                self._paragraph_buf.append(clean)
        if self._heading_tag:
            self._heading_buf.append(clean)
            return
        self.text_parts.append(clean)
        self._cur_section["text_parts"].append(clean)
        if self._main_depth:
            self.main_text_parts.append(clean)
        # first_nodes approximates "above the fold" content. Navigation and header
        # chrome is excluded: menu labels are identical on every page, so counting
        # them as opening content makes a blank hero look informative.
        if len(self.first_nodes) < 60 and self._hidden_depth == 0 and self._chrome_depth == 0:
            self.first_nodes.append(clean)

    def _close_paragraph(self):
        # Real <p> lengths. Splitting page text on line breaks instead turned layouts
        # built from inline elements into single multi-thousand-word "paragraphs".
        if self._in_paragraph:
            words = len(" ".join(self._paragraph_buf).split())
            if words:
                self.paragraph_words.append(words)
        self._in_paragraph, self._paragraph_buf = False, []

    def _close_section(self):
        text = " ".join(self._cur_section["text_parts"]).strip()
        if self._cur_section["heading"] or text:
            self.sections.append({
                "heading": self._cur_section["heading"],
                "level": self._cur_section["level"],
                "text": text[:6000],
                "word_count": len(text.split()),
            })

    # -- HTMLParser callbacks ----------------------------------------------
    def handle_starttag(self, tag, attrs):
        attr = {k.lower(): (v or "") for k, v in attrs}
        if tag in self.counts:
            self.counts[tag] += 1
        if tag not in VOID:
            self._stack.append(tag)

        if attr.get("aria-hidden") == "true" or "hidden" in attr:
            self._hidden_depth += 1
            self._stack.append("__hidden__")
        if "data-nosnippet" in attr and tag not in VOID:
            self._nosnippet_depth += 1
            self._stack.append("__nosnippet__")
        if tag == "p":
            self._close_paragraph()               # an unclosed <p> ends where the next begins
            self._in_paragraph = True

        if tag in ("script", "style", "svg"):
            if tag == "script" and attr.get("type", "").lower() in (
                    "application/ld+json", "application/json+ld"):
                self._in_ld, self._ld_buf = True, []
                return
            self._skip += 1
            if tag == "script" and attr.get("src"):
                self.scripts.append({"src": attr["src"], "async": "async" in attr,
                                     "defer": "defer" in attr,
                                     "module": attr.get("type") == "module",
                                     "in_head": not self.body_seen})
            return
        if self._skip:
            return

        if tag == "title":
            self.in_title = True
        elif tag == "html":
            self.html_lang = attr.get("lang")
        elif tag == "body":
            self.body_seen = True
        elif tag == "noscript":
            self._in_noscript += 1
        elif tag == "meta":
            name = (attr.get("name") or attr.get("property")
                    or attr.get("http-equiv") or "").lower()
            content = attr.get("content", "")
            if name.startswith("og:"):
                self.og[name] = content
            elif name.startswith("twitter:"):
                self.twitter[name] = content
            elif name:
                self.meta[name] = content
            if name == "viewport":
                self.viewport = content
        elif tag == "link":
            rel = attr.get("rel", "").lower()
            href = attr.get("href", "")
            if "canonical" in rel:
                self.canonical = href
            elif "alternate" in rel and attr.get("hreflang"):
                self.hreflang.append({"hreflang": attr["hreflang"], "href": href})
            elif "stylesheet" in rel:
                self.stylesheets.append({"href": href, "media": attr.get("media", "")})
        elif tag == "a":
            href = attr.get("href", "")
            self.links.append({
                "href": href, "rel": attr.get("rel", ""), "text": "",
                "aria_label": attr.get("aria-label", ""), "target": attr.get("target", ""),
            })
            if href.startswith("#") and "skip" in href.lower():
                self.has_skip_link = True
        elif tag == "img":
            self.images.append({
                "src": attr.get("src") or attr.get("data-src", ""),
                "alt": attr.get("alt"), "width": attr.get("width"),
                "height": attr.get("height"), "loading": attr.get("loading", ""),
                "in_first_screen": len(self.first_nodes) < 25,
            })
        elif tag == "iframe":
            self.iframes.append({"src": attr.get("src", ""), "title": attr.get("title", ""),
                                 "loading": attr.get("loading", "")})
        elif tag in ("nav", "header", "footer"):
            self._chrome_depth += 1
            self._stack.append("__chrome__")
            self.has_header = self.has_header or tag == "header"
            self.has_footer = self.has_footer or tag == "footer"
        elif tag in ("main", "article"):
            self._main_depth += 1
            self.has_main = self.has_main or tag == "main"
            self.has_article = self.has_article or tag == "article"
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            level = int(tag[1])
            if level <= 3:
                self._close_section()
                self._cur_section = {"heading": None, "level": level, "text_parts": []}
            self._heading_tag, self._heading_buf = tag, []
        elif tag == "form":
            self._form = {"action": attr.get("action", ""),
                          "method": (attr.get("method") or "get").lower(),
                          "fields": [], "labels": 0}
        elif tag in ("input", "select", "textarea") and self._form is not None:
            itype = attr.get("type", "text").lower()
            if itype not in ("hidden", "submit", "button", "image"):
                self._form["fields"].append({
                    "type": itype, "name": attr.get("name", ""),
                    "required": "required" in attr,
                    "labelled": bool(attr.get("aria-label") or attr.get("id")
                                     or attr.get("placeholder")),
                })
        elif tag == "label" and self._form is not None:
            self._form["labels"] += 1

        if attr.get("itemtype"):
            self.microdata_types.append(attr["itemtype"])
        if attr.get("typeof"):
            self.rdfa_types.append(attr["typeof"])
        if attr.get("style"):
            self.inline_style_bytes += len(attr["style"])

    def handle_endtag(self, tag):
        if self._in_ld and tag == "script":
            self.jsonld.append("".join(self._ld_buf))
            self._in_ld, self._ld_buf = False, []
            return
        if tag in ("script", "style", "svg") and self._skip:
            self._skip -= 1
            return
        if self._skip:
            return
        if tag == "title":
            self.in_title = False
        elif tag == "noscript" and self._in_noscript:
            self._in_noscript -= 1
        elif tag in ("nav", "header", "footer") and self._chrome_depth:
            self._chrome_depth -= 1
        elif tag in ("main", "article") and self._main_depth:
            self._main_depth -= 1
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6") and self._heading_tag:
            text = " ".join(self._heading_buf).strip()
            self.headings.append({"level": int(self._heading_tag[1]), "text": text[:300]})
            if int(self._heading_tag[1]) <= 3:
                self._cur_section["heading"] = text[:300]
            if len(self.first_nodes) < 60:
                self.first_nodes.append(text)
            self.text_parts.append(text)
            if self._main_depth:
                self.main_text_parts.append(text)
            self._heading_tag, self._heading_buf = None, []
        elif tag == "form" and self._form is not None:
            self.forms.append(self._form)
            self._form = None

        if tag == "p":
            self._close_paragraph()
        while self._stack and self._stack[-1] == "__hidden__":
            self._stack.pop()
            self._hidden_depth = max(0, self._hidden_depth - 1)
        if tag in self._stack:
            while self._stack:
                popped = self._stack.pop()
                if popped == "__hidden__":
                    self._hidden_depth = max(0, self._hidden_depth - 1)
                elif popped == "__chrome__":
                    self._chrome_depth = max(0, self._chrome_depth - 1)
                elif popped == "__nosnippet__":
                    self._nosnippet_depth = max(0, self._nosnippet_depth - 1)
                elif popped == tag:
                    break
        if tag in BLOCK:
            self.text_parts.append("\n")
            self._cur_section["text_parts"].append("\n")

    def handle_data(self, data):
        if self._in_ld:
            self._ld_buf.append(data)
            return
        if self._skip:
            self.inline_script_bytes += len(data)
            return
        if self.in_title:
            self.title_parts.append(data)
            return
        self._push_text(data)

    def close(self):
        super().close()
        self._close_paragraph()
        self._close_section()


def _norm_text(parts):
    text = " ".join(parts)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\s*\n\s*", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def parse_html(html, base_url):
    """HTML string -> normalised parsed-page record."""
    extractor = PageExtract()
    try:
        extractor.feed(html)
        extractor.close()
    except Exception:
        pass                          # tolerate malformed markup; keep what we got

    text = _norm_text(extractor.text_parts)
    main_text = _norm_text(extractor.main_text_parts)

    # attach anchor text (HTMLParser gives tags, not element text)
    anchors = re.findall(r"<a\b[^>]*>(.*?)</a>", html, re.I | re.S)
    for idx, anchor in enumerate(anchors[:len(extractor.links)]):
        stripped = re.sub(r"<[^>]+>", " ", anchor)
        extractor.links[idx]["text"] = re.sub(
            r"\s+", " ", html_mod.unescape(stripped)).strip()[:200]

    for link in extractor.links:
        href = (link.get("href") or "").strip()
        if not href or href.startswith(("javascript:", "mailto:", "tel:", "#")):
            link["abs"] = ""
        else:
            try:
                link["abs"] = parse.urldefrag(parse.urljoin(base_url, href))[0]
            except Exception:
                link["abs"] = ""

    jsonld_parsed, jsonld_errors = [], []
    for idx, blob in enumerate(extractor.jsonld):
        stripped = blob.strip()
        if not stripped:
            jsonld_errors.append({"index": idx, "snippet": "",
                                  "error": "empty <script type=application/ld+json> block"})
            continue
        try:
            jsonld_parsed.append(json.loads(stripped))
        except json.JSONDecodeError as exc:
            jsonld_errors.append({
                "index": idx,
                "error": f"{exc.msg} at line {exc.lineno} col {exc.colno}",
                "snippet": stripped[max(0, exc.pos - 60):exc.pos + 60]})

    dates = sorted({m.group(0) for m in ISO_DATE.finditer(text)}
                   | {m.group(0) for m in LONG_DATE.finditer(text)}
                   | {m.group(0) for m in DMY_DATE.finditer(text)})
    copyright_years = [int(m.group(2) or m.group(1)) for m in COPYRIGHT.finditer(html)]

    return {
        "title": _norm_text(extractor.title_parts)[:400],
        "lang": extractor.html_lang,
        "canonical": parse.urljoin(base_url, extractor.canonical) if extractor.canonical else None,
        "meta": extractor.meta,
        "og": extractor.og,
        "twitter": extractor.twitter,
        "viewport": extractor.viewport,
        "robots_meta": extractor.meta.get("robots", ""),
        "hreflang": extractor.hreflang,
        "headings": extractor.headings,
        "sections": extractor.sections[:80],
        "text": text[:400_000],
        "word_count": len(text.split()),
        "main_text": main_text[:200_000],
        "main_word_count": len(main_text.split()),
        "first_screen_text": _norm_text(extractor.first_nodes)[:4000],
        "noscript_text": _norm_text(extractor.noscript_parts)[:4000],
        "visible_text_chars": extractor.visible_chars,
        "data_nosnippet_chars": extractor.nosnippet_chars,
        "paragraph_word_counts": extractor.paragraph_words[:400],
        "links": extractor.links[:600],
        "images": extractor.images[:300],
        "iframes": extractor.iframes[:60],
        "scripts": extractor.scripts[:200],
        "stylesheets": extractor.stylesheets[:60],
        "forms": extractor.forms[:30],
        "jsonld_raw_count": len(extractor.jsonld),
        "jsonld": jsonld_parsed,
        "jsonld_errors": jsonld_errors,
        "microdata_types": sorted(set(extractor.microdata_types))[:60],
        "rdfa_types": sorted(set(extractor.rdfa_types))[:60],
        "counts": extractor.counts,
        "has_main": extractor.has_main,
        "has_article": extractor.has_article,
        "has_header": extractor.has_header,
        "has_footer": extractor.has_footer,
        "has_skip_link": extractor.has_skip_link,
        "inline_script_bytes": extractor.inline_script_bytes,
        "inline_style_bytes": extractor.inline_style_bytes,
        "dates_in_text": dates[:60],
        "copyright_years": sorted(set(copyright_years)),
        "years_mentioned": sorted({int(y) for y in YEAR_MENTION.findall(text)})[-20:],
        "emails": sorted(set(EMAIL_RE.findall(text)))[:20],
        "prices_in_text": sorted({m.group(0).strip() for m in PRICE_RE.finditer(text)})[:40],
        "html_bytes": len(html.encode("utf-8", errors="ignore")),
        "text_to_html_ratio": round(len(text) / max(1, len(html)), 4),
        "main_text_ratio": round(len(main_text) / max(1, len(text)), 4) if text else 0.0,
    }


# ------------------------------------------------------------------------ crawl

def page_id(url):
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


def registrable(host):
    """Coarse eTLD+1. Keeps the crawl on-site without a public-suffix dependency."""
    host = (host or "").lower()
    if host.startswith("www."):
        host = host[4:]
    parts = host.split(".")
    if len(parts) <= 2:
        return host
    two = {"co", "com", "org", "net", "gov", "edu", "ac", "or", "ne", "go"}
    if len(parts) >= 3 and parts[-2] in two and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def brand_key_variants(host):
    """Name-bearing labels of a host, used to recognise the brand's own other domains.

    'www.icici.bank.in' -> {'icicibank', 'bank'}; 'www.bbc.co.uk' -> {'bbc'}. Short
    trailing labels (com, co, uk, in) are dropped, and every suffix of what remains is a
    candidate, so a subdomain such as 'shop.nike.in' still yields 'nike'.
    """
    labels = [l for l in (host or "").lower().split(":")[0].split(".") if l and l != "www"]
    while len(labels) > 1 and len(labels[-1]) <= 3:
        labels.pop()
    return {"".join(labels[i:]) for i in range(len(labels))}


def same_brand(site_root, host):
    site = max(brand_key_variants(site_root), key=len, default="")
    return len(site) >= 3 and site in brand_key_variants(host)


def classify(url, parsed):
    """Deterministic page-type inference used by the schema + engagement skills.

    URL signals first (cheap, stable), on-page signals as a tiebreak. Returns a
    primary label plus every label that matched, so downstream checks can stay
    conservative when a page is genuinely ambiguous.
    """
    path = (parse.urlparse(url).path or "/").lower().rstrip("/")
    text = (parsed.get("text") or "")[:8000].lower()
    heads = " ".join(h["text"] for h in parsed.get("headings", []))[:1500].lower()
    hits = []

    def add(label, condition):
        if condition:
            hits.append(label)

    add("homepage", path in ("", "/index.html", "/home"))

    # A product DETAIL page has exactly one dominant product. Storefront platforms put
    # a cart drawer, price and "add to cart" into the global template, so those signals
    # alone fire on every page of a Shopify/Woo site -- including /pages/about-us.
    # A URL match is authoritative; the on-page heuristic must be narrow enough not to
    # swallow category grids and static pages.
    listing_url = bool(re.search(
        r"/(category|categories|collections?|catalog|tag|tags|archive|search|shop|store|"
        r"all|new-in|sale|brands?)(?:[/?#]|$)", path + "/"))
    static_page_url = bool(re.search(r"/(pages|page|info|help|policies)/", path + "/"))
    product_url = bool(re.search(r"/(products?|item|dp|sku|p)/", path + "/"))
    distinct_prices = len(parsed.get("prices_in_text") or [])
    single_h1 = sum(1 for h in parsed.get("headings", []) if h["level"] == 1) == 1
    product_heuristic = (
        not listing_url and not static_page_url
        and distinct_prices > 0
        # a grid shows many prices; a detail page shows the item's own few
        and distinct_prices <= 4
        and single_h1
        and bool(re.search(r"add to (cart|bag|basket)|buy now", text)))
    add("product", product_url or product_heuristic)
    add("pricing", bool(re.search(r"/(pricing|plans?|price|subscribe)\b", path))
        or ("pricing" in heads and bool(parsed.get("prices_in_text"))))
    add("article", bool(re.search(r"/(blog|news|article|post|insights?|stories|resources)/", path + "/"))
        or bool(re.search(r"\b(posted|published) on\b", text)))
    # An FAQ *block* is not an FAQ *page*. Storefronts routinely append a few
    # question-shaped headings to a category page for SEO; treating that as the page's
    # primary purpose mislabels the whole catalogue. A listing URL is authoritative
    # about what the page is for, so the heuristic yields to it -- but "faq" still
    # lands in page_tags, so the embedded block can still be recommended for markup.
    # News fronts are full of question-shaped headlines, so a count alone labels a
    # section page an FAQ. Questions must also dominate the page's section headings.
    question_heads = sum(1 for h in parsed.get("headings", []) if h["text"].strip().endswith("?"))
    section_heads = sum(1 for h in parsed.get("headings", []) if h["level"] in (2, 3))
    add("faq", bool(re.search(r"/(faq|faqs|help|support|questions)\b", path))
        or (not listing_url and question_heads >= 3
            and question_heads >= 0.4 * max(1, section_heads)))
    add("about", bool(re.search(r"/(about|about-us|company|who-we-are|our-story|team)\b", path)))
    add("contact", bool(re.search(r"/(contact|contact-us|get-in-touch|locations?|find-us)\b", path)))
    add("docs", bool(re.search(r"/(docs?|documentation|guide|api|reference|manual)/", path + "/")))
    add("legal", bool(re.search(r"/(privacy|terms|legal|cookie|gdpr|policy|policies)\b", path)))
    add("careers", bool(re.search(r"/(careers?|jobs?|hiring|vacanc)", path)))
    # A link-heavy page is only a listing if it is also listing SHAPED. Mega-menus put
    # 120+ links on every page of a storefront, so link count alone labels the contact
    # page a category grid.
    add("listing", listing_url
        or (len([l for l in parsed.get("links", []) if l.get("abs")]) > 120
            and distinct_prices >= 5))
    add("local", bool(re.search(r"\b(open|opening) (hours|times)\b|monday\s*[-–]\s*friday", text)))

    order = ["homepage", "pricing", "product", "faq", "docs", "article", "about",
             "contact", "careers", "legal", "listing", "local"]
    primary = next((label for label in order if label in hits), "generic")
    return primary, sorted(set(hits))


def try_render(urls, timeout_ms=15000):
    """Render pages with Playwright when installed.

    Absence is not an error: it downgrades render findings to heuristic
    confidence rather than letting the audit guess.
    """
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        return {}, {"available": False,
                    "reason": f"playwright not importable ({type(exc).__name__})"}
    out = {}
    try:
        with sync_playwright() as play:
            browser = play.chromium.launch(args=["--disable-dev-shm-usage"])
            ctx = browser.new_context(user_agent=UA_SELF,
                                      viewport={"width": 1280, "height": 900})
            for url in urls:
                page = ctx.new_page()
                try:
                    page.goto(url, wait_until="networkidle", timeout=timeout_ms)
                    page.wait_for_timeout(600)
                    out[url] = page.content()
                except Exception:
                    pass
                finally:
                    page.close()
            browser.close()
    except Exception as exc:
        return out, {"available": bool(out), "reason": f"render aborted: {type(exc).__name__}"}
    return out, {"available": True, "reason": "playwright chromium", "engine": "chromium"}


def parse_sitemap(text):
    """Return (urls, lastmods, child_sitemaps) from a sitemap or sitemap index."""
    locs = re.findall(r"<loc>\s*([^<]+?)\s*</loc>", text, re.I)
    lastmods = re.findall(r"<lastmod>\s*([^<]+?)\s*</lastmod>", text, re.I)
    is_index = bool(re.search(r"<sitemapindex", text, re.I))
    return ([] if is_index else locs), lastmods, (locs if is_index else [])


def main():
    ap = argparse.ArgumentParser(description="Evidence-bundle crawler + access auditor")
    ap.add_argument("url")
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--max-pages", type=int, default=25)
    ap.add_argument("--max-depth", type=int, default=3)
    ap.add_argument("--budget-seconds", type=int, default=180)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--delay-ms", type=int, default=250)
    ap.add_argument("--render", choices=["auto", "off"], default="auto")
    ap.add_argument("--render-pages", type=int, default=5)
    ap.add_argument("--ignore-robots", action="store_true",
                    help="Never use on third-party sites; present only for auditing your own site.")
    args = ap.parse_args()

    started_at = datetime.now(timezone.utc)
    started_clock = time.time()
    deadline = started_clock + args.budget_seconds
    start_url = args.url if "://" in args.url else "https://" + args.url

    # Resolve redirects before deriving the host. Auditing "example.com" when the
    # canonical host is "www.example.com" would otherwise probe robots.txt,
    # sitemap.xml and llms.txt on the wrong hostname and mis-report every result.
    landing = fetch(start_url, timeout=15)
    if landing.get("status") == 200 and landing.get("final_url"):
        final = parse.urlparse(landing["final_url"])
        if final.netloc and registrable(final.netloc) == registrable(
                parse.urlparse(start_url).netloc):
            start_url = f"{final.scheme}://{final.netloc}/"

    origin = parse.urlparse(start_url)
    site_host = origin.netloc
    site_root = registrable(site_host)
    workspace = os.path.abspath(args.workspace)
    os.makedirs(os.path.join(workspace, "pages"), exist_ok=True)
    os.makedirs(os.path.join(workspace, "findings"), exist_ok=True)

    findings, notes = [], []

    def finding(check_id, title, severity, evidence, action, priority=None, **extra):
        item = {"check_id": check_id, "title": title, "severity": severity,
                "evidence": evidence, "pillar": "access", "confidence": "high",
                "suggested_action": {"summary": action["summary"],
                                     "priority": priority or severity,
                                     "steps": action.get("steps", []),
                                     "effort": action.get("effort", "medium"),
                                     "owner": action.get("owner", "web/dev")}}
        item.update(extra)
        findings.append(item)

    # ---- scheme / TLS -----------------------------------------------------
    scheme_note = {}
    if origin.scheme == "http":
        https_probe = fetch("https://" + site_host + "/", timeout=10)
        scheme_note = {"https_status": https_probe.get("status")}
        if not https_probe.get("status") or https_probe.get("tls_error"):
            finding("REACH-011", "Site is not reachable over HTTPS", "critical",
                    f"Requested https://{site_host}/ -> status={https_probe.get('status')} "
                    f"tls_error={https_probe.get('tls_error') or https_probe.get('error')}. "
                    "Retrieval agents and search crawlers de-prioritise or refuse plain-HTTP origins.",
                    {"summary": "Serve the whole site over HTTPS with a valid certificate and 301 all HTTP URLs to it.",
                     "steps": ["Install a valid TLS certificate (e.g. Let's Encrypt) on the origin/CDN.",
                               "301-redirect every http:// URL to its https:// equivalent.",
                               "Enable HSTS once the redirects are verified.",
                               "Update sitemap.xml and rel=canonical to https:// URLs."],
                     "effort": "medium", "owner": "infrastructure"})
            start_url = "http://" + site_host + "/"
        else:
            start_url = "https://" + site_host + "/"
    scheme = parse.urlparse(start_url).scheme

    # ---- robots.txt -------------------------------------------------------
    robots_url = f"{scheme}://{site_host}/robots.txt"
    robots_resp = fetch(robots_url, timeout=10)
    robots_text = robots_resp.get("text", "") if robots_resp.get("status") == 200 else ""
    if robots_resp.get("status") == 200 and "<html" in robots_text[:400].lower():
        robots_text = ""
        notes.append("robots.txt returned an HTML document (likely a soft-404); treated as absent.")
    policy = RobotsPolicy(robots_text, robots_resp.get("status"), robots_url)

    # If robots.txt got no response at all, check the homepage before spending the rest
    # of the budget. A site that answers this client with nothing (timeouts, dropped
    # connections) cannot be audited, and probing sitemaps, hosts and edge agents against
    # it only burns minutes of the five-minute limit.
    unreachable, early_home = False, None
    if not robots_resp.get("status"):
        early_home = fetch(start_url, timeout=10)
        unreachable = not early_home.get("status")
        if unreachable:
            notes.append(f"{start_url} and robots.txt both returned no response to this client "
                         f"({early_home.get('error') or early_home.get('tls_error') or 'no response'}). "
                         "Sitemap, host, edge and page checks were skipped: nothing can be evaluated "
                         "until the site answers.")

    blocked_retrieval, blocked_training = [], []
    for agent, purpose in RETRIEVAL_AGENTS.items():
        blocked, group, rule = policy.blanket_blocked(agent)
        if blocked:
            blocked_retrieval.append({"agent": agent, "purpose": purpose,
                                      "matched_group": group, "rule": rule})
    for agent, purpose in TRAINING_AGENTS.items():
        blocked, group, rule = policy.blanket_blocked(agent)
        if blocked:
            blocked_training.append({"agent": agent, "purpose": purpose,
                                     "matched_group": group, "rule": rule})

    if robots_resp.get("status") == 200 and blocked_retrieval:
        listed = "; ".join(f"{b['agent']} (via User-agent: {b['matched_group']} -> {b['rule']}) "
                           f"= {b['purpose']}" for b in blocked_retrieval)
        # Cite the exact line that blocks, and explain a merged duplicate group when
        # that is the cause. Without this, a reader who checks with a naive validator
        # (Python's stdlib robotparser stops at the first matching group instead of
        # merging) sees "allowed" and wrongly concludes the audit is wrong.
        first = blocked_retrieval[0]
        line_no = policy.rule_line(first["agent"], first["rule"])
        where = f" The blocking rule is at line {line_no} of robots.txt." if line_no else ""
        dup = policy.duplicate_groups.get((first["matched_group"] or "").lower())
        merge_note = ""
        if dup and len(dup) > 1:
            merge_note = (
                f" NOTE: `User-agent: {first['matched_group']}` is declared {len(dup)} times "
                f"(lines {', '.join(str(n) for n in dup)}). RFC 9309 section 2.2.1 and Google's "
                "implementation both merge duplicate groups for the same product token, so the "
                "rules combine and the blanket Disallow wins over the specific rules above it. "
                "This is almost always accidental -- a blanket block appended to an existing "
                "file. Naive validators that stop at the first matching group (including Python's "
                "stdlib robotparser) will report this site as crawlable; the crawlers that "
                "matter do not.")
        finding("REACH-001", "robots.txt blocks answer-time retrieval agents from the whole site",
                "critical",
                f"GET {robots_url} -> 200. Root path '/' is disallowed for: {listed}.{where}"
                f"{merge_note} These agents fetch pages at answer time; while they are blocked the "
                "site cannot be quoted or cited, no matter how good its content is.",
                {"summary": "Allow the retrieval/search agents (they produce citations) while keeping "
                            "any training-corpus opt-out you want.",
                 "steps": ["FIRST, if the report above notes a duplicated User-agent group: delete "
                           "the redundant group and its blanket `Disallow: /`, keeping one group "
                           "per product token. That single edit usually resolves this finding.",
                           "Add explicit Allow groups for OAI-SearchBot, ChatGPT-User, Claude-User, "
                           "Claude-SearchBot and PerplexityBot.",
                           "Keep Disallow groups for training-only agents (GPTBot, CCBot, "
                           "Google-Extended) if opting out of training is a deliberate policy.",
                           "Re-fetch robots.txt and confirm each agent's verdict on '/' is Allow.",
                           "Leave genuinely private paths (/admin, /cart, /account) disallowed for all."],
                 "effort": "low", "owner": "web/dev"},
                blocked_agents=[b["agent"] for b in blocked_retrieval])
    elif robots_resp.get("status") == 200 and blocked_training:
        listed = ", ".join(b["agent"] for b in blocked_training)
        finding("REACH-002", "Training-corpus crawlers are blocked (deliberate trade-off, not a defect)",
                "low",
                f"robots.txt disallows '/' for: {listed}. Answer-time retrieval agents are NOT blocked, "
                "so live citation still works. This only removes the brand from future training corpora.",
                {"summary": "Confirm this is intentional; if brand recall inside models matters more "
                            "than content control, narrow the block to sensitive paths only.",
                 "steps": ["Decide policy: opt out of training vs. maximise long-term model recall.",
                           "If recall matters, restrict Disallow to /account, /checkout and similar "
                           "rather than '/'.",
                           "Document the decision so it is not silently reverted."],
                 "effort": "low", "owner": "marketing/legal"},
                informational=True)

    if robots_resp.get("status") == 200 and policy.duplicate_groups and not blocked_retrieval:
        listed = "; ".join(f"`User-agent: {a}` on lines {', '.join(str(n) for n in lines)}"
                           for a, lines in list(policy.duplicate_groups.items())[:3])
        finding("REACH-020", "robots.txt declares the same user-agent in more than one group",
                "medium",
                f"{len(policy.duplicate_groups)} user-agent token(s) are declared in multiple "
                f"groups: {listed}. RFC 9309 requires crawlers to merge duplicate groups, so rules "
                "far apart in the file combine in ways that are easy to misread -- and a blanket "
                "rule appended later silently overrides the specific rules above it. Nothing is "
                "blocked site-wide right now, but the file is one careless append away from it.",
                {"summary": "Consolidate each user-agent into exactly one group.",
                 "steps": ["Merge the duplicate groups by hand so each product token appears once.",
                           "Re-read the merged result and confirm the effective policy is intended.",
                           "Keep the file short enough to audit visually; generate it from a "
                           "template rather than appending to it over time."],
                 "effort": "low", "owner": "web/dev"})

    if robots_resp.get("status") == 200 and policy.parse_errors:
        sample = "; ".join(f"line {e['line']}: {e['reason']} ({e['text']})"
                           for e in policy.parse_errors[:4])
        finding("REACH-003", "robots.txt contains syntax errors", "medium",
                f"{len(policy.parse_errors)} malformed directive(s) in {robots_url}: {sample}. "
                "Crawlers skip lines they cannot parse, so the intended policy is not the enforced policy.",
                {"summary": "Fix the malformed robots.txt directives so the intended rules actually apply.",
                 "steps": ["Correct each flagged line to `Field: value` form.",
                           "Ensure every Allow/Disallow sits under a preceding User-agent line.",
                           "Re-validate and confirm each agent's verdict after editing."],
                 "effort": "low", "owner": "web/dev"})
    elif robots_resp.get("status") == 404:
        notes.append("No robots.txt (404). Not a defect -- absence means 'crawl everything' -- but no "
                     "Sitemap directive is advertised to crawlers either.")
    elif robots_resp.get("status") and 400 <= robots_resp["status"] < 500:
        # RFC 9309 section 2.3.1.3: a 4xx on robots.txt means "unavailable", and crawlers
        # may then access the site without restriction. That is not a block.
        notes.append(f"robots.txt returned {robots_resp['status']}. Under RFC 9309 a 4xx means no "
                     "rules apply, so crawlers may fetch the whole site; not scored as a defect.")
    elif robots_resp.get("status") not in (200, 404):
        finding("REACH-004", "robots.txt is unreachable or returns an error", "high",
                f"GET {robots_url} -> status={robots_resp.get('status')} "
                f"error={robots_resp.get('error') or robots_resp.get('tls_error')}. Several crawlers "
                "treat a 5xx/timeout on robots.txt as 'disallow everything' and stop.",
                {"summary": "Make /robots.txt return 200 with a valid body (or a clean 404).",
                 "steps": ["Serve the path statically, never behind auth, WAF or a redirect loop.",
                           "Return 200 + text/plain, or 404 if you intend no rules -- never 5xx.",
                           "Add a Sitemap: line pointing at the XML sitemap."],
                 "effort": "low", "owner": "infrastructure"})

    # ---- llms.txt (recorded, never scored) ---------------------------------
    # No major AI search crawler documents reading llms.txt, so its absence is not a
    # visibility defect. The status is kept in the manifest for anyone who wants it.
    llms_resp = {"status": None} if unreachable else fetch(f"{scheme}://{site_host}/llms.txt", timeout=8)
    has_llms = (llms_resp.get("status") == 200
                and "<html" not in llms_resp.get("text", "")[:300].lower())

    # ---- sitemaps ---------------------------------------------------------
    sitemap_candidates = list(dict.fromkeys(
        policy.sitemaps + [f"{scheme}://{site_host}/sitemap.xml",
                           f"{scheme}://{site_host}/sitemap_index.xml"]))
    sitemap_urls, sitemap_lastmods, sitemap_reports = [], [], []
    for sm_url in ([] if unreachable else sitemap_candidates[:4]):
        resp = fetch(sm_url, timeout=12)
        ok = resp.get("status") == 200 and "<" in resp.get("text", "")[:200]
        report = {"url": sm_url, "status": resp.get("status"), "ok": ok,
                  "declared_in_robots": sm_url in policy.sitemaps}
        if ok:
            locs, lastmods, children = parse_sitemap(resp["text"])
            for child in children[:3]:
                child_resp = fetch(child, timeout=12)
                if child_resp.get("status") == 200:
                    c_locs, c_lastmods, _ = parse_sitemap(child_resp["text"])
                    locs += c_locs
                    lastmods += c_lastmods
            report.update(url_count=len(locs), lastmod_count=len(lastmods),
                          child_sitemaps=len(children))
            sitemap_urls += locs
            sitemap_lastmods += lastmods
        sitemap_reports.append(report)
        if ok:
            break

    sitemap_found = any(r["ok"] for r in sitemap_reports)
    # Only a definitive answer proves absence: 404/410, or a 200 that is not XML. A 403,
    # 406, 429, 5xx or timeout says this client was refused, not that no sitemap exists.
    sitemap_absent = bool(sitemap_reports) and all(
        r["status"] in (404, 410) or (r["status"] == 200 and not r["ok"]) for r in sitemap_reports)
    if not sitemap_found and not sitemap_absent and sitemap_reports:
        notes.append("Sitemap not evaluated: " + ", ".join(f"{r['url']} -> {r['status']}"
                                                         for r in sitemap_reports) +
                     ". These responses refuse this client rather than prove the sitemap is "
                     "missing, so REACH-005 is not scored.")
    if not sitemap_found and sitemap_absent:
        finding("REACH-005", "No usable XML sitemap", "medium",
                "Probed " + ", ".join(f"{r['url']} -> {r['status']}" for r in sitemap_reports) +
                ". Without a sitemap, crawlers discover pages only by following links, so weakly-linked "
                "pages (new posts, deep product pages) may never be fetched.",
                {"summary": "Publish an XML sitemap with accurate <lastmod> values and reference it "
                            "from robots.txt.",
                 "steps": ["Generate sitemap.xml covering every canonical, indexable URL.",
                           "Set <lastmod> from real content-modification time, not build time.",
                           "Add `Sitemap: https://<host>/sitemap.xml` to robots.txt.",
                           "Submit it in Google Search Console and Bing Webmaster Tools."],
                 "effort": "low", "owner": "web/dev"})
    elif sitemap_found and not policy.sitemaps and robots_resp.get("status") == 200:
        found = next(r["url"] for r in sitemap_reports if r["ok"])
        finding("REACH-006", "Sitemap exists but is not advertised in robots.txt", "low",
                f"A sitemap responded at {found} but robots.txt contains no `Sitemap:` directive, so "
                "crawlers that do not guess the conventional path may never find it.",
                {"summary": "Add a `Sitemap:` line to robots.txt.",
                 "steps": ["Append `Sitemap: <absolute sitemap URL>` to robots.txt.",
                           "Use the absolute HTTPS URL on the canonical host."],
                 "effort": "low", "owner": "web/dev"})

    # ---- host canonicalisation -------------------------------------------
    apex = site_host[4:] if site_host.startswith("www.") else site_host
    alt_host = apex if site_host.startswith("www.") else "www." + apex
    alt = {} if unreachable else fetch(f"{scheme}://{alt_host}/", timeout=10)
    prim = early_home if unreachable else fetch(start_url, timeout=15)
    if alt.get("status") == 200 and prim.get("status") == 200:
        alt_final_host = parse.urlparse(alt.get("final_url", "")).netloc
        prim_final_host = parse.urlparse(prim.get("final_url", "")).netloc
        if alt_final_host != prim_final_host and registrable(alt_final_host) == site_root:
            finding("REACH-007",
                    "Both www and apex hosts serve 200 without redirecting to one canonical host",
                    "medium",
                    f"{scheme}://{site_host}/ -> 200 and {scheme}://{alt_host}/ -> 200, and neither "
                    "redirects to the other. Duplicate hosts split link signals and let assistants "
                    "cite an inconsistent URL for the same brand.",
                    {"summary": "Pick one canonical host and 301-redirect the other to it.",
                     "steps": [f"Choose the canonical host (currently referenced as {site_host}).",
                               "301-redirect all URLs on the other host to the same path on it.",
                               "Make rel=canonical, sitemap URLs and internal links all use it."],
                     "effort": "low", "owner": "infrastructure"})

    # ---- edge access probe ------------------------------------------------
    # A site can allow OAI-SearchBot in robots.txt while its CDN refuses it -- the most
    # common way a brand vanishes from AI answers without anyone having decided so.
    # One GET per identity to the homepage, short timeout, no retries, and only for
    # agents robots.txt allows (a robots block is already REACH-001). A browser-UA
    # control request separates "refuses AI agents" from "refuses everyone"
    # (paywall, geo-block, outage).
    def page_signals(resp):
        html = resp.get("text", "") or ""
        title_match = re.search(r"<title[^>]*>(.*?)</title>", html[:20000], re.I | re.S)
        body = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
        words = len(re.sub(r"<[^>]+>", " ", body).split())
        title = title_match.group(1).strip() if title_match else ""
        return title, words, detect_challenge(html, resp.get("headers", {}), title, words)

    edge_probe = {"url": start_url, "control": None, "agents": [], "refused": []}
    home_path = parse.urlparse(start_url).path or "/"
    probe_agents = [] if unreachable else [a for a in EDGE_PROBE_AGENTS
                                            if policy.verdict(a, home_path)[0]]
    if probe_agents:
        control = fetch(start_url, ua=BROWSER_CONTROL_UA, timeout=8, max_bytes=400_000)
        _, control_words, control_challenge = page_signals(control)
        control_ok = control.get("status") == 200 and not control_challenge
        edge_probe["control"] = {"status": control.get("status"), "words": control_words,
                                 "challenge": bool(control_challenge)}
        if control_ok:
            with ThreadPoolExecutor(max_workers=len(probe_agents)) as edge_pool:
                responses = list(edge_pool.map(
                    lambda a: fetch(start_url, ua=EDGE_PROBE_AGENTS[a], timeout=8,
                                    max_bytes=400_000), probe_agents))
            for agent, resp in zip(probe_agents, responses):
                status = resp.get("status")
                _, words, challenge = page_signals(resp)
                if status in EDGE_REFUSAL_STATUS:
                    outcome = f"HTTP {status}"
                elif status == 200 and challenge:
                    outcome = f"HTTP 200 carrying a {challenge['vendor']} challenge page"
                elif status == 200 and words < 60 <= control_words // 5:
                    outcome = f"HTTP 200 with only {words} words (browser received {control_words})"
                else:
                    outcome = None
                edge_probe["agents"].append({"agent": agent, "status": status, "words": words,
                                             "refused": bool(outcome)})
                if outcome:
                    edge_probe["refused"].append({"agent": agent, "outcome": outcome})
                elif not status:
                    notes.append(f"Edge probe: {agent} request got no response "
                                 f"({resp.get('error') or 'timeout'}); not counted as a refusal.")
        else:
            notes.append(
                f"Edge probe skipped: a browser User-Agent was also refused ({start_url} -> "
                f"{control.get('status')}{', challenge page' if control_challenge else ''}), so any "
                "refusal of AI agents would reflect a paywall, geo-block or blanket bot rule rather "
                "than an AI-specific policy.")

    if edge_probe["refused"]:
        allowed_note = ", ".join(r["agent"] for r in edge_probe["refused"])
        detail = "; ".join(f"{r['agent']} -> {r['outcome']}" for r in edge_probe["refused"])
        finding("REACH-023", "The server refuses AI search agents that robots.txt allows", "high",
                f"GET {start_url} with a browser User-Agent -> {edge_probe['control']['status']} "
                f"({edge_probe['control']['words']} words). The same URL requested with the published "
                f"User-Agent of each AI search agent: {detail}. robots.txt allows "
                f"{len(edge_probe['refused'])}/{len(probe_agents)} of these agents on "
                f"'{home_path}', so the refusal comes from the server, CDN or firewall, not from "
                "policy. One request per identity, no retries. Caveat: some CDNs admit the genuine "
                "crawlers by verifying their published IP ranges and refuse only impersonators; "
                "this probe cannot originate from those ranges, so confirm before changing rules.",
                {"summary": "Allow the AI search agents at the CDN/WAF layer so the robots.txt policy "
                            "is what actually applies.",
                 "steps": [f"Check the bot-management rules in your CDN or firewall for anything that "
                           f"matches {allowed_note} (managed 'AI bot' or 'AI crawler' blocks, "
                           "User-Agent deny lists, bot-score thresholds).",
                           "Allow the verified search and user-fetch agents; keep a training-crawler "
                           "block there if that is your policy.",
                           "Confirm in server or CDN logs that requests from each agent's published "
                           "IP ranges now receive 200.",
                           "Re-run this audit to verify."],
                 "effort": "low", "owner": "infrastructure"},
                confidence="medium", refused_agents=[r["agent"] for r in edge_probe["refused"]])

    # ---- BFS crawl --------------------------------------------------------
    seed_urls = []
    for url in sitemap_urls:
        parsed_seed = parse.urlparse(url)
        if registrable(parsed_seed.netloc) == site_root and not SKIP_EXT.search(url)                 and not PRIVATE_PATH.search(parsed_seed.path or "")                 and not PRIVATE_HOST.match(parsed_seed.netloc or ""):
            seed_urls.append(url)
        if len(seed_urls) > 400:
            break

    queue = deque([] if unreachable else [(start_url, 0)])
    queued = {start_url}
    queued_keys, fetched_keys = {url_key(start_url)}, set()
    # About, contact, pricing and FAQ pages carry the identity facts most checks need.
    # On a small page budget, breadth-first order often never reaches them.
    priority_budget = 6
    # URLs seen as internal link targets, kept apart from `queued` so sitemap top-ups
    # never count as "linked". The frontier flags record whether link-following ran
    # to completion, which REACH-017 needs before it may call a URL an orphan.
    linked = {start_url}
    link_frontier_exhausted = frontier_capped = False
    fetched, blocked_by_robots, doc_links = {}, [], []
    skipped_private = []
    polite_delay = max(args.delay_ms / 1000.0, min(policy.crawl_delay.get("*", 0), 2.0))

    def worker(job):
        url, depth = job
        allowed, _, rule = policy.verdict(UA_SELF, parse.urlparse(url).path or "/")
        if not allowed and not args.ignore_robots:
            return ("blocked", url, rule, depth)
        time.sleep(polite_delay)
        return ("ok", url, fetch(url, timeout=15), depth)

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        while queue and len(fetched) < args.max_pages and time.time() < deadline:
            batch = []
            while queue and len(batch) < args.concurrency and \
                    len(fetched) + len(batch) < args.max_pages:
                batch.append(queue.popleft())
            for kind, url, payload, depth in pool.map(worker, batch):
                if kind == "blocked":
                    blocked_by_robots.append({"url": url, "rule": payload})
                    continue
                resp = payload
                final_key = url_key(resp.get("final_url") or url)
                if resp.get("status") == 200 and final_key in fetched_keys:
                    continue                # a redirect or slash variant of a page already read
                fetched_keys.add(final_key)
                ctype = (resp.get("content_type") or "").lower()
                body_head = resp.get("text", "")[:200].lstrip()
                if "html" not in ctype and resp.get("status") == 200 \
                        and not body_head.startswith("<"):
                    fetched[url] = {"response": resp, "depth": depth, "parsed": None,
                                    "non_html": True}
                    continue
                parsed = parse_html(resp.get("text", ""), resp.get("final_url", url))
                ptype, ptags = classify(url, parsed)
                # Only the audited host's root is "the homepage". Other subdomains' roots
                # (a support portal, a language edition) are ordinary pages of this site.
                if ptype == "homepage" and \
                        parse.urlparse(url).netloc.lower().removeprefix("www.") != \
                        parse.urlparse(start_url).netloc.lower().removeprefix("www."):
                    ptype = "generic"
                fetched[url] = {"response": resp, "depth": depth, "parsed": parsed,
                                "page_type": ptype, "page_tags": ptags, "non_html": False}
                if depth < args.max_depth and resp.get("status") == 200:
                    for link in parsed["links"]:
                        target = link.get("abs")
                        if not target:
                            continue
                        linked.add(target)
                        if target in queued or url_key(target) in queued_keys:
                            continue
                        if DOC_EXT.search(target):
                            doc_links.append({"from": url, "href": target,
                                              "text": link.get("text", "")[:120]})
                            continue
                        if SKIP_EXT.search(target):
                            continue
                        parsed_target = parse.urlparse(target)
                        if registrable(parsed_target.netloc) != site_root:
                            continue
                        if PRIVATE_PATH.search(parsed_target.path or "") or                                 PRIVATE_HOST.match(parsed_target.netloc or ""):
                            skipped_private.append(target)
                            continue
                        queued.add(target)
                        queued_keys.add(url_key(target))
                        if priority_budget and PRIORITY_PATH.search(parsed_target.path or ""):
                            priority_budget -= 1
                            queue.appendleft((target, depth + 1))
                        else:
                            queue.append((target, depth + 1))
                        if len(queued) > 600:
                            frontier_capped = True
                            break
            if not queue and len(fetched) < args.max_pages:
                link_frontier_exhausted = True
                for url in seed_urls:                       # top up from the sitemap
                    if url not in queued and url_key(url) not in queued_keys:
                        queued.add(url)
                        queued_keys.add(url_key(url))
                        queue.append((url, 1))
                    if len(queue) >= args.max_pages:
                        break
                if not queue:
                    break

    # ---- render parity sample --------------------------------------------
    render_targets = [u for u, rec in fetched.items()
                      if not rec["non_html"] and rec["response"].get("status") == 200
                      ][:args.render_pages]
    if args.render == "off":
        rendered_html, render_meta = {}, {"available": False, "reason": "render disabled (--render off)"}
    else:
        rendered_html, render_meta = try_render(render_targets)
    render_meta["attempted_urls"] = render_targets

    # ---- persist bundle ---------------------------------------------------
    page_index = []
    for url, rec in fetched.items():
        pid = page_id(url)
        resp = rec["response"]
        rendered = rendered_html.get(url)
        record = {
            "id": pid, "url": url, "final_url": resp.get("final_url", url),
            "status": resp.get("status"), "redirects": resp.get("redirects", []),
            "headers": resp.get("headers", {}), "content_type": resp.get("content_type", ""),
            "declared_charset": resp.get("declared_charset"),
            "fetch_ms": resp.get("elapsed_ms"), "bytes": resp.get("raw_bytes", 0),
            "depth": rec["depth"], "non_html": rec["non_html"],
            "page_type": rec.get("page_type"), "page_tags": rec.get("page_tags", []),
            "raw": rec["parsed"],
            "rendered": parse_html(rendered, resp.get("final_url", url)) if rendered else None,
            "render_available": bool(rendered),
            "network_error": resp.get("error") or resp.get("tls_error"),
        }
        with open(os.path.join(workspace, "pages", f"{pid}.json"), "w", encoding="utf-8") as fh:
            json.dump(record, fh, ensure_ascii=False)
        with open(os.path.join(workspace, "pages", f"{pid}.raw.html"), "w", encoding="utf-8") as fh:
            fh.write(resp.get("text", "")[:1_500_000])
        if rendered:
            with open(os.path.join(workspace, "pages", f"{pid}.rendered.html"), "w",
                      encoding="utf-8") as fh:
                fh.write(rendered[:1_500_000])
        page_index.append({"id": pid, "url": url, "status": resp.get("status"),
                           "page_type": rec.get("page_type"), "depth": rec["depth"],
                           "word_count": (rec["parsed"] or {}).get("word_count", 0),
                           "render_available": bool(rendered)})

    html_pages = [p for p in page_index if p["status"] == 200]

    # ---- soft-block / shell detection ---------------------------------------
    # A 200 response is not proof the crawl read anything. Bot-challenge pages and
    # client-rendered routes both return 200 with byte-identical chrome on every URL.
    # Measuring distinct bodies catches that; status codes alone do not.
    body_signatures, challenged = {}, []
    for url, rec in fetched.items():
        if rec["non_html"] or not rec["parsed"] or rec["response"].get("status") != 200:
            continue
        text = (rec["parsed"].get("text") or "").strip()
        digest = hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()[:12]
        body_signatures.setdefault(digest, []).append(url)
        hit = detect_challenge(rec["response"].get("text", ""),
                               rec["response"].get("headers", {}),
                               rec["parsed"].get("title", ""),
                               rec["parsed"].get("word_count", 0))
        if hit:
            challenged.append({"url": url, **hit})

    if challenged:
        vendors = sorted({c["vendor"] for c in challenged})
        first = challenged[0]
        finding("REACH-022", "A bot-protection service is serving challenge pages instead of content",
                "critical",
                f"{len(challenged)}/{len(html_pages)} crawled pages returned HTTP 200 but the body is "
                f"a {' / '.join(vendors)} challenge interstitial, not the page. Example: "
                f"{first['url']} ({first['word_count']} words; {'; '.join(first['signals'])}). "
                "Because the status is 200, uptime monitoring and naive crawl tools report the site "
                "as healthy. Answer-time retrieval agents do not solve JavaScript challenges, so they "
                "receive this interstitial and index nothing -- the site is invisible to AI assistants "
                "while appearing perfectly fine to a browser.",
                {"summary": f"Allowlist the documented AI retrieval agents in {' / '.join(vendors)} "
                            "so they receive content instead of a challenge.",
                 "steps": [f"In the {vendors[0]} dashboard, add a bot rule that allows the verified "
                           "AI crawlers (OAI-SearchBot, ChatGPT-User, Claude-User, Claude-SearchBot, "
                           "PerplexityBot) by their published user-agents and IP ranges.",
                           "Verify with `curl -A 'OAI-SearchBot' <url> | wc -w` -- a real page returns "
                           "hundreds of words, a challenge returns a handful.",
                           "Keep the challenge for genuinely abusive traffic; scope the exception to "
                           "the documented, IP-verifiable agents rather than disabling protection.",
                           "If challenging must continue, return a 403 rather than a 200 so the block "
                           "is at least honest and monitorable."],
                 "effort": "medium", "owner": "infrastructure"},
                affected_urls=[c["url"] for c in challenged[:20]],
                metrics={"vendors": vendors, "challenged_pages": len(challenged)})
    distinct_bodies = len(body_signatures)
    largest_group = max(body_signatures.values(), key=len, default=[])
    duplicate_ratio = (len(largest_group) / max(1, len(html_pages))) if html_pages else 0.0

    if len(html_pages) >= 4 and duplicate_ratio >= 0.6:
        sample_url = largest_group[0]
        sample_words = next((p["word_count"] for p in page_index if p["url"] == sample_url), 0)
        finding("REACH-021", "Different URLs return byte-identical page content",
                "critical" if duplicate_ratio >= 0.9 else "high",
                f"{len(largest_group)}/{len(html_pages)} crawled URLs returned exactly the same "
                f"{sample_words}-word body ({distinct_bodies} distinct bodies across "
                f"{len(html_pages)} pages). Example: {sample_url}. Every one of those URLs "
                "responded 200, so status codes alone suggest a healthy crawl -- but no "
                "page-specific content was served. The usual causes are a bot-challenge or "
                "geo/consent interstitial returned under a 200 status, or client-side routing "
                "where the server sends the same shell for every path. Either way, a "
                "non-executing fetcher sees no unique content on these pages, so none of them "
                "can be indexed or cited for anything specific.",
                {"summary": "Serve unique, page-specific content in the server response for each URL.",
                 "steps": ["Fetch two of the listed URLs with `curl -s <url> | wc -w` and confirm "
                           "the bodies are identical.",
                           "If a bot challenge is responsible, allowlist the documented AI "
                           "retrieval agents at the CDN/WAF, and return a real error status rather "
                           "than 200 when challenging.",
                           "If client-side routing is responsible, server-render or pre-render each "
                           "route so its own content ships in the initial HTML.",
                           "Re-run this audit and confirm the distinct-body count matches the page "
                           "count."],
                 "effort": "high", "owner": "infrastructure"},
                affected_urls=largest_group[:20],
                metrics={"distinct_bodies": distinct_bodies,
                         "identical_pages": len(largest_group),
                         "pages_checked": len(html_pages)})

    # ---- access findings over the crawled sample -------------------------
    home = fetched.get(start_url, {}).get("response", prim)
    # 4xx counts too: a homepage that 404s or 403s to a bot is exactly as invisible as
    # one that 500s, and treating only 5xx as failure let a fully-failed crawl through.
    client_level = False
    if not home.get("status") or home.get("status", 0) >= 400:
        # Separate "refuses bots" from "refuses this client". Bot management often keys on
        # IP reputation, TLS fingerprint or geography, which refuses a browser User-Agent
        # from the same machine too -- and then this audit cannot know whether verified AI
        # crawlers are admitted. Asserting a critical block there would be a guess.
        control = edge_probe.get("control")
        if control is None and not unreachable:
            control_resp = fetch(start_url, ua=BROWSER_CONTROL_UA, timeout=8, max_bytes=400_000)
            _, control_words, control_challenge = page_signals(control_resp)
            control = {"status": control_resp.get("status"), "words": control_words,
                       "challenge": bool(control_challenge)}
            edge_probe["control"] = control
        client_level = not control or control.get("status") != 200 or control.get("challenge")
        if client_level:
            reach008_severity, reach008_confidence = "high", "low"
            reach008_context = (
                f" A browser User-Agent sent from the same client was refused too "
                f"(status={(control or {}).get('status')}), so the refusal is keyed on the client "
                "(IP reputation, TLS fingerprint or geography), not on the user-agent string. This "
                "audit cannot tell whether verified AI crawlers are admitted; confirm in CDN or "
                "server logs before acting.")
        else:
            reach008_severity, reach008_confidence = "critical", "high"
            reach008_context = (
                f" A browser User-Agent from the same client received 200 ({control['words']} "
                "words), so the refusal is keyed on the user-agent: non-browser clients are turned "
                "away.")
        finding("REACH-008", "Homepage does not return a successful response to a bot user-agent",
                reach008_severity,
                f"GET {start_url} with UA '{UA_SELF}' -> status={home.get('status')} "
                f"error={home.get('error') or home.get('tls_error')}.{reach008_context} If the entry "
                "point fails for non-browser clients, nothing on the site can be indexed or cited.",
                {"summary": "Make the homepage return 200 to non-browser user-agents.",
                 "steps": ["Reproduce with `curl -A '<AI bot UA>' -I <url>` and compare to a browser UA.",
                           "Check WAF/CDN bot rules, rate limits and geo-blocks for false positives.",
                           "Allowlist the documented AI retrieval agents by UA and published IP ranges."],
                 "effort": "medium", "owner": "infrastructure"},
                confidence=reach008_confidence)
        if client_level and not robots_resp.get("status"):
            # robots.txt went unanswered for the same reason the homepage did.
            findings[:] = [f for f in findings if f["check_id"] != "REACH-004"]

    # Not every non-2xx is a broken link, and blaming the site for all of them is a
    # false positive with three distinct causes:
    #   401/403 -- deliberately gated content, working as designed
    #   410     -- an explicit, correct "this is gone" signal
    #   429     -- OUR rate limiting tripped the site; an audit artefact, never a defect
    # Only genuinely broken responses are counted.
    GATED = {401, 403}
    DELIBERATE = {410}
    SELF_INFLICTED = {429}
    errors, gated, throttled = [], [], []
    for p in page_index:
        status = p.get("status")
        if not status or status < 400 or status >= 600:
            continue
        if status in SELF_INFLICTED:
            throttled.append(p)
        elif status in GATED or status in DELIBERATE:
            gated.append(p)
        else:
            errors.append(p)

    if throttled:
        notes.append(
            f"{len(throttled)} URL(s) returned 429 (rate limited). That is this audit's own "
            "crawl rate, not a site defect, so it is excluded from REACH-009. Re-run with a "
            "higher --delay-ms for full coverage of those URLs.")
    if gated:
        notes.append(
            f"{len(gated)} URL(s) returned 401/403/410. Treated as deliberately gated or "
            "retired rather than broken, so excluded from REACH-009. Example: "
            f"{gated[0]['url']} -> {gated[0]['status']}.")

    if errors and html_pages:
        pct = round(100 * len(errors) / max(1, len(page_index)))
        sample = "; ".join(f"{e['url']} -> {e['status']}" for e in errors[:5])
        finding("REACH-009", "Internally-linked pages return error status codes",
                "high" if pct >= 20 else "medium",
                f"{len(errors)}/{len(page_index)} crawled URLs ({pct}%) returned a genuine error "
                f"(4xx/5xx excluding 401/403/410 gating and 429 rate limiting) while being "
                f"linked from within the site. Examples: {sample}. Broken internal links waste crawl "
                "budget and break the paths a crawler uses to reach real content.",
                {"summary": "Fix or 301-redirect the broken internal links, then remove them from the sitemap.",
                 "steps": ["Repoint each broken internal link to its live URL.",
                           "301-redirect removed URLs that still earn external links.",
                           "Return a real 404 status (not 200) for genuinely missing pages.",
                           "Add a link check to CI to stop regressions."],
                 "effort": "medium", "owner": "web/dev"},
                affected_urls=[e["url"] for e in errors[:20]])

    nosnippet, noindex, short_snippet, masked = [], [], [], []
    for url, rec in fetched.items():
        if rec["non_html"] or not rec["parsed"]:
            continue
        directives = (rec["parsed"].get("robots_meta") or "").lower()
        header_robots = (rec["response"].get("headers", {}).get("x-robots-tag") or "").lower()
        combined = (directives + " " + header_robots).strip()
        if "noindex" in combined:
            noindex.append((url, combined, "noindex" not in directives))
        # Snippet controls on legal pages are a deliberate choice, not a visibility defect.
        if rec.get("page_type") == "legal":
            continue
        limits = [int(v) for v in SNIPPET_LIMIT.findall(combined)]
        if "nosnippet" in combined or 0 in limits:
            nosnippet.append((url, combined))
            continue
        positive = [v for v in limits if v > 0]           # -1 means unlimited
        if positive and min(positive) <= 50:
            short_snippet.append((url, min(positive)))
        visible = rec["parsed"].get("visible_text_chars") or 0
        hidden = rec["parsed"].get("data_nosnippet_chars") or 0
        if visible >= 400 and hidden / visible > 0.30:
            masked.append((url, round(100 * hidden / visible)))

    if nosnippet:
        finding("REACH-010", "Pages forbid snippets, which forbids quotation",
                "critical" if len(nosnippet) >= max(1, len(html_pages) // 2) else "high",
                f"{len(nosnippet)}/{len(html_pages)} crawled pages carry a nosnippet / max-snippet:0 "
                f"directive. Example: {nosnippet[0][0]} -> '{nosnippet[0][1][:120]}'. Assistants respect "
                "this directive by refusing to quote the page even when it ranks -- the page can be "
                "indexed and still never be cited.",
                {"summary": "Remove nosnippet / max-snippet:0 from pages you want assistants to quote.",
                 "steps": ["Delete the directive from the meta robots tag and the X-Robots-Tag header.",
                           "If some content must stay unquotable, wrap only that block in "
                           "`data-nosnippet` instead of penalising the whole page.",
                           "Set `max-snippet:-1` explicitly to allow full-length snippets."],
                 "effort": "low", "owner": "web/dev"},
                affected_urls=[u for u, _ in nosnippet[:20]])

    if short_snippet or masked:
        parts = []
        if short_snippet:
            parts.append(f"{len(short_snippet)}/{len(html_pages)} crawled pages cap snippets at "
                         f"max-snippet:{short_snippet[0][1]} or less (example: {short_snippet[0][0]}). "
                         f"At {short_snippet[0][1]} characters an assistant can quote roughly "
                         f"{max(1, short_snippet[0][1] // 6)} words, so no complete fact survives.")
        if masked:
            worst = max(masked, key=lambda m: m[1])
            parts.append(f"{len(masked)}/{len(html_pages)} crawled pages wrap more than 30% of their "
                         f"visible text in data-nosnippet (worst: {worst[1]}% on {worst[0]}), which "
                         "removes that text from anything a search-grounded assistant may quote.")
        affected = list(dict.fromkeys([u for u, _ in short_snippet] + [u for u, _ in masked]))
        finding("REACH-024", "Snippet controls leave too little of the page quotable",
                "high" if len(affected) >= max(1, len(html_pages) // 2) else "medium",
                " ".join(parts),
                {"summary": "Loosen snippet controls on pages you want assistants to quote.",
                 "steps": ["Remove low max-snippet values, or set `max-snippet:-1` for no limit.",
                           "Reserve `data-nosnippet` for the specific blocks that must not be quoted "
                           "(legal boilerplate, gated excerpts), not whole content regions.",
                           "Re-check the page source and the X-Robots-Tag header after the change."],
                 "effort": "low", "owner": "web/dev"},
                affected_urls=affected[:20])

    if noindex:
        # Under 50 words, a noindexed URL is a fragment or widget endpoint (content that is
        # stitched into other pages), not a page anyone meant to rank.
        content_noindex = [(u, d, h) for u, d, h in noindex
                           if fetched[u].get("page_type") not in ("legal", "listing", None)
                           and (fetched[u]["parsed"] or {}).get("word_count", 0) >= 50]
        if content_noindex:
            header_only = sum(1 for _, _, h in content_noindex if h)
            header_note = (f" {header_only} of these set noindex only in the X-Robots-Tag HTTP "
                           "header, which is invisible in the page source and in CMS SEO settings -- "
                           "look in the server or CDN configuration." if header_only else "")
            share = len(content_noindex) / max(1, len(html_pages))
            # One deliberately-noindexed page is a question to confirm, not a site-wide
            # defect. Severity tracks how much of the site is actually withheld.
            noindex_severity = ("high" if share >= 0.5 else
                                "medium" if share >= 0.15 else "low")
            finding("REACH-012", "Content pages are marked noindex", noindex_severity,
                    f"{len(content_noindex)}/{len(html_pages)} crawled content page(s) carry a "
                    f"noindex directive. "
                    f"Example: {content_noindex[0][0]} -> '{content_noindex[0][1][:120]}'. Noindexed "
                    "pages are dropped from the search indexes assistants query for grounding."
                    f"{header_note}",
                    {"summary": "Remove noindex from pages that should be discoverable; keep it only on "
                                "thin, duplicate or private pages.",
                     "steps": ["Audit each noindexed URL and confirm the directive is deliberate.",
                               "Remove it from anything with unique customer-facing value.",
                               "Prefer canonicalisation over noindex for duplicate variants."],
                     "effort": "low", "owner": "web/dev"},
                    affected_urls=[u for u, _, _ in content_noindex[:20]])

    canon_issues = []
    for url, rec in fetched.items():
        if rec["non_html"] or not rec["parsed"] or rec["response"].get("status") != 200:
            continue
        canon = rec["parsed"].get("canonical")
        if not canon:
            canon_issues.append((url, "missing"))
        elif registrable(parse.urlparse(canon).netloc) != site_root:
            canon_issues.append((url, f"cross-domain -> {canon}"))
    cross_domain = [c for c in canon_issues if c[1].startswith("cross-domain")]
    # A canonical on the brand's own other domain (a regional edition, or a move to a new
    # domain) is a deliberate consolidation, not a site crediting a stranger.
    own_domain = [c for c in cross_domain
                  if same_brand(site_root, parse.urlparse(c[1].split("-> ", 1)[1]).netloc)]
    cross_domain = [c for c in cross_domain if c not in own_domain]
    if cross_domain:
        finding("REACH-013", "Pages canonicalise to a different domain", "high",
                f"{len(cross_domain)} page(s) declare a rel=canonical on another registrable domain. "
                f"Example: {cross_domain[0][0]} -> {cross_domain[0][1]}. This tells crawlers to credit "
                "and cite the other domain instead of this one.",
                {"summary": "Point rel=canonical at the page's own URL on the canonical host.",
                 "steps": ["Set a self-referencing absolute canonical on every indexable page.",
                           "Use cross-domain canonicals only for genuine syndication you do not own."],
                 "effort": "low", "owner": "web/dev"},
                affected_urls=[c[0] for c in cross_domain[:20]])
    elif own_domain:
        finding("REACH-013", "Pages canonicalise to a different domain", "low",
                f"{len(own_domain)} page(s) declare a rel=canonical on another domain that appears to "
                f"belong to the same brand. Example: {own_domain[0][0]} -> {own_domain[0][1]}. That is "
                "correct when intended (a regional edition or a domain migration), but assistants "
                "will cite the canonical domain rather than this one, and a canonical that depends "
                "on the visitor's location can change with where the request comes from.",
                {"summary": "Confirm the cross-domain canonical is the intended consolidation.",
                 "steps": ["Check that the canonical domain is the one you want cited.",
                           "If this host is being retired, 301-redirect it rather than relying on "
                           "canonicals alone.",
                           "If the canonical varies by visitor location, use hreflang alternates so "
                           "each regional page stays self-canonical."],
                 "effort": "low", "owner": "web/dev"},
                confidence="medium", affected_urls=[c[0] for c in own_domain[:20]])
    elif html_pages and len(canon_issues) >= max(2, len(html_pages) * 0.5):
        finding("REACH-014", "Most pages have no rel=canonical", "medium",
                f"{len(canon_issues)}/{len(html_pages)} crawled 200-pages declare no rel=canonical. "
                "Without it, URL variants (tracking parameters, trailing slashes, index.html) compete "
                "as separate documents and dilute the signals for the real page.",
                {"summary": "Add a self-referencing absolute rel=canonical to every indexable page.",
                 "steps": ["Emit `<link rel=\"canonical\" href=\"<absolute clean URL>\">` in every head.",
                           "Strip tracking parameters from the canonical value.",
                           "Keep canonical, sitemap and internal link URLs byte-identical."],
                 "effort": "low", "owner": "web/dev"})

    # Latency is the noisiest thing this audit measures, so the statistic has to be
    # robust to the three ways a naive version goes wrong:
    #   - one slow page (a cold cache, a heavy report) is not a slow site
    #   - the first request pays DNS + TLS setup that later ones would amortise
    #   - transient congestion on the auditor's own network is not the site's fault
    # A median over the whole sample absorbs all three; a count of outliers does not.
    # Reporting "median of the slow set" would also be circular -- that set is already
    # filtered to exceed the threshold, so its median always does too.
    timings = sorted(r["response"]["elapsed_ms"] for r in fetched.values()
                     if r["response"].get("status") == 200
                     and r["response"].get("elapsed_ms") is not None)
    # Drop the single slowest sample: on a small crawl that is usually the connection
    # warm-up rather than a representative response.
    representative = timings[:-1] if len(timings) >= 5 else timings
    if len(representative) >= 4:
        median_ms = representative[len(representative) // 2]
        slow = [(u, r["response"]["elapsed_ms"]) for u, r in fetched.items()
                if r["response"].get("elapsed_ms", 0) > 2500
                and r["response"].get("status") == 200]
        if median_ms > 2500:
            finding("REACH-015", "Server responses are slow enough to cost crawl budget and visitors",
                    "medium",
                    f"Median server response across {len(representative)} sampled pages is "
                    f"{median_ms} ms (range {representative[0]}-{representative[-1]} ms; "
                    f"{len(slow)} page(s) over 2500 ms). The slowest sample is excluded as "
                    "connection warm-up, and each timing includes TLS setup because the crawler "
                    "does not reuse connections -- so treat this as a screening signal and confirm "
                    "with a real performance tool before sizing the work. Retrieval agents fetch "
                    "under short timeouts and drop slow sources; visitors abandon at the same point.",
                    {"summary": "Cut server response time below ~800 ms with caching and a CDN.",
                     "steps": ["Put full-page or edge caching in front of anonymous HTML responses.",
                               "Profile the slowest templates and fix N+1 queries / uncached API calls.",
                               "Serve from a CDN close to the audience and enable compression."],
                     "effort": "medium", "owner": "infrastructure"},
                    pillar="engagement")

    long_chains = [(u, r["response"].get("redirects", [])) for u, r in fetched.items()
                   if len(r["response"].get("redirects", [])) >= 3]
    if long_chains:
        hops = " -> ".join(str(h["status"]) for h in long_chains[0][1])
        finding("REACH-016", "Long redirect chains on internal URLs", "low",
                f"{len(long_chains)} crawled URL(s) went through 3+ redirects. Example: "
                f"{long_chains[0][0]} -> {hops}. Each hop costs latency and some fetchers cap "
                "redirect depth.",
                {"summary": "Collapse redirect chains to a single hop to the final URL.",
                 "steps": ["Rewrite internal links to point directly at the final URL.",
                           "Flatten stacked rules (http->https->www->path) into one redirect."],
                 "effort": "low", "owner": "infrastructure"})

    def same_page(u):
        p = parse.urlparse(u)
        return (p.netloc.lower(), (p.path or "/").rstrip("/") or "/", p.query)

    sampled_sitemap = sitemap_urls[:200]
    linked_keys = {same_page(u) for u in linked}
    orphans = [u for u in sampled_sitemap
               if same_page(u) not in linked_keys and registrable(parse.urlparse(u).netloc) == site_root]
    orphan_threshold = max(3, len(sampled_sitemap) * 0.3)
    # A URL is only an orphan if link-following ran to completion. When the page budget
    # stops the crawl first, unseen sitemap URLs describe the sample, not the site.
    frontier_complete = link_frontier_exhausted and not frontier_capped
    if sampled_sitemap and html_pages and len(orphans) >= orphan_threshold and not frontier_complete:
        notes.append(
            f"REACH-017 (orphan sitemap URLs) not evaluated: the crawl reached its budget "
            f"({len(fetched)} pages) before exhausting internal links, so the {len(orphans)} sitemap "
            "URLs not yet seen as link targets reflect the audit's sample, not missing links.")
    elif sampled_sitemap and html_pages and len(orphans) >= orphan_threshold:
        finding("REACH-017", "Sitemap URLs are not reachable through internal links", "medium",
                f"{len(orphans)}/{len(sampled_sitemap)} sampled sitemap URLs were never encountered as "
                f"a link target within {args.max_depth} clicks of the homepage, after the crawl had "
                f"followed every internal link in that range. Example: {orphans[0]}. "
                "Pages with no internal links receive little crawl priority and no internal authority.",
                {"summary": "Link every important page from a relevant hub or navigation surface.",
                 "steps": ["Add contextual internal links from related pages to each orphan.",
                           "Build hub pages (category, topic, resource index) that link the long tail.",
                           "Keep every important page within three clicks of the homepage."],
                 "effort": "medium", "owner": "content"},
                affected_urls=orphans[:20])

    if blocked_by_robots:
        # Distinguish "blocked the right things" from "blocked real content". A site
        # that excludes /search, /cart and honeypot traps is following best practice;
        # flagging that as high severity teaches readers to ignore the severity field.
        content_blocked = [b for b in blocked_by_robots
                           if not NON_CONTENT_DISALLOW.search(
                               parse.urlparse(b["url"]).path or "/")
                           and "?" not in b["url"]]
        housekeeping = len(blocked_by_robots) - len(content_blocked)
        if content_blocked:
            finding("REACH-018", "Content paths are disallowed for all crawlers",
                    "high" if len(content_blocked) >= 3 else "medium",
                    f"{len(content_blocked)} of {len(blocked_by_robots)} internally-linked URL(s) "
                    f"skipped by robots.txt do not match the usual non-content patterns (search, "
                    f"filters, user areas, alternate formats). Example: {content_blocked[0]['url']} "
                    f"(rule: {content_blocked[0]['rule']}). The other {housekeeping} look like "
                    "deliberate housekeeping exclusions and are not counted here. If the listed "
                    "paths hold public content, that content is invisible to every AI system.",
                    {"summary": "Narrow the Disallow rules so they cover only private/duplicate paths.",
                     "steps": ["Review each listed path and confirm it holds nothing publicly valuable.",
                               "Replace broad prefix blocks with precise ones.",
                               "Use noindex (not robots Disallow) for pages that must be fetchable but "
                               "unindexed -- a disallowed page can never be read to discover its noindex."],
                     "effort": "low", "owner": "web/dev"},
                    affected_urls=[b["url"] for b in content_blocked[:20]])
        else:
            notes.append(
                f"robots.txt disallowed {len(blocked_by_robots)} internally-linked URL(s), all of "
                "which match standard non-content patterns (search, filters, user areas, alternate "
                "formats, crawler traps). This is correct practice; no finding raised.")

    if not has_llms and html_pages:
        notes.append(f"/llms.txt -> {llms_resp.get('status')}. Recorded, not scored: no major AI "
                     "search crawler documents reading llms.txt, so its absence is not a visibility "
                     "defect.")

    # ---- manifest ---------------------------------------------------------
    agent_verdicts = {}
    for agent in list(RETRIEVAL_AGENTS) + list(TRAINING_AGENTS):
        blocked, group, rule = policy.blanket_blocked(agent)
        agent_verdicts[agent] = {
            "allowed_root": not blocked, "matched_group": group, "rule": rule,
            "class": "retrieval" if agent in RETRIEVAL_AGENTS else "training"}

    manifest = {
        "schema_version": "1.0",
        "site": site_host,
        "site_root": site_root,
        "start_url": start_url,
        "crawled_at": started_at.isoformat().replace("+00:00", "Z"),
        "finished_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "duration_seconds": round(time.time() - started_clock, 1),
        "user_agent": UA_SELF,
        "limits": {"max_pages": args.max_pages, "max_depth": args.max_depth,
                   "budget_seconds": args.budget_seconds, "delay_ms": args.delay_ms},
        "pages": page_index,
        "page_count": len(page_index),
        "ok_page_count": len(html_pages),
        "distinct_text_bodies": distinct_bodies,
        "duplicate_body_ratio": round(duplicate_ratio, 3),
        "page_types": {t: sum(1 for p in page_index if p["page_type"] == t)
                       for t in {p["page_type"] for p in page_index if p["page_type"]}},
        "robots": {
            "url": robots_url, "status": robots_resp.get("status"),
            "present": robots_resp.get("status") == 200 and bool(robots_text),
            "sitemaps_declared": policy.sitemaps,
            "parse_errors": policy.parse_errors,
            "agent_verdicts": agent_verdicts,
            "blocked_retrieval_agents": [b["agent"] for b in blocked_retrieval],
            "blocked_training_agents": [b["agent"] for b in blocked_training],
            "raw_excerpt": robots_text[:2000],
        },
        "sitemaps": sitemap_reports,
        "sitemap_url_count": len(sitemap_urls),
        "sitemap_lastmods": sitemap_lastmods[:500],
        "sitemap_urls_sample": sitemap_urls[:300],
        "llms_txt": {"present": has_llms, "status": llms_resp.get("status")},
        "edge_access_probe": edge_probe,
        "crawl_frontier": {"link_frontier_exhausted": link_frontier_exhausted,
                           "frontier_capped": frontier_capped},
        "document_links": doc_links[:100],
        "render": render_meta,
        "blocked_by_robots": blocked_by_robots[:50],
        "skipped_private_urls": skipped_private[:50],
        "scheme_probe": scheme_note,
        "notes": notes,
    }
    with open(os.path.join(workspace, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)

    out = {
        "skill": "crawl-access-audit",
        "pillar": "access",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "checks_run": [f"REACH-{n:03d}" for n in range(1, 25) if n != 19],
        "pages_analysed": len(page_index),
        "findings": findings,
        "notes": notes,
    }
    with open(os.path.join(workspace, "findings", "crawl-access-audit.json"), "w",
              encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)

    print(json.dumps({"workspace": workspace, "pages": len(page_index),
                      "ok_pages": len(html_pages), "findings": len(findings),
                      "render_available": render_meta.get("available"),
                      "duration_s": manifest["duration_seconds"]}, indent=2))


if __name__ == "__main__":
    main()
