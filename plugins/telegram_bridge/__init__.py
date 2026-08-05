"""Two-way bridge between the Factorio server and a Telegram chat.

Relays chat both directions and exposes the server controls worth having on a
phone. It is also the **Telegram service** other plugins build on: see
``service.py`` for the registry, and ``mod_manager`` for a plugin that uses it.

Setup:
  1. pip install "python-telegram-bot>=21"
  2. Talk to @BotFather, create a bot, copy the token.
  3. Start FactorioReforge once to generate
     ``config/telegram_bridge/config.json``, then fill in ``token``.
  4. Send the bot a message and check the log: it prints the chat id of any
     unauthorised sender, so you can paste it into ``allowed_chat_ids``.
  5. ``!!FR plugin reload telegram_bridge``

Security: the token lives in ``config/``, outside the repo; only listed chat ids
get any answer at all; anything destructive goes through an inline-button
confirmation; and ``/cmd``, which runs arbitrary commands, needs ``owner``.
"""

from __future__ import annotations

import asyncio
import contextlib
import html

from factorio_reforge.command.source import PluginCommandSource
from factorio_reforge.core.errors import QueryError
from factorio_reforge.core.info import Info, InfoActionFlag, InfoSource
from factorio_reforge.permission import PermissionLevel
from factorio_reforge.plugin.events import event_listener

from .service import READY_EVENT, TelegramContext, TelegramService

PLUGIN_METADATA = {
    "id": "telegram_bridge",
    "version": "2.0.0",
    "name": "Telegram Bridge",
    "description": "Relay chat to Telegram, and host Telegram commands for other plugins",
    "author": "FactorioReforge",
    "dependencies": {"factorio_reforge": ">=0.1.0"},
}

DEFAULT_CONFIG = {
    "enabled": True,
    "token": "",
    #: Chats the bot answers at all. Empty means it answers nobody.
    "allowed_chat_ids": [],
    #: Telegram user ids allowed to run destructive commands.
    "admin_user_ids": [],
    #: Telegram user ids allowed to run /cmd, which is unrestricted.
    "owner_user_ids": [],
    "forward_chat": True,
    "forward_join_leave": True,
    "forward_death": True,
    "forward_server_state": True,
    "game_prefix": "[TG]",
}

_service: TelegramService | None = None
_state: dict = {}


# ---------------------------------------------------------------------------
# public API -- what other plugins call via get_plugin_instance("telegram_bridge")
# ---------------------------------------------------------------------------

def _tr(key: str, **kwargs) -> str:
    """Translate from a Telegram handler, which has no CommandSource to use."""
    server = _state.get("server")
    return server.tr(key, **kwargs) if server is not None else key


def register_command(plugin_id, name, handler, *, level="admin", help=""):
    """Add a ``/name`` Telegram command owned by ``plugin_id``.

    Safe to call before the bot is up; the command attaches when it starts.
    """
    if _service is None:
        raise RuntimeError("the Telegram bridge is not loaded")
    _service.register_command(plugin_id, name, handler, level=level, help=help)


def unregister_plugin(plugin_id: str) -> None:
    if _service is not None:
        _service.unregister_plugin(plugin_id)


async def broadcast(text: str, *, html_escape: bool = False) -> None:
    if _service is not None:
        await _service.broadcast(text, html_escape=html_escape)


async def broadcast_photo(
    data: bytes,
    *,
    caption: str = "",
    filename: str = "image.png",
    as_document: bool = False,
) -> None:
    """Push an image to every allowed chat. Plugins pass bytes, not telegram types."""
    if _service is not None:
        await _service.broadcast_photo(
            data, caption=caption, filename=filename, as_document=as_document
        )


def is_ready() -> bool:
    return _service is not None and _service.is_ready()


# ---------------------------------------------------------------------------
# lifecycle
# ---------------------------------------------------------------------------

def on_load(server, prev):
    global _service
    config = server.load_config_simple("config.json", DEFAULT_CONFIG)
    _service = TelegramService(server.logger)
    _service.config = config
    _state.clear()
    _state.update(server=server, config=config, task=None)

    _register_builtin_commands(server)

    if not config.get("enabled", True):
        server.logger.info("telegram_bridge is disabled in its config")
        return
    if not config.get("token"):
        server.logger.warning(
            server.tr("no_token", path=server.get_data_folder() / "config.json"),
        )
        return
    if not config.get("allowed_chat_ids"):
        server.logger.warning(
            server.tr("no_chats")
        )

    _state["task"] = asyncio.create_task(_run_bot(server, config))


