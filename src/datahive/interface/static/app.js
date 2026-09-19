// Plain JS, no build step, no framework. Talks to the same JSON API that
// backs `datahive list`/`validate`/`upload`/`delete`/`sync`.

const listEl = document.getElementById("list");
const detailEl = document.getElementById("detail");
const searchEl = document.getElementById("search");
const statusEl = document.getElementById("statusFilter");
const syncBtn = document.getElementById("syncBtn");
const profileBtn = document.getElementById("profileBtn");
const profileOverlay = document.getElementById("profileOverlay");
const profileBody = document.getElementById("profileBody");
const profileCloseBtn = document.getElementById("profileCloseBtn");
const statsBtn = document.getElementById("statsBtn");
const statsOverlay = document.getElementById("statsOverlay");
const statsBody = document.getElementById("statsBody");
const statsCloseBtn = document.getElementById("statsCloseBtn");
const taskOverlay = document.getElementById("taskOverlay");
const taskCloseBtn = document.getElementById("taskCloseBtn");
const failureHelpOverlay = document.getElementById("failureHelpOverlay");
const failureHelpCloseBtn = document.getElementById("failureHelpCloseBtn");
const toastContainer = document.getElementById("toastContainer");
const hubStatusEl = document.getElementById("hubStatus");
const bulkBar = document.getElementById("bulkBar");
const bulkCountEl = document.getElementById("bulkCount");
const bulkValidateBtn = document.getElementById("bulkValidateBtn");
const bulkUploadBtn = document.getElementById("bulkUploadBtn");
const bulkDeleteBtn = document.getElementById("bulkDeleteBtn");
const bulkClearBtn = document.getElementById("bulkClearBtn");
const themeToggleBtn = document.getElementById("themeToggleBtn");
const themeIconMoon = document.getElementById("themeIconMoon");
const themeIconSun = document.getElementById("themeIconSun");

// --- Theme toggle. index.html's inline <head> script already applied the
// stored/OS-preferred theme before first paint; this just wires the button
// and persists explicit choices. ---
const THEME_KEY = "datahive-theme";

function currentTheme() {
  return document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
}

function updateThemeIcon(theme) {
  themeIconMoon.hidden = theme === "dark";
  themeIconSun.hidden = theme !== "dark";
}

themeToggleBtn.addEventListener("click", () => {
  const next = currentTheme() === "dark" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", next);
  try { localStorage.setItem(THEME_KEY, next); } catch (e) { /* ignore */ }
  updateThemeIcon(next);
});

updateThemeIcon(currentTheme());

let selectedId = null;
// Bumped on every selectEpisode() call; a call whose token has since been
// superseded (e.g. the user clicked another episode before this one's
// fetches resolved) abandons instead of overwriting the newer render with
// stale data -- this was the "have to F5" bug: clicking through episodes
// quickly let an older, slower request finish last and silently clobber
// (or, on a hard error, just never repaint) the detail panel.
let selectEpisodeToken = 0;
let attachmentsCache = null;
const selectedEpisodes = new Set();

// Video playback preferences, persisted across episode switches for this
// page load (not per-viewer storage -- just in-memory module state).
let videoPlaybackRate = 1;
let videoLayoutCols = 1;
let currentVideoElements = [];

function stepVideos(frameDelta) {
  const frameDuration = 1.0 / 30.0;
  const first = currentVideoElements[0];
  if (!first) return;
  const target = Math.max(0, first.currentTime + frameDelta * frameDuration);
  currentVideoElements.forEach((v) => {
    v.pause();
    v.currentTime = target;
  });
}

function toggleVideosPlay() {
  const first = currentVideoElements[0];
  if (!first) return;
  if (first.paused) {
    currentVideoElements.forEach((v) => v.play().catch(() => {}));
  } else {
    currentVideoElements.forEach((v) => v.pause());
  }
}

let currentEpisodeList = [];

const hideAnnotatedChk = document.getElementById("hideAnnotatedChk");
const HIDE_ANNOTATED_KEY = "datahive-hide-annotated";
const AUTO_ADVANCE_KEY = "datahive-auto-advance";
const LAST_OPERATOR_KEY = "datahive-last-operator";
const LAST_ANNOTATOR_KEY = "datahive-last-annotator";

let isDirty = false;

function setDirty(val) {
  isDirty = !!val;
  const ind = document.getElementById("dirtyIndicator");
  if (ind) ind.hidden = !isDirty;
}

function confirmDiscardIfDirty() {
  if (!isDirty || !selectedId) return true;
  const ok = window.confirm("You have unsaved changes. Discard them and continue?");
  if (ok) setDirty(false);
  return ok;
}

window.addEventListener("beforeunload", (e) => {
  if (isDirty && selectedId) {
    e.preventDefault();
    e.returnValue = "";
  }
});

function isAutoAdvanceEnabled() {
  try {
    return localStorage.getItem(AUTO_ADVANCE_KEY) !== "0";
  } catch (_) {
    return true;
  }
}

if (hideAnnotatedChk) {
  try {
    hideAnnotatedChk.checked = localStorage.getItem(HIDE_ANNOTATED_KEY) === "1";
  } catch (_) {}
  hideAnnotatedChk.addEventListener("change", () => {
    try {
      localStorage.setItem(HIDE_ANNOTATED_KEY, hideAnnotatedChk.checked ? "1" : "0");
    } catch (_) {}
    refreshList();
  });
}

function findNextUnannotated(fromIdx) {
  if (!currentEpisodeList.length) return null;
  const start = fromIdx >= 0 ? fromIdx : -1;
  for (let i = start + 1; i < currentEpisodeList.length; i++) {
    const ep = currentEpisodeList[i];
    if (ep.status === "recorded") return ep.episode_id;
  }
  for (let i = 0; i <= start && i < currentEpisodeList.length; i++) {
    const ep = currentEpisodeList[i];
    if (ep.status === "recorded" && ep.episode_id !== selectedId) return ep.episode_id;
  }
  return null;
}

function updateNavButtons() {
  const prevBtn = document.getElementById("prevBtn");
  const nextBtn = document.getElementById("nextBtn");
  const nextUnBtn = document.getElementById("nextUnannotatedBtn");
  const nameEl = document.getElementById("sessionH5Name");
  const posEl = document.getElementById("episodePosition");
  if (!nameEl) return;

  const idx = currentEpisodeList.findIndex((e) => e.episode_id === selectedId);

  if (selectedId) {
    nameEl.textContent = selectedId;
    nameEl.classList.remove("placeholder");
    if (posEl) {
      posEl.textContent = (idx >= 0 && currentEpisodeList.length)
        ? `${idx + 1} / ${currentEpisodeList.length}`
        : "";
    }
    if (prevBtn) prevBtn.disabled = !(idx > 0);
    if (nextBtn) nextBtn.disabled = !(currentEpisodeList.length && idx >= 0 && idx < currentEpisodeList.length - 1);
  } else {
    nameEl.textContent = "No episode selected";
    nameEl.classList.add("placeholder");
    if (posEl) posEl.textContent = "";
    if (prevBtn) prevBtn.disabled = true;
    if (nextBtn) nextBtn.disabled = currentEpisodeList.length === 0;
  }

  if (nextUnBtn) {
    const nextUn = findNextUnannotated(idx);
    nextUnBtn.disabled = nextUn === null;
  }
}

async function navigateBy(delta) {
  if (!currentEpisodeList.length) return;
  if (!confirmDiscardIfDirty()) return;
  const idx = currentEpisodeList.findIndex((e) => e.episode_id === selectedId);
  let target = idx < 0 ? (delta > 0 ? 0 : currentEpisodeList.length - 1) : idx + delta;
  target = Math.max(0, Math.min(currentEpisodeList.length - 1, target));
  if (target >= 0 && target < currentEpisodeList.length) {
    const nextEp = currentEpisodeList[target];
    if (nextEp && nextEp.episode_id !== selectedId) {
      await selectEpisode(nextEp.episode_id);
    }
  }
}

async function navigateToNextUnannotated() {
  if (!currentEpisodeList.length) return false;
  if (!confirmDiscardIfDirty()) return false;
  const idx = currentEpisodeList.findIndex((e) => e.episode_id === selectedId);
  const nextId = findNextUnannotated(idx);
  if (nextId) {
    await selectEpisode(nextId);
    return true;
  }
  return false;
}

async function api(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    const text = await res.text();
    let message = text;
    try {
      const parsed = JSON.parse(text);
      message = parsed.detail || text;
    } catch (_) { /* not JSON, use raw text */ }
    throw new Error(message);
  }
  return res.json();
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

