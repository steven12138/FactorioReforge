"""Shared exception base.

A query can fail for two unrelated reasons -- the transport is down (RCON not
connected, connection lost, timeout) or the Lua itself failed (bad expression,
missing player). Plugin code almost always wants to handle both the same way:
tell the user it could not look something up. Giving them one base class means
that is a single ``except`` rather than a tuple everyone has to remember.
"""

from __future__ import annotations


class QueryError(Exception):
    """A query against the running server could not be answered."""
