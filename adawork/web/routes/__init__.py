"""The pages and endpoints of the web interface, one module per area."""

from adawork.web.routes import maps, search, settings, wizard

ROUTERS = [m.router for m in (search, maps, wizard, settings)]