// Turns "safety_stop" into "Safety stop" -- used wherever an enum value
// (outcome, failure_cause, ...) is shown as a human-facing label. The
// underlying <option value="..."> keeps the raw lowercase value.
function humanize(value) {
  if (!value) return "";
  const s = String(value).replace(/_/g, " ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

// A field label with a red required-asterisk immediately after the text.
// Labels are flex columns (text above the input), so the text and the
// asterisk must be wrapped in one inline element -- otherwise the
// asterisk becomes its own flex child and drops onto its own line
// instead of sitting to the right of the word. For fields that are only
// conditionally required (Failure cause, Reason, Stage reached), this is
// only ever rendered while that field is also visible -- i.e. exactly
// when it's actually required.
function fieldLabel(text) {
  return `<span class="field-label-text">${text}<span class="required-mark" title="Required">*</span></span>`;
}

// A button-group ("segmented control") in place of a native <select>, for
// short enum fields where clicking an option beats picking from a
// dropdown. Renders a hidden input (so it's a normal form field on
// submit) plus one button per option; wireSegmentedControls() below
// handles the click-to-select behavior for every one of these in a form.
function segmentedControlHtml(name, options, current, titles = {}, allowDeselect = false) {
  const currentStr = current != null ? String(current) : "";
  const buttons = options.map((v) => {
    const tooltip = titles[v] ? ` data-tooltip="${escapeHtml(titles[v])}"` : "";
    return `<button type="button" class="segment${v === currentStr ? " active" : ""}" data-value="${v}"${tooltip}>${humanize(v)}</button>`;
  }).join("");
  const deselectAttr = allowDeselect ? ` data-allow-deselect="true"` : "";
  return `<input type="hidden" name="${name}" value="${currentStr}"><div class="segmented" data-name="${name}"${deselectAttr}>${buttons}</div>`;
}

// Shown on hover over each Outcome option -- what the evaluator actually
// checks to reach that outcome for a HiveBoard trial.
const OUTCOME_MEANINGS = {
  success: "The task's success criterion was fully met before the timeout.",
  fail: "The attempt ended without meeting the success criterion, before the timeout was reached.",
  timeout: "The time limit was reached before the success criterion could be met.",
  safety_stop: "The trial was stopped early by a safety event (e.g. a force/torque limit or emergency stop).",
};

// Shown on hover over each Strategy option.
const STRATEGY_MEANINGS = {
  prehensile: "Grasping or gripping the object with the end-effector (e.g. pinch, power grasp).",
  non_prehensile: "Manipulating without grasping (e.g. pushing, sliding, rolling, or poking).",
};

function wireSegmentedControls(form) {
  form.querySelectorAll(".segmented").forEach((group) => {
    group.addEventListener("click", (e) => {
      const btn = e.target.closest(".segment");
      if (!btn) return;
      const fieldName = group.dataset.name;
      const input = form.querySelector(`input[name="${fieldName}"]`) || form.elements[fieldName];
      const allowDeselect = group.dataset.allowDeselect === "true";
      const wasActive = btn.classList.contains("active");

      if (allowDeselect && wasActive) {
        if (input) input.value = "";
        btn.classList.remove("active");
      } else {
        if (input) input.value = btn.dataset.value;
        group.querySelectorAll(".segment").forEach((b) => b.classList.toggle("active", b === btn));
      }
      group.dispatchEvent(new Event("segmentchange", { bubbles: true }));
    });
  });
}

// Sets a segmented control's value programmatically (e.g. "Copy from
// previous") -- same active-state + hidden-input + segmentchange effects
// a real click would have, without needing to fake a click event.
function setSegmentedValue(form, name, value) {
  const group = form.querySelector(`.segmented[data-name="${name}"]`);
  if (!group) return;
  const v = value != null ? String(value) : "";
  const input = form.querySelector(`input[name="${name}"]`) || form.elements[name];
  if (input) input.value = v;
  group.querySelectorAll(".segment").forEach((b) => b.classList.toggle("active", b.dataset.value === v));
  group.dispatchEvent(new Event("segmentchange", { bubbles: true }));
}

// Stage definitions from HiveBoard documentation (tasks & evaluation-runner)
// for Composed Assembly tasks (multi-stage attachments).
const COMPOSED_TASK_STAGES = {
  button: ["Open cover", "Press button"],
  lock: ["Grasp key", "Insert key vertically", "Rotate to unlock"],
  drawer: ["Grasp handle", "Pull open", "Push closed"],
  shock_absorber: ["Grasp pin", "Align with hole", "Insert fully"],
};

function isComposedTask(info, taskId) {
  if (info && info.composed_assembly) return true;
  const tid = taskId || (info && (info.attachment_id || info.id));
  return Boolean(tid && COMPOSED_TASK_STAGES[tid]);
}

function getTaskStages(info, taskId) {
  if (info && info.stages && info.stages.length) return info.stages;
  const tid = taskId || (info && (info.attachment_id || info.id));
  if (tid && COMPOSED_TASK_STAGES[tid]) return COMPOSED_TASK_STAGES[tid];
  return null;
}

// "Last completed stage *" as a dropdown <select> menu of the selected composed
// task's stage names (0 = "0 — No stage completed") based on HiveBoard tasks protocol.
// Completion time can come from the manual stopwatch, the HDF5 recording or the video.
const COMPLETION_SOURCE_LABELS = { timer: "Timer", hdf5: "HDF5", video: "Video" };

function completionSourceHtml(sources, current) {
  const avail = Object.keys(COMPLETION_SOURCE_LABELS).filter((k) => sources[k] != null && sources[k] > 0);
  if (!avail.length) return `<input value="Not available yet" disabled>`;
  const chosen = avail.includes(current) ? current : avail[0];
  const buttons = avail.map((k) => `<button type="button" class="segment${k === chosen ? " active" : ""}" data-value="${k}" data-seconds="${sources[k]}">${COMPLETION_SOURCE_LABELS[k]} &middot; ${formatDuration(sources[k])}</button>`).join("");
  return `<input type="hidden" name="completion_source" value="${chosen}"><div class="segmented" data-name="completion_source">${buttons}</div>`;
}

// The Validate button reports the episode's state: not checked, valid, or not valid.
function validateStateOf(index) {
  const st = index && index.status;
  if (st === "validated" || st === "uploaded") return "is-valid";
  return index && index.last_error ? "is-invalid" : "";
}
function validateLabel(state) { return state === "is-valid" ? "Valid" : state === "is-invalid" ? "Not valid" : state === "is-busy" ? "Checking\u2026" : "Validate"; }
function validateIconHtml(state) {
  if (state === "is-invalid") return '<line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line>';
  if (state === "is-busy") return '<path d="M21 12a9 9 0 1 1-6.2-8.6"></path>';
  return '<polyline points="20 6 9 17 4 12"></polyline>';
}

function stageFieldHtml(info, current, outcome, taskId) {
  const stages = getTaskStages(info, taskId);
  let value = (current !== null && current !== undefined && current !== "") ? String(current) : "";
  if (value === "" && outcome === "success" && stages && stages.length) {
    value = String(stages.length);
  }

  const options = [
    `<option value="" disabled ${value === "" ? "selected" : ""}>– Select stage –</option>`,
    `<option value="0" ${value === "0" ? "selected" : ""}>0 — No stage completed</option>`,
  ];
  if (stages && stages.length) {
    stages.forEach((name, i) => {
      const v = String(i + 1);
      options.push(`<option value="${v}" ${value === v ? "selected" : ""}>${i + 1} — ${escapeHtml(name)}</option>`);
    });
  }
  // Only tasks with stages need it: a hidden required field would silently block Save.
  const needed = isComposedTask(info, taskId);
  return `<select name="stage_reached" ${needed ? "required" : "disabled"}>${options.join("")}</select>`;
}

function formatDuration(seconds) {
  if (seconds == null) return "–";
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `${m}m ${s}s`;
}

function formatRate(hz) {
  return hz == null ? "–" : `${hz.toFixed(0)} Hz`;
}

function formatSteps(n) {
  return n == null ? "–" : n.toLocaleString();
}

// --- Toasts (top-of-page popups for errors/successes) ---

const TOAST_ICONS = {
  error: '<circle cx="12" cy="12" r="10"></circle><line x1="12" y1="8" x2="12" y2="12"></line><line x1="12" y1="16" x2="12.01" y2="16"></line>',
  success: '<path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path><polyline points="22 4 12 14.01 9 11.01"></polyline>',
};

function showToast(message, { type = "error", title, timeout = 7000 } = {}) {
  const toast = document.createElement("div");
  toast.className = `toast ${type}`;
  const bodyHtml = escapeHtml(message).replace(/\n/g, "<br>");
  toast.innerHTML = `
    <svg class="toast-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${TOAST_ICONS[type] || TOAST_ICONS.error}</svg>
    <div class="toast-body">
      ${title ? `<div class="toast-title">${title}</div>` : ""}
      <div>${bodyHtml}</div>
    </div>
    <button class="toast-close" aria-label="Dismiss">✕</button>
  `;
  const remove = () => toast.remove();
  toast.querySelector(".toast-close").onclick = remove;
  toastContainer.appendChild(toast);
  if (timeout) setTimeout(remove, timeout);
  return toast;
}

async function loadAttachments() {
  if (!attachmentsCache) {
    attachmentsCache = await api("/api/attachments");
  }
  return attachmentsCache;
}

// --- Hub sync status badge (header) ---

async function refreshHubStatus() {
  let status;
  try {
    status = await api("/api/status");
  } catch (err) {
    hubStatusEl.className = "hub-status error";
    hubStatusEl.querySelector(".hub-status-text").textContent = "Status unavailable";
    hubStatusEl.title = err.message;
    return;
  }

  let cls, text, title;
  if (!status.configured) {
    cls = "not-configured";
    text = "Not connected";
    title = "No ~/.datahive/config.yaml yet -- run `datahive init`.";
  } else if (!status.connected) {
    cls = "error";
    text = "Sync error";
    title = status.error || "Could not reach the Hugging Face Hub.";
  } else if (status.pending_count > 0) {
    cls = "pending";
    text = `${status.pending_count} pending`;
    title = `${status.pending_count} episode(s) validated but not yet uploaded to ${status.repo_id}.`;
  } else {
    cls = "synced";
    text = "Synced";
    title = `${status.uploaded_count}/${status.total_count} episode(s) uploaded to ${status.repo_id}.`;
  }
  hubStatusEl.className = `hub-status ${cls}`;
  hubStatusEl.querySelector(".hub-status-text").textContent = text;
  hubStatusEl.title = title;
}

// --- Left-panel dataset summary (total / annotated / online) -- always
// counts the whole samples/ tree, independent of the active search/status
// filter, so it reads as "how much data do I have" rather than "how many
// results are showing". ---

async function renderListStats() {
  const el = document.getElementById("listStats");
  try {
    const all = await api("/api/episodes");
    const total = all.length;
    const annotated = all.filter((e) => !e.status.startsWith("recorded")).length;
    const online = all.filter((e) => e.status.startsWith("uploaded")).length;
    el.innerHTML = `
      <span><strong>${total}</strong> episode${total === 1 ? "" : "s"}</span>
      <span><strong>${annotated}</strong> annotated</span>
      <span><strong>${online}</strong> online</span>
    `;
  } catch (err) {
    el.innerHTML = "";
  }
}

// --- Task (attachment) picker: mirrors HiveBoard's Evaluation Runner
// task-selection grid (https://hiveboard-bench.github.io/hivedocs/benchmark/evaluation-runner). ---

function taskChipHtml(info, taskId) {
  if (!taskId) {
    return `<span class="task-chip-placeholder">Select a task…</span>`;
  }
  const name = info ? info.name : taskId;
  const family = info ? info.family : null;
  const img = info && info.image ? `<img src="/tasks/${info.image}" alt="">` : `<span class="task-chip-noimg"></span>`;
  return `
    ${img}
    <span class="task-chip-body">
      <strong>${escapeHtml(name)}</strong>
      ${family ? `<span>${escapeHtml(family)}${info.timeout ? ` · ${info.timeout}s` : ""}</span>` : ""}
    </span>
    <svg class="task-chip-edit" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path></svg>
  `;
}

function taskCardHtml(taskId, info, isCurrent, countLabel = "") {
  const img = info.image ? `<img src="/tasks/${info.image}" alt="">` : `<div class="task-card-noimg"></div>`;
  return `
    <button type="button" class="task-card${isCurrent ? " selected" : ""}" data-task-id="${taskId}">
      ${img}
      <span class="task-card-body">
        <strong>${escapeHtml(info.name)}</strong>
        ${info.composed_assembly ? `<span class="task-count">${(info.stages || []).length || info.n_stages || ""} stages</span>` : ""}
        ${countLabel ? `<span class="task-count">${countLabel}</span>` : ""}
      </span>
    </button>`;
}

// Family-grouped task cards, shared by the single-select picker (annotation),
// the multi-select picker (Runner setup) and the Runner's plan view.
function taskGroupsHtml(attachments, cardFn) {
  const families = [];
  const byFamily = new Map();
  Object.entries(attachments).forEach(([id, info]) => {
    const family = info.family || "Other";
    if (!byFamily.has(family)) {
      byFamily.set(family, []);
      families.push(family);
    }
    byFamily.get(family).push([id, info]);
  });
  return families.map((family) => `
    <div class="task-family-group">
      <h4>${escapeHtml(family)}</h4>
      <div class="task-grid">
        ${byFamily.get(family).map(([id, info]) => cardFn(id, info)).join("")}
      </div>
    </div>
  `).join("") || `<p class="empty-hint">No attachments in the registry.</p>`;
}

function setTaskOverlayTitle(text, intro) {
  const h = taskOverlay.querySelector(".overlay-header h2");
  if (h) h.textContent = text;
  const p = taskOverlay.querySelector(".profile-intro");
  if (p) {
    if (!p.dataset.original) p.dataset.original = p.innerHTML;
    p.innerHTML = intro || p.dataset.original;
  }
}

function openTaskPicker(attachments, currentId, onSelect) {
  const grid = document.getElementById("taskGrid");
  setTaskOverlayTitle("Select task");
  grid.innerHTML = taskGroupsHtml(attachments, (id, info) => taskCardHtml(id, info, id === currentId));

  grid.querySelectorAll(".task-card").forEach((card) => {
    card.addEventListener("click", () => {
      onSelect(card.dataset.taskId);
      taskOverlay.classList.add("hidden");
    });
  });

  taskOverlay.classList.remove("hidden");
}

// Same overlay and cards, but several tasks can be toggled before pressing Done.
function openTaskPickerMulti(attachments, selectedIds, onDone) {
  const grid = document.getElementById("taskGrid");
  const chosen = new Set(selectedIds);
  setTaskOverlayTitle("Select tasks", "Choose the tasks to include in this session. Click a card to add or remove it, then press Done.");
  grid.innerHTML = `
    <div class="task-multi-bar">
      <span id="taskMultiCount"></span>
      <span><button type="button" id="taskMultiAll">All</button> <button type="button" id="taskMultiNone">None</button>
      <button type="button" class="primary" id="taskMultiDone">Done</button></span>
    </div>` + taskGroupsHtml(attachments, (id, info) => taskCardHtml(id, info, chosen.has(id)));
  const cards = () => grid.querySelectorAll(".task-card");
  const refresh = () => {
    cards().forEach((c) => c.classList.toggle("selected", chosen.has(c.dataset.taskId)));
    document.getElementById("taskMultiCount").textContent = `${chosen.size} of ${Object.keys(attachments).length} selected`;
  };
  cards().forEach((card) => card.addEventListener("click", () => {
    const id = card.dataset.taskId;
    if (chosen.has(id)) chosen.delete(id); else chosen.add(id);
    refresh();
  }));
  document.getElementById("taskMultiAll").onclick = () => { Object.keys(attachments).forEach((id) => chosen.add(id)); refresh(); };
  document.getElementById("taskMultiNone").onclick = () => { chosen.clear(); refresh(); };
  document.getElementById("taskMultiDone").onclick = () => {
    taskOverlay.classList.add("hidden");
    onDone(Object.keys(attachments).filter((id) => chosen.has(id)));
  };
  refresh();
  taskOverlay.classList.remove("hidden");
}

// --- Episode list: grouped by day, with per-row selection checkboxes ---

function dayKey(date) {
  return `${date.getUTCFullYear()}-${date.getUTCMonth()}-${date.getUTCDate()}`;
}

function dayLabel(date) {
  const now = new Date();
  const yesterday = new Date(now);
  yesterday.setUTCDate(now.getUTCDate() - 1);
  if (dayKey(date) === dayKey(now)) return "Today";
  if (dayKey(date) === dayKey(yesterday)) return "Yesterday";
  const opts = { weekday: "short", month: "short", day: "numeric", timeZone: "UTC" };
  if (date.getUTCFullYear() !== now.getUTCFullYear()) opts.year = "numeric";
  return date.toLocaleDateString(undefined, opts);
}

function updateBulkBar() {
  const n = selectedEpisodes.size;
  bulkCountEl.textContent = `${n} selected`;
  const disabled = n === 0;
  bulkValidateBtn.disabled = disabled;
  bulkUploadBtn.disabled = disabled;
  bulkDeleteBtn.disabled = disabled;
  bulkClearBtn.disabled = disabled;
}

function toggleSelection(episodeId, checked) {
  if (checked) selectedEpisodes.add(episodeId);
  else selectedEpisodes.delete(episodeId);
  updateBulkBar();
}

// Which day groups are collapsed, keyed by dayKey(). Persists across
// refreshList() calls (in-memory, for this page load) so filtering/search
// doesn't fight the user's collapse state.
const collapsedDays = new Set();
let dayGroupsStartCollapsed = true; // all groups start minimized on first render only

function buildEpisodeRow(ep) {
  const row = document.createElement("div");
  row.className = "episode-row" + (ep.episode_id === selectedId ? " selected" : "");
  const badgeClass = (ep.status || "").split(" ")[0];
  const checked = selectedEpisodes.has(ep.episode_id) ? "checked" : "";
  const stepsLabel = ep.n_steps != null ? `${formatSteps(ep.n_steps)} steps` : null;
  const metaExtra = stepsLabel ? ` · ${stepsLabel}` : "";
  row.innerHTML = `
    <input type="checkbox" class="row-check" ${checked} aria-label="Select ${ep.episode_id}">
    <div class="row-main">
      <div class="eid">${ep.episode_id}</div>
      <div class="meta">${ep.session_id} · trial ${ep.trial_id || "?"}${metaExtra}
        <span class="badge ${badgeClass}">${humanize(ep.status)}</span>
        ${ep.missing && ep.missing.length ? `<span class="badge incomplete" title="Missing: ${escapeHtml(ep.missing.join(", "))}">Incomplete</span>` : ""}
      </div>
    </div>`;
  row.querySelector(".row-check").addEventListener("click", (e) => {
    e.stopPropagation();
    toggleSelection(ep.episode_id, e.target.checked);
  });
  row.querySelector(".row-main").onclick = () => {
    if (!confirmDiscardIfDirty()) return;
    selectEpisode(ep.episode_id);
  };
  return row;
}

async function refreshList() {
  const q = encodeURIComponent(searchEl.value || "");
  const status = encodeURIComponent(statusEl.value || "");
  let episodes = await api(`/api/episodes?q=${q}&status=${status}`);
  if (hideAnnotatedChk && hideAnnotatedChk.checked) {
    episodes = episodes.filter((e) => e.status !== "validated" && e.status !== "uploaded");
  }
  currentEpisodeList = episodes;

  if (selectedId) {
    const ep = episodes.find((e) => e.episode_id === selectedId);
    if (ep) {
      const created = ep.created_at ? new Date(ep.created_at) : null;
      const key = created ? dayKey(created) : "unknown";
      collapsedDays.delete(key);
    }
  }

  // Drop selections for episodes that fell out of the current view.
  const visibleIds = new Set(episodes.map((e) => e.episode_id));
  for (const id of Array.from(selectedEpisodes)) {
    if (!visibleIds.has(id)) selectedEpisodes.delete(id);
  }

  // Group in order of first appearance (episodes already arrive sorted
  // newest-recorded-first from the backend).
  const groups = [];
  const groupsByKey = new Map();
  for (const ep of episodes) {
    const created = ep.created_at ? new Date(ep.created_at) : null;
    const key = created ? dayKey(created) : "unknown";
    let group = groupsByKey.get(key);
    if (!group) {
      group = { key, label: created ? dayLabel(created) : "Unknown date", episodes: [] };
      groupsByKey.set(key, group);
      groups.push(group);
    }
    group.episodes.push(ep);
  }

  if (dayGroupsStartCollapsed) {
    groups.forEach((g) => collapsedDays.add(g.key));
    dayGroupsStartCollapsed = false; // only seed on the very first render
  }

  listEl.innerHTML = "";
  for (const group of groups) {
    const isCollapsed = collapsedDays.has(group.key);

    const groupEl = document.createElement("div");
    groupEl.className = "day-group";

    const groupIds = group.episodes.map((e) => e.episode_id);
    const selectedInGroup = groupIds.filter((id) => selectedEpisodes.has(id)).length;

    const header = document.createElement("div");
    header.className = "list-group-header" + (isCollapsed ? " collapsed" : "");
    header.innerHTML = `
      <input type="checkbox" class="day-select-all" aria-label="Select all episodes from ${escapeHtml(group.label)}">
      <svg class="chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"></polyline></svg>
      <span class="day-label">${group.label}</span>
      <span class="day-count">${group.episodes.length}</span>
    `;
    const dayCheckbox = header.querySelector(".day-select-all");
    dayCheckbox.checked = selectedInGroup > 0 && selectedInGroup === groupIds.length;
    dayCheckbox.indeterminate = selectedInGroup > 0 && selectedInGroup < groupIds.length;
    dayCheckbox.addEventListener("click", async (e) => {
      e.stopPropagation();
      const selectAll = dayCheckbox.checked;
      groupIds.forEach((id) => {
        if (selectAll) selectedEpisodes.add(id);
        else selectedEpisodes.delete(id);
      });
      await refreshList();
    });

    const rowsEl = document.createElement("div");
    rowsEl.className = "day-rows" + (isCollapsed ? " collapsed" : "");
    for (const ep of group.episodes) rowsEl.appendChild(buildEpisodeRow(ep));

    header.addEventListener("click", () => {
      const nowCollapsed = !rowsEl.classList.contains("collapsed");
      rowsEl.classList.toggle("collapsed", nowCollapsed);
      header.classList.toggle("collapsed", nowCollapsed);
      if (nowCollapsed) collapsedDays.add(group.key);
      else collapsedDays.delete(group.key);
    });

    groupEl.appendChild(header);
    groupEl.appendChild(rowsEl);
    listEl.appendChild(groupEl);
  }
  updateBulkBar();
  updateNavButtons();
}

bulkValidateBtn.addEventListener("click", async () => {
  const ids = Array.from(selectedEpisodes);
  if (!ids.length) return;
  bulkValidateBtn.disabled = true;
  try {
    const { results } = await api("/api/episodes/bulk-validate", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ episode_ids: ids }),
    });
    const succeeded = results.filter((r) => r.ok);
    const failed = results.filter((r) => !r.ok);
    if (succeeded.length) {
      showToast(`${succeeded.length} episode(s) are valid.`, { type: "success", title: "Bulk validate", timeout: 4000 });
    }
    if (failed.length) {
      const detail = failed.map((r) => `${r.episode_id}: ${r.error || "failed"}`).join("\n\n");
      showToast(detail, { type: "error", title: `${failed.length} episode(s) failed validation`, timeout: 14000 });
    }
  } catch (err) {
    showToast(err.message, { type: "error", title: "Bulk validate failed" });
  } finally {
    bulkValidateBtn.disabled = false;
    await refreshList();
  }
});

