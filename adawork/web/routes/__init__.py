"""The pages and endpoints of the web interface, one module per area."""

from adawork.web.routes import (
    ai,
    commute,
    employers,
    jobs,
    maps,
    saved,
    search,
    settings,
    sources,
    wizard,
)

ROUTERS = [
    m.router for m in (search, jobs, maps, commute, saved, employers, ai, sources, wizard, settings)
]
