"""`datahive install-skill`: copies the bundled AI-assistant skills
(src/datahive/skills/*) into an assistant's skills directory."""

from __future__ import annotations

import shutil
from pathlib import Path

from datahive.errors import DatahiveError

SKILLS_SOURCE = Path(__file__).parent / "skills"


def bundled_skills() -> list[Path]:
    if not SKILLS_SOURCE.is_dir():
        return []
    return sorted(p for p in SKILLS_SOURCE.iterdir() if (p / "SKILL.md").is_file())


def default_target(*, user: bool, cwd: Path | None = None) -> Path:
    return Path.home() / ".claude" / "skills" if user else (cwd or Path.cwd()) / ".claude" / "skills"


def install_skills(target: Path, *, force: bool = False) -> tuple[list[Path], list[Path]]:
    """Copies every bundled skill into `target`. Returns (installed, skipped);
    an existing skill folder is skipped unless `force` (which replaces it)."""
    skills = bundled_skills()
    if not skills:
        raise DatahiveError("No bundled skills found; reinstall datahive-tools.")
    target.mkdir(parents=True, exist_ok=True)
    installed: list[Path] = []
    skipped: list[Path] = []
    for src in skills:
        dest = target / src.name
        if dest.exists() or dest.is_symlink():
            if not force:
                skipped.append(dest)
                continue
            if dest.is_dir() and not dest.is_symlink():
                shutil.rmtree(dest)
            else:
                dest.unlink()
        shutil.copytree(src, dest, ignore=shutil.ignore_patterns("__pycache__"))
        installed.append(dest)
    return installed, skipped
