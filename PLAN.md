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

Status: **M0–M10 done**, architecture rework A1–A6 done and folded into them (2026-10-16).
Still to do: M7's 3-day unattended run, M10's non-technical tester.
Spike results: `docs/spikes.md`. How the code is organized: [ARCHITECTURE.md](ARCHITECTURE.md).

---

## 1. Goals and non-goals

### Goals
- Collect open positions for **any profession** from the top 10 Czech job sites, LinkedIn,
  Facebook (groups, Marketplace), Instagram, public forums and the official Úřad práce data.
- **As simple as possible for the user:**
  - one installer, no Python, no Ollama, no browser extension, no command line;
  - one **first-run setup wizard** that asks only what the app cannot work out itself
    (home address, AI model choice, which sources to read), with defaults for everything
    else; logins and API keys are set later in Settings and on the Sources page;
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

Verified live in the M0 spike (`docs/spikes/sources.md`, 2026-10-06). No portal needs a browser;
only the opt-in Facebook/Instagram reader drives Edge (§4.3.1).

| Source | Access method | Verified facts |
|---|---|---|
| **MPSV / Úřad práce ČR open data** | Free JSON, no key | **`volna-mista.json.gz` (17.5 MB**, the plain JSON is 187 MB), updated **daily**, ETag → 304. 39,487 records. CZ-ISCO, salary range, hours, employer **with IČO**, workplace (72 % with a RÚIAN address code), flags, 21 codebooks. Structured → **no AI needed**. Detail link: `up.gov.cz/volna-mista-v-cr#/volna-mista-detail/<portalId>`. **Implemented in M1.** |
| **python-jobspy** | Python library | **Not used**: reads Indeed through a robots-disallowed API and pulls in pandas; LinkedIn has an own adapter (§14). |
| **Indeed CZ (HTML)** | Public search page `cz.indeed.com/jobs` | Allowed by robots.txt, but Cloudflare answers an honest User-Agent with 403. **Link-out only** (§14). |
| **prace.cz** (19.6k ads), **jenprace.cz** (24.1k) | JSON-LD `JobPosting` on detail pages | prace.cz robots disallows `/search/`, `/hledat/` (not needed). jenprace.cz redirects `/nabidky` by Geo-IP → discover via explicit town slugs. prace.cz shares ads with jobs.cz. |
| **profesia.cz** (1.4k) | **Microdata** `JobPosting` (not JSON-LD) | Branded employer templates have no structured data → meta tags + AI fallback. |
| **jobs.cz** (16.5k) | **Server-rendered HTML** (not JS-only) | 30 cards per listing page, stable `data-test="jd-*"` attributes on detail, no JSON-LD → custom HTML adapter, no browser. robots disallows `/api/`, `/iapi/`. |
| **startupjobs.cz** (0.4k) | Offers sitemap → JSON-LD on detail pages | Listing pages are client-rendered and carry no ads. Only portal with GPS. |
| **prace.bazos.cz** (6k) | Plain HTML | Very messy ads; crawl the 24 category pages (robots disallows search params), filter by PSČ locally. |
| **hitprace.cz** | JSON-LD on detail pages | Aggregator that re-lists other portals → heavy duplicates. |
| **dobraprace.cz** (7.7k) (= jobsik.cz, same operator) | Region listings → JSON-LD | jobsik.cz skipped at first (same inventory). |
| **fajn-brigady.cz** (2.1k) | Detail sitemap with `lastmod` → rule-based HTML parser | |
| ~~volnamista.cz~~ | — | **Dropped**: robots.txt allows only named search engines (`User-agent: * Disallow: /`). |
| **pracezarohem.cz** (21.5k) | Listing pages carry `__NEXT_DATA__` JSON (50 ads/page) | Checked 2026-10-06, robots.txt allows listings. GPS, salary text, IČO inside `ga.itemBrand`. Part of the inventory is re-listed from profesia.cz (`PROF…` IDs). M6, confirmed. |
| **inwork.cz** (31.9k) | Detail sitemap → HTML (no structured data) | Checked 2026-10-06, robots.txt allows details. Custom HTML parser. M6, confirmed. |
| **easy-prace.cz** (10.4k own + 24k re-listed) | Region listings → HTML (no structured data) | Checked 2026-10-06, `Allow: /`. Only the own ads (`/nabidka/…`) are worth reading, and only they are in the region listings; `/volne-misto/…` re-lists Úřad práce. M6, confirmed. |
| **personalka.cz** (40.6k) | Sitemaps → HTML | Checked 2026-10-06, robots.txt allows details. Mostly re-listed Úřad práce ads, merged by dedup. M6. |
| **Jooble** / **Careerjet** | Free APIs with key | Careerjet API v4: key as HTTP Basic user, `user_ip` + `user_agent` required. Jooble: request limit unconfirmed (sign-up page behind Cloudflare). |
| `outpost2026/MCP-Jobs` | GitHub, no license | Reference only. |

### 2.2 Social media and forums

| Where | Logged-out access | Approach |
|---|---|---|
| Facebook groups | No (a logged-out request returns a ~1.5 KB stub page) | Automatic reader with the user's own login |
| Facebook Marketplace | Search needs login; the jobs category exists only in the US, so in CZ jobs are ordinary listings | Same reader, keyword searches near home |
| Instagram | Login wall; Graph API needs a Business account + Meta developer app | Same reader with the user's Instagram login |
| Public forums | Usually open | Checked in M6b: classified boards (HyperInzerce, Annonce, Bazar.cz, cBazar, Mimibazar) carry offers; discussion forums (eMimino, Okoun) carry none (§4.3) |

