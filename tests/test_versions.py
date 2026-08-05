"""Reading versions, judging a swap, and moving an install into the layout.

Everything here is checkable without a Factorio binary, which is the point: the
decisions this code makes are ones you find out are wrong by losing a world.

The fixtures build saves and version trees byte by byte from the formats
measured on 2.0.77 -- see :mod:`factorio_reforge.versions.savefile` and
:mod:`factorio_reforge.versions.binary` for where each number came from.
"""

from __future__ import annotations

import struct
import zipfile
from pathlib import Path

import pytest

from factorio_reforge.versions.binary import BinaryInfo, parse_version_output
from factorio_reforge.versions.compat import (
    BLOCK,
    ModCompat,
    blockers,
    check_switch,
    read_mod_series,
)
from factorio_reforge.versions.download import (
    DownloadError,
    download_url,
    parse_available_versions,
)
from factorio_reforge.versions.errors import VersionError
from factorio_reforge.versions.layout import SHARED_ENTRIES, Installation
from factorio_reforge.versions.savefile import MapVersion, parse_version, read_save_version

#: Verbatim from ``./bin/x64/factorio --version`` on the 2.0.77 headless build.
VERSION_OUTPUT = """Version: 2.0.77 (build 84539, linux64, headless)
Version: 64
Map input version: 1.0.0-0
Map output version: 2.0.77-0
"""


def write_save(path: Path, version=(2, 0, 77, 0), name: str = "world") -> Path:
    """A minimal file with the parts of a save this code reads."""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(f"{name}/level-init.dat", struct.pack("<4H", *version) + b"\x00rest")
        archive.writestr(f"{name}/level.dat0", b"")
    return path


def binary(release="2.0.77", output="2.0.77-0", input_="1.0.0-0") -> BinaryInfo:
    return BinaryInfo(
        version=parse_version(release),
        build=1,
        platform="linux64",
        flavour="headless",
        map_input=parse_version(input_),
        map_output=parse_version(output),
    )


# ---------------------------------------------------------------------------
# reading a save
# ---------------------------------------------------------------------------

class TestSaveVersion:
    def test_reads_the_version_the_save_was_written_by(self, tmp_path):
        assert read_save_version(write_save(tmp_path / "s.zip")) == MapVersion(2, 0, 77, 0)

    def test_finds_level_init_whatever_the_save_is_called(self, tmp_path):
        path = write_save(tmp_path / "s.zip", name="some other name")
        assert read_save_version(path).release == "2.0.77"

    def test_a_zip_that_is_not_a_save_is_an_error_not_a_guess(self, tmp_path):
        path = tmp_path / "s.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("readme.txt", "hello")
        with pytest.raises(VersionError, match="not a Factorio save"):
            read_save_version(path)

    def test_a_truncated_header_is_an_error(self, tmp_path):
        path = tmp_path / "s.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("w/level-init.dat", b"\x02\x00")
        with pytest.raises(VersionError, match="truncated"):
            read_save_version(path)

    def test_a_nonsense_version_is_refused(self, tmp_path):
        """A wrong offset would otherwise produce a plausible number.

        Reading four zeroes as "version 0.0.0" and letting a swap through on
        that basis is the failure this guards: the check would pass, and the
        world would be the thing that finds out.
        """
        path = write_save(tmp_path / "s.zip", version=(0, 0, 0, 0))
        with pytest.raises(VersionError, match="does not look like"):
            read_save_version(path)

    def test_a_missing_file_says_so(self, tmp_path):
        with pytest.raises(VersionError, match="no save"):
            read_save_version(tmp_path / "nope.zip")


class TestVersionText:
    @pytest.mark.parametrize(
        "text,expected",
        [("2.0.77", (2, 0, 77, 0)), ("2.0.77-0", (2, 0, 77, 0)), ("1.0.0-3", (1, 0, 0, 3))],
    )
    def test_parses_both_forms(self, text, expected):
        assert tuple(parse_version(text)) == expected

    def test_ordering_is_numeric_not_lexical(self):
        assert parse_version("2.0.9") < parse_version("2.0.77")

    def test_series_is_what_a_mod_pins_to(self):
        assert parse_version("2.0.77").series == "2.0"

    def test_rubbish_is_refused(self):
        with pytest.raises(VersionError):
            parse_version("latest")


# ---------------------------------------------------------------------------
# asking the binary
# ---------------------------------------------------------------------------

