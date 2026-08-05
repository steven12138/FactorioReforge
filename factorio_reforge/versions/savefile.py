"""Read the version a save was written by, without starting Factorio.

This is the half of the safety check that the binary cannot answer. Asking
Factorio to open a save it cannot read is a way to find out, but it costs a
stop, a failed start and a restore; reading eight bytes costs nothing and can
be done while the server is still up.

**The format, measured on a 2.0.77 save.** A save is a zip whose entries live
under one directory named after the save::

    probe/control.lua  probe/description.json  probe/info.json
    probe/level-init.dat  probe/level.dat0  probe/level.datmetadata
    probe/script.dat

``level-init.dat`` opens with four little-endian ``uint16``::

    02 00  00 00  4d 00  00 00   ->  2.0.77-0

which is exactly what the binary that wrote it reports as its *map output
version*. ``info.json`` is not useful here -- on the sampled save it contains
the four bytes ``null``.
"""

from __future__ import annotations

import re
import struct
import zipfile
from pathlib import Path
from typing import NamedTuple

from factorio_reforge.versions.errors import VersionError

#: The header is four uint16 and nothing else we need; the bytes after it are a
#: flag and then length-prefixed strings (the scenario name, then the mod list).
_HEADER = struct.Struct("<4H")

_VERSION_TEXT = re.compile(r"^\s*(\d+)\.(\d+)\.(\d+)(?:-(\d+))?\s*$")


class MapVersion(NamedTuple):
    """A Factorio version as the engine orders it: ``main.major.minor-dev``.

    Tuple ordering is the version ordering, which is the only comparison this
    module needs and the reason this is a NamedTuple.
    """

    main: int
    major: int
    minor: int
    dev: int = 0

    def __str__(self) -> str:
        return f"{self.main}.{self.major}.{self.minor}-{self.dev}"

    @property
    def release(self) -> str:
        """The form people and download URLs use: ``2.0.77``, no ``-0``."""
        return f"{self.main}.{self.major}.{self.minor}"

    @property
    def series(self) -> str:
        """``2.0`` -- what a mod's ``factorio_version`` is written against."""
        return f"{self.main}.{self.major}"


def parse_version(text: str) -> MapVersion:
    """``"2.0.77"`` or ``"2.0.77-0"`` to a :class:`MapVersion`."""
    match = _VERSION_TEXT.match(text or "")
    if match is None:
        raise VersionError(f"{text!r} is not a Factorio version like 2.0.77")
    main, major, minor, dev = match.groups()
    return MapVersion(int(main), int(major), int(minor), int(dev or 0))


def read_save_version(path: Path) -> MapVersion:
    """The version that wrote the save at ``path``.

    Raises :class:`VersionError` rather than returning a guess. A caller that
    cannot tell what a save is must refuse a downgrade, not assume the best.
    """
    if not path.is_file():
        raise VersionError(f"there is no save at {path}")
    try:
        with zipfile.ZipFile(path) as archive:
            name = _level_init_entry(archive)
            with archive.open(name) as handle:
                head = handle.read(_HEADER.size)
    except (zipfile.BadZipFile, OSError) as exc:
        raise VersionError(f"{path.name} could not be read as a save: {exc}") from exc

    if len(head) < _HEADER.size:
        raise VersionError(f"{path.name} has a truncated {Path(name).name}")
    version = MapVersion(*_HEADER.unpack(head))
    if version.main == 0 or version.main > 100:
        # Not a sanity check for its own sake: a wrong offset would otherwise
        # produce a plausible-looking number and silently authorise a swap.
        raise VersionError(f"{path.name} does not look like a Factorio save (read {version})")
    return version


def _level_init_entry(archive: zipfile.ZipFile) -> str:
    for name in archive.namelist():
        if name.rsplit("/", 1)[-1] == "level-init.dat":
            return name
    raise VersionError("no level-init.dat in the archive; this is not a Factorio save")
