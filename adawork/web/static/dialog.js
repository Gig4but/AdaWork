// Dialogs grow out of the control that opened them and shrink back into it (app.css).
window.adaworkShowModal = (dialog, trigger) => {
  dialog.classList.add("opening");
  dialog.showModal();
  const article = dialog.querySelector("article");
  const settling = [...article.children].flatMap((child) => child.getAnimations());
  Promise.allSettled(settling.map((a) => a.finished)).then(() => dialog.classList.remove("opening"));
  if (!trigger || !trigger.isConnected) {
    article.style.transformOrigin = "";
    return;
  }
  // Layout offsets, not the bounding box: the article is still scaled down at this point.
  const box = trigger.getBoundingClientRect();
  article.style.transformOrigin = `${box.left + box.width / 2 - article.offsetLeft}px `
                                + `${box.top + box.height / 2 - article.offsetTop}px`;
};

// `done` waits for the closing animation, so the dialog does not shrink away empty.
window.adaworkOnClosed = (dialog, done) => {
  const article = dialog.querySelector("article");
  dialog.addEventListener("close", async () => {
    await Promise.allSettled(article.getAnimations().map((a) => a.finished));
    if (!dialog.open) done();
  });
};
