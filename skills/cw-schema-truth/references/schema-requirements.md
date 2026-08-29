# Structured data requirements

## Page type to expected schema types

Page type is inferred deterministically by `cw-reach-gate` and stored in the bundle.
Each row states *why* the markup matters to an answer, because that is what decides
severity.

| Page type | Expected types | Why an assistant needs it |
|---|---|---|
| homepage | Organization, LocalBusiness, Corporation, NGO, WebSite, OnlineStore | Identifies who the brand is, so facts attach to the right entity |
| product | Product, ProductGroup, SoftwareApplication, Vehicle, Book, Course | Supplies name, price, availability, rating |
| pricing | Product, Offer, AggregateOffer, Service, PriceSpecification | Lets an assistant quote an exact, current price with a currency |
| article | Article, BlogPosting, NewsArticle, TechArticle | Author, publish date and headline drive freshness and trust judgements |
| faq | FAQPage, QAPage | Maps questions to answers — the most directly quotable structure there is |
| contact | Organization, LocalBusiness, ContactPage, PostalAddress | Answers "how do I reach them" |
| about | Organization, AboutPage, Corporation | Anchors the entity description assistants repeat back |
| careers | JobPosting | Makes roles eligible for job-answer surfaces |
| local | LocalBusiness, Restaurant, Store, Place | Hours, address and geo for local answers |
| docs | TechArticle, HowTo, APIReference | Marks procedural content so steps can be quoted in order |

## Required and recommended properties

`required` — the consuming parser typically rejects the whole node without these, taking
its valid siblings down with it. `recommended` — the node survives, but the answer is
weaker.

| Type | Required | High-value recommended |
|---|---|---|
| Organization | name, url | logo, description, sameAs, address, contactPoint, @id |
| LocalBusiness | name, address | telephone, openingHoursSpecification, geo, priceRange, sameAs |
| Product | name | description, image, offers, brand, sku, aggregateRating |
| Offer | price, priceCurrency | availability, url, priceValidUntil, itemCondition |
| Article / BlogPosting / NewsArticle | headline | author, datePublished, dateModified, image, publisher |
| FAQPage | mainEntity | — |
| JobPosting | title, datePosted, hiringOrganization | jobLocation, baseSalary, employmentType, validThrough |
| WebSite | name, url | potentialAction, publisher |
| Event | name, startDate, location | endDate, offers, organizer, eventStatus |
| SoftwareApplication | name | offers, applicationCategory, operatingSystem |
| BreadcrumbList | itemListElement | — |

## The entity graph pattern

The single highest-value structural improvement, and what `MARK-004` and `MARK-005` are
really asking for. Publish one canonical Organization node with a stable `@id`, then
reference that `@id` everywhere else instead of repeating the entity:

```json
{
  "@context": "https://schema.org",
  "@graph": [
    { "@type": "Organization",
      "@id": "https://example.com/#organization",
      "name": "Acme",
      "url": "https://example.com/",
      "description": "Acme is a project-management tool for construction subcontractors.",
      "logo": "https://example.com/logo.png",
      "foundingDate": "2014-03-01",
      "sameAs": ["https://www.linkedin.com/company/acme",
                 "https://www.crunchbase.com/organization/acme",
                 "https://www.wikidata.org/wiki/Q000000"] },
    { "@type": "WebSite",
      "@id": "https://example.com/#website",
      "url": "https://example.com/",
      "name": "Acme",
      "publisher": { "@id": "https://example.com/#organization" } }
  ]
}
```

Then on an article: `"publisher": {"@id": "https://example.com/#organization"}`. On a
product: `"brand": {"@id": "https://example.com/#organization"}`.

This is what turns a set of independent per-page annotations into a single coherent claim
about one entity — which is exactly what entity resolution needs, and what `sameAs` then
connects to the outside world. It is also why `MARK-004` is graded `high`: without it, every
other piece of markup on the site is an orphan fact.

## Drift is worse than absence (MARK-009)

Markup that disagrees with the visible page is asserted with machine-grade confidence, so
an assistant repeats the wrong figure without hedging, and search engines treat persistent
mismatch as a spam signal.

The check compares markup `price` / `name` / `headline` against the rendered text, and
**only fires when the page displays a competing value** — a page showing no price at all is
never accused of drift.

The fix is structural rather than a one-off correction: generate the JSON-LD from the same
server-side model that renders the template, and add a regression test asserting they
match. Drift almost always appears at the next content edit rather than at launch, which is
why hand-corrected markup re-breaks within a release or two.

## Validation

`MARK-003` reports the parser's exact message, line, column and surrounding snippet. The
usual causes, in order of frequency:

1. An unescaped quote inside a description or review body.
2. A trailing comma.
3. A raw newline inside a string.
4. An unrendered template variable (`{{ product.price }}` reaching production).

All four are prevented by serialising a real object (`json.dumps` / `JSON.stringify`)
rather than concatenating strings in a template, plus a build-time assertion that every
`ld+json` block parses.

## A note on markup that invites penalties

`MARK-009`'s remediation deliberately does not recommend adding `Review` or
`AggregateRating` markup to raise visibility. Self-serving review markup on a company's own
pages is against most consumers' guidelines and risks a manual action. The audit
recommends earning ratings on independent platforms and linking them via `sameAs` instead —
which also satisfies the corroboration requirement in `cw-entity-consensus`.
