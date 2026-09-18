// Plain JS, no build step, no framework. Talks to the same JSON API that
// backs `datahive list`/`validate`/`upload`/`delete`/`sync`.

const listEl = document.getElementById("list");
const detailEl = document.getElementById("detail");
const searchEl = document.getElementById("search");
const statusEl = document.getElementById("statusFilter");
const syncBtn = document.getElementById("syncBtn");

let selectedId = null;
let attachmentsCache = null;

async function api(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status}: ${text}`);
  }
  return res.json();
}

async function loadAttachments() {
  if (!attachmentsCache) {
    attachmentsCache = await api("/api/attachments");
  }
  return attachmentsCache;
}

async function refreshList() {
  const q = encodeURIComponent(searchEl.value || "");
  const status = encodeURIComponent(statusEl.value || "");
  const episodes = await api(`/api/episodes?q=${q}&status=${status}`);
  listEl.innerHTML = "";
  for (const ep of episodes) {
    const row = document.createElement("div");
    row.className = "episode-row" + (ep.episode_id === selectedId ? " selected" : "");
    const badgeClass = (ep.status || "").split(" ")[0];
    row.innerHTML = `
      <div class="eid">${ep.episode_id}</div>
      <div class="meta">${ep.session_id} · trial ${ep.trial_id || "?"}
        <span class="badge ${badgeClass}">${ep.status}</span>
      </div>`;
    row.onclick = () => selectEpisode(ep.episode_id);
    listEl.appendChild(row);
  }
}

function drawTrajectory(canvas, traj) {
  const ctx = canvas.getContext("2d");
  const w = (canvas.width = canvas.clientWidth * 2);
  const h = (canvas.height = 260 * 2);
  ctx.clearRect(0, 0, w, h);
  const ts = traj.timestamp;
  const series = traj.joint_position;
  if (!ts || !series || !ts.length) {
    ctx.fillText("No proprioception data", 20, 40);
    return;
  }
  const nJoints = Array.isArray(series[0]) ? series[0].length : 1;
  const colors = ["#2563eb", "#16a34a", "#d97706", "#dc2626", "#7c3aed", "#0891b2", "#db2777"];
  const tmin = ts[0], tmax = ts[ts.length - 1];
  const allVals = [];
  for (const row of series) {
    if (Array.isArray(row)) allVals.push(...row); else allVals.push(row);
  }
  const vmin = Math.min(...allVals), vmax = Math.max(...allVals);
  const pad = 30;
  const x = (t) => pad + ((t - tmin) / (tmax - tmin || 1)) * (w - 2 * pad);
  const y = (v) => h - pad - ((v - vmin) / (vmax - vmin || 1)) * (h - 2 * pad);

  ctx.strokeStyle = "#9ca3af";
  ctx.beginPath();
  ctx.moveTo(pad, pad); ctx.lineTo(pad, h - pad); ctx.lineTo(w - pad, h - pad);
  ctx.stroke();

  for (let j = 0; j < nJoints; j++) {
    ctx.strokeStyle = colors[j % colors.length];
    ctx.lineWidth = 2;
    ctx.beginPath();
    ts.forEach((t, i) => {
      const v = nJoints > 1 ? series[i][j] : series[i];
      const px = x(t), py = y(v);
      if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
    });
    ctx.stroke();
  }
}

function renderHeader(header) {
  const rows = [
    ["lab_id", header.lab_id], ["platform_id", header.platform_id],
    ["session_id", header.session_id], ["episode_id", header.episode_id],
    ["trial_id", header.trial_id], ["control_mode", header.control_mode],
    ["policy", header.policy], ["board_mounting", header.board_mounting],
    ["hiveboard_version", header.hiveboard_version],
    ["low_level.mode", header.low_level && header.low_level.mode],
    ["manipulator.model", header.manipulator && header.manipulator.model],
    ["cameras", (header.cameras || []).map(c => c.name).join(", ")],
  ];
  return `<dl class="header-fields">${rows.map(([k, v]) => `<dt>${k}</dt><dd>${v ?? ""}</dd>`).join("")}</dl>`;
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

  detailEl.innerHTML = `
    <h2>${episodeId} <span class="badge ${(data.index && data.index.status || "").split(" ")[0]}">${data.index ? data.index.status : ""}</span></h2>
    ${renderHeader(data.header)}
    <h3>Trajectory</h3>
    <canvas id="traj" style="width:100%;height:260px"></canvas>
    <h3>Cameras</h3>
    ${data.cameras.map(c => `<div>${c}<br><video controls src="/api/episodes/${episodeId}/video/${c}"></video></div>`).join("") || "<p>No videos.</p>"}
    <h3>Validate / Annotate</h3>
    <form class="validate-form" id="annForm">
      <label>attachment_id <input name="attachment_id" value="${attachmentId}" list="attachmentList"></label>
      <datalist id="attachmentList">${Object.keys(attachments).map(a => `<option value="${a}">`).join("")}</datalist>
      <label>outcome
        <select name="outcome">
          ${["success", "fail", "timeout", "safety_stop"].map(o => `<option ${o === outcome ? "selected" : ""}>${o}</option>`).join("")}
        </select>
      </label>
      <label id="failureCauseField" style="${outcome === "success" ? "display:none" : ""}">failure_cause
        <select name="failure_cause">
          ${["grasp_geometry", "kinematic_limit", "perception", "slip", "force_limit", "control_precision", "other"].map(c => `<option ${c === ann.failure_cause ? "selected" : ""}>${c}</option>`).join("")}
        </select>
      </label>
      <label id="completionTimeField" style="${outcome !== "success" ? "display:none" : ""}">completion_time_s
        <input name="completion_time_s" type="number" step="0.01" value="${ann.completion_time_s || ""}">
      </label>
      <label>n_attempts <input name="n_attempts" type="number" value="${ann.n_attempts || 1}"></label>
      <label>n_regrasps <input name="n_regrasps" type="number" value="${ann.n_regrasps || 0}"></label>
      <label id="stageField" style="${composed ? "" : "display:none"}">stage_reached
        <input name="stage_reached" type="number" value="${ann.stage_reached || ""}">
      </label>
      <label>strategy
        <select name="strategy">
          ${["prehensile", "non_prehensile"].map(s => `<option ${s === ann.strategy ? "selected" : ""}>${s}</option>`).join("")}
        </select>
      </label>
      <label>notes <textarea name="notes">${ann.notes || ""}</textarea></label>
      <button type="submit" class="primary">Save & validate</button>
    </form>
    <div class="actions">
      <button id="uploadBtn" class="primary">Upload</button>
      <button id="deleteBtn" class="danger">Delete</button>
    </div>
    <div class="status-msg" id="statusMsg"></div>
  `;

  const traj = await api(`/api/episodes/${episodeId}/trajectory?max_points=2000`);
  drawTrajectory(document.getElementById("traj"), traj);

  const form = document.getElementById("annForm");
  form.outcome.addEventListener("change", () => {
    const isSuccess = form.outcome.value === "success";
    document.getElementById("failureCauseField").style.display = isSuccess ? "none" : "";
    document.getElementById("completionTimeField").style.display = isSuccess ? "" : "none";
  });
  form.attachment_id.addEventListener("change", () => {
    const a = attachments[form.attachment_id.value];
    document.getElementById("stageField").style.display = a && a.composed_assembly ? "" : "none";
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(form);
    const payload = Object.fromEntries(fd.entries());
    for (const k of ["completion_time_s", "n_attempts", "n_regrasps", "stage_reached"]) {
      payload[k] = payload[k] === "" ? null : Number(payload[k]);
    }
    const msg = document.getElementById("statusMsg");
    try {
      const result = await api(`/api/episodes/${episodeId}/validate`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
      });
      msg.textContent = result.ok ? "Saved and validated." : `Saved, but validation failed: ${result.error}`;
      await selectEpisode(episodeId);
    } catch (err) {
      msg.textContent = `Error: ${err.message}`;
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
    }
  };
}

searchEl.addEventListener("input", refreshList);
statusEl.addEventListener("change", refreshList);
syncBtn.addEventListener("click", async () => {
  syncBtn.disabled = true;
  syncBtn.textContent = "Syncing…";
  try {
    await api("/api/sync", { method: "POST" });
  } finally {
    syncBtn.disabled = false;
    syncBtn.textContent = "Sync";
    await refreshList();
  }
});

refreshList();
