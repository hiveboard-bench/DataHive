// The workflow guide on the Home page: five plain steps that check themselves off from the
// real state of the data, highlight the next one, and jump to the right place.

let guideTimer = null;

async function guideState() {
  const get = (path, fallback) => api(path).catch(() => fallback);
  const [profile, sessions, episodes, status] = await Promise.all([
    get("/api/profile", { problems: ["profile"], exists: false }),
    get("/api/runner/sessions", []),
    get("/api/episodes", []),
    get("/api/status", { configured: false, connected: false }),
  ]);
  const valid = episodes.filter((e) => e.status === "validated" || e.status === "uploaded").length;
  const uploaded = episodes.filter((e) => e.status === "uploaded").length;
  return { profile, sessions, episodes, status, valid, uploaded, total: episodes.length };
}

function guideSteps(s) {
  const problems = (s.profile.problems || []).length;
  const planned = s.sessions.reduce((n, x) => n + (x.n_planned || 0), 0);
  const hubReady = !!(s.status && s.status.configured);
  return [
    {
      title: "Connect your lab",
      text: "One time only. In a terminal, run the command below. It asks for your lab ID and your Hugging Face token, and keeps them private on this computer.",
      code: "datahive init",
      done: hubReady,
      status: hubReady ? "Connected" : "Not connected yet. Run the command, then reload this page",
      cta: "Copy command", go: () => guideCopy("datahive init"),
    },
    {
      title: "Describe your robot",
      text: "Tell DataHive about your robot, gripper and cameras once. Every episode keeps a copy.",
      done: s.profile.exists !== false && problems === 0,
      status: s.profile.exists === false ? "Not started" : problems === 0 ? "Complete" : `${problems} thing${problems === 1 ? "" : "s"} left to fill in`,
      cta: "Open Robot Profile", go: () => openProfileOverlay(),
    },
    {
      title: "Plan your trials",
      text: "A session is one testing day. DataHive makes the plan for you: 5 trials of every task.",
      done: s.sessions.length > 0,
      status: s.sessions.length ? `${s.sessions.length} session${s.sessions.length === 1 ? "" : "s"} · ${planned} trials planned` : "No session yet",
      cta: "Open Runner", go: () => showView("runner"),
    },
    {
      title: "Run the trials",
      text: "Time each trial in the browser and upload its recording, or let your robot record it for you.",
      done: s.total > 0,
      status: s.total ? `${s.total} episode${s.total === 1 ? "" : "s"} recorded` : "Nothing recorded yet",
      cta: "Open Runner", go: () => showView("runner"),
    },
    {
      title: "Annotate and check",
      text: "Say what happened in each trial: the outcome, and a note if you like. DataHive checks the data for you.",
      done: s.total > 0 && s.valid === s.total,
      status: s.total ? `${s.valid} of ${s.total} episodes validated` : "Waiting for your first episode",
      cta: "Open Annotate", go: () => showView("annotate"),
    },
    {
      title: "Send it to HiveBoard",
      text: "Upload the checked episodes to your lab's Hugging Face dataset.",
      done: s.total > 0 && s.uploaded === s.total,
      status: !hubReady ? "Connect your lab first (step 1)" : s.total ? `${s.uploaded} of ${s.total} uploaded` : "Nothing to upload yet",
      cta: "Open Annotate", go: () => showView("annotate"),
    },
  ];
}

async function guideCopy(text) {
  try { await navigator.clipboard.writeText(text); showToast("Copied. Paste it in a terminal.", { type: "success" }); }
  catch (e) { showToast("Select the command and copy it."); }
}

function guideRender(s) {
  const steps = guideSteps(s);
  const nextIdx = steps.findIndex((st) => !st.done);
  const list = document.getElementById("guideSteps");
  list.innerHTML = steps.map((st, i) => {
    const cls = st.done ? "done" : i === nextIdx ? "next" : "todo";
    return `<li class="guide-step ${cls}">
      <span class="guide-num">${st.done ? "&#10003;" : i + 1}</span>
      <div class="guide-main">
        <h4>${st.title}${i === nextIdx ? '<span class="guide-tag">Next step</span>' : ""}</h4>
        <p>${st.text}</p>${st.code ? `<code class="guide-code">${st.code}</code>` : ""}
        <span class="guide-status">${st.status}</span>
      </div>
      <button type="button" class="guide-cta${i === nextIdx ? " primary-cta" : ""}" data-step="${i}">${st.cta}</button>
    </li>`;
  }).join("");
  list.querySelectorAll(".guide-cta").forEach((b) => (b.onclick = () => steps[Number(b.dataset.step)].go()));

  const next = document.getElementById("homeNext");
  if (nextIdx === -1) {
    next.hidden = false;
    next.textContent = "All steps done. Nice work!";
    next.onclick = null;
    next.className = "home-next done";
  } else {
    next.hidden = false;
    next.className = "home-next";
    next.innerHTML = `<span>Next</span>${steps[nextIdx].title} &rarr;`;
    next.onclick = steps[nextIdx].go;
  }
}

async function guideRefresh() {
  try { guideRender(await guideState()); } catch (e) { /* the guide is optional: never block the page */ }
}

window.addEventListener("viewchange", (e) => {
  if (guideTimer) { window.clearInterval(guideTimer); guideTimer = null; }
  if (e.detail.view !== "home") return;
  guideRefresh();
  guideTimer = window.setInterval(() => { if (!document.hidden) guideRefresh(); }, 8000);
});
