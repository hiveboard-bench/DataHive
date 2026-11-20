"""Scaffolding shared by every source adapter and by the batch loop.

Schema-shaped work lives in ``datahive`` itself. What is left over is the part
that does not change between source formats: episode identifiers, the batch
loop that keeps going when one episode fails, and the run report.
"""

from __future__ import annotations

import json
import re
import sys
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

# Re-exported so callers do not duplicate the constants the validator enforces.
from datahive.validate import MIN_SAMPLE_RATE_HZ  # noqa: F401

_SLUG = re.compile(r"[^A-Za-z0-9_.-]+")


class Skip(Exception):
    """Drop one source episode without counting it as a failure.

    Raised for episodes that are legitimately out of scope: shorter than the
    validator's duration bounds, missing a camera, or with a video segment that
    cannot be resolved. Real errors are raised as themselves.
    """


class ConversionError(Exception):
    """Configuration or data problem that should fail the run."""


def slug(text: str) -> str:
    return _SLUG.sub("_", text).strip("_")


def session_id_for(lab_id: str, platform_id: str, when: datetime | None = None) -> str:
    when = when or datetime.now(timezone.utc)
    return f"{slug(lab_id)}_{slug(platform_id)}_{when:%Y%m%d}"


def episode_id_for(session_id: str, trial_id: int) -> str:
    return f"{session_id}_t{trial_id}"


@dataclass
class EpisodeRecord:
    trial_id: int
    episode_id: str
    source_index: int
    status: str  # written | exists | skipped | failed
    frames: int = 0
    duration_s: float = 0.0
    assumed_annotation: bool = False
    reason: str = ""
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "trial_id": self.trial_id,
            "episode_id": self.episode_id,
            "source_index": self.source_index,
            "status": self.status,
            "frames": self.frames,
            "duration_s": round(self.duration_s, 4),
            "assumed_annotation": self.assumed_annotation,
            "reason": self.reason,
            "detail": self.detail,
        }


@dataclass
class BatchResult:
    written: list[EpisodeRecord] = field(default_factory=list)
    exists: list[EpisodeRecord] = field(default_factory=list)
    skipped: list[EpisodeRecord] = field(default_factory=list)
    failed: list[EpisodeRecord] = field(default_factory=list)

    @property
    def records(self) -> list[EpisodeRecord]:
        return self.written + self.exists + self.skipped + self.failed

    def exit_code(self) -> int:
        return 1 if self.failed else 0


def run_batch(
    items: Iterable[tuple[int, int, str]],
    convert_one: Callable[[int, int, str], EpisodeRecord],
    *,
    progress: bool = True,
) -> BatchResult:
    """Convert ``(trial_id, source_index, episode_id)`` triples, one by one.

    A ``Skip`` is recorded as skipped; any other exception is recorded as a
    failure and the loop continues, because one bad episode should not cost the
    rest of a 100-episode run.
    """
    result = BatchResult()
    items = list(items)
    total = len(items)
    for position, (trial_id, source_index, episode_id) in enumerate(items, 1):
        try:
            record = convert_one(trial_id, source_index, episode_id)
        except Skip as err:
            record = EpisodeRecord(trial_id, episode_id, source_index, "skipped", reason=str(err))
        except Exception as err:  # noqa: BLE001 - deliberately isolate per episode
            record = EpisodeRecord(
                trial_id,
                episode_id,
                source_index,
                "failed",
                reason=type(err).__name__,
                detail=str(err),
            )
            if progress:
                print(f"  ! {episode_id}: {type(err).__name__}: {err}", file=sys.stderr)
                traceback.print_exc(limit=3, file=sys.stderr)
        if progress:
            print(f"[{position}/{total}] {record.status:8s} {episode_id} {record.reason}")
        if record.status == "written":
            result.written.append(record)
        elif record.status == "exists":
            result.exists.append(record)
        elif record.status == "skipped":
            result.skipped.append(record)
        else:
            result.failed.append(record)
    return result


def summarize(result: BatchResult) -> str:
    assumed = sum(1 for r in result.records if r.assumed_annotation)
    lines = [
        f"written={len(result.written)} exists={len(result.exists)} "
        f"skipped={len(result.skipped)} failed={len(result.failed)}",
    ]
    if assumed:
        lines.append(
            f"WARNING: {assumed} episode(s) were labelled with --assume-success. "
            "These are not real labels; re-annotate before submitting."
        )
    if result.skipped:
        lines.append("skipped episodes (not failures):")
        lines.extend(f"  {r.episode_id}: {r.reason}" for r in result.skipped)
    if result.failed:
        lines.append("failed episodes:")
        lines.extend(f"  {r.episode_id}: {r.reason}: {r.detail}" for r in result.failed)
    return "\n".join(lines)


def write_report(path: Path, result: BatchResult, settings: dict[str, Any]) -> None:
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "settings": settings,
        "summary": {
            "written": len(result.written),
            "exists": len(result.exists),
            "skipped": len(result.skipped),
            "failed": len(result.failed),
            "assumed_annotations": sum(1 for r in result.records if r.assumed_annotation),
        },
        "episodes": [r.as_dict() for r in result.records],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
