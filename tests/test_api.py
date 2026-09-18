from __future__ import annotations

import pytest
import yaml
from fastapi.testclient import TestClient

from datahive import ops
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
    data["manipulator"] = {"model": "TestArm", "dof": 6, "joint_names": ["j1"]}
    data["low_level"] = {"mode": "stock"}
    data["cameras"] = [{"name": "external"}]
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
            "attachment_id": "valve_ball", "outcome": "success", "completion_time_s": 4.0,
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
        {"attachment_id": "valve_ball", "outcome": "success", "completion_time_s": 7.0,
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
        "manipulator": {"model": "TestArm", "dof": 6, "joint_names": ["j1", "j2"]},
        "end_effector": {"type": "gripper", "actuated_dof": 1, "command_modality": "position"},
        "low_level": {"mode": "stock", "controller_type": "pid", "rate_hz": 500, "gains": None},
        "control_mode": "joint_position",
        "policy": None,
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
            "attachment_id": "valve_ball", "outcome": "success", "completion_time_s": 3.0,
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
