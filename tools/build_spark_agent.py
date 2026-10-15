"""Compile the optional agent for Java 8 using javac or an existing ECJ tool."""

import shutil
import subprocess
import tempfile
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "companion" / "astrbot-spark-agent.jar"
SOURCE = ROOT / "companion" / "src" / "astrbot" / "spark" / "SparkAgent.java"
MANIFEST = "Manifest-Version: 1.0\r\nPremain-Class: astrbot.spark.SparkAgent\r\nCan-Redefine-Classes: false\r\nCan-Retransform-Classes: false\r\n\r\n"


def build():
    with tempfile.TemporaryDirectory(prefix="astrbot-spark-build-") as temporary:
        classes = Path(temporary) / "classes"
        classes.mkdir()
        javac = shutil.which("javac")
        if javac:
            arguments = [javac, "-source", "8", "-target", "8", "-encoding", "UTF-8", "-d", str(classes), str(SOURCE)]
        else:
            java = shutil.which("java")
            compiler = ROOT / ".research" / "agent" / "ecj-3.26.0.jar"
            if not java or not compiler.is_file():
                raise RuntimeError("Install a JDK, or provide Java and the development ECJ compiler.")
            arguments = [java, "-jar", str(compiler), "-1.8", "-encoding", "UTF-8", "-d", str(classes), str(SOURCE)]
        subprocess.run(arguments, check=True, timeout=60)
        with ZipFile(OUTPUT, "w", ZIP_DEFLATED) as archive:
            archive.writestr("META-INF/MANIFEST.MF", MANIFEST)
            for path in classes.rglob("*.class"):
                archive.write(path, path.relative_to(classes).as_posix())
    with ZipFile(OUTPUT) as archive:
        if archive.testzip() is not None or "astrbot/spark/SparkAgent.class" not in archive.namelist():
            raise RuntimeError("Agent JAR validation failed")
    print(f"Built {OUTPUT} ({OUTPUT.stat().st_size:,} bytes)")
    return OUTPUT


if __name__ == "__main__":
    build()
