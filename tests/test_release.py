"""Release regression tests, including AstrBot's actual archive inspector."""

import ast
import copy
import logging
import os
import shutil
import tempfile
import types
import unittest
from pathlib import Path
from zipfile import ZipFile

import yaml

from tools.build_release import ROOT, build, validate_metadata

RELEASE = ROOT / ".research" / "release" / "astrbot" / "core"
HAS_OFFICIAL = (RELEASE / "star" / "updater.py").is_file() and (RELEASE / "zip_updater.py").is_file()


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
        "zipfile": __import__("zipfile"),
        "yaml": yaml,
        "Path": Path,
        "logger": logging.getLogger("archive-regression"),
        "shutil": types.SimpleNamespace(rmtree=checked_rmtree, move=checked_move),
        "on_error": on_error,
        "ensure_dir": lambda path: Path(path).mkdir(parents=True, exist_ok=True),
    }
    nodes = []
    for source, class_name, methods, base in [
        (
            RELEASE / "zip_updater.py",
            "_RepoZipUpdater",
            {"_resolve_archive_root_dir", "_finalize_extracted_archive"},
            [],
        ),
        (
            RELEASE / "star" / "updater.py",
            "_PluginUpdater",
            {
                "find_plugin_metadata_entry",
                "validate_plugin_metadata",
                "inspect_plugin_archive",
                "validate_plugin_archive",
                "_extract_plugin_archive",
            },
            [ast.Name(id="_RepoZipUpdater", ctx=ast.Load())],
        ),
    ]:
        tree = ast.parse(source.read_text(encoding="utf-8"))
        if class_name == "_PluginUpdater":
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
    return namespace["_PluginUpdater"]


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
            self.assertEqual(path.name, "astrbot_plugin_mc_manager-1.0.1.zip")
            with ZipFile(path) as archive:
                self.assertIsNone(archive.testzip())
                metadata = validate_metadata(yaml.safe_load(archive.read("astrbot_plugin_mc_manager/metadata.yaml")))
                self.assertEqual(metadata["version"], "v1.0.1")
                for name in ("main.py", "_conf_schema.json", "mc_manager/service.py", "pages/console/index.html"):
                    self.assertIn("astrbot_plugin_mc_manager/" + name, archive.namelist())
                self.assertFalse(any("sqlite" in name or ".research" in name for name in archive.namelist()))

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
            self.assertEqual(inspected["metadata"]["version"], "v1.0.1")
            destination = directory / "installed"
            inspector()._extract_plugin_archive(str(path), str(destination))
            for name in ("main.py", "metadata.yaml", "_conf_schema.json", "pages/console/index.html"):
                self.assertTrue((destination / name).is_file(), name)
            self.assertFalse((destination / "astrbot_plugin_mc_manager").exists())
            self.assertFalse(path.exists())
