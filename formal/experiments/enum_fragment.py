#!/usr/bin/env python3
"""The closed enum fragment: generator, compiler check and abicheck run.

Fragment (formal/ENUM_FRAGMENT.md): one edit -- insert, remove or re-initialize
one enumerator -- to a C11 enum of 1..4 enumerators whose initializers are
implicit, ``= 0``, ``= 2`` or ``= -1``, LP64 SysV. The case space, the oracle
and the class key come from Lean (``formal/lean/AbiLean/EnumFragment.lean``,
exported to ``formal/lean/enum_fragment_cases.json``). This module holds:

* ``generate()`` -- the C generator. It enumerates program pairs with its own
  loops (a different order from Lean's ``space``) and never reads the Lean
  export; ``decode()`` parses a generated pair back to a case. The bijection
  is checked by ``tests/test_formal_enum_fragment.py``.
* ``run()`` -- builds every selected case with gcc and clang, compares each
  enumerator's value and the enum's size with the Lean oracle, and runs
  ``abicheck compare`` on each build, comparing findings and verdict per case
  with the oracle.

Usage::

    enum_fragment.py OUT_DIR [--reduced] [--per-case] [--compilers gcc,clang] [--slice K/N]

writes OUT_DIR/summary.json. Needs gcc/clang, castxml and abicheck on PATH.
"""

from __future__ import annotations

import argparse
import itertools
import json
import re
import shutil
import subprocess
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXPORT = HERE.parent / "lean" / "enum_fragment_cases.json"

#: Initializer codes (as in the export) and their C spelling.
INITS = ("imp", "0", "2", "-1")
SPELL = {"imp": "", "0": " = 0", "2": " = 2", "-1": " = -1"}
MAX_ENUMERATORS = 4
#: The enum's size the oracle assumes (every value is in [-1, 6]); the probe asserts it.
ENUM_SIZE = 4


@dataclass(frozen=True)
class Program:
    """A generated program pair: the old and new enumerator lists."""

    old_body: str
    new_body: str


Decl = tuple[str, str]  # (name, initializer code)


def _body(decls: list[Decl]) -> str:
    return ", ".join(n + SPELL[i] for n, i in decls)


def render(base: tuple[str, ...], op: str, pos: int, init: str) -> Program:
    """The generator's program pair for one case."""
    old = [(f"e{j}", b) for j, b in enumerate(base)]
    if op == "ins":
        new = old[:pos] + [("ins", init)] + old[pos:]
    elif op == "rem":
        new = old[:pos] + old[pos + 1 :]
    else:
        new = list(old)
        new[pos] = (f"e{pos}", init)
    return Program(_body(old), _body(new))


def generate() -> list[Program]:
    """Every program pair of the fragment, enumerated by the generator itself.

    Order: edit kind (set, rem, ins), enum length (descending), position
    (descending), initializer, then the old initializers -- deliberately not
    Lean's order, so agreement is a check rather than a copy.
    """
    out = []
    for op in ("set", "rem", "ins"):
        for n in range(MAX_ENUMERATORS, 0, -1):
            if op == "rem" and n < 2:
                continue  # C11 6.7.2.2: an enumerator list is not empty
            for pos in range(n if op == "ins" else n - 1, -1, -1):
                for init in INITS if op != "rem" else ("",):
                    for base in itertools.product(INITS, repeat=n):
                        if op == "set" and base[pos] == init:
                            continue  # not an edit
                        out.append(render(base, op, pos, init))
    return out


def fnv1a64(programs: list[Program]) -> int:
    """FNV-1a-64 over ``old_body\\n new_body\\n`` of each pair, in order (as
    ``genChecksum`` in EnumFragmentExport.lean)."""
    h = 14695981039346656037
    for p in programs:
        for b in (p.old_body + "\n" + p.new_body + "\n").encode():
            h = ((h ^ b) * 1099511628211) % 2**64
    return h


