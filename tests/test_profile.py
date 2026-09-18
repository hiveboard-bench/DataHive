from __future__ import annotations

import yaml
import pytest

from datahive.errors import ProfileIncomplete, ProfileMissing
from datahive.episode import EpisodeWriter, read_header
from datahive.paths import profile_path
from datahive.profile import camera_consistency_problems, load_profile, write_profile_skeleton

from conftest import make_episode


def test_new_profile_writes_skeleton(samples_root):
    path = write_profile_skeleton(samples_root)
    assert path == profile_path(samples_root)
    data = yaml.safe_load(path.read_text())
    assert data["low_level"]["mode"] is None
    assert data["cameras"] == []
    assert data["manipulator"]["joint_names"] == []


def test_new_profile_refuses_to_clobber_without_force(samples_root):
    write_profile_skeleton(samples_root)
    with pytest.raises(FileExistsError):
        write_profile_skeleton(samples_root)
    write_profile_skeleton(samples_root, force=True)  # should not raise


def test_load_profile_missing_raises(samples_root):
    with pytest.raises(ProfileMissing):
        load_profile(samples_root)


def test_load_profile_incomplete_low_level_mode(samples_root):
    write_profile_skeleton(samples_root)
    with pytest.raises(ProfileIncomplete, match="low_level.mode"):
        load_profile(samples_root)


def test_load_profile_incomplete_joint_names(samples_root):
    path = write_profile_skeleton(samples_root)
    data = yaml.safe_load(path.read_text())
    data["low_level"]["mode"] = "stock"
    data["cameras"] = [{"name": "external"}]
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ProfileIncomplete, match="joint_names"):
        load_profile(samples_root)


def test_load_profile_incomplete_cameras(samples_root):
    path = write_profile_skeleton(samples_root)
    data = yaml.safe_load(path.read_text())
    data["low_level"]["mode"] = "stock"
    data["manipulator"]["joint_names"] = ["j1"]
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ProfileIncomplete, match="cameras"):
        load_profile(samples_root)


def test_camera_consistency_noop_for_fewer_than_two_cameras():
    assert camera_consistency_problems([]) == []
    assert camera_consistency_problems([{"name": "external", "resolution": "1280x720", "fps": 30}]) == []


def test_camera_consistency_detects_mismatched_resolution():
    cameras = [
        {"name": "external", "resolution": "1280x720", "fps": 30},
        {"name": "wrist", "resolution": "640x480", "fps": 30},
    ]
    problems = camera_consistency_problems(cameras)
    assert len(problems) == 1
    assert "resolution" in problems[0]


def test_camera_consistency_detects_mismatched_fps():
    cameras = [
        {"name": "external", "resolution": "1280x720", "fps": 30},
        {"name": "wrist", "resolution": "1280x720", "fps": 60},
    ]
    problems = camera_consistency_problems(cameras)
    assert len(problems) == 1
    assert "fps" in problems[0]


def test_camera_consistency_passes_when_matching():
    cameras = [
        {"name": "external", "resolution": "1280x720", "fps": 30},
        {"name": "wrist", "resolution": "1280x720", "fps": 30},
        {"name": "overhead", "resolution": "1280x720", "fps": 30},
    ]
    assert camera_consistency_problems(cameras) == []


def test_load_profile_rejects_mismatched_cameras(samples_root):
    path = write_profile_skeleton(samples_root)
    data = yaml.safe_load(path.read_text())
    data["low_level"]["mode"] = "stock"
    data["manipulator"]["joint_names"] = ["j1"]
    data["cameras"] = [
        {"name": "external", "resolution": "1280x720", "fps": 30},
        {"name": "wrist", "resolution": "640x480", "fps": 30},
    ]
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ProfileIncomplete, match="resolution"):
        load_profile(samples_root)


def test_episode_header_merges_profile_and_episode_fields(samples_root, filled_profile):
    h5_path = make_episode(samples_root, "sess1", "ep1", profile=filled_profile)
    header = read_header(h5_path)
    assert header.manipulator["model"] == "TestArm"
    assert header.low_level["mode"] == "stock"
    assert header.episode_id == "ep1"
    assert header.session_id == "sess1"
    assert header.trial_id == "trial-1"
    assert header.cameras[0]["name"] == "external"
    # attach_video() must write the actual filename back onto the matching
    # camera spec -- both in memory and in the persisted header attrs --
    # so the header stays self-describing (and validate.py's "referenced
    # video file exists" check has something real to check).
    assert header.cameras[0]["file"] == "ep1_cam_external.mp4"


def test_episode_keeps_profile_snapshot_after_profile_edited(samples_root, filled_profile):
    h5_path = make_episode(samples_root, "sess1", "ep1", profile=filled_profile)
    header_before = read_header(h5_path)
    assert header_before.manipulator["model"] == "TestArm"

    # Edit robot_profile.yaml after the fact -- the existing episode's
    # header must stay a byte-identical snapshot.
    path = profile_path(samples_root)
    data = yaml.safe_load(path.read_text())
    data["manipulator"]["model"] = "NewArmV2"
    path.write_text(yaml.safe_dump(data))

    header_after = read_header(h5_path)
    assert header_after.manipulator["model"] == "TestArm"

    # A newly written episode picks up the change.
    new_profile = load_profile(samples_root)
    h5_path2 = make_episode(samples_root, "sess1", "ep2", profile=new_profile)
    header2 = read_header(h5_path2)
    assert header2.manipulator["model"] == "NewArmV2"
