// A job's whole text: a click anywhere on its card opens it in a dialog, except on the card's
// own links, buttons and pictures, or after selecting text in it.
(() => {
  const dialog = document.getElementById("job-detail");
  if (!dialog) return;
  const body = document.getElementById("job-detail-body");
  let opening = false;
  let trigger = null;

  function jobOf(event) {
    const link = event.target.closest(".job-open");
    if (link) return link.closest("#other") ? null : link.dataset.job; // beside the search: there
    const card = event.target.closest("#job-list > .job");
    if (!card || event.target.closest("a, button, input, select, textarea, label, summary")) return null;
    if (String(window.getSelection())) return null;
    return card.dataset.id;
  }

  document.addEventListener("click", (event) => {
    if (event.ctrlKey || event.shiftKey) return; // Ctrl/Shift-click on the title opens the page
    const job = jobOf(event);
    if (!job) return;
    event.preventDefault();
    opening = true;
    const source = event.target.closest(".job") || undefined;
    trigger = event.target.closest(".job-open") || source;
    htmx.ajax("GET", `/jobs/${job}`, { source, target: body, swap: "innerHTML" });
  });
  document.body.addEventListener("htmx:afterSwap", (event) => {
    if (event.detail.target !== body || !opening) return;
    opening = false;
    if (!dialog.open) window.adaworkShowModal(dialog, trigger);
    body.scrollTop = 0;
  });
  window.adaworkOnClosed(dialog, () => body.replaceChildren());
})();
