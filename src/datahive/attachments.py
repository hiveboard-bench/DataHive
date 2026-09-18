"""Attachment registry: which attachment_ids are "composed-assembly"
(multi-stage) attachments, for which stage_reached is meaningful.

Bundled defaults live in datahive/attachments.yaml; a lab can add or
override entries with samples/attachments.yaml.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from datahive.paths import attachments_override_path

_BUNDLED_PATH = Path(__file__).parent / "attachments.yaml"


@dataclass
class AttachmentInfo:
    attachment_id: str
    name: str
    composed_assembly: bool
    n_stages: int | None = None
    family: str | None = None
    timeout: int | None = None
    success: str | None = None
    reset: str | None = None
    stages: list[str] | None = None
    image: str | None = None


@lru_cache(maxsize=1)
def _bundled() -> dict:
    return yaml.safe_load(_BUNDLED_PATH.read_text(encoding="utf-8")) or {}


def load_registry(samples_root: Path | None = None) -> dict[str, AttachmentInfo]:
    raw: dict = dict(_bundled())
    if samples_root is not None:
        override_path = attachments_override_path(samples_root)
        if override_path.is_file():
            override = yaml.safe_load(override_path.read_text(encoding="utf-8")) or {}
            raw.update(override)
    return {
        aid: AttachmentInfo(
            attachment_id=aid,
            name=info.get("name", aid),
            composed_assembly=bool(info.get("composed_assembly", False)),
            n_stages=info.get("n_stages"),
            family=info.get("family"),
            timeout=info.get("timeout"),
            success=info.get("success"),
            reset=info.get("reset"),
            stages=info.get("stages"),
            image=info.get("image"),
        )
        for aid, info in raw.items()
    }


def is_composed_assembly(attachment_id: str, samples_root: Path | None = None) -> bool | None:
    """Returns True/False if the attachment is known, or None if unknown
    (caller should treat this as 'no constraint, but warn')."""
    registry = load_registry(samples_root)
    info = registry.get(attachment_id)
    if info is None:
        return None
    return info.composed_assembly
