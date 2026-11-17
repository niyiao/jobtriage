# Findings

One platform per investigation, one verdict each. Every board in
`platforms` got probed the same way: how the site actually hands a
browser its job listings, what a sample record looks like, what
stands in the way, and whether it's worth adding to the CLI. Probed
10-11 Nov 2026, one subagent per board.

Categories, from easiest to hardest:

- **public-api** — a JSON endpoint serves the listings; one request,
  parse, done. The gold standard.
- **static-html** — the listings are in the server-rendered page;
  stdlib HTML parsing, slightly more brittle than an API.
- **js-rendered** — the page is an empty shell until JavaScript runs;
  either find the underlying API the client calls or give up on
  stdlib-only.
- **multitenant-ats** — not a board but thousands: every customer
  company gets its own board, with no global listing. The greenhouse
  problem — per-board APIs exist, tenant discovery is the hard half.
- **bot-protected / auth-paywalled** — a wall in front of the data,
  and we don't fight walls.
- **not-a-fit** — works fine, but lists nothing the CLI wants.

## Already handled

- **greenhouse.io** — multitenant-ats, tabled. Full research lives in
  docs/scrapers.md "Investigating".
- **remotive.com** — built, source `rm`.
- **workingnomads.com** — built, source `wn`, the default.
- **Built 11 Nov 2026** — jobgether `jg`, weworkremotely `wwr`, remoteok
  `rok`, inclusivelyremote `ir`, wellfound `wf`, remocate `rc`,
  eurotechjobs `etj`, euremotejobs `erj`, remotefrontendjobs `rfj`,
  and bruntworkcareers.co `bw` (a new board, researched from scratch).
  Module notes, including the drift and traps the builds found after
  these reports were written, live in docs/scrapers.md "Board notes".
- **Deliberately skipped** — crossover through the end of the build
  order (crossover, kickresume, remote.co, simplyhired, linkedin,
  lever, ashby): thin volume, ToS trouble, or the greenhouse-class
  request bill. The research stands; revisit if priorities change.

## Reports

### app.dover.com — Dover (ATS)

**public-api · easy.** Not a greenhouse dead end: alongside the
per-tenant boards (`jobs.dover.com/<slug>`), Dover ships a public,
unauthenticated **cross-tenant feed** of every client's open jobs.
501 live jobs across 202 companies at time of checking, same-day
fresh.

- **Endpoints.**
  `GET https://app.dover.com/api/v1/job-board/jobs/?limit=100&offset=N`
  (DRF envelope `{count, next, previous, results}`; `limit` hard-caps
  at 100, so the whole corpus is ~6 requests). Per-tenant
  Greenhouse-style feed at
  `https://jobs.dover.com/feed/v1/boards/<slug>/jobs` (404 = churned).
  Detail endpoint `/api/v1/job-board/jobs/<uuid>/` adds the HTML
  description. Tenant discovery is solved for you:
  `jobs.dover.com/sitemap.xml` → careers sitemap (2,934 board slugs)
  and jobs sitemap (9,770 job URLs).
- **Record shape** (keys as returned): `title`, `client.name` /
  `client.slug`, `workplace_type` (ONSITE/HYBRID/REMOTE),
  `employment_type`, `locations[]` with
  `location_option.display_name` + `country`, `compensation`
  (`upper_bound`, `lower_bound`, `currency_code`, `salary_range_type`),
  `date_posted` (ISO-8601 UTC). Job URL:
  `https://jobs.dover.com/<client.slug>/<id>`.
- **The one trap:** Cloudflare 403s `Python-urllib/*` on both hosts —
  stdlib `urllib` dies out of the box. Any other `User-Agent` gets a
  plain 200; there's no challenge to solve. Set the header, done.
- **"International" quirk:** remote-worldwide jobs carry
  `display_name: "International"` but `country: "US"` — special-case
  the display name before country logic. And "International" can
  hide timezone constraints in the description (one wanted ±2h of
  GMT+8), so read descriptions before applying.
- **Other gotchas.** `robots.txt` disallows `/api/` on both hosts (the
  `/feed/` endpoints are allowed) — a robots-strict build uses the
  per-tenant feeds; server-side filters like `?client=` are silently
  ignored, so group locally; stale tenants keep serving old jobs, so
  always date-filter.
- **Fit.** Small YC-stage corpus, but real: 116 of 501 remote, and
  after the remote + worldwide/EMEA filter, 38 jobs — ~17
  engineering/product, several explicitly "International" or Europe,
  two Nigeria. Salary on 216 of 501. Worth adding as a supplement.

