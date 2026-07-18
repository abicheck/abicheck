#!/usr/bin/env python3
# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Keep examples/README.md's "Current Validation Status" table honest.

The table's "Result" column is hand-typed prose summarizing the latest
gcc/clang/runtime/release/stripped/build-source validator runs. Nothing
previously checked it against a real run, so it silently drifted for several
releases (the catalog grew from 169 to 181 cases while the table still said
169; PR #547's own audit found the table still claiming 148 PASS / 1 XFAIL
when the actual run was 145 PASS / 4 XFAIL). This script is the fix: given the
same JSON artifacts the `Examples Validation` CI workflow already produces, it
recomputes each row's Result cell and either checks the README against that
(``--check``, the CI mode — exits 1 on drift) or writes it in place
(default, for local refreshes).

This is a narrow structural sync check, not a generator for the whole table:
the Command/Executed-where columns and the free-form narrative bullets below
the table stay hand-maintained, because they require judgment (why a
release-headers case regressed, whether a gap is release-blocking) that a
script cannot manufacture from a status count alone. The Scope column is the
one exception: for the four lanes that always run the whole catalog
(``Default/debug verdicts``, ``Runtime smoke``, ``Release headers``,
``Stripped headers``), Scope is a plain "<N> catalog cases" count that must
equal ``len(ground_truth.json["verdicts"])`` — checked/fixed unconditionally
(no artifact needed), since a catalog-size bump (e.g. 186 -> 191) is otherwise
silent here exactly like a stale Result cell was before this script existed.

Usage:

    python scripts/check_examples_validation_status_sync.py \\
        --gcc results/validate_examples-gcc.json \\
        --clang results/validate_examples-clang.json \\
        --runtime results/example-runtime-smoke.json \\
        --release results/validate_examples-release-headers.json \\
        --stripped results/validate_examples-stripped-headers.json \\
        --build-source results/validate_examples-build-source.json \\
        --check

Any artifact flag may be omitted (e.g. for a partial local run); that row is
then left untouched and excluded from the check.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parents[1]
README = REPO_DIR / "examples" / "README.md"
GROUND_TRUTH = REPO_DIR / "examples" / "ground_truth.json"

# Rows whose Scope column (4th cell) is a plain "<N> catalog cases" count that
# must track the live case count in ground_truth.json — these lanes always
# run every case (skips/xfails included), unlike the fixed representative
# subsets ("Build/autodiscovery", "Build/source smoke"). Checked unconditionally
# (no artifact needed): this caught the table claiming "186 catalog cases" in
# every one of these rows for several releases after the catalog grew to 191,
# because nothing previously compared the Scope cell to ground_truth.json.
SCOPE_CATALOG_ROWS = (
    "Default/debug verdicts",
    "Runtime smoke",
    "Release headers",
    "Stripped headers",
)
_SCOPE_COUNT_RE = re.compile(r"\d+(?= catalog cases)")

# Row label (must match the README table's first column verbatim) -> the
# fixed status-count order this script renders it in. Renders only counts
# that are actually present (nonzero), joined as "N STATUS / N STATUS / ...".
ROW_STATUS_ORDER = {
    "Default/debug verdicts": ("PASS", "FAIL", "XFAIL", "SKIP", "ERROR"),
    "Runtime smoke": (
        "DEMONSTRATED",
        "NO_RUNTIME_SIGNAL",
        "BASELINE_SIGNAL",
        "SKIP",
        "BUILD_ERROR",
    ),
    "Release headers": ("PASS", "FAIL", "XFAIL", "SKIP", "ERROR"),
    "Stripped headers": ("PASS", "FAIL", "XFAIL", "SKIP", "ERROR"),
    "Build/source smoke": ("PASS", "FAIL", "XFAIL", "SKIP", "ERROR"),
}


def _load_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _format_summary(summary: dict[str, object], order: tuple[str, ...]) -> str:
    parts = [f"{summary[status]} {status}" for status in order if summary.get(status)]
    if not parts:
        raise ValueError(f"empty summary for order {order!r}: {summary!r}")
    return " / ".join(parts)


def _row_result(label: str, artifact: dict[str, object] | None) -> str | None:
    if artifact is None:
        return None
    summary = artifact.get("summary")
    if not isinstance(summary, dict):
        raise ValueError(f"{label}: artifact has no 'summary' dict")
    order = ROW_STATUS_ORDER[label]
    return _format_summary(summary, order)


def _replace_result_cell(text: str, label: str, new_result: str) -> str:
    """Replace the Result column (5th ``|``-delimited cell) of one table row.

    Table rows look like ``| Label | Command | Executed where | Scope |
    Result | Status |`` — split on ``|`` and replace index 5 (1-indexed
    cells after the leading empty split), leaving every other column and
    the free-form Status column untouched.
    """
    pattern = re.compile(rf"^\| {re.escape(label)} \|.*\|$", re.MULTILINE)
    match = pattern.search(text)
    if not match:
        raise ValueError(f"{README}: no table row found for label {label!r}")
    cells = match.group(0).split("|")
    # cells[0] is "" (text before the leading '|'); cells[1] is the label.
    if len(cells) < 7:
        raise ValueError(f"{README}: row for {label!r} has fewer than 6 columns")
    cells[5] = f" {new_result} "
    new_row = "|".join(cells)
    return text[: match.start()] + new_row + text[match.end() :]