async def on_unload(server):
    global _service
    task = _state.pop("task", None)
    app = _service.app if _service else None
    if app is not None:
        try:
            await app.updater.stop()
            await app.stop()
            await app.shutdown()
        except Exception:
            server.logger.debug("telegram_bridge shutdown was not clean", exc_info=True)
    if task is not None:
        task.cancel()
    _service = None
    _state.clear()


#: How long to wait before trying Telegram again, in seconds, then the last
#: value forever. Not being able to reach Telegram is a property of the network
#: rather than a fault in the server, and networks come back: a host behind a
#: filtered link, or one waiting on a proxy to come up, should end up connected
#: without anyone reloading a plugin.
RETRY_DELAYS = (30, 60, 120, 300, 600)

#: Say something on the first failure and then only every so often. A server
#: that cannot reach Telegram at all would otherwise write a line every ten
#: minutes forever, which trains everyone to skip the log.
REMIND_EVERY = 6


async def _run_bot(server, config):
    try:
        from telegram.error import InvalidToken, NetworkError
        from telegram.ext import Application, CallbackQueryHandler, MessageHandler, filters
    except ImportError:
        server.logger.error(
            "telegram_bridge needs python-telegram-bot: pip install 'python-telegram-bot>=21'"
        )
        return

    def build():
        app = Application.builder().token(config["token"]).build()
        _service.app = app
        _service.attach_all()
        app.add_handler(CallbackQueryHandler(_service.on_callback))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _on_message))
        return app

    failures = 0
    while True:
        try:
            await _poll_forever(server, build())
        except asyncio.CancelledError:
            raise
        except InvalidToken as exc:
            # Waiting will not fix a token Telegram has rejected, and retrying
            # a bad one every ten minutes is how a bot gets rate limited.
            server.logger.error(server.tr("bad_token", error=exc))
            return
        except NetworkError as exc:
            delay = RETRY_DELAYS[min(failures, len(RETRY_DELAYS) - 1)]
            if failures == 0:
                server.logger.warning(server.tr(
                    "unreachable", error=_reason(exc), seconds=delay))
            elif failures % REMIND_EVERY == 0:
                server.logger.warning(server.tr(
                    "still_unreachable", error=_reason(exc), seconds=delay))
            else:
                server.logger.debug("telegram_bridge still cannot connect: %s", exc)
            failures += 1
            await asyncio.sleep(delay)
        except Exception:
            # Not a reachability problem, so the traceback is the point.
            server.logger.exception("telegram_bridge stopped unexpectedly")
            return


async def _poll_forever(server, app):
    """Connect and stay connected. Returns only by being cancelled.

    The application is torn down on the way out whatever happened, because a
    half-initialised one cannot be started again and the next attempt builds a
    fresh one anyway.
    """
    started = False
    try:
        await app.initialize()
        await app.start()
        started = True
        await app.updater.start_polling(drop_pending_updates=True)
        server.logger.info(server.tr("polling", count=len(_service.commands)))
        # Tell sub-plugins the bridge exists. They register here as well as in
        # their own on_load, so reloading either side puts things back.
        await server.dispatch_event(READY_EVENT)
        await asyncio.Event().wait()
    finally:
        with contextlib.suppress(Exception):
            if started:
                await app.stop()
            await app.shutdown()


def _reason(exc: Exception) -> str:
    """The message, or the exception's type when it has none.

    ``telegram.error.TimedOut`` carries "Timed out"; some network errors arrive
    with an empty string, and "could not reach Telegram: " reads like a bug.
    """
    return str(exc).strip() or type(exc).__name__


# ---------------------------------------------------------------------------
# game -> telegram
# ---------------------------------------------------------------------------

@event_listener("reforge.user_info")
async def on_user_info(server, info: Info):
    config = _state.get("config") or {}
    if not config.get("forward_chat") or info.source is not InfoSource.SERVER:
        return
    if not info.player or info.is_echo:
        return
    await broadcast(f"<b>{html.escape(info.player)}</b>: {html.escape(info.content)}")


