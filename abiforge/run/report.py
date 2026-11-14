"""Per-(build, consumer) pair report of an edit-corpus run.

Each corpus cell writes its pairs as one JSON file (``write_cell``), so xdist
workers never share a file; ``summarize`` folds a run's files into a Markdown
table for the workflow summary and its tracking issue. Run as
``python -m abiforge.run.report RESULTS_DIR`` to print that table
(stdlib only: the workflow's report job runs it without installing abicheck).

The summary counts what each pair *did*: ``binary-break``, ``source-break``,
``no-break`` (only those three ran), ``infeasible`` (a mix that cannot work
whatever the edit) and ``unavailable`` (a host gap). The last two are listed
with their reasons and are never counted as passes.
"""

from __future__ import annotations

import itertools
import json
import sys
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # stdlib-only at runtime: the report job installs nothing
    from abiforge.run.cells import CellResult


def write_cell(out_dir: Path, cell_id: str, result: CellResult) -> Path:
    """Write *result*'s pair rows (or its whole-cell status) under *out_dir*."""
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = result.pair_rows() or [
        {
            "op": result.op,
            "seed": result.seed,
            "build": result.config.build_id,
            "producer": result.config.producer.id,
            "consumer": "*",
            "outcome": result.status,
            "cell": result.cls,
            "detail": result.reason,
        }
    ]
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in cell_id)
    text = json.dumps(rows, indent=1, sort_keys=True) + "\n"
    # Two cells can share an id (pairwise rows that collapse to one config) or
    # sanitize to one name: never overwrite, take the next free ``~N`` name.
    # Exclusive create, so concurrent xdist workers cannot claim the same file.
    for n in itertools.count(1):
        path = out_dir / (f"{safe}.json" if n == 1 else f"{safe}~{n}.json")
        try:
            with path.open("x", encoding="utf-8") as fh:
                fh.write(text)
        except FileExistsError:
            continue
        return path
    raise AssertionError("unreachable")


def load(pairs_dir: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for f in sorted(pairs_dir.glob("*.json")):
        rows.extend(json.loads(f.read_text(encoding="utf-8")))
    return rows


def summarize(rows: list[dict[str, str]], limit: int = 40) -> str:
    """Markdown: outcome counts per consumer, then every pair that did not run."""
    if not rows:
        return "No (build, consumer) pairs were recorded.\n"
    outcomes = sorted({r["outcome"] for r in rows})
    # Consumer ids are ``<toolchain>-<standard>-<flags>``; neither the standard
    # nor the flags id has a ``-`` but a toolchain id can (``apple-clang15``):
    # drop the flags from the right, one row per (compiler, standard).
    by_consumer: dict[str, Counter[str]] = {}
    for r in rows:
        key = r["consumer"].rsplit("-", 1)[0]
        by_consumer.setdefault(key, Counter())[r["outcome"]] += 1
    lines = [
        f"{len(rows)} (build, consumer) pairs over "
        f"{len({(r['op'], r['seed'], r['build']) for r in rows})} library builds.",
        "",
        "| consumer (toolchain-standard) | " + " | ".join(outcomes) + " |",
        "|---|" + "---|" * len(outcomes),
    ]
    for consumer, counts in sorted(by_consumer.items()):
        lines.append(
            f"| `{consumer}` | " + " | ".join(str(counts[o]) for o in outcomes) + " |"
        )
    pairs: dict[tuple[str, str], Counter[str]] = {}
    for r in rows:
        if r["consumer"] != "*":
            key = (r.get("producer", "?"), r["consumer"].rsplit("-", 2)[0])
            pairs.setdefault(key, Counter())[r["outcome"]] += 1
    if pairs:
        lines += [
            "",
            "| build toolchain | consumer toolchain | " + " | ".join(outcomes) + " |",
            "|---|---|" + "---|" * len(outcomes),
        ]
        for (producer, user), counts in sorted(pairs.items()):
            lines.append(
                f"| {producer} | {user} | "
                + " | ".join(str(counts[o]) for o in outcomes)
                + " |"
            )
    blocked = [r for r in rows if r["outcome"] in ("infeasible", "unavailable")]
    if blocked:
        lines += [
            "",
            f"Pairs that did not run ({len(blocked)}; never counted as passes):",
            "",
        ]
        reasons = Counter((r["outcome"], r["detail"]) for r in blocked)
        for (outcome, detail), n in reasons.most_common(limit):
            lines.append(f"- {outcome} x{n}: {detail}")
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: python -m abiforge.run.report RESULTS_DIR", file=sys.stderr)
        return 64
    sys.stdout.write(summarize(load(Path(argv[0]) / "pairs")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
