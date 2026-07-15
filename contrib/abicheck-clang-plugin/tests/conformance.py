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
# Side-channel comparisons (latest-main Clang plugin review, PR1b/PR2/PR4):
# the entity comparison above proves the existing entity contract; it says
# nothing about the newer source_edges/read_files/fact_set/coverage channels.
# Extended here, each scoped to what can be asserted without a hand-verified
# oracle for every AST-implicit declaration the two producers might walk
# differently:
#   - source_edges: the two overload-resolved DECL_CALLS_DECL edges the
#     `overload(int)`/`overload(double)` fixture exists to exercise are
#     required from BOTH producers (errors if missing); any additional
#     divergence elsewhere in the edge set is reported as informational only
#     (ADR-041 P1, a broader concern than the overload-identity regression
#     this fixture targets).
#   - read_files: both producers must have read the fixture's own primary
#     source and header (a required subset, not exact equality — the two
#     collection mechanisms are not guaranteed to enumerate transitively-
#     included system headers identically).
#   - fact_set: both producers must declare the same canonical name/version/
#     compiler_family (the "same fact-set semantic recipe" ADR-038 C.8 keys
#     comparison-compatibility on).
#   - coverage: neither producer should report partial/failed for a
#     mandatory family on this clean fixture (informational warning, since a
#     real future regression here is exactly what this extension exists to
#     catch, but the exact expected states are not hand-verified per-family).
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


def _load_tus(pack_dir: Path) -> list:
    """Load a pack's per-TU dumps (``SourceAbiTu`` records) as-is."""
    from abicheck.buildsource.inputs_pack import load_inputs_manifest, read_source_facts

    manifest = load_inputs_manifest(pack_dir)
    return read_source_facts(pack_dir, manifest)


def _load_entities(tus: list) -> dict[tuple[str, str], object]:
    """Fold a pack's per-TU dumps into a {(kind, identity): entity} map."""
    out: dict[tuple[str, str], object] = {}
    for tu in tus:
        for e in tu.all_entities():
            out[(e.kind, e.identity())] = e
    return out


#: DECL_CALLS_DECL edges the overload regression fixture (widget.cpp/hpp's
#: overload(int)/overload(double)/callOverloadInt/callOverloadDouble) must
#: produce identically from both producers -- the PR1b regression target:
#: clang's compact `referencedDecl` carries no `mangledName` (verified against
#: a real Clang 17/18 JSON AST dump), so a naive resolver collapses both
#: overloads onto one bare-name endpoint instead of their distinct mangled
#: identities.
_REQUIRED_CALL_EDGES: frozenset[tuple[str, str, str]] = frozenset(
    {
        (
            "DECL_CALLS_DECL",
            "_ZN4demo15callOverloadIntEv",
            "_ZN4demo8overloadEi",
        ),
        (
            "DECL_CALLS_DECL",
            "_ZN4demo18callOverloadDoubleEv",
            "_ZN4demo8overloadEd",
        ),
    }
)


def _edge_keys(tus: list) -> set[tuple[str, str, str]]:
    keys: set[tuple[str, str, str]] = set()
    for tu in tus:
        for row in tu.source_edges:
            if not isinstance(row, dict):
                continue
            keys.add(
                (str(row.get("edge", "")), str(row.get("src", "")), str(row.get("dst", "")))
            )
    return keys


def _compare_source_edges(
    plugin_tus: list, wrapper_tus: list
) -> tuple[list[str], list[str]]:
    """Required overload-resolved edges (errors) + informational-only extras."""
    errors: list[str] = []
    warnings: list[str] = []
    plugin_edges = _edge_keys(plugin_tus)
    wrapper_edges = _edge_keys(wrapper_tus)
    for key in sorted(_REQUIRED_CALL_EDGES):
        if key not in plugin_edges:
            errors.append(
                f"plugin source_edges is missing required overload-resolved "
                f"edge {key} -- does the plugin still resolve callees by "
                "mangled identity?"
            )
        if key not in wrapper_edges:
            errors.append(
                f"clang-backend source_edges is missing required "
                f"overload-resolved edge {key} -- PR1b referencedDecl "
                "id-index resolution regression?"
            )
    # Any OTHER divergence (implicit/builtin declarations the two producers
    # are not guaranteed to walk identically -- e.g. compiler-injected
    # __int128_t-style typedefs) is reported but not gated: reconciling that
    # is the broader ADR-041 P1 concern, not the overload-identity regression
    # this fixture targets.
    extra_plugin = sorted(plugin_edges - wrapper_edges - _REQUIRED_CALL_EDGES)
    extra_wrapper = sorted(wrapper_edges - plugin_edges - _REQUIRED_CALL_EDGES)
    if extra_plugin:
        warnings.append(
            f"plugin source_edges has {len(extra_plugin)} edge(s) absent from "
            f"the clang backend (informational, not gated): {extra_plugin[:5]}"
        )
    if extra_wrapper:
        warnings.append(
            f"clang-backend source_edges has {len(extra_wrapper)} edge(s) "
            f"absent from the plugin (informational, not gated): {extra_wrapper[:5]}"
        )
    return errors, warnings


