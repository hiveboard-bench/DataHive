from __future__ import annotations

from datahive import ops
from datahive.errors import HubError
from datahive.hub import Hub
from datahive.index import Index

from conftest import make_episode, write_valid_annotation


def _validated_episode(samples_root, filled_profile, session_id="sess1", episode_id="ep1", trial_id="trial-1"):
    make_episode(samples_root, session_id, episode_id, trial_id=trial_id, profile=filled_profile)
    write_valid_annotation(samples_root, session_id, trial_id)
    from datahive.validate import validate_episode

    validate_episode(samples_root, episode_id)


def test_new_episode_folder_picked_up_as_recorded(samples_root, filled_profile):
    make_episode(samples_root, "sess1", "ep1", profile=filled_profile)
    with Index(samples_root) as idx:
        added = idx.scan()
        assert added == ["ep1"]
        rec = idx.get("ep1")
        assert rec.status == "recorded"


def test_sync_uploads_only_validated_not_yet_uploaded(samples_root, filled_profile, fake_hub):
    _validated_episode(samples_root, filled_profile, episode_id="ep1")
    make_episode(samples_root, "sess1", "ep2", trial_id="trial-2", profile=filled_profile)  # left 'recorded'

    report = ops.sync(samples_root, hub=fake_hub)

    assert report.uploaded == ["ep1"]
    assert any("ep1" in c["path_in_repo"] for c in fake_hub._fake_api.upload_calls)
    assert not any("ep2" in c["path_in_repo"] for c in fake_hub._fake_api.upload_calls)

    with Index(samples_root) as idx:
        assert idx.get("ep1").status == "uploaded"
        assert idx.get("ep2").status == "recorded"


def test_second_sync_with_no_changes_makes_no_new_upload_calls(samples_root, filled_profile, fake_hub):
    _validated_episode(samples_root, filled_profile, episode_id="ep1")
    ops.sync(samples_root, hub=fake_hub)
    n_calls_after_first = len(fake_hub._fake_api.upload_calls)
    assert n_calls_after_first > 0

    report2 = ops.sync(samples_root, hub=fake_hub)
    assert report2.uploaded == []
    assert len(fake_hub._fake_api.upload_calls) == n_calls_after_first


def test_touching_h5_reenables_upload(samples_root, filled_profile, fake_hub):
    _validated_episode(samples_root, filled_profile, episode_id="ep1")
    ops.sync(samples_root, hub=fake_hub)
    n_calls = len(fake_hub._fake_api.upload_calls)

    from datahive.trials import upsert_row
    from datahive.paths import trials_csv_path
    from conftest import write_valid_annotation

    write_valid_annotation(samples_root, "sess1", "trial-1", outcome="fail", failure_cause="slip")

    report2 = ops.sync(samples_root, hub=fake_hub)
    assert "ep1" in report2.uploaded
    assert len(fake_hub._fake_api.upload_calls) > n_calls


def test_remote_only_file_detected_not_downloaded(samples_root, filled_profile, fake_hub):
    fake_hub._fake_api.uploaded_files["other_session/episodes/ghost_ep.h5"] = b"x"

    report = ops.sync(samples_root, hub=fake_hub)
    assert report.remote_only == ["ghost_ep"]

def test_upload_failure_marks_upload_failed_and_retries_next_sync(samples_root, filled_profile, fake_hub, monkeypatch):
    _validated_episode(samples_root, filled_profile, episode_id="ep1")

    original_upload_episode = fake_hub.upload_episode

    def boom(*args, **kwargs):
        raise HubError("simulated failure")

    fake_hub.upload_episode = boom
    report = ops.sync(samples_root, hub=fake_hub)
    assert report.upload_failed == ["ep1"]
    with Index(samples_root) as idx:
        assert idx.get("ep1").status == "upload_failed"

    fake_hub.upload_episode = original_upload_episode
    report2 = ops.sync(samples_root, hub=fake_hub)
    assert "ep1" in report2.uploaded


def test_cli_sync_dry_run_lists_what_would_be_uploaded(samples_root, filled_profile, fake_hub, monkeypatch):
    from typer.testing import CliRunner

    from datahive.cli import app

    _validated_episode(samples_root, filled_profile, episode_id="ep1")
    monkeypatch.setattr(ops, "_get_hub", lambda hub=None: fake_hub)

    res = CliRunner().invoke(app, ["sync", "--samples", str(samples_root), "--dry-run"])
    assert res.exit_code == 0, res.output
    assert "Would upload: ep1" in res.output
    assert not fake_hub._fake_api.upload_calls
