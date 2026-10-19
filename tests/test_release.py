"""Release regression tests, including AstrBot's actual archive inspector."""

import ast
import asyncio
import copy
import os
import posixpath
import re
import shutil
import tempfile
import types
import unittest
from dataclasses import dataclass
from pathlib import Path
from zipfile import ZipFile

import yaml
from logger_stub import logger_stub
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from tools.build_release import ROOT, build, validate_metadata

RELEASE_ROOT = Path(os.environ.get("MC_TEST_ASTRBOT_RELEASE", ROOT / ".research/release/astrbot"))
RELEASE = RELEASE_ROOT / "core"
LEGACY_INSTALLER = (RELEASE / "star" / "updator.py").is_file()
UPDATER_SOURCE = RELEASE / "star" / ("updator.py" if LEGACY_INSTALLER else "updater.py")
ZIP_UPDATER_SOURCE = RELEASE / ("zip_updator.py" if LEGACY_INSTALLER else "zip_updater.py")
UPDATER_CLASS = "PluginUpdator" if LEGACY_INSTALLER else "_PluginUpdater"
ZIP_UPDATER_CLASS = "RepoZipUpdator" if LEGACY_INSTALLER else "_RepoZipUpdater"
EXTRACT_METHOD = "unzip_file" if LEGACY_INSTALLER else "_extract_plugin_archive"
HAS_OFFICIAL = UPDATER_SOURCE.is_file() and ZIP_UPDATER_SOURCE.is_file()


def official_archive_inspector(allowed_root: Path):
    """Compile the unmodified release's archive methods without starting AstrBot."""
    allowed_root = allowed_root.resolve()

    def check_path(path):
        resolved = Path(path).resolve()
        if resolved == allowed_root or not resolved.is_relative_to(allowed_root):
            raise ValueError("test filesystem operation escaped the temporary directory")

    def checked_rmtree(path, **kwargs):
        check_path(path)
        return shutil.rmtree(path, **kwargs)

    def checked_move(source, destination):
        check_path(source)
        check_path(destination)
        return shutil.move(source, destination)

    def on_error(function, path, error):
        raise error[1]

    namespace = {
        "os": os,
        "re": re,
        "copy": copy.copy,
        "zipfile": __import__("zipfile"),
        "yaml": yaml,
        "Path": Path,
        "logger": logger_stub("archive-regression"),
        "shutil": types.SimpleNamespace(rmtree=checked_rmtree, move=checked_move),
        "on_error": on_error,
        "ensure_dir": lambda path: Path(path).mkdir(parents=True, exist_ok=True),
    }
    nodes = []
    for source, class_name, methods, base in [
        (
            ZIP_UPDATER_SOURCE,
            ZIP_UPDATER_CLASS,
            {"_resolve_archive_root_dir", "_finalize_extracted_archive"},
            [],
        ),
        (
            UPDATER_SOURCE,
            UPDATER_CLASS,
            {
                "find_plugin_metadata_entry",
                "validate_plugin_metadata",
                "inspect_plugin_archive",
                "validate_plugin_archive",
                EXTRACT_METHOD,
            },
            [ast.Name(id=ZIP_UPDATER_CLASS, ctx=ast.Load())],
        ),
    ]:
        tree = ast.parse(source.read_text(encoding="utf-8"))
        if class_name == UPDATER_CLASS:
            nodes.extend(
                copy.deepcopy(node)
                for node in tree.body
                if isinstance(node, ast.Assign)
                and any(
                    isinstance(target, ast.Name) and target.id.startswith("PLUGIN_METADATA_") for target in node.targets
                )
            )
        original = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
        selected = [
            copy.deepcopy(node) for node in original.body if isinstance(node, ast.FunctionDef) and node.name in methods
        ]
        if len(selected) != len(methods):
            raise AssertionError("AstrBot release archive methods have changed")
        nodes.append(ast.ClassDef(name=class_name, bases=base, keywords=[], body=selected, decorator_list=[]))
    module = ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[]))
    exec(compile(module, "astrbot-release-archive-methods", "exec"), namespace)
    return namespace[UPDATER_CLASS]


def official_version_checker():
    source = RELEASE / "star" / "star_manager.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    original = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_validate_astrbot_version_specifier"
    )
    method = copy.deepcopy(original)
    method.decorator_list = []
    module = ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[]))
    namespace = {
        "SpecifierSet": SpecifierSet,
        "InvalidSpecifier": InvalidSpecifier,
        "Version": Version,
        "InvalidVersion": InvalidVersion,
        "VERSION": "4.28.0",
    }
    exec(compile(module, str(source), "exec"), namespace)
    return namespace["_validate_astrbot_version_specifier"]


