#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


IGNORED_TOP_LEVEL_KEYS = {"platform", "diagnostics", "tests"}
IGNORED_PATHS = {"tests.runnerKind"}
MISSING_VALUE = object()


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
    if value is MISSING_VALUE:
        return value
    if isinstance(value, dict):
        return {key: normalize_for_compare(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return sorted((normalize_for_compare(item) for item in value), key=lambda item: json.dumps(item, sort_keys=True))
    return value


def diff_entry(path: str, left: Any, right: Any) -> dict[str, Any]:
    linux_missing = left is MISSING_VALUE
    windows_missing = right is MISSING_VALUE
    return {
        "path": path,
        "linux": None if linux_missing else left,
        "windows": None if windows_missing else right,
        "linuxMissing": linux_missing,
        "windowsMissing": windows_missing,
    }


def flatten_diff(path: str, left: Any, right: Any) -> list[dict[str, Any]]:
    if path in IGNORED_PATHS:
        return []
    left = normalize_for_compare(left)
    right = normalize_for_compare(right)
    if left == right:
        return []
    if isinstance(left, dict) and isinstance(right, dict):
        diffs: list[dict[str, Any]] = []
        for key in sorted(set(left) | set(right)):
            child_path = f"{path}.{key}" if path else key
            left_value = left[key] if key in left else MISSING_VALUE
            right_value = right[key] if key in right else MISSING_VALUE
            diffs.extend(flatten_diff(child_path, left_value, right_value))
        return diffs
    return [diff_entry(path, left, right)]


def item_matches(item: Any, match: dict[str, Any]) -> bool:
    if not isinstance(item, dict):
        return False
    return all(item.get(key) == value for key, value in match.items())


def item_key(item: Any) -> str:
    return json.dumps(normalize_for_compare(item), sort_keys=True)


def list_remainder(items: list[Any], items_to_subtract: list[Any]) -> list[Any]:
    subtract_counts: dict[str, int] = {}
    for item in items_to_subtract:
        key = item_key(item)
        subtract_counts[key] = subtract_counts.get(key, 0) + 1

    remainder: list[Any] = []
    for item in items:
        key = item_key(item)
        count = subtract_counts.get(key, 0)
        if count:
            subtract_counts[key] = count - 1
        else:
            remainder.append(item)
    return remainder


def pop_first_matching(items: list[Any], match: dict[str, Any]) -> tuple[Any | None, list[Any]]:
    for index, item in enumerate(items):
        if item_matches(item, match):
            return item, items[:index] + items[index + 1 :]
    return None, items


def format_diff_side(diff: dict[str, Any], side: str) -> str:
    if diff.get(f"{side}Missing", False):
        return "<MISSING>"
    return json.dumps(diff[side], sort_keys=True)


def apply_known_deltas(diffs: list[dict[str, Any]], known_deltas: list[dict[str, Any]]) -> CompareResult:
    unexpected: list[dict[str, Any]] = []
    applied: list[dict[str, Any]] = []
    for diff in diffs:
        handled = False
        if diff["path"] == "apps.customApps":
            if not isinstance(diff.get("linux"), list) or not isinstance(diff.get("windows"), list):
                unexpected.append(diff)
                continue
            linux_items = diff["linux"]
            windows_items = diff["windows"]
            remaining_linux = list_remainder(linux_items, windows_items)
            remaining_windows = list_remainder(windows_items, linux_items)
            for delta in known_deltas:
                if delta.get("path") != "apps.customApps[]":
                    continue
                matched_linux, remaining_linux = pop_first_matching(remaining_linux, delta.get("match", {}))
                if matched_linux is None:
                    continue
                applied.append({"delta": delta, "diff": {"path": diff["path"], "linux": [matched_linux], "windows": []}})
            if not remaining_linux and not remaining_windows:
                handled = True
            elif remaining_linux != linux_items or remaining_windows != windows_items:
                unexpected.append(
                    {
                        "path": diff["path"],
                        "linux": remaining_linux,
                        "windows": remaining_windows,
                        "linuxMissing": False,
                        "windowsMissing": False,
                    }
                )
                handled = True
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
        print(f"  linux:   {format_diff_side(diff, 'linux')}")
        print(f"  windows: {format_diff_side(diff, 'windows')}")
    return 1 if result.unexpected else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
