// Travel time on the search page (PLAN §4.6): the start point box, the time limit, and the
// "Jak se tam dostanu" window with its map.
(() => {
  const form = document.getElementById("search-form");
  const from = form && form.querySelector("input[name=from]");
  if (!from) return;
  const at = form.querySelector("input[name=at]");
  const limit = form.querySelector("input[name=max_min]");
  const limitLabel = document.getElementById("max-label");
  const dialog = document.getElementById("commute");
  const body = document.getElementById("commute-body");

  // A typed start point becomes coordinates before the list reloads, so the form's own
  // "change" waits for the lookup.
  from.addEventListener("change", async (event) => {
    event.stopPropagation();
    const text = from.value.trim();
    if (!text) {
      at.value = "";
    } else {
      const response = await fetch("/commute/locate?text=" + encodeURIComponent(text));
      if (!response.ok) {
        at.value = ""; // not the previous point under the new name
        from.setCustomValidity(from.dataset.unknown || "?");
        from.reportValidity();
        return;
      }
      from.setCustomValidity("");
      at.value = (await response.json()).at;
    }
    syncSort();
    htmx.trigger(form, "change");
  });
  for (const type of ["input", "blur"]) from.addEventListener(type, () => from.setCustomValidity(""));

  // The start point set from the map ("Dojíždět odsud").
  window.adaworkSetStart = (lat, lon) => {
    from.value = `${lat.toFixed(5)},${lon.toFixed(5)}`;
    from.dispatchEvent(new Event("change", { bubbles: true }));
  };

  function syncSort() {
    const sort = form.querySelector("select[name=sort]");
    sort.querySelector("option[value=travel]").disabled = !at.value;
    if (!at.value && sort.value === "travel") sort.value = "relevance";
  }

  // The profile was saved in Settings (static/nav.js): its start point, mode and limit.
  window.adaworkProfileBar = async () => {
    const response = await fetch("/commute/defaults");
    if (!response.ok) return;
    for (const [name, value] of Object.entries(await response.json())) {
      form.elements[name].value = value;
    }
    from.setCustomValidity("");
    limit.dispatchEvent(new Event("input"));
    syncSort();
  };

  document.body.addEventListener("htmx:afterSettle", () => {
    const found = document.getElementById("origin")?.dataset;
    if (found?.at && !at.value && found.from === from.value.trim()) {
      at.value = found.at;
      syncSort();
    }
  });

  limit.addEventListener("input", () => {
    const value = Number(limit.value);
    limitLabel.textContent = value ? `${value} ${limit.dataset.minutes}` : limitLabel.dataset.none;
    document.dispatchEvent(new CustomEvent("adawork:limit", { detail: value }));
  });

  // --- The commute window ---
  let trigger = null;
  window.adaworkCommute = (jobId, source) => {
    trigger = source;
    const params = new URLSearchParams(new FormData(form));
    htmx.ajax("GET", `/commute/job/${jobId}?${params}`, { source, target: body, swap: "innerHTML" });
  };
  document.addEventListener("click", (event) => {
    const button = event.target.closest(".commute-open");
    if (button) window.adaworkCommute(button.dataset.job, button);
  });

  let map = null;
  document.body.addEventListener("htmx:afterSwap", (event) => {
    if (event.detail.target !== body) return;
    if (!dialog.open) window.adaworkShowModal(dialog, trigger);
    showMap();
  });
  window.adaworkOnClosed(dialog, () => {
    if (map) map.remove();
    map = null;
  });

  const dark = () => getComputedStyle(document.documentElement).colorScheme === "dark";
  const legColor = ["match", ["get", "mode"], "WALK", "#888", "BIKE", "#2e9e5b", "CAR", "#1e6ec8",
                    "#d9480f"];

  function showMap() {
    const container = document.getElementById("commute-map");
    if (map) map.remove();
    map = null;
    if (!container) return;
    const geo = JSON.parse(container.dataset.geo);
    const ends = JSON.parse(container.dataset.ends);
    map = new maplibregl.Map({ container, style: `/map/style.json?dark=${dark() ? 1 : 0}`,
                               bounds: ends, fitBoundsOptions: { padding: 60, maxZoom: 15 } });
    window.adaworkCommuteMap = map; // for UI tests
    map.addControl(new maplibregl.NavigationControl(), "top-right");
    new maplibregl.Marker({ color: "#2e9e5b" }).setLngLat(ends[0]).addTo(map);
    new maplibregl.Marker({ color: "#d64545" }).setLngLat(ends[1]).addTo(map);
    const range = body.querySelector(".area-range input");
    const output = body.querySelector(".area-range output");
    const own = map; // a later window has its own map: late answers for this one are dropped
    let area = null;
    let loading = 0; // the slider value being loaded: dragging asks once per range

    async function loadArea(minutes) {
      loading = minutes;
      const url = new URL(container.dataset.area, location.href);
      url.searchParams.set("max_min", minutes);
      try {
        const response = await fetch(url);
        if (!response.ok || map !== own) return;
        const data = await response.json();
        if (map !== own) return; // the window was closed meanwhile
        area = data;
        own.getSource("area").setData(area);
      } finally {
        if (loading === minutes) loading = 0;
      }
      filterArea(Number(range.value));
    }
    function filterArea(minutes) {
      own.setFilter("area", ["<=", ["get", "m"], minutes]);
      if (area && minutes > area.bucket && !(loading && minutes <= loading)) loadArea(minutes);
    }

    map.on("load", () => {
      map.addSource("area", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      map.addLayer({ id: "area", type: "fill", source: "area",
                     paint: { "fill-color": "#1e6ec8", "fill-opacity": 0.18 } });
      map.addSource("route", { type: "geojson", data: geo });
      // Walking dashed, riding solid (a dash pattern cannot depend on the feature).
      const layout = { "line-cap": "round", "line-join": "round" };
      map.addLayer({ id: "route-walk", type: "line", source: "route", layout,
                     paint: { "line-color": "#888", "line-width": 4,
                              "line-dasharray": [1, 1.5] } });
      map.addLayer({ id: "route-ride", type: "line", source: "route", layout,
                     paint: { "line-color": legColor, "line-width": 5 } });
      showRoute(0);
      loadArea(Number(range.value));
    });
    function showRoute(n) {
      const route = ["==", ["get", "route"], n];
      const walking = ["==", ["get", "mode"], "WALK"];
      map.setFilter("route-walk", ["all", route, walking]);
      map.setFilter("route-ride", ["all", route, ["!", walking]]);
    }
    range.addEventListener("input", () => {
      output.textContent = range.value;
      if (map.getLayer("area")) filterArea(Number(range.value));
    });
    // Another connection opened in the list: drawn instead.
    for (const details of body.querySelectorAll("details[data-route]")) {
      details.addEventListener("toggle", () => {
        if (!details.open || !map.getLayer("route-ride")) return;
        showRoute(Number(details.dataset.route));
        for (const other of body.querySelectorAll("details[data-route]")) {
          if (other !== details) other.open = false;
        }
      });
    }
  }
})();