def discover_official_pages(plugin_root: Path):
    source = RELEASE_ROOT / "dashboard" / "services" / "plugin_page_service.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    page = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "PluginPage")
    service = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "PluginPageService")
    needed = {"normalize_plugin_page_name", "get_plugin_root_dir", "resolve_plugin_pages_root", "discover_plugin_pages"}
    methods = [
        copy.deepcopy(node)
        for node in service.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in needed
    ]
    if len(methods) != len(needed):
        raise AssertionError("AstrBot page discovery methods have changed")

    async def isdir(path):
        return Path(path).is_dir()

    async def isfile(path):
        return Path(path).is_file()

    namespace = {
        "dataclass": dataclass,
        "posixpath": posixpath,
        "Path": Path,
        "StarMetadata": types.SimpleNamespace,
        "PLUGIN_PAGE_ROOT_DIR_NAME": "pages",
        "PLUGIN_PAGE_ROOT_DIR_NAMES": ("views", "pages"),
        "PLUGIN_PAGE_ENTRY_FILE_NAME": "index.html",
        "aio_ospath": types.SimpleNamespace(isdir=isdir, isfile=isfile),
    }
    module = ast.Module(
        body=[
            copy.deepcopy(page),
            ast.ClassDef(name="PluginPageService", bases=[], keywords=[], body=methods, decorator_list=[]),
        ],
        type_ignores=[],
    )
    exec(compile(ast.fix_missing_locations(module), str(source), "exec"), namespace)
    instance = namespace["PluginPageService"]()
    instance.plugin_manager = types.SimpleNamespace(
        plugin_store_path=str(plugin_root.parent), reserved_plugin_path=str(plugin_root.parent)
    )
    metadata = types.SimpleNamespace(root_dir_name=plugin_root.name, reserved=False)
    return asyncio.run(instance.discover_plugin_pages(metadata))


def read_official_changelog(plugin_root: Path):
    source = RELEASE_ROOT / "dashboard" / "services" / "plugin_service.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    method = next(
        node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "get_plugin_changelog"
    )
    module = ast.fix_missing_locations(ast.Module(body=[copy.deepcopy(method)], type_ignores=[]))
    namespace = {"logger": logger_stub(__name__), "PluginServiceError": ValueError}
    exec(compile(module, str(source), "exec"), namespace)
    service = types.SimpleNamespace(resolve_plugin_dir=lambda name: plugin_root)
    return namespace["get_plugin_changelog"](service, "astrbot_plugin_mc_manager")


