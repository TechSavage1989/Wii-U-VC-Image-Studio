"use strict";

let currentState = null;
let lastPreviewSignature = "";
let loading = false;

async function loadDetachedPreviews(state) {
  const signature = `${state.job_id}:${state.preview_revision}`;
  if (signature === lastPreviewSignature) return;
  lastPreviewSignature = signature;
  for (const tile of document.querySelectorAll(".detached-preview-tile")) {
    const key = tile.dataset.preview;
    const image = tile.querySelector("img");
    const message = tile.querySelector(".window-message");
    const available = Boolean(state.job_id && state.preview_available[key]);
    tile.classList.toggle("unavailable", !available);
    if (!available) {
      image.removeAttribute("src");
      image.classList.add("hidden");
      message.classList.remove("hidden");
      continue;
    }
    try {
      const result = await window.pywebview.api.get_preview(state.job_id, key, 1400, 900);
      if (!result?.ok) throw new Error(result?.error || "Preview unavailable");
      if (currentState?.job_id !== state.job_id || currentState?.preview_revision !== state.preview_revision) return;
      image.src = result.data_url;
      image.classList.remove("hidden");
      message.classList.add("hidden");
    } catch (error) {
      image.removeAttribute("src");
      image.classList.add("hidden");
      message.textContent = error.message || String(error);
      message.classList.remove("hidden");
      tile.classList.add("unavailable");
    }
  }
}

async function refreshDetachedPreviewPanel() {
  if (loading || !window.pywebview?.api) return;
  loading = true;
  try {
    const state = await window.pywebview.api.get_state();
    if (!state?.ok) throw new Error(state?.error || "Selected Preview is unavailable.");
    currentState = state;
    document.getElementById("previewPanelGame").textContent = state.game_name;
    await loadDetachedPreviews(state);
  } catch (error) {
    document.getElementById("previewPanelGame").textContent = error.message || String(error);
  } finally {
    loading = false;
  }
}

for (const tile of document.querySelectorAll(".detached-preview-tile")) {
  tile.addEventListener("click", async () => {
    const key = tile.dataset.preview;
    if (tile.classList.contains("unavailable") || !currentState?.job_id) return;
    await window.pywebview.api.open_large_preview(currentState.job_id, key);
  });
}

document.getElementById("adjustFramingButton").addEventListener("click", async () => {
  if (!window.pywebview?.api) return;
  await window.pywebview.api.adjust_framing();
});

document.getElementById("reattachPreviewButton").addEventListener("click", async () => {
  if (!window.pywebview?.api) return;
  await window.pywebview.api.reattach();
});

window.addEventListener("pywebviewready", () => {
  refreshDetachedPreviewPanel();
  setInterval(refreshDetachedPreviewPanel, 700);
});
