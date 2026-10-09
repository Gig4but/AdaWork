# AdaWork — Implementation Plan

Universal, local-first app for open job positions in Czechia (all industries, not only IT).

Three main modules:
1. **Scraper**: collects job ads from portals, official data, LinkedIn, Facebook,
   Instagram and forums.
2. **Web interface**: search and filters, map, job detail, and a **commute window**
   (car / public transport / walking / bike, with a travel-time slider that also filters
   the listings).
3. **Employer reviews**: an employer card built from employee reviews, maps ratings, social
   media and the web, plus official checks (company register, insolvency, VAT reliability,
   agency licence).

Shared services: a small **local AI model** (reads messy ads and posters, summarizes
reviews) and a **local routing engine** (travel times and routes, offline maps).

Status: **M0–M6 done** (2026-10-07); next is M6b. Spike results: `docs/spikes.md`.

---

## 1. Goals and non-goals

### Goals
- Collect open positions for **any profession** from the top 10 Czech job sites, LinkedIn,
  Facebook (groups, Marketplace), Instagram, public forums and the official Úřad práce data.
- **As simple as possible for the user:**
  - one installer, no Python, no Ollama, no browser extension, no command line;
  - one **first-run setup wizard** that asks only what the app cannot work out itself
    (home address, AI model choice, optional logins and API keys), with defaults for
    everything else;
  - after setup the app can run **on its own**:
    - scheduled scraping every 6, 12, 24 or 48 h (**off by default**, one switch to turn on);
    - AI processing, employer checks and notifications about new matches run automatically
      after each scrape, whether it was started manually or by the schedule.
- Prefer, in this order: official/free APIs and open data → existing open-source libraries →
  one generic standards-based extractor (schema.org `JobPosting`) → custom code only when
  nothing else works.
- Normalize every posting into one schema, so filters work across sources.
- Show **how long the commute takes** by car, public transport, walking or bike, and filter
  jobs by "reachable from my starting point within N minutes".
- Show **what people say about the employer**, with sources, and flag official warning signs.
- No paid services required. Data stays on the computer.

### Non-goals (for v1)
- Applying to jobs, CV handling, multi-user hosting, cloud deployment.
- Bypassing CAPTCHAs, paywalls or security checks; storing passwords.
- Live traffic for car travel times (times are without traffic).

---

## 2. Research findings (verified 2026-10-06)

### 2.1 Job portals and official data

Verified live in the M0 spike (`docs/spikes/sources.md`, 2026-10-06). No source needs a browser.

| Source | Access method | Verified facts |
|---|---|---|
| **MPSV / Úřad práce ČR open data** | Free JSON, no key | **`volna-mista.json.gz` (17.5 MB**, the plain JSON is 187 MB), updated **daily**, ETag → 304. 39,487 records. CZ-ISCO, salary range, hours, employer **with IČO**, workplace (72 % with a RÚIAN address code), flags, 21 codebooks. Structured → **no AI needed**. Detail link: `up.gov.cz/volna-mista-v-cr#/volna-mista-detail/<portalId>`. **Implemented in M1.** |
| **python-jobspy** (MIT, v1.2.0, 2026-10-02) | Python library | Indeed and LinkedIn both returned results without errors. **Not used** (§14): jobspy reads Indeed through `apis.indeed.com/graphql` (robots `Disallow: /`) and pulls in pandas; LinkedIn is read by an own small adapter on the same guest pages (robots.txt disallows `/jobs-guest/`, so off by default). |
| **Indeed CZ (HTML)** | Public search page `cz.indeed.com/jobs` | Allowed by robots.txt; 15 job cards per page as embedded JSON. **But** the page answers an honest User-Agent with a Cloudflare "Security Check" (403, checked 2026-10-06); the spike got through only with a browser-like User-Agent. **Link-out only** (§14). |
| **prace.cz** (19.6k ads), **jenprace.cz** (24.1k) | JSON-LD `JobPosting` on detail pages | prace.cz robots disallows `/search/`, `/hledat/` (not needed). jenprace.cz redirects `/nabidky` by Geo-IP → discover via explicit town slugs. prace.cz shares ads with jobs.cz. |
| **profesia.cz** (1.4k) | **Microdata** `JobPosting` (not JSON-LD) | Branded employer templates have no structured data → meta tags + AI fallback. |
| **jobs.cz** (16.5k) | **Server-rendered HTML** (not JS-only) | 30 cards per listing page, stable `data-test="jd-*"` attributes on detail, no JSON-LD → custom HTML adapter, no browser. robots disallows `/api/`, `/iapi/`. |
| **startupjobs.cz** (0.4k) | Offers sitemap → JSON-LD on detail pages | Listing pages are client-rendered and carry no ads. Only portal with GPS. |
| **prace.bazos.cz** (6k) | Plain HTML | Very messy ads; crawl the 24 category pages (robots disallows search params), filter by PSČ locally. |
| **hitprace.cz** | JSON-LD on detail pages | Aggregator that re-lists other portals → heavy duplicates. |
| **dobraprace.cz** (7.7k) (= jobsik.cz, same operator) | Ads sitemap → JSON-LD | jobsik.cz skipped at first (same inventory). |
| **fajn-brigady.cz** (2.1k) | Detail sitemap with `lastmod` → rule-based HTML parser | |
| ~~volnamista.cz~~ | — | **Dropped**: robots.txt allows only named search engines (`User-agent: * Disallow: /`). |
| **pracezarohem.cz** (21.5k) | Listing pages carry `__NEXT_DATA__` JSON (50 ads/page) | Checked 2026-10-06, robots.txt allows listings. GPS, salary text, IČO inside `ga.itemBrand`. Part of the inventory is re-listed from profesia.cz (`PROF…` IDs). M6, confirmed. |
| **inwork.cz** (31.9k) | Detail sitemap → HTML (no structured data) | Checked 2026-10-06, robots.txt allows details. Custom HTML parser. M6, confirmed. |
| **easy-prace.cz** (10.4k own + 24k re-listed) | Sitemaps → HTML (no structured data) | Checked 2026-10-06, `Allow: /`. Only the own ads (`/nabidka/…`) are worth reading; `/volne-misto/…` re-lists Úřad práce. M6, confirmed. |
| **personalka.cz** (40.6k) | Sitemaps → HTML | Checked 2026-10-06, robots.txt allows details. Titles are CZ-ISCO names: probably re-listed Úřad práce ads. Check the share of new ads before building. |
| **Jooble** / **Careerjet** | Free APIs with key | Careerjet API v4: key as HTTP Basic user, `user_ip` + `user_agent` required. Jooble: request limit unconfirmed (sign-up page behind Cloudflare). |
| `outpost2026/MCP-Jobs` | GitHub, no license | Reference only. |

### 2.2 Social media and forums

| Where | Logged-out access | Approach |
|---|---|---|
| Facebook groups | No (a logged-out request returns a ~1.5 KB stub page) | Automatic reader with the user's own login |
| Facebook Marketplace | Search needs login; the jobs category exists only in the US, so in CZ jobs are ordinary listings | Same reader, keyword searches near home |
| Instagram | Login wall; Graph API needs a Business account + Meta developer app | Same reader with the user's Instagram login |
| Public forums | Usually open | Generic forum source + AI |

Facebook's robots.txt disallows all crawlers and prohibits automated collection without
permission. Automatic reading through a logged-in session **breaks Meta's terms and can get
the account restricted**. Facebook and Instagram are therefore **off by default**. The wizard
shows a visible warning, and the user must switch them on and confirm (§7.1 step 4).

### 2.3 Top 10 Czech job sites

Based on the jenprace.cz overview (18 Sep 2026, written by a competitor) and traffic knowledge:
jobs.cz, prace.cz, jenprace.cz, ~~volnamista.cz~~ (robots.txt blocks it, replaced by
dobraprace.cz), profesia.cz, Indeed CZ, LinkedIn, hitprace.cz, bazos.cz – práce,
startupjobs.cz; plus fajn-brigady.cz, MPSV, Facebook, Instagram, forums (11 verified sections in
`resources/forums.yaml`), Jooble, Careerjet.

### 2.4 Employer reviews and checks

| Source | What it gives | Access | Verified facts / notes |
|---|---|---|---|
Verified live in the M0 spike (`docs/spikes/employers.md`).

