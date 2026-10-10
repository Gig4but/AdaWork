"""The pages and endpoints of the web interface, one module per area."""

from adawork.web.routes import ai, jobs, maps, search, settings, sources, wizard

ROUTERS = [m.router for m in (search, jobs, maps, ai, sources, wizard, settings)]
