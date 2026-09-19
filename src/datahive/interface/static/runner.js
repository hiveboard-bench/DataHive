// Trial/evaluation runner, modelled on HiveBoard's Evaluation Runner: set up a
// session, generate the trial plan, run each trial with a 5 s countdown and a
// stopwatch (with sounds and automatic timeout), record the outcome, review.
// Trials are saved by the server into samples/<session>/trials.csv.

const RN_SESSION_KEY = "datahive-runner-session";
const RN_SOUND_KEY = "datahive-runner-sound";
const RN_OPERATOR_KEY = "datahive-operator";
const RN_OUTCOMES = ["success", "fail", "timeout", "safety_stop"];
const RN_CAUSES = ["grasp_geometry", "kinematic_limit", "perception", "slip", "force_limit", "control_precision", "other"];
const RN_SEVERITIES = ["minor", "moderate", "critical"];
const RN_STRATEGIES = ["prehensile", "non_prehensile"];
const RN_MODE_TITLES = {
  manual: "You time each trial here, then upload the HDF5 and one video per camera",
  automatic: "Your robot script records each episode through the collection pipeline",
};

const rn = {
  root: document.getElementById("view-runner"),
  loaded: false,
  defaults: null,
  tasks: {},
  sessions: [],
  setupTasks: null,
  cameras: [],
  editTasks: [],
  upload: { files: {}, busy: false, result: null },
  session: null,
  step: "setup",
  entry: null,
  error: "",
  sound: (() => { try { return localStorage.getItem(RN_SOUND_KEY) !== "off"; } catch (e) { return true; } })(),
  timer: { state: "idle", countdown: 5, elapsedMs: 0, t0: 0, endAt: 0, ticker: null },
};

function beep(frequency = 660, duration = 0.08) {
  if (!rn.sound) return;
  try {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    const ctx = new Ctx();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.frequency.value = frequency;
    gain.gain.setValueAtTime(0.08, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + duration);
    osc.connect(gain).connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + duration);
    osc.addEventListener("ended", () => ctx.close());
  } catch (e) { /* audio unavailable */ }
}

function formatStopwatch(ms) {
  const total = Math.max(0, ms) / 1000;
  const m = Math.floor(total / 60);
  const s = (total % 60).toFixed(2).padStart(5, "0");
  return `${String(m).padStart(2, "0")}:${s}`;
}

const RN_ICON_EDIT = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 20h9"></path><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"></path></svg>`;
const RN_ICON_TRASH = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"></path><path d="M10 11v6"></path><path d="M14 11v6"></path><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"></path></svg>`;
const rnIconBtn = (attrs, icon, label, cls = "") => `<button type="button" class="rn-icon-btn ${cls}" ${attrs} title="${label}" aria-label="${label}">${icon}</button>`;

// ---- Episode upload pieces shared by the Runner (manual mode) and Annotate ----
function epUploadItems(cameras) {
  return [{ key: "h5", label: "HDF5 file", hint: ".h5 / .hdf5", accept: ".h5,.hdf5" },
    ...cameras.map((c) => ({ key: `video:${c}`, label: `Video: ${c}`, hint: "camera from the robot profile", accept: "video/*,.mp4" }))];
}

function epRowsHtml(items, files, busy, existing = {}) {
  return items.map((i) => {
    const f = files[i.key], had = !!existing[i.key];
    let text, action, cls, icon;
    if (f) {
      text = `${had ? "Replaces the current one: " : ""}${escapeHtml(f.name)} &middot; ${(f.size / (1024 * 1024)).toFixed(1)} MB`;
      action = "Change"; cls = "has"; icon = "&#10003;";
    } else if (had) {
      text = "Added: already uploaded"; action = "Replace"; cls = "existing"; icon = "&#10003;";
    } else {
      text = escapeHtml(i.hint); action = "Choose"; cls = ""; icon = "&#8593;";
    }
    return `<label class="rn-file ${cls}"><input type="file" data-key="${escapeHtml(i.key)}" accept="${i.accept}" ${busy ? "disabled" : ""}>
      <span class="rn-file-icon">${icon}</span>
      <span class="rn-file-text"><b>${escapeHtml(i.label)}</b><small>${text}</small></span>
      <span class="rn-file-action">${action}</span></label>`;
  }).join("");
}

function epWireRows(root, files, onChange) {
  root.querySelectorAll('input[type="file"]').forEach((inp) => inp.addEventListener("change", () => {
    if (inp.files[0]) { files[inp.dataset.key] = inp.files[0]; onChange(); }
  }));
}

function epQuery(partial, empty, timerS) {
  const q = [];
  if (partial) q.push("partial=true");
  if (empty) q.push("empty=true");
  if (timerS != null) q.push(`timer_s=${timerS}`);
  return q.length ? `?${q.join("&")}` : "";
}

async function epSend(sessionId, trialId, files, partial, empty = false, timerS = null) {
  const fd = new FormData();
  Object.entries(files).forEach(([key, file]) => fd.append(key, file));
  const res = await fetch(`/api/runner/sessions/${encodeURIComponent(sessionId)}/trials/${encodeURIComponent(trialId)}/episode${epQuery(partial, empty, timerS)}`, { method: "POST", body: fd });
  const body = await res.json();
  if (!res.ok) throw new Error(body.detail || "Upload failed");
  return body;
}

async function epOpenInAnnotate(episodeId) {
  showView("annotate");
  await selectEpisode(episodeId);
}

const RN_RING_C = 2 * Math.PI * 54;

function rnRingHtml(progress, colorClass, inner) {
  return `<div class="rn-ring ${colorClass}" id="rnRing">
    <svg viewBox="0 0 120 120" aria-hidden="true"><circle class="bg" cx="60" cy="60" r="54"></circle>
      <circle class="fg" id="rnRingFg" cx="60" cy="60" r="54" stroke-dasharray="${RN_RING_C.toFixed(1)}" stroke-dashoffset="${(RN_RING_C * (1 - progress)).toFixed(1)}"></circle></svg>
    <div class="rn-ring-inner">${inner}</div></div>`;
}

function rnSetRing(progress, colorClass) {
  const fg = document.getElementById("rnRingFg");
  if (fg) fg.setAttribute("stroke-dashoffset", (RN_RING_C * (1 - Math.max(0, Math.min(1, progress)))).toFixed(1));
  const ring = document.getElementById("rnRing");
  if (ring && colorClass) ring.className = `rn-ring ${colorClass}`;
}

function rnRunningColor(elapsedMs, timeout) {
  const left = timeout - elapsedMs / 1000;
  return left <= 5 ? "danger" : left <= 10 ? "warn" : "running";
}

function rnStepsHtml(actions = "") {
  const idle = rn.timer.state === "idle" && auto.state.status === "idle";
  if (rn.session) {
    return `<div class="rn-backbar"><button type="button" class="rn-back" data-back ${idle ? "" : "disabled"}>&larr; Sessions</button><span class="rn-backbar-actions">${actions}</span></div>`;
  }
  const order = ["setup", "review"];
  const labels = { setup: "Setup", review: "Review" };
  const current = order.indexOf(rn.step);
  return `<ol class="rn-steps">${order.map((s, i) =>
    `<li class="${i === current ? "active" : ""}"><button type="button" data-step="${s}" ${idle ? "" : "disabled"}>${labels[s]}</button></li>`).join("")}</ol>`;
}

function rnEstimateMinutes(ids, perTask) {
  const secs = ids.reduce((sum, id) => sum + (rnTask(id).timeout || 0), 0) * perTask;
  return Math.round(secs / 60);
}

function rnTask(id) { return rn.tasks[id] || { name: id, timeout: 0, family: "" }; }
function rnImg(id) { const t = rnTask(id); return t.image ? `<img src="/tasks/${t.image}" alt="">` : ""; }

function rnClearTicker() {
  if (rn.timer.ticker) window.clearInterval(rn.timer.ticker);
  rn.timer.ticker = null;
}

function rnResetTimer() {
  rnClearTicker();
  Object.assign(rn.timer, { state: "idle", countdown: 5, elapsedMs: 0, t0: 0 });
  rn.error = "";
}

