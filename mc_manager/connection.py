"""Read-only connection checks for unsaved dashboard server drafts."""

from __future__ import annotations

import asyncio
import time

from .config import MCError
from .message_catalog import text as message_text
from .monitoring import command_error, parse_players
from .rcon import Rcon
from .text import safe_text


async def probe_connection(server, timeout, rcon_factory=Rcon):
    started = time.monotonic()
    try:
        async with asyncio.timeout(2 * timeout + 2):
            async with rcon_factory(server, timeout) as client:
                result = safe_text(await client.command("list"), 6000).strip()
    except (OSError, TimeoutError, EOFError) as exc:
        raise MCError(
            message_text("error_connection_failed", error=safe_text(str(exc) or type(exc).__name__, 300))
        ) from None
    if not result or command_error(result):
        raise MCError(message_text("error_connection_reply", result=result))
    return {
        "server": server.name,
        "server_id": server.id,
        "latency_ms": round((time.monotonic() - started) * 1000, 1),
        "result": result,
        "query_command": "list",
        **parse_players(result),
    }