// Shows what is about to be uploaded (counts, size, problems, diversity) and
// resolves true only if the user confirms and nothing blocks the upload.
async function confirmUpload(ids) {
  const s = await api("/api/episodes/upload-preflight", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ episode_ids: ids }),
  });
  const mb = (s.total_bytes / (1024 * 1024)).toFixed(1);
  const stat = (n, label, bad) => `<div class="upload-stat ${bad && n ? "bad" : ""}"><b>${n}</b>${label}</div>`;
  const items = [];
  for (const ep of s.episodes) {
    ep.problems.forEach((p) => items.push(`<li class="problem">${escapeHtml(ep.episode_id)}: ${escapeHtml(p)}</li>`));
    ep.warnings.forEach((w) => items.push(`<li class="warn">${escapeHtml(ep.episode_id)}: ${escapeHtml(w)}</li>`));
  }
  s.directory_problems.forEach((p) => items.push(`<li class="problem">${escapeHtml(p)}</li>`));
  s.directory_warnings.forEach((w) => items.push(`<li class="warn">${escapeHtml(w)}</li>`));
  s.diversity.forEach((d) => items.push(`<li class="warn">Diversity: ${escapeHtml(d)}</li>`));
  document.getElementById("uploadSummary").innerHTML =
    `<div class="upload-stats">${stat(s.n_episodes, "episodes")}${stat(mb + " MB", "total size")}` +
    `${stat(s.n_unannotated, "unannotated", true)}${stat(s.n_warnings, "warnings")}</div>` +
    (items.length ? `<ul class="upload-issues">${items.join("")}</ul>` : "<p>No problems found.</p>");
  const overlay = document.getElementById("uploadOverlay");
  const confirmBtn = document.getElementById("uploadConfirmBtn");
  confirmBtn.disabled = s.directory_problems.length > 0;
  overlay.classList.remove("hidden");
  return new Promise((resolve) => {
    const done = (value) => {
      overlay.classList.add("hidden");
      confirmBtn.onclick = null;
      document.getElementById("uploadCancelBtn").onclick = null;
      resolve(value);
    };
    confirmBtn.onclick = () => done(true);
    document.getElementById("uploadCancelBtn").onclick = () => done(false);
  });
}

bulkUploadBtn.addEventListener("click", async () => {
  const ids = Array.from(selectedEpisodes);
  if (!ids.length) return;
  bulkUploadBtn.disabled = true;
  try {
    if (!(await confirmUpload(ids))) { bulkUploadBtn.disabled = false; return; }
    const { results } = await api("/api/episodes/bulk-upload", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ episode_ids: ids }),
    });
    const succeeded = results.filter((r) => r.uploaded);
    const failed = results.filter((r) => !r.uploaded);
    if (succeeded.length) {
      showToast(`Uploaded ${succeeded.length} episode(s).`, { type: "success", title: "Bulk upload", timeout: 4000 });
    }
    if (failed.length) {
      const detail = failed.map((r) => `${r.episode_id}: ${r.error || r.skipped_reason || "failed"}`).join("\n");
      showToast(detail, { type: "error", title: `${failed.length} episode(s) not uploaded`, timeout: 12000 });
    }
  } catch (err) {
    showToast(err.message, { type: "error", title: "Bulk upload failed" });
  } finally {
    bulkUploadBtn.disabled = false;
    selectedEpisodes.clear();
    await refreshList();
    await refreshHubStatus();
    await renderListStats();
  }
});

bulkDeleteBtn.addEventListener("click", async () => {
  const ids = Array.from(selectedEpisodes);
  if (!ids.length) return;
  if (!confirm(`Delete ${ids.length} episode(s)? This also removes uploaded ones from the Hub.`)) return;
  bulkDeleteBtn.disabled = true;
  try {
    const { results } = await api("/api/episodes/bulk-delete", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ episode_ids: ids }),
    });
    const failed = results.filter((r) => !r.deleted_local);
    if (failed.length) {
      const detail = failed.map((r) => `${r.episode_id}: ${r.error || "failed"}`).join("\n");
      showToast(detail, { type: "error", title: `${failed.length} episode(s) not deleted`, timeout: 12000 });
    }
    if (selectedId && ids.includes(selectedId)) {
      selectedId = null;
      detailEl.innerHTML = '<p class="empty-hint">Select an episode to see its details.</p>';
    }
  } catch (err) {
    showToast(err.message, { type: "error", title: "Bulk delete failed" });
  } finally {
    bulkDeleteBtn.disabled = false;
    selectedEpisodes.clear();
    await refreshList();
    await refreshHubStatus();
    await renderListStats();
  }
});

bulkClearBtn.addEventListener("click", () => {
  selectedEpisodes.clear();
  refreshList();
});

// --- Episode detail panel ---

// Flattens a plain object into [dottedKey, displayValue] pairs -- arrays
// join with ", ", nested objects recurse with a dotted prefix, booleans
// and numbers stringify as-is. Used to dump robot_profile.yaml-shaped
// data (manipulator, end_effector, low_level, units_and_frames,
// board_fabrication) into the Overview panel without hand-listing every
// field.
function flattenKV(obj, prefix = "") {
  const out = [];
  if (obj == null) return out;
  for (const [k, v] of Object.entries(obj)) {
    const key = prefix ? `${prefix}.${k}` : k;
    if (v !== null && typeof v === "object" && !Array.isArray(v)) {
      out.push(...flattenKV(v, key));
    } else if (Array.isArray(v)) {
      out.push([key, v.length ? v.join(", ") : "–"]);
    } else {
      out.push([key, v === null || v === "" || v === undefined ? "–" : String(v)]);
    }
  }
  return out;
}

function kvListHtml(pairs) {
  if (!pairs.length) return `<p class="empty-hint">None recorded.</p>`;
  return `<dl class="kv-list">${pairs.map(([k, v]) => `<dt>${escapeHtml(k)}</dt><dd>${escapeHtml(String(v))}</dd>`).join("")}</dl>`;
}

function datasetInfoHtml(fields) {
  const entries = Object.entries(fields || {});
  if (!entries.length) return `<p class="empty-hint">None recorded.</p>`;
  return `<dl class="kv-list">${entries.map(([name, info]) => {
    const shape = `[${info.shape.join(", ")}]`;
    const value = `${shape} · ${info.dtype}${info.provenance ? ` · ${info.provenance}` : ""}`;
    return `<dt>${escapeHtml(name)}</dt><dd>${escapeHtml(value)}</dd>`;
  }).join("")}</dl>`;
}

// Short "N state key(s) · M action key(s)" summary, shown to the right of
// the Overview heading (not inside the collapsible body, so it's visible
// even while collapsed).
function overviewSummaryText(data) {
  const proprio = (data.dataset_info && data.dataset_info.proprioception) || {};
  const commands = (data.dataset_info && data.dataset_info.commands) || {};
  return `${Object.keys(proprio).length} state key(s) · ${Object.keys(commands).length} action key(s)`;
}

function renderOverview(data) {
  const header = data.header;
  const stats = data.stats || {};
  const ann = data.annotation || {};
  const proprio = (data.dataset_info && data.dataset_info.proprioception) || {};
  const commands = (data.dataset_info && data.dataset_info.commands) || {};
  const videoPaths = (header.cameras || []).filter((c) => c.file);

  const attributes = [
    ["episode_id", header.episode_id], ["session_id", header.session_id],
    ["trial_id", header.trial_id], ["lab_id", header.lab_id],
    ["platform_id", header.platform_id], ["task_ids", (header.task_ids || []).join(", ") || "–"],
    ["created_at", header.created_at], ["datahive_version", header.datahive_version],
  ];

  const robotProfile = [
    ...flattenKV(header.manipulator, "manipulator"),
    ...flattenKV(header.end_effector, "end_effector"),
    ...flattenKV(header.low_level, "low_level"),
    ["control_mode", header.control_mode ?? "–"],
    ["policy", header.policy ?? "–"],
    ["board_mounting", header.board_mounting ?? "–"],
    ["hiveboard_version", header.hiveboard_version ?? "–"],
    ["camera_names", (header.cameras || []).map((c) => c.name).join(", ") || "–"],
    ...flattenKV(header.units_and_frames, "units_and_frames"),
    ...flattenKV(header.board_fabrication, "board_fabrication"),
  ];

  const other = [
    ["trajectory_length", formatSteps(stats.n_steps)],
    ["duration_s", stats.duration_s != null ? stats.duration_s.toFixed(3) : "–"],
    ["sample_rate_hz", stats.sample_rate_hz != null ? stats.sample_rate_hz.toFixed(1) : "–"],
    ["operator_name", ann.operator_name || "–"],
    ["annotator_name", ann.annotator_name || "–"],
  ];

  return `
    <h4 class="overview-section">Attributes</h4>
    ${kvListHtml(attributes)}

    <h4 class="overview-section">Robot profile</h4>
    ${kvListHtml(robotProfile)}

    <h4 class="overview-section">observations/robot_states (shape · dtype)</h4>
    ${datasetInfoHtml(proprio)}

    <h4 class="overview-section">actions (shape · dtype)</h4>
    ${datasetInfoHtml(commands)}

    <h4 class="overview-section">observations/video_paths</h4>
    ${kvListHtml(videoPaths.length ? videoPaths.map((c) => [c.name, c.file]) : [])}

    <h4 class="overview-section">Other</h4>
    ${kvListHtml(other)}
  `;
}

function cameraTooltipText(spec) {
  if (!spec) return "No metadata recorded";
  const lines = [];
  if (spec.resolution) lines.push(`Resolution: ${spec.resolution}`);
  if (spec.fps != null && spec.fps !== "") lines.push(`FPS: ${spec.fps}`);
  if (spec.encoding) lines.push(`Encoding: ${spec.encoding}`);
  if (spec.position) lines.push(`Position: ${spec.position}`);
  if (spec.orientation) lines.push(`Orientation: ${spec.orientation}`);
  return lines.length ? lines.join("\n") : "No metadata recorded";
}

