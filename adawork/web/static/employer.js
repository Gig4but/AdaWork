// The employer card (PLAN §4.5): a click on an employer's name opens it in a dialog.
(() => {
  const dialog = document.getElementById("employer");
  if (!dialog) return;
  const body = document.getElementById("employer-body");
  let opening = false; // only a click opens the dialog, not a card that finished refreshing
  let trigger = null;
  document.addEventListener("click", (event) => {
    const button = event.target.closest(".employer-open");
    if (!button) return;
    event.preventDefault();
    opening = true;
    trigger = button;
    htmx.ajax("GET", `/employer/${button.dataset.employer}`,
              { source: button, target: body, swap: "innerHTML" });
  });
  document.body.addEventListener("htmx:afterSwap", (event) => {
    if (event.detail.target !== body || !opening) return;
    opening = false;
    if (!dialog.open) window.adaworkShowModal(dialog, trigger);
  });
  window.adaworkOnClosed(dialog, () => body.replaceChildren()); // a closed card stops asking
})();
