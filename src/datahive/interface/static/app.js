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
const toastContainer = document.getElementById("toastContainer");
const bulkBar = document.getElementById("bulkBar");
const bulkCountEl = document.getElementById("bulkCount");
const bulkValidateBtn = document.getElementById("bulkValidateBtn");
const bulkUploadBtn = document.getElementById("bulkUploadBtn");
const bulkDeleteBtn = document.getElementById("bulkDeleteBtn");
const bulkClearBtn = document.getElementById("bulkClearBtn");

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
        <span class="badge ${badgeClass}">${ep.status}</span>
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
      <span class="badge badge-lg ${statusClass}">${data.index ? data.index.status : ""}</span>
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

    <div class="card">
      <h3>Cameras</h3>
      <div class="camera-grid">
        ${data.cameras.map((c) => `
          <div class="camera-card">
            <div class="cam-name">${c}</div>
            <video controls src="/api/episodes/${episodeId}/video/${c}"></video>
          </div>`).join("") || "<p class=\"empty-hint\">No videos recorded.</p>"}
      </div>
    </div>

    <div class="card">
      <h3>Annotate</h3>
      <form class="validate-form" id="annForm">
        <fieldset>
          <legend>Who</legend>
          <div class="field-grid">
            <label>Operator name <input name="operator_name" value="${ann.operator_name || ""}" placeholder="Who ran this trial"></label>
            <label>Annotator name <input name="annotator_name" value="${ann.annotator_name || ""}" placeholder="Who is annotating"></label>
          </div>
        </fieldset>
        <fieldset>
          <legend>Trial outcome</legend>
          <div class="field-grid">
            <label>Attachment
              <input name="attachment_id" value="${attachmentId}" list="attachmentList">
            </label>
            <datalist id="attachmentList">${Object.keys(attachments).map((a) => `<option value="${a}">`).join("")}</datalist>
            <label>Outcome
              <select name="outcome">
                ${OUTCOMES.map((o) => `<option value="${o}" ${o === outcome ? "selected" : ""}>${humanize(o)}</option>`).join("")}
              </select>
            </label>
            <label id="failureCauseField" style="${outcome === "success" ? "display:none" : ""}">Failure cause
              <select name="failure_cause">
                ${FAILURE_CAUSES.map((c) => `<option value="${c}" ${c === ann.failure_cause ? "selected" : ""}>${humanize(c)}</option>`).join("")}
              </select>
            </label>
            <label id="severityField" style="${outcome === "success" ? "display:none" : ""}">Severity
              <select name="severity">
                <option value="">–</option>
                ${SEVERITIES.map((s) => `<option value="${s}" ${s === ann.severity ? "selected" : ""}>${humanize(s)}</option>`).join("")}
              </select>
            </label>
            <label id="completionTimeField" style="${outcome !== "success" ? "display:none" : ""}">Completion time (s)
              <input name="completion_time_s" type="number" step="0.01" value="${ann.completion_time_s || ""}">
            </label>
            <label>Attempts <input name="n_attempts" type="number" value="${ann.n_attempts || 1}"></label>
            <label>Regrasps <input name="n_regrasps" type="number" value="${ann.n_regrasps || 0}"></label>
            <label id="stageField" style="${composed ? "" : "display:none"}">Stage reached
              <input name="stage_reached" type="number" value="${ann.stage_reached || ""}">
            </label>
            <label>Strategy
              <select name="strategy">
                ${STRATEGIES.map((s) => `<option value="${s}" ${s === ann.strategy ? "selected" : ""}>${humanize(s)}</option>`).join("")}
              </select>
            </label>
            <label class="full">Notes <textarea name="notes">${ann.notes || ""}</textarea></label>
          </div>
        </fieldset>
        <div class="actions">
          <button type="submit" class="primary btn-lg">Save</button>
          <button type="button" id="validateBtn" class="btn-lg">Validate</button>
        </div>
      </form>
    </div>

    <div class="actions">
      <button id="uploadBtn" class="primary btn-lg">Upload</button>
      <button id="deleteBtn" class="danger btn-lg">Delete</button>
    </div>
    <div class="status-msg" id="statusMsg"></div>
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
  form.outcome.addEventListener("change", () => {
    const isSuccess = form.outcome.value === "success";
    document.getElementById("failureCauseField").style.display = isSuccess ? "none" : "";
    document.getElementById("severityField").style.display = isSuccess ? "none" : "";
    document.getElementById("completionTimeField").style.display = isSuccess ? "" : "none";
  });
  form.attachment_id.addEventListener("change", () => {
    const a = attachments[form.attachment_id.value];
    document.getElementById("stageField").style.display = a && a.composed_assembly ? "" : "none";
  });

  function collectAnnotationPayload() {
    const fd = new FormData(form);
    const payload = Object.fromEntries(fd.entries());
    for (const k of ["completion_time_s", "n_attempts", "n_regrasps", "stage_reached"]) {
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

function cameraRowHtml(cam = {}) {
  return `
    <div class="camera-row">
      <input placeholder="name" data-field="name" value="${cam.name || ""}">
      <input placeholder="resolution" data-field="resolution" value="${cam.resolution || ""}">
      <input placeholder="encoding" data-field="encoding" value="${cam.encoding || ""}">
      <input placeholder="fps" type="number" data-field="fps" value="${cam.fps ?? ""}">
      <input placeholder="position" data-field="position" value="${cam.position || ""}">
      <input placeholder="orientation" data-field="orientation" value="${cam.orientation || ""}">
      <button type="button" class="remove-camera" title="Remove">✕</button>
    </div>`;
}

function renderProfileForm(profile, problems, exists) {
  const m = profile.manipulator || {};
  const ee = profile.end_effector || {};
  const ll = profile.low_level || {};
  const cams = profile.cameras || [];

  const problemsHtml = problems.length
    ? `<ul class="problem-list">${problems.map((p) => `<li>${p}</li>`).join("")}</ul>`
    : `<p class="status-msg">Profile is complete.</p>`;

  profileBody.innerHTML = `
    ${!exists ? `<p>No robot profile yet for this samples/ directory.</p>
      <button id="createProfileBtn" class="primary">Create profile</button>` : `
    <div>${problemsHtml}</div>
    <form id="profileForm">
      <fieldset>
        <legend>Manipulator</legend>
        <div class="field-grid">
          <label>Model <input name="manipulator.model" value="${m.model || ""}"></label>
          <label>DoF <input name="manipulator.dof" type="number" value="${m.dof ?? ""}"></label>
          <label class="full">Joint names (comma-separated)
            <input name="manipulator.joint_names" value="${(m.joint_names || []).join(", ")}">
          </label>
        </div>
      </fieldset>

      <fieldset>
        <legend>End effector</legend>
        <div class="field-grid">
          <label>Type
            <select name="end_effector.type">
              <option value="">--</option>
              ${["gripper", "dexterous_hand", "prosthetic_hand"].map((v) => `<option ${ee.type === v ? "selected" : ""}>${v}</option>`).join("")}
            </select>
          </label>
          <label>Actuated DoF <input name="end_effector.actuated_dof" type="number" value="${ee.actuated_dof ?? ""}"></label>
          <label>Command modality
            <select name="end_effector.command_modality">
              <option value="">--</option>
              ${["binary", "position", "velocity"].map((v) => `<option ${ee.command_modality === v ? "selected" : ""}>${v}</option>`).join("")}
            </select>
          </label>
        </div>
      </fieldset>

      <fieldset>
        <legend>Low-level control (mandatory)</legend>
        <div class="field-grid">
          <label>Mode
            <select name="low_level.mode" required>
              <option value="">--</option>
              ${["stock", "custom"].map((v) => `<option ${ll.mode === v ? "selected" : ""}>${v}</option>`).join("")}
            </select>
          </label>
          <label>Controller type <input name="low_level.controller_type" value="${ll.controller_type || ""}"></label>
          <label>Rate (Hz) <input name="low_level.rate_hz" type="number" value="${ll.rate_hz ?? ""}"></label>
          <label>Gains (free text) <input name="low_level.gains" value="${ll.gains ?? ""}"></label>
        </div>
      </fieldset>

      <fieldset>
        <legend>Cameras (at least one required)</legend>
        <div id="cameraList">${cams.map(cameraRowHtml).join("")}</div>
        <button type="button" id="addCameraBtn">+ Add camera</button>
      </fieldset>

      <fieldset>
        <legend>General</legend>
        <div class="field-grid">
          <label>Control mode <input name="control_mode" value="${profile.control_mode || ""}"></label>
          <label>Policy <input name="policy" value="${profile.policy || ""}"></label>
          <label>Board mounting
            <select name="board_mounting">
              <option value="">--</option>
              ${["horizontal", "vertical"].map((v) => `<option ${profile.board_mounting === v ? "selected" : ""}>${v}</option>`).join("")}
            </select>
          </label>
          <label>HiveBoard version <input name="hiveboard_version" value="${profile.hiveboard_version || ""}"></label>
          <label>Platform ID <input name="platform_id" value="${profile.platform_id || ""}"></label>
        </div>
      </fieldset>

      <div class="actions">
        <button type="submit" class="primary">Save profile</button>
      </div>
      <div class="status-msg" id="profileMsg"></div>
    </form>
    `}
  `;

  if (!exists) {
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

  document.getElementById("addCameraBtn").onclick = () => {
    document.getElementById("cameraList").insertAdjacentHTML("beforeend", cameraRowHtml());
  };
  document.getElementById("cameraList").addEventListener("click", (e) => {
    if (e.target.classList.contains("remove-camera")) {
      e.target.closest(".camera-row").remove();
    }
  });

  const form = document.getElementById("profileForm");
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(form);
    const payload = { manipulator: {}, end_effector: {}, low_level: {} };
    for (const [key, value] of fd.entries()) {
      if (key.includes(".")) {
        const [group, field] = key.split(".");
        payload[group][field] = value === "" ? null : (field === "dof" || field === "actuated_dof" || field === "rate_hz" ? Number(value) : (field === "joint_names" ? value.split(",").map((s) => s.trim()).filter(Boolean) : value));
      } else {
        payload[key] = value === "" ? null : value;
      }
    }
    payload.cameras = Array.from(document.querySelectorAll("#cameraList .camera-row")).map((row) => {
      const cam = {};
      row.querySelectorAll("input").forEach((inp) => {
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
