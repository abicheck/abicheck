#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Nikolay Petrov
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

"""Split the example catalog across CI shards, and merge the shards back.

``tests/validate_examples.py`` runs the whole ~195-case catalog in one
sequential process; on the Examples Validation workflow that step measured
5-8 minutes and, once the artifact-mode job was parallelised, became the
critical path. The runner itself needs no changes to be shardable -- it
already accepts a positional case-name filter -- so this module owns only
the two pieces CI additionally needs:

``list``
    Which case names belong to shard *i* of *n*. Assignment is round-robin
    over the sorted ground-truth keys rather than contiguous slicing,
    because per-case cost is very uneven (a ``sources``/build-source case
    costs far more than a plain two-binary compare) and contiguous slices
    would leave one straggler leg. Round-robin interleaves cheap and
    expensive cases across every shard without needing per-case timings.

``merge``
    Recombine the shards' JSON artifacts into the single whole-catalog
    artifact the downstream collector requires.
    ``validation/scripts/collect_full_example_matrix.py`` enforces that the
    gcc/clang artifacts cover the catalog exactly -- no missing ids, no
    duplicates, and ``selected_cases`` equal to the full catalog size -- so
    a bare shard artifact is rejected by design, and something has to put
    the pieces back together before it runs.

Case names are matched by *full* name, never by prefix: ``case10`` is a
prefix of ``case100``-``case109``, so prefix-based shards would overlap.
This module verifies that no ground-truth name is a substring of another
(``assert_names_are_unambiguous``), which is what makes the runner's
substring filter safe to drive from a shard list.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

_REPO_DIR = Path(__file__).resolve().parent.parent

# Phase 3 resolver (scripts/CLAUDE.md, docs/contribute/plans/examples-catalog-split.md).
if str(_REPO_DIR / "scripts") not in sys.path:
    sys.path.insert(0, str(_REPO_DIR / "scripts"))
import example_catalog  # noqa: E402

GROUND_TRUTH = example_catalog.GROUND_TRUTH_PATH

# Fields that describe the *run*, not the cases in it, and so must agree
# across every shard of one toolchain. Disagreement means the shards came
# from different checkouts or different toolchains and must not be merged.
_INVARIANT_FIELDS = (
    "schema_version",
    "runner",
    "ground_truth_sha256",
    "ground_truth_cases",
    "toolchain",
    "artifact_variants",
)


def load_case_names(ground_truth: Path = GROUND_TRUTH) -> list[str]:
    """Return every ground-truth case name, sorted (the runner's own order)."""
    data = json.loads(ground_truth.read_text(encoding="utf-8"))
    return sorted(data["verdicts"].keys())


def assert_names_are_unambiguous(names: list[str]) -> None:
    """Fail if any case name is a substring of another.

    The runner filters cases by substring, so an ambiguous name would make
    a shard silently pull in cases assigned to a different shard -- which
    surfaces downstream only as a confusing "duplicate case ids" error.
    Checking here turns that into an immediate, self-explaining failure.
    """
    ambiguous = [(a, b) for a in names for b in names if a != b and a in b]
    if ambiguous:
        pairs = ", ".join(f"{a!r} in {b!r}" for a, b in ambiguous)
        raise SystemExit(f"ambiguous case names would overlap across shards: {pairs}")


def shard_names(names: list[str], shard: int, of: int) -> list[str]:
    """Return the names assigned to 1-based *shard* out of *of* shards."""
    if not 1 <= shard <= of:
        raise SystemExit(f"--shard must be in 1..{of}, got {shard}")
    return names[shard - 1 :: of]


def merge_payloads(
    payloads: list[dict[str, Any]], *, expected: list[str]
) -> dict[str, Any]:
    """Merge shard payloads into one whole-catalog payload.

    Refuses to produce an artifact the downstream collector would reject:
    the merged case set must be exactly *expected*, with no duplicates.
    Catching that here names the offending shard boundary, instead of
    surfacing as a bare "missing case ids" list two jobs later.
    """
    if not payloads:
        raise SystemExit("no shard payloads to merge")

    reference = payloads[0]
    for field in _INVARIANT_FIELDS:
        values = {json.dumps(p.get(field), sort_keys=True) for p in payloads}
        if len(values) != 1:
            raise SystemExit(
                f"shards disagree on {field!r}: {sorted(values)} -- "
                "they were not produced by one checkout/toolchain"
            )

    results: list[dict[str, Any]] = []
    for payload in payloads:
        rows = payload.get("results")
        if not isinstance(rows, list):
            raise SystemExit("a shard payload has no 'results' list")
        results.extend(rows)

    case_ids = [str(row.get("case_id") or row.get("name")) for row in results]
    duplicates = sorted(c for c, n in Counter(case_ids).items() if n > 1)
    if duplicates:
        raise SystemExit(f"duplicate case ids across shards: {', '.join(duplicates)}")
    missing = sorted(set(expected) - set(case_ids))
    if missing:
        raise SystemExit(f"missing case ids after merge: {', '.join(missing)}")
    unexpected = sorted(set(case_ids) - set(expected))
    if unexpected:
        raise SystemExit(f"unexpected case ids after merge: {', '.join(unexpected)}")

    # Status counts are per-case, so they sum; everything else describing the
    # run is invariant and carried through from the first shard.
    summary: dict[str, int] = {}
    for payload in payloads:
        for status, count in (payload.get("summary") or {}).items():
            summary[status] = summary.get(status, 0) + int(count)

    merged = dict(reference)
    merged["results"] = sorted(results, key=lambda row: str(row.get("case_id") or ""))
    merged["summary"] = summary
    merged["selected_cases"] = len(case_ids)
    merged["command"] = [
        f"merged from {len(payloads)} shards by tests/example_shards.py"
    ]
    return merged


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="print the case names for one shard")
    p_list.add_argument("--shard", type=int, required=True, help="1-based shard index")
    p_list.add_argument("--of", type=int, required=True, help="total number of shards")
    p_list.add_argument(
        "--pytest-k",
        action="store_true",
        help="emit a pytest -k expression instead of a space-separated list",
    )

    p_merge = sub.add_parser("merge", help="merge shard JSON artifacts into one")
    p_merge.add_argument("inputs", nargs="+", type=Path, help="shard JSON artifacts")
    p_merge.add_argument("--out", type=Path, required=True, help="merged artifact path")

    args = ap.parse_args(argv)
    names = load_case_names()
    assert_names_are_unambiguous(names)

    if args.command == "list":
        selected = shard_names(names, args.shard, args.of)
        if not selected:
            raise SystemExit(
                f"shard {args.shard}/{args.of} is empty -- more shards than cases?"
            )
        print(" or ".join(selected) if args.pytest_k else " ".join(selected))
        return 0

    payloads = [json.loads(p.read_text(encoding="utf-8")) for p in args.inputs]
    merged = merge_payloads(payloads, expected=names)
    args.out.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    print(
        f"merged {len(args.inputs)} shards -> {args.out} "
        f"({merged['selected_cases']} cases, summary={json.dumps(merged['summary'], sort_keys=True)})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
