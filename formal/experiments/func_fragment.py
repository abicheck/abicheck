#!/usr/bin/env python3
"""The closed function-signature fragment: generator, execution oracle, abicheck run.

Fragment (formal/FUNC_FRAGMENT.md): one edit -- insert, remove or retype one
parameter -- to a C function with 1..3 parameters of type ``int``, ``long``,
``double`` or ``void *``, x86-64 System V. The case space, the report contract,
the calling-convention model and the class key come from Lean
(``formal/lean/AbiLean/FuncFragment.lean``, exported to
``formal/lean/func_fragment_cases.json``). This module holds:

* ``generate()`` / ``decode()`` -- the C generator with its own enumeration
  order, and its inverse (checked by ``tests/test_formal_func_fragment.py``);
* ``run()`` -- per compiler:
  - the **execution oracle**: a caller compiled against the OLD header calls
    the NEW library with distinct marker values; the new function records
    what it received. A case is call-compatible iff every old parameter the
    new function still has received exactly the caller's value and nothing
    was inserted. Compared with Lean's ``callCompatible`` on every case.
  - ``abicheck compare`` (with headers, ``-g``): findings and verdict per case
    against the report contract.

Usage::

    func_fragment.py OUT_DIR [--reduced] [--compilers gcc,clang]

writes OUT_DIR/summary.json; exits 1 on any disagreement. Needs gcc/clang,
castxml and abicheck on PATH, x86-64 Linux.
"""

from __future__ import annotations

import argparse
import itertools
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXPORT = HERE.parent / "lean" / "func_fragment_cases.json"

TYPES = ("int", "long", "double", "void *")
MAX_PARAMS = 3


@dataclass(frozen=True)
class Program:
    """A generated program pair: the old and new C parameter lists."""

    old_body: str
    new_body: str


Decl = tuple[str, str]  # (name, type)


def _decl(name: str, ty: str) -> str:
    return f"{ty}{name}" if ty.endswith("*") else f"{ty} {name}"


def _body(decls: list[Decl]) -> str:
    return ", ".join(_decl(n, t) for n, t in decls) or "void"


def _edit(base: tuple[str, ...], op: str, pos: int, ty: str) -> list[Decl]:
    old = [(f"p{j}", t) for j, t in enumerate(base)]
    if op == "ins":
        return old[:pos] + [("ins", ty)] + old[pos:]
    if op == "rem":
        return old[:pos] + old[pos + 1 :]
    new = list(old)
    new[pos] = (f"p{pos}", ty)
    return new


def render(base: tuple[str, ...], op: str, pos: int, ty: str) -> Program:
    """The generator's program pair for one case."""
    return Program(
        _body([(f"p{j}", t) for j, t in enumerate(base)]),
        _body(_edit(base, op, pos, ty)),
    )


def generate() -> list[Program]:
    """Every program pair of the fragment, in the generator's own order
    (edit kind set/rem/ins, length descending, position descending, type,
    then the old types) -- deliberately not Lean's order."""
    out = []
    for op in ("set", "rem", "ins"):
        for n in range(MAX_PARAMS, 0, -1):
            for pos in range(n if op == "ins" else n - 1, -1, -1):
                for ty in TYPES if op != "rem" else ("",):
                    for base in itertools.product(TYPES, repeat=n):
                        if op == "set" and base[pos] == ty:
                            continue
                        out.append(render(base, op, pos, ty))
    return out


def fnv1a64(programs: list[Program]) -> int:
    """FNV-1a-64 over ``old_body\\n new_body\\n`` of each pair, in order."""
    h = 14695981039346656037
    for p in programs:
        for b in (p.old_body + "\n" + p.new_body + "\n").encode():
            h = ((h ^ b) * 1099511628211) % 2**64
    return h


_DECL = re.compile(r"^(int|long|double|void \*)\s?(p\d+|ins)$")


def _parse(body: str) -> list[Decl] | None:
    if body == "void":
        return []
    out = []
    for part in body.split(", "):
        m = _DECL.match(part)
        if not m:
            return None
        out.append((m.group(2), m.group(1)))
    return out if _body(out) == body else None