| Source | What it gives | Access | Verified facts / notes |
|---|---|---|---|
| **ARES** (official business register) | Name, IČO, DIČ, legal form, founding date, address, NACE, per-register status incl. **insolvency flag** (`stavZdrojeIr`); RES endpoint: **employee band** + main NACE | Free REST API, no key | Detail by IČO works. Name search is a word-based AND match (no prefix/fuzzy, no "ČR" expansion), **sorted by IČO, not relevance**, > 1000 hits → HTTP 400. RES accepts a batch of ≤ 100 IČOs. Blocking possible above 500 req/min. |
| **ISIR** (insolvency register) | Proceedings and their state | Free SOAP `isir.justice.cz:8443/isir_cuzk_ws` | Works with raw SOAP via httpx (no zeep). Strict element order, no empty elements. ≤ 50 req/min, 3000/day → called only when ARES `stavZdrojeIr` is not `NEEXISTUJICI`. |
| **Unreliable VAT payer register** (Ministry of Finance) | "Nespolehlivý plátce DPH" | Free SOAP (ADIS) | **Daily bulk list** (`getSeznamNespolehlivyPlatce`, 0.5 MB, ~4 300 DIČs) → local lookup by DIČ from ARES. |
| **MPSV register of employment agencies** | Is the agency licensed, for what (incl. temp staffing) | Open data **JSON**, `data.mpsv.cz/od/soubory/agentury-prace/agentury-prace.json` (4 MB, daily, ETag) | Lists only agencies with a valid permit; most permits have **no end date**, so "valid until" is often unknown. |
| **Atmoskop.cz** (Alma Career) | **Employee reviews**: **% satisfaction** (employer / colleagues / boss), counts, review texts with dates — **no stars** | `__NEXT_DATA__` JSON in each page; sitemap `sitemap.xml` (37 664 profiles) | No IČO and no usable search → matched via a sitemap slug index (`slugify(ARES name)` hit 6/6 big employers). robots allows profiles (disallows `/graphql/`). Reviews paginate via `/hodnoceni/N`. |
| **Indeed CZ company pages** (`/cmp/<name>/reviews`) | Employee reviews, mainly large companies | — | **Cloudflare 403 challenge for plain HTTP** → v1 shows only a link (found via ddgs). |
| **Google Maps rating + reviews** | **Customer** reviews of a shop/branch (not employees), rating and count | Official Places API (New), **optional key** | IDs-only Text Search is free and unlimited; Place Details with `rating` = Enterprise SKU, with `reviews` = Enterprise + Atmosphere SKU, **1 000 free/month each**. Needs a billing account (card). Max 5 reviews per place. |
| **Facebook pages** | Recommendations, comments about the employer | Social reader (if logged in) | Only for employers the user looks at, low volume. |
| **Web mentions** (forums, news, discussion sites) | "Zkušenosti", complaints such as unpaid wages | **ddgs** 9.16 (`backend="auto"`) + fetch top results + AI | Good for big employers, noisy for small ones; **transient empty results** → 2–3 retries with backoff, daily cap, 30-day cache. Also surfaces jobinsider.cz, jenfirmy.cz, vimvic.cz, kdepracujeme.cz (terms not yet reviewed). |
| Firmy.cz / Mapy.cz reviews | Customer reviews | — | **Excluded**: robots.txt disallows all bots except search engines. Shown as a link "open in Mapy.cz". |
| Glassdoor | Employee reviews | — | Excluded: login wall. |
| SÚIP labour inspections | Fines | — | Only yearly summary reports, no per-employer open data → not available. |

### 2.5 Routing, public transport, maps

Verified in the M0 spike (`docs/spikes/routing.md`): **GO for MOTIS**, with the conditions below.

| Component | Facts |
|---|---|
| **MOTIS** (MIT, v2.11.3, 2026-09-10) | `motis-windows.zip` (51.7 MB) runs as is. Walk, bike, car, public transport, one-to-many, one-to-all, geocoding, vector tiles + offline fonts all work. **No isochrone polygon endpoint**: the reachable area is built from one-to-all results. Whole CZ: import 7 min, peak 5.5 GB RAM, 4.0 GB data; server 0.5–1.6 GB; 5,000 destinations by car 2.5 s, transit plans/tiles/geocoding 20–100 ms. Call `127.0.0.1` (not `localhost`, +2 s), tiles need `Accept-Encoding: gzip`, raise `onetomany_max_many`. |
| **OpenStreetMap Czech extract** (Geofabrik) | `czech-republic-latest.osm.pbf`, 948 MB. |
| **Spojenka GTFS** (nationwide, rebuilt daily, 14 days validity) | CIS JŘ + PID + SŽ + regional data merged **with stop coordinates**: 93k stops, 370k trips, 85 MB. **The only nationwide feed that routes correctly.** License: **non-commercial use only** → fine, the app stays personal/free (§14). Needs sanitizing before import (drop in-seat transfers, otherwise MOTIS loops). |
| CIS JŘ NeTEx / JDF (`ftp.cisjr.cz`) | NeTEx loads but is **unusable**: rail stops have no coordinates, bus/urban stop IDs collide across files. JDF converts to GTFS easily (converters exist), but has no stop coordinates either. Not used while Spojenka is available. |
| Regional GTFS | PID (Prague + Central Bohemia), IDS JMK (South Moravia) work directly; fallback if Spojenka disappears. tangero CC BY 4.0 aggregate not tried. |
| **osmium-tool** 1.19.1 (GPL-3.0, separate 9 MB exe from conda-forge) | Clips OSM to 150 km (16 s) and **renumbers IDs (22 s)**, which is what lowers MOTIS import RAM on 8 GB machines (clipping alone does not). |
| Mapy.com REST API | Plan B only: car/foot/bike routes and matrix (≤ 100 pairs), no public transport, no isochrones. |
| Deep links | Mapy.cz, IDOS and Google Maps route links (no API, no key): "open this route in…". |
| **RÚIAN address points** (ČÚZK, monthly 63 MB ZIP) | Exact job coordinates from MPSV's RÚIAN address codes, municipality centres for the rest (96.7 % of MPSV jobs placed). **Implemented in M2.** MOTIS geocoding is used only for free-text addresses. |

**Key insights:**
- MPSV gives broad structured job data for free.
- One JSON-LD extractor covers several portals.
- **MOTIS** covers all commute needs locally (car, public transport, walking, bike, isochrones,
  offline map), with no API key, and **ARES + Atmoskop + ddgs** give useful employer
  information without any key.
- Google ratings are the only part that needs a key, and they are optional.

---

## 3. Technology choices

### 3.1 What the user gets (no separate installs)

| Concern | Choice |
|---|---|
| Installer | **Inno Setup** `AdaWork-Setup.exe` wrapping a **PyInstaller** build (Python inside). |
| App window | **pywebview** on Edge WebView2 (part of Windows 11). |
| Background | **pystray** tray icon, optional start with Windows, Windows toast notifications. |
| AI runtime | **llama.cpp `llama-server`** bundled (Vulkan build for integrated Intel/AMD graphics + CPU build). Started on demand, stopped when idle. Ollama usable instead (advanced setting). |
| AI models | Chosen in the wizard (§6.6), downloaded automatically from Hugging Face (GGUF). |
| Routing engine | **MOTIS** Windows binary bundled; data (OSM + timetables) downloaded and imported in the background on first run, refreshed automatically. |
| Map | **MapLibre GL JS** with **vector tiles served locally by MOTIS** → works offline. |
| Browser automation | **Playwright** with the installed **Microsoft Edge** (`channel="msedge"`). |
| OCR | The vision model transcribes images (M5: ~30 s per poster with the 2B model); **RapidOCR** (ONNX) only if slow PCs need it. |
| Embeddings | `multilingual-e5-small` (int8 ONNX, 118 MB) run directly with onnxruntime + tokenizers (no fastembed). |
| Web search | **ddgs** (metasearch, no key). |
| Secrets | **keyring** → Windows Credential Manager. |

### 3.2 Hardware: dynamic adaptation, 8 GB as the minimum

Hardware varies a lot between users, so **nothing is hard-coded to one machine**. A
`HardwareProfile` is measured on the user's PC, and the app derives every resource-related
setting from it (§3.2.1).

**Minimum test machine (confirmed):** **8 GB RAM**, a common budget AMD Ryzen (e.g. Ryzen 5
5500U / 7520U class, 4–6 cores) with integrated Radeon graphics (shares system RAM), SSD.
The app must run well here; it is the lower bound for acceptance tests. Stronger machines get
bigger models, parallel work and larger routing areas automatically.

#### 3.2.1 What is decided dynamically

| Setting | Derived from | Examples |
|---|---|---|
| **AI model tier** (Auto mode, default) | RAM, GPU/VRAM, CPU, disk, measured speed | 8 GB iGPU → Simple; 16 GB → Good; 32 GB or a GPU with ≥ 8 GB VRAM → Excellent |
| GPU offload, threads, batch, context size | GPU type and VRAM, CPU cores | Dedicated GPU → all layers on the GPU; iGPU → partial offload; CPU only → threads = physical cores |
| **Parallel heavy jobs** | free RAM after loading the model | 8 GB → one at a time; 16 GB → model + routing together; 32 GB → model + routing + browser |
| **Routing area** | RAM and free disk | 8 GB → ~150 km around home; ≥ 16 GB and enough disk → whole Czech Republic (no re-import when the address changes) |
| AI queue scope | measured seconds per ad | Slow → only ads matching the profile; fast → all incomplete ads |
| Images with the vision model | measured seconds per image | Slow → fewer images per run; fast → every image ad |

**When it is re-evaluated:**
- **At every start:** a quick probe (< 1 s) of RAM, GPU, CPU and disk. If the hardware
  changed, e.g. after a RAM upgrade, the settings are recomputed.
- **During use:**
  - The real speed of each AI call is tracked. If it is consistently slower or faster than
    expected, the icons and the recommendation update.
  - Before the model starts, **free RAM is checked**. If the user is running other heavy
    programs, AI work waits, or uses a smaller tier if one is already downloaded, instead of
    slowing the PC down.
  - The app never makes the computer feel slow: heavy jobs pause while the user is active and
    free RAM is low, and continue when the PC is idle.

