#!/usr/bin/env python3
"""Same-class struct returns: member layouts against execution (E14b).

Fragment (formal/RET_FRAGMENT.md, "Struct member layouts"): change the
declared members of a C struct returned by value from a function with no
parameters, among every struct of one to three members of type ``int``,
``long`` or ``double`` (natural layout, x86-64 System V) whose return
classification is one of the modelled shapes -- one or two INTEGER
eightbytes, one or two SSE eightbytes, or caller memory (over 16 bytes).
Mixed INTEGER/SSE structs are outside the fragment. A member's identity is
its name, which is its type and byte offset (``l8``: a ``long`` at byte 8),
so a member keeps its name exactly when it keeps its place and type.

Two independent answers per case:

* the **execution oracle**: a caller compiled against the OLD header calls
  the NEW library in a forked child, every call in its own frame under
  ``-fstack-protector-all``. The new function returns a distinct marker in each member; the caller prints
  every member it reads. The result is intact iff the child exited cleanly
  (the canary above the result buffer catches a new function writing past
  the old caller's buffer; AddressSanitizer does not, as gcc leaves the sret
  store uninstrumented)
  and every member the old caller reads holds the marker of the new member of
  the same name;
* **abicheck**: both libraries are dumped with their header (``-H``, no
  DWARF) through each header backend (castxml, clang), each return is
  classified by ``abicheck.model.sysv_aggregate.struct_return_of`` and the
  pair is judged by ``abicheck.compare.call_interface.assess_call_interface``.
  It must answer PRESERVED or BROKEN (never UNKNOWN) and agree with the
  oracle on every case, on gcc- and clang-built libraries.

Usage::

    struct_ret_fragment.py OUT_DIR [--compilers gcc,clang]
        [--frontends castxml,clang] [--limit N]

writes OUT_DIR/summary.json; exits 1 on any disagreement. Needs gcc/clang,
castxml and clang, x86-64 Linux.
"""

from __future__ import annotations

import argparse
import itertools
import json
import subprocess
import sys
import time
from pathlib import Path

#: Member types: C type, size and alignment in bytes, SysV class.
TYPES = {
    "i": ("int", 4, "INTEGER"),
    "l": ("long", 8, "INTEGER"),
    "d": ("double", 8, "SSE"),
}
MAX_MEMBERS = 3
#: Two four-member structs (32 bytes, MEMORY).
LARGER = ("llll", "lldd")