async function rnLoad() {
  const [defaults, tasks, sessions] = await Promise.all([
    api("/api/runner/defaults"), api("/api/attachments"), api("/api/runner/sessions"),
  ]);
  rn.defaults = defaults; rn.tasks = tasks; rn.sessions = sessions;
  await rnEnsureDefaultSession();
  await rnLoadCameras();
  rn.step = "setup";
  rnRestoreState();
  rn.loaded = true;
}

// Opening the Runner always leaves a plan for today ready: 5 trials of every task.
async function rnEnsureDefaultSession() {
  const d = rn.defaults;
  if (!d || !d.lab_id || !d.platform_id || rn.sessions.some((s) => s.date === d.date)) return;
  let operator = "";
  try { operator = localStorage.getItem(RN_OPERATOR_KEY) || ""; } catch (e) { /* ignore */ }
  try {
    const session = await api("/api/runner/sessions", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ operator_name: operator || "Unassigned", date: d.date, attachment_ids: Object.keys(rn.tasks),
        per_task: d.required_trials, randomize: true, mode: "manual" }),
    });
    rn.sessions = [session, ...rn.sessions];
    showToast(`A default plan for ${d.date} was created: ${session.n_planned} trials. Use the pencil to change it${operator ? "" : " and set the operator name"}.`,
      { type: "success", title: "Plan ready", timeout: 6000 });
  } catch (e) { /* the sessions list still works without a default plan */ }
}

// A page refresh keeps you inside the open session (plan or review). A trial in progress cannot
// survive a refresh, so it comes back on the plan. Switching tabs still starts on the sessions list.
const RN_STATE_KEY = "datahive-runner-state";

function rnSaveState() {
  try {
    if (rn.session) localStorage.setItem(RN_STATE_KEY, JSON.stringify({ session_id: rn.session.session_id, step: rn.step === "review" ? "review" : "task" }));
    else localStorage.removeItem(RN_STATE_KEY);
  } catch (e) { /* storage unavailable */ }
}

function rnRestoreState() {
  try {
    const saved = JSON.parse(localStorage.getItem(RN_STATE_KEY) || "null");
    const session = saved && rn.sessions.find((x) => x.session_id === saved.session_id);
    if (session) { rn.session = session; rn.step = saved.step === "review" ? "review" : "task"; }
  } catch (e) { /* ignore a broken value */ }
}

async function rnLoadCameras() {
  try {
    const p = await api("/api/profile");
    rn.cameras = ((p.profile && p.profile.cameras) || []).map((c) => c.name).filter(Boolean);
  } catch (e) { rn.cameras = []; }
}

function rnUseSession(session, step) {
  rn.session = session;
  rn.step = step;
  rnResetTimer();
  try { localStorage.setItem(RN_SESSION_KEY, session.session_id); } catch (e) { /* ignore */ }
  rnRender();
}

// ---------------------------------------------------------------- setup
function rnSessionRowHtml(s) {
  const pct = s.n_planned ? Math.round((100 * s.n_recorded) / s.n_planned) : 0;
  return `<div class="rn-session">
    <div class="rn-session-main">
      <div class="rn-session-title"><code>${escapeHtml(s.session_id)}</code><span class="chip-static">${escapeHtml(humanize(s.mode || "manual"))}</span></div>
      <div class="rn-muted">${escapeHtml(s.operator_name)} &middot; ${s.date}</div>
      <div class="progress-bar"><div style="width:${pct}%"></div></div>
    </div>
    <div class="rn-session-count"><b>${s.n_recorded}</b>/${s.n_planned}<span>trials</span></div>
    <div class="rn-session-actions">
      <button type="button" class="primary" data-resume="${escapeHtml(s.session_id)}">${s.complete ? "Open" : "Resume"}</button>
      ${rnIconBtn(`data-edit="${escapeHtml(s.session_id)}"`, RN_ICON_EDIT, "Edit plan")}
      ${rnIconBtn(`data-delete="${escapeHtml(s.session_id)}"`, RN_ICON_TRASH, "Delete session", "danger")}
    </div></div>`;
}

function rnSetupHtml() {
  const list = rn.sessions.length
    ? `<div class="rn-sessions">${rn.sessions.map(rnSessionRowHtml).join("")}</div>`
    : `<div class="rn-empty"><b>No sessions yet</b><span>Create a session to plan and run your trials.</span></div>`;
  return `
  <div class="view-inner rn-wide">
    <div class="rn-head"><p class="eyebrow">Runner</p><h2>Sessions</h2>
      <p class="rn-muted">A session is one evaluation run on your platform. Resume one, or create a new session to generate its trial plan.</p></div>
    ${rnStepsHtml()}
    <div class="rn-toolbar"><span class="rn-muted">${rn.sessions.length} session${rn.sessions.length === 1 ? "" : "s"}</span><span class="rn-spacer"></span>
      <button type="button" class="rn-cta" id="rnNewBtn">+ Create a new session</button></div>
    ${list}
  </div>`;
}

function rnNewSessionModalHtml() {
  const d = rn.defaults;
  let operator = "";
  try { operator = localStorage.getItem(RN_OPERATOR_KEY) || ""; } catch (e) { /* ignore */ }
  const missing = [];
  if (!d.lab_id) missing.push("the laboratory ID (run <code>datahive init</code>)");
  if (!d.platform_id) missing.push("the robot name and gripper name (set them in <b>Robot Profile</b>)");
  const problems = (missing.length ? `<div class="rn-alert">Missing ${missing.join(" and ")}.</div>` : "")
    + (d.profile_problems.length
      ? `<div class="rn-alert">The robot profile is incomplete (${d.profile_problems.length} issue${d.profile_problems.length > 1 ? "s" : ""}). Open <b>Robot Profile</b> to finish it before recording episodes.</div>` : "");
  return `
  <div class="rn-modal" id="rnModal">
    <div class="rn-modal-panel" role="dialog" aria-label="Create a new session">
      <div class="rn-modal-head"><h2>Create a new session</h2><button type="button" id="rnModalClose" aria-label="Close">&#10005;</button></div>
      <form id="rnSetupForm" class="rn-setup">
        <div class="rn-setup-main">
          ${problems}
          <section class="rn-card">
            <h3>Session</h3>
            <div class="rn-muted rn-identity">Laboratory <b>${escapeHtml(d.lab_id || "not set")}</b> &middot; Platform <b>${escapeHtml(d.platform_id || "not set")}</b> <span class="rn-muted">(from robot and gripper names)</span></div>
            <div class="field-grid">
              <label>${fieldLabel("Operator name")}<input name="operator_name" value="${escapeHtml(operator)}" placeholder="Who runs the trials"></label>
              <label>${fieldLabel("Evaluation date (UTC)")}<input name="date" type="date" value="${d.date}"></label>
            </div>
          </section>
          <section class="rn-card">
            <h3>Trial plan</h3>
            <div class="field-grid">
              <label>Trials per task<input name="per_task" type="number" min="1" max="50" value="${d.required_trials}"></label>
              <label>Trial order
                ${segmentedControlHtml("order", ["shuffled", "ordered"], "shuffled", { shuffled: "Interleave the tasks in a random order", ordered: "Run all trials of one task, then the next" }, false)}
              </label>
              <label class="full">Recording mode
                ${segmentedControlHtml("mode", ["manual", "automatic"], "manual", RN_MODE_TITLES, false)}
                <span class="rn-muted" style="font-weight:400">You can switch mode at any time from the trial screen.</span>
              </label>
              <div class="full">
                <div class="rn-label">${fieldLabel("Tasks")}</div>
                <button type="button" class="task-chip" id="rnPickTasks">${rnTasksChipHtml()}</button>
              </div>
            </div>
          </section>
        </div>
        <aside class="rn-summary">
          <div class="rn-card rn-summary-card">
            <h3>Plan summary</h3>
            <div class="rn-big" id="rnSumTrials">0</div><div class="rn-muted">trials to run</div>
            <dl class="rn-facts"><div><dt>Tasks</dt><dd id="rnSumTasks">0</dd></div>
              <div><dt>Longest possible</dt><dd id="rnSumTime">0 min</dd></div></dl>
            <p class="rn-muted" id="rnSumNote"></p>
            <p class="view-error" id="rnFormError"></p>
            <button type="submit" class="rn-cta" ${d.lab_id && d.platform_id ? "" : "disabled"}>Generate trial plan</button>
          </div>
        </aside>
      </form>
    </div>
  </div>`;
}

