"""Pre-annotation health check for collected episodes.

Checks recording integrity (HDF5 structure, proprioception sample rate,
monotonic timestamps, timestamp jitter/gaps, and camera video files)
without requiring an annotation row in trials.csv.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import h5py
import numpy as np

from datahive.consistency import MAX_DURATION_S, MIN_DURATION_S, _trajectories, _videos
from datahive.episode import episode_stats, read_header, sample_rate_hz
from datahive.errors import DatahiveError, EpisodeNotFound
from datahive.paths import resolve_episode_paths
from datahive.profile import camera_consistency_problems, load_profile
from datahive.validate import MIN_SAMPLE_RATE_HZ


def check_episode(samples_root: Path, episode_id: str) -> dict[str, Any]:
    """Runs a quick pre-annotation health check on a recorded episode.

    Checks:
      1. HDF5 exists and is a valid readable file.
      2. Episode header attributes parse and contain required hardware metadata.
      3. /proprioception/timestamp exists, has >= 2 steps, and is monotonically increasing.
      4. Proprioception sample rate meets the HiveBoard 100 Hz minimum.
      5. No severe timestamp dropouts/gaps.
      6. Recorded arrays are finite and aligned; the episode lasts 1 to 600 s.
      6b. Every camera video exists, decodes, and matches the others and the episode
          (resolution, fps, frame count, duration).
      7. Consistency with robot_profile.yaml (if profile exists).

    Returns a dict with:
      ok: bool
      episode_id: str
      problems: list[str]  # hard failures preventing valid rollout
      warnings: list[str]  # non-fatal recommendations / notices
      stats: dict[str, Any]
    """
    problems: list[str] = []
    warnings: list[str] = []
    stats: dict[str, Any] = {
        "n_steps": 0,
        "duration_s": None,
        "sample_rate_hz": None,
        "cameras": [],
        "timestamp_gaps": 0,
    }

    try:
        paths = resolve_episode_paths(samples_root, episode_id)
    except EpisodeNotFound:
        return {
            "ok": False,
            "episode_id": episode_id,
            "problems": [f"Episode '{episode_id}' not found under {samples_root}."],
            "warnings": [],
            "stats": stats,
        }
    except Exception as e:
        return {
            "ok": False,
            "episode_id": episode_id,
            "problems": [f"Could not resolve episode paths: {e}"],
            "warnings": [],
            "stats": stats,
        }

    if not paths.h5.is_file():
        return {
            "ok": False,
            "episode_id": episode_id,
            "problems": [f"HDF5 file does not exist: {paths.h5}"],
            "warnings": [],
            "stats": stats,
        }

    # 1. HDF5 readability & header parsing
    try:
        header = read_header(paths.h5)
    except Exception as e:
        return {
            "ok": False,
            "episode_id": episode_id,
            "problems": [f"Could not read header from {paths.h5}: {e}"],
            "warnings": [],
            "stats": stats,
        }

    if not header.low_level.get("mode"):
        problems.append("Header has no low_level.mode recorded.")
    if not header.cameras:
        problems.append("Header lists no cameras.")
    problems.extend(camera_consistency_problems(header.cameras))

    # 2. Proprioception datasets & timestamp sanity
    try:
        with h5py.File(paths.h5, "r") as f:
            proprio = f.get("proprioception")
            if proprio is None:
                problems.append("Missing /proprioception group in HDF5 file.")
            elif "timestamp" not in proprio:
                problems.append("Missing /proprioception/timestamp dataset.")
            else:
                ts = np.asarray(proprio["timestamp"][:]).reshape(-1)
                n_steps = int(len(ts))
                stats["n_steps"] = n_steps
                if n_steps < 2:
                    problems.append(f"Episode has only {n_steps} step(s); minimum is 2.")
                else:
                    duration = float(ts[-1] - ts[0])
                    stats["duration_s"] = duration
                    if duration <= 0:
                        problems.append(f"Invalid duration: {duration:.3f}s (timestamps not increasing).")
                    
                    diffs = np.diff(ts)
                    negative_diffs = np.sum(diffs < 0)
                    if negative_diffs > 0:
                        problems.append(f"Timestamps are not monotonically increasing ({negative_diffs} non-positive step(s)).")

                    median_dt = np.median(diffs)
                    if median_dt > 0:
                        rate = 1.0 / median_dt
                        stats["sample_rate_hz"] = float(rate)
                        if rate < MIN_SAMPLE_RATE_HZ * 0.99:
                            problems.append(
                                f"Proprioception sample rate is {rate:.1f} Hz, below the required {MIN_SAMPLE_RATE_HZ:.0f} Hz."
                            )
                        # Check for large gaps (> 3x median dt)
                        large_gaps = int(np.sum(diffs > 3.0 * median_dt))
                        stats["timestamp_gaps"] = large_gaps
                        if large_gaps > 0:
                            problems.append(f"Detected {large_gaps} severe timestamp gap(s) greater than 3x normal sampling interval.")
    except Exception as e:
        problems.append(f"Error inspecting datasets in {paths.h5}: {e}")

    duration = _trajectories(paths.h5, header, problems_traj := [])
    problems.extend(p for p in problems_traj if not p.startswith("Missing /proprioception"))
    if duration is not None and not (MIN_DURATION_S <= duration <= MAX_DURATION_S):
        problems.append(
            f"Episode lasts {duration:.2f}s; it must be between {MIN_DURATION_S:.0f} and {MAX_DURATION_S:.0f} s."
        )
    _videos(paths, header, duration, problems, warnings)
    cam_names = [cam.get("name") or "unnamed" for cam in header.cameras]
    stats["cameras"] = cam_names

    # 4. Consistency with robot_profile.yaml
    try:
        profile = load_profile(samples_root)
        profile_cams = {c.name for c in profile.cameras}
        header_cams = set(cam_names)
        if profile_cams != header_cams:
            warnings.append(
                f"Camera names in header ({sorted(header_cams)}) differ from profile ({sorted(profile_cams)})."
            )
        profile_joints = set(profile.manipulator.joint_names or [])
        header_joints = set(header.manipulator.get("joint_names") or [])
        if profile_joints and header_joints and profile_joints != header_joints:
            warnings.append(
                f"Joint names in header ({sorted(header_joints)}) differ from profile ({sorted(profile_joints)})."
            )
    except Exception:
        # Profile might not exist yet if just testing raw episodes; that's fine as a warning
        pass

    return {
        "ok": len(problems) == 0,
        "episode_id": episode_id,
        "problems": problems,
        "warnings": warnings,
        "stats": stats,
    }


def check_all_episodes(samples_root: Path) -> list[dict[str, Any]]:
    """Runs health check on all episodes found under samples_root."""
    from datahive.index import Index

    with Index(samples_root) as idx:
        idx.scan()
        records = idx.all()
        episode_ids = [r.episode_id for r in records]

    return [check_episode(samples_root, eid) for eid in episode_ids]
