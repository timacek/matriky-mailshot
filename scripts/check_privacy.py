"""Fail when a tracked file looks like it carries personal data.

Run before every push:  python scripts/check_privacy.py
Exit code 0 = clean, 1 = at least one hit.

The patterns below are deliberately generic (any e-mail outside example.*, any CZ
phone number, any absolute local path) — the denylist never names a real address,
because this file is published too. Legacy campaign words are assembled from parts
for the same reason.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE = re.compile(r"(?<!\d)(?:\+?420)?[\s-]?\d{3}[\s-]?\d{3}[\s-]?\d{3}(?!\d)")
WIN_PATH = re.compile(r"(?i)\b[a-z]:[\\/](?:users|vyvoj|temp|projekty)\b")
UNIX_HOME = re.compile(r"/home/[a-z0-9_.-]+/")
# Assembled from parts so this scanner never flags itself (see module docstring).
PRIVATE_STACK = re.compile(
    r"(?i)\b(?:"
    + "|".join(["hermes" + "_home", "google_token" + r"\.json", "bridge" + "-token", r"api[_-]?key\s*[:=]"])
    + r")\b"
)
LEGACY_WORDS = re.compile(
    r"(?i)\b(?:" + "|".join(["sva" + "tba", "O" + "AMP", "CENO" + "MAR", "rodn[ýé] list"]) + r")\b"
)
EXAMPLE_DOMAINS = ("example.com", "example.org", "example.net", "example.cz")

SKIP_SUFFIXES = {".pyc", ".png", ".jpg", ".jpeg", ".ico", ".gif", ".db", ".sqlite", ".woff", ".woff2"}
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".venv", "venv", "node_modules"}

# The shipped SOVM dataset is open data (DIA declares it contains no personal data), so its
# e-mail column is expected. Everything else — phones, paths, secrets — is still checked there,
# and the header must match, so the file cannot silently become a dumping ground.
DATA_FILES = {
    "data/matriky-cz.csv": ("kod_uradu", "nazev_uradu", "obec", "kraj", "lat", "lon", "email")
}


def _is_example_email(match: re.Match) -> bool:
    domain = match.group(0).split("@", 1)[1].lower()
    return any(domain == allowed or domain.endswith("." + allowed) for allowed in EXAMPLE_DOMAINS)


def scan_line(line: str, where: str, allow_email: bool = False) -> list[str]:
    hits = []
    if not allow_email:
        for match in EMAIL.finditer(line):
            if not _is_example_email(match):
                hits.append(f"{where}: e-mail mimo example.*: {match.group(0)[:60]}")
    match = PHONE.search(line)
    if match:
        hits.append(f"{where}: telefonní číslo: {match.group(0).strip()}")
    match = WIN_PATH.search(line)
    if match:
        hits.append(f"{where}: absolutní cesta: {match.group(0)}")
    match = UNIX_HOME.search(line)
    if match:
        hits.append(f"{where}: domovská cesta: {match.group(0)}")
    match = PRIVATE_STACK.search(line)
    if match:
        hits.append(f"{where}: stopa soukromého stacku: {match.group(0)}")
    match = LEGACY_WORDS.search(line)
    if match:
        hits.append(f"{where}: stopa původní kampaně: {match.group(0)}")
    return hits


def scan(paths: list[Path]) -> list[str]:
    hits: list[str] = []
    for path in paths:
        if path.suffix.lower() in SKIP_SUFFIXES or not path.is_file():
            continue
        key = path.as_posix()
        allow_email = key in DATA_FILES
        text = path.read_text(encoding="utf-8", errors="replace")
        if allow_email:
            header = tuple(text.splitlines()[0].split(";")) if text else ()
            if not header[: len(DATA_FILES[key])] == DATA_FILES[key]:
                hits.append(f"{key}:1: datový soubor nemá očekávanou hlavičku")
        for number, line in enumerate(text.splitlines(), 1):
            hits.extend(scan_line(line, f"{path}:{number}", allow_email=allow_email))
    return hits


def tracked_files() -> list[Path]:
    output = subprocess.run(
        ["git", "ls-files"], capture_output=True, text=True, check=True
    ).stdout
    return [Path(line) for line in output.splitlines() if line.strip()]


def main() -> int:
    paths = tracked_files()
    hits = scan(paths)
    for hit in hits:
        print("LEAK " + hit)
    print(f"PRIVACY {'ok' if not hits else 'failed'} files={len(paths)} hits={len(hits)}")
    return 0 if not hits else 1


if __name__ == "__main__":
    sys.exit(main())
