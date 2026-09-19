from __future__ import annotations

import threading
import time

import pytest
from fastapi.testclient import TestClient

from datahive import runner
from datahive.collect import CollectClient, CollectError, CollectRuntime
from datahive.errors import AnnotationError, DatahiveError
from datahive.interface.app import create_app
from datahive.paths import trials_csv_path
from datahive.trials import read_rows

from conftest import make_episode

pytest.importorskip("cv2")


def _session(samples_root, **kw):
    args = dict(lab_id="lab_test", platform_id="rig-01", operator_name="Op", attachment_ids=["valve_ball", "lock"],
                per_task=2, randomize=False)
    args.update(kw)
    return runner.create_session(samples_root, **args)


def test_plan_is_generated_per_task_and_reproducibly_shuffled():
    plan = runner.generate_plan(["a", "b"], 3, randomize=False)
    assert [p["attachment_id"] for p in plan] == ["a"] * 3 + ["b"] * 3
    assert [p["trial_id"] for p in plan] == ["1", "2", "3", "4", "5", "6"]
    one = runner.generate_plan(["a", "b"], 3, seed=7)
    assert one == runner.generate_plan(["a", "b"], 3, seed=7)
    assert sorted(p["attachment_id"] for p in one) == ["a"] * 3 + ["b"] * 3


def test_default_plan_covers_all_13_tasks_five_times(samples_root):
    s = runner.create_session(samples_root, lab_id="l", platform_id="p", operator_name="Op")
    assert len(s["plan"]) == 65


def test_session_ids_are_unique_and_validated(samples_root):
    a, b = _session(samples_root), _session(samples_root)
    assert a["session_id"] != b["session_id"] and b["session_id"].endswith("_2")
    with pytest.raises(DatahiveError, match="operator_name"):
        _session(samples_root, operator_name=" ")
    with pytest.raises(DatahiveError, match="Unknown attachment"):
        _session(samples_root, attachment_ids=["nope"])
    with pytest.raises(DatahiveError, match="date"):
        _session(samples_root, date="01/02/2026")


def test_record_trial_success_and_progress(samples_root):
    s = _session(samples_root)
    sid = s["session_id"]
    row = runner.record_trial(samples_root, sid, None, {"outcome": "success", "completion_time_s": 12.3, "strategy": "prehensile"})
    assert row["trial_id"] == "1" and row["attachment_id"] == "valve_ball" and row["lab_id"] == "lab_test"
    assert row["operator_name"] == "Op" and row["schema_version"] and row["annotated_at"]
    p = runner.session_progress(samples_root, sid)
    assert p["n_recorded"] == 1 and p["next_trial"]["trial_id"] == "2" and p["per_task"]["valve_ball"] == {"planned": 2, "recorded": 1}


def test_record_trial_rules(samples_root):
    sid = _session(samples_root)["session_id"]
    ok = {"strategy": "prehensile"}
    with pytest.raises(AnnotationError, match="exceeds the 60s"):
        runner.record_trial(samples_root, sid, "1", {**ok, "outcome": "success", "completion_time_s": 61})
    with pytest.raises(AnnotationError, match="failure_cause"):
        runner.record_trial(samples_root, sid, "1", {**ok, "outcome": "fail"})
    with pytest.raises(AnnotationError, match="not part of"):
        runner.record_trial(samples_root, sid, "99", {**ok, "outcome": "success", "completion_time_s": 5})
    # composed assembly (lock, 3 stages): trial 3
    with pytest.raises(AnnotationError, match="stage_reached is required"):
        runner.record_trial(samples_root, sid, "3", {**ok, "outcome": "fail", "failure_cause": "slip"})
    with pytest.raises(AnnotationError, match="every stage"):
        runner.record_trial(samples_root, sid, "3", {**ok, "outcome": "success", "completion_time_s": 20, "stage_reached": 2})
    with pytest.raises(AnnotationError, match="cannot exceed"):
        runner.record_trial(samples_root, sid, "3", {**ok, "outcome": "fail", "failure_cause": "slip", "stage_reached": 4})
    runner.record_trial(samples_root, sid, "3", {**ok, "outcome": "success", "completion_time_s": 20, "stage_reached": 3})


def test_remove_trial_and_complete(samples_root):
    sid = _session(samples_root, attachment_ids=["valve_ball"], per_task=1)["session_id"]
    runner.record_trial(samples_root, sid, None, {"outcome": "success", "completion_time_s": 5, "strategy": "prehensile"})
    assert runner.session_progress(samples_root, sid)["complete"]
    assert runner.remove_trial(samples_root, sid, "1")
    assert not runner.session_progress(samples_root, sid)["complete"]
    assert read_rows(trials_csv_path(samples_root, sid)) == []


# ---- state machine -------------------------------------------------------------

def test_collect_state_machine_transitions():
    rt = CollectRuntime()
    assert rt.snapshot()["status"] == "idle"
    with pytest.raises(CollectError):
        rt.start()
    rt.submit({"trial_id": "1"})
    with pytest.raises(CollectError, match="pending"):
        rt.submit({"trial_id": "2"})
    rt.start()
    assert rt.snapshot()["status"] == "running" and rt.snapshot()["elapsed_s"] is not None
    rt.done()  # stale call while running must not disturb it
    assert rt.snapshot()["status"] == "running"
    rt.annotating("ep1")
    assert rt.mark_annotated("other") is False and rt.mark_annotated("ep1") is True
    rt.done()
    s = rt.snapshot()
    assert s["status"] == "idle" and "ep1" in s["annotated_episode_ids"]
    rt.submit({"trial_id": "2"})
    rt.abort()
    assert rt.snapshot()["status"] == "idle" and rt.snapshot()["aborted"] is True


# ---- API + robot client --------------------------------------------------------

@pytest.fixture
def client(samples_root):
    return TestClient(create_app(samples_root))


def _requester(client):
    def request(method, path, body):
        resp = client.request(method, path, json=body) if body is not None else client.request(method, path)
        assert resp.status_code < 400, resp.text
        return resp.json()
    return request


def test_runner_api_flow(client, samples_root):
    d = client.get("/api/runner/defaults").json()
    assert d["required_trials"] == 5 and d["date"]
    r = client.post("/api/runner/sessions", json={"operator_name": "Op", "lab_id": "lab_test", "platform_id": "rig-01",
                                                  "attachment_ids": ["valve_ball"], "per_task": 1})
    assert r.status_code == 200, r.text
    sid = r.json()["session_id"]
    bad = client.post(f"/api/runner/sessions/{sid}/trials", json={"outcome": "success", "completion_time_s": 99, "strategy": "prehensile"})
    assert bad.status_code == 422
    ok = client.post(f"/api/runner/sessions/{sid}/trials", json={"outcome": "success", "completion_time_s": 9.5, "strategy": "prehensile"})
    assert ok.status_code == 200 and ok.json()["session"]["complete"]
    assert client.get(f"/api/runner/sessions/{sid}/trials.csv").status_code == 409     # no valid episode yet
    assert client.get("/api/runner/sessions").json()[0]["session_id"] == sid
    assert client.get("/api/runner/sessions/nope").status_code == 404
    assert client.delete(f"/api/runner/sessions/{sid}/trials/1").json()["ok"] is True


