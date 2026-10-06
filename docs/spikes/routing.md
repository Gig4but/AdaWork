# M0 spike: routing (MOTIS on Windows)

Date: 2026-10-06. Tested on Windows 11, 32 GB RAM, 14 hardware threads, NVMe SSD.
Scripts and configs are in `spikes/routing/`. Downloads and imported data are in
`spikes/routing/data/` (git-ignored). Example API responses are in `spikes/routing/samples/`.
Home for all queries: Brno, náměstí Svobody (49.1951, 16.6068). Departure: Thursday
2026-10-08 07:00 local time (`2026-10-08T05:00:00Z`).

## Verdict: **GO for MOTIS, with conditions**

MOTIS v2.11.3 runs on Windows out of the box. It covers car, walk, bike and public transport,
one-to-many, one-to-all, geocoding and vector tiles. It is fast enough for every M8 target:
5,000 job locations by car take 1.3–2.5 s, and every other query takes under 0.3 s.

Conditions:

1. **Timetables must come as GTFS with stop coordinates.** The CIS JŘ NeTEx files load, but
   they cannot be used for routing as published (see "Timetable data"). The only
   nationwide feed that worked is **Spojenka GTFS**. Its license is **non-commercial only**,
   so it fits a personal or free app. A commercial product would need its own feed:
   PID + IDS JMK + other regional GTFS, plus CIS JŘ data with stop coordinates from
   another source.
2. **On 8 GB machines, prepare the data before the import:**
   - clip OSM to 150 km and renumber it with `osmium-tool` (bundled, 9 MB);
   - clip the GTFS with our script;
   - turn off MOTIS geocoding (RÚIAN covers job addresses);
   - use the `background` tile profile.

   With these steps, the import peaks at a **1.65 GB working set** (whole CZ: 5.5 GB). The
   data folder is 1.3 GB (whole CZ: 4.1 GB), and the import takes **1 minute**.
3. **Sanitize the GTFS before the import** (`clip_gtfs.py` does this). With in-seat transfers
   left in, MOTIS gets stuck in a logging loop (see "Failures").
4. **Call `127.0.0.1`, not `localhost`.** With `localhost`, each request takes about 2 s longer,
   probably because Windows tries IPv6 first. Tile requests must send
   `Accept-Encoding: gzip`.

**Plan B** (if a condition cannot be met): the Mapy.com API covers car, foot and bike routes
and matrices (≤ 100 pairs per call, 250k free credits per month). Public transport would get
only IDOS deep links, with no travel times, no transit filter and no isochrones. Plan B is
clearly worse, because public transport is the main gap it leaves open.

## Versions and URLs used

| Item | Source | Size | Download time |
|---|---|---|---|
| MOTIS **v2.11.3** (latest, 2026-09-10), asset `motis-windows.zip` (name verified) | https://github.com/motis-project/motis/releases/download/v2.11.3/motis-windows.zip | 51.7 MB (motis.exe 23 MB + MSVC runtime DLLs, `ui/`, `tiles-profiles/`) | 7 s |
| OSM Czech Republic (Geofabrik) | https://download.geofabrik.de/europe/czech-republic-latest.osm.pbf | 948 MB | 172 s |
| CIS JŘ NeTEx rail, annual | ftp://ftp.cisjr.cz/netex/NeTEx_GVD2026.zip (+ monthly `_updates_YYYY-MM.zip`) | 63.8 MB (13,252 XML files, 1.3 GB unzipped) | 23 s |
| CIS JŘ NeTEx urban | ftp://ftp.cisjr.cz/netex/NeTEx_DrahyMestske.zip | 55.5 MB (802 files) | 23 s |
| CIS JŘ NeTEx **buses** (new, not in PLAN.md) | ftp://ftp.cisjr.cz/netex/NeTEx_VerejnaLinkovaDoprava.zip | 197.9 MB (13,105 files, 5.9 GB unzipped) | 40 s |
| CIS JŘ JDF buses | ftp://ftp.cisjr.cz/JDF/JDF.zip | 105.5 MB (13,105 inner zips) | 27 s |
| PID GTFS (Prague + Central Bohemia) | https://data.pid.cz/PID_GTFS.zip | 50.0 MB, valid 2026-10-06 – 10-19 | 13 s |
| IDS JMK GTFS (South Moravia, Brno) | https://kordis-jmk.cz/gtfs/gtfs.zip | 8.5 MB, valid to 2026-12-11 | 2 s |
| **Spojenka national GTFS** (CIS JŘ + PID + SŽ + regional open data, merged, with stop coordinates) | https://www.spojenka.cz/jrdata/jizdnirady-gtfs.zip → https://spojenka.d3s.mff.cuni.cz/api/data/timetable/gtfs | 84.6 MB (413 MB unzipped), 93,410 stops, 370k trips, valid 14 days, rebuilt daily at about 04:30 | 11 s |
| osmium-tool 1.19.1 (win-64, conda-forge) | fetched by `fetch_osmium_tool.py` (no conda needed) | 3.4 MB download, 9.1 MB unpacked (exe + boost, zlib, bz2, lz4, expat DLLs) | 3 s |
| pyosmium 4.x (`osmium` wheel from PyPI) | `uv pip install osmium` | wheel | – |

