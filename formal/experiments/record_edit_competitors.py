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

    record_edit_competitors.py OUT_DIR [--compilers gcc,clang]   # scores the rows OUT_DIR built
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

from defusedxml import ElementTree as ET

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


_DELETIONS = re.compile(r"\d+ data member deletions?:")
_CHANGES = re.compile(r"\d+ data member (changes?|insertions?)")


class _AbidiffRecord:
    """Line-by-line reader of one ``in pointed to type 'struct sN'`` block."""

    def __init__(self, rec: dict) -> None:
        self.rec = rec
        self.mode: str | None = (
            None  # None: record level; "removed"/"changed": member list
        )
        self.last: int | None = None  # the member a continuation line refers to

    def feed(self, s: str) -> None:
        if "type size changed" in s and self.mode is None:
            # Record level only: the same words also appear under a member's
            # "type of 'char f0' changed:" block, about the field's size.
            self.rec["size"] = True
        elif _DELETIONS.match(s):
            self.mode = "removed"
        elif _CHANGES.match(s):
            self.mode = "changed"
        else:
            self._member(s)

    def _member(self, s: str) -> None:
        m = _FIELD.match(s)
        if self.mode == "removed" and m:
            self.rec["removed"].add(int(m.group(1)))
        elif s.startswith("type of ") and (m := _FIELD.search(s)):
            self.last = int(m.group(1))
            self.rec["retyped"].add(self.last)
        elif s.startswith("and offset changed from") and self.last is not None:
            # "type of 'char f1' changed: ... and offset changed from 8 to 16"
            self.rec["moved"].add(self.last)
        elif "offset changed from" in s and m:
            self.rec["moved"].add(int(m.group(1)))


def parse_abidiff(text: str) -> dict[str, dict]:
    """Per record (``s<N>``): the members abidiff names and whether size changed."""
    out: dict[str, dict] = defaultdict(_empty)
    cur: _AbidiffRecord | None = None
    for line in text.splitlines():
        m = re.search(r"in pointed to type 'struct (s\d+)'", line)
        s = line.strip()
        if m:
            cur = _AbidiffRecord(out[m.group(1)])
            cur.rec["flagged"] = True
        elif s[:3] in ("[C]", "[A]", "[D]"):
            cur = None
        elif cur is not None:
            cur.feed(s)
    return dict(out)


def parse_abicc(xml_text: str) -> dict[str, dict]:
    out: dict[str, dict] = defaultdict(_empty)
    root = ET.fromstring(xml_text)
    if root.tag != "report" or root.find("problem_summary") is None:
        raise ValueError(f"not an ABICC report (root <{root.tag}>)")
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
    changes = report["changes"]
    if not isinstance(changes, list) or not all(isinstance(c, dict) for c in changes):
        raise ValueError("abicheck report: 'changes' is not a list of findings")
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


def _read(path: Path, parse):  # type: ignore[no-untyped-def]
    try:
        return parse(path.read_text()), None
    # ValueError: bad JSON; KeyError/TypeError/AttributeError: valid JSON or
    # XML of the wrong structure (a report without "changes", say)
    except (
        OSError,
        ValueError,
        ET.ParseError,
        KeyError,
        TypeError,
        AttributeError,
    ) as e:
        return None, f"parse_error: {type(e).__name__}: {e}"


def _parse_all(out: Path, cc: str, status: dict) -> tuple[dict, dict]:
    """Each tool's per-record claims, or None with the reason in *errors*.
    One tool's unreadable output never stops the others being scored."""
    parsed: dict[str, dict | None] = {}
    errors: dict[str, str] = {}
    rep = out / f"compare_{cc}_dwarf.json"
    libs = [out / f"lib{side}_{cc}_g.so" for side in ("old", "new")]
    if not rep.exists():
        errors["abicheck"] = "unavailable: no dwarf-lane report"
    elif any(rep.stat().st_mtime < lib.stat().st_mtime for lib in libs):
        # a report left by an earlier batch over other libraries
        errors["abicheck"] = "stale: dwarf-lane report older than the libraries"
    else:
        parsed["abicheck"], err = _read(rep, lambda t: from_abicheck(json.loads(t)))
        if err:
            errors["abicheck"] = err
    for tool, name, parse in (
        ("abidiff", f"abidiff_{cc}.txt", parse_abidiff),
        ("abicc", f"abicc_{cc}.xml", parse_abicc),
    ):
        if not status[tool].get("ok"):
            errors[tool] = "error_or_unavailable"
            continue
        parsed[tool], err = _read(out / name, parse)
        if err:
            errors[tool] = err
    for tool in ("abicheck", "abidiff", "abicc"):
        parsed.setdefault(tool, None)
    return parsed, errors


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("out", type=Path)
    ap.add_argument("--compilers", default="gcc,clang")
    a = ap.parse_args(argv)
    out = a.out.resolve()  # ABICC runs with cwd=out; every path must be absolute
    built_file = out / "built_rows.json"
    if not built_file.exists():
        ap.error(f"{built_file} missing: run record_edit_fragment.py {a.out} first")
    built = set(json.loads(built_file.read_text()))
    rows = [r for r in frag.load_export()["rows"] if r["id"] in built]
    summary: dict = {"cases": len(rows), "compilers": {}}
    for cc in a.compilers.split(","):
        status = run_tools(out, cc)
        parsed, errors = _parse_all(out, cc, status)
        tools: dict = {}
        for tool, recs in parsed.items():
            if recs is None:
                tools[tool] = {"status": errors.get(tool, "error_or_unavailable")}
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
    (out / "competitors.json").write_text(json.dumps(summary, indent=1, default=list))
    print(json.dumps(summary, indent=1, default=list))
    return 0


if __name__ == "__main__":
    sys.exit(main())
