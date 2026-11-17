#!/usr/bin/env python3
"""The closed return-type fragment: generator, execution oracle, abicheck run.

Fragment (formal/RET_FRAGMENT.md): change the return type of a C function with
0..2 parameters of type ``int``, ``long``, ``double`` or ``void *``, among
``void``, those four, and three structs (``s2l``: two longs, returned in
``rax:rdx``; ``s2d``: two doubles, ``xmm0:xmm1``; ``s3l``: three longs,
returned through a hidden pointer). x86-64 System V. The case space, the
report contract, the calling-convention model and the class key come from Lean
(``formal/lean/AbiLean/RetFragment.lean``, exported to
``formal/lean/ret_fragment_cases.json``). This module holds:

* ``generate()`` / ``decode()`` -- the C generator with its own enumeration
  order, and its inverse (checked by ``tests/test_formal_ret_fragment.py``);
* ``check_batch()`` -- per compiler:
  - the **execution oracle**: a caller compiled against the OLD header calls
    the NEW library, each call in its own forked child. The new function
    records its arguments in shared memory, then returns a marker value of
    its type; the caller prints what it received as the OLD type. When the
    caller passes no INTEGER argument, it first points ``rdi`` at a sentinel
    buffer, so that a new function writing a result through a hidden pointer
    the caller never passed is seen (instead of corrupting a random address).
    A case's arguments are intact iff the new function recorded every marker;
    its result is intact iff the child did not crash, nothing was written to
    the sentinel, and an old non-``void`` caller received exactly the value
    the new function returned. Both are compared with Lean's ``argsIntact``
    and ``callCompatible`` on every case.
  - ``abicheck compare`` (with headers, ``-g``): findings and verdict per case
    against the report contract.

Usage::

    ret_fragment.py OUT_DIR [--reduced] [--compilers gcc,clang]

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
EXPORT = HERE.parent / "lean" / "ret_fragment_cases.json"

PARAM_TYPES = ("int", "long", "double", "void *")
RET_TYPES = (
    "void",
    "int",
    "long",
    "double",
    "void *",
    "struct s2l",
    "struct s2d",
    "struct s3l",
)
MAX_PARAMS = 2
STRUCTS = (
    "struct s2l { long a, b; };",
    "struct s2d { double a, b; };",
    "struct s3l { long a, b, c; };",
)

Case = tuple[tuple[str, ...], str, str]


@dataclass(frozen=True)
class Program:
    """A generated program pair: the old and new prototype, without the name
    (``long (int p0, void *p1)``)."""

    old: str
    new: str


def _decl(j: int, ty: str) -> str:
    return f"{ty}p{j}" if ty.endswith("*") else f"{ty} p{j}"


def _params(params: tuple[str, ...]) -> str:
    return ", ".join(_decl(j, t) for j, t in enumerate(params)) or "void"


def render(params: tuple[str, ...], old: str, new: str) -> Program:
    """The generator's program pair for one case."""
    p = _params(params)
    return Program(f"{old} ({p})", f"{new} ({p})")


def generate() -> list[Program]:
    """Every program pair of the fragment, in the generator's own order (new
    return type, old return type, length descending, then the parameter
    types) -- deliberately not Lean's order."""
    out = []
    for new in RET_TYPES:
        for old in RET_TYPES:
            if old == new:
                continue
            for n in range(MAX_PARAMS, -1, -1):
                for params in itertools.product(PARAM_TYPES, repeat=n):
                    out.append(render(params, old, new))
    return out


def fnv1a64(programs: list[Program]) -> int:
    """FNV-1a-64 over ``old\\n new\\n`` of each pair, in order."""
    h = 14695981039346656037
    for p in programs:
        for b in (p.old + "\n" + p.new + "\n").encode():
            h = ((h ^ b) * 1099511628211) % 2**64
    return h


_RET = "|".join(re.escape(t) for t in RET_TYPES)
_PROTO = re.compile(rf"^({_RET}) \((.*)\)$")
_DECL = re.compile(r"^(int|long|double|void \*)\s?p(\d+)$")


def _parse(proto: str) -> tuple[str, tuple[str, ...]] | None:
    m = _PROTO.match(proto)
    if not m:
        return None
    ret, body = m.groups()
    params: list[str] = []
    if body != "void":
        for j, part in enumerate(body.split(", ")):
            d = _DECL.match(part)
            if not d or int(d.group(2)) != j:
                return None
            params.append(d.group(1))
    return ret, tuple(params)


def decode(p: Program) -> Case | None:
    """Parse a program pair back to ``(params, old, new)``, or None unless it
    is exactly what the generator emits for some case of the space."""
    old, new = _parse(p.old), _parse(p.new)
    if old is None or new is None or old[1] != new[1] or old[0] == new[0]:
        return None
    if len(old[1]) > MAX_PARAMS:
        return None
    case = (old[1], old[0], new[0])
    return case if render(*case) == p else None


