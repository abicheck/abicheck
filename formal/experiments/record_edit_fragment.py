#!/usr/bin/env python3
"""E16: the closed record-edit fragments -- remove, swap, replace.

Fragment (formal/RECORD_EDIT_FRAGMENT.md): a C struct of 1..4 fields typed
from {char, short, int, double}; remove one field (n >= 2), swap two fields
(field identity kept), or retype one field to a different type from
{char, short, int, double, unsigned int, float}. LP64 SysV, x86-64. The space,
the generator, the layout oracle and the expected findings come from Lean
(``formal/lean/AbiLean/RecordEditFragment.lean``, exported to
``formal/lean/record_edit_fragment_cases.json``). This module holds:

* ``generate()`` -- the C generator: its own loops, in its own order, never
  reading the export; ``decode()`` parses a program pair back to a case.
* ``run()`` -- builds every selected case (plus unchanged-record controls)
  with each compiler and observes, independently of one another:

  ========== ===================================================== =========================
  layer      observation                                           checked against
  ========== ===================================================== =========================
  layout     sizeof/_Alignof/offsetof probes, old and new          Lean layout
  findings   typed kind/symbol/field_name per lane                 Lean findings (per lane)
  values     typed old_value/new_value (bits, type spellings)      Lean layout
  verdict    report severity; exit code in per-case mode           contract (breaking)
  consumer   old-header consumer, old vs new provider, raw bytes   byte model + findings
  ========== ===================================================== =========================

  Lanes: ``hdr`` (no -g, public header), ``dwarf`` (-g, no header), ``both``
  (-g and header), each with its own ``abicheck compare``.

Outcome per case and lane: ``agree``, ``checker_mismatch`` (finding set
differs), ``value_mismatch`` (findings agree, a typed value does not),
``oracle_conflict`` (compiler layout differs from the model), ``missing``
(no report entry for a record that should have one). An extractor failure or
infrastructure error aborts the run with the tool's own output.

Usage::

    record_edit_fragment.py OUT_DIR [--reduced] [--per-case] [--sample N]
        [--compilers gcc,clang] [--lanes hdr,dwarf,both] [--repeat N]

writes OUT_DIR/summary.json and OUT_DIR/cases.jsonl (one line per case).
"""

from __future__ import annotations

import argparse
import itertools
import json
import platform
import random
import re
import resource
import shutil
import struct
import subprocess
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXPORT = HERE.parent / "lean" / "record_edit_fragment_cases.json"

#: Type index -> (C spelling, size = alignment on LP64 SysV, struct format).
TYPES = (
    ("char", 1, "b"),
    ("short", 2, "h"),
    ("int", 4, "i"),
    ("double", 8, "d"),
    ("unsigned int", 4, "I"),
    ("float", 4, "f"),
)
BASE = (0, 1, 2, 3)
MAX_FIELDS = 4
PREMISE = "\n".join(
    f"_Static_assert(sizeof({t}) == {n} && _Alignof({t}) == {n}, "
    f'"LP64 SysV premise: {t} is {n} bytes, {n}-aligned");'
    for t, n, _ in TYPES
)
LANES = ("hdr", "dwarf", "both")
#: Kinds a lane cannot observe, by the kind's own ``observable_at``
#: declaration: record alignment is a header fact; DWARF records it only when
#: explicit (model/change_catalog/types_3.py, _OBS_ALIGN_H).
LANE_UNOBSERVABLE = {"dwarf": {"type_alignment_changed"}}
SENTINEL = 0x5A


@dataclass(frozen=True)
class Program:
    old_body: str
    new_body: str


def _body(decls: list[tuple[int, int]]) -> str:
    return " ".join(f"{TYPES[t][0]} f{j};" for j, t in decls)


def render(base: tuple[int, ...], edit: tuple[int, int, int]) -> Program:
    """The generator's program pair: ``edit`` is ``(op, a, b)`` with op 0
    remove a, 1 swap a b, 2 retype a to type b."""
    old = list(enumerate(base))
    op, a, b = edit
    if op == 0:
        new = old[:a] + old[a + 1 :]
    elif op == 1:
        new = list(old)
        new[a], new[b] = new[b], new[a]
    else:
        new = list(old)
        new[a] = (a, b)
    return Program(_body(old), _body(new))


