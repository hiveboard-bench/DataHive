"""Command line for the dataset converter.

The user-facing interface is the ``datahive convert*`` commands in
``datahive.cli``; this module implements them and is also runnable as
``python -m datahive.converter.cli``.

    python -m datahive.converter.cli profile --samples DIR
    python -m datahive.converter.cli probe   --source PATH_OR_REPO [--metadata-only]
    python -m datahive.converter.cli convert --source ... --samples DIR ...
    python -m datahive.converter.cli selftest
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from datahive import load_profile

from .annotations import AnnotationError, AnnotationPolicy, load_annotations
from .common import (
    MIN_SAMPLE_RATE_HZ,
    ConversionError,
    EpisodeRecord,
    episode_id_for,
    run_batch,
    session_id_for,
    slug,
    summarize,
    write_report,
)
from .convert import ConvertOptions, convert_episode, episode_is_complete
from .sources import open_source

PROFILES_DIR = Path(__file__).resolve().parent / "profiles"
NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
PLACEHOLDER_LABS = {"", "your_lab_id"}
PLACEHOLDER_OPERATORS = {"", "unassigned"}


def resolve_profile(profile: str) -> Path:
    candidate = PROFILES_DIR / f"{profile}.yaml"
    if candidate.exists():
        return candidate
    path = Path(profile)
    if path.exists():
        return path
    available = sorted(p.stem for p in PROFILES_DIR.glob("*.yaml"))
    raise ConversionError(f"unknown profile {profile!r}; bundled profiles: {available}, or pass a path")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m datahive.converter.cli", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_profile = sub.add_parser("profile", help="write a robot_profile.yaml into a samples root")
    p_profile.add_argument("--samples", required=True, type=Path)
    p_profile.add_argument("--profile", default="so101")
    p_profile.add_argument("--policy", default=None, help="override the profile's policy field")
    p_profile.add_argument("--control-freq", type=float, default=None)
    p_profile.add_argument("--force", action="store_true")

    p_probe = sub.add_parser("probe", help="print what the source adapter sees")
    p_probe.add_argument("--source", required=True)
    p_probe.add_argument("--cache-dir", type=Path, default=Path(".cache/datasets"))
    p_probe.add_argument("--metadata-only", action="store_true")
    p_probe.add_argument("--camera-map", action="append", default=[], metavar="NAME=SOURCE_KEY")
    p_probe.add_argument("--json", action="store_true")

    p_tpl = sub.add_parser(
        "annotations-template", help="write a sidecar annotation CSV skeleton for the selected episodes"
    )
    p_tpl.add_argument("--source", required=True)
    p_tpl.add_argument("--cache-dir", type=Path, default=Path(".cache/datasets"))
    p_tpl.add_argument("--start-index", type=int, default=0)
    p_tpl.add_argument("--limit", type=int, default=None)
    p_tpl.add_argument("--strategy", default="prehensile")
    p_tpl.add_argument("--stage-reached", type=int, default=None)
    p_tpl.add_argument("--output", required=True, type=Path)
    p_tpl.add_argument("--force", action="store_true")

    p_conv = sub.add_parser("convert", help="convert source episodes into a samples root")
    p_conv.add_argument("--source", required=True)
    p_conv.add_argument("--samples", required=True, type=Path)
    p_conv.add_argument("--cache-dir", type=Path, default=Path(".cache/datasets"))
    p_conv.add_argument("--lab-id", required=True)
    p_conv.add_argument("--operator-name", required=True)
    p_conv.add_argument("--annotator-name", required=True)
    p_conv.add_argument("--attachment-id", required=True, help="HiveBoard attachment id, e.g. button")
    p_conv.add_argument("--strategy", required=True, help="prehensile | non_prehensile")
    p_conv.add_argument("--annotations", type=Path, default=None)
    p_conv.add_argument("--assume-success", action="store_true")
    p_conv.add_argument("--stage-reached", type=int, default=None)
    p_conv.add_argument("--profile", default="so101")
    p_conv.add_argument("--camera-map", action="append", default=[], metavar="NAME=SOURCE_KEY")
    p_conv.add_argument("--start-index", type=int, default=0)
    p_conv.add_argument("--limit", type=int, default=None)
    p_conv.add_argument("--resample-hz", type=float, default=MIN_SAMPLE_RATE_HZ)
    p_conv.add_argument(
        "--source-joint-units",
        choices=("radians", "degrees"),
        default=None,
        help="units of the source arm joints (default: the profile's source_joint_units, else radians)",
    )
    p_conv.add_argument("--allow-low-rate", action="store_true")
    p_conv.add_argument("--video-mode", choices=("transcode", "copy"), default="transcode")
    p_conv.add_argument("--video-crf", type=int, default=19)
    p_conv.add_argument("--video-preset", default="medium")
    p_conv.add_argument("--session-id", default=None)
    p_conv.add_argument("--collection-mode", default="manual")
    p_conv.add_argument("--overwrite", action="store_true")
    p_conv.add_argument("--dry-run", action="store_true")
    p_conv.add_argument("--no-validate", action="store_true", help="skip the per-episode datahive validate")
    p_conv.add_argument("--keep-invalid", action="store_true", help="keep episodes that fail validation")
    p_conv.add_argument("--report", type=Path, default=None)

    p_self = sub.add_parser("selftest", help="run the Python unit tests")
    p_self.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "profile":
            return cmd_profile(args)
        if args.command == "probe":
            return cmd_probe(args)
        if args.command == "annotations-template":
            return cmd_annotations_template(args)
        if args.command == "convert":
            return cmd_convert(args)
        if args.command == "selftest":
            return cmd_selftest(args)
    except ConversionError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    except AnnotationError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    return 2


def cmd_profile(args) -> int:
    import yaml

    source = resolve_profile(args.profile)
    data = yaml.safe_load(source.read_text(encoding="utf-8"))
    if args.policy:
        data["policy"] = args.policy
    if args.control_freq is not None:
        data["control_freq"] = args.control_freq
    args.samples.mkdir(parents=True, exist_ok=True)
    dest = args.samples / "robot_profile.yaml"
    if dest.exists() and not args.force:
        print(f"error: {dest} already exists; pass --force to replace it", file=sys.stderr)
        return 2
    dest.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    profile = load_profile(args.samples)
    print(f"wrote {dest}")
    print(f"platform_id={profile.platform_id} control_freq={profile.control_freq} "
          f"cameras={[c['name'] for c in profile.cameras]}")
    return 0


def cmd_probe(args) -> int:
    camera_map = _parse_camera_map(args.camera_map)
    source = open_source(
        args.source,
        cache_dir=args.cache_dir,
        metadata_only=args.metadata_only,
        camera_map=camera_map or None,
    )
    payload = source.probe()
    source.close()
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0
    print(f"source     {payload['kind']}  {payload['root']}")
    print(f"version    {payload['codebase_version']}   fps={payload['fps']}   robot={payload['robot_type']}")
    print(f"episodes   {payload['total_episodes']}   frames={payload['total_frames']}")
    print(f"cameras    " + ", ".join(f"{dst}<-{src}" for dst, src in payload["camera_map"].items()))
    print(f"joints     {payload['joint_names']}")
    print(f"tasks      {payload['tasks']}")
    print(f"columns    {payload['episodes_columns']}")
    first = payload["first_episode"]
    print(f"episode0   length={first.get('length')} dataset=[{first.get('dataset_from_index')}, "
          f"{first.get('dataset_to_index')})")
    for key, value in first.items():
        if "/from_timestamp" in key or "/to_timestamp" in key or "/file_index" in key:
            print(f"           {key} = {value}")
    return 0


def cmd_annotations_template(args) -> int:
    import csv

    if args.output.exists() and not args.force:
        raise ConversionError(f"{args.output} already exists; pass --force to replace it")
    if args.strategy not in ("prehensile", "non_prehensile"):
        raise ConversionError("--strategy must be prehensile or non_prehensile")

    source = open_source(args.source, cache_dir=args.cache_dir)
    try:
        indices = [i for i in source.episode_indices() if i >= args.start_index]
        if args.limit is not None:
            indices = indices[: args.limit]
    finally:
        source.close()
    if not indices:
        raise ConversionError("no episodes selected")

    columns = ["episode_index", "outcome", "strategy", "stage_reached",
               "failure_cause", "failure_cause_detail", "severity",
               "n_attempts", "n_regrasps", "notes"]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for index in indices:
            writer.writerow(
                {
                    "episode_index": index,
                    "outcome": "",
                    "strategy": args.strategy,
                    "stage_reached": "" if args.stage_reached is None else args.stage_reached,
                }
            )
    print(f"wrote {args.output} ({len(indices)} rows)")
    print("fill in the outcome column ('success' | 'fail' | 'timeout' | 'safety_stop'), then run:")
    print(f"  datahive convert <samples> ... --annotations {args.output}")
    return 0


def cmd_convert(args) -> int:
    _check_identifiers(args)
    if args.resample_hz < MIN_SAMPLE_RATE_HZ and not args.allow_low_rate:
        raise ConversionError(
            f"--resample-hz {args.resample_hz:g} is below the {MIN_SAMPLE_RATE_HZ:g} Hz minimum that "
            "datahive_episode_v1 enforces. Pass --allow-low-rate to write it anyway (validation will fail)."
        )
    profile_path = resolve_profile(args.profile)
    profile_file = args.samples / "robot_profile.yaml"
    if not profile_file.exists():
        raise ConversionError(
            f"{profile_file} does not exist. Run `datahive convert-profile {args.samples} "
            f"--profile {args.profile}` first."
        )
    profile = load_profile(args.samples)
    from datahive.profile import read_raw_profile

    raw_profile = read_raw_profile(args.samples) or {}
    source_joint_units = args.source_joint_units or str(
        raw_profile.get("source_joint_units") or "radians"
    )
    if source_joint_units not in ("radians", "degrees"):
        raise ConversionError(
            f"source_joint_units {source_joint_units!r} in {profile_file} is not supported; "
            "use 'radians' or 'degrees'"
        )
    if abs(float(profile.control_freq) - args.resample_hz) > 0.1:
        print(
            f"warning: profile control_freq={profile.control_freq} but --resample-hz={args.resample_hz:g}. "
            "Validation warns when they differ by more than 10%.",
            file=sys.stderr,
        )

    annotations = (
        load_annotations(args.annotations, default_strategy=args.strategy) if args.annotations else {}
    )
    policy = AnnotationPolicy(
        attachment_id=args.attachment_id,
        annotator_name=args.annotator_name,
        operator_name=args.operator_name,
        strategy=args.strategy,
        annotations=annotations,
        assume_success=args.assume_success,
        stage_reached=args.stage_reached,
    )
    if not annotations and not args.assume_success:
        raise ConversionError(
            "no --annotations file was supplied. datahive_episode_v1 has no unlabelled state, and "
            "annotation is human judgement. Supply a CSV, or pass --assume-success to record an "
            "explicit assumption (NOT a real label)."
        )

    session_id = args.session_id or session_id_for(args.lab_id, profile.platform_id)
    source = open_source(
        args.source,
        cache_dir=args.cache_dir,
        camera_map=_parse_camera_map(args.camera_map) or None,
    )
    try:
        indices = [i for i in source.episode_indices() if i >= args.start_index]
        if args.limit is not None:
            indices = indices[: args.limit]
        if not indices:
            raise ConversionError("no episodes selected")
        items = [
            (trial_id, index, episode_id_for(session_id, trial_id))
            for trial_id, index in enumerate(indices, 1)
        ]
        options = ConvertOptions(
            samples_root=args.samples,
            session_id=session_id,
            lab_id=args.lab_id,
            profile=profile,
            annotation_policy=policy,
            target_hz=args.resample_hz,
            source_joint_units=source_joint_units,
            video_mode=args.video_mode,
            crf=args.video_crf,
            preset=args.video_preset,
            collection_mode=args.collection_mode,
            overwrite=args.overwrite,
            validate=not args.no_validate,
            keep_invalid=args.keep_invalid,
        )
        print(f"session    {session_id}")
        print(f"profile    {profile_path} -> {profile_file}")
        print(f"episodes   {len(items)} selected from source index {indices[0]}")
        print(f"rate       {source.info.fps:g} Hz source -> {args.resample_hz:g} Hz output")

        if args.dry_run:
            _print_plan(source, items, options, policy)
            return 0

        def convert_one(trial_id: int, source_index: int, episode_id: str) -> EpisodeRecord:
            if not args.overwrite and episode_is_complete(options, episode_id, trial_id):
                meta = source.episode_meta(source_index)
                return EpisodeRecord(trial_id, episode_id, source_index, "exists", frames=meta.length)
            return convert_episode(source, source_index, trial_id, episode_id, options)

        result = run_batch(items, convert_one)
    finally:
        source.close()

    print()
    print(summarize(result))
    report_path = args.report or (args.samples / "conversion_report.json")
    write_report(
        report_path,
        result,
        {
            "source": str(args.source),
            "session_id": session_id,
            "attachment_id": args.attachment_id,
            "assumed_annotations": bool(args.assume_success and not annotations),
            "resample_hz": args.resample_hz,
            "source_joint_units": source_joint_units,
            "video_mode": args.video_mode,
        },
    )
    print(f"report     {report_path}")
    return result.exit_code()


def cmd_selftest(args) -> int:
    import unittest

    loader = unittest.TestLoader()
    package = Path(__file__).resolve().parent
    # top_level_dir is the directory that holds the `datahive` package, so the
    # tests import as datahive.converter.tests.* and their relative imports
    # resolve against the package.
    suite = loader.discover(str(package / "tests"), top_level_dir=str(package.parent.parent))
    runner = unittest.TextTestRunner(verbosity=2 if args.verbose else 1)
    return 0 if runner.run(suite).wasSuccessful() else 1


def _check_identifiers(args) -> None:
    if not args.source.strip():
        raise ConversionError("--source is empty")
    if args.lab_id.strip().lower() in PLACEHOLDER_LABS:
        raise ConversionError("--lab-id must be your registered lab id, not empty or 'your_lab_id'")
    if args.operator_name.strip().lower() in PLACEHOLDER_OPERATORS:
        raise ConversionError("--operator-name must name who ran the trial, not empty or 'Unassigned'")
    for flag, value in (("--annotator-name", args.annotator_name), ("--lab-id", args.lab_id)):
        if not NAME_RE.match(value.strip()):
            raise ConversionError(f"{flag} {value!r} may only contain letters, digits, '_', '.', '-'")


def _parse_camera_map(pairs: list[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            raise ConversionError(f"--camera-map expects NAME=SOURCE_KEY, got {pair!r}")
        name, key = pair.split("=", 1)
        if not name or not key:
            raise ConversionError(f"--camera-map expects NAME=SOURCE_KEY, got {pair!r}")
        mapping[slug(name)] = key
    return mapping


def _print_plan(source, items, options, policy) -> None:
    print("\ntrial  episode_id                              T_src  T_out  duration  cameras")
    for trial_id, index, episode_id in items:
        meta = source.episode_meta(index)
        try:
            annotation = policy.resolve(index)
            label = annotation.outcome + (" (assumed)" if annotation.assumed else "")
        except AnnotationError as err:
            label = f"MISSING ({err})"
        from .resample import grid_length

        out = grid_length(meta.length, source.info.fps, options.target_hz)
        print(
            f"{trial_id:5d}  {episode_id:38s} {meta.length:6d} {out:6d}  "
            f"{(out - 1) / options.target_hz:8.2f}s  {','.join(meta.videos)}  {label}"
        )
    print("\ndry run: nothing written")


if __name__ == "__main__":
    raise SystemExit(main())
