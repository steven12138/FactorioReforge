"""Parser tests driven by lines actually observed on a 2.0.77 headless server."""

import pytest

from factorio_reforge.core.handler import FactorioHandler
from factorio_reforge.core.info import InfoKind


@pytest.fixture
def h():
    return FactorioHandler()


class TestEngineLog:
    def test_with_level_and_source(self, h):
        info = h.parse_server_stdout(
            "   0.578 Info ServerMultiplayerManager.cpp:808: updateTick(926) "
            "changing state from(CreatingGame) to(InGame)"
        )
        assert info.kind is InfoKind.ENGINE_LOG
        assert info.level == "Info"
        assert info.elapsed == pytest.approx(0.578)
        assert info.content.endswith("to(InGame)")
        assert h.is_startup_done(info)

    @pytest.mark.parametrize(
        "line, content",
        [
            ("   0.577 Hosting game at IP ADDR:({0.0.0.0:34199})",
             "Hosting game at IP ADDR:({0.0.0.0:34199})"),
            ("   0.543 Loading map /tmp/probe.zip: 863501 bytes.",
             "Loading map /tmp/probe.zip: 863501 bytes."),
            ("  16.011 Received SIGINT, shutting down", "Received SIGINT, shutting down"),
            ("  16.528 Goodbye", "Goodbye"),
        ],
    )
    def test_bare_elapsed_prefix_has_no_level(self, h, line, content):
        """These are the lines that broke a level-mandatory regex."""
        info = h.parse_server_stdout(line)
        assert info.kind is InfoKind.ENGINE_PLAIN
        assert info.level is None
        assert info.content == content

    def test_error_level(self, h):
        info = h.parse_server_stdout(
            "   5.998 Error InterruptibleStdioStream.cpp:55: Got EOF on stdin; closing"
        )
        assert info.level == "Error"
        assert info.content == "Got EOF on stdin; closing"

    def test_rcon_marker(self, h):
        rcon = h.parse_server_stdout(
            "   0.578 Info RemoteCommandProcessor.cpp:126: Starting RCON interface at IP ADDR:({0.0.0.0:27019})"
        )
        assert h.is_rcon_ready(rcon)

    @pytest.mark.parametrize(
        "line",
        [
            # /server-save goes through AppManager...
            "   4.619 Info AppManager.cpp:419: Saving finished",
            # ...while the save taken during shutdown goes through MainLoop.
            "  16.030 Info MainLoop.cpp:448: Saving progress: 100.000000%",
        ],
    )
    def test_both_save_completion_shapes_count(self, h, line):
        assert h.is_save_done(h.parse_server_stdout(line))

    def test_save_start_is_not_mistaken_for_completion(self, h):
        starting = h.parse_server_stdout(
            "   4.574 Info AppManager.cpp:416: Saving game as /srv/saves/reforge.zip"
        )
        assert not h.is_save_done(starting)
        assert not h.is_save_done(h.parse_server_stdout("Saving the map"))


class TestGameEvent:
    def test_chat_from_player(self, h):
        info = h.parse_server_stdout("2026-08-02 02:16:35 [CHAT] Alice: hello world")
        assert info.kind is InfoKind.GAME_EVENT
        assert info.tag == "CHAT"
        assert info.player == "Alice"
        assert info.content == "hello world"
        assert info.is_user
        assert not info.is_echo

    def test_own_say_is_an_echo_and_not_user_input(self, h):
        """Without this the Telegram bridge would relay its own messages forever."""
        info = h.parse_server_stdout("2026-08-02 02:16:35 [CHAT] <server>: hello from probe")
        assert info.player == "<server>"
        assert info.is_echo
        assert not info.is_user

    def test_chat_containing_a_colon(self, h):
        info = h.parse_server_stdout("2026-08-02 02:16:35 [CHAT] Bob: ratio is 1:2 here")
        assert info.player == "Bob"
        assert info.content == "ratio is 1:2 here"

    def test_join_and_leave(self, h):
        join = h.parse_server_stdout("2026-08-02 02:16:35 [JOIN] Alice joined the game")
        assert join.tag == "JOIN" and join.player == "Alice"
        leave = h.parse_server_stdout("2026-08-02 02:17:35 [LEAVE] Alice left the game")
        assert leave.tag == "LEAVE" and leave.player == "Alice"

    def test_unknown_tag_is_kept_and_reported_once(self, h):
        h.parse_server_stdout("2026-08-02 02:16:35 [BRANDNEW] something happened")
        h.parse_server_stdout("2026-08-02 02:16:36 [BRANDNEW] again")
        assert h.take_new_unknown_tags() == {"BRANDNEW"}
        assert h.take_new_unknown_tags() == set()


class TestCommandResponse:
    @pytest.mark.parametrize("line", ["Players (0):", "2.0.77", "7 seconds", ""])
    def test_unprefixed_lines_fall_back_rather_than_raise(self, h, line):
        info = h.parse_server_stdout(line)
        assert info.kind is InfoKind.COMMAND_RESPONSE
        assert info.content == line


def test_ansi_codes_are_stripped_from_content_but_not_from_echo(h):
    info = h.parse_server_stdout("\x1b[32m  1.000 Hosting game\x1b[0m")
    assert info.content == "Hosting game"
    assert "\x1b[32m" in info.raw_content
