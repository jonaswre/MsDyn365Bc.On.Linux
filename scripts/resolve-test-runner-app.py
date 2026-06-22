#!/usr/bin/env python3
"""Resolve Microsoft Test Runner from a Business Central artifact tree."""

from __future__ import annotations

import argparse
import os
import sys

from _bcapp import read_app_info, version_tuple

TEST_RUNNER_APP_ID = "23de40a6-dfe8-4f80-80db-d70f83ce8caf"


def resolve_test_runner_app(artifact_dir: str) -> str | None:
    best: dict | None = None
    for root, _, files in os.walk(artifact_dir):
        for filename in files:
            if not filename.endswith(".app"):
                continue
            if ".symbols." in filename.lower():
                continue
            info = read_app_info(os.path.join(root, filename))
            if info is None:
                continue
            if info.get("id") != TEST_RUNNER_APP_ID:
                continue
            if info.get("name") != "Test Runner" or info.get("publisher") != "Microsoft":
                continue
            if best is None or version_tuple(info["version"]) > version_tuple(best["version"]):
                best = info

    if best is None:
        return None
    return str(best["path"])


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Print the Microsoft Test Runner .app path from BC artifacts."
    )
    parser.add_argument("artifact_dir", help="Business Central artifact root")
    args = parser.parse_args()

    path = resolve_test_runner_app(args.artifact_dir)
    if path is None:
        print(
            f"Microsoft Test Runner app not found in {args.artifact_dir}",
            file=sys.stderr,
        )
        return 1
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