def generate() -> list[Program]:
    """Every program pair, enumerated by the generator itself: op descending
    (retype, swap, remove), length descending, then edit parameters, then
    the base -- deliberately not Lean's order."""
    out = []
    for n in range(MAX_FIELDS, 0, -1):
        for k in range(n):
            for t in reversed(range(len(TYPES))):
                for base in itertools.product(BASE, repeat=n):
                    if base[k] != t:
                        out.append(render(base, (2, k, t)))
    for n in range(MAX_FIELDS, 1, -1):
        for i, j in itertools.combinations(range(n), 2):
            for base in itertools.product(BASE, repeat=n):
                out.append(render(base, (1, i, j)))
    for n in range(MAX_FIELDS, 1, -1):
        for k in range(n):
            for base in itertools.product(BASE, repeat=n):
                out.append(render(base, (0, k, 0)))
    return out


_DECL = re.compile(r"(unsigned int|\w+) f(\d+);")
_TYPE_OF = {t[0]: i for i, t in enumerate(TYPES)}


def decode(p: Program) -> tuple[tuple[int, ...], tuple[int, int, int]] | None:
    """Parse a program pair back to ``(base, edit)``, or None unless it is
    exactly what the generator emits for some case of the space."""
    old = [(int(j), _TYPE_OF.get(t)) for t, j in _DECL.findall(p.old_body)]
    new = [(int(j), _TYPE_OF.get(t)) for t, j in _DECL.findall(p.new_body)]
    if any(t is None for _, t in old + new):
        return None
    if _body(old) != p.old_body or _body(new) != p.new_body:  # type: ignore[arg-type]
        return None
    n = len(old)
    if not 1 <= n <= MAX_FIELDS or [j for j, _ in old] != list(range(n)):
        return None
    base = tuple(t for _, t in old)
    if any(t not in BASE for t in base):
        return None
    if len(new) == n - 1 and n >= 2:
        gone = sorted(set(range(n)) - {j for j, _ in new})
        if len(gone) == 1 and new == [d for d in old if d[0] != gone[0]]:
            return base, (0, gone[0], 0)
        return None
    if len(new) != n:
        return None
    diff = [i for i in range(n) if new[i] != old[i]]
    if len(diff) == 1 and new[diff[0]][0] == diff[0]:
        k = diff[0]
        return base, (2, k, new[k][1])  # type: ignore[return-value]
    if len(diff) == 2:
        i, j = diff
        if new[i] == old[j] and new[j] == old[i]:
            return base, (1, i, j)
    return None


def fnv1a64(programs: list[Program]) -> int:
    """Same checksum as ``fnv1a64``/``genChecksum`` in the Lean export."""
    h = 14695981039346656037
    for p in programs:
        for b in (p.old_body + "\n" + p.new_body + "\n").encode():
            h = ((h ^ b) * 1099511628211) % 2**64
    return h


def load_export() -> dict:
    data = json.loads(EXPORT.read_text(encoding="utf-8"))
    reps = {c["rep"] for c in data["classes"]}
    rows = []
    for i, (base, edit, old, new, cls) in enumerate(data["rows"]):
        p = render(tuple(base), tuple(edit))
        rows.append(
            {
                "id": i,
                "base": base,
                "edit": edit,
                "old": old,
                "new": new,
                "class": cls,
                "rep": i in reps,
                "old_body": p.old_body,
                "new_body": p.new_body,
            }
        )
    data["rows"] = rows
    return data


def controls() -> list[dict]:
    """Unchanged records, one per base of the space: no finding expected."""
    out = []
    for n in range(1, MAX_FIELDS + 1):
        for base in itertools.product(BASE, repeat=n):
            body = _body(list(enumerate(base)))
            out.append({"id": len(out), "old_body": body, "new_body": body})
    return out


# ------------------------------------------------------------ oracle helpers