def load_export() -> dict:
    """The Lean export, each compact row expanded to a dict and rendered."""
    data = json.loads(EXPORT.read_text(encoding="utf-8"))
    reps = {c["rep"] for c in data["classes"]}
    rows = []
    for i, (params, old, new, call, args, cls) in enumerate(data["rows"]):
        p = render(tuple(params), old, new)
        rows.append(
            {
                "id": i,
                "params": params,
                "old": old,
                "new": new,
                "call": call,
                "args": args,
                "class": cls,
                "rep": i in reps,
                "old_proto": p.old,
                "new_proto": p.new,
            }
        )
    data["rows"] = rows
    return data


# ----------------------------------------------------------- execution oracle

#: Marker value the old caller passes for parameter j (as in the
#: function-signature fragment): a negative int, a long and a pointer outside
#: 32 bits, a double.
ARG = {
    "int": lambda j: (f"{-5 - j}", -5 - j),
    "long": lambda j: (f"{0x100000000 * (j + 1) + 7}L", 0x100000000 * (j + 1) + 7),
    "double": lambda j: (f"{1.5 + j}", 1.5 + j),
    "void *": lambda j: (f"(void *){0x123456789A00 + j}UL", 0x123456789A00 + j),
}

#: The value the new function returns, by return type: its C expression and
#: the fields a reader of that type sees. An ``int`` marker is negative so a
#: 64-bit reader sees the zero-extended upper half; integer markers are outside
#: 32 bits so a 32-bit reader sees them truncated.
RET = {
    "int": ("-7", (-7,)),
    "long": ("0x500000003L", (0x500000003,)),
    "void *": ("(void *)0x500000003UL", (0x500000003,)),
    "double": ("2.25", (2.25,)),
    "struct s2l": (
        "(struct s2l){0x700000001L, 0x700000002L}",
        (0x700000001, 0x700000002),
    ),
    "struct s2d": ("(struct s2d){3.5, 4.75}", (3.5, 4.75)),
    "struct s3l": (
        "(struct s3l){0x900000001L, 0x900000002L, 0x900000003L}",
        (0x900000001, 0x900000002, 0x900000003),
    ),
}

#: How the old caller prints a received value: format and field expressions.
SHOW = {
    "void": ("", ()),
    "int": ("%d", ("r",)),
    "long": ("%ld", ("r",)),
    "void *": ("%lu", ("(unsigned long)r",)),
    "double": ("%.17g", ("r",)),
    "struct s2l": ("%ld %ld", ("r.a", "r.b")),
    "struct s2d": ("%.17g %.17g", ("r.a", "r.b")),
    "struct s3l": ("%ld %ld %ld", ("r.a", "r.b", "r.c")),
}


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)  # noqa: S603


def _proto(name: str, ret: str, params: list[str]) -> str:
    return f"{ret} {name}({_params(tuple(params))})"


def _write_sources(out: Path, rows: list[dict]) -> None:
    # The recording pointers and the structs are declared on both sides, so
    # they are an unchanged public surface, not a finding.
    common = [
        "/* generated by formal/experiments/ret_fragment.py */",
        *STRUCTS,
        "extern long long *rec; extern double *recd;",
    ]
    old_h, new_h = list(common), list(common)
    old_c = ['#include "old.h"', "long long *rec; double *recd;"]
    new_c = ['#include "new.h"', "long long *rec; double *recd;"]
    main = [
        "#include <stdio.h>",
        "#include <unistd.h>",
        "#include <sys/mman.h>",
        "#include <sys/wait.h>",
        '#include "old.h"',
        f"enum {{ N = {len(rows)} }};",
        "int main(void){",
        "rec = mmap(0, N * 4 * sizeof *rec, PROT_READ | PROT_WRITE, MAP_SHARED | MAP_ANONYMOUS, -1, 0);",
        "recd = mmap(0, N * 4 * sizeof *recd, PROT_READ | PROT_WRITE, MAP_SHARED | MAP_ANONYMOUS, -1, 0);",
        "if (rec == MAP_FAILED || recd == MAP_FAILED) return 2;",
    ]
    for slot, r in enumerate(rows):
        i, params, old, new = r["id"], r["params"], r["old"], r["new"]
        old_h.append(_proto(f"f{i}", old, params) + ";")
        new_h.append(_proto(f"f{i}", new, params) + ";")
        # The old side returns the same marker expressions as the new side, so
        # both libraries import the same symbols (a zero-initialised struct
        # makes clang call memset).
        body = "return;" if old == "void" else f"return {RET[old][0]};"
        old_c.append(_proto(f"f{i}", old, params) + f" {{ {body} }}")
        recs = []
        for k, ty in enumerate(params):
            if ty == "double":
                recs.append(f"recd[{slot * 4 + k}] = p{k};")
            elif ty == "void *":
                recs.append(f"rec[{slot * 4 + k}] = (long long)(unsigned long)p{k};")
            else:
                recs.append(f"rec[{slot * 4 + k}] = (long long)p{k};")
        ret = "return;" if new == "void" else f"return {RET[new][0]};"
        new_c.append(_proto(f"f{i}", new, params) + f" {{ {' '.join(recs)} {ret} }}")
        args = ", ".join(ARG[ty](j)[0] for j, ty in enumerate(params))
        # With no INTEGER argument and no sret of its own, the caller leaves
        # rdi alone: point it at a sentinel so a stray sret write is visible.
        guard = all(t == "double" for t in params) and old != "struct s3l"
        set_rdi = (
            '__asm__ volatile("movq %0, %%rdi" :: "r"(s) : "rdi");' if guard else ""
        )
        fmt, fields = SHOW[old]
        call = f"f{i}({args});" if old == "void" else f"{old} r = f{i}({args});"
        shown = "".join(f", {f}" for f in fields)
        main.append(
            "{ pid_t pid = fork(); if (pid == 0) {"
            " long s[3] = {0x5a5a5a5a5a5aL, 0x5a5a5a5a5a5aL, 0x5a5a5a5a5a5aL};"
            f" {set_rdi} {call}"
            " int hit = s[0] != 0x5a5a5a5a5a5aL || s[1] != 0x5a5a5a5a5a5aL"
            " || s[2] != 0x5a5a5a5a5a5aL;"
            f' printf("R {i} %d {fmt}\\n", hit{shown}); fflush(stdout); _exit(0); }}'
            " int st = 0; waitpid(pid, &st, 0);"
            f' printf("A {i} %d %lld %lld %.17g %.17g\\n", WIFSIGNALED(st) ? 1 : 0,'
            f" rec[{slot * 4}], rec[{slot * 4 + 1}], recd[{slot * 4}], recd[{slot * 4 + 1}]); fflush(stdout); }}"
        )
    main.append("return 0;}")
    for name, text in (
        ("old.h", old_h),
        ("new.h", new_h),
        ("old.c", old_c),
        ("new.c", new_c),
        ("main.c", main),
    ):
        (out / name).write_text("\n".join(text) + "\n")


