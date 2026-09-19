// Automatic mode of the Runner: the human-in-the-loop collection pipeline (like
// oopsie-data). The operator sends the current trial to a robot script
// (datahive.collect.CollectClient); the robot records the episode; the episode is
// then annotated in the Annotate section. State lives on the server (/api/collect/*).

const AUTO_RING_C = 2 * Math.PI * 54;
const auto = {
  state: { status: "idle", task: null, episode_id: null, annotated_episode_ids: [] },
  host: null,
  renderedKey: null,
  t0: 0,
  polled: false,
  navigated: null,
};

// The template uses this machine's real samples folder, server address and camera names.
function autoSnippetText() {
  const samples = (rn.defaults && rn.defaults.samples_root) || "samples";
  const cams = (rn.cameras && rn.cameras.length) ? rn.cameras : ["external"];
  const videos = cams.map((c) => `        writer.attach_video("${c}", "${c}.mp4")`).join("\n");
  return `from datahive.collect import CollectClient

robot = CollectClient(
    "${samples}",
    base_url="${location.origin}",
)

while True:
    # Blocks until you press "Send to robot" in the interface.
    task = robot.wait_for_task()

    # An EpisodeWriter for this trial.
    with robot.new_writer(task) as writer:
        # Your control loop, at the control frequency.
        while recording:
            writer.append_proprioception(
                timestamp=t, joint_position=q, joint_velocity=dq,
                joint_torque_or_current=tau, ee_pose=pose, ee_state=gripper,
                provenance={},  # e.g. {"ee_pose": "estimated"}
            )
            writer.append_command(
                timestamp=t, target=action, control_mode="joint_position",
            )

        # One video per camera in the robot profile.
${videos}

    # Opens the episode in Annotate and waits for you to save it.
    robot.finish(writer)`;
}