def decls(body: str) -> list[tuple[int, int]]:
    return [(int(j), _TYPE_OF[t]) for t, j in _DECL.findall(body)]


#: Probe values. Each round writes the same mathematical value V into a field
#: whatever its type, converted as C converts it (integers: truncate, then
#: wrap to the type's width -- GCC's and Clang's documented behaviour for a
#: signed narrowing): round 0 V = -(j + 2) - 0.5, round 1 V = 70000 + j + 0.25,
#: a magnitude no narrower integer type holds. Literals are computed here, so
#: no out-of-range conversion happens in C.
ROUNDS = 2


def probe_value(j: int, t: int, rnd: int) -> float:
    v = -(j + 2) - 0.5 if rnd == 0 else 70000 + j + 0.25
    fmt = TYPES[t][2]
    if fmt in "fd":
        return struct.unpack("<" + fmt, struct.pack("<" + fmt, v))[0]
    bits = 8 * TYPES[t][1]
    w = int(v) % (1 << bits)  # int() truncates toward zero, like C
    if fmt != "I" and w >= 1 << (bits - 1):
        w -= 1 << bits
    return w


def value_bytes(j: int, t: int, rnd: int = 0) -> bytes:
    """The bytes a writer stores into field ``f<j>`` of type ``t``."""
    v = probe_value(j, t, rnd)
    fmt = TYPES[t][2]
    return struct.pack("<" + fmt, v if fmt in "fd" else int(v))


def _literal(j: int, t: int, rnd: int) -> str:
    v = probe_value(j, t, rnd)
    fmt = TYPES[t][2]
    if fmt in "fd":
        return repr(float(v)) + ("f" if fmt == "f" else "")
    return f"{int(v)}u" if fmt == "I" else f"({int(v)})"


def consumer_model(row: dict, provider: str, rnd: int = 0) -> dict[int, str]:
    """What the old-header consumer reads, per old field, as hex: the
    provider ``provider`` ('old'/'new') writes every field it declares into a
    buffer of SENTINEL bytes; the consumer reads the old layout. Byte-level,
    from the Lean offsets; independent of the compiler and of abicheck."""
    buf = bytearray([SENTINEL] * 64)
    body = row[f"{provider}_body"]
    offs = row[provider][0]
    for (j, t), o in zip(decls(body), offs):
        buf[o : o + TYPES[t][1]] = value_bytes(j, t, rnd)
    return {
        j: buf[o : o + TYPES[t][1]].hex()
        for (j, t), o in zip(decls(row["old_body"]), row["old"][0])
    }


def write_model(row: dict, provider: str, rnd: int) -> dict[int, float | None]:
    """The write direction: the old-header consumer stores every old field
    into a SENTINEL buffer; provider ``provider`` reads its own fields. Per old
    field: the value that provider reads for it (None: it has no such field)."""
    buf = bytearray([SENTINEL] * 64)
    for (j, t), o in zip(decls(row["old_body"]), row["old"][0]):
        buf[o : o + TYPES[t][1]] = value_bytes(j, t, rnd)
    seen = {}
    for (j, t), o in zip(decls(row[f"{provider}_body"]), row[provider][0]):
        seen[j] = struct.unpack("<" + TYPES[t][2], bytes(buf[o : o + TYPES[t][1]]))[0]
    return {j: seen.get(j) for j, _ in decls(row["old_body"])}


def expected_values(row: dict) -> dict[str, tuple[str | None, str | None]]:
    """Typed old/new values per expected finding (bits, type spellings)."""
    out: dict[str, tuple[str | None, str | None]] = {}
    od, nd = decls(row["old_body"]), decls(row["new_body"])
    ooff = dict(zip([j for j, _ in od], row["old"][0]))
    noff = dict(zip([j for j, _ in nd], row["new"][0]))
    nty = dict(nd)
    for j, t in od:
        if j in noff and noff[j] != ooff[j]:
            out[f"type_field_offset_changed:f{j}"] = (
                str(8 * ooff[j]),
                str(8 * noff[j]),
            )
        if j in nty and nty[j] != t:
            out[f"type_field_type_changed:f{j}"] = (TYPES[t][0], TYPES[nty[j]][0])
    out["type_size_changed:"] = (str(8 * row["old"][1]), str(8 * row["new"][1]))
    out["type_alignment_changed:"] = (str(8 * row["old"][2]), str(8 * row["new"][2]))
    return out