def layout(seq: str) -> tuple[list[tuple[str, str, int]], int]:
    """``([(name, C type, byte offset)], size in bytes)`` of a member sequence
    under natural alignment."""
    off, align, out = 0, 1, []
    for t in seq:
        ctype, size, a = TYPES[t][0], TYPES[t][1], TYPES[t][1]
        off = -(-off // a) * a
        out.append((f"{t}{off}", ctype, off))
        off += size
        align = max(align, a)
    return out, -(-off // align) * align


def shape(seq: str) -> str | None:
    """The psABI return classification of *seq*, written out independently of
    abicheck: ``memory``, ``int1``/``int2``/``sse1``/``sse2``, or ``None`` for
    a mixed struct."""
    members, size = layout(seq)
    if size > 16:
        return "memory"
    classes = []
    for eb in range(-(-size // 8)):
        cls = {TYPES[n[0]][2] for n, _c, off in members if off // 8 == eb}
        classes.append("INTEGER" if "INTEGER" in cls else "SSE")
    if len(set(classes)) != 1:
        return None
    return ("int" if classes[0] == "INTEGER" else "sse") + str(len(classes))


def structs() -> list[str]:
    """Every member sequence of the fragment, shortest first."""
    out = []
    for n in range(1, MAX_MEMBERS + 1):
        for seq in itertools.product(TYPES, repeat=n):
            if shape("".join(seq)) is not None:
                out.append("".join(seq))
    # Three members make every MEMORY struct 24 bytes; two 32-byte ones add
    # a memory result that grows or shrinks.
    return out + list(LARGER)


def space() -> list[tuple[str, str]]:
    """Every (old, new) pair of distinct structs of the fragment."""
    ss = structs()
    return [(o, n) for o in ss for n in ss if o != n]


def _marker(name: str) -> tuple[str, int | float]:
    t, off = name[0], int(name[1:])
    if t == "i":
        return f"{-100 - off}", -100 - off
    if t == "l":
        v = 0x700000000 + off
        return f"{v}L", v
    return f"{1.25 + off}", 1.25 + off


def _decl(i: int, seq: str) -> str:
    members, _ = layout(seq)
    body = " ".join(f"{c} {n};" for n, c, _o in members)
    return f"struct R{i} {{ {body} }};\nstruct R{i} f{i}(void);"


def write_sources(out: Path, cases: list[tuple[str, str]]) -> None:
    old_h = [_decl(i, o) for i, (o, _n) in enumerate(cases)]
    new_h = [_decl(i, n) for i, (_o, n) in enumerate(cases)]
    old_c, new_c = ['#include "old.h"'], ['#include "new.h"']
    main = [
        "#include <errno.h>",
        "#include <stdio.h>",
        "#include <stdlib.h>",
        "#include <unistd.h>",
        "#include <sys/wait.h>",
        '#include "old.h"',
        # A harness failure (fork/waitpid) is not an observation: exit 2.
        "static int run_case(void (*c)(void)){ fflush(stdout); pid_t pid = fork();"
        ' if (pid < 0) { perror("fork"); exit(2); }'
        " if (pid == 0) { c(); fflush(stdout); _exit(0); }"
        " int st = 0; pid_t w;"
        " do { w = waitpid(pid, &st, 0); } while (w < 0 && errno == EINTR);"
        ' if (w != pid) { perror("waitpid"); exit(2); }'
        " return WIFEXITED(st) && WEXITSTATUS(st) == 0 ? 0 : 1; }",
        "int main(void){",
    ]
    callers: list[str] = []
    for i, (old, new) in enumerate(cases):
        for src, seq in ((old_c, old), (new_c, new)):
            sets = " ".join(f"r.{n} = {_marker(n)[0]};" for n, _c, _o in layout(seq)[0])
            src.append(f"struct R{i} f{i}(void) {{ struct R{i} r; {sets} return r; }}")
        fmt, vals = [], []
        for n, c, _o in layout(old)[0]:
            fmt.append("%.17g" if c == "double" else "%ld")
            vals.append(f"(long)r.{n}" if c == "int" else f"r.{n}")
        # Each call in its own frame, so the stack protector's canary sits
        # right above the result buffer and a new function writing past it
        # aborts the child.
        callers.append(
            f"__attribute__((noinline)) static void c{i}(void) {{"
            f" struct R{i} r = f{i}();"
            f' printf("R {i} {" ".join(fmt)}\\n", {", ".join(vals)}); }}'
        )
        main.append(f'printf("A {i} %d\\n", run_case(c{i}));')
    main.append("return 0;}")
    main[-2 - len(cases) : -2 - len(cases)] = callers
    for name, text in (
        ("old.h", old_h),
        ("new.h", new_h),
        ("old.c", old_c),
        ("new.c", new_c),
        ("main.c", main),
    ):
        (out / name).write_text("\n".join(text) + "\n", encoding="utf-8")


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)  # noqa: S603


def build_and_run(out: Path, cc: str) -> tuple[dict[int, bool], dict[int, list[str]]]:
    """Per case: did the child exit cleanly, and the member values it read."""
    flags = ["-std=gnu11", "-O0", "-fstack-protector-all"]
    for side in ("old", "new"):
        _run(
            [
                cc,
                *flags,
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
            *flags,
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
    proc = _run([str(exe.resolve())])
    if proc.returncode != 0:
        raise RuntimeError(
            f"{exe.name} failed (exit {proc.returncode}): {proc.stderr.strip()}"
        )
    got: dict[int, bool] = {}
    reads: dict[int, list[str]] = {}
    for ln in proc.stdout.splitlines():
        parts = ln.split()
        if parts[0] == "R":
            reads[int(parts[1])] = parts[2:]
        elif parts[0] == "A":
            got[int(parts[1])] = parts[2] == "0"
    # Every case needs its status record: a missing one is a harness fault,
    # not a "broken" observation. A child that crashed still reports one.
    cases = (out / "main.c").read_text(encoding="utf-8").count(", run_case(c")
    missing = sorted(set(range(cases)) - set(got))
    if missing:
        raise RuntimeError(f"{exe.name}: no status record for case(s) {missing[:10]}")
    # A child that exited cleanly has made its call and printed the members
    # it read; no read record then is a harness fault, not a broken result.
    unread = sorted(i for i, clean in got.items() if clean and i not in reads)
    if unread:
        raise RuntimeError(f"{exe.name}: clean case(s) without a read {unread[:10]}")
    return got, reads


def oracle(
    cases: list[tuple[str, str]], run: tuple[dict[int, bool], dict[int, list[str]]]
) -> dict[int, bool]:
    """Per case: did the old caller read the new result intact?"""
    clean, reads = run
    out = {}
    for i, (old, new) in enumerate(cases):
        if not clean.get(i) or i not in reads:
            out[i] = False
            continue
        new_names = {n for n, _c, _o in layout(new)[0]}
        ok = True
        for (n, _c, _o), text in zip(layout(old)[0], reads[i], strict=True):
            want = _marker(n)[1]
            value = float(text) if n[0] == "d" else int(text)
            if n not in new_names or value != want:
                ok = False
        out[i] = ok
    return out


def abicheck_answers(out: Path, cc: str, frontend: str) -> dict[int, str]:
    from abicheck.compare.call_interface import (
        assess_call_interface,
        call_signature_of,
        linkage_evidence_reliable,
    )
    from abicheck.model.call_interface import SYSV_AMD64
    from abicheck.model.sysv_aggregate import struct_return_of
    from abicheck.serialization import load_snapshot

    cfg = out / f"{frontend}.yml"
    cfg.write_text(f"compile:\n  frontend: {frontend}\n  lang: c\n", encoding="utf-8")
    snaps = []
    for side in ("old", "new"):
        dump = out / f"{side}_{cc}_{frontend}.json"
        proc = _run(
            [
                sys.executable,
                "-m",
                "abicheck",
                "dump",
                str(out / f"lib{side}_{cc}.so"),
                "-H",
                str(out / f"{side}.h"),
                "--config",
                str(cfg),
                "-o",
                str(dump),
            ]
        )
        if proc.returncode:
            raise SystemExit(f"abicheck dump failed: {proc.stderr[-2000:]}")
        snaps.append(load_snapshot(str(dump)))
    old_s, new_s = snaps
    old_fns = {f.name: f for f in old_s.declarations.functions}
    answers = {}
    for f in new_s.declarations.functions:
        if not f.name.startswith("f") or f.name not in old_fns:
            continue
        o = old_fns[f.name]
        sig_o = call_signature_of(
            o,
            struct_return_of(o, old_s),
            linkage_reliable=linkage_evidence_reliable(old_s),
        )
        sig_n = call_signature_of(
            f,
            struct_return_of(f, new_s),
            linkage_reliable=linkage_evidence_reliable(new_s),
        )
        answers[int(f.name[1:])] = assess_call_interface(
            sig_o, sig_n, SYSV_AMD64
        ).status.value
    return answers


def check(
    out: Path,
    cases: list[tuple[str, str]],
    compilers: tuple[str, ...],
    frontends: tuple[str, ...],
) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    write_sources(out, cases)
    res: dict = {"cases": len(cases), "runs": {}}
    for cc in compilers:
        t0 = time.perf_counter()
        truth = oracle(cases, build_and_run(out, cc))
        res["runs"][f"{cc}/oracle"] = {
            "intact": sum(truth.values()),
            "intact_pairs": [list(cases[i]) for i, ok in sorted(truth.items()) if ok],
            "seconds": round(time.perf_counter() - t0, 2),
        }
        for fe in frontends:
            t1 = time.perf_counter()
            got = abicheck_answers(out, cc, fe)
            bad = []
            for i, (old, new) in enumerate(cases):
                want = "preserved" if truth[i] else "broken"
                if got.get(i) != want:
                    bad.append(
                        {
                            "case": i,
                            "old": old,
                            "new": new,
                            "oracle": want,
                            "abicheck": got.get(i),
                        }
                    )
            res["runs"][f"{cc}/{fe}"] = {
                "agree": len(cases) - len(bad),
                "disagree": len(bad),
                "examples": bad[:5],
                "seconds": round(time.perf_counter() - t1, 2),
            }
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("out", type=Path)
    ap.add_argument("--compilers", default="gcc,clang")
    ap.add_argument("--frontends", default="castxml,clang")
    ap.add_argument("--limit", type=int, default=0, help="first N cases only")
    a = ap.parse_args(argv)
    cases = space()
    if a.limit:
        cases = cases[: a.limit]
    res = check(
        a.out, cases, tuple(a.compilers.split(",")), tuple(a.frontends.split(","))
    )
    (a.out / "summary.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    print(json.dumps(res, indent=1))
    return 1 if any(r.get("disagree") for r in res["runs"].values()) else 0


if __name__ == "__main__":
    sys.exit(main())
