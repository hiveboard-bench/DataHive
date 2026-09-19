from __future__ import annotations

import csv

import h5py
import numpy as np
import pytest

from datahive.annotate import annotate_episode
from datahive.episode import read_header
from datahive.errors import ValidationError
from datahive.paths import resolve_episode_paths
from datahive.schema import ANNOTATION_SCHEMA_CURRENT, EPISODE_SCHEMA_CURRENT, TrialAnnotation
from datahive.trials import read_rows
from datahive.validate import validate_episode

from conftest import make_episode, write_test_video

cv2 = pytest.importorskip("cv2")

OK = dict(attachment_id="valve_ball", outcome="success", strategy="prehensile",
          operator_name="Op", annotator_name="Ann")


def _ready(samples_root, filled_profile, **kw):
    make_episode(samples_root, "s", "ep1", trial_id="t1", profile=filled_profile, **kw)
    annotate_episode(samples_root, "ep1", dict(OK), validate_after=False)


def _problems(samples_root):
    with pytest.raises(ValidationError) as exc:
        validate_episode(samples_root, "ep1", update_index=False)
    return "\n".join(exc.value.problems)


def test_valid_episode_passes(samples_root, filled_profile):
    _ready(samples_root, filled_profile)
    assert validate_episode(samples_root, "ep1", update_index=False) == []


def test_new_records_are_stamped_with_schema_versions(samples_root, filled_profile):
    _ready(samples_root, filled_profile)
    paths = resolve_episode_paths(samples_root, "ep1")
    assert read_header(paths.h5).schema_version == EPISODE_SCHEMA_CURRENT
    row = read_rows(paths.trials_csv)[0]
    assert row["schema_version"] == ANNOTATION_SCHEMA_CURRENT
    assert row["annotated_at"]


