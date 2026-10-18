"""Capacity failures must preserve config and avoid futile atomic rollback writes."""

import ast
import asyncio
import copy
import errno
import json
import os
import sqlite3
import tempfile
import threading
import types
import unittest
from unittest.mock import AsyncMock, patch

import test_plugin as fixture

from mc_manager.storage_errors import capacity_issue, capacity_message

SOURCE = fixture.RELEASE_ROOT / "core/config/astrbot_config.py"


def sdk_save_methods():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    config = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "AstrBotConfig")
    names = {"save_config_async", "_prepare_config_snapshot", "_write_config_snapshot"}
    methods = [
        copy.deepcopy(node)
        for node in config.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names
    ]
    namespace = dict(asyncio=asyncio, copy=copy, json=json, os=os, tempfile=tempfile)
    module = ast.Module(
        body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *methods],
        type_ignores=[],
    )
    exec(compile(ast.fix_missing_locations(module), str(SOURCE), "exec"), namespace)
    return {name: namespace[name] for name in names}


def attach_sdk_writer(config, path):
    for name, method in sdk_save_methods().items():
        setattr(config, name, types.MethodType(method, config))
    config.config_path = str(path)
    config._save_state_lock = threading.Lock()
    config._save_commit_lock = threading.Lock()
    config._save_revision = 0
    config._save_committed_revision = 0


class CapacityErrorTests(unittest.TestCase):
    def test_posix_space_and_quota(self):
        self.assertEqual(capacity_issue(OSError(errno.ENOSPC, "No space left on device")), "space")
        if hasattr(errno, "EDQUOT"):
            error = OSError(errno.EDQUOT, "Disk quota exceeded")
            self.assertEqual(capacity_issue(error), "quota")
            self.assertIn("存储配额", capacity_message(error))

    def test_windows_full_and_sqlite_full(self):
        for code in (39, 112):
            error = OSError("Windows disk full")
            error.winerror = code
            self.assertEqual(capacity_issue(error), "space")
        error = sqlite3.OperationalError("database or disk is full")
        error.sqlite_errorcode = sqlite3.SQLITE_FULL
        self.assertEqual(capacity_issue(error), "space")

    def test_other_errors_are_not_reported_as_capacity_failures(self):
        for error in (
            PermissionError(errno.EACCES, "denied"),
            FileNotFoundError(errno.ENOENT, "missing"),
            OSError("disk full"),
            RuntimeError("No space left on device"),
            sqlite3.OperationalError("database is locked"),
        ):
            with self.subTest(error=error):
                self.assertIsNone(capacity_issue(error))

    def test_wrapped_failure_and_cyclic_causes(self):
        outer = RuntimeError("wrapped")
        outer.__cause__ = OSError(errno.ENOSPC, "full")
        self.assertEqual(capacity_issue(outer), "space")
        outer.__cause__ = outer
        self.assertIsNone(capacity_issue(outer))