### crossover.com — Crossover

**public-api · easy.** The `/jobs` page is an Angular shell, but the
data behind it is a public, unauthenticated JSON API returning the
whole active board in one request. 48 active jobs at time of
checking — the pitch is worldwide USD remote with public salary
bands; the current volume isn't.

- **Endpoints.**
  `GET https://profile-api.crossover.com/pipelines?status=Active` —
  Salesforce-proxied records, envelope `{totalSize, done, records}`.
  Keys that matter: `Name` (title), `Brand__r.Name` (company),
  `Work_Model__c` (Remote/Hybrid/On-site),
  `Geographic_Restriction__c` (Global/Country/State/City),
  `Work_Country__c`, `Yearly_Rate__c`/`Hourly_Rate__c`,
  `Landing_Page_URL__c`, `ProductCode` (sequential — creation order).
  Remote + Global = open worldwide.
- **No dates in the API.** The date source is the CMS:
  `GET https://kontent-proxy.crossover.com/items?system.type=pipeline&elements.pipeline_status[contains]=active&elements=pipeline_code&kontent-cache=on`
  joins `pipeline_code` ↔ `ProductCode` and yields per-job
  `system.last_modified` (ISO-8601 UTC). It's last-modified, not
  posted; fall back to `int(ProductCode)` desc as creation order.
  The Kontent list is stale (281 items incl. hidden/closed jobs) —
  treat profile-api as the truth, Kontent for dates only.
- **Obstacles.** None technical: no auth, no bot protection, robots
  open, even the default urllib UA passes. The landmine is the ToS —
  it bluntly forbids "any automated means" and screen/database
  scraping. User's call.
- **Fit.** One engineering job open worldwide the day we checked
  ($15k/yr at that). Costs one request per run and catches the
  engineering postings when they return.

### dice.com

**not-a-fit · hard** (and bot-protected). Dead on both axes. The old
public API is gone; the current site serves listings inside Next.js
`__next_f` chunks — but every path 403s from this Lagos egress, even
in the real headed Chrome. It's an IP/geo WAF (Nigeria and datacenter
ranges blocked at the edge), not TLS fingerprinting; US residential
proxies plus curl-cffi impersonation are the only known-good route,
both outside stdlib. And even past the wall, Dice is a US-only board:
"remote" means US-remote (default search `countryCode2:"US"`,
JSON-LD `applicantLocationRequirements: USA`), so the
worldwide/EMEA filter discards essentially 100%. robots disallows
`/jobs?q*`. Don't build.

### euremotejobs.com

**static-html · easy.** WordPress + WP Job Manager, server-rendered.
The REST route for listings 401s and the homepage render pins
featured jobs — the real surface is the AJAX feed the homepage's own
JS calls.

- `POST /wp-admin/admin-ajax.php` with
  `action=erj_ajax_search&nonce=<from homepage inline JS>&page=N`,
  optionally `search_region[]=remote-jobs-worldwide&search_region[]=remote-jobs-emea`
  for a server-side region filter. Returns `{"data": {"html": "<60
  cards>"}}`, newest-first, ~130 jobs/day, clean pagination.
- Card parse: `a.job-card-link` → `h2.job-title`,
  `div.company-name`, `meta-location`/`meta-type`/`meta-level`, a bare
  `meta-item` matched on a currency regex = salary (~20%), and
  `time[datetime]` (date only; detail pages carry JobPosting JSON-LD
  with exact `datePosted` and per-country
  `applicantLocationRequirements`).
- **Traps.** Cloudflare 1010 bans the default urllib UA (and UAs
  with parentheses) — set a plain custom UA. The nonce is
  action-bound and expires roughly daily — on `success:false`
  refetch the homepage. Leave the `website` honeypot field empty.
  ToS explicitly prohibits automated collection; robots.txt, oddly,
  allows the admin-ajax endpoint and publishes sitemaps.
- **Fit.** 100% remote, EMEA-first, engineering/data/it/product
  categories — strong.

### eurotechjobs.com

**static-html · easy.** Laravel, server-rendered; even the default
urllib UA passes untouched. Filters are URL path segments:
`/job_search/workplace/remote` returns all 37 remote jobs on one
page; `/job_search/category/developer` all 195. Parse is an
h3-anchored regex per listing: title, `companyName`, `location`,
`bg-job-tag` badges (Remote), `Posted (today|N days ago)` — relative
dates only, so `published` is date-precision. No salary anywhere.