async def on_player_joined(server, player, info=None):
    if (_state.get("config") or {}).get("forward_join_leave"):
        await broadcast(_tr("alerts.joined", player=html.escape(str(player))))


async def on_player_left(server, player, info=None):
    if (_state.get("config") or {}).get("forward_join_leave"):
        await broadcast(_tr("alerts.left", player=html.escape(str(player))))


async def on_player_death(server, player, info=None):
    if (_state.get("config") or {}).get("forward_death"):
        await broadcast(_tr(
            "alerts.died",
            message=html.escape((info.content if info else None) or str(player)),
        ))


async def on_server_startup(server):
    if (_state.get("config") or {}).get("forward_server_state"):
        await broadcast(_tr("alerts.server_up"))


async def on_server_crash(server, code):
    """Always sent, whatever forward_server_state says -- this is the alert."""
    await broadcast(_tr("alerts.server_crashed", code=code))


async def on_rollback_finished(server, snapshot, ok):
    await broadcast(_tr(
        "alerts.restored" if ok else "alerts.restore_failed",
        slot=html.escape(snapshot.describe()),
    ))


# ---------------------------------------------------------------------------
# telegram -> game
# ---------------------------------------------------------------------------

async def _on_message(update, context):
    if _service is None:
        return
    chat, user = update.effective_chat, update.effective_user
    if chat is None or user is None:
        return
    if _service.level_of(chat.id, user.id) is None:
        _state["server"].logger.info(
            "telegram_bridge ignored a message from chat %s; add it to allowed_chat_ids",
            chat.id,
        )
        return

    server = _state["server"]
    text = (update.message.text or "").strip()
    if not text:
        return
    if not server.is_server_running():
        await update.message.reply_text("The server is not running.")
        return

    name = user.first_name or user.username or "telegram"
    prefix = _state["config"].get("game_prefix", "[TG]")
    await server.say(f"{prefix} {name}: {text}")


# ---------------------------------------------------------------------------
# built-in commands, registered through the same service other plugins use
# ---------------------------------------------------------------------------

def _register_builtin_commands(server):
    def add(name, handler, level="admin", help=""):
        _service.register_command("telegram_bridge", name, handler, level=level, help=help)

    add("start", _cmd_help, "viewer", "list commands")
    add("help", _cmd_help, "viewer", "list commands")
    add("status", _cmd_status, "viewer", "server state")
    add("players", _cmd_players, "viewer", "who is online")
    add("say", _cmd_say, "viewer", "send a chat message")
    add("save", _cmd_save, "admin", "snapshot the world")
    add("saves", _cmd_saves, "viewer", "list snapshots")
    add("rollback", _cmd_rollback, "admin", "roll back to a snapshot")
    add("restart", _cmd_restart, "admin", "restart the server")
    add("stopserver", _cmd_stop, "admin", "stop the server")
    add("startserver", _cmd_start_server, "admin", "start the server")
    add("cmd", _cmd_raw, "owner", "run any console command")


async def _cmd_help(ctx: TelegramContext):
    lines = [_tr("help_header"), ""]
    by_plugin: dict[str, list[str]] = {}
    for command in sorted(_service.commands.values(), key=lambda c: c.name):
        if not _service._allows(command.level, ctx.level):
            continue
        tag = "" if command.level == "viewer" else f" ({command.level})"
        by_plugin.setdefault(command.plugin_id, []).append(
            f"/{command.name} - {command.help}{tag}"
        )
    for plugin_id, entries in sorted(by_plugin.items()):
        lines.append(f"[{plugin_id}]")
        lines.extend(f"  {entry}" for entry in entries)
        lines.append("")
    await ctx.reply("\n".join(lines).strip())


async def _cmd_status(ctx: TelegramContext):
    server = _state["server"]
    lines = [
        _tr("status.running", value=server.is_server_running()),
        _tr("status.startup", value=server.is_server_startup()),
        _tr("status.rcon", value=server.is_rcon_running()),
        _tr("status.backups", count=len(server.saves.list())),
    ]
    try:
        stats = await server.get_server_stats()
        lines.append(_tr("status.online", online=stats.get("players_online", 0),
                         total=stats.get("players_total", 0)))
        lines.append(_tr("status.evolution", value=f"{(stats.get('evolution') or 0) * 100:.2f}%"))
    except QueryError as exc:
        lines.append(_tr("status.unavailable", error=exc))
    await ctx.reply("\n".join(lines))


