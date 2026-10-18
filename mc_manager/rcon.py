from __future__ import annotations

import asyncio
import struct

from .config import MCError, Server
from .message_catalog import text as message_text


class Rcon:
    """Minecraft RCON with exact framing and a harmless time-query response delimiter."""

    def __init__(self, server: Server, timeout: float = 5):
        self.server = server
        self.timeout = timeout
        self.reader = None
        self.writer = None
        self.counter = 10
        self.sent_stop = False

    async def __aenter__(self):
        try:
            async with asyncio.timeout(self.timeout):
                if not self.server.password:
                    raise MCError(message_text("error_rcon_006"))
                self.reader, self.writer = await asyncio.open_connection(self.server.host, self.server.port)
                await self.send(1, 3, self.server.password)
                for _ in range(4):
                    rid, kind, _ = await self.packet()
                    if rid == -1:
                        raise MCError(message_text("error_rcon_009"))
                    if kind == 2 and rid == 1:
                        return self
                raise MCError(message_text("error_rcon_005"))
        except BaseException:
            await self.close()
            raise

    async def __aexit__(self, *_):
        await self.close()

    async def close(self):
        if self.writer:
            self.writer.close()
            try:
                async with asyncio.timeout(1):
                    await self.writer.wait_closed()
            except (OSError, TimeoutError):
                pass

    async def send(self, identifier: int, kind: int, command: str):
        if any(c in command for c in "\n\r\x00"):
            raise MCError(message_text("error_rcon_001"))
        data = struct.pack("<ii", identifier, kind) + command.encode("utf-8") + b"\0\0"
        if len(data) > 4096:
            raise MCError(message_text("error_rcon_002"))
        self.writer.write(struct.pack("<i", len(data)) + data)
        await self.writer.drain()

    async def packet(self) -> tuple[int, int, bytes]:
        length = struct.unpack("<i", await self.reader.readexactly(4))[0]
        if not 10 <= length <= 4 * 1024 * 1024:
            raise MCError(message_text("error_rcon_003"))
        data = await self.reader.readexactly(length)
        if data[-2:] != b"\0\0":
            raise MCError(message_text("error_rcon_004"))
        rid, kind = struct.unpack("<ii", data[:8])
        return rid, kind, data[8:-2]

    async def command(self, command: str) -> str:
        self.counter += 2
        identifier = self.counter
        delimiter = identifier + 1
        async with asyncio.timeout(self.timeout):
            await self.send(identifier, 2, command)
            # Vanilla processes RCON commands in order. Unlike an empty command,
            # a time query reliably produces one short response and cannot alter state.
            await self.send(delimiter, 2, "time query gametime")
            parts = []
            length = 0
            while True:
                rid, kind, body = await self.packet()
                if rid == delimiter and kind == 0:
                    return b"".join(parts).decode("utf-8", errors="replace")
                if rid != identifier or kind != 0:
                    raise MCError(message_text("error_rcon_007"))
                parts.append(body)
                length += len(body)
                if length > 1024 * 1024:
                    raise MCError(message_text("error_rcon_008"))

    async def stop(self):
        async with asyncio.timeout(self.timeout):
            await self.send(900000, 2, "stop")
            self.sent_stop = True

    async def wait_disconnect(self, timeout: float):
        async with asyncio.timeout(timeout):
            while await self.reader.read(8192):
                pass