def test_collect_end_to_end_with_robot_client(client, samples_root, filled_profile):
    sid = client.post("/api/runner/sessions", json={"operator_name": "Op", "lab_id": "lab_test", "platform_id": "rig-01",
                                                    "attachment_ids": ["valve_ball"], "per_task": 2}).json()["session_id"]
    assert client.post("/api/collect/submit", json={"session_id": "nope"}).status_code == 404
    assert client.post("/api/collect/submit", json={"session_id": sid}).status_code == 200
    assert client.post("/api/collect/submit", json={"session_id": sid}).status_code == 409

    robot = CollectClient(samples_root, request=_requester(client), poll_s=0.01)
    task = robot.wait_for_task(timeout_s=2)
    assert task["trial_id"] == "1" and task["attachment_id"] == "valve_ball" and task["timeout"] == 60
    assert client.get("/api/collect/state").json()["status"] == "running"

    # The robot records an episode for that trial, then hands it over.
    make_episode(samples_root, task["session_id"], "ep_c1", trial_id=task["trial_id"], profile=filled_profile)
    result = {}
    class W:  # writer stand-in: episode already written by make_episode
        episode_id = "ep_c1"
        def close(self): pass
    t = threading.Thread(target=lambda: result.update(ok=robot.finish(W(), timeout_s=5)))
    t.start()
    for _ in range(200):
        if client.get("/api/collect/state").json()["status"] == "annotating":
            break
        time.sleep(0.01)
    assert client.get("/api/collect/state").json()["episode_id"] == "ep_c1"
    resp = client.post("/api/episodes/ep_c1/annotate", json={
        "attachment_id": "valve_ball", "operator_name": "Op", "annotator_name": "Ann", "outcome": "success",
        "strategy": "prehensile"})
    assert resp.status_code == 200, resp.text
    t.join(5)
    assert result["ok"] is True
    assert client.get("/api/collect/state").json()["status"] == "idle"
    # The annotation saved for the episode is the row for trial 1, so the plan moves on to trial 2.
    progress = client.get(f"/api/runner/sessions/{sid}").json()
    assert progress["n_recorded"] == 1 and progress["next_trial"]["trial_id"] == "2"


def test_abort_releases_the_waiting_robot(client, samples_root):
    sid = client.post("/api/runner/sessions", json={"operator_name": "Op", "lab_id": "l", "platform_id": "p",
                                                    "attachment_ids": ["valve_ball"], "per_task": 1}).json()["session_id"]
    client.post("/api/collect/submit", json={"session_id": sid})
    robot = CollectClient(samples_root, request=_requester(client), poll_s=0.01)
    robot.wait_for_task(timeout_s=2)
    class W:
        episode_id = "epx"
        def close(self): pass
    out = {}
    t = threading.Thread(target=lambda: out.update(ok=robot.finish(W(), timeout_s=5)))
    t.start()
    time.sleep(0.1)
    client.post("/api/collect/abort")
    t.join(5)
    assert out["ok"] is False


def test_new_writer_uses_the_task(samples_root, filled_profile):
    robot = CollectClient(samples_root, request=lambda *a: {}, poll_s=0.01)
    task = {"session_id": "s1", "trial_id": "4", "attachment_id": "lock", "lab_id": "lab_test"}
    with robot.new_writer(task, profile=filled_profile) as w:
        assert w.header.trial_id == "4" and w.header.task_ids == ["lock"] and w.header.session_id == "s1"


def test_plan_cards_follow_registry_order_not_shuffle_order(samples_root):
    from datahive.attachments import load_registry
    s = runner.create_session(samples_root, lab_id="l", platform_id="p", operator_name="Op", seed=3)
    order = list(runner.session_progress(samples_root, s["session_id"])["per_task"])
    assert order == list(load_registry(samples_root))


def test_runner_and_annotate_sections_are_wired_in_the_page(client):
    html = client.get("/").text
    for view in ("runner", "annotate"):
        assert f'data-view="{view}"' in html
    assert 'data-view="collect"' not in html and 'id="view-collect"' not in html
    assert 'id="view-home"' in html and html.count('class="home-card"') == 2
    for ident in ('id="view-runner"', 'id="view-annotate"'):
        assert ident in html
    for script in ("runner.js", "automatic.js", "nav.js"):
        assert client.get(f"/{script}").status_code == 200
    assert client.get("/collect.js").status_code == 404
    runner_js = client.get("/runner.js").text
    for marker in ("AudioContext", "Start countdown", "rnFinishTiming", "event.code !== \"Space\"", "rnUploadEpisode", "autoMount("):
        assert marker in runner_js



def test_runner_reuses_the_annotation_task_picker(client):
    app_js = client.get("/app.js").text
    runner_js = client.get("/runner.js").text
    assert "function openTaskPickerMulti" in app_js and "function taskGroupsHtml" in app_js
    for marker in ("openTaskPickerMulti(rn.tasks", "taskGroupsHtml(inSession", "taskChipHtml(", 'id="rnPickTasks"'):
        assert marker in runner_js
    assert 'type="checkbox" name="task"' not in runner_js


def test_opening_without_a_section_shows_the_start_screen(client):
    nav = client.get("/nav.js").text
    assert 'VIEW_IDS[name] ? name : "home"' in nav
    assert "localStorage.getItem(VIEW_KEY)" not in nav


def test_runner_designs_are_in_place(client):
    auto_js = client.get("/automatic.js").text
    runner_js = client.get("/runner.js").text
    for marker in ("autoPanelHtml", "autoShellHtml", "autoAnnotate", "AUTO_RING_C", "Connect your robot script"):
        assert marker in auto_js
    for marker in ("rnStepsHtml", "rnRingHtml", "rnEstimateMinutes", "Plan summary", "rnUploadHtml", "rnModeToggleHtml"):
        assert marker in runner_js
    css = client.get("/style.css").text
    tail = css[css.index("/* ---- Section navigation ---- */"):]
    assert ".view .eyebrow" in tail
    for line in tail.splitlines():
        assert not line.startswith((".stat-row", ".eyebrow")), f"generic override leaks into Annotate: {line}"
    assert 'body:not([data-view="annotate"]) #statsBtn { display: none; }' in css


def test_automatic_mode_sends_you_to_the_annotate_page_not_an_embedded_form(client):
    auto_js = client.get("/automatic.js").text
    assert 'showView("annotate")' in auto_js and "selectEpisode(id)" in auto_js
    assert "collectAnnotateHost" not in auto_js and "appendChild(detailEl)" not in auto_js


