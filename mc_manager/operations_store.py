"""Operational persistence: retry queues, approvals, filtered logs and retention."""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

from .config import MCError


class OperationsStore:
    def upgrade_operations(self):
        additions = {
            "outbox": {
                "next_attempt": "REAL NOT NULL DEFAULT 0",
                "state": "TEXT NOT NULL DEFAULT 'pending'",
                "last_error": "TEXT NOT NULL DEFAULT ''",
            },
            "jobs": {"phase": "TEXT NOT NULL DEFAULT 'queued'"},
        }
        with self.transaction():
            for table, columns in additions.items():
                present = {row[1] for row in self.db.execute(f"PRAGMA table_info({table})")}
                for column, definition in columns.items():
                    if column not in present:
                        self.db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            for statement in (
                "CREATE INDEX IF NOT EXISTS logs_scope_id ON logs(scope,id DESC)",
                "CREATE INDEX IF NOT EXISTS logs_scope_user_id ON logs(scope,user,id DESC)",
                "CREATE INDEX IF NOT EXISTS logs_scope_player_time ON logs(scope,player,time)",
                "CREATE INDEX IF NOT EXISTS outbox_due ON outbox(state,next_attempt,id)",
                "CREATE TABLE IF NOT EXISTS requests(id INTEGER PRIMARY KEY AUTOINCREMENT,scope TEXT,platform TEXT,group_id TEXT,user TEXT,user_name TEXT,kind TEXT,target INTEGER,expected TEXT,player TEXT,uuid TEXT,created TEXT,status TEXT DEFAULT 'pending',reviewer TEXT DEFAULT '',detail TEXT DEFAULT '')",
                "CREATE UNIQUE INDEX IF NOT EXISTS requests_one_pending ON requests(scope,user) WHERE status='pending'",
                "CREATE TABLE IF NOT EXISTS alerts(server TEXT,kind TEXT,active INTEGER DEFAULT 0,count INTEGER DEFAULT 0,last_sent REAL DEFAULT 0,detail TEXT DEFAULT '',PRIMARY KEY(server,kind))",
            ):
                self.db.execute(statement)

    def outbox(self) -> list[dict]:
        return [
            dict(row)
            for row in self.db.execute(
                "SELECT * FROM outbox WHERE state='pending' AND next_attempt<=? ORDER BY id LIMIT 20", (time.time(),)
            )
        ]

    def delivered(
        self,
        identifier: int,
        success: bool,
        error: str = "",
        retry_seconds: int = 30,
        max_attempts: int = 5,
        max_age_hours: int = 24,
    ):
        with self.transaction():
            if success:
                self.db.execute("DELETE FROM outbox WHERE id=?", (identifier,))
                return
            item = self.db.execute("SELECT * FROM outbox WHERE id=?", (identifier,)).fetchone()
            if not item:
                return
            try:
                created = datetime.fromisoformat(item["created"]).timestamp()
            except (ValueError, TypeError):
                created = time.time()
            attempts = item["attempts"] + 1
            state = "failed" if attempts >= max_attempts or time.time() - created >= max_age_hours * 3600 else "pending"
            delay = min(86400, retry_seconds * 2 ** min(attempts - 1, 10))
            self.db.execute(
                "UPDATE outbox SET attempts=?,next_attempt=?,state=?,last_error=? WHERE id=?",
                (attempts, time.time() + delay, state, error[:500], identifier),
            )

    def notifications(self, scope: str = "") -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM outbox" + (" WHERE scope=?" if scope else "") + " ORDER BY id DESC LIMIT 200",
            (scope,) if scope else (),
        )
        return [dict(row) for row in rows]

    def retry_notification(self, identifier: int, scope: str = ""):
        with self.transaction():
            row = self.db.execute("SELECT * FROM outbox WHERE id=?", (identifier,)).fetchone()
            if not row or (scope and row["scope"] != scope):
                raise MCError("未找到当前配置组的通知。")
            self.db.execute(
                "UPDATE outbox SET state='pending',attempts=0,next_attempt=0,last_error='',created=? WHERE id=?",
                (datetime.now(timezone.utc).isoformat(timespec="seconds"), identifier),
            )

    def enqueue(self, recipients: list[tuple[str, str]], text: str):
        created = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for scope, origin in recipients:
            self.db.execute(
                "INSERT INTO outbox(origin,text,created,scope) VALUES(?,?,?,?)", (origin, text, created, scope)
            )

    @staticmethod
    def log_filter(scope: str, user: str = "", player: str = "", action: str = "", start: str = "", end: str = ""):
        clauses, values = ["scope=?"], [scope]
        for column, value in (("user", user), ("action", action)):
            if value:
                clauses.append(column + "=?")
                values.append(value)
        if player:
            clauses.append("instr(lower(player),lower(?))>0")
            values.append(player)
        for operator, value in ((">=", start), ("<=", end)):
            if value:
                try:
                    stamp = datetime.fromisoformat(value)
                    if stamp.tzinfo is None:
                        stamp = stamp.replace(tzinfo=timezone.utc)
                    normalized = stamp.astimezone(timezone.utc).isoformat(timespec="seconds")
                except ValueError:
                    raise MCError("日期格式应为 ISO 8601，例如 2026-10-16T00:00:00+08:00。") from None
                clauses.append("time" + operator + "?")
                values.append(normalized)
        return " AND ".join(clauses), values

    def filtered_logs(self, scope: str, page: int, size: int, **filters) -> tuple[list[dict], int]:
        clause, args = self.log_filter(scope, **filters)
        total = self.db.execute("SELECT COUNT(*) FROM logs WHERE " + clause, args).fetchone()[0]
        rows = self.db.execute(
            "SELECT * FROM logs WHERE " + clause + " ORDER BY id DESC LIMIT ? OFFSET ?",
            (*args, size, (page - 1) * size),
        )
        return [dict(row) for row in rows], total

    def retention_ids(self, days: int, maximum: int) -> list[int]:
        clauses, values = [], []
        if days:
            clauses.append("time<?")
            values.append((datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds"))
        if maximum:
            clauses.append("id NOT IN (SELECT id FROM logs ORDER BY id DESC LIMIT ?)")
            values.append(maximum)
        if not clauses:
            return []
        return [row[0] for row in self.db.execute("SELECT id FROM logs WHERE " + " OR ".join(clauses), values)]

    def prune_logs(self, identifiers: list[int]) -> int:
        with self.transaction():
            before = self.db.total_changes
            self.db.executemany("DELETE FROM logs WHERE id=?", ((item,) for item in identifiers))
            return self.db.total_changes - before

    def job_phase(self, sid: str, phase: str, detail: str = ""):
        with self.transaction():
            self.db.execute(
                "UPDATE jobs SET phase=?,detail=? WHERE server=? AND status='running'", (phase, detail, sid)
            )

    def create_request(self, group, actor, kind: str, name: str, identifier: str, entry: dict | None = None) -> int:
        import sqlite3

        try:
            cursor = self.db.execute(
                "INSERT INTO requests(scope,platform,group_id,user,user_name,kind,target,expected,player,uuid,created) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    group.id,
                    actor.platform,
                    actor.group,
                    actor.user,
                    actor.name,
                    kind,
                    entry["id"] if entry else None,
                    json.dumps(entry, ensure_ascii=False) if entry else "",
                    name,
                    identifier,
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                ),
            )
        except sqlite3.IntegrityError:
            raise MCError("你已有待审核的申请，请等待管理员处理。") from None
        return cursor.lastrowid

    def pending_requests(self, scope: str, user: str = "") -> list[dict]:
        return [
            dict(row)
            for row in self.db.execute(
                "SELECT * FROM requests WHERE scope=? AND status='pending'"
                + (" AND user=?" if user else "")
                + " ORDER BY id LIMIT 500",
                (scope, user) if user else (scope,),
            )
        ]
