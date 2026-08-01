"""The reusable half of the Telegram bridge: a registry other plugins talk to.

The point of this module is that a plugin adding a Telegram command should never
import ``telegram``. It registers a name and an async handler, and receives a
:class:`TelegramContext` -- args, a reply method, and who is asking. That keeps
python-telegram-bot an optional dependency of one plugin instead of a hard
dependency of everything that wants to be reachable from a phone.

    tg = server.get_plugin_instance("telegram_bridge")
    tg.register_command("mod_manager", "mods", handler, level="admin", help="list mods")

Registrations are keyed by owning plugin so unloading that plugin takes its
commands with it, and the bridge re-announces itself with a ``telegram.ready``
event so sub-plugins can re-register after the bridge itself reloads.
"""

from __future__ import annotations

import asyncio
import dataclasses
import html
import io
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

READY_EVENT = "telegram.ready"
"""Dispatched when the bot starts polling. Sub-plugins register on this."""

LEVELS = ("viewer", "admin", "owner")


@dataclasses.dataclass
class TelegramContext:
    """What a handler is given. Deliberately free of python-telegram-bot types."""

    args: list[str]
    text: str
    user_id: int
    chat_id: int
    user_name: str
    level: str
    _reply: Callable[[str], Awaitable[None]]
    _confirm: Callable[..., Awaitable[bool]]
    _send_photo: Callable[..., Awaitable[None]]

    async def reply(self, text: str) -> None:
        await self._reply(text)

    async def send_photo(
        self, data: bytes, *, caption: str = "", filename: str = "image.png"
    ) -> None:
        """Reply with an image. Bytes in, no telegram types out."""
        await self._send_photo(data, caption, filename, False)

    async def send_image_file(
        self, data: bytes, *, caption: str = "", filename: str = "image.png"
    ) -> None:
        """Reply with an image as a *document*, so Telegram does not recompress it.

        Telegram re-encodes anything sent as a photo, which turns a map where a
        single pixel is a single tile into mush. Sent as a document it arrives
        byte-for-byte, and clients still show a preview.
        """
        await self._send_photo(data, caption, filename, True)

    async def confirm(self, question: str, *, timeout: float = 60.0) -> bool:
        """Ask for a yes/no with inline buttons. Returns False on timeout.

        Anything destructive should go through this rather than acting on the
        first message: a phone keyboard makes mistyped commands easy, and there
        is no undo for "install this mod on the live server".
        """
        return await self._confirm(question, timeout=timeout)

    @property
    def is_admin(self) -> bool:
        return self.level in ("admin", "owner")

    @property
    def is_owner(self) -> bool:
        return self.level == "owner"


@dataclasses.dataclass
class Command:
    plugin_id: str
    name: str
    handler: Callable[[TelegramContext], Awaitable[None]]
    level: str = "admin"
    help: str = ""