_DECL = re.compile(r"^(e\d+|ins)(?: = (0|2|-1))?$")


def _parse(body: str) -> list[Decl] | None:
    out = []
    for part in body.split(", "):
        m = _DECL.match(part)
        if not m:
            return None
        out.append((m.group(1), m.group(2) or "imp"))
    return out if _body(out) == body else None


def decode(p: Program) -> tuple[tuple[str, ...], str, int, str] | None:
    """Parse a program pair back to ``(base, op, pos, init)``, or None unless
    it is exactly what the generator emits for some case of the space."""
    old, new = _parse(p.old_body), _parse(p.new_body)
    if old is None or new is None or not 1 <= len(old) <= MAX_ENUMERATORS:
        return None
    if [n for n, _ in old] != [f"e{j}" for j in range(len(old))]:
        return None
    base = tuple(i for _, i in old)
    ins = [k for k, (n, _) in enumerate(new) if n == "ins"]
    if len(ins) == 1:
        k = ins[0]
        case = (base, "ins", k, new[k][1])
    elif ins:
        return None
    elif len(new) == len(old) - 1:
        k = next(
            (j for j in range(len(old)) if j >= len(new) or new[j] != old[j]), None
        )
        case = (base, "rem", k, "")
    elif len(new) == len(old):
        diff = [j for j in range(len(old)) if new[j] != old[j]]
        if len(diff) != 1:
            return None
        k = diff[0]
        case = (base, "set", k, new[k][1])
    else:
        return None
    if case[2] is None or render(*case) != p:
        return None
    return case


def load_export() -> dict:
    """The Lean export, with each compact row expanded to a dict and rendered."""
    data = json.loads(EXPORT.read_text(encoding="utf-8"))
    reps = {c["rep"] for c in data["classes"]}
    rows = []
    for i, (base, op, pos, init, ov, nv, cls) in enumerate(data["rows"]):
        p = render(tuple(base), op, pos, init)
        rows.append(
            {
                "id": i,
                "base": base,
                "op": op,
                "pos": pos,
                "init": init,
                "old": ov,
                "new": nv,
                "class": cls,
                "rep": i in reps,
                "old_body": p.old_body,
                "new_body": p.new_body,
            }
        )
    data["rows"] = rows
    return data


# --------------------------------------------------------------------- build


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)  # noqa: S603


def _c_body(i: int, body: str) -> str:
    """The C enumerator list: enumerators share one name space, so each is
    prefixed with its enum's tag (``E<i>_``)."""
    return ", ".join(f"E{i}_{part}" for part in body.split(", "))


def _names(body: str) -> list[str]:
    return [part.split(" ")[0] for part in body.split(", ")]


def _write_sources(out: Path, rows: list[dict]) -> None:
    for side in ("old", "new"):
        h = ["/* generated by formal/experiments/enum_fragment.py */"]
        c = [f'#include "{side}.h"']
        probe = ["#include <stdio.h>", f'#include "{side}.h"', "int main(void){"]
        for r in rows:
            i, body = r["id"], r[f"{side}_body"]
            h.append(f"enum en{i} {{ {_c_body(i, body)} }};")
            h.append(f"int use_en{i}(enum en{i} x);")
            c.append(f"int use_en{i}(enum en{i} x) {{ return (int)x; }}")
            vals = ",".join(f"(long)E{i}_{n}" for n in _names(body))
            fmt = " ".join("%ld" for _ in _names(body))
            probe.append(f'printf("{i} %d {fmt}\\n",(int)sizeof(enum en{i}),{vals});')
        probe.append("return 0;}")
        (out / f"{side}.h").write_text("\n".join(h) + "\n")
        (out / f"{side}.c").write_text("\n".join(c) + "\n")
        (out / f"probe_{side}.c").write_text("\n".join(probe) + "\n")


