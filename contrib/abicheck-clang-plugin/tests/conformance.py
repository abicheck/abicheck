#!/usr/bin/env python3
# Copyright 2026 Nikolay Petrov
#
# Licensed under the Apache License, Version 2.0 (the "License").
#
# ADR-038 C.6 differential-conformance test for the abicheck Clang facts plugin.
#
# The plugin is correct **iff** it is a drop-in for the clang backend. This
# compiles one fixture TU two ways with the SAME clang version —
#   1. with the plugin (`-fplugin`, `public-roots=include`), and
#   2. with the `abicheck-cc` wrapper pinned to the clang extractor
#      (`ABICHECK_CC_EXTRACTOR=clang`, `ABICHECK_CC_HEADERS=include`) —
# then ingests both `abicheck_inputs/` packs and asserts the two public
# surfaces are entity-equivalent: equal sets keyed by `SourceEntity.identity()`
# with equal signature/type/body hashes, values, and visibility.
#
# Non-macro entities (functions, inline bodies, records, enums, typedefs,
# templates, constexpr) are compared **strictly** — any difference fails.
# Macros are compared by name strictly but by value **leniently** (a mismatch
# warns, not fails): operator-adjacent spacing of function-like macros is the one
# documented soft edge (ADR-038 C.7), reconciled here rather than gated.
#
# Runs only where a matching clang is available; it is never a required
# abicheck-CI gate (it lives in the separate `clang-plugin` workflow).

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"

# Every entity kind is compared strictly except macros, whose *values* are
# compared leniently (see _compare). Using an exclusion set rather than an
# allowlist means new SourceEntity kinds (union, variable, …) are covered by
# default instead of being silently dropped.
LENIENT_KINDS = {"macro"}
COMPARED_FIELDS = (
    "signature_hash",
    "type_hash",
    "body_hash",
    "value",
    "visibility",
    "api_relevant",
)


def _load_entities(pack_dir: Path) -> dict[tuple[str, str], object]:
    """Fold a pack's per-TU dumps into a {(kind, identity): entity} map."""
    from abicheck.buildsource.inputs_pack import load_inputs_manifest, read_source_facts

    manifest = load_inputs_manifest(pack_dir)
    tus = read_source_facts(pack_dir, manifest)
    out: dict[tuple[str, str], object] = {}
    for tu in tus:
        for e in tu.all_entities():
            out[(e.kind, e.identity())] = e
    return out