function rnSelectedTasks() {
  if (!rn.setupTasks) rn.setupTasks = new Set(Object.keys(rn.tasks));
  return Object.keys(rn.tasks).filter((id) => rn.setupTasks.has(id));
}

// Same chip as the annotation task selector; several tasks show a summary.
function rnTasksChipHtml(ids = rnSelectedTasks()) {
  if (ids.length === 0) return taskChipHtml(null, null);
  if (ids.length === 1) return taskChipHtml(rn.tasks[ids[0]], ids[0]);
  const thumbs = ids.map((id) => (rn.tasks[id].image ? `<img src="/tasks/${rn.tasks[id].image}" alt="" title="${escapeHtml(rn.tasks[id].name)}">` : "")).join("");
  return `
    <span class="task-chip-body"><strong>${ids.length} tasks selected</strong><span class="rn-thumbs">${thumbs}</span></span>
    <svg class="task-chip-edit" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path></svg>`;
}

function rnCloseModal() {
  const m = document.getElementById("rnModal");
  if (m) m.remove();
  document.removeEventListener("keydown", rnModalKey);
}

function rnModalKey(e) {
  if (e.key === "Escape" && taskOverlay.classList.contains("hidden")) rnCloseModal();
}

function rnOpenNewSession() {
  rnCloseModal();
  rn.setupTasks = null;
  rn.root.insertAdjacentHTML("beforeend", rnNewSessionModalHtml());
  document.addEventListener("keydown", rnModalKey);
  const modal = document.getElementById("rnModal");
  document.getElementById("rnModalClose").onclick = rnCloseModal;
  modal.addEventListener("mousedown", (e) => { if (e.target === modal) rnCloseModal(); });
  const form = document.getElementById("rnSetupForm");
  wireSegmentedControls(form);
  const size = () => {
    const ids = rnSelectedTasks();
    const per = Number(form.elements["per_task"].value) || 0;
    document.getElementById("rnSumTrials").textContent = ids.length * per;
    document.getElementById("rnSumTasks").textContent = ids.length;
    document.getElementById("rnSumTime").textContent = `${rnEstimateMinutes(ids, per)} min`;
    document.getElementById("rnSumNote").textContent = per === rn.defaults.required_trials
      ? "" : `HiveBoard submissions need ${rn.defaults.required_trials} trials per task.`;
  };
  form.addEventListener("input", size); form.addEventListener("change", size); size();
  document.getElementById("rnPickTasks").onclick = () => openTaskPickerMulti(rn.tasks, rnSelectedTasks(), (ids) => {
    rn.setupTasks = new Set(ids);
    document.getElementById("rnPickTasks").innerHTML = rnTasksChipHtml();
    size();
  });
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(form);
    const ids = rnSelectedTasks();
    const err = document.getElementById("rnFormError");
    if (!ids.length) { err.textContent = "Select at least one task."; return; }
    try { localStorage.setItem(RN_OPERATOR_KEY, String(fd.get("operator_name") || "").trim()); } catch (x) { /* ignore */ }
    try {
      const session = await api("/api/runner/sessions", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          operator_name: fd.get("operator_name"), date: fd.get("date"), attachment_ids: ids,
          per_task: Number(fd.get("per_task")), randomize: fd.get("order") !== "ordered", mode: fd.get("mode") || "manual",
        }),
      });
      rnCloseModal();
      rn.sessions = await api("/api/runner/sessions");
      rnUseSession(session, "task");
    } catch (x) { err.textContent = x.message; }
  });
}

function rnEditPlanModalHtml(s) {
  const opts = { shuffled: "Interleave the remaining tasks in a random order", ordered: "Run all remaining trials of one task, then the next" };
  return `
  <div class="rn-modal" id="rnModal">
    <div class="rn-modal-panel" role="dialog" aria-label="Edit plan">
      <div class="rn-modal-head"><h2>Edit plan</h2><button type="button" id="rnModalClose" aria-label="Close">&#10005;</button></div>
      <form id="rnEditForm" class="rn-setup">
        <div class="rn-setup-main">
          <section class="rn-card">
            <h3>Session</h3>
            <div class="rn-muted rn-identity">${escapeHtml(s.session_id)}</div>
            <div class="field-grid">
              <label>${fieldLabel("Operator name")}<input name="operator_name" value="${escapeHtml(s.operator_name)}"></label>
              <label>Evaluation date (UTC)<input value="${s.date}" disabled><span class="rn-muted" style="font-weight:400">Fixed: recorded trials use it.</span></label>
            </div>
          </section>
          <section class="rn-card">
            <h3>Trial plan</h3>
            <div class="field-grid">
              <label>Trials per task<input name="per_task" type="number" min="1" max="50" value="${s.trials_per_task || 5}"></label>
              <label>Order of the remaining trials
                ${segmentedControlHtml("order", ["shuffled", "ordered"], s.randomized === false ? "ordered" : "shuffled", opts, false)}
              </label>
              <div class="full">
                <div class="rn-label">${fieldLabel("Tasks")}</div>
                <button type="button" class="task-chip" id="rnPickTasks">${rnTasksChipHtml(rn.editTasks)}</button>
                <span class="rn-muted" style="font-weight:400;font-size:0.85rem">Tasks with recorded trials stay in the plan.</span>
              </div>
            </div>
          </section>
        </div>
        <aside class="rn-summary">
          <div class="rn-card rn-summary-card">
            <h3>Plan summary</h3>
            <div class="rn-big" id="rnSumTrials">0</div><div class="rn-muted">trials in total</div>
            <dl class="rn-facts"><div><dt>Recorded (kept)</dt><dd>${s.n_recorded}</dd></div>
              <div><dt>Still to run</dt><dd id="rnSumTasks">0</dd></div>
              <div><dt>Longest possible</dt><dd id="rnSumTime">0 min</dd></div></dl>
            <p class="view-error" id="rnFormError"></p>
            <button type="submit" class="rn-cta">Save plan</button>
          </div>
        </aside>
      </form>
    </div>
  </div>`;
}

// Opens the edit dialog for `session` (from the sessions list) or for the open session (plan page).
function rnOpenEditPlan(session = null) {
  rnCloseModal();
  const s = session || rn.session;
  const recordedBy = {};
  s.plan.filter((p) => p.recorded).forEach((p) => { recordedBy[p.attachment_id] = (recordedBy[p.attachment_id] || 0) + 1; });
  rn.editTasks = Object.keys(rn.tasks).filter((id) => s.per_task[id]);
  rn.root.insertAdjacentHTML("beforeend", rnEditPlanModalHtml(s));
  document.addEventListener("keydown", rnModalKey);
  const modal = document.getElementById("rnModal");
  document.getElementById("rnModalClose").onclick = rnCloseModal;
  modal.addEventListener("mousedown", (e) => { if (e.target === modal) rnCloseModal(); });
  const form = document.getElementById("rnEditForm");
  wireSegmentedControls(form);
  const size = () => {
    const per = Number(form.elements["per_task"].value) || 0;
    const total = rn.editTasks.reduce((sum, id) => sum + Math.max(per, recordedBy[id] || 0), 0);
    const pending = rn.editTasks.reduce((sum, id) => sum + Math.max(0, per - (recordedBy[id] || 0)), 0);
    document.getElementById("rnSumTrials").textContent = total;
    document.getElementById("rnSumTasks").textContent = pending;
    document.getElementById("rnSumTime").textContent = `${Math.round(rn.editTasks.reduce((sum, id) => sum + Math.max(0, per - (recordedBy[id] || 0)) * (rnTask(id).timeout || 0), 0) / 60)} min`;
  };
  form.addEventListener("input", size); form.addEventListener("change", size); size();
  document.getElementById("rnPickTasks").onclick = () => openTaskPickerMulti(rn.tasks, rn.editTasks, (ids) => {
    const locked = Object.keys(recordedBy).filter((id) => !ids.includes(id));
    if (locked.length) showToast("Tasks with recorded trials stay in the plan.", { type: "success", title: "Kept", timeout: 3000 });
    rn.editTasks = Object.keys(rn.tasks).filter((id) => ids.includes(id) || recordedBy[id]);
    document.getElementById("rnPickTasks").innerHTML = rnTasksChipHtml(rn.editTasks);
    size();
  });
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(form);
    try {
      const updated = await api(`/api/runner/sessions/${s.session_id}/plan`, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          attachment_ids: rn.editTasks, per_task: Number(fd.get("per_task")),
          randomize: fd.get("order") !== "ordered", operator_name: fd.get("operator_name"),
        }),
      });
      if (session) rn.sessions = rn.sessions.map((x) => (x.session_id === updated.session_id ? updated : x));   // stay on the list
      else rn.session = updated;
      rnCloseModal();
      showToast("The remaining trials were re-planned.", { type: "success", title: "Plan updated", timeout: 3000 });
      rnRender();
    } catch (x) { document.getElementById("rnFormError").textContent = x.message; }
  });
}