def _build(out: Path, cc: str) -> dict[str, dict[int, tuple[int, list[int]]]]:
    """Build both sides with ``cc``; return the compiler's (sizeof, values) per side."""
    res: dict[str, dict[int, tuple[int, list[int]]]] = {}
    for side in ("old", "new"):
        common = ["-std=c11", "-pedantic-errors", "-I", str(out)]
        _run(
            [
                cc,
                *common,
                "-g",
                "-O0",
                "-shared",
                "-fPIC",
                str(out / f"{side}.c"),
                "-o",
                str(out / f"lib{side}_{cc}.so"),
            ],
            check=True,
        )
        exe = out / f"probe_{side}_{cc}"
        _run([cc, *common, str(out / f"probe_{side}.c"), "-o", str(exe)], check=True)
        got = {}
        for line in _run([str(exe)], check=True).stdout.splitlines():
            i, size, *vals = map(int, line.split())
            got[i] = (size, vals)
        res[side] = got
    return res


_SUBJ = re.compile(r"^en(\d+)(?:::E\d+_(\w+))?$")


def _compare(out: Path, cc: str) -> tuple[int, dict[int, list[dict]]]:
    rep = out / f"compare_{cc}.json"
    rep.unlink(missing_ok=True)
    proc = _run(
        [
            "abicheck",
            "compare",
            str(out / f"libold_{cc}.so"),
            str(out / f"libnew_{cc}.so"),
            "--header",
            f"old={out / 'old.h'}",
            "--header",
            f"new={out / 'new.h'}",
            "-o",
            f"json={rep}",
        ]
    )
    if proc.returncode not in (0, 2, 4) or not rep.exists():
        sys.stderr.write(proc.stderr)
        raise SystemExit(f"abicheck compare ({cc}) failed with exit {proc.returncode}")
    by_enum: dict[int, list[dict]] = {}
    for ch in json.loads(rep.read_text(encoding="utf-8"))["changes"]:
        m = _SUBJ.match(ch["symbol"])
        key = int(m.group(1)) if m else -1  # -1: a finding on no fragment enum
        by_enum.setdefault(key, []).append(ch)
    return proc.returncode, by_enum


def observed(changes: list[dict]) -> tuple[list[str], str]:
    """A case's findings as ``kind:enumerator`` and its verdict."""
    fs = []
    for ch in changes:
        m = _SUBJ.match(ch["symbol"])
        fs.append(f"{ch['kind']}:{(m.group(2) if m else None) or ''}")
    sev = {ch["severity"] for ch in changes}
    if not changes:
        verdict = "no_change"
    elif "breaking" in sev:
        verdict = "breaking"
    elif sev <= {"compatible"}:
        verdict = "compatible"
    else:
        verdict = "other:" + ",".join(sorted(sev))
    return sorted(fs), verdict


def _expected(row: dict, side: str) -> tuple[int, list[int]]:
    return ENUM_SIZE, row[side]


def check_batch(
    out: Path, rows: list[dict], classes: list[dict], compilers: tuple[str, ...]
) -> dict:
    """All selected cases in one library pair per compiler."""
    out.mkdir(parents=True, exist_ok=True)
    _write_sources(out, rows)
    res: dict = {"cases": len(rows), "compilers": {}}
    for cc in compilers:
        t0 = time.perf_counter()
        lay = _build(out, cc)
        t1 = time.perf_counter()
        _, by_enum = _compare(out, cc)
        t2 = time.perf_counter()
        val_bad, abi_bad = [], []
        for r in rows:
            for side in ("old", "new"):
                if lay[side][r["id"]] != _expected(r, side):
                    val_bad.append((r["id"], side, lay[side][r["id"]], r[side]))
            cls = classes[r["class"]]
            got = observed(by_enum.get(r["id"], []))
            want = (sorted(cls["findings"]), cls["verdict"])
            if got != want:
                abi_bad.append((r["id"], got, want))
        res["compilers"][cc] = {
            "values_agree": 2 * len(rows) - len(val_bad),
            "values_disagree": len(val_bad),
            "abicheck_agree": len(rows) - len(abi_bad),
            "abicheck_disagree": len(abi_bad),
            "stray_findings": len(by_enum.get(-1, [])),
            "values_examples": val_bad[:5],
            "abicheck_examples": abi_bad[:5],
            "seconds_build_and_probe": round(t1 - t0, 2),
            "seconds_abicheck": round(t2 - t1, 2),
        }
    return res


