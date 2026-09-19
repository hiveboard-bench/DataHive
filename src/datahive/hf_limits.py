"""HuggingFace rejects a directory with too many files. Uploads land in
<session>/episodes/, so that folder is what must stay under the limit."""

from __future__ import annotations

from pathlib import Path

HF_DIR_FILE_LIMIT = 10_000
WARN_FRACTION = 0.9


def split_suggestion(session_id: str, n_files: int, limit: int) -> str:
    excess = n_files - limit
    return (
        f"Split it: move the newest ~{max(excess, 1)} episode(s) (their .h5 and .mp4 files) "
        f"into a new session such as '{session_id}_part2' (with its own trials.csv rows), "
        "then upload again."
    )


def directory_problems(
    samples_root: Path, session_ids: set[str], *, remote_files: list[str] | None = None,
    limit: int | None = None,
) -> tuple[list[str], list[str]]:
    """(problems, warnings) for each session's episodes/ directory, counting
    every local file plus any already on the Hub."""
    limit = limit or HF_DIR_FILE_LIMIT
    problems: list[str] = []
    warnings: list[str] = []
    for session_id in sorted(session_ids):
        edir = Path(samples_root) / session_id / "episodes"
        names = {p.name for p in edir.iterdir() if p.is_file()} if edir.is_dir() else set()
        prefix = f"{session_id}/episodes/"
        for remote in remote_files or []:
            if remote.startswith(prefix) and "/" not in remote[len(prefix):]:
                names.add(remote[len(prefix):])
        n = len(names)
        if n > limit:
            problems.append(
                f"Session '{session_id}' has {n} files in episodes/, over the HuggingFace limit "
                f"of {limit} per directory. " + split_suggestion(session_id, n, limit)
            )
        elif n >= limit * WARN_FRACTION:
            warnings.append(
                f"Session '{session_id}' has {n} of {limit} files allowed in episodes/; "
                "start a new session soon."
            )
    return problems, warnings