function rnWireSetup() {
  const find = (id) => rn.sessions.find((x) => x.session_id === id);
  rn.root.querySelectorAll("[data-resume]").forEach((b) => (b.onclick = () => { const s = find(b.dataset.resume); rnUseSession(s, s.complete ? "review" : "task"); }));
  rn.root.querySelectorAll("[data-edit]").forEach((b) => (b.onclick = () => rnOpenEditPlan(find(b.dataset.edit))));   // no navigation
  rn.root.querySelectorAll("[data-delete]").forEach((b) => (b.onclick = async () => {
    const sid = b.dataset.delete;
    if (!confirm(`Delete session ${sid} and its trials? This cannot be undone.`)) return;
    try {
      await api(`/api/runner/sessions/${encodeURIComponent(sid)}`, { method: "DELETE" });
      rn.sessions = await api("/api/runner/sessions");
      rnRender();
      showToast(`Session ${sid} deleted.`, { type: "success", title: "Deleted", timeout: 3000 });
    } catch (err) { showToast(err.message, { type: "error", title: "Delete failed", timeout: 9000 }); }
  }));
  document.getElementById("rnNewBtn").onclick = rnOpenNewSession;
}

// ---------------------------------------------------------------- task plan
function rnSessionHeaderHtml() {
  const s = rn.session;
  const pct = s.n_planned ? Math.round((100 * s.n_recorded) / s.n_planned) : 0;
  const rate = s.n_recorded ? Math.round((100 * s.n_success) / s.n_recorded) : null;
  return `
    <section class="rn-hero">
      <div class="rn-hero-top">
        <div><p class="eyebrow">Session</p><h2><code>${escapeHtml(s.session_id)}</code></h2>
          <div class="rn-chips"><span class="chip-static">${escapeHtml(s.lab_id)}</span><span class="chip-static">${escapeHtml(s.platform_id)}</span>
          <span class="chip-static">${escapeHtml(s.operator_name)}</span><span class="chip-static">${s.date}</span></div></div>
        <label class="toggle-control" title="Countdown and timer beeps"><input type="checkbox" id="rnSound" ${rn.sound ? "checked" : ""}><span>Sounds</span></label>
      </div>
      <div class="rn-stats">
        <div><b>${s.n_recorded}<small>/${s.n_planned}</small></b><span>trials recorded</span></div>
        <div><b>${s.n_success}</b><span>successful</span></div>
        <div><b>${rate === null ? "&ndash;" : rate + "%"}</b><span>success rate</span></div>
        <div><b>${s.n_planned - s.n_recorded}</b><span>remaining</span></div>
        <div><b>${s.n_episodes_valid || 0}<small>/${s.n_recorded}</small></b><span>valid episodes</span></div>
      </div>
      <div class="progress-bar rn-progress"><div style="width:${pct}%"></div></div>
    </section>`;
}

