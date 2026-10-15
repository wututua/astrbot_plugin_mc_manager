"""Build an installable archive with one plugin root and no local secrets."""

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "dist" / "astrbot_plugin_mc_manager-1.0.0.zip"
DIRECTORIES = ("mc_manager", "pages", ".astrbot-plugin", "docs")
FILES = ("main.py", "metadata.yaml", "_conf_schema.json", "requirements.txt", "README.md", "config.example.json")


def build():
    OUTPUT.parent.mkdir(exist_ok=True)
    paths = [ROOT / name for name in FILES]
    for directory in DIRECTORIES:
        paths.extend(p for p in (ROOT / directory).rglob("*") if p.is_file() and "__pycache__" not in p.parts)
    with ZipFile(OUTPUT, "w", ZIP_DEFLATED) as archive:
        for path in sorted(paths):
            archive.write(path, "astrbot_plugin_mc_manager/" + path.relative_to(ROOT).as_posix())
    with ZipFile(OUTPUT) as archive:
        assert archive.testzip() is None
        assert "astrbot_plugin_mc_manager/main.py" in archive.namelist()
        assert not any(".research" in n or "sqlite" in n or "__pycache__" in n for n in archive.namelist())
    print(f"Built {OUTPUT} ({OUTPUT.stat().st_size:,} bytes; {len(paths)} files)")


if __name__ == "__main__":
    build()
