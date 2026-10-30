"use strict";

let lastRevision = -1;
let loading = false;
let sourceWidth = 1;
let sourceHeight = 1;

function fitPreview() {
  const stage = document.getElementById("detachedPreviewStage");
  const image = document.getElementById("detachedPreviewImage");
  if (image.classList.contains("hidden") || !sourceWidth || !sourceHeight) return;
  const style = getComputedStyle(stage);
  const availableWidth = Math.max(
    1,
    stage.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight)
  );
  const availableHeight = Math.max(
    1,
    stage.clientHeight - parseFloat(style.paddingTop) - parseFloat(style.paddingBottom)
  );
  const scale = Math.min(availableWidth / sourceWidth, availableHeight / sourceHeight);
  image.style.width = `${Math.max(1, Math.round(sourceWidth * scale))}px`;
  image.style.height = `${Math.max(1, Math.round(sourceHeight * scale))}px`;
}

async function refreshPreview() {
  if (loading || !window.pywebview?.api) return;
  loading = true;
  try {
    const state = await window.pywebview.api.get_state();
    if (!state?.ok) throw new Error(state?.error || "Preview state is unavailable.");
    document.getElementById("previewGame").textContent = state.game_name;
    document.getElementById("previewLabel").textContent = state.label;
    if (!state.available) throw new Error("The generated preview is not available.");
    if (state.revision !== lastRevision) {
      const result = await window.pywebview.api.get_preview(3840, 2160);
      if (!result?.ok) throw new Error(result?.error || "Preview image is unavailable.");
      const image = document.getElementById("detachedPreviewImage");
      sourceWidth = result.source_width;
      sourceHeight = result.source_height;
      image.onload = fitPreview;
      image.src = result.data_url;
      image.alt = `${state.game_name} ${state.label}`;
      image.classList.remove("hidden");
      document.getElementById("previewMessage").classList.add("hidden");
      document.getElementById("previewSize").textContent = `${result.source_width} x ${result.source_height}`;
      lastRevision = state.revision;
      fitPreview();
    }
  } catch (error) {
    const message = document.getElementById("previewMessage");
    message.textContent = error.message || String(error);
    message.classList.remove("hidden");
    document.getElementById("detachedPreviewImage").classList.add("hidden");
  } finally {
    loading = false;
  }
}

document.getElementById("closePreviewButton").addEventListener("click", async () => {
  if (!window.pywebview?.api) return;
  await window.pywebview.api.close();
});

document.addEventListener("keydown", async event => {
  if (event.key !== "Escape" || !window.pywebview?.api) return;
  event.preventDefault();
  await window.pywebview.api.close();
});

new ResizeObserver(fitPreview).observe(document.getElementById("detachedPreviewStage"));

window.addEventListener("pywebviewready", () => {
  refreshPreview();
  setInterval(refreshPreview, 800);
});
