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
const taskOverlay = document.getElementById("taskOverlay");
const taskCloseBtn = document.getElementById("taskCloseBtn");
const toastContainer = document.getElementById("toastContainer");
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
let attachmentsCache = null;
const selectedEpisodes = new Set();

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

// A button-group ("segmented control") in place of a native <select>, for
// short enum fields where clicking an option beats picking from a
// dropdown. Renders a hidden input (so it's a normal form field on
// submit) plus one button per option; wireSegmentedControls() below
// handles the click-to-select behavior for every one of these in a form.
function segmentedControlHtml(name, options, current) {
  const buttons = options.map((v) => `<button type="button" class="segment${v === current ? " active" : ""}" data-value="${v}">${humanize(v)}</button>`).join("");
  return `<input type="hidden" name="${name}" value="${current || ""}"><div class="segmented" data-name="${name}">${buttons}</div>`;
}

function wireSegmentedControls(form) {
  form.querySelectorAll(".segmented").forEach((group) => {
    group.addEventListener("click", (e) => {
      const btn = e.target.closest(".segment");
      if (!btn) return;
      form.elements[group.dataset.name].value = btn.dataset.value;
      group.querySelectorAll(".segment").forEach((b) => b.classList.toggle("active", b === btn));
      group.dispatchEvent(new Event("segmentchange", { bubbles: true }));
    });
  });
}

