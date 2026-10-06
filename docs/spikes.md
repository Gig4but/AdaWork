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