def test_home_has_purpose_data_scope_and_faq(client):
    html = client.get("/").text
    assert "helps labs collect data for HiveBoard" in html
    for heading in (">Purpose<", ">We collect<", ">Not collected yet<", ">Questions<"):
        assert heading in html
    for item in ("Depth images and point clouds", "Odometry for actuated bases"):
        assert item in html
    assert html.count("<details>") >= 6
    css = client.get("/style.css").text
    assert ".faq summary::after" in css
    nav = client.get("/nav.js").text
    assert "homeMore" in nav and 'name === "collect" ? "runner"' in nav



def test_annotate_layout_can_shrink_without_horizontal_scroll(client):
    css = client.get("/style.css").text
    assert "minmax(0, 3fr) minmax(0, 2fr)" in css
    assert ".episode-detail { overflow-x: hidden; }" in css


# ---- manual mode: upload the HDF5 and one video per profile camera ----------------------

def _manual_setup(client, samples_root, tmp_path, filled_profile, *, cameras_video=True):
    """A session with trial 1 recorded, plus an HDF5 + video an operator would upload."""
    sid = client.post("/api/runner/sessions", json={"operator_name": "Op", "lab_id": "lab_test", "platform_id": "rig-01",
                      "attachment_ids": ["valve_ball"], "per_task": 1, "mode": "manual"}).json()["session_id"]
    ok = client.post(f"/api/runner/sessions/{sid}/trials", json={"outcome": "success", "completion_time_s": 1.5, "strategy": "prehensile"})
    assert ok.status_code == 200, ok.text
    src_root = tmp_path / "operator_files"
    src_root.mkdir()
    h5 = make_episode(src_root, "x", "src_ep", profile=filled_profile)
    video = next(h5.parent.glob("*.mp4")).read_bytes()
    return sid, h5.read_bytes(), video


def test_session_mode_is_stored_and_switchable(client):
    sid = client.post("/api/runner/sessions", json={"operator_name": "Op", "lab_id": "l", "platform_id": "p",
                      "attachment_ids": ["valve_ball"], "per_task": 1, "mode": "automatic"}).json()["session_id"]
    assert client.get(f"/api/runner/sessions/{sid}").json()["mode"] == "automatic"
    assert client.patch(f"/api/runner/sessions/{sid}", json={"mode": "manual"}).json()["mode"] == "manual"
    assert client.patch(f"/api/runner/sessions/{sid}", json={"mode": "nope"}).status_code == 422


def test_manual_upload_makes_a_valid_episode(client, samples_root, tmp_path, filled_profile):
    sid, h5, video = _manual_setup(client, samples_root, tmp_path, filled_profile)
    r = client.post(f"/api/runner/sessions/{sid}/trials/1/episode",
                    files={"h5": ("e.h5", h5), "video:external": ("v.mp4", video)})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["valid"] is True and body["problems"] == [] and body["cameras"] == ["external"]
    progress = client.get(f"/api/runner/sessions/{sid}").json()
    ep = progress["plan"][0]["episode"]
    assert ep["episode_id"] == body["episode_id"] and ep["valid"] is True
    assert progress["n_episodes_valid"] == 1
    from datahive.episode import read_header
    from datahive.paths import resolve_episode_paths
    header = read_header(resolve_episode_paths(samples_root, body["episode_id"]).h5)
    assert header.collection_mode == "manual" and header.trial_id == "1" and header.session_id == sid
    assert header.cameras[0]["file"].endswith("_cam_external.mp4")


def test_manual_upload_requires_a_video_for_every_profile_camera(client, samples_root, tmp_path, filled_profile):
    sid, h5, video = _manual_setup(client, samples_root, tmp_path, filled_profile)
    r = client.post(f"/api/runner/sessions/{sid}/trials/1/episode", files={"h5": ("e.h5", h5)})
    assert r.status_code == 422 and "Missing: ['external']" in r.json()["detail"]
    r = client.post(f"/api/runner/sessions/{sid}/trials/1/episode",
                    files={"h5": ("e.h5", h5), "video:external": ("v.mp4", video), "video:wrist": ("w.mp4", video)})
    assert r.status_code == 422 and "not in the robot profile" in r.json()["detail"]
    r = client.post(f"/api/runner/sessions/{sid}/trials/1/episode", files={"video:external": ("v.mp4", video)})
    assert r.status_code == 422 and "HDF5" in r.json()["detail"]
    assert client.get(f"/api/runner/sessions/{sid}").json()["plan"][0]["episode"] is None


def test_manual_upload_rejects_bad_files(client, samples_root, tmp_path, filled_profile):
    sid, h5, video = _manual_setup(client, samples_root, tmp_path, filled_profile)
    bad = client.post(f"/api/runner/sessions/{sid}/trials/1/episode",
                      files={"h5": ("e.h5", b"not hdf5"), "video:external": ("v.mp4", video)})
    assert bad.status_code == 422 and "HDF5" in bad.json()["detail"]


def test_upload_does_not_need_the_trial_to_be_annotated_first(client, samples_root, tmp_path, filled_profile):
    _, h5, video = _manual_setup(client, samples_root, tmp_path, filled_profile)
    sid2 = client.post("/api/runner/sessions", json={"operator_name": "Op", "lab_id": "l", "platform_id": "p",
                       "attachment_ids": ["valve_ball"], "per_task": 1}).json()["session_id"]
    r = client.post(f"/api/runner/sessions/{sid2}/trials/1/episode",
                    files={"h5": ("e.h5", h5), "video:external": ("v.mp4", video)})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["valid"] is True and body["needs_annotation"] is True and body["problems"] == []
    detail = client.get(f"/api/episodes/{body['episode_id']}").json()
    assert detail["annotation"] is None
    assert detail["annotation_defaults"]["attachment_id"] == "valve_ball"
    assert detail["annotation_defaults"]["operator_name"] == "Op"
    # Annotating it (as Annotate does) records the trial in the session.
    ok = client.post(f"/api/episodes/{body['episode_id']}/annotate", json={
        "attachment_id": "valve_ball", "operator_name": "Op", "annotator_name": "Ann", "outcome": "success", "strategy": "prehensile"})
    assert ok.status_code == 200, ok.text
    progress = client.get(f"/api/runner/sessions/{sid2}").json()
    assert progress["n_recorded"] == 1 and progress["plan"][0]["episode"]["episode_id"] == body["episode_id"]
    assert client.post(f"/api/episodes/{body['episode_id']}/validate").json()["ok"] is True
    assert client.get(f"/api/runner/sessions/{sid2}").json()["plan"][0]["episode"]["valid"] is True


