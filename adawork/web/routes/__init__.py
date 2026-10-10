"""The pages and endpoints of the web interface, one module per area."""

from adawork.web.routes import ai, maps, search, settings, sources, wizard

ROUTERS = [m.router for m in (search, maps, ai, sources, wizard, settings)]