#### 3.2.2 Memory budget on the minimum machine (8 GB)

Windows and background apps already use about 3–4 GB, so on 8 GB the heavy parts **never run
at the same time**. A `ResourceManager` lets only one heavy job run at a time, in this order
(with more RAM, the same manager allows several):
1. user-facing work (commute recompute, opening an employer card);
2. AI enrichment;
3. scraping with the browser;
4. data imports.

| Component | RAM while running | When it runs |
|---|---|---|
| Windows + other apps | ~3–4 GB | always |
| App (Python, SQLite, UI window) | ~0.4–0.7 GB | always |
| AI model, **Simple** tier (Qwen3-VL 2B) | 2.1 GB, 3.0 GB with images (measured) | on demand; stopped after 5 min idle |
| AI model, **Good** tier (Gemma 4 E2B) | 3.6 GB (measured) | on demand; tight next to Windows on 8 GB (yellow icon) |
| MOTIS routing server (home area) | ~0.5–1.5 GB (to be measured in M0) | on demand for route/commute calls; stopped when idle; results cached |
| MOTIS import | to be measured in M0 | only when no other heavy job runs, preferably while the PC is idle |
| Edge (Playwright: social reader only) | ~0.4–0.8 GB | short sessions, never in parallel with the AI model |
| Embeddings (e5-small) | ~0.15 GB | in short batches |

**Consequences on the minimum machine:**
- **Recommended model there: Simple (Qwen3-VL 2B).** Expected icons:
  Simple 🟢, Good 🟡, Excellent 🟡 (both ~3.6 GB). Good can still be chosen; the app then
  stops the routing server and browser whenever the model runs.
- **Throughput** (measured in M5 on an Intel iGPU laptop, not yet on the budget Ryzen):
  ~8 s per full ad (6.8 s when only missing fields are asked), 2.5 s per listing card
  (occupation only) and ~30 s per image with the 2B model. The iGPU through Vulkan reads
  prompts 4× faster than the CPU but gives no extra memory. That is about 400–500 full ads
  per hour while the PC is idle. The queue processes **jobs
  matching the user's profile first** (home area, kinds of work); the rest follow later or
  run rules-only.
- **Routing data is clipped to the home area** (on machines below ~16 GB): the OSM extract is
  cut to a box of about 150 km around the home address (pyosmium, or bundled osmium-tool if it fits), and timetables
  are limited to that area. This is enough for commutes up to 120 min and makes the import
  much lighter than the whole Czech Republic. Changing the home address far away triggers a
  new background import.
- **AI context is capped** at ~4k tokens, so ad text is trimmed (trafilatura) before the model.
- **Disk:** a budget 256 GB SSD is enough (§11).

### 3.3 Inside the app
Python 3.12, uv, httpx, selectolax, parsel, extruct, trafilatura, ijson,
SQLite + SQLAlchemy 2/SQLModel + Alembic (auto-migrate), FTS5, sqlite-vec, Pillow,
imagehash, rapidfuzz, FastAPI + Jinja2, HTMX + Alpine.js + Pico.css (vendored), SSE,
APScheduler, pydantic v2; ruff, pytest, pytest-asyncio, respx.

Why not Scrapy or crawl4ai: the volume is moderate and the sources are very different from
each other; a thin async plugin layer is simpler and shares the app's event loop.

---

## 4. Architecture

```
┌──────────── App window (pywebview) + tray icon + Windows notifications ────────────┐
│ Wizard │ Dashboard │ Search+Filters │ Map │ Job detail │ Commute window │          │
│ Employer card │ Sources │ Settings                                                 │
└──────┬───────────────────────┬──────────────────────────┬──────────────────────────┘
┌──────▼───────────────────────▼──────────────────────────▼──────────────────────────┐
│ Services: Scheduler · RunManager · SearchService · EnrichmentQueue · ModelManager  │
│           CommuteService · EmployerService · DataUpdater · Secrets                 │
└──┬───────────────────┬──────────────────────┬─────────────────────┬────────────────┘
┌──▼─────────────┐ ┌───▼──────────────────┐ ┌─▼───────────────────┐ ┌▼───────────────┐
│ 1. SCRAPER     │ │ 3. EMPLOYER REVIEWS  │ │ AI (llama-server)   │ │ ROUTING (MOTIS)│
│ portals, MPSV, │ │ resolver (name→IČO), │ │ extraction, vision, │ │ OSM + NeTEx +  │
│ LinkedIn,social│ │ ARES/ISIR/VAT/agency │ │ ISCO, embeddings,   │ │ GTFS; routes,  │
│ forums, manual │ │ Atmoskop, Indeed,    │ │ review summaries,   │ │ one-to-many,   │
│                │ │ Google*, Facebook,   │ │ NL query            │ │ isochrones,    │
│                │ │ web mentions         │ │                     │ │ geocoder, tiles│
└──┬─────────────┘ └───┬──────────────────┘ └─────────────────────┘ └────────────────┘
┌──▼───────────────────▼──────────── PIPELINE ──────────────────────────────────────┐
│ discover → fetch → parse → normalize → enrich(AI) → geocode(address) → dedup →   │
│ index → employer link → expire → notify                                          │
└──┬────────────────────────────────────────────────────────────────────────────────┘
┌──▼──── SQLite (%LOCALAPPDATA%\AdaWork\adawork.db) ─────────────────────────┐
│ jobs · sources · employers · employer_reviews · employer_checks · travel_times ·   │
│ locations · codebooks · saved_searches · llm_cache · settings                      │
└────────────────────────────────────────────────────────────────────────────────────┘
* optional, needs a Google API key
```

### 4.1 Source plugin contract

```python
class Source(Protocol):
    id: str
    capabilities: SourceCaps   # supports_keyword, supports_region, full_dump, needs_browser, needs_login
    def config_schema(self) -> type[BaseModel]: ...
    def discover(self, q: ScrapeQuery) -> AsyncIterator[Lead]: ...
    async def fetch(self, lead: Lead) -> RawPosting: ...
    def parse(self, raw: RawPosting) -> PartialJob: ...
```
`ScrapeQuery` is generated from the wizard profile (home, commute limit, optional kinds of
work). Full-dump sources fetch everything and filter locally.

### 4.2 Shared HTTP layer
Per-domain rate limits, robots.txt, honest User-Agent, retries/backoff, pause on 403/429,
ETag, raw cache.

### 4.3 LinkedIn, Facebook, Instagram, forums
- **LinkedIn** (`scraper/sources/linkedin.py`): own small adapter on the public guest pages
  (`/jobs-guest/jobs/api/seeMoreJobPostings/search`, `/jobs-guest/jobs/api/jobPosting/<id>`),
  no login. A search near home (distance in miles from the travel-time radius, one per kind
  of work), newest first, ≤ 100 cards and ≤ 30 detail pages per run, 5 s between requests,
  a 429 pauses it for 12 h; unseen for 21 days = gone. **Off by default** with a warning and
  opt-in like Facebook/Instagram, because LinkedIn's robots.txt disallows these pages. The
  risk is a temporary IP block, not the user's account.
- **Indeed CZ:** **link-out only.** The search page has "Hledat také na Indeed", which opens
  the same search (keywords, home town, radius) on Indeed in the user's browser. Indeed's
  Cloudflare check refuses the app's honest User-Agent, and disguising the app is not done.
- **Facebook/Instagram `social_reader`:**
  - **Off by default.** Enabled only by an explicit opt-in in the wizard (step 4) or in
    Settings, each time after the risk warning.
  - The user logs in once in a real Edge window. Only the session is kept, never the password.
  - Automatic discovery:
    - job groups the user is a member of, plus public groups found with generated queries
      ("práce {town}", "brigády {town}"…);
    - Marketplace keyword searches near home;
    - Instagram hashtags (`#prace{town}`, `#brigada{town}`…) and the profiles of known
      employers.
    - Private groups are only suggested (one-click join), never joined automatically.
  - **Limits (no "safe" number exists, see §4.3.1):**
    - Defaults: **Facebook ≤ 100 posts/day** and **Instagram ≤ 50 posts/day**, read in at most
      **2 sessions a day of ~10–15 min** in daytime, whatever the main interval.
    - Irregular pauses of 5–30 s; one tab; read only, no likes, follows, comments, joins or
      messages.
    - Settings allow up to 200 / 100 with a warning.
  - On a session expiry, verification request or "going too fast" block: stop, notify, and
    pause that platform (7 days after a block). One click opens the login window.
- **Forums:** a built-in list of public Czech forums with job sections plus user-added URLs;
  auto-detected post blocks → keyword check → AI.
- **Manual import:** paste text or drop a photo/screenshot.
- Social posts: repost dedup (SimHash + image hash), 14-day expiry.

### 4.3.1 Facebook/Instagram limits and risks (researched 2026-10-06)

**Is 200 posts a day safe? Nobody can confirm a safe number.**
- Meta does not publish its limits. Facebook's help pages only say that limits depend on
  "speed and quantity" and cannot be shared.
