"""The pages and endpoints of the web interface, one module per area."""

from adawork.web.routes import maps, search, settings, sources, wizard

ROUTERS = [m.router for m in (search, maps, sources, wizard, settings)]
