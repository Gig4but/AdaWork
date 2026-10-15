# AdaWork

Desktop app that collects open job positions in Czechia from all major sources, reads badly
written ads with a small local AI model, shows employer reviews and filters jobs by commute time.

See [PLAN.md](PLAN.md) for the full plan and milestones, [ARCHITECTURE.md](ARCHITECTURE.md) for
how the code is organized.

## Development

```powershell
uv sync                 # create .venv with Python 3.12 and all dependencies
uv run python tools/fetch_llama.py   # llama.cpp builds into vendor/ (the AI model runtime)
uv run --with zstandard python tools/fetch_motis.py  # MOTIS + osmium into vendor/ (map, travel times)
uv run adawork          # open the app window + tray icon (log: %LOCALAPPDATA%\AdaWork\Logs)
uv run python -m adawork --browser  # run only the local web server and open it in the browser
uv run pytest
uv run ruff check .
uv run pyright           # types
uv run lint-imports      # layers: a package imports only from those below it
uv run python -m adawork probe     # can every portal still be read? (live, one listing + one ad each)
uv run python -m adawork eval --model qwen3-vl-2b --db <crawled adawork.db>  # AI accuracy and speed
powershell -ExecutionPolicy Bypass -File packaging\build.ps1  # dist\AdaWork-Setup.exe
```

The AI model itself is downloaded by the app (wizard step "Umělá inteligence" or Settings → AI).

## License

MIT (`LICENSE`). The bundled programs and libraries keep their own licenses:
`packaging/THIRD-PARTY-NOTICES.txt`, and in the app Settings → Zobrazit licence.