- The numbers found online come from automation-tool vendors and concern **actions**
  (posting, liking, following), not reading. Examples: posting to 25–50 groups/day on aged
  accounts; on Instagram, ~60–80 likes and 20–30 follows per hour; ~100 profiles/hour for
  Instagram scraping. None of them is backed by Meta.
- Detection is **behavioural**, not just a counter: account age, device and IP trust, timing
  regularity, session patterns, and signs of browser automation all matter. A low count
  reduces risk but does not remove it.
- Reading 100–200 posts a day is similar to normal human browsing volume. The app also runs
  on the user's own computer, IP address and long-lived session, which avoids the
  "new device / new location" signals.
- Hence the conservative defaults above (100 Facebook / 50 Instagram per day, read-only,
  irregular timing) and stopping at the first warning.

**What can happen to the account** (from mild to severe):

| Level | What happens | Duration |
|---|---|---|
| "Going too fast" temporary block | A feature (e.g. viewing groups) is blocked | Typically 24–48 h; Facebook cannot lift it early |
| Checkpoint / verification | Must confirm identity (code, sometimes a photo or ID) before using the account | Until resolved by the user |
| Instagram action block | Actions blocked; first blocks usually clear in 24–72 h, repeats last longer | Days |
| Temporary restriction | Limited features (groups, Marketplace) | Days to weeks |
| Permanent disable | Account lost, including Messenger, groups, Marketplace and photos | Permanent (appeal possible) |

**Other risks:**
- **Terms of use:** Meta's Automated Data Collection Terms prohibit automated collection
  without written permission. Since **1 Jan 2025** they apply both logged-in and logged-out.
  Earlier, in *Meta v. Bright Data* (Jan 2024), a US court had allowed logged-out scraping of
  public data. Meta's enforcement against individuals is normally account action; lawsuits
  and cease-and-desist letters have targeted commercial scrapers. The risk for a personal
  user is mainly the account.
- **GDPR:** posts contain personal data (names, phone numbers). Purely personal use on the
  user's own computer generally falls under the GDPR "household" exemption (Art. 2(2)(c)).
  Sharing or commercial use of the collected data would bring full GDPR duties. Therefore the
  data stays local, social posts expire after 14 days, and there is no export of contact
  details from social posts.
- **Scam ads:** Facebook job groups contain many fraudulent offers. The `red_flags` check and
  the employer module (§4.5) mark them.
- **Breakage:** Meta changes its page structure often; the reader can stop working until it
  is updated. The rest of the app is unaffected.

**Ways to lower the risk:**
- **Instagram:** Instagram officially allows several accounts per person, so the wizard
  suggests logging in with a separate Instagram account instead of the main one.
- **Facebook:** a second personal account breaks Facebook's "one account per person" rule,
  so the app does not suggest one. The user decides whether to use their main account at all.
- Facebook and Instagram are **off by default**. The wizard shows this risk table as a
  visible warning before the user can switch them on. They can be switched off any time, and
  the rest of the app works without them.

### 4.4 Pipeline stages
1. Discover → 2. Fetch → 3. Parse (source JSON → JSON-LD → microdata → `__NEXT_DATA__` →
HTML → text).
4. Normalize with rules: salary → monthly CZK, contract types, shifts, education, licences,
languages, location.
5. Enrich with AI when needed.
6. **Geocode** from RÚIAN: the exact address point (MPSV address codes, portal street +
house number), else the street, the city part ("Praha-Krč") or the municipality centre.
A precision flag is stored: `address` / `street` / `part` / `municipality` / `district` /
`region`. The MOTIS geocoder (M8) is only needed for free-text addresses.
7. Dedup (`scraper/dedup.py`): same place + employer + matching title across sources;
partial title matches also need overlapping descriptions (word 3-grams). Image hash for
social posts (M6b).
8. Index (FTS5 + embeddings).
9. **Employer link**: resolve the employer (§4.5) and queue cheap official checks.
10. Expire.
11. Notify.

### 4.5 Module 3 — Employer reviews (`EmployerService`)

**Resolving who the employer is**
1. **IČO in the ad** (MPSV, many JSON-LD ads) → ARES detail, done.
2. **Local lookups, no network:** learned cache `name_normalized → IČO` (confirmed matches),
   a curated **brand alias table** for the ~200 biggest Czech employers (brand names alone are
   hopeless in ARES: "Albert" = 529 hits, mostly sole traders), and the MPSV agency list.
3. **Normalize the name** into variants: expand `ČR`/`CZ` → "Česká republika", drop generic
   venue words (hospoda, restaurace, prodejna, pobočka…), keep the legal form if present.
4. **ARES search**, ≤ 3 queries per employer, stopping at the first variant with 1–200 hits.
5. **Rank candidates**: rapidfuzz `token_sort_ratio` (not `token_set_ratio`, which scores every
   hit containing the query as 100), legal-form prior (penalize unions, associations,
   foundations), active only, **employee band and main NACE from one RES batch call**, NACE fit
   with the job category, registered seat = job municipality as a bonus only (chains have
   their seat in Prague).
6. **Decide**: clear winner → resolved; close candidates → the AI picks using the ad text;
   otherwise the card says "nejisté přiřazení" (uncertain match) and shows the candidates.
   Small pubs run by sole traders under a personal name cannot be resolved without an IČO.
7. Personnel agencies: matched against the MPSV agency register. If the ad names the real
   client company ("pro našeho klienta, výrobce automobilů v Kolíně…"), the AI extracts it, and
   the card shows both.

**Collecting (each source is a small plugin, like scraper sources)**

| Tier | Sources | When |
|---|---|---|
| Bulk lists (local lookup) | Unreliable VAT payers (0.5 MB), MPSV agency register (4 MB), Atmoskop sitemap slug index | Downloaded daily (VAT, agencies, conditional GET) and weekly (Atmoskop sitemap) |
| Official checks (fast, free) | ARES detail + RES (insolvency flag, DIČ, employee band, NACE); ISIR only if ARES flags insolvency; VAT and agency licence from the local lists | For **every** employer with an IČO, in the background, refreshed every 30 days |
| Employee reviews | Atmoskop (% satisfaction, counts, review texts); Indeed as a link only (Cloudflare blocks plain HTTP) | For employers of jobs that match the user's profile (commute limit + kinds of work), and on demand when a job is opened |
| Customer reviews | Google Places API (only if a key was entered): rating, count, ≤ 5 reviews per branch | Same as employee reviews; stays within the free 1 000/month (counter shown in Settings) |
| Social | Facebook page recommendations and posts (if logged in) | On demand only |
| Web mentions | ddgs search ("{firma} zkušenosti zaměstnanci", "{firma} recenze práce", "{firma} nevyplacená mzda"…) → top ~5 pages → trafilatura → AI picks relevant passages | On demand + profile matches, small daily limit, cached 30 days |

**AI summary** (same local model): from collected review texts and mentions → Czech
summary, "Co chválí" / "Co kritizují" (what people praise / criticize), warning signals
(unpaid wages, illegal work, unpaid trial), each point with a **link to its source**.
The prompt forbids claims not found in the texts; the summary is labelled as AI-generated.

**Employer card ("Karta zaměstnavatele")**
- **Ověření** (official): in ARES since 2009 · active · no insolvency · reliable VAT payer ·
  licensed agency (date only when the register has one) (or warnings in red; a missing VAT
  registration is neutral, not a warning).
- **Zaměstnanci** (employees): spokojenost 62 % (861 hodnocení, Atmoskop; also colleagues/boss
  %) + summary; Indeed as a link.
- **Zákazníci** (customers): ★ 4.4 (2 310, Google) — labelled clearly as customer reviews.
- **Internet a sociální sítě**: summary of mentions, links.
- Links: Atmoskop, Indeed, Google Maps, Mapy.cz, company website, ARES/justice.cz.
- "Aktualizovat" (refresh) button; date of last update per source.

**Filters fed by this module**:
- minimum employee satisfaction (Atmoskop %);
- hide employers with official warnings;
- only verified employers (resolved IČO);
- hide unlicensed agencies.

### 4.6 Commute module (`CommuteService` + MOTIS)

**Data and updates (automatic):**
- First run: download the OSM CZ extract (948 MB) and the Spojenka GTFS (85 MB), sanitize the
  GTFS, and import with MOTIS in the background.
- **Profile by hardware** (measured in M0):
  - ≥ 16 GB RAM: whole Czechia, ~7 min import, peak 5.5 GB, 4 GB data, geocoding on.
  - 8 GB RAM: osmium extract 150 km around home + renumber, clip GTFS, import (~2 min, peak
    1.65 GB, 1.3 GB data), geocoding off, `background` tile profile. A new home address
    re-runs this pipeline. The import reserves ~5 GB of virtual memory, so check the page file.
- Until the import finishes, travel times are **estimates** (straight-line distance × mode
  factor), labelled "odhad".
- Timetables refresh weekly (Spojenka is valid 14 days), OSM monthly; the updated data is
  imported in the background and switched in when ready.
- No own JDF converter is needed while Spojenka is used; regional GTFS (PID, IDS JMK) is the
  fallback if it disappears.