@unittest.skipUnless(fixture.HAS_WEB and SOURCE.is_file(), "requires official AstrBot SDK")
class ConfigCapacityTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.PluginAdapterTests.asyncSetUp
    asyncTearDown = fixture.PluginAdapterTests.asyncTearDown
    real_service = fixture.PluginAdapterTests.real_service
    bound_request = fixture.PluginAdapterTests.bound_request

    def payload(self):
        raw = copy.deepcopy(dict(self.plugin.config))
        raw["page_size"] = 20
        return {"config": raw, "revision": self.plugin.config_revision}

    async def save(self, payload):
        with (
            self.bound_request(payload),
            patch.object(
                self.main,
                "Service",
                lambda settings, path, send, **kwargs: self.real_service(settings, path, send, **kwargs),
            ),
        ):
            return await self.plugin.page_save_config()

    async def test_real_sdk_atomic_enospc_keeps_file_and_instance_then_retries(self):
        path = self.root / "plugin-config.json"
        original = json.dumps(dict(self.plugin.config), ensure_ascii=False).encode("utf-8")
        path.write_bytes(original)
        attach_sdk_writer(self.plugin.config, path)
        current = self.plugin.service
        revision = self.plugin.config_revision
        payload = self.payload()
        writer = self.plugin.config.save_config_async
        with (
            patch.object(self.plugin.config, "save_config_async", wraps=writer) as saves,
            patch("os.fsync", side_effect=OSError(errno.ENOSPC, "No space left on device")),
            patch.object(current, "pause", wraps=current.pause) as pause,
        ):
            response = await self.save(payload)
        self.assertEqual(response.status_code, 400)
        self.assertIn("可用空间不足", json.loads(response.body)["message"])
        self.assertIn("本次修改未生效", json.loads(response.body)["message"])
        self.assertEqual(saves.await_count, 1)
        pause.assert_not_awaited()
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(list(self.root.glob(".plugin-config.json.*.tmp")), [])
        self.assertIs(self.plugin.service, current)
        self.assertFalse(current.closed)
        self.assertEqual(self.plugin.config_revision, revision)
        self.assertNotIn("page_size", self.plugin.config)
        self.plugin.logger.exception.assert_not_called()
        # The framework can commit a later revision after storage is writable.
        response = await self.save(payload)
        self.assertEqual(response.status_code, 200, response.body)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8-sig"))["page_size"], 20)
        self.assertIsNot(self.plugin.service, current)

    async def test_windows_full_uses_one_write_and_returns_actionable_message(self):
        error = OSError("disk full")
        error.winerror = 112
        with patch.object(self.plugin.config, "save_config_async", new=AsyncMock(side_effect=error)) as saves:
            response = await self.save(self.payload())
        self.assertEqual(saves.await_count, 1)
        self.assertIn("运行 AstrBot 的服务器", json.loads(response.body)["message"])
        self.assertNotIn("page_size", self.plugin.config)

    async def test_quota_failure_is_distinguished_and_not_retried(self):
        if not hasattr(errno, "EDQUOT"):
            self.skipTest("platform has no EDQUOT")
        with patch.object(
            self.plugin.config, "save_config_async", new=AsyncMock(side_effect=OSError(errno.EDQUOT, "quota"))
        ) as saves:
            response = await self.save(self.payload())
        self.assertEqual(saves.await_count, 1)
        self.assertIn("存储配额已用尽", json.loads(response.body)["message"])

    async def test_candidate_database_full_keeps_current_service(self):
        current = self.plugin.service
        error = sqlite3.OperationalError("database or disk is full")
        error.sqlite_errorcode = sqlite3.SQLITE_FULL
        with self.bound_request(self.payload()), patch.object(self.main, "Service", side_effect=error):
            response = await self.plugin.page_save_config()
        self.assertEqual(response.status_code, 400)
        self.assertIn("可用空间不足", json.loads(response.body)["message"])
        self.assertIs(self.plugin.service, current)
        self.assertNotIn("page_size", self.plugin.config)

    async def test_committed_config_is_written_back_after_later_failure(self):
        current = self.plugin.service
        before = dict(self.plugin.config)
        candidate = self.real_service(
            self.main.Settings(self.payload()["config"]),
            self.root / "data",
            self.plugin._send,
            reserve_scopes=False,
            logger=self.plugin.logger,
        )
        with (
            self.bound_request(self.payload()),
            patch.object(self.main, "Service", return_value=candidate),
            patch.object(candidate.store, "validate_scopes", side_effect=RuntimeError("scope failure")),
            patch.object(self.plugin.config, "save_config_async", new=AsyncMock(side_effect=[True, True])) as saves,
        ):
            response = await self.plugin.page_save_config()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(saves.await_count, 2)
        self.assertEqual(dict(self.plugin.config), before)
        self.assertIs(self.plugin.service, current)
        self.assertFalse(current.closed)

    async def test_rollback_full_does_not_claim_disk_was_restored(self):
        current = self.plugin.service
        candidate = self.real_service(
            self.main.Settings(self.payload()["config"]),
            self.root / "data",
            self.plugin._send,
            reserve_scopes=False,
            logger=self.plugin.logger,
        )
        with (
            self.bound_request(self.payload()),
            patch.object(self.main, "Service", return_value=candidate),
            patch.object(candidate.store, "validate_scopes", side_effect=RuntimeError("scope failure")),
            patch.object(
                self.plugin.config,
                "save_config_async",
                new=AsyncMock(side_effect=[True, OSError(errno.ENOSPC, "full")]),
            ) as saves,
        ):
            response = await self.plugin.page_save_config()
        text = json.loads(response.body)["message"]
        self.assertIn("回滚写盘也失败", text)
        self.assertIn("磁盘配置可能不同", text)
        self.assertIn("可用空间不足", text)
        self.assertEqual(saves.await_count, 2)
        self.assertIs(self.plugin.service, current)
        self.assertFalse(current.closed)
        self.assertNotIn("page_size", self.plugin.config)

    async def test_capacity_error_after_install_reports_config_already_written(self):
        candidate = self.real_service(
            self.main.Settings(self.payload()["config"]),
            self.root / "data",
            self.plugin._send,
            reserve_scopes=False,
            logger=self.plugin.logger,
        )
        with (
            self.bound_request(self.payload()),
            patch.object(self.main, "Service", return_value=candidate),
            patch.object(candidate, "start", new=AsyncMock(side_effect=OSError(errno.ENOSPC, "full"))),
        ):
            response = await self.plugin.page_save_config()
        self.assertIn("配置已写入，但服务切换未完成", json.loads(response.body)["message"])
        self.assertEqual(self.plugin.config["page_size"], 20)
        self.assertIs(self.plugin.service, candidate)

    async def test_cancellation_is_preserved_when_rollback_cannot_be_written(self):
        current = self.plugin.service
        with patch.object(
            self.plugin,
            "_persist_config",
            new=AsyncMock(side_effect=[asyncio.CancelledError(), OSError(errno.ENOSPC, "full")]),
        ) as saves:
            with self.assertRaises(asyncio.CancelledError):
                await self.save(self.payload())
        self.assertEqual(saves.await_count, 2)
        self.assertIs(self.plugin.service, current)
        self.assertFalse(current.closed)
        self.assertNotIn("page_size", self.plugin.config)