- **Traps.** The default `/job_search` page lists only the paid
  tiers (~23 jobs) and misses recent free listings — always use a
  filtered or category URL. Rate-limit header says 150 per (long)
  window; a run needs 1-2 requests. Some entries leak onto
  workplace/remote without being remote-wide ("Krakow, Poland") —
  re-check the location text.
- **Fit.** Europe-only, no US listings; "Remote, Multiple
  Countries" is the worldwide marker and "Remote, EMEA" exists
  explicitly.

### flexjobs.com

**bot-protected · hard.** Next.js SSR with jobs in
`__NEXT_DATA__.props.pageProps.jobsData` (50/page) and a
`/_next/data/<buildId>/remote-jobs/search.json` route — but Akamai
Bot Manager tarpits every plain-HTTP client: curl and urllib receive
zero bytes, ever. Only the real Chrome gets in, with per-route WAF
rules and velocity limits. And the paywall gates exactly what the
CLI needs: company name is null on ~99.6% of logged-out listings
(the page renders a literal "Company details here"); only 1,035
GJW-sitemap jobs carry company names. The true worldwide pool is 268
of 131,731 jobs. Don't build — the transport alone excludes stdlib,
and even in-browser the data is teaser-shaped.

### freelancer.com

**not-a-fit · easy.** The access is the easiest of the batch — an
official public API, `GET https://www.freelancer.com/api/projects/0.1/projects/active/`,
no auth, 200/min + 1000/hr limits, verified working with the
default urllib UA (flaky under bursts: intermittent empty bodies, so
pace and retry). Map `title`, `seo_url`, `submitdate` (epoch),
budget/currency for salary. But it's a gig marketplace: $10-250
micro-projects with 100+ bid wars, anonymized employers (`owner_id:
null` — no company field possible), ~0% full-time listings. A
different product from a salaried-jobs feed. ToS §33 technically
bans automated access even via the API. Skip unless the CLI ever
grows a contract-gigs mode, in which case it's a trivial build.

### freshersworld.com

**not-a-fit · easy.** Server-rendered PHP (Yii), no bot protection,
JobPosting JSON-LD with exact `datePosted` on detail pages, sitemaps
with 1,548+ active job URLs. But the content is India-only,
fresher-level, mostly onsite — even the "remote" jobs are
India-resident work-from-home
(`applicantLocationRequirements: {"name": "IN"}`). Zero listings
survive the default filter. ToS has an explicit anti-scraping
clause; robots disallows the listing paths (sitemaps and detail
pages are allowed).

### hrjobsremote.com

**not-a-fit · easy.** A Softr app over Airtable. The HTML is a
shell, but the frontend POSTs to a public unauthenticated
datasource endpoint on the site's own domain (all GUIDs recoverable
from the homepage HTML; POST-only, GET 500s). Airtable-shaped
records: `Position`, `Company`, `Region` (multi-select incl.
Worldwide/EMEA — zero EMEA records in practice), `Apply link`,
`createdTime`. All 569 records pulled: 60% Recruiter, zero
developer roles, 94% US. `createdTime` is the board's daily
bulk-refresh time, not the posting date. HR-only board — skip.

### inclusivelyremote.com

**public-api · easy.** WordPress with the full WP REST API exposed —
the cleanest WordPress case:

- `GET /wp-json/wp/v2/job_listing?per_page=100&after=<ISO>&job_listing_location=163,161&_fields=id,date_gmt,link,title,metas,class_list`
  — the location filter (163 = Remote Jobs Worldwide, 161 = Remote
  Jobs in EMEA) and the `after` recency filter work server-side.
  Metas carry `_job_employer_name`, `_job_location`,
  `_job_salary` (HTML-escaped — `html.unescape` it),
  `_job_apply_url`. `date_gmt` is UTC.
- **Traps.** Default urllib UA 403s — any custom UA passes. Always
  pass `_fields`: full records are ~100KB each. `class_list` carries
  location slugs (`job_listing_location-worldwide`) as a cross-check.
- **Fit.** Remote-only board; Engineering 63 open, AI 15, Product 42;
  EMEA 51 jobs, Nigeria 21; salary strings present ($230k/year
  RevenueCat role seen). Strong.

### jobgether.com

**public-api · easy.** The best board of the batch: a documented,
keyless public search API explicitly built for AI-agent consumption
(`/developers`, `/openapi.json`, `/llms.txt`, even an MCP server).

