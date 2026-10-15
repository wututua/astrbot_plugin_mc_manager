import asyncio
import struct
import unittest
from pathlib import Path

from mc_manager.config import MCError, Server
from mc_manager.rcon import Rcon


def packet(identifier, kind, body):
    raw = struct.pack("<ii", identifier, kind) + body + b"\0\0"
    return struct.pack("<i", len(raw)) + raw


class RconTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.handlers = set()
        self.commands = []
        self.mode = "normal"
        self.closed = asyncio.Event()
        self.listener = await asyncio.start_server(self.handle, "127.0.0.1", 0)
        port = self.listener.sockets[0].getsockname()[1]
        self.server = Server("s", "test", Path.cwd(), "127.0.0.1", port, "secret")

    async def asyncTearDown(self):
        self.listener.close()
        await self.listener.wait_closed()
        tasks = list(self.handlers)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def handle(self, reader, writer):
        task = asyncio.current_task()
        self.handlers.add(task)
        try:
            while True:
                size = struct.unpack("<i", await reader.readexactly(4))[0]
                raw = await reader.readexactly(size)
                identifier, kind = struct.unpack("<ii", raw[:8])
                body = raw[8:-2].decode("utf-8")
                if kind == 3:
                    if self.mode == "bad-auth":
                        writer.write(packet(-1, 2, b""))
                    else:
                        writer.write(packet(identifier, 0, b"") + packet(identifier, 2, b""))
                else:
                    self.commands.append(body)
                    if body == "stop":
                        writer.write(packet(identifier, 0, b"Stopping server"))
                        await writer.drain()
                        break
                    if self.mode == "bad-length":
                        writer.write(struct.pack("<i", -1))
                    elif self.mode == "timeout":
                        await asyncio.sleep(5)
                    elif body == "time query gametime":
                        writer.write(packet(identifier, 0, b"The time is 123"))
                    else:
                        # Split a UTF-8 codepoint between protocol packets and
                        # split frames across network writes as well.
                        response = "测试 TPS 20.0".encode("utf-8")
                        data = packet(identifier, 0, response[:2]) + packet(identifier, 0, response[2:])
                        for index in range(0, len(data), 3):
                            writer.write(data[index : index + 3])
                            await writer.drain()
                            await asyncio.sleep(0)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError, asyncio.CancelledError):
            pass
        finally:
            writer.close()
            with __import__("contextlib").suppress(OSError):
                await writer.wait_closed()
            self.handlers.discard(task)
            self.closed.set()

    async def test_auth_fragmentation_multipacket_utf8_and_sequential_commands(self):
        async with Rcon(self.server, 1) as client:
            self.assertEqual(await client.command("tps"), "测试 TPS 20.0")
            self.assertEqual(await client.command("list"), "测试 TPS 20.0")
        self.assertEqual(self.commands, ["tps", "time query gametime", "list", "time query gametime"])

    async def test_auth_rejection(self):
        self.mode = "bad-auth"
        with self.assertRaisesRegex(MCError, "认证失败"):
            async with Rcon(self.server, 1):
                pass

    async def test_invalid_packet_length_rejected(self):
        async with Rcon(self.server, 1) as client:
            self.mode = "bad-length"
            with self.assertRaisesRegex(MCError, "长度"):
                await client.command("tps")

    async def test_response_timeout(self):
        async with Rcon(self.server, 1) as client:
            client.timeout = 0.02
            self.mode = "timeout"
            with self.assertRaises(TimeoutError):
                await client.command("tps")

    async def test_stop_preserves_connection_until_eof(self):
        async with Rcon(self.server, 1) as client:
            await client.stop()
            self.assertTrue(client.sent_stop)
            await client.wait_disconnect(1)
            self.assertTrue(client.reader.at_eof())
        self.assertIn("stop", self.commands)

    async def test_newline_injection_and_oversize_rejected(self):
        async with Rcon(self.server, 1) as client:
            for command in ["list\nstop", "x" * 5000]:
                with self.assertRaises(MCError):
                    await client.command(command)
        self.assertEqual(self.commands, [])


if __name__ == "__main__":
    unittest.main()
