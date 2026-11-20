"""Resampling to the proprioception rate ``datahive_episode_v1`` requires.

The schema rejects episodes whose median sample interval implies a rate below
99 Hz, and most third-party datasets (including LeRobot's SO-101 recordings)
are 25-30 Hz. The converter therefore resamples state and action arrays onto a
uniform grid with linear interpolation.

This is interpolation, not measurement. ``provenance`` is a dataset-level
attribute, so once an array is resampled every value in it is written as
``estimated`` -- original and synthesised ticks cannot be told apart inside the
file. The converter prints a reminder in every skipped/failed summary and the
conversion report records the setting.
"""

from __future__ import annotations

import numpy as np


def grid_length(n_source: int, source_fps: float, target_hz: float) -> int:
    """Number of samples covering the same wall-clock span at ``target_hz``."""
    if n_source <= 1:
        return n_source
    duration = (n_source - 1) / source_fps
    return int(round(duration * target_hz)) + 1


def sample_times(n: int, rate_hz: float) -> np.ndarray:
    return np.arange(n, dtype=np.float64) / rate_hz


def to_grid(values: np.ndarray, source_fps: float, target_hz: float) -> np.ndarray:
    """Linearly interpolate ``(T, D)`` values onto a uniform ``target_hz`` grid.

    The first and last samples are preserved exactly, so the episode start and
    end poses are never altered.
    """
    values = np.asarray(values, dtype=np.float64)
    if values.ndim != 2:
        raise ValueError(f"expected a (T, D) array, got shape {values.shape}")
    n_source = values.shape[0]
    if n_source < 2:
        return values.copy()
    if abs(target_hz - source_fps) < 1e-9:
        return values.copy()

    source_t = sample_times(n_source, source_fps)
    n_out = grid_length(n_source, source_fps, target_hz)
    target_t = sample_times(n_out, target_hz)
    out = np.empty((n_out, values.shape[1]), dtype=np.float64)
    for column in range(values.shape[1]):
        out[:, column] = np.interp(target_t, source_t, values[:, column])
    out[0] = values[0]
    out[-1] = values[-1]
    return out


def provenance(source_fps: float, target_hz: float) -> str:
    """``estimated`` whenever the output grid differs from the source cadence."""
    return "measured" if abs(target_hz - source_fps) < 1e-9 else "estimated"
