"""Source RCON client for Factorio, on asyncio.

stdin can send commands but never reports what they did. RCON is the only way to
read a result back, so anything that returns a value -- the online player list, a
Lua expression -- goes through here while fire-and-forget output stays on stdin.

Factorio's RCON is plain Source RCON: little-endian ``[size][id][type][body\\0\\0]``.
Responses over 4096 bytes arrive split across packets, which is handled by
sending a sentinel request after each command and reading until it comes back.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import struct
from typing import Optional

from factorio_reforge.core.errors import QueryError

SERVERDATA_AUTH = 3
SERVERDATA_AUTH_RESPONSE = 2
SERVERDATA_EXECCOMMAND = 2
SERVERDATA_RESPONSE_VALUE = 0

_AUTH_FAILED_ID = -1
_MAX_PACKET = 4096 + 16
#: A reply at or above this length was probably truncated into more packets.
_SPLIT_THRESHOLD = 4000
_CONTINUATION_WAIT = 0.3


class RconError(QueryError):
    """The RCON transport failed: not connected, lost, or timed out."""


class RconAuthError(RconError):
    pass


class RconClient:
    def __init__(
        self,
        host: str,
        port: int,
        password: str,
        *,
        connect_timeout: float = 5.0,
        command_timeout: float = 10.0,
        logger: Optional[logging.Logger] = None,
    ):
        self.host = host
        self.port = port
        self.password = password
        self.connect_timeout = connect_timeout
        self.command_timeout = command_timeout
        self.logger = logger or logging.getLogger(__name__)

        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._request_id = 0
        self._authenticated = False
        # One reader at a time. This guards the handshake as well as commands:
        # the socket is open before auth completes, and a command slipping in
        # during that window would have two coroutines reading the same stream.
        self._lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        return (
            self._authenticated
            and self._writer is not None
            and not self._writer.is_closing()
        )

    async def connect(self) -> None:
        async with self._lock:
            await self._connect_locked()

    async def _connect_locked(self) -> None:
        await self._close_locked()
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), self.connect_timeout
            )
        except (OSError, asyncio.TimeoutError) as exc:
            raise RconError(f"cannot reach RCON at {self.host}:{self.port}: {exc}") from exc

        request_id = self._next_id()
        try:
            await self._send(request_id, SERVERDATA_AUTH, self.password)
            response_id, _, _ = await self._recv()
            # Some servers answer with an empty RESPONSE_VALUE before the real
            # auth result, so read one more packet when that happens.
            if response_id == request_id and response_id != _AUTH_FAILED_ID:
                try:
                    response_id, _, _ = await asyncio.wait_for(self._recv(), 0.5)
                except asyncio.TimeoutError:
                    pass
        except (OSError, EOFError, ConnectionError, struct.error) as exc:
            # The server hangs up mid-handshake when it is shutting down, which
            # is ordinary rather than exceptional -- report it as unreachable.
            await self._close_locked()
            raise RconError(f"RCON handshake failed: {exc}") from exc

        if response_id == _AUTH_FAILED_ID:
            await self._close_locked()
            raise RconAuthError("RCON authentication failed: wrong password")
        self._authenticated = True
        self.logger.info("RCON connected to %s:%s", self.host, self.port)

    async def close(self) -> None:
        async with self._lock:
            await self._close_locked()

    async def _close_locked(self) -> None:
        self._authenticated = False
        writer, self._writer, self._reader = self._writer, None, None
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except (OSError, asyncio.TimeoutError):
                pass

    async def execute(self, command: str) -> str:
        """Run a command and return everything the server printed."""
        async with self._lock:
            if not self.connected:
                raise RconError("RCON is not connected")
            try:
                return await asyncio.wait_for(self._execute(command), self.command_timeout)
            except asyncio.TimeoutError as exc:
                # The socket is now mid-packet and unusable; drop it so the
                # manager reconnects instead of reading garbage next time.
                await self._close_locked()
                raise RconError(f"RCON command timed out: {command!r}") from exc
            except (OSError, EOFError, ConnectionError, struct.error) as exc:
                await self._close_locked()
                raise RconError(f"RCON connection lost: {exc}") from exc

    async def _execute(self, command: str) -> str:
        """Send one command and collect its reply.

        The usual Source trick -- a trailing empty command whose echo marks the
        end of the reply -- does not work here: Factorio never answers an empty
        command, so waiting for that sentinel just hangs. Instead the first
        packet carrying our id is the reply, and any continuation is picked up
        with a short grace period.
        """
        command_id = self._next_id()
        await self._send(command_id, SERVERDATA_EXECCOMMAND, command)

        chunks: list[str] = []
        while True:
            response_id, _, body = await self._recv()
            if response_id == command_id:
                chunks.append(body)
                break
            # A stray packet from an earlier, timed-out command; drop it.

        # A reply longer than one packet arrives as several back to back. Nothing
        # marks the last one, so stop as soon as the socket goes quiet.
        while len(chunks[-1]) >= _SPLIT_THRESHOLD:
            try:
                response_id, _, body = await asyncio.wait_for(self._recv(), _CONTINUATION_WAIT)
            except asyncio.TimeoutError:
                break
            if response_id == command_id:
                chunks.append(body)

        return "".join(chunks).strip()

    async def _send(self, request_id: int, packet_type: int, body: str) -> None:
        assert self._writer is not None
        payload = struct.pack("<ii", request_id, packet_type) + body.encode("utf-8") + b"\x00\x00"
        self._writer.write(struct.pack("<i", len(payload)) + payload)
        await self._writer.drain()

    async def _recv(self) -> tuple[int, int, str]:
        assert self._reader is not None
        size = struct.unpack("<i", await self._reader.readexactly(4))[0]
        if not 10 <= size <= _MAX_PACKET:
            raise RconError(f"implausible RCON packet size {size}")
        payload = await self._reader.readexactly(size)
        request_id, packet_type = struct.unpack("<ii", payload[:8])
        body = payload[8:-2].decode("utf-8", errors="replace")
        return request_id, packet_type, body

    def _next_id(self) -> int:
        # Stay positive: -1 is reserved for the auth-failure signal.
        self._request_id = (self._request_id + 1) % 0x7FFFFFFF or 1
        return self._request_id


class RconManager:
    """Keeps an :class:`RconClient` connected, retrying in the background.

    RCON only starts listening once the map has loaded, so connecting is retried
    rather than attempted once at startup and given up on.
    """

    def __init__(
        self,
        client: RconClient,
        *,
        retry_interval: float = 3.0,
        logger: Optional[logging.Logger] = None,
        on_connect=None,
        on_lost=None,
    ):
        self.client = client
        self.retry_interval = retry_interval
        self.logger = logger or logging.getLogger(__name__)
        self.on_connect = on_connect
        self.on_lost = on_lost
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()

    @property
    def connected(self) -> bool:
        return self.client.connected

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self._keep_connected())

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            # The task may already be failing for its own reasons; stopping must
            # not re-raise whatever it was in the middle of.
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await self._task
            self._task = None
        await self.client.close()

    async def _keep_connected(self) -> None:
        while not self._stop.is_set():
            if not self.client.connected:
                try:
                    await self.client.connect()
                    if self.on_connect is not None:
                        await self.on_connect()
                except RconAuthError as exc:
                    # A wrong password will never fix itself; stop hammering.
                    self.logger.error("%s -- giving up on RCON", exc)
                    return
                except RconError as exc:
                    self.logger.debug("RCON not up yet: %s", exc)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # Keep the retry loop alive whatever happens; losing it
                    # would silently disable every query API for the session.
                    self.logger.exception("Unexpected error while connecting to RCON")
            await asyncio.sleep(self.retry_interval)

    async def execute(self, command: str) -> str:
        try:
            return await self.client.execute(command)
        except RconError:
            if self.on_lost is not None:
                await self.on_lost()
            raise
