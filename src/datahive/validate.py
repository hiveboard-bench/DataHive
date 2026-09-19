"""`datahive validate`: local schema + profile checks for one episode."""

from __future__ import annotations

from pathlib import Path

from datahive.attachments import is_composed_assembly
from datahive.consistency import consistency_problems, missing_parts
from datahive.episode import read_header, sample_rate_hz
from datahive.errors import DatahiveError, ProfileIncomplete, ProfileMissing, ValidationError
from datahive.index import Index
from datahive.paths import resolve_episode_paths
from datahive.profile import camera_consistency_problems, load_profile
from datahive.schema import TrialAnnotation
from datahive.trials import get_row

MIN_SAMPLE_RATE_HZ = 100.0


def validate_episode(samples_root: Path, episode_id: str, *, update_index: bool = True) -> list[str]:
    """Raises ValidationError (with .problems) if the episode is invalid.
    Returns a list of non-fatal warnings on success. Also updates the local
    index status to 'validated' on success (unless update_index=False)."""
    problems: list[str] = []
    warnings: list[str] = []

    try:
        load_profile(samples_root)
    except (ProfileMissing, ProfileIncomplete) as e:
        raise ValidationError(str(e), problems=[str(e)]) from e

    try:
        paths = resolve_episode_paths(samples_root, episode_id)
    except Exception as e:
        raise ValidationError(str(e), problems=[str(e)]) from e

    try:
        header = read_header(paths.h5)
    except Exception as e:
        raise ValidationError(
            f"Could not read header from {paths.h5}: {e}", problems=[str(e)]
        ) from e

    if not header.low_level.get("mode"):
        problems.append("Episode header has no low_level.mode recorded.")
    if not header.cameras:
        problems.append("Episode header lists no cameras.")
    problems.extend(camera_consistency_problems(header.cameras))

    rate = sample_rate_hz(paths.h5)
    if rate is None:
        problems.append("Could not determine proprioception sample rate (no timestamps).")
    elif rate < MIN_SAMPLE_RATE_HZ * 0.99:
        problems.append(
            f"Proprioception sample rate is {rate:.1f} Hz, below the required "
            f"{MIN_SAMPLE_RATE_HZ:.0f} Hz."
        )

    if rate is not None and header.control_freq and abs(rate - header.control_freq) > 0.1 * header.control_freq:
        warnings.append(
            f"Recorded rate is {rate:.1f} Hz but the profile's control_freq is {header.control_freq:g} Hz."
        )

    row = get_row(paths.trials_csv, header.trial_id)
    extra_problems, extra_warnings = consistency_problems(samples_root, paths, header, row)
    problems.extend(extra_problems)
    warnings.extend(extra_warnings)
    if row is None:
        problems.append(
            f"No annotation row found in {paths.trials_csv} for trial_id "
            f"'{header.trial_id}'. Run `datahive annotate {episode_id}` first."
        )
    else:
        composed = is_composed_assembly(row.get("attachment_id", ""), samples_root)
        context = {} if composed is None else {"composed_assembly": composed}
        if composed is None:
            warnings.append(
                f"attachment_id '{row.get('attachment_id')}' is not in the "
                "attachment registry; stage_reached was not checked."
            )
        try:
            TrialAnnotation.from_csv_row(row, context=context)
        except Exception as e:
            problems.append(f"Annotation for trial '{header.trial_id}' is invalid: {e}")

    missing = missing_parts(paths, header)
    if missing:
        summary = f"Episode is incomplete: missing {', '.join(missing)}. Upload the missing file(s) in Annotate."
        if "HDF5 recording data" in missing:
            problems = [summary]
        else:
            problems.insert(0, summary)

    if problems:
        bullets = "\n".join(f"  - {p}" for p in problems)
        message = f"Episode '{episode_id}' failed validation:\n{bullets}"
        if update_index:
            with Index(samples_root) as idx:
                if idx.get(episode_id) is None:
                    idx.scan()
                idx.set_status(episode_id, "recorded", error=message)
        raise ValidationError(message, problems=problems)

    if update_index:
        with Index(samples_root) as idx:
            if idx.get(episode_id) is None:
                idx.scan()
            idx.refresh_hash(episode_id)
            idx.set_status(episode_id, "validated")

    return warnings


def bulk_validate_episodes(samples_root: Path, episode_ids: list[str]) -> list[dict]:
    """Validates several episodes, same rules as validate_episode() applied
    one at a time. A bad episode_id doesn't abort the batch -- it comes
    back as an ok=False result like any other validation failure."""
    results: list[dict] = []
    for episode_id in episode_ids:
        try:
            warnings = validate_episode(samples_root, episode_id)
            results.append({"episode_id": episode_id, "ok": True, "warnings": warnings, "error": None})
        except DatahiveError as e:
            results.append({"episode_id": episode_id, "ok": False, "warnings": [], "error": str(e)})
    return results
