#!/usr/bin/env python3
"""E16 competitor lane: libabigail ``abidiff`` and ABICC on the record-edit space.

Runs on the libraries a ``record_edit_fragment.py`` batch run left in OUT_DIR
(``lib{old,new}_{cc}_g.so``, built with -g). Each tool runs its own documented
workflow on the same paired inputs:

* ``abidiff OLD.so NEW.so`` -- DWARF, every type reachable from an export.
  (``--headers-dir1/2`` filters by the header *file name* recorded in DWARF;
  this corpus names the two versions' headers ``old.h``/``new.h``, so header
  filtering would drop every type. The plain workflow is used instead.)
* ``abi-dumper`` on each library, then ``abi-compliance-checker -binary
  -report-format xml`` on the two dumps.

**Common vocabulary.** Each tool's per-record output is mapped to the claims
all three can express, and scored per claim against the Lean oracle:

==================  ===============================================  ========================
claim               meaning                                          tools that express it
==================  ===============================================  ========================
flagged             the record is reported as changed                abicheck, abidiff, ABICC
edit_member         the removed / retyped field is named as such     abicheck, abidiff, ABICC
size                a sizeof change is reported (iff it changed)     abicheck, abidiff, ABICC
offset_moved        exactly the fields whose offset changed          abicheck, abidiff
position_moved      a swap names both swapped fields                 ABICC (``Moved_Field``)
==================  ===============================================  ========================

A claim a tool's output format cannot express is ``unsupported`` for that
tool, never a miss. Raw outputs stay in OUT_DIR (``abidiff_{cc}.txt``,
``abicc_{cc}.xml``); a tool that fails or is absent is an ``error`` /
``unavailable`` cell, recorded with its stderr. abicheck's own column comes
from the batch run's ``compare_{cc}_dwarf.json`` (same DWARF evidence).

Usage::

    record_edit_competitors.py OUT_DIR [--compilers gcc,clang] [--rows reduced|full]
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import record_edit_fragment as frag  # noqa: E402

CLAIMS = ("flagged", "edit_member", "size", "offset_moved", "position_moved")
SUPPORTS = {
    "abicheck": {"flagged", "edit_member", "size", "offset_moved"},
    "abidiff": {"flagged", "edit_member", "size", "offset_moved"},
    "abicc": {"flagged", "edit_member", "size", "position_moved"},
}


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)  # noqa: S603


# --------------------------------------------------------------- oracle side


def expected_claims(row: dict) -> dict[str, object]:
    od = frag.decls(row["old_body"])
    nd = frag.decls(row["new_body"])
    ooff = dict(zip([j for j, _ in od], row["old"][0]))
    noff = dict(zip([j for j, _ in nd], row["new"][0]))
    op, a, b = row["edit"]
    return {
        "flagged": True,
        "edit_member": ("removed", a)
        if op == 0
        else ("retyped", a)
        if op == 2
        else None,
        "size": row["old"][1] != row["new"][1],
        "offset_moved": frozenset(j for j in ooff if j in noff and noff[j] != ooff[j]),
        "position_moved": frozenset((a, b)) if op == 1 else frozenset(),
    }


# ----------------------------------------------------------------- parsers


def _empty() -> dict:
    return {
        "flagged": False,
        "removed": set(),
        "retyped": set(),
        "moved": set(),
        "size": False,
    }


_FIELD = re.compile(r"'(?:[\w ]+?) f(\d+)'")


def parse_abidiff(text: str) -> dict[str, dict]:
    """Per record (``s<N>``): the members abidiff names and whether size changed."""
    out: dict[str, dict] = defaultdict(_empty)
    cur = None
    mode = None
    last: int | None = None  # the member a continuation line refers to
    for line in text.splitlines():
        m = re.search(r"in pointed to type 'struct (s\d+)'", line)
        if m:
            cur = m.group(1)
            out[cur]["flagged"] = True
            mode = None
            continue
        if cur is None:
            continue
        s = line.strip()
        if s.startswith("[C]") or s.startswith("[A]") or s.startswith("[D]"):
            cur = None
            continue
        if "type size changed" in s and mode is None:
            # Record-level only: the same words also appear under a member's
            # "type of 'char f0' changed:" block, about the field's size.
            out[cur]["size"] = True
        elif re.match(r"\d+ data member deletions?:", s):
            mode = "removed"
        elif re.match(r"\d+ data member (changes?|insertions?)", s):
            mode = "changed"
        elif mode == "removed" and (m := _FIELD.match(s)):
            out[cur]["removed"].add(int(m.group(1)))
        elif s.startswith("type of ") and (m := _FIELD.search(s)):
            last = int(m.group(1))
            out[cur]["retyped"].add(last)
        elif s.startswith("and offset changed from") and last is not None:
            # "type of 'char f1' changed: ... and offset changed from 8 to 16"
            out[cur]["moved"].add(last)
        elif "offset changed from" in s and (m := _FIELD.match(s)):
            out[cur]["moved"].add(int(m.group(1)))
    return dict(out)


def parse_abicc(xml_text: str) -> dict[str, dict]:
    out: dict[str, dict] = defaultdict(_empty)
    root = ET.fromstring(xml_text)
    for t in root.iter("type"):
        name = (t.get("name") or "").removeprefix("struct ")
        if not re.fullmatch(r"s\d+", name):
            continue
        rec = out[name]
        for p in t.iter("problem"):
            pid = p.get("id") or ""
            ch = p.find("change")
            target = ch.get("target") if ch is not None else None
            j = int(target[1:]) if target and re.fullmatch(r"f\d+", target) else None
            rec["flagged"] = True
            # The record's own sizeof: DataType_Size. ``*_And_Size`` problem
            # ids name the field's size or repeat the record's; they are not
            # read as a size claim on their own.
            if pid == "DataType_Size":
                rec["size"] = True
            if j is None:
                continue
            if pid.startswith("Removed_Field") or pid.startswith(
                "Removed_Middle_Field"
            ):
                rec["removed"].add(j)
            elif pid.startswith("Field_Type") or pid.startswith("Field_BaseType"):
                rec["retyped"].add(j)
            elif pid.startswith("Moved_Field"):
                rec["moved"].add(j)
    return dict(out)


def from_abicheck(report: dict) -> dict[str, dict]:
    out: dict[str, dict] = defaultdict(_empty)
    for c in report["changes"]:
        rec = out[c["symbol"].split("::")[0]]
        rec["flagged"] = True
        f = c.get("field_name")
        j = int(f[1:]) if f and re.fullmatch(r"f\d+", f) else None
        k = c["kind"]
        if k in ("type_size_changed", "struct_size_changed"):
            rec["size"] = True
        elif j is not None and k.endswith("field_removed"):
            rec["removed"].add(j)
        elif j is not None and k.endswith("field_type_changed"):
            rec["retyped"].add(j)
        elif j is not None and k.endswith("field_offset_changed"):
            rec["moved"].add(j)
    return dict(out)


def score(tool: str, row: dict, rec: dict | None) -> dict[str, str]:
    exp = expected_claims(row)
    rec = rec or _empty()
    got = {
        "flagged": rec["flagged"],
        "edit_member": (
            ("removed", next(iter(rec["removed"])))
            if len(rec["removed"]) == 1 and not rec["retyped"]
            else ("retyped", next(iter(rec["retyped"])))
            if len(rec["retyped"]) == 1 and not rec["removed"]
            else None
            if not (rec["removed"] or rec["retyped"])
            else ("several", tuple(sorted(rec["removed"] | rec["retyped"])))
        ),
        "size": rec["size"],
        "offset_moved": frozenset(rec["moved"]),
        "position_moved": frozenset(rec["moved"]),
    }
    res = {}
    for claim in CLAIMS:
        if claim not in SUPPORTS[tool]:
            res[claim] = "unsupported"
        elif claim == "position_moved" and row["edit"][0] != 1:
            res[claim] = "n/a"
        else:
            res[claim] = "match" if got[claim] == exp[claim] else "miss"
    return res


# ------------------------------------------------------------------- driver


def run_tools(out: Path, cc: str) -> dict[str, dict]:
    status: dict[str, dict] = {}
    old, new = out / f"libold_{cc}_g.so", out / f"libnew_{cc}_g.so"
    if shutil.which("abidiff"):
        t0 = time.perf_counter()
        p = _run(["abidiff", str(old), str(new)])
        (out / f"abidiff_{cc}.txt").write_text(p.stdout)
        ok = p.returncode in (0, 4, 12) and p.returncode & 1 == 0
        status["abidiff"] = {
            "exit": p.returncode,
            "ok": ok,
            "stderr": p.stderr[-2000:],
            "seconds": round(time.perf_counter() - t0, 2),
            "version": _run(["abidiff", "--version"]).stdout.strip(),
        }
    else:
        status["abidiff"] = {"ok": False, "unavailable": True}
    if shutil.which("abi-dumper") and shutil.which("abi-compliance-checker"):
        t0 = time.perf_counter()
        for side, lib, ver in (("old", old, "0"), ("new", new, "1")):
            d = _run(
                [
                    "abi-dumper",
                    str(lib),
                    "-o",
                    str(out / f"abicc_{cc}_{side}.dump"),
                    "-lver",
                    ver,
                ]
            )
            if d.returncode:
                status["abicc"] = {
                    "ok": False,
                    "exit": d.returncode,
                    "stderr": d.stderr[-2000:],
                }
                return status
        rep = out / f"abicc_{cc}.xml"
        p = _run(
            [
                "abi-compliance-checker",
                "-l",
                "e16",
                "-binary",
                "-report-format",
                "xml",
                "-old",
                str(out / f"abicc_{cc}_old.dump"),
                "-new",
                str(out / f"abicc_{cc}_new.dump"),
                "-report-path",
                str(rep),
            ],
            cwd=out,
        )
        status["abicc"] = {
            "exit": p.returncode,
            "ok": rep.exists() and p.returncode in (0, 1),
            "stderr": p.stderr[-2000:],
            "seconds": round(time.perf_counter() - t0, 2),
            "version": _run(["abi-compliance-checker", "-dumpversion"]).stdout.strip(),
        }
    else:
        status["abicc"] = {"ok": False, "unavailable": True}
    return status


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("out", type=Path)
    ap.add_argument("--compilers", default="gcc,clang")
    ap.add_argument("--rows", choices=("reduced", "full"), default="full")
    a = ap.parse_args(argv)
    data = frag.load_export()
    rows = data["rows"] if a.rows == "full" else [r for r in data["rows"] if r["rep"]]
    summary: dict = {"cases": len(rows), "compilers": {}}
    for cc in a.compilers.split(","):
        status = run_tools(a.out, cc)
        parsed: dict[str, dict | None] = {}
        rep = a.out / f"compare_{cc}_dwarf.json"
        parsed["abicheck"] = (
            from_abicheck(json.loads(rep.read_text())) if rep.exists() else None
        )
        parsed["abidiff"] = (
            parse_abidiff((a.out / f"abidiff_{cc}.txt").read_text())
            if status["abidiff"].get("ok")
            else None
        )
        parsed["abicc"] = (
            parse_abicc((a.out / f"abicc_{cc}.xml").read_text())
            if status["abicc"].get("ok")
            else None
        )
        tools: dict = {}
        for tool, recs in parsed.items():
            if recs is None:
                tools[tool] = {"status": "error_or_unavailable"}
                continue
            counts: dict[str, Counter] = {c: Counter() for c in CLAIMS}
            per_op: dict[str, Counter] = defaultdict(Counter)
            misses = []
            for r in rows:
                sc = score(tool, r, recs.get(f"s{r['id']}"))
                for c, v in sc.items():
                    counts[c][v] += 1
                    per_op[f"{('remove', 'swap', 'replace')[r['edit'][0]]}/{c}"][v] += 1
                if "miss" in sc.values() and len(misses) < 6:
                    misses.append(
                        {
                            "id": r["id"],
                            "old": r["old_body"],
                            "new": r["new_body"],
                            **sc,
                        }
                    )
            tools[tool] = {
                "claims": {c: dict(v) for c, v in counts.items()},
                "per_operation": {k: dict(v) for k, v in sorted(per_op.items())},
                "miss_examples": misses,
            }
        summary["compilers"][cc] = {"tool_status": status, "tools": tools}
    (a.out / "competitors.json").write_text(json.dumps(summary, indent=1, default=list))
    print(json.dumps(summary, indent=1, default=list))
    return 0


if __name__ == "__main__":
    sys.exit(main())
