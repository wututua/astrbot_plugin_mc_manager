"""Audit tagged configuration examples without modifying user config or plugin runtime."""

from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from logger_stub import logger_stub  # noqa: E402

from mc_manager.config import Settings  # noqa: E402
from mc_manager.service import Service  # noqa: E402


def git_json(tag, filename):
    return json.loads(subprocess.check_output(["git", "show", f"{tag}:{filename}"], cwd=ROOT))


def extract_sdk_methods(root):
    source = root / "core/config/astrbot_config.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    original = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "AstrBotConfig")
    names = {"_config_schema_to_default_config", "check_config_integrity"}
    methods = [
        copy.deepcopy(node) for node in original.body if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    assert {node.name for node in methods} == names
    default_tree = ast.parse((root / "core/config/default.py").read_text(encoding="utf-8"))
    default_node = next(
        node
        for node in default_tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "DEFAULT_VALUE_MAP" for target in node.targets)
    )
    namespace = {"DEFAULT_VALUE_MAP": ast.literal_eval(default_node.value), "logger": logger_stub("config-audit")}
    module = ast.Module(
        body=[
            ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
            ast.ClassDef(name="SDKConfigProbe", bases=[], keywords=[], body=methods, decorator_list=[]),
        ],
        type_ignores=[],
    )
    exec(compile(ast.fix_missing_locations(module), str(source), "exec"), namespace)
    return namespace["SDKConfigProbe"](), hashlib.sha256(source.read_bytes()).hexdigest()


def flatten(value, prefix=""):
    output = {}
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else key
            if isinstance(child, (dict, list)) and child:
                output.update(flatten(child, path))
            else:
                output[path] = child
    elif isinstance(value, list):
        for index, child in enumerate(value):
            output.update(flatten(child, f"{prefix}[{index}]"))
    else:
        output[prefix] = value
    return output


def removed(before, after):
    return sorted(set(flatten(before)) - set(flatten(after)))


def normalize_sdk(probe, schema, source):
    candidate = copy.deepcopy(source)
    defaults = probe._config_schema_to_default_config(copy.deepcopy(schema))
    changed = probe.check_config_integrity(defaults, candidate, schema=copy.deepcopy(schema))
    return candidate, changed


def audit(sdks):
    schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
    tags = subprocess.check_output(["git", "tag", "--list"], cwd=ROOT, text=True).splitlines()
    result = {
        "date": "2026-10-19",
        "scope": "Tagged repository examples and extracted official schema-integrity methods; no user files, services, network endpoints or databases are changed.",
        "sdks": {},
        "versions": tags,
    }
    for label, path in sdks:
        probe, digest = extract_sdk_methods(path)
        matrix = []
        for tag in tags:
            raw = git_json(tag, "config.example.json")
            loaded, changed = normalize_sdk(probe, schema, raw)
            row = {"version": tag, "framework_changed": changed, "removed_by_framework": removed(raw, loaded)}
            try:
                settings = Settings(loaded)
                row.update(
                    loadable=True,
                    removed_by_plugin=removed(loaded, settings.raw),
                    preserved_messages=all(
                        settings.messages.get(key) == value for key, value in raw.get("messages", {}).items()
                    ),
                    preserved_commands=all(
                        settings.commands.get(key) == value for key, value in raw.get("commands", {}).items()
                    ),
                    preserved_server_ids=[item["id"] for item in raw.get("servers", [])] == list(settings.servers),
                    preserved_group_ids=[item["id"] for item in raw.get("groups", [])] == list(settings.groups),
                )
            except Exception as error:
                row.update(loadable=False, error=str(error))
            matrix.append(row)
        sentinel = git_json("v1.5.0", "config.example.json")
        sentinel["audit_unknown_top"] = "keep-top"
        sentinel["messages"]["audit_unknown_template"] = "keep-template"
        sentinel["servers"][0]["audit_unknown_server"] = "keep-server"
        loaded, _ = normalize_sdk(probe, schema, sentinel)
        native = git_json("v1.4.0", "config.example.json")
        native["servers"][0]["tps_mode"] = "native"
        native["servers"][0].pop("tps_command", None)
        native_loaded, _ = normalize_sdk(probe, schema, native)
        settings = Settings(native_loaded)
        sid = native["servers"][0]["id"]
        response = "Overall: Mean tick time: 12.3 ms. Mean TPS: 19.98"
        rendered = Service.format_tps(SimpleNamespace(settings=settings), sid, response)
        result["sdks"][label] = {
            "source_sha256": digest,
            "matrix": matrix,
            "sentinel": {
                "top_level_preserved": "audit_unknown_top" in loaded,
                "message_preserved": "audit_unknown_template" in loaded["messages"],
                "server_item_preserved": "audit_unknown_server" in loaded["servers"][0],
            },
            "legacy_native": {
                "source_version": "v1.4.0",
                "source_mode": "native",
                "source_has_tps_output": "tps_output" in native["servers"][0],
                "effective_command": settings.servers[sid].tps_command,
                "effective_output": settings.servers[sid].tps_output,
                "old_expected_output": "raw",
                "raw_output_preserved": rendered == response,
                "rendered_preview": rendered[:1200],
            },
        }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sdk", action="append", required=True, help="Label=path/to/astrbot (repeat for multiple pinned SDKs)"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    sdks = [(value.split("=", 1)[0], Path(value.split("=", 1)[1])) for value in args.sdk]
    result = audit(sdks)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for version, data in result["sdks"].items():
        print(version, "examples_loadable=", sum(row["loadable"] for row in data["matrix"]), "/", len(data["matrix"]))
        print("sentinel:", data["sentinel"])
        print("native:", {key: value for key, value in data["legacy_native"].items() if key != "rendered_preview"})
        print("framework_removed:", sorted({path for row in data["matrix"] for path in row["removed_by_framework"]}))
        print(
            "plugin_removed:",
            sorted(
                {
                    path.split("].", 1)[-1] if path.startswith("servers[") else path
                    for row in data["matrix"]
                    for path in row.get("removed_by_plugin", [])
                }
            ),
        )


if __name__ == "__main__":
    main()
