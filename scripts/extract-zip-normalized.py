#!/usr/bin/env python3
"""Extract zip files while normalizing Windows path separators."""

from __future__ import annotations

import fnmatch
import shutil
import sys
import zipfile
from pathlib import Path


def normalize_name(name: str) -> str:
    return name.replace("\\", "/").lstrip("/")


def is_safe_target(root: Path, target: Path) -> bool:
    return target == root or root in target.parents


def should_extract(name: str, patterns: list[str]) -> bool:
    if not patterns:
        return True
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns)


def extract_zip(archive: Path, destination: Path, patterns: list[str]) -> int:
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    normalized_patterns = [normalize_name(pattern) for pattern in patterns]
    extracted = 0

    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            name = normalize_name(info.filename)
            if not name or not should_extract(name, normalized_patterns):
                continue

            target = (destination / name).resolve()
            if not is_safe_target(root, target):
                raise RuntimeError(f"unsafe zip entry: {info.filename}")

            if info.is_dir() or name.endswith("/"):
                target.mkdir(parents=True, exist_ok=True)
                continue

            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
            extracted += 1

    if patterns and extracted == 0:
        return 1
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(
            "Usage: extract-zip-normalized.py <zip> <destination> [glob ...]",
            file=sys.stderr,
        )
        return 2

    archive = Path(argv[1])
    destination = Path(argv[2])
    patterns = argv[3:]

    try:
        return extract_zip(archive, destination, patterns)
    except Exception as exc:
        print(f"extract-zip-normalized.py: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
