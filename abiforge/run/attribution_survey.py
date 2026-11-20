"""Run the entity-attribution cells through the standard runner and count them.

``python -m abiforge.run.attribution_survey OUT_DIR`` runs every
(operator, seed) of the attribution family (``operators/attribution.py``)
plus ``func_remove`` on ``c_core`` and ``cxx_classes``, for every installed
build toolchain at binary, DWARF, headers and stripped-headers evidence,
through ``run.cells.run_cell`` (execution oracle on). It writes one JSON row
per cell to ``OUT_DIR/cells.json`` and prints the metrics with numerator and
denominator. Synthetic-corpus rates: never a real-release FP rate.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from abiforge.model.config import default_config
from abiforge.model.expect import MISMATCH_CLASSES
from abiforge.model.seeds import SEED_BY_NAME
from abiforge.operators import OP_BY_NAME
from abiforge.run.cells import CellContext, run_cell
from abiforge.run.toolchains import build_toolchains

CELLS = (
    ("func_remove", "c_core", "breaking-control"),
    ("func_remove", "cxx_classes", "breaking-control"),
    ("public_record_grew", "c_byval", "breaking-control"),
    ("private_record_grew", "c_opaque", "compatible"),
    ("func_removed_and_private_record_grew", "c_opaque", "mixed"),
)
EVIDENCE = (("binary", False), ("dwarf", False), ("headers", False), ("headers", True))


def main(out: Path) -> int:
    out.mkdir(parents=True, exist_ok=True)
    ctx = CellContext.at(out / "work")
    rows = []
    t0 = time.monotonic()
    for op_name, seed_name, role in CELLS:
        seed = SEED_BY_NAME[seed_name]
        for tc in build_toolchains():
            for ev, stripped in EVIDENCE:
                cfg = default_config(seed.lang, tc).with_(evidence=ev)
                if stripped:
                    cfg = cfg.with_(stripped=True)
                t = time.monotonic()
                c = run_cell(OP_BY_NAME[op_name], seed, cfg, ctx)
                rows.append(
                    {
                        "op": op_name,
                        "seed": seed_name,
                        "role": role,
                        "config": cfg.id,
                        "status": c.status,
                        "cls": c.cls,
                        "verdict": c.verdict,
                        "problems": list(c.reconciled.problems) if c.reconciled else [],
                        "findings": [f.describe() for f in c.findings],
                        "exec_binary_break": bool(
                            c.exec_truth and c.exec_truth.binary_break
                        ),
                        "exec_source_break": bool(
                            c.exec_truth and c.exec_truth.source_break
                        ),
                        "exec_available": bool(c.exec_truth and c.exec_truth.available),
                        "seconds": round(time.monotonic() - t, 2),
                    }
                )
    wall = time.monotonic() - t0
    (out / "cells.json").write_text(json.dumps(rows, indent=1) + "\n", encoding="utf-8")
    ran = [r for r in rows if r["status"] == "ran"]

    def frac(sel, pred):
        return f"{sum(1 for r in sel if pred(r))}/{len(sel)}"

    compat = [r for r in ran if r["role"] == "compatible"]
    mixed = [r for r in ran if r["role"] == "mixed"]
    control = [r for r in ran if r["role"] == "breaking-control"]
    print(
        f"scenarios={len(CELLS)} configs/scenario={len(build_toolchains()) * len(EVIDENCE)}"
    )
    print(
        f"cells={len(rows)} ran={len(ran)} skipped={len(rows) - len(ran)} wall={wall:.1f}s"
    )
    print(
        "compatible pairs blocked by the scanner:",
        frac(compat, lambda r: r["verdict"] in ("API_BREAK", "BREAKING")),
        " of which decided false-positive:",
        frac(compat, lambda r: r["cls"] == "false-positive"),
    )
    print(
        "mixed pairs with >=1 confirmed false claim:",
        frac(mixed, lambda r: any(p.startswith("false claim") for p in r["problems"])),
    )
    print(
        "required breaks detected on breaking controls:",
        frac(
            control,
            lambda r: r["cls"] not in MISMATCH_CLASSES and r["verdict"] == "BREAKING",
        ),
        " (unobservable at that evidence:",
        frac(control, lambda r: r["cls"] == "unobservable"),
        ")",
    )
    print(
        "unknown (evidence-limited):",
        frac(ran, lambda r: r["cls"] == "evidence-limited"),
    )
    print("mismatches:", frac(ran, lambda r: r["cls"] in MISMATCH_CLASSES))
    for r in ran:
        print(
            f"  {r['op']:38} {r['config']:34} {r['cls']:18} {r['verdict']}"
            + (f"  {r['problems']}" if r["problems"] else "")
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1])))
