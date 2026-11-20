"""Convert datasets recorded outside HiveBoard into ``datahive_episode_v1``.

The package writes through the ``datahive`` package's own API so that the
output is accepted by ``datahive validate``, ``datahive check`` and
``datahive upload`` without modification.
"""

from datahive.version import __version__ as _datahive_version

# The converter ships inside datahive-tools and follows the package version.
__version__ = _datahive_version

