#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


IGNORED_TOP_LEVEL_KEYS = {"platform", "diagnostics"}
MISSING_VALUE = object()
MISSING_VALUE_OUTPUT = {"__missing_key__": True}


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


def render_diff_value(value: Any) -> Any:
    if value is MISSING_VALUE:
        return MISSING_VALUE_OUTPUT
    if isinstance(value, dict):
        return {key: render_diff_value(child) for key, child in value.items()}
    if isinstance(value, list):
        return [render_diff_value(item) for item in value]
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
            left_value = left[key] if key in left else MISSING_VALUE
            right_value = right[key] if key in right else MISSING_VALUE
            diffs.extend(flatten_diff(child_path, left_value, right_value))
        return diffs
    return [{"path": path, "linux": render_diff_value(left), "windows": render_diff_value(right)}]


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
                matched_linux = [item for item in remaining_linux if item_matches(item, delta.get("match", {}))]
                if not matched_linux:
                    continue
                applied.append({"delta": delta, "diff": {"path": diff["path"], "linux": matched_linux, "windows": []}})
                remaining_linux = list_remainder(remaining_linux, matched_linux)
            if not remaining_linux and not remaining_windows:
                handled = True
            elif remaining_linux != linux_items or remaining_windows != windows_items:
                unexpected.append({"path": diff["path"], "linux": remaining_linux, "windows": remaining_windows})
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
        print(f"  linux:   {json.dumps(diff['linux'], sort_keys=True)}")
        print(f"  windows: {json.dumps(diff['windows'], sort_keys=True)}")
    return 1 if result.unexpected else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