# --------------------------------------------------------------------- build


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)  # noqa: S603


def _write_sources(out: Path, rows: list[dict], ctrl: list[dict]) -> None:
    for side in ("old", "new"):
        h = ["/* generated by formal/experiments/record_edit_fragment.py */", PREMISE]
        c = ["#include <stdio.h>", f'#include "{side}.h"']
        probe = ["#include <stdio.h>", "#include <stddef.h>", f'#include "{side}.h"']
        probe.append("int main(void){")
        recs = [(f"s{r['id']}", r[f"{side}_body"]) for r in rows]
        recs += [(f"c{r['id']}", r[f"{side}_body"]) for r in ctrl]
        for name, body in recs:
            h.append(f"struct {name} {{ {body} }};")
            h.append(f"int use_{name}(struct {name} *p);")
            h.append(f"void fill_{name}(struct {name} *p, int r);")
            h.append(f"void show_{name}(const struct {name} *p);")
            ds = decls(body)
            c.append(f"int use_{name}(struct {name} *p) {{ return (int)sizeof *p; }}")
            fill = [
                f"p->f{j} = r ? {_literal(j, t, 1)} : {_literal(j, t, 0)};"
                for j, t in ds
            ]
            c.append(
                f"void fill_{name}(struct {name} *p, int r) {{ {' '.join(fill)} }}"
            )
            show = " ".join(f'printf(" f{j}=%.17g", (double)p->f{j});' for j, t in ds)
            c.append(
                f'void show_{name}(const struct {name} *p) {{ {show} printf("\\n"); }}'
            )
            if name.startswith("s"):
                offs = ",".join(f"(int)offsetof(struct {name},f{j})" for j, _ in ds)
                fmt = " ".join("%d" for _ in ds)
                probe.append(
                    f'printf("{name[1:]} %d %d {fmt}\\n",(int)sizeof(struct {name}),'
                    f"(int)_Alignof(struct {name}),{offs});"
                )
        probe.append("return 0;}")
        (out / f"{side}.h").write_text("\n".join(h) + "\n")
        (out / f"{side}.c").write_text("\n".join(c) + "\n")
        (out / f"probe_{side}.c").write_text("\n".join(probe) + "\n")
    # The consumer: compiled against old.h only, linked to libprov.so, which
    # is the old or the new provider at run time.
    cons = [
        "#include <stdio.h>",
        "#include <string.h>",
        "#include <stddef.h>",
        '#include "old.h"',
        "static union { unsigned char b[64]; max_align_t a; } u;",
        "static void hex(const void *p, size_t n){const unsigned char*q=p;"
        'for(size_t i=0;i<n;i++)printf("%02x",q[i]);}',
        f"int main(void){{ for (int r = 0; r < {ROUNDS}; r++) {{",
    ]
    for r in rows:
        i = r["id"]
        cons.append(f"memset(u.b,{SENTINEL},64); fill_s{i}((struct s{i}*)u.b, r);")
        cons.append(f'printf("{i} %d", r);')
        for j, t in decls(r["old_body"]):
            cons.append(
                f'printf(" "); hex(u.b+offsetof(struct s{i},f{j}),{TYPES[t][1]});'
            )
        cons.append('printf("\\n");')
        # write direction: the consumer stores its (old) fields, the provider
        # reports what it reads
        cons.append(f"memset(u.b,{SENTINEL},64);")
        for j, t in decls(r["old_body"]):
            cons.append(
                f"((struct s{i}*)u.b)->f{j} = r ? {_literal(j, t, 1)} : {_literal(j, t, 0)};"
            )
        cons.append(
            f'printf("W{i} %d", r); fflush(stdout); show_s{i}((struct s{i}*)u.b); fflush(stdout);'
        )
    cons.append("} return 0;}")
    (out / "consumer.c").write_text("\n".join(cons) + "\n")