async function selectEpisode(episodeId) {
  const myToken = ++selectEpisodeToken;
  selectedId = episodeId;
  updateNavButtons();

  let data, attachments, previousAnnotations;
  try {
    await refreshList();
    if (myToken !== selectEpisodeToken) return; // superseded by a newer click
    const activeRow = document.querySelector(".episode-row.selected");
    if (activeRow) activeRow.scrollIntoView({ block: "nearest" });
    data = await api(`/api/episodes/${episodeId}`);
    attachments = await loadAttachments();
    previousAnnotations = await api(`/api/episodes/${episodeId}/previous-annotations`)
      .then((r) => r.results)
      .catch(() => []); // non-fatal -- "Copy from previous" just stays empty
  } catch (err) {
    if (myToken !== selectEpisodeToken) return; // superseded meanwhile -- don't show a stale error either
    showToast(err.message, { type: "error", title: `Could not load ${episodeId}` });
    detailEl.innerHTML = `<p class="empty-hint">Error loading ${escapeHtml(episodeId)}: ${escapeHtml(err.message)}</p>`;
    return;
  }
  if (myToken !== selectEpisodeToken) return; // superseded by a newer click while fetching

  const ann = { ...(data.annotation_defaults || {}), ...(data.annotation || {}) };
  const validateState = validateStateOf(data.index);
  try {
    if (!ann.operator_name) ann.operator_name = localStorage.getItem(LAST_OPERATOR_KEY) || "";
    if (!ann.annotator_name) ann.annotator_name = localStorage.getItem(LAST_ANNOTATOR_KEY) || "";
  } catch (_) {}

  let initialNotes = ann.notes || "";
  let extractedFailureTime = "";
  const ftMatch = initialNotes.match(/\[failure_time:\s*([\d.]+)s?\]/i);
  if (ftMatch) {
    extractedFailureTime = ftMatch[1];
    initialNotes = initialNotes.replace(/\[failure_time:\s*[\d.]+s?\]\s*/i, "").trim();
  }

  // Blank (not defaulted to "success") until the annotator actually picks
  // one -- the rest of the form stays hidden until then.
  const outcome = ann.outcome || "";
  const attachmentId = ann.attachment_id || "";
  const info = attachments[attachmentId];
  const composed = isComposedTask(info, attachmentId);
  const stats = data.stats || {};
  const statusClass = (data.index && data.index.status || "").split(" ")[0];

  const OUTCOMES = ["success", "fail", "timeout", "safety_stop"];
  const FAILURE_CAUSES = ["grasp_geometry", "kinematic_limit", "perception", "slip", "force_limit", "control_precision", "other"];
  const SEVERITIES = ["minor", "moderate", "critical"];
  const STRATEGIES = ["prehensile", "non_prehensile"];

  detailEl.innerHTML = `
    <div class="detail-header">
      <div>
        <h2>${episodeId}</h2>
        <div class="detail-sub">${data.header.session_id} · trial ${data.header.trial_id}</div>
      </div>
      <span class="badge badge-lg ${statusClass}">${data.index ? humanize(data.index.status) : ""}</span>
    </div>

    ${(data.missing || []).length ? `<div class="incomplete-banner"><span><b>Incomplete episode.</b> Missing: ${data.missing.map(escapeHtml).join(", ")}.</span>
      <button type="button" id="addFilesBtn" data-episode="${escapeHtml(data.header.episode_id)}" data-session="${escapeHtml(data.header.session_id)}" data-trial="${escapeHtml(data.header.trial_id)}">Add files</button></div>` : ""}
    <div class="stat-row">
      <div class="stat"><span class="stat-value">${formatSteps(stats.n_steps)}</span><span class="stat-label">Steps</span></div>
      <div class="stat"><span class="stat-value">${formatDuration(stats.duration_s)}</span><span class="stat-label">Duration</span></div>
      <div class="stat"><span class="stat-value">${formatRate(stats.sample_rate_hz)}</span><span class="stat-label">Rate</span></div>
      <div class="stat"><span class="stat-value">${data.cameras.length}</span><span class="stat-label">Cameras</span></div>
    </div>

    <div class="card">
      <h3 class="collapsible-header" id="overviewToggle">
        <svg class="chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"></polyline></svg>
        <span>Overview</span>
        <span class="overview-summary">${overviewSummaryText(data)}</span>
      </h3>
      <div class="collapsible-body" id="overviewBody" hidden>
        ${renderOverview(data)}
      </div>
    </div>

    ${(data.header.action_space || []).includes("cartesian_position") ? `
    <div class="card">
      <h3 class="collapsible-header collapsed" id="trajToggle">
        <svg class="chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"></polyline></svg>
        <span>Trajectory</span>
        <span class="overview-summary">end-effector path · last 100 steps</span>
      </h3>
      <div class="collapsible-body" id="trajBody" hidden></div>
    </div>` : ""}

    <div class="detail-split">
      <div class="pane camera-pane">
        <div class="pane-header">
          <h3>Cameras</h3>
          <div class="camera-controls">
            <button type="button" id="stepBackBtn" class="video-step-btn" title="Step backward 1 frame (,)">◀ 1f</button>
            <button type="button" id="toggleVideosBtn" class="video-step-btn" title="Play/Pause all cameras (Space)">Play / Pause</button>
            <button type="button" id="stepForwardBtn" class="video-step-btn" title="Step forward 1 frame (.)">1f ▶</button>
            <select id="videoSpeedSelect" title="Playback speed">
              ${[0.5, 1, 1.5, 2].map((r) => `<option value="${r}" ${r === videoPlaybackRate ? "selected" : ""}>${r}x</option>`).join("")}
            </select>
            <div class="layout-toggle" id="videoLayoutToggle">
              <button type="button" data-cols="1" class="${videoLayoutCols === 1 ? "active" : ""}" title="1 wide">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="6" width="18" height="12" rx="1"></rect></svg>
              </button>
              <button type="button" data-cols="2" class="${videoLayoutCols === 2 ? "active" : ""}" title="2 side by side">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="6" width="9" height="12" rx="1"></rect><rect x="13" y="6" width="9" height="12" rx="1"></rect></svg>
              </button>
              <button type="button" data-cols="3" class="${videoLayoutCols === 3 ? "active" : ""}" title="3 side by side">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="1" y="6" width="6.5" height="12" rx="1"></rect><rect x="8.75" y="6" width="6.5" height="12" rx="1"></rect><rect x="16.5" y="6" width="6.5" height="12" rx="1"></rect></svg>
              </button>
            </div>
          </div>
        </div>
        <div class="camera-grid" id="cameraGrid" style="grid-template-columns: repeat(${videoLayoutCols}, 1fr);">
          ${data.cameras.map((c) => {
            const spec = (data.header.cameras || []).find((cam) => cam.name === c) || null;
            const tooltip = cameraTooltipText(spec);
            return `
            <div class="camera-card">
              <div class="cam-name">
                <svg class="cam-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M23 7l-7 5 7 5V7z"></path><rect x="1" y="5" width="15" height="14" rx="2" ry="2"></rect></svg>
                <span>${c}</span>
                <span class="info-icon" data-tooltip="${escapeHtml(tooltip)}" tabindex="0">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="16" x2="12" y2="12"></line><line x1="12" y1="8" x2="12.01" y2="8"></line></svg>
                </span>
              </div>
              <video controls src="/api/episodes/${episodeId}/video/${c}"></video>
            </div>`;
          }).join("") || "<p class=\"empty-hint\">No videos recorded.</p>"}
        </div>
        <div class="video-hint">Shortcuts: <kbd>Space</kbd> Play/Pause · <kbd>,</kbd> / <kbd>.</kbd> Step frame</div>
      </div>

      <div class="pane annotate-pane">
      <h3>Annotate</h3>
      <div class="task-instruction-card" id="taskInstructionCard" style="${info && (info.success || info.name) ? "" : "display:none"}">
        <div class="task-instruction-header">
          <svg class="target-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><circle cx="12" cy="12" r="6"></circle><circle cx="12" cy="12" r="2"></circle></svg>
          <strong>Task goal:</strong>
          <span id="taskInstructionText">${escapeHtml(info ? (info.success || info.name) : "")}</span>
        </div>
      </div>
      <select id="copyPrevSelect" class="copy-prev-select" title="Copy from a previous annotation" ${previousAnnotations.length ? "" : "disabled"}>
        <option value="" selected>${previousAnnotations.length ? "Copy from previous…" : "No previous annotations"}</option>
        ${previousAnnotations.map((r) => {
          const task = attachments[r.annotation.attachment_id];
          const taskLabel = task ? task.name : (r.annotation.attachment_id || "no task");
          return `<option value="${escapeHtml(r.episode_id)}">${escapeHtml(r.episode_id)} — ${escapeHtml(taskLabel)} — ${escapeHtml(humanize(r.annotation.outcome))}</option>`;
        }).join("")}
      </select>
      <form class="validate-form" id="annForm">
        <fieldset>
          <div class="field-grid">
            <label>${fieldLabel("Operator name")}<input name="operator_name" value="${ann.operator_name || ""}" placeholder="Who ran this trial" required></label>
            <label>${fieldLabel("Annotator name")}<input name="annotator_name" value="${ann.annotator_name || ""}" placeholder="Who is annotating" required></label>
            <label class="full">${fieldLabel("Task")}
              <input type="hidden" name="attachment_id" value="${attachmentId}">
              <button type="button" id="taskPickerBtn" class="task-chip">${taskChipHtml(info, attachmentId)}</button>
            </label>
            <label class="full">${fieldLabel("Outcome")}
              ${segmentedControlHtml("outcome", OUTCOMES, outcome, OUTCOME_MEANINGS)}
            </label>
            <hr id="outcomeDivider" class="field-divider" style="${!outcome || outcome === "success" ? "display:none" : ""}">
            <div id="restFields" style="${outcome ? "" : "display:none"}">
              <label id="failureCauseField" style="${outcome === "success" ? "display:none" : ""}">
                <span class="field-label-row">
                  <span class="field-label-text">Failure cause<span class="required-mark" title="Required">*</span></span>
                  <button type="button" id="failureHelpBtn" class="help-link-btn" title="View failure causes guide" aria-label="Failure causes guide">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"></path><line x1="12" y1="17" x2="12.01" y2="17"></line></svg>
                    <span>Help</span>
                  </button>
                </span>
                <select name="failure_cause">
                  ${FAILURE_CAUSES.map((c) => `<option value="${c}" ${c === ann.failure_cause ? "selected" : ""}>${humanize(c)}</option>`).join("")}
                </select>
              </label>
              <label id="failureCauseDetailField" class="full" style="${(outcome === "success" || ann.failure_cause !== "other") ? "display:none" : ""}">${fieldLabel("Reason")}
                <input name="failure_cause_detail" value="${ann.failure_cause_detail || ""}" placeholder="What happened?">
              </label>
              <label id="severityField" class="full" style="${outcome === "success" ? "display:none" : ""}">Severity
                ${segmentedControlHtml("severity", SEVERITIES, ann.severity)}
              </label>
              <label id="failureTimeField" class="full" style="${outcome === "success" ? "display:none" : ""}">
                <span class="field-label-row">
                  <span class="field-label-text">Failure timestamp (s) <span class="optional-mark">(optional)</span></span>
                  <button type="button" id="markCurrentTimeBtn" class="help-link-btn" title="Capture current playback time from video">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 16 14"></polyline></svg>
                    <span>Use video time</span>
                  </button>
                </span>
                <input name="failure_time_s" type="number" step="0.01" min="0" value="${extractedFailureTime || ""}" placeholder="e.g. 14.5">
              </label>
              <hr id="successDivider" class="field-divider" style="${outcome === "success" ? "" : "display:none"}">
              <label id="completionTimeField" class="full" style="${outcome !== "success" ? "display:none" : ""}">Completion time
                ${completionSourceHtml(data.completion_sources || { hdf5: stats.duration_s }, ann.completion_source)}
              </label>
              <label>Attempts <input name="n_attempts" type="number" value="${ann.n_attempts || 1}"></label>
              <label>Regrasps <input name="n_regrasps" type="number" value="${ann.n_regrasps || 0}"></label>
              <label class="full">${fieldLabel("Strategy")}
                ${segmentedControlHtml("strategy", STRATEGIES, ann.strategy, STRATEGY_MEANINGS)}
              </label>
              <label id="stageField" class="full" style="${composed ? "" : "display:none"}">${fieldLabel("Last completed stage")}
                <div id="stageFieldBody">${stageFieldHtml(info, ann.stage_reached, outcome, ann.attachment_id)}</div>
              </label>
            </div>
            <hr class="field-divider">
            <label id="notesField" class="full">Note (optional)
              <textarea name="notes" placeholder="Anything else worth recording about this trial…">${initialNotes}</textarea>
            </label>
          </div>
        </fieldset>

        <div class="form-options-row">
          <label class="toggle-control auto-advance-control" title="Automatically jump to the next unannotated episode after saving">
            <input type="checkbox" id="autoAdvanceChk" ${isAutoAdvanceEnabled() ? "checked" : ""}>
            <span>Auto-advance on save</span>
          </label>
          <span id="dirtyIndicator" class="dirty-indicator" hidden>● Unsaved changes</span>
        </div>

        <div class="actions">
          <button type="button" id="deleteBtn" class="btn-icon danger" title="Delete this episode">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"></path><path d="M10 11v6"></path><path d="M14 11v6"></path><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"></path></svg>
            <span>Delete</span>
          </button>
          <div class="actions-right">
            <button type="button" id="validateBtn" class="btn-icon btn-validate ${validateState}" title="Check the episode against the HiveBoard rules">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" id="vdSvg">${validateIconHtml(validateState)}</svg>
              <span id="vdLabel">${validateLabel(validateState)}</span>
            </button>
            <button type="submit" class="btn-icon solid" title="Save annotation (Ctrl+Enter)">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"></path><polyline points="17 21 17 13 7 13 7 21"></polyline><polyline points="7 3 7 8 15 8"></polyline></svg>
              <span>Save</span>
            </button>
            <button type="button" id="uploadBtn" class="btn-icon primary">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="17 8 12 3 7 8"></polyline><line x1="12" y1="3" x2="12" y2="15"></line></svg>
              <span>Upload</span>
            </button>
          </div>
        </div>
        <div class="status-msg" id="statusMsg"></div>
      </form>
      </div>
    </div>
  `;

  const overviewToggle = document.getElementById("overviewToggle");
  const overviewBody = document.getElementById("overviewBody");
  overviewToggle.addEventListener("click", () => {
    const nowHidden = !overviewBody.hidden;
    overviewBody.hidden = nowHidden;
    overviewToggle.classList.toggle("collapsed", nowHidden);
  });
  overviewToggle.classList.add("collapsed"); // starts minimized
  wireTrajectory(episodeId);

  const videos = Array.from(document.querySelectorAll(".camera-grid video"));
  currentVideoElements = videos;
  videos.forEach((v) => { v.playbackRate = videoPlaybackRate; });
  document.getElementById("videoSpeedSelect").addEventListener("change", (e) => {
    videoPlaybackRate = parseFloat(e.target.value);
    videos.forEach((v) => { v.playbackRate = videoPlaybackRate; });
  });

  // Synchronized playback across camera views
  let isSyncingVideo = false;
  videos.forEach((v) => {
    v.addEventListener("play", () => {
      if (isSyncingVideo) return;
      isSyncingVideo = true;
      videos.forEach((other) => {
        if (other !== v && other.paused) other.play().catch(() => {});
      });
      isSyncingVideo = false;
    });
    v.addEventListener("pause", () => {
      if (isSyncingVideo) return;
      isSyncingVideo = true;
      videos.forEach((other) => {
        if (other !== v && !other.paused) other.pause();
      });
      isSyncingVideo = false;
    });
    v.addEventListener("seeked", () => {
      if (isSyncingVideo) return;
      isSyncingVideo = true;
      const t = v.currentTime;
      videos.forEach((other) => {
        if (other !== v && Math.abs(other.currentTime - t) > 0.05) {
          other.currentTime = t;
        }
      });
      isSyncingVideo = false;
    });
  });

  const toggleVideosBtn = document.getElementById("toggleVideosBtn");
  if (toggleVideosBtn) toggleVideosBtn.addEventListener("click", toggleVideosPlay);
  const stepBackBtn = document.getElementById("stepBackBtn");
  if (stepBackBtn) stepBackBtn.addEventListener("click", () => stepVideos(-1));
  const stepForwardBtn = document.getElementById("stepForwardBtn");
  if (stepForwardBtn) stepForwardBtn.addEventListener("click", () => stepVideos(1));

  const cameraGrid = document.getElementById("cameraGrid");
  document.querySelectorAll("#videoLayoutToggle button").forEach((btn) => {
    btn.addEventListener("click", () => {
      videoLayoutCols = Number(btn.dataset.cols);
      cameraGrid.style.gridTemplateColumns = `repeat(${videoLayoutCols}, 1fr)`;
      document.querySelectorAll("#videoLayoutToggle button").forEach((b) => b.classList.toggle("active", b === btn));
    });
  });

  const form = document.getElementById("annForm");
  wireSegmentedControls(form);

  form.addEventListener("input", () => setDirty(true));
  form.addEventListener("change", () => setDirty(true));
  form.addEventListener("segmentchange", () => setDirty(true));

  const autoAdvanceChk = document.getElementById("autoAdvanceChk");
  if (autoAdvanceChk) {
    autoAdvanceChk.checked = isAutoAdvanceEnabled();
    autoAdvanceChk.addEventListener("change", () => {
      try {
        localStorage.setItem(AUTO_ADVANCE_KEY, autoAdvanceChk.checked ? "1" : "0");
      } catch (_) {}
    });
  }

  const markTimeBtn = document.getElementById("markCurrentTimeBtn");
  if (markTimeBtn) {
    markTimeBtn.addEventListener("click", () => {
      const first = videos[0];
      if (first && Number.isFinite(first.currentTime)) {
        const ftInput = form.querySelector('input[name="failure_time_s"]');
        if (ftInput) {
          ftInput.value = first.currentTime.toFixed(2);
          setDirty(true);
        }
      }
    });
  }

  function updateFailureCauseDetailVisibility() {
    const isSuccess = form.outcome.value === "success";
    const isOther = form.failure_cause.value === "other";
    document.getElementById("failureCauseDetailField").style.display = (!isSuccess && isOther) ? "" : "none";
  }

  form.querySelector('.segmented[data-name="outcome"]').addEventListener("segmentchange", () => {
    document.getElementById("restFields").style.display = ""; // reveal once any outcome is picked
    const isSuccess = form.outcome.value === "success";
    document.getElementById("failureCauseField").style.display = isSuccess ? "none" : "";
    document.getElementById("outcomeDivider").style.display = isSuccess ? "none" : "";
    document.getElementById("severityField").style.display = isSuccess ? "none" : "";
    const failureTimeEl = document.getElementById("failureTimeField");
    if (failureTimeEl) failureTimeEl.style.display = isSuccess ? "none" : "";
    document.getElementById("successDivider").style.display = isSuccess ? "" : "none";
    document.getElementById("completionTimeField").style.display = isSuccess ? "" : "none";
    updateFailureCauseDetailVisibility();

    const currentTask = (attachments && form.attachment_id) ? (attachments[form.attachment_id.value] || info) : info;
    const isComposed = isComposedTask(currentTask, form.attachment_id ? form.attachment_id.value : null);
    const stages = getTaskStages(currentTask, form.attachment_id ? form.attachment_id.value : null);
    if (isComposed && form.elements.stage_reached) {
      if (isSuccess && stages && stages.length) {
        form.elements.stage_reached.value = String(stages.length);
      } else if (!isSuccess && stages && stages.length) {
        if (form.elements.stage_reached.value === String(stages.length) || form.elements.stage_reached.value === "") {
          form.elements.stage_reached.value = "0";
        }
      }
    }
  });
  form.failure_cause.addEventListener("change", updateFailureCauseDetailVisibility);

  function applyTaskSelection(taskId) {
    form.attachment_id.value = taskId;
    const a = attachments[taskId];
    const isComposed = isComposedTask(a, taskId);
    document.getElementById("taskPickerBtn").innerHTML = taskChipHtml(a, taskId);
    const stageFieldEl = document.getElementById("stageField");
    if (stageFieldEl) stageFieldEl.style.display = isComposed ? "" : "none";
    const currentOutcome = form.outcome ? form.outcome.value : "";
    document.getElementById("stageFieldBody").innerHTML = stageFieldHtml(a, null, currentOutcome, taskId);

    const taskCard = document.getElementById("taskInstructionCard");
    const taskText = document.getElementById("taskInstructionText");
    if (taskCard && taskText) {
      const goal = a ? (a.success || a.name) : "";
      taskText.textContent = goal;
      taskCard.style.display = goal ? "" : "none";
    }

    wireSegmentedControls(form);
    setDirty(true);
  }

  document.getElementById("taskPickerBtn").addEventListener("click", () => {
    openTaskPicker(attachments, form.attachment_id.value, applyTaskSelection);
  });

  const failureHelpBtn = document.getElementById("failureHelpBtn");
  if (failureHelpBtn) {
    failureHelpBtn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      openFailureHelpOverlay();
    });
  }

  function applyAnnotationRow(row) {
    form.operator_name.value = row.operator_name || "";
    form.annotator_name.value = row.annotator_name || "";
    if (row.attachment_id) applyTaskSelection(row.attachment_id);

    // Dispatches "segmentchange", which the outcome listener below already
    // uses to reveal #restFields and show/hide failure cause / severity /
    // completion time -- no need to duplicate that here.
    setSegmentedValue(form, "outcome", row.outcome);

    form.failure_cause.value = row.failure_cause || "";
    form.failure_cause_detail.value = row.failure_cause_detail || "";
    setSegmentedValue(form, "severity", row.severity);
    form.n_attempts.value = row.n_attempts || 1;
    form.n_regrasps.value = row.n_regrasps || 0;
    setSegmentedValue(form, "strategy", row.strategy);

    let noteStr = row.notes || "";
    const m = noteStr.match(/\[failure_time:\s*([\d.]+)s?\]/i);
    if (m) {
      const ftInput = form.querySelector('input[name="failure_time_s"]');
      if (ftInput) ftInput.value = m[1];
      noteStr = noteStr.replace(/\[failure_time:\s*[\d.]+s?\]\s*/i, "").trim();
    }
    form.notes.value = noteStr;
    updateFailureCauseDetailVisibility();
    setDirty(true);

    // Stage reached depends on the (just-selected) task -- set it after
    // applyTaskSelection() has rebuilt the field for that task's stages.
    if (form.elements.stage_reached) {
      let stageVal = row.stage_reached;
      if (stageVal === null || stageVal === undefined || stageVal === "") {
        const curTask = (attachments && form.attachment_id) ? (attachments[form.attachment_id.value] || info) : info;
        const curStages = getTaskStages(curTask, form.attachment_id ? form.attachment_id.value : null);
        if (row.outcome === "success" && curStages && curStages.length) {
          stageVal = String(curStages.length);
        } else if (row.outcome && row.outcome !== "success") {
          stageVal = "0";
        }
      }
      form.elements.stage_reached.value = (stageVal !== null && stageVal !== undefined && stageVal !== "") ? String(stageVal) : "";
    }
  }

  const copyPrevSelect = document.getElementById("copyPrevSelect");
  copyPrevSelect.addEventListener("change", () => {
    const match = previousAnnotations.find((r) => r.episode_id === copyPrevSelect.value);
    copyPrevSelect.value = ""; // one-shot action -- reset to the placeholder right away
    if (match) {
      applyAnnotationRow(match.annotation);
      showToast(`Copied from ${match.episode_id}.`, { type: "success", timeout: 3000 });
    }
  });

  function collectAnnotationPayload() {
    const fd = new FormData(form);
    const payload = Object.fromEntries(fd.entries());
    // completion_time_s is intentionally not a form field -- the backend
    // derives it from the episode's own recorded duration.
    for (const k of ["n_attempts", "n_regrasps", "stage_reached"]) {
      payload[k] = payload[k] === "" ? null : Number(payload[k]);
    }
    if (payload.severity === "") payload.severity = null;
    // Send only what applies to the outcome: hidden failure fields keep their default values.
    if (payload.outcome === "success") {
      payload.failure_cause = null;
      payload.failure_cause_detail = "";
      payload.severity = null;
    } else {
      payload.completion_time_s = null;
      if (payload.failure_cause !== "other") payload.failure_cause_detail = "";
    }
    // Completion time: the chosen source's duration (success only).
    if (payload.outcome === "success" && payload.completion_source) {
      const btn = form.querySelector(`.segmented[data-name="completion_source"] .segment[data-value="${payload.completion_source}"]`);
      if (btn) payload.completion_time_s = Number(btn.dataset.seconds);
    } else {
      payload.completion_source = null;
    }

    const ftVal = (form.failure_time_s ? form.failure_time_s.value : "").trim();
    let noteText = (payload.notes || "").trim();
    if (ftVal && payload.outcome !== "success") {
      noteText = `[failure_time: ${ftVal}s] ${noteText}`.trim();
    }
    payload.notes = noteText;
    delete payload.failure_time_s;

    return payload;
  }

  // The browser blocks an invalid form silently when the field cannot be focused; say why.
  form.addEventListener("invalid", (e) => {
    const f = e.target;
    const label = (f.closest("label") && f.closest("label").querySelector(".field-label-text, .field-label-row")) || null;
    const name = (label ? label.textContent : f.name || "a field").replace("*", "").trim();
    const m = document.getElementById("statusMsg");
    if (m) m.textContent = `Error: ${name} needs a value.`;
    showToast(`${name} needs a value.`, { type: "error", title: "Cannot save" });
  }, true);

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const msg = document.getElementById("statusMsg");
    try {
      const opName = (form.operator_name.value || "").trim();
      const annName = (form.annotator_name.value || "").trim();
      if (!opName) {
        msg.textContent = "Error: Operator name is required.";
        showToast("Operator name is required.", { type: "error", title: "Validation error" });
        form.operator_name.focus();
        return;
      }
      if (!annName) {
        msg.textContent = "Error: Annotator name is required.";
        showToast("Annotator name is required.", { type: "error", title: "Validation error" });
        form.annotator_name.focus();
        return;
      }

      const currentTask = (attachments && form.attachment_id) ? (attachments[form.attachment_id.value] || info) : info;
      const isComposed = isComposedTask(currentTask, form.attachment_id ? form.attachment_id.value : null);
      const payload = collectAnnotationPayload();

      if (isComposed) {
        if (payload.stage_reached === null || isNaN(payload.stage_reached)) {
          msg.textContent = "Error: Last completed stage is required for this task.";
          showToast("Please select the last completed stage.", { type: "error", title: "Validation error" });
          return;
        }
        const stages = getTaskStages(currentTask, form.attachment_id ? form.attachment_id.value : null);
        if (payload.outcome === "success" && stages && stages.length && payload.stage_reached !== stages.length) {
          msg.textContent = "Error: Success requires completion of every stage.";
          showToast("Success requires completion of every stage.", { type: "error", title: "Validation error" });
          return;
        }
      } else {
        payload.stage_reached = null;
      }

      payload.operator_name = opName;
      payload.annotator_name = annName;

      try { localStorage.setItem(LAST_OPERATOR_KEY, opName); } catch (_) {}
      try { localStorage.setItem(LAST_ANNOTATOR_KEY, annName); } catch (_) {}

      await api(`/api/episodes/${episodeId}/annotate`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
      });
      setDirty(false);
      msg.textContent = "Saved.";
      showToast("Saved.", { type: "success", title: episodeId, timeout: 3000 });

      if (isAutoAdvanceEnabled()) {
        const advanced = await navigateToNextUnannotated();
        if (!advanced) {
          await selectEpisode(episodeId);
        }
      } else {
        await selectEpisode(episodeId);
      }
    } catch (err) {
      msg.textContent = `Error: ${err.message}`;
      showToast(err.message, { type: "error", title: "Save failed" });
    }
  });

  document.getElementById("validateBtn").addEventListener("click", async () => {
    const msg = document.getElementById("statusMsg");
    const btn = document.getElementById("validateBtn");
    const setState = (st) => {
      btn.classList.remove("is-valid", "is-invalid", "is-busy");
      if (st) btn.classList.add(st);
      document.getElementById("vdLabel").textContent = validateLabel(st);
      document.getElementById("vdSvg").innerHTML = validateIconHtml(st);
    };
    setState("is-busy");
    try {
      const result = await api(`/api/episodes/${episodeId}/validate`, { method: "POST" });
      setState(result.ok ? "is-valid" : "is-invalid");
      if (result.ok) {
        msg.textContent = "Episode is valid.";
        showToast("Episode is valid.", { type: "success", title: episodeId, timeout: 4000 });
      } else {
        msg.textContent = "Validation failed.";
        showToast(result.error, { type: "error", title: `Validation failed: ${episodeId}`, timeout: 12000 });
      }
      await selectEpisode(episodeId);
    } catch (err) {
      setState("");
      msg.textContent = `Error: ${err.message}`;
      showToast(err.message, { type: "error", title: "Validate failed" });
    }
  });

  document.getElementById("uploadBtn").onclick = async () => {
    const msg = document.getElementById("statusMsg");
    try {
      if (!(await confirmUpload([episodeId]))) return;
      const result = await api(`/api/episodes/${episodeId}/upload`, { method: "POST" });
      msg.textContent = result.uploaded ? "Uploaded." : `Skipped: ${result.skipped_reason || result.error}`;
      await refreshList();
      await refreshHubStatus();
      await renderListStats();
    } catch (err) {
      msg.textContent = `Error: ${err.message}`;
      showToast(err.message, { type: "error", title: "Upload failed" });
    }
  };

  document.getElementById("deleteBtn").onclick = async () => {
    if (!confirm(`Delete episode ${episodeId}? This also removes it from the Hub if uploaded.`)) return;
    try {
      await api(`/api/episodes/${episodeId}`, { method: "DELETE" });
      selectedId = null;
      detailEl.innerHTML = '<p class="empty-hint">Select an episode to see its details.</p>';
      await refreshList();
      await refreshHubStatus();
      await renderListStats();
    } catch (err) {
      document.getElementById("statusMsg").textContent = `Error: ${err.message}`;
      showToast(err.message, { type: "error", title: "Delete failed" });
    }
  };
}