class TestBinaryOutput:
    def test_parses_the_real_output(self):
        info = parse_version_output(VERSION_OUTPUT)
        assert info.release == "2.0.77"
        assert info.build == 84539
        assert info.flavour == "headless"
        assert info.map_input == MapVersion(1, 0, 0, 0)
        assert info.map_output == MapVersion(2, 0, 77, 0)

    def test_the_second_version_line_is_not_mistaken_for_the_version(self):
        """``Version: 64`` is an internal API number and sits right underneath."""
        assert parse_version_output(VERSION_OUTPUT).release == "2.0.77"

    def test_a_save_from_the_same_build_loads(self):
        assert parse_version_output(VERSION_OUTPUT).can_load(MapVersion(2, 0, 77, 0))

    def test_a_save_from_a_newer_build_does_not(self):
        assert not parse_version_output(VERSION_OUTPUT).can_load(MapVersion(2, 0, 78, 0))

    def test_an_older_save_loads_because_factorio_migrates_forward(self):
        assert parse_version_output(VERSION_OUTPUT).can_load(MapVersion(1, 1, 110, 0))

    def test_without_the_map_lines_it_still_refuses_a_newer_save(self):
        """An older build might not print them; the conservative reading holds."""
        info = parse_version_output("Version: 1.1.110 (build 1, linux64, headless)\n")
        assert info.can_load(MapVersion(1, 1, 110, 0))
        assert not info.can_load(MapVersion(2, 0, 77, 0))

    def test_output_with_no_version_at_all_is_an_error(self):
        with pytest.raises(VersionError, match="did not report a version"):
            parse_version_output("bash: factorio: No such file or directory")


# ---------------------------------------------------------------------------
# judging a swap
# ---------------------------------------------------------------------------

class TestCheckSwitch:
    def keys(self, findings, severity=None):
        return [
            f.key for f in findings if severity is None or f.severity == severity
        ]

    def test_a_plain_upgrade_has_nothing_blocking(self):
        findings = check_switch(
            target=binary("2.0.78", output="2.0.78-0"),
            current_release="2.0.77",
            save=MapVersion(2, 0, 77, 0),
        )
        assert blockers(findings) == []

    def test_a_downgrade_past_the_world_is_blocked(self):
        findings = check_switch(
            target=binary("2.0.77"),
            current_release="2.0.78",
            save=MapVersion(2, 0, 78, 0),
        )
        assert "save_too_new" in self.keys(findings, BLOCK)

    def test_the_block_names_the_way_out(self):
        """Refusing is not enough: a downgrade is possible, just not alone."""
        findings = check_switch(
            target=binary("2.0.77"),
            current_release="2.0.78",
            save=MapVersion(2, 0, 78, 0),
        )
        finding = next(f for f in findings if f.key == "save_too_new")
        assert finding.values["max"] == "2.0.77-0"
        assert finding.values["save"] == "2.0.78-0"

    def test_pairing_an_old_world_with_the_old_binary_is_allowed(self):
        findings = check_switch(
            target=binary("2.0.77"),
            current_release="2.0.78",
            save=MapVersion(2, 0, 78, 0),
            paired_save=MapVersion(2, 0, 77, 0),
            paired_slot="pre-upgrade",
        )
        assert blockers(findings) == []
        assert "world_replaced" in self.keys(findings)

    def test_the_paired_world_is_the_one_checked_not_the_live_one(self):
        """The live world is about to be replaced, so its version is moot."""
        findings = check_switch(
            target=binary("2.0.77"),
            current_release="2.0.78",
            save=MapVersion(2, 0, 78, 0),
            paired_save=MapVersion(2, 0, 90, 0),
            paired_slot="3",
        )
        assert "save_too_new" in self.keys(findings, BLOCK)

    def test_an_unreadable_paired_slot_blocks(self):
        findings = check_switch(
            target=binary("2.0.77"),
            current_release="2.0.78",
            save=MapVersion(2, 0, 78, 0),
            paired_slot="3",
            paired_error="broken zip",
        )
        assert "paired_unreadable" in self.keys(findings, BLOCK)

    def test_an_unreadable_world_blocks_rather_than_being_shrugged_off(self):
        findings = check_switch(
            target=binary("2.0.78", output="2.0.78-0"),
            current_release="2.0.77",
            save=None,
            save_error="no save",
        )
        assert "save_unreadable" in self.keys(findings, BLOCK)

    def test_switching_to_what_is_already_running_is_refused(self):
        findings = check_switch(
            target=binary("2.0.77"), current_release="2.0.77",
            save=MapVersion(2, 0, 77, 0),
        )
        assert "same_version" in self.keys(findings, BLOCK)

    def test_a_patch_upgrade_does_not_disturb_mods(self):
        """Mods pin major.minor, so 2.0.77 to 2.0.78 cannot invalidate one."""
        findings = check_switch(
            target=binary("2.0.78", output="2.0.78-0"),
            current_release="2.0.77",
            save=MapVersion(2, 0, 77, 0),
            mods=[ModCompat("krastorio2", "1.3.24", "2.0")],
        )
        assert blockers(findings) == []

    def test_a_series_change_blocks_on_the_mods_it_would_break(self):
        findings = check_switch(
            target=binary("2.1.12", output="2.1.12-0"),
            current_release="2.0.77",
            save=MapVersion(2, 0, 77, 0),
            mods=[ModCompat("krastorio2", "1.3.24", "2.0"),
                  ModCompat("newthing", "1.0.0", "2.1")],
        )
        finding = next(f for f in findings if f.key == "mods_wrong_series")
        assert finding.values["count"] == 1
        assert "krastorio2" in finding.values["mods"]

    def test_players_online_is_a_warning_not_a_block(self):
        findings = check_switch(
            target=binary("2.0.78", output="2.0.78-0"),
            current_release="2.0.77",
            save=MapVersion(2, 0, 77, 0),
            online=["Alice", "Bob"],
        )
        assert blockers(findings) == []
        assert "players_online" in self.keys(findings)

    def test_blockers_come_first(self):
        findings = check_switch(
            target=binary("2.0.77"),
            current_release="2.0.78",
            save=MapVersion(2, 0, 78, 0),
            online=["Alice"],
        )
        assert findings[0].severity == BLOCK

    def test_everyone_is_told_their_client_has_to_match(self):
        """The one consequence no amount of care on the server can soften."""
        findings = check_switch(
            target=binary("2.0.78", output="2.0.78-0"),
            current_release="2.0.77",
            save=MapVersion(2, 0, 77, 0),
        )
        assert "clients_must_match" in self.keys(findings)


