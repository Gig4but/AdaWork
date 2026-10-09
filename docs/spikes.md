# Spike and live-run results

Measurements behind the decisions in PLAN.md. Full M0 reports:
[sources.md](spikes/sources.md), [employers.md](spikes/employers.md),
[routing.md](spikes/routing.md).

## M0 spikes (2026-10-06)

**Job sources: work, with changes.** Every portal is readable with plain HTTP. MPSV open
data: 39,487 jobs, daily 17.5 MB `.json.gz` with ETag. jobs.cz is server-rendered HTML;
volnamista.cz is dropped (robots.txt); profesia.cz uses microdata; startupjobs.cz lists jobs
only in its sitemap; jenprace.cz redirects by Geo-IP; dobraprace.cz and jobsik.cz are one
operator. LinkedIn is off by default (robots.txt). Fixtures: `tests/fixtures/sources/`.

**Employer data: works, Indeed reviews blocked.** ARES, RES, ISIR, the VAT list, the MPSV
agency register and Atmoskop work without keys; Indeed reviews are behind Cloudflare (link
only); ddgs needs retries. ARES name search is weak, so the resolver uses aliases, name
variants, RES ranking and the AI (PLAN §4.5).

**Routing: GO for MOTIS, with conditions.**

| Measure | Whole Czechia | 150 km profile (8 GB machines) |
|---|---|---|
| Import time | 7 min 11 s | ~2 min (osmium extract + renumber, GTFS clip, import) |
| Import peak RAM | 5.5 GB | 1.65 GB (3.7 GB during the osmium extract) |
| Data on disk | 4.0 GB | 1.3 GB |
| Server RAM idle / under load | 0.5 / 1.6 GB | 0.2 / 0.9 GB |
| 5,000 destinations by car | 2.5 s | 1.25 s |
| Transit plan, one-to-all, geocoding, tiles | 20–100 ms | similar |

1. Timetables must be GTFS with stop coordinates: only **Spojenka GTFS** (non-commercial
   license) works nationwide; CIS JŘ NeTEx has no rail stop coordinates.
2. 8 GB machines need osmium renumbering (clipping alone does not lower import RAM).
3. Sanitize GTFS (in-seat transfers loop MOTIS); call `127.0.0.1`; gzip for tiles.
4. No isochrone endpoint: the reachable area is built from one-to-all results.

Built during M0/M1: job coordinates from RÚIAN (96.7 % of MPSV jobs); categories are the 16
official Úřad práce fields.

## M3 live run (2026-10-06)

Home Kolín, 30 min by car, ≤ 15 detail pages per portal, on a copy of the real database.

| Source | Listing pages + details | Time | New jobs | Merged into existing |
|---|---|---|---|---|
| prace.cz | ~105 + 15 | 3 min 35 s | 4,080 | 180 (into MPSV) |
| jenprace.cz | ~40 + 15 | 1 min 26 s | 1,123 | 2,174 (1,872 into MPSV, 302 into prace.cz) |
| profesia.cz | ~15 + 15 | 57 s | 206 | 70 (into prace.cz and jenprace.cz) |

No refusals, 1.8 s per request on average. jenprace.cz re-lists Úřad práce ads: 98 % merged
into their MPSV job. Salary known for 49–66 % of portal jobs.

## M4 checks (2026-10-06)

- **Indeed** answers an honest User-Agent with a Cloudflare 403: link-out only.
- **LinkedIn** guest pages answer normally: Kolín, 3 min, 29 ads, 15 merged into Úřad práce jobs.

## M5 AI eval (2026-10-06/07)

Test PC: Intel Core Ultra 7 255U, Intel iGPU (shared memory), 32 GB RAM, llama.cpp Vulkan.
Golden set: 70 text ads (`tests/fixtures/eval/text_ads.jsonl`) labelled from Úřad práce data,
spread over the 9 ISCO major groups. Occupation candidates come from similar Úřad práce titles
(e5-small); the model picks one only when the vote is unclear. Answers are grammar-forced JSON,
checked against the ad text.

| Model | Download | RAM (text / +images) | ISCO major / 2-digit / 4-digit | Category | Kraj | Salary | s per ad |
|---|---|---|---|---|---|---|---|
| **Qwen3-VL 2B** (Simple) | 1.6 GB | 2.0 / 3.0 GB | 76 / 73 / 66 % | 89 % | **99 %** | 91 % | **7.9** |
| **Gemma 4 E2B** (Good) | 3.4 GB | 3.6 GB | **80** / 73 / 64 % | 90 % | **99 %** | 84 % | 8.0 |
| **Qwen3-VL 4B** (Excellent) | 3.0 GB | 3.6 GB | 79 / **74** / **69** % | **94 %** | 97 % | 93 % | 14.5 |
| Gemma 4 E4B | 5.2 GB | ~5 GB | 73 / 67 / 60 % | 91 % | 97 % | 93 % | 13.2 |
| Qwen3.5 2B | 1.9 GB | ~2 GB | 73 / 69 / 61 % | 93 % | 97 % | 77 % | 9.3 |
| Qwen3.5 4B | 3.4 GB | ~3.4 GB | 73 / 67 / 60 % | 91 % | 96 % | **94 %** | 17.6 |
| Similar titles only, no model | – | – | 70 / 67 / 60 % | – | 99 % (rules) | – | – |