def _cc(cc: str, *args: str) -> None:
    proc = _run([cc, "-std=c11", *args])
    if proc.returncode:
        sys.stderr.write(proc.stderr[-4000:])
        raise SystemExit(f"infrastructure_error: {cc} {' '.join(args[-3:])}")


def _build(out: Path, cc: str) -> dict:
    """Both sides with and without -g, the layout probes and the consumer."""
    res: dict = {"layout": {}}
    for side in ("old", "new"):
        src = str(out / f"{side}.c")
        _cc(
            cc,
            "-g",
            "-O0",
            "-shared",
            "-fPIC",
            "-I",
            str(out),
            src,
            "-o",
            str(out / f"lib{side}_{cc}_g.so"),
        )
        _cc(
            cc,
            "-O0",
            "-shared",
            "-fPIC",
            "-I",
            str(out),
            src,
            "-o",
            str(out / f"lib{side}_{cc}_nog.so"),
        )
        exe = out / f"probe_{side}_{cc}"
        _cc(cc, "-I", str(out), str(out / f"probe_{side}.c"), "-o", str(exe))
        lay = {}
        for line in _run([str(exe)], check=True).stdout.splitlines():
            i, size, align, *offs = map(int, line.split())
            lay[i] = [offs, size, align]
        res["layout"][side] = lay
    # consumer, linked against a soname-less provider name swapped at run time
    prov = out / f"prov_{cc}"
    prov.mkdir(exist_ok=True)
    shutil.copy(out / f"libold_{cc}_nog.so", prov / "libprov.so")
    _cc(
        cc,
        "-O0",
        "-I",
        str(out),
        str(out / "consumer.c"),
        "-L",
        str(prov),
        "-lprov",
        "-o",
        str(out / f"consumer_{cc}"),
    )
    reads = {}
    for side in ("old", "new"):
        shutil.copy(out / f"lib{side}_{cc}_nog.so", prov / "libprov.so")
        proc = _run(
            [str(out / f"consumer_{cc}")],
            env={"LD_LIBRARY_PATH": str(prov)},
            check=True,
        )
        rd: dict = {}
        for line in proc.stdout.splitlines():
            p = line.split()
            if p[0].startswith("W"):
                rd[("W", int(p[0][1:]), int(p[1]))] = {
                    int(kv.split("=")[0][1:]): float(kv.split("=")[1]) for kv in p[2:]
                }
            else:
                rd[(int(p[0]), int(p[1]))] = p[2:]
        reads[side] = rd
    res["consumer"] = reads
    return res


def _compare(out: Path, cc: str, lane: str) -> tuple[int, dict]:
    rep = out / f"compare_{cc}_{lane}.json"
    rep.unlink(missing_ok=True)
    lib = "nog" if lane == "hdr" else "g"
    cmd = [
        "abicheck",
        "compare",
        str(out / f"libold_{cc}_{lib}.so"),
        str(out / f"libnew_{cc}_{lib}.so"),
        "-o",
        f"json={rep}",
    ]
    if lane != "dwarf":
        cmd[4:4] = [
            "--header",
            f"old={out / 'old.h'}",
            "--header",
            f"new={out / 'new.h'}",
        ]
    proc = _run(cmd)
    if proc.returncode not in (0, 2, 4) or not rep.exists():
        sys.stderr.write(proc.stderr[-4000:])
        raise SystemExit(
            f"extractor_failure: abicheck compare {cc}/{lane} exit {proc.returncode}"
        )
    return proc.returncode, json.loads(rep.read_text(encoding="utf-8"))


def _by_record(report: dict) -> dict[str, list[dict]]:
    by: dict[str, list[dict]] = {}
    for c in report["changes"]:
        by.setdefault(c["symbol"].split("::")[0], []).append(c)
    return by


