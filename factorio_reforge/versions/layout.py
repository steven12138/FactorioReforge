"""Several Factorio trees side by side, with one of them wired up as live.

::

    server/
    ├── versions/
    │   ├── 2.0.77/          the unpacked headless tree: bin/ data/
    │   └── 2.0.78/
    ├── shared/              saves/ mods/ config/ server-settings.json ...
    └── factorio -> versions/2.0.78

**Why a symlink and not an in-place upgrade.** Rolling back has to work at the
worst moment -- the server is down, the new build did not come up, and someone
is watching. A symlink flip is local, instant and cannot fail halfway. Undoing
an in-place upgrade means downloading the old version again, over the network,
at exactly that moment.

**Why the mutable state is symlinked back in.** Factorio resolves its data
paths from the executable: ``config-path.cfg`` says
``config-path=__PATH__executable__/../../config`` and ``config.ini`` says
``write-data=__PATH__executable__/../..``. Those resolve inside the version
tree, so saves written by 2.0.78 would land in ``versions/2.0.78/saves`` and
vanish from view on a rollback. Linking ``saves``, ``mods`` and the settings
files back out to ``shared/`` keeps one world across every installed version,
which is the point.

**Why the live path keeps its name.** ``server/factorio`` stays where it was,
as a symlink, so ``working_directory`` and ``start_command`` need no edit and
any absolute path written into a config file still resolves.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from factorio_reforge.versions.errors import VersionError

#: What belongs to the world rather than to a build. Anything not listed stays
#: inside the version tree, which is right for ``bin``, ``data``, ``temp`` and
#: the per-run logs -- those describe the build that wrote them.
SHARED_ENTRIES: tuple[str, ...] = (
    "saves",
    "mods",
    "script-output",
    "config",
    "server-settings.json",
    "server-adminlist.json",
    "server-banlist.json",
    "server-whitelist.json",
    "player-data.json",
    "achievements.dat",
)

#: Relative to a version tree.
BINARY_SUFFIX = Path("bin/x64/factorio")


class Installation:
    """The directory layout above, rooted at wherever ``working_directory`` points.

    Constructing this touches nothing. Every method that changes the filesystem
    says so, and every one of them requires the server to be stopped.
    """

    def __init__(self, active_path: Path, versions_directory: Path | None = None):
        self.active_path = Path(active_path)
        self.root = self.active_path.parent
        self.versions_dir = Path(versions_directory) if versions_directory else self.root / "versions"
        self.shared_dir = self.root / "shared"

    # -- reading -------------------------------------------------------------

    @property
    def is_managed(self) -> bool:
        """True once :meth:`adopt` has run: the live path is a link into ``versions/``."""
        if not self.active_path.is_symlink():
            return False
        try:
            target = self.active_path.resolve()
            return target.parent == self.versions_dir.resolve()
        except OSError:
            return False

    @property
    def active_version(self) -> str | None:
        """The directory name ``server/factorio`` points at, or None if unmanaged."""
        return self.active_path.resolve().name if self.is_managed else None

    @property
    def active_binary(self) -> Path:
        """Always through the live path, so it is whatever is wired up right now."""
        return self.active_path / BINARY_SUFFIX

    def version_dir(self, version: str) -> Path:
        return self.versions_dir / version

    def binary_of(self, version: str) -> Path:
        return self.version_dir(version) / BINARY_SUFFIX

    def is_installed(self, version: str) -> bool:
        return self.binary_of(version).is_file()

    def installed(self) -> list[str]:
        """Every version tree that actually has a binary in it.

        A half-extracted directory is not offered, because the first thing that
        would happen to it is being made live.
        """
        if not self.versions_dir.is_dir():
            return []
        found = [
            entry.name
            for entry in self.versions_dir.iterdir()
            if entry.is_dir() and (entry / BINARY_SUFFIX).is_file()
        ]
        return sorted(found, key=_sort_key)

    # -- changing ------------------------------------------------------------

    def activate(self, version: str) -> None:
        """Point the live path at ``version``. The server must be stopped.

        The link is written next to its destination and renamed over it, so
        there is no moment where ``server/factorio`` does not exist.

        The shared paths are wired up first, every time. A tree that someone
        extracted by hand has no ``saves`` link in it, and making that tree
        live would give the server an empty world directory and a fresh map --
        which is a far worse outcome than the refusal that happens instead if
        the links cannot be made.
        """
        if not self.is_installed(version):
            raise VersionError(f"{version} is not installed under {self.versions_dir}")
        if self.active_path.exists() and not self.active_path.is_symlink():
            raise VersionError(
                f"{self.active_path} is a real directory, not a link. "
                "Run adopt first, so there is something to switch between."
            )
        self.link_shared(version)

        target = os.path.join(self.versions_dir.name, version)
        staging = self.active_path.with_name(self.active_path.name + ".switching")
        staging.unlink(missing_ok=True)
        staging.symlink_to(target, target_is_directory=True)
        os.replace(staging, self.active_path)

    def link_shared(self, version: str) -> list[str]:
        """Wire one version tree's mutable paths out to ``shared/``.

        Returns the entries linked. An entry that exists in the tree as real
        content is refused rather than deleted: on a freshly extracted tarball
        there is nothing to collide with, so a collision means something is
        there that was not expected, and the safe move is to stop.
        """
        tree = self.version_dir(version)
        if not tree.is_dir():
            raise VersionError(f"{tree} does not exist")

        linked = []
        for entry in SHARED_ENTRIES:
            source = self.shared_dir / entry
            if not source.exists():
                continue
            inside = tree / entry
            if inside.is_symlink():
                inside.unlink()
            elif inside.exists():
                raise VersionError(
                    f"{inside} already exists as real content; it would shadow "
                    f"{source}. Move or delete it, then try again."
                )
            inside.symlink_to(
                os.path.relpath(source, tree), target_is_directory=source.is_dir()
            )
            linked.append(entry)
        return linked

    def adopt(self, version: str) -> list[str]:
        """Convert an ordinary install into the layout above. Server stopped.

        Moves ``server/factorio`` to ``versions/<version>``, lifts the world out
        into ``shared/``, links it back in, and puts a symlink where the
        directory used to be. Every step is undone if a later one fails, so a
        failed adopt leaves the install exactly as it was found.
        """
        if self.is_managed:
            raise VersionError(f"{self.active_path} is already a managed layout")
        if self.active_path.is_symlink():
            raise VersionError(
                f"{self.active_path} is a symlink, but not into {self.versions_dir}. "
                "Point working_directory at the real install first."
            )
        if not self.active_path.is_dir():
            raise VersionError(f"{self.active_path} is not a directory")
        if not (self.active_path / BINARY_SUFFIX).is_file():
            raise VersionError(
                f"no Factorio binary at {self.active_path / BINARY_SUFFIX}; "
                "this does not look like a server install"
            )
        tree = self.version_dir(version)
        if tree.exists():
            raise VersionError(f"{tree} already exists")

        self.versions_dir.mkdir(parents=True, exist_ok=True)
        self.shared_dir.mkdir(parents=True, exist_ok=True)

        undo: list[tuple[Path, Path]] = []
        try:
            os.rename(self.active_path, tree)
            undo.append((tree, self.active_path))

            for entry in SHARED_ENTRIES:
                inside = tree / entry
                if not inside.exists() or inside.is_symlink():
                    continue
                outside = self.shared_dir / entry
                if outside.exists():
                    raise VersionError(
                        f"{outside} already exists; refusing to overwrite it with "
                        f"{inside}"
                    )
                os.rename(inside, outside)
                undo.append((outside, inside))

            self.link_shared(version)
            self.activate(version)
        except Exception:
            for source, destination in reversed(undo):
                try:
                    if destination.is_symlink():
                        destination.unlink()
                    os.rename(source, destination)
                except OSError:
                    # Report what could not be undone rather than swallowing it:
                    # a half-migrated tree needs a human, and silence would hide
                    # which half.
                    raise VersionError(
                        f"adopt failed and {source} could not be put back at "
                        f"{destination}. The install is half-migrated; move it "
                        "back by hand before starting the server."
                    ) from None
            raise
        return self.installed()

    def remove(self, version: str) -> None:
        """Delete one version tree. Never the live one."""
        if version == self.active_version:
            raise VersionError(f"{version} is the version in use; switch away from it first")
        tree = self.version_dir(version)
        if not tree.is_dir():
            raise VersionError(f"{version} is not installed")
        shutil.rmtree(tree)


def _sort_key(version: str) -> tuple:
    """Numeric where it can be, so 2.0.9 sorts before 2.0.77."""
    parts = version.replace("-", ".").split(".")
    return tuple(int(part) if part.isdigit() else 0 for part in parts), version