def decode(p: Program) -> tuple[tuple[str, ...], str, int, str] | None:
    """Parse a program pair back to ``(base, op, pos, type)``, or None unless
    it is exactly what the generator emits for some case of the space."""
    old, new = _parse(p.old_body), _parse(p.new_body)
    if old is None or new is None or not 1 <= len(old) <= MAX_PARAMS:
        return None
    if [n for n, _ in old] != [f"p{j}" for j in range(len(old))]:
        return None
    base = tuple(t for _, t in old)
    ins = [k for k, (n, _) in enumerate(new) if n == "ins"]
    if len(ins) > 1:
        return None
    if ins:
        case = (base, "ins", ins[0], new[ins[0]][1])
    elif len(new) == len(old) - 1:
        k = next(j for j in range(len(old)) if j >= len(new) or new[j] != old[j])
        case = (base, "rem", k, "")
    elif len(new) == len(old):
        diff = [j for j in range(len(old)) if new[j] != old[j]]
        if len(diff) != 1:
            return None
        case = (base, "set", diff[0], new[diff[0]][1])
    else:
        return None
    return case if render(*case) == p else None


def load_export() -> dict:
    """The Lean export, each compact row expanded to a dict and rendered."""
    data = json.loads(EXPORT.read_text(encoding="utf-8"))
    reps = {c["rep"] for c in data["classes"]}
    rows = []
    for i, (base, op, pos, ty, call, cls) in enumerate(data["rows"]):
        p = render(tuple(base), op, pos, ty)
        rows.append(
            {
                "id": i,
                "base": base,
                "op": op,
                "pos": pos,
                "type": ty,
                "call": call,
                "class": cls,
                "rep": i in reps,
                "old_body": p.old_body,
                "new_body": p.new_body,
            }
        )
    data["rows"] = rows
    return data


# ----------------------------------------------------------- execution oracle

#: Marker values the old caller passes for old parameter j: an int that is
#: negative (so a zero-extending 32-bit write is visible to a 64-bit read), a
#: long and a pointer outside 32 bits, a double.
MARK = {
    "int": lambda j: (f"{-5 - j}", -5 - j),
    "long": lambda j: (f"{0x100000000 * (j + 1) + 7}L", 0x100000000 * (j + 1) + 7),
    "double": lambda j: (f"{1.5 + j}", 1.5 + j),
    "void *": lambda j: (f"(void *){0x123456789A00 + j}UL", 0x123456789A00 + j),
}


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)  # noqa: S603


def _decls(body: str) -> list[Decl]:
    d = _parse(body)
    assert d is not None, body
    return d


def _write_sources(out: Path, rows: list[dict]) -> None:
    n = len(rows)
    # The recording buffers are declared and defined on both sides, so they
    # are an unchanged public surface, not a finding.
    buf = f"extern long long rec[{n}][4]; extern double recd[{n}][4];"
    old_h = ["/* generated by formal/experiments/func_fragment.py */", buf]
    new_h = ["/* generated by formal/experiments/func_fragment.py */", buf]
    old_c = ['#include "old.h"', f"long long rec[{n}][4]; double recd[{n}][4];"]
    new_c = ['#include "new.h"', f"long long rec[{n}][4]; double recd[{n}][4];"]
    main = [
        "#include <stdio.h>",
        '#include "old.h"',
        "extern long long rec[][4]; extern double recd[][4];",
        "int main(void){",
    ]
    for slot, r in enumerate(rows):
        i, old, new = r["id"], _decls(r["old_body"]), _decls(r["new_body"])
        old_h.append(f"int f{i}({r['old_body']});")
        new_h.append(f"int f{i}({r['new_body']});")
        old_c.append(f"int f{i}({r['old_body']}) {{ return 0; }}")
        rec = []
        for k, (name, ty) in enumerate(new):
            if ty == "double":
                rec.append(f"recd[{slot}][{k}] = {name};")
            elif ty == "void *":
                rec.append(f"rec[{slot}][{k}] = (long long)(unsigned long){name};")
            else:
                rec.append(f"rec[{slot}][{k}] = (long long){name};")
        new_c.append(f"int f{i}({r['new_body']}) {{ {' '.join(rec)} return 0; }}")
        args = ", ".join(MARK[ty](j)[0] for j, (_, ty) in enumerate(old))
        fmt = " ".join("%lld %.17g" for _ in new)
        vals = "".join(
            f", rec[{slot}][{k}], recd[{slot}][{k}]" for k in range(len(new))
        )
        main.append(f'f{i}({args}); printf("{i} {fmt}\\n"{vals});')
    main.append("return 0;}")
    for name, text in (
        ("old.h", old_h),
        ("new.h", new_h),
        ("old.c", old_c),
        ("new.c", new_c),
        ("main.c", main),
    ):
        (out / name).write_text("\n".join(text) + "\n")


