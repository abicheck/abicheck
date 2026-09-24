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
import subprocess  # nosec B404 - runs the compilers being measured
import sys
import tempfile
import time
import xml.etree.ElementTree as ET  # nosec B405 - parses castxml output this script wrote
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
    stack = _seed_ids(root, target_roots)
    seeds = len(stack)
    keep: set[str] = set()
    while stack:
        eid = stack.pop()
        if eid in keep or eid not in by_id:
            continue
        keep.add(eid)
        stack.extend(_closure_refs(by_id[eid]))
    keep.update(_implied_ids(root, keep))
    return keep, seeds


def _seed_ids(root: ET.Element, target_roots: list[str]) -> list[str]:
    """Every element declared in a file under one of *target_roots*."""
    owned_files = {
        el.get("id", "")
        for el in root
        if el.tag == "File"
        and any(under_root(el.get("name", ""), r) for r in target_roots)
    }
    return [el.get("id", "") for el in root if el.get("file") in owned_files]


def _closure_refs(el: ET.Element) -> Iterable[str]:
    """What keeping *el* requires: a namespace needs only its parent."""
    if el.tag == "Namespace":
        parent = el.get("context")
        return [parent] if parent else []
    return element_refs(el)


def _implied_ids(root: ET.Element, keep: set[str]) -> Iterable[str]:
    """Files, and the cv-qualified variant of every kept type (castxml gives
    it its own element)."""
    for el in root:
        if el.tag == "File" or (
            el.tag == "CvQualifiedType" and _strip_cv(el.get("type", "")) in keep
        ):
            yield el.get("id", "")


def prune_to(root: ET.Element, keep: set[str]) -> None:
    """Drop every element not in *keep*, in one pass."""
    root[:] = [el for el in root if el.get("id", "") in keep]


# ── process measurement ───────────────────────────────────────────────────


def _wait_for_peak_rss_mb(proc: subprocess.Popen[bytes]) -> int | None:
    """Reap *proc*, setting its return code, and return its own peak RSS in
    MB -- or ``None`` where the platform cannot report one (no ``os.wait4``
    on Windows). Unknown is never reported as zero."""
    wait4 = getattr(os, "wait4", None)
    if wait4 is None:
        proc.wait()
        return None
    _, status, usage = wait4(proc.pid, 0)
    proc.returncode = os.waitstatus_to_exitcode(status)
    # ru_maxrss is kilobytes on Linux and bytes on macOS.
    per_mb = 1 << 20 if sys.platform == "darwin" else 1 << 10
    return round(usage.ru_maxrss / per_mb)


def run_measured(argv: list[str]) -> dict[str, object]:
    """Run *argv*; wall time, the child's own peak RSS, and stdout bytes.

    Stdout is counted and discarded chunk by chunk, so a multi-GB clang JSON
    never has to fit in this process.
    """
    start = time.monotonic()
    # stderr goes to a file, never a second pipe: only stdout is drained
    # below, and a child that fills an undrained stderr pipe (castxml's
    # warnings do) blocks forever.
    with tempfile.TemporaryFile() as errfile:
        # argv is built by this script from its own arguments, never a shell.
        with subprocess.Popen(  # nosec B603
            argv, stdout=subprocess.PIPE, stderr=errfile
        ) as proc:
            stdout = proc.stdout
            if stdout is None:  # Popen(stdout=PIPE) always sets it
                raise RuntimeError("no stdout pipe")
            written = sum(
                len(chunk) for chunk in iter(lambda: stdout.read(1 << 20), b"")
            )
            peak_rss_mb = _wait_for_peak_rss_mb(proc)
        errfile.seek(0)
        stderr = errfile.read()
    status = proc.returncode
    return {
        "returncode": proc.returncode,
        "seconds": round(time.monotonic() - start, 1),
        "peak_rss_mb": peak_rss_mb,
        "stdout_mb": round(written / 1e6, 1),
        "stderr_tail": stderr.decode(errors="replace")[-400:] if status else "",
    }