## Measurements

The MOTIS import was sampled every 0.5–2 s with `run_measured.ps1`, which records the working
set, private bytes and the OS `PeakWorkingSet64`. "WS" is the working set (physical RAM in use).
"Private" is committed memory. The `osr` step commits about 5 GB even when it touches much less
(see "Clipping").

### Import

| Scenario | Inputs | Wall time | Peak WS | Peak private | Data folder |
|---|---|---|---|---|---|
| Whole CZ, all features (`config_full_cz.yml`) | OSM CZ + NeTEx rail + PID + JMK | **7 min 11 s** (tt 35 s, osr 1:33, adr 2:19, tiles 2:25, run in parallel) | **5.55 GB** | 4.9 GB | **4.0 GB** (tiles 2.7 GB, osr 611 MB, adr 401 MB, tt 108 MB + tt_ext 117 MB) |
| Whole CZ, timetable swapped to Spojenka (`config_full_cz_spojenka.yml`) | same OSM, Spojenka (sanitized) | 22 s (only tt, matches, osr_footpath and adr_extend re-ran; OSM tasks were reused) | 0.95 GB | 0.95 GB | 4.1 GB |
| 150 km clip, all features (`config_clip150.yml`) | osmium `complete_ways` extract + clipped Spojenka | 79 s | 4.76 GB | 4.9 GB | 2.2 GB (tiles 1.5 GB, osr 299 MB, adr 209 MB) |
| ↳ the same, per task (`--filter`) | | osr 14 s / tt 5 s / adr 20 s / tiles 50 s | osr **4.68**, tt 0.52, adr 0.83, tiles 3.08 GB | | |
| 50 km clip, osr only | 95 MB extract | 5 s | **4.0 GB** (the osr peak does not shrink with area) | 4.9 GB | |
| 50 km clip, osr only, **renumbered** OSM | 89 MB | 4 s | **0.60 GB** | 5.0 GB | |
| **150 km, 8 GB profile** (`config_clip150_8gb.yml`) | renumbered OSM, geocoding off, `background.lua` tiles | **59 s** | **1.65 GB** | 5.0 GB | **1.28 GB** |
| Tiles alone, 150 km, `full.lua` vs `background.lua` | | 50 s vs 29 s | 3.08 vs 1.66 GB | | 1.5 GB vs 0.76 GB |

Extrapolation to the budget Ryzen (4–6 cores, 8 GB): CPU-bound steps should take about 2–3×
longer, so the 8 GB-profile import should take **2–4 min**. The peak WS should stay at about
1.7 GB, because it comes from tiles and the osr step no longer drives it. The 5 GB commit needs
a page file. A Windows-managed page file on an 8 GB machine provides this. Check this on the
real test machine in M8.

### Running server