def _num(s: str) -> int | float:
    return float(s) if any(ch in s for ch in ".en") else int(s)


def observed(row: dict, r_line: str | None, a_line: str) -> tuple[bool, bool]:
    """``(arguments intact, call compatible)`` as observed for one case."""
    a = a_line.split()[2:]
    crashed = a[0] == "1"
    got_int, got_dbl = (int(a[1]), int(a[2])), (float(a[3]), float(a[4]))
    args_ok = True
    for j, ty in enumerate(row["params"]):
        got = got_dbl[j] if ty == "double" else got_int[j]
        if got != ARG[ty](j)[1]:
            args_ok = False
    if crashed or r_line is None:
        return args_ok, False
    r = r_line.split()[2:]
    hit, received = r[0] == "1", tuple(_num(x) for x in r[1:])
    if hit:
        result_ok = False
    elif row["old"] == "void":
        result_ok = True
    elif row["new"] == "void":
        result_ok = False  # nothing was returned: whatever the caller reads is stale
    else:
        result_ok = received == RET[row["new"]][1]
    return args_ok, args_ok and result_ok


def _build(out: Path, cc: str) -> tuple[dict[int, str], dict[int, str]]:
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
            "-std=gnu11",
            "-O0",
            "-I",
            str(out),
            str(out / "main.c"),
            str(out / f"libnew_{cc}.so"),
            f"-Wl,-rpath,{out}",
            "-o",
            str(exe),
        ],
        check=True,
    )
    lines = _run([str(exe)], check=True).stdout.splitlines()
    rs = {int(ln.split()[1]): ln for ln in lines if ln.startswith("R ")}
    as_ = {int(ln.split()[1]): ln for ln in lines if ln.startswith("A ")}
    return rs, as_


_SUBJ = re.compile(r"^f(\d+)$")


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
    """A case's finding kinds and its verdict."""
    sev = {ch["severity"] for ch in changes}
    verdict = (
        "no_change"
        if not changes
        else "breaking"
        if "breaking" in sev
        else ",".join(sorted(sev))
    )
    return sorted(ch["kind"] for ch in changes), verdict


def check_batch(
    out: Path, rows: list[dict], classes: list[dict], compilers: tuple[str, ...]
) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    _write_sources(out, rows)
    res: dict = {"cases": len(rows), "compilers": {}}
    for cc in compilers:
        t0 = time.perf_counter()
        rs, as_ = _build(out, cc)
        t1 = time.perf_counter()
        by = _compare(out, cc)
        t2 = time.perf_counter()
        call_bad, abi_bad = [], []
        for r in rows:
            got = observed(r, rs.get(r["id"]), as_[r["id"]])
            if got != (r["args"], r["call"]):
                call_bad.append((r["id"], got, (r["args"], r["call"])))
            cls = classes[r["class"]]
            got_r = observed_report(by.get(r["id"], []))
            want = (sorted(cls["findings"]), cls["verdict"])
            if got_r != want:
                abi_bad.append((r["id"], got_r, want))
        res["compilers"][cc] = {
            "call_agree": len(rows) - len(call_bad),
            "call_disagree": len(call_bad),
            "call_compatible": sum(r["call"] for r in rows),
            "crashed": sum(as_[r["id"]].split()[2] == "1" for r in rows),
            "sentinel_written": sum(
                r["id"] in rs and rs[r["id"]].split()[2] == "1" for r in rows
            ),
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
