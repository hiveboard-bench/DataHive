from __future__ import annotations

import json
from pathlib import Path
import numpy as np
import yaml
from typer.testing import CliRunner

from datahive.check import check_episode, check_all_episodes
from datahive.cli import app
from datahive.profile import load_profile, write_profile_skeleton
from conftest import make_episode

runner = CliRunner()


def _fill_profile(samples_root):
    path = write_profile_skeleton(samples_root)
    data = yaml.safe_load(path.read_text())
    data["manipulator"] = {"model": "TestArm", "dof": 6, "joint_names": ["j1", "j2", "j3", "j4", "j5", "j6"]}
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


def test_check_episode_passing(samples_root):
    profile = _fill_profile(samples_root)
    make_episode(samples_root, "sess1", "ep1", profile=profile, rate_hz=100.0, n_points=150)
    
    report = check_episode(samples_root, "ep1")
    assert report["ok"] is True
    assert report["episode_id"] == "ep1"
    assert report["problems"] == []
    assert report["stats"]["n_steps"] == 150
    assert report["stats"]["sample_rate_hz"] is not None
    assert 99.0 <= report["stats"]["sample_rate_hz"] <= 101.0
    assert "external" in report["stats"]["cameras"]


def test_check_episode_missing_video(samples_root):
    profile = _fill_profile(samples_root)
    make_episode(samples_root, "sess1", "ep1", profile=profile, with_video=False)
    
    report = check_episode(samples_root, "ep1")
    assert report["ok"] is False
    assert any("external" in p and "video" in p.lower() for p in report["problems"])


def test_check_episode_low_sample_rate(samples_root):
    profile = _fill_profile(samples_root)
    make_episode(samples_root, "sess1", "ep1", profile=profile, rate_hz=50.0, n_points=100)
    
    report = check_episode(samples_root, "ep1")
    assert report["ok"] is False
    assert any("50.0" in p and "100" in p for p in report["problems"])


def test_check_episode_timestamp_gap(samples_root):
    from datahive.episode import EpisodeWriter

    profile = _fill_profile(samples_root)
    writer = EpisodeWriter(
        samples_root, "sess1", "ep1",
        trial_id="trial-1", task_ids=["insert_peg"], lab_id="lab_test", profile=profile,
    )
    prov = {
        "joint_position": "measured", "joint_velocity": "measured",
        "joint_torque_or_current": "measured", "ee_pose": "estimated", "ee_state": "measured"
    }
    # create timestamps with an abnormal gap
    timestamps = [0.0, 0.01, 0.02, 0.50, 0.51, 0.52]  # large gap from 0.02 to 0.50
    for t in timestamps:
        writer.append_proprioception(
            timestamp=t,
            joint_position=np.zeros(6),
            joint_velocity=np.zeros(6),
            joint_torque_or_current=np.zeros(6),
            ee_pose=np.zeros(7),
            ee_state=np.array([1.0]),
            provenance=prov,
        )
        writer.append_command(timestamp=t, target=np.zeros(6), control_mode="joint_position")
    
    stub = samples_root / "_stub.mp4"
    stub.write_bytes(b"\x00" * 64)
    writer.attach_video("external", stub, move=True)
    writer.close()

    report = check_episode(samples_root, "ep1")
    assert report["ok"] is False
    assert any("gap" in p.lower() or "dropout" in p.lower() for p in report["problems"])


def test_check_episode_nonexistent(samples_root):
    report = check_episode(samples_root, "ep_does_not_exist")
    assert report["ok"] is False
    assert any("not found" in p.lower() for p in report["problems"])


def test_check_all_episodes(samples_root):
    profile = _fill_profile(samples_root)
    make_episode(samples_root, "sess1", "ep1", profile=profile, rate_hz=100.0)
    make_episode(samples_root, "sess1", "ep2", profile=profile, rate_hz=50.0)

    reports = check_all_episodes(samples_root)
    assert len(reports) == 2
    r_map = {r["episode_id"]: r for r in reports}
    assert r_map["ep1"]["ok"] is True
    assert r_map["ep2"]["ok"] is False


def test_cli_check_single_episode(samples_root):
    profile = _fill_profile(samples_root)
    make_episode(samples_root, "sess1", "ep1", profile=profile, rate_hz=100.0)

    res = runner.invoke(app, ["check", "ep1", "--samples", str(samples_root)])
    assert res.exit_code == 0
    assert "ep1" in res.output
    assert "PASS" in res.output


def test_cli_check_failing_episode(samples_root):
    profile = _fill_profile(samples_root)
    make_episode(samples_root, "sess1", "ep1", profile=profile, rate_hz=50.0)

    res = runner.invoke(app, ["check", "ep1", "--samples", str(samples_root)])
    assert res.exit_code == 1
    assert "FAIL" in res.output
    assert "100" in res.output


def test_cli_check_json(samples_root):
    profile = _fill_profile(samples_root)
    make_episode(samples_root, "sess1", "ep1", profile=profile, rate_hz=100.0)

    res = runner.invoke(app, ["check", "ep1", "--samples", str(samples_root), "--json"])
    assert res.exit_code == 0
    data = json.loads(res.output)
    assert data["episode_id"] == "ep1"
    assert data["ok"] is True

    # When all episodes checked
    res_all = runner.invoke(app, ["check", "--samples", str(samples_root), "--json"])
    assert res_all.exit_code == 0
    data_all = json.loads(res_all.output)
    assert isinstance(data_all, list)
    assert len(data_all) == 1


def test_check_catches_what_validate_would(samples_root):
    """check runs the same recording rules as validate (minus annotation)."""
    import h5py
    import numpy as np

    profile = _fill_profile(samples_root)
    make_episode(samples_root, "sess1", "short", profile=profile, rate_hz=100.0, n_points=50)
    report = check_episode(samples_root, "short")
    assert report["ok"] is False
    assert any("must be between 1 and 600" in p for p in report["problems"])

    h5 = make_episode(samples_root, "sess1", "nan", profile=profile, rate_hz=100.0, n_points=150)
    with h5py.File(h5, "r+") as f:
        f["proprioception/joint_position"][3] = np.nan
    report = check_episode(samples_root, "nan")
    assert report["ok"] is False
    assert any("NaN" in p for p in report["problems"])
