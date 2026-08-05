"""Which Factorio build the server runs, and how to change it safely.

A version swap is not a download problem. Save format upgrades are a one-way
door: once 2.0.78 has loaded and written the world, 2.0.77 can never open it
again. So the whole thing is shaped like a restore -- stage, back up, swap,
verify, put it back if it did not come up -- and not like ``!!mod install``.

Nothing here hardcodes a compatibility rule, because it does not have to.
Measured on 2.0.77, the binary declares its own window::

    $ ./bin/x64/factorio --version
    Version: 2.0.77 (build 84539, linux64, headless)
    Map input version: 1.0.0-0
    Map output version: 2.0.77-0

and the save declares its own version in the first eight bytes of
``level-init.dat``. Between the two, whether a swap will work is arithmetic
rather than a guess -- see :mod:`~factorio_reforge.versions.binary` and
:mod:`~factorio_reforge.versions.savefile`.
"""

from factorio_reforge.versions.binary import (
    BinaryInfo,
    can_load,
    parse_version_output,
    read_binary,
)
from factorio_reforge.versions.download import (
    DownloadError,
    download_url,
    fetch_latest_releases,
    install_version,
)
from factorio_reforge.versions.errors import VersionError
from factorio_reforge.versions.layout import SHARED_ENTRIES, Installation
from factorio_reforge.versions.savefile import MapVersion, parse_version, read_save_version

__all__ = [
    "SHARED_ENTRIES",
    "BinaryInfo",
    "DownloadError",
    "Installation",
    "MapVersion",
    "VersionError",
    "can_load",
    "download_url",
    "fetch_latest_releases",
    "install_version",
    "parse_version",
    "parse_version_output",
    "read_binary",
    "read_save_version",
]