class TestModSeries:
    def mod(self, directory: Path, name: str, series: str, version="1.0.0"):
        path = directory / f"{name}_{version}.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(
                f"{name}_{version}/info.json",
                f'{{"name": "{name}", "version": "{version}", '
                f'"factorio_version": "{series}"}}',
            )
        return path

    def test_reads_what_each_mod_was_built_for(self, tmp_path):
        self.mod(tmp_path, "krastorio2", "2.0")
        assert read_mod_series(tmp_path) == [ModCompat("krastorio2", "1.0.0", "2.0")]

    def test_the_expansion_is_not_counted(self, tmp_path):
        """space-age ships inside data/ and moves with the binary.

        Measured: data/space-age/info.json on a 2.0.77 tree reads
        "version": "2.0.77". Treating a copy in mods/ as a blocker would refuse
        every upgrade for a reason that is not real.
        """
        self.mod(tmp_path, "space-age", "2.0", version="2.0.77")
        self.mod(tmp_path, "krastorio2", "2.0")
        assert [m.name for m in read_mod_series(tmp_path)] == ["krastorio2"]

    def test_a_directory_with_no_mods_is_empty_not_an_error(self, tmp_path):
        assert read_mod_series(tmp_path / "nope") == []


# ---------------------------------------------------------------------------
# the layout
# ---------------------------------------------------------------------------

def make_install(root: Path) -> Path:
    """An ordinary Factorio install, the shape install.sh leaves behind."""
    tree = root / "factorio"
    (tree / "bin" / "x64").mkdir(parents=True)
    (tree / "bin" / "x64" / "factorio").write_text("#!/bin/sh\n")
    (tree / "data" / "base").mkdir(parents=True)
    (tree / "saves").mkdir()
    (tree / "saves" / "reforge.zip").write_text("world")
    (tree / "mods").mkdir()
    (tree / "config").mkdir()
    (tree / "server-settings.json").write_text("{}")
    return tree