function rnPlanHtml() {
  const s = rn.session;
  const inSession = Object.fromEntries(Object.entries(rn.tasks).filter(([id]) => s.per_task[id]));
  const cards = taskGroupsHtml(inSession, (id, info) => {
    const c = s.per_task[id];
    return taskCardHtml(id, info, c.recorded >= c.planned, `${c.recorded}/${c.planned} trials`);
  });
  const next = s.next_trial;
  return `
  <div class="view-inner">
    ${rnStepsHtml(`${rnIconBtn('id="rnEditPlan"', RN_ICON_EDIT, "Edit plan")}<button type="button" id="rnReview">Review ${s.n_recorded} recorded</button>`)}
    ${rnSessionHeaderHtml()}
    <div class="rn-toolbar">
      <span class="rn-spacer"></span>
      ${next ? `<button type="button" class="rn-cta" id="rnRunNext"><small>Next up</small>${escapeHtml(rnTask(next.attachment_id).name)} <em>#${next.trial_id}</em> &#9654;</button>` : `<span class="rn-done">All trials recorded</span>`}
    </div>
    <div class="rn-plan-tasks">${cards}</div>
  </div>`;
}

function rnOpenTrial(entry) {
  rn.entry = entry;
  rn.step = "trial";
  rnResetTimer();
  rnRender();
}

function rnWirePlan() {
  const s = rn.session;
  document.getElementById("rnReview").onclick = () => { rn.step = "review"; rnRender(); };
  document.getElementById("rnEditPlan").onclick = () => rnOpenEditPlan();
  const runNext = document.getElementById("rnRunNext");
  if (runNext) runNext.onclick = () => rnOpenTrial(s.next_trial);
  rn.root.querySelectorAll(".task-card").forEach((b) => (b.onclick = () => {
    const entry = s.plan.find((p) => p.attachment_id === b.dataset.taskId && !p.recorded);
    if (!entry) { showToast("Every trial of this task is recorded.", { type: "success", title: "Task complete", timeout: 3000 }); return; }
    rnOpenTrial(entry);
  }));
}

// ---------------------------------------------------------------- trial
function rnModeToggleHtml(busy) {
  const s = rn.session;
  if (busy) return `<span class="chip-static" title="Finish or cancel this trial to change mode">${escapeHtml(humanize(s.mode))} mode</span>`;
  return `<form id="rnModeForm" class="rn-mode">${segmentedControlHtml("session_mode", ["manual", "automatic"], s.mode, RN_MODE_TITLES, false)}</form>`;
}

function rnAutomaticTrialHtml() {
  const s = rn.session, e = rn.entry, task = rnTask(e.attachment_id);
  const sameTask = s.plan.filter((p) => p.attachment_id === e.attachment_id);
  const doneCount = sameTask.filter((p) => p.recorded).length;
  const pct = Math.round((100 * s.n_recorded) / s.n_planned);
  const busy = auto.state.status !== "idle";
  return `
  <div class="view-inner">
    ${rnStepsHtml()}
    <div class="rn-trial-head">
      <div><p class="eyebrow">${escapeHtml(task.family || "")} &middot; queue #${e.trial_id} of ${s.n_planned}</p>
        <h2>${escapeHtml(task.name)}</h2>
        <div class="rn-muted">Trial ${Math.min(doneCount + 1, sameTask.length)} of ${sameTask.length} for this task
          <span class="recorded-dots">${sameTask.map((p) => `<span class="${p.recorded ? "filled" : ""}"></span>`).join("")}</span></div></div>
      <div class="rn-trial-side">${rnModeToggleHtml(busy)}<div class="progress-bar rn-progress"><div style="width:${pct}%"></div></div></div>
    </div>
    <div class="trial-layout">
      ${rnTaskRefHtml(e.attachment_id)}
      <div id="rnAutoHost"></div>
    </div>
    ${autoSnippetHtml(false)}
    <div class="rn-bottombar"><button type="button" id="rnBack" ${busy ? "disabled" : ""}>&larr; Back to plan</button></div>
  </div>`;
}

function rnUploadItems() { return epUploadItems(rn.cameras); }

function rnUploadHtml() {
  const items = rnUploadItems();
  const u = rn.upload, e = rn.entry;
  const missing = items.filter((i) => !u.files[i.key]);
  const noCams = rn.cameras.length === 0;
  const problems = u.result && !u.result.valid
    ? `<div class="rn-result bad"><b>The upload has problems.</b><ul>${(u.result.problems || []).map((p) => `<li>${escapeHtml(p)}</li>`).join("")}</ul></div>` : "";
  return `
  <section class="rn-card" id="rnUploadCard" style="margin-top:1rem">
    <h3>Upload the recording for trial #${e.trial_id}</h3>
    <p class="rn-muted" style="margin-top:0">Upload the HDF5 file <b>and one video for every camera</b> in the robot profile (${rn.cameras.length ? rn.cameras.map(escapeHtml).join(", ") : "none defined"}). When it is uploaded you go straight to <b>Annotate</b> to annotate this episode.</p>
    ${noCams ? `<div class="rn-alert">The robot profile has no cameras. Add them in <b>Robot Profile</b> first.</div>` : ""}
    <div class="rn-uploads">${epRowsHtml(items, u.files, u.busy)}</div>
    ${problems}
    <div class="rn-form-actions">
      <span class="rn-later"><button type="button" id="rnLater">Do it later</button>
        <span class="rn-muted">${missing.length ? `Still needed: ${missing.map((m) => escapeHtml(m.label)).join(", ")}` : ""}</span></span>
      <button type="button" class="rn-cta" id="rnUploadBtn" ${missing.length || u.busy || noCams ? "disabled" : ""}>${u.busy ? "Uploading&hellip;" : "Upload and annotate &rarr;"}</button>
    </div>
  </section>`;
}

const RN_ICON_GOAL = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"></circle><circle cx="12" cy="12" r="4"></circle><circle cx="12" cy="12" r="0.8" fill="currentColor"></circle></svg>`;
const RN_ICON_RESET = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="1 4 1 10 7 10"></polyline><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"></path></svg>`;
const RN_ICON_STAGES = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="8" y1="6" x2="21" y2="6"></line><line x1="8" y1="12" x2="21" y2="12"></line><line x1="8" y1="18" x2="21" y2="18"></line><circle cx="3.5" cy="6" r="0.8" fill="currentColor"></circle><circle cx="3.5" cy="12" r="0.8" fill="currentColor"></circle><circle cx="3.5" cy="18" r="0.8" fill="currentColor"></circle></svg>`;
const RN_ICON_CLOCK = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"></circle><polyline points="12 7 12 12 15 14"></polyline></svg>`;

// The task description shown beside the timer (manual) or the robot panel (automatic).
function rnTaskRefHtml(attachmentId) {
  const t = rnTask(attachmentId);
  const stages = t.stages && t.stages.length
    ? `<div class="task-ref-block"><div class="task-ref-label">${RN_ICON_STAGES}Stages, in order</div>
        <ol class="task-ref-stages">${t.stages.map((st, i) => `<li><b>${i + 1}</b><span>${escapeHtml(st)}</span></li>`).join("")}</ol></div>` : "";
  return `
  <section class="rn-card task-ref">
    <div class="task-ref-media">
      ${rnImg(attachmentId) || '<div class="task-ref-noimg"></div>'}
      <span class="task-ref-tag">${escapeHtml(t.family || "Task")}</span>
      ${t.timeout ? `<span class="task-ref-tag time">${RN_ICON_CLOCK}${t.timeout} s limit</span>` : ""}
    </div>
    <div class="task-ref-body">
      <div class="task-ref-block goal"><div class="task-ref-label">${RN_ICON_GOAL}Goal</div><p>${escapeHtml(t.success || "")}</p></div>
      ${stages}
      <div class="task-ref-block reset"><div class="task-ref-label">${RN_ICON_RESET}Reset before the next trial</div><p>${escapeHtml(t.reset || "")}</p></div>
    </div>
  </section>`;
}

function rnTrialHtml() {
  if (rn.session.mode === "automatic") return rnAutomaticTrialHtml();
  const s = rn.session, e = rn.entry, task = rnTask(e.attachment_id), t = rn.timer;
  const sameTask = s.plan.filter((p) => p.attachment_id === e.attachment_id);
  const doneCount = sameTask.filter((p) => p.recorded).length;
  const pct = Math.round((100 * s.n_recorded) / s.n_planned);
  let ring = "", label = "", actions = "";
  if (t.state === "countdown") {
    label = "Get ready";
    ring = rnRingHtml(1 - t.countdown / 5, "countdown", `<div class="rn-ring-num" id="rnCountdown">${t.countdown}</div>`);
    actions = `<button type="button" id="rnCancel">Cancel</button>`;
  } else if (t.state === "running") {
    label = `Timeout at ${task.timeout} s`;
    ring = rnRingHtml(t.elapsedMs / (task.timeout * 1000), rnRunningColor(t.elapsedMs, task.timeout),
      `<div class="rn-ring-time" id="rnStopwatch">${formatStopwatch(t.elapsedMs)}</div><div class="rn-ring-sub">running</div>`);
    actions = `<button type="button" class="rn-stop" id="rnStop"><span class="rn-btn-icon"><svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="6.5" y="6.5" width="11" height="11" rx="1.5"></rect></svg></span><span>Stop timer</span></button><div class="rn-key-hint">Press <kbd>Space</kbd> to stop</div>`;
  } else if (t.state === "upload") {
    label = t.elapsedMs >= task.timeout * 1000 ? "Time limit reached" : "Timer stopped";
    ring = rnRingHtml(t.elapsedMs / (task.timeout * 1000), "done", `<div class="rn-ring-time">${formatStopwatch(t.elapsedMs)}</div><div class="rn-ring-sub">stopped</div>`);
  } else {
    label = `Timeout at ${task.timeout} s`;
    ring = rnRingHtml(0, "idle", `<div class="rn-ring-time">00:00.00</div><div class="rn-ring-sub">ready</div>`);
    actions = `<button type="button" class="rn-go" id="rnStart"><span class="rn-btn-icon"><svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><polygon points="8 5 19 12 8 19 8 5"></polygon></svg></span><span>Start countdown</span></button><div class="rn-key-hint">Press <kbd>Space</kbd> to start</div>`;
  }
  return `
  <div class="view-inner">
    ${rnStepsHtml()}
    <div class="rn-trial-head">
      <div><p class="eyebrow">${escapeHtml(task.family || "")} &middot; queue #${e.trial_id} of ${s.n_planned}</p>
        <h2>${escapeHtml(task.name)}</h2>
        <div class="rn-muted">Trial ${Math.min(doneCount + 1, sameTask.length)} of ${sameTask.length} for this task
          <span class="recorded-dots">${sameTask.map((p) => `<span class="${p.recorded ? "filled" : ""}"></span>`).join("")}</span></div></div>
      <div class="rn-trial-side">${rnModeToggleHtml(t.state !== "idle")}<div class="progress-bar rn-progress"><div style="width:${pct}%"></div></div></div>
    </div>
    <aside class="recording-reminder"><strong>External recording required.</strong> Record the whole trial with an external camera. Keep the board, robot, end-effector and final task state visible.</aside>
    <div class="trial-layout">
      ${rnTaskRefHtml(e.attachment_id)}
      <section class="timer-panel ${t.state}" id="rnTimerPanel">
        <div class="rn-timer-label">${label}</div>
        ${ring}
        <div class="timer-actions">${actions}</div>
      </section>
    </div>
    ${t.state === "upload" ? rnUploadHtml() : ""}
    <div class="rn-bottombar"><button type="button" id="rnBack" ${t.state === "idle" ? "" : "disabled"}>&larr; Back to plan</button></div>
  </div>`;
}

function rnTimerSeconds() {
  const ms = rn.timer.elapsedMs;
  return ms > 0 ? Number((ms / 1000).toFixed(3)) : null;
}

function rnStartCountdown() {
  const t = rn.timer;
  if (t.state !== "idle") return;
  t.state = "countdown"; t.countdown = 5; t.endAt = performance.now() + 5000;
  beep(520);
  rnClearTicker();
  t.ticker = window.setInterval(() => {
    t.countdown = Math.max(0, Math.ceil((t.endAt - performance.now()) / 1000));
    const el = document.getElementById("rnCountdown");
    if (el) el.textContent = t.countdown;
    rnSetRing(1 - t.countdown / 5);
    if (t.countdown > 0) beep(520);
    else { rnClearTicker(); rnStartTimer(); }
  }, 1000);
  rnRender();
}

function rnStartTimer() {
  const t = rn.timer, task = rnTask(rn.entry.attachment_id);
  t.state = "running"; t.elapsedMs = 0; t.t0 = performance.now();
  beep(880, 0.16);
  rnClearTicker();
  t.ticker = window.setInterval(() => {
    t.elapsedMs = performance.now() - t.t0;
    if (t.elapsedMs >= task.timeout * 1000) { t.elapsedMs = task.timeout * 1000; rnFinishTiming(true); return; }
    const el = document.getElementById("rnStopwatch");
    if (el) el.textContent = formatStopwatch(t.elapsedMs);
    rnSetRing(t.elapsedMs / (task.timeout * 1000), rnRunningColor(t.elapsedMs, task.timeout));
  }, 50);
  rnRender();
}

function rnFinishTiming(timedOut) {
  const t = rn.timer;
  if (t.state !== "running") return;
  if (!timedOut) t.elapsedMs = Math.min(performance.now() - t.t0, rnTask(rn.entry.attachment_id).timeout * 1000);
  rnClearTicker();
  t.state = "upload";
  rn.upload = { files: {}, busy: false, result: null };
  beep(timedOut ? 360 : 740, 0.18);
  rnRender();
}

async function rnRefreshSession() {
  rn.session = await api(`/api/runner/sessions/${rn.session.session_id}`);
}

async function rnUploadEpisode() {
  const u = rn.upload;
  u.busy = true; u.result = null; rnRender();
  try {
    const body = await epSend(rn.session.session_id, rn.entry.trial_id, u.files, false, false, rnTimerSeconds());
    u.result = body;
    if (body.valid) {
      await rnRefreshSession();
      rnResetTimer();
      rn.step = "task";
      rnRender();
      showToast(`Episode ${body.episode_id} uploaded. Annotate it now.`, { type: "success", title: "Uploaded", timeout: 3500 });
      await epOpenInAnnotate(body.episode_id);
      return;
    }
  } catch (err) {
    u.result = { valid: false, problems: [err.message] };
  }
  u.busy = false;
  rnRender();
}

function rnWireUpload() {
  const card = document.getElementById("rnUploadCard");
  if (!card) return;
  epWireRows(card, rn.upload.files, () => { rn.upload.result = null; rnRender(); });
  const btn = document.getElementById("rnUploadBtn");
  if (btn) btn.onclick = rnUploadEpisode;
  const later = document.getElementById("rnLater");
  if (later) later.onclick = rnDoItLater;
}

// "Do it later": the episode is created now, without files, and marked Incomplete so it
// shows up in Annotate. The operator adds the files and annotates it there.
async function rnDoItLater() {
  const e = rn.entry, s = rn.session, task = rnTask(e.attachment_id);
  let body;
  try {
    body = await epSend(s.session_id, e.trial_id, {}, true, true, rnTimerSeconds());
  } catch (err) { showToast(err.message, { type: "error", title: "Could not create the episode" }); return; }
  const close = () => { const m = document.getElementById("rnLaterModal"); if (m) m.remove(); };
  close();
  document.body.insertAdjacentHTML("beforeend", `
  <div class="rn-modal" id="rnLaterModal" style="z-index:60">
    <div class="rn-modal-panel" style="width:min(520px,100%)" role="dialog" aria-label="Finish this trial in Annotate">
      <div class="rn-modal-head"><h2>Finish this trial in Annotate</h2><button type="button" id="rnLaterX" aria-label="Close">&#10005;</button></div>
      <section class="rn-card">
        <p style="margin-top:0">Trial <b>#${e.trial_id}</b> (${escapeHtml(task.name)}) was saved as an <b>incomplete episode</b>: <code>${escapeHtml(body.episode_id)}</code>.</p>
        <p class="rn-muted">Open it in <b>Annotate</b>, use <b>Add files</b> to upload the HDF5 file and the video for ${rn.cameras.length ? `each camera (${rn.cameras.map(escapeHtml).join(", ")})` : "each camera"}, then annotate it. The duration comes from the episode itself. It stays marked <b>Incomplete</b> until every file is uploaded.</p>
        <div class="rn-form-actions"><button type="button" id="rnLaterBack">Back to plan</button>
          <button type="button" class="rn-cta" id="rnLaterGo">Go to Annotate &rarr;</button></div>
      </section>
    </div>
  </div>`);
  const leave = () => { close(); rnResetTimer(); rn.step = "task"; rnRender(); };
  document.getElementById("rnLaterX").onclick = leave;
  document.getElementById("rnLaterBack").onclick = leave;
  document.getElementById("rnLaterGo").onclick = async () => {
    leave();
    showView("annotate");
    await refreshList();
    await renderListStats();
    await selectEpisode(body.episode_id);
  };
}

function rnWireModeToggle() {
  const form = document.getElementById("rnModeForm");
  if (!form) return;
  wireSegmentedControls(form);
  form.addEventListener("segmentchange", async () => {
    const mode = form.elements["session_mode"].value;
    if (!mode || mode === rn.session.mode) return;
    try {
      rn.session = await api(`/api/runner/sessions/${rn.session.session_id}`, {
        method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ mode }),
      });
      rnResetTimer();
      rnRender();
    } catch (err) { showToast(err.message, { type: "error", title: "Mode" }); rnRender(); }
  });
}

