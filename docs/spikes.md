# M0 spike results (2026-10-06)

Three spikes checked the plan against the live sources before building on them. Full reports:
[sources.md](spikes/sources.md), [employers.md](spikes/employers.md),
[routing.md](spikes/routing.md). Decisions that came out of them are recorded in PLAN.md §14.

## Job sources: works, with four changes

- **Every planned portal is readable with plain HTTP**; no source needs a browser.
- **MPSV** open data: 39,487 jobs, daily, ETag. Use the 17.5 MB `.json.gz` (served with
  `Content-Encoding: gzip`; keep the bytes compressed). Implemented in M1.
- Changes to the plan:
  - jobs.cz is server-rendered HTML, not JavaScript-only: custom HTML adapter, no browser.
  - volnamista.cz is **dropped**: its robots.txt disallows all generic crawlers.
  - profesia.cz uses microdata, not JSON-LD; startupjobs.cz lists jobs only in its sitemap.
  - jenprace.cz redirects by Geo-IP; dobraprace.cz and jobsik.cz are one operator.
- **Decided:** LinkedIn (robots.txt disallows the guest endpoints) is **off by default** with a
  warning, like Facebook. Indeed uses an **own adapter on the public search page** (allowed by
  robots.txt) instead of python-jobspy's internal API.
- 11 public forum sections verified (`resources/forums.yaml`).
- Fixtures: `tests/fixtures/sources/`. They contain contact data from real ads; anonymize
  before sharing the repository.

## Employer data: works, Indeed reviews blocked

- ARES (detail, RES employee band, insolvency flag), ISIR, unreliable VAT payers (daily bulk
  list), MPSV agency register (JSON) and Atmoskop (`__NEXT_DATA__`, % satisfaction instead of
  stars) all work without keys. Indeed company reviews are behind a Cloudflare challenge:
  link only. ddgs works but needs retries.
- ARES name search is weak (exact words, sorted by IČO), so name → IČO resolution uses a brand
  alias table, name variants, RES-based ranking and the AI for close calls.
- PLAN.md §2.4, §4.5, §5 and M9 are updated.

## Routing: GO for MOTIS, with conditions

| Measure | Whole Czechia | 150 km profile (8 GB machines) |
|---|---|---|
| Import time | 7 min 11 s | ~2 min (osmium extract + renumber, GTFS clip, import) |
| Import peak RAM | 5.5 GB | 1.65 GB (3.7 GB during the osmium extract) |
| Data on disk | 4.0 GB | 1.3 GB |
| Server RAM idle / under load | 0.5 / 1.6 GB | 0.2 / 0.9 GB |
| 5,000 destinations by car | 2.5 s | 1.25 s |
| Transit plan, one-to-all, geocoding, tiles | 20–100 ms | similar |

Conditions:
1. **Timetables must be GTFS with stop coordinates.** CIS JŘ NeTEx loads but is unusable (no
   rail stop coordinates; bus/urban stop IDs collide). The only nationwide feed that worked
   is **Spojenka GTFS, licensed for non-commercial use only**. JDF → GTFS conversion is easy;
   stop coordinates and stop deduplication are the hard part.
2. **8 GB machines** need OSM renumbering with osmium-tool (bundled, GPL-3.0, separate exe);
   clipping alone does not lower import RAM. Geocoding stays off in that profile.
3. Sanitize GTFS before import (in-seat transfers make MOTIS loop); call `127.0.0.1`, send
   `Accept-Encoding: gzip` for tiles, raise `onetomany_max_many`.
4. No isochrone polygon endpoint: the app builds the reachable area from one-to-all results.

Plan B (Mapy.com API + IDOS links) is kept as a fallback; it gives no public-transport times.

## Built during M0/M1 that changes the plan

- **Job coordinates come from RÚIAN** (ČÚZK address points, monthly 63 MB ZIP): exact
  positions for MPSV jobs with an address code, municipality or district centres otherwise.
  96.7 % of MPSV jobs get coordinates. MOTIS geocoding is only needed for free-text addresses.
- Categories are the 16 official Úřad práce fields, derived from CZ-ISCO.

## M3 live run (2026-10-06)

Home Kolín, 30 min by car → crawl scope Středočeský, Pardubický and Královéhradecký kraj.
At most 15 detail pages per portal (the default is 300), on a copy of the real database
(39,487 MPSV jobs).

| Source | Listing pages + details | Time | New jobs | Merged into existing |
|---|---|---|---|---|
| prace.cz | ~105 + 15 | 3 min 35 s | 4,080 | 180 (into MPSV) |
| jenprace.cz | ~40 + 15 | 1 min 26 s | 1,123 | 2,174 (1,872 into MPSV, 302 into prace.cz) |
| profesia.cz | ~15 + 15 | 57 s | 206 | 70 (into prace.cz and jenprace.cz) |

