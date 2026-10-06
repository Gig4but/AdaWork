// In the app window: paints the title bar and window border in the page's colours, so the
// frame blends with the page (desktop/window.py). Does nothing in a browser.
(() => {
  function paint() {
    const api = window.pywebview && window.pywebview.api;
    if (!api || !api.frame) return;
    const root = getComputedStyle(document.documentElement);
    api.frame(root.backgroundColor, root.color, root.colorScheme === "dark");
  }
  window.addEventListener("pywebviewready", paint);
  paint(); // the API may be ready before this script runs
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", paint);
  window.adaworkPaintFrame = paint; // the wizard repaints while previewing a scheme
})();