# ── owned-declaration accounting ──────────────────────────────────────────


def under_root(path: str, root: str) -> bool:
    """Whether *path* is *root* or inside it -- ``/proj/include-private`` is
    not under ``/proj/include``."""
    root = root.rstrip("/\\")
    return path == root or path.startswith((root + "/", root + "\\"))


def _owner(path: str, owners: list[tuple[str, str]]) -> str:
    for name, prefix in owners:
        if under_root(path, prefix):
            return name
    return "other"


def owned_counts(xml_path: Path, owners: list[tuple[str, str]]) -> dict[str, object]:
    """Functions and types per owner, via abicheck's own castxml parser."""
    from abicheck.dumper_castxml import _CastxmlParser

    root = ET.parse(xml_path).getroot()  # nosec B314 - local castxml output
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

    def owned(item: object) -> bool:
        return _owner(getattr(item, "source_location", None) or "", owners) == target

    out["target_keys"] = sorted(
        f"f:{f.mangled or f.name}" for f in functions if owned(f)
    ) + sorted(f"t:{t.name}@{t.source_location}" for t in types if owned(t))
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
    result["clang_full"] = run_measured(clang)
    filtered = clang[:-1] + ["-Xclang", f"-ast-dump-filter={args.start}::", tu]
    result["clang_filter"] = run_measured(filtered)

    xml_full = work / "full.xml"
    base = [castxml, "--castxml-output=1", *emulate, *flags]
    result["castxml_full"] = _run_castxml([*base, tu], xml_full)
    xml_start = work / "start.xml"
    result["castxml_start"] = _run_castxml(
        [*base, "--castxml-start", args.start, tu], xml_start
    )

    # The closure runs in its own process so its time and peak RSS are
    # measured the same way as the frontends'.
    xml_closure = work / "closure.xml"
    seeds_file = work / "closure-seeds.json"
    for stale in (xml_closure, seeds_file):
        stale.unlink(missing_ok=True)
    closure_argv = [sys.executable, str(Path(__file__).resolve()), "--closure-only"]
    closure_argv += [str(xml_full), str(xml_closure), owners[0][1], str(seeds_file)]
    closure = run_measured(closure_argv)
    if closure["returncode"] != 0:
        raise RuntimeError(f"closure failed: {closure['stderr_tail']}")
    closure["seeds"] = json.loads(seeds_file.read_text())["seeds"]
    closure["xml_mb"] = round(xml_closure.stat().st_size / 1e6, 1)
    result["closure"] = closure

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


def _run_castxml(argv: list[str], out: Path) -> dict[str, object]:
    """Run castxml into a fresh *out*; a failed run is an error, never a
    measurement of whatever an earlier run left behind."""
    out.unlink(missing_ok=True)
    measured = run_measured([*argv, "-o", str(out)])
    if measured["returncode"] != 0 or not out.exists():
        raise RuntimeError(f"castxml failed: {measured['stderr_tail']}")
    measured["xml_mb"] = round(out.stat().st_size / 1e6, 1)
    return measured


def _closure_only(xml_in: str, xml_out: str, root: str, seeds_out: str) -> int:
    """Child-process entry point for the measured closure step."""
    tree = ET.parse(xml_in)  # nosec B314 - local castxml output
    keep, seeds = ownership_closure(tree.getroot(), [root])
    prune_to(tree.getroot(), keep)
    tree.write(xml_out)
    Path(seeds_out).write_text(json.dumps({"seeds": seeds}))
    return 0


def _dump_config(flags: list[str], frontend: str) -> str:
    """The ``.abicheck.yml`` text reproducing *flags* for *frontend*."""
    lines = ["compile:", f"  frontend: {frontend}"]
    std = [f.split("=", 1)[1] for f in flags if f.startswith("-std=")]
    if std:
        lines.append(f"  std: {std[-1]}")
    lines += _yaml_list("include_dirs", _stripped(flags, "-I"))
    lines += _yaml_list("defines", _stripped(flags, "-D"))
    lines += _yaml_list("options", [f for f in flags if f.startswith(("-f", "-m"))])
    return "\n".join(lines) + "\n"


