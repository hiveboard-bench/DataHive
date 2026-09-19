from __future__ import annotations

from datetime import datetime

import pytest

from datahive import hf_limits, ops
from datahive.annotate import annotate_episode, read_h5_annotations
from datahive.config import to_utc_iso
from datahive.index import Index
from datahive.paths import resolve_episode_paths
from datahive.preflight import diversity_warnings, upload_preflight
from datahive.schema import ANNOTATION_SCHEMA_CURRENT
from datahive.trials import get_row, read_rows
from datahive.validate import validate_episode

from conftest import make_episode

pytest.importorskip("cv2")

OK = dict(attachment_id="valve_ball", outcome="success", strategy="prehensile",
          operator_name="Op", annotator_name="Ann")


def _ready(samples_root, profile, eid="ep1", session="s"):
    make_episode(samples_root, session, eid, trial_id=f"t-{eid}", profile=profile)
    annotate_episode(samples_root, eid, dict(OK))


def test_to_utc_iso_normalises():
    assert to_utc_iso("2026-01-01T10:00:00+02:00") == "2026-01-01T08:00:00+00:00"
    assert to_utc_iso(datetime(2026, 1, 1, 8)) == "2026-01-01T08:00:00+00:00"
    assert to_utc_iso("2026-01-01T08:00:00Z") == "2026-01-01T08:00:00+00:00"


def test_timestamps_are_utc(samples_root, filled_profile):
    _ready(samples_root, filled_profile)
    paths = resolve_episode_paths(samples_root, "ep1")
    row = get_row(paths.trials_csv, "t-ep1")
    assert row["annotated_at"].endswith("+00:00")
    with Index(samples_root) as idx:
        idx.scan()
        assert idx.get("ep1").created_at.endswith("+00:00")


def test_annotation_stored_in_h5_with_schema_version(samples_root, filled_profile):
    _ready(samples_root, filled_profile)
    stored = read_h5_annotations(resolve_episode_paths(samples_root, "ep1").h5)
    assert stored["Ann"]["schema_version"] == ANNOTATION_SCHEMA_CURRENT
    assert stored["Ann"]["outcome"] == "success"


def test_h5_annotation_out_of_sync_is_flagged(samples_root, filled_profile):
    from datahive.trials import set_fields
    _ready(samples_root, filled_profile)
    paths = resolve_episode_paths(samples_root, "ep1")
    set_fields(paths.trials_csv, "t-ep1", outcome="fail")
    with pytest.raises(Exception) as exc:
        validate_episode(samples_root, "ep1", update_index=False)
    assert "disagrees with trials.csv" in str(exc.value)


def test_upload_stamps_uploaded_at_and_stays_unchanged(samples_root, filled_profile, fake_hub):
    _ready(samples_root, filled_profile)
    assert ops.upload_episode(samples_root, "ep1", hub=fake_hub).uploaded
    row = get_row(resolve_episode_paths(samples_root, "ep1").trials_csv, "t-ep1")
    assert row["uploaded_at"].endswith("+00:00")
    again = ops.upload_episode(samples_root, "ep1", hub=fake_hub)
    assert not again.uploaded and "unchanged" in again.skipped_reason


def test_directory_guard_blocks_upload_and_suggests_split(samples_root, filled_profile, fake_hub, monkeypatch):
    _ready(samples_root, filled_profile)
    monkeypatch.setattr(hf_limits, "HF_DIR_FILE_LIMIT", 1)
    result = ops.upload_episode(samples_root, "ep1", hub=fake_hub)
    assert not result.uploaded
    assert "over the HuggingFace limit" in result.error and "part2" in result.error


def test_directory_warning_near_limit(samples_root, filled_profile, monkeypatch):
    _ready(samples_root, filled_profile)
    problems, warnings = hf_limits.directory_problems(samples_root, {"s"}, limit=2)
    assert not problems and warnings  # 2 files (h5 + mp4) of 2 allowed


def test_directory_guard_counts_remote_files(samples_root, filled_profile):
    _ready(samples_root, filled_profile)
    remote = [f"s/episodes/other_{i}.h5" for i in range(5)]
    problems, _ = hf_limits.directory_problems(samples_root, {"s"}, remote_files=remote, limit=5)
    assert problems


def test_preflight_summary(samples_root, filled_profile, fake_hub):
    _ready(samples_root, filled_profile, "ep1")
    make_episode(samples_root, "s", "ep2", trial_id="t-ep2", profile=filled_profile)  # unannotated
    summary = upload_preflight(samples_root, ["ep1", "ep2"], hub=fake_hub)
    assert summary["n_episodes"] == 2
    assert summary["n_unannotated"] == 1
    assert summary["n_invalid"] == 1
    assert summary["total_bytes"] > 0
    assert summary["ok"] is False


def test_diversity_flags_copy_pasted_notes_and_uniform_causes():
    rows = [{"outcome": "fail", "failure_cause": "slip", "notes": "Gripper slipped", "failure_cause_detail": ""}
            for _ in range(6)]
    msgs = diversity_warnings(rows)
    assert any("identical" in m for m in msgs)
    assert any("same failure_cause" in m for m in msgs)
    assert diversity_warnings(rows[:3]) == []


def test_preflight_api(samples_root, filled_profile):
    from fastapi.testclient import TestClient
    from datahive.interface.app import create_app
    _ready(samples_root, filled_profile)
    client = TestClient(create_app(samples_root))
    body = client.post("/api/episodes/upload-preflight", json={"episode_ids": ["ep1"]}).json()
    assert body["n_episodes"] == 1 and body["ok"] is True