def observed(changes: list[dict]) -> tuple[list[str], dict[str, tuple], str]:
    """Typed observation: ``kind:field_name`` per finding, typed values, verdict."""
    fs, vals = [], {}
    for c in changes:
        key = f"{c['kind']}:{c.get('field_name') or ''}"
        fs.append(key)
        vals[key] = (c.get("old_value"), c.get("new_value"))
    sev = {c["severity"] for c in changes}
    verdict = (
        "breaking"
        if "breaking" in sev
        else ("compatible" if sev <= {"compatible"} else "other")
    )
    if not changes:
        verdict = "none"
    return sorted(fs), vals, verdict


def expected(cls: dict, lane: str) -> list[str]:
    drop = LANE_UNOBSERVABLE.get(lane, set())
    return sorted(f for f in cls["findings"] if f.split(":")[0] not in drop)


#: Every spelling a producer uses for the fragment's types (clang: ``short``;
#: CastXML and GCC's DWARF: ``short int``). A typed value names a type, so it
#: is compared by identity, not by spelling.
_SPELLINGS = {
    "char": "char",
    "short": "short",
    "short int": "short",
    "signed short": "short",
    "int": "int",
    "signed int": "int",
    "double": "double",
    "unsigned int": "unsigned int",
    "unsigned": "unsigned int",
    "float": "float",
}


def _canon(v: object) -> object:
    return _SPELLINGS.get(v, v) if isinstance(v, str) else v


def judge(
    row: dict, cls: dict, lane: str, changes: list[dict], layout_ok: bool
) -> dict:
    want = expected(cls, lane)
    got, vals, verdict = observed(changes)
    ev = expected_values(row)
    bad_vals = {
        k: (vals[k], ev[k])
        for k in got
        if k in ev and k in want and tuple(map(_canon, vals[k])) != ev[k]
    }
    spellings = sorted(
        {
            str(v)
            for k in got
            if k.startswith("type_field_type_changed")
            for v in vals[k]
        }
    )
    if not layout_ok:
        outcome = "oracle_conflict"
    elif not changes:
        outcome = "missing"
    elif got != want or verdict != cls["verdict"]:
        outcome = "checker_mismatch"
    elif bad_vals:
        outcome = "value_mismatch"
    else:
        outcome = "agree"
    aff = sorted({a for c in changes for a in c.get("affected_symbols") or []})
    own = {f"use_s{row['id']}", f"fill_s{row['id']}"}
    return {
        "outcome": outcome,
        "got": got,
        "want": want,
        "verdict": verdict,
        "bad_values": bad_vals,
        "type_spellings": spellings,
        "affected": "exact"
        if set(aff) == own
        else ("superset" if own <= set(aff) else "other"),
        "affected_extra": len(set(aff) - own),
    }


def consumer_judge(row: dict, reads: dict, lane_got: list[str]) -> dict:
    """Old consumer, old vs new provider: which old fields read differently,
    whether the byte model predicted it, and whether abicheck names each such
    field (removed, retyped or moved)."""
    fields = [j for j, _ in decls(row["old_body"])]
    model_ok, changed_r, changed_w = True, set(), set()
    for rnd in range(ROUNDS):
        old_r = reads["old"][(row["id"], rnd)]
        new_r = reads["new"][(row["id"], rnd)]
        m_old = consumer_model(row, "old", rnd)
        m_new = consumer_model(row, "new", rnd)
        model_ok &= old_r == [m_old[j] for j in fields] and new_r == [
            m_new[j] for j in fields
        ]
        changed_r |= {j for j, a, b in zip(fields, old_r, new_r) if a != b}
        w_old = reads["old"][("W", row["id"], rnd)]
        w_new = reads["new"][("W", row["id"], rnd)]
        wm_old, wm_new = write_model(row, "old", rnd), write_model(row, "new", rnd)
        model_ok &= all(w_old.get(j) == wm_old[j] for j in fields)
        model_ok &= all(w_new.get(j) == wm_new[j] for j in fields)
        changed_w |= {j for j in fields if w_old.get(j) != w_new.get(j)}
    changed = sorted(changed_r | changed_w)
    named = {g.split(":")[1] for g in lane_got if g.split(":")[1]}
    return {
        "model_ok": model_ok,
        "changed_fields": changed,
        "changed_read": sorted(changed_r),
        "changed_write": sorted(changed_w),
        "unexplained": [j for j in changed if f"f{j}" not in named],
        "named_but_unchanged": sorted(n for n in named if int(n[1:]) not in changed),
    }


