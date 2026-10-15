#!/usr/bin/env python3
# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Lower ADR-061 debt baselines that sit above a file's real line count.

``check_architecture.py`` fails on *slack*: a ``baseline_lines`` greater than
the file's actual length lets the file grow back up to the baseline unseen.
This script closes that gap by rewriting each slack entry's ``baseline_lines``
to the actual line count.

Contract:

* Only ``baseline_lines`` values are lowered. Nothing is ever raised, no entry
  is added or removed, and every other byte of ``architecture/debt.yaml`` is
  left as it was (the edit is a targeted text substitution, not a re-dump).
* An entry whose file is now under its ``architecture/modules.yaml`` cap is
  *not* lowered: the schema floor is the cap, so such an entry cannot carry the
  real size. It is reported instead, and must be removed by hand -- the cap
  itself binds that file once its entry is gone.
* Idempotent: a second run finds no slack and changes nothing.

Run it after a shrink::

    python scripts/tighten_debt_baselines.py          # rewrite the ledger
    python scripts/tighten_debt_baselines.py --check  # report only, exit 1 on slack

Exit codes: 0 = no slack left (or just rewritten); 1 = ``--check`` found slack,
or a rewrite could not be applied safely.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from check_architecture import _line_count  # noqa: E402 -- same counter as the gate


@dataclass(frozen=True)
class Slack:
    """One debt entry whose baseline is above the file's real line count."""

    path: str
    baseline: int
    actual: int
    floor: int

    @property
    def lowerable(self) -> bool:
        return self.actual >= self.floor


def find_slack(root: Path) -> list[Slack]:
    """Return every entry with ``baseline_lines`` above its actual size."""
    debt = json.loads((root / "architecture/debt.yaml").read_text(encoding="utf-8"))
    limits = json.loads(
        (root / "architecture/modules.yaml").read_text(encoding="utf-8")
    ).get("limits", {})
    production = limits.get("production", 800)
    test = limits.get("test", 1200)
    slack: list[Slack] = []
    for record in debt["files"]:
        path = record["path"]
        target = root / path
        if not target.is_file():
            continue  # a missing file is the gate's "debt-path" finding
        actual = _line_count(target)
        baseline = record["baseline_lines"]
        if actual < baseline:
            floor = test if path.startswith("tests/") else production
            slack.append(Slack(path, baseline, actual, floor))
    return slack


def tighten_text(text: str, slack: list[Slack]) -> str:
    """Return ``text`` with every lowerable slack baseline rewritten.

    Each substitution locates the one flat JSON object that carries the entry's
    ``"path"``, in either key order, then rewrites the ``"baseline_lines"`` value
    inside that object only. The result is re-parsed and must differ from the
    input only in the intended baseline values, so a formatting surprise fails
    loudly instead of corrupting the ledger.
    """
    before = json.loads(text)
    rewritten = text
    for item in slack:
        if not item.lowerable:
            continue
        # A record is the block from its own ``    {`` line to its ``    }`` line.
        # Matching by line, not by brace, keeps a brace inside a rationale from
        # cutting the record short.
        record_pattern = re.compile(
            r"^    \{\n(?:      [^\n]*\n)*?    \}", re.MULTILINE
        )
        records = [
            match
            for match in record_pattern.finditer(rewritten)
            if f'"path": "{item.path}"' in match.group(0)
        ]
        if len(records) != 1:
            raise ValueError(
                f"{item.path}: expected one record naming it, found {len(records)}"
            )
        record = records[0].group(0)
        if len(re.findall(r'"baseline_lines": \d+', record)) != 1:
            raise ValueError(f"{item.path}: expected one baseline_lines in its record")
        new_record = re.sub(
            r'("baseline_lines": )\d+',
            lambda m, n=item.actual: m.group(1) + str(n),
            record,
            count=1,
        )
        start, end = records[0].span()
        rewritten = rewritten[:start] + new_record + rewritten[end:]
    after = json.loads(rewritten)
    lowered = {item.path: item.actual for item in slack if item.lowerable}
    for old, new in zip(before["files"], after["files"], strict=True):
        if old["path"] != new["path"]:
            raise ValueError(f"entry order changed at {old['path']}")
        expected = lowered.get(old["path"], old["baseline_lines"])
        if new["baseline_lines"] != expected or {
            k: v for k, v in old.items() if k != "baseline_lines"
        } != {k: v for k, v in new.items() if k != "baseline_lines"}:
            raise ValueError(f"unexpected change to {old['path']}")
    if len(before["files"]) != len(after["files"]):
        raise ValueError("entry count changed")
    return rewritten


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="report slack without rewriting; exit 1 if any is found",
    )
    parser.add_argument(
        "--root", type=Path, default=ROOT, help=argparse.SUPPRESS
    )  # tests exercise miniature trees
    args = parser.parse_args(argv)
    root: Path = args.root
    ledger = root / "architecture/debt.yaml"
    slack = find_slack(root)
    if not slack:
        print("debt baselines: no slack")
        return 0
    for item in sorted(slack, key=lambda s: s.path):
        if item.lowerable:
            print(
                f"{item.path}: baseline {item.baseline} -> {item.actual} "
                f"(actual {item.actual})"
            )
        else:
            print(
                f"{item.path}: actual {item.actual} is under its cap {item.floor}; "
                "remove this entry from architecture/debt.yaml (not lowered: the "
                "schema floor is the cap)"
            )
    if args.check:
        print(f"debt baselines: {len(slack)} slack entr(y/ies); run without --check")
        return 1
    text = ledger.read_text(encoding="utf-8")
    rewritten = tighten_text(text, slack)
    if rewritten != text:
        ledger.write_text(rewritten, encoding="utf-8")
    remaining = [item for item in slack if not item.lowerable]
    if remaining:
        print(
            f"debt baselines: {len(remaining)} entr(y/ies) under cap need manual removal"
        )
        return 1
    print("debt baselines: lowered")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
