# Architecture

How the code is put together, for a programmer new to it. What the app does and why is in
[PLAN.md](PLAN.md).

## Layers

A package imports only from the layers below it; `uv run lint-imports` checks this
(`[tool.importlinter]` in `pyproject.toml`).

| Layer | Packages | What lives there |
|---|---|---|
| entry | `cli` | starts the window, the tray or a developer tool (`eval`, `probe`) |
| web | `web` | FastAPI app, one router per area in `web/routes/`, Jinja templates, HTMX |
| wiring | `services` | builds every long-lived part once and connects them (the only place that does) |
| features | `desktop`, `scheduler`, `employers` | window and tray; automatic searches; employer cards |
| | `scraper` | reading the sources, storing and merging jobs |
| | `social`, `ai` | Facebook/Instagram in Edge; the local model (llama-server) |
| | `search`, `routing` | queries and filters; travel times (MOTIS) |
| shared | `geo`, `db`, `normalize` | places; the SQLite schema; text and vocabulary |
| base | `core`, `net`, `system`, `config`, `i18n`, `worker`, … | parsed jobs, HTTP, this PC, settings |

`core.py` holds what every source produces (`ParsedJob`) and the values of the coded text
columns (`JobStatus`, `Precision`, …), so a typo is a type error (`uv run pyright`).

## One request

`web/routes/<area>.py` → a route takes `svc: Svc` (the `Services` of `services.py`) → calls a
service → `pages.render()` fills a template. Routes hold no logic beyond reading the form and
choosing the template; what two pages share is a function next to them (e.g. `source_rows`).

## One search

`ScrapeRunner` (`scraper/runner.py`) on its own thread, by source kind:
1. `mpsv.MpsvSource`: the daily Úřad práce file, first (all Czechia at once);
2. places for what is stored so far (`geo/`);
3. the portals' ads the user's list shows, each checked on its own page (`scraper/check.py`;
   not after the stored jobs were cleared);
4. portals and boards (`CrawlSource`, `scraper/crawl.py`) on four parallel slots, one host per
   slot, slow hosts beside them; key-based APIs (`ApiSource`) beside them too;
5. Facebook/Instagram (`SocialSource`), one at a time, in a heavy-job slot;
6. pictures, reposts merged; every other portal ad not seen in this run checked; then
   `RunHooks.after_run` starts the AI and the employer pass.

Every source writes through `JobStore` (`scraper/store.py`), which merges duplicates
(`scraper/dedup.py`) and expires what a source no longer lists. A run of a source is a row in
`runs`.

## Background threads

| Thread | Owner | Runs | Stops |
|---|---|---|---|
| `scheduler`, `employers`, `routing-data`, `updates` | `Worker` subclasses (`worker.py`) | a round, then rests; `wake()` starts the next now | `stop()` (the app's shutdown) |
| `scrape` | `ScrapeRunner.start` | one search | `stop()`: each site at its next request |
| `enrich`, `model-download` | `ai/enrich.py`, `ai/manager.py` | once, when started | `stop()` / `shutdown()` |
| `commute`, `employer-<id>`, `login-<platform>`, `routing-update` | the service that starts them | one job | end with it |
| `web-server` | `desktop/server.py` | uvicorn | `server.stop()` |

Heavy work (the model, a routing import, Edge) takes a slot of `system/resources.py`, which
lets one run at a time on 8 GB and more on bigger PCs. The model and MOTIS are separate
processes; `system/winjob.py` ends them with the app.

## Adding things

- **A portal:** a `CrawlSource` subclass in `scraper/sources/<site>.py` (listing URL, how to read
  a listing card and a detail page), saved pages in `tests/fixtures/sources/<site>/`, a test,
  and the source in `CRAWL_SOURCES` (`scraper/sources/__init__.py`). The Sources page, labels
  and switches follow from that list. Check it live with `uv run python -m adawork probe <id>`.
- **A page:** a route in the area's `web/routes/` module (or a new module in
  `web/routes/__init__.py`), a template, texts in `i18n.py` in both languages (a test checks).
- **A setting:** a field in `config.py` (pydantic, with a default), its form in the Settings
  section's route and template.
- **A column:** the model in `db/models.py` and a migration (`uv run python
  tools/make_migration.py`); the app migrates on start after copying the database to
  `adawork.db.bak`. A new `ParsedJob` field gets a column of the same name (a test checks).

## Checks

`uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run lint-imports
&& uv run pytest` — all green on every commit.