function rnWireTrial() {
  rnWireModeToggle();
  document.getElementById("rnBack").onclick = () => { rn.step = "task"; rnRender(); };
  rnWireUpload();
  if (rn.session.mode === "automatic") { autoMount(document.getElementById("rnAutoHost")); autoWireSnippet(); return; }
  const on = (id, fn) => { const el = document.getElementById(id); if (el) el.onclick = fn; };
  on("rnStart", rnStartCountdown);
  on("rnCancel", () => { rnResetTimer(); rnRender(); });
  on("rnStop", () => rnFinishTiming(false));
}

// ---------------------------------------------------------------- review
// Review table: a status badge for the episode, and compact actions in their own column.
function rnEpisodeBadgeHtml(p) {
  if (p.episode && p.episode.incomplete) return `<span class="rn-badge warn" title="Missing: ${escapeHtml(p.episode.missing.join(", "))}">Incomplete</span>`;
  if (p.episode && p.episode.valid) return `<span class="rn-badge ok">Valid</span>`;
  if (p.episode) return `<span class="rn-badge bad" title="${escapeHtml(p.episode.status)}">Not valid</span>`;
  return `<span class="rn-badge none">No episode</span>`;
}

function rnEpisodeActionsHtml(p) {
  const mini = (attrs, label) => `<button type="button" class="rn-mini" ${attrs}>${label}</button>`;
  const eid = p.episode ? escapeHtml(p.episode.episode_id) : "";
  const buttons = [];
  if (p.episode && p.episode.incomplete) buttons.push(mini(`data-complete="${p.trial_id}"`, "Complete"), mini(`data-annotate="${eid}"`, "Annotate"));
  else if (p.episode && p.episode.valid) buttons.push(mini(`data-annotate="${eid}"`, "Annotate"));
  else if (p.episode) buttons.push(mini(`data-annotate="${eid}"`, "Open"), mini(`data-upload="${p.trial_id}"`, "Re-upload"));
  else buttons.push(mini(`data-upload="${p.trial_id}"`, "Upload"));
  if (p.row) buttons.push(rnIconBtn(`data-remove="${p.trial_id}"`, RN_ICON_TRASH, "Remove trial", "danger"));
  return `<div class="rn-row-actions">${buttons.join("")}</div>`;
}