async def _cmd_players(ctx: TelegramContext):
    try:
        players = await _state["server"].get_online_player_details()
    except QueryError as exc:
        await ctx.reply(_tr("players.failed", error=exc))
        return
    if not players:
        await ctx.reply(_tr("players.nobody"))
        return
    await ctx.reply("\n".join(f"{p['name']} - {p.get('online_time', 0) // 3600}m" for p in players))


async def _cmd_say(ctx: TelegramContext):
    if not ctx.text:
        await ctx.reply(_tr("say.usage"))
        return
    prefix = _state["config"].get("game_prefix", "[TG]")
    await _state["server"].say(f"{prefix} {ctx.user_name}: {ctx.text}")
    await ctx.reply(_tr("say.sent"))


async def _cmd_save(ctx: TelegramContext):
    await ctx.reply(_tr("save.working"))
    try:
        snapshot = await _state["server"].snapshot(
            ctx.text or "via telegram", created_by=f"tg:{ctx.user_id}"
        )
    except Exception as exc:
        await ctx.reply(_tr("save.failed", error=exc))
        return
    await ctx.reply(_tr("save.done", slot=snapshot.describe()))


async def _cmd_saves(ctx: TelegramContext):
    snapshots = _state["server"].saves.list()
    if not snapshots:
        await ctx.reply(_tr("save.none"))
        return
    await ctx.reply("\n".join(s.describe() for s in snapshots[:20]))


async def _cmd_rollback(ctx: TelegramContext):
    if not ctx.args:
        await ctx.reply(_tr("rollback.usage"))
        return
    try:
        slot = int(ctx.args[0])
    except ValueError:
        await ctx.reply(_tr("rollback.not_a_number"))
        return

    server = _state["server"]
    info = server.saves.get(slot)
    if info is None:
        await ctx.reply(_tr("rollback.empty", slot=slot))
        return

    if not await ctx.confirm(_tr("rollback.confirm", slot=info.describe())):
        await ctx.reply(_tr("rollback.cancelled"))
        return

    await ctx.reply(_tr("rollback.working"))
    try:
        restored = await server.rollback(slot, countdown=10, requested_by=f"tg:{ctx.user_id}")
    except Exception as exc:
        await ctx.reply(_tr("rollback.failed", error=exc))
        return
    await ctx.reply(_tr("rollback.done", slot=restored.describe()))


async def _cmd_restart(ctx: TelegramContext):
    if not await ctx.confirm(_tr("server.confirm_restart")):
        await ctx.reply(_tr("rollback.cancelled"))
        return
    await ctx.reply(_tr("server.restarting"))
    await _state["server"].restart()
    await ctx.reply(_tr("server.restarted"))


async def _cmd_stop(ctx: TelegramContext):
    if not await ctx.confirm(_tr("server.confirm_stop")):
        await ctx.reply(_tr("rollback.cancelled"))
        return
    await _state["server"].stop()
    await ctx.reply(_tr("server.stopped"))


async def _cmd_start_server(ctx: TelegramContext):
    started = await _state["server"].start()
    await ctx.reply(_tr("server.starting") if started else _tr("server.already_running"))


async def _cmd_raw(ctx: TelegramContext):
    """Run an arbitrary FactorioReforge or Factorio command. Owner only."""
    if not ctx.text:
        await ctx.reply(_tr("cmd.usage"))
        return

    server = _state["server"]
    replies: list[str] = []

    async def collect(message: str) -> None:
        replies.append(message)

    source = PluginCommandSource(
        server, PermissionLevel.OWNER, f"tg:{ctx.user_id}", collect
    )
    core = server._server  # noqa: SLF001 -- deliberate: /cmd is a core escape hatch
    if core.commands.looks_like_command(ctx.text):
        await core.commands.dispatch(source, ctx.text)
    else:
        await core.feed_info(
            Info(
                source=InfoSource.PLUGIN, raw_content=ctx.text, content=ctx.text,
                action_flag=InfoActionFlag.default(),
            )
        )
        replies.append(_tr("cmd.sent"))

    await ctx.reply("\n".join(replies) if replies else _tr("cmd.no_output"))