def _one_case(out: Path, row: dict, cls: dict, compilers: tuple[str, ...]) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    _write_sources(out, [row])
    r = {"id": row["id"]}
    want = (sorted(cls["findings"]), cls["verdict"])
    for cc in compilers:
        lay = _build(out, cc)
        code, by_enum = _compare(out, cc)
        r[cc] = {
            "values_ok": all(
                lay[s][row["id"]] == _expected(row, s) for s in ("old", "new")
            ),
            # a finding on anything but this case's enum (its probe function
            # included) is a failure too
            "abicheck_ok": observed(by_enum.get(row["id"], [])) == want
            and set(by_enum) <= {row["id"]},
            # per-case run: the exit code is this case's own verdict
            "exit_ok": code == (4 if cls["verdict"] == "breaking" else 0),
        }
    shutil.rmtree(out)
    return r


def check_per_case(
    out: Path,
    rows: list[dict],
    classes: list[dict],
    compilers: tuple[str, ...],
    jobs: int,
) -> dict:
    """Each case as its own library pair and compare run (true per-case exit code)."""
    with ThreadPoolExecutor(max_workers=jobs) as ex:
        results = list(
            ex.map(
                lambda r: _one_case(
                    out / f"c{r['id']}", r, classes[r["class"]], compilers
                ),
                rows,
            )
        )
    summary: dict = {"cases": len(rows), "compilers": {}}
    for cc in compilers:
        summary["compilers"][cc] = dict(
            Counter(
                f"values={x[cc]['values_ok']} abicheck={x[cc]['abicheck_ok']} exit={x[cc]['exit_ok']}"
                for x in results
            )
        )
    summary["bad"] = [
        x for x in results if not all(all(x[cc].values()) for cc in compilers)
    ][:5]
    return summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("out", type=Path)
    ap.add_argument("--reduced", action="store_true", help="only class representatives")
    ap.add_argument(
        "--per-case", action="store_true", help="one build+compare per case"
    )
    ap.add_argument("--compilers", default="gcc,clang")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--slice", default="1/1", help="K/N: run only the K-th of N slices")
    a = ap.parse_args(argv)
    data = load_export()
    rows, classes = data["rows"], data["classes"]
    if a.reduced:
        rows = [r for r in rows if r["rep"]]
    k, n = map(int, a.slice.split("/"))
    if not 1 <= k <= n:
        ap.error(f"--slice {a.slice}: need 1 <= K <= N")
    rows = rows[(k - 1) * len(rows) // n : k * len(rows) // n]
    if not rows:
        # an empty shard checked nothing; it must not read as a pass
        print(f"slice {a.slice} selects no cases: nothing was checked")
        return 2
    compilers = tuple(a.compilers.split(","))
    t0 = time.perf_counter()
    if a.per_case:
        res = check_per_case(a.out, rows, classes, compilers, a.jobs)
    else:
        res = check_batch(a.out, rows, classes, compilers)
    res["slice"] = a.slice
    res["mode"] = ("per-case" if a.per_case else "batch") + (
        " reduced" if a.reduced else " full"
    )
    res["wall_seconds"] = round(time.perf_counter() - t0, 2)
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "summary.json").write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))
    if "bad" in res:
        failed = bool(res["bad"])
    else:
        failed = any(
            c["values_disagree"] or c["abicheck_disagree"] or c["stray_findings"]
            for c in res["compilers"].values()
        )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