- `GET https://jobgether.com/api/v1/jobs?locations=emea&sort=date&limit=25&page=N`
  — `locations=emea` includes "Anywhere"-tagged jobs too, so one
  server-side param implements the whole default filter. Default UA
  passes. 60 req/min limit.
- Keys: `title`, `company`, `url`, `location` (comma-separated scope
  tags), `remote` ("Full Remote"), `experience`, `salaryRange`
  (~16%), `postedAt` (ISO-8601 UTC).
- **Gotchas.** `sort=date` must be explicit (default is relevance).
  `postedAt` is batch-ingestion time — jobs share timestamps, so
  don't read it as the employer's posting date. Multi-value filters
  AND together server-side (never combine OR-intended values).
  `salaryMin` filters silently re-rank on internal data. `keyword`
  stems loosely — "developer" matches "Business Development"; prefer
  `jobReferences` or filter client-side.
- **Fit.** ~40k IT listings, remote-first, strong EMEA/worldwide
  tagging. Easiest module of the whole set.

### jobs.ashbyhq.com — Ashby

**multitenant-ats · medium.** Greenhouse's problem, one size down.
Per-company boards at `jobs.ashbyhq.com/<company>` are JS shells;
the data comes from the official, documented, keyless posting API:

- `GET https://api.ashbyhq.com/posting-api/job-board/<slug>?includeCompensation=true`
  — whole board in one response, no pagination, 404 = dead slug,
  `{"jobs": []}` = live but empty. Keys: `title`, `location`,
  `secondaryLocations` (with `address.postalAddress.addressCountry`
  — "European Union", "Poland", "United Kingdom"), `department`,
  `employmentType`, `isRemote`, `workplaceType` ("Remote"),
  `publishedAt` (original publish date, ISO), `jobUrl`, and
  `compensation.scrapeableCompensationSalarySummary` (opt-in per
  company: Ramp/Notion/OpenAI fill it, Linear/ElevenLabs don't).
  No company name in the payload — needs a slug→name map.
- **The one trap:** the default urllib UA 403s the API host; any
  other UA passes. Same Cloudflare UA block as Dover.
- **Tenant discovery, solved via Wayback CDX:**
  `web.archive.org/cdx/search/cdx?url=jobs.ashbyhq.com/*&fl=original&collapse=urlkey&filter=original:^https?://jobs\.ashbyhq\.com/[^/?]{2,50}/?$`
  → 5,744 distinct board slugs; ~60-66% live on a 50-sample check.
  Slugs are case-insensitive and unpredictable (`1Password` works,
  `figma` 404s). No sitemap, no global list.
- **Fit.** Strong on the right boards: ElevenLabs 48 EMEA-remote of
  99 remote, Linear 11, Zapier 4 of 10. OpenAI/Ramp are mostly
  US-remote noise. Same shape as greenhouse: per-board fetch is
  easy, breadth is the request-volume question — the
  search-as-limiter plan transfers directly.

### kickresume.com/jobs

**public-api · easy.** A React shell over a public DRF API — and the
board is a cross-ATS aggregator: 4.9M listings mirrored from
Greenhouse, Lever, Deel, Workable, etc., normalized in one place.

- `GET https://www.kickresume.com/api/job-posts/?work_arrangement=remote&q=engineer&limit=100`
  (+ one `country=` param per EMEA country; repeated params OR).
  Keys: `title`, `organization`, `url` (external ATS page),
  `date_posted`, `locations_derived`/`countries_derived` (nullable),
  `ai_work_arrangement`, `ai_salary_minvalue`/`maxvalue`/`currency`,
  `ai_visa_sponsorship`. No auth; default urllib UA passes.
- **Traps.** `limit` ≤ 100, `offset+limit` ≤ 10,000. Sort
  client-side (`q` switches the server to relevance). Sequential or
  ≤5 concurrent — 10-way bursts all 500. Plain-name `company=` params
  return nothing; semicolon lists don't work, repeated params do.
  `posted_since_hours` accepts only 24/168/720.
- **Worth knowing.** It duplicates what greenhouse/lever modules
  would fetch tenant-by-tenant, but normalized and searchable — one
  request replaces thousands of per-tenant fetches. No "worldwide"
  flag: remote jobs with null locations are ambiguous (5-7%), so
  EMEA comes from `country=` ORs plus the existing location
  heuristics.

### lever.co — Lever

**multitenant-ats · medium.** Same class as greenhouse/Ashby.
Official public per-board API, no auth:

- `GET https://api.lever.co/v0/postings/<slug>?mode=json` (EU boards:
  `api.eu.lever.co`) — bare JSON array, `limit=100`/`skip` paging.
  Keys: `text` (title — not `title`), `createdAt` (epoch
  milliseconds), `categories.location`/`allLocations`, `country`
  (ISO alpha-2), `workplaceType`
  (unspecified/on-site/remote/hybrid), `salaryRange` (sometimes),
  `hostedUrl`. Company name is not in the payload — derive from the
  slug. Default list order is not date-desc. The API host accepts
  the urllib UA; the HTML host (`jobs.lever.co`) Cloudflare-403s it —
  use the API.
- **Tenant discovery:** no global list, no sitemap, root 302s to
  marketing. Wayback CDX (`url=jobs.lever.co/*`, anchored regex
  `^https://jobs\.lever\.co/[^/?]+/?$`) yields ~7,100 candidate
  slugs (6,959 + 139 EU); a 60-sample check says ~30% live ≈ ~2,000
  boards. 404s are clean JSON, so pruning is cheap. Big tech churns
  (archived OpenAI/Netflix boards are dead).
- **Fit.** Real EMEA supply exists (Spotify: 27 remote, London roles
  with `country: "GB"`), but live boards skew US — the filter does
  real work. robots: `Allow: /`, `Crawl-delay: 1`.

### linkedin.com

**static-html · medium.** The guest surface is open, logged-out, and
server-rendered — but it's the ToS-hostile board of the set.

- `GET /jobs-guest/jobs/api/seeMoreJobPostings/search?keywords=…&location=…&f_TPR=r604800&start=N`
  — 10 cards per call (~1,000 per query ceiling); the full SERP
  `/jobs/search` gives 60 cards. Cards parse with stdlib: title
  (`h3.base-search-card__title`), company (`h4…subtitle a`),
  location span, `time@datetime` (real ISO date). Detail pages add
  description text only.
- **Traps.** ~1 in 20 responses is an anti-bot interstitial served
  as HTTP 200 with no cards — validate for `base-search-card`,
  retry once. `f_WT=2` (the remote filter) is silently ignored;
  `sortBy` too; `f_TPR` (recency) is honored. So "remote" is
  heuristics: query variants, location text, description regex.
  Salary effectively never appears in cards.
- **The wall that matters is legal:** robots disallows everything
  (`User-agent: * / Disallow: /`), and LinkedIn litigates — hiQ, and
  Proxycurl was shut down in July 2025. A low-volume, personal,
  paced scraper is the only defensible posture, with a
  remote-filter heuristic of known false rates. Build last, if at
  all.

### remocate.app

**static-html · easy.** Next.js, fully server-rendered; plain curl
gets the cards. One aggregator board, robots permissive, no
protection, no anti-scraping clause in the terms.

- `GET https://www.remocate.app/?workType=remote&fresh=week`
  (+ `&category=development`, `&country=europe`, `&page=N`, 20 per
  page). Cards: `<h2>` title, following `<p>` company, `<time
  datetime>` (UTC — it's remocate's daily ~05:30 UTC sync time,
  first-seen not posted), a salary span
  (`title="Salary from the job post"`), and badges: Remote /
  Relocation, category, and country — **no country badge = World**
  (verified against JSON-LD `applicantLocationRequirements:
  "World"`).
- Don't cut the HTML at the first `<script>` — RSC chunks are
  interleaved with markup; use a real parser. Filtered queries 404
  past their last page — stop on 404. Every request hits origin
  (`cache-control: no-store`), so keep it to a few per run.
- **Fit.** Development is the largest category (1,036 jobs); salary
  display strings in mixed €/$; Europe/World/country eligibility is
  a first-class badge. Strong, easy build.

### remote.co

**public-api · medium.** Same BOLD/FlexJobs portal as flexjobs.com,
free sister site. Undocumented but open JSON API found in the JS
bundle:
`GET https://remote.co/ej/api/v1/jobs/search?page=1&size=2000&categories=48&sortbyposteddate=true`.
Akamai fingerprint checks make it reachable only over HTTP/2 with a
full Sec-Fetch header set — stdlib urllib is mathematically excluded
(HTTP/1.1 only); it would need a `subprocess` curl call. Keys map
cleanly (`title`, `postedDate`, `allowedCandidateLocation`,
`remoteOptions`, `salaryRange`, `slug`), but like FlexJobs,
`company` is null for ~99% of records (the "Company details here"
wall) — unrecoverable without an account. Developer category: 1,100
jobs, ~12/day, of which 7 are tagged Work from Anywhere; EMEA
appears as country lists inside `allowedCandidateLocation`. Default
sort is relevance — `sortbyposteddate=true` is required. ToS bans
scraping outright. Marginal: salary strings are its one distinctive
data point, and company=None is the real disqualifier.

### remotefrontendjobs.com

**public-api · easy.** Publishes its own documented free API
(advertised at `/developers`), with RSS and Markdown variants:

- `GET https://www.remotefrontendjobs.com/api/jobs?board=frontend&regions=europe,africa,middle-east,worldwide&limit=100`
  — the `regions` param does the default filter server-side. Keys:
  `title`, `author` (company), `isoDate` (ISO), `location`,
  `geoRegions`/`geoLabel`, `salaryMinYearly`/`MaxYearly` (~29% of
  listings), `seniority`, `forcedRemote`. Usage terms require
  linking to their canonical `/{id}` page, not the employer ATS
  `link`.
- **Traps.** Cloudflare 403s the default urllib UA — one header fix.
  24-hour Pro holdback: nothing public is fresher than ~1 day
  (`postedWithin=24h` returns 0 by design). Salary parsing can be
  bogus ("1k") — sanity-check. Dedupe aggregator cross-posts (the
  same Jobgether listing repeated 4×). `offset+limit` ≤ 250.
- **Fit.** Frontend/full-stack web only — narrower scope than the
  CLI's, but the seniority mix skews senior (47 of the latest 100)
  and EMEA geo tags dominate. One API serves a 3-board network
  (remotebackendjobs.com, remotejavajobs.com) via `board=`.

### remoteok.com

**public-api · easy.** The famous feed is still open:
`GET https://remoteok.com/api` → a 100-element array, `[0]` a
`{last_updated, legal}` meta, the rest jobs, newest-first. Keys:
`position`, `company`, `date` (ISO), `salary_min`/`max` (ints,
unitless, 0 = missing; one employer posts 9-figure local-currency
values — format raw or clamp), `location` (free text, 50% empty =
worldwide; the JSON-LD hardcodes "Anywhere", so filter on the API
location text), `tags` (`dev`, `digital nomad`), `url`. No params,
no pagination — fixed at the newest ~99.

- **Traps.** Cloudflare 1010 bans the default urllib UA — one
  header fix. ~5% of records have double-encoded UTF-8 (`â`, `Ã`)
  — repair via a latin-1→utf-8 round-trip. The `legal` field
  demands a follow link-back and a "Remote OK" source mention — the
  CLI carries the `url` field anyway; mention the source in
  credits.
- **Fit.** Remote-only by definition, dev-skewed (40/99 `dev`-tagged
  on check day), healthy worldwide/EMEA share, ~10% US-only noise
  for the filter to drop.

### simplyhired.com

**static-html · medium.** Alive in 2026 — the shutdown rumor traces
to a dissolved UK shell entity, not the platform. It's now an
Indeed-family skin: Next.js SSR with jobs in
`__NEXT_DATA__.props.pageProps.jobs` (20/page) and base64
`pageCursors` pagination. Keys: `title`, `company`, `location`,
`remoteAttributes`, `salaryInfo` ("$120,000 - $145,000 a year"),
`dateOnIndeed` (epoch ms — the site's own display freshness),
`jobKey`, `botUrl` (use this — never the robots-disallowed
`encodedUrl`/`/out?r=` tracker). `/search` and `/job` paths work
with a browser UA; the homepage 403s.

- **Traps.** Server-side sort `s=d` silently no-ops — sort
  client-side by `dateOnIndeed`. Filter URL params are undiscovered
  — all filtering is client-side. 24 country domains (no Nigeria);
  the `.com` remote pool is US-anchored and EMEA lives on country
  domains (`.co.uk` verified: Monzo, GitHub, GBP salaries) — so EMEA
  coverage means querying 2-4 domains. Dedupe against any future
  Indeed module: it's the same index.
- **Fit.** Workable but the USiest profile of the batch. Medium
  effort for medium reward; GPTBot/CCBot are robots-banned,
  `/search` allowed.

### totaljobs.com

**bot-protected · hard.** Akamai blocks every client from this
egress: curl with full browser headers, and even the real headed
Chrome, all get 403 Access Denied on every path including
robots.txt. Wayback's own crawlers started getting 403s in Dec 2025.
The data shape (from archives) would map 1:1 — React SSR with
`window.__PRELOADED_STATE__` items carrying `title`, `companyName`,
`workFromHome` ("Partially remote"), `publishFromDate`, free-text
GBP salary — but the transport is impossible from here, and the
board is UK-only (UK remote = UK right-to-work, which doesn't help
a Lagos-based candidate). Don't build; if ever included, return
`[]` gracefully on 403.

### wellfound.com

**static-html · easy.** Startup jobs, fully server-rendered: the
listing pages embed the whole Apollo cache in `__NEXT_DATA__`.
`GET https://wellfound.com/role/r/<role>?page=N` (roles:
software-engineer 1,812 remote, product-manager, ai-engineer,
machine-learning-engineer, data-engineer, frontend-engineer) with a
browser UA — the default urllib UA 403s; any browser UA passes via
plain urllib. Jobs are `JobListingSearchResult` entities; company
comes from joining `StartupResult.highlightedJobListings` refs.
Keys: `title`, `liveStartAt` (unix seconds), `locationNames`,
`acceptedRemoteLocationNames` ("Europe", "Nigeria", "Asia", …),
`remote` (bool — reliable; ignore `remoteConfig.kind`),
`compensation` ("$73k – $73k • 0.4% – 0.75%" — salary + equity in
one string), `jobType`, `yearsExperienceMin`. URL:
`https://wellfound.com/jobs/{id}-{slug}`. Dedupe by `id` (promoted
listings repeat); `?page=2` works server-side; sort by `liveStartAt`.

- **The one gotcha that matters:** empty `acceptedRemoteLocationNames`
  does NOT mean worldwide — spot-checked detail pages say US-only.
  Treat empty as US/unknown. `/role/l/<role>/europe` landing pages
  exist for an EMEA-native feed. Never touch `/graphql` or
  `/company/*` — those are Cloudflare-challenge-gated, and never
  needed.
- **Fit.** Strong: startup roles, equity strings, remote EMEA
  accepted-locations, postings dated today. robots allows the
  listing pages; ToS has only a generic content clause.

### weworkremotely.com

**public-api · easy.** The per-category RSS feeds are the official,
advertised interface — and they're a complete census, not a recent
slice: `GET /categories/<slug>.rss` returns every active listing in
the category (full-stack feed: 104 items = the site's own "View all
104 jobs" — no pagination). Categories that matter: full-stack,
back-end, front-end, devops, product. The site-wide
`/remote-jobs.rss` is capped at 100 — don't use it. The `.json`
endpoints are dead (406); the official REST API is token-gated and
unnecessary.

- Item shape: `<title>` is "Company: Title" (split on the first ": "),
  `<region>` is the location restriction — the field the default
  filter wants: "Anywhere in the World" 73%, "USA Only" 19%,
  Europe/EMEA variants rare but present, plus flag-emoji country
  lists in `<country>` (a job listing ~80 countries is de-facto
  worldwide). `pubDate` is RFC-822 UTC. No salary element — prose
  only.
- **Traps.** The default urllib UA 403s even the feeds — send a
  browser UA. HTML surfaces (homepage, search, sitemap) sit behind
  a Cloudflare managed challenge; the feeds don't. The region tag
  occasionally lies ("U.S. Based, Remote Contractor" tagged
  Anywhere) — a light title/description regex catches the worst
  cases; skip WWR's own test posts ("Do Not Apply").
- **Fit.** ~160 live dev listings across the four engineering
  categories, ~74% survive a worldwide+EMEA filter. `/api-terms`
  says API consumers may only use API data — but the feeds are the
  site's own published interface, robots-allowed; keep apply links
  routed through WWR.

## Summary

| board | category | difficulty | one-line verdict |
|---|---|---|---|
| jobgether.com | public-api | easy | documented AI-agent API; `locations=emea` is the default filter |
| weworkremotely.com | public-api | easy | RSS feeds are a complete census; `region` field is first-class |
| remoteok.com | public-api | easy | one request, newest ~99; attribution required |
| inclusivelyremote.com | public-api | easy | WP REST API; server-side location + recency filters |
| app.dover.com | public-api | easy | cross-tenant feed — the ATS that avoided the greenhouse problem |
| remotefrontendjobs.com | public-api | easy | documented API; frontend-only scope, skews senior |
| crossover.com | public-api | easy | one request; blunt ToS; thin engineering volume right now |
| kickresume.com/jobs | public-api | easy | cross-ATS aggregator (4.9M listings) with normalized fields |
| remote.co | public-api | medium | open API but Akamai needs HTTP/2 (no urllib); company=null ~99% |
| euremotejobs.com | static-html | easy | WordPress AJAX feed + nonce dance; EMEA-first |
| eurotechjobs.com | static-html | easy | path-segment filters; all remote jobs on one page |
| remocate.app | static-html | easy | SSR cards; country badge is eligibility (missing = World) |
| wellfound.com | static-html | easy | Apollo cache in `__NEXT_DATA__`; equity strings |
| simplyhired.com | static-html | medium | Indeed-skin `__NEXT_DATA__`; sort no-ops; EMEA = country domains |
| linkedin.com | static-html | medium | guest endpoints open; remote filter dead; ToS litigates |
| jobs.ashbyhq.com | multitenant-ats | medium | official per-board API; CDX 5,744 slugs, ~60% live |
| lever.co | multitenant-ats | medium | official per-board API; CDX ~7,100 slugs, ~30% live |
| flexjobs.com | bot-protected | hard | Akamai tarpits plain HTTP; company=null anyway |
| totaljobs.com | bot-protected | hard | Akamai 403s everything from here; UK-only anyway |
| dice.com | not-a-fit | hard | IP/geo WAF + US-only inventory |
| freelancer.com | not-a-fit | easy | gig marketplace, anonymized employers |
| freshersworld.com | not-a-fit | easy | India fresher, mostly-onsite board |
| hrjobsremote.com | not-a-fit | easy | HR/recruiting jobs only, 94% US |

### Suggested build order

1. **jobgether** — the filter is a URL param and the docs welcome
   scrapers. Start here.
2. **weworkremotely** — feeds are a census; the `region` field does
   the filtering; send a browser UA.
3. **remoteok** — single request; carry the attribution requirement.
4. **inclusivelyremote** — WP API with server-side location and
   recency filters.
5. **wellfound** — one UA header and one embedded-JSON parse; equity
   strings are a distinctive data point.
6. **remocate** — static parse; badges give eligibility directly.
7. **eurotechjobs** — one filtered URL gets every remote job.
8. **euremotejobs** — the nonce dance, but strong EMEA-first content.
9. **remotefrontendjobs** — cleanest terms of the batch, narrow
   scope.
10. **crossover** — one request per run; weigh the ToS first.
11. **kickresume** — cross-ATS aggregator; overlaps what
    greenhouse/lever modules would do, but in one normalized place.
12. **remote.co** — marginal; company=None on ~99% is the
    disqualifier.
13. **simplyhired** — multi-domain EMEA queries; dedupe against any
    future Indeed module.
14. **linkedin** — ToS-hostile; build last, if at all.
15. **lever / ashby** — greenhouse-class; apply the
    search-as-limiter plan from docs/scrapers.md "Investigating"
    (Brave `site:` queries name the tenants, then fetch only those
    boards).
16. Skip: dice, flexjobs, freelancer, freshersworld, hrjobsremote,
    totaljobs.

### What cuts across the findings

- **The default urllib UA is the most common wall.** Eight boards
  (remoteok, weworkremotely, inclusivelyremote, wellfound,
  simplyhired, euremotejobs, ashby, and remote.co — which needs
  HTTP/2 besides) block or exclude `Python-urllib/*` specifically.
  One shared browser-UA constant fixes seven of them at once; only
  remote.co needs more.
- **Three boards are unreachable from this network at all** — dice,
  flexjobs, totaljobs. Edge IP/geo walls that stop real Chrome too;
  nothing stdlib can do from a Lagos egress.
- **No board landed in js-rendered.** Every JS shell we met —
  crossover, jobgether's HTML, kickresume, wellfound, ashby, dover —
  had a public API or SSR data underneath. The browser was needed to
  find endpoints, never to fetch the data: every module runs
  browserless.
- **The ToS spread is wide.** jobgether, remotefrontendjobs, and
  remoteok explicitly welcome programmatic use (remoteok demands
  link-back attribution). crossover, euremotejobs, remote.co, and
  freelancer have blunt anti-scraping language. linkedin is the only
  one that litigates.
- **Four boards implement the default filter server-side** —
  jobgether (`locations=emea`), weworkremotely (`region`),
  remotefrontendjobs (`regions=`), inclusivelyremote (location
  taxonomy). Everywhere else it's the client-side vocabulary already
  worked out for greenhouse in docs/scrapers.md.
- **The multitenant class grew to three**: greenhouse (tabled, ~10k
  candidates), lever (~7,100 candidates, ~2,000 live), ashby (5,744
  candidates, ~60% live). Dover shows the escape hatch — some ATSes
  ship a cross-tenant feed, so always check for one before building
  CDX discovery. For lever and ashby, the search-as-limiter plan
  transfers directly.
