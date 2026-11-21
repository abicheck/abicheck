"""Bounded campaign over the union and bit-field operators, with a receipt.

``python -m abiforge.run.fragment_campaign OUT [--family gcc,clang]
[--pack none,1] [--opt O0,O2] [--evidence dwarf,headers.stripped,headers]``
runs every ``operators/fragments`` operator on the ``c_layout`` seed over the
cross of the selected axes (C17) through the normal cell path
(``run.cells.run_cell``: build, checker, execution oracle, exact
assertions), and writes ``OUT/receipt.json`` and ``OUT/cells.csv``.

The receipt keeps the counts apart, never as a percentage of a language:

- ``generated``: configurations x operators enumerated;
- ``unsupported``: cells the layout model or the host does not cover
  (not applicable / unavailable / infeasible), each with its reason;
- ``built_and_compared``: cells that built both sides and ran the checker;
- ``obligations``: exact rows checked (``judged``) and rows left
  ``unknown`` because the configuration's evidence cannot establish them;
- ``classes``: the reconciled class of every compared cell; ``failures``
  lists the mismatching cells with their problems and the known-gap entry
  that records them, if any.

Routine CI runs only ``tiers.FRAGMENTS_PR``; this campaign is the extended
sweep (the nightly/weekly tiers also draw these operators).
"""

from __future__ import annotations

import argparse
import csv
import json
import platform
import subprocess
import sys
from collections import Counter
from pathlib import Path

from abiforge.known import known_mismatch
from abiforge.model.config import Flags, default_config
from abiforge.model.expect import MISMATCH_CLASSES, issue_keys
from abiforge.model.seeds import SEED_BY_NAME
from abiforge.operators.fragments import FRAGMENT_OPERATORS
from abiforge.run.cells import CellContext, run_cell
from abiforge.run.tiers import evidence_fields
from abiforge.run.toolchains import newest


def _version(cc: str) -> str:
    try:
        out = subprocess.run(
            [cc, "--version"], capture_output=True, text=True, check=True
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        return f"unavailable: {exc}"
    return out.stdout.splitlines()[0]


def _revision() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def run(
    out: Path,
    families: list[str],
    packs: list[int | None],
    opts: list[str],
    evidences: list[str],
) -> dict[str, object]:
    out.mkdir(parents=True, exist_ok=True)
    ctx = CellContext.at(out / "work")
    seed = SEED_BY_NAME["c_layout"]
    rows: list[dict[str, object]] = []
    toolchains: dict[str, str] = {}
    missing: list[str] = []
    for family in families:
        tc = newest(family)
        if tc is None:
            missing.append(family)
            continue
        toolchains[tc.id] = _version(tc.cc)
        for pack in packs:
            for opt in opts:
                for ev in evidences:
                    evidence, stripped = evidence_fields(ev)
                    cfg = default_config("c", tc).with_(
                        flags=Flags(opt=opt, pack=pack),
                        evidence=evidence,
                        stripped=stripped,
                    )
                    for op in FRAGMENT_OPERATORS:
                        r = run_cell(op, seed, cfg, ctx)
                        rec = r.reconciled
                        exp = r.expectation
                        judged = sum(len(a.rows) for a in exp.assertions) if exp else 0
                        gap = ""
                        if rec is not None and rec.cls in MISMATCH_CLASSES:
                            k = known_mismatch(op.name, seed.name, cfg)
                            # A recorded gap covers only its exact signature:
                            # class, issues and verdict. A changed failure in
                            # the same cell stays unrecorded.
                            if k is not None and k.matches(
                                rec.cls, issue_keys(rec.problems), r.verdict
                            ):
                                gap = k.gap
                        rows.append(
                            {
                                "op": op.name,
                                "config": cfg.id,
                                "family": family,
                                "pack": pack,
                                "opt": opt,
                                "evidence": ev,
                                "status": r.status,
                                "reason": r.reason,
                                "verdict": r.verdict,
                                "cls": rec.cls if rec else "",
                                "problems": list(rec.problems) if rec else [],
                                "undecided": exp.undecided if exp else "",
                                "judged_rows": judged,
                                "unknown": bool(exp and exp.undecided),
                                "known_gap": gap,
                            }
                        )
    ran = [r for r in rows if r["status"] == "ran"]
    receipt: dict[str, object] = {
        "command": " ".join(
            ["python -m abiforge.run.fragment_campaign", *sys.argv[1:]]
        ),
        "revision": _revision(),
        "python": platform.python_version(),
        "host": f"{platform.system()} {platform.machine()}",
        "toolchains": toolchains,
        "families_unavailable": missing,
        "axes": {"family": families, "pack": packs, "opt": opts, "evidence": evidences},
        "operators": len(FRAGMENT_OPERATORS),
        "generated": len(rows),
        "unsupported": dict(
            Counter(str(r["status"]) for r in rows if r["status"] != "ran")
        ),
        "built_and_compared": len(ran),
        "obligations": {
            "judged_rows": sum(int(r["judged_rows"]) for r in ran),  # type: ignore[call-overload]
            "cells_with_unknown_rows": sum(1 for r in ran if r["unknown"]),
        },
        "classes": dict(Counter(str(r["cls"]) for r in ran)),
        "failures": [
            {k: r[k] for k in ("op", "config", "cls", "problems", "known_gap")}
            for r in ran
            if r["cls"] in MISMATCH_CLASSES
        ],
    }
    (out / "receipt.json").write_text(json.dumps(receipt, indent=2, default=str))
    with (out / "cells.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]) if rows else ["op"])
        w.writeheader()
        for r in rows:
            w.writerow({**r, "problems": " | ".join(r["problems"])})  # type: ignore[arg-type]
    return receipt


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("out", type=Path)
    p.add_argument("--family", default="gcc,clang")
    p.add_argument("--pack", default="none,1")
    p.add_argument("--opt", default="O0,O2")
    p.add_argument("--evidence", default="dwarf,headers.stripped,headers")
    a = p.parse_args(argv)
    receipt = run(
        a.out,
        a.family.split(","),
        [None if x == "none" else int(x) for x in a.pack.split(",")],
        a.opt.split(","),
        a.evidence.split(","),
    )
    summary = {
        k: receipt[k]
        for k in (
            "generated",
            "unsupported",
            "built_and_compared",
            "obligations",
            "classes",
        )
    }
    print(json.dumps(summary, indent=2))
    unrecorded = [f for f in receipt["failures"] if not f["known_gap"]]  # type: ignore[union-attr,index]
    if not receipt["built_and_compared"]:
        print("no cell was built and compared: nothing was checked", file=sys.stderr)
        return 1
    return 1 if unrecorded else 0


if __name__ == "__main__":
    raise SystemExit(main())