def test_legacy_row_without_version_is_upcast_with_warning(samples_root, filled_profile):
    _ready(samples_root, filled_profile)
    paths = resolve_episode_paths(samples_root, "ep1")
    rows = read_rows(paths.trials_csv)
    for col in ("schema_version", "annotated_at"):
        rows[0].pop(col)
    with open(paths.trials_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    warnings = validate_episode(samples_root, "ep1", update_index=False)
    assert any("predates schema versioning" in w for w in warnings)


def test_unknown_annotation_schema_is_rejected():
    with pytest.raises(Exception, match="unsupported annotation schema_version"):
        TrialAnnotation.model_validate(dict(
            trial_id="t", lab_id="l", platform_id="p", attachment_id="valve_ball",
            date="2026-01-01", outcome="success", completion_time_s=2, strategy="prehensile",
            schema_version="datahive_trial_v99"))


def test_unknown_episode_schema_is_rejected(samples_root, filled_profile):
    _ready(samples_root, filled_profile)
    with h5py.File(resolve_episode_paths(samples_root, "ep1").h5, "r+") as f:
        f.attrs["schema_version"] = "datahive_episode_v99"
    assert "Unsupported episode schema" in _problems(samples_root)


def test_missing_operator_and_annotator_names(samples_root, filled_profile):
    make_episode(samples_root, "s", "ep1", trial_id="t1", profile=filled_profile)
    annotate_episode(samples_root, "ep1", {**OK, "operator_name": "", "annotator_name": ""}, validate_after=False)
    msg = _problems(samples_root)
    assert "operator_name is required" in msg and "annotator_name is required" in msg


def test_placeholder_lab_id(samples_root, filled_profile):
    make_episode(samples_root, "s", "ep1", trial_id="t1", profile=filled_profile, lab_id="your_lab_id")
    annotate_episode(samples_root, "ep1", dict(OK), validate_after=False)
    assert "placeholder" in _problems(samples_root)


def test_duration_too_short(samples_root, filled_profile):
    _ready(samples_root, filled_profile, n_points=50)
    assert "must be between 1 and 600" in _problems(samples_root)


def test_nan_in_trajectory(samples_root, filled_profile):
    _ready(samples_root, filled_profile)
    with h5py.File(resolve_episode_paths(samples_root, "ep1").h5, "r+") as f:
        f["proprioception/joint_position"][3, 0] = np.nan
    assert "NaN or infinite" in _problems(samples_root)


def test_arrays_must_share_length(samples_root, filled_profile):
    _ready(samples_root, filled_profile)
    with h5py.File(resolve_episode_paths(samples_root, "ep1").h5, "r+") as f:
        f["commands/target"].resize(150, axis=0)
    assert "same number of steps" in _problems(samples_root)


def test_joint_dof_must_match_names(samples_root, filled_profile):
    _ready(samples_root, filled_profile, n_joints=5)
    assert "5 DOF but the header lists 6" in _problems(samples_root)


def test_missing_video_is_a_problem(samples_root, filled_profile):
    _ready(samples_root, filled_profile)
    paths = resolve_episode_paths(samples_root, "ep1")
    next(paths.h5.parent.glob("*.mp4")).unlink()
    assert "missing video file" in _problems(samples_root)


def test_video_resolution_bounds_and_declared_size(samples_root, filled_profile):
    _ready(samples_root, filled_profile)
    vid = next(resolve_episode_paths(samples_root, "ep1").h5.parent.glob("*.mp4"))
    write_test_video(vid, seconds=1.99, width=160, height=120)
    msg = _problems(samples_root)
    assert "each side must be between 180 and 1280" in msg
    assert "header records 1280x720" in msg


def test_video_duration_must_match_episode(samples_root, filled_profile):
    _ready(samples_root, filled_profile)
    vid = next(resolve_episode_paths(samples_root, "ep1").h5.parent.glob("*.mp4"))
    write_test_video(vid, seconds=5)
    assert "video lasts" in _problems(samples_root)


def test_video_path_must_stay_inside_samples(samples_root, filled_profile):
    import json
    _ready(samples_root, filled_profile)
    with h5py.File(resolve_episode_paths(samples_root, "ep1").h5, "r+") as f:
        cams = json.loads(f.attrs["cameras"])
        cams[0]["file"] = "../../../etc/passwd"
        f.attrs["cameras"] = json.dumps(cams)
    assert "escapes the samples directory" in _problems(samples_root)


def test_success_longer_than_timeout(samples_root, filled_profile):
    _ready(samples_root, filled_profile, n_points=7001)  # 70 s > 60 s valve_ball timeout
    assert "exceeds the 60s timeout" in _problems(samples_root)


def test_completion_time_longer_than_recording(samples_root, filled_profile):
    make_episode(samples_root, "s", "ep1", trial_id="t1", profile=filled_profile)
    annotate_episode(samples_root, "ep1", {**OK, "completion_time_s": 30}, validate_after=False)
    assert "longer than the recording" in _problems(samples_root)


def test_attach_video_records_camera_facts_from_the_file(samples_root, filled_profile):
    make_episode(samples_root, "s", "ep1", trial_id="t1", profile=filled_profile)
    cam = read_header(resolve_episode_paths(samples_root, "ep1").h5).cameras[0]
    assert cam["resolution"] == "1280x720"
    assert abs(cam["fps"] - 30) < 0.5
    assert cam["encoding"]


def test_cameras_must_share_resolution_and_fps(samples_root, filled_profile):
    import json
    import shutil
    _ready(samples_root, filled_profile)
    paths = resolve_episode_paths(samples_root, "ep1")
    second = paths.h5.parent / "ep1_cam_wrist.mp4"
    write_test_video(second, seconds=1.99, width=640, height=480, fps=24)
    with h5py.File(paths.h5, "r+") as f:
        cams = json.loads(f.attrs["cameras"])
        cams.append({"name": "wrist", "file": second.name})
        f.attrs["cameras"] = json.dumps(cams)
    msg = _problems(samples_root)
    assert "same resolution" in msg and "same fps" in msg