| Data | Startup | Idle WS / private | After the query suite | After 5,000-destination queries |
|---|---|---|---|---|
| Whole CZ (NeTEx + PID + JMK) | 2.7 s (cold cache) | 505 MB / 1.03 GB | 1.16 GB / 1.98 GB | – |
| Whole CZ (Spojenka) | 0.6 s | 570 MB / 1.10 GB | – | **1.60 GB / 2.21 GB** |
| 150 km, 8 GB profile | < 1 s | **215 MB / 789 MB** | – | **874 MB / 1.45 GB** |

These numbers are within PLAN §3.2.2's estimate of "0.5–1.5 GB". On 8 GB, keep the server
on-demand and stop it when idle, as planned.

### Query latency (warm median; first call in brackets)

Measured with `queries.py`, `car_scaling.py` and `scale_5000.py` through `127.0.0.1`.

| Query | Whole CZ (Spojenka) | 150 km clip | Result |
|---|---|---|---|
| one-to-many CAR, 50 dest., max 7200 s | 1952 ms | 987 ms | 48/50 found, median 21.5 min |
| one-to-many CAR, 50 dest., max 1800 s / 3600 s | 156 / 529 ms | – | latency grows steeply with `max` |
| one-to-many CAR, 128 dest., max 3600 s (GET / POST) | 545 / 538 ms | – | |
| one-to-many CAR, **5,000 dest.** (POST), max 7200 s | **2.5 s** | **1.25 s** | 4,448 found |
| one-to-many WALK, 50 / 5,000 dest. | 59 ms / 0.30 s | 49 ms / 0.20 s | |
| one-to-many BIKE, 50 / 5,000 dest. | 81 ms / 0.27 s | 55 ms / 0.19 s | |
| one-to-many-intermodal (transit + walk), 50 / **5,000** dest., 120 min | 52 ms / **1.6 s** | 51 ms / 1.1 s | 22/50 reachable, median 35 min |
| plan (transit), 5 destinations | 54–105 ms | 45–66 ms | see below |
| plan, direct CAR only | 304 ms | 210 ms | Brno → Blansko 25 min |
| one-to-all transit, 30 / 60 / 90 min | 21 / 28 / 49 ms | 22 / 33 / 28 ms | 573 / 1,465 / 2,243 stops |
| geocode / reverse-geocode | 26 / 25 ms | off | |
| vector tile z10 / z14 | 29 / 104 ms | 18 / 48 ms | 353 / 474 KB (full profile); 51 / 154 KB (background) |

The 5,000-location recompute target (< 30 s) is met by a wide margin: all four modes together
take about 5 s for the whole CZ.

Transit plans from home at 07:00 on a Thursday (Spojenka data):

| To | Best | Legs |
|---|---|---|
| Brno-Bohunice, campus | 28 min | walk, tram 6, walk, bus 25, walk |
| Blansko | 54 min | walk, S2 train, walk |
| Vyškov | 64 min | tram 9, bus 33, bus 107 |
| Hodonín | 129 min (with NeTEx + JMK: 90 min, via R13 train + bus 901) | trams, bus 109, bus 911 |
| Praha, Václavské nám. | 182 min (Ex3 train) | none with NeTEx data (rail stops have no coordinates); none in the 150 km clip (outside the area) |

The Hodonín result differs between the feeds, and Spojenka's is 39 min slower. This is worth
including in the M8 check "10 routes vs IDOS within ±15 %".

## API endpoints to use (MOTIS v2.11.3, OpenAPI in the repo's `openapi.yaml`)

