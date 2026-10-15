from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .config import MCError, Settings


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "mc.sqlite3"
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS scopes(id TEXT PRIMARY KEY, platform TEXT, group_id TEXT,
            next_id INTEGER NOT NULL DEFAULT 1, initialized INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS bindings(server TEXT PRIMARY KEY, scope TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS entries(scope TEXT, id INTEGER, name TEXT COLLATE NOCASE,
            uuid TEXT, owner TEXT, created TEXT, PRIMARY KEY(scope,id),
            UNIQUE(scope,name), UNIQUE(scope,uuid));
        CREATE TABLE IF NOT EXISTS logs(id INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT,
            time TEXT, platform TEXT, group_id TEXT, user TEXT, user_name TEXT,
            action TEXT, player TEXT, detail TEXT);
        CREATE TABLE IF NOT EXISTS sessions(scope TEXT PRIMARY KEY, origin TEXT);
        CREATE TABLE IF NOT EXISTS dirty(server TEXT PRIMARY KEY, error TEXT DEFAULT '待同步');
        CREATE TABLE IF NOT EXISTS jobs(server TEXT PRIMARY KEY, scope TEXT, origin TEXT,
            started TEXT, status TEXT, detail TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS outbox(id INTEGER PRIMARY KEY AUTOINCREMENT,
            origin TEXT, text TEXT, created TEXT, attempts INTEGER DEFAULT 0);
        """)

    @contextmanager
    def transaction(self):
        with self.db:
            yield

    def validate_scopes(self, settings: Settings, reserve: bool = True):
        with self.transaction():
            for group in settings.groups.values():
                row = self.db.execute("SELECT * FROM scopes WHERE id=?", (group.id,)).fetchone()
                if row and (row["platform"], row["group_id"]) != (group.platform, group.group_id):
                    raise MCError(f"群配置 ID {group.id} 已有其他群的数据，不能改绑；请使用新的 ID。")
                if reserve:
                    self.db.execute(
                        "INSERT OR IGNORE INTO scopes(id,platform,group_id) VALUES(?,?,?)",
                        (group.id, group.platform, group.group_id),
                    )
                for sid in group.servers:
                    existing = self.db.execute("SELECT scope FROM bindings WHERE server=?", (sid,)).fetchone()
                    if existing and existing[0] != group.id:
                        raise MCError(f"服务器 {sid} 已有群 {existing[0]} 的历史数据，不能直接跨群改绑。")
                    if reserve:
                        self.db.execute("INSERT OR IGNORE INTO bindings VALUES(?,?)", (sid, group.id))

    def initialized(self, scope: str) -> bool:
        return bool(self.db.execute("SELECT initialized FROM scopes WHERE id=?", (scope,)).fetchone()[0])

    def entries(self, scope: str) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM entries WHERE scope=? ORDER BY id", (scope,))]

    def find(self, scope: str, target: str) -> dict:
        if target.isdecimal():
            row = self.db.execute("SELECT * FROM entries WHERE scope=? AND id=?", (scope, int(target))).fetchone()
        else:
            row = self.db.execute("SELECT * FROM entries WHERE scope=? AND name=?", (scope, target)).fetchone()
        if not row:
            raise MCError("未找到该白名单条目；编号按群固定，删除后不会复用。")
        return dict(row)

    def insert(self, scope: str, name: str, uuid: str, owner: str) -> int:
        identifier = self.db.execute("SELECT next_id FROM scopes WHERE id=?", (scope,)).fetchone()[0]
        self.db.execute("INSERT INTO entries VALUES(?,?,?,?,?,?)", (scope, identifier, name, uuid, owner, now()))
        self.db.execute("UPDATE scopes SET next_id=next_id+1 WHERE id=?", (scope,))
        return identifier

    def audit(self, group, actor, action: str, player: str = "", detail: str = ""):
        self.db.execute(
            "INSERT INTO logs(scope,time,platform,group_id,user,user_name,action,player,detail) VALUES(?,?,?,?,?,?,?,?,?)",
            (group.id, now(), actor.platform, actor.group, actor.user, actor.name, action, player, detail),
        )

    def mark_dirty(self, servers):
        for sid in servers:
            self.db.execute(
                "INSERT INTO dirty(server,error) VALUES(?,?) ON CONFLICT(server) DO UPDATE SET error=excluded.error",
                (sid, "待同步"),
            )

    def sync_result(self, sid: str, error: str):
        with self.transaction():
            if error:
                self.db.execute(
                    "INSERT INTO dirty VALUES(?,?) ON CONFLICT(server) DO UPDATE SET error=excluded.error", (sid, error)
                )
            else:
                self.db.execute("DELETE FROM dirty WHERE server=?", (sid,))

    def dirty(self) -> dict[str, str]:
        return {r[0]: r[1] for r in self.db.execute("SELECT * FROM dirty")}

    def remember(self, scope: str, origin: str):
        if origin:
            with self.transaction():
                self.db.execute(
                    "INSERT INTO sessions VALUES(?,?) ON CONFLICT(scope) DO UPDATE SET origin=excluded.origin",
                    (scope, origin),
                )

    def origin(self, scope: str) -> str:
        row = self.db.execute("SELECT origin FROM sessions WHERE scope=?", (scope,)).fetchone()
        return row[0] if row else ""

    def logs(self, scope: str, page: int, size: int, user: str = "") -> tuple[list[dict], int]:
        clause = "scope=?" + (" AND user=?" if user else "")
        args = (scope, user) if user else (scope,)
        count = self.db.execute("SELECT COUNT(*) FROM logs WHERE " + clause, args).fetchone()[0]
        rows = self.db.execute(
            "SELECT * FROM logs WHERE " + clause + " ORDER BY id DESC LIMIT ? OFFSET ?",
            (*args, size, (page - 1) * size),
        )
        return [dict(r) for r in rows], count

    def job(self, sid: str, scope: str, origin: str):
        with self.transaction():
            self.db.execute(
                "INSERT INTO jobs VALUES(?,?,?,?,?,?) ON CONFLICT(server) DO UPDATE SET scope=excluded.scope,origin=excluded.origin,started=excluded.started,status=excluded.status,detail=excluded.detail",
                (sid, scope, origin, now(), "running", ""),
            )

    def finish_job(self, sid: str, status: str, detail: str, origin: str, text: str):
        with self.transaction():
            self.db.execute("UPDATE jobs SET status=?,detail=? WHERE server=?", (status, detail, sid))
            if origin:
                self.db.execute("INSERT INTO outbox(origin,text,created) VALUES(?,?,?)", (origin, text, now()))

    def jobs(self) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM jobs ORDER BY started DESC")]

    def outbox(self) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM outbox ORDER BY id LIMIT 20")]

    def delivered(self, identifier: int, success: bool):
        with self.transaction():
            if success:
                self.db.execute("DELETE FROM outbox WHERE id=?", (identifier,))
            else:
                self.db.execute("UPDATE outbox SET attempts=attempts+1 WHERE id=?", (identifier,))

    def close(self):
        self.db.close()
