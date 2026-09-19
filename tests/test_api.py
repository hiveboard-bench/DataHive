from __future__ import annotations

import re

import pytest
import yaml
from fastapi.testclient import TestClient

from datahive import ops
from datahive.annotate import annotate_episode
from datahive.trials import read_rows
from datahive.paths import trials_csv_path
from datahive.index import Index
from datahive.paths import profile_path
from datahive.profile import load_profile, write_profile_skeleton
from datahive.interface.app import create_app

from conftest import make_episode, write_valid_annotation


def _client(samples_root):
    app = create_app(samples_root)
    return TestClient(app)


def _fill_profile(samples_root):
    path = write_profile_skeleton(samples_root)
    data = yaml.safe_load(path.read_text())
    data["manipulator"] = {"model": "TestArm", "dof": 6, "joint_names": [f"j{i}" for i in range(6)]}
    data["low_level"] = {"mode": "stock"}
    data["cameras"] = [{"name": "external", "resolution": "1280x720", "encoding": "h264", "fps": 30}]
    data["platform_id"] = "rig-01"
    data["robot_name"] = "test_arm"
    data["gripper_name"] = "test_gripper"
    data["is_biarm"] = False
    data["uses_mobile_base"] = False
    data["control_freq"] = 100
    data["action_space"] = ["joint_position", "gripper_binary"]
    data["action_joint_names"] = ["j0", "j1", "j2", "j3", "j4", "j5"]
    data["policy"] = "teleop_spacemouse"
    data["end_effector"] = {"type": "gripper", "actuated_dof": 1, "command_modality": "position"}
    path.write_text(yaml.safe_dump(data))
    return load_profile(samples_root)


def test_list_episodes_endpoint(samples_root):
    profile = _fill_profile(samples_root)
    make_episode(samples_root, "sess1", "ep1", trial_id="t1", profile=profile)
    client = _client(samples_root)
    resp = client.get("/api/episodes")
    assert resp.status_code == 200
    ids = [e["episode_id"] for e in resp.json()]
    assert "ep1" in ids


def test_get_episode_detail_endpoint(samples_root):
    profile = _fill_profile(samples_root)
    make_episode(samples_root, "sess1", "ep1", trial_id="t1", profile=profile)
    client = _client(samples_root)
    resp = client.get("/api/episodes/ep1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["header"]["episode_id"] == "ep1"
    assert "external" in body["cameras"]


def test_trajectory_endpoint_respects_max_points(samples_root):
    profile = _fill_profile(samples_root)
    make_episode(samples_root, "sess1", "ep1", trial_id="t1", profile=profile, n_points=500)
    client = _client(samples_root)
    resp = client.get("/api/episodes/ep1/trajectory?max_points=50")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["timestamp"]) <= 50