- No source refused us. The polite pace averaged 1.8 s per request.
- **jenprace.cz re-lists Úřad práce ads** (IDs starting with `u`): 1,830 of 1,867 merged into
  their MPSV job (98 %). The first run found 87 %; the misses were jenprace's
  "<village>, <district>" place format and MPSV jobs placed only by district, both fixed.
- 2,370 jobs now have more than one source, 54 have three.
- Portal places: 66 % municipality, 21 % city part, 4 % region, 8 % none. The 8 % are
  "Celá ČR", remote work or abroad; "Kolín IV"-style town parts and "Okres X" were fixed
  after the run.
- Salary is known for 66 % of jenprace, 49 % of prace.cz and 62 % of profesia jobs (from
  listing cards; detail pages add some).

## M4 checks (2026-10-06)

- **Indeed** `cz.indeed.com/jobs` answers the app's honest User-Agent with HTTP 403 "Security
  Check" (Cloudflare). The M0 spike got through only with a browser-like User-Agent.
  Decision: link-out only ("Hledat také na Indeed" opens the search in the user's browser).
- **LinkedIn** guest search and job pages answer the honest User-Agent normally. A live run
  for Kolín (25 miles, newest first, opted in) read 3 search pages and 29 detail pages in
  3 min, without refusals: 14 new jobs, 15 merged into Úřad práce jobs (LinkedIn re-lists
  them too). Fixtures: `tests/fixtures/sources/linkedin/guest_{search,detail}.html`.

## M5 AI eval (2026-10-06/07)

**Test PC:** Intel Core Ultra 7 255U (12 cores), Intel Graphics (4 Xe cores, shared memory),
32 GB RAM, llama.cpp b11449 (Vulkan build). The 8 GB budget Ryzen and a PC with a dedicated
GPU were not available; their icons come from the estimate formula, calibrated on this PC,
and are corrected by the 20-second benchmark after the download.

**Golden set:** 70 text ads (`tests/fixtures/eval/text_ads.jsonl`, `tools/make_golden.py`):
40 portal ads (prace.cz, jenprace.cz) merged with an Úřad práce ad, plus 30 long Úřad práce
ads, spread over the 9 ISCO major groups (harder than the real mix, where warehouse and
production jobs dominate). Labels come from Úřad práce's structured data, the salary label
from the salary shown in the text. E-mails and phone numbers are removed.

**Pipeline:** occupation candidates from similar Úřad práce titles (multilingual-e5-small
embeddings + shared words, 39k labelled titles); when the vote is clear the code is taken
directly, otherwise the model picks one of ≤ 8 candidates. One call per ad, the answer is
compact JSON forced by a grammar, every value is checked against the ad text (salary
numbers, town, shift and education words must appear).

### Results (final prompt, `adawork eval`)

| Model | Download | RAM (text / +images) | ISCO major / 2-digit / 4-digit | Category | Kraj | Salary | s per ad |
|---|---|---|---|---|---|---|---|
| **Qwen3-VL 2B** (Simple) | 1.6 GB | 2.0 / 3.0 GB | 76 / 73 / 66 % | 89 % | **99 %** | 91 % | **7.9** |
| **Gemma 4 E2B** (Good) | 3.4 GB | 3.6 GB | **80** / 73 / 64 % | 90 % | **99 %** | 84 % | 8.0 |
| **Qwen3-VL 4B** (Excellent) | 3.0 GB | 3.6 GB | 79 / **74** / **69** % | **94 %** | 97 % | 93 % | 14.5 |
| Gemma 4 E4B | 5.2 GB | ~5 GB | 73 / 67 / 60 % | 91 % | 97 % | 93 % | 13.2 |
| Qwen3.5 2B | 1.9 GB | ~2 GB | 73 / 69 / 61 % | 93 % | 97 % | 77 % | 9.3 |
| Qwen3.5 4B | 3.4 GB | ~3.4 GB | 73 / 67 / 60 % | 91 % | 96 % | **94 %** | 17.6 |
| Similar titles only, no model | – | – | 70 / 67 / 60 % | – | 99 % (rules) | – | – |

Re-run on 2026-10-09 with the reviewed code and each model's best prompt style; seconds per ad
are from the first run (the re-run was 10–40 % slower on a busier PC, the code adds no work).

- **100 % valid JSON** for every model (the grammar allows nothing else).
- **Occupation:** no model reaches the planned ≥ 85 % at the 2-digit level. A manual review
  of the 23 misses of the title vote found ~6 clearly wrong ("Fakturant/ka – vyhrazeno pro
  OZP" → cleaners); the rest are defensible alternatives to Úřad práce's label ("Kalkulant/ka"
  → "Pracovníci kalkulací" instead of "Odborní pracovníci v účetnictví"). The categories the
  filter uses overlap with the label for 89–94 % of ads. The model adds only a few points over
  the title vote: it decides only the unclear cases.
- **Contract type is no longer asked from the model.** The Qwen3.5 models named a contract
  type in 32–43 of 70 ads that state none ("praxe" → internship); Qwen3-VL and Gemma were
  cautious but found only ~22 % of the stated ones. Ads that state it use fixed phrases, so
  improved rules read them ("HPP", "hlavní pracovní poměr", "plný i zkrácený úvazek", "DPP"):
  found in 24 % of the golden ads, agreeing with Úřad práce in 88 %. The other 76 % of the
  ads do not state it; their Úřad práce label cannot be read from the text at all.
- **The model copies prompt examples:** with `"35 tis." = 35000` in the instructions, Qwen3.5
  2B answered 35000 in a third of the ads. No concrete numbers in prompts now.
- **Bigger is not better here:** the 4B models were not clearly more accurate than the 2B
  ones (differences of at most 3 points = 2 ads), at twice the time.
- **Speed:** output tokens dominate (~15–20 tokens/s on the iGPU, ~70 per answer). Compact
  JSON (no whitespace, own grammar) saved ~20 % of them; asking only for missing fields
  brings real portal ads to 6.8 s (Qwen3-VL 2B), listing cards (occupation only) to 2.5 s.
- **llama-bench, Qwen3-VL 2B Q4_K_M:** Vulkan iGPU prompt 345 tokens/s, output 21 tokens/s;
  CPU only (8 threads) 89 / 20 tokens/s. The iGPU makes prompt reading 4× faster.
- **Images:** a generated poster ("HLEDÁME ŘIDIČE SK. C+E … Mzda 42 000 – 48 000 Kč")
  was transcribed almost exactly by Qwen3-VL 2B in 23 s (the image is ~970 tokens), then
  extracted in 7 s; ~30 s per image ad. Without the text checks the model added "vyučení"
  (apprenticeship), which the poster does not mention: education and shifts are now kept only
  when the ad names them.
- **Memory (measured, llama-server private bytes):** Qwen3-VL 2B 2.1 GB, with the image part
  3.0 GB; Gemma 4 E2B 3.6 GB; Qwen3-VL 4B 3.6 GB. The estimates are within 0.2 GB.

### Real data (copy of the M3 live database, Qwen3-VL 2B)

- Rules: 1,354 of 5,423 portal jobs without an occupation got one from a clear title vote
  in 42 s (the live database holds mostly listing cards).
- Model: 40 full ads in 271 s (6.8 s each) filled occupation 28×, education 11×, shifts 4×,
  salary 2×; 30 listing cards got an occupation in 76 s (2.5 s each), mostly sensible
  ("Řidič sk. C/skladník" → 83321 truck drivers), some not ("pracuj z pohodlí domova" for a
  call line → cleaners).

### Decision

Tiers: **Simple = Qwen3-VL 2B**, **Good = Gemma 4 E2B**, **Excellent = Qwen3-VL 4B** (all
multimodal, Apache 2.0). Contract types come from the rules. On an Intel iGPU laptop Auto
picks Good with ≥ 16 GB RAM and Simple with 8 GB; the 4B tier is yellow (slower than 15 s)
without a dedicated GPU.

## M5 follow-up (2026-10-07): why bigger models did not win, estimates from published data

### Why the bigger models scored lower

The model decides the occupation only when the title vote is unclear (46 of the 70 golden
ads). On those 46: the title vote's first candidate is right (2-digit group) 24×; the right
group is among the ≤ 8 candidates 43×, so a perfect chooser could reach 93 %.

| Model (code answers) | Right group | Picked the first candidate |
|---|---|---|
| Qwen3.5 2B | 23/46 | 35× |
| Qwen3-VL 2B | 27/46 | 30× |
| Gemma 4 E2B | 28/46 | 21× |
| Qwen3-VL 4B | 28/46 | 26× |
| Qwen3.5 4B | 21/46 | 11× |
| Gemma 4 E4B | 21/46 | 13× |

The 2B models mostly take the first candidate (the one similar Úřad práce ads used most),
so they inherit the title vote's agreement with Úřad práce's labelling habits. The 4B models
decide for themselves: some picks are real corrections ("Fakturant/ka – vyhrazeno pro OZP"
→ Fakturanti instead of cleaners, "Vedoucí expediční dílny" → logistics managers instead of
forklift drivers), others are real mistakes ("Zahradník" → street cleaners, "Kalkulant/ka" →
construction technicians), and many swap between "Specialisté" (2xxx) and "Odborní
pracovníci" (3xxx), which Úřad práce itself uses inconsistently. Bare 4-digit codes give a
model nothing to hold on to, which is why the prompt styles below were tried.

### Prompt styles (occupation only, the 46 unclear ads)

`adawork eval --fields profese --only-unclear --style …`; *code* = answer with the code,
*shares* = each candidate shows how often similar Úřad práce ads used it ("když si nejsi
jistý, vyber tu nejčastější"), *name* = answer with the occupation's name, *en* = English
instructions (ad and answer values stay Czech).

| Model | code | code+shares | name+shares |
|---|---|---|---|
| Qwen3-VL 2B | 59 % | 50 % | **61 %** |
| Gemma 4 E2B | **61 %** | **61 %** | 59 % |
| Qwen3-VL 4B | 61 % | **63 %** | 61 % |
| Gemma 4 E4B | 46 % | **52 %** | **52 %** |

Differences of 1–2 ads are within noise; Gemma 4 E4B gains most from extra guidance.

### Speed estimates from published measurements

For PCs the project cannot test, the advisor now uses constants per graphics class derived
from published llama.cpp numbers for 7B Q4 models (Vulkan scoreboard
github.com/ggml-org/llama.cpp/discussions/10879, CUDA scoreboard /discussions/15013,
localscore.ai), scaled down for small models (fixed costs per token: ~0.6 at 2B, 1.0 from 7B):

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

Resulting estimates (seconds per ad, icon, RAM): see the table in PLAN §6.6. On this PC the
estimate is within 10 % of the measurement (7.7 / 8.7 / 13.9 s vs 7.9 / 8.0 / 14.5 s).
Not found anywhere: Vulkan numbers for the Radeon 610M and for the exact 5500U.

### 8–14B models (full eval, best prompt style per model)

Occupation-only test (46 unclear ads): every model reached 61–63 % at best with
"code+shares"; English instructions made every 8–14B model worse (48–61 %). Full eval:

| Model | ISCO major / 2-digit / 4-digit | Kraj | Salary | Shifts / education agree | s per ad | RAM (est.) |
|---|---|---|---|---|---|---|
| Qwen3-VL 2B (Simple) | 76 / 73 / 66 % | 99 % | 91 % | 67 / 86 % | 7.9 | 2.1 GB |
| Gemma 4 E2B (Good) | 80 / 73 / 64 % | 99 % | 84 % | 59 / 76 % | 8.0 | 3.6 GB |
| Qwen3-VL 4B (Excellent) | 79 / 74 / 69 % | 97 % | 93 % | 79 / 84 % | 14.5 | 3.6 GB |
| Qwen3-VL 8B | 77 / 73 / 64 % | 97 % | 84 % | 89 / 78 % | 24.0 | ~6.3 GB |
| Ministral 3 8B | 73 / 67 / 63 % | 97 % | 87 % | 72 / 80 % | 22.4 | ~6.3 GB |
| Qwen3.5 9B | 79 / 73 / 66 % | 97 % | 91 % | 60 / 71 % | 26.5 | ~6.6 GB |
| Gemma 4 12B | 74 / 70 / 60 % | 97 % | 91 % | 67 / 80 % | 45.1 | ~7.8 GB |
| Ministral 3 14B | 77 / 70 / 61 % | 97 % | 93 % | 65 / 74 % | 36.7 | ~9.2 GB |
| Qwen3 14B (text only) | 76 / 73 / 66 % | 97 % | 96 % | 83 / 78 % | 41.6 | ~9.3 GB |

All: 100 % valid JSON, categories 89–94 %. Re-run on 2026-10-09 with the reviewed code: the
8–14B rows came out identical, the tier rows now use their best style too; seconds per ad are
from the first run.

**Conclusion:** the task is limited by the inputs, not by model size. The occupation
plateaus at ~73 % (2-digit) for every model from 2B to 14B because the reference labels are
inconsistent between "Specialisté" and "Odborní pracovníci" and the candidates come from the
same title vote; place and salary are near their ceiling already at 2B. Bigger models are
3–6× slower for at most a few points on salary and shifts. Tiers stay Qwen3-VL 2B / Gemma 4
E2B / Qwen3-VL 4B; the 8–14B models remain selectable under "Pokročilé". To gain more,
the next step is better candidates and labels (e.g. a 2-level choice: major group first),
not a bigger model.