def _row_cells(text: str, label: str) -> list[str]:
    pattern = re.compile(rf"^\| {re.escape(label)} \|.*\|$", re.MULTILINE)
    match = pattern.search(text)
    if not match:
        raise ValueError(f"{README}: no table row found for label {label!r}")
    cells = match.group(0).split("|")
    if len(cells) < 7:
        raise ValueError(f"{README}: row for {label!r} has fewer than 6 columns")
    return cells


def _scope_catalog_count_mismatch(text: str, label: str, expected_n: int) -> str | None:
    """Return a description if *label*'s Scope cell doesn't say
    "<expected_n> catalog cases", else None.

    Only fires when the cell already matches the "<N> catalog cases" shape —
    a row using different wording (e.g. a fixed representative-subset count)
    is left alone rather than forced into this shape.
    """
    scope_cell = _row_cells(text, label)[4]
    m = _SCOPE_COUNT_RE.search(scope_cell)
    if m is None or int(m.group(0)) == expected_n:
        return None
    return f"{label} Scope: was '{m.group(0)} catalog cases', ground_truth.json has {expected_n}"


def _fix_scope_catalog_count(text: str, label: str, expected_n: int) -> str:
    pattern = re.compile(rf"^\| {re.escape(label)} \|.*\|$", re.MULTILINE)
    match = pattern.search(text)
    assert match is not None
    cells = match.group(0).split("|")
    cells[4] = _SCOPE_COUNT_RE.sub(str(expected_n), cells[4])
    new_row = "|".join(cells)
    return text[: match.start()] + new_row + text[match.end() :]


def _combine_gcc_clang(
    gcc: dict[str, object] | None, clang: dict[str, object] | None
) -> dict[str, object] | None:
    """The "Default/debug verdicts" row reports gcc and clang as one cell
    today ("gcc: ... ; clang: ..."), but ROW_STATUS_ORDER assumes a single
    summary dict. Build a synthetic combined artifact whose 'summary' is a
    two-part string instead, matching the existing cell's shape.
    """
    if gcc is None or clang is None:
        return None
    gcc_summary = gcc.get("summary")
    clang_summary = clang.get("summary")
    if not isinstance(gcc_summary, dict) or not isinstance(clang_summary, dict):
        raise ValueError("gcc/clang artifacts must both have a 'summary' dict")
    order = ROW_STATUS_ORDER["Default/debug verdicts"]
    combined = (
        f"gcc: {_format_summary(gcc_summary, order)}; "
        f"clang: {_format_summary(clang_summary, order)}"
    )
    return {"summary": {"__combined__": combined}}


def _row_result_combined(artifact: dict[str, object] | None) -> str | None:
    if artifact is None:
        return None
    return str(artifact["summary"]["__combined__"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gcc", type=Path)
    parser.add_argument("--clang", type=Path)
    parser.add_argument("--runtime", type=Path)
    parser.add_argument("--release", type=Path)
    parser.add_argument("--stripped", type=Path)
    parser.add_argument("--build-source", type=Path)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 if examples/README.md's table would change; do not write.",
    )
    args = parser.parse_args(argv)

    gcc = _load_json(args.gcc) if args.gcc else None
    clang = _load_json(args.clang) if args.clang else None
    runtime = _load_json(args.runtime) if args.runtime else None
    release = _load_json(args.release) if args.release else None
    stripped = _load_json(args.stripped) if args.stripped else None
    build_source = _load_json(args.build_source) if args.build_source else None

    text = README.read_text(encoding="utf-8")

    ground_truth = _load_json(GROUND_TRUTH)
    verdicts = ground_truth.get("verdicts")
    if not isinstance(verdicts, dict):
        print(f"error: {GROUND_TRUTH} has no 'verdicts' object", file=sys.stderr)
        return 1
    catalog_count = len(verdicts)

    changed: list[str] = []
    for label in SCOPE_CATALOG_ROWS:
        mismatch = _scope_catalog_count_mismatch(text, label, catalog_count)
        if mismatch is not None:
            changed.append(mismatch)
            text = _fix_scope_catalog_count(text, label, catalog_count)

    combined = _combine_gcc_clang(gcc, clang)
    row_updates = {
        "Default/debug verdicts": _row_result_combined(combined),
        "Runtime smoke": _row_result("Runtime smoke", runtime),
        "Release headers": _row_result("Release headers", release),
        "Stripped headers": _row_result("Stripped headers", stripped),
        "Build/source smoke": _row_result("Build/source smoke", build_source),
    }

    for label, new_result in row_updates.items():
        if new_result is None:
            continue
        pattern = re.compile(rf"^\| {re.escape(label)} \|.*\|$", re.MULTILINE)
        match = pattern.search(text)
        if not match:
            print(f"error: {README} has no table row for {label!r}", file=sys.stderr)
            return 1
        current_result = match.group(0).split("|")[5].strip()
        if current_result != new_result:
            changed.append(f"{label}:\n  was: {current_result}\n  now: {new_result}")
            text = _replace_result_cell(text, label, new_result)

    if not changed:
        print("examples/README.md validation-status table is in sync.")
        return 0

    if args.check:
        print(
            "examples/README.md's validation-status table is stale. "
            "Run `python scripts/check_examples_validation_status_sync.py "
            "<same --flags as CI>` (without --check) to refresh it:\n",
            file=sys.stderr,
        )
        for entry in changed:
            print(entry, file=sys.stderr)
        return 1

    README.write_text(text, encoding="utf-8")
    print("Updated examples/README.md:\n")
    for entry in changed:
        print(entry)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
