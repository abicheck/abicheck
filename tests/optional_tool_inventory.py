# SPDX-License-Identifier: Apache-2.0
"""What abicheck looks up outside itself, and what a default install promises without it.

A default install is ``pip install abicheck`` plus the frontend the requested
depth names (castxml or clang, and the C/C++ compiler they drive). Everything
else abicheck may find on ``PATH``, import or read from the environment is
optional. The clang companion layout tool (``abicheck-clang-layout-tool`` /
``ABICHECK_CLANG_LAYOUT_TOOL``) was that kind of lookup, and for months it was
the only producer of clang record layout: without it, clang snapshots carried
no sizes or offsets, and no test noticed, because every test that looked at
layout either had DWARF to backfill it or asked castxml (see the 2026-11-04
entry in ``docs/contribute/archive/known-gaps-closed.md``).

This module is the ledger. Each :class:`ToolLookup` names one external
dependency, every source site that looks it up, its :class:`Role`, and the
default-install expectation: which fact families stay present without it at
each depth (``docs/learn/evidence-and-detectability.md``), which read as an
explicit gap (``NOT_COLLECTED``/unknown, ADR-028/063) instead, and the test
that proves it.

Two tests hold it to the code:

* ``tests/test_optional_tool_inventory_gate.py`` scans ``abicheck/`` for every
  lookup shape (``shutil.which``, ``find_*_bin``, ``ABICHECK_*`` environment
  names, literal argv programs, optional third-party imports) and fails when a
  site is not registered here, or a registered site no longer exists.
* ``tests/test_default_install_fact_invariants.py`` hides every
  :attr:`ToolLookup.binaries` and :attr:`ToolLookup.modules` of an
  :data:`Role.OPTIONAL_ENRICHMENT` or :data:`Role.INPUT_ACQUISITION` entry and
  every ``ABICHECK_*`` variable, dumps real libraries through the CLI with both
  header frontends, and checks the preserved families against a compiled
  ``sizeof``/``offsetof`` oracle.

Adding a lookup: add its site to an existing entry, or a new entry with a role,
an expectation, and (for optional enrichment) a proof in the invariant suite.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

#: The ``--depth`` values (``docs/learn/evidence-and-detectability.md``).
DEPTHS = frozenset({"binary", "headers", "build", "source"})


class Role(StrEnum):
    """Why abicheck looks the dependency up, which fixes what its absence may do."""

    #: The producer a depth is defined by (castxml, clang, the compiler they
    #: drive). Its absence fails the run or records the family FAILED; it never
    #: yields an empty surface. Not hidden by the invariant suite: hiding it is
    #: not a default install.
    REQUIRED_FRONTEND = "required_frontend"
    #: Adds or cross-checks facts. A default install without it still carries
    #: every family in :attr:`ToolLookup.preserved`; the families in
    #: :attr:`ToolLookup.marked` read as an explicit gap. Hidden by the
    #: invariant suite, which must name a proof.
    OPTIONAL_ENRICHMENT = "optional_enrichment"
    #: Reads one input format or build system (a package archive, a Bazel or
    #: Ninja graph). Without it that input is refused or its family is marked;
    #: it is never read as an empty input. Hidden by the invariant suite.
    INPUT_ACQUISITION = "input_acquisition"
    #: An environment switch that changes which facts are collected. Its unset
    #: default is the promised state; the invariant suite runs with every
    #: ``ABICHECK_*`` variable unset.
    FACT_SWITCH = "fact_switch"
    #: Tuning, caching, progress, provenance or diagnostics. Cannot change a
    #: recorded fact; :attr:`ToolLookup.without` says why.
    NO_FACT_EFFECT = "no_fact_effect"


@dataclass(frozen=True)
class ToolLookup:
    """One external dependency and the default-install promise around it.

    ``sites`` are the gate's keys: ``which:<path>:<argument source>``,
    ``find_bin:<path>:<function>``, ``argv:<path>:<program>``,
    ``import:<module>`` and ``env:<NAME>``.
    """

    id: str
    role: Role
    sites: tuple[str, ...]
    without: str
    depths: tuple[str, ...] = ()
    #: Executable names the invariant suite removes from ``PATH``.
    binaries: tuple[str, ...] = ()
    #: Python modules the invariant suite makes unimportable.
    modules: tuple[str, ...] = ()
    #: Fact families a default install still carries without it.
    preserved: tuple[str, ...] = ()
    #: Fact families that read NOT_COLLECTED/unknown without it.
    marked: tuple[str, ...] = ()
    #: ``tests/<file>.py::<test function>`` nodes that prove the expectation.
    proof: tuple[str, ...] = ()


_SUITE = "tests/test_default_install_fact_invariants.py"

#: The header fact families the invariant suite checks for both frontends.
HEADER_FAMILIES = (
    "record_size",
    "record_alignment",
    "field_offset",
    "vtable",
    "vptr_offset",
    "enum_values",
    "typedef_target",
    "explicit_instantiation_record",
    "builtin_abi_fact",
)

_F = Role.REQUIRED_FRONTEND
_O = Role.OPTIONAL_ENRICHMENT
_I = Role.INPUT_ACQUISITION
_S = Role.FACT_SWITCH
_N = Role.NO_FACT_EFFECT


INVENTORY: tuple[ToolLookup, ...] = (
    # --- optional enrichment -------------------------------------------------
    ToolLookup(
        id="clang-layout-tool",
        role=_O,
        sites=(
            "find_bin:abicheck/extract/headers/clang/layout_tool.py:find_layout_tool_bin",
            "which:abicheck/extract/headers/clang/layout_tool.py:_DEFAULT_BIN_NAME",
            "env:ABICHECK_CLANG_LAYOUT_TOOL",
        ),
        binaries=("abicheck-clang-layout-tool",),
        depths=("headers", "build", "source"),
        without=(
            "The built-in pass (extract/headers/clang/record_layout_dump.py) asks "
            "clang itself for every record still unsized after DWARF backfill, so a "
            "clang snapshot of a binary without DWARF carries sizes, alignment and "
            "offsets. A record clang cannot name with sizeof (private nested, "
            "unnamed) and a class-template pattern keep size_bits None (unknown), "
            "never 0."
        ),
        preserved=HEADER_FAMILIES + ("data_size",),
        proof=(
            f"{_SUITE}::test_header_facts_survive_without_optional_tools",
            f"{_SUITE}::test_c_header_facts_survive_without_optional_tools",
            f"{_SUITE}::test_optional_tools_do_not_change_header_facts",
        ),
    ),
    ToolLookup(
        id="cxxfilt-module",
        role=_O,
        sites=("import:cxxfilt",),
        modules=("cxxfilt",),
        depths=("binary", "headers", "build", "source"),
        without=(
            "Demangling falls back to c++filt, then to no demangling. Demangled "
            "spellings are a report-time convenience: no snapshot fact depends on "
            "them, so a dump is byte-identical in its facts with or without it."
        ),
        preserved=("exported_symbols", "debug_layout") + HEADER_FAMILIES,
        proof=(
            f"{_SUITE}::test_binary_facts_do_not_depend_on_optional_tools",
            f"{_SUITE}::test_optional_tools_do_not_change_header_facts",
        ),
    ),
    ToolLookup(
        id="c++filt",
        role=_O,
        sites=("argv:abicheck/demangle.py:c++filt",),
        binaries=("c++filt", "llvm-cxxfilt"),
        depths=("binary", "headers", "build", "source"),
        without="As cxxfilt-module: reports show the mangled spelling; facts are unchanged.",
        preserved=("exported_symbols", "debug_layout") + HEADER_FAMILIES,
        proof=(
            f"{_SUITE}::test_binary_facts_do_not_depend_on_optional_tools",
            f"{_SUITE}::test_optional_tools_do_not_change_header_facts",
        ),
    ),
    ToolLookup(
        id="system-linker",
        role=_O,
        # The builtin-ABI probe (extract/builtin_abi.probe_builtin_abi) links and
        # runs a program with the frontend's own driver; it has no lookup site of
        # its own, so the entry carries no site and names the binaries the suite
        # hides in its linker-less arm.
        sites=(),
        binaries=(),
        depths=("headers", "build", "source"),
        without=(
            "Not hidden by the main arm: a compiler install ships its linker. When "
            "the clang frontend's builtin-ABI probe cannot link or run (no ld, a "
            "cross target), every builtin type slot reads type_abi_fact "
            "NOT_COLLECTED; record layout, enums, typedefs and vtables stay present "
            "(known gap: the probe is not compile-only)."
        ),
        preserved=tuple(f for f in HEADER_FAMILIES if f != "builtin_abi_fact"),
        marked=("builtin_abi_fact",),
        proof=(f"{_SUITE}::test_clang_builtin_abi_facts_are_marked_without_a_linker",),
    ),
    ToolLookup(
        id="android-header-abi-dumper",
        role=_O,
        sites=(
            "which:abicheck/buildsource/source_extractors/android.py:self.dumper_bin",
        ),
        binaries=("header-abi-dumper",),
        depths=("source",),
        without=(
            "An alternative L4 source extractor. Unavailable means the clang/castxml "
            "source extractors run instead; header and binary facts never consult it."
        ),
        preserved=("exported_symbols",) + HEADER_FAMILIES,
        proof=(f"{_SUITE}::test_header_facts_survive_without_optional_tools",),
    ),
    ToolLookup(
        id="cross-prefixed-compiler",
        role=_O,
        sites=(
            "which:abicheck/extract/compiler_driver.py:prefixed",
            "which:abicheck/extract/headers/sysinc.py:cand",
        ),
        depths=("headers", "build", "source"),
        without=(
            "A --gcc-prefix toolchain candidate. Without it the plain driver is used, "
            "which is the default-install path the suite runs."
        ),
        preserved=HEADER_FAMILIES,
        proof=(f"{_SUITE}::test_header_facts_survive_without_optional_tools",),
    ),
    # --- required frontends --------------------------------------------------
    ToolLookup(
        id="castxml",
        role=_F,
        sites=(
            "which:abicheck/workflows/header_frontend_preflight.py:'castxml'",
            "which:abicheck/buildsource/source_extractors/castxml.py:self.castxml_bin",
            "which:abicheck/buildsource/source_extractors/castxml.py:path",
        ),
        depths=("headers", "build", "source"),
        without="The castxml frontend refuses the run in preflight (exit 64), never an empty header surface.",
    ),
    ToolLookup(
        id="clang",
        role=_F,
        sites=(
            "which:abicheck/extract/compiler_driver.py:clang_bin",
            "which:abicheck/extract/dpcpp_jobs.py:clang_bin",
            "which:abicheck/buildsource/header_graph.py:self.clang_bin",
            "which:abicheck/buildsource/include_graph.py:self.clang_bin",
            "which:abicheck/buildsource/preprocessor_facts.py:self.clang_bin",
            "which:abicheck/buildsource/source_extractors/clang.py:self.clang_bin",
            "which:abicheck/buildsource/source_extractors/clang_command.py:'clang++'",
            "which:abicheck/buildsource/l5_ast_pass.py:binary",
            "which:abicheck/workflows/dump/cache/key.py:clang_driver",
        ),
        depths=("headers", "build", "source"),
        without=(
            "The clang frontend and the L4/L5 passes. Missing clang fails the clang "
            "header parse; an L4/L5 pass records its family unavailable with a "
            "diagnostic instead of an empty fact."
        ),
    ),
    ToolLookup(
        id="c-compiler",
        role=_F,
        sites=(
            "which:abicheck/workflows/header_frontend_preflight.py:cc_bin",
            "which:abicheck/extract/headers/castxml/backend.py:cc_bin",
            "which:abicheck/extract/headers/castxml/abi_fundamental.py:cc_bin",
            "which:abicheck/extract/cache_input_digest.py:cc_bin",
            "which:abicheck/extract/headers/toolchain.py:executable",
        ),
        depths=("headers", "build", "source"),
        without="castxml's --castxml-cc driver: preflight refuses the run when it is missing.",
    ),
    # --- input acquisition ---------------------------------------------------
    ToolLookup(
        id="package-extractors",
        role=_I,
        sites=(
            "which:abicheck/extract/packages.py:'rpm2cpio'",
            "which:abicheck/extract/packages.py:'cpio'",
            "which:abicheck/extract/packages.py:'ar'",
            "which:abicheck/extract/packages.py:'zstd'",
        ),
        binaries=("rpm2cpio", "cpio", "zstd"),
        without=(
            "Package (.rpm/.deb) inputs. A missing extractor fails that input with "
            "a message naming the tool; a plain library is never routed through it. "
            "`ar` stays on PATH in the suite: it ships with the compiler's binutils."
        ),
        proof=(
            "tests/test_package.py::test_extract_missing_rpm2cpio",
            "tests/test_package.py::test_extract_missing_cpio",
        ),
    ),
    ToolLookup(
        id="build-system-query",
        role=_I,
        sites=(
            "which:abicheck/buildsource/adapters/bazel.py:'bazel'",
            "which:abicheck/buildsource/adapters/bazel.py:'bazelisk'",
            "which:abicheck/buildsource/adapters/ninja.py:'ninja'",
            "which:abicheck/buildsource/build_query.py:name",
            "which:abicheck/buildsource/build_query.py:tool",
            "which:abicheck/buildsource/build_query.py:'bazel'",
            "which:abicheck/buildsource/build_query.py:'bazelisk'",
            "argv:abicheck/buildsource/build_query.py:bazel",
            "argv:abicheck/buildsource/build_query.py:cmake",
        ),
        binaries=("bazel", "bazelisk", "ninja", "cmake"),
        depths=("build",),
        without=(
            "L3 build-graph queries. Without the build tool the adapter is "
            "unavailable and the build family is recorded as not collected; header "
            "and binary facts are unaffected."
        ),
        preserved=("exported_symbols",) + HEADER_FAMILIES,
        proof=(
            f"{_SUITE}::test_header_facts_survive_without_optional_tools",
            "tests/test_bazel_adapter.py::test_bazel_executable_missing_diagnostic",
            "tests/test_layer_coverage.py::test_ninja_executable_missing_diagnostic",
        ),
    ),
    # --- no fact effect ------------------------------------------------------
    ToolLookup(
        id="launcher-path-resolution",
        role=_N,
        sites=(
            "which:abicheck/buildsource/source_extractors/launcher_env_path.py:token",
            "which:abicheck/model/execution_cache.py:name",
            "which:abicheck/dry_run.py:name",
            "which:abicheck/probe_harness.py:cfg.compiler",
        ),
        without=(
            "Resolves a recorded launcher token, a cache-key identity, a --dry-run "
            "tool listing, or the probe harness's own compiler; none records a fact."
        ),
    ),
    ToolLookup(
        id="git",
        role=_N,
        sites=(
            "argv:abicheck/workflows/provenance.py:git",
            "argv:abicheck/workflows/changed_paths.py:git",
        ),
        without="Run provenance and changed-path selection; a missing git leaves those fields empty, not facts.",
    ),
    # --- fact switches -------------------------------------------------------
    ToolLookup(
        id="header-frontend-switches",
        role=_S,
        sites=(
            "env:ABICHECK_AST_FRONTEND",
            "env:ABICHECK_ALLOW_AST_FALLBACK",
            "env:ABICHECK_ALLOW_UNSUPPORTED_CASTXML",
            "env:ABICHECK_AUTO_SYSTEM_INCLUDES",
            "env:ABICHECK_CLANG_PRUNE_DEPENDENCY_DECLS",
            "env:ABICHECK_PREPROCESSOR_SCAN",
        ),
        depths=("headers", "build", "source"),
        without=(
            "Unset is the default install. The suite sets only ABICHECK_AST_FRONTEND "
            "(the frontend under test) and unsets every other ABICHECK_* variable."
        ),
        proof=(f"{_SUITE}::test_header_facts_survive_without_optional_tools",),
    ),
    ToolLookup(
        id="build-source-switches",
        role=_S,
        sites=(
            "env:ABICHECK_COLLECT_COMDAT",
            "env:ABICHECK_L4_CACHE_DIR",
            "env:ABICHECK_INPUTS_VERSION",
            "env:ABICHECK_INPUTS_DIR",
            "env:ABICHECK_CC_DISABLE",
            "env:ABICHECK_CC_EXTRACTOR",
            "env:ABICHECK_CC_HEADERS",
            "env:ABICHECK_CC_LIBRARY",
            "env:ABICHECK_CC_VERSION",
        ),
        depths=("build", "source"),
        without=(
            "L3/L4 collection switches and the abicheck-cc wrapper's settings. Unset "
            "collects the documented default; binary and header facts never read them."
        ),
    ),
    ToolLookup(
        id="tuning",
        role=_N,
        sites=tuple(
            f"env:{name}"
            for name in (
                "ABICHECK_L4_JOB_MEM_GIB",
                "ABICHECK_L4_JOBS",
                "ABICHECK_L4_EXECUTOR",
                "ABICHECK_INCLUDE_MAP_JOBS",
                "ABICHECK_INCLUDE_MAP_JOB_MEM_GIB",
                "ABICHECK_CALL_GRAPH_JOBS",
                "ABICHECK_PATTERN_SCAN_JOBS",
                "ABICHECK_PREPROCESSOR_SCAN_JOBS",
                "ABICHECK_PREPROCESSOR_SCAN_MAX_PROBES",
                "ABICHECK_DWARF_LOW_MEMORY_MB",
                "ABICHECK_TU_JOBS",
                "ABICHECK_TU_JOB_MEM_GIB",
                "ABICHECK_UNPARSEABLE_PROBE_JOBS",
                "ABICHECK_PARALLEL_EXTRACTION",
                "ABICHECK_SIDE_PROCESSES",
                "ABICHECK_MEMBER_PROCESSES",
                "ABICHECK_RELEASE_MEM_UTILIZATION",
                "ABICHECK_RELEASE_MEM_RESERVE_GIB",
                "ABICHECK_RELEASE_JOB_MEM_GIB",
                "ABICHECK_ZSTD_THREADS",
                "ABICHECK_MEMBER_JOBS",
                "ABICHECK_MAX_THREADS",
                "ABICHECK_HEADER_GRAPH_STREAM_MIN_MIB",
                "ABICHECK_AST_CACHE_MAX_BYTES",
                "ABICHECK_SNAPSHOT_CACHE_WRITE",
                "ABICHECK_SNAPSHOT_MAX_DECODED_BYTES",
                "ABICHECK_SNAPSHOT_MAX_STORED_BYTES",
                "ABICHECK_REFERENCE_MODE",
            )
        ),
        without=(
            "Parallelism, memory budgets, cache and size limits. They change how "
            "work is scheduled or bounded, never which facts a completed run records."
        ),
    ),
    ToolLookup(
        id="diagnostics-and-provenance",
        role=_N,
        sites=tuple(
            f"env:{name}"
            for name in (
                "ABICHECK_PROGRESS",
                "ABICHECK_MEMORY_TRACE",
                "ABICHECK_MEMORY_TRACE_TRACEMALLOC",
                "ABICHECK_CTX_TESTED_SHA",
                "ABICHECK_CTX_PR_HEAD_SHA",
                "ABICHECK_CTX_PR_BASE_SHA",
                "ABICHECK_CTX_PR_BASE_REF",
                "ABICHECK_CTX_PR_NUMBER",
                "ABICHECK_CTX_REPOSITORY",
                "ABICHECK_CTX_RUN_ID",
                "ABICHECK_CTX_RUN_ATTEMPT",
                "ABICHECK_CTX_WORKFLOW_REF",
                "ABICHECK_CTX_EVENT",
                "ABICHECK_CTX_ORCHESTRATION_REF",
                "ABICHECK_CTX_PROFILE",
            )
        ),
        without="Progress output, memory tracing and Action run provenance; reports carry them, facts do not.",
    ),
)


def entries_with_role(*roles: Role) -> tuple[ToolLookup, ...]:
    """The inventory entries whose role is one of *roles*."""
    return tuple(e for e in INVENTORY if e.role in roles)


def hidden_binaries() -> frozenset[str]:
    """Every executable the invariant suite removes from ``PATH``."""
    return frozenset(
        b
        for e in entries_with_role(Role.OPTIONAL_ENRICHMENT, Role.INPUT_ACQUISITION)
        for b in e.binaries
    )


def hidden_modules() -> frozenset[str]:
    """Every Python module the invariant suite makes unimportable."""
    return frozenset(m for e in INVENTORY for m in e.modules)
