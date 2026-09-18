"""trials.csv read/write: one row per trial, atomic writes, exact column
order/count as specified by the schema. A tiny lockfile guards against a
CLI `annotate` and a GUI POST racing on the same file."""

from __future__ import annotations

import csv
import os
import time
from pathlib import Path

from datahive.schema import TRIAL_COLUMNS, TrialAnnotation

_LOCK_TIMEOUT_S = 5.0
_LOCK_STALE_S = 30.0


class _FileLock:
    def __init__(self, target: Path):
        self.lockfile = target.with_suffix(target.suffix + ".lock")

    def __enter__(self):
        deadline = time.time() + _LOCK_TIMEOUT_S
        while True:
            try:
                fd = os.open(str(self.lockfile), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                return self
            except FileExistsError:
                try:
                    age = time.time() - self.lockfile.stat().st_mtime
                    if age > _LOCK_STALE_S:
                        self.lockfile.unlink(missing_ok=True)
                        continue
                except FileNotFoundError:
                    continue
                if time.time() > deadline:
                    raise TimeoutError(f"Timed out waiting for lock {self.lockfile}")
                time.sleep(0.05)

    def __exit__(self, exc_type, exc, tb):
        self.lockfile.unlink(missing_ok=True)


def read_rows(csv_path: Path) -> list[dict[str, str]]:
    if not csv_path.is_file():
        return []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return [dict(row) for row in reader]


def get_row(csv_path: Path, trial_id: str) -> dict[str, str] | None:
    for row in read_rows(csv_path):
        if row.get("trial_id") == trial_id:
            return row
    return None


def _write_rows(csv_path: Path, rows: list[dict[str, str]]) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = csv_path.with_suffix(csv_path.suffix + ".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(TRIAL_COLUMNS))
        writer.writeheader()
        for row in rows:
            writer.writerow({col: row.get(col, "") for col in TRIAL_COLUMNS})
    os.replace(tmp, csv_path)


def upsert_row(csv_path: Path, annotation: TrialAnnotation) -> None:
    with _FileLock(csv_path):
        rows = read_rows(csv_path)
        new_row = annotation.to_csv_row()
        found = False
        for i, row in enumerate(rows):
            if row.get("trial_id") == annotation.trial_id:
                rows[i] = new_row
                found = True
                break
        if not found:
            rows.append(new_row)
        _write_rows(csv_path, rows)


def merge_rows(
    local_rows: list[dict[str, str]], remote_rows: list[dict[str, str]]
) -> list[dict[str, str]]:
    """Merge two trials.csv row sets by trial_id, local wins on conflict.
    Used when uploading, to avoid two machines clobbering each other's rows
    for the same session (see plan assumption A6)."""
    by_id: dict[str, dict[str, str]] = {r["trial_id"]: r for r in remote_rows if r.get("trial_id")}
    for r in local_rows:
        if r.get("trial_id"):
            by_id[r["trial_id"]] = r
    return list(by_id.values())