| Need | Endpoint | Notes | Sample |
|---|---|---|---|
| Car/walk/bike times to many jobs | `GET or POST /api/v1/one-to-many` with `one=lat;lon`, `many=[lat;lon,…]`, `mode=CAR\|WALK\|BIKE`, `max` (s), `maxMatchingDistance`, `arriveBy` | Response: array of `{duration?}` in the same order as `many`; an empty object means not reachable. Allowed size is set by `limits.onetomany_max_many` (default 128; set to 10000 in our configs). Use POST for large batches. | `one_to_many_car.json` |
| Public transport times to many jobs | `GET or POST /api/experimental/one-to-many-intermodal` with `one`, `many`, `time`, `maxTravelTime` (min) | Response: `{street_durations:[{duration?}], transit_durations:[[{duration, transfers}]]}`; take the minimum of the two. **POST takes `lat,lon`, while one-to-many POST takes `lat;lon`.** A wrong format returns `unknown feed id ""` or `key mode not found`. It is marked experimental. | `one_to_many_intermodal.json` |
| Route detail (commute window, MHD legs) | `GET /api/v6/plan?fromPlace=lat,lon&toPlace=lat,lon&time=…&numItineraries=3` (+ `directModes=CAR&transitModes=` for car only) | `itineraries[].legs[]` with mode, `routeShortName`, stops, times and `legGeometry` (polyline); `direct[]` for street-only routes; cursors for earlier and later connections | `plan_transit.json`, `plan_car_direct.json` |
| Reachable area (isochrone) | `GET /api/v6/one-to-all?one=lat,lon&time=…&maxTravelTime=…` | Returns reachable **stops** with `duration` (minutes). There is **no polygon endpoint**. Build the isochrone in the app: a buffer of the remaining walk around each stop, or a one-to-many grid for street modes. Max time is set by `limits.onetoall_max_travel_minutes` (default 90). | `one_to_all.json` |
| Geocoding | `GET /api/v1/geocode?text=…&language=cs`, `GET /api/v1/reverse-geocode?place=lat,lon` | Needs `geocoding: true` (+ `reverse_geocoding`). | `geocode.json`, `reverse_geocode.json` |
| Map tiles | `GET /tiles/{z}/{x}/{y}.mvt` (MapLibre vector source), `GET /glyphs/{fontstack}/{range}.pbf`; style as in MOTIS `ui/` | Returns **501 unless the request has `Accept-Encoding: gzip`** (MapLibre sends it). The body is gzip-encoded MVT. | – |
| Health and config | `GET /api/v1/health`, `GET /api/v1/map/initial` | `map/initial` shows `maxOneToManySize`, `hasStreetRouting` and other limits | – |

### Tiles and geocoding in this build

- **Tiles:** both work, and both are built by `motis import` from the OSM extract when
  `tiles.profile` is set. `full.lua` gives the detailed map: 2.7 GB for the whole CZ, or
  1.5 GB for 150 km. `background.lua` gives a lighter map: 0.76 GB for 150 km. The bundled UI
  at `http://127.0.0.1:8080/` shows them. Glyphs (fonts) are served by MOTIS too, so the
  map works fully offline.
- **Geocoding:** the address index is built from OSM (`adr`, 401 MB for the whole CZ).
  Czech addresses work well, at 6–25 ms per query (`geocode_check.py`). 9 of 12 typical
  job-ad strings resolved to the right address point, with full conscription/house numbers
  such as "Kounicova 966/67a". The misses were:
  - "Pražská 1, Praha" → a shop named "Pražská Čokoláda";
  - "Lidická 1880/4, Praha 2" → Lidická 842/4 in Praha 5;
  - "Průmyslová 1, Modřice" → a stop in České Budějovice.

  So it is fine for autocomplete. RÚIAN should stay the source for job coordinates. Note
  also that geocoding **cannot be combined with a renumbered OSM file** (see "Failures"), so
  the 8 GB profile turns it off.

## Timetable data: what worked and what failed