Facebook's robots.txt disallows all crawlers and prohibits automated collection without
permission. Automatic reading through a logged-in session **breaks Meta's terms and can get
the account restricted**. Facebook and Instagram are therefore **off by default**. The wizard
shows a visible warning, and the user must switch them on and confirm (§7.1 step 4).

### 2.3 Top 10 Czech job sites

Based on the jenprace.cz overview (18 Sep 2026, written by a competitor) and traffic knowledge:
jobs.cz, prace.cz, jenprace.cz, ~~volnamista.cz~~ (robots.txt blocks it, replaced by
dobraprace.cz), profesia.cz, Indeed CZ, LinkedIn, hitprace.cz, bazos.cz – práce,
startupjobs.cz; plus fajn-brigady.cz, MPSV, Facebook, Instagram, classified boards (7 sections in
`adawork/scraper/sources/classifieds.yaml`), Jooble, Careerjet.

### 2.4 Employer reviews and checks

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
| Secrets | Windows Credential Manager through the Windows API (`adawork/secrets.py`). |

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
| **AI model tier** (Auto mode, default) | RAM, GPU/VRAM, CPU, disk, measured speed | 8 GB CPU only → Simple; 8–16 GB iGPU → Good; 32 GB or a GPU with ≥ 8 GB VRAM → Excellent |
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
| AI model, **Simple** tier (Qwen3.5 0.8B) | ~1.1 GB (est.) | on demand; stopped after 5 min idle |
| AI model, **Good** tier (Qwen3.5 2B) | ~1.8 GB (est.) | on demand; stopped after 5 min idle |
| MOTIS routing server (home area) | ~0.5–1.5 GB (to be measured in M0) | on demand for route/commute calls; stopped when idle; results cached |
| MOTIS import | to be measured in M0 | only when no other heavy job runs, preferably while the PC is idle |
| Edge (Playwright: social reader only) | ~0.4–0.8 GB | short sessions, never in parallel with the AI model |
| Embeddings (e5-small) | ~0.15 GB | in short batches |

**Consequences on the minimum machine:**
- **Recommended model there: Good (Qwen3.5 2B).** Expected icons:
  Simple 🟢, Good 🟢, Excellent 🟡 (~3.6 GB). Excellent can still be chosen; the app then
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
Python 3.12, uv, httpx, selectolax, ijson, SQLite + SQLAlchemy 2 + Alembic (auto-migrate,
backup first), FTS5, onnxruntime + tokenizers (embeddings), numpy, Pillow, pyproj,
rapidfuzz, ddgs, Playwright (social only), FastAPI + Jinja2, HTMX + Pico.css + MapLibre
(vendored), pydantic v2; ruff, pyright, import-linter, pytest.

Background work runs on threads, not asyncio: the database access, the parsers and the helper
processes are synchronous, and FastAPI runs the synchronous routes in its thread pool. Why not
Scrapy or crawl4ai: the volume is moderate and the sources are very different from each
other; a thin plugin layer (`scraper/crawl.py`) is simpler.

---

## 4. Architecture

```
┌──────────── App window (pywebview) + tray icon + Windows notifications ────────────┐
│ Wizard │ Dashboard │ Search+Filters │ Map │ Job detail │ Commute window │          │
│ Employer card │ Sources │ Settings                                                 │
└──────┬───────────────────────┬──────────────────────────┬──────────────────────────┘
┌──────▼───────────────────────▼──────────────────────────▼──────────────────────────┐
│ services.py: Scheduler · ScrapeRunner · TravelFilter · Enricher · ModelManager     │
│              CommuteService · RoutingData · EmployerService · Secrets              │
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
┌──▼──── SQLite (%LOCALAPPDATA%\AdaWork\adawork.db) ─────────────────────────────────┐
│ jobs · sources · employers · employer_names · employer_reviews · travel_times ·    │
│ locations · codebooks · saved_searches · llm_cache · settings                      │
└────────────────────────────────────────────────────────────────────────────────────┘
* optional, needs a Google API key
```

### 4.1 Source plugin contract

```python
class Source(Protocol):            # adawork/core.py; every source in scraper/sources/__init__.py
    id: str
    label: str
    kind: SourceKind               # portal | classifieds | api | social
    default_enabled: bool
    needs_consent: bool            # off until the user accepts a warning (LinkedIn, social)
```
How a source is read depends on its class: `MpsvSource.read` (one daily dump),
`CrawlSource` (listing and detail pages, `scraper/crawl.py`), `ApiSource.search` (the user's
key), `SocialSource` (a session in Edge). Every one produces `ParsedJob`s for `JobStore`.
`ScrapeQuery` is generated from the wizard profile (home, commute limit, optional kinds of
work). Full-dump sources fetch everything and filter locally.

### 4.2 Shared HTTP layer
`net/`: per-domain pace, robots.txt, honest User-Agent, retries/backoff, pause on 403/429,
ETag, resumable downloads; a stop the app sets when it closes ends a search at its next
request. A robots.txt that does not arrive (the site or the network down) is asked for
again, not taken as "disallow all" for the session; one starting with a byte order mark is
read (v1.2).

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
  - **Off by default.** Enabled only by an explicit opt-in on the Sources page (not in the
    wizard, which asks for no logins), each time after the risk warning.
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
- **Classified boards** (`scraper/sources/classifieds.py` + `classifieds.yaml`): 7 job sections
  of HyperInzerce, Annonce, Bazar.cz, cBazar and Mimibazar, each a source of its own. A board is
  described by a link pattern and a text selector; "label: value" rows (place, pay, employer,
  education, contract) are read generically, the place also from the ad URL or page title.
  Newest first, ≤ 60 ads per board and run, unseen for 30 days = gone. Checked 2026-10-09:
  avizo.cz now answers an honest User-Agent with a bot challenge (dropped); the discussion
  forums eMimino "Zaměstnání" and Okoun "Práce" had no job offers (68 threads, 0 offers), so
  they are not read; their employer-experience threads belong to M9 (web mentions).
  User-added board URLs are not supported: a board needs its link pattern and text selector.
