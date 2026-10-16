"""Download a fixed official AstrBot wheel for isolated compatibility checks."""

import re
import shutil
import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile


def fetch(version: str, destination: Path):
    if not re.fullmatch(r"4\.\d+\.\d+", version):
        raise ValueError("Use a fixed AstrBot 4.x release such as 4.29.0")
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    wheels = destination.parent / "wheels"
    wheels.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "download",
            "--disable-pip-version-check",
            "--no-deps",
            "--only-binary=:all:",
            "--dest",
            str(wheels),
            "astrbot==" + version,
        ],
        check=True,
    )
    archives = list(wheels.glob("astrbot-" + version + "-*.whl"))
    if len(archives) != 1:
        raise ValueError("Expected one fixed AstrBot wheel")
    with ZipFile(archives[0]) as archive:
        for info in archive.infolist():
            if not info.filename.startswith("astrbot/"):
                continue
            path = (destination / info.filename).resolve()
            if not path.is_relative_to(destination) or (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("Unsafe wheel member")
            if info.is_dir():
                path.mkdir(parents=True, exist_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, path.open("wb") as output:
                    shutil.copyfileobj(source, output)
    root = destination / "astrbot"
    if not (root / "api/web.py").is_file() or not (root / "dashboard/plugin_page_bridge.js").is_file():
        raise ValueError("The fixed wheel does not provide the required plugin APIs")
    print("Official AstrBot source:", root)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("Usage: python tools/fetch_astrbot.py VERSION DESTINATION")
    fetch(sys.argv[1], Path(sys.argv[2]))