#: The fixture's own primary files -- both producers must have read at least
#: these (a required subset, not exact read_files equality).
_REQUIRED_READ_FILES = frozenset({"widget.cpp", "widget.hpp"})


def _compare_read_files(plugin_tus: list, wrapper_tus: list) -> list[str]:
    def _basenames(tus: list) -> set[str]:
        return {Path(f).name for tu in tus for f in tu.read_files}

    errors: list[str] = []
    plugin_files = _basenames(plugin_tus)
    wrapper_files = _basenames(wrapper_tus)
    for name in sorted(_REQUIRED_READ_FILES):
        if name not in plugin_files:
            errors.append(f"plugin read_files is missing required file {name!r}")
        if name not in wrapper_files:
            errors.append(f"clang-backend read_files is missing required file {name!r}")
    return errors


def _compare_fact_set(plugin_tus: list, wrapper_tus: list) -> list[str]:
    from abicheck.buildsource.source_abi import (
        SOURCE_ABI_FACT_SET_NAME,
        SOURCE_ABI_FACT_SET_VERSION,
    )

    errors: list[str] = []
    for label, tus in (("plugin", plugin_tus), ("clang backend", wrapper_tus)):
        for tu in tus:
            if not tu.fact_set:
                errors.append(f"{label} TU {tu.tu_id} carries no fact_set identity")
                continue
            name = tu.fact_set.get("name")
            if name != SOURCE_ABI_FACT_SET_NAME:
                errors.append(
                    f"{label} TU {tu.tu_id} fact_set.name={name!r}, expected "
                    f"{SOURCE_ABI_FACT_SET_NAME!r}"
                )
            version = tu.fact_set.get("version")
            if version != SOURCE_ABI_FACT_SET_VERSION:
                errors.append(
                    f"{label} TU {tu.tu_id} fact_set.version={version!r}, expected "
                    f"{SOURCE_ABI_FACT_SET_VERSION!r}"
                )
            family = tu.fact_set.get("compiler_family")
            if family != "clang":
                errors.append(
                    f"{label} TU {tu.tu_id} fact_set.compiler_family={family!r}, "
                    "expected 'clang'"
                )
    return errors


def _compare_coverage(plugin_tus: list, wrapper_tus: list) -> list[str]:
    from abicheck.buildsource.source_abi import (
        FACT_FAMILIES,
        INCOMPLETE_COVERAGE_STATES,
    )

    warnings: list[str] = []
    for label, tus in (("plugin", plugin_tus), ("clang backend", wrapper_tus)):
        for tu in tus:
            if not tu.fact_set:
                continue
            for family in FACT_FAMILIES:
                state = tu.coverage.get(family)
                if state in INCOMPLETE_COVERAGE_STATES:
                    warnings.append(
                        f"{label} TU {tu.tu_id} family {family!r} reported "
                        f"{state!r} coverage on the C.6 fixture"
                    )
    return warnings


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

        plugin_tus = _load_tus(plugin_pack)
        wrapper_tus = _load_tus(wrapper_pack)
        plugin_ents = _load_entities(plugin_tus)
        wrapper_ents = _load_entities(wrapper_tus)
        print(
            f"\nplugin emitted {len(plugin_ents)} entities; "
            f"clang backend emitted {len(wrapper_ents)}",
            flush=True,
        )

        errors, warnings = _compare(plugin_ents, wrapper_ents)

        # Side-channel comparisons (PR1b/PR2/PR4): source_edges, read_files,
        # fact_set, and coverage -- none of which the entity comparison above
        # touches at all (see module docstring).
        edge_errors, edge_warnings = _compare_source_edges(plugin_tus, wrapper_tus)
        errors.extend(edge_errors)
        warnings.extend(edge_warnings)
        errors.extend(_compare_read_files(plugin_tus, wrapper_tus))
        errors.extend(_compare_fact_set(plugin_tus, wrapper_tus))
        warnings.extend(_compare_coverage(plugin_tus, wrapper_tus))

        for w in warnings:
            print(f"::warning::{w}")
        if errors:
            print("\nC.6 CONFORMANCE FAILED:\n", file=sys.stderr)
            for e in errors:
                print(f"  - {e}", file=sys.stderr)
            return 1
        print("\nC.6 conformance PASSED: plugin surface is entity-equivalent "
              "to the clang backend (entities, source_edges, read_files, "
              "fact_set).")
        return 0
    finally:
        if created_tmp and not args.keep:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