def _stripped(flags: list[str], prefix: str) -> list[str]:
    """Operands of *prefix* in both spellings: ``-Idir`` and ``-I dir``."""
    values: list[str] = []
    operand_next = False
    for flag in flags:
        if operand_next:
            values.append(flag)
            operand_next = False
        elif flag == prefix:
            operand_next = True
        elif flag.startswith(prefix):
            values.append(flag[len(prefix) :])
    return values


def _yaml_list(key: str, values: list[str]) -> list[str]:
    return [f"  {key}:", *(f"    - {v}" for v in values)] if values else []


def _dump(args: argparse.Namespace, frontend: str, work: Path) -> dict[str, object]:
    config = work / f"abicheck-{frontend}.yml"
    config.write_text(_dump_config(shlex.split(args.flags), frontend))
    out = work / f"snapshot-{frontend}.json"
    argv = [sys.executable, "-m", "abicheck", "dump", args.binary]
    argv += ["--config", str(config)]
    for header in args.header:
        argv += ["-H", header]
    env_cache = work / f"cache-{frontend}"
    shutil.rmtree(env_cache, ignore_errors=True)
    os.environ["ABICHECK_CACHE_DIR"] = str(env_cache)
    measured = run_measured([*argv, "-o", str(out)])
    if out.exists():
        measured.update(_read_back(out, args.owner))
    return measured


def _read_back(out: Path, owner_args: list[str]) -> dict[str, object]:
    from abicheck.errors import AbicheckError

    size = out.stat().st_size
    result: dict[str, object] = {
        "snapshot_mb": round(size / 1e6, 1),
        # A default dump can write a snapshot larger than the default
        # reader accepts; _snapshot_summary reads it directly regardless.
        "exceeds_default_read_limit": size > (1 << 30),
    }
    owners = [tuple(o.split("=", 1)) for o in owner_args]
    try:
        result.update(_snapshot_summary(out, owners))
    except (OSError, ValueError, AbicheckError) as exc:  # record and go on
        result["summary_error"] = f"{type(exc).__name__}: {exc}"[:300]
    return result


def _snapshot_summary(path: Path, owners: list[tuple[str, str]]) -> dict[str, object]:
    from abicheck.serialization import snapshot_from_dict

    # One parse serves both the section sizes and the snapshot. Reading the
    # file directly also bypasses the default snapshot size limit, which a
    # benchmark must not trip over (M4 records when a dump exceeds it).
    with open(path, encoding="utf-8") as fh:
        document = json.load(fh)
    section_mb = {
        k: round(len(json.dumps(v)) / 1e6, 1)
        for k, v in document.get("sections", {}).items()
    }
    snap = snapshot_from_dict(document)
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
        rss = r.get("peak_rss_mb")  # type: ignore[union-attr]
        rss = "—" if rss is None else rss
        rows.append(f"| {key} | {r['seconds']} s | {rss} MB | {size} MB | {lost} |")  # type: ignore[index]
    return "\n".join(rows)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--tu", help="translation unit of #include lines")
    ap.add_argument("--flags", default="", help="compile flags, one shell string")
    ap.add_argument("--start", help="target namespace for the name filters")
    ap.add_argument(
        "--owner",
        action="append",
        help="NAME=PATH_PREFIX; the first one is the target, the rest named dependencies",
    )
    ap.add_argument("--work", help="scratch directory for XML and snapshots")
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
    ap.add_argument("--closure-only", nargs=4, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    if args.closure_only:
        return _closure_only(*args.closure_only)
    missing = [
        f"--{n}" for n in ("tu", "start", "owner", "work") if not getattr(args, n)
    ]
    if missing:
        ap.error(f"required: {', '.join(missing)}")
    result = measure(args)
    print(_markdown(result) if args.markdown else json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
