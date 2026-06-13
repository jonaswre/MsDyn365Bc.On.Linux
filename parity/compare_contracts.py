#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


IGNORED_TOP_LEVEL_KEYS = {"platform", "diagnostics"}


@dataclass
class CompareResult:
    unexpected: list[dict[str, Any]]
    applied_known_deltas: list[dict[str, Any]]


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_known_deltas(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    data = load_json(path)
    if not isinstance(data, list):
        raise ValueError(f"{path} must contain a JSON array")
    return data


def normalize_for_compare(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: normalize_for_compare(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return sorted((normalize_for_compare(item) for item in value), key=lambda item: json.dumps(item, sort_keys=True))
    return value


def flatten_diff(path: str, left: Any, right: Any) -> list[dict[str, Any]]:
    left = normalize_for_compare(left)
    right = normalize_for_compare(right)
    if left == right:
        return []
    if isinstance(left, dict) and isinstance(right, dict):
        diffs: list[dict[str, Any]] = []
        for key in sorted(set(left) | set(right)):
            child_path = f"{path}.{key}" if path else key
            diffs.extend(flatten_diff(child_path, left.get(key), right.get(key)))
        return diffs
    return [{"path": path, "linux": left, "windows": right}]


def item_matches(item: Any, match: dict[str, Any]) -> bool:
    if not isinstance(item, dict):
        return False
    return all(item.get(key) == value for key, value in match.items())


def apply_known_deltas(diffs: list[dict[str, Any]], known_deltas: list[dict[str, Any]]) -> CompareResult:
    unexpected: list[dict[str, Any]] = []
    applied: list[dict[str, Any]] = []
    for diff in diffs:
        handled = False
        for delta in known_deltas:
            if delta.get("path") == "apps.customApps[]" and diff["path"] == "apps.customApps":
                linux_items = diff.get("linux") if isinstance(diff.get("linux"), list) else []
                windows_items = diff.get("windows") if isinstance(diff.get("windows"), list) else []
                extra_linux = [item for item in linux_items if item not in windows_items]
                if extra_linux and all(item_matches(item, delta.get("match", {})) for item in extra_linux):
                    applied.append({"delta": delta, "diff": diff})
                    handled = True
                    break
        if not handled:
            unexpected.append(diff)
    return CompareResult(unexpected=unexpected, applied_known_deltas=applied)


def compare_contracts(linux_contract: dict[str, Any], windows_contract: dict[str, Any], known_deltas: list[dict[str, Any]]) -> CompareResult:
    left = {key: value for key, value in linux_contract.items() if key not in IGNORED_TOP_LEVEL_KEYS}
    right = {key: value for key, value in windows_contract.items() if key not in IGNORED_TOP_LEVEL_KEYS}
    return apply_known_deltas(flatten_diff("", left, right), known_deltas)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Compare Linux and Windows BC parity contracts")
    parser.add_argument("--linux", required=True, type=Path)
    parser.add_argument("--windows", required=True, type=Path)
    parser.add_argument("--known-deltas", default=Path("parity/known-deltas.json"), type=Path)
    args = parser.parse_args(argv)

    result = compare_contracts(load_json(args.linux), load_json(args.windows), load_known_deltas(args.known_deltas))
    for applied in result.applied_known_deltas:
        reason = applied["delta"].get("reason", "known delta")
        print(f"KNOWN {applied['diff']['path']}: {reason}")
    for diff in result.unexpected:
        print(f"DIFF {diff['path']}")
        print(f"  linux:   {json.dumps(diff['linux'], sort_keys=True)}")
        print(f"  windows: {json.dumps(diff['windows'], sort_keys=True)}")
    return 1 if result.unexpected else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