class TestInstallation:
    def test_an_ordinary_install_is_not_switchable(self, tmp_path):
        install = Installation(make_install(tmp_path))
        assert not install.is_managed
        assert install.active_version is None
        assert install.installed() == []

    def test_adopt_makes_the_live_path_a_link(self, tmp_path):
        tree = make_install(tmp_path)
        install = Installation(tree)
        install.adopt("2.0.77")

        assert install.is_managed
        assert install.active_version == "2.0.77"
        assert install.installed() == ["2.0.77"]
        assert tree.is_symlink()

    def test_adopt_keeps_the_world_reachable_at_the_same_path(self, tmp_path):
        """The whole point: working_directory and start_command do not change."""
        tree = make_install(tmp_path)
        Installation(tree).adopt("2.0.77")
        assert (tree / "saves" / "reforge.zip").read_text() == "world"

    def test_the_world_lives_outside_the_version_tree(self, tmp_path):
        """Otherwise saves written by the new build vanish on a rollback."""
        tree = make_install(tmp_path)
        install = Installation(tree)
        install.adopt("2.0.77")

        assert (install.shared_dir / "saves" / "reforge.zip").is_file()
        assert (install.version_dir("2.0.77") / "saves").is_symlink()

    def test_everything_shared_is_moved_out(self, tmp_path):
        tree = make_install(tmp_path)
        install = Installation(tree)
        install.adopt("2.0.77")
        moved = {e for e in SHARED_ENTRIES if (install.shared_dir / e).exists()}
        assert {"saves", "mods", "config", "server-settings.json"} <= moved

    def test_the_build_itself_stays_in_the_version_tree(self, tmp_path):
        tree = make_install(tmp_path)
        install = Installation(tree)
        install.adopt("2.0.77")
        data = install.version_dir("2.0.77") / "data"
        assert data.is_dir() and not data.is_symlink()

    def test_adopting_twice_is_refused(self, tmp_path):
        tree = make_install(tmp_path)
        install = Installation(tree)
        install.adopt("2.0.77")
        with pytest.raises(VersionError, match="already"):
            install.adopt("2.0.78")

    def test_a_directory_with_no_binary_is_not_an_install(self, tmp_path):
        (tmp_path / "factorio").mkdir()
        with pytest.raises(VersionError, match="no Factorio binary"):
            Installation(tmp_path / "factorio").adopt("2.0.77")

    def test_a_failed_adopt_puts_everything_back(self, tmp_path):
        """A half-migrated install is worse than an unmigrated one."""
        tree = make_install(tmp_path)
        install = Installation(tree)
        # Something already occupying shared/saves stops the migration midway,
        # after the tree has been renamed.
        (install.shared_dir / "saves").mkdir(parents=True)

        with pytest.raises(VersionError):
            install.adopt("2.0.77")

        assert tree.is_dir() and not tree.is_symlink()
        assert (tree / "saves" / "reforge.zip").read_text() == "world"
        assert (tree / "bin" / "x64" / "factorio").is_file()

    def test_a_resolved_path_no_longer_points_at_the_layout(self, tmp_path):
        """The trap that shipped: Path.resolve() follows the live symlink.

        config.resolve() ends in Path.resolve(), so working_dir_path lands on
        versions/2.0.77 rather than on the link. Everything then reads as an
        ordinary unadopted install -- and only after adopt has succeeded, which
        is the worst moment to discover it. version_manager builds the path
        without resolving for exactly this reason.
        """
        tree = make_install(tmp_path)
        Installation(tree).adopt("2.0.77")

        assert Installation(tree).is_managed
        assert not Installation(tree.resolve()).is_managed

    def test_activate_switches_which_tree_is_live(self, tmp_path):
        tree = make_install(tmp_path)
        install = Installation(tree)
        install.adopt("2.0.77")
        _add_tree(install, "2.0.78")

        install.activate("2.0.78")
        assert install.active_version == "2.0.78"
        assert (tree / "bin" / "x64" / "factorio").read_text() == "#!/bin/sh\n# 2.0.78\n"

    def test_activate_and_back_again(self, tmp_path):
        """Rolling back has to work when the network does not."""
        tree = make_install(tmp_path)
        install = Installation(tree)
        install.adopt("2.0.77")
        _add_tree(install, "2.0.78")

        install.activate("2.0.78")
        install.activate("2.0.77")
        assert install.active_version == "2.0.77"

    def test_activate_wires_up_a_tree_nobody_linked(self, tmp_path):
        """Otherwise the server comes up on an empty saves directory.

        A tree extracted by hand has no ``saves`` link in it, and Factorio
        would happily generate a new map rather than complain.
        """
        tree = make_install(tmp_path)
        install = Installation(tree)
        install.adopt("2.0.77")
        raw = install.versions_dir / "2.0.78" / "bin" / "x64"
        raw.mkdir(parents=True)
        (raw / "factorio").write_text("x")

        install.activate("2.0.78")
        assert (tree / "saves" / "reforge.zip").read_text() == "world"

    def test_activating_something_not_installed_is_refused(self, tmp_path):
        install = Installation(make_install(tmp_path))
        install.adopt("2.0.77")
        with pytest.raises(VersionError, match="not installed"):
            install.activate("2.0.99")

    def test_activate_on_an_unadopted_install_says_to_adopt(self, tmp_path):
        install = Installation(make_install(tmp_path))
        (install.versions_dir / "2.0.78" / "bin" / "x64").mkdir(parents=True)
        (install.versions_dir / "2.0.78" / "bin" / "x64" / "factorio").write_text("x")
        with pytest.raises(VersionError, match="adopt"):
            install.activate("2.0.78")

    def test_a_half_extracted_tree_is_not_offered(self, tmp_path):
        install = Installation(make_install(tmp_path))
        install.adopt("2.0.77")
        (install.versions_dir / "2.0.78").mkdir()
        assert install.installed() == ["2.0.77"]

    def test_versions_sort_numerically(self, tmp_path):
        install = Installation(make_install(tmp_path))
        install.adopt("2.0.9")
        _add_tree(install, "2.0.77")
        _add_tree(install, "2.1.3")
        assert install.installed() == ["2.0.9", "2.0.77", "2.1.3"]

    def test_removing_the_live_version_is_refused(self, tmp_path):
        install = Installation(make_install(tmp_path))
        install.adopt("2.0.77")
        with pytest.raises(VersionError, match="in use"):
            install.remove("2.0.77")

    def test_removing_another_version_works(self, tmp_path):
        install = Installation(make_install(tmp_path))
        install.adopt("2.0.77")
        _add_tree(install, "2.0.78")
        install.remove("2.0.78")
        assert install.installed() == ["2.0.77"]

    def test_link_shared_refuses_to_shadow_real_content(self, tmp_path):
        """A tree with real saves in it means something unexpected is there."""
        install = Installation(make_install(tmp_path))
        install.adopt("2.0.77")
        (install.versions_dir / "2.0.78" / "bin" / "x64").mkdir(parents=True)
        (install.versions_dir / "2.0.78" / "bin" / "x64" / "factorio").write_text("x")
        (install.versions_dir / "2.0.78" / "saves").mkdir()

        with pytest.raises(VersionError, match="shadow"):
            install.link_shared("2.0.78")