// --- Robot profile: create/edit from the interface, same file the CLI's
// `datahive new-profile` writes and `robot_profile.yaml` on disk. ---

const EE_TYPES = ["gripper", "dexterous_hand", "prosthetic_hand", "other"];
const EE_TYPE_TITLES = {
  gripper: "Parallel-jaw, suction, or standard gripper",
  dexterous_hand: "Multi-jointed anthropomorphic or dexterous hand",
  prosthetic_hand: "Prosthetic terminal device or hand",
  other: "Any other end effector: describe it below",
};

const EE_MODALITIES = ["binary", "position", "velocity"];
const EE_MODALITY_TITLES = {
  binary: "Open / close discrete commands",
  position: "Continuous joint position or width setpoints",
  velocity: "Continuous velocity commands",
};

const LOW_LEVEL_MODES = ["stock", "custom"];
const LOW_LEVEL_MODE_TITLES = {
  stock: "Standard manufacturer control loop / firmware",
  custom: "Custom torque, impedance, or velocity controller loop",
};

const BOARD_MOUNTINGS = ["horizontal", "vertical"];
const BOARD_MOUNTING_TITLES = {
  horizontal: "Board laid flat on workspace surface",
  vertical: "Board mounted vertically or on fixture",
};

let cameraSeq = 0;

function cameraEntryHtml(cam = {}, index = null) {
  const id = `cam-${cameraSeq++}`;
  const title = cam.name ? cam.name : (index != null ? `Camera ${index + 1}` : "New camera");
  return `
    <div class="camera-entry" data-entry-id="${id}">
      <div class="camera-entry-header">
        <span class="camera-title-badge">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M23 7l-7 5 7 5V7z"></path><rect x="1" y="5" width="15" height="14" rx="2" ry="2"></rect></svg>
          <span class="cam-header-name">${escapeHtml(title)}</span>
        </span>
        <button type="button" class="remove-camera" title="Remove camera">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
        </button>
      </div>
      <div class="field-grid">
        <label class="full">${fieldLabel("Name")}<input placeholder="external" data-field="name" value="${escapeHtml(cam.name || "")}"></label>
      </div>
    </div>`;
}

const IC_ARM = `<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 21h16"></path><path d="M6 21v-4a2 2 0 0 1 2-2h1"></path><circle cx="12" cy="12" r="2.5"></circle><path d="M13.5 10l3.5-4.5"></path><circle cx="18" cy="4" r="2"></circle><path d="M10 13.5l-2.5 3"></path></svg>`;
const IC_EE = `<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="16" width="6" height="6" rx="1"></rect><path d="M12 16v-5"></path><path d="M8 11h8"></path><path d="M8 11V6a3 3 0 0 1 3-3"></path><path d="M16 11V6a3 3 0 0 0-3-3"></path></svg>`;
const IC_LL = `<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="4" width="16" height="16" rx="2"></rect><rect x="9" y="9" width="6" height="6"></rect><line x1="9" y1="1" x2="9" y2="4"></line><line x1="15" y1="1" x2="15" y2="4"></line><line x1="9" y1="20" x2="9" y2="23"></line><line x1="15" y1="20" x2="15" y2="23"></line><line x1="20" y1="9" x2="23" y2="9"></line><line x1="20" y1="14" x2="23" y2="14"></line><line x1="1" y1="9" x2="4" y2="9"></line><line x1="1" y1="14" x2="4" y2="14"></line></svg>`;
const IC_CAM = `<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M23 7l-7 5 7 5V7z"></path><rect x="1" y="5" width="15" height="14" rx="2" ry="2"></rect></svg>`;
const IC_GEN2 = `<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 1 1-3-6.7"></path><polyline points="21 3 21 9 15 9"></polyline></svg>`;
const IC_GEN = `<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><line x1="4" y1="21" x2="4" y2="14"></line><line x1="4" y1="10" x2="4" y2="3"></line><line x1="12" y1="21" x2="12" y2="12"></line><line x1="12" y1="8" x2="12" y2="3"></line><line x1="20" y1="21" x2="20" y2="16"></line><line x1="20" y1="12" x2="20" y2="3"></line><line x1="1" y1="14" x2="7" y2="14"></line><line x1="9" y1="8" x2="15" y2="8"></line><line x1="17" y1="16" x2="23" y2="16"></line></svg>`;
const IC_BOARD = `<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"></path><polyline points="3.27 6.96 12 12.01 20.73 6.96"></polyline><line x1="12" y1="22.08" x2="12" y2="12"></line></svg>`;
const ARM_ACTIONS = {
  joint_position: "Absolute joint angles",
  joint_velocity: "Joint velocities",
  cartesian_position: "End-effector pose (position + orientation)",
  cartesian_velocity: "End-effector twist (linear + angular velocity)",
};
const GRIPPER_ACTIONS = {
  gripper_position: "Continuous gripper opening / width",
  gripper_velocity: "Gripper opening speed",
  gripper_binary: "Open / close (0 or 1)",
};
const BASE_ACTIONS = { base_velocity: "Base velocity command", base_position: "Base position / pose target" };