def test_manual_upload_with_a_broken_video_is_not_valid(client, samples_root, tmp_path, filled_profile):
    sid, h5, good = _manual_setup(client, samples_root, tmp_path, filled_profile)
    r = client.post(f"/api/runner/sessions/{sid}/trials/1/episode",
                    files={"h5": ("e.h5", h5), "video:external": ("v.mp4", b"\x00" * 64)})
    assert r.status_code == 200
    body = r.json()
    assert body["valid"] is False and any("video" in p.lower() for p in body["problems"])
    assert client.get(f"/api/runner/sessions/{sid}").json()["plan"][0]["episode"]["valid"] is False
    # Re-uploading a good pair replaces it and the episode becomes valid.
    again = client.post(f"/api/runner/sessions/{sid}/trials/1/episode",
                        files={"h5": ("e.h5", h5), "video:external": ("v.mp4", good)}).json()
    assert again["valid"] is True
    assert client.get(f"/api/runner/sessions/{sid}").json()["plan"][0]["episode"]["valid"] is True


def test_automatic_writer_marks_the_episode_as_automatic(samples_root, filled_profile):
    robot = CollectClient(samples_root, request=lambda *a: {}, poll_s=0.01)
    task = {"session_id": "s1", "trial_id": "4", "attachment_id": "lock", "lab_id": "lab_test"}
    with robot.new_writer(task, profile=filled_profile) as w:
        assert w.header.collection_mode == "automatic"


def test_session_takes_lab_from_config_and_platform_from_profile(client, samples_root, filled_profile, config):
    from datahive.config import save_config
    save_config(config)
    d = client.get("/api/runner/defaults").json()
    assert d["lab_id"] == "lab_test" and d["platform_id"] == "rig-01"
    r = client.post("/api/runner/sessions", json={"operator_name": "Op", "attachment_ids": ["valve_ball"], "per_task": 1})
    assert r.status_code == 200, r.text
    assert r.json()["lab_id"] == "lab_test" and r.json()["platform_id"] == "rig-01"


def test_session_without_lab_or_platform_explains_what_is_missing(client):
    r = client.post("/api/runner/sessions", json={"operator_name": "Op", "attachment_ids": ["valve_ball"], "per_task": 1})
    assert r.status_code == 422 and "lab_id" in r.json()["detail"]


def test_setup_form_only_asks_for_operator_and_date(client):
    js = client.get("/runner.js").text
    setup = js[js.index("function rnNewSessionModalHtml()"):js.index("function rnSelectedTasks()")]
    assert 'name="lab_id"' not in setup and 'name="platform_id"' not in setup
    assert 'name="operator_name"' in setup and 'name="date"' in setup
    css = client.get("/style.css").text
    assert ".view .field-grid .full { grid-column: 1 / -1; }" in css


def test_runner_starts_with_setup_and_review_only(client):
    js = client.get("/runner.js").text
    steps = js[js.index("function rnStepsHtml("):js.index("function rnEstimateMinutes")]
    assert 'const order = ["setup", "review"]' in steps
    assert "data-back" in steps and "&larr; Sessions" in steps and '"task"' not in steps
    # The session form lives in a dialog opened by a button, with a light grey panel.
    assert 'id="rnNewBtn"' in js and "Create a new session" in js and "function rnOpenNewSession" in js
    setup = js[js.index("function rnSetupHtml()"):js.index("function rnNewSessionModalHtml()")]
    assert 'id="rnSetupForm"' not in setup
    css = client.get("/style.css").text
    assert ".rn-modal-panel" in css and "background: var(--soft)" in css and "--soft: #f3f4f6" in css
    # No session is restored automatically: the initial view is the sessions list.
    assert "RN_SESSION_KEY) ||" not in js and "localStorage.getItem(RN_SESSION_KEY)" not in js


# ---- editing the plan ----------------------------------------------------------------

def _record(samples_root, sid, trial_id, **kw):
    fields = {"outcome": "success", "completion_time_s": 5, "strategy": "prehensile", **kw}
    return runner.record_trial(samples_root, sid, trial_id, fields)


def test_edit_plan_keeps_recorded_trials_and_replans_the_rest(samples_root):
    s = _session(samples_root, attachment_ids=["valve_ball", "button"], per_task=2, randomize=False)
    sid = s["session_id"]
    _record(samples_root, sid, "1")                                   # valve_ball recorded
    _record(samples_root, sid, "3", stage_reached=2)                  # button recorded (composed: needs stage)
    out = runner.edit_plan(samples_root, sid, attachment_ids=["valve_ball", "button", "lock"], per_task=3, randomize=False)
    plan = out["plan"]
    assert [(e["trial_id"], e["attachment_id"]) for e in plan[:2]] == [("1", "valve_ball"), ("3", "button")]
    counts = {a: sum(1 for e in plan if e["attachment_id"] == a) for a in ("valve_ball", "button", "lock")}
    assert counts == {"valve_ball": 3, "button": 3, "lock": 3}
    assert [e["trial_id"] for e in plan[2:]] == [str(i) for i in range(4, 4 + 7)]      # renumbered after the last recorded id
    progress = runner.session_progress(samples_root, sid)
    assert progress["n_recorded"] == 2 and progress["n_planned"] == 9 and progress["per_task"]["lock"]["planned"] == 3


def test_edit_plan_rules(samples_root):
    sid = _session(samples_root, attachment_ids=["valve_ball", "button"], per_task=2, randomize=False)["session_id"]
    _record(samples_root, sid, "1")
    with pytest.raises(DatahiveError, match="cannot be removed"):
        runner.edit_plan(samples_root, sid, attachment_ids=["button"])
    with pytest.raises(DatahiveError, match="at least one"):
        runner.edit_plan(samples_root, sid, attachment_ids=[])
    with pytest.raises(DatahiveError, match="Unknown"):
        runner.edit_plan(samples_root, sid, attachment_ids=["valve_ball", "nope"])
    with pytest.raises(DatahiveError, match="per_task"):
        runner.edit_plan(samples_root, sid, per_task=0)
    # Lowering the target below what is recorded never drops recorded trials.
    out = runner.edit_plan(samples_root, sid, per_task=1, randomize=False)
    assert sum(1 for e in out["plan"] if e["attachment_id"] == "valve_ball") == 1
    # A task without recorded trials can be removed.
    out = runner.edit_plan(samples_root, sid, attachment_ids=["valve_ball"])
    assert {e["attachment_id"] for e in out["plan"]} == {"valve_ball"}
    assert runner.edit_plan(samples_root, sid, operator_name="New Op")["operator_name"] == "New Op"


def test_edit_plan_api(client, samples_root):
    sid = client.post("/api/runner/sessions", json={"operator_name": "Op", "lab_id": "l", "platform_id": "p",
                      "attachment_ids": ["valve_ball"], "per_task": 1, "randomize": False}).json()["session_id"]
    r = client.put(f"/api/runner/sessions/{sid}/plan", json={"attachment_ids": ["valve_ball", "lock"], "per_task": 2, "randomize": False})
    assert r.status_code == 200 and r.json()["n_planned"] == 4
    assert client.put(f"/api/runner/sessions/{sid}/plan", json={"per_task": 99}).status_code == 422
    assert client.put("/api/runner/sessions/nope/plan", json={}).status_code == 404