- **Offers and red flags** (`normalize/offers.py`), for every free-text source (boards, Bazoš,
  social): posts that are not offers (people looking for work, services, loans, sales) are
  stored but never listed, so they are not read again; red flags (fee from the applicant, MLM,
  assembly at home, easy money, unpaid trial days, adult, referral-only, crypto) hide an offer
  until the user ticks "Zobrazit i podezřelé nabídky", and the card names the warning.
- **Manual import** ("Vložit inzerát", `/add`): paste text and/or drop a photo or screenshot;
  the picture is read by the vision model, a photo-only ad takes its title from the poster.
- Reposts: the same picture (dHash ≤ 4 bits) or the same text start, with agreeing words, is
  one offer (`scraper/images.py`, `scraper/reposts.py`). Social posts are deleted 14 days after
  they were last seen.

### 4.3.1 Facebook/Instagram limits and risks (researched 2026-10-06)

**No safe number exists.** Meta publishes no limits; numbers found online come from
automation-tool vendors and concern actions (posting, liking), not reading. Detection is
behavioural (account age, device and IP trust, timing, automation signs). Reading 100–200
posts a day is close to normal browsing, on the user's own PC, IP and session; hence the
conservative defaults and stopping at the first warning.

| Level | What happens | Duration |
|---|---|---|
| "Going too fast" temporary block | A feature (e.g. viewing groups) is blocked | Typically 24–48 h |
| Checkpoint / verification | Identity check before using the account | Until resolved |
| Instagram action block | Actions blocked | 24–72 h, repeats longer |
| Temporary restriction | Limited features (groups, Marketplace) | Days to weeks |
| Permanent disable | Account lost, incl. Messenger, groups, photos | Permanent (appeal possible) |

- **Terms:** Meta's Automated Data Collection Terms forbid automated collection without
  permission, logged in or out (since 1 Jan 2025). Before that, *Meta v. Bright Data* (Jan 2024)
  had allowed logged-out scraping of public data in a US court. Against individuals Meta acts
  on the account; lawsuits and letters have targeted commercial scrapers.
- **GDPR:** personal use on the user's own PC falls under the household exemption
  (Art. 2(2)(c)); data stays local, social posts expire after 14 days, no contact export.
