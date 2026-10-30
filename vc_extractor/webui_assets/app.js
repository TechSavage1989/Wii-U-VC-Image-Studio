"use strict";

const ui = {
  state: null,
  lastStateRevision: -1,
  lastLogRevision: -1,
  lastPreviewKey: "",
  pollBusy: false,
  selectionAnchor: null,
  activeModal: null,
  activityCollapsed: false,
  settingsTimer: null,
  dragDepth: 0,
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function singleJob() {
  if (!ui.state || ui.state.selected_ids.length !== 1) return null;
  return ui.state.jobs.find(job => job.id === ui.state.selected_ids[0]) || null;
}

function selectedJobs() {
  if (!ui.state) return [];
  const selected = new Set(ui.state.selected_ids);
  return ui.state.jobs.filter(job => selected.has(job.id));
}

function formatPath(value) {
  return value && String(value).trim() ? String(value) : "Not configured";
}

function showToast(message, level = "info", title = "Wii U VC Image Studio") {
  if (!message) return;
  const host = $("#toastHost");
  const item = document.createElement("div");
  item.className = `toast ${level}`;
  item.innerHTML = `<strong>${escapeHtml(title)}</strong><span>${escapeHtml(message)}</span>`;
  host.appendChild(item);
  const timeout = Math.max(5000, Math.min(11000, 4200 + String(message).length * 22));
  const remove = () => item.remove();
  item.addEventListener("click", remove);
  setTimeout(remove, timeout);
}

async function callApi(method, ...args) {
  const bridge = window.pywebview && window.pywebview.api;
  if (!bridge || typeof bridge[method] !== "function") {
    throw new Error("The local application bridge is not ready.");
  }
  const result = await bridge[method](...args);
  if (!result || result.ok === false) {
    throw new Error((result && result.error) || "The operation could not be completed.");
  }
  return result;
}

async function invoke(method, args = [], options = {}) {
  try {
    const result = await callApi(method, ...args);
    if (options.success) showToast(options.success, "success");
    if (result.state) applyState(result.state, true);
    else if (options.refresh !== false) await refreshState(true);
    return result;
  } catch (error) {
    showToast(error.message || String(error), "error", options.title || "Operation failed");
    return null;
  }
}

async function refreshState(force = false) {
  if (ui.pollBusy) return;
  ui.pollBusy = true;
  try {
    const state = await window.pywebview.api.get_state();
    applyState(state, force);
  } catch (error) {
    if (force) showToast(error.message || String(error), "error", "Connection error");
  } finally {
    ui.pollBusy = false;
  }
}

function applyState(state, force = false) {
  if (!state) return;
  const stateChanged = force || state.state_revision !== ui.lastStateRevision;
  const logChanged = force || state.log_revision !== ui.lastLogRevision;
  ui.state = state;
  window.studioUpdates?.render(state.updates, state.busy);
  if (stateChanged) {
    ui.lastStateRevision = state.state_revision;
    renderHeader();
    renderPaths();
    renderTable();
    renderControls();
    renderOperation();
    renderDetachedPanels();
    updateActionAvailability();
    loadMainPreviews();
  } else {
    renderOperation();
  }
  if (logChanged) {
    ui.lastLogRevision = state.log_revision;
    renderActivity();
  }
  for (const notification of state.notifications || []) {
    showToast(notification.message, notification.level || "info", notification.title || "Notice");
  }
}

function renderHeader() {
  $("#versionLabel").textContent = `v${ui.state.version}`;
  const chip = $("#operationChip");
  chip.classList.toggle("busy", ui.state.busy);
  chip.classList.toggle("ready", !ui.state.busy);
  $("span:last-child", chip).textContent = ui.state.busy ? ui.state.busy_kind : "Ready";
}

function renderPaths() {
  const settings = ui.state.settings;
  const mappings = [
    ["#retroPath", settings.retroarch_path],
    ["#dolphinPath", settings.dolphin_path],
    ["#outputPath", settings.output_root],
  ];
  for (const [selector, value] of mappings) {
    const element = $(selector);
    element.textContent = formatPath(value);
    element.title = formatPath(value);
  }
}

function renderTable() {
  const body = $("#romTableBody");
  const empty = $("#emptyState");
  const table = $(".rom-table");
  const jobs = ui.state.jobs;
  empty.classList.toggle("hidden", jobs.length > 0);
  table.classList.toggle("hidden", jobs.length === 0);
  const selected = new Set(ui.state.selected_ids);
  body.innerHTML = jobs.map((job, index) => `
    <tr data-job-id="${job.id}" data-row-index="${index}" class="${selected.has(job.id) ? "selected" : ""}" title="${escapeHtml(job.file_path)}">
      <td class="check-column"><input class="capture-check" type="checkbox" ${job.checked ? "checked" : ""} aria-label="Capture ${escapeHtml(job.name)}"></td>
      <td class="remove-column"><button class="remove-row" title="Remove this row without deleting the source ROM" aria-label="Remove ${escapeHtml(job.name)}">X</button></td>
      <td>${escapeHtml(job.file_name)}</td>
      <td>${escapeHtml(job.source)}</td>
      <td>${escapeHtml(job.name)}</td>
      <td>${escapeHtml(job.system)}</td>
      <td>${escapeHtml(job.year || "-")}</td>
      <td>${escapeHtml(job.players || "-")}</td>
      <td>${escapeHtml(job.product_code || "-")}</td>
      <td class="status-cell">${escapeHtml(job.status)}</td>
    </tr>
  `).join("");

  for (const row of $$("tr[data-job-id]", body)) {
    const jobId = row.dataset.jobId;
    row.addEventListener("click", event => handleRowSelection(event, jobId, Number(row.dataset.rowIndex)));
    row.addEventListener("dblclick", event => {
      if (event.target.closest("input, button")) return;
      const job = ui.state.jobs.find(item => item.id === jobId);
      if (job && job.candidate_count) openCaptureReview(job);
      else openDetailsModal(job);
    });
    const checkbox = $(".capture-check", row);
    checkbox.addEventListener("click", event => event.stopPropagation());
    checkbox.addEventListener("change", () => invoke("toggle_capture_checked", [jobId, checkbox.checked]));
    const remove = $(".remove-row", row);
    remove.addEventListener("click", event => {
      event.stopPropagation();
      invoke("remove_job", [jobId]);
    });
  }
}

async function handleRowSelection(event, jobId, index) {
  if (event.target.closest("input, button")) return;
  let selected = new Set(ui.state.selected_ids);
  if (event.shiftKey && ui.selectionAnchor !== null) {
    const start = Math.min(ui.selectionAnchor, index);
    const end = Math.max(ui.selectionAnchor, index);
    if (!event.ctrlKey && !event.metaKey) selected.clear();
    for (let position = start; position <= end; position += 1) selected.add(ui.state.jobs[position].id);
  } else if (event.ctrlKey || event.metaKey) {
    if (selected.has(jobId)) selected.delete(jobId);
    else selected.add(jobId);
    ui.selectionAnchor = index;
  } else {
    selected = new Set([jobId]);
    ui.selectionAnchor = index;
  }
  ui.state.selected_ids = Array.from(selected);
  renderTable();
  renderControls();
  updateActionAvailability();
  loadMainPreviews(true);
  await invoke("set_selection", [Array.from(selected)], {refresh: false});
}

function setActivePanelTab(name) {
  for (const tab of $$(".panel-tab")) tab.classList.toggle("active", tab.dataset.tab === name);
  for (const page of $$(".tab-page")) page.classList.toggle("active", page.dataset.tabPage === name);
}

function fillSelect(element, options, current, emptyLabel = "No options available") {
  if (!options || !options.length) {
    element.innerHTML = `<option value="">${escapeHtml(emptyLabel)}</option>`;
    element.disabled = true;
    return;
  }
  element.innerHTML = options.map(option => {
    const value = typeof option === "string" ? option : option.value;
    const label = typeof option === "string" ? option : option.label;
    return `<option value="${escapeHtml(value)}" ${value === current ? "selected" : ""}>${escapeHtml(label)}</option>`;
  }).join("");
  element.disabled = false;
}

function renderControls() {
  const job = singleJob();
  $("#gameControlsEmpty").classList.toggle("hidden", Boolean(job));
  $("#gameControls").classList.toggle("hidden", !job);
  $("#soundControlsEmpty").classList.toggle("hidden", Boolean(job));
  $("#soundControls").classList.toggle("hidden", !job);

  const settings = ui.state.settings;
  const fields = {
    warmupInput: settings.warmup_seconds,
    durationInput: settings.duration_seconds,
    intervalInput: settings.interval_seconds,
    candidateInput: settings.candidate_count,
    captureCropSelect: settings.screen_crop,
  };
  for (const [id, value] of Object.entries(fields)) {
    const input = document.getElementById(id);
    if (document.activeElement !== input) input.value = value;
  }
  $("#makeIconCheck").checked = Boolean(settings.create_icon_image);
  $("#makeTvCheck").checked = Boolean(settings.create_tv_image);
  $("#makeGamepadCheck").checked = Boolean(settings.create_gamepad_image);
  $("#overlayCheck").checked = Boolean(settings.include_banner_metadata);
  $("#titleOverlayCheck").checked = Boolean(settings.include_game_title);
  $("#releaseOverlayCheck").checked = Boolean(settings.include_release_date);
  $("#playersOverlayCheck").checked = Boolean(settings.include_player_count);
  for (const input of [$("#titleOverlayCheck"), $("#releaseOverlayCheck"), $("#playersOverlayCheck")]) {
    input.disabled = !settings.include_banner_metadata;
  }

  if (!job) return;
  $("#selectedGameName").textContent = job.name;
  $("#selectedSystemBadge").textContent = job.system;
  fillSelect($("#iconFrameSelect"), job.icon_frame_choices, job.icon_template);
  $("#sourceAreaSelect").value = job.source_area_selection;
  const showTvSource = ["Wii", "WiiWare"].includes(job.system);
  $("#tvSourceGroup").classList.toggle("hidden", !showTvSource);
  if (showTvSource) fillSelect($("#tvSourceSelect"), job.tv_gamepad_sources, job.tv_gamepad_source);
  $("#artworkCountBadge").textContent = job.artwork_match_count;
  $("#candidateCountBadge").textContent = job.candidate_count;

  const sounds = job.boot_sound_options || [];
  const currentSound = sounds.find(option => option.active);
  const soundOptions = sounds.map(option => ({value: option.token, label: option.label}));
  if (!currentSound && soundOptions.length) soundOptions.unshift({value: "", label: "Choose a boot sound..."});
  fillSelect(
    $("#bootSoundSelect"),
    soundOptions,
    currentSound ? currentSound.token : "",
    "No sound selected"
  );
  $("#bootSoundSummary").textContent = currentSound
    ? `Active: ${currentSound.label}. Playback and export use this exact selection.`
    : sounds.length
      ? `${sounds.length} sound choice(s) available. Select one, search online sources, or import a local file.`
      : "No sound is selected. Browse for a local WAV, MP3, or BTSND file, set a console default, or search available sources.";
  $("#playSoundButton").disabled = !job.has_boot_sound;
}

function renderOperation() {
  const busy = ui.state.busy;
  const progress = ui.state.progress || {current: 0, total: 0};
  const total = Number(progress.total) || 0;
  const current = Number(progress.current) || 0;
  const percent = busy ? (total ? Math.max(3, Math.min(100, current / total * 100)) : 10) : 0;
  $("#operationText").textContent = busy ? (ui.state.busy_message || ui.state.busy_kind) : "Ready";
  $("#operationFraction").textContent = busy && total ? `${current} of ${total}` : "";
  $("#progressFill").style.width = `${percent}%`;
}

function renderDetachedPanels() {
  const previewDetached = Boolean(ui.state.preview_detached);
  $("#detachPreviewButton").textContent = previewDetached ? "Reattach Preview" : "Detach Preview";
  $("#activityPanel").classList.toggle("detached", Boolean(ui.state.activity_detached));
}

function renderActivity() {
  const log = $("#activityLog");
  const wasNearBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 70;
  log.textContent = (ui.state.activity || []).join("\n");
  if (wasNearBottom || !log.dataset.initialized) log.scrollTop = log.scrollHeight;
  log.dataset.initialized = "true";
}

function updateActionAvailability() {
  const job = singleJob();
  const selected = selectedJobs();
  const busy = ui.state.busy;
  const hasJobs = ui.state.jobs.length > 0;
  for (const element of $$('[data-requires="single"]')) element.disabled = !job || busy;
  $("#toggleSelectedButton").disabled = selected.length === 0 || busy;
  const selectedAllChecked = selected.length > 0 && selected.every(item => item.checked);
  $("#toggleSelectedButton").textContent = selectedAllChecked ? "Uncheck Selected" : "Check Selected";
  const checkedCount = ui.state.jobs.filter(item => item.checked).length;
  $("#removeCheckedButton").disabled = checkedCount === 0 || busy;
  $("#removeCheckedButton").textContent = checkedCount ? `Remove Checked (${checkedCount})` : "Remove Checked";
  $("#startCaptureButton").disabled = busy || !hasJobs;
  $("#stopCaptureButton").disabled = ui.state.busy_kind !== "capture";
  for (const element of $$('[data-action="add-roms"], [data-action="add-folder"]')) element.disabled = busy;
  for (const action of ["choose-retro", "choose-dolphin", "choose-output", "fetch-metadata", "build-images", "check-all", "clear-checks", "edit-details", "open-framing", "open-placement", "review-artwork", "review-captures", "clear-artwork", "import-banner", "browse-sound", "set-console-sound", "search-sounds", "more-sounds", "clear-sound"]) {
    for (const element of $$(`[data-action="${action}"]`)) {
      const needsJob = ["edit-details", "open-framing", "open-placement", "review-artwork", "review-captures", "clear-artwork", "import-banner", "browse-sound", "set-console-sound", "search-sounds", "more-sounds", "clear-sound"].includes(action);
      element.disabled = busy || !hasJobs || (needsJob && !job);
    }
  }
  $("#iconFrameSelect").disabled = busy || !job || !job.icon_frame_choices.length;
  $("#sourceAreaSelect").disabled = busy || !job;
  $("#tvSourceSelect").disabled = busy || !job || !["Wii", "WiiWare"].includes(job.system);
  $("#bootSoundSelect").disabled = busy || !job || !job.boot_sound_options.length;
  for (const input of $$("#warmupInput, #durationInput, #intervalInput, #candidateInput, #captureCropSelect, #makeIconCheck, #makeTvCheck, #makeGamepadCheck, #overlayCheck")) input.disabled = busy;
  for (const input of $$("#titleOverlayCheck, #releaseOverlayCheck, #playersOverlayCheck")) input.disabled = busy || !ui.state.settings.include_banner_metadata;
  $("#playSoundButton").disabled = busy || !job || !job.has_boot_sound;
}

async function loadMainPreviews(force = false) {
  const job = singleJob();
  const detached = Boolean(ui.state.preview_detached);
  $("#previewDetachedState").classList.toggle("hidden", !detached);
  if (detached) {
    $("#previewEmpty").classList.add("hidden");
    $("#previewGrid").classList.add("hidden");
    ui.lastPreviewKey = "";
    return;
  }
  $("#previewEmpty").classList.toggle("hidden", Boolean(job));
  $("#previewGrid").classList.toggle("hidden", !job);
  if (!job) {
    ui.lastPreviewKey = "";
    return;
  }
  const cacheKey = `${job.id}:${job.preview_revision}`;
  if (!force && ui.lastPreviewKey === cacheKey) return;
  ui.lastPreviewKey = cacheKey;
  for (const tile of $$(".preview-tile")) {
    const key = tile.dataset.preview;
    const available = Boolean(job.preview_available[key]);
    tile.classList.toggle("unavailable", !available);
    const image = $("img", tile);
    if (!available) {
      image.removeAttribute("src");
      continue;
    }
    callApi("get_preview", job.id, key, 860, 540)
      .then(result => {
        const current = singleJob();
        if (!current || current.id !== job.id || current.preview_revision !== job.preview_revision) return;
        image.src = result.data_url;
      })
      .catch(error => {
        tile.classList.add("unavailable");
        image.removeAttribute("src");
        console.error(error);
      });
  }
}

function modalTemplate(title, body, footer = "", size = "") {
  const backdropClass = size === "framing" ? "modal-backdrop framing-backdrop" : "modal-backdrop";
  return `
    <div class="${backdropClass}" role="presentation">
      <section class="modal ${size}" role="dialog" aria-modal="true" aria-label="${escapeHtml(title)}">
        <header class="modal-header"><h2>${escapeHtml(title)}</h2><button class="modal-close" aria-label="Close">X</button></header>
        <div class="modal-body">${body}</div>
        ${footer ? `<footer class="modal-footer">${footer}</footer>` : ""}
      </section>
    </div>`;
}

function openModal({title, body, footer = "", size = "", onClose = null}) {
  closeModal(false);
  const host = $("#modalHost");
  host.innerHTML = modalTemplate(title, body, footer, size);
  const backdrop = $(".modal-backdrop", host);
  ui.activeModal = {element: backdrop, onClose};
  $(".modal-close", backdrop).addEventListener("click", () => closeModal(true));
  backdrop.addEventListener("mousedown", event => {
    if (event.target === backdrop) closeModal(true);
  });
  return backdrop;
}

function closeModal(runCallback = true) {
  if (!ui.activeModal) return;
  const active = ui.activeModal;
  ui.activeModal = null;
  active.element.remove();
  if (runCallback && active.onClose) active.onClose();
}

function confirmAction(title, message, confirmLabel, onConfirm) {
  const modal = openModal({
    title,
    body: `<p class="confirm-copy">${escapeHtml(message)}</p>`,
    footer: `<button class="button" data-modal-cancel>Cancel</button><button class="button danger" data-modal-confirm>${escapeHtml(confirmLabel)}</button>`,
  });
  $("[data-modal-cancel]", modal).addEventListener("click", () => closeModal(false));
  $("[data-modal-confirm]", modal).addEventListener("click", async () => {
    closeModal(false);
    await onConfirm();
  });
}

function openDetailsModal(job) {
  if (!job) {
    showToast("Select exactly one game first.", "warning");
    return;
  }
  const body = `
    <form id="detailsForm" class="modal-form two-columns">
      <label class="span-two">Game Name<textarea name="name" rows="3" required>${escapeHtml(job.name)}</textarea></label>
      <label>Release year<input name="year" type="text" value="${escapeHtml(job.year)}"></label>
      <label>Players<input name="players" type="text" value="${escapeHtml(job.players)}"></label>
      <label>Product code<input name="product_code" type="text" value="${escapeHtml(job.product_code)}"></label>
      <label>Region<input name="region" type="text" value="${escapeHtml(job.region)}"></label>
      <label>Publisher<input name="publisher" type="text" value="${escapeHtml(job.publisher)}"></label>
      <label>Developer<input name="developer" type="text" value="${escapeHtml(job.developer)}"></label>
      <div class="info-box span-two">Manual line breaks in Game Name are preserved up to three lines. Changing the name or product code clears retained repository assignments so new matches can be checked.</div>
    </form>`;
  const modal = openModal({
    title: "Edit Game Details",
    body,
    footer: '<button class="button" data-modal-cancel>Cancel</button><button class="button primary" data-modal-save>Save and Rebuild</button>',
  });
  $("[data-modal-cancel]", modal).addEventListener("click", () => closeModal(false));
  $("[data-modal-save]", modal).addEventListener("click", async () => {
    const form = $("#detailsForm", modal);
    if (!form.reportValidity()) return;
    const values = Object.fromEntries(new FormData(form).entries());
    const result = await invoke("update_game_details", [job.id, values], {refresh: true});
    if (result) closeModal(false);
  });
}

function framingGroupMarkup(prefix, label, framing) {
  const fill = framing[`${prefix}_fill_mode`];
  const color = framing[`${prefix}_fill_color`];
  return `
    <section class="framing-group" data-framing-group="${prefix}">
      <h3>${escapeHtml(label)}</h3>
      <div class="range-row"><label>Horizontal focus</label><input type="range" min="0" max="100" value="${framing[`${prefix}_x`]}" data-frame-field="${prefix}_x"><span class="range-value">${framing[`${prefix}_x`]}%</span></div>
      <div class="range-row"><label>Vertical focus</label><input type="range" min="0" max="100" value="${framing[`${prefix}_y`]}" data-frame-field="${prefix}_y"><span class="range-value">${framing[`${prefix}_y`]}%</span></div>
      <div class="range-row"><label>Zoom</label><input type="range" min="25" max="300" value="${framing[`${prefix}_zoom`]}" data-frame-field="${prefix}_zoom"><span class="range-value">${framing[`${prefix}_zoom`]}%</span></div>
      <label class="field-label">Fill when zoomed out
        <select class="select-control" data-frame-field="${prefix}_fill_mode">
          <option ${fill === "Black" ? "selected" : ""}>Black</option>
          <option ${fill === "Blurred Image" ? "selected" : ""}>Blurred Image</option>
          <option ${fill === "Solid Color" ? "selected" : ""}>Solid Color</option>
        </select>
      </label>
      <label class="field-label">Fill color<input type="color" value="${escapeHtml(color)}" data-frame-field="${prefix}_fill_color"></label>
    </section>`;
}

function readFramingValues(modal) {
  const values = {};
  for (const input of $$('[data-frame-field]', modal)) {
    const key = input.dataset.frameField;
    values[key] = input.type === "range" ? Number(input.value) : input.value;
  }
  values.source_area_selection = $("#modalSourceArea", modal).value;
  return values;
}

function openFramingModal(job) {
  if (!job) return showToast("Select exactly one game first.", "warning");
  const original = JSON.parse(JSON.stringify(job.framing));
  let liveTimer = null;
  let liveUpdateActive = false;
  let liveUpdateQueued = false;
  let activeUpdatePromise = null;
  let closing = false;
  const body = `
    <div class="framing-layout">
      <nav class="framing-nav">
        <button class="active" data-frame-tab="icon">Icon Image</button>
        <button data-frame-tab="tv">TV Image</button>
        <button data-frame-tab="gamepad">Gamepad Image</button>
        <label class="check-row"><input id="linkBanners" type="checkbox" checked>Link TV and Gamepad</label>
      </nav>
      <div>
        <label class="field-label">Source image area
          <select id="modalSourceArea" class="select-control">
            <option value="auto">Automatic</option><option value="full">Full Image</option><option value="top">Top</option>
            <option value="bottom">Bottom</option><option value="left">Left</option><option value="right">Right</option>
          </select>
        </label>
        ${framingGroupMarkup("icon", "Icon Image", job.framing)}
        ${framingGroupMarkup("tv", "TV Image", job.framing)}
        ${framingGroupMarkup("gamepad", "Gamepad Image", job.framing)}
        <div id="framingLiveStatus" class="framing-live-status" aria-live="polite">Preview is current.</div>
        <div class="info-box">Changes rebuild the selected game's downloaded, captured, or imported artwork in real time. Zoom below 100 percent reveals the selected background fill.</div>
      </div>
    </div>`;
  let committed = false;
  const modal = openModal({
    title: "Adjust Image Crop & Zoom",
    body,
    footer: '<button class="button" data-frame-revert>Cancel and Restore</button><button class="button" data-frame-reset>Reset All</button><button class="button primary" data-frame-done>Done</button>',
    size: "framing",
    onClose: () => {
      closing = true;
      liveUpdateQueued = false;
      clearTimeout(liveTimer);
      if (!committed) {
        void (async () => {
          if (activeUpdatePromise) await activeUpdatePromise;
          await invoke("update_framing", [job.id, original, true], {refresh: true});
        })();
      }
    },
  });
  const status = $("#framingLiveStatus", modal);
  const footerButtons = $$('[data-frame-revert], [data-frame-reset], [data-frame-done]', modal);
  const setStatus = (message, state = "") => {
    if (!status.isConnected) return;
    status.textContent = message;
    status.className = `framing-live-status ${state}`.trim();
  };
  const setFooterDisabled = disabled => {
    for (const button of footerButtons) button.disabled = disabled;
  };
  $("#modalSourceArea", modal).value = job.framing.source_area_selection;
  const groups = $$("[data-framing-group]", modal);
  const showGroup = name => {
    for (const group of groups) group.classList.toggle("hidden", group.dataset.framingGroup !== name);
    for (const button of $$('[data-frame-tab]', modal)) button.classList.toggle("active", button.dataset.frameTab === name);
  };
  showGroup("icon");
  for (const button of $$('[data-frame-tab]', modal)) button.addEventListener("click", () => showGroup(button.dataset.frameTab));

  const applyLinkedValue = changedInput => {
    if (changedInput && $("#linkBanners", modal).checked) {
      const field = changedInput.dataset.frameField || "";
      const match = field.match(/^(tv|gamepad)_(.+)$/);
      if (match) {
        const other = match[1] === "tv" ? "gamepad" : "tv";
        const counterpart = $(`[data-frame-field="${other}_${match[2]}"]`, modal);
        if (counterpart) {
          counterpart.value = changedInput.value;
          if (counterpart.type === "range") counterpart.nextElementSibling.textContent = `${counterpart.value}%`;
        }
      }
    }
  };

  const runLiveUpdate = async () => {
    clearTimeout(liveTimer);
    liveTimer = null;
    if (closing) return;
    if (liveUpdateActive) {
      liveUpdateQueued = true;
      return;
    }
    liveUpdateActive = true;
    liveUpdateQueued = false;
    const values = readFramingValues(modal);
    setStatus("Updating preview...", "busy");
    activeUpdatePromise = (async () => {
      const result = await invoke("update_framing", [job.id, values, true], {refresh: true});
      if (result) loadMainPreviews(true);
      return result;
    })();
    const result = await activeUpdatePromise;
    activeUpdatePromise = null;
    liveUpdateActive = false;
    if (closing) return;
    if (liveUpdateQueued) {
      clearTimeout(liveTimer);
      setStatus("Applying the latest change...", "pending");
      liveTimer = setTimeout(runLiveUpdate, 70);
    } else if (result) {
      setStatus("Preview updated.", "success");
    } else {
      setStatus("Preview update failed.", "error");
    }
  };

  const scheduleLive = (changedInput, immediate = false) => {
    applyLinkedValue(changedInput);
    liveUpdateQueued = true;
    clearTimeout(liveTimer);
    setStatus("Change pending...", "pending");
    liveTimer = setTimeout(runLiveUpdate, immediate ? 0 : 280);
  };

  const waitForLiveUpdate = async () => {
    while (activeUpdatePromise) await activeUpdatePromise;
  };

  for (const input of $$('[data-frame-field]', modal)) {
    if (input.type === "range") {
      input.addEventListener("input", () => {
        input.nextElementSibling.textContent = `${input.value}%`;
        scheduleLive(input, false);
      });
      input.addEventListener("change", () => scheduleLive(input, true));
    } else {
      input.addEventListener("change", () => scheduleLive(input, true));
    }
  }
  $("#modalSourceArea", modal).addEventListener("change", () => scheduleLive(null, true));
  $("[data-frame-revert]", modal).addEventListener("click", async () => {
    closing = true;
    liveUpdateQueued = false;
    clearTimeout(liveTimer);
    setFooterDisabled(true);
    setStatus("Restoring previous settings...", "busy");
    await waitForLiveUpdate();
    const result = await invoke("update_framing", [job.id, original, true]);
    if (result) {
      committed = true;
      closeModal(false);
    } else {
      closing = false;
      setFooterDisabled(false);
      setStatus("Previous settings could not be restored.", "error");
    }
  });
  $("[data-frame-reset]", modal).addEventListener("click", () => {
    $("#modalSourceArea", modal).value = "auto";
    for (const input of $$('[data-frame-field]', modal)) {
      const field = input.dataset.frameField || "";
      if (field.endsWith("_fill_mode")) input.value = "Black";
      else if (field.endsWith("_fill_color")) input.value = "#000000";
      else if (field.endsWith("_zoom")) input.value = "100";
      else input.value = "50";
      if (input.type === "range") input.nextElementSibling.textContent = `${input.value}%`;
    }
    scheduleLive(null, true);
  });
  $("[data-frame-done]", modal).addEventListener("click", async () => {
    closing = true;
    liveUpdateQueued = false;
    clearTimeout(liveTimer);
    setFooterDisabled(true);
    setStatus("Saving framing settings...", "busy");
    await waitForLiveUpdate();
    const result = await invoke("update_framing", [job.id, readFramingValues(modal), true]);
    if (result) {
      committed = true;
      closeModal(false);
    } else {
      closing = false;
      setFooterDisabled(false);
      setStatus("Framing settings could not be saved.", "error");
    }
  });
}

function openPlacementModal(job) {
  if (!job) return showToast("Select exactly one game first.", "warning");
  const placement = job.placement;
  const body = `
    <form id="placementForm" class="modal-form two-columns">
      <label>X position<input name="x" type="number" min="0" max="1280" value="${placement.x}"></label>
      <label>Y position<input name="y" type="number" min="0" max="720" value="${placement.y}"></label>
      <label>Width<input name="width" type="number" min="1" max="1280" value="${placement.width}"></label>
      <label>Height<input name="height" type="number" min="1" max="720" value="${placement.height}"></label>
      <div class="info-box span-two">This changes the destination rectangle on the 1280 x 720 TV template. Crop focus and zoom are controlled separately.</div>
    </form>`;
  const modal = openModal({
    title: "Template Placement and Size",
    body,
    footer: '<button class="button" data-modal-cancel>Cancel</button><button class="button" data-placement-reset>Console Default</button><button class="button primary" data-modal-save>Save and Rebuild</button>',
  });
  $("[data-modal-cancel]", modal).addEventListener("click", () => closeModal(false));
  $("[data-placement-reset]", modal).addEventListener("click", async () => {
    const result = await invoke("reset_placement", [job.id]);
    if (result) closeModal(false);
  });
  $("[data-modal-save]", modal).addEventListener("click", async () => {
    const form = $("#placementForm", modal);
    if (!form.reportValidity()) return;
    const values = Object.fromEntries(new FormData(form).entries());
    const result = await invoke("update_placement", [job.id, values]);
    if (result) closeModal(false);
  });
}

async function openLargePreview(job, key) {
  if (!job || !job.preview_available[key]) return;
  try {
    await callApi("open_preview_window", job.id, key);
    return;
  } catch (error) {
    console.warn("Detached preview was unavailable; using the in-window preview.", error);
  }
  const titles = {title: "Title Screen", icon: "Icon Image", tv: "TV Image", gamepad: "Gamepad Image"};
  const modal = openModal({
    title: titles[key],
    body: '<div class="large-image-stage"><span class="preview-placeholder">Loading preview...</span><img class="hidden" alt=""></div>',
    size: "extra-large preview-modal",
  });
  try {
    const result = await callApi("get_preview", job.id, key, 3840, 2160);
    const image = $("img", modal);
    image.src = result.data_url;
    image.alt = `${titles[key]} preview`;
    image.classList.remove("hidden");
    $(".preview-placeholder", modal).classList.add("hidden");
  } catch (error) {
    closeModal(false);
    showToast(error.message, "error", "Preview unavailable");
  }
}

async function loadArtworkReviewPreview(modal, job, index, requestedAsset = null) {
  const match = job.artwork_matches[index];
  if (!match) return;
  for (const item of $$('[data-art-index]', modal)) item.classList.toggle("active", Number(item.dataset.artIndex) === index);
  const assets = match.assets.length ? match.assets : ["source"];
  const asset = requestedAsset && assets.includes(requestedAsset) ? requestedAsset : assets[0];
  const tabs = $(".asset-tabs", modal);
  const labels = {source: "Source", icon: "Icon", tv: "TV", gamepad: "Gamepad"};
  tabs.innerHTML = assets.map(value => `<button data-art-asset="${value}" class="${value === asset ? "active" : ""}">${labels[value]}</button>`).join("");
  for (const button of $$('[data-art-asset]', tabs)) {
    button.addEventListener("click", () => loadArtworkReviewPreview(modal, job, index, button.dataset.artAsset));
  }
  $(".review-details", modal).innerHTML = `
    <span>Source: ${escapeHtml(match.provider)}</span><span>Match: ${escapeHtml(match.match_method)}</span>
    <span>Title: ${escapeHtml(match.matched_title || "Not applicable")}</span><span>Confidence: ${Math.round(match.confidence * 100)}%</span>
    <span>Type: ${escapeHtml(match.source_kind)}</span><span>Code: ${escapeHtml(match.product_code || "Not available")}</span>`;
  const preview = $(".review-preview", modal);
  preview.innerHTML = '<span class="preview-placeholder">Loading...</span>';
  try {
    const result = await callApi("get_artwork_preview", job.id, index, asset, 1120, 720);
    preview.innerHTML = `<img src="${result.data_url}" alt="Artwork match preview">`;
  } catch (error) {
    preview.innerHTML = `<span class="preview-placeholder">${escapeHtml(error.message)}</span>`;
  }
  for (const button of $$('[data-assign-art]', modal)) {
    const assignment = button.dataset.assignArt;
    button.disabled = assignment === "icon"
      ? !match.available_outputs.includes("Icon")
      : assignment === "tv_gamepad"
        ? !match.available_outputs.some(value => ["TV", "Gamepad"].includes(value))
        : false;
    button.onclick = async () => {
      const result = await invoke("assign_artwork", [job.id, index, assignment]);
      if (result) {
        closeModal(false);
        const updated = singleJob();
        if (updated) openArtworkReview(updated);
      }
    };
  }
}

function openArtworkReview(job) {
  if (!job) return showToast("Select exactly one game first.", "warning");
  if (!job.artwork_matches.length) return showToast("No retained artwork matches are available. Search artwork sources first.", "warning");
  const body = `
    <div class="review-layout">
      <div class="review-list">
        ${job.artwork_matches.map(match => `
          <button class="review-item" data-art-index="${match.index}">
            <strong>${escapeHtml(match.provider)}</strong>
            <span>${escapeHtml(match.source_kind)} - ${escapeHtml(match.matched_title || match.product_code || "Repository match")}</span>
            <span>${escapeHtml(match.match_method)} - ${Math.round(match.confidence * 100)}%</span>
            <span class="assignment-markers">
              ${match.assigned_icon ? '<span class="assignment-marker">Icon assigned</span>' : ""}
              ${match.assigned_tv_gamepad ? '<span class="assignment-marker">TV/Gamepad assigned</span>' : ""}
            </span>
          </button>`).join("")}
      </div>
      <div class="review-main">
        <div class="asset-tabs"></div>
        <div class="review-preview"></div>
        <div class="review-details"></div>
      </div>
    </div>`;
  const footer = `
    <button class="button" data-modal-close>Done</button>
    <button class="button" data-assign-art="icon">Use for Icon Image</button>
    <button class="button" data-assign-art="tv_gamepad">Use for TV/Gamepad</button>
    <button class="button primary" data-assign-art="all">Use for All Outputs</button>`;
  const modal = openModal({title: `Artwork Matches - ${job.name}`, body, footer, size: "extra-large"});
  $("[data-modal-close]", modal).addEventListener("click", () => closeModal(false));
  for (const item of $$('[data-art-index]', modal)) {
    item.addEventListener("click", () => loadArtworkReviewPreview(modal, job, Number(item.dataset.artIndex)));
  }
  loadArtworkReviewPreview(modal, job, 0);
}

async function loadCapturePreview(modal, job, index) {
  for (const item of $$('[data-capture-index]', modal)) item.classList.toggle("active", Number(item.dataset.captureIndex) === index);
  const candidate = job.candidates[index];
  const preview = $(".review-preview", modal);
  preview.innerHTML = '<span class="preview-placeholder">Loading...</span>';
  $(".review-details", modal).innerHTML = `
    <span>Time: ${candidate.timestamp.toFixed(2)} seconds</span><span>Score: ${candidate.score.toFixed(3)}</span>
    <span>Screen: ${escapeHtml(candidate.screen_label || "Full frame")}</span><span>${candidate.selected ? "Currently selected" : "Alternative"}</span>`;
  try {
    const result = await callApi("get_candidate_preview", job.id, index, 1120, 720);
    preview.innerHTML = `<img src="${result.data_url}" alt="Captured frame preview">`;
  } catch (error) {
    preview.innerHTML = `<span class="preview-placeholder">${escapeHtml(error.message)}</span>`;
  }
  $("[data-use-capture]", modal).onclick = async () => {
    const result = await invoke("choose_candidate", [job.id, index]);
    if (result) closeModal(false);
  };
}

function openCaptureReview(job) {
  if (!job) return showToast("Select exactly one game first.", "warning");
  if (!job.candidates.length) return showToast("No retained captured frames are available for this game.", "warning");
  const body = `
    <div class="review-layout">
      <div class="review-list">
        ${job.candidates.map(candidate => `
          <button class="review-item" data-capture-index="${candidate.index}">
            <strong>Candidate ${candidate.index + 1}${candidate.selected ? " - Selected" : ""}</strong>
            <span>${candidate.timestamp.toFixed(2)} seconds - Score ${candidate.score.toFixed(3)}</span>
            <span>${escapeHtml(candidate.screen_label || "Full frame")}</span>
          </button>`).join("")}
      </div>
      <div class="review-main">
        <div></div><div class="review-preview"></div><div class="review-details"></div>
      </div>
    </div>`;
  const modal = openModal({
    title: `Captured Frames - ${job.name}`,
    body,
    footer: '<button class="button" data-modal-close>Done</button><button class="button primary" data-use-capture>Use This Frame and Re-export</button>',
    size: "extra-large",
  });
  $("[data-modal-close]", modal).addEventListener("click", () => closeModal(false));
  for (const item of $$('[data-capture-index]', modal)) item.addEventListener("click", () => loadCapturePreview(modal, job, Number(item.dataset.captureIndex)));
  const selectedIndex = Math.max(0, job.candidates.findIndex(candidate => candidate.selected));
  loadCapturePreview(modal, job, selectedIndex);
}

function settingsMarkup(settings) {
  return `
    <form id="settingsForm" class="modal-form two-columns">
      <label class="span-two">Export folder<input name="output_root" type="text" value="${escapeHtml(settings.output_root)}"></label>
      <label class="span-two">RetroArch.exe<input name="retroarch_path" type="text" value="${escapeHtml(settings.retroarch_path)}"></label>
      <label class="span-two">Dolphin.exe fallback<input name="dolphin_path" type="text" value="${escapeHtml(settings.dolphin_path)}"></label>
      <div class="span-two field-with-action"><label>Artwork font<input name="uwuvci_font_path" type="text" value="${escapeHtml(settings.uwuvci_font_path)}"></label><button class="button" type="button" data-settings-font>Browse</button></div>
      <fieldset class="fieldset span-two">
        <legend>Repository artwork</legend>
        <label class="check-row"><input name="automatic_repository_artwork" type="checkbox" ${settings.automatic_repository_artwork ? "checked" : ""}>Automatic artwork and sound search</label>
        <label class="check-row"><input name="repository_title_fallback" type="checkbox" ${settings.repository_title_fallback ? "checked" : ""}>Allow title and metadata fallbacks</label>
        <label class="check-row"><input name="repository_uncertain_matches" type="checkbox" ${settings.repository_uncertain_matches ? "checked" : ""}>Retain possible matches for review</label>
        <label class="check-row"><input name="libretro_artwork_enabled" type="checkbox" ${settings.libretro_artwork_enabled ? "checked" : ""}>Search Libretro Thumbnails</label>
        <label class="check-row"><input name="gamecube_artwork_enabled" type="checkbox" ${settings.gamecube_artwork_enabled ? "checked" : ""}>Search GameCube artwork collection</label>
      </fieldset>
      <fieldset class="fieldset span-two">
        <legend>Activity logging</legend>
        <label class="check-row"><input name="activity_logging_enabled" type="checkbox" ${settings.activity_logging_enabled ? "checked" : ""}>Write activity to a session log file</label>
        <div class="field-with-action"><label>Log folder<input name="activity_log_dir" type="text" value="${escapeHtml(settings.activity_log_dir)}"></label><button class="button" type="button" data-settings-log-folder>Browse</button></div>
      </fieldset>
      <div class="info-box span-two">Configuration folder: ${escapeHtml(ui.state.configuration_folder)}</div>
    </form>`;
}

function openSettingsModal() {
  const modal = openModal({
    title: "Settings",
    body: settingsMarkup(ui.state.settings),
    footer: `
      <button class="button" data-settings-config>Change Config Folder</button>
      <button class="button" data-settings-default-config>Default Config Folder</button>
      <button class="button" data-settings-cache>Open Download Cache</button>
      <button class="button" data-settings-database>Update Metadata Database</button>
      <button class="button" data-modal-cancel>Cancel</button>
      <button class="button primary" data-settings-save>Save</button>`,
    size: "large",
  });
  $("[data-modal-cancel]", modal).addEventListener("click", () => closeModal(false));
  $("[data-settings-config]", modal).addEventListener("click", () => invoke("choose_configuration_folder", []));
  $("[data-settings-default-config]", modal).addEventListener("click", () => invoke("use_default_configuration_folder", []));
  $("[data-settings-cache]", modal).addEventListener("click", () => invoke("open_repository_cache", [], {refresh: false}));
  $("[data-settings-database]", modal).addEventListener("click", () => invoke("update_metadata_database", []));
  $("[data-settings-font]", modal).addEventListener("click", async () => {
    const result = await invoke("pick_artwork_font", [], {refresh: false});
    if (result?.path) $('[name="uwuvci_font_path"]', modal).value = result.path;
  });
  $("[data-settings-log-folder]", modal).addEventListener("click", async () => {
    const result = await invoke("pick_activity_log_folder", [], {refresh: false});
    if (result?.path) {
      $('[name="activity_log_dir"]', modal).value = result.path;
      $('[name="activity_logging_enabled"]', modal).checked = true;
    }
  });
  $("[data-settings-save]", modal).addEventListener("click", async () => {
    const form = $("#settingsForm", modal);
    const data = Object.fromEntries(new FormData(form).entries());
    for (const checkbox of $$('input[type="checkbox"]', form)) data[checkbox.name] = checkbox.checked;
    const result = await invoke("update_settings", [data]);
    if (result) closeModal(false);
  });
}

async function showHelpTopic(modal, title) {
  for (const button of $$('[data-help-topic]', modal)) button.classList.toggle("active", button.dataset.helpTopic === title);
  const documentView = $(".help-document", modal);
  documentView.innerHTML = `<h2>${escapeHtml(title)}</h2><span class="preview-placeholder">Loading...</span>`;
  try {
    const result = await callApi("get_help_topic", title);
    documentView.innerHTML = `<h2>${escapeHtml(result.title)}</h2>${escapeHtml(result.body)}`;
  } catch (error) {
    documentView.innerHTML = `<h2>${escapeHtml(title)}</h2>${escapeHtml(error.message)}`;
  }
}

function openHelpModal(initial = "Getting Started") {
  const topics = ui.state.help_topics || [];
  const body = `
    <div class="help-layout">
      <nav class="help-nav">${topics.map(title => `<button data-help-topic="${escapeHtml(title)}">${escapeHtml(title)}</button>`).join("")}</nav>
      <article class="help-document"></article>
    </div>`;
  const modal = openModal({title: "Documentation Center", body, footer: '<button class="button primary" data-modal-close>Done</button>', size: "extra-large"});
  $("[data-modal-close]", modal).addEventListener("click", () => closeModal(false));
  for (const button of $$('[data-help-topic]', modal)) button.addEventListener("click", () => showHelpTopic(modal, button.dataset.helpTopic));
  showHelpTopic(modal, topics.includes(initial) ? initial : topics[0]);
}

function saveCaptureSettings() {
  clearTimeout(ui.settingsTimer);
  ui.settingsTimer = setTimeout(() => {
    invoke("update_settings", [{
      warmup_seconds: Number($("#warmupInput").value),
      duration_seconds: Number($("#durationInput").value),
      interval_seconds: Number($("#intervalInput").value),
      candidate_count: Number($("#candidateInput").value),
      screen_crop: $("#captureCropSelect").value,
      create_icon_image: $("#makeIconCheck").checked,
      create_tv_image: $("#makeTvCheck").checked,
      create_gamepad_image: $("#makeGamepadCheck").checked,
      include_banner_metadata: $("#overlayCheck").checked,
      include_game_title: $("#titleOverlayCheck").checked,
      include_release_date: $("#releaseOverlayCheck").checked,
      include_player_count: $("#playersOverlayCheck").checked,
    }]);
  }, 220);
}

async function handleAction(action) {
  const job = singleJob();
  switch (action) {
    case "add-roms": return invoke("choose_add_roms");
    case "add-folder": return invoke("choose_add_folder");
    case "choose-retro": return invoke("choose_retroarch");
    case "choose-dolphin": return invoke("choose_dolphin");
    case "choose-output": return invoke("choose_output_folder");
    case "import-screenshot": return job && invoke("choose_import_screenshot", [job.id]);
    case "import-banner": return job && invoke("choose_import_banner", [job.id]);
    case "fetch-metadata": return invoke("fetch_metadata", [ui.state.selected_ids]);
    case "search-artwork": return job && invoke("search_artwork", [job.id]);
    case "build-images": return invoke("build_images", [ui.state.selected_ids]);
    case "toggle-selected": return invoke("toggle_selected_capture");
    case "check-all": return invoke("check_all");
    case "clear-checks": return invoke("clear_checks");
    case "remove-checked": {
      const count = ui.state.jobs.filter(item => item.checked).length;
      if (!count) return;
      return confirmAction("Remove checked ROMs", `Remove ${count} checked ROM row(s) from the list? Source ROMs and generated files will not be deleted.`, "Remove Checked", () => invoke("remove_checked"));
    }
    case "edit-details": return openDetailsModal(job);
    case "open-framing": return openFramingModal(job);
    case "open-placement": return openPlacementModal(job);
    case "review-artwork": return openArtworkReview(job);
    case "review-captures": return openCaptureReview(job);
    case "clear-artwork": return job && invoke("clear_repository_artwork", [job.id]);
    case "open-output": return job && invoke("open_output", [job.id], {refresh: false});
    case "open-metadata": return job && invoke("open_metadata", [job.id], {refresh: false});
    case "start-capture": return invoke("start_capture");
    case "stop-capture": return invoke("stop_capture");
    case "browse-sound": return job && invoke("choose_game_boot_sound", [job.id]);
    case "set-console-sound": return job && invoke("choose_console_boot_sound", [job.id]);
    case "search-sounds": return job && invoke("search_boot_sounds", [job.id]);
    case "more-sounds": return job && invoke("open_more_sounds", [job.id], {refresh: false});
    case "clear-sound": return job && invoke("clear_boot_sound", [job.id]);
    case "play-sound": return playSound(job);
    case "stop-sound": return stopSound();
    case "open-settings": return openSettingsModal();
    case "open-help": return openHelpModal();
    case "clear-activity": return invoke("clear_activity");
    case "detach-preview": return ui.state.preview_detached
      ? invoke("reattach_preview_panel")
      : invoke("open_preview_panel_window");
    case "detach-activity": return invoke("open_activity_window");
    case "toggle-activity": {
      ui.activityCollapsed = !ui.activityCollapsed;
      $("#activityPanel").classList.toggle("collapsed", ui.activityCollapsed);
      $('[data-action="toggle-activity"]').textContent = ui.activityCollapsed ? "Show" : "Hide";
      return;
    }
  }
}

function bindWorkspaceSplitter() {
  const grid = $(".workspace-grid");
  const splitter = $("#workspaceSplitter");
  if (!grid || !splitter) return;
  let startX = 0;
  let startWidth = 0;

  const setWidth = value => {
    const rect = grid.getBoundingClientRect();
    const compact = window.innerWidth <= 1220;
    const minimum = compact ? 400 : 430;
    const previewMinimum = compact ? 380 : 400;
    const splitterWidth = splitter.getBoundingClientRect().width || 8;
    const gap = Number.parseFloat(getComputedStyle(grid).columnGap) || 8;
    const max = Math.max(minimum, rect.width - previewMinimum - splitterWidth - gap * 2);
    const width = Math.max(minimum, Math.min(max, value));
    grid.style.setProperty("--controls-width", `${width}px`);
  };

  const stop = event => {
    if (!splitter.classList.contains("dragging")) return;
    splitter.classList.remove("dragging");
    try { splitter.releasePointerCapture(event.pointerId); } catch (_) { /* already released */ }
  };

  splitter.addEventListener("pointerdown", event => {
    if (window.innerWidth <= 980) return;
    startX = event.clientX;
    startWidth = $(".controls-panel").getBoundingClientRect().width;
    splitter.classList.add("dragging");
    splitter.setPointerCapture(event.pointerId);
    event.preventDefault();
  });
  splitter.addEventListener("pointermove", event => {
    if (!splitter.classList.contains("dragging")) return;
    setWidth(startWidth + event.clientX - startX);
  });
  splitter.addEventListener("pointerup", stop);
  splitter.addEventListener("pointercancel", stop);
  splitter.addEventListener("keydown", event => {
    if (!["ArrowLeft", "ArrowRight"].includes(event.key) || window.innerWidth <= 980) return;
    const current = $(".controls-panel").getBoundingClientRect().width;
    setWidth(current + (event.key === "ArrowRight" ? 24 : -24));
    event.preventDefault();
  });
  window.addEventListener("resize", () => {
    const configured = Number.parseFloat(grid.style.getPropertyValue("--controls-width"));
    if (Number.isFinite(configured) && window.innerWidth > 980) setWidth(configured);
  });
}

async function playSound(job) {
  if (!job) return showToast("Select exactly one game first.", "warning");
  try {
    const result = await callApi("get_boot_sound_audio", job.id);
    const player = $("#soundPlayer");
    player.pause();
    player.currentTime = 0;
    player.src = result.data_url;
    await player.play();
    showToast(`Playing ${result.label}`, "success", "Boot Sound");
  } catch (error) {
    showToast(error.message, "error", "Playback failed");
  }
}

function stopSound() {
  const player = $("#soundPlayer");
  player.pause();
  player.currentTime = 0;
}

function bindStaticEvents() {
  bindWorkspaceSplitter();
  document.addEventListener("click", event => {
    const action = event.target.closest("[data-action]");
    if (action && !action.disabled) handleAction(action.dataset.action);
  });
  for (const tab of $$(".panel-tab")) tab.addEventListener("click", () => setActivePanelTab(tab.dataset.tab));
  for (const tile of $$(".preview-tile")) tile.addEventListener("click", () => openLargePreview(singleJob(), tile.dataset.preview));

  $("#iconFrameSelect").addEventListener("change", event => {
    const job = singleJob();
    if (job) invoke("set_icon_template", [job.id, event.target.value]);
  });
  $("#sourceAreaSelect").addEventListener("change", event => {
    const job = singleJob();
    if (job) invoke("update_framing", [job.id, {...job.framing, source_area_selection: event.target.value}, true]);
  });
  $("#tvSourceSelect").addEventListener("change", event => {
    const job = singleJob();
    if (job) invoke("set_tv_gamepad_source", [job.id, event.target.value]);
  });
  $("#bootSoundSelect").addEventListener("change", event => {
    const job = singleJob();
    if (job && event.target.value) invoke("select_boot_sound", [job.id, event.target.value]);
  });

  for (const input of $$("#warmupInput, #durationInput, #intervalInput, #candidateInput")) input.addEventListener("change", saveCaptureSettings);
  for (const input of $$("#captureCropSelect, #makeIconCheck, #makeTvCheck, #makeGamepadCheck, #overlayCheck, #titleOverlayCheck, #releaseOverlayCheck, #playersOverlayCheck")) input.addEventListener("change", saveCaptureSettings);

  document.addEventListener("keydown", event => {
    if (event.key === "Escape" && ui.activeModal) {
      event.preventDefault();
      closeModal(true);
      return;
    }
    if (event.key === "F1") {
      event.preventDefault();
      openHelpModal();
      return;
    }
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "o") {
      event.preventDefault();
      handleAction(event.shiftKey ? "add-folder" : "add-roms");
      return;
    }
    const active = document.activeElement;
    const editing = active && (active.matches("input, textarea, select") || active.isContentEditable);
    if (event.key === "Delete" && !ui.activeModal && !editing) handleAction("remove-checked");
  });

  document.addEventListener("dragenter", event => {
    event.preventDefault();
    ui.dragDepth += 1;
    document.body.classList.add("dragging");
  });
  document.addEventListener("dragover", event => event.preventDefault());
  document.addEventListener("dragleave", event => {
    event.preventDefault();
    ui.dragDepth = Math.max(0, ui.dragDepth - 1);
    if (!ui.dragDepth) document.body.classList.remove("dragging");
  });
  document.addEventListener("drop", event => {
    event.preventDefault();
    ui.dragDepth = 0;
    document.body.classList.remove("dragging");
    const paths = Array.from(event.dataTransfer.files || [])
      .map(file => file.pywebviewFullPath || file.path || "")
      .filter(Boolean);
    if (paths.length) invoke("add_dropped_paths", [paths]);
  });
}

