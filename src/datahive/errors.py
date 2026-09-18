"""Error hierarchy for datahive-tools.

All errors that should be shown to the user as a clean one-paragraph message
(never a Python traceback) derive from ``DatahiveError``. The CLI's top-level
exception hook catches exactly this family; anything else is a real bug and
is allowed to raise normally.
"""

from __future__ import annotations


class DatahiveError(Exception):
    """Base class for all expected, user-facing datahive-tools errors."""


class ConfigError(DatahiveError):
    """Problems with ~/.datahive/config.yaml (missing, insecure, invalid)."""


class ConfigInsideGitRepo(ConfigError):
    """Refused to write the config file because it would land inside a git repo."""


class InsecureConfigPermissions(ConfigError):
    """The config file is group- or world-readable."""


class ConfigMissing(ConfigError):
    """datahive init has not been run yet."""


class ProfileError(DatahiveError):
    """Problems with samples/robot_profile.yaml."""


class ProfileMissing(ProfileError):
    pass


class ProfileIncomplete(ProfileError):
    pass


class EpisodeError(DatahiveError):
    """Problems with a specific episode's files."""


class EpisodeNotFound(EpisodeError):
    pass


class ValidationError(DatahiveError):
    """The episode failed schema/profile validation."""

    def __init__(self, message: str, problems: list[str] | None = None):
        super().__init__(message)
        self.problems = problems or []


class AnnotationError(DatahiveError):
    """The trial annotation supplied is invalid or incomplete."""


class HubError(DatahiveError):
    """Something went wrong talking to the Hugging Face Hub."""
