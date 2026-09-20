"""`datahive annotate`: fills outcome/failure_cause/etc. for an episode's
trial, shared by the CLI's interactive prompts and the GUI's validation
form (both end up calling `annotate_episode`)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import ValidationError

from datahive.attachments import is_composed_assembly
from datahive.episode import read_header
from datahive.errors import AnnotationError
from datahive.paths import resolve_episode_paths
from datahive.schema import ANNOTATION_SCHEMA_CURRENT, TrialAnnotation
from datahive.trials import upsert_row


def _readable(err: ValidationError) -> str:
    """One line per problem, without pydantic's type codes and doc links."""
    lines = []
    for item in err.errors():
        field = ".".join(str(p) for p in item["loc"])
        msg = str(item["msg"]).removeprefix("Value error, ")
        lines.append(f"{field}: {msg}" if field else msg)
    return "; ".join(lines)


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
        from datetime import datetime, timezone

        data["date"] = datetime.now(timezone.utc).date().isoformat()
    for name_field in ("operator_name", "annotator_name", "failure_cause_detail"):
        if data.get(name_field) is None:
            data[name_field] = ""

    if data.get("outcome") == "success" and data.get("completion_time_s") is None:
        from datahive.episode import episode_stats

        duration = episode_stats(paths.h5).get("duration_s")
        if duration is not None:
            data["completion_time_s"] = round(duration, 3)
            data.setdefault("completion_source", "hdf5")

    from datetime import datetime, timezone

    data["annotated_at"] = datetime.now(timezone.utc).isoformat()
    data["schema_version"] = ANNOTATION_SCHEMA_CURRENT

    attachment_id = data.get("attachment_id")
    composed = is_composed_assembly(attachment_id or "", samples_root)
    context = {} if composed is None else {"composed_assembly": composed}

    try:
        annotation = TrialAnnotation.model_validate(data, context=context)
    except ValidationError as e:
        raise AnnotationError(f"Invalid annotation: {_readable(e)}") from e
    except Exception as e:
        raise AnnotationError(f"Invalid annotation: {e}") from e

    upsert_row(paths.trials_csv, annotation)
    write_h5_annotation(paths.h5, annotation)

    if validate_after:
        from datahive.validate import validate_episode

        try:
            validate_episode(samples_root, episode_id)
        except Exception:
            pass  
        
    return annotation


def _annotator_key(name: str) -> str:
    return (name or "unknown").strip().replace("/", "_") or "unknown"


def write_h5_annotation(h5_path: Path, annotation: TrialAnnotation) -> None:
    """Stores the annotation inside the episode as
    episode_annotations/<annotator>/ (attrs), stamped with schema_version, so
    the file stays self-describing if trials.csv is lost or the episode moved."""
    import json

    import h5py

    row = annotation.to_csv_row()
    key = _annotator_key(annotation.annotator_name)
    with h5py.File(h5_path, "r+") as f:
        root = f.require_group("episode_annotations")
        if key in root:
            del root[key]
        grp = root.create_group(key)
        grp.attrs["schema_version"] = annotation.schema_version
        grp.attrs["source"] = "human"
        grp.attrs["timestamp"] = row["annotated_at"]
        grp.attrs["annotation"] = json.dumps(row, sort_keys=True)


def read_h5_annotations(h5_path: Path) -> dict[str, dict]:
    """{annotator: row-dict} from the episode file. Groups without a
    schema_version are upcast to the legacy version; nothing is rewritten."""
    import json

    import h5py

    from datahive.schema import ANNOTATION_SCHEMA_LEGACY

    out: dict[str, dict] = {}
    with h5py.File(h5_path, "r") as f:
        root = f.get("episode_annotations")
        if root is None:
            return out
        for name, grp in root.items():
            try:
                row = json.loads(grp.attrs.get("annotation", "{}"))
            except (TypeError, ValueError):
                row = {}
            row["schema_version"] = grp.attrs.get("schema_version") or row.get("schema_version") or ANNOTATION_SCHEMA_LEGACY
            out[name] = row
    return out