- 100 % valid JSON for every model.
- Occupation: no model reaches 85 % at 2 digits; most misses are defensible alternatives to
  Úřad práce's label. The model only decides the unclear cases.
- Contract type is read by rules, not the model (models guessed it): found in 24 % of ads,
  88 % agreeing with Úřad práce.
- Models copy numbers from prompt examples, so prompts have none.
- Speed is set by output tokens (~70 per answer, 15–20 t/s on the iGPU); missing fields only:
  6.8 s per ad, listing cards 2.5 s. The iGPU reads prompts 4× faster than the CPU.
- A poster image: ~30 s per ad (23 s transcription + 7 s extraction).
- On real data: 1,354 of 5,423 portal jobs got an occupation from the rules alone.

**Decision:** Simple = Qwen3-VL 2B, Good = Gemma 4 E2B, Excellent = Qwen3-VL 4B.

### Bigger models and prompt styles (2026-10-07)

On the 46 unclear ads the right group is among the candidates 43×, but every model reaches
only 61–63 %: the 2B models mostly take the first candidate, the 4B models decide more often
and make as many corrections as mistakes. Prompt styles (shares, names, English) and a
two-step choice (group first) did not help; two-step was worse for every model and stays off
(`PromptStyle(two_step=True)`).

| Model | ISCO major / 2-digit / 4-digit | Kraj | Salary | Shifts / education agree | s per ad | RAM (est.) |
|---|---|---|---|---|---|---|
| Qwen3-VL 2B (Simple) | 76 / 73 / 66 % | 99 % | 91 % | 67 / 86 % | 7.9 | 2.1 GB |
| Qwen3-VL 8B | 77 / 73 / 64 % | 97 % | 84 % | 89 / 78 % | 24.0 | ~6.3 GB |
| Ministral 3 8B | 73 / 67 / 63 % | 97 % | 87 % | 72 / 80 % | 22.4 | ~6.3 GB |
| Qwen3.5 9B | 79 / 73 / 66 % | 97 % | 91 % | 60 / 71 % | 26.5 | ~6.6 GB |
| Gemma 4 12B | 74 / 70 / 60 % | 97 % | 91 % | 67 / 80 % | 45.1 | ~7.8 GB |
| Ministral 3 14B | 77 / 70 / 61 % | 97 % | 93 % | 65 / 74 % | 36.7 | ~9.2 GB |
| Qwen3 14B (text only) | 76 / 73 / 66 % | 97 % | 96 % | 83 / 78 % | 41.6 | ~9.3 GB |

The task is limited by the inputs (inconsistent reference labels), not by model size; 8–14B
models are 3–6× slower for a few points. Removed from the catalog on 2026-10-09.

### Models below 2B (2026-10-08)

| Model | Occupation group | Kraj | Salary | s per ad (iGPU) | s per ad (CPU, 4 threads) |
|---|---|---|---|---|---|
| **Qwen3-VL 2B** (Simple) | 73 % | 99 % | 91 % | 7.9 | 41.1 |
| Qwen3 1.7B (text) | 69 % | 97 % | 37 % | 10.7 | – |
| Qwen3.5 0.8B (text) | 66 % | 99 % | 27–30 % | 6.0 | **14.5** |
| Gemma 3 1B (text) | 57 % | 99 % | 26 % | 8.9 | – |
| Qwen3 0.6B (text) | 67 % | 99 % | 19 % | 5.4 | 27.4 |
| LFM2.5 1.2B (text) | 67 % | 99 % | 16 % | 10.1 | – |
| LFM2.5-VL 1.6B | 67 % | 99 % | 16 % | 5.4 | 22.3 |

