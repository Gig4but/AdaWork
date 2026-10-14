// On the search page, Settings and adding an ad open in #other beside the search, which
// stays loaded and hidden: going back is instant and finds the filters, list, map and scroll
// as they were. Links and forms inside #other load there through hx-boost.
(() => {
  const page = document.getElementById("page");
  const other = document.getElementById("other");
  const searchTitle = document.title;
  let scrollY = 0;
  let saved = false; // a setting was saved, which may change what the search shows
  let profileSaved = false; // the travel-time bar takes the profile's values

  // As the server marks a page: only the page itself (a Settings sub-page marks nothing).
  function mark(path) {
    const current = path.startsWith("/add") ? "/add" : path;
    for (const link of document.querySelectorAll(".app-header a.nav-button")) {
      if (link.getAttribute("href") === current) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    }
  }

  function showSearch() {
    if (other.hidden) return;
    other.hidden = true;
    other.replaceChildren(); // also stops its polling
    page.hidden = false;
    document.title = searchTitle;
    mark("/");
    window.scrollTo(0, scrollY);
    if (saved) {
      const bar = profileSaved && window.adaworkProfileBar;
      saved = profileSaved = false;
      Promise.resolve(bar && bar()).finally(() => htmx.trigger(document.body, "jobs-updated"));
    }
  }

  document.querySelector(".app-header").addEventListener("click", (event) => {
    const link = event.target.closest("a.nav-button");
    if (!link || event.ctrlKey || event.shiftKey) return;
    event.preventDefault();
    const href = link.getAttribute("href");
    if (href === "/") showSearch();
    else htmx.ajax("GET", href, { target: other, swap: "innerHTML", select: "#page > *" });
  });

  document.body.addEventListener("htmx:afterSwap", (event) => {
    if (event.detail.target !== other) return;
    const request = event.detail.requestConfig;
    if (request.verb !== "get") {
      saved = true;
      if (request.path.startsWith("/settings/profile")) profileSaved = true;
    }
    if (other.hidden) {
      scrollY = window.scrollY;
      page.hidden = true;
      other.hidden = false;
    }
    mark(new URL(event.detail.xhr.responseURL).pathname);
    window.scrollTo(0, 0);
  });
})();
