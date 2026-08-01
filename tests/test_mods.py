"""Mod portal parsing and local mod-directory management.

The behaviours locked down here were all found by running against the real
portal and a real 2.0.77 server, not by reading docs.
"""

import json
import zipfile

import pytest

from factorio_reforge.mods.manager import BUILTIN_MODS, ModError, ModManager
from factorio_reforge.mods.portal import (
    ModSummary,
    Release,
    parse_dependency,
    read_player_data_credentials,
)

pytestmark = pytest.mark.asyncio


class TestDependencyParsing:
    @pytest.mark.parametrize(
        "entry, expected",
        [
            ("base >= 2.1.7", ("base", ">= 2.1.7")),
            ("flib", ("flib", "")),
            ("Krastorio2Assets >= 2.1.0", ("Krastorio2Assets", ">= 2.1.0")),
            # ~ and + only affect load order, but must still be installed.
            ("~ some-mod >= 1.0", ("some-mod", ">= 1.0")),
            ("+ ChangeInserterDropLane >= 1.3.0", ("ChangeInserterDropLane", ">= 1.3.0")),
        ],
    )
    def test_required_forms(self, entry, expected):
        assert parse_dependency(entry) == expected

    @pytest.mark.parametrize(
        "entry",
        [
            "? flib >= 0.16.2",       # optional
            "(?) Aircraft >= 1.6.6",  # hidden optional
            "! bobores",              # incompatible
        ],
    )
    def test_entries_that_must_not_be_installed(self, entry):
        """Installing every optional dependency would drag in dozens of mods."""
        assert parse_dependency(entry)[0] is None

    def test_required_dependencies_skips_base_and_optionals(self):
        release = Release(
            version="2.1.2", file_name="x.zip", download_url="/d", sha1="", released_at="",
            dependencies=[
                "base >= 2.1.7",
                "Krastorio2Assets >= 2.1.0",
                "? flib >= 0.16.2",
                "(?) Aircraft",
                "! conflicting",
                "+ ChangeInserterDropLane >= 1.3.0",
            ],
        )
        assert release.required_dependencies() == [
            ("Krastorio2Assets", ">= 2.1.0"),
            ("ChangeInserterDropLane", ">= 1.3.0"),
        ]


class TestRelease:
    def test_from_dict_lifts_factorio_version_out_of_info_json(self):
        release = Release.from_dict({
            "version": "0.16.5",
            "file_name": "flib_0.16.5.zip",
            "download_url": "/download/flib/abc",
            "sha1": "deadbeef",
            "released_at": "2025-01-01T00:00:00Z",
            "info_json": {"factorio_version": "2.0", "dependencies": ["base >= 2.0"]},
        })
        assert release.factorio_version == "2.0"
        assert release.dependencies == ["base >= 2.0"]

    def test_missing_info_json_does_not_raise(self):
        assert Release.from_dict({"version": "1.0"}).factorio_version == ""


class TestModSummary:
    def test_from_dict_uses_latest_release(self):
        mod = ModSummary.from_dict({
            "name": "Krastorio2", "title": "Krastorio 2", "owner": "raiguard",
            "summary": "overhaul", "downloads_count": 385068,
            "latest_release": {"version": "2.1.2", "info_json": {"factorio_version": "2.0"}},
        })
        assert mod.latest_version == "2.1.2"
        assert "385,068" in mod.describe()


class TestCredentials:
    def test_read_from_player_data(self, tmp_path):
        path = tmp_path / "player-data.json"
        path.write_text(json.dumps({"service-username": "steven", "service-token": "secret"}))
        assert read_player_data_credentials(path) == ("steven", "secret")

    def test_missing_file_returns_blanks_rather_than_raising(self, tmp_path):
        assert read_player_data_credentials(tmp_path / "nope.json") == ("", "")

    def test_a_file_with_no_login_returns_blanks(self, tmp_path):
        path = tmp_path / "player-data.json"
        path.write_text(json.dumps({"last-played": {}}))
        assert read_player_data_credentials(path) == ("", "")


def make_mod_zip(directory, name, version):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}_{version}.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            f"{name}_{version}/info.json", json.dumps({"name": name, "version": version})
        )
    return path


@pytest.fixture
def mods_dir(tmp_path):
    directory = tmp_path / "mods"
    directory.mkdir()
    (directory / "mod-list.json").write_text(
        json.dumps({"mods": [{"name": "base", "enabled": True}]})
    )
    return directory


@pytest.fixture
def manager(mods_dir):
    return ModManager(mods_dir, portal=None)