**Starting point and defaults:**
- Set in the wizard: home address (autocomplete), usual mode, max travel time, usual
  departure time (default weekdays 7:00).
- Can be changed any time in Search and in the commute window (type an address, use the home
  address, or click the map).

**Travel-time filter (Search page):**
- Controls: start point · mode (Auto / MHD / Pěšky / Kolo / "any of them") · **slider
  5–120 min** · departure time.
- When the start, mode or departure time changes:
  - car/walk/bike: one MOTIS **one-to-many** call from the start to all distinct job
    locations;
  - public transport: one-to-all from the start, then each job gets the time of the best
    nearby stop + the walk.
  - Results are stored in `travel_times(origin, mode, depart_profile, location_id, minutes)`.
- **Moving the slider is instant**: it only filters cached times (computed up to 120 min).
- The map shows the reachable area for the slider value, drawn from one-to-all results
  (MOTIS has no isochrone polygon endpoint).
- Remote jobs always pass. Jobs whose location is only a municipality or region show
  "přibližně" (approximately) next to the time.
- Result list shows travel time per job ("🚌 38 min · 🚗 22 min") and can sort by it.
- (Later) Use the job's shifts: for night shifts, check public transport at the shift times,
  and warn "no connection at 22:00".

**Commute window ("Jak se tam dostanu", opened from a job or a map pin):**
- Tabs **Auto / MHD / Pěšky / Kolo**, each with the route drawn on the map, duration and
  distance.
- MHD (public transport): legs with line numbers, stops, departure/arrival times, transfers,
  walking parts, and the next few connections.
- Departure/arrival time picker (default from the profile).
- **Time slider** showing the area reachable from the start within N minutes.
- Buttons to open the same route in Mapy.cz, IDOS or Google Maps (deep links).

---

## 5. Data model (SQLite)

- `settings`, `sources`, `runs`, `raw_postings`, `job_sources`, `job_images`, `jobs_fts`,
  `job_vec`, `saved_searches`, `llm_cache` (as before).
- `jobs`: all normalized fields (title, ISCO, category, employer name, place, salary, terms,
  requirements, benefits, provenance) + `employer_id`, `location_id`.
- `locations(id, lat, lon, precision, address_text, municipality_code, okres, kraj)`: shared
  by jobs at the same place, which keeps routing calls low.
- `employers(id, ico, dic, name, name_normalized, legal_form, founded, address, nace,
  nace_main, employee_category, is_agency, agency_temp_staffing, agency_licence_until,
  atmoskop_url, website, match_confidence, updated_at)`
- `employer_aliases(name_normalized, ico, source[curated|confirmed|agency_register])`
- `bulk_lists(kind[vat_unreliable|agencies|atmoskop_sitemap], etag, fetched_at)` + their
  local lookup tables.
- `employer_checks(employer_id, kind[ares|isir|vat|agency], status, detail_json, checked_at)`
- `employer_reviews(id, employer_id, source, rating, rating_scale[stars5|percent], count,
  scores_json, review_texts_json, url, fetched_at)`
- `employer_mentions(id, employer_id, url, title, snippet, sentiment, fetched_at)`
- `employer_summary(employer_id, summary_json, model, prompt_version, created_at)`
- `travel_times(origin_key, mode, depart_profile, location_id, minutes, computed_at)`
- Codebooks: CZ-ISCO, CZ-NACE, UI categories, municipalities with GPS + English names.

---

## 6. AI module

### 6.1–6.5 (text ads)
- Rules first; the AI processes only incomplete ads in a background queue, cached
  (`llm_cache`), once per content version. Implemented in M5 (`adawork/ai/`):
  - rules: an occupation code from similar Úřad práce titles when the vote is clear, contract
    types from fixed phrases in the description;
  - the model asks only for missing fields (occupation from ≤ 8 candidates, town, salary,
    shifts, education); listing cards without a description only for the occupation;
  - every answer is checked against the ad text (numbers, town, shift and education words);
    contract types are not asked from the model (it guessed them, §M5 eval);
  - filled fields are marked in `job.extra["origin"]` ("doplnila AI" on the result card);
  - jobs that neither the rules nor the AI can place get the category **"Nezařazeno"** (code
    `0`), so the category filter shows them instead of hiding them.
- Extraction JSON schema enforced by llama-server; post-validation of numbers and places.
- ISCO classification: top-10 candidates by embeddings, then the model picks one.
- Semantic search and "ask in a sentence" → filter chips.
- `is_job_offer` drops non-offers (people looking for work); `red_flags` (MLM, unpaid trial,
  fees for the applicant).

### 6.6 Model choice (wizard step and Settings)

All tiers use **one multimodal model** (text + images), so only one model sits in RAM.
The vision part is loaded only for image work. Catalog chosen by the M5 eval
(`docs/spikes.md`; pinned Hugging Face commits, SHA-256 checked, `adawork/ai/catalog.py`):

| Friendly name | Shown as | Download | RAM needed (measured) |
|---|---|---|---|
| **Jednoduchý / Simple** | `Jednoduchý (Qwen3-VL 2B, 1.6 GB)` | 1.6 GB | 2.1 GB, 3.0 GB with images |
| **Dobrý / Good** | `Dobrý (Gemma 4 E2B, 3.4 GB)` | 3.4 GB | 3.6 GB |
| **Výborný / Excellent** | `Výborný (Qwen3-VL 4B, 3.0 GB)` | 3.0 GB | 3.6 GB |

Qwen3.5 2B/4B and Gemma 4 E4B stay selectable under "Pokročilé". The 8–14B models tested
on 2026-10-07 (Qwen3-VL 8B, Qwen3.5 9B, Ministral 3 8B/14B, Gemma 4 12B, Qwen3 14B) were not
meaningfully more accurate and 3–6× slower; models below 2B (Qwen3.5 0.8B, Qwen3 0.6B/1.7B,
Gemma 3 1B, LFM2.5 1.2B and VL 1.6B, tested 2026-10-08) only repeat what the rules give and
miss most salaries (`docs/spikes.md`). Both groups were **removed from the catalog**
(2026-10-09); the pay stated in ad text is read by a rule instead.

**The best model is decided dynamically for each computer.**

The list starts with **`⚙ Automaticky – nejlepší pro tento počítač`** (automatic, best for
this computer), which is the **default**. Below it are the three tiers with their icons. The
user can pick a tier manually instead; Auto then stops changing it.

**How the app decides (`ModelAdvisor`):**
1. **Probe** the hardware:
   - total RAM and typical free RAM (sampled during the wizard);
   - GPU via Vulkan: vendor, model, dedicated or shared memory;
   - CPU: physical cores, AVX2/AVX-512;
   - free disk space.
2. **Fit check** per model:
   - RAM need = model file + KV cache for the context + runtime overhead.
   - It must fit into total RAM − OS reserve (~3 GB) − app (~0.7 GB).
   - With a dedicated GPU, layers offloaded to VRAM reduce the system-RAM need.
   - Too little disk → 🔴.
3. **Speed estimate:**
   - Before download: a built-in table of seconds per ad by model size × CPU/GPU class.
   - After download: a **20-second real benchmark** (prompt + generation tokens/s), which
     replaces the estimate.
4. **Pick:** the largest tier that is 🟢. If none is green, the largest 🟡. If none is
   yellow, Simple with a warning, or rules-only if even Simple does not fit.
5. **Keep it right over time (Auto mode only):**
   - Re-check at every start and from the running speed statistics (§3.2.1).
   - If a bigger tier becomes 🟢 (e.g. after a RAM upgrade), download it in the background,
     benchmark it, and switch only if it really is faster than the 🟡 threshold. Then notify:
     "Model byl vylepšen na Dobrý".
   - The old model stays as a fallback until the new one has passed. **Models are never
     deleted automatically** (decided 2026-10-07): the AI page lists the downloaded models
     with a "Smazat" button for each one not in use.
   - If the current model becomes too slow or memory-starved, switch down to a smaller tier.
   - Downloads over a metered connection (Windows "metered" flag) wait for permission.

| Icon | Meaning | Rule (initial) |
|---|---|---|
| 🟢 (green check) | "Poběží plynule" — runs smoothly | RAM need ≤ 40 % of total RAM **and** estimated ≤ 15 s per ad |
| 🟡 (yellow gauge) | "Poběží, ale pomaleji" — works, slower | RAM need ≤ 60 % **or** 15–40 s per ad |
| 🔴 (red cross) | "Nedoporučeno" — not enough memory / too slow / not enough disk | otherwise; can still be chosen after a confirmation |

- Icons are SVGs with a text label and a tooltip (not color only), e.g.
  `🟢 Dobrý (Gemma 4 E2B, 3.4 GB) — ~8 s na inzerát`.
- "Automaticky" is preselected. The tier it currently chooses is marked "Doporučeno"
  (recommended) in the list, so the user can see what Auto means on this PC.
- Settings → AI shows the same list, the measured speed, and the reason for the current
  choice, e.g. "8 GB RAM, integrovaná grafika → Jednoduchý". The user can switch any time.
  Ollama or a custom GGUF can be chosen under "Pokročilé" (advanced).