def test_plan_page_has_an_edit_plan_dialog(client):
    js = client.get("/runner.js").text
    for marker in ('id="rnEditPlan"', "function rnOpenEditPlan", "function rnEditPlanModalHtml", "/plan`", "Tasks with recorded trials stay in the plan."):
        assert marker in js


def test_delete_session(client, samples_root, filled_profile, tmp_path):
    sid = client.post("/api/runner/sessions", json={"operator_name": "Op", "lab_id": "l", "platform_id": "p",
                      "attachment_ids": ["valve_ball"], "per_task": 1}).json()["session_id"]
    client.post(f"/api/runner/sessions/{sid}/trials", json={"outcome": "success", "completion_time_s": 1.5, "strategy": "prehensile"})
    assert client.delete(f"/api/runner/sessions/{sid}").json() == {"ok": True}
    assert client.get(f"/api/runner/sessions/{sid}").status_code == 404
    assert not (samples_root / sid).exists()
    assert client.delete("/api/runner/sessions/nope").status_code == 404


def test_delete_session_refuses_when_episodes_exist(client, samples_root, filled_profile):
    sid = client.post("/api/runner/sessions", json={"operator_name": "Op", "lab_id": "l", "platform_id": "p",
                      "attachment_ids": ["valve_ball"], "per_task": 1}).json()["session_id"]
    make_episode(samples_root, sid, "ep_keep", trial_id="1", profile=filled_profile)
    r = client.delete(f"/api/runner/sessions/{sid}")
    assert r.status_code == 409 and "1 episode" in r.json()["detail"]
    assert client.get(f"/api/runner/sessions/{sid}").status_code == 200


def test_session_rows_offer_edit_and_delete_not_review(client):
    js = client.get("/runner.js").text
    row = js[js.index("function rnSessionRowHtml"):js.index("function rnSetupHtml()")]
    assert "data-edit" in row and "data-delete" in row and "data-review" not in row


def test_edit_and_delete_controls_are_icon_buttons(client):
    js = client.get("/runner.js").text
    assert 'rnIconBtn(\'id="rnEditPlan"\'' in js
    assert 'RN_ICON_TRASH, "Delete session"' in js and 'RN_ICON_TRASH, "Remove trial"' in js
    assert ">Edit plan</button>" not in js and ">Remove</button>" not in js and ">Delete</button>" not in js


def test_progress_exposes_the_trials_per_task_number(samples_root):
    s = _session(samples_root, per_task=4, attachment_ids=["valve_ball"])
    p = runner.session_progress(samples_root, s["session_id"])
    assert p["trials_per_task"] == 4 and isinstance(p["per_task"], dict)
    js = open("src/datahive/interface/static/runner.js").read()
    assert "s.trials_per_task" in js and "value=\"${s.per_task ||" not in js


def test_robot_script_box_is_a_full_width_block_below_the_trial_layout(client):
    runner_js = client.get("/runner.js").text
    auto_js = client.get("/automatic.js").text
    body = runner_js[runner_js.index("function rnAutomaticTrialHtml"):runner_js.index("function rnUploadItems")]
    assert body.index('id="rnAutoHost"') < body.index("${autoSnippetHtml(false)}")
    panel = auto_js[auto_js.index("function autoPanelHtml"):auto_js.index("function autoRender")]
    assert "autoSnippetHtml(" not in panel


def test_sessions_and_review_pages_are_centered(client):
    assert 'toggle("rn-centered", !rn.session' in client.get("/runner.js").text
    css = client.get("/style.css").text
    assert "main#view-runner.rn-centered { display: flex; }" in css and "margin: auto" in css
    assert "padding-bottom: 24vh" in css


def test_automatic_panel_stretches_to_the_task_card_height(client):
    css = client.get("/style.css").text
    assert "#rnAutoHost { display: flex; flex-direction: column; }" in css
    assert "#rnAutoHost > .timer-panel { flex: 1; }" in css


def test_back_to_plan_is_the_last_thing_on_the_trial_page(client):
    js = client.get("/runner.js").text
    for fn, end in (("function rnAutomaticTrialHtml", "function rnUploadItems"), ("function rnTrialHtml()", "function rnStartCountdown")):
        body = js[js.index(fn):js.index(end)]
        assert body.count('id="rnBack"') == 1 and 'class="rn-bottombar"' in body
        assert body.index('id="rnBack"') > body.index("trial-layout")
        assert "rn-timer-foot" not in body


def test_back_to_plan_has_no_border(client):
    css = client.get("/style.css").text
    assert ".rn-bottombar button { border: none; background: transparent;" in css


def test_opening_annotate_refreshes_the_episode_list(client):
    nav = client.get("/nav.js").text
    assert 'name === "annotate" && typeof refreshList === "function"' in nav


# ---- partial uploads (Annotate): any subset of files, episode flagged incomplete ---------

def _plain_session(client):
    return client.post("/api/runner/sessions", json={"operator_name": "Op", "lab_id": "l", "platform_id": "p",
                       "attachment_ids": ["valve_ball"], "per_task": 1}).json()["session_id"]


def test_partial_upload_of_only_a_video_creates_an_incomplete_episode(client, samples_root, tmp_path, filled_profile):
    _, h5, video = _manual_setup(client, samples_root, tmp_path, filled_profile)
    sid = _plain_session(client)
    url = f"/api/runner/sessions/{sid}/trials/1/episode?partial=true"
    r = client.post(url, files={"video:external": ("v.mp4", video)})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["valid"] is False and body["incomplete"] is True and body["missing"] == ["HDF5 recording data"]
    listed = {e["episode_id"]: e for e in client.get("/api/episodes").json()}
    assert listed[body["episode_id"]]["missing"] == ["HDF5 recording data"]
    detail = client.get(f"/api/episodes/{body['episode_id']}").json()
    assert detail["missing"] == ["HDF5 recording data"] and detail["cameras"] == ["external"]
    v = client.post(f"/api/episodes/{body['episode_id']}/validate").json()
    assert v["ok"] is False and "incomplete" in v["error"]
    # The missing HDF5 arrives later and is merged with the video that is already there.
    r2 = client.post(url, files={"h5": ("e.h5", h5)})
    assert r2.status_code == 200, r2.text
    done = r2.json()
    assert done["incomplete"] is False and done["missing"] == [] and done["valid"] is True
    assert client.get(f"/api/episodes/{done['episode_id']}").json()["missing"] == []


