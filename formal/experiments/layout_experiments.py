#!/usr/bin/env python3
"""Experiments E5 + E6 over the Lean-exported layout class table.

E6a  Lean layout model  vs  gcc offsetof/sizeof          (model validity)
E6b  abicheck-extracted offsets (castxml headers, DWARF)  vs  gcc   (extraction soundness)
E5   abicheck compare findings  vs  model-predicted moves / size changes

Usage: layout_experiments.py OUT_DIR   (writes OUT_DIR/summary.json)
Needs gcc, castxml and abicheck on PATH.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
TABLE = json.loads(
    (HERE.parent / "lean" / "layout_table.json").read_text(encoding="utf-8")
)["rows"]
CTYPE = {"char": "char", "short": "short", "int": "int", "double": "double"}


def fields_of(row: dict, side: str) -> list[tuple[str, str]]:
    base = [(f"f{i}", t) for i, t in enumerate(row["base"])]
    if side == "old":
        return base
    k = row["insert_at"]
    return base[:k] + [("ins", row["inserted"])] + base[k:]


def header(side: str) -> str:
    out = ["/* generated */"]
    for i, row in enumerate(TABLE):
        body = " ".join(f"{CTYPE[t]} {n};" for n, t in fields_of(row, side))
        out.append(f"struct s{i} {{ {body} }};")
        out.append(f"int use_s{i}(struct s{i} *p);")
    return "\n".join(out) + "\n"


def source(side: str) -> str:
    out = [f'#include "{side}.h"']
    for i, row in enumerate(TABLE):
        n0 = fields_of(row, side)[0][0]
        out.append(f"int use_s{i}(struct s{i} *p) {{ return (int)p->{n0}; }}")
    return "\n".join(out) + "\n"


def probe(side: str) -> str:
    out = [
        "#include <stdio.h>",
        "#include <stddef.h>",
        f'#include "{side}.h"',
        "int main(void){",
    ]
    for i, row in enumerate(TABLE):
        offs = ",".join(
            f"(int)offsetof(struct s{i},{n})" for n, _ in fields_of(row, side)
        )
        fmt = " ".join("%d" for _ in fields_of(row, side))
        out.append(f'printf("{i} %d {fmt}\\n",(int)sizeof(struct s{i}),{offs});')
    out.append("return 0;}")
    return "\n".join(out) + "\n"


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)  # noqa: S603


def gcc_layouts(out: Path, side: str) -> dict[int, tuple[int, list[int]]]:
    (out / f"probe_{side}.c").write_text(probe(side))
    run(
        [
            "gcc",
            "-I",
            str(out),
            str(out / f"probe_{side}.c"),
            "-o",
            str(out / f"probe_{side}"),
        ],
        check=True,
    )
    res = {}
    for line in run([str(out / f"probe_{side}")], check=True).stdout.splitlines():
        i, size, *offs = map(int, line.split())
        res[i] = (size, offs)
    return res


def snapshot_types(path: Path) -> dict[str, dict]:
    snap = json.loads(path.read_text(encoding="utf-8"))
    types = snap.get("types") or snap.get("declarations", {}).get("types") or []
    if not types:  # storage v2: find the record list wherever it lives

        def walk(o):
            if isinstance(o, dict):
                if "fields" in o and "name" in o:
                    yield o
                for v in o.values():
                    yield from walk(v)
            elif isinstance(o, list):
                for v in o:
                    yield from walk(v)

        types = list(walk(snap))
    return {t["name"]: t for t in types if str(t.get("name", "")).startswith("s")}


def main(out: Path) -> int:
    out.mkdir(parents=True, exist_ok=True)
    for side in ("old", "new"):
        (out / f"{side}.h").write_text(header(side))
        (out / f"{side}.c").write_text(source(side))
        run(
            [
                "gcc",
                "-g",
                "-O0",
                "-shared",
                "-fPIC",
                "-I",
                str(out),
                str(out / f"{side}.c"),
                "-o",
                str(out / f"lib{side}.so"),
            ],
            check=True,
        )
    gcc = {s: gcc_layouts(out, s) for s in ("old", "new")}

    # E6a: model vs gcc
    e6a = Counter()
    e6a_bad = []
    for i, row in enumerate(TABLE):
        for side, key_o, key_s in (
            ("old", "old_offsets", "old_size"),
            ("new", "new_offsets", "new_size"),
        ):
            ok = gcc[side][i] == (row[key_s], row[key_o])
            e6a["agree" if ok else "disagree"] += 1
            if not ok:
                e6a_bad.append((i, side, row, gcc[side][i]))

    # E6b: extraction vs gcc, per backend
    e6b: dict[str, Counter] = {}
    e6b_bad: dict[str, list] = {}
    for backend, extra in (
        ("castxml_headers", ["-H", None]),
        ("dwarf_only", ["--depth", "binary"]),
    ):
        e6b[backend] = Counter()
        e6b_bad[backend] = []
        for side in ("old", "new"):
            snap = out / f"{backend}_{side}.json"
            args = [a if a is not None else str(out / f"{side}.h") for a in extra]
            r = run(
                ["abicheck", "dump", str(out / f"lib{side}.so"), *args, "-o", str(snap)]
            )
            if r.returncode != 0:
                e6b[backend]["dump_failed"] += 1
                (out / f"{backend}_{side}.err").write_text(r.stderr)
                continue
            types = snapshot_types(snap)
            for i, row in enumerate(TABLE):
                t = types.get(f"s{i}")
                g_size, g_offs = gcc[side][i]
                if t is None:
                    e6b[backend]["record_missing"] += 1
                    continue
                names = [n for n, _ in fields_of(row, side)]
                by = {f["name"]: f.get("offset_bits") for f in t.get("fields", [])}
                ext = [by.get(n) for n in names]
                want = [o * 8 for o in g_offs]
                size_ok = t.get("size_bits") in (None, g_size * 8)
                if ext == want and size_ok:
                    e6b[backend]["agree"] += 1
                elif any(x is None for x in ext) and all(
                    x is None or x == w for x, w in zip(ext, want)
                ):
                    e6b[backend]["unknown_not_wrong"] += 1
                else:
                    e6b[backend]["disagree"] += 1
                    e6b_bad[backend].append(
                        (i, side, ext, want, t.get("size_bits"), g_size * 8)
                    )

    # E5: abicheck compare vs model prediction
    rep = out / "compare.json"
    run(
        [
            "abicheck",
            "compare",
            str(out / "libold.so"),
            str(out / "libnew.so"),
            "--header",
            f"old={out / 'old.h'}",
            "--header",
            f"new={out / 'new.h'}",
            "-o",
            f"json={rep}",
        ]
    )
    changes = json.loads(rep.read_text(encoding="utf-8"))["changes"]
    moved_found: set[tuple[str, str]] = set()
    size_found: set[str] = set()
    for c in changes:
        if c["kind"] in ("type_field_offset_changed", "struct_field_offset_changed"):
            # description: "Field offset changed: sN::fK (...)"
            d = c.get("description", "")
            if "::" in d:
                rec, fld = d.split(": ", 1)[1].split(" ")[0].split("::")
                moved_found.add((rec, fld))
        if c["kind"] in ("type_size_changed", "struct_size_changed"):
            size_found.add(c["symbol"])
    e5 = Counter()
    e5_bad = []
    for i, row in enumerate(TABLE):
        rec = f"s{i}"
        k = row["insert_at"]
        for j, o in enumerate(row["old_offsets"]):
            new_o = row["new_offsets"][j if j < k else j + 1]
            pred = new_o != o
            got = (rec, f"f{j}") in moved_found
            e5[("offset", pred, got)] += 1
            if pred != got:
                e5_bad.append(("offset", rec, f"f{j}", row))
        pred_s = row["new_size"] != row["old_size"]
        got_s = rec in size_found
        e5[("size", pred_s, got_s)] += 1
        if pred_s != got_s:
            e5_bad.append(("size", rec, None, row))

    # E4 statistics: how often does the class (insert before f / append) determine the outcome?
    stats = Counter()
    for row in TABLE:
        k = row["insert_at"]
        n = len(row["base"])
        for j in range(k, n):
            moved = row["new_offsets"][j + 1] != row["old_offsets"][j]
            stats[("insert_before_field", "moved" if moved else "NOT moved")] += 1
        if k == n:
            stats[
                (
                    "append",
                    "size grew" if row["new_size"] != row["old_size"] else "size SAME",
                )
            ] += 1

    summary = {
        "cases": len(TABLE),
        "E6a_model_vs_gcc": dict(e6a),
        "E6a_examples": e6a_bad[:5],
        "E6b_extraction_vs_gcc": {k: dict(v) for k, v in e6b.items()},
        "E6b_examples": {k: v[:5] for k, v in e6b_bad.items()},
        "E5_compare_vs_model": {
            f"{a}:pred={b}:found={c}": n for (a, b, c), n in sorted(e5.items())
        },
        "E5_examples": e5_bad[:8],
        "E4_class_outcomes": {f"{a}:{b}": n for (a, b), n in sorted(stats.items())},
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    print(
        json.dumps(
            {k: v for k, v in summary.items() if not k.endswith("examples")}, indent=1
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1])))