# ------------------------------------------------------------------- drivers


def _peak_rss_mb() -> float:
    return round(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024, 1)


def _tool_versions(compilers: tuple[str, ...]) -> dict:
    def first(cmd: list[str]) -> str:
        try:
            return _run(cmd).stdout.splitlines()[0]
        except (OSError, IndexError):
            return "unavailable"

    v = {cc: first([cc, "--version"]) for cc in compilers}
    v["castxml"] = first(["castxml", "--version"])
    v["abicheck"] = first(["abicheck", "--version"])
    v["python"] = sys.version.split()[0]
    v["target"] = f"{platform.system()} {platform.machine()}"
    v["flags"] = "-std=c11 -O0 -shared -fPIC (+ -g for dwarf/both lanes)"
    try:
        v["commit"] = _run(["git", "-C", str(HERE), "rev-parse", "HEAD"]).stdout.strip()
    except OSError:
        v["commit"] = "unknown"
    return v


def check_batch(
    out: Path, rows: list[dict], classes: list[dict], compilers, lanes, ctrl
) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    _write_sources(out, rows, ctrl)
    res: dict = {"cases": len(rows), "controls": len(ctrl), "compilers": {}}
    res["seconds_generate"] = round(time.perf_counter() - t0, 2)
    lines = []
    for cc in compilers:
        t1 = time.perf_counter()
        b = _build(out, cc)
        t2 = time.perf_counter()
        cres: dict = {"seconds_build_probe_consumer": round(t2 - t1, 2), "lanes": {}}
        lay_bad = [
            (r["id"], s)
            for r in rows
            for s in ("old", "new")
            if b["layout"][s][r["id"]] != r[s]
        ]
        cres["layout_agree"] = 2 * len(rows) - len(lay_bad)
        cres["layout_disagree"] = len(lay_bad)
        lay_bad_ids = {i for i, _ in lay_bad}
        per_case: dict[int, dict] = {r["id"]: {"id": r["id"], "cc": cc} for r in rows}
        for lane in lanes:
            t3 = time.perf_counter()
            _, rep = _compare(out, cc, lane)
            secs = round(time.perf_counter() - t3, 2)
            by = _by_record(rep)
            outcomes: Counter = Counter()
            aff: Counter = Counter()
            examples = []
            for r in rows:
                j = judge(
                    r,
                    classes[r["class"]],
                    lane,
                    by.get(f"s{r['id']}", []),
                    r["id"] not in lay_bad_ids,
                )
                outcomes[j["outcome"]] += 1
                aff[j["affected"]] += 1
                per_case[r["id"]][lane] = j
                if j["outcome"] != "agree" and len(examples) < 8:
                    examples.append(
                        {"id": r["id"], "old": r["old_body"], "new": r["new_body"], **j}
                    )
            ctrl_hits = sorted(k for k in by if k.startswith("c"))
            cres["lanes"][lane] = {
                "outcomes": dict(outcomes),
                "affected_symbols": dict(aff),
                "controls_with_findings": len(ctrl_hits),
                "control_examples": ctrl_hits[:5],
                "detected_total": rep["disposition_audit"]["detected_total"],
                "deduplicated": rep["disposition_audit"]["counts"]["deduplicated"],
                "examples": examples,
                "seconds_compare": secs,
            }
        cons: Counter = Counter()
        cons_ex = []
        ref_lane = "both" if "both" in lanes else lanes[0]
        for r in rows:
            cj = consumer_judge(r, b["consumer"], per_case[r["id"]][ref_lane]["got"])
            per_case[r["id"]]["consumer"] = cj
            cons["model_ok" if cj["model_ok"] else "model_conflict"] += 1
            cons["changed" if cj["changed_fields"] else "unchanged"] += 1
            if cj["unexplained"]:
                cons["unexplained"] += 1
                if len(cons_ex) < 5:
                    cons_ex.append({"id": r["id"], **cj})
            if cj["named_but_unchanged"]:
                cons["named_but_unchanged"] += 1
        cres["consumer"] = {
            "counts": dict(cons),
            "reference_lane": ref_lane,
            "unexplained_examples": cons_ex,
        }
        res["compilers"][cc] = cres
        lines += [json.dumps(v) for v in per_case.values()]
    (out / "cases.jsonl").write_text("\n".join(lines) + "\n")
    res["peak_child_rss_mb"] = _peak_rss_mb()
    return res