def test_partial_upload_of_only_the_hdf5_is_incomplete_until_videos_are_added(client, samples_root, tmp_path, filled_profile):
    _, h5, video = _manual_setup(client, samples_root, tmp_path, filled_profile)
    sid = _plain_session(client)
    url = f"/api/runner/sessions/{sid}/trials/1/episode?partial=true"
    first = client.post(url, files={"h5": ("e.h5", h5)}).json()
    assert first["incomplete"] is True and first["missing"] == ["video: external"]
    second = client.post(url, files={"video:external": ("v.mp4", video)}).json()
    assert second["incomplete"] is False and second["valid"] is True


def test_partial_upload_needs_at_least_one_file_and_strict_mode_still_needs_all(client, samples_root, tmp_path, filled_profile):
    _, h5, _ = _manual_setup(client, samples_root, tmp_path, filled_profile)
    sid = _plain_session(client)
    assert client.post(f"/api/runner/sessions/{sid}/trials/1/episode?partial=true", files={}).status_code == 422
    strict = client.post(f"/api/runner/sessions/{sid}/trials/1/episode", files={"h5": ("e.h5", h5)})
    assert strict.status_code == 422 and "Missing" in strict.json()["detail"]


def test_manual_flow_is_timer_then_upload_then_annotate_and_annotate_can_upload(client):
    js = client.get("/runner.js").text
    assert "function rnFormHtml" not in js and 'id="rnTrialForm"' not in js      # no outcome form in the Runner
    assert 'showView("annotate")' in js and "epOpenInAnnotate(body.episode_id)" in js
    for marker in ("function openEpisodeUpload", "partial=true", "epSubmit", "addFilesBtn"):
        assert marker in js
    html = client.get("/").text
    assert 'id="uploadEpisodeBtn"' in html and "Upload episode" in html
    app_js = client.get("/app.js").text
    assert "badge incomplete" in app_js and "incomplete-banner" in app_js and "data.missing" in app_js
    auto_js = client.get("/automatic.js").text
    assert "auto.navigated" in auto_js and "selectEpisode(state.episode_id)" in auto_js


def test_manual_upload_step_has_do_it_later_with_a_popup_pointing_to_annotate(client):
    js = client.get("/runner.js").text
    for marker in ('id="rnLater"', "async function rnDoItLater", "Finish this trial in Annotate", "Go to Annotate", "epSend(s.session_id, e.trial_id, {}, true, true, rnTimerSeconds())", "incomplete episode"):
        assert marker in js


def test_do_it_later_creates_an_incomplete_episode_without_any_file(client, samples_root, filled_profile):
    sid = _plain_session(client)
    r = client.post(f"/api/runner/sessions/{sid}/trials/1/episode?partial=true&empty=true")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["incomplete"] is True and body["valid"] is False
    assert set(body["missing"]) == {"HDF5 recording data", "video: external"}
    listed = {e["episode_id"]: e for e in client.get("/api/episodes").json()}
    assert set(listed[body["episode_id"]]["missing"]) == {"HDF5 recording data", "video: external"}
    detail = client.get(f"/api/episodes/{body['episode_id']}").json()
    assert detail["annotation_defaults"]["attachment_id"] == "valve_ball"
    # Without the empty flag, a partial upload with no files is still rejected.
    assert client.post(f"/api/runner/sessions/{sid}/trials/1/episode?partial=true").status_code == 422
    # Calling it again does not change or duplicate the episode.
    again = client.post(f"/api/runner/sessions/{sid}/trials/1/episode?partial=true&empty=true").json()
    assert again["episode_id"] == body["episode_id"] and again["incomplete"] is True


def test_upload_dialog_shows_which_files_are_already_added_and_offers_replace(client):
    js = client.get("/runner.js").text
    for marker in ("Added: already uploaded", '"Replace"', "Replaces the current one: ", "async function epLoadExisting", "replaced\" : \"added\""):
        assert marker in js
    assert ".rn-file.existing" in client.get("/style.css").text


# ---- manual timer as completion time, with HDF5 / video alternatives -----------------------

def test_timer_is_stored_on_the_episode_and_offered_as_a_completion_source(client, samples_root, tmp_path, filled_profile):
    _, h5, video = _manual_setup(client, samples_root, tmp_path, filled_profile)
    sid = _plain_session(client)
    r = client.post(f"/api/runner/sessions/{sid}/trials/1/episode?timer_s=1.75", files={"h5": ("e.h5", h5), "video:external": ("v.mp4", video)})
    assert r.status_code == 200, r.text
    eid = r.json()["episode_id"]
    sources = client.get(f"/api/episodes/{eid}").json()["completion_sources"]
    assert sources["timer"] == 1.75
    assert abs(sources["hdf5"] - 1.99) < 0.05 and abs(sources["video"] - 2.0) < 0.1


def test_do_it_later_keeps_the_timer_too(client, filled_profile):
    sid = _plain_session(client)
    r = client.post(f"/api/runner/sessions/{sid}/trials/1/episode?partial=true&empty=true&timer_s=42.5").json()
    assert client.get(f"/api/episodes/{r['episode_id']}").json()["completion_sources"]["timer"] == 42.5


def test_completion_source_is_saved_and_a_timer_time_is_not_checked_against_the_recording(client, samples_root, tmp_path, filled_profile):
    _, h5, video = _manual_setup(client, samples_root, tmp_path, filled_profile)
    sid = _plain_session(client)
    eid = client.post(f"/api/runner/sessions/{sid}/trials/1/episode?timer_s=30", files={"h5": ("e.h5", h5), "video:external": ("v.mp4", video)}).json()["episode_id"]
    body = {"attachment_id": "valve_ball", "operator_name": "Op", "annotator_name": "Ann", "outcome": "success",
            "strategy": "prehensile", "completion_time_s": 30, "completion_source": "timer"}
    assert client.post(f"/api/episodes/{eid}/annotate", json=body).status_code == 200
    row = client.get(f"/api/episodes/{eid}").json()["annotation"]
    assert row["completion_source"] == "timer" and float(row["completion_time_s"]) == 30.0
    assert client.post(f"/api/episodes/{eid}/validate").json()["ok"] is True       # 30 s > 2 s recording is accepted for the timer
    # The same time with an HDF5 source is inconsistent with the 2 s recording.
    body.update(completion_source="hdf5")
    client.post(f"/api/episodes/{eid}/annotate", json=body)
    bad = client.post(f"/api/episodes/{eid}/validate").json()
    assert bad["ok"] is False and "longer than the recording" in bad["error"]
    body.update(completion_source="stopwatch")
    assert client.post(f"/api/episodes/{eid}/annotate", json=body).status_code == 422


def test_annotate_form_offers_timer_hdf5_video_for_the_completion_time(client):
    js = client.get("/app.js").text
    for marker in ("function completionSourceHtml", "COMPLETION_SOURCE_LABELS", "completion_sources", "payload.completion_time_s = Number(btn.dataset.seconds)"):
        assert marker in js
    assert "rnTimerSeconds()" in client.get("/runner.js").text


