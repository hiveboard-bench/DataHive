from __future__ import annotations

from datetime import date

from datahive.schema import TRIAL_COLUMNS, TrialAnnotation
from datahive.trials import get_row, merge_rows, read_rows, upsert_row


def _ann(trial_id, **overrides):
    data = dict(
        trial_id=trial_id, lab_id="lab_test", platform_id="rig-01", attachment_id="peg_round",
        date=date.today().isoformat(), outcome="success", completion_time_s=5.0, strategy="prehensile",
    )
    data.update(overrides)
    return TrialAnnotation.model_validate(data)


def test_column_order_and_count_preserved(tmp_path):
    csv_path = tmp_path / "trials.csv"
    upsert_row(csv_path, _ann("t1"))
    text = csv_path.read_text().splitlines()[0]
    assert text.split(",") == list(TRIAL_COLUMNS)


def test_blank_vs_none_representation(tmp_path):
    csv_path = tmp_path / "trials.csv"
    upsert_row(csv_path, _ann("t1", outcome="fail", completion_time_s=None, failure_cause="slip"))
    row = get_row(csv_path, "t1")
    assert row["completion_time_s"] == ""
    assert row["failure_cause"] == "slip"


def test_upsert_replaces_matching_trial_leaves_siblings(tmp_path):
    csv_path = tmp_path / "trials.csv"
    upsert_row(csv_path, _ann("t1"))
    upsert_row(csv_path, _ann("t2"))
    upsert_row(csv_path, _ann("t1", notes="updated"))

    rows = read_rows(csv_path)
    assert len(rows) == 2
    t1 = get_row(csv_path, "t1")
    assert t1["notes"] == "updated"
    t2 = get_row(csv_path, "t2")
    assert t2["notes"] == ""


def test_merge_rows_local_wins():
    remote = [{"trial_id": "t1", "notes": "remote"}, {"trial_id": "t2", "notes": "remote-only"}]
    local = [{"trial_id": "t1", "notes": "local"}]
    merged = merge_rows(local, remote)
    by_id = {r["trial_id"]: r for r in merged}
    assert by_id["t1"]["notes"] == "local"
    assert by_id["t2"]["notes"] == "remote-only"