They only repeat what the rules give and fail at salary; no tier below Simple. Removed from
the catalog on 2026-10-09. Instead, a rule reads the pay stated in the ad text ("Mzda 32 000 –
38 000 Kč měsíčně", "180 Kč/hod.", "35 tis.", "32-38k").

### Speed estimates from published measurements

For PCs not tested here, `ai/advisor.py` uses constants per graphics class from published
llama.cpp numbers for 7B Q4 models (Vulkan scoreboard github.com/ggml-org/llama.cpp/discussions/10879,
CUDA /discussions/15013, localscore.ai), scaled for small models. On the test PC the estimate is
within 10 % of the measurement.

| Class (adapter name) | Prompt K (t/s × B) | Bandwidth (GB/s) | Source |
|---|---|---|---|
| AMD Radeon 760M/780M/880M/890M | 1800 | 76 | Vulkan scoreboard, 780M |
| AMD Radeon 610M | 260 | 45 | no Vulkan data: Ryzen 5 7520U CPU (localscore) |
| AMD Radeon Vega ("AMD Radeon(TM) Graphics") | 610 | 42 | Vulkan scoreboard, Ryzen 5000 |
| Intel Arc 130V/140V | 5800 | 93 | Vulkan scoreboard, Lunar Lake |
| Intel Arc (Meteor/Arrow Lake H) | 1250 | 31 | Vulkan scoreboard, one entry |
| Intel Iris Xe | 700 | 30 | Vulkan scoreboard (builds vary 2×) |
| Intel Graphics (Core Ultra 200U) | 1000 | 36 | **measured here** |
| Intel UHD | 300 | 20 | conservative guess |
| Dedicated GPU < 12 GB / ≥ 12 GB | 8000 / 12000 | 170 / 250 | RTX 3050 / RX 6600, RTX 3060 / 4060 Ti |
| CPU only | 45 per core | 35 | Ryzen 5 5600, i5-12400 (localscore), measured here |

## M6 live smoke run (2026-10-07)

Home Kolín, 30 min by car, ≤ 2 listing pages per region and 5 detail pages per portal.

| Portal | How it is read | Result |
|---|---|---|
| jobs.cz | region listings (30/page), `data-test` hooks | 162 cards, details OK; career sites (`<firma>.jobs.cz`) → card only |
| Práce za rohem | region listings with `__NEXT_DATA__` (50/page, GPS, IČO) | 298 cards (178 merged); re-listed `PROF…`/`G2…` ads card only |
| dobraprace.cz | sitemap + JSON-LD, full text from the page | details OK |
| Easy-práce.cz | own-ads sitemap with lastmod | details OK |
| Personálka.cz | region listings, newest first | 180 cards (31 merged), details OK |
| inwork.cz | region listings (the sitemap lags weeks) | 240 cards placed from the URL |
| Fajn brigády | sitemap with lastmod | details OK, two label layouts |
| StartupJobs.cz | offers sitemap + JSON-LD | details OK |
| HitPráce.cz | search around home + JSON-LD | details OK |
| Bazoš – práce | 24 category pages, PSČ filter before details | 151 ads in the region |

No refusals. Personal data removed from all fixtures.

## M6b live checks (2026-10-09/10)

### Classified boards

All 7 sections, ≤ 60 ads per board:

| Board | Ads read | Not offers | Red flags | Placed | Pictures |
|---|---|---|---|---|---|
| HyperInzerce – práce | 60 | 5 (loans) | 3 | 43 | most |
| HyperInzerce – brigády | 60 | 5 (people looking) | 3 | 43 | most |
| Annonce – nabídky práce | 60 (11 joined portal jobs) | 0 | 0 | 60 | few |
| Annonce – brigády | 60 | **46** (mostly "Poptávka") | 0 | 60 | few |
| Bazar.cz | 22 (one page) | 2 | 2 | 16 | all |
| cBazar | 60 | 0 | 2 | 30 | none |
| Mimibazar | 60 | 1 | 11 (MLM) | 60 | all |

Every flag and non-offer was read by hand; denied fees ("žádné vstupní poplatky") no longer
flag. 0 red flags on 45k portal jobs. Not added: avizo.cz (bot challenge), eMimino and Okoun
(0 offers in 68 threads).

### Pictures

40 real board pictures: 8 posters read correctly, 18 logos (now read once per hash), 12 photos
without text (empty), 1 looping banner and 2 prompt echoes (both now cleaned).

| Model (vision) | s per poster, iGPU | s per poster, CPU 4 threads |
|---|---|---|
| Qwen3-VL 2B (Simple) | 14–56, typical ~25 | 35–63, median 42 |
| Gemma 4 E2B (Good) | 9–24, median 16 | – |
| Qwen3-VL 4B (Excellent) | 19–45, median 32 | – |

**Decision:** no RapidOCR; pictures are read only for ads with < 600 characters of own text.

### Complete first run

Copy of the M6 database without jobs, home Kolín, every default source (Úřad práce, 15
portals, 7 boards), pictures and geocoding included, ≤ 300 detail pages per portal.

| | first timed run (10-09) | after the fixes (10-10) |
|---|---|---|
| **Total** | 90 min | **54.5 min** |
| Annonce (20 s crawl delay) | 46 min per section, at the end | 24 + 17.6 min in its own thread from the start |
| Portals | done after ~35 min | done after ~48 min, 4 slots the whole time |
| Failed sources | easy-prace.cz | none |
| jobs.cz | 2,291 new (no Vysočina) | 2,734 new |
| Pictures | 248, none from HyperInzerce, ~10 min | 304, ~6 min |
| Result | 61,535 active jobs | 61,653 active jobs |

Fixes: database writes one at a time in short transactions; sources on one host run in one
group, longest first; sites asking ≥ 5 s between requests run beside the 4 slots; a failed page
is skipped (3 dropped connections in a row: partial run, nothing expired); jobs.cz Vysočina
slug; Annonce read "offers only"; pictures per host in parallel. The slowest portals left are
Personálka and Bazoš (~18 min each).