class ReleaseTests(unittest.TestCase):
    def metadata(self):
        return yaml.safe_load((ROOT / "metadata.yaml").read_text(encoding="utf-8"))

    def test_desc_is_required(self):
        metadata = self.metadata()
        metadata.pop("desc")
        with self.assertRaisesRegex(ValueError, "desc"):
            validate_metadata(metadata)

    def test_required_fields_must_be_nonempty_strings(self):
        for field in ("name", "desc", "version", "author"):
            for invalid in ("", "  ", None, False, 123, []):
                with self.subTest(field=field, invalid=invalid):
                    metadata = self.metadata()
                    metadata[field] = invalid
                    with self.assertRaises(ValueError):
                        validate_metadata(metadata)

    def test_short_desc_does_not_substitute_for_desc(self):
        metadata = self.metadata()
        self.assertTrue(metadata["short_desc"])
        metadata.pop("desc")
        with self.assertRaisesRegex(ValueError, "desc"):
            validate_metadata(metadata)

    def test_release_zip_contains_install_files_and_consistent_version(self):
        with tempfile.TemporaryDirectory() as temp:
            path = build(Path(temp))
            self.assertEqual(
                path.name, "astrbot_plugin_mc_manager-" + self.metadata()["version"].removeprefix("v") + ".zip"
            )
            with ZipFile(path) as archive:
                self.assertIsNone(archive.testzip())
                metadata = validate_metadata(yaml.safe_load(archive.read("astrbot_plugin_mc_manager/metadata.yaml")))
                self.assertEqual(metadata["version"], self.metadata()["version"])
                for name in (
                    "main.py",
                    "LICENSE",
                    "CHANGELOG.md",
                    "logo.png",
                    "_conf_schema.json",
                    "mc_manager/service.py",
                    "pages/console/index.html",
                    "pages/console/logo.png",
                    "views/console/logo.png",
                    "pages/console/settings.js",
                    "pages/console/operations.js",
                    "views/console/operations.js",
                    "mc_manager/backups.py",
                    "mc_manager/operations.py",
                    "mc_manager/operations_store.py",
                    "views/console/index.html",
                    "views/console/app.js",
                    "views/console/settings.js",
                ):
                    self.assertIn("astrbot_plugin_mc_manager/" + name, archive.namelist())
                self.assertFalse(any("sqlite" in name or ".research" in name for name in archive.namelist()))
                self.assertFalse(any("/tests/" in name or "/tools/" in name for name in archive.namelist()))
                self.assertFalse(
                    any("/companion/" in name or name.endswith((".jar", ".java")) for name in archive.namelist())
                )
                for filename in ("index.html", "app.js", "settings.js", "operations.js", "style.css"):
                    self.assertEqual(
                        archive.read("astrbot_plugin_mc_manager/pages/console/" + filename),
                        archive.read("astrbot_plugin_mc_manager/views/console/" + filename),
                    )

    @unittest.skipUnless(HAS_OFFICIAL, "requires researched AstrBot release source")
    def test_official_changelog_reads_repository_and_installed_package(self):
        expected = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn("## " + self.metadata()["version"], expected)
        content, message = read_official_changelog(ROOT)
        self.assertEqual(content["content"], expected)
        self.assertEqual(message, "成功获取更新日志")
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            archive = build(directory / "archive")
            installed = directory / "installed"
            inspector = official_archive_inspector(directory)
            getattr(inspector(), EXTRACT_METHOD)(str(archive), str(installed))
            content, message = read_official_changelog(installed)
            self.assertEqual(content["content"], expected)
            self.assertEqual(message, "成功获取更新日志")

    @unittest.skipUnless(HAS_OFFICIAL, "requires researched AstrBot release source")
    def test_official_inspector_reproduces_missing_desc_error(self):
        with tempfile.TemporaryDirectory() as temp:
            inspector = official_archive_inspector(Path(temp))
            metadata = self.metadata()
            metadata.pop("desc")
            path = Path(temp) / "broken.zip"
            with ZipFile(path, "w") as archive:
                archive.writestr(
                    "astrbot_plugin_mc_manager/metadata.yaml", yaml.safe_dump(metadata, allow_unicode=True)
                )
            with self.assertRaisesRegex(ValueError, "desc"):
                inspector.inspect_plugin_archive(str(path))

    @unittest.skipUnless(HAS_OFFICIAL, "requires researched AstrBot release source")
    def test_official_inspector_accepts_zip_and_import_flattens_plugin_root(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            path = build(directory / "archive")
            inspector = official_archive_inspector(directory)
            inspected = inspector.inspect_plugin_archive(str(path))
            self.assertEqual(inspected["metadata"]["name"], "astrbot_plugin_mc_manager")
            self.assertEqual(inspected["metadata"]["version"], self.metadata()["version"])
            destination = directory / "installed"
            getattr(inspector(), EXTRACT_METHOD)(str(path), str(destination))
            for name in ("main.py", "metadata.yaml", "_conf_schema.json", "pages/console/index.html"):
                self.assertTrue((destination / name).is_file(), name)
            self.assertFalse((destination / "astrbot_plugin_mc_manager").exists())
            self.assertFalse(path.exists())
            pages = discover_official_pages(destination)
            self.assertEqual([page.name for page in pages], ["console"])

    @unittest.skipUnless(HAS_OFFICIAL, "requires researched AstrBot release source")
    def test_official_version_gate_accepts_supported_428_and_429_releases(self):
        checker = official_version_checker()
        for version in ("4.28.0", "4.28.1", "4.28.2", "4.29.0"):
            with self.subTest(version=version):
                checker.__globals__["VERSION"] = version
                self.assertEqual(checker(self.metadata()["astrbot_version"]), (True, None))
        checker.__globals__["VERSION"] = "5.0.0"
        self.assertFalse(checker(self.metadata()["astrbot_version"])[0])

    @unittest.skipUnless(HAS_OFFICIAL, "requires researched AstrBot release source")
    def test_official_version_gate_rejects_pre_428_releases(self):
        checker = official_version_checker()
        for version in ("4.26.3", "4.27.3"):
            with self.subTest(version=version):
                checker.__globals__["VERSION"] = version
                valid, error = checker(self.metadata()["astrbot_version"])
                self.assertFalse(valid)
                self.assertEqual(error, f"AstrBot {version} does not satisfy plugin astrbot_version: >=4.28.0,<5")
