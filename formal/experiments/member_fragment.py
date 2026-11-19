#!/usr/bin/env python3
"""The implicit-``this`` extension of E13/E14: execution oracle.

Claim (formal/MEMBER_FRAGMENT.md): on x86-64 System V with the Itanium C++
ABI, a non-static member function ``R K::f(P...)`` is called exactly like the
free function ``R f(K *this, P...)``: ``this`` takes the first INTEGER
register, and when the result is returned in memory the hidden sret pointer
comes first and ``this`` second. That is the proved models' own placement
(``FuncFragment.locAt``, ``RetFragment.argLoc``) applied to a prepended
pointer argument; and because both sides of a member-to-member edit gain the
same leading INTEGER argument, every INTEGER index shifts by one on both
sides, so each case keeps Lean's ``callCompatible`` answer.

This module checks the claim by execution, per compiler (g++ and clang++):

* **instance -> instance**: every E13 case (2160) and every E14 case (1176)
  re-generated as members of ``struct K``. The old caller, compiled against the
  old class, calls ``k.fN(...)`` on the new library. The case is observed call
  compatible iff the new function received ``this == &k`` and every surviving
  argument, and (E14) the old caller received the new result intact, nothing
  wrote through a pointer the caller did not pass (``k`` and a sentinel are
  checked), and the child did not crash. Compared with Lean's ``call``.
* **static <-> instance**: every parameter list of E13's types up to three
  parameters (85), unchanged, with the function turned from a static member
  into a non-static one and back (the symbol does not change: Itanium does not
  mangle ``static``). There is no Lean answer for these; the observations are
  written to ``formal/member_fragment_observed.json`` (with a SHA-256 of its
  rows) and ``tests/test_call_interface_members.py`` checks the production
  call axis against every row.

Usage::

    member_fragment.py OUT_DIR [--compilers g++,clang++] [--write-observed]

writes OUT_DIR/summary.json; exits 1 on any disagreement with Lean or between
compilers. Needs g++/clang++ on x86-64 Linux.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import itertools
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
OBSERVED = HERE.parent / "member_fragment_observed.json"


def _load(name: str):  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


FUNC = _load("func_fragment")
RET = _load("ret_fragment")

TYPES = ("int", "long", "double", "void *")
KSENT = "0x4b4b4b4b4b4bL"


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)  # noqa: S603


def _rec(slot: int, k: int, name: str, ty: str) -> str:
    if ty == "double":
        return f"recd[{slot * 4 + k}] = {name};"
    if ty == "void *":
        return f"rec[{slot * 4 + k}] = (long long)(unsigned long){name};"
    return f"rec[{slot * 4 + k}] = (long long){name};"


def _main_prologue(n: int) -> list[str]:
    return [
        "#include <stdio.h>",
        "#include <unistd.h>",
        "#include <sys/mman.h>",
        "#include <sys/wait.h>",
        '#include "old.h"',
        "K k;",
        "int main(void){",
        f"rec = (long long *)mmap(0, {n} * 4 * sizeof *rec, PROT_READ | PROT_WRITE, MAP_SHARED | MAP_ANONYMOUS, -1, 0);",
        f"recd = (double *)mmap(0, {n} * 4 * sizeof *recd, PROT_READ | PROT_WRITE, MAP_SHARED | MAP_ANONYMOUS, -1, 0);",
        "if (rec == MAP_FAILED || recd == MAP_FAILED) return 2;",
    ]


#: One case of the oracle: an id, the old and new declaration inside ``K``
#: (``static`` or not), the old caller's argument types and the new
#: function's parameters as (old index, type) -- index ``None`` for one the old
#: caller never passed -- and the old and new return types.
Case = dict


def e13_cases() -> list[Case]:
    out = []
    for r in FUNC.load_export()["rows"]:
        old = FUNC._parse(r["old_body"])
        new = FUNC._parse(r["new_body"])
        out.append(
            {
                "id": f"e13-{r['id']}",
                "old_kind": "instance",
                "new_kind": "instance",
                "old": [t for _, t in old],
                "new": [(None if n == "ins" else int(n[1:]), t) for n, t in new],
                "old_ret": "int",
                "new_ret": "int",
                "lean_call": r["call"],
            }
        )
    return out


def e14_cases() -> list[Case]:
    out = []
    for r in RET.load_export()["rows"]:
        out.append(
            {
                "id": f"e14-{r['id']}",
                "old_kind": "instance",
                "new_kind": "instance",
                "old": list(r["params"]),
                "new": list(enumerate(r["params"])),
                "old_ret": r["old"],
                "new_ret": r["new"],
                "lean_call": r["call"],
            }
        )
    return out


def mixed_cases() -> list[Case]:
    out = []
    for kinds in (("static", "instance"), ("instance", "static")):
        for n in range(4):
            for base in itertools.product(TYPES, repeat=n):
                out.append(
                    {
                        "id": f"{kinds[0]}-{kinds[1]}-" + ",".join(base),
                        "old_kind": kinds[0],
                        "new_kind": kinds[1],
                        "old": list(base),
                        "new": list(enumerate(base)),
                        "old_ret": "int",
                        "new_ret": "int",
                        "lean_call": None,
                    }
                )
    return out


def _decl(params: list[str]) -> str:
    return ", ".join(f"{t} p{j}" for j, t in enumerate(params)) or "void"


def _new_decl(new: list[tuple[int | None, str]]) -> str:
    names = [("ins" if j is None else f"p{j}", t) for j, t in new]
    return ", ".join(f"{t} {n}" for n, t in names) or "void"


def _write_sources(out: Path, cases: list[Case]) -> None:
    common = [
        "/* generated by formal/experiments/member_fragment.py */",
        *RET.STRUCTS,
        "extern long long *rec; extern double *recd;",
    ]
    old_h, new_h = list(common), list(common)
    old_h.append("struct K { long tag[3];")
    new_h.append("struct K { long tag[3];")
    old_c = ['#include "old.h"', "long long *rec; double *recd;"]
    new_c = ['#include "new.h"', "long long *rec; double *recd;"]
    main = _main_prologue(len(cases))
    for slot, c in enumerate(cases):
        st_old = "static " if c["old_kind"] == "static" else ""
        st_new = "static " if c["new_kind"] == "static" else ""
        old_ret, new_ret = c["old_ret"], c["new_ret"]
        # One asm label for both sides: a C++ symbol mangles the parameter
        # types, and this oracle asks what a changed signature behind an
        # unchanged symbol does (the ``call`` axis), not whether it loads.
        label = f' __asm__("mf{slot}")'
        old_h.append(f"  {st_old}{old_ret} f{slot}({_decl(c['old'])}){label};")
        new_h.append(f"  {st_new}{new_ret} f{slot}({_new_decl(c['new'])}){label};")
        body = "return;" if old_ret == "void" else f"return {RET.RET[old_ret][0]};"
        old_c.append(f"{old_ret} K::f{slot}({_decl(c['old'])}) {{ {body} }}")
        recs = [
            _rec(slot, k, "ins" if j is None else f"p{j}", t)
            for k, (j, t) in enumerate(c["new"])
        ]
        if c["new_kind"] == "instance":
            recs.append(f"rec[{slot * 4 + 3}] = (long long)(unsigned long)this;")
        ret = "return;" if new_ret == "void" else f"return {RET.RET[new_ret][0]};"
        new_c.append(
            f"{new_ret} K::f{slot}({_new_decl(c['new'])}) {{ {' '.join(recs)} {ret} }}"
        )
        args = ", ".join(RET.ARG[t](j)[0] for j, t in enumerate(c["old"]))
        callee = f"K::f{slot}" if c["old_kind"] == "static" else f"k.f{slot}"
        fmt, fields = RET.SHOW[old_ret]
        call = (
            f"{callee}({args});"
            if old_ret == "void"
            else f"{old_ret} r = {callee}({args});"
        )
        shown = "".join(f", {f}" for f in fields)
        main.append(
            "{ pid_t pid = fork(); if (pid == 0) {"
            f" k.tag[0] = k.tag[1] = k.tag[2] = {KSENT};"
            " long s[3] = {0x5a5a5a5a5a5aL, 0x5a5a5a5a5a5aL, 0x5a5a5a5a5a5aL};"
            # With no INTEGER argument and no this, rdi is the caller's
            # leftover: point it at the sentinel so a stray sret write shows.
            + (
                ' __asm__ volatile("movq %0, %%rdi" :: "r"(s) : "rdi");'
                if c["old_kind"] == "static"
                and all(t == "double" for t in c["old"])
                and old_ret != "struct s3l"
                else ""
            )
            + f" {call}"
            " int hit = s[0] != 0x5a5a5a5a5a5aL || s[1] != 0x5a5a5a5a5a5aL"
            f" || s[2] != 0x5a5a5a5a5a5aL || k.tag[0] != {KSENT}"
            f" || k.tag[1] != {KSENT} || k.tag[2] != {KSENT};"
            f' printf("R {slot} %d {fmt}\\n", hit{shown}); fflush(stdout); _exit(0); }}'
            " int st = 0; waitpid(pid, &st, 0);"
            f' printf("A {slot} %d %lld %lld %lld %lld %.17g %.17g %.17g %d\\n",'
            " WIFSIGNALED(st) ? 1 : 0,"
            f" rec[{slot * 4}], rec[{slot * 4 + 1}], rec[{slot * 4 + 2}],"
            f" rec[{slot * 4 + 3}], recd[{slot * 4}], recd[{slot * 4 + 1}],"
            f" recd[{slot * 4 + 2}], rec[{slot * 4 + 3}] == (long long)(unsigned long)&k);"
            " fflush(stdout); }"
        )
    old_h.append("};")
    new_h.append("};")
    main.append("return 0;}")
    for name, text in (
        ("old.h", old_h),
        ("new.h", new_h),
        ("old.cpp", old_c),
        ("new.cpp", new_c),
        ("main.cpp", main),
    ):
        (out / name).write_text("\n".join(text) + "\n")


def _build(out: Path, cxx: str) -> dict[tuple[str, int], str]:
    common = ["-std=gnu++17", "-O0", "-w", "-I", str(out)]
    tag = cxx.replace("+", "x")
    for side in ("old", "new"):
        _run(
            [
                cxx,
                *common,
                "-shared",
                "-fPIC",
                str(out / f"{side}.cpp"),
                "-o",
                str(out / f"lib{side}_{tag}.so"),
            ],
            check=True,
        )
    exe = out / f"main_{tag}"
    _run(
        [
            cxx,
            *common,
            str(out / "main.cpp"),
            str(out / f"libnew_{tag}.so"),
            f"-Wl,-rpath,{out}",
            "-o",
            str(exe),
        ],
        check=True,
    )
    lines = _run([str(exe)], check=True).stdout.splitlines()
    return {(ln[0], int(ln.split()[1])): ln for ln in lines}


def observed(c: Case, r_line: str | None, a_line: str) -> bool:
    """Whether the old caller's call of the new function is intact."""
    a = a_line.split()[2:]
    if a[0] == "1" or r_line is None:
        return False
    ints = [int(x) for x in a[1:5]]
    dbls = [float(x) for x in a[5:8]]
    if c["new_kind"] == "instance" and a[8] != "1":
        return False  # `this` did not arrive
    for k, (j, ty) in enumerate(c["new"]):
        if j is None:
            return False  # a parameter the old caller never passed
        old_ty = c["old"][j]
        if (ty == "double") != (old_ty == "double"):
            return False
        got = dbls[k] if ty == "double" else ints[k]
        if got != RET.ARG[old_ty](j)[1]:
            return False
    r = r_line.split()[2:]
    if r[0] == "1":
        return False
    if c["old_ret"] == "void":
        return True
    if c["new_ret"] == "void":
        return False
    received = tuple(RET._num(x) for x in r[1:])
    return received == RET.RET[c["new_ret"]][1]