function bindTooltips() {
  let timer = null;
  let active = null;
  let startPoint = null;
  const hide = () => {
    clearTimeout(timer);
    timer = null;
    if (active) active.remove();
    active = null;
    startPoint = null;
  };
  document.addEventListener("mouseover", event => {
    const target = event.target.closest("[title]");
    if (!target || !target.title) return;
    const text = target.title;
    target.dataset.tooltipText = text;
    target.removeAttribute("title");
    startPoint = {x: event.clientX, y: event.clientY};
    timer = setTimeout(() => {
      active = document.createElement("div");
      active.className = "tooltip";
      active.textContent = text;
      document.body.appendChild(active);
      const rect = active.getBoundingClientRect();
      active.style.left = `${Math.min(window.innerWidth - rect.width - 10, event.clientX + 14)}px`;
      active.style.top = `${Math.min(window.innerHeight - rect.height - 10, event.clientY + 18)}px`;
      setTimeout(hide, Math.max(5000, Math.min(11000, 4200 + text.length * 20)));
    }, 500);
  });
  document.addEventListener("mouseout", event => {
    const target = event.target.closest("[data-tooltip-text]");
    if (target && !target.contains(event.relatedTarget)) {
      target.title = target.dataset.tooltipText;
      delete target.dataset.tooltipText;
      hide();
    }
  });
  document.addEventListener("mousemove", event => {
    if (active && startPoint && (Math.abs(event.clientX - startPoint.x) >= 8 || Math.abs(event.clientY - startPoint.y) >= 8)) hide();
  });
  for (const eventName of ["mousedown", "wheel", "keydown", "focusout"]) document.addEventListener(eventName, hide, true);
}

window.openFramingFromDetachedPreview = () => {
  const job = singleJob();
  if (job) openFramingModal(job);
  else showToast("Select exactly one game first.", "warning");
};

async function startApp() {
  window.studioUpdates = new StudioUpdates(window, document, callApi);
  window.studioUpdates.start();
  bindStaticEvents();
  bindTooltips();
  try {
    const result = await callApi("bootstrap");
    applyState(result.state, true);
    const healthTimer = setInterval(async () => {
      if (document.visibilityState !== "visible") return;
      try {
        const ready = await callApi("report_ui_ready");
        if (ready.healthy) clearInterval(healthTimer);
      } catch (_) { /* Retry while bridge/native window becomes ready. */ }
    }, 700);
    setInterval(() => refreshState(false), 700);
  } catch (error) {
    showToast(error.message || String(error), "error", "Application failed to start");
  }
}

window.addEventListener("pywebviewready", startApp, {once: true});
