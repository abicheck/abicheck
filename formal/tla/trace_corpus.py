#!/usr/bin/env python3
"""Record disposition traces while abiforge runs its edit corpus (E11).

Every (operator, seed) cell of abiforge's default matrix is built with one
compiler and compared through abicheck, exactly as
``tests/test_abi_edit_corpus.py`` does, with :mod:`disposition_trace`
recording each disposition pipeline run. The traces are written as JSON for
``check_trace.py``.

    python3 formal/tla/trace_corpus.py --out traces.json [--compiler gcc]
        [--limit N] [--only OP_NAME ...]
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1]))  # abiforge lives at the repo root

from disposition_trace import recording  # noqa: E402

from abiforge.model.config import Toolchain, default_config  # noqa: E402
from abiforge.operators import matrix  # noqa: E402
from abiforge.run.harness import BaselineCache, _as_config, run_operator  # noqa: E402


def _applies(op, seed) -> bool:  # type: ignore[no-untyped-def]
    placeholder = Toolchain("gcc", 0, "gcc", "g++")
    return op.requires(default_config(seed.lang, placeholder)) is None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--compiler", default="gcc")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only", nargs="*", default=())
    a = ap.parse_args(argv)
    cells = [(op, seed) for op, seed in matrix() if _applies(op, seed)]
    if a.only:
        cells = [(op, s) for op, s in cells if op.name in a.only]
    if a.limit:
        cells = cells[: a.limit]
    ran = failed = 0
    with tempfile.TemporaryDirectory() as tmp, recording() as rec:
        root = Path(tmp)
        cache = BaselineCache(root / "baseline")
        for i, (op, seed) in enumerate(cells):
            rec.label = f"{op.name}/{seed.name}/{a.compiler}"
            try:
                cfg = _as_config(seed, a.compiler)
                run_operator(op, seed, cfg, cache, root / f"cell{i}")
                ran += 1
            except Exception as exc:  # noqa: BLE001 - an unbuildable cell is skipped and counted
                failed += 1
                print(f"skip {rec.label}: {type(exc).__name__}: {str(exc)[:120]}")
        rec.dump(a.out)
    print(f"cells={len(cells)} ran={ran} skipped={failed} traces={len(rec.traces)}")
    return 0 if ran else 2


if __name__ == "__main__":
    sys.exit(main())