- Rules-only mode when no model is downloaded yet.
- Expected results by hardware (estimate formula calibrated in M5 on one Intel iGPU laptop;
  the other rows are still to be checked on real machines):

| PC | Simple | Good | Excellent | Auto picks |
|---|---|---|---|---|
| 8 GB, budget Ryzen, iGPU (minimum) | 🟢 | 🟡 | 🟡 | Simple |
| 16 GB, mid-range CPU, iGPU | 🟢 | 🟢 | 🟡 | Good |
| 32 GB, Intel Core Ultra 7, iGPU (**measured**) | 🟢 7.9 s | 🟢 8.0 s | 🟡 14.5 s | Good |
| 16–32 GB + GPU with ≥ 8 GB VRAM | 🟢 | 🟢 | 🟢 | Excellent |

### 6.7 Evaluation
Golden set: ~60 text ads + ~30 social posts + ~20 image ads + ~20 employer review sets
(summary faithfulness). `adawork eval` per model: per-field accuracy, JSON validity, speed.

### 6.8 Images
Skip non-ads → RapidOCR → text path; if the result is weak, the vision model reads the
image → same schema. Images are stored and shown; perceptual-hash dedup.

---

## 7. User interface

The app window is served from `127.0.0.1` on a free port. UI in Czech, English selectable.

### 7.1 First-run setup wizard ("Průvodce nastavením")
Every step has defaults; optional steps have "Skip"; downloads run in the background.

1. **Vítejte** — language.
2. **Odkud dojíždíte a co hledáte** (where you commute from, what you look for):
   - home address (autocomplete; precise address or just a town);
   - usual travel mode (Auto / MHD / Pěšky / Kolo / "nevím, ukaž vše");
   - max travel time slider (default 45 min);
   - usual departure time (default weekdays 7:00);
   - optional kinds of work (empty = everything; asked only here, prefills the search).
     Free words ("ucitelka ve skolce, druzinarka") are mapped to CZ-ISCO occupations: the
     title index finds candidates, the model picks from them (`adawork/ai/wanted.py`). A job
     matches by occupation or by the words; keyword portals also get a plain job title.
   This drives generated searches, the default travel-time filter and employer prefetching.
