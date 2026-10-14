"""A MapLibre style for the vector tiles MOTIS builds from the imported map (offline).

Two tile profiles exist (routing/data.py): "full" for the whole country (layers streets,
water_polygons, land, buildings, place_labels, ...) and the lighter "background" around home
(road, rail, water, landuse, building, cities). The style has layers for both; those of the
other profile simply find no data. Fonts come with MOTIS (Noto Sans).
"""

LIGHT = {
    "background": "#f2efe9", "water": "#aad3df", "forest": "#d5e6c8", "green": "#e3edd4",
    "built": "#ebe6df", "building": "#d9d0c9", "minor": "#ffffff", "minor_case": "#d6d0c8",
    "major": "#fcd6a4", "motorway": "#e892a2", "rail": "#9a9a9a", "text": "#333333",
    "halo": "#ffffff", "boundary": "#9e9cab",
}  # fmt: skip
DARK = {
    "background": "#1f2124", "water": "#2b3f52", "forest": "#24302a", "green": "#26302a",
    "built": "#26282c", "building": "#33363b", "minor": "#3a3d42", "minor_case": "#2a2c30",
    "major": "#6b5a3e", "motorway": "#7d4a55", "rail": "#5f6266", "text": "#d8d8d8",
    "halo": "#1f2124", "boundary": "#6c6a78",
}  # fmt: skip
ATTRIBUTION = "© OpenStreetMap contributors"
FONT = ["Noto Sans Regular"]
BOLD = ["Noto Sans Bold"]

MAJOR = ["primary", "secondary", "tertiary"]
MINOR = ["residential", "unclassified", "living_street", "service", "pedestrian"]
GREEN = ["park", "garden", "grass", "meadow", "village_green", "recreation_ground", "allotments",
         "cemetery", "grave_yard", "orchard", "vineyard"]  # fmt: skip
BUILT = ["residential", "industrial", "commercial", "retail", "railway", "garages"]
SOURCE = "motis"


def _width(base: float) -> list:
    return ["interpolate", ["exponential", 1.5], ["zoom"], 6, base * 0.3, 12, base * 1.5,
            16, base * 6]  # fmt: skip


def _is(prop: str, values: list[str]) -> list:
    """Filter: the feature's `prop` is one of `values`."""
    return ["in", ["get", prop], ["literal", values]]


def _layer(id_: str, type_: str, layer: str, flt: list | None, minzoom: int, **spec) -> dict:
    base = {"id": id_, "type": type_, "source": SOURCE, "source-layer": layer, "minzoom": minzoom}
    return base | ({"filter": flt} if flt else {}) | spec


def _fill(id_: str, layer: str, color: str, flt: list | None = None, minzoom: int = 0) -> dict:
    return _layer(id_, "fill", layer, flt, minzoom, paint={"fill-color": color})


def _line(id_: str, layer: str, color: str, width, flt=None, minzoom=0, dash=None) -> dict:
    paint = {"line-color": color, "line-width": width} | ({"line-dasharray": dash} if dash else {})
    layout = {"line-cap": "round", "line-join": "round"}
    return _layer(id_, "line", layer, flt, minzoom, layout=layout, paint=paint)


def style(tiles_url: str, glyphs_url: str, dark: bool = False) -> dict:
    c = DARK if dark else LIGHT

    def label(id_, layer, size, flt=None, minzoom=0, bold=False):
        layout = {
            "text-field": ["coalesce", ["get", "name:cs"], ["get", "name"]],
            "text-font": BOLD if bold else FONT,
            "text-size": size,
            "text-max-width": 8,
        }
        paint = {"text-color": c["text"], "text-halo-color": c["halo"], "text-halo-width": 1.5}
        return _layer(id_, "symbol", layer, flt, minzoom, layout=layout, paint=paint)  # fmt: skip

    layers = [
        {"id": "background", "type": "background", "paint": {"background-color": c["background"]}},
        # "full" profile
        _fill("land-forest", "land", c["forest"], _is("kind", ["forest", "scrub"])),
        _fill("land-green", "land", c["green"], _is("kind", GREEN)),
        _fill("land-built", "land", c["built"], _is("kind", BUILT)),
        _fill("water", "water_polygons", c["water"]),
        _line("waterway", "water_lines", c["water"], 1.2, minzoom=9),
        _fill("buildings", "buildings", c["building"], minzoom=14),
        _line("streets-minor", "streets", c["minor"], _width(1.2), _is("kind", MINOR), 12),
        _line("streets-major", "streets", c["major"], _width(2.2), _is("kind", MAJOR), 7),
        _line(
            "streets-motorway",
            "streets",
            c["motorway"],
            _width(3),
            _is("kind", ["motorway", "trunk"]),
            5,
        ),  # fmt: skip
        _line("rail", "streets", c["rail"], 1.2, ["==", ["get", "rail"], True], 8, [3, 2]),
        _line("boundaries", "boundaries", c["boundary"], 1, dash=[4, 2]),
        # "background" profile
        _fill("landuse", "landuse", c["green"]),
        _fill("water-bg", "water", c["water"]),
        _line("waterway-bg", "waterway", c["water"], 1.2, minzoom=9),
        _fill("building-bg", "building", c["building"], minzoom=14),
        _line("road-minor", "road", c["minor"], _width(1.2), _is("highway", MINOR), 12),
        _line(
            "road-major",
            "road",
            c["major"],
            _width(2.2),
            _is("highway", [*MAJOR, "primary_link", "secondary_link"]),
            7,
        ),  # fmt: skip
        _line(
            "road-motorway",
            "road",
            c["motorway"],
            _width(3),
            _is("highway", ["motorway", "trunk", "motorway_link", "trunk_link"]),
            5,
        ),  # fmt: skip
        _line("rail-bg", "rail", c["rail"], 1.2, ["==", ["get", "rail"], "primary"], 8, [3, 2]),
        # labels
        label("streets-label", "streets", 11, _is("kind", [*MAJOR, *MINOR]), 14),
        label("road-label", "road", 11, _is("highway", [*MAJOR, *MINOR]), 14),
        label(
            "place-villages", "place_labels", 11, _is("kind", ["village", "suburb", "hamlet"]), 11
        ),
        label("place-towns", "place_labels", 13, ["==", ["get", "kind"], "town"], 8),
        label(
            "place-cities",
            "place_labels",
            15,
            _is("kind", ["city", "capital", "state_capital"]),
            5,
            bold=True,
        ),  # fmt: skip
        label("cities-bg", "cities", 13, None, 6),
    ]
    source = {"type": "vector", "tiles": [tiles_url], "maxzoom": 20, "attribution": ATTRIBUTION}
    return {"version": 8, "glyphs": glyphs_url, "sources": {SOURCE: source}, "layers": layers}
