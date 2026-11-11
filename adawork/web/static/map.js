// Job map: clustered points for the current filters, the area reachable within the travel
// time limit, and the start point. The map is offline from the imported routing data
// (/map/style.json), online (OpenFreeMap) until that is ready.
(() => {
  const form = document.getElementById("search-form");
  const container = document.getElementById("map");
  if (!form || !container) return;

  const dark = getComputedStyle(document.documentElement).colorScheme === "dark";
  const style = `/map/style.json?dark=${dark ? 1 : 0}`;
  let start = null; // marker of the start point
  let area = null; // reachable cells (GeoJSON) and the query they are for
  let asked = null; // the area being loaded: { key, minutes }
  let refreshes = 0; // the points asked last win over slower earlier answers
  let map = null;

  const empty = document.getElementById("map-empty");
  const query = () => {
    const params = new URLSearchParams(new FormData(form));
    params.delete("view");
    return params.toString();
  };

  function init() {
    if (map) return;
    map = new maplibregl.Map({ container, style, center: [15.4, 49.8], zoom: 6.4 });
    window.adaworkMap = map; // for debugging and UI tests
    map.addControl(new maplibregl.NavigationControl(), "top-right");
    map.on("load", () => {
      map.addSource("area", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      map.addLayer({ id: "area", type: "fill", source: "area",
                     paint: { "fill-color": "#2e9e5b", "fill-opacity": 0.15 } });
      map.addSource("jobs", {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
        cluster: true,
        clusterRadius: 45,
        clusterMaxZoom: 13,
        clusterProperties: { jobs: ["+", ["get", "count"]] },
      });
      const size = (v) => ["step", v, 12, 10, 16, 100, 22, 1000, 30];
      map.addLayer({
        id: "clusters", type: "circle", source: "jobs", filter: ["has", "point_count"],
        paint: { "circle-color": "#1e6ec8", "circle-opacity": 0.85,
                 "circle-radius": size(["get", "jobs"]),
                 "circle-stroke-width": 2, "circle-stroke-color": "#fff" },
      });
      map.addLayer({
        id: "points", type: "circle", source: "jobs", filter: ["!", ["has", "point_count"]],
        paint: { "circle-color": ["case", ["get", "approx"], "#fff", "#1e6ec8"],
                 "circle-radius": size(["get", "count"]),
                 "circle-stroke-width": 2, "circle-stroke-color": "#1e6ec8" },
      });
      const label = (id, value, color) => map.addLayer({
        id, type: "symbol", source: "jobs",
        filter: id === "cluster-count" ? ["has", "point_count"] : ["!", ["has", "point_count"]],
        layout: { "text-field": ["to-string", value], "text-size": 12,
                  "text-font": ["Noto Sans Bold"], "text-allow-overlap": true },
        paint: { "text-color": color },
      });
      label("cluster-count", ["get", "jobs"], "#fff");
      label("point-count", ["get", "count"], ["case", ["get", "approx"], "#1e6ec8", "#fff"]);

      map.on("click", "clusters", async (e) => {
        const feature = e.features[0];
        const zoom = await map.getSource("jobs").getClusterExpansionZoom(feature.properties.cluster_id);
        map.flyTo({ center: feature.geometry.coordinates, zoom }); // an arc (out, across, in)
      });
      map.on("click", "points", async (e) => {
        const feature = e.features[0];
        const response = await fetch(`/map/location/${feature.properties.id}?${query()}`);
        if (!response.ok) return;
        const html = await response.text();
        new maplibregl.Popup({ maxWidth: "360px" })
          .setLngLat(feature.geometry.coordinates).setHTML(html).addTo(map);
      });
      // Right click: commute from here (once the map for travel times is downloaded).
      map.on("contextmenu", (e) => {
        if (!window.adaworkSetStart) return;
        const button = document.createElement("button");
        button.type = "button";
        button.className = "secondary small";
        button.textContent = container.dataset.startHere;
        const popup = new maplibregl.Popup().setLngLat(e.lngLat).setDOMContent(button).addTo(map);
        button.addEventListener("click", () => {
          popup.remove();
          window.adaworkSetStart(e.lngLat.lat, e.lngLat.lng);
        });
      });
      for (const layer of ["clusters", "points"]) {
        map.on("mouseenter", layer, () => (map.getCanvas().style.cursor = "pointer"));
        map.on("mouseleave", layer, () => (map.getCanvas().style.cursor = ""));
      }
      refresh();
    });
  }

  async function refresh() {
    const source = map && map.getSource("jobs");
    if (!source) return;
    const n = ++refreshes;
    const response = await fetch("/map/points?" + query());
    if (n !== refreshes || !response.ok) return;
    const points = await response.json();
    if (n !== refreshes) return;
    source.setData(points);
    // Jobs match but none has a place yet (geocoding runs at the end of a search): say so,
    // instead of an empty map.
    empty.hidden = points.features.length > 0 || !document.querySelector("#job-list > .job");
    showStart();
  }

  // Cells reachable within `minutes`; none for 0.
  const areaFilter = (minutes) => (minutes ? ["<=", ["get", "m"], minutes] : ["==", 1, 0]);

  // The start point and the area reachable from it within the limit (cells with "m" minutes;
  // moving the slider only filters them, unless it leaves the range they were made for).
  async function showStart() {
    const params = new URLSearchParams(new FormData(form));
    const at = params.get("at");
    const minutes = Number(params.get("max_min") || 0);
    if (start) start.remove();
    start = null;
    if (!at) {
      map.getSource("area").setData({ type: "FeatureCollection", features: [] });
      return;
    }
    const [lat, lon] = at.split(",").map(Number);
    start = new maplibregl.Marker({ color: "#2e9e5b" }).setLngLat([lon, lat]).addTo(map);
    const key = ["at", "mode", "depart"].map((k) => params.get(k)).join("|");
    if (!minutes) {
      map.setFilter("area", areaFilter(0));
      return;
    }
    if (!area || area.key !== key || minutes > area.data.bucket) {
      if (asked && asked.key === key && minutes <= asked.minutes) return; // on its way
      const request = (asked = { key, minutes });
      const query = new URLSearchParams({ at, from: params.get("from") || "", mode: params.get("mode"),
                                          depart: params.get("depart"), max_min: minutes });
      const response = await fetch("/commute/area?" + query);
      if (asked !== request) return; // another start point or limit asked since: that one wins
      asked = null;
      if (!response.ok) return;
      area = { key, data: await response.json() };
      map.getSource("area").setData(area.data);
    }
    const limit = Number(new FormData(form).get("max_min") || 0); // the slider may have moved
    map.setFilter("area", areaFilter(limit));
  }
  document.addEventListener("adawork:limit", (e) => {
    if (!map || !map.getLayer("area") || !area) return;
    if (e.detail > area.data.bucket) showStart();
    else map.setFilter("area", areaFilter(e.detail));
  });

  function show(view) {
    form.querySelector("input[name=view]").value = view;
    document.body.dataset.view = view;
    for (const b of document.querySelectorAll("[data-show-view]")) {
      b.setAttribute("aria-pressed", String(b.dataset.showView === view));
    }
    if (view === "map") {
      init();
      map.resize();
      refresh();
    }
    const params = new URLSearchParams(location.search);
    params.set("view", view);
    history.replaceState(null, "", "/?" + params.toString());
  }

  for (const button of document.querySelectorAll("[data-show-view]")) {
    button.addEventListener("click", () => show(button.dataset.showView));
  }
  // Filters changed: the list reloads through htmx; the map reloads its points.
  document.body.addEventListener("htmx:afterSwap", (e) => {
    if (e.detail.target.id === "results" && document.body.dataset.view === "map") refresh();
  });
  show(document.body.dataset.view || "list");
})();
