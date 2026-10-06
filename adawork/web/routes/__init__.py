"""The pages and endpoints of the web interface, one module per area."""

from adawork.web.routes import search, settings, wizard

ROUTERS = [m.router for m in (search, wizard, settings)]
