from __future__ import annotations

import yaml

from datahive import ops
from datahive.annotate import annotate_episode
from datahive.errors import ValidationError
from datahive.index import Index
from datahive.paths import profile_path
from datahive.profile import RobotProfile, load_profile, write_profile_skeleton
from datahive.validate import validate_episode

from conftest import make_episode


def _fill_profile(samples_root):
    path = write_profile_skeleton(samples_root)
    data = yaml.safe_load(path.read_text())
    data["manipulator"] = {"model": "TestArm", "dof": 6, "joint_names": [f"j{i}" for i in range(6)]}
    data["end_effector"] = {"type": "gripper", "actuated_dof": 1, "command_modality": "position"}
    data["low_level"] = {"mode": "stock", "controller_type": "pid", "rate_hz": 500, "gains": None}
    data["control_mode"] = "joint_position"
    data["cameras"] = [{"name": "external", "resolution": "1280x720", "encoding": "h264", "fps": 30}]
    data["board_mounting"] = "horizontal"
    data["hiveboard_version"] = "v2"
    data["platform_id"] = "rig-01"
    path.write_text(yaml.safe_dump(data))
    return load_profile(samples_root)


def test_full_lifecycle(samples_root, fake_hub):
    # 1. new-profile -> fill in.
    profile = _fill_profile(samples_root)

    # 2. build one fake episode folder.
    make_episode(samples_root, "sess1", "ep1", trial_id="trial-1", profile=profile)

    # 3. validate() fails first: no annotation yet.
    try:
        validate_episode(samples_root, "ep1")
        assert False, "expected ValidationError before annotation"
    except ValidationError as e:
        assert any("annotate" in p.lower() for p in e.problems)

    with Index(samples_root) as idx:
        assert idx.get("ep1").status == "recorded"

    # 4. annotate() fills in a trials.csv row.
    annotate_episode(
        samples_root, "ep1",
        {
            "attachment_id": "valve_ball", "outcome": "success", "completion_time_s": 9.0,
            "n_attempts": 1, "n_regrasps": 0, "strategy": "prehensile",
        },
    )

    # validate() now passes.
    validate_episode(samples_root, "ep1")
    with Index(samples_root) as idx:
        assert idx.get("ep1").status == "validated"

    # 5. upload() (mock, capture args).
    result = ops.upload_episode(samples_root, "ep1", hub=fake_hub)
    assert result.uploaded is True
    assert any("ep1.h5" in p for p in result.remote_paths)
    assert any("ep1.h5" in p for p in fake_hub._fake_api.uploaded_files)

    with Index(samples_root) as idx:
        assert idx.get("ep1").status == "uploaded"

    # 6. sync() is idempotent -- running it twice in a row only calls the
    # mock once (no new upload_file calls on the second pass).
    n_calls_before = len(fake_hub._fake_api.upload_calls)
    ops.sync(samples_root, hub=fake_hub)
    ops.sync(samples_root, hub=fake_hub)
    assert len(fake_hub._fake_api.upload_calls) == n_calls_before

    # 7. delete() removes local files and calls the mock delete.
    h5_path = samples_root / "sess1" / "episodes" / "ep1.h5"
    assert h5_path.exists()
    delete_result = ops.delete_episode(samples_root, "ep1", hub=fake_hub)
    assert delete_result.deleted_local is True
    assert delete_result.deleted_remote is True
    assert not h5_path.exists()
    assert len(fake_hub._fake_api.delete_calls) == 1
    assert any("ep1.h5" in p for p in fake_hub._fake_api.delete_calls[0])

    with Index(samples_root) as idx:
        assert idx.get("ep1") is None


def test_validate_rejects_episode_with_mismatched_cameras(samples_root):
    """Defense in depth: even if an episode's own header snapshot ended up
    with mismatched camera resolutions/fps (e.g. it was recorded before the
    profile was corrected), validate_episode() must still catch it from the
    episode's own header -- not just from loading the current profile."""
    # A valid, consistent profile is on disk (what a lab would normally
    # have after fixing a mismatch)...
    _fill_profile(samples_root)
    # ...but this particular episode was snapshotted under different,
    # mismatched camera settings (simulating "recorded before the fix").
    stale_profile = RobotProfile.from_dict(
        {
            "manipulator": {"model": "TestArm", "dof": 6, "joint_names": ["j1"]},
            "low_level": {"mode": "stock"},
            "cameras": [
                {"name": "external", "resolution": "1280x720", "fps": 30},
                {"name": "wrist", "resolution": "640x480", "fps": 30},
            ],
        }
    )
    make_episode(samples_root, "sess1", "ep1", trial_id="t1", profile=stale_profile)
    from datahive.annotate import annotate_episode as _annotate

    _annotate(
        samples_root, "ep1",
        {"attachment_id": "valve_ball", "outcome": "success", "n_attempts": 1, "n_regrasps": 0, "strategy": "prehensile"},
        validate_after=False,
    )

    try:
        validate_episode(samples_root, "ep1")
        assert False, "expected ValidationError for mismatched camera resolution"
    except ValidationError as e:
        assert any("resolution" in p for p in e.problems)


def test_cli_end_to_end(tmp_path, monkeypatch):
    """Drives the same lifecycle through the actual CLI surface."""
    from typer.testing import CliRunner

    from datahive.cli import app

    runner = CliRunner()
    samples = tmp_path / "samples"

    result = runner.invoke(app, ["new-profile", "--samples", str(samples)])
    assert result.exit_code == 0, result.output

    path = profile_path(samples)
    data = yaml.safe_load(path.read_text())
    data["manipulator"] = {"model": "TestArm", "dof": 6, "joint_names": ["j1"]}
    data["low_level"] = {"mode": "stock"}
    data["cameras"] = [{"name": "external"}]
    path.write_text(yaml.safe_dump(data))
    profile = load_profile(samples)

    make_episode(samples, "sess1", "ep1", trial_id="trial-1", profile=profile)

    result = runner.invoke(app, ["validate", "ep1", "--samples", str(samples)])
    assert result.exit_code != 0  # no annotation yet

    result = runner.invoke(
        app,
        [
            "annotate", "ep1", "--samples", str(samples), "--non-interactive",
            "--attachment-id", "valve_ball", "--outcome", "success",
            "--completion-time-s", "3.2", "--strategy", "prehensile",
            "--n-attempts", "1", "--n-regrasps", "0",
        ],
    )
    assert result.exit_code == 0, result.output

    result = runner.invoke(app, ["validate", "ep1", "--samples", str(samples)])
    assert result.exit_code == 0, result.output

    result = runner.invoke(app, ["list", "--samples", str(samples)])
    assert "ep1" in result.output
    assert "validated" in result.output
