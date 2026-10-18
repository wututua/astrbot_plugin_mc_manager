"""Read only newly appended spark console reports after one RCON command."""

from __future__ import annotations

import asyncio
import os
import re
import stat
import time

from .config import MCError
from .message_catalog import text as message_text
from .spark import parse_spark_tps
from .text import safe_text

SPARK_COMMAND = re.compile(r"^/?(?:[a-z0-9_-]+:)?spark\s+tps\s*$", re.I)
ENVELOPE = re.compile(r"^\[[^\]\r\n]+\] \[(?P<thread>[^\]\r\n]+)/INFO\](?: \[[^\]\r\n]+\])?:\s*(?P<body>.*)$")
CONSOLE = re.compile(r"^\[[^\]\r\n]+ INFO\]:\s*(?P<body>.*)$")
TPS_HEADER = re.compile(r"^TPS\s+from\s+last\s+[^:\r\n]+:\s*", re.I)
SECTION = re.compile(r"^(?:TPS\s+from\s+last|Tick\s+durations|CPU\s+usage)\b", re.I)
MAX_BYTES = 256 * 1024


async def file_call(function):
    # Await cancellation of the actual thread before reloading/unloading.
    task = asyncio.create_task(asyncio.to_thread(function))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await asyncio.gather(task, return_exceptions=True)
        raise


class SparkLogCapture:
    def __init__(self, server):
        self.root = server.directory
        self.relative = server.spark_log_path
        self.encoding = server.spark_log_encoding
        self.identity = None
        self.offset = 0
        self.anchor = b""
        self.pending = b""
        self.discard_line = False
        self.bytes_read = 0
        self.thread = None
        self.lines = []
        self.finished = False
        self.marked = False

    def path(self):
        if self.root.resolve() != self.root:
            raise MCError(message_text("error_spark_log_001"))
        target = self.root / self.relative
        if target.is_symlink() or not target.resolve().is_relative_to(self.root):
            raise MCError(message_text("error_spark_log_002"))
        return target

    def _open(self):
        target = self.path()
        try:
            if not stat.S_ISREG(target.stat().st_mode):
                raise MCError(message_text("error_spark_log_005"))
            return target.open("rb")
        except FileNotFoundError:
            raise MCError(message_text("error_spark_log_006", relative=self.relative)) from None

    def mark(self):
        with self._open() as stream:
            info = os.fstat(stream.fileno())
            self.identity = (info.st_dev, info.st_ino)
            self.offset = info.st_size
            stream.seek(max(0, self.offset - 128))
            self.anchor = stream.read(min(128, self.offset))
            self.discard_line = bool(self.anchor and not self.anchor.endswith(b"\n"))
        self.marked = True

    def read(self):
        if not self.marked:
            raise MCError(message_text("error_spark_log_003"))
        with self._open() as stream:
            info = os.fstat(stream.fileno())
            if (info.st_dev, info.st_ino) != self.identity or info.st_size < self.offset:
                raise MCError(message_text("error_spark_log_007"))
            stream.seek(self.offset - len(self.anchor))
            if stream.read(len(self.anchor)) != self.anchor:
                raise MCError(message_text("error_spark_log_008"))
            stream.seek(self.offset)
            chunk = stream.read(min(65536, MAX_BYTES - self.bytes_read + 1))
            self.bytes_read += len(chunk)
            if self.bytes_read > MAX_BYTES:
                raise MCError(message_text("error_spark_log_009"))
            self.offset += len(chunk)
            stream.seek(max(0, self.offset - 128))
            self.anchor = stream.read(min(128, self.offset))
        parts = (self.pending + chunk).split(b"\n")
        self.pending = parts.pop()
        for part in parts:
            if self.discard_line:
                self.discard_line = False
                continue
            if len(part) > 8192:
                continue
            self.feed(part.decode(self.encoding, errors="replace").rstrip("\r"))
        if len(self.pending) > 8192:
            self.pending = b""
            self.discard_line = True

    def feed(self, line):
        line = safe_text(line, 8192).strip()
        envelope = ENVELOPE.fullmatch(line)
        if envelope:
            thread = envelope["thread"]
            if not (thread.casefold().startswith("spark-") or thread in {"Server thread", "ServerMain", "main"}):
                return
            body = envelope["body"]
        else:
            console = CONSOLE.fullmatch(line)
            if not console:
                return
            thread, body = "console", console["body"]
        # Forge's logger includes a [?] component; Bukkit may add [spark].
        body = re.sub(r"^(?:\[[^\]\r\n]{1,32}\]\s*)+", "", body).strip()
        if TPS_HEADER.match(body):
            if self.thread is not None:
                raise MCError(message_text("error_spark_log_010"))
            self.thread = thread
            self.lines.append(body)
            return
        if self.thread != thread:
            return
        # Only measurement/header lines from the report's own logger thread.
        if SECTION.match(body) or re.fullmatch(r"[\d+*.,/;%()\sA-Za-z-]+", body or " "):
            self.lines.append(body)
            if re.search(r"\(process\)\s*$", body, re.I):
                self.finished = True
        if len(self.lines) > 80 or sum(map(len, self.lines)) > 16000:
            raise MCError(message_text("error_spark_log_004"))

    def report(self):
        text = "\n".join(self.lines).strip()
        metrics = parse_spark_tps(text)
        return text if any(name.startswith("tps_") and name not in metrics.missing for name in metrics.values) else ""

    async def wait(self, seconds):
        deadline = time.monotonic() + seconds
        while True:
            await file_call(self.read)
            report = self.report()
            if report and (self.finished or parse_spark_tps(report).status == "完整"):
                return report
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if report:
                    return report
                raise MCError(message_text("error_spark_log_011", value1=f"{seconds:g}"))
            await asyncio.sleep(min(0.1, remaining))
