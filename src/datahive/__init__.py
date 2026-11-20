"""datahive-tools: client package for collecting, validating, annotating and
uploading HiveBoard manipulation episodes.

Public library API (also used internally by the CLI and the local GUI, so
all three surfaces stay in sync):

    from datahive import EpisodeWriter, RobotProfile, TrialAnnotation
"""

from datahive.episode import EpisodeWriter, read_header, read_trajectory, resolve_episode
from datahive.errors import DatahiveError
from datahive.profile import RobotProfile, load_profile, write_profile_skeleton
from datahive.schema import EpisodeHeader, FailureCause, Outcome, Strategy, TrialAnnotation
from datahive.version import __version__

__all__ = [
    "EpisodeWriter",
    "read_header",
    "read_trajectory",
    "resolve_episode",
    "DatahiveError",
    "RobotProfile",
    "load_profile",
    "write_profile_skeleton",
    "EpisodeHeader",
    "FailureCause",
    "Outcome",
    "Strategy",
    "TrialAnnotation",
]