function rnReviewHtml() {
  const s = rn.session;
  const rows = s.plan.filter((p) => p.recorded || p.episode).map((p) => `
    <tr><td><b>${p.trial_id}</b></td><td>${escapeHtml(rnTask(p.attachment_id).name)}</td>
    <td>${p.row ? `<span class="outcome-badge outcome-${escapeHtml(p.row.outcome)}">${escapeHtml(humanize(p.row.outcome))}</span>` : `<span class="rn-muted">not annotated</span>`}</td>
    <td>${p.row ? (p.row.completion_time_s || "&ndash;") : "&ndash;"}</td><td>${p.row ? p.row.n_attempts : "&ndash;"}</td><td>${p.row ? p.row.n_regrasps : "&ndash;"}</td>
    <td>${rnEpisodeBadgeHtml(p)}</td>
    <td class="rn-right">${rnEpisodeActionsHtml(p)}</td></tr>`).join("");
  return `
  <div class="view-inner">
    ${rnStepsHtml()}
    ${rnSessionHeaderHtml()}
    <section class="rn-card">
      ${rows ? `<div class="table-scroll"><table class="trial-table rn-trials-table"><thead><tr><th>#</th><th>Task</th><th>Outcome</th><th>Time (s)</th><th>Attempts</th><th>Regrasps</th><th>Episode</th><th class="rn-right">Actions</th></tr></thead><tbody>${rows}</tbody></table></div>`
             : `<p class="rn-muted" style="margin:0">No trials recorded yet.</p>`}
    </section>
    <div class="rn-toolbar">
      <button type="button" id="rnToPlan">&larr; Back to plan</button>
      <span class="rn-spacer"></span>
      ${s.n_episodes_valid ? `<a href="/api/runner/sessions/${encodeURIComponent(s.session_id)}/trials.csv" class="rn-link"><button type="button">Download trials.csv (${s.n_episodes_valid} valid)</button></a>` : `<span class="rn-muted" style="font-size:0.85rem">trials.csv can be downloaded once an episode is valid</span>`}
      
    </div>
  </div>`;
}

function rnGlobalReviewHtml() {
  const rows = rn.sessions.map((s) => `
    <tr><td><code>${escapeHtml(s.session_id)}</code></td><td>${s.date}</td><td>${escapeHtml(s.operator_name)}</td>
    <td>${escapeHtml(humanize(s.mode || "manual"))}</td><td>${s.n_recorded}/${s.n_planned}</td><td>${s.n_success}</td>
    <td>${s.n_episodes_valid || 0}</td>
    <td class="rn-right"><div class="rn-row-actions"><button type="button" class="rn-mini" data-open="${escapeHtml(s.session_id)}">Open</button>${s.n_episodes_valid ? `<a href="/api/runner/sessions/${encodeURIComponent(s.session_id)}/trials.csv" class="rn-link"><button type="button" class="rn-mini">trials.csv</button></a>` : ""}</div></td></tr>`).join("");
  return `
  <div class="view-inner rn-wide">
    <div class="rn-head"><p class="eyebrow">Runner</p><h2>Review</h2>
      <p class="rn-muted">Results of every session. Open one to see its trials and episodes.</p></div>
    ${rnStepsHtml()}
    <section class="rn-card">
      ${rows ? `<div class="table-scroll"><table class="trial-table rn-sessions-table"><thead><tr><th>Session</th><th>Date</th><th>Operator</th><th>Mode</th><th>Trials</th><th>Success</th><th>Valid</th><th class="rn-right">Actions</th></tr></thead><tbody>${rows}</tbody></table></div>`
             : `<p class="rn-muted" style="margin:0">No sessions yet. Create one from Setup.</p>`}
    </section>
  </div>`;
}

function rnWireGlobalReview() {
  rn.root.querySelectorAll("[data-open]").forEach((b) => (b.onclick = () => rnUseSession(rn.sessions.find((x) => x.session_id === b.dataset.open), "review")));
}

function rnWireReview() {
  document.getElementById("rnToPlan").onclick = () => { rn.step = "task"; rnRender(); };
  rn.root.querySelectorAll("[data-annotate]").forEach((b) => (b.onclick = async () => { showView("annotate"); await selectEpisode(b.dataset.annotate); }));
  rn.root.querySelectorAll("[data-complete]").forEach((b) => (b.onclick = () => {
    const entry = rn.session.plan.find((p) => p.trial_id === b.dataset.complete);
    openEpisodeUpload({ episode_id: entry.episode.episode_id, session_id: rn.session.session_id, trial_id: entry.trial_id });
  }));
  rn.root.querySelectorAll("[data-upload]").forEach((b) => (b.onclick = () => {
    const entry = rn.session.plan.find((p) => p.trial_id === b.dataset.upload);
    rn.entry = entry; rn.step = "trial";
    rnResetTimer(); rn.timer.state = "upload"; rn.upload = { files: {}, busy: false, result: null };
    rnRender();
  }));
  rn.root.querySelectorAll("[data-remove]").forEach((b) => (b.onclick = async () => {
    if (!confirm(`Remove trial ${b.dataset.remove} from the session?`)) return;
    try {
      const res = await api(`/api/runner/sessions/${rn.session.session_id}/trials/${b.dataset.remove}`, { method: "DELETE" });
      rn.session = res.session;
      rnRender();
    } catch (err) { showToast(err.message, { type: "error", title: "Remove failed" }); }
  }));
}

// ---------------------------------------------------------------- shell
function rnRender() {
  if (!rn.loaded) { rn.root.innerHTML = '<div class="view-inner"><p>Loading&hellip;</p></div>'; return; }
  if (!rn.session && rn.step !== "setup" && rn.step !== "review") rn.step = "setup";
  if (rn.step === "trial" && !rn.entry) rn.step = "task";
  autoUnmount();
  rnCloseModal();
  const html = { setup: rnSetupHtml, task: rnPlanHtml, trial: rnTrialHtml, review: rn.session ? rnReviewHtml : rnGlobalReviewHtml }[rn.step]();
  rn.root.innerHTML = html;
  rn.root.classList.toggle("rn-centered", !rn.session && (rn.step === "setup" || rn.step === "review"));
  rnSaveState();
  rn.root.querySelectorAll("[data-back]").forEach((b) => (b.onclick = () => { rn.session = null; rn.entry = null; rn.step = "setup"; rnRender(); }));
  rn.root.querySelectorAll(".rn-steps button[data-step]").forEach((b) => (b.onclick = () => {
    if (b.dataset.step === "setup") { rn.session = null; rn.entry = null; }
    rn.step = b.dataset.step;
    rnRender();
  }));
  const sound = document.getElementById("rnSound");
  if (sound) sound.onchange = () => {
    rn.sound = sound.checked;
    try { localStorage.setItem(RN_SOUND_KEY, rn.sound ? "on" : "off"); } catch (e) { /* ignore */ }
    if (rn.sound) beep(660);
  };
  ({ setup: rnWireSetup, task: rnWirePlan, trial: rnWireTrial, review: rn.session ? rnWireReview : rnWireGlobalReview })[rn.step]();
}

window.addEventListener("viewchange", async (e) => {
  if (e.detail.view !== "runner") return;
  // Opening the Runner always starts on the plans list, unless a trial is being timed or recorded.
  const busy = ["countdown", "running"].includes(rn.timer.state) || ["pending", "running"].includes(auto.state.status);
  if (!busy) { rn.session = null; rn.entry = null; rn.step = "setup"; rnResetTimer(); rnCloseModal(); }
  try {
    if (!rn.loaded) await rnLoad();
    else {
      await rnLoadCameras();
      if (rn.step === "setup" || rn.step === "task" || rn.step === "review") {
        rn.sessions = await api("/api/runner/sessions");
        if (!rn.session) await rnEnsureDefaultSession();
        if (rn.session) rn.session = rn.sessions.find((s) => s.session_id === rn.session.session_id) || rn.session;
      }
    }
    rnRender();
  } catch (err) {
    rn.root.innerHTML = `<div class="view-inner"><p class="view-error">${escapeHtml(err.message)}</p></div>`;
  }
});

document.addEventListener("keydown", (event) => {
  if (document.body.dataset.view !== "runner" || rn.step !== "trial" || event.code !== "Space") return;
  if (rn.session.mode === "automatic" || rn.timer.state === "upload") return;
  const tag = event.target && event.target.tagName;
  if (["INPUT", "SELECT", "TEXTAREA", "BUTTON"].includes(tag)) return;
  event.preventDefault();
  if (rn.timer.state === "idle") rnStartCountdown();
  else if (rn.timer.state === "running") rnFinishTiming(false);
});

// Called by automatic.js when the annotation of an automatically recorded episode was saved.
async function rnAutoTrialDone(episodeId) {
  if (!rn.session) return;
  try { await rnRefreshSession(); } catch (e) { return; }
  showToast(`Episode ${episodeId} annotated. Open the Runner for the next trial.`, { type: "success", title: "Trial recorded", timeout: 4000 });
  if (rn.step === "trial" && rn.session.mode === "automatic") {
    if (rn.session.next_trial) rnOpenTrial(rn.session.next_trial);
    else { rn.step = "review"; rnRender(); }
  } else if (rn.loaded) rnRender();
}


// ---------------------------------------------------------------- Annotate: upload an episode
// Any subset of the files can be uploaded (HDF5 and/or videos). An episode that lacks
// files is marked Incomplete until the rest is added.
const ep = { files: {}, busy: false, error: "", result: null, sessions: [], cameras: [], sessionId: "", trialId: "", fixed: null, existing: {} };

function epCloseDialog() {
  const m = document.getElementById("epModal");
  if (m) m.remove();
  document.removeEventListener("keydown", epKey);
}

function epKey(e) { if (e.key === "Escape") epCloseDialog(); }

function epTrialOptions() {
  const s = ep.sessions.find((x) => x.session_id === ep.sessionId);
  if (!s) return [];
  const name = (id) => ((typeof rn !== "undefined" && rn.tasks[id]) || { name: id }).name;
  const sorted = [...s.plan].sort((a, b) => (a.episode ? 1 : 0) - (b.episode ? 1 : 0));
  return sorted.map((p) => ({ id: p.trial_id, label: `#${p.trial_id} \u00b7 ${name(p.attachment_id)}${p.episode ? " (has an episode: files are merged)" : ""}` }));
}

