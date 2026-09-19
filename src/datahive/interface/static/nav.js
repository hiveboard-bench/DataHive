// Section navigation: Home (start screen), Runner (trials, manual or automatic) and
// Annotate (review, annotate, validate and upload).
const VIEW_KEY = "datahive-view";
const VIEW_IDS = { home: "view-home", annotate: "view-annotate", runner: "view-runner" };

function showView(name) {
  if (!VIEW_IDS[name]) name = "home";
  Object.entries(VIEW_IDS).forEach(([key, id]) => { document.getElementById(id).hidden = key !== name; });
  document.body.dataset.view = name;
  document.querySelectorAll(".mode-tab").forEach((b) => b.classList.toggle("active", b.dataset.view === name));
  try { localStorage.setItem(VIEW_KEY, name); } catch (e) { /* ignore */ }
  if (location.hash !== `#/${name}`) history.replaceState(null, "", `#/${name}`);
  window.dispatchEvent(new CustomEvent("viewchange", { detail: { view: name } }));
  // Episodes may have been added by the Runner or a robot script since the list was last drawn.
  if (name === "annotate" && typeof refreshList === "function") { refreshList(); renderListStats(); }
}

document.querySelectorAll(".mode-tab, .home-card").forEach((b) => b.addEventListener("click", () => showView(b.dataset.view)));
document.getElementById("homeLink").addEventListener("click", () => showView("home"));
document.getElementById("homeMore").addEventListener("click", (e) => {
  e.preventDefault();
  document.getElementById("guide").scrollIntoView({ behavior: "smooth", block: "start" });
});
window.addEventListener("hashchange", () => {
  const name = location.hash.replace("#/", "");
  if (VIEW_IDS[name]) showView(name);
  else if (name === "collect") showView("runner");
});

// Opening the interface without a section in the URL shows the start screen.
(function initialView() {
  const name = location.hash.replace("#/", "");
  showView(name === "collect" ? "runner" : VIEW_IDS[name] ? name : "home");
})();