// Light syntax colouring in one pass (comments, strings, keywords), so nothing is matched twice.
function autoHighlight(code) {
  const token = /(#.*$)|("[^"\n]*")|\b(from|import|while|with|as|True|False)\b/gm;
  let out = "", last = 0, m;
  while ((m = token.exec(code)) !== null) {
    out += escapeHtml(code.slice(last, m.index));
    const cls = m[1] ? "tok-c" : m[2] ? "tok-s" : "tok-k";
    out += `<span class="${cls}">${escapeHtml(m[0])}</span>`;
    last = m.index + m[0].length;
  }
  return out + escapeHtml(code.slice(last));
}

function autoStateKey(s) {
  return [s.status, s.task && `${s.task.session_id}/${s.task.trial_id}`, s.episode_id, s.seq,
    rn.entry && rn.entry.trial_id].join("|");
}

function autoSnippetHtml(open) {
  const samples = (rn.defaults && rn.defaults.samples_root) || "samples";
  return `<details class="rn-card cl-robot auto-connect" ${open ? "open" : ""}>
    <summary><span class="auto-connect-icon">${AUTO_ICON}</span><span class="auto-connect-title"><b>Connect your robot script</b><small>Python &middot; about 10 lines around your control loop</small></span><span class="card-chevron" aria-hidden="true">&#8250;</span></summary>
    <div class="auto-connect-body">
      <ol class="auto-connect-steps">
        <li><b>1</b><div><strong>Use this samples folder</strong><p>The script must write to the folder this interface reads.</p><code class="auto-path">${escapeHtml(samples)}</code></div></li>
        <li><b>2</b><div><strong>Point it at this server</strong><p>The client polls it for the trial you send.</p><code class="auto-path">${escapeHtml(location.origin)}</code></div></li>
        <li><b>3</b><div><strong>Run it, then press Send to robot</strong><p>It records the trial, and you annotate it in Annotate. Attach one video per camera: ${(rn.cameras || []).map(escapeHtml).join(", ") || "the cameras in your robot profile"}.</p></div></li>
      </ol>
      <div class="auto-code">
        <div class="auto-code-bar"><span>robot_script.py</span><button type="button" id="autoCopy" title="Copy the script">Copy</button></div>
        <pre class="auto-code-pre"><code>${autoHighlight(autoSnippetText())}</code></pre>
      </div>
    </div>
  </details>`;
}

function autoRingHtml(cls, inner) {
  return `<div class="rn-ring ${cls}" id="autoRing"><svg viewBox="0 0 120 120" aria-hidden="true"><circle class="bg" cx="60" cy="60" r="54"></circle>
    <circle class="fg" id="autoRingFg" cx="60" cy="60" r="54" stroke-dasharray="${AUTO_RING_C.toFixed(1)}" stroke-dashoffset="${AUTO_RING_C.toFixed(1)}"></circle></svg>
    <div class="rn-ring-inner">${inner}</div></div>`;
}

const AUTO_ICON = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="4" y="8" width="16" height="11" rx="2"></rect><line x1="12" y1="8" x2="12" y2="4"></line><circle cx="12" cy="3.4" r="1"></circle><circle cx="9" cy="13.5" r="1.2" fill="currentColor"></circle><circle cx="15" cy="13.5" r="1.2" fill="currentColor"></circle><line x1="2" y1="12" x2="4" y2="12"></line><line x1="20" y1="12" x2="22" y2="12"></line></svg>`;
const AUTO_FLOW = ["Send", "Robot records", "Annotate"];
const AUTO_STATE = {
  idle: { step: 0, label: "Ready", cls: "" },
  pending: { step: 1, label: "Queued", cls: "warn" },
  running: { step: 1, label: "Recording", cls: "live" },
  annotating: { step: 2, label: "Recorded", cls: "ok" },
};

function autoShellHtml(status, bodyHtml, footHtml, bodyCls = "") {
  const st = AUTO_STATE[status];
  return `
  <section class="auto-panel ${status}">
    <div class="auto-head">
      <span class="auto-icon">${AUTO_ICON}</span>
      <div class="auto-title"><h3>Automatic collection</h3><p>Your robot script records this trial</p></div>
      <span class="auto-chip ${st.cls}">${st.label}</span>
    </div>
    <ol class="auto-flow">${AUTO_FLOW.map((t, i) => `<li class="${i < st.step ? "done" : i === st.step ? "active" : ""}"><b>${i < st.step ? "&#10003;" : i + 1}</b><span>${t}</span></li>`).join("")}</ol>
    <div class="auto-body ${bodyCls}">${bodyHtml}</div>
    <div class="auto-foot">${footHtml}</div>
  </section>`;
}

function autoPanelHtml() {
  const s = auto.state, e = rn.entry, task = rnTask(e.attachment_id);
  if (s.status === "pending") {
    return autoShellHtml("pending", `
      <div class="cl-pulse"><span></span><span></span><i></i></div>
      <h4>Waiting for your robot</h4>
      <p>Trial <b>#${s.task.trial_id}</b> is queued. Run <code>robot.wait_for_task()</code> in your control loop; recording starts as soon as it picks the trial up.</p>`,
      `<button type="button" id="autoAbort">Cancel</button>`, "center");
  }
  if (s.status === "running") {
    return autoShellHtml("running", `
      <div class="auto-rec"><span class="cl-rec"></span>Recording trial #${s.task.trial_id}</div>
      ${autoRingHtml("running", `<div class="rn-ring-time" id="autoStopwatch">00:00.00</div><div class="rn-ring-sub">limit ${s.task.timeout}s</div>`)}`,
      `<button type="button" class="danger" id="autoAbort">Abort trial</button>`, "center");
  }
  if (s.status === "annotating") {
    return autoShellHtml("annotating", `
      <h4>Episode recorded</h4>
      <p><code>${escapeHtml(s.episode_id)}</code> is ready. Annotate it now; saving the annotation releases the robot for the next trial.</p>`,
      `<button type="button" id="autoSkip" title="Release the robot without annotating; annotate later in the Annotate section">Skip for now</button>
       <button type="button" class="rn-cta" id="autoAnnotate">Annotate this episode &rarr;</button>`, "center");
  }
  return autoShellHtml("idle", `
    <div class="auto-trial"><b>#${e.trial_id}</b><span>${escapeHtml(task.name)}</span>${task.timeout ? `<em>${task.timeout} s limit</em>` : ""}</div>
    <label class="auto-field">Instruction for the robot <small>(optional)</small>
      <textarea id="autoInstruction" rows="3" placeholder="Defaults to the task's goal: ${escapeHtml((task.success || "").slice(0, 80))}"></textarea>
    </label>`,
    `<button type="button" class="rn-go auto-send" id="autoSend"><span class="rn-btn-main"><svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><polygon points="6 4 20 12 6 20 6 4"></polygon></svg>Send to robot</span><span class="rn-btn-hint">your script starts recording</span></button>`);
}