def test_review_lists_incomplete_episodes_with_complete_and_annotate_actions(client, filled_profile):
    sid = _plain_session(client)
    client.post(f"/api/runner/sessions/{sid}/trials/1/episode?partial=true&empty=true")
    entry = client.get(f"/api/runner/sessions/{sid}").json()["plan"][0]
    assert entry["recorded"] is True and entry["annotated"] is False          # counts as a trial ...
    assert entry["episode"]["incomplete"] is True and entry["episode"]["valid"] is False   # ... but is not valid
    assert set(entry["episode"]["missing"]) == {"HDF5 recording data", "video: external"}
    js = client.get("/runner.js").text
    for marker in ("p.recorded || p.episode", "data-complete", "openEpisodeUpload({ episode_id: entry.episode.episode_id", 'rn-badge warn'):
        assert marker in js


def test_timer_survives_files_added_later(client, samples_root, tmp_path, filled_profile):
    _, h5, video = _manual_setup(client, samples_root, tmp_path, filled_profile)
    sid = _plain_session(client)
    base = f"/api/runner/sessions/{sid}/trials/1/episode"
    eid = client.post(f"{base}?partial=true&empty=true&timer_s=1.5").json()["episode_id"]
    client.post(f"{base}?partial=true", files={"video:external": ("v.mp4", video)})
    client.post(f"{base}?partial=true", files={"h5": ("e.h5", h5)})       # rebuilds the header
    sources = client.get(f"/api/episodes/{eid}").json()["completion_sources"]
    assert sources["timer"] == 1.5 and sources["hdf5"] and sources["video"]


def test_an_incomplete_episode_counts_as_a_trial_but_is_not_valid(client, filled_profile):
    sid = _plain_session(client)
    client.post("/api/runner/sessions", json={"operator_name": "Op", "lab_id": "l", "platform_id": "p", "attachment_ids": ["valve_ball"], "per_task": 1})
    sid = client.post("/api/runner/sessions", json={"operator_name": "Op", "lab_id": "l", "platform_id": "p",
                      "attachment_ids": ["valve_ball"], "per_task": 3, "randomize": False}).json()["session_id"]
    before = client.get(f"/api/runner/sessions/{sid}").json()
    assert before["next_trial"]["trial_id"] == "1" and before["n_recorded"] == 0
    client.post(f"/api/runner/sessions/{sid}/trials/1/episode?partial=true&empty=true")
    after = client.get(f"/api/runner/sessions/{sid}").json()
    assert after["n_recorded"] == 1 and after["n_incomplete"] == 1 and after["n_episodes_valid"] == 0
    assert after["next_trial"]["trial_id"] == "2"                                  # the plan moves on
    assert after["per_task"]["valve_ball"] == {"planned": 3, "recorded": 1}
    # An episode-holding trial is locked when the plan is edited.
    edited = client.put(f"/api/runner/sessions/{sid}/plan", json={"per_task": 2, "randomize": False}).json()
    assert edited["plan"][0]["trial_id"] == "1" and edited["plan"][0]["episode"]["incomplete"] is True
    assert edited["n_planned"] == 2


def test_runner_creates_a_default_plan_for_today(client):
    js = client.get("/runner.js").text
    body = js[js.index("async function rnEnsureDefaultSession"):js.index("async function rnLoadCameras")]
    for marker in ("s.date === d.date", "per_task: d.required_trials", "attachment_ids: Object.keys(rn.tasks)", "randomize: true", 'mode: "manual"'):
        assert marker in body
    assert js.count("await rnEnsureDefaultSession()") == 2


def test_trials_csv_only_exports_trials_with_valid_episodes(client, samples_root, tmp_path, filled_profile):
    _, h5, video = _manual_setup(client, samples_root, tmp_path, filled_profile)
    sid = client.post("/api/runner/sessions", json={"operator_name": "Op", "lab_id": "l", "platform_id": "p",
                      "attachment_ids": ["valve_ball"], "per_task": 3, "randomize": False}).json()["session_id"]
    assert client.get(f"/api/runner/sessions/{sid}/trials.csv").status_code == 409
    files = {"h5": ("e.h5", h5), "video:external": ("v.mp4", video)}
    e1 = client.post(f"/api/runner/sessions/{sid}/trials/1/episode?timer_s=1.5", files=files).json()["episode_id"]
    e2 = client.post(f"/api/runner/sessions/{sid}/trials/2/episode?timer_s=1.5", files={"h5": ("e.h5", h5), "video:external": ("v.mp4", video)}).json()["episode_id"]
    body = {"attachment_id": "valve_ball", "operator_name": "Op", "annotator_name": "Ann", "outcome": "success", "strategy": "prehensile"}
    for eid in (e1, e2):
        client.post(f"/api/episodes/{eid}/annotate", json=body)
    assert client.get(f"/api/runner/sessions/{sid}/trials.csv").status_code == 409     # annotated, not validated yet
    assert client.post(f"/api/episodes/{e1}/validate").json()["ok"] is True
    r = client.get(f"/api/runner/sessions/{sid}/trials.csv")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    lines = r.text.strip().splitlines()
    assert lines[0].startswith("trial_id") and len(lines) == 2 and lines[1].startswith("1,")   # only the valid trial
    assert client.get(f"/api/runner/sessions/{sid}").json()["n_episodes_valid"] == 1


def test_review_table_separates_the_status_badge_from_compact_actions(client):
    js = client.get("/runner.js").text
    for marker in ("function rnEpisodeBadgeHtml", "function rnEpisodeActionsHtml", 'class="rn-mini"', 'class="rn-row-actions"', "s.n_episodes_valid ?"):
        assert marker in js
    assert "function rnEpisodeCellHtml" not in js
    assert ".rn-row-actions" in client.get("/style.css").text


def test_review_tables_stay_inside_their_card(client):
    js = client.get("/runner.js").text
    assert js.count('<div class="table-scroll">') == 2 and 'class="view-inner rn-wide"' in js
    body = js[js.index("function rnGlobalReviewHtml"):js.index("function rnWireGlobalReview")]
    assert 'class="rn-mini" data-open' in body and 'class="rn-row-actions"' in body
    css = client.get("/style.css").text
    assert ".table-scroll { overflow: visible; }" in css and "overflow-x: auto" not in css[css.index(".table-scroll"):css.index(".table-scroll") + 60]
    assert "white-space: nowrap" in css and ".rn-wide { max-width: 1060px; }" in css


def test_save_profile_button_is_on_the_right(client):
    assert 'class="actions profile-actions"' in client.get("/app.js").text
    assert ".actions.profile-actions { justify-content: flex-end; }" in client.get("/style.css").text