def _one_case(out: Path, row: dict, cls: dict, compilers, lanes) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    _write_sources(out, [row], [])
    r: dict = {"id": row["id"]}
    for cc in compilers:
        b = _build(out, cc)
        layout_ok = all(b["layout"][s][row["id"]] == row[s] for s in ("old", "new"))
        for lane in lanes:
            code, rep = _compare(out, cc, lane)
            j = judge(
                row, cls, lane, _by_record(rep).get(f"s{row['id']}", []), layout_ok
            )
            r[f"{cc}/{lane}"] = {
                "outcome": j["outcome"],
                "exit_ok": code == (4 if cls["verdict"] == "breaking" else 0),
                "exit": code,
            }
    shutil.rmtree(out)
    return r


def check_per_case(out: Path, rows, classes, compilers, lanes, jobs: int) -> dict:
    with ThreadPoolExecutor(max_workers=jobs) as ex:
        results = list(
            ex.map(
                lambda r: _one_case(
                    out / f"c{r['id']}", r, classes[r["class"]], compilers, lanes
                ),
                rows,
            )
        )
    summary: dict = {"cases": len(rows), "cells": {}}
    for key in results[0]:
        if key == "id":
            continue
        summary["cells"][key] = dict(
            Counter(f"{x[key]['outcome']} exit_ok={x[key]['exit_ok']}" for x in results)
        )
    summary["bad"] = [
        x
        for x in results
        if any(
            v["outcome"] != "agree" or not v["exit_ok"]
            for k, v in x.items()
            if k != "id"
        )
    ][:8]
    summary["peak_child_rss_mb"] = _peak_rss_mb()
    return summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("out", type=Path)
    ap.add_argument("--reduced", action="store_true", help="only class representatives")
    ap.add_argument(
        "--sample", type=int, default=0, help="N cases drawn with a fixed seed"
    )
    ap.add_argument("--per-case", action="store_true")
    ap.add_argument("--compilers", default="gcc,clang")
    ap.add_argument("--lanes", default=",".join(LANES))
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument(
        "--repeat", type=int, default=1, help="repeat the run for timing samples"
    )
    ap.add_argument("--no-controls", action="store_true")
    a = ap.parse_args(argv)
    data = load_export()
    rows, classes = data["rows"], data["classes"]
    if a.reduced:
        rows = [r for r in rows if r["rep"]]
    if a.sample:
        rows = sorted(random.Random(15).sample(rows, a.sample), key=lambda r: r["id"])
    compilers = tuple(a.compilers.split(","))
    lanes = tuple(a.lanes.split(","))
    ctrl = [] if (a.no_controls or a.per_case) else controls()
    runs = []
    for k in range(a.repeat):
        t0 = time.perf_counter()
        if a.per_case:
            res = check_per_case(a.out, rows, classes, compilers, lanes, a.jobs)
        else:
            res = check_batch(a.out, rows, classes, compilers, lanes, ctrl)
        res["wall_seconds"] = round(time.perf_counter() - t0, 2)
        runs.append(res)
    summary = runs[-1]
    summary["mode"] = ("per-case" if a.per_case else "batch") + (
        " reduced" if a.reduced else f" sample={a.sample}" if a.sample else " full"
    )
    summary["environment"] = _tool_versions(compilers)
    summary["wall_seconds_samples"] = [r["wall_seconds"] for r in runs]
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
