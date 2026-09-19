"""~/.datahive/config.yaml handling.

This file holds the lab's Hugging Face token and is treated like an SSH
private key:

  * always written under the user's home directory, never inside a project
    or git working tree;
  * written with 0600 permissions, checked on every load;
  * the token is never printed anywhere -- ``masked_dict``/``mask_token``
    are the only supported ways to display it.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml

from datahive.errors import ConfigInsideGitRepo, ConfigMissing, InsecureConfigPermissions

CONFIG_DIRNAME = ".datahive"
CONFIG_FILENAME = "config.yaml"
_ENV_HOME = "DATAHIVE_CONFIG_HOME"
_ENV_OOPSIE = "OOPSIE_CONFIG_DIR"


def config_home() -> Path:
    """Directory holding config.yaml. Overridable via DATAHIVE_CONFIG_HOME
    or OOPSIE_CONFIG_DIR (used by tests so nothing ever touches a real $HOME)."""
    override = os.environ.get(_ENV_HOME) or os.environ.get(_ENV_OOPSIE)
    if override:
        return Path(override)
    return Path.home() / CONFIG_DIRNAME


def config_path() -> Path:
    ch = config_home()
    if (ch / "contributor_config.yaml").is_file() and not (ch / CONFIG_FILENAME).is_file():
        return ch / "contributor_config.yaml"
    return ch / CONFIG_FILENAME


@dataclass
class Config:
    lab_id: str
    repo_id: str
    hf_token: str
    endpoint: str | None = None
    created_at: str = ""

    def to_dict(self, *, reveal_token: bool = True) -> dict:
        return {
            "lab_id": self.lab_id,
            "repo_id": self.repo_id,
            "hf_token": self.hf_token if reveal_token else mask_token(self.hf_token),
            "endpoint": self.endpoint,
            "created_at": self.created_at,
        }

    def __repr__(self) -> str: 
        return (
            f"Config(lab_id={self.lab_id!r}, repo_id={self.repo_id!r}, "
            f"hf_token={mask_token(self.hf_token)!r}, endpoint={self.endpoint!r})"
        )

    __str__ = __repr__


def mask_token(token: str) -> str:
    if not token:
        return ""
    if len(token) <= 8:
        return "*" * len(token)
    return f"{token[:4]}****...****{token[-4:]}"


def masked_dict(cfg: Config) -> dict:
    return cfg.to_dict(reveal_token=False)


def _is_inside_git_repo(path: Path) -> Path | None:
    """Walk upward from path looking for a .git directory. Returns the repo
    root if found, else None. path need not exist yet."""
    current = path.resolve()
    for ancestor in [current, *current.parents]:
        if (ancestor / ".git").exists():
            return ancestor
    return None


def assert_not_in_git(path: Path, *, also_check: Path | None = None) -> None:
    """Refuse if `path` (the resolved config file path) would land inside a
    git working tree. Also checks `also_check` (typically the current
    working directory) as a second, independent guard."""
    hit = _is_inside_git_repo(path.parent)
    if hit is not None:
        raise ConfigInsideGitRepo(
            f"Refusing to write config to {path}: {hit} is a git repository. "
            "The datahive config file holds a secret token and must never be "
            "placed where it could be `git add`ed. Run `datahive init` from "
            "outside any git working tree, or set DATAHIVE_CONFIG_HOME."
        )
    if also_check is not None:
        hit2 = _is_inside_git_repo(also_check)
        if hit2 is not None and hit2 in (path.parent, *path.parent.parents):
            raise ConfigInsideGitRepo(
                f"Refusing to write config to {path}: the current directory "
                f"is inside the git repository at {hit2}."
            )


def check_permissions(path: Path) -> None:
    """Raise InsecureConfigPermissions if the file is group- or
    world-readable/writable. No-op on platforms without POSIX permission
    bits (e.g. Windows), where this check is not meaningful."""
    if os.name != "posix":
        return
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise InsecureConfigPermissions(
            f"{path} has insecure permissions ({oct(mode)}). It must be "
            f"readable only by you. Run:\n\n    chmod 600 {path}\n\n"
            "and try again."
        )


def save_config(cfg: Config, *, path: Path | None = None, check_git: bool = True) -> Path:
    target = path or config_path()
    if check_git:
        assert_not_in_git(target, also_check=Path.cwd())
    target.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        os.chmod(target.parent, 0o700)

    data = cfg.to_dict(reveal_token=True)
    text = yaml.safe_dump(data, sort_keys=False)

    if os.name == "posix":
        fd = os.open(str(target), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        try:
            os.write(fd, text.encode("utf-8"))
        finally:
            os.close(fd)
        os.chmod(target, 0o600)
    else:
        target.write_text(text, encoding="utf-8")
    return target


def load_config(*, path: Path | None = None) -> Config:
    target = path or config_path()
    if not target.is_file():
        raise ConfigMissing(
            f"No config found at {target}. Run `datahive init` first."
        )
    check_permissions(target)
    raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    lab_id = raw.get("lab_id", "")
    repo_id = raw.get("repo_id") or (f"sua-org/{lab_id}" if lab_id else "")
    hf_token = raw.get("hf_token") or raw.get("huggingface_token", "")
    return Config(
        lab_id=lab_id,
        repo_id=repo_id,
        hf_token=hf_token,
        endpoint=raw.get("endpoint"),
        created_at=raw.get("created_at", ""),
    )


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_utc_iso(value: "datetime | str") -> str:
    """ISO-8601 in UTC (+00:00). Naive datetimes are taken to be UTC."""
    dt = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


GITIGNORE_TEMPLATE = """\
# datahive-tools: never let the local index / secrets travel with your data.
.datahive/
"""


def maybe_write_gitignore(parent_dir: Path) -> Path | None:
    """Drop a .gitignore template into parent_dir if one doesn't already
    exist. This is a second line of defense only -- the primary one is that
    the config file is never written under a project directory at all."""
    gi = parent_dir / ".gitignore"
    if gi.exists():
        return None
    gi.write_text(GITIGNORE_TEMPLATE, encoding="utf-8")
    return gi
