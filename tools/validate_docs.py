import json
import re
import subprocess
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def headings(text):
    result = set()
    counts = {}
    for line in text.splitlines():
        match = re.match(r"^#{1,6}\s+(.+?)(?:\s+#+)?$", line)
        if not match:
            continue
        slug = re.sub(r"<[^>]+>", "", match[1]).strip().lower()
        slug = "".join(ch for ch in slug if ch.isalnum() or ch in "_- ")
        slug = slug.replace(" ", "-")
        duplicate = counts.get(slug, 0)
        counts[slug] = duplicate + 1
        result.add(slug + (f"-{duplicate}" if duplicate else ""))
    return result


tracked = (
    subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT)
    .decode()
    .split("\0")
)
tracked = list(dict.fromkeys(ROOT / name for name in tracked if name and (ROOT / name).is_file()))
errors = []
links = 0
snippets = 0
for source in [p for p in tracked if p.suffix == ".md"]:
    text = source.read_text(encoding="utf-8")
    for match in re.finditer(r"\[[^\]\n]*\]\(([^)\n]+)\)", text):
        target = match[1].strip().split(' "')[0].strip("<>")
        parsed = urllib.parse.urlsplit(target)
        if parsed.scheme or parsed.netloc:
            continue
        path = (source.parent / urllib.parse.unquote(parsed.path)).resolve() if parsed.path else source
        links += 1
        if not path.exists():
            errors.append(f"{source.relative_to(ROOT)}: missing {target}")
        elif (
            parsed.fragment
            and path.suffix == ".md"
            and urllib.parse.unquote(parsed.fragment) not in headings(path.read_text(encoding="utf-8"))
        ):
            errors.append(f"{source.relative_to(ROOT)}: missing anchor {target}")
    for match in re.finditer(r"(?ms)^\x60{3}json\s*\n(.*?)^\x60{3}", text):
        json.loads(match[1])
        snippets += 1
for path in [p for p in tracked if p.suffix == ".json"]:
    json.loads(path.read_text(encoding="utf-8"))

for path in tracked:
    if path.suffix in {".png", ".jar"}:
        continue
    data = path.read_text(encoding="utf-8")
    for label, pattern in {
        "private key": r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
        "GitHub token": r"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{50,})",
        "OpenAI key": r"sk-(?:proj-)?[A-Za-z0-9_-]{45,}",
        "AWS access key": r"AKIA[0-9A-Z]{16}",
    }.items():
        if re.search(pattern, data):
            errors.append(f"{path.relative_to(ROOT)}: possible {label}")
if errors:
    raise SystemExit("\n".join(errors))
print(
    f"Document checks passed: {links} relative links, {snippets} JSON snippets; tracked JSON and secret-pattern scan passed."
)