function epDialogHtml() {
  const items = epUploadItems(ep.cameras);
  const fixed = ep.fixed;
  const opts = epTrialOptions();
  if (!ep.trialId && opts.length) ep.trialId = opts[0].id;
  const nFiles = Object.keys(ep.files).length;
  const noSessions = !fixed && !ep.sessions.length;
  const r = ep.result;
  const result = r ? (r.incomplete
    ? `<div class="rn-result warn"><b>Uploaded, but the episode is incomplete.</b> Still missing: ${r.missing.map(escapeHtml).join(", ")}.</div>`
    : r.valid ? `<div class="rn-result ok"><b>Complete.</b> <code>${escapeHtml(r.episode_id)}</code></div>`
    : `<div class="rn-result bad"><b>Problems:</b><ul>${(r.problems || []).map((p) => `<li>${escapeHtml(p)}</li>`).join("")}</ul></div>`) : "";
  return `
  <div class="rn-modal" id="epModal">
    <div class="rn-modal-panel" role="dialog" aria-label="Upload an episode">
      <div class="rn-modal-head"><h2>Upload an episode</h2><button type="button" id="epClose" aria-label="Close">&#10005;</button></div>
      <section class="rn-card">
        <p class="rn-muted" style="margin-top:0">Add the <b>HDF5 recording</b> and/or the <b>videos</b> of an episode recorded outside DataHive (one video per camera in the robot profile${ep.cameras.length ? `: ${ep.cameras.map(escapeHtml).join(", ")}` : ""}). You can upload them separately; an episode with missing files is marked <b>Incomplete</b> until you add the rest.</p>
        ${noSessions ? `<div class="rn-alert">Episodes belong to a trial. Create a session in the <b>Runner</b> first.</div>` : ""}
        ${!ep.cameras.length ? `<div class="rn-alert">The robot profile has no cameras. Add them in <b>Robot Profile</b> to upload videos.</div>` : ""}
        ${fixed ? `<div class="rn-identity rn-muted">Episode <b>${escapeHtml(fixed.episode_id)}</b></div>` : `
        <div class="field-grid">
          <label>Session<select id="epSession">${ep.sessions.map((s) => `<option value="${escapeHtml(s.session_id)}" ${s.session_id === ep.sessionId ? "selected" : ""}>${escapeHtml(s.session_id)}</option>`).join("")}</select></label>
          <label>Trial<select id="epTrial">${opts.map((o) => `<option value="${o.id}" ${o.id === ep.trialId ? "selected" : ""}>${escapeHtml(o.label)}</option>`).join("")}</select></label>
        </div>`}
        <div class="rn-uploads">${epRowsHtml(items, ep.files, ep.busy, ep.existing)}</div>
        ${ep.error ? `<p class="view-error">${escapeHtml(ep.error)}</p>` : ""}
        ${result}
        <div class="rn-form-actions"><button type="button" id="epCancel">${r ? "Close" : "Cancel"}</button>
          <button type="button" class="rn-cta" id="epSubmit" ${nFiles && !ep.busy && !noSessions ? "" : "disabled"}>${ep.busy ? "Uploading&hellip;" : "Upload"}</button></div>
      </section>
    </div>
  </div>`;
}

// Which files the episode of the chosen trial already has, so the rows can show "Added".
async function epLoadExisting() {
  ep.existing = {};
  const s = ep.sessions.find((x) => x.session_id === ep.sessionId);
  const entry = s && s.plan.find((p) => p.trial_id === ep.trialId);
  const id = ep.fixed ? ep.fixed.episode_id : (entry && entry.episode && entry.episode.episode_id);
  if (!id) return;
  try {
    const d = await api(`/api/episodes/${encodeURIComponent(id)}`);
    const missing = d.missing || [];
    ep.existing = { h5: !missing.includes("HDF5 recording data") };
    ep.cameras.forEach((c) => { ep.existing[`video:${c}`] = !missing.includes(`video: ${c}`); });
  } catch (e) { ep.existing = {}; }
}

function epRender() {
  const m = document.getElementById("epModal");
  if (m) m.remove();
  document.body.insertAdjacentHTML("beforeend", epDialogHtml());
  const modal = document.getElementById("epModal");
  document.getElementById("epClose").onclick = epCloseDialog;
  document.getElementById("epCancel").onclick = epCloseDialog;
  modal.addEventListener("mousedown", (e) => { if (e.target === modal) epCloseDialog(); });
  epWireRows(modal, ep.files, () => { ep.result = null; ep.error = ""; epRender(); });
  const sel = document.getElementById("epSession");
  if (sel) sel.onchange = async () => { ep.sessionId = sel.value; ep.trialId = ""; ep.files = {}; epTrialOptions(); const o = epTrialOptions(); ep.trialId = o.length ? o[0].id : ""; await epLoadExisting(); epRender(); };
  const tr = document.getElementById("epTrial");
  if (tr) tr.onchange = async () => { ep.trialId = tr.value; ep.files = {}; await epLoadExisting(); epRender(); };
  document.getElementById("epSubmit").onclick = epSubmit;
}

async function epSubmit() {
  ep.busy = true; ep.error = ""; epRender();
  try {
    const sid = ep.fixed ? ep.fixed.session_id : ep.sessionId;
    const tid = ep.fixed ? ep.fixed.trial_id : ep.trialId;
    const labels = Object.fromEntries(epUploadItems(ep.cameras).map((i) => [i.key, i.label]));
    const summary = Object.keys(ep.files).map((k) => `${labels[k] || k} ${ep.existing[k] ? "replaced" : "added"}`).join(", ");
    const body = await epSend(sid, tid, ep.files, true);
    ep.busy = false;
    epCloseDialog();
    showToast(body.incomplete ? `${summary}. The episode is still incomplete: missing ${body.missing.join(", ")}.` : `${summary}. The episode is complete.`,
      { type: body.incomplete ? "error" : "success", title: body.incomplete ? "Incomplete episode" : "Uploaded", timeout: 6000 });
    showView("annotate");
    await refreshList();
    await renderListStats();
    await selectEpisode(body.episode_id);
  } catch (err) {
    ep.busy = false; ep.error = err.message; epRender();
  }
}

async function openEpisodeUpload(fixed = null, preset = null) {
  Object.assign(ep, { files: {}, busy: false, error: "", result: null, fixed, trialId: fixed ? fixed.trial_id : "" });
  try {
    const [sessions, profile] = await Promise.all([api("/api/runner/sessions"), api("/api/profile")]);
    ep.sessions = sessions;
    ep.cameras = ((profile.profile && profile.profile.cameras) || []).map((c) => c.name).filter(Boolean);
    ep.sessionId = fixed ? fixed.session_id : preset ? preset.session_id : ((sessions.find((s) => !s.complete) || sessions[0] || {}).session_id || "");
    if (preset) ep.trialId = preset.trial_id;
  } catch (err) { showToast(err.message, { type: "error", title: "Upload episode" }); return; }
  if (!ep.trialId) { const o = epTrialOptions(); ep.trialId = o.length ? o[0].id : ""; }
  await epLoadExisting();
  document.addEventListener("keydown", epKey);
  epRender();
}

document.getElementById("uploadEpisodeBtn").addEventListener("click", () => openEpisodeUpload());
document.addEventListener("click", (e) => {
  const b = e.target.closest("#addFilesBtn");
  if (b) openEpisodeUpload({ episode_id: b.dataset.episode, session_id: b.dataset.session, trial_id: b.dataset.trial });
});