def observed_call(row: dict, line: str) -> bool:
    """Whether the new function received every surviving old argument intact."""
    old, new = _decls(row["old_body"]), _decls(row["new_body"])
    if any(n == "ins" for n, _ in new):
        return False
    f = line.split()[1:]
    for k, (name, ty) in enumerate(new):
        j = int(name[1:])
        old_ty = old[j][1]
        want = MARK[old_ty](j)[1]
        if (ty == "double") != (old_ty == "double"):
            return False
        got = float(f[2 * k + 1]) if ty == "double" else int(f[2 * k])
        if got != want:
            return False
    return True


def _build(out: Path, cc: str) -> dict[int, str]:
    common = ["-std=c11", "-O0", "-I", str(out)]
    for side in ("old", "new"):
        _run(
            [
                cc,
                *common,
                "-g",
                "-shared",
                "-fPIC",
                str(out / f"{side}.c"),
                "-o",
                str(out / f"lib{side}_{cc}.so"),
            ],
            check=True,
        )
    exe = out / f"main_{cc}"
    _run(
        [
            cc,
            *common,
            str(out / "main.c"),
            str(out / f"libnew_{cc}.so"),
            f"-Wl,-rpath,{out}",
            "-o",
            str(exe),
        ],
        check=True,
    )
    lines = _run([str(exe)], check=True).stdout.splitlines()
    return {int(ln.split()[0]): ln for ln in lines}


_SUBJ = re.compile(r"^f(\d+)$")
_POS = re.compile(r"param (\d+):|param p(\d+) ")


def _compare(out: Path, cc: str) -> dict[int, list[dict]]:
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
    by: dict[int, list[dict]] = {}
    for ch in json.loads(rep.read_text(encoding="utf-8"))["changes"]:
        m = _SUBJ.match(ch["symbol"])
        by.setdefault(int(m.group(1)) if m else -1, []).append(ch)
    return by


def observed_report(changes: list[dict]) -> tuple[list[str], str]:
    """A case's findings as ``kind:position`` and its verdict."""
    fs = []
    for ch in changes:
        if ch["kind"] == "func_params_changed":
            fs.append("func_params_changed:")
            continue
        m = _POS.search(ch.get("description", ""))
        fs.append(f"{ch['kind']}:{(m.group(1) or m.group(2)) if m else '?'}")
    sev = {ch["severity"] for ch in changes}
    verdict = (
        "no_change"
        if not changes
        else "breaking"
        if "breaking" in sev
        else ",".join(sorted(sev))
    )
    return sorted(fs), verdict


def check_batch(
    out: Path, rows: list[dict], classes: list[dict], compilers: tuple[str, ...]
) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    _write_sources(out, rows)
    res: dict = {"cases": len(rows), "compilers": {}}
    for cc in compilers:
        t0 = time.perf_counter()
        lines = _build(out, cc)
        t1 = time.perf_counter()
        by = _compare(out, cc)
        t2 = time.perf_counter()
        call_bad, abi_bad = [], []
        for r in rows:
            got = observed_call(r, lines[r["id"]])
            if got != r["call"]:
                call_bad.append((r["id"], got, r["call"]))
            cls = classes[r["class"]]
            got_r = observed_report(by.get(r["id"], []))
            want = (sorted(cls["findings"]), cls["verdict"])
            if got_r != want:
                abi_bad.append((r["id"], got_r, want))
        res["compilers"][cc] = {
            "call_agree": len(rows) - len(call_bad),
            "call_disagree": len(call_bad),
            "call_compatible": sum(r["call"] for r in rows),
            "abicheck_agree": len(rows) - len(abi_bad),
            "abicheck_disagree": len(abi_bad),
            "stray_findings": len(by.get(-1, [])),
            "call_examples": call_bad[:5],
            "abicheck_examples": abi_bad[:5],
            "seconds_build_and_run": round(t1 - t0, 2),
            "seconds_abicheck": round(t2 - t1, 2),
        }
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("out", type=Path)
    ap.add_argument("--reduced", action="store_true", help="only class representatives")
    ap.add_argument("--compilers", default="gcc,clang")
    a = ap.parse_args(argv)
    data = load_export()
    rows, classes = data["rows"], data["classes"]
    if a.reduced:
        rows = [r for r in rows if r["rep"]]
    t0 = time.perf_counter()
    res = check_batch(a.out, rows, classes, tuple(a.compilers.split(",")))
    res["mode"] = "batch " + ("reduced" if a.reduced else "full")
    res["wall_seconds"] = round(time.perf_counter() - t0, 2)
    (a.out / "summary.json").write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))
    failed = any(
        c["call_disagree"] or c["abicheck_disagree"] or c["stray_findings"]
        for c in res["compilers"].values()
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
