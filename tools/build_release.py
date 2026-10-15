"""Build an installable archive and validate required AstrBot metadata."""

import ast
import re
import tomllib
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import yaml

ROOT = Path(__file__).resolve().parent.parent
DIRECTORIES = ("mc_manager", "pages", ".astrbot-plugin", "docs", "companion")
FILES = (
    "main.py",
    "metadata.yaml",
    "_conf_schema.json",
    "requirements.txt",
    "README.md",
    "LICENSE",
    "logo.png",
    "config.example.json",
    "config.default.json",
    "tps.variables.json",
)
REQUIRED_FIELDS = ("name", "desc", "version", "author")


def validate_metadata(metadata: object) -> dict:
    if not isinstance(metadata, dict):
        raise ValueError("metadata.yaml must contain an object")
    for name in REQUIRED_FIELDS:
        if name not in metadata:
            raise ValueError(f"metadata.yaml is missing required field: {name}")
        if not isinstance(metadata[name], str) or not metadata[name].strip():
            raise ValueError(f"metadata.yaml field {name} must be a nonempty string")
    if not re.fullmatch(r"[A-Za-z0-9_]+", metadata["name"]):
        raise ValueError("metadata.yaml name must be a safe plugin directory name")
    if not re.fullmatch(r"v?\d+\.\d+\.\d+", metadata["version"]):
        raise ValueError("metadata.yaml version must use semantic version format")
    return metadata


def build(output_directory: Path | None = None) -> Path:
    metadata = validate_metadata(yaml.safe_load((ROOT / "metadata.yaml").read_text(encoding="utf-8")))
    version = metadata["version"].removeprefix("v")
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    if project["project"]["version"] != version:
        raise ValueError("pyproject.toml version differs from metadata.yaml")
    core_tree = ast.parse((ROOT / "mc_manager" / "__init__.py").read_text(encoding="utf-8"))
    core_version = next(
        ast.literal_eval(node.value)
        for node in core_tree.body
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__version__" for t in node.targets)
    )
    if core_version != version:
        raise ValueError("core version differs from metadata.yaml")
    output_dir = Path(output_directory) if output_directory is not None else ROOT / "dist"
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"{metadata['name']}-{version}.zip"
    prefix = metadata["name"] + "/"
    paths = [ROOT / name for name in FILES]
    for directory in DIRECTORIES:
        paths.extend(p for p in (ROOT / directory).rglob("*") if p.is_file() and "__pycache__" not in p.parts)
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for path in sorted(paths):
            relative = path.relative_to(ROOT).as_posix()
            archive.write(path, prefix + relative)
            # 4.28 discovers pages/, while 4.29 prefers views/. Both entries
            # come from one maintained source so their content stays identical.
            if relative.startswith("pages/"):
                archive.write(path, prefix + "views/" + relative.removeprefix("pages/"))
    with ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise ValueError("ZIP checksum validation failed")
        entries = set(archive.namelist())
        for name in (
            "main.py",
            "metadata.yaml",
            "LICENSE",
            "logo.png",
            "_conf_schema.json",
            "pages/console/index.html",
            "views/console/index.html",
        ):
            if prefix + name not in entries:
                raise ValueError(f"ZIP is missing required file: {name}")
        validate_metadata(yaml.safe_load(archive.read(prefix + "metadata.yaml").decode("utf-8")))
        for path in (ROOT / "pages").rglob("*"):
            if path.is_file():
                relative = path.relative_to(ROOT / "pages").as_posix()
                if archive.read(prefix + "pages/" + relative) != archive.read(prefix + "views/" + relative):
                    raise ValueError("The pages and views entry files must match")
        if any(".research" in n or "sqlite" in n or "__pycache__" in n for n in entries):
            raise ValueError("ZIP contains a research, database, or cache file")
    print(f"Built {output} ({output.stat().st_size:,} bytes; {len(entries)} files)")
    return output


if __name__ == "__main__":
    build()
