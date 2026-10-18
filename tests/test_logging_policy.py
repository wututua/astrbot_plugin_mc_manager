"""Keep plugin and development sources within AstrBot's logger convention."""

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def standard_logger_imports(paths):
    found = []
    for path in paths:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else:
                continue
            if any(module == "logging" or module.startswith("logging.") for module in modules):
                found.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    return found


class LoggingPolicyTests(unittest.TestCase):
    def test_runtime_sources_do_not_import_standard_logger(self):
        paths = [ROOT / "main.py", *sorted((ROOT / "mc_manager").rglob("*.py"))]
        self.assertEqual(standard_logger_imports(paths), [])

    def test_development_sources_use_memory_only_logger_doubles(self):
        paths = [path for directory in ("tests", "tools") for path in (ROOT / directory).rglob("*.py")]
        self.assertEqual(standard_logger_imports(paths), [])