function actionChipsHtml(options, selected) {
  return `<div class="chip-group">${Object.entries(options).map(([v, tip]) => `
    <label class="chip" data-tooltip="${escapeHtml(tip)}"><input type="checkbox" name="action_space" value="${v}" ${selected.includes(v) ? "checked" : ""}><span>${v}</span></label>`).join("")}</div>`;
}

const ORIENTATION_OPTIONS = {
  quat: "quat: quaternion, scalar-last, shape (4,)",
  matrix: "matrix: rotation matrix, shape (3, 3)",
  rot6d: "rot6d: first two matrix columns, shape (6,) (openpi)",
  rotvec: "rotvec: axis-angle, shape (3,)",
  euler_xyz: "euler_xyz: extrinsic, shape (3,)", euler_zyx: "euler_zyx: extrinsic, shape (3,)", euler_xyx: "euler_xyx: extrinsic, shape (3,)",
  euler_XYZ: "euler_XYZ: intrinsic, shape (3,)", euler_ZYX: "euler_ZYX: intrinsic, shape (3,)", euler_XYX: "euler_XYX: intrinsic, shape (3,)",
};

function orientationSelectHtml(name, current) {
  const known = !current || current in ORIENTATION_OPTIONS;
  return `<select name="${name}"><option value="">— not set —</option>
    ${Object.entries(ORIENTATION_OPTIONS).map(([v, label]) => `<option value="${v}" ${v === current ? "selected" : ""}>${label}</option>`).join("")}
    ${known ? "" : `<option value="${escapeHtml(current)}" selected>${escapeHtml(current)} (invalid)</option>`}</select>`;
}

const YES_NO = ["yes", "no"];
function boolToYesNo(v) { return v === true ? "yes" : v === false ? "no" : ""; }

function profileGroupTitle(title, hint) {
  return `<div class="profile-group-title"><h4>${title}</h4><span>${hint}</span></div>`;
}

// Recommended cards start minimized; clicking the header opens them.
function profileCard(icon, title, badge, desc, inner, key = "") {
  const badgeHtml = badge ? `<span class="${badge.cls}">${badge.text}</span>` : "";
  const collapsible = !!badge && badge.cls === "recommended-badge";
  return `
      <div class="card profile-card${collapsible ? " collapsible" : ""}" ${key ? `data-card="${key}"` : ""}>
        <div class="card-header-with-icon" ${collapsible ? 'role="button" tabindex="0" aria-expanded="false"' : ""}>
          <div class="section-icon-wrap" aria-hidden="true">${icon}</div>
          <div class="card-header-text">
            <div class="card-title-row"><h3>${title}</h3>${badgeHtml}</div>
            <p class="section-desc">${desc}</p>
          </div>
          ${collapsible ? '<span class="card-chevron" aria-hidden="true">&#8250;</span>' : ""}
        </div>
        <div class="profile-card-body">${inner}</div>
      </div>`;
}

const IC_GAIN = `<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><line x1="4" y1="21" x2="4" y2="14"></line><line x1="4" y1="10" x2="4" y2="3"></line><line x1="12" y1="21" x2="12" y2="12"></line><line x1="12" y1="8" x2="12" y2="3"></line><line x1="20" y1="21" x2="20" y2="16"></line><line x1="20" y1="12" x2="20" y2="3"></line></svg>`;
const IC_CALIB = `<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"></rect><line x1="3" y1="9" x2="21" y2="9"></line><line x1="3" y1="15" x2="21" y2="15"></line><line x1="9" y1="3" x2="9" y2="21"></line><line x1="15" y1="3" x2="15" y2="21"></line></svg>`;

// Gains follow oopsie: the active controller maps to arrays of numbers.
const GAIN_FIELDS = { joint_position: ["kp", "kd"], joint_velocity: ["kv"], osc: ["kp_pos", "kd_pos", "kp_ori", "kd_ori"] };
const GAIN_TITLES = {
  joint_position: "Joint position controller: kp and kd arrays",
  joint_velocity: "Joint velocity controller: kv array",
  osc: "Operational space controller: kp_pos, kd_pos, kp_ori, kd_ori arrays",
};
function gainsController(gains) {
  return gains && typeof gains === "object" ? Object.keys(gains).find((k) => k in GAIN_FIELDS) || "" : "";
}
function gainsInputsHtml(controller, gains) {
  if (!controller) return "";
  return GAIN_FIELDS[controller].map((f) => {
    const arr = (gains && gains[controller] && gains[controller][f]) || [];
    return `<label>${f} (comma-separated numbers)<input data-gain="${f}" value="${escapeHtml(arr.join(", "))}" placeholder="e.g. 100, 100, 100"></label>`;
  }).join("");
}

function matrixToText(m) { return Array.isArray(m) ? m.map((row) => row.join(", ")).join("\n") : ""; }
function textToMatrix(text) {
  const rows = String(text || "").split("\n").map((l) => l.split(/[\s,;]+/).filter(Boolean).map(Number)).filter((r) => r.length);
  return rows.length ? rows : null;
}

function calibrationHtml(cams, profile) {
  const names = cams.map((c) => c.name).filter(Boolean);
  if (!names.length) return `<p class="rn-muted" style="margin:0">Add a camera above and save the profile first; its matrices can be entered here.</p>`;
  return names.map((n) => `
    <div class="calib-cam"><div class="calib-name">${escapeHtml(n)}</div>
      <div class="field-grid">
        <label>Intrinsic matrix (3&times;3, one row per line)<textarea rows="3" data-calib="intrinsic" data-cam="${escapeHtml(n)}" placeholder="fx, 0, cx&#10;0, fy, cy&#10;0, 0, 1">${escapeHtml(matrixToText((profile.intrinsic_calibration_matrix || {})[n]))}</textarea></label>
        <label>Extrinsic matrix (4&times;4, one row per line)<textarea rows="4" data-calib="extrinsic" data-cam="${escapeHtml(n)}" placeholder="r11, r12, r13, tx&#10;r21, r22, r23, ty&#10;r31, r32, r33, tz&#10;0, 0, 0, 1">${escapeHtml(matrixToText((profile.extrinsic_calibration_matrix || {})[n]))}</textarea></label>
      </div></div>`).join("");
}

// Live "N of 5 required fields" line, computed from the form as it is edited.
function updateProfileProgress(form) {
  const val = (n) => (form.elements[n] ? form.elements[n].value.trim() : "");
  const cams = Array.from(form.querySelectorAll("#cameraList .camera-entry"));
  const camOk = cams.some((e) => e.querySelector('[data-field="name"]').value.trim());
  const joints = val("manipulator.joint_names").split(",").map((x) => x.trim()).filter(Boolean);
  const eeOther = val("end_effector.type") === "other";
  const eeField = document.getElementById("eeOtherField");
  if (eeField) eeField.hidden = !eeOther;
  const isCustom = val("low_level.mode") === "custom";
  const customField = document.getElementById("customControllerField");
  if (customField) customField.hidden = !isCustom;
  const mobile = !!(form.elements["uses_mobile_base"] && form.elements["uses_mobile_base"].checked);
  const baseField = document.getElementById("baseActionField");
  if (baseField) baseField.hidden = !mobile;
  const picked = new Set(Array.from(form.querySelectorAll('input[name="action_space"]:checked')).map((i) => i.value));
  const anyOf = (opts) => Object.keys(opts).some((k) => picked.has(k));
  const jointAction = picked.has("joint_position") || picked.has("joint_velocity");
  const actionJoints = String(val("action_joint_names")).split(",").map((x) => x.trim()).filter(Boolean);
  const jm = document.getElementById("jointNamesMark"), am = document.getElementById("actionJointMark");
  if (jm) jm.hidden = !jointAction;
  if (am) am.hidden = !jointAction;
  const cartesian = picked.has("cartesian_position") || picked.has("cartesian_velocity");
  const sm = document.getElementById("stateOrientMark");
  if (sm) sm.hidden = !cartesian;
  const om = document.getElementById("actionOrientMark");
  if (om) om.hidden = !picked.has("cartesian_position");
  const needAction = picked.has("cartesian_position") && !val("orientation_representation");
  const needState = cartesian && !val("robot_state_orientation_representation");
  if (needAction || needState) {
    const card = form.querySelector('.profile-card[data-card="orientation"]');   // a required field must not hide
    if (card) card.classList.add("open");
  }
  const checks = [
    ["Policy name", !!val("policy")],
    ["Robot name", !!val("robot_name")],
    ["Gripper name", !!val("gripper_name")],
    ["Control frequency", Number(val("control_freq")) > 0],
    ["An arm action", anyOf(ARM_ACTIONS)],
    ...(jointAction ? [["Robot state joint names", joints.length > 0], ["Action joint names", actionJoints.length > 0]] : []),
    ...(picked.has("cartesian_position") ? [["Orientation representation (cartesian_position action)", !!val("orientation_representation")]] : []),
    ...(cartesian ? [["Recorded pose orientation representation", !!val("robot_state_orientation_representation")]] : []),
    ["A gripper action", anyOf(GRIPPER_ACTIONS)],
    ...(mobile ? [["A base action (mobile base)", !!val("base_action")]] : []),
    ["Low-level mode", !!val("low_level.mode")],
    ...(isCustom ? [["Custom controller description", !!val("low_level.custom_description")]] : []),
    ["End effector (type, actuated DoF, command modality)", !!val("end_effector.type") && !!val("end_effector.actuated_dof") && !!val("end_effector.command_modality")],
    ...(eeOther ? [["End effector description", !!val("end_effector.type_description")]] : []),
    ["At least one camera (name)", camOk],
  ];
  const done = checks.filter((c) => c[1]).length;
  const missing = checks.filter((c) => !c[1]).map((c) => c[0]);
  const el = document.getElementById("profileProgress");
  el.className = `profile-progress ${missing.length ? "pending" : "done"}`;
  el.innerHTML = missing.length
    ? `<b>${done} of ${checks.length}</b> required fields done. Missing: ${missing.join(", ")}.`
    : `<b>${done} of ${checks.length}</b> required fields done. Ready to record.`;
  const dof = document.getElementById("dofReadout");
  if (dof) dof.textContent = joints.length ? `${joints.length} (counted from the joint names)` : "– (add joint names to record it)";
}