3. **Umělá inteligence** — shows the detected hardware ("16 GB RAM · AMD Radeon · 120 GB
   volného místa"). Below it: **"Automaticky – nejlepší pro tento počítač"** (preselected),
   then Simple / Good / Excellent with hardware-fit icons (§6.6). The download of the chosen
   model starts in the background.
4. **Sociální sítě (volitelné, ve výchozím stavu vypnuto)** — social networks, optional, off
   by default:
   - Two switches, **Facebook** and **Instagram**, both **off**. "Další" (Next) with both off
     is the default path.
   - A **visible warning box** (orange/red frame, warning icon, always shown on this step, not
     hidden behind a link). In plain Czech it says:
     - automatic reading breaks Meta's terms of use;
     - Facebook/Instagram can temporarily block or **permanently disable the account**
       (lost Messenger, groups, photos);
     - no limit is guaranteed to be safe;
     - the app reads slowly (100 / 50 posts a day), stops at the first warning, and never
       stores the password.
     - A "Více o rizicích" (more about risks) expander shows the full risk table from
       §4.3.1.
   - Turning a switch on opens a confirmation dialog. It repeats the main risk and requires
     ticking **"Rozumím riziku a chci tuto funkci zapnout"** ("I understand the risk and want to
     turn this on"). Only then does the "Přihlásit se" button open the Edge login window.
   - For Instagram, a hint suggests a separate Instagram account.
   - **LinkedIn** switch on the same step, **off** by default, with its own short warning: it
     breaks LinkedIn's rules for robots, the computer's IP can be blocked for a while, no
     login is used so the account is not at risk.
   - The same switches, warning and confirmation appear in Settings, so the user can turn
     either on later or back off any time. A small warning badge stays next to an enabled
     Facebook/Instagram source on the Sources page.
5. **Další zdroje (volitelné)** — API keys, each with a "Získat zdarma" link and a short how-to,
   stored in Windows Credential Manager:
   - Jooble, Careerjet;
   - **Google Maps** (customer ratings): Google requires a billing account with a card even
     for the free 1 000 requests/month; the app never exceeds the free limit.
6. **Automatika**:
   - "Automaticky hledat nové nabídky" (check for new jobs automatically): switch **off by
     default**. When switched on, the interval is **6 h / 12 h / 24 h / 48 h** (12 h
     preselected); social media runs at most twice a day regardless.
   - Start with Windows (default on); notify me about new matches (default on).
   - With automatic checks off, scraping runs when the user clicks "Hledat nové nabídky" on
     the Dashboard or in the tray menu.
   - Automatic checks can be switched on/off or paused any time from the tray or Settings.
7. **Hotovo** — summary; "Start" runs MPSV first (fast, all industries).
   - Map/routing data and the model download continue in the background.
   - The dashboard shows their progress ("Mapy a jízdní řády: 60 %").

### 7.2 Main pages
1. **Přehled / Dashboard** — jobs, new since last visit, source status, AI queue, background
   data progress, next run (or "automatické hledání vypnuto" with a "Hledat nové nabídky" button).
2. **Hledání / Search**:
   - **Dojíždění** (commute) bar on top: start point, mode, **time slider**, departure time;
     reachable-area outline on the map.
   - Filters:
     - position / category / ISCO;
     - place (kraj → okres → obec; the radius filter is kept as an alternative);
     - salary;
     - terms and shifts;
     - requirements;
     - suitable for;
     - benefits;
     - **employer** (min rating, no warnings, verified, licensed agency);
     - meta (source, date, hide red flags).
   - Facets, sorting (date, salary, **travel time**, employer rating, relevance), list or map
     (MapLibre, offline), saved searches with notifications, CSV/XLSX export.
3. **Detail pozice** — fields with origin badges, AI summary, images, source links,
   **travel time per mode with a "Jak se tam dostanu" button**, **employer card preview**
   (rating, warnings) with "Více o zaměstnavateli".
4. **Jak se tam dostanu** (commute window) — §4.6.
5. **Zaměstnavatel** (employer card) — §4.5; also lists all current jobs of this employer.
6. **Zdroje / Sources**, **Vložit inzerát**, **Nastavení / Settings** (reopen wizard, profile,
   accounts, API keys + Google quota counter, automatic checks on/off + interval 6/12/24/48 h, social limits, AI model
   list with icons, map data status/refresh, data folder, logs).

---

## 8. Project structure

```
AdaWork/
├─ pyproject.toml / uv.lock / README.md / PLAN.md
├─ adawork/
│  ├─ __main__.py, cli.py, config.py, secrets.py, paths.py
│  ├─ desktop/  window.py, tray.py, notify.py, autostart.py
│  ├─ db/  models.py, session.py, fts.py, vec.py, migrations/
│  ├─ scraper/                      # MODULE 1
│  │  ├─ base.py, registry.py, query_builder.py, http.py, browser.py, pipeline.py, scheduler.py
│  │  ├─ extract/  jsonld.py, nextdata.py, text.py, forum_blocks.py
│  │  └─ sources/  mpsv, linkedin, indeed (link), prace_cz, jenprace, profesia,
│  │               startupjobs_cz, jobs_cz, bazos_cz, hitprace_cz,
│  │               dobraprace_cz, fajnbrigady_cz, forum_generic, facebook, instagram,
│  │               manual, jooble, careerjet
│  ├─ employers/                    # MODULE 3
│  │  ├─ resolver.py (name → IČO), service.py, summary.py, scoring.py
│  │  └─ sources/  ares, isir, vat_register, agency_register, atmoskop, indeed_reviews,
│  │               google_places, facebook_page, web_mentions (ddgs)
│  ├─ routing/                      # commute
│  │  ├─ motis.py (process + API client), data_updater.py (OSM, NeTEx, GTFS),
│  │  ├─ jdf_to_gtfs.py (only if no OSS converter fits), commute.py (filter, cache),
│  │  └─ deeplinks.py (Mapy.cz, IDOS, Google)
│  ├─ normalize/  salary, contract, education, location, dedup
│  ├─ ai/  hardware, catalog, download, advisor (fit icons), runtime (llama-server),
│  │       winjob, manager (Auto mode), benchmark, client, grammar, embeddings, isco,
│  │       extract, enrich (queue), ask (sentence -> filters), vision, evaluate
│  ├─ system/resources.py  ResourceManager (heavy-job slots, priorities, "PC is busy")
│  ├─ search/  service, filters, facets
│  └─ web/                          # MODULE 2
│     ├─ app.py, routes/ (wizard, search, job, commute, employer, sources, settings, manual)
│     └─ templates/, static/ (htmx, alpine, pico, maplibre — vendored)
├─ resources/  codebooks/, forums.yaml, categories.yaml
├─ vendor/  llama.cpp/, motis/      # fetched by the build script
├─ packaging/  adawork.spec, installer.iss, build.ps1
└─ tests/  fixtures/, golden/, unit/, contract/, integration/
```

---

## 9. Implementation milestones

### M0 — Environment, skeleton, spikes (2 days)
- Dev tools (`winget`: Python 3.12, uv, Git), scaffold, app window + tray "hello".
- Source spike: fixtures for every portal; list of public forums.
- **Routing spike:** MOTIS on Windows with the CZ OSM extract + CIS JŘ NeTEx + one GTFS;
  measure import time, RAM, disk; find a JDF→GTFS converter.
- **Employer spike:** ARES search by name, ISIR and VAT web services, Atmoskop page parse,
  ddgs query.
- **Done when:** the window opens; spike results recorded in `docs/spikes.md`, including a
  go/no-go for MOTIS (plan B: Mapy.com API for car/walk/bike + IDOS links for public
  transport).

### M1 — Data core, MPSV, basic search, wizard v1 (3 days)
- Job list: a job's title opens its whole text; a button leads back to the top.
- **Done when:** fresh start → wizard steps 1, 2, 7 → MPSV import → full-text search in
  < 300 ms.

### M2 — Codebooks and normalization (2–3 days) — done
- **Done when:** the full filter set works with facets; the map shows jobs.
- Built: shared vocabulary (`normalize/vocab.py`), RÚIAN coordinates, filters + facet counts
  (≤ 150 ms on 40k jobs), list/map toggle with clustered pins and popups.
- Map tiles come **online from OpenFreeMap** (free, no key, apps allowed, attribution shown)
  until M8 switches to offline MOTIS tiles.

### M3 — Generic JSON-LD + prace.cz, jenprace.cz, profesia.cz + dedup (3 days) — done
- Built:
  - Polite HTTP (`net/polite.py`, `robots.py`): robots.txt with `*`/`$` wildcards
    (urllib.robotparser has neither), ≥ 1.5 s between requests per host, retries on 5xx,
    a source that answers 403/429 or a bot challenge is paused for 12 h.
  - Generic `JobPosting` reader (`scraper/structured.py`): JSON-LD and microdata, lenient
    dates, `baseSalary` units, address → location.
  - Crawl framework (`scraper/crawl.py`): region listings → cards → detail pages, ≤ 300 detail
    pages per source and run (the rest stay card-only and are completed on later runs),
    expiry only after a complete pass. Adapters: prace.cz (jobs.cz career pages: card only),
    jenprace.cz, profesia.cz (branded pages: card + page text).
  - Crawl scope: the regions reachable from home within the travel-time limit
    (`geo/scope.py`); every region when home is unknown.
  - Places (`geo/places.py`): "Praha-Krč", "Brno - Líšeň", "Králíky, Ústí nad Orlicí",
    regions; same-name villages told apart by PSČ, district or region. Street addresses and
    city parts get RÚIAN coordinates in the geocode step.
  - Dedup (`scraper/dedup.py`): same place + same employer + matching title from another
    source → one job with several sources. Partial title matches also need similar
    descriptions; district-level MPSV jobs match on the full title. The primary source owns
    the fields, others fill gaps; a job expires when no source offers it.
- **Done when:** a run for one home (Kolín, 30 min by car) reads all three portals without
  errors, and re-listed ads appear once with several source links. Measured 2026-10-06:
  see `docs/spikes.md` § M3.

### M4 — Indeed CZ + LinkedIn (2 days) — done
- Indeed: link-out only (the search page blocks an honest User-Agent; §14 item 7).
- LinkedIn: own guest-page adapter, off by default; switched on in the wizard's new
  "Zdroje nabídek" step or on the Sources page, only after ticking "Rozumím riziku…".
- Sources page (`/sources`): every source can be switched off; choices live in settings.
- Crawl framework generalized: a source lists region listings or searches near home
  (`ScrapeQuery`), with its own pace, lead/detail limits and expiry rule.
- **Done when:** LinkedIn stays silent until opted in; with opt-in a live run for Kolín read
  29 ads in 3 min without refusals (15 were Úřad práce ads already known, merged).

### M5 — AI layer + model picker + dynamic hardware adaptation (6–7 days) — done
- llama-server management, `HardwareProfile` probe, **`ModelAdvisor` (Auto mode)**, model
  list with fit icons, real speed benchmark, resumable download, automatic upgrade/downgrade
  with fallback, free-RAM check before each model start.
- `ResourceManager` limits derived from the hardware profile (§3.2.1).
- Extraction, ISCO, embeddings, NL query, eval.
- **Done when:**
  - On the **minimum test machine (8 GB, budget Ryzen)** with the **Simple** tier: ≥ 85 % ISCO
    group, ≥ 90 % kraj, ≥ 80 % contract type, 100 % valid JSON, ≤ 15 s per text ad, and no
    memory pressure (no swapping) with the app window open.
  - Icons match the measured speed on at least 3 different machines (8 GB iGPU, 16 GB iGPU,
    one with a dedicated GPU), and Auto picks the expected tier on each (§6.6 table).
  - Simulated changes work: Auto switches tiers after a simulated RAM change, and AI work
    waits instead of starting when free RAM is too low.
  - If the 2B model misses the accuracy targets, try another 2–3B multimodal model or a
    text-only 1.7–3B model for text plus the 2B vision model loaded only for images. The
    decision is recorded in `docs/spikes.md`.
- **Result (2026-10-07, details in `docs/spikes.md`):** six models evaluated on 70 golden
  ads; tiers Qwen3-VL 2B / Gemma 4 E2B / Qwen3-VL 4B.
  - Met: 100 % valid JSON; kraj 99 % (Simple); 7.9 s per ad on the test laptop's iGPU;
    Auto upgrade after a RAM change and "AI waits when RAM is low" (unit tests); the model
    process ends with the app even after a crash (job object).
  - **Not met as written:** ISCO 73 % at the 2-digit level with Simple (best model 74 %);
    a manual review counts about 90 % sensible codes, the filter categories overlap 89 %.
    Contract type is read by rules (88 % right where an ad states it; 76 % of ads do not);
    no model did better.
  - **Not measured:** the 8 GB budget Ryzen and a dedicated-GPU PC; only one test machine
    was available. The icons there come from the calibrated estimate, and the benchmark
    after the download corrects them on each PC.
  - Also built: wizard step "Umělá inteligence", page `/ai` (choice, download progress,
    Pokročilé: model, own server such as Ollama, metered downloads), "Zeptejte se větou"
    on the search page, `adawork eval`. Search for "řidič" no longer finds "řídící".
  - Deferred: semantic search over all jobs (embeddings of every ad), the image pipeline
    itself (images arrive with the social and bazos sources, M6b).

### M6 — Rest of the top 10 portals (5–6 days) — done
- jobs.cz (custom HTML; also the career pages prace.cz links to), startupjobs.cz, hitprace.cz,
  dobraprace.cz, fajn-brigady.cz, prace.bazos.cz.
- **Confirmed (2026-10-07): every site that can legally be added is added** (robots.txt and
  terms allow it, honest User-Agent, no login): pracezarohem.cz (+0.5 day), inwork.cz (+1),
  easy-prace.cz own ads (+0.5–1), personalka.cz (re-listed Úřad práce ads merge into the
  existing jobs by dedup). New sites found later follow the same rule.
- **Result (2026-10-07):** 10 new portals, 15 sources in total (`docs/spikes.md`, "M6 live
  smoke run"). Sitemap discovery added to the crawl framework (lastmod re-reads, region
  pre-filter from the URL, ads near home first, nationwide expiry). jobsik.cz is not read
  separately (same ads as dobraprace.cz). Career pages of big employers on jobs.cz load the ad
  with JavaScript and stay card-only. Still to do: time a complete first run, and read
  different portals in parallel to shorten it.

### M6b — Social media, forums, images (7–9 days)

### M7 — Automation (2–3 days)
- Scheduler: automatic checks **off by default**, **6/12/24/48 h** intervals, pause/resume, manual "Hledat nové nabídky" button, start with Windows,
  notifications, saved searches, export; Jooble/Careerjet keys.
- Background data refresh jobs that run regardless of the automatic-checks switch (small,
  conditional downloads): unreliable VAT payer list and MPSV agency register daily, Atmoskop
  sitemap weekly.
- **Done when:**
  - Automatic checks are off after a fresh install, and the manual "Hledat nové nabídky" button
    works.
  - Once switched on, the app runs 3 days unattended from the tray.
  - Changing the interval or switching automation on/off reschedules without a restart.

### M8 — Commute module (7–9 days)
- MOTIS + osmium-tool bundling, background download (OSM, Spojenka GTFS), GTFS sanitizing,
  hardware profile (whole CZ vs. 150 km clip + renumber), import/refresh scheduled by the
  `ResourceManager`, estimate fallback. MOTIS runs on demand and is stopped when idle.
- Job coordinates already come from RÚIAN (M2); MOTIS geocoding serves the address box.
- `travel_times` cache, one-to-many / one-to-all, travel-time filter + slider + isochrone,
  sort by travel time.
- Commute window (4 modes, transit legs, time picker, slider, deep links), MapLibre with
  MOTIS tiles.
- Wizard step 2 commute fields.
- **Done when:**
  - Moving the slider updates results in < 200 ms.
  - A new start point recomputes ≥ 5 000 job locations in < 30 s.
  - 10 sample routes match Mapy.cz/IDOS within ±15 %.
  - Before the import finishes, estimates are shown and labelled.

### M9 — Employer reviews module (6–7 days)
- Resolver (IČO, alias table + learned cache, ARES search with name variants, RES batch
  ranking, AI disambiguation, agency + client extraction).
- Official checks: ARES + RES, ISIR (only when flagged), VAT and agency register from local
  bulk lists.
- Atmoskop reviews via sitemap slug index + `__NEXT_DATA__`; Indeed as a link only; Google
  Places (optional key, quota counter); Facebook page (if logged in); web mentions via ddgs
  with retries.
- AI summary with source links; employer card; employer filters; prefetch for profile
  matches.
- Wizard step 5 Google key.
- **Done when:**
  - ≥ 90 % of 50 sample employers resolve to the right IČO.
  - Cards show official checks for all of them, and reviews where they exist.
  - Every summary point links to a source; a manual check of 20 summaries finds no invented
    claims.

### M10 — Installer and delivery (3 days)
- PyInstaller + Inno Setup; bundles llama-server and MOTIS; clean Windows 11 VM test; one-page
  Czech user guide.
- **Done when:** a non-technical tester installs and gets results, commute times and employer
  cards without help.

### Estimate

| Milestone | Days |
|---|---|
| M0 Environment + spikes | 2 |
| M1 Data core, MPSV, search, wizard v1 | 3 |
| M2 Codebooks, normalization, filters | 2.5 |
| M3 Generic JSON-LD portals + dedup | 3 |
| M4 Indeed + LinkedIn | 2 |
| M5 AI layer + model picker + dynamic hardware adaptation | 6.5 |
| M6 Rest of the top 10 portals | 5.5 |
| M6b Social media, forums, images | 8 |
| M7 Automation | 2.5 |
| M8 Commute module | 8 |
| M9 Employer reviews module | 6.5 |
| M10 Installer and delivery | 3 |
| **Sum** | **52.5 days ≈ 10.5 weeks** |
| + ~15–20 % buffer (site changes, timetable data surprises, model tuning) | **≈ 12 weeks** |

Weeks are 5 working days of one full-time developer. Milestones are delivered in order and
each one is usable:
- after M1–M2 (~1.5 weeks): official data + search;
- after M5 (~3.5 weeks): AI;
- after M7 (~7 weeks): all job sources and automation;
- after M8–M9 (~10.5 weeks): commute and employer modules;
- M10: the installer.

---

## 10. Testing strategy
- **Unit:** normalizers, filters, dedup, query builder, hardware-fit rules, employer
  resolver, commute cache.
- **Contract:** saved pages for every job source and review source.
- **Integration:** pipeline on an MPSV sample; MOTIS on a small regional extract (e.g., one
  kraj) in CI-sized tests.
- **AI eval:** extraction, images, review summaries (faithfulness).
- **Installer:** clean VM smoke test.

---

## 11. Setup and disk usage

**End user:** `AdaWork-Setup.exe` → wizard. Automatic downloads:

| Item | Size |
|---|---|
| AI model | 1.6–3.4 GB (by tier) |
| OSM Czech extract | ~0.95 GB |
| MPSV jobs | ~0.02 GB download (gzip) + ~0.1 GB database |
| Embeddings model | ~0.12 GB |
| Timetables (Spojenka GTFS) | ~0.09 GB |
| MOTIS imported data | 4.0 GB whole CZ; 1.3 GB with the 150 km profile |
| RÚIAN address points | ~0.06 GB |

Total free disk needed: roughly **8–15 GB**; on the minimum test machine with the Simple model
and a home-area routing import, about **6–8 GB**. The wizard checks free disk space and shows it
next to the model icons.

**Developer:** `winget install Python.Python.3.12 astral-sh.uv Git.Git`, then `uv sync`;
`build.ps1` fetches the llama.cpp and MOTIS release binaries.

---

## 12. Legal and ethical notes
- MPSV, ARES, ISIR, the VAT register, the agency register, RÚIAN (ČÚZK) and OSM (ODbL,
  attribution shown on the map) are published for reuse.
- **Spojenka GTFS is licensed for non-commercial use only.** The app stays personal/free
  (§14); a commercial version would need its own timetable feed (regional GTFS + JDF with stop
  coordinates). Attribution shown in the commute window.
- osmium-tool is GPL-3.0: shipped unmodified as a separate executable that the app calls,
  with its license and source link in the installer.
- Portals, Atmoskop and Indeed reviews: personal, low-volume use, robots.txt respected,
  no redistribution; every source can be switched off. Firmy.cz/Mapy.cz reviews are not
  scraped (robots.txt).
- Google ratings only through the official API within its terms.
- Map tiles: OpenFreeMap (free, no key, attribution "OpenFreeMap © OpenMapTiles, data ©
  OpenStreetMap" shown by the map) until offline MOTIS tiles replace them in M8.
- LinkedIn: guest endpoints only, no login; off by default because its robots.txt disallows
  them. Indeed: not read at all, only linked.
- Facebook/Instagram: breaks Meta's terms; runs only after explicit consent, low limits, stops
  on verification; no password storage.
- Reviews and mentions are shown with their sources; AI summaries are labelled as
  AI-generated, so the user can verify claims about real companies.
- Personal data stays local; social posts expire after 14 days.

---

## 13. Risks and mitigations
| Risk | Mitigation |
|---|---|
| 8 GB RAM is not enough when components overlap | `ResourceManager` runs one heavy job at a time; model and MOTIS start on demand and stop when idle; Simple tier recommended; memory tested on the minimum test machine in M0, M5 and M8. |
| 2B model not accurate enough | Profile-matching ads first, rules first, candidate lists for ISCO; alternatives in the M5 eval; the user can choose Good (yellow) and accept slower processing. |
| MOTIS import too heavy for an 8 GB notebook | Measured in M0: 150 km clip + osmium renumber peaks at 1.65 GB (3.7 GB in the osmium step); still to confirm on a real 8 GB machine (page file). Plan B: Mapy.com API (car/walk/bike) + IDOS links. |
| Spojenka GTFS disappears or changes license | Regional GTFS (PID, IDS JMK) works directly; JDF converters exist, stop coordinates would need OSM matching (jrutil-style). |
| MOTIS loops on a malformed GTFS | Sanitize before import (drop in-seat transfers); import in the background with a time limit; keep the previous data until the new import succeeds. |
| Job location only known as a town | Precision flag, "přibližně" label, address geocoding when available. |
| Wrong employer match → wrong reviews | IČO first, confidence score, "nejisté přiřazení" label, candidates shown, manual fix. |
| AI summary misrepresents reviews | Summaries only from collected texts, each point linked, faithfulness eval, AI label. |
| ddgs / Atmoskop / Indeed blocking | Small daily limits, cache 30 days, on-demand fetching. |
| Google key needs a card | Optional; the app works without it and never exceeds the free quota. |
| Facebook/Instagram account restriction (no published safe limit) | Read-only, 100/50 posts per day, 2 short daytime sessions, irregular timing, own computer and session, stop + 7-day pause on any block, separate Instagram account suggested, risk table before consent (§4.3.1). |
| Portal layout changes | JSON-LD first, contract tests, health flags. |
| Small model inaccurate / slow | Tiered models with hardware-fit icons, rules first, eval, background queue. |
| Large first-run downloads | Background, resumable, app usable with MPSV data within minutes. |
| Unsigned installer warnings | No UPX, submit to Microsoft; code-signing certificate later (paid). |

---

## 14. Decisions to confirm before implementation
1. ~~UI language~~ — **confirmed:** Czech by default, English selectable.
2. ~~Facebook/Instagram~~ — **confirmed:** risk accepted. **Off by default**, with a visible
   warning and explicit opt-in in the wizard (§7.1 step 4); limits 100 Facebook / 50 Instagram
   posts a day.
3. ~~Defaults~~ — **confirmed:**
   - automatic checks **off** (when switched on: 6/12/24/48 h, 12 h preselected);
   - start with Windows on, notifications on;
   - all sources on except Facebook/Instagram and key-based sources.
4. ~~Google ratings~~ — **confirmed:** optional wizard step (step 5), with a note that Google
   requires a billing account; the app stays within the free 1 000 requests/month.
5. ~~Target notebook~~ — **confirmed:** hardware varies, so the model and resource settings
   are decided dynamically per computer (§3.2.1, §6.6). The minimum test machine is 8 GB RAM
   with a budget Ryzen and integrated Radeon.
6. ~~LinkedIn~~ — **confirmed:** same as Facebook: off by default, visible warning, opt-in
   (§7.1 step 4). Built in M4 as an own small adapter on the guest pages python-jobspy
   uses (no pandas/tls-client dependency, same polite fetcher). No robots-compliant LinkedIn
   tool exists; the logged-in alternatives risk the user's account.
7. ~~Indeed~~ — **confirmed, then changed in M4 (2026-10-06):** the public search page
   blocks the app's honest User-Agent with a Cloudflare check, so Indeed is **link-out only**
   (user's choice over a browser-like User-Agent, a later Edge reader or dropping it).
8. ~~Public-transport timetables~~ — **confirmed:** Spojenka GTFS (nationwide, non-commercial
   license). The app stays personal/free; replacing the feed is needed only if that changes.