class TelegramService:
    """Owns the bot connection and the command registry."""

    def __init__(self, logger: logging.Logger):
        self.logger = logger
        self.config: dict[str, Any] = {}
        self.commands: dict[str, Command] = {}
        self.app: Any = None
        self._pending_confirms: dict[str, asyncio.Future] = {}
        self._confirm_counter = 0

    # -- registry ------------------------------------------------------------

    def register_command(
        self,
        plugin_id: str,
        name: str,
        handler: Callable[[TelegramContext], Awaitable[None]],
        *,
        level: str = "admin",
        help: str = "",
    ) -> None:
        """Add a ``/name`` command. Re-registering the same name replaces it.

        Replacing rather than rejecting is deliberate: a plugin reload
        re-registers everything it owns, and treating that as a conflict would
        make reload unusable.
        """
        if level not in LEVELS:
            raise ValueError(f"level must be one of {LEVELS}, got {level!r}")
        name = name.lstrip("/").lower()
        existing = self.commands.get(name)
        if existing is not None and existing.plugin_id != plugin_id:
            self.logger.warning(
                "Plugin %r is taking over /%s from %r", plugin_id, name, existing.plugin_id
            )
        self.commands[name] = Command(plugin_id, name, handler, level, help)
        self._attach(name)

    def unregister_plugin(self, plugin_id: str) -> None:
        for name in [n for n, c in self.commands.items() if c.plugin_id == plugin_id]:
            del self.commands[name]

    def is_ready(self) -> bool:
        return self.app is not None

    # -- outbound ------------------------------------------------------------

    async def broadcast_photo(
        self,
        data: bytes,
        *,
        caption: str = "",
        filename: str = "image.png",
        as_document: bool = False,
    ) -> None:
        """Push an image to every allowed chat.

        ``as_document`` avoids Telegram's photo recompression, which matters for
        anything with fine detail -- a map at one pixel per tile survives as a
        document and does not as a photo.
        """
        if self.app is None:
            return
        for chat_id in self.config.get("allowed_chat_ids", []):
            try:
                payload = _as_input_file(data, filename)
                if as_document:
                    await self.app.bot.send_document(
                        chat_id=chat_id, document=payload, caption=caption[:1024]
                    )
                else:
                    await self.app.bot.send_photo(
                        chat_id=chat_id, photo=payload, caption=caption[:1024]
                    )
            except Exception:
                self.logger.warning("Could not send an image to chat %s", chat_id, exc_info=True)

    async def broadcast(self, text: str, *, html_escape: bool = False) -> None:
        """Send to every allowed chat. Safe to call when the bot is not up."""
        if self.app is None:
            return
        body = html.escape(text) if html_escape else text
        for chat_id in self.config.get("allowed_chat_ids", []):
            try:
                await self.app.bot.send_message(chat_id=chat_id, text=body, parse_mode="HTML")
            except Exception:
                self.logger.warning("Could not send to chat %s", chat_id, exc_info=True)

    # -- authorisation -------------------------------------------------------

    def level_of(self, chat_id: int, user_id: int) -> str | None:
        """The caller's level, or None if this chat is not allowed at all."""
        if chat_id not in self.config.get("allowed_chat_ids", []):
            return None
        if user_id in self.config.get("owner_user_ids", []):
            return "owner"
        if user_id in self.config.get("admin_user_ids", []):
            return "admin"
        return "viewer"

    @staticmethod
    def _allows(required: str, actual: str) -> bool:
        return LEVELS.index(actual) >= LEVELS.index(required)

    # -- bot wiring ----------------------------------------------------------

    def _attach(self, name: str) -> None:
        """Hook a command into a running Application.

        Commands can be registered before or after the bot starts -- a
        sub-plugin may load either side of the bridge -- so registration
        attaches immediately when possible and :meth:`attach_all` catches up
        with the rest at startup.
        """
        if self.app is None:
            return
        from telegram.ext import CommandHandler

        self.app.add_handler(CommandHandler(name, self._make_entry(name)))

    def attach_all(self) -> None:
        for name in self.commands:
            self._attach(name)

    def _make_entry(self, name: str):
        async def entry(update, context):
            await self._dispatch(name, update, context)

        return entry

    async def _dispatch(self, name: str, update, context) -> None:
        command = self.commands.get(name)
        if command is None:
            return

        chat = update.effective_chat
        user = update.effective_user
        if chat is None or user is None:
            return

        level = self.level_of(chat.id, user.id)
        if level is None:
            # Log instead of replying: answering an unknown chat confirms the
            # bot exists to whoever found it.
            self.logger.info(
                "Ignored /%s from chat %s; add it to allowed_chat_ids to permit it",
                name, chat.id,
            )
            return

        if not self._allows(command.level, level):
            await update.message.reply_text("You are not allowed to do that.")
            return

        ctx = self._build_context(update, context, level)
        try:
            await command.handler(ctx)
        except Exception:
            self.logger.exception("Plugin %r raised handling /%s", command.plugin_id, name)
            await ctx.reply(f"That failed: internal error in {command.plugin_id}")

    def _build_context(self, update, context, level: str) -> TelegramContext:
        message = update.message
        user = update.effective_user

        async def reply(text: str) -> None:
            # Telegram rejects messages over 4096 characters; splitting beats
            # having a long mod list vanish with an API error.
            for chunk in _chunk(text, 4000):
                await message.reply_text(chunk)

        async def confirm(question: str, *, timeout: float = 60.0) -> bool:
            return await self._ask_confirm(message, question, timeout)

        async def send_photo(
            data: bytes, caption: str, filename: str, as_document: bool
        ) -> None:
            payload = _as_input_file(data, filename)
            if as_document:
                await message.reply_document(document=payload, caption=caption[:1024])
            else:
                await message.reply_photo(photo=payload, caption=caption[:1024])

        return TelegramContext(
            args=list(context.args or []),
            text=" ".join(context.args or []),
            user_id=user.id,
            chat_id=update.effective_chat.id,
            user_name=user.first_name or user.username or str(user.id),
            level=level,
            _reply=reply,
            _confirm=confirm,
            _send_photo=send_photo,
        )

    # -- confirmation buttons ------------------------------------------------

    async def _ask_confirm(self, message, question: str, timeout: float) -> bool:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        self._confirm_counter += 1
        token = f"c{self._confirm_counter}:{int(time.time())}"
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending_confirms[token] = future

        keyboard = InlineKeyboardMarkup(
            [[
                InlineKeyboardButton("Yes, do it", callback_data=f"confirm:{token}:yes"),
                InlineKeyboardButton("Cancel", callback_data=f"confirm:{token}:no"),
            ]]
        )
        await message.reply_text(question, reply_markup=keyboard)
        try:
            return await asyncio.wait_for(future, timeout)
        except TimeoutError:
            return False
        finally:
            self._pending_confirms.pop(token, None)

    async def on_callback(self, update, context) -> None:
        query = update.callback_query
        await query.answer()
        parts = (query.data or "").split(":")
        if len(parts) != 4 or parts[0] != "confirm":
            return
        token = f"{parts[1]}:{parts[2]}"
        answer = parts[3] == "yes"

        if self.level_of(update.effective_chat.id, update.effective_user.id) is None:
            return
        future = self._pending_confirms.get(token)
        if future is not None and not future.done():
            future.set_result(answer)
        await query.edit_message_text(
            f"{query.message.text}\n\n-> {'confirmed' if answer else 'cancelled'}"
        )


def _chunk(text: str, size: int) -> list[str]:
    """Split on line boundaries where possible, so output stays readable."""
    if len(text) <= size:
        return [text]
    chunks, current = [], ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > size:
            if current:
                chunks.append(current)
            current = line[:size] if len(line) > size else line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    return chunks


def _as_input_file(data: bytes, filename: str):
    """Wrap raw bytes for python-telegram-bot without leaking its types outward."""
    from telegram import InputFile

    return InputFile(io.BytesIO(data), filename=filename)
