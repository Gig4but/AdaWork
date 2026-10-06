# AdaWork

Desktop app that collects open job positions in Czechia from all major sources, reads badly
written ads with a small local AI model, shows employer reviews and filters jobs by commute time.

See [PLAN.md](PLAN.md) for the full plan and milestones.

## Development

```powershell
uv sync                 # create .venv with Python 3.12 and all dependencies
uv run adawork          # open the app window + tray icon
uv run adaworkw         # the same without a console (log: %LOCALAPPDATA%\AdaWork\Logs)
uv run adawork --browser  # run only the local web server and open it in the browser
uv run pytest
uv run ruff check .
```