| Feed | Loads in MOTIS? | Usable? | Details |
|---|---|---|---|
| NeTEx rail `NeTEx_GVD2026.zip` | **Yes**: nigiri has a NeTEx loader that reads a zip of XML files; 13,252 trips and 3,386 stops in 7 s | **No** | The `StopPlace` elements have **no coordinates**: `geocode?text=Liberec` returns `rail_CZ:CISJR_CZPTT:StopPlace:CZ54212 Liberec 0.0 0.0`. Routing from coordinates never reaches these stops: Brno → Praha found 0 itineraries. Updates come as replacement files per train (monthly `_updates_` zips), which MOTIS cannot merge with the base. |
| NeTEx urban `NeTEx_DrahyMestske.zip` (and buses `NeTEx_VerejnaLinkovaDoprava.zip`, same JDF-derived format) | Imports without error | **No** | IDs are local to each file (`CZ:cisjr_jdf:StopPlace:1`, `:2`, …), so stops from different lines merge. 230,671 trips collapsed into **56 locations**. There are no coordinates either. |
| PID GTFS + IDS JMK GTFS | Yes (35 s together) | Yes | 10,925 + 19,137 stops; trams, buses and S-trains in Brno and Prague route correctly. Each covers one region only. |
| **Spojenka GTFS** | Yes, after sanitizing (6 s parse) | **Yes**, nationwide | 92,374 locations, 370,019 trips, with coordinates. **License: "any non-commercial purposes", without warranty.** The feed covers 14 days and is rebuilt daily. |
| tangero aggregated GTFS (CC BY 4.0) | not tried | unclear | Last push 2026-02-21, updated by hand. Its own README warns about stops at 0.0, 0.0. It is the only open-licensed nationwide option seen, so it is worth a check if Spojenka's license is a blocker. |

### Failures and exact errors

1. **Spojenka GTFS makes the import hang.** `tt.txt` repeats this in an endless loop:
   ```
   [info][loader.gtfs.write_seated] starting loader.gtfs.write_seated
   ERROR! NOT SORTED: transport_offset=0, offset=0
   trips: (29312, 695) (29313, 693) …   stops: ("Mělník, autobusové nádraží", "S286.P7.Z4") …
   ```
   The log grew to **19 GB in 12 minutes** and the progress bar stayed at "83% | spoj: Stay
   Seated". Blanking `block_id` alone did not help. Also dropping the 4,933 `transfers.txt`
   rows with `transfer_type=4` (in-seat) fixed it. `clip_gtfs.py` does both, and with a
   radius of 5000 it only sanitizes the feed without clipping it. Upstream bug candidate
   for nigiri.
2. **osmium `extract -s simple`** output fails the MOTIS import after about 10 s with
   `unable to import: invalid location` (exit code 1). Use `-s complete_ways`.
3. **Renumbered OSM + geocoding:** the `adr` step ran at 100 % CPU on 11 cores for over 10 min
   at "adr: Load OSM / Pass 2" and was killed. Normally it takes 12 s. This is why the 8 GB
   profile sets `geocoding: false`.
4. Tile requests without `Accept-Encoding: gzip` → `HTTP 501` with an empty body.
5. Through `http://localhost:8080`, every request took about 2.05 s longer than through
   `127.0.0.1`. This is consistent with Windows trying IPv6 `::1` first, though that cause
   was not confirmed.
6. one-to-many with more than 128 destinations → `422 {"error":"number of many locations too
   high (1000 > 128). The server admin can change this limit in config.yml with
   'onetomany_max_many'…"}`.
7. The PID feed, the 150 km clip and Spojenka (14 days) are short-validity feeds. Set
   `timetable.first_day` and `num_days`, and refresh weekly, as PLAN §4.6 already says.

## Clipping (for 8 GB machines)

| Approach | Time | Peak RAM | Output (150 km around Brno) | Verdict |
|---|---|---|---|---|
| **osmium-tool 1.19.1** `extract -p circle.geojson -s complete_ways` | **16 s** | 3.7 GB | 481 MB (from 948 MB) | **Use this**. 9 MB of files, fetched from conda-forge by `fetch_osmium_tool.py`. License is GPL-3.0; shipping it as a separate exe that we call is fine, but note it in the installer. |
| osmium `extract -s simple` | 23 s | 2.0 GB | 479 MB | Breaks the MOTIS import (see above) |
| osmium `renumber` (after extract) | 22 s | 0.66 GB | 457 MB | **Required for low import RAM:** osr peak WS 4.0 → 0.6 GB |
| pyosmium script `clip_osm.py` (via uv) | **1,454 s (24 min)** | ~3.9 GB | 486 MB | Works, but is 90× slower than osmium-tool. Not worth it. |
| GTFS: `clip_gtfs.py` (pure Python) | 30 s | < 0.5 GB | Spojenka 85 → 36 MB; 194k trips and 47k stops kept (complete trips that touch the circle) | Use it, together with the sanitizing fix |
| MOTIS built-in options | – | – | – | MOTIS has no clip or bounding-box option. Only `timetable.first_day` / `num_days` limit the timetable in time. |