function renderProfileForm(profile, problems, exists) {
  if (!exists) {
    profileBody.innerHTML = `
      <div class="profile-empty">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="4" width="16" height="16" rx="2" ry="2"></rect><rect x="9" y="9" width="6" height="6"></rect><line x1="9" y1="1" x2="9" y2="4"></line><line x1="15" y1="1" x2="15" y2="4"></line><line x1="9" y1="20" x2="9" y2="23"></line><line x1="15" y1="20" x2="15" y2="23"></line><line x1="20" y1="9" x2="23" y2="9"></line><line x1="20" y1="14" x2="23" y2="14"></line><line x1="1" y1="9" x2="4" y2="9"></line><line x1="1" y1="14" x2="4" y2="14"></line></svg>
        <p>No robot profile yet for this <code>samples/</code> directory.</p>
        <p class="empty-hint">Filled in once for this rig -- every episode recorded afterward picks up these values automatically.</p>
        <button id="createProfileBtn" class="primary btn-lg">Create profile</button>
      </div>`;
    document.getElementById("createProfileBtn").onclick = async () => {
      try {
        const result = await api("/api/profile", { method: "POST" });
        renderProfileForm(result.profile, result.problems, result.exists);
      } catch (err) {
        profileBody.innerHTML += `<p class="status-msg">Error: ${err.message}</p>`;
      }
    };
    return;
  }

  const m = profile.manipulator || {};
  const ee = profile.end_effector || {};
  const ll = profile.low_level || {};
  const cams = profile.cameras || [];
  const bf = profile.board_fabrication || {};

  const problemsHtml = problems.length
    ? `<div class="problem-banner"><ul class="problem-list">${problems.map((p) => `<li>${p}</li>`).join("")}</ul></div>`
    : `<div class="ok-banner">Profile is complete.</div>`;

  const REQ = { text: "Required", cls: "required-badge" };
  const REC = { text: "Recommended", cls: "recommended-badge" };

  profileBody.innerHTML = `
    <p class="profile-intro">Filled in once for this rig. See the <a href="https://hiveboard-bench.github.io/hivedocs/" target="_blank" rel="noopener noreferrer">HiveBoard documentation ↗</a> for setup guidance. Editing it only affects episodes recorded afterward -- already-recorded ones keep their original snapshot.</p>
    ${problemsHtml}
    <div id="profileProgress" class="profile-progress"></div>
    <form id="profileForm">
      ${profileGroupTitle("Required to record", "Episodes cannot be recorded or validated without these")}
      ${profileCard(IC_ARM, "Rig and manipulator", REQ, "The rig, its robot and gripper, and its control frequency", `
        <div class="field-grid">
          <label class="full" data-tooltip="Name of the policy being evaluated (or the teleoperation device). Used when a recording does not name its own; override per recording with EpisodeWriter(policy=...).">${fieldLabel("Policy name")}
            <input name="policy" value="${escapeHtml(profile.policy || "")}" placeholder="e.g. teleop_spacemouse, vla_pi0, diffusion_policy">
          </label>
          <label class="full" data-tooltip="Identifier for the robot platform">${fieldLabel("Robot name")}
            <input name="robot_name" value="${escapeHtml(profile.robot_name || m.model || "")}" placeholder="e.g. franka_panda, ur5e">
          </label>
          <label class="full" data-tooltip="Identifier for the gripper">${fieldLabel("Gripper name")}
            <input name="gripper_name" value="${escapeHtml(profile.gripper_name || "")}" placeholder="e.g. robotiq_2f_85">
          </label>
          <label class="full toggle-row" data-tooltip="Whether the setup has two arms"><span class="field-label-text">Bimanual (two arms)</span>
            <span class="switch"><input type="checkbox" name="is_biarm" ${profile.is_biarm === true ? "checked" : ""}><span class="switch-slider"></span></span>
          </label>
          <label class="full toggle-row" data-tooltip="Whether the platform has a mobile base"><span class="field-label-text">Mobile base</span>
            <span class="switch"><input type="checkbox" name="uses_mobile_base" ${profile.uses_mobile_base === true ? "checked" : ""}><span class="switch-slider"></span></span>
          </label>
          <label class="full" data-tooltip="Control frequency in Hz: how often commands are sent and states are recorded. The recorded rate is checked against it.">${fieldLabel("Control frequency (Hz)")}
            <input name="control_freq" type="number" min="1" step="any" value="${profile.control_freq ?? ""}" placeholder="e.g. 100">
          </label>
          <label class="full" data-tooltip="Robot state joint names: what each index of the joint state is. Required when the action space has joint_position or joint_velocity."><span class="field-label-text">Robot state joint names (comma-separated, in order)<span class="required-mark" id="jointNamesMark" title="Required with a joint action" hidden>*</span></span>
            <input name="manipulator.joint_names" value="${escapeHtml((m.joint_names || []).join(", "))}" placeholder="e.g. panda_joint1, panda_joint2, …">
          </label>
          <div class="full derived-line">Degrees of freedom: <b id="dofReadout"></b></div>
        </div>`)}
      ${profileCard(IC_LL, "Action space", REQ, "What the policy outputs. Pick at least one arm action and one gripper action", `
        <div class="field-grid">
          <label class="full">${fieldLabel("Arm action (one or more)")}${actionChipsHtml(ARM_ACTIONS, profile.action_space || [])}</label>
          <label class="full">${fieldLabel("Gripper action (one or more)")}${actionChipsHtml(GRIPPER_ACTIONS, profile.action_space || [])}</label>
          <label class="full" data-tooltip="Names of the entries of the joint action vector, in order. Required when the action space has joint_position or joint_velocity."><span class="field-label-text">Action joint names (comma-separated, in order)<span class="required-mark" id="actionJointMark" title="Required with a joint action" hidden>*</span></span>
            <input name="action_joint_names" value="${escapeHtml((profile.action_joint_names || []).join(", "))}" placeholder="e.g. panda_joint1, panda_joint2, …">
          </label>
          <label class="full" id="baseActionField" ${profile.uses_mobile_base === true ? "" : "hidden"} data-tooltip="Wheeled mobile platform: pick at most one base command.">${fieldLabel("Base command (at most one)")}
            ${segmentedControlHtml("base_action", Object.keys(BASE_ACTIONS), Object.keys(BASE_ACTIONS).find((k) => (profile.action_space || []).includes(k)) || "", BASE_ACTIONS, true)}
          </label>
        </div>`)}
      ${profileCard(IC_LL, "Low-level control", REQ, "Who runs the real-time control loop", `
        <div class="field-grid">
          <label class="full">${fieldLabel("Mode")}
            ${segmentedControlHtml("low_level.mode", LOW_LEVEL_MODES, ll.mode, LOW_LEVEL_MODE_TITLES, false)}
          </label>
          <label class="full" id="customControllerField" ${ll.mode === "custom" ? "" : "hidden"}>${fieldLabel("Describe your custom controller")}
            <textarea name="low_level.custom_description" placeholder="What it is and how it works: control law, loop structure, sensing, anything needed to compare results">${escapeHtml(ll.custom_description || "")}</textarea>
          </label>
        </div>`)}
      ${profileCard(IC_EE, "End effector", REQ, "Attached gripper or hand and how it is commanded", `
        <div class="field-grid">
          <label class="full">${fieldLabel("Type")}
            ${segmentedControlHtml("end_effector.type", EE_TYPES, ee.type, EE_TYPE_TITLES, false)}
          </label>
          <label class="full" id="eeOtherField" ${ee.type === "other" ? "" : "hidden"}>${fieldLabel("Describe the end effector")}
            <textarea name="end_effector.type_description" rows="2" placeholder="What it is and how it works, e.g. a three-finger underactuated hand with a suction cup">${escapeHtml(ee.type_description || "")}</textarea>
          </label>
          <label class="full">${fieldLabel("Actuated DoF")}
            <input name="end_effector.actuated_dof" type="number" value="${ee.actuated_dof ?? ""}" placeholder="e.g. 1">
          </label>
          <label class="full">${fieldLabel("Command modality")}
            ${segmentedControlHtml("end_effector.command_modality", EE_MODALITIES, ee.command_modality, EE_MODALITY_TITLES, false)}
          </label>
        </div>`)}
      ${profileCard(IC_CAM, "Cameras", { text: "At least one required", cls: "required-badge" }, "Just name each camera. Resolution, fps and encoding are read from the recorded videos, and all cameras must match", `
        <div id="cameraList" class="camera-list">${cams.map((c, i) => cameraEntryHtml(c, i)).join("")}</div>
        <button type="button" id="addCameraBtn" class="btn-icon">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line></svg>
          <span>Add camera</span>
        </button>`)}

      ${profileGroupTitle("Recommended", "Not enforced, but improves comparability between labs")}
      ${profileCard(IC_GEN2, "Orientation", REC, "How rotations are stored in the recorded pose and in the actions", `
        <div class="field-grid">
          <label class="full" data-tooltip="Required when the action space has cartesian_position: how the orientation of the commanded pose is expressed."><span class="field-label-text">Action orientation representation<span class="required-mark" id="actionOrientMark" title="Required with a cartesian_position action" ${(profile.action_space || []).includes("cartesian_position") ? "" : "hidden"}>*</span></span>
            ${orientationSelectHtml("orientation_representation", profile.orientation_representation)}
          </label>
          <label class="full" data-tooltip="Same options. How the orientation of the recorded end-effector pose (ee_pose) is expressed."><span class="field-label-text">Recorded pose orientation representation<span class="required-mark" id="stateOrientMark" title="Required with a Cartesian action" ${((profile.action_space || []).some((a) => a.startsWith("cartesian"))) ? "" : "hidden"}>*</span></span>
            ${orientationSelectHtml("robot_state_orientation_representation", profile.robot_state_orientation_representation)}
          </label>
        </div>`, "orientation")}
      ${profileCard(IC_GAIN, "Gains", REC, "Controller gains, following the active controller", `
        <div class="field-grid">
          <div class="full gains-field">
            <span class="field-label-text">Active controller</span>
            ${segmentedControlHtml("gains_controller", Object.keys(GAIN_FIELDS), gainsController(profile.gains), GAIN_TITLES, true)}
            <div id="gainsInputs" class="gains-inputs">${gainsInputsHtml(gainsController(profile.gains), profile.gains)}</div>
          </div>
        </div>`)}
      ${profileCard(IC_CALIB, "Camera calibration", REC, "Intrinsic (3x3) and extrinsic (4x4) matrices for each camera", calibrationHtml(cams, profile))}
      ${profileCard(IC_GEN, "Workspace and HiveBoard", REC, "Board mounting and HiveBoard version", `
        <div class="field-grid">
          <label class="full">Board mounting
            ${segmentedControlHtml("board_mounting", BOARD_MOUNTINGS, profile.board_mounting, BOARD_MOUNTING_TITLES, true)}
          </label>
          <label class="full">HiveBoard version <input name="hiveboard_version" value="${escapeHtml(profile.hiveboard_version || "")}" placeholder="e.g. v2"></label>
        </div>`)}
      ${profileCard(IC_BOARD, "Board fabrication", REC, "3D printing specifications matching the Evaluation Runner setup", `
        <div class="field-grid">
          <label>Printer <input name="board_fabrication.printer" value="${escapeHtml(bf.printer || "")}" placeholder="Manufacturer and model"></label>
          <label>Material <input name="board_fabrication.material" value="${escapeHtml(bf.material || "")}" placeholder="Filament type and manufacturer"></label>
          <label class="full">Print settings
            <textarea name="board_fabrication.print_settings" placeholder="Nozzle, layer height, walls, infill, and part orientation">${escapeHtml(bf.print_settings || "")}</textarea>
          </label>
          <label class="full">Post-processing
            <textarea name="board_fabrication.post_processing" placeholder="Sanding, lubrication, dimensional adjustments, or None">${escapeHtml(bf.post_processing || "")}</textarea>
          </label>
          <label class="full">Calibration notes
            <input name="board_fabrication.calibration_notes" value="${escapeHtml(bf.calibration_notes || "")}" placeholder="Relevant calibration or setup changes">
          </label>
        </div>`)}

      <div class="actions profile-actions">
        <button type="submit" class="primary btn-lg">Save profile</button>
      </div>
      <div class="status-msg" id="profileMsg"></div>
    </form>
  `;

  const cameraList = document.getElementById("cameraList");
  document.getElementById("addCameraBtn").onclick = () => {
    const count = cameraList.querySelectorAll(".camera-entry").length;
    cameraList.insertAdjacentHTML("beforeend", cameraEntryHtml({}, count));
  };
  cameraList.addEventListener("click", (e) => {
    const btn = e.target.closest(".remove-camera");
    if (btn) btn.closest(".camera-entry").remove();
  });
  cameraList.addEventListener("input", (e) => {
    if (e.target.dataset.field === "name") {
      const entry = e.target.closest(".camera-entry");
      const titleSpan = entry.querySelector(".cam-header-name") || entry.querySelector(".camera-entry-header span");
      if (titleSpan) titleSpan.textContent = e.target.value || "New camera";
    }
  });

  const form = document.getElementById("profileForm");
  wireSegmentedControls(form);
  form.querySelectorAll(".profile-card.collapsible > .card-header-with-icon").forEach((h) => {
    const toggle = () => { const card = h.parentElement; const open = card.classList.toggle("open"); h.setAttribute("aria-expanded", String(open)); };
    h.addEventListener("click", toggle);
    h.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); } });
  });
  form.addEventListener("segmentchange", (e) => {
    if (e.target.dataset.name !== "gains_controller") return;
    document.getElementById("gainsInputs").innerHTML = gainsInputsHtml(form.elements["gains_controller"].value, null);
  });
  const refreshProgress = () => updateProfileProgress(form);
  form.addEventListener("input", refreshProgress);
  form.addEventListener("change", refreshProgress);
  form.addEventListener("segmentchange", refreshProgress);
  cameraList.addEventListener("click", () => setTimeout(refreshProgress, 0));
  document.getElementById("addCameraBtn").addEventListener("click", () => setTimeout(refreshProgress, 0));
  refreshProgress();
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(form);
    const payload = { manipulator: {}, end_effector: {}, low_level: {}, board_fabrication: {} };
    for (const [key, value] of fd.entries()) {
      if (key === "action_space" || key === "base_action" || key === "gains_controller" || key === "is_biarm" || key === "uses_mobile_base") continue;
      if (key.includes(".")) {
        const [group, field] = key.split(".");
        payload[group][field] = value === "" ? null : (field === "dof" || field === "actuated_dof" || field === "rate_hz" ? Number(value) : (field === "joint_names" ? value.split(",").map((s) => s.trim()).filter(Boolean) : value));
      } else {
        payload[key] = value === "" ? null : value;
      }
    }
    payload.cameras = Array.from(document.querySelectorAll("#cameraList .camera-entry")).map((entry) => {
      const cam = {};
      entry.querySelectorAll("input").forEach((inp) => {
        const f = inp.dataset.field;
        cam[f] = inp.value === "" ? null : (f === "fps" ? Number(inp.value) : inp.value);
      });
      return cam;
    }).filter((c) => c.name);
    payload.action_space = fd.getAll("action_space");
    payload.action_joint_names = String(fd.get("action_joint_names") || "").split(",").map((x) => x.trim()).filter(Boolean);
    payload.is_biarm = !!form.elements["is_biarm"].checked;
    payload.uses_mobile_base = !!form.elements["uses_mobile_base"].checked;
    if (payload.uses_mobile_base && fd.get("base_action")) payload.action_space.push(fd.get("base_action"));   // at most one
    const gainsChoice = fd.get("gains_controller");
    payload.gains = null;
    if (gainsChoice) {
      const values = {};
      form.querySelectorAll("[data-gain]").forEach((inp) => { values[inp.dataset.gain] = inp.value.split(",").map((x) => x.trim()).filter(Boolean).map(Number); });
      payload.gains = { [gainsChoice]: values };
    }
    payload.intrinsic_calibration_matrix = {};
    payload.extrinsic_calibration_matrix = {};
    form.querySelectorAll("[data-calib]").forEach((ta) => {
      const m = textToMatrix(ta.value);
      if (m) payload[`${ta.dataset.calib}_calibration_matrix`][ta.dataset.cam] = m;
    });
    if (payload.low_level.mode !== "custom") payload.low_level.custom_description = null;
    if (payload.end_effector.type !== "other") payload.end_effector.type_description = null;
    payload.units_and_frames = profile.units_and_frames || {};

    const msg = document.getElementById("profileMsg");
    try {
      const result = await api("/api/profile", {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
      });
      msg.textContent = result.problems.length ? `Saved, but still incomplete (${result.problems.length} issue(s)).` : "Saved. Profile is complete.";
      renderProfileForm(result.profile, result.problems, result.exists);
      attachmentsCache = null; // harmless cache-bust
      await refreshList();
    } catch (err) {
      msg.textContent = `Error: ${err.message}`;
    }
  });
}

async function openProfileOverlay() {
  profileOverlay.classList.remove("hidden");
  try {
    const result = await api("/api/profile");
    renderProfileForm(result.profile, result.problems, result.exists);
  } catch (err) {
    profileBody.innerHTML = `<p class="status-msg">Error: ${err.message}</p>`;
  }
}

window.openProfileOverlay = openProfileOverlay;
window._loadProfile = openProfileOverlay;

profileBtn.addEventListener("click", openProfileOverlay);
profileCloseBtn.addEventListener("click", () => profileOverlay.classList.add("hidden"));
profileOverlay.addEventListener("click", (e) => {
  if (e.target === profileOverlay) profileOverlay.classList.add("hidden");
});

// The statistics refresh by themselves while the panel is open, so new episodes,
// annotations and uploads show up without reopening it.
let statsTimer = null;
let statsLastKey = "";

async function refreshStats(force = false) {
  try {
    const data = await api("/api/statistics");
    const key = JSON.stringify(data);
    if (!force && key === statsLastKey) return;            // nothing changed: do not redraw
    statsLastKey = key;
    const scroller = statsBody.closest(".overlay-panel");
    const top = scroller ? scroller.scrollTop : 0;
    renderStats(data);
    if (scroller) scroller.scrollTop = top;
  } catch (err) {
    if (force) statsBody.innerHTML = `<p class="status-msg">Error: ${escapeHtml(err.message)}</p>`;
  }
}

async function openStatsOverlay() {
  if (!statsOverlay) return;
  statsOverlay.classList.remove("hidden");
  statsBody.innerHTML = '<p class="status-msg">Loading statistics…</p>';
  statsLastKey = "";
  await refreshStats(true);
  if (statsTimer) window.clearInterval(statsTimer);
  statsTimer = window.setInterval(() => {
    if (statsOverlay.classList.contains("hidden")) { window.clearInterval(statsTimer); statsTimer = null; return; }
    if (!document.hidden) refreshStats();
  }, 2500);
}
window.openStatsOverlay = openStatsOverlay;
window._loadStats = openStatsOverlay;

// Statistics are split by plan (session). "" means all plans together.
let statsPlan = null;

function planLabel(p) {
  const mode = p.mode ? ` \u00b7 ${humanize(p.mode)}` : "";
  return `${p.date || p.session_id}${p.operator_name ? ` \u00b7 ${escapeHtml(p.operator_name)}` : ""}${mode}`;
}

function renderStats(data) {
  const plans = data.plans || [];
  if (statsPlan === null || (statsPlan !== "" && !plans.some((p) => p.session_id === statsPlan))) {
    statsPlan = plans.length ? plans[0].session_id : "";   // default: the most recent plan
  }
  const view = statsPlan === "" ? data : plans.find((p) => p.session_id === statsPlan);
  const showPlanColumn = statsPlan === "" && plans.length > 1;
  const tabs = plans.length ? `
    <div class="stats-plans" role="tablist" aria-label="Plans">
      ${plans.map((p) => `<button type="button" role="tab" class="stats-plan-tab${p.session_id === statsPlan ? " active" : ""}" data-plan="${escapeHtml(p.session_id)}" title="${escapeHtml(p.session_id)}">
        <b>${planLabel(p)}</b><small>${p.summary.total_trials}/${p.summary.total_target_trials} trials</small></button>`).join("")}
      ${plans.length > 1 ? `<button type="button" role="tab" class="stats-plan-tab${statsPlan === "" ? " active" : ""}" data-plan=""><b>All plans</b><small>${data.summary.total_trials} trials</small></button>` : ""}
    </div>` : "";
  renderStatsView(view, tabs, showPlanColumn, statsPlan !== "" ? plans.find((p) => p.session_id === statsPlan) : null);
  statsBody.querySelectorAll(".stats-plan-tab").forEach((b) => (b.onclick = () => { statsPlan = b.dataset.plan; renderStats(data); }));
}