def _add_tree(install: Installation, version: str) -> None:
    tree = install.version_dir(version)
    (tree / "bin" / "x64").mkdir(parents=True)
    (tree / "bin" / "x64" / "factorio").write_text(f"#!/bin/sh\n# {version}\n")
    install.link_shared(version)


# ---------------------------------------------------------------------------
# what the updater returns
# ---------------------------------------------------------------------------

class TestAvailableVersions:
    """Sampled from updater.factorio.com: one request, 376 versions.

    The channel markers ride along as an entry with no from/to, which is why
    this endpoint is used instead of /api/latest-releases -- that one knows
    only what is newest, and "newest" is never the question when going back.
    """

    SAMPLE = {
        "core-linux_headless64": [
            {"from": "2.0.75", "to": "2.0.76"},
            {"from": "2.0.76", "to": "2.0.77"},
            {"from": "2.0.9", "to": "2.0.10"},
            {"experimental": "2.1.13", "stable": "2.0.77"},
        ]
    }

    def test_both_ends_of_every_step_count(self):
        """The newest is only ever a "to", the oldest only ever a "from"."""
        versions, _ = parse_available_versions(self.SAMPLE)
        assert "2.0.77" in versions and "2.0.9" in versions

    def test_versions_are_ordered_numerically(self):
        versions, _ = parse_available_versions(self.SAMPLE)
        assert versions == ["2.0.9", "2.0.10", "2.0.75", "2.0.76", "2.0.77"]

    def test_the_channel_markers_come_out_too(self):
        _, channels = parse_available_versions(self.SAMPLE)
        assert channels == {"experimental": "2.1.13", "stable": "2.0.77"}

    def test_the_marker_entry_is_not_read_as_a_version(self):
        versions, _ = parse_available_versions(self.SAMPLE)
        assert "2.1.13" not in versions

    def test_a_response_without_the_headless_key_is_an_error(self):
        with pytest.raises(DownloadError, match="headless version list"):
            parse_available_versions({"core-win64": []})

    def test_a_download_url_names_the_version_asked_for(self):
        assert download_url("2.0.76").endswith("/2.0.76/headless/linux64")
