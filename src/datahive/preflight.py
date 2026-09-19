"""Pre-upload summary: what is about to go to the Hub, and anything worth a
second look. Shared by the CLI and the GUI."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from datahive.errors import DatahiveError
from datahive.hf_limits import directory_problems
from datahive.hub import Hub
from datahive.index import Index
from datahive.paths import resolve_episode_paths
from datahive.trials import get_row

MIN_EPISODES_FOR_DIVERSITY = 5
DUP_TEXT_FRACTION = 0.8


def diversity_warnings(rows: list[dict]) -> list[str]:
    """Attribute-only heuristics that catch copy-pasted annotations or an
    inattentive annotator. Advisory: they warn, they never block by default."""
    if len(rows) < MIN_EPISODES_FOR_DIVERSITY:
        return []
    out: list[str] = []
    texts = [
        " ".join(((r.get("failure_cause_detail") or "") + " " + (r.get("notes") or "")).lower().split())
        for r in rows
    ]
    filled = [t for t in texts if t]
    if len(filled) >= MIN_EPISODES_FOR_DIVERSITY:
        text, count = Counter(filled).most_common(1)[0]
        if count / len(filled) > DUP_TEXT_FRACTION:
            out.append(
                f"{count} of {len(filled)} written notes are identical (\"{text[:60]}\"); "
                "this looks copy-pasted."
            )
    failures = [r for r in rows if r.get("outcome") != "success"]
    if len(failures) >= MIN_EPISODES_FOR_DIVERSITY:
        cause, count = Counter(r.get("failure_cause") for r in failures).most_common(1)[0]
        if count == len(failures):
            out.append(f"All {count} failures share the same failure_cause '{cause}'.")
    return out


def upload_preflight(
    samples_root: Path, episode_ids: list[str], *, hub: Hub | None = None
) -> dict[str, Any]:
    from datahive.validate import validate_episode

    with Index(samples_root) as idx:
        idx.scan()
        records = {r.episode_id: r for r in idx.all()}

    episodes: list[dict[str, Any]] = []
    rows: list[dict] = []
    sessions: set[str] = set()
    total_bytes = 0
    n_unannotated = n_warnings = n_invalid = n_unchanged = 0

    for eid in episode_ids:
        rec = records.get(eid)
        if rec is None:
            episodes.append({"episode_id": eid, "problems": ["not found"], "warnings": []})
            n_invalid += 1
            continue
        sessions.add(rec.session_id)
        paths = resolve_episode_paths(samples_root, eid, rec.session_id)
        size = sum(p.stat().st_size for p in [paths.h5, *paths.videos.values()] if p.exists())
        total_bytes += size
        row = get_row(paths.trials_csv, rec.trial_id) if rec.trial_id else None
        if row is None:
            n_unannotated += 1
        else:
            rows.append(row)
        problems: list[str] = []
        warnings: list[str] = []
        try:
            warnings = validate_episode(samples_root, eid, update_index=False)
        except DatahiveError as e:
            problems = list(getattr(e, "problems", None) or [str(e)])
        if problems:
            n_invalid += 1
        n_warnings += len(warnings)
        if rec.status == "uploaded" and rec.content_hash == rec.uploaded_hash:
            n_unchanged += 1
        episodes.append({"episode_id": eid, "size_bytes": size, "problems": problems, "warnings": warnings})

    remote_files: list[str] | None = None
    try:
        if hub is not None:
            remote_files = hub.list_files()
    except DatahiveError:
        remote_files = None
    dir_problems, dir_warnings = directory_problems(samples_root, sessions, remote_files=remote_files)
    diversity = diversity_warnings(rows)

    return {
        "n_episodes": len(episode_ids),
        "n_unannotated": n_unannotated,
        "n_invalid": n_invalid,
        "n_warnings": n_warnings + len(dir_warnings),
        "n_unchanged": n_unchanged,
        "total_bytes": total_bytes,
        "directory_problems": dir_problems,
        "directory_warnings": dir_warnings,
        "diversity": diversity,
        "episodes": episodes,
        "ok": not dir_problems and n_invalid == 0 and n_unannotated == 0,
    }