// "Stage reached" as a segmented control of the selected task's actual
// stage names (0 = "Not started") when the task has stages recorded;
// falls back to a plain number input when it doesn't (unknown task, or
// no stages listed).
function stageFieldHtml(info, current) {
  const stages = info && info.stages;
  if (stages && stages.length) {
    const value = current ?? "";
    const options = [
      { v: "0", label: "Not started" },
      ...stages.map((name, i) => ({ v: String(i + 1), label: `${i + 1}. ${name}` })),
    ];
    const buttons = options.map((o) => `<button type="button" class="segment${String(value) === o.v ? " active" : ""}" data-value="${o.v}">${escapeHtml(o.label)}</button>`).join("");
    return `<input type="hidden" name="stage_reached" value="${value}"><div class="segmented segmented-stages" data-name="stage_reached">${buttons}</div>`;
  }
  return `<input name="stage_reached" type="number" value="${current ?? ""}">`;
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

function taskCardHtml(taskId, info, isCurrent) {
  const img = info.image ? `<img src="/tasks/${info.image}" alt="">` : `<div class="task-card-noimg"></div>`;
  return `
    <button type="button" class="task-card${isCurrent ? " selected" : ""}" data-task-id="${taskId}">
      ${img}
      <span class="task-card-body">
        <strong>${escapeHtml(info.name)}</strong>
        <span>${escapeHtml(info.family || "")}${info.timeout ? ` · ${info.timeout}s` : ""}</span>
        ${info.composed_assembly ? `<span class="task-count">${(info.stages || []).length || info.n_stages || ""} stages</span>` : ""}
      </span>
    </button>`;
}

function openTaskPicker(attachments, currentId, onSelect) {
  const grid = document.getElementById("taskGrid");
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

  grid.innerHTML = families.map((family) => `
    <div class="task-family-group">
      <h4>${escapeHtml(family)}</h4>
      <div class="task-grid">
        ${byFamily.get(family).map(([id, info]) => taskCardHtml(id, info, id === currentId)).join("")}
      </div>
    </div>
  `).join("") || `<p class="empty-hint">No attachments in the registry.</p>`;

  grid.querySelectorAll(".task-card").forEach((card) => {
    card.addEventListener("click", () => {
      onSelect(card.dataset.taskId);
      taskOverlay.classList.add("hidden");
    });
  });

  taskOverlay.classList.remove("hidden");
}

// --- Episode list: grouped by day, with per-row selection checkboxes ---

function dayKey(date) {
  return `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`;
}

function dayLabel(date) {
  const now = new Date();
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (dayKey(date) === dayKey(now)) return "Today";
  if (dayKey(date) === dayKey(yesterday)) return "Yesterday";
  const opts = { weekday: "short", month: "short", day: "numeric" };
  if (date.getFullYear() !== now.getFullYear()) opts.year = "numeric";
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
      </div>
    </div>`;
  row.querySelector(".row-check").addEventListener("click", (e) => {
    e.stopPropagation();
    toggleSelection(ep.episode_id, e.target.checked);
  });
  row.querySelector(".row-main").onclick = () => selectEpisode(ep.episode_id);
  return row;
}

async function refreshList() {
  const q = encodeURIComponent(searchEl.value || "");
  const status = encodeURIComponent(statusEl.value || "");
  const episodes = await api(`/api/episodes?q=${q}&status=${status}`);

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

  listEl.innerHTML = "";
  for (const group of groups) {
    const isCollapsed = collapsedDays.has(group.key);

    const groupEl = document.createElement("div");
    groupEl.className = "day-group";

    const header = document.createElement("div");
    header.className = "list-group-header" + (isCollapsed ? " collapsed" : "");
    header.innerHTML = `
      <svg class="chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"></polyline></svg>
      <span class="day-label">${group.label}</span>
      <span class="day-count">${group.episodes.length}</span>
    `;

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

bulkUploadBtn.addEventListener("click", async () => {
  const ids = Array.from(selectedEpisodes);
  if (!ids.length) return;
  bulkUploadBtn.disabled = true;
  try {
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
  }
});

bulkClearBtn.addEventListener("click", () => {
  selectedEpisodes.clear();
  refreshList();
});

// --- Episode detail panel ---

function renderHeader(header) {
  const rows = [
    ["lab_id", header.lab_id], ["platform_id", header.platform_id],
    ["session_id", header.session_id], ["episode_id", header.episode_id],
    ["trial_id", header.trial_id], ["control_mode", header.control_mode],
    ["policy", header.policy], ["board_mounting", header.board_mounting],
    ["hiveboard_version", header.hiveboard_version],
    ["low_level.mode", header.low_level && header.low_level.mode],
    ["manipulator.model", header.manipulator && header.manipulator.model],
    ["cameras", (header.cameras || []).map((c) => c.name).join(", ")],
  ];
  return `<dl class="header-fields">${rows.map(([k, v]) => `<dt>${k}</dt><dd>${v ?? "–"}</dd>`).join("")}</dl>`;
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
  selectedId = episodeId;
  await refreshList();
  const data = await api(`/api/episodes/${episodeId}`);
  const attachments = await loadAttachments();
  const ann = data.annotation || {};
  const outcome = ann.outcome || "success";
  const attachmentId = ann.attachment_id || "";
  const info = attachments[attachmentId];
  const composed = info ? info.composed_assembly : null;
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

    <div class="stat-row">
      <div class="stat"><span class="stat-value">${formatSteps(stats.n_steps)}</span><span class="stat-label">Steps</span></div>
      <div class="stat"><span class="stat-value">${formatDuration(stats.duration_s)}</span><span class="stat-label">Duration</span></div>
      <div class="stat"><span class="stat-value">${formatRate(stats.sample_rate_hz)}</span><span class="stat-label">Rate</span></div>
      <div class="stat"><span class="stat-value">${data.cameras.length}</span><span class="stat-label">Cameras</span></div>
    </div>

    <div class="card">
      <h3 class="collapsible-header" id="overviewToggle">
        <svg class="chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"></polyline></svg>
        Overview
      </h3>
      <div class="collapsible-body" id="overviewBody" hidden>
        ${renderHeader(data.header)}
      </div>
    </div>

    <div class="detail-split">
      <div class="pane camera-pane">
        <h3>Cameras</h3>
        <div class="camera-grid">
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
      </div>

      <div class="pane annotate-pane">
      <h3>Annotate</h3>
      <form class="validate-form" id="annForm">
        <fieldset>
          <div class="field-grid">
            <label>Operator name <input name="operator_name" value="${ann.operator_name || ""}" placeholder="Who ran this trial"></label>
            <label>Annotator name <input name="annotator_name" value="${ann.annotator_name || ""}" placeholder="Who is annotating"></label>
            <label class="full">Task
              <input type="hidden" name="attachment_id" value="${attachmentId}">
              <button type="button" id="taskPickerBtn" class="task-chip">${taskChipHtml(info, attachmentId)}</button>
            </label>
            <label class="full">Outcome
              ${segmentedControlHtml("outcome", OUTCOMES, outcome)}
            </label>
            <label id="failureCauseField" style="${outcome === "success" ? "display:none" : ""}">Failure cause
              <select name="failure_cause">
                ${FAILURE_CAUSES.map((c) => `<option value="${c}" ${c === ann.failure_cause ? "selected" : ""}>${humanize(c)}</option>`).join("")}
              </select>
            </label>
            <label id="failureCauseDetailField" class="full" style="${(outcome === "success" || ann.failure_cause !== "other") ? "display:none" : ""}">Reason
              <input name="failure_cause_detail" value="${ann.failure_cause_detail || ""}" placeholder="What happened?">
            </label>
            <label id="severityField" class="full" style="${outcome === "success" ? "display:none" : ""}">Severity
              ${segmentedControlHtml("severity", SEVERITIES, ann.severity)}
            </label>
            <label id="completionTimeField" style="${outcome !== "success" ? "display:none" : ""}">Completion time
              <input value="${formatDuration(stats.duration_s)}" disabled>
            </label>
            <label>Attempts <input name="n_attempts" type="number" value="${ann.n_attempts || 1}"></label>
            <label>Regrasps <input name="n_regrasps" type="number" value="${ann.n_regrasps || 0}"></label>
            <label id="stageField" class="full" style="${composed ? "" : "display:none"}">Stage reached
              <div id="stageFieldBody">${stageFieldHtml(info, ann.stage_reached)}</div>
            </label>
            <label class="full">Strategy
              ${segmentedControlHtml("strategy", STRATEGIES, ann.strategy)}
            </label>
            <label class="full">Notes
              <textarea name="notes" placeholder="Anything else worth recording about this trial…">${ann.notes || ""}</textarea>
            </label>
          </div>
        </fieldset>

        <div class="actions">
          <button type="submit" class="btn-icon primary">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"></path><polyline points="17 21 17 13 7 13 7 21"></polyline><polyline points="7 3 7 8 15 8"></polyline></svg>
            <span>Save</span>
          </button>
          <button type="button" id="validateBtn" class="btn-icon">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path><polyline points="22 4 12 14.01 9 11.01"></polyline></svg>
            <span>Validate</span>
          </button>
          <button type="button" id="uploadBtn" class="btn-icon primary">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="17 8 12 3 7 8"></polyline><line x1="12" y1="3" x2="12" y2="15"></line></svg>
            <span>Upload</span>
          </button>
          <button type="button" id="deleteBtn" class="btn-icon danger">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"></path><path d="M10 11v6"></path><path d="M14 11v6"></path><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"></path></svg>
            <span>Delete</span>
          </button>
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

  const form = document.getElementById("annForm");
  wireSegmentedControls(form);

  function updateFailureCauseDetailVisibility() {
    const isSuccess = form.outcome.value === "success";
    const isOther = form.failure_cause.value === "other";
    document.getElementById("failureCauseDetailField").style.display = (!isSuccess && isOther) ? "" : "none";
  }

  form.querySelector('.segmented[data-name="outcome"]').addEventListener("segmentchange", () => {
    const isSuccess = form.outcome.value === "success";
    document.getElementById("failureCauseField").style.display = isSuccess ? "none" : "";
    document.getElementById("severityField").style.display = isSuccess ? "none" : "";
    document.getElementById("completionTimeField").style.display = isSuccess ? "" : "none";
    updateFailureCauseDetailVisibility();
  });
  form.failure_cause.addEventListener("change", updateFailureCauseDetailVisibility);

  function applyTaskSelection(taskId) {
    form.attachment_id.value = taskId;
    const a = attachments[taskId];
    document.getElementById("taskPickerBtn").innerHTML = taskChipHtml(a, taskId);
    document.getElementById("stageField").style.display = a && a.composed_assembly ? "" : "none";
    document.getElementById("stageFieldBody").innerHTML = stageFieldHtml(a, null);
    wireSegmentedControls(form);
  }

  document.getElementById("taskPickerBtn").addEventListener("click", () => {
    openTaskPicker(attachments, form.attachment_id.value, applyTaskSelection);
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
    return payload;
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const msg = document.getElementById("statusMsg");
    try {
      await api(`/api/episodes/${episodeId}/annotate`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(collectAnnotationPayload()),
      });
      msg.textContent = "Saved.";
      await selectEpisode(episodeId);
    } catch (err) {
      msg.textContent = `Error: ${err.message}`;
      showToast(err.message, { type: "error", title: "Save failed" });
    }
  });

  document.getElementById("validateBtn").addEventListener("click", async () => {
    const msg = document.getElementById("statusMsg");
    try {
      const result = await api(`/api/episodes/${episodeId}/validate`, { method: "POST" });
      if (result.ok) {
        msg.textContent = "Episode is valid.";
        showToast("Episode is valid.", { type: "success", title: episodeId, timeout: 4000 });
      } else {
        msg.textContent = "Validation failed.";
        showToast(result.error, { type: "error", title: `Validation failed: ${episodeId}`, timeout: 12000 });
      }
      await selectEpisode(episodeId);
    } catch (err) {
      msg.textContent = `Error: ${err.message}`;
      showToast(err.message, { type: "error", title: "Validate failed" });
    }
  });

  document.getElementById("uploadBtn").onclick = async () => {
    const msg = document.getElementById("statusMsg");
    try {
      const result = await api(`/api/episodes/${episodeId}/upload`, { method: "POST" });
      msg.textContent = result.uploaded ? "Uploaded." : `Skipped: ${result.skipped_reason || result.error}`;
      await refreshList();
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
    } catch (err) {
      document.getElementById("statusMsg").textContent = `Error: ${err.message}`;
      showToast(err.message, { type: "error", title: "Delete failed" });
    }
  };
}

// --- Robot profile: create/edit from the interface, same file the CLI's
// `datahive new-profile` writes and `robot_profile.yaml` on disk. ---

let cameraSeq = 0;

function cameraEntryHtml(cam = {}) {
  const id = `cam-${cameraSeq++}`;
  return `
    <div class="camera-entry" data-entry-id="${id}">
      <div class="camera-entry-header">
        <span>${cam.name ? cam.name : "New camera"}</span>
        <button type="button" class="remove-camera" title="Remove camera">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
        </button>
      </div>
      <div class="field-grid">
        <label>Name <input placeholder="external" data-field="name" value="${cam.name || ""}"></label>
        <label>Resolution <input placeholder="1280x720" data-field="resolution" value="${cam.resolution || ""}"></label>
        <label>Encoding <input placeholder="h264" data-field="encoding" value="${cam.encoding || ""}"></label>
        <label>FPS <input placeholder="30" type="number" data-field="fps" value="${cam.fps ?? ""}"></label>
        <label>Position <input placeholder="front" data-field="position" value="${cam.position || ""}"></label>
        <label>Orientation <input placeholder="level" data-field="orientation" value="${cam.orientation || ""}"></label>
      </div>
    </div>`;
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

  profileBody.innerHTML = `
    <p class="profile-intro">Filled in once for this rig. Editing it only affects episodes recorded afterward -- already-recorded ones keep their original snapshot.</p>
    ${problemsHtml}
    <form id="profileForm">
      <div class="card">
        <h3>Manipulator</h3>
        <div class="field-grid">
          <label>Model <input name="manipulator.model" value="${m.model || ""}"></label>
          <label>DoF <input name="manipulator.dof" type="number" value="${m.dof ?? ""}"></label>
          <label class="full">Joint names (comma-separated)
            <input name="manipulator.joint_names" value="${(m.joint_names || []).join(", ")}">
          </label>
        </div>
      </div>

      <div class="card">
        <h3>End effector</h3>
        <div class="field-grid">
          <label>Type
            <select name="end_effector.type">
              <option value="">–</option>
              ${["gripper", "dexterous_hand", "prosthetic_hand"].map((v) => `<option value="${v}" ${ee.type === v ? "selected" : ""}>${humanize(v)}</option>`).join("")}
            </select>
          </label>
          <label>Actuated DoF <input name="end_effector.actuated_dof" type="number" value="${ee.actuated_dof ?? ""}"></label>
          <label>Command modality
            <select name="end_effector.command_modality">
              <option value="">–</option>
              ${["binary", "position", "velocity"].map((v) => `<option value="${v}" ${ee.command_modality === v ? "selected" : ""}>${humanize(v)}</option>`).join("")}
            </select>
          </label>
        </div>
      </div>

      <div class="card">
        <h3>Low-level control <span class="required-badge">Required</span></h3>
        <div class="field-grid">
          <label>Mode
            <select name="low_level.mode" required>
              <option value="">–</option>
              ${["stock", "custom"].map((v) => `<option value="${v}" ${ll.mode === v ? "selected" : ""}>${humanize(v)}</option>`).join("")}
            </select>
          </label>
          <label>Controller type <input name="low_level.controller_type" value="${ll.controller_type || ""}"></label>
          <label>Rate (Hz) <input name="low_level.rate_hz" type="number" value="${ll.rate_hz ?? ""}"></label>
          <label>Gains (free text) <input name="low_level.gains" value="${ll.gains ?? ""}"></label>
        </div>
      </div>

      <div class="card">
        <h3>Cameras <span class="required-badge">At least one required</span></h3>
        <div id="cameraList" class="camera-list">${cams.map(cameraEntryHtml).join("")}</div>
        <button type="button" id="addCameraBtn" class="btn-icon">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line></svg>
          <span>Add camera</span>
        </button>
      </div>

      <div class="card">
        <h3>General</h3>
        <div class="field-grid">
          <label>Control mode <input name="control_mode" value="${profile.control_mode || ""}" placeholder="joint_position"></label>
          <label>Policy <input name="policy" value="${profile.policy || ""}" placeholder="teleop_spacemouse"></label>
          <label>Board mounting
            <select name="board_mounting">
              <option value="">–</option>
              ${["horizontal", "vertical"].map((v) => `<option value="${v}" ${profile.board_mounting === v ? "selected" : ""}>${humanize(v)}</option>`).join("")}
            </select>
          </label>
          <label>HiveBoard version <input name="hiveboard_version" value="${profile.hiveboard_version || ""}"></label>
          <label>Platform ID <input name="platform_id" value="${profile.platform_id || ""}"></label>
        </div>
      </div>

      <div class="card">
        <h3>Board fabrication</h3>
        <p class="field-hint">Same fields as HiveBoard's Evaluation Runner setup details, so a submission there matches this profile.</p>
        <div class="field-grid">
          <label>Printer <input name="board_fabrication.printer" value="${bf.printer || ""}" placeholder="Manufacturer and model"></label>
          <label>Material <input name="board_fabrication.material" value="${bf.material || ""}" placeholder="Filament type and manufacturer"></label>
          <label class="full">Print settings
            <textarea name="board_fabrication.print_settings" placeholder="Nozzle, layer height, walls, infill, and part orientation">${bf.print_settings || ""}</textarea>
          </label>
          <label class="full">Post-processing
            <textarea name="board_fabrication.post_processing" placeholder="Sanding, lubrication, dimensional adjustments, or None">${bf.post_processing || ""}</textarea>
          </label>
          <label class="full">Calibration notes
            <input name="board_fabrication.calibration_notes" value="${bf.calibration_notes || ""}" placeholder="Relevant calibration or setup changes">
          </label>
        </div>
      </div>

      <div class="actions">
        <button type="submit" class="primary btn-lg">Save profile</button>
      </div>
      <div class="status-msg" id="profileMsg"></div>
    </form>
  `;

  const cameraList = document.getElementById("cameraList");
  document.getElementById("addCameraBtn").onclick = () => {
    cameraList.insertAdjacentHTML("beforeend", cameraEntryHtml());
  };
  cameraList.addEventListener("click", (e) => {
    const btn = e.target.closest(".remove-camera");
    if (btn) btn.closest(".camera-entry").remove();
  });
  cameraList.addEventListener("input", (e) => {
    if (e.target.dataset.field === "name") {
      const entry = e.target.closest(".camera-entry");
      entry.querySelector(".camera-entry-header span").textContent = e.target.value || "New camera";
    }
  });

  const form = document.getElementById("profileForm");
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(form);
    const payload = { manipulator: {}, end_effector: {}, low_level: {}, board_fabrication: {} };
    for (const [key, value] of fd.entries()) {
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

profileBtn.addEventListener("click", openProfileOverlay);
profileCloseBtn.addEventListener("click", () => profileOverlay.classList.add("hidden"));
profileOverlay.addEventListener("click", (e) => {
  if (e.target === profileOverlay) profileOverlay.classList.add("hidden");
});

taskCloseBtn.addEventListener("click", () => taskOverlay.classList.add("hidden"));
taskOverlay.addEventListener("click", (e) => {
  if (e.target === taskOverlay) taskOverlay.classList.add("hidden");
});

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
  }
});

refreshList();