class TestListing:
    def test_zips_and_mod_list_entries_are_merged(self, manager, mods_dir):
        make_mod_zip(mods_dir, "flib", "0.16.5")
        (mods_dir / "mod-list.json").write_text(
            json.dumps({"mods": [
                {"name": "base", "enabled": True},
                {"name": "flib", "enabled": False},
            ]})
        )
        flib = next(m for m in manager.list_installed() if m.name == "flib")
        assert flib.version == "0.16.5"
        assert flib.enabled is False

    def test_a_zip_with_no_mod_list_entry_still_shows_up(self, manager, mods_dir):
        """An orphan zip is something the operator needs to see, not hide."""
        make_mod_zip(mods_dir, "orphan", "1.0.0")
        assert any(m.name == "orphan" for m in manager.list_installed())

    def test_builtin_mods_are_flagged(self, manager):
        base = next(m for m in manager.list_installed() if m.name == "base")
        assert base.builtin

    def test_a_corrupt_mod_list_is_reported_not_swallowed(self, manager, mods_dir):
        (mods_dir / "mod-list.json").write_text("{{{ not json")
        with pytest.raises(ModError, match="unreadable"):
            manager.list_installed()


class TestToggling:
    async def test_enable_and_disable_round_trip(self, manager, mods_dir):
        make_mod_zip(mods_dir, "flib", "0.16.5")
        assert await manager.set_enabled("flib", False) is True
        assert manager.get_installed("flib").enabled is False
        assert await manager.set_enabled("flib", True) is True
        assert manager.get_installed("flib").enabled is True

    async def test_base_cannot_be_disabled(self, manager):
        with pytest.raises(ModError, match="base"):
            await manager.set_enabled("base", False)

    async def test_toggling_an_uninstalled_mod_is_false(self, manager):
        assert await manager.set_enabled("nothing-here", True) is False


class TestRemoval:
    async def test_remove_deletes_the_zip_and_the_entry(self, manager, mods_dir):
        path = make_mod_zip(mods_dir, "flib", "0.16.5")
        await manager.set_enabled("flib", True)
        assert await manager.remove("flib") is True
        assert not path.exists()
        assert manager.get_installed("flib") is None

    @pytest.mark.parametrize("name", sorted(BUILTIN_MODS))
    async def test_builtin_mods_cannot_be_removed(self, manager, name):
        with pytest.raises(ModError, match="ships with the game"):
            await manager.remove(name)

    async def test_removing_something_absent_is_false(self, manager):
        assert await manager.remove("nothing-here") is False


class TestIntentSurvivesFactorio:
    """A running Factorio rewrites mod-list.json from memory when it exits.

    Measured on 2.0.77: a mod enabled mid-session was gone from the file after
    the server stopped. The intent file is what lets us put it back.
    """

    async def test_reapply_restores_an_entry_factorio_dropped(self, manager, mods_dir):
        make_mod_zip(mods_dir, "flib", "0.16.5")
        await manager.set_enabled("flib", True)

        # Simulate Factorio writing its own idea of the list on exit.
        (mods_dir / "mod-list.json").write_text(
            json.dumps({"mods": [{"name": "base", "enabled": True}]})
        )
        assert manager.get_installed("flib").enabled is True  # zip default

        changed = manager.reapply_intent()
        assert changed == ["+flib"]
        listed = json.loads((mods_dir / "mod-list.json").read_text())
        assert {"name": "flib", "enabled": True} in listed["mods"]

    async def test_reapply_preserves_a_disabled_state(self, manager, mods_dir):
        make_mod_zip(mods_dir, "flib", "0.16.5")
        await manager.set_enabled("flib", False)
        (mods_dir / "mod-list.json").write_text(
            json.dumps({"mods": [{"name": "base", "enabled": True}, {"name": "flib", "enabled": True}]})
        )
        assert manager.reapply_intent() == ["~flib"]
        assert manager.get_installed("flib").enabled is False

    async def test_reapply_drops_entries_whose_zip_is_gone(self, manager, mods_dir):
        """Otherwise a removed mod comes back as a phantom entry forever."""
        make_mod_zip(mods_dir, "flib", "0.16.5")
        await manager.set_enabled("flib", True)
        (mods_dir / "flib_0.16.5.zip").unlink()
        (mods_dir / "mod-list.json").write_text(
            json.dumps({"mods": [{"name": "base", "enabled": True}, {"name": "flib", "enabled": True}]})
        )
        assert manager.reapply_intent() == ["-flib"]
        listed = json.loads((mods_dir / "mod-list.json").read_text())
        assert all(entry["name"] != "flib" for entry in listed["mods"])

    async def test_reapply_is_a_noop_when_nothing_drifted(self, manager, mods_dir):
        make_mod_zip(mods_dir, "flib", "0.16.5")
        await manager.set_enabled("flib", True)
        assert manager.reapply_intent() == []

    def test_reapply_without_any_recorded_intent_does_nothing(self, manager):
        assert manager.reapply_intent() == []


class TestModListWriting:
    async def test_base_is_written_first_as_factorio_does(self, manager, mods_dir):
        make_mod_zip(mods_dir, "zzz", "1.0.0")
        make_mod_zip(mods_dir, "aaa", "1.0.0")
        await manager.set_enabled("zzz", True)
        await manager.set_enabled("aaa", True)
        listed = json.loads((mods_dir / "mod-list.json").read_text())
        assert listed["mods"][0]["name"] == "base"