def test_task_description_is_one_shared_card_with_goal_stages_and_reset(client):
    js = client.get("/runner.js").text
    assert js.count("${rnTaskRefHtml(e.attachment_id)}") == 2 and 'class="rn-card task-reference"' not in js
    card = js[js.index("function rnTaskRefHtml"):js.index("function rnTrialHtml()")]
    for marker in ("task-ref-media", "task-ref-tag time", "Goal", "Stages, in order", "task-ref-stages", "Reset before the next trial"):
        assert marker in card
    css = client.get("/style.css").text
    assert ".task-ref-block.goal" in css and ".task-ref-stages li b" in css


def test_start_and_stop_buttons_show_the_keyboard_hint_on_their_own_line(client):
    js = client.get("/runner.js").text
    for btn, word in (('id="rnStart"', "to start"), ('id="rnStop"', "to stop")):
        i = js.index(btn)
        html = js[i:js.index("</div>", i)]
        assert 'class="rn-btn-icon"' in html and "</button>" in html
        assert html.index("</button>") < html.index('class="rn-key-hint"') and f"<kbd>Space</kbd> {word}" in html   # hint below the button
    css = client.get("/style.css").text
    assert "button.rn-go, button.rn-stop { display: inline-flex; flex-direction: row;" in css and ".rn-key-hint" in css


def test_automatic_panel_is_a_structured_card_with_header_flow_body_and_footer(client):
    js = client.get("/automatic.js").text
    shell = js[js.index("function autoShellHtml"):js.index("function autoPanelHtml")]
    for marker in ('class="auto-head"', "auto-chip", 'class="auto-flow"', 'class="auto-body', 'class="auto-foot"'):
        assert marker in shell
    for label in ('"Send", "Robot records", "Annotate"', "Ready", "Queued", "Recording", "Recorded"):
        assert label in js
    panel = js[js.index("function autoPanelHtml"):js.index("function autoRender")]
    assert panel.count("autoShellHtml(") == 4 and 'id="autoSend"' in panel and 'id="autoAbort"' in panel
    css = client.get("/style.css").text
    assert ".auto-panel" in css and ".auto-foot { display: flex; justify-content: flex-end;" in css


def test_edit_from_the_sessions_list_does_not_navigate_to_the_plan_page(client):
    js = client.get("/runner.js").text
    assert "function rnOpenEditPlan(session = null)" in js
    assert 'b.onclick = () => rnOpenEditPlan(find(b.dataset.edit))' in js
    wire = js[js.index("function rnWireSetup"):js.index("// ---------------------------------------------------------------- task plan")]
    assert "rnUseSession" not in wire.split("data-edit")[1].split("data-delete")[0]          # the edit handler never opens the session
    assert 'document.getElementById("rnEditPlan").onclick = () => rnOpenEditPlan();' in js   # the plan page's own icon still works
    assert "rn.sessions = rn.sessions.map(" in js


def test_defaults_expose_the_samples_folder_for_the_robot_script_box(client, samples_root):
    assert client.get("/api/runner/defaults").json()["samples_root"] == str(samples_root.resolve())


def test_connect_robot_script_box_uses_real_values_and_a_copy_button(client):
    js = client.get("/automatic.js").text
    for marker in ("function autoSnippetText", "rn.defaults.samples_root", "location.origin", "rn.cameras", "function autoHighlight",
                   "Connect your robot script", "auto-connect-steps", 'id="autoCopy"', "Copied \\u2713", "wait_for_task()"):
        assert marker in js
    assert "AUTO_SNIPPET" not in js and 'writer.attach_video("${c}"' in js
    assert ".auto-code-pre" in client.get("/style.css").text


def test_snippet_highlighter_is_single_pass(client):
    js = client.get("/automatic.js").text
    body = js[js.index("function autoHighlight"):js.index("function autoSnippetHtml")]
    assert body.count(".replace(") == 0 and "token.exec(code)" in body and "tok-c" in body and "tok-s" in body and "tok-k" in body


def test_every_runner_helper_that_is_called_is_defined(client):
    """A removed helper (like autoStateKey once was) only fails at runtime in the browser, so check statically."""
    import re
    files = ["app.js", "nav.js", "runner.js", "automatic.js"]
    src = "\n".join(client.get(f"/{f}").text for f in files)
    defined = set(re.findall(r"(?:async\s+)?function\s+([A-Za-z_$][\w$]*)", src)) | set(re.findall(r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=", src))
    for prefix in ("auto", "rn", "ep", "cl"):
        called = set(re.findall(rf"(?<![.\w$])({prefix}[A-Z][\w$]*)\(", src))
        assert not (called - defined), f"called but not defined: {sorted(called - defined)}"
    assert "function autoStateKey" in src


def test_review_tables_never_need_a_horizontal_scrollbar(client):
    css = client.get("/style.css").text
    assert "@media (max-width: 1150px)" in css and "@media (max-width: 860px)" in css and "@media (max-width: 640px)" in css
    assert ".rn-trials-table th:nth-child(5)" in css and ".rn-sessions-table th:nth-child(4)" in css
    js = client.get("/runner.js").text
    assert 'class="trial-table rn-trials-table"' in js and "<th>Valid</th>" in js


def test_a_page_refresh_keeps_the_open_session_and_page(client):
    js = client.get("/runner.js").text
    for marker in ('const RN_STATE_KEY = "datahive-runner-state"', "function rnSaveState", "function rnRestoreState"):
        assert marker in js
    load = js[js.index("async function rnLoad() {"):js.index("function rnUseSession")]
    assert "rnRestoreState();" in load and load.index('rn.step = "setup";') < load.index("rnRestoreState();") < load.index("rn.loaded = true;")
    assert 'rn.step === "review" ? "review" : "task"' in js           # a trial in progress comes back on the plan
    render = js[js.index("function rnRender()"):js.index("function rnRender()") + 900]
    assert "rnSaveState();" in render
    # Opening the tab from another section still starts on the sessions list.
    assert 'rn.step = "setup"; rnResetTimer(); rnCloseModal();' in js


def test_setup_and_review_share_the_same_width(client):
    js = client.get("/runner.js").text
    setup = js[js.index("function rnSetupHtml()"):js.index("function rnNewSessionModalHtml()")]
    review = js[js.index("function rnGlobalReviewHtml()"):js.index("function rnWireGlobalReview()")]
    assert 'class="view-inner rn-wide"' in setup and 'class="view-inner rn-wide"' in review


def test_home_guide_is_wired_up():
    from pathlib import Path

    static = Path(__file__).parent.parent / "src" / "datahive" / "interface" / "static"
    html = (static / "index.html").read_text()
    assert 'id="guideSteps"' in html and "/guide.js" in html
    guide = (static / "guide.js").read_text()
    for helper in ("showView", "openProfileOverlay"):
        assert helper in guide
    assert guide.count("cta:") == 6