def check(out: Path, cases: list[Case], compilers: tuple[str, ...]) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    _write_sources(out, cases)
    res: dict = {"cases": len(cases), "compilers": {}, "observed": {}}
    for cxx in compilers:
        t0 = time.perf_counter()
        lines = _build(out, cxx)
        bad = []
        for slot, c in enumerate(cases):
            got = observed(c, lines.get(("R", slot)), lines[("A", slot)])
            res["observed"].setdefault(c["id"], {})[cxx] = got
            if c["lean_call"] is not None and got != c["lean_call"]:
                bad.append((c["id"], got, c["lean_call"]))
        res["compilers"][cxx] = {
            "lean_cases": sum(c["lean_call"] is not None for c in cases),
            "lean_disagree": len(bad),
            "examples": bad[:5],
            "seconds": round(time.perf_counter() - t0, 2),
        }
    res["compiler_disagree"] = [
        k for k, v in res["observed"].items() if len(set(v.values())) != 1
    ]
    return res


def observed_rows(cases: list[Case], res: dict) -> dict:
    rows = [
        [
            c["old_kind"],
            c["new_kind"],
            c["old"],
            next(iter(res["observed"][c["id"]].values())),
        ]
        for c in cases
        if c["lean_call"] is None
    ]
    digest = hashlib.sha256(
        json.dumps(rows, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "generated_by": "formal/experiments/member_fragment.py --write-observed",
        "compilers": sorted(res["compilers"]),
        "rows_sha256": digest,
        "rows": rows,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("out", type=Path)
    ap.add_argument("--compilers", default="g++,clang++")
    ap.add_argument("--write-observed", action="store_true")
    a = ap.parse_args(argv)
    cases = e13_cases() + e14_cases() + mixed_cases()
    t0 = time.perf_counter()
    res = check(a.out, cases, tuple(a.compilers.split(",")))
    res["wall_seconds"] = round(time.perf_counter() - t0, 2)
    summary = {k: v for k, v in res.items() if k != "observed"}
    (a.out / "summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))
    failed = res["compiler_disagree"] or any(
        c["lean_disagree"] for c in res["compilers"].values()
    )
    # Only a run on which every compiler agreed may become the committed
    # observation: the file keeps one result per row.
    if a.write_observed and not failed:
        OBSERVED.write_text(json.dumps(observed_rows(cases, res), indent=0) + "\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