Main finding: the area alone does **not** lower MOTIS's peak import RAM. The `osr` step
touches about 4–4.7 GB whether the input is 95 MB or 948 MB, probably because its arrays are
sized by the largest OSM ID (about 1.3·10¹⁰). Renumbering the IDs removes this, and the tile
profile then sets the peak. The 8 GB pipeline is:

1. `osmium extract` (16 s)
2. `osmium renumber` (22 s)
3. `clip_gtfs.py` (30 s)
4. `motis import` (59 s)

This takes about 2 min here, peaks at 3.7 GB during the osmium extract and 1.65 GB during the
import, and leaves 1.3 GB of data. Changing the home address triggers the same pipeline again.
If a lower extract peak is needed, extracting a bounding box with `-s simple` and then
`complete_ways` on the smaller file is an option to try in M8.

## JDF → GTFS

| Project | Language | License | Maintenance | Notes |
|---|---|---|---|---|
| **dvdkon/jrutil** (GitLab mirror on GitHub) | F# / .NET | **AGPL-3.0** | active (last push 2026-09-08) | The most complete tool: JDF 1.09–1.11 and CZPTT → GTFS, plus a stop matcher for OSM and external coordinates. It needs the .NET SDK (not installed here, not tried). The AGPL makes bundling it hard. |
| **0Charliecat/jdf2gtfs** (npm `jdf2gtfs` 2.1.1) | TypeScript, isomorphic | custom, **non-commercial only** | 2026-05 | **Tried** on 2 batches from `JDF.zip` (`10000.zip`, `1000.zip`): both converted in 18 and 62 ms to valid GTFS. But all stops come out as `stop_lat,stop_lon = 0,0`, and stop IDs are local to each batch (`1`, `2`, …). |
| masopust/jdf2gtfs | Python + PostgreSQL/PostGIS | MIT | 2022, pinned SQLAlchemy 1.3.13 | Needs a PostgreSQL server. Too heavy to bundle. |
| xtompok/JDF2GTFS | Python + PostgreSQL | MIT | 2018 | Linux-only, needs ≥ 8 GB RAM. |
| Hnatekmar/jdf2gtfs | Scala | MIT | 2017 | abandoned |

**Finding:** converting the format is the easy part, and our own about 300-line converter
would be fine. The hard parts are:
- **JDF has no stop coordinates**;
- each batch has its own stop numbering, so stops must be deduplicated by name across
  13,105 batches.

The coordinates have to come from a stop register, OSM or geocoding. Spojenka has already
done all of this nationwide, so it is the pragmatic choice where its license allows it. The
new **NeTEx bus export** (`NeTEx_VerejnaLinkovaDoprava.zip`) has the same two problems, so it
does not replace JDF either.

## Files

- `run_measured.ps1`: runs a process and samples WS, private bytes and peak WS to CSV.
  For `uv`/venv Python, the launcher process is measured, not the child process, so pyosmium
  RAM was read with `Get-Process`.
- `config_full_cz.yml`, `config_full_cz_spojenka.yml`, `config_clip150.yml`,
  `config_clip150_8gb.yml`: MOTIS configs. Paths are relative to `data/full` or `data/clip150`.
- `queries.py`: the latency suite; it also writes `samples/*.json`.
- `car_scaling.py`, `scale_5000.py`, `geocode_check.py`.
- `clip_gtfs.py` (also sanitizes the feed), `clip_osm.py` (pyosmium), `make_circle.py`,
  `fetch_osmium_tool.py`.
- Raw outputs: `data/full/*.csv|*.json|*.txt` and `data/clip150/*.csv|*.json|*.txt`.
