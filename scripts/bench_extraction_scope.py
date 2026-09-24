#!/usr/bin/env python3
# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Measure what each way of narrowing an L2 header parse costs and loses.

Phase 0 of ``docs/contribute/plans/target-ownership-and-extraction-scope.md``:
one harness, rerun unchanged by every later phase, so a claim that a scoping
change "saves X" or "loses nothing" is always the same measurement.

For one translation unit (a file of ``#include`` lines) and its compile
flags it runs:

* ``clang`` — ``-ast-dump=json``, full and with ``-ast-dump-filter=<name>``;
* ``castxml`` — full and with ``--castxml-start <name>``;
* ``closure`` — the ownership-rooted closure over the full castxml XML:
  seed with every element declared in a file under a target root, then
  follow type/return/context/member/base/argument references to a fixed
  point (the design the plan calls ``dependency_evidence: referenced``,
  prototyped here, not yet in abicheck);
* ``dump`` — ``abicheck dump`` end to end with each frontend (optional,
  ``--dump``; needs a binary, a stub is fine for header-only measurement).

For every XML variant it parses the result through abicheck's own castxml
parser and counts functions and types per *owner* (the first ``--owner``
whose path prefix matches the declaring file), so "what did narrowing lose"
is answered in owned declarations, not bytes.

Peak RSS is each child process's own ``ru_maxrss`` (from ``os.wait4``):
the largest single process in that child's tree, not a summed tree figure.
Output is a JSON document on stdout (``--markdown`` for the plan's tables).

Usage::

    python scripts/bench_extraction_scope.py \\
        --tu svs_core.cpp --flags "$(cat svs.flags)" --start svs \\
        --owner svs=/work/svs/include --owner fmt=/work/deps/fmt \\
        --work /work/out --dump --binary libstub.so -H .../vamana.h
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

#: Element attributes that name another element by id.
_REF_ATTRS = ("type", "returns", "context")
#: Attributes holding a space-separated id list (``members``) or
#: ``[access:]id`` entries (``bases``).
_REF_LIST_ATTRS = ("members", "bases")


def _strip_cv(ref: str) -> str:
    """castxml spells a cv-qualified reference as the id plus ``c``/``v``."""
    return ref.rstrip("cv")


def element_refs(el: ET.Element) -> Iterable[str]:
    """Every element id *el* refers to (its dependencies in the closure)."""
    for attr in _REF_ATTRS:
        value = el.get(attr)
        if value:
            yield _strip_cv(value)
    for attr in _REF_LIST_ATTRS:
        for item in (el.get(attr) or "").split():
            yield _strip_cv(item.rsplit(":", 1)[-1])
    for child in el:  # <Argument type=...>, <Base type=...>, <Enumeration> values
        value = child.get("type")
        if value:
            yield _strip_cv(value)


def ownership_closure(
    root: ET.Element, target_roots: list[str]
) -> tuple[set[str], int]:
    """Ids kept by the ownership-rooted closure, and the number of seeds.

    Seeds are the elements declared in a file under any of *target_roots*.
    A kept ``Namespace`` pulls in only its own parent, never its members --
    otherwise the first referenced ``std::`` type would drag every
    declaration in ``std`` back in and the closure would be the whole
    document. Linear in the document size.
    """
    by_id = {el.get("id", ""): el for el in root}
    owned_files = {
        el.get("id", "")
        for el in root
        if el.tag == "File"
        and any(el.get("name", "").startswith(r) for r in target_roots)
    }
    stack = [el.get("id", "") for el in root if el.get("file") in owned_files]
    seeds = len(stack)
    keep: set[str] = set()
    while stack:
        eid = stack.pop()
        if eid in keep or eid not in by_id:
            continue
        keep.add(eid)
        el = by_id[eid]
        if el.tag == "Namespace":
            parent = el.get("context")
            if parent:
                stack.append(parent)
            continue
        stack.extend(element_refs(el))
    # A cv-qualified variant of a kept type is its own element.
    for el in root:
        if el.tag == "CvQualifiedType" and _strip_cv(el.get("type", "")) in keep:
            keep.add(el.get("id", ""))
    keep.update(el.get("id", "") for el in root if el.tag == "File")
    return keep, seeds


def prune_to(root: ET.Element, keep: set[str]) -> None:
    """Drop every element not in *keep*, in one pass."""
    root[:] = [el for el in root if el.get("id", "") in keep]


# ── process measurement ───────────────────────────────────────────────────


def run_measured(argv: list[str], stdout_path: Path | None) -> dict[str, object]:
    """Run *argv*; wall time, the child's own peak RSS, and stdout bytes.

    Stdout is streamed into *stdout_path* (or counted and discarded), so a
    multi-GB clang JSON never has to fit in this process.
    """
    start = time.monotonic()
    sink = open(stdout_path, "wb") if stdout_path else None  # noqa: SIM115
    # stderr goes to a file, never a second pipe: only stdout is drained
    # below, and a child that fills an undrained stderr pipe (castxml's
    # warnings do) blocks forever.
    errfile = tempfile.TemporaryFile()  # noqa: SIM115
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=errfile)
    written = 0
    if proc.stdout is None:  # Popen(stdout=PIPE) always sets it
        raise RuntimeError("no stdout pipe")
    for chunk in iter(lambda: proc.stdout.read(1 << 20), b""):  # type: ignore[union-attr]
        written += len(chunk)
        if sink:
            sink.write(chunk)
    _, status, usage = os.wait4(proc.pid, 0)
    errfile.seek(0)
    stderr = errfile.read()
    errfile.close()
    if sink:
        sink.close()
    return {
        "returncode": os.waitstatus_to_exitcode(status),
        "seconds": round(time.monotonic() - start, 1),
        "peak_rss_mb": round(usage.ru_maxrss / 1024),
        "stdout_mb": round(written / 1e6, 1),
        "stderr_tail": stderr.decode(errors="replace")[-400:] if status else "",
    }


# ── owned-declaration accounting ──────────────────────────────────────────


def _owner(path: str, owners: list[tuple[str, str]]) -> str:
    for name, prefix in owners:
        if path.startswith(prefix):
            return name
    return "other"


def owned_counts(xml_path: Path, owners: list[tuple[str, str]]) -> dict[str, object]:
    """Functions and types per owner, via abicheck's own castxml parser."""
    from abicheck.dumper_castxml import _CastxmlParser

    root = ET.parse(xml_path).getroot()
    target_dirs = [prefix for _, prefix in owners[:1]]
    parser = _CastxmlParser(
        root, set(), set(), public_dir_paths=target_dirs, no_binary_evidence=True
    )
    functions = parser.parse_functions()
    types = parser.parse_types()
    out: dict[str, object] = {"elements": len(root)}
    for label, items in (("functions", functions), ("types", types)):
        counts = Counter(_owner(item.source_location or "", owners) for item in items)
        out[label] = dict(sorted(counts.items()))
    target = owners[0][0]
    out["target_keys"] = sorted(
        f"f:{f.mangled or f.name}"
        for f in functions
        if _owner(f.source_location or "", owners) == target
    ) + sorted(
        f"t:{t.name}@{t.source_location}"
        for t in types
        if _owner(t.source_location or "", owners) == target
    )
    return out


def _lost(full: dict[str, object], narrowed: dict[str, object]) -> dict[str, object]:
    full_keys = set(full.pop("target_keys"))  # type: ignore[arg-type]
    kept = set(narrowed.pop("target_keys"))  # type: ignore[arg-type]
    lost = sorted(full_keys - kept)
    return {"target_lost": len(lost), "target_lost_sample": lost[:12]}


# ── the matrix ────────────────────────────────────────────────────────────


def measure(args: argparse.Namespace) -> dict[str, object]:
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    flags = shlex.split(args.flags)
    owners = [tuple(o.split("=", 1)) for o in args.owner]
    tu = str(Path(args.tu).resolve())
    castxml = args.castxml or shutil.which("castxml") or "castxml"
    std = [f for f in flags if f.startswith("-std=")]
    emulate = ["--castxml-cc-gnu", "(", "g++", *std, ")"]
    result: dict[str, object] = {"tu": tu, "flags": flags}

    clang = [args.clang, *flags, "-fsyntax-only", "-Xclang", "-ast-dump=json", tu]
    result["clang_full"] = run_measured(clang, None)
    filtered = clang[:-1] + ["-Xclang", f"-ast-dump-filter={args.start}::", tu]
    result["clang_filter"] = run_measured(filtered, None)

    xml_full = work / "full.xml"
    base = [castxml, "--castxml-output=1", *emulate, *flags]
    result["castxml_full"] = run_measured([*base, tu, "-o", str(xml_full)], None)
    xml_start = work / "start.xml"
    result["castxml_start"] = run_measured(
        [*base, "--castxml-start", args.start, tu, "-o", str(xml_start)], None
    )
    for key, path in (("castxml_full", xml_full), ("castxml_start", xml_start)):
        result[key]["xml_mb"] = round(path.stat().st_size / 1e6, 1)  # type: ignore[index]

    t0 = time.monotonic()
    tree = ET.parse(xml_full)
    keep, seeds = ownership_closure(tree.getroot(), [owners[0][1]])
    prune_to(tree.getroot(), keep)
    xml_closure = work / "closure.xml"
    tree.write(xml_closure)
    result["closure"] = {
        "seconds": round(time.monotonic() - t0, 1),
        "seeds": seeds,
        "xml_mb": round(xml_closure.stat().st_size / 1e6, 1),
    }
    del tree

    full_counts = owned_counts(xml_full, owners)
    result["castxml_full"]["owned"] = full_counts  # type: ignore[index]
    for key, path in (("castxml_start", xml_start), ("closure", xml_closure)):
        counts = owned_counts(path, owners)
        counts.update(_lost(dict(full_counts), counts))
        result[key]["owned"] = counts  # type: ignore[index]
    full_counts.pop("target_keys", None)

    if args.dump:
        result["dump"] = {fe: _dump(args, fe, work) for fe in ("clang", "castxml")}
    return result


def _dump(args: argparse.Namespace, frontend: str, work: Path) -> dict[str, object]:
    flags = shlex.split(args.flags)
    config = work / f"abicheck-{frontend}.yml"
    lines = ["compile:", f"  frontend: {frontend}"]
    std = [f.split("=", 1)[1] for f in flags if f.startswith("-std=")]
    if std:
        lines.append(f"  std: {std[-1]}")
    for key, prefix in (("include_dirs", "-I"), ("defines", "-D")):
        values = [f[2:] for f in flags if f.startswith(prefix)]
        if values:
            lines.append(f"  {key}:")
            lines += [f"    - {v}" for v in values]
    options = [f for f in flags if f.startswith("-f") or f.startswith("-m")]
    if options:
        lines.append("  options:")
        lines += [f"    - {v}" for v in options]
    config.write_text("\n".join(lines) + "\n")
    out = work / f"snapshot-{frontend}.json"
    argv = [
        sys.executable,
        "-m",
        "abicheck",
        "dump",
        args.binary,
        "--config",
        str(config),
    ]
    for header in args.header:
        argv += ["-H", header]
    env_cache = work / f"cache-{frontend}"
    shutil.rmtree(env_cache, ignore_errors=True)
    os.environ["ABICHECK_CACHE_DIR"] = str(env_cache)
    measured = run_measured([*argv, "-o", str(out)], None)
    if out.exists():
        size = out.stat().st_size
        measured["snapshot_mb"] = round(size / 1e6, 1)
        # Recorded, then lifted for this read-back only: a default dump can
        # write a snapshot larger than the default reader will accept.
        measured["exceeds_default_read_limit"] = size > (1 << 30)
        for var in (
            "ABICHECK_SNAPSHOT_MAX_STORED_BYTES",
            "ABICHECK_SNAPSHOT_MAX_DECODED_BYTES",
        ):
            os.environ[var] = str(16 << 30)
        owners = [tuple(o.split("=", 1)) for o in args.owner]
        try:
            measured.update(_snapshot_summary(out, owners))
        except Exception as exc:  # a measurement, not a gate: record and go on
            measured["summary_error"] = f"{type(exc).__name__}: {exc}"[:300]
    return measured


def _snapshot_summary(path: Path, owners: list[tuple[str, str]]) -> dict[str, object]:
    from abicheck.serialization import load_snapshot

    with open(path, encoding="utf-8") as fh:
        sections = json.load(fh).get("sections", {})
    section_mb = {k: round(len(json.dumps(v)) / 1e6, 1) for k, v in sections.items()}
    snap = load_snapshot(path)
    out: dict[str, object] = {
        "section_mb": dict(sorted(section_mb.items(), key=lambda kv: -kv[1])[:5])
    }
    for label in ("functions", "variables", "types"):
        items = getattr(snap, label)
        counts = Counter(_owner(i.source_location or "", owners) for i in items)
        out[label] = dict(sorted(counts.items()))
    return out


def _markdown(result: dict[str, object]) -> str:
    rows = [
        "| Mode | Time | Peak RSS | Output | Target lost |",
        "|---|---|---|---|---|",
    ]
    for key in (
        "clang_full",
        "clang_filter",
        "castxml_full",
        "castxml_start",
        "closure",
    ):
        r = result[key]  # type: ignore[index]
        size = r.get("xml_mb", r.get("stdout_mb"))  # type: ignore[union-attr]
        lost = r.get("owned", {}).get("target_lost", "—")  # type: ignore[union-attr]
        rss = r.get("peak_rss_mb", "—")  # type: ignore[union-attr]
        rows.append(f"| {key} | {r['seconds']} s | {rss} MB | {size} MB | {lost} |")  # type: ignore[index]
    return "\n".join(rows)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--tu", required=True, help="translation unit of #include lines")
    ap.add_argument("--flags", default="", help="compile flags, one shell string")
    ap.add_argument(
        "--start", required=True, help="target namespace for the name filters"
    )
    ap.add_argument(
        "--owner",
        action="append",
        required=True,
        help="NAME=PATH_PREFIX; the first one is the target, the rest named dependencies",
    )
    ap.add_argument(
        "--work", required=True, help="scratch directory for XML and snapshots"
    )
    ap.add_argument("--clang", default="clang++")
    ap.add_argument("--castxml", default=None)
    ap.add_argument(
        "--dump", action="store_true", help="also run abicheck dump with each frontend"
    )
    ap.add_argument("--binary", help="library for --dump (a stub .so is fine)")
    ap.add_argument(
        "-H", "--header", action="append", default=[], help="public header for --dump"
    )
    ap.add_argument("--markdown", action="store_true")
    args = ap.parse_args(argv)
    result = measure(args)
    print(json.dumps(result, indent=2))
    if args.markdown:
        print(_markdown(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