function autoRender() {
  if (!auto.host) return;
  auto.host.innerHTML = autoPanelHtml();
  const on = (id, fn) => { const el = document.getElementById(id); if (el) el.onclick = fn; };
  on("autoSend", () => autoPost("/api/collect/submit", {
    session_id: rn.session.session_id, trial_id: rn.entry.trial_id,
    instruction: (document.getElementById("autoInstruction").value.trim() || null),
  }));
  on("autoAbort", () => autoPost("/api/collect/abort"));
  on("autoSkip", () => autoPost("/api/collect/done"));
  on("autoAnnotate", async () => {
    const id = auto.state.episode_id;
    showView("annotate");
    await selectEpisode(id);
  });
  if (auto.state.status === "running") auto.t0 = performance.now() - (auto.state.elapsed_s || 0) * 1000;
}

function autoWireSnippet() {
  const btn = document.getElementById("autoCopy");
  if (btn) btn.onclick = () => navigator.clipboard.writeText(autoSnippetText()).then(
    () => { btn.textContent = "Copied \u2713"; btn.classList.add("done"); window.setTimeout(() => { btn.textContent = "Copy"; btn.classList.remove("done"); }, 1800); },
    () => showToast("Select the code and copy it manually.", { type: "error", title: "Copy failed" }));
}

function autoMount(host) {
  auto.host = host;
  auto.renderedKey = null;
  autoApply(auto.state);
}

function autoUnmount() { auto.host = null; }

async function autoPost(path, body) {
  try {
    const state = await api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
    autoApply(state);
  } catch (err) {
    showToast(err.message, { type: "error", title: "Automatic collection" });
  }
}

function autoApply(state) {
  const prev = auto.state;
  auto.state = state;
  const dot = document.querySelector('.mode-tab[data-view="runner"] .mode-tab-dot');
  if (dot) dot.hidden = state.status === "idle";
  const key = autoStateKey(state);
  if (auto.host && key !== auto.renderedKey) { auto.renderedKey = key; autoRender(); }
  if (prev.status !== state.status) {
    if (state.status === "running") beep(880, 0.16);
    else if (state.status === "annotating") beep(740, 0.18);
  }
  if (auto.polled && prev.status !== "annotating" && state.status === "annotating" && auto.navigated !== state.episode_id) {
    // The episode is recorded: open it in Annotate straight away.
    auto.navigated = state.episode_id;
    showView("annotate");
    selectEpisode(state.episode_id);
  }
  if (state.status === "annotating" && state.annotated_episode_ids.includes(state.episode_id)) {
    // Annotation saved: release the robot (idempotent with the robot script's own call).
    fetch("/api/collect/done", { method: "POST" });
  }
}

async function autoPoll() {
  if (document.hidden) return;
  try {
    const before = auto.state;
    const state = await api("/api/collect/state");
    const finished = before.status === "annotating" && state.status === "idle"
      && before.episode_id && state.annotated_episode_ids.includes(before.episode_id);
    autoApply(state);
    auto.polled = true;
    if (finished) rnAutoTrialDone(before.episode_id);
  } catch (e) { /* server unreachable; keep last state */ }
}

function autoTick() {
  if (auto.state.status !== "running" || !auto.host) return;
  const ms = performance.now() - auto.t0;
  const sw = document.getElementById("autoStopwatch");
  if (sw) sw.textContent = formatStopwatch(ms);
  const timeout = auto.state.task && auto.state.task.timeout;
  const fg = document.getElementById("autoRingFg");
  if (fg && timeout) {
    fg.setAttribute("stroke-dashoffset", (AUTO_RING_C * (1 - Math.min(1, ms / (timeout * 1000)))).toFixed(1));
    const left = timeout - ms / 1000;
    document.getElementById("autoRing").className = `rn-ring ${left <= 5 ? "danger" : left <= 10 ? "warn" : "running"}`;
  }
}

window.setInterval(autoPoll, 1000);
window.setInterval(autoTick, 100);
autoPoll();