function renderStatsView(data, tabsHtml, showPlanColumn, plan) {
  const summary = data.summary || {};
  const readiness = data.readiness || {};
  const checks = readiness.checks || [];
  const conditions = data.conditions || [];
  const trials = data.trials || [];

  const trialsHtml = trials.length
    ? `
      <div class="stats-trials-section">
        <h3 class="stats-subtitle">Recorded trials (${trials.length})</h3>
        <div class="table-wrap">
          <table class="stats-table">
            <thead>
              <tr>
                <th>Trial</th>
                ${showPlanColumn ? "<th>Plan</th>" : ""}
                <th>Attachment</th>
                <th>Outcome</th>
                <th>Time (s)</th>
                <th>Attempts</th>
                <th>Regrasps</th>
              </tr>
            </thead>
            <tbody>
              ${trials.map((t) => `
                <tr class="stats-trial-row ${t.episode_id ? 'clickable' : ''}" data-episode-id="${escapeHtml(t.episode_id || "")}" title="${t.episode_id ? `Click to view episode ${escapeHtml(t.episode_id)}` : ""}">
                  <td><code>${escapeHtml(t.trial_id || "—")}</code></td>
                  ${showPlanColumn ? `<td><small>${escapeHtml(t.session_id || "")}</small></td>` : ""}
                  <td><code>${escapeHtml(t.attachment_name || t.attachment_id || "—")}</code></td>
                  <td><span class="outcome-badge outcome-${escapeHtml(t.outcome || "")}">${escapeHtml(humanize(t.outcome || "—"))}</span></td>
                  <td>${escapeHtml(t.completion_time_s != null ? String(t.completion_time_s) : "—")}</td>
                  <td>${escapeHtml(t.n_attempts != null ? String(t.n_attempts) : "—")}</td>
                  <td>${escapeHtml(t.n_regrasps != null ? String(t.n_regrasps) : "—")}</td>
                </tr>
              `).join("")}
            </tbody>
          </table>
        </div>
      </div>
    `
    : `
      <div class="stats-trials-section">
        <h3 class="stats-subtitle">Recorded trials</h3>
        <div class="empty-hint" style="padding: 1.5rem 0;">No trials recorded yet.</div>
      </div>
    `;

  statsBody.innerHTML = `
    ${tabsHtml}
    ${plan ? `<p class="stats-plan-meta"><code>${escapeHtml(plan.session_id)}</code>${plan.has_plan ? "" : " &middot; no Runner plan (episodes recorded directly)"}</p>` : ""}
    <div class="review-stats">
      <div>
        <strong>${summary.total_trials || 0}</strong>
        <span>Total trials</span>
      </div>
      <div>
        <strong>${summary.successful_trials || 0}</strong>
        <span>Successful</span>
      </div>
      <div>
        <strong>${summary.conditions_tested || 0}</strong>
        <span>Conditions</span>
      </div>
    </div>

    <section class="readiness-panel" aria-labelledby="readiness-title">
      <div class="readiness-heading">
        <div>
          <p class="eyebrow">Submission package</p>
          <h3 id="readiness-title">Check trial records</h3>
        </div>
        <strong class="${readiness.status_class || (readiness.is_complete ? 'ready' : 'incomplete')}">
          ${escapeHtml(readiness.status_label || (readiness.is_complete ? 'Trial records complete' : 'Trial records incomplete'))}
        </strong>
      </div>
      <ul class="validation-list">
        ${checks.map((c) => `
          <li class="${c.passed ? 'passed' : ''}">
            <span aria-hidden="true">${c.passed ? '✓' : '—'}</span>
            ${escapeHtml(c.label)}
          </li>
        `).join('')}
      </ul>
      <div class="condition-progress">
        ${conditions.map((c) => `
          <div class="condition-item ${c.complete ? 'complete' : ''} clickable" data-task-id="${escapeHtml(c.id)}" title="Click to filter by ${escapeHtml(c.name)}">
            <span>${escapeHtml(c.name)}</span>
            <strong>${c.count}/${c.target}</strong>
          </div>
        `).join('')}
      </div>
      <p class="readiness-note">${plan && plan.has_plan
        ? `This plan has ${summary.total_target_trials} trials over ${summary.conditions_total} conditions. A complete HiveBoard submission requires five trials for each of the 13 conditions (65 trials).`
        : "A complete submission requires five trials for each of the 13 conditions (65 trials total)."}</p>
    </section>

    ${trialsHtml}
  `;

  statsBody.querySelectorAll(".condition-item.clickable").forEach((el) => {
    el.addEventListener("click", () => {
      const taskId = el.dataset.taskId;
      if (taskId && searchEl) {
        searchEl.value = taskId;
        refreshList();
        statsOverlay.classList.add("hidden");
      }
    });
  });

  statsBody.querySelectorAll(".stats-trial-row.clickable").forEach((row) => {
    row.addEventListener("click", () => {
      const epId = row.dataset.episodeId;
      if (epId) {
        statsOverlay.classList.add("hidden");
        selectEpisode(epId);
      }
    });
  });
}

if (statsBtn) statsBtn.addEventListener("click", openStatsOverlay);
if (statsCloseBtn) statsCloseBtn.addEventListener("click", () => statsOverlay.classList.add("hidden"));
if (statsOverlay) {
  statsOverlay.addEventListener("click", (e) => {
    if (e.target === statsOverlay) statsOverlay.classList.add("hidden");
  });
}

taskCloseBtn.addEventListener("click", () => taskOverlay.classList.add("hidden"));
taskOverlay.addEventListener("click", (e) => {
  if (e.target === taskOverlay) taskOverlay.classList.add("hidden");
});

function openFailureHelpOverlay() {
  if (failureHelpOverlay) failureHelpOverlay.classList.remove("hidden");
}

function closeFailureHelpOverlay() {
  if (failureHelpOverlay) failureHelpOverlay.classList.add("hidden");
}

if (failureHelpCloseBtn) failureHelpCloseBtn.addEventListener("click", closeFailureHelpOverlay);
if (failureHelpOverlay) {
  failureHelpOverlay.addEventListener("click", (e) => {
    if (e.target === failureHelpOverlay) closeFailureHelpOverlay();
  });
  failureHelpOverlay.querySelectorAll("tbody tr").forEach((row) => {
    row.style.cursor = "pointer";
    row.setAttribute("title", "Click to select this cause");
    row.addEventListener("click", () => {
      const cause = row.dataset.cause;
      const form = document.getElementById("annForm");
      if (form && form.failure_cause) {
        form.failure_cause.value = cause;
        form.failure_cause.dispatchEvent(new Event("change", { bubbles: true }));
        closeFailureHelpOverlay();
      }
    });
  });
}

searchEl.addEventListener("input", refreshList);
statusEl.addEventListener("change", refreshList);
const syncBtnLabel = syncBtn.querySelector("span");
syncBtn.addEventListener("click", async () => {
  syncBtn.disabled = true;
  syncBtnLabel.textContent = "Syncing…";
  try {
    const report = await api("/api/sync", { method: "POST" });
    if (report.upload_failed && report.upload_failed.length) {
      showToast(
        `${report.upload_failed.length} episode(s) failed to upload: ${report.upload_failed.join(", ")}`,
        { type: "error", title: "Sync failed" },
      );
    } else {
      const total = (report.uploaded || []).length;
      if (total) showToast(`Uploaded ${total} episode(s).`, { type: "success", title: "Sync complete", timeout: 4000 });
    }
  } catch (err) {
    showToast(err.message, { type: "error", title: "Sync failed" });
  } finally {
    syncBtn.disabled = false;
    syncBtnLabel.textContent = "Sync";
    await refreshList();
    await refreshHubStatus();
    await renderListStats();
  }
});

const prevBtn = document.getElementById("prevBtn");
const nextBtn = document.getElementById("nextBtn");
const nextUnannotatedBtn = document.getElementById("nextUnannotatedBtn");

if (prevBtn) prevBtn.addEventListener("click", () => navigateBy(-1));
if (nextBtn) nextBtn.addEventListener("click", () => navigateBy(1));
if (nextUnannotatedBtn) nextUnannotatedBtn.addEventListener("click", () => navigateToNextUnannotated());

window.addEventListener("keydown", (e) => {
  // Global Save shortcuts (Ctrl+Enter, Cmd+Enter, Ctrl+S, Cmd+S)
  if ((e.ctrlKey || e.metaKey) && (e.key === "Enter" || e.key === "s" || e.key === "S")) {
    const form = document.getElementById("annForm");
    if (form && !form.closest(".hidden") && selectedId) {
      e.preventDefault();
      const saveBtn = form.querySelector('button[type="submit"]');
      if (saveBtn && !saveBtn.disabled) {
        if (typeof form.requestSubmit === "function") {
          form.requestSubmit();
        } else {
          form.dispatchEvent(new Event("submit", { cancelable: true, bubbles: true }));
        }
      }
      return;
    }
  }

  if (e.key === "Escape") {
    if (failureHelpOverlay && !failureHelpOverlay.classList.contains("hidden")) {
      closeFailureHelpOverlay();
      return;
    }
    if (profileOverlay && !profileOverlay.classList.contains("hidden")) {
      profileOverlay.classList.add("hidden");
      return;
    }
    if (statsOverlay && !statsOverlay.classList.contains("hidden")) {
      statsOverlay.classList.add("hidden");
      return;
    }
    if (taskOverlay && !taskOverlay.classList.contains("hidden")) {
      taskOverlay.classList.add("hidden");
      return;
    }
  }

  const profileOpen = profileOverlay && !profileOverlay.classList.contains("hidden");
  const statsOpen = statsOverlay && !statsOverlay.classList.contains("hidden");
  const taskOpen = taskOverlay && !taskOverlay.classList.contains("hidden");
  const failureHelpOpen = failureHelpOverlay && !failureHelpOverlay.classList.contains("hidden");
  if (profileOpen || statsOpen || taskOpen || failureHelpOpen) return;

  const t = e.target;
  const typing = t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable);
  if (typing) return;

  if (e.code === "Space" || e.key === " ") {
    const mediaCard = document.getElementById("mediaCard");
    if (mediaCard && !mediaCard.classList.contains("hidden") && currentVideoElements.length) {
      e.preventDefault();
      toggleVideosPlay();
      return;
    }
  }
  if (e.key === "," || e.key === "<") {
    const mediaCard = document.getElementById("mediaCard");
    if (mediaCard && !mediaCard.classList.contains("hidden") && currentVideoElements.length) {
      e.preventDefault();
      stepVideos(-1);
      return;
    }
  }
  if (e.key === "." || e.key === ">") {
    const mediaCard = document.getElementById("mediaCard");
    if (mediaCard && !mediaCard.classList.contains("hidden") && currentVideoElements.length) {
      e.preventDefault();
      stepVideos(1);
      return;
    }
  }

  if (e.key === "ArrowRight" || e.key === "n" || e.key === "j") {
    e.preventDefault();
    navigateBy(1);
    return;
  }
  if (e.key === "ArrowLeft" || e.key === "p" || e.key === "k") {
    e.preventDefault();
    navigateBy(-1);
    return;
  }
});

refreshList();
refreshHubStatus();
renderListStats();
updateNavButtons();


// --- Trajectory viewer (cartesian_position actions): last 100 steps, scrub with a slider ---

const TRAJ_WINDOW = 100;

function wireTrajectory(episodeId) {
  const toggle = document.getElementById("trajToggle");
  const body = document.getElementById("trajBody");
  if (!toggle || !body) return;
  let loaded = false;
  toggle.addEventListener("click", async () => {
    const nowHidden = !body.hidden;
    body.hidden = nowHidden;
    toggle.classList.toggle("collapsed", nowHidden);
    if (nowHidden || loaded) return;
    loaded = true;
    body.innerHTML = '<p class="muted">Loading&hellip;</p>';
    try {
      const { points } = await api(`/api/episodes/${encodeURIComponent(episodeId)}/cartesian-path`);
      if (!points.length) { body.innerHTML = '<p class="muted">No cartesian_position data in this episode.</p>'; return; }
      trajectoryRender(body, points);
    } catch (e) {
      body.innerHTML = `<p class="muted">Could not load the trajectory: ${escapeHtml(e.message || e)}</p>`;
    }
  });
}

function trajectoryRender(body, points) {
  const n = points.length;
  body.innerHTML = `
    <div class="traj-views">
      <figure><canvas class="traj-canvas" data-plane="xy" width="420" height="300"></canvas><figcaption>Top view (x, y)</figcaption></figure>
      <figure><canvas class="traj-canvas" data-plane="xz" width="420" height="300"></canvas><figcaption>Side view (x, z)</figcaption></figure>
    </div>
    <div class="traj-controls">
      <input type="range" id="trajSlider" min="0" max="${n - 1}" value="${n - 1}" aria-label="Trajectory position">
      <span class="traj-readout" id="trajReadout"></span>
    </div>`;
  const slider = body.querySelector("#trajSlider");
  const readout = body.querySelector("#trajReadout");
  const xs = points.map((p) => p[0]), ys = points.map((p) => p[1]), zs = points.map((p) => p[2]);
  const span = (a) => { const lo = Math.min(...a), hi = Math.max(...a); return [lo, hi - lo || 1]; };
  const ranges = { x: span(xs), y: span(ys), z: span(zs) };
  const css = getComputedStyle(document.documentElement);
  const color = (v, d) => css.getPropertyValue(v).trim() || d;

  function draw(end) {
    const start = Math.max(0, end - TRAJ_WINDOW + 1);
    body.querySelectorAll(".traj-canvas").forEach((cv) => {
      const [a, b] = cv.dataset.plane === "xy" ? [0, 1] : [0, 2];
      const [ka, kb] = cv.dataset.plane === "xy" ? ["x", "y"] : ["x", "z"];
      const ctx = cv.getContext("2d");
      const W = cv.width, H = cv.height, pad = 24;
      const scale = Math.min((W - 2 * pad) / ranges[ka][1], (H - 2 * pad) / ranges[kb][1]);
      const px = (p) => pad + (p[a] - ranges[ka][0]) * scale;
      const py = (p) => H - pad - (p[b] - ranges[kb][0]) * scale;
      ctx.clearRect(0, 0, W, H);
      ctx.strokeStyle = color("--border", "#ccc");
      ctx.lineWidth = 1;
      ctx.beginPath();
      points.forEach((p, i) => (i ? ctx.lineTo(px(p), py(p)) : ctx.moveTo(px(p), py(p))));
      ctx.globalAlpha = 0.5; ctx.stroke(); ctx.globalAlpha = 1;
      ctx.strokeStyle = color("--accent", "#2563eb");
      ctx.lineWidth = 2.5;
      for (let i = start + 1; i <= end; i++) {
        ctx.globalAlpha = 0.2 + 0.8 * ((i - start) / Math.max(1, end - start));
        ctx.beginPath(); ctx.moveTo(px(points[i - 1]), py(points[i - 1])); ctx.lineTo(px(points[i]), py(points[i])); ctx.stroke();
      }
      ctx.globalAlpha = 1;
      ctx.fillStyle = color("--accent-solid", "#2563eb");
      ctx.beginPath(); ctx.arc(px(points[end]), py(points[end]), 5, 0, Math.PI * 2); ctx.fill();
    });
    const p = points[end];
    readout.textContent = `step ${end + 1} / ${n}  ·  x ${p[0].toFixed(3)}  y ${p[1].toFixed(3)}  z ${p[2].toFixed(3)}`;
  }
  slider.addEventListener("input", () => draw(Number(slider.value)));
  draw(n - 1);
}
