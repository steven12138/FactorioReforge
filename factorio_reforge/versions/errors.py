"""One exception type for everything version-related."""

from __future__ import annotations


class VersionError(Exception):
    """Something about a version, a binary or an installation layout is wrong."""
