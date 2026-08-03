"""Leave a message for somebody who is not here.

Servers where people play in different timezones lose most of what they say to
each other: "I moved the iron bus" is said to an empty room and then has to be
said again tomorrow. A mailbox is the oldest fix there is, and on this framework
it is almost free -- the join event and per-plugin storage already exist.

Delivery is deliberately *not* instant-on-join. A player who has just connected
is looking at a loading screen and then at their character; a message printed in
that moment scrolls past behind the join spam. It waits a few seconds.
"""

from __future__ import annotations

import asyncio
import json
import time

from factorio_reforge.command.builder import GreedyText, Literal, Text
from factorio_reforge.core.errors import QueryError
from factorio_reforge.permission import PermissionLevel

PLUGIN_METADATA = {
    "id": "mail",
    "version": "1.0.0",
    "name": "Mail",
    "description": "Leave messages for players who are offline",
    "author": "FactorioReforge",
    "dependencies": {"factorio_reforge": ">=0.1.0"},
}

DEFAULT_CONFIG = {
    #: Seconds to wait after a join before delivering. Long enough that the
    #: player is in the world and looking at chat rather than a loading screen.
    "deliver_after_seconds": 8,
    #: Messages kept per recipient. Beyond this the oldest is dropped.
    "max_per_player": 20,
    "max_length": 200,
    #: Let anyone write to everyone at once. Off by default: a broadcast
    #: mailbox is a megaphone, and those get abused before they get used.
    "allow_broadcast_permission": "admin",
}

_state: dict = {}


def on_load(server, prev):
    config = server.load_config_simple("config.json", DEFAULT_CONFIG)
    _state.clear()
    _state.update(config=config, box=_load(server), tasks=set())

    broadcast = _parse_level(config.get("allow_broadcast_permission", "admin"), server)
    server.register_command(
        Literal("!!mail")
        .requires(PermissionLevel.USER)
        .runs(_cmd_read)
        .then(Literal("read").runs(_cmd_read))
        .then(Literal("clear").runs(_cmd_clear))
        .then(Literal("all").requires(broadcast)
              .then(GreedyText("message").runs(_cmd_broadcast)))
        .then(Text("player").then(GreedyText("message").runs(_cmd_send)))
    )
    server.register_help_message(
        "!!mail <player> <message>", server.tr("help"), PermissionLevel.USER,
        detail=(server.tr("detail.read"),),
    )


async def on_unload(server):
    for task in _state.get("tasks") or ():
        task.cancel()
    if _state.get("box"):
        _save(server)
    _state.clear()


def _parse_level(value, server) -> PermissionLevel:
    try:
        return PermissionLevel.parse(value)
    except ValueError:
        server.logger.warning("broadcast permission %r is not a level; using admin", value)
        return PermissionLevel.ADMIN


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def _path(server):
    return server.get_data_folder() / "mailbox.json"


def _load(server) -> dict[str, list[dict]]:
    path = _path(server)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        server.logger.error("The mailbox is unreadable (%s); starting empty", exc)
        return {}
    return data if isinstance(data, dict) else {}


def _save(server) -> None:
    path = _path(server)
    temp = path.with_suffix(".json.tmp")
    temp.write_text(
        json.dumps(_state["box"], indent=2, ensure_ascii=False), encoding="utf-8"
    )
    temp.replace(path)


# ---------------------------------------------------------------------------
# Pure logic
# ---------------------------------------------------------------------------

def key_for(player: str) -> str:
    """Mailboxes are case-insensitive; Factorio names are not reliably typed."""
    return player.strip().lower()


def deliver_to(box: dict, player: str, message: dict, limit: int) -> None:
    """Append, dropping the oldest past ``limit``.

    Dropping the oldest rather than refusing the newest: a full mailbox is
    usually a player who has not logged in for a month, and the message that
    matters is the one just written.
    """
    inbox = box.setdefault(key_for(player), [])
    inbox.append(message)
    if len(inbox) > limit:
        del inbox[: len(inbox) - limit]


def take(box: dict, player: str) -> list[dict]:
    """Read and empty a mailbox."""
    return box.pop(key_for(player), [])


def format_age(seconds: float) -> str:
    minutes = int(seconds / 60)
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h{minutes:02d}m"
    days, hours = divmod(hours, 24)
    return f"{days}d{hours:02d}h"


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

async def _cmd_send(source, ctx):
    server = source.server
    config = _state["config"]
    recipient = ctx["player"].strip()
    text = ctx["message"].strip()[: int(config.get("max_length", 200))]
    if not text:
        await source.reply(server.tr("error.empty"))
        return

    sender = source.player or server.tr("common.console")
    deliver_to(_state["box"], recipient, {
        "from": sender, "text": text, "at": time.time(),
    }, int(config.get("max_per_player", 20)))
    _save(server)

    await source.reply(server.tr("send.stored", player=recipient))
    # If they are online, say it now as well -- waiting for them to reconnect
    # to hear something said while they were standing there is absurd.
    try:
        online = await server.get_online_players()
    except QueryError:
        return
    if any(key_for(name) == key_for(recipient) for name in online):
        await _flush(server, recipient)


async def _cmd_broadcast(source, ctx):
    server = source.server
    text = ctx["message"].strip()
    if not text:
        await source.reply(server.tr("error.empty"))
        return
    try:
        everyone = await server.get_all_players()
    except QueryError as exc:
        await source.reply(server.tr("error.no_players", error=exc))
        return

    sender = source.player or server.tr("common.console")
    limit = int(_state["config"].get("max_per_player", 20))
    count = 0
    for entry in everyone:
        name = entry.get("name")
        if not name or key_for(name) == key_for(sender):
            continue
        deliver_to(_state["box"], name, {
            "from": sender, "text": text, "at": time.time(), "broadcast": True,
        }, limit)
        count += 1
    _save(server)
    await source.reply(server.tr("send.broadcast", count=count))


async def _cmd_read(source):
    server = source.server
    if source.player is None:
        await source.reply(server.tr("error.console_has_no_mail"))
        return
    await _flush(server, source.player, announce_empty=True)


async def _cmd_clear(source):
    server = source.server
    if source.player is None:
        await source.reply(server.tr("error.console_has_no_mail"))
        return
    messages = take(_state["box"], source.player)
    _save(server)
    await source.reply(server.tr("clear.done", count=len(messages)))


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------

async def on_player_joined(server, player, info=None):
    delay = max(0, int((_state.get("config") or {}).get("deliver_after_seconds", 8)))
    task = asyncio.create_task(_deliver_later(server, player, delay))
    _state["tasks"].add(task)
    task.add_done_callback(_state["tasks"].discard)


async def _deliver_later(server, player: str, delay: int) -> None:
    try:
        await asyncio.sleep(delay)
        await _flush(server, player)
    except asyncio.CancelledError:
        pass
    except Exception:
        server.logger.exception("Mail delivery failed for %s", player)


async def _flush(server, player: str, *, announce_empty: bool = False) -> None:
    messages = take(_state["box"], player)
    if not messages:
        if announce_empty:
            await server.tell(player, server.tr("read.empty"))
        return
    _save(server)

    await server.tell(player, server.tr("read.header", count=len(messages)))
    now = time.time()
    for message in messages:
        await server.tell(player, server.tr(
            "read.entry",
            sender=message.get("from", "?"),
            ago=format_age(now - float(message.get("at", now))),
            text=message.get("text", ""),
        ))


# -- for other plugins -------------------------------------------------------

def pending_for(player: str) -> int:
    """How many messages are waiting, e.g. for a join MOTD to mention."""
    return len((_state.get("box") or {}).get(key_for(player), []))