- **Scam ads** are marked by the red-flag rules and the employer module (§4.5).
- **Breakage:** Meta changes its pages often; only the reader stops.
- A separate Instagram account is suggested (Instagram allows several); a second Facebook
  account is not (against Facebook's rules).

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
10. Expire: after a complete pass (a region's listing read to its end; not when most known
ads went unseen at once), or when unseen for N days on sources that expire by time
(newest-first searches, HitPráce); never after a run the site refused or broke off, or one
that saw no ads while no listing said it has none. A newest-first listing cut short (also
by the run's limit of detail pages) is walked to its end on a later run (v1.2).
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

The background work (ARES name resolving, official checks, profile-match reviews, mentions,
summaries) runs only with the switch "Ověřovat zaměstnavatele automaticky po každém hledání"
(off by default). Off: ads are still linked locally (IČO, brands, agency list), and everything
else runs when a card is opened.

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

**Data and updates (automatic once downloaded):**
- Nothing downloads until the user clicks "Stáhnout mapu" (wizard step 6 or Settings → Mapy).
  Then: download the OSM CZ extract (948 MB) and the Spojenka GTFS (85 MB), sanitize the
  GTFS, and import with MOTIS in the background.
- **Profile by hardware** (measured in M0):
  - ≥ 16 GB RAM: whole Czechia, ~7 min import, peak 5.5 GB, 4 GB data, geocoding on.
  - 8 GB RAM: osmium extract 150 km around home + renumber, clip GTFS, import (~2 min, peak
    1.65 GB, 1.3 GB data), geocoding off, `background` tile profile. A new home address
    re-runs this pipeline. The import reserves ~5 GB of virtual memory, so check the page file.
- Until the first import finishes, and during later ones, travel times are **estimates**
  (straight-line distance × mode factor), labelled "odhad"; the filter, the sort and the
  commute window use them (before the first import a note links to the download).
- Timetables refresh weekly (Spojenka is valid 14 days), OSM monthly; the updated data is
  imported in the background and switched in when ready.
- No own JDF converter is needed while Spojenka is used; regional GTFS (PID, IDS JMK) is the
  fallback if it disappears.

**Starting point and defaults:**
- Set in the wizard: home address (autocomplete), usual mode, max travel time. Departure time
  defaults to weekdays 7:00.
- Can be changed any time in Search and in the commute window (type an address, use the home
  address, or click the map).

**Travel-time filter (Search page):**
- Controls: start point · mode (Auto / MHD / Pěšky / Kolo / "any of them") · **slider
  5–120 min** · departure time.
- When the start, mode or departure time changes:
  - car/walk/bike: one MOTIS **one-to-many** call from the start to all distinct job
    locations;
  - public transport: intermodal one-to-many (walk + transit), the best connection leaving
    within the hour after the departure time.
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
- `employers(id, ico, name, match, confidence, candidates, dic, legal_form, founded, ended,
  address, nace_main, employee_band, insolvency, vat_unreliable, agency, looks_agency,
  warnings, checked_at, satisfaction, links, reviews_at, mentions_at, jobs_count)`: one row
  per IČO, or per name that could not be resolved (with its candidates).
- `employer_names(key, employer_id, source[ad|ad_ok|brand|agency|inside|ares|ai|manual|
  uncertain|none])`: the learned name → employer cache. Curated brands: `employers/brands.yaml`.
- The VAT, agency and Atmoskop lists stay files in the cache (M7 downloads).
- `employer_reviews(employer_id, source[atmoskop|google], rating, scale[percent|stars5], count,
  scores, texts, url, fetched_at)`
- `employer_mentions(employer_id, source, url, title, snippet, passages, fetched_at)`
- `employer_summaries(employer_id, summary, sources, inputs, model, prompt_version)`
- `travel_sets(origin, depart, mode)` + `travel_times(set_id, location_id, minutes, version)`
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
| **Jednoduchý / Simple** | `Jednoduchý (Qwen3.5 0.8B, 0.7 GB)` | 0.7 GB | ~1.1 GB (est.) |
| **Dobrý / Good** | `Dobrý (Qwen3.5 2B, 1.9 GB)` | 1.9 GB | ~1.8 GB (est.) |
| **Výborný / Excellent** | `Výborný (Qwen3-VL 4B, 3.0 GB)` | 3.0 GB | 3.6 GB |

Qwen3.5 4B and Qwen3-VL 2B stay selectable under "Pokročilé"; Gemma 4 was removed
(2026-11-14). 8–14B models (3–6× slower, no more accurate) were tested and removed from the
catalog (`docs/spikes.md`).

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
   yellow, rules only (decided 2026-11-17: over 4 s per ad, turning the AI off is
   recommended).
5. **Keep it right over time (Auto mode only):**
   - Re-check at every start and from the running speed statistics (§3.2.1).
   - If a bigger tier becomes 🟢 (e.g. after a RAM upgrade), download it in the background,
     benchmark it, and switch only if it really is faster than the 🟡 threshold. Then notify:
     "Model byl vylepšen na Dobrý".
   - The old model stays as a fallback until the new one has passed. **Models are never
     deleted automatically** (decided 2026-10-07): the AI page lists the downloaded models
     with a "Smazat" button for each and "Smazat vše".
   - If the current model becomes too slow or memory-starved, switch down to a smaller tier.

| Icon | Meaning | Rule (initial) |
|---|---|---|
| 🟢 (green check) | "Poběží plynule" — runs smoothly | RAM need ≤ 40 % of total RAM **and** estimated ≤ 1 s per ad |
| 🟡 (yellow gauge) | "Poběží, ale pomaleji" — works, slower | RAM need ≤ 60 % **or** 1–4 s per ad |
| 🔴 (red cross) | "Nedoporučeno" — not enough memory / too slow / not enough disk | otherwise; can still be chosen after a confirmation |

- A legend under the list explains the icons; for 🔴 it recommends turning the AI off.
- An own server (set under "Pokročilé") shows as a selected "Vlastní server" option below the
  tiers; picking another option turns it off.
- Icons are SVGs with a text label and a tooltip (not color only), e.g.
  `🟢 Dobrý (Qwen3.5 2B, 1.9 GB) — ~9 s na inzerát`.
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
| 8 GB, budget Ryzen, iGPU (minimum) | 🔴 | 🔴 | 🔴 | rules only |
| 16 GB, mid-range CPU, iGPU | 🔴 | 🔴 | 🔴 | rules only |
| 32 GB, Intel Core Ultra 7, iGPU (**measured**) | 🟡 3.6 s | 🔴 6.4 s | 🔴 14.5 s | Simple |
| 16–32 GB + GPU with ≥ 8 GB VRAM | 🟢 | 🟡 | 🟡 | Simple |

### 6.7 Evaluation
Golden set: ~60 text ads + ~30 social posts + ~20 image ads + ~20 employer review sets
(summary faithfulness). `adawork eval` per model: per-field accuracy, JSON validity, speed.

### 6.8 Images
Implemented in M6b without RapidOCR: pictures of ads are downloaded (≤ 1280 px JPEG, image
cache), shown on the result card, and for ads with less than 600 characters of own text the
vision model writes out the picture's text, which is added to the ad and goes through the
rules and the AI (same checks). A picture without text gives nothing; a picture already read
(same hash, e.g. an employer's logo on many ads) is not read again. Measured on real board
pictures (`docs/spikes.md`): 15–30 s per poster on the iGPU, ~40 s on 4 CPU
threads, so RapidOCR is not needed for the expected volume (tens of picture ads a day).

---

## 7. User interface

The app window is served from `127.0.0.1` on a free port. UI in Czech, English selectable.

### 7.1 First-run setup wizard ("Průvodce nastavením")
Every step has defaults; optional steps have "Skip"; downloads run in the background.

1. **Vítejte** — language and colour scheme (follow Windows, light, dark or one of five
   colour schemes).
2. **Odkud dojíždíte a co hledáte** (where you commute from, what you look for):
   - home address (autocomplete; precise address or just a town);
   - usual travel mode (Auto / MHD / Pěšky / Kolo / "nevím, ukaž vše");
   - max travel time slider (default 45 min);
   - optional kinds of work (empty = everything; asked only here, prefills the search).
     Free words ("ucitelka ve skolce, druzinarka") are mapped to CZ-ISCO occupations: the
     title index finds candidates, the model picks from them (`adawork/ai/wanted.py`). A job
     matches by occupation or by the words; keyword portals also get a plain job title.
   This drives generated searches, the default travel-time filter and employer prefetching.
3. **Umělá inteligence** — shows the detected hardware ("16 GB RAM · AMD Radeon · 120 GB
   volného místa"). Below it: **"Automaticky – nejlepší pro tento počítač"** (preselected),
   then Simple / Good / Excellent with hardware-fit icons (§6.6). The download of the chosen
   model starts in the background.
4. **Zdroje** (sources) — switches for the job portals and classifieds, all **on** by default:
   - **LinkedIn** switch on the same step, **off** by default, with its own short warning: it
     breaks LinkedIn's rules for robots, the computer's IP can be blocked for a while, no
     login is used so the account is not at risk. Turning it on needs the confirmation tick.
   - The wizard asks for **no logins and no API keys**. Facebook and Instagram (a login in an
     Edge window) and Jooble and Careerjet (API keys) are not shown here; they are set on the
     Sources page and in Settings → Klíče k dalším službám. Running the wizard again keeps their switches.
   - **Facebook / Instagram** on the Sources page: two switches, both **off** by default.
     - A **visible warning box** (orange/red frame, warning icon, always shown, not hidden
       behind a link). In plain Czech it says:
       - automatic reading breaks Meta's terms of use;
       - Facebook/Instagram can temporarily block or **permanently disable the account**
         (lost Messenger, groups, photos);
       - no limit is guaranteed to be safe;
       - the app reads slowly (100 / 50 posts a day), stops at the first warning, and never
         stores the password.
       - A "Více o rizicích" (more about risks) expander shows the full risk table from
         §4.3.1.
     - Turning a switch on requires ticking **"Rozumím riziku a chci tuto funkci zapnout"**
       ("I understand the risk and want to turn this on"). Only then does the "Přihlásit se"
       button open the Edge login window.
     - For Instagram, a hint suggests a separate Instagram account.
     - A small warning badge stays next to an enabled Facebook/Instagram source.
   - **API keys** (Settings → Klíče k dalším službám), each with a "Získat zdarma" link and a short how-to,
     stored in Windows Credential Manager:
     - Jooble, Careerjet;
     - **Google Maps** (customer ratings): Google requires a billing account with a card even
       for the free 1 000 requests/month; the app never exceeds the free limit.
5. **Automatika**:
   - "Automaticky hledat nové nabídky" (check for new jobs automatically): switch **off by
     default**. When switched on, the interval is **6 h / 12 h / 24 h / 48 h** (12 h
     preselected); social media runs at most twice a day regardless.
   - Start with Windows (default on); notify me about new matches (default on).
   - With automatic checks off, scraping runs when the user clicks "Hledat nové nabídky" on
     the Dashboard or in the tray menu.
   - Automatic checks can be switched on/off or paused any time from the tray or Settings.
6. **Mapy a jízdní řády** — "Stáhnout mapu" (~1 GB) and the area; "Přeskočit" leaves travel
   times off until downloaded from Settings.
7. **Zaměstnavatelé** — "Ověřovat zaměstnavatele automaticky po každém hledání" (**off** by
   default: checks and reviews only when a card is opened) and the review sources. Google
   customer ratings are not offered here: they need an API key (Settings → Zaměstnavatelé).
8. **Hotovo** — summary; "Start" runs MPSV first (fast, all industries).
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
6. **Vložit inzerát**, and **Nastavení / Settings** behind the gear at top right: the wizard opens
   by itself only on first start and again from a button at the top of Settings; below it one
   page per wizard step in wizard order (language and look, profile, AI model list, sources +
   social limits and accounts, API keys + Google quota counter, automatic checks), then map
   data status/refresh, data folder, logs.

---

## 8. Project structure

```
AdaWork/
├─ pyproject.toml / uv.lock / README.md / PLAN.md / ARCHITECTURE.md
├─ adawork/
│  ├─ __main__.py, cli.py, services.py (wiring), scheduler.py, worker.py (background loop)
│  ├─ core.py (ParsedJob, Source, coded values), config.py, i18n.py, secrets.py, paths.py,
│  │  licenses.py, logs.py
│  ├─ net/  http.py (client, downloads), polite.py (pace, robots, stop), robots.py
│  ├─ system/  hardware.py, resources.py (heavy-job slots), winjob.py, ports.py
│  ├─ desktop/  window.py, tray.py, icon.py, autostart.py, instance.py, server.py
│  ├─ db/  models.py, session.py (migrate + backup), migrations/
│  ├─ scraper/                      # MODULE 1
│  │  ├─ runner.py (a search), crawl.py (portal plugin), api.py, store.py, dedup.py,
│  │  ├─ structured.py (JSON-LD, microdata), images.py, reposts.py, probe.py (dev check)
│  │  └─ sources/  mpsv, prace_cz, jobs_cz, jenprace, pracezarohem, profesia, dobraprace,
│  │               easy_prace, personalka, inwork, fajn_brigady, startupjobs, hitprace,
│  │               bazos, classifieds (+ classifieds.yaml), linkedin, indeed (link),
│  │               jooble, careerjet
│  ├─ social/  browser.py (Edge via Playwright), posts.py, reader.py, logins.py
│  ├─ employers/                    # MODULE 3
│  │  ├─ service.py (background pass, cards), common.py, linking.py (name → employer),
│  │  ├─ checks.py (registers), reviews.py (reviews, mentions, summaries)
│  │  └─ official.py, resolver.py, names.py, brands.yaml, atmoskop.py, google.py,
│  │     mentions.py, summary.py (AI), downloads.py
│  ├─ routing/  motis.py, data.py (OSM + GTFS, import), gtfs.py, commute.py, deeplinks.py
│  ├─ geo/  places.py, home.py, scope.py, ruian.py, geocode.py
│  ├─ normalize/  text.py, vocab.py, offers.py
│  ├─ ai/  catalog, advisor, download, runtime (llama-server), manager, benchmark, client,
│  │       grammar, embeddings, isco, extract, enrich (queue), ask, vision, wanted, evaluate
│  ├─ search/  service.py (filters, facets), saved.py, travel.py (travel-time filter),
│  │           kinds.py (kinds of work → occupations, stored)
│  └─ web/                          # MODULE 2
│     ├─ app.py, pages.py (templates, Svc), routes/ (one router per area), export.py,
│     │  mapstyle.py (offline map style)
│     └─ templates/, static/ (htmx, pico, maplibre — vendored)
├─ vendor/  llama.cpp/, motis/, osmium/   # tools/fetch_*.py
├─ packaging/  adawork.spec, launch.py, installer.iss, build.ps1, navod.html (Czech guide)
├─ tools/  fetch_*.py, make_migration.py, make_golden.py, bench_mpsv.py
└─ tests/  fixtures/, unit/
```

---

## 9. Implementation milestones

### M0 — Environment, skeleton, spikes — done
- App window + tray; source, employer and routing spikes (`docs/spikes.md`). MOTIS: go (§2.5).
- **Done when:** the window opens; spike results in `docs/spikes.md` with a go/no-go for MOTIS.

### M1 — Data core, MPSV, basic search, wizard v1 — done
- Job list: a job's title opens its whole text; a button leads back to the top.
- **Done when:** fresh start → wizard steps 1, 2, 7 → MPSV import → full-text search in
  < 300 ms.

### M2 — Codebooks and normalization — done
- Shared vocabulary (`normalize/vocab.py`), RÚIAN coordinates, filters with facet counts
  (≤ 150 ms on 40k jobs), map with clustered pins. Tiles from OpenFreeMap until M8.
- **Done when:** the full filter set works with facets; the map shows jobs.

### M3 — JSON-LD portals + dedup — done
- Polite HTTP (`net/polite.py`, `robots.py`): robots.txt with `*`/`$`, ≥ 1.5 s per host,
  retries, 12 h pause on 403/429 or a bot challenge.
- `JobPosting` reader (JSON-LD, microdata); crawl framework: region listings → cards → ≤ 300
  detail pages per run (a month-old one is read again), expiry in a region only after its
  listing was read to the end; scope = regions reachable from home.
- Places (`geo/places.py`), dedup (`scraper/dedup.py`, §4.4); prace.cz, jenprace.cz,
  profesia.cz.
- **Done when:** a run for one home (Kolín, 30 min by car) reads all three portals without
  errors, and re-listed ads appear once with several source links (`docs/spikes.md` § M3).

### M4 — Indeed CZ + LinkedIn — done
- Indeed link-out only (§14.7). LinkedIn guest-page adapter, off by default, opt-in in the
  wizard or on `/sources`, where every source can be switched off.
- **Done when:** LinkedIn stays silent until opted in; an opted-in live run for Kolín read 29
  ads in 3 min without refusals.

### M5 — AI layer, model picker, hardware adaptation — done
- llama-server management, `HardwareProfile`, `ModelAdvisor` (Auto), fit icons, benchmark,
  resumable download, `ResourceManager`; extraction, ISCO, "Zeptejte se větou", `adawork eval`.
- Tiers Qwen3-VL 2B / Gemma 4 E2B / Qwen3-VL 4B (§6.6).
- **Done when:** on the 8 GB budget Ryzen with the Simple tier: ≥ 85 % ISCO group, ≥ 90 % kraj,
  ≥ 80 % contract type, 100 % valid JSON, ≤ 15 s per text ad, no swapping; fit icons match
  the measured speed on 3 machines and Auto picks the expected tier; AI work waits when free
  RAM is too low.
- Targets not met as written: ISCO 73 % at 2 digits (target 85 %; filter categories 89 %);
  contract type comes from rules. Not measured on the 8 GB Ryzen or a dedicated GPU.
- Deferred: semantic search over all jobs.

### M6 — Rest of the top 10 portals — done
- jobs.cz, Práce za rohem, dobraprace.cz, Easy-práce.cz, Personálka.cz, inwork.cz, Fajn
  brigády, StartupJobs.cz, HitPráce.cz, Bazoš (15 sources); sitemap discovery.
- Every site that can legally be added is added; new sites follow the same rule.

### M6b — Social media, forums, images — done
- Classified boards, offer and red-flag rules (§4.3); pictures (§6.8); reposts; manual import;
  Facebook/Instagram reader (§4.3.1, tested on hand-made pages only).
- **Done when:** every board parses on live pages; non-offers and scams stay out of the default
  results with no false alarms on portal ads; a poster's text reaches the filters; the social
  reader stops on a block page and clicks nothing but "See more"; the first run is timed.
- Sources in parallel: 4 slots, one host per group (longest first), slow sites (≥ 5 s crawl
  delay) in their own thread; a failed page is skipped.
- "Obnovit nabídky" (refresh): the portals' ads the list shows are checked on their own pages
  first, in list order; then the new finds go above the jobs found before (also when ranked
  by words); then every other ad not seen is checked. Ads not checked yet are dimmed, gone
  ones drop out, and the filters keep working throughout. "Obnovit od začátku" empties the
  list at once (a running search stops for it) and finds everything again.
- Complete first run: 54.5 min, 61.7k jobs.

### M7 — Automation — done
- Scheduler (`adawork/scheduler.py`): automatic checks **off by default**, 6/12/24/48 h counted
  from the last search (manual ones too), pause for 24 h / resume, retry after an hour when a
  check failed everywhere. Tray menu: search now, checks on/off, pause/resume. Start with
  Windows (HKCU Run value, `--minimized`) while checks are on.
- Notifications after an automatic check: new matches per saved search (or all new jobs when
  none is saved), and a social reader that stopped.
- Saved searches with a "new" count until opened; CSV/XLSX export of the filtered list.
- Jooble and Careerjet with the user's key (Settings → Klíče k dalším službám), kept in the Windows
  Credential Manager. Not tried live: no key.
- Background downloads, also with checks off: unreliable VAT payers (as DIČ → date) and the
  agency register daily, the Atmoskop sitemap weekly; a failure is retried after an hour.
- **Done when:**
  - Automatic checks are off after a fresh install, and the manual "Obnovit nabídky" button
    works. ✓
  - Once switched on, the app runs 3 days unattended from the tray. **Not run yet.**
  - Changing the interval or switching automation on/off reschedules without a restart. ✓

### M8 — Commute module — done
- MOTIS 2.11.3 and osmium-tool in `vendor/` (`tools/fetch_motis.py`). Map (OSM, monthly) and
  timetables (Spojenka, weekly) download in the background once the user asks, resumable; the
  GTFS is sanitized (and clipped); one import at a time through the `ResourceManager`. A failed
  import with new timetables re-imports the previous ones; new downloads stay pending until
  imported. Area by hardware or Settings: whole CZ (import 161 s, peak 5.7 GB, 4.2 GB data) or
  150 km around home (252 s, osmium peak 3.9 GB, import 2.3 GB, 1.9 GB data).
- `travel_sets` + `travel_times` (WITHOUT ROWID): minutes per location for car, transit, walk,
  bike and "any", up to 120 min. Straight-line estimates at once (labelled "odhad"), MOTIS in
  the background (parallel one-to-many; transit = best of 07:00/15/30/45). Car times × 1.14
  (MOTIS was faster than OSRM on all 10 sample trips). Places with no location or only a
  region always pass. The app process opts out of Windows power throttling (EcoQoS), which
  made it 3× slower without a visible window.
- Search: travel bar (start = home or typed address or map right-click, mode, slider 0–120,
  departure), sort by travel time, times on cards, reachable area on the map (grid cells,
  slider filters them). Commute window: 4 modes, legs with lines and times, departure/arrival,
  area slider, Mapy.com / IDOS / Google links. Offline map style for MOTIS tiles (both
  profiles); OpenFreeMap until the import is ready. Wizard step 2: address suggestions,
  usual departure time.
- **Done when** (61.7k jobs, 18.6k locations, Core Ultra 7 255U):
  - Slider updates the list in < 200 ms: median 179 ms. ✓
  - New start point ≥ 5 000 locations in < 30 s: 18.6k in 21.8 s (whole CZ), 23.9 s (150 km). ✓
  - 10 sample routes from Kolín within ±15 %: transit vs IDOS 9/10 (Poděbrady 14 vs 12 min);
    car vs OSRM 10/10 after the 1.14 factor fitted on the same trips (Mapy.com/Google have no
    keyless access). Partly ✓
  - Estimates shown and labelled before the import finishes. ✓
- Not measured on an 8 GB machine; the 150 km profile ran on 32 GB.

### M9 — Employer reviews module — done
- Jobs link to employers (`employers/service.py`, background after each search and hourly):
  the IČO in the ad; names learned from ads that carry one (a learned name unlike the
  register name is checked once in ARES: portals give a group's IČO, "IMOBA" → Agrofert);
  `brands.yaml` (~120 brands); the agency register; a known employer inside a longer name;
  ARES search with variants (ČR expanded, no venue words, no town, brackets, no country),
  ranked by name, legal form, size and activity (RES), seat. Close candidates: the AI picks
  with the ad's text, else "nejisté přiřazení" with candidates; the user can fix any match
  by IČO. Agency ads that name their client show it on the job card (rule, not AI).
- Official checks for every employer with an IČO, 100 per ARES/RES call (20.2k in ~25 min),
  ISIR only when ARES flags insolvency, VAT and agency lists locally; again after 30 days.
  Warnings: ceased, insolvency, unreliable VAT payer, agency work without a licence.
- For employers of profile matches (and when a card opens): Atmoskop (% satisfaction,
  reviews), Google Places with the user's key (stops at 950 details a month), web mentions
  via ddgs (retries; review sites read politely, job-site pages skipped); daily limits.
  Indeed and Facebook only as links (Facebook pages are not read).
- AI summary (`summary.py`): numbered texts, every point ends with its sources and is checked
  against them: praise only from praise, criticism only from complaints (the halves of an
  Atmoskop review), web passages back only warnings (unpaid wages, no contract…); words must
  stand together in one source; list parts no source says are cut. A small model invents
  otherwise (M9: "práce na černo" from a changed job title).
- Card (dialog from the job's employer name), badges on job cards, filters (satisfaction ≥,
  no warnings, verified, no unlicensed agencies, one employer's jobs), sort by employer,
  Settings → Zaměstnavatelé, Google key in Settings → Klíče k dalším službám with the month's count.
- **Done when** (61.7k jobs, 20.6k employers, Core Ultra 7 255U, Gemma 4 E2B):
  - ≥ 90 % of 50 sample employers resolve to the right IČO: 47/50 (94 %) of names from ads
    without an IČO, judged by hand (2 not found, 1 sole trader left uncertain with the right
    one first; none wrong); 46/50 on two samples of ads that give an IČO (3 ads per sample
    give the group's IČO for a subsidiary that was matched by name). ✓
  - Cards show official checks for all of them, and reviews where they exist: the 47 with an
    IČO show the checks, the 3 without one say so; Atmoskop where a profile exists (live
    check in Edge: card from the list, all jobs of the employer, uncertain match). ✓
- Employer filters on the full list: +20–40 ms (105 → 125–147 ms), sort by employer 224 ms.
  - Every summary point links to a source; no invented claims in 20 summaries: 21 written
    (5–46 s each), 14 with points, none invented on a manual check of every point against
    its sources; 7 left empty (nothing could be confirmed). ✓

### M10 — Installer and delivery — done (tester still to do)
- `packaging/build.ps1`: PyInstaller (one folder, no console, no UPX) + Inno Setup →
  `dist/AdaWork-Setup.exe`, 134 MB (525 MB installed). Installs for the user, no admin
  (`%LOCALAPPDATA%\Programs\AdaWork`), Czech/English, Start menu with the guide. An upgrade closes
  the running app; uninstall closes it, removes the start-with-Windows value and asks (default
  No) whether to delete the data (`%LOCALAPPDATA%\AdaWork`). Without WebView2 (an older
  Windows 10) it offers its download.
- Bundled: llama-server (Vulkan and CPU, only the server's files), MOTIS and osmium, with the
  Microsoft C++ runtime next to them (a clean Windows may lack it). AdaWork itself is MIT
  (`LICENSE`). License texts of all of it,
  down to the libraries built into MOTIS (its `.pkg` files), llama-server and the Python wheels
  (`THIRD-PARTY-NOTICES.txt`, `licenses/`, `tools/fetch_licenses.py`); Settings ends with
  "Zobrazit licence", which shows each one. pystray (LGPL) ships as source files. One-page
  Czech guide (`packaging/navod.html`).
- `ADAWORK_DATA` points the app at another data folder (testing, a portable copy); such a copy
  leaves the start-with-Windows value alone.
- The first-run tests found five faults; each was fixed in the milestone that caused it (M1,
  M3, M5, M8, M9).
- **Done when:** a non-technical tester installs and gets results, commute times and employer
  cards without help.
  - On this PC as a new user (own data folder; the app's own window driven through WebView2's
    debug port): wizard with defaults → Úřad práce jobs placed 3.5 min after Start (2 of 15.2k
    places without a point), map ready after 13 min, 2,986 of 42k jobs within 45 min of Kolín,
    exact times for 17.6k places in 18–57 s while the list stays, the AI on Vulkan, an employer
    card with official checks and mentions; upgrade and uninstall with the app running. ✓
  - Clean Windows 11 (Windows Sandbox, 8 GB, no Python, no C++ runtime; `dist/sandbox.wsb`):
    installed in 33 s; Auto picked Jednoduchý and the 150 km map; Úřad práce jobs a minute after
    Start, employer cards with official checks after 3 min, map imported (106 s) and exact times
    for 15.3k places (21.7 s) 9 min after Start, 3,865 jobs within 45 min of Kolín; the AI ran
    (~8.5 s per ad). ✓
  - Non-technical tester: not run.

### A1–A6 — Architecture rework — done (2026-10-16)
Folded into the milestones whose code it changed, so each commit stays green.
- **A1 Layering and checks:** shared types out of the feature packages (`core.py`, `net/`,
  `system/`, `i18n.py`), import-linter layers (no cycles), pyright, a test for UI texts in
  both languages, the database copied before a migration.
- **A2 Web:** `services.py` builds and connects the services; `create_app` only serves them;
  one router per area (`web/routes/`), the travel-time filter in `search/travel.py`.
- **A3 Background work:** one `Worker` loop (scheduler, employers, map data), `RunHooks` for
  what follows a search, and a stop that ends a search at its sites' next requests.
- **A4 Sources and data:** one `Source` contract with MPSV as a source, the registry derived
  from one list, the runs table written in one place, the job columns derived from
  `ParsedJob`, coded values as types.
- **A5 Big units:** `EmployerService` split into linking, checks and reviews; `crawl()` into
  one step per phase.
- **A6 Tools and docs:** `adawork probe` reads every portal live (one listing, one ad; all 20
  readable on 2026-10-16, LinkedIn refused with 429); ARCHITECTURE.md; this plan matches the
  code again.

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
| A1–A6 Architecture rework | 3 |
| **Sum** | **55.5 days ≈ 11 weeks** |
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
- **Contract:** saved pages for every job source and review source; `adawork probe` reads
  the live portals (one listing, one ad each) to catch layout changes the saved pages miss.
- **Checks on every commit (run by hand, no CI):** ruff, pyright, import-linter (layers),
  pytest.
- **Integration:** the first-run tests of M10 (a new data folder, Windows Sandbox).
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
`packaging\build.ps1` fetches the pinned programs and builds `dist\AdaWork-Setup.exe`
(needs Inno Setup: `winget install JRSoftware.InnoSetup --scope user`).

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
- Map tiles: offline from the imported OSM data (MOTIS, "© OpenStreetMap contributors");
  OpenFreeMap (free, no key, its attribution shown) only until the import is done.
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
| A virus scanner removes a new program (ESET removed the Vulkan llama-server.exe once) | The build stops when a bundled program is missing; the app falls back to the CPU build. |

---

## 14. Decisions
1. UI in Czech, English selectable.
2. Facebook/Instagram: off by default, visible warning, opt-in (§7.1 step 4); 100 / 50 posts a day.
3. Defaults: automatic checks off (6/12/24/48 h, 12 h preselected); start with Windows and
   notifications on; all sources on except Facebook/Instagram, LinkedIn and key-based ones.
4. Google ratings: optional (step 5), within the free 1 000 requests/month.
5. Hardware varies: model and resources are decided per computer (§3.2.1, §6.6); the minimum
   test machine is 8 GB RAM, budget Ryzen, integrated Radeon.
6. LinkedIn: own guest-page adapter, off by default, opt-in (§7.1 step 4).
7. Indeed: link-out only; Cloudflare blocks the honest User-Agent (M4).
8. Timetables: Spojenka GTFS (non-commercial license); the app stays personal/free.
