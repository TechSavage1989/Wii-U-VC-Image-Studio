"use strict";

let lastRevision = -1;
let loading = false;

async function refreshActivity(force = false) {
  if (loading || !window.pywebview?.api) return;
  loading = true;
  try {
    const state = await window.pywebview.api.get_state();
    if (!state?.ok) throw new Error(state?.error || "Activity is unavailable.");
    document.getElementById("activityTitle").textContent = `${state.app_name} v${state.version}`;
    document.getElementById("activityStatus").textContent = state.busy ? (state.busy_message || "Working") : "Ready";
    if (force || state.revision !== lastRevision) {
      const log = document.getElementById("detachedActivityLog");
      const nearBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 70;
      log.textContent = (state.activity || []).join("\n") || "No activity has been recorded in this session.";
      if (nearBottom || lastRevision < 0) log.scrollTop = log.scrollHeight;
      lastRevision = state.revision;
    }
  } catch (error) {
    document.getElementById("detachedActivityLog").textContent = error.message || String(error);
  } finally {
    loading = false;
  }
}

document.getElementById("clearActivityButton").addEventListener("click", async () => {
  if (!window.pywebview?.api) return;
  await window.pywebview.api.clear_activity();
  refreshActivity(true);
});

document.getElementById("reattachActivityButton").addEventListener("click", async () => {
  if (!window.pywebview?.api) return;
  await window.pywebview.api.reattach();
});

window.addEventListener("pywebviewready", () => {
  refreshActivity(true);
  setInterval(refreshActivity, 700);
});
