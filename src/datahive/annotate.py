"""`datahive annotate`: fills outcome/failure_cause/etc. for an episode's
trial, shared by the CLI's interactive prompts and the GUI's validation
form (both end up calling `annotate_episode`)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from datahive.attachments import is_composed_assembly
from datahive.episode import read_header
from datahive.errors import AnnotationError
from datahive.paths import resolve_episode_paths
from datahive.schema import TrialAnnotation
from datahive.trials import upsert_row


def annotate_episode(
    samples_root: Path, episode_id: str, fields: dict[str, Any], *, validate_after: bool = True
) -> TrialAnnotation:
    """`fields` is a dict of TrialAnnotation column values (trial_id/lab_id/
    platform_id/attachment_id/date are filled in automatically from the
    episode header + config when omitted). Writes the row to trials.csv and,
    by default, runs full validation afterward so the index status reflects
    whether the episode is now `validated`."""
    paths = resolve_episode_paths(samples_root, episode_id)
    header = read_header(paths.h5)

    data = dict(fields)
    data.setdefault("trial_id", header.trial_id)
    data.setdefault("lab_id", header.lab_id)
    data.setdefault("platform_id", header.platform_id)
    if "date" not in data or data["date"] is None:
        from datetime import date

        data["date"] = date.today().isoformat()

    attachment_id = data.get("attachment_id")
    composed = is_composed_assembly(attachment_id or "", samples_root)
    context = {} if composed is None else {"composed_assembly": composed}

    try:
        annotation = TrialAnnotation.model_validate(data, context=context)
    except Exception as e:
        raise AnnotationError(f"Invalid annotation: {e}") from e

    upsert_row(paths.trials_csv, annotation)

    if validate_after:
        from datahive.validate import validate_episode

        try:
            validate_episode(samples_root, episode_id)
        except Exception:
            pass  # annotate() succeeds even if other validation problems remain

    return annotation