def test_annotate_then_validate_via_gui_visible_in_cli_list(samples_root):
    """Save (POST .../annotate) and Validate (POST .../validate) are
    separate calls in the GUI, matching the CLI's `datahive annotate` /
    `datahive validate` split."""
    profile = _fill_profile(samples_root)
    make_episode(samples_root, "sess1", "ep1", trial_id="t1", profile=profile)
    client = _client(samples_root)

    resp = client.post(
        "/api/episodes/ep1/annotate",
        json={
            "attachment_id": "valve_ball", "outcome": "success", "completion_time_s": 1.5, "operator_name": "Op", "annotator_name": "Ann",
            "n_attempts": 1, "n_regrasps": 0, "strategy": "prehensile",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    # Saving alone does not validate -- status stays 'recorded'.
    statuses = ops.list_episodes(samples_root)
    assert statuses[0].status == "recorded"

    resp = client.post("/api/episodes/ep1/validate")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    # Same index the CLI's `datahive list` reads from.
    statuses = ops.list_episodes(samples_root)
    assert statuses[0].status == "validated"


def test_annotate_via_cli_visible_via_gui_api(samples_root):
    from datahive.annotate import annotate_episode

    profile = _fill_profile(samples_root)
    make_episode(samples_root, "sess1", "ep1", trial_id="t1", profile=profile)
    annotate_episode(
        samples_root, "ep1",
        {"attachment_id": "valve_ball", "outcome": "success", "completion_time_s": 1.5, "operator_name": "Op", "annotator_name": "Ann",
         "n_attempts": 1, "n_regrasps": 0, "strategy": "prehensile"},
    )
    client = _client(samples_root)
    resp = client.get("/api/episodes/ep1")
    assert resp.json()["annotation"]["outcome"] == "success"
    assert resp.json()["index"]["status"] == "validated"


def test_upload_and_delete_via_gui_use_same_mocked_hub(samples_root, fake_hub, monkeypatch):
    profile = _fill_profile(samples_root)
    make_episode(samples_root, "sess1", "ep1", trial_id="t1", profile=profile)
    write_valid_annotation(samples_root, "sess1", "t1")
    from datahive.validate import validate_episode

    validate_episode(samples_root, "ep1")

    # Patch ops._get_hub so the API routes (which build their own Hub from
    # config) use our fake instead of touching the network.
    monkeypatch.setattr(ops, "_get_hub", lambda hub, cfg=None: fake_hub)

    client = _client(samples_root)
    resp = client.post("/api/episodes/ep1/upload")
    assert resp.status_code == 200
    assert resp.json()["uploaded"] is True

    with Index(samples_root) as idx:
        assert idx.get("ep1").status == "uploaded"

    resp = client.delete("/api/episodes/ep1")
    assert resp.status_code == 200
    assert resp.json()["deleted_remote"] is True

    with Index(samples_root) as idx:
        assert idx.get("ep1") is None


def test_get_profile_when_missing(samples_root):
    client = _client(samples_root)
    resp = client.get("/api/profile")
    assert resp.status_code == 200
    body = resp.json()
    assert body["exists"] is False
    assert "low_level.mode is not set" in " ".join(body["problems"])


def test_create_profile_via_api(samples_root):
    client = _client(samples_root)
    resp = client.post("/api/profile")
    assert resp.status_code == 200
    body = resp.json()
    assert body["exists"] is True
    assert (samples_root / "robot_profile.yaml").is_file()

    # Creating again without force is refused.
    resp2 = client.post("/api/profile")
    assert resp2.status_code == 409


def test_update_profile_via_api_completes_it(samples_root):
    client = _client(samples_root)
    client.post("/api/profile")

    payload = {
        "manipulator": {"model": "TestArm", "dof": 6, "joint_names": [f"j{i}" for i in range(6)]},
        "end_effector": {"type": "gripper", "actuated_dof": 1, "command_modality": "position"},
        "low_level": {"mode": "stock", "controller_type": "pid", "rate_hz": 500, "gains": None},
        "control_mode": "joint_position",
        "policy": "teleop_spacemouse",
        "cameras": [{"name": "external", "resolution": "1280x720", "encoding": "h264", "fps": 30}],
        "board_mounting": "horizontal",
        "hiveboard_version": "v2",
        "board_fabrication": {
            "printer": "Prusa MK4",
            "material": "PETG (Prusament)",
            "print_settings": "0.4mm nozzle, 0.2mm layers, 4 walls, 40% infill",
            "post_processing": "Light sanding on mating surfaces",
            "calibration_notes": "Bed leveled before this batch",
        },
        "units_and_frames": {},
        "platform_id": "rig-01",
        "robot_name": "TestArm", "gripper_name": "g", "is_biarm": False, "uses_mobile_base": False, "control_freq": 100, "action_space": ["joint_position", "gripper_binary"], "action_joint_names": ["j0", "j1", "j2", "j3", "j4", "j5"],
    }
    resp = client.put("/api/profile", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["problems"] == []
    assert body["profile"]["board_fabrication"]["printer"] == "Prusa MK4"

    # The same file `datahive validate` / EpisodeWriter read.
    profile = load_profile(samples_root)
    assert profile.manipulator["model"] == "TestArm"
    assert profile.board_fabrication["material"] == "PETG (Prusament)"

    # And an episode can now be built and validated against it -- the
    # board fabrication details are snapshotted into its header too.
    make_episode(samples_root, "sess1", "ep1", trial_id="t1", profile=profile)
    write_valid_annotation(samples_root, "sess1", "t1")
    from datahive.validate import validate_episode

    validate_episode(samples_root, "ep1")

    detail = client.get("/api/episodes/ep1").json()
    assert detail["header"]["board_fabrication"]["printer"] == "Prusa MK4"


def test_bulk_upload_and_delete_via_api(samples_root, fake_hub, monkeypatch):
    profile = _fill_profile(samples_root)
    for i in (1, 2):
        make_episode(samples_root, "sess1", f"ep{i}", trial_id=f"t{i}", profile=profile)
        write_valid_annotation(samples_root, "sess1", f"t{i}")
    from datahive.validate import validate_episode

    validate_episode(samples_root, "ep1")
    validate_episode(samples_root, "ep2")

    monkeypatch.setattr(ops, "_get_hub", lambda hub, cfg=None: fake_hub)
    client = _client(samples_root)

    resp = client.post("/api/episodes/bulk-upload", json={"episode_ids": ["ep1", "ep2", "does-not-exist"]})
    assert resp.status_code == 200
    results = resp.json()["results"]
    by_id = {r["episode_id"]: r for r in results}
    assert by_id["ep1"]["uploaded"] is True
    assert by_id["ep2"]["uploaded"] is True
    assert by_id["does-not-exist"]["uploaded"] is False
    assert by_id["does-not-exist"]["error"]

    resp = client.post("/api/episodes/bulk-delete", json={"episode_ids": ["ep1", "ep2"]})
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert all(r["deleted_local"] for r in results)
    with Index(samples_root) as idx:
        assert idx.get("ep1") is None
        assert idx.get("ep2") is None


def test_bulk_upload_with_missing_config_fails_cleanly_per_episode(samples_root, monkeypatch):
    """Regression: bulk_upload_episodes must not let a Hub-resolution error
    (e.g. no ~/.datahive/config.yaml) crash the whole batch with a 500 --
    every id should come back as a normal failed result."""
    profile = _fill_profile(samples_root)
    make_episode(samples_root, "sess1", "ep1", trial_id="t1", profile=profile)
    write_valid_annotation(samples_root, "sess1", "t1")
    from datahive.validate import validate_episode

    validate_episode(samples_root, "ep1")

    client = _client(samples_root)  # no config fixture used -> load_config() raises
    resp = client.post("/api/episodes/bulk-upload", json={"episode_ids": ["ep1"]})
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert results[0]["uploaded"] is False
    assert results[0]["error"]


def test_bulk_delete_without_upload_needs_no_hub_config(samples_root):
    """Deleting never-uploaded episodes must not require a Hub config at
    all (mirrors single delete_episode's laziness)."""
    profile = _fill_profile(samples_root)
    make_episode(samples_root, "sess1", "ep1", trial_id="t1", profile=profile)

    client = _client(samples_root)  # no config fixture -> would raise if Hub were touched
    resp = client.post("/api/episodes/bulk-delete", json={"episode_ids": ["ep1"]})
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert results[0]["deleted_local"] is True
    assert results[0]["deleted_remote"] is False


def test_annotate_saves_operator_annotator_and_severity(samples_root):
    profile = _fill_profile(samples_root)
    make_episode(samples_root, "sess1", "ep1", trial_id="t1", profile=profile)
    client = _client(samples_root)

    resp = client.post(
        "/api/episodes/ep1/annotate",
        json={
            "attachment_id": "valve_ball", "operator_name": "Alex", "outcome": "fail",
            "failure_cause": "slip", "severity": "critical", "n_attempts": 2, "n_regrasps": 1,
            "strategy": "prehensile", "annotator_name": "Sam",
        },
    )
    assert resp.status_code == 200

    detail = client.get("/api/episodes/ep1").json()
    ann = detail["annotation"]
    assert ann["operator_name"] == "Alex"
    assert ann["annotator_name"] == "Sam"
    assert ann["severity"] == "critical"


def test_severity_forbidden_on_success(samples_root):
    profile = _fill_profile(samples_root)
    make_episode(samples_root, "sess1", "ep1", trial_id="t1", profile=profile)
    client = _client(samples_root)

    resp = client.post(
        "/api/episodes/ep1/annotate",
        json={
            "attachment_id": "valve_ball", "outcome": "success", "completion_time_s": 1.5, "operator_name": "Op", "annotator_name": "Ann",
            "severity": "minor", "strategy": "prehensile",
        },
    )
    assert resp.status_code == 422


def test_annotate_derives_completion_time_from_episode_duration(samples_root):
    """completion_time_s is not a form field any more -- the GUI never
    sends it for a success outcome, so annotate_episode() must derive it
    from the episode's own recorded duration."""
    profile = _fill_profile(samples_root)
    make_episode(samples_root, "sess1", "ep1", trial_id="t1", profile=profile, n_points=500, rate_hz=100.0)
    client = _client(samples_root)

    resp = client.post(
        "/api/episodes/ep1/annotate",
        json={"attachment_id": "valve_ball", "outcome": "success", "strategy": "prehensile"},
    )
    assert resp.status_code == 200

    detail = client.get("/api/episodes/ep1").json()
    completion_time = float(detail["annotation"]["completion_time_s"])
    expected = detail["stats"]["duration_s"]
    assert completion_time == pytest.approx(expected, abs=0.01)


def test_status_when_not_configured(samples_root):
    client = _client(samples_root)  # no config fixture -> load_config() raises
    resp = client.get("/api/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["configured"] is False
    assert body["connected"] is False


def test_previous_annotations_lists_newest_first_excluding_self(samples_root):
    profile = _fill_profile(samples_root)
    make_episode(samples_root, "sess1", "ep1", trial_id="t1", profile=profile)
    make_episode(samples_root, "sess1", "ep2", trial_id="t2", profile=profile)
    make_episode(samples_root, "sess1", "ep3", trial_id="t3", profile=profile)
    client = _client(samples_root)

    # No annotations yet anywhere.
    resp = client.get("/api/episodes/ep3/previous-annotations")
    assert resp.status_code == 200
    assert resp.json()["results"] == []

    client.post(
        "/api/episodes/ep1/annotate",
        json={"attachment_id": "valve_ball", "operator_name": "Alex", "outcome": "success", "strategy": "prehensile"},
    )
    resp = client.post(
        "/api/episodes/ep2/annotate",
        json={"attachment_id": "thread_m8", "operator_name": "Sam", "outcome": "fail", "failure_cause": "slip", "strategy": "prehensile"},
    )
    assert resp.status_code == 200, resp.text

    # ep3 sees both, newest (ep2) first.
    resp = client.get("/api/episodes/ep3/previous-annotations")
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert [r["episode_id"] for r in results] == ["ep2", "ep1"]
    assert results[0]["annotation"]["operator_name"] == "Sam"
    assert results[1]["annotation"]["operator_name"] == "Alex"

    # ep1 itself is excluded from its own "previous annotations" list.
    resp = client.get("/api/episodes/ep1/previous-annotations")
    assert [r["episode_id"] for r in resp.json()["results"]] == ["ep2"]


def test_status_when_connected(samples_root, fake_hub, monkeypatch):
    from datahive import ops

    profile = _fill_profile(samples_root)
    make_episode(samples_root, "sess1", "ep1", trial_id="t1", profile=profile)
    write_valid_annotation(samples_root, "sess1", "t1")
    from datahive.validate import validate_episode

    validate_episode(samples_root, "ep1")

    monkeypatch.setattr(ops, "_get_hub", lambda hub, cfg=None: fake_hub)
    client = _client(samples_root)
    resp = client.get("/api/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["configured"] is True
    assert body["connected"] is True
    assert body["pending_count"] == 1  # ep1 is validated but not yet uploaded


def test_status_when_hub_unreachable(samples_root, config, monkeypatch):
    from datahive import ops
    from datahive.errors import HubError

    _fill_profile(samples_root)

    class FailingHub:
        def whoami(self):
            raise HubError("simulated network failure")

    monkeypatch.setattr(ops, "_get_hub", lambda hub, cfg=None: FailingHub())
    client = _client(samples_root)
    resp = client.get("/api/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["configured"] is True
    assert body["connected"] is False
    assert body["error"]


def test_attachments_registry_matches_hiveboard_evaluation_runner(samples_root):
    """The bundled registry mirrors HiveBoard's Evaluation Runner task list
    (https://hiveboard-bench.github.io/hivedocs/benchmark/evaluation-runner):
    13 conditions, each with family/timeout, and each task-thumbnail image
    is actually served."""
    client = _client(samples_root)
    resp = client.get("/api/attachments")
    assert resp.status_code == 200
    registry = resp.json()
    assert len(registry) == 13

    valve = registry["valve_ball"]
    assert valve["name"] == "Ball valve"
    assert valve["family"] == "Torque"
    assert valve["timeout"] == 60
    assert valve["composed_assembly"] is False

    lock = registry["lock"]
    assert lock["composed_assembly"] is True
    assert lock["stages"] == ["Grasp key", "Insert key vertically", "Rotate to unlock"]

    for aid, info in registry.items():
        if info["image"]:
            img_resp = client.get(f"/tasks/{info['image']}")
            assert img_resp.status_code == 200, f"missing thumbnail for {aid}: {info['image']}"


def test_bulk_validate_via_api(samples_root):
    profile = _fill_profile(samples_root)
    make_episode(samples_root, "sess1", "ep1", trial_id="t1", profile=profile)
    make_episode(samples_root, "sess1", "ep2", trial_id="t2", profile=profile)
    write_valid_annotation(samples_root, "sess1", "t1")
    client = _client(samples_root)

    resp = client.post("/api/episodes/bulk-validate", json={"episode_ids": ["ep1", "ep2"]})
    assert resp.status_code == 200
    results = {r["episode_id"]: r for r in resp.json()["results"]}
    assert results["ep1"]["ok"] is True
    assert results["ep2"]["ok"] is False
    assert results["ep2"]["error"]


def test_server_binds_localhost_only():
    import inspect

    from datahive.cli import serve

    source = inspect.getsource(serve)
    assert '"127.0.0.1"' in source
    assert "--host" not in source


def test_web_interface_navigation_bar(samples_root):
    client = _client(samples_root)
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.text
    assert "◀ Prev" in html
    assert "No episode selected" in html
    assert "Next ▶" in html
    assert "Next unannotated ▶▶" in html
    assert 'id="prevBtn"' in html
    assert 'id="nextBtn"' in html
    assert 'id="nextUnannotatedBtn"' in html
    assert 'id="sessionH5Name"' in html


def test_web_interface_failure_help_modal(samples_root):
    client = _client(samples_root)
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.text
    assert 'id="failureHelpOverlay"' in html
    assert "Failure Causes Guide" in html
    assert "Cause" in html and "Use when" in html and "Discriminator" in html
    for cause in [
        "grasp_geometry",
        "kinematic_limit",
        "perception",
        "slip",
        "force_limit",
        "control_precision",
        "other",
    ]:
        assert f"<code>{cause}</code>" in html

    js_resp = client.get("/app.js")
    assert js_resp.status_code == 200
    assert "failureHelpBtn" in js_resp.text
    assert "openFailureHelpOverlay" in js_resp.text


def test_web_interface_select_task_help_link(samples_root):
    client = _client(samples_root)
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.text
    assert 'id="taskOverlay"' in html
    assert "https://hiveboard-bench.github.io/hivedocs/benchmark/tasks" in html
    assert "Select task" in html


def test_web_interface_strategy_tooltips(samples_root):
    client = _client(samples_root)
    resp = client.get("/app.js")
    assert resp.status_code == 200
    js = resp.text
    assert "STRATEGY_MEANINGS" in js
    assert "prehensile" in js and "non_prehensile" in js
    assert 'segmentedControlHtml("strategy", STRATEGIES, ann.strategy, STRATEGY_MEANINGS)' in js


def test_web_interface_auto_advance_and_hide_annotated(samples_root):
    client = _client(samples_root)
    html = client.get("/").text
    assert 'id="hideAnnotatedChk"' in html

    js = client.get("/app.js").text
    assert 'id="autoAdvanceChk"' in js
    assert "datahive-hide-annotated" in js
    assert "datahive-auto-advance" in js
    assert "isAutoAdvanceEnabled" in js


def test_web_interface_unsaved_changes_and_autofill(samples_root):
    client = _client(samples_root)
    js = client.get("/app.js").text
    assert 'id="dirtyIndicator"' in js
    assert "isDirty" in js
    assert "confirmDiscardIfDirty" in js
    assert "beforeunload" in js
    assert "datahive-last-operator" in js
    assert "datahive-last-annotator" in js


def test_web_interface_task_banner_and_video_controls(samples_root):
    client = _client(samples_root)
    js = client.get("/app.js").text
    assert 'id="taskInstructionCard"' in js
    assert 'id="taskInstructionText"' in js
    assert 'id="toggleVideosBtn"' in js
    assert 'id="stepBackBtn"' in js
    assert 'id="stepForwardBtn"' in js
    assert 'id="failureTimeField"' in js
    assert 'id="markCurrentTimeBtn"' in js
    assert "stepVideos" in js
    assert "toggleVideosPlay" in js
    assert "isSyncingVideo" in js
    assert "failure_time" in js


def test_web_interface_last_completed_stage_under_strategy(samples_root):
    client = _client(samples_root)
    js = client.get("/app.js").text
    assert 'fieldLabel("Last completed stage")' in js
    assert "0 — No stage completed" in js

    # Verify stageField is placed after Strategy in the template
    strat_pos = js.find('segmentedControlHtml("strategy"')
    stage_pos = js.find('id="stageField"')
    assert strat_pos != -1 and stage_pos != -1
    assert stage_pos > strat_pos, "stageField must be below strategy"
    assert '<label id="stageField" class="full" style="${composed ? "" : "display:none"}">' in js
    assert '<select name="stage_reached"' in js

    # Verify registry returns stages for all 4 composed-assembly tasks
    resp = client.get("/api/attachments")
    assert resp.status_code == 200
    att = resp.json()
    assert len(att) == 13
    assert att["button"]["stages"] == ["Open cover", "Press button"]
    assert att["lock"]["stages"] == ["Grasp key", "Insert key vertically", "Rotate to unlock"]
    assert att["drawer"]["stages"] == ["Grasp handle", "Pull open", "Push closed"]
    assert att["shock_absorber"]["stages"] == ["Grasp pin", "Align with hole", "Insert fully"]
    assert att["valve_ball"]["composed_assembly"] is False
    assert att["valve_ball"]["stages"] is None
    assert att["peg_insertion"]["composed_assembly"] is False
    assert att["peg_insertion"]["stages"] is None


def test_web_interface_hide_annotated_below_filter(samples_root):
    client = _client(samples_root)
    html = client.get("/").text
    assert '<div class="filter-options-row">' in html
    filter_row_pos = html.find('class="filter-row"')
    options_row_pos = html.find('class="filter-options-row"')
    assert filter_row_pos != -1 and options_row_pos != -1
    assert options_row_pos > filter_row_pos, "Hide annotated row must be below filter select row"
    assert 'id="hideAnnotatedChk"' in html[options_row_pos:]


def test_web_interface_profile_help_link(samples_root):
    client = _client(samples_root)
    html = client.get("/").text
    profile_pos = html.find('id="profileOverlay"')
    assert profile_pos != -1
    profile_html = html[profile_pos:html.find('</div>', profile_pos + 500)]
    assert "https://hiveboard-bench.github.io/hivedocs/" in profile_html
    assert "Help" in profile_html

    js = client.get("/app.js").text
    assert "https://hiveboard-bench.github.io/hivedocs/" in js


def test_web_interface_operator_annotator_required(samples_root):
    client = _client(samples_root)
    js = client.get("/app.js").text
    assert 'fieldLabel("Operator name")' in js
    assert 'fieldLabel("Annotator name")' in js
    assert 'name="operator_name"' in js and "required" in js
    assert 'name="annotator_name"' in js and "required" in js
    assert "Operator name is required." in js
    assert "Annotator name is required." in js


def test_web_interface_form_options_row_above_actions(samples_root):
    client = _client(samples_root)
    js = client.get("/app.js").text
    options_row_pos = js.find('class="form-options-row"')
    actions_pos = js.find('<div class="actions">', options_row_pos)
    assert options_row_pos != -1 and actions_pos != -1
    assert options_row_pos < actions_pos, "form-options-row must be above actions buttons"
    assert 'id="autoAdvanceChk"' in js[options_row_pos:actions_pos]
    assert 'id="dirtyIndicator"' in js[options_row_pos:actions_pos]


def test_web_interface_stats_blue_and_stage_dropdown_full_width(samples_root):
    client = _client(samples_root)
    css = client.get("/style.css").text
    assert ".stat-value {" in css
    assert "color: var(--accent)" in css
    assert "#stageFieldBody select {" in css
    assert "width: 100%" in css
    assert "#stageField {" in css and "font-weight: 400" in css
    assert "#notesField {" in css and "font-weight: 400" in css


def test_api_statistics_empty(samples_root):
    client = _client(samples_root)
    resp = client.get("/api/statistics")
    assert resp.status_code == 200
    data = resp.json()
    assert data["summary"]["total_trials"] == 0
    assert data["summary"]["successful_trials"] == 0
    assert data["summary"]["conditions_tested"] == 0
    assert data["summary"]["conditions_total"] == 13
    assert data["summary"]["target_trials_per_condition"] == 5
    assert data["summary"]["total_target_trials"] == 65

    assert data["readiness"]["is_complete"] is False
    assert data["readiness"]["status_class"] == "incomplete"
    check_labels = [c["label"] for c in data["readiness"]["checks"]]
    assert "Experimental setup recorded" in check_labels
    assert "All 13 conditions recorded" in check_labels
    assert "5 trials recorded for every condition" in check_labels
    assert "Trial entries valid" in check_labels

    assert len(data["conditions"]) == 13
    cond_ids = [c["id"] for c in data["conditions"]]
    assert "valve_ball" in cond_ids
    assert "button" in cond_ids
    assert "drawer" in cond_ids
    for c in data["conditions"]:
        assert c["count"] == 0
        assert c["target"] == 5
        assert c["complete"] is False


def test_api_statistics_with_data(samples_root):
    profile = _fill_profile(samples_root)
    client = _client(samples_root)

    # Add 5 trials for valve_ball (all success)
    for i in range(5):
        ep_id = f"ep_vb_{i}"
        make_episode(samples_root, "sess1", ep_id, trial_id=f"t_vb_{i}", profile=profile)
        write_valid_annotation(
            samples_root, "sess1", f"t_vb_{i}",
            outcome="success", attachment_id="valve_ball", completion_time_s=1.5,
        )

    # Add 2 trials for button (1 fail, 1 success)
    for i in range(2):
        ep_id = f"ep_btn_{i}"
        make_episode(samples_root, "sess1", ep_id, trial_id=f"t_btn_{i}", profile=profile)
        outcome = "success" if i == 0 else "fail"
        write_valid_annotation(
            samples_root, "sess1", f"t_btn_{i}",
            outcome=outcome, attachment_id="button",
            stage_reached=2 if outcome == "success" else 1,
            composed_assembly=True,
        )

    resp = client.get("/api/statistics")
    assert resp.status_code == 200
    data = resp.json()
    assert data["summary"]["total_trials"] == 7
    assert data["summary"]["successful_trials"] == 6
    assert data["summary"]["conditions_tested"] == 2
    assert data["summary"]["conditions_total"] == 13

    cond_map = {c["id"]: c for c in data["conditions"]}
    assert cond_map["valve_ball"]["count"] == 5
    assert cond_map["valve_ball"]["complete"] is True
    assert cond_map["button"]["count"] == 2
    assert cond_map["button"]["complete"] is False

    checks = {c["label"]: c["passed"] for c in data["readiness"]["checks"]}
    assert checks["Experimental setup recorded"] is True
    assert checks["All 13 conditions recorded"] is False
    assert checks["5 trials recorded for every condition"] is False
    assert checks["Trial entries valid"] is True


def test_web_interface_statistics_modal_and_layout(samples_root):
    client = _client(samples_root)
    html = client.get("/").text

    # Statistics button next to profileBtn
    profile_btn_pos = html.find('id="profileBtn"')
    stats_btn_pos = html.find('id="statsBtn"')
    sync_btn_pos = html.find('id="syncBtn"')
    assert profile_btn_pos != -1 and stats_btn_pos != -1 and sync_btn_pos != -1
    assert profile_btn_pos < stats_btn_pos < sync_btn_pos
    assert "Statistics" in html[stats_btn_pos:sync_btn_pos]

    # Modal overlay
    stats_overlay_pos = html.find('id="statsOverlay"')
    assert stats_overlay_pos != -1
    stats_html = html[stats_overlay_pos:html.find('</div>\n  </div>', stats_overlay_pos) + 20]
    assert 'class="overlay-panel stats-overlay-panel"' in stats_html
    assert 'id="statsCloseBtn"' in stats_html
    assert 'id="statsBody"' in stats_html
    assert "https://hiveboard-bench.github.io/hivedocs/benchmark/evaluation-runner" in stats_html

    # JS application logic
    js = client.get("/app.js").text
    assert "openStatsOverlay" in js
    assert "renderStats" in js
    assert 'api("/api/statistics")' in js
    assert "review-stats" in js
    assert "readiness-panel" in js
    assert "condition-progress" in js
    assert "validation-list" in js
    assert "Check trial records" in js
    assert "Trial records complete" in js
    assert "Submission package" in js

    # CSS styles matching HiveBoard Evaluation Runner layout
    css = client.get("/style.css").text
    assert ".review-stats" in css
    assert ".readiness-panel" in css
    assert ".readiness-heading" in css
    assert ".condition-progress" in css
    assert ".validation-list" in css
    assert ".eyebrow" in css
    assert ".stats-overlay-panel" in css


def test_robot_profile_redesigned_ui(samples_root):
    """Verifies that Robot Profile UI features icons, section subtitles,
    segmented controls for <= 3 options (instead of select dropdowns),
    required asterisks on mandatory fields, and proper CSS styling."""
    client = _client(samples_root)

    # HTML references cache-busted asset URLs
    html = client.get("/").text
    assert re.search(r'<link rel="stylesheet" href="/style\.css\?v=\d+">', html)
    assert re.search(r'<script src="/app\.js\?v=\d+"></script>', html)

    # JS profile logic
    js = client.get("/app.js").text
    # Segmented controls instead of dropdowns for <= 3 options
    assert 'segmentedControlHtml("end_effector.type"' in js
    assert 'segmentedControlHtml("end_effector.command_modality"' in js
    assert 'segmentedControlHtml("low_level.mode"' in js
    assert 'segmentedControlHtml("board_mounting"' in js
    assert '<select name="end_effector.type">' not in js
    assert '<select name="end_effector.command_modality">' not in js
    assert '<select name="low_level.mode"' not in js
    assert '<select name="board_mounting">' not in js

    # Required asterisks via fieldLabel on mandatory fields
    assert "Robot state joint names (comma-separated, in order)" in js
    assert '${fieldLabel("Mode")}' in js
    assert '${fieldLabel("Name")}' in js
    # Resolution, fps and encoding are detected from the videos, never typed.
    for gone in ('data-field="resolution"', 'data-field="fps"', 'data-field="encoding"'):
        assert gone not in js

    # Icons, badges, and section descriptions
    assert "card-header-with-icon" in js
    assert "section-icon-wrap" in js
    assert "card-title-row" in js
    assert "section-desc" in js
    assert "camera-title-badge" in js
    assert "wireSegmentedControls(form)" in js
    assert "allowDeselect" in js

    # CSS styles
    css = client.get("/style.css").text
    assert ".card-header-with-icon" in css
    assert ".section-icon-wrap" in css
    assert ".card-title-row" in css
    assert ".section-desc" in css
    assert ".profile-card" in css
    assert ".camera-title-badge" in css
    assert ".required-mark" in css



def test_statistics_are_split_by_plan_and_trial_ids_do_not_collide(samples_root):
    from datahive import runner
    a = runner.create_session(samples_root, lab_id="l", platform_id="p", operator_name="Ana", date="2026-09-18",
                              attachment_ids=["valve_ball"], per_task=2, randomize=False)["session_id"]
    b = runner.create_session(samples_root, lab_id="l", platform_id="p", operator_name="Bruno", date="2026-09-19",
                              attachment_ids=["valve_ball", "lock"], per_task=1, randomize=False)["session_id"]
    ok = {"outcome": "success", "completion_time_s": 5, "strategy": "prehensile"}
    runner.record_trial(samples_root, a, "1", ok)                               # trial id 1 in both plans
    runner.record_trial(samples_root, b, "1", ok)
    runner.record_trial(samples_root, b, "2", {**ok, "stage_reached": 3})
    data = _client(samples_root).get("/api/statistics").json()
    assert data["summary"]["total_trials"] == 3                                 # not merged by trial id
    by = {p["session_id"]: p for p in data["plans"]}
    assert [p["session_id"] for p in data["plans"]] == [b, a]                   # most recent first
    assert by[a]["summary"]["total_trials"] == 1 and by[a]["summary"]["total_target_trials"] == 2
    assert by[b]["summary"]["total_trials"] == 2 and by[b]["summary"]["conditions_total"] == 2
    assert by[b]["operator_name"] == "Bruno" and by[b]["mode"] == "manual" and by[b]["has_plan"] is True
    assert [c["id"] for c in by[b]["conditions"]] == ["valve_ball", "lock"]
    assert {c["id"]: c["target"] for c in by[a]["conditions"]} == {"valve_ball": 2}
    labels = [c["label"] for c in by[b]["readiness"]["checks"]]
    assert "All 2 conditions of this plan recorded" in labels and "All 2 planned trials recorded" in labels
    assert by[a]["readiness"]["is_complete"] is False and all(t["session_id"] == a for t in by[a]["trials"])


def test_statistics_view_has_plan_tabs(samples_root):
    js = _client(samples_root).get("/app.js").text
    for marker in ("stats-plan-tab", "let statsPlan", "renderStatsView", "All plans", "showPlanColumn"):
        assert marker in js
    assert ".stats-plan-tab.active" in _client(samples_root).get("/style.css").text


def test_statistics_panel_refreshes_while_open(samples_root):
    js = _client(samples_root).get("/app.js").text
    body = js[js.index("async function refreshStats"):js.index("window.openStatsOverlay")]
    for marker in ("setInterval", "2500", "key === statsLastKey", "scroller.scrollTop = top", "statsOverlay.classList.contains(\"hidden\")"):
        assert marker in body


def test_deleting_an_episode_removes_its_trial_row_and_it_leaves_the_statistics(samples_root):
    profile = _fill_profile(samples_root)
    make_episode(samples_root, "session_001", "ep1", trial_id="t1", profile=profile)
    annotate_episode(samples_root, "ep1", {"attachment_id": "valve_ball", "outcome": "success", "strategy": "prehensile",
                                           "operator_name": "Op", "annotator_name": "Ann"}, validate_after=False)
    client = _client(samples_root)
    assert client.get("/api/statistics").json()["summary"]["total_trials"] == 1
    assert read_rows(trials_csv_path(samples_root, "session_001"))
    assert client.delete("/api/episodes/ep1?remote=false").status_code == 200
    assert read_rows(trials_csv_path(samples_root, "session_001")) == []
    stats = client.get("/api/statistics").json()
    assert stats["summary"]["total_trials"] == 0 and stats["plans"] == []


def test_statistics_ignore_leftover_rows_of_a_session_without_episodes_or_plan(samples_root):
    from datahive.trials import upsert_row
    from datahive.schema import TrialAnnotation
    ann = TrialAnnotation.model_validate(dict(trial_id="old", lab_id="l", platform_id="p", attachment_id="valve_ball",
                                              date="2026-01-01", outcome="success", completion_time_s=3, strategy="prehensile"))
    (samples_root / "session_001").mkdir()
    upsert_row(trials_csv_path(samples_root, "session_001"), ann)               # a row whose episode was removed by hand
    stats = _client(samples_root).get("/api/statistics").json()
    assert stats["summary"]["total_trials"] == 0 and stats["plans"] == []


def test_stage_select_is_required_only_for_tasks_with_stages(samples_root):
    js = _client(samples_root).get("/app.js").text
    body = js[js.index("function stageFieldHtml"):js.index("function completionSourceHtml") if js.index("function completionSourceHtml") > js.index("function stageFieldHtml") else None]
    assert 'needed ? "required" : "disabled"' in js and "isComposedTask(info, taskId)" in js
    assert 'form.addEventListener("invalid"' in js and "needs a value." in js


def test_annotation_form_only_sends_the_fields_that_apply_to_the_outcome(samples_root):
    js = _client(samples_root).get("/app.js").text
    body = js[js.index("function collectAnnotationPayload"):js.index('form.addEventListener("invalid"')]
    for marker in ('payload.outcome === "success"', "payload.failure_cause = null;", "payload.severity = null;", "payload.completion_time_s = null;"):
        assert marker in body


def test_dark_theme_has_layered_surfaces_and_readable_buttons(samples_root):
    css = _client(samples_root).get("/style.css").text
    dark = css[css.index(':root[data-theme="dark"] {'):css.index("* { box-sizing")]
    for token in ("--canvas: #0d1117", "--bg: #161b22", "--input-bg: #0d1117", "--accent-solid: #2c5aa0", "--seg-active-bg: #2c5aa0", "--danger: #f85149"):
        assert token in dark, token
    assert "body { background: var(--canvas); }" in css
    assert ".card, .rn-card" in css and "background-color: var(--bg)" in css
    assert "button.rn-cta, button.rn-cta:hover" in css and "background: var(--accent-solid)" in css
    assert ".badge.validated { color: var(--accent);" in css


def test_validate_button_shows_the_episode_state(samples_root):
    js = _client(samples_root).get("/app.js").text
    for marker in ('class="btn-icon btn-validate ${validateState}"', "function validateStateOf", "is-valid", "is-invalid", "is-busy", 'id="vdLabel"', 'setState(result.ok ? "is-valid" : "is-invalid")'):
        assert marker in js
    assert ".btn-validate.is-valid" in _client(samples_root).get("/style.css").text
    # Delete on the left, Save as the main (solid) action.
    actions = js[js.index('<div class="actions">\n          <button type="button" id="deleteBtn"'):js.index('id="statusMsg"')]
    assert actions.index('id="deleteBtn"') < actions.index('id="validateBtn"') < actions.index('class="btn-icon solid"') < actions.index('id="uploadBtn"')


def test_runner_always_opens_on_the_plans_list_unless_a_trial_is_running(samples_root):
    js = _client(samples_root).get("/runner.js").text
    body = js[js.index('window.addEventListener("viewchange", async (e) => {\n  if (e.detail.view !== "runner")'):]
    body = body[:body.index("try {")]
    assert 'rn.step = "setup"' in body and 'rn.session = null' in body
    assert '["countdown", "running"].includes(rn.timer.state)' in body and '["pending", "running"].includes(auto.state.status)' in body


def test_validate_button_has_the_same_shape_as_the_other_buttons(samples_root):
    client = _client(samples_root)
    js, css = client.get("/app.js").text, client.get("/style.css").text
    assert 'class="btn-icon btn-validate ${validateState}"' in js and "vd-icon" not in js
    assert "border-radius: 999px" not in css[css.index(".btn-validate.is-valid"):css.index("@keyframes vdspin")]


def test_dark_theme_blue_is_calm_and_the_send_button_stacks_its_hint(samples_root):
    css = _client(samples_root).get("/style.css").text
    assert "#1f6feb" not in css                                          # the vivid blue is gone everywhere
    assert "button.rn-go.auto-send { flex-direction: column;" in css


def test_cartesian_path_only_for_cartesian_position(samples_root):
    profile = _fill_profile(samples_root)
    make_episode(samples_root, "sess1", "ep1", trial_id="t1", profile=profile, n_points=50)
    client = _client(samples_root)
    assert client.get("/api/episodes/ep1/cartesian-path").json() == {"points": [], "n": 0}

    import dataclasses
    profile = dataclasses.replace(profile, action_space=["cartesian_position", "gripper_binary"])
    make_episode(samples_root, "sess1", "ep2", trial_id="t2", profile=profile, n_points=50)
    data = client.get("/api/episodes/ep2/cartesian-path").json()
    assert data["n"] == 50 and len(data["points"][0]) == 3
