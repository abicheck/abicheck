#!/usr/bin/env python3
# Copyright 2026 Nikolay Petrov
#
# Licensed under the Apache License, Version 2.0 (the "License").
#
# ADR-038 Flow C end-to-end scan validation for the abicheck Clang facts plugin.
#
# The C.6 conformance test (`conformance.py`) proves the plugin's emitted
# entities equal the clang backend's. This test proves the complementary thing:
# a pack the plugin drops during a *real build* is actually consumable by the
# ordinary abicheck scan pipeline — i.e. the documented Flow C user flow works,
# not just the entity comparison. It:
#   1. compiles the fixture TU into a shared library WITH the plugin active, so
#      the same build both links the .so and drops `abicheck_inputs/` beside it;
#   2. `abicheck dump`s the .so (L0/L1 binary facts — no `-H`, so no castxml
#      dependency in the plugin CI lane);
#   3. `abicheck merge`s the plugin-emitted pack into the binary baseline (the
#      real ingest path, no re-parse) and asserts the L4 source-ABI + L5 graph
#      layers were folded in with a non-empty set of source entities;
#   4. `abicheck compare`s the merged baseline against itself and asserts a
#      clean (exit 0) verdict — proving the folded baseline is a valid, stable
#      comparison input.
#
# Runs only where a matching clang is available; it is never a required
# abicheck-CI gate (it lives in the separate `clang-plugin` workflow).

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"

#: Source-entity list keys inside the embedded build_source payload; their total
#: length is the count of source facts the plugin pack contributed to the merge.
_ENTITY_LIST_KEYS = frozenset(
    {"functions", "types", "templates", "inline_bodies", "constexpr_values", "macros"}
)


def _run(cmd: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> str:
    print("+ " + " ".join(cmd), flush=True)
    proc = subprocess.run(
        cmd, cwd=str(cwd), env=env, timeout=300, capture_output=True, text=True
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        raise SystemExit(f"command failed ({proc.returncode}): {' '.join(cmd)}")
    return proc.stdout


def _count_entities(obj: object) -> int:
    """Total source entities anywhere inside the embedded build_source payload."""
    total = 0
    if isinstance(obj, dict):
        for key, val in obj.items():
            if key in _ENTITY_LIST_KEYS and isinstance(val, list):
                total += len(val)
            total += _count_entities(val)
    elif isinstance(obj, list):
        for item in obj:
            total += _count_entities(item)
    return total


def _compile_shared_lib_with_plugin(work: Path, plugin: Path, clangxx: str) -> Path:
    """Build the fixture TU into a .so with the plugin active; return the .so."""
    shutil.copytree(FIXTURES, work / "fx")
    src = work / "fx"
    so = work / "libwidget.so"
    _run(
        [
            clangxx,
            "-std=c++17",
            "-fPIC",
            "-shared",
            "-Iinclude",
            f"-fplugin={plugin}",
            "-Xclang", "-plugin-arg-abicheck-facts", "-Xclang", "out=abicheck_inputs",
            "-Xclang", "-plugin-arg-abicheck-facts", "-Xclang", "public-roots=include",
            "-Xclang", "-plugin-arg-abicheck-facts", "-Xclang", "library=widget",
            "widget.cpp",
            "-o",
            str(so),
        ],
        cwd=src,
    )
    pack = src / "abicheck_inputs"
    if not (pack / "manifest.json").is_file():
        raise SystemExit(f"plugin emitted no pack at {pack}")
    return so


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--plugin", required=True, help="path to libabicheck-facts.so")
    ap.add_argument("--clangxx", default="clang++", help="clang++ to compile with")
    ap.add_argument("--work", default=None, help="work dir (default: a temp dir)")
    ap.add_argument("--keep", action="store_true", help="keep the work dir")
    args = ap.parse_args(argv)

    plugin = Path(args.plugin).resolve()
    if not plugin.is_file():
        raise SystemExit(f"plugin not found: {plugin}")

    work = Path(args.work).resolve() if args.work else Path(
        tempfile.mkdtemp(prefix="abicheck-scan-")
    )
    work.mkdir(parents=True, exist_ok=True)
    src = work / "fx"

    try:
        so = _compile_shared_lib_with_plugin(work, plugin, args.clangxx)

        # 2. Binary facts (L0/L1) — no -H, so the lane needs no castxml.
        _run(["abicheck", "dump", str(so), "-o", "widget.bin.json"], cwd=src)

        # 3. Fold the plugin pack into the baseline (real ingest, no re-parse).
        merge_out = _run(
            [
                "abicheck", "merge", "widget.bin.json", "./abicheck_inputs/",
                "-o", "widget.baseline.json",
            ],
            cwd=src,
        )
        print(merge_out, end="")
        baseline = json.loads((src / "widget.baseline.json").read_text())
        build_source = baseline.get("build_source") or {}
        if not build_source:
            raise SystemExit("merged baseline has no embedded build_source payload")
        folded = _count_entities(build_source)
        if folded <= 0:
            raise SystemExit("merged baseline folded zero source entities from the pack")

        # 4. The merged baseline must be a valid comparison input (self-compare
        #    is compatible → exit 0 under the legacy verdict scheme).
        _run(
            ["abicheck", "compare", "widget.baseline.json", "widget.baseline.json"],
            cwd=src,
        )
        print(
            f"\nFlow C scan validation PASSED: plugin pack ingested via merge "
            f"({folded} source entities folded) and the baseline compares clean."
        )
        return 0
    finally:
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
