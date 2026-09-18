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
    # operator_name/annotator_name are plain (non-Optional) strings on the
    # model; callers (the GUI's JSON payload in particular) may send an
    # explicit None for "not provided" -- normalize that to "" here so both
    # the CLI and the GUI can omit them freely.
    for name_field in ("operator_name", "annotator_name", "failure_cause_detail"):
        if data.get(name_field) is None:
            data[name_field] = ""

    # completion_time_s is derived from the episode's own recorded duration
    # rather than typed in by hand -- the .h5's timestamps are the source
    # of truth for how long a successful trial actually took. A caller can
    # still pass an explicit value to override it.
    if data.get("outcome") == "success" and data.get("completion_time_s") is None:
        from datahive.episode import episode_stats

        duration = episode_stats(paths.h5).get("duration_s")
        if duration is not None:
            data["completion_time_s"] = round(duration, 3)

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
