#!/usr/bin/env python3
# Copyright 2026 Nikolay Petrov
#
# Licensed under the Apache License, Version 2.0 (the "License").
#
# ADR-038 Plugin injection end-to-end scan validation for the abicheck Clang facts plugin.
#
# The C.6 conformance test (`conformance.py`) proves the plugin's emitted
# entities equal the clang backend's. This test proves the complementary thing:
# a pack the plugin drops during a *real build* is actually consumable by the
# ordinary abicheck scan pipeline — i.e. the documented Plugin-injection user flow works,
# not just the entity comparison. It:
#   1. compiles the fixture TU into a shared library WITH the plugin active, so
#      the same build both links the .so and drops `abicheck_inputs/` beside it;
#   2. `abicheck dump`s the .so with `--build-info ./abicheck_inputs/` (L0/L1
#      binary facts — no `-H`, so no castxml dependency in the plugin CI
#      lane), which auto-detects and folds the plugin-emitted pack into the
#      baseline in the same step (the real ingest path, no re-parse — the
#      standalone `collect`/`merge` commands this used to go through were
#      removed with no replacement in the ADR-043 CLI reset; `dump
#      --build-info`/`--sources` now does the same auto-detect-and-fold
#      inline) and asserts the L4 source-ABI + L5 graph layers were folded in
#      with a non-empty set of source entities, AND that the specific
#      DECL_CALLS_DECL edges the fixture's overload(int)/overload(double)
#      calls produce are actually present in the embedded L5 graph
#      (latest-main Clang plugin review, PR1: previously only L4 entity
#      counts were asserted here, so this test could pass even if
#      source_edges never reached the graph at all — the exact gap that
#      review found);
#   3. `abicheck compare`s the resulting baseline against itself and asserts a
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

#: Source-entity list keys inside the *embedded* build_source payload. link_source_abi
#: folds functions/methods/variables/constexpr into `declarations`, so the embedded
#: SourceAbiSurface has exactly these five buckets — NOT the raw per-TU `functions`/
#: `constexpr_values` names. Getting these wrong makes the folded>0 gate silently
#: ignore a declarations-only (e.g. C, function-only) surface and fail spuriously.
_ENTITY_LIST_KEYS = frozenset(
    {"declarations", "types", "templates", "inline_bodies", "macros"}
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


#: The embedded L5 graph edges the fixture's overload(int)/overload(double)
#: calls (widget.cpp/hpp) must produce -- source_graph.fold_source_edges maps
#: a DECL_CALLS_DECL row's raw src/dst identity onto a `decl://<identity>`
#: graph node id (the same scheme call_graph.augment_graph_with_calls uses).
#: Asserting these are present in the *graph*, not merely that L4 entity
#: counts are non-zero, is what the latest-main Clang plugin review's PR1
#: finding asked for: "coverage['source_edges'] == 'complete' describes
#: collection success, not end-to-end availability."
_REQUIRED_GRAPH_EDGES = frozenset(
    {
        (
            "DECL_CALLS_DECL",
            "decl://_ZN4demo15callOverloadIntEv",
            "decl://_ZN4demo8overloadEi",
        ),
        (
            "DECL_CALLS_DECL",
            "decl://_ZN4demo18callOverloadDoubleEv",
            "decl://_ZN4demo8overloadEd",
        ),
    }
)


def _graph_edge_keys(build_source: dict) -> set[tuple[str, str, str]]:
    edges = (build_source.get("source_graph") or {}).get("edges") or []
    keys: set[tuple[str, str, str]] = set()
    for e in edges:
        if not isinstance(e, dict):
            continue
        keys.add((str(e.get("edge", "")), str(e.get("src", "")), str(e.get("dst", ""))))
    return keys


def _compile_shared_lib_with_plugin(work: Path, plugin: Path, clangxx: str) -> Path:
    """Build the fixture TU into a .so with the plugin active; return the .so."""
    shutil.copytree(FIXTURES, work / "fx", dirs_exist_ok=True)
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

    # Only auto-created temp dirs are ours to delete; a caller-supplied --work
    # is owned by the caller and must never be rmtree'd (Codex review).
    created_tmp = args.work is None
    work = Path(args.work).resolve() if args.work else Path(
        tempfile.mkdtemp(prefix="abicheck-scan-")
    )
    work.mkdir(parents=True, exist_ok=True)
    src = work / "fx"

    try:
        so = _compile_shared_lib_with_plugin(work, plugin, args.clangxx)

        # 2. Binary facts (L0/L1, no -H so the lane needs no castxml) with the
        # plugin-emitted pack folded in inline (real ingest, no re-parse).
        dump_out = _run(
            [
                "abicheck", "dump", str(so), "--build-info", "./abicheck_inputs/",
                "-o", "widget.baseline.json",
            ],
            cwd=src,
        )
        print(dump_out, end="")
        baseline = json.loads((src / "widget.baseline.json").read_text())
        build_source = baseline.get("build_source") or {}
        if not build_source:
            raise SystemExit("merged baseline has no embedded build_source payload")
        folded = _count_entities(build_source)
        if folded <= 0:
            raise SystemExit("merged baseline folded zero source entities from the pack")

        # PR1: a non-empty L4 entity count alone does not prove source_edges
        # ever reached the L5 graph -- assert the specific edges the
        # fixture's overload calls must produce are actually present.
        graph_edges = _graph_edge_keys(build_source)
        missing_edges = sorted(_REQUIRED_GRAPH_EDGES - graph_edges)
        if missing_edges:
            raise SystemExit(
                "merged baseline's embedded L5 graph is missing required "
                f"DECL_CALLS_DECL edge(s) from source_edges: {missing_edges}"
            )

        # 3. The baseline must be a valid comparison input (self-compare
        #    is compatible → exit 0 under the legacy verdict scheme).
        _run(
            ["abicheck", "compare", "widget.baseline.json", "widget.baseline.json"],
            cwd=src,
        )
        print(
            f"\nPlugin injection scan validation PASSED: plugin pack ingested via "
            f"dump --build-info ({folded} source entities folded, "
            f"{len(graph_edges)} L5 graph edges including the required "
            "DECL_CALLS_DECL overload edges) and the baseline compares clean."
        )
        return 0
    finally:
        if created_tmp and not args.keep:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