def _run(cmd: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> None:
    print("+ " + " ".join(cmd), flush=True)
    # Bound each invocation so a hung clang++/abicheck-cc (e.g. a plugin crash
    # loop) fails the job fast instead of blocking CI.
    subprocess.run(cmd, cwd=str(cwd), env=env, check=True, timeout=300)


def _compile_with_plugin(work: Path, plugin: Path, clangxx: str) -> Path:
    out = work / "out_plugin"
    argp = ["-Xclang", "-plugin-arg-abicheck-facts", "-Xclang"]
    _run(
        [
            clangxx,
            "-std=c++17",
            "-Iinclude",
            f"-fplugin={plugin}",
            *argp,
            f"out={out}",
            *argp,
            "public-roots=include",
            *argp,
            "library=widget",
            "-c",
            "widget.cpp",
            "-o",
            str(work / "widget_plugin.o"),
        ],
        cwd=work,
    )
    return out


def _compile_with_wrapper(work: Path, clangxx: str) -> Path:
    out = work / "out_wrapper"
    env = dict(os.environ)
    env.update(
        ABICHECK_INPUTS_DIR=str(out),
        ABICHECK_CC_EXTRACTOR="clang",
        ABICHECK_CC_HEADERS="include",
        ABICHECK_CC_LIBRARY="widget",
    )
    _run(
        [
            "abicheck-cc",
            clangxx,
            "-std=c++17",
            "-Iinclude",
            "-c",
            "widget.cpp",
            "-o",
            str(work / "widget_wrapper.o"),
        ],
        cwd=work,
        env=env,
    )
    return out


def _compare(plugin: dict, wrapper: dict) -> tuple[list[str], list[str]]:
    """Return (errors, warnings). Strict for non-macro entities; lenient macros."""
    errors: list[str] = []
    warnings: list[str] = []

    def _strict(d: dict) -> set:
        return {k for k in d if k[0] not in LENIENT_KINDS}

    pk, wk = _strict(plugin), _strict(wrapper)
    for missing in sorted(wk - pk):
        errors.append(f"plugin is MISSING entity present in clang backend: {missing}")
    for extra in sorted(pk - wk):
        errors.append(f"plugin emitted EXTRA entity absent from clang backend: {extra}")
    for key in sorted(pk & wk):
        pe, we = plugin[key], wrapper[key]
        for f in COMPARED_FIELDS:
            if getattr(pe, f) != getattr(we, f):
                errors.append(
                    f"{key} field '{f}' differs:\n"
                    f"    plugin : {getattr(pe, f)!r}\n"
                    f"    clang  : {getattr(we, f)!r}"
                )

    # Macros: names strict, values lenient.
    pm = {k[1] for k in plugin if k[0] == "macro"}
    wm = {k[1] for k in wrapper if k[0] == "macro"}
    for missing in sorted(wm - pm):
        errors.append(f"plugin is MISSING macro present in clang backend: {missing}")
    for extra in sorted(pm - wm):
        errors.append(f"plugin emitted EXTRA macro absent from clang backend: {extra}")
    for name in sorted(pm & wm):
        pe = plugin[("macro", name)]
        we = wrapper[("macro", name)]
        if pe.value != we.value:
            warnings.append(
                f"macro {name!r} value differs (soft, ADR-038 C.7):\n"
                f"    plugin : {pe.value!r}\n"
                f"    clang  : {we.value!r}"
            )
    return errors, warnings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--plugin", required=True, help="path to libabicheck-facts.so")
    ap.add_argument("--clangxx", default="clang++", help="clang++ to compile with")
    ap.add_argument("--work", default=None, help="work dir (default: a temp dir)")
    ap.add_argument("--keep", action="store_true", help="keep the work dir")
    args = ap.parse_args(argv)

    plugin = Path(args.plugin).resolve()
    if not plugin.is_file():
        print(f"error: plugin not found: {plugin}", file=sys.stderr)
        return 2

    # Only auto-created temp dirs are ours to delete; a caller-supplied --work
    # is owned by the caller and must never be rmtree'd (Codex review).
    created_tmp = args.work is None
    work = Path(args.work).resolve() if args.work else Path(tempfile.mkdtemp(prefix="abicheck-c6-"))
    work.mkdir(parents=True, exist_ok=True)
    shutil.copytree(FIXTURES / "include", work / "include", dirs_exist_ok=True)
    shutil.copyfile(FIXTURES / "widget.cpp", work / "widget.cpp")

    try:
        plugin_pack = _compile_with_plugin(work, plugin, args.clangxx)
        wrapper_pack = _compile_with_wrapper(work, args.clangxx)

        plugin_ents = _load_entities(plugin_pack)
        wrapper_ents = _load_entities(wrapper_pack)
        print(
            f"\nplugin emitted {len(plugin_ents)} entities; "
            f"clang backend emitted {len(wrapper_ents)}",
            flush=True,
        )

        errors, warnings = _compare(plugin_ents, wrapper_ents)
        for w in warnings:
            print(f"::warning::{w}")
        if errors:
            print("\nC.6 CONFORMANCE FAILED:\n", file=sys.stderr)
            for e in errors:
                print(f"  - {e}", file=sys.stderr)
            return 1
        print("\nC.6 conformance PASSED: plugin surface is entity-equivalent "
              "to the clang backend.")
        return 0
    finally:
        if created_tmp and not args.keep:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
