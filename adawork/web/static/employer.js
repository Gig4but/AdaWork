// The employer card (PLAN §4.5): a click on an employer's name opens it in a dialog.
(() => {
  const dialog = document.getElementById("employer");
  if (!dialog) return;
  const body = document.getElementById("employer-body");
  let opening = false; // only a click opens the dialog, not a card that finished refreshing
  document.addEventListener("click", (event) => {
    const button = event.target.closest(".employer-open");
    if (!button) return;
    event.preventDefault();
    opening = true;
    htmx.ajax("GET", `/employer/${button.dataset.employer}`, { target: body, swap: "innerHTML" });
  });
  document.body.addEventListener("htmx:afterSwap", (event) => {
    if (event.detail.target !== body || !opening) return;
    opening = false;
    if (!dialog.open) dialog.showModal();
  });
  dialog.addEventListener("close", () => body.replaceChildren()); // a closed card stops asking
})();
