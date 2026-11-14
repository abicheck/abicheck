"""Coverage of the generated edit corpus: which ChangeKinds it checks, which space it runs.

Two separate measures.

**ChangeKind coverage.** ``SCOPE_BUCKETS`` partitions every ``ChangeKind``
into buckets: the kinds a single Linux ELF shared library with DWARF and
public headers can show (*in scope*: the corpus's denominator) and the rest,
each bucket with its reason; every report lists them. Over that scope:

- *declared*: some operator names the kind in an expectation (``expected``,
  or a member of an ``any_of`` group) under some rule. Computed from the
  operators alone, no compiler (``declared_kinds``).
- *verified*, per tier: a cell expected the kind, abicheck produced it, and
  the cell reconciled to a passing class (``CellRecord.verified``). A strict
  xfail (``abiforge.known``) or a skipped cell verifies nothing, and an
  unobservable cell (below its evidence floor) expects nothing, so it
  verifies nothing either.
  A kind abicheck produced that the cell did not expect is listed as
  *produced but not expected* (a possible false positive or a missing
  expectation), with its cell count, never as coverage.

The committed ledger (``LEDGER_PATH``) is the ratchet: ``declared_covered``
may only grow, ``declared_uncovered`` lists the in-scope kinds no operator
names yet, ``derived_by_construction`` lists the in-scope kinds that follow
from a cell's verdict (``DERIVED_KINDS``, e.g. the SONAME-bump advisory every
binary break owes) and that some operator owes -- covered by construction,
never counted as declared coverage -- and ``verified_floor[tier]`` lists the kinds that tier verified
when the ledger was written. ``tests/test_edit_corpus_coverage.py`` checks
the declared side without a compiler; ``check_floor`` checks a run.

**Language-space coverage.** Each record carries the ``abiforge.model.space``
points its cell ran; ``report`` puts them against the feasible space.

Records: a corpus test calls ``write_record`` when
``ABICHECK_EDIT_CORPUS_COVERAGE_DIR`` is set (one JSON file per cell, safe
under xdist); ``python -m abiforge.coverage report DIR...``
aggregates them into a JSON report and a Markdown step summary.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path

from abiforge.checkers import abicheck as checker
from abiforge.model import space as space_model
from abiforge.model.config import Config
from abiforge.model.expect import PASSING_CLASSES, ExecTruth, Expectation
from abiforge.model.space import (
    cell_points,
    coverage as space_coverage,
    point_from_id,
)

LEDGER_PATH = Path(__file__).with_name("coverage_ledger.json")
COVERAGE_DIR_ENV = "ABICHECK_EDIT_CORPUS_COVERAGE_DIR"
TIERS: tuple[str, ...] = ("pr", "nightly", "weekly")
# The ledger keys ``ledger`` recomputes and checks (``verified_floor`` is
# raised from runs instead).
LEDGER_DECLARED_KEYS: tuple[str, ...] = (
    "declared_covered",
    "declared_uncovered",
    "derived_by_construction",
    "out_of_scope",
    "edit_classes_without_operator",
)

# --- scope --------------------------------------------------------------------


@dataclass(frozen=True)
class ScopeBucket:
    """One named group of ChangeKinds, in or out of the corpus's scope, and why.

    A *named* bucket claims the kinds whose value starts with one of its
    ``prefixes`` or is one of its ``names``; named buckets must not overlap
    (``test_every_kind_is_in_exactly_one_scope_bucket``). A *module* bucket
    takes every kind its taxonomy ``module`` declares that no named bucket
    claims, so a platform- or ecosystem-specific kind is excluded by what it
    is about, wherever the catalog filed it.
    """

    id: str
    in_scope: bool
    reason: str
    prefixes: tuple[str, ...] = ()
    names: frozenset[str] = frozenset()
    module: str | None = None

    @property
    def named(self) -> bool:
        return self.module is None

    def claims(self, value: str) -> bool:
        return value in self.names or value.startswith(self.prefixes or ("\0",))


_ELF_ONE_LIBRARY = "one Linux ELF shared library with DWARF and public headers shows it"

# The corpus builds one Linux ELF .so per side, from C or C++, and gives
# abicheck at most binary + DWARF + public headers.
SCOPE_BUCKETS: tuple[ScopeBucket, ...] = (
    # --- named, out of scope
    ScopeBucket(
        "pe_coff", False, "PE/COFF only; the corpus targets Linux ELF", ("pe_",)
    ),
    ScopeBucket(
        "apple",
        False,
        "Mach-O / Apple deployment only; the corpus targets Linux ELF",
        ("macho_",),
        frozenset(
            {
                "compat_version_changed",
                "os_deployment_floor_raised",
                "macos_deployment_target_raised",
            }
        ),
    ),
    ScopeBucket(
        "python_packaging",
        False,
        "Python, NumPy C-API and wheel packaging; the corpus builds native libraries",
        ("python_", "numpy_", "wheel_", "musllinux_"),
    ),
    ScopeBucket(
        "sycl",
        False,
        "SYCL runtime/plugin metadata; the corpus builds plain C/C++",
        ("sycl_",),
    ),
    ScopeBucket(
        "kernel_abi",
        False,
        "Linux kernel module ABI (Module.symvers), not a userspace .so",
        ("kabi_",),
    ),
    ScopeBucket(
        "multi_library",
        False,
        "multi-library bundle scope (intra-bundle dependencies, providers, "
        "SONAME skew, manifests): reachable by a bundle seed building several "
        "libraries per side, which is not modeled yet; a corpus cell is one library",
        ("bundle_",),
    ),
    ScopeBucket(
        "needs_source_graph",
        False,
        "needs the L5 source graph (call/reference edges from public entries), "
        "though the catalog files it under symbols",
        names=frozenset({"internal_symbol_required_by_public_api"}),
    ),
    ScopeBucket(
        "no_extractor_fact",
        False,
        "no extractor records the fact the detector reads, so no edit of a real "
        "library reaches it: every backend and DWARF flatten an anonymous "
        "member into its parent's fields",
        names=frozenset({"anon_field_changed"}),
    ),
    ScopeBucket(
        "non_x86_build_mode",
        False,
        "-mfloat-abi is an ARM option: no x86/x86-64 build has a float ABI to "
        "change, and the corpus's toolchains target x86",
        names=frozenset({"float_abi_changed"}),
    ),
    # --- named, out of scope: L3 build kinds the compile DB a build_data
    # config gives (one compile command per side) cannot reach.
    ScopeBucket(
        "l3_link_facts",
        False,
        "read from L3 link units or build-system target metadata (version "
        "script / SONAME per link target, generator graph, several compile "
        "units per target); a compile_commands.json records compile commands "
        "only, and a corpus library is one translation unit",
        names=frozenset(
            {
                "compile_context_conflict",
                "generated_file_dependency_unstable",
                "link_export_policy_changed",
            }
        ),
    ),
    ScopeBucket(
        "l3_non_abi_or_toolchain_identity",
        False,
        "a change of non-ABI build metadata (include order, output paths) or "
        "of the toolchain identity (--target / --sysroot): two builds of one "
        "corpus library differ in neither without changing the toolchain the "
        "cell is defined by",
        names=frozenset({"build_context_changed", "toolchain_version_changed"}),
    ),
    ScopeBucket(
        "l2_l3_context_reconciliation",
        False,
        "a disagreement between the header parse and the build's own context "
        "(or DWARF): the harness parses headers with exactly the build's "
        "context by construction, so a correct cell never reaches it",
        names=frozenset(
            {
                "header_binary_context_mismatch",
                "header_build_context_mismatch",
                "header_parse_context_drift",
            }
        ),
    ),
    ScopeBucket(
        "release_inventory_or_policy",
        False,
        "judged against a release inventory, a declared support promise, a "
        "standard floor from a build configuration matrix or a documented "
        "default: inputs outside two builds of one library",
        names=frozenset(
            {
                "behavioural_default_changed",
                "cxx_standard_floor_raised",
                "support_promise_component_introduced",
                "support_promise_component_retired",
            }
        ),
    ),
    ScopeBucket(
        "asymmetric_evidence",
        False,
        "the two sides were analyzed with different evidence; every corpus "
        "cell gives both sides one evidence level (an asymmetric-evidence "
        "axis is not modeled)",
        names=frozenset(
            {"layer_coverage_asymmetric", "source_fact_coverage_incomplete"}
        ),
    ),
    # --- named, out of scope: L4/L5 source kinds two builds of one corpus
    # library with sources (a ``sources`` config) cannot reach.
    ScopeBucket(
        "source_replay_twin",
        False,
        "a source-replay copy of a header-tier finding: a sources config always "
        "carries headers, and compare drops the replay twin for the header-tier "
        "kind that names the same entity (compare/source_replay_twins.REPLAY_TWINS: "
        "CONSTANT_CHANGED, TYPEDEF_REMOVED, FUNC_REMOVED/visibility); only a "
        "headerless source-only dump, which the corpus does not model, keeps it",
        names=frozenset(
            {
                "constexpr_value_changed",
                "public_typedef_removed",
                "source_decl_binary_symbol_mismatch",
                "source_to_binary_mapping_changed",
            }
        ),
    ),
    ScopeBucket(
        "comparison_policy_input",
        False,
        "decided against an input the corpus never gives compare: a policy "
        "file's require_evidence, a suppression file, or a probe environment "
        "matrix (several configurations of one version)",
        names=frozenset(
            {
                "api_depends_on_consumer_env",
                "evidence_required_missing",
                "suppression_reachability_unknown",
                "suppression_would_hide_public_break",
            }
        ),
    ),
    ScopeBucket(
        "build_target_graph",
        False,
        "read from build-system target metadata (CMake file API, Bazel, ...): "
        "target public headers (TARGET_HAS_PUBLIC_HEADER), target-to-target "
        "and dependency-role edges, a compile unit's target (option-to-symbol "
        "edges) and declared generated files; a compile_commands.json, all a "
        "corpus build writes, has none of them",
        names=frozenset(
            {
                "build_option_reaches_public_symbol",
                "generated_header_changed",
                "generated_header_reaches_public_api",
                "include_graph_public_header_drift",
                "public_dependency_role_changed",
                "public_dependency_role_unknown",
                "target_dependency_added",
            }
        ),
    ),
    ScopeBucket(
        "multi_translation_unit",
        False,
        "needs several translation units in one library (an exported symbol's "
        "defining source file moving, two TUs defining one type differently); "
        "a corpus library is one translation unit",
        names=frozenset(
            {"exported_symbol_source_owner_changed", "odr_source_conflict"}
        ),
    ),
    ScopeBucket(
        "private_header_provenance",
        False,
        "needs a private (non-installed) header the dump also parses, so a "
        "declaration's origin can be private; a corpus seed renders one public "
        "header and the dump is given only that",
        names=frozenset(
            {
                "identity_collision_detected",
                "odr_type_variant",
                "private_header_leak",
                "rtti_for_internal_type",
            }
        ),
    ),
    ScopeBucket(
        "source_artifact_coherence",
        False,
        "a disagreement between the source tree and the binary built from it "
        "(a stale or foreign DSO): the harness compiles the DSO from exactly "
        "the tree it hands over, so a correct cell never reaches it",
        names=frozenset(
            {"source_binary_provenance_mismatch", "source_surface_dso_mismatch"}
        ),
    ),
    ScopeBucket(
        "source_graph_reconciliation",
        False,
        "a declaration's L5 identity is its symbol (decl://<mangled>), so a "
        "moved or re-spelled declaration keeps its id and is never added/removed "
        "(nothing to reconcile), and an exported function's rename changes its "
        "symbol, which the structural tier does not pair (verified on two "
        "builds: a line shift and a rename reach no reconciliation finding); "
        "a type-level rename of an internal type a public API reaches changes "
        "the public record's layout spelling and is reported by the header "
        "tier instead",
        names=frozenset(
            {
                "declaration_coordinates_shifted",
                "declaration_identity_reconciled",
                "declaration_identity_reconciled_unresolved",
                "declaration_identity_unchanged",
                "declaration_moved",
                "declaration_renamed",
                "public_api_internal_type_renamed",
            }
        ),
    ),
    ScopeBucket(
        "source_surface_boundary",
        False,
        "the L4/L5 surface records only declarations a public header reaches: "
        "an existing export that gains or loses its header declaration is "
        "absent from one side's graph, so it reads as an addition/removal "
        "(FUNC_ADDED, PUBLIC_SURFACE_GREW), never as a persisting declaration "
        "crossing the public boundary (verified on two builds)",
        names=frozenset({"public_reachability_changed"}),
    ),
    ScopeBucket(
        "consumer_or_runtime_stack",
        False,
        "judged against consumer artifacts given to compare (--consumer) or the "
        "loaded dependency stack (which library provides a symbol, weak "
        "resolution across libraries); corpus consumers feed only the "
        "execution oracle, and a cell builds one library",
        names=frozenset(
            {
                "consumer_required_symbol_removed",
                "runtime_symbol_provider_changed",
                "runtime_weak_resolution_changed",
            }
        ),
    ),
    # --- named, in scope: L4/L5 source kinds a sources config reaches
    # (abiforge.operators.source_evidence).
    ScopeBucket(
        "l4_l5_source_evidence",
        True,
        "seen only by L4 source replay or the L5 source graph; a sources config "
        "gives each side's source tree (abicheck dump --sources)",
        names=frozenset(
            {
                "call_graph_public_entry_reachability_changed",
                "default_argument_changed",
                "inline_body_changed",
                "public_api_internal_dependency_added",
                "public_macro_removed",
                "public_macro_value_changed",
                "public_surface_grew",
                "public_surface_shrank",
                "public_to_internal_dependency",
                "public_virtual_dispatch_set_changed",
                "template_body_changed",
                "undocumented_export_ratio_increased",
                "uninstantiated_template_removed",
            }
        ),
    ),
    ScopeBucket(
        "binary_runtime_floor",
        True,
        "the highest provider version node a library's imports need, read "
        "from its .gnu.version_r: the binary alone shows it, although the "
        "catalog files it under build",
        names=frozenset({"runtime_floor_raised"}),
    ),
    # --- named, in scope: L3 build-mode kinds a build_data config reaches
    # (abiforge.operators.build_data).
    ScopeBucket(
        "l3_build_mode",
        True,
        "a compile mode recorded only in the build's compile commands; a "
        "build_data config gives each side's compile_commands.json",
        names=frozenset(
            {
                "abi_relevant_build_flag_changed",
                "exceptions_mode_changed",
                "lto_mode_changed",
                "rtti_mode_changed",
                "sanitizer_mode_changed",
                "stdlib_debug_mode_changed",
                "struct_packing_mode_changed",
                "threadsafe_statics_mode_changed",
                "tls_model_changed",
                "whole_program_vtables_mode_changed",
            }
        ),
    ),
    ScopeBucket(
        "non_x86_elf",
        False,
        "an ELF header or note fact no x86/x86-64 build can change "
        "(AArch64 BTI/PAC, e_flags, byte order, a non-GNU OS ABI); the "
        "corpus's toolchains target x86",
        names=frozenset(
            {
                "branch_protection_improved",
                "branch_protection_weakened",
                "elf_abi_flags_changed",
                "elf_endianness_changed",
                "elf_osabi_changed",
            }
        ),
    ),
    ScopeBucket(
        "executable_only",
        False,
        "a property of an executable (PT_INTERP, DF_1_PIE); a shared library "
        "linked with -shared has neither",
        names=frozenset({"interpreter_changed", "pie_disabled"}),
    ),
    ScopeBucket(
        "pe_macho_version",
        False,
        "a PE file version or Mach-O current_version; an ELF library has no such field",
        names=frozenset({"library_version_downgraded"}),
    ),
    ScopeBucket(
        "unlinkable_symbol_shape",
        False,
        "STT_COMMON exists only in relocatable objects: the linker allocates "
        "a common symbol in .bss, so a linked shared library never has one",
        names=frozenset({"common_symbol_risk"}),
    ),
    ScopeBucket(
        "declared_deployment_floor",
        False,
        "judged against a declared deployment floor (`.abicheck.yml` "
        "runtime_floors), a policy input the corpus does not give",
        names=frozenset({"platform_baseline_floor_raised"}),
    ),
    ScopeBucket(
        "libcxx_abi_v2_runtime",
        False,
        "needs a libc++ built with LIBCXX_ABI_VERSION=2 (std::__2): distribution "
        "libc++ pins _LIBCPP_ABI_VERSION=1 in __config_site, so no corpus "
        "toolchain can build the other ABI version",
        names=frozenset({"libcpp_abi_version_changed"}),
    ),
    ScopeBucket(
        "stored_layout_generation",
        False,
        "a record with a layout descriptor on one side and no size at all on "
        "the other: a stored snapshot from an extractor generation that "
        "recorded no layout. Every library the corpus builds and dumps live "
        "records a size on both sides (DWARF and every header backend), and a "
        "pair with headers on one side only is compared at binary depth",
        names=frozenset({"layout_unverifiable"}),
    ),
    # --- named, in scope: source-taxonomy kinds a public header alone shows.
    # Macro values, constexpr values, default-argument values, a removed public
    # typedef and an uninstantiated template stay out (module bucket below):
    # abicheck reads them from L4 sources only
    # (docs/learn/evidence-and-detectability.md).
    ScopeBucket(
        "header_visible_source",
        True,
        "a public header alone shows it (L2), although the catalog files it as source",
        names=frozenset(
            {
                "concept_constraint_changed",
                "concept_tightened",
                "exported_not_public",
                "header_only_function_params_changed",
                "inline_function_removed",
                "internal_template_leaks_via_public_api",
                "internal_type_leaks_via_public_api",
                "public_not_exported",
                "public_typedef_target_changed",
                "unversioned_exported_symbol",
            }
        ),
    ),
    # --- module buckets: the module's kinds no named bucket claims
    ScopeBucket("elf_symbols", True, _ELF_ONE_LIBRARY, module="symbols"),
    ScopeBucket("elf_types", True, _ELF_ONE_LIBRARY, module="types"),
    ScopeBucket("elf_platform", True, _ELF_ONE_LIBRARY, module="platform"),
    ScopeBucket(
        "source_graph", False, "needs the L5 source graph", module="source_graph_risk"
    ),
    ScopeBucket(
        "disposition",
        False,
        "a policy disposition, not an observed change",
        module="disposition",
    ),
)
SCOPE_BUCKET_BY_ID: dict[str, ScopeBucket] = {b.id: b for b in SCOPE_BUCKETS}
HEADER_VISIBLE_SOURCE_KINDS = SCOPE_BUCKET_BY_ID["header_visible_source"].names


def _taxonomy() -> dict[str, str]:
    """ChangeKind value -> the taxonomy module that declares it."""
    return {value: module for _, value, module in checker.kind_catalog()}


def buckets_of(value: str, module: str) -> list[ScopeBucket]:
    """Every bucket that claims the kind *value*: exactly one when the table is sound."""
    named = [b for b in SCOPE_BUCKETS if b.named and b.claims(value)]
    if named:
        return named
    return [b for b in SCOPE_BUCKETS if b.module == module]


def scope_buckets() -> dict[str, list[str]]:
    """Bucket id -> the ChangeKind names it holds (first claim; see ``buckets_of``)."""
    out: dict[str, list[str]] = {b.id: [] for b in SCOPE_BUCKETS}
    for name, value, module in checker.kind_catalog():
        out[buckets_of(value, module)[0].id].append(name)
    return out


def scope() -> dict[str, str | None]:
    """Every ChangeKind *name* -> ``None`` (in scope) or why it is out of scope."""
    out: dict[str, str | None] = {}
    for bucket_id, kinds in scope_buckets().items():
        b = SCOPE_BUCKET_BY_ID[bucket_id]
        for name in kinds:
            out[name] = None if b.in_scope else b.reason
    return out


def in_scope() -> frozenset[str]:
    return frozenset(k for k, why in scope().items() if why is None)


def scope_report() -> dict[str, object]:
    """Every bucket with its verdict, reason and kinds: the auditable denominator."""
    held = scope_buckets()
    return {
        "in_scope": sum(
            len(v) for b, v in held.items() if SCOPE_BUCKET_BY_ID[b].in_scope
        ),
        "total": sum(len(v) for v in held.values()),
        "buckets": {
            b.id: {
                "in_scope": b.in_scope,
                "reason": b.reason,
                "count": len(held[b.id]),
                "kinds": sorted(held[b.id]),
            }
            for b in SCOPE_BUCKETS
        },
    }


# --- declared coverage ----------------------------------------------------------


def expectation_kinds(exp: Expectation) -> frozenset[str]:
    """Every kind an expectation names: required ones and ``any_of`` members."""
    out = set(exp.expected)
    for group in exp.any_of:
        out |= group
    return frozenset(out)


def _operators() -> tuple[object, ...]:
    from abiforge.operators import OPERATORS

    return tuple(OPERATORS)


def _derived_kinds() -> frozenset[str]:
    from abiforge.model.opkit import DERIVED_KINDS

    return DERIVED_KINDS


def op_declared_kinds(op: object) -> frozenset[str]:
    """Kinds *op* names under its base expectation or any of its rules: what
    the edit itself stands behind. Kinds that follow from the verdict
    (``EditOp.with_derived``) are ``op_derived_kinds``, never declared."""
    from abiforge.operators import EditOp

    assert isinstance(op, EditOp)
    base = op.base()
    out = set(expectation_kinds(base))
    for rule in op.rules:
        out |= expectation_kinds(rule.apply(base))
    return frozenset(out - _derived_kinds())


def op_derived_kinds(op: object) -> frozenset[str]:
    """The derived kinds *op* owes under its base expectation or a rule."""
    from abiforge.operators import EditOp

    assert isinstance(op, EditOp)
    base = op.base()
    out = set(expectation_kinds(op.with_derived(base)))
    for rule in op.rules:
        out |= expectation_kinds(op.with_derived(rule.apply(base)))
    return frozenset(out & _derived_kinds())


def derived_owed_kinds() -> frozenset[str]:
    """The derived kinds (``DERIVED_KINDS``) at least one operator owes: a kind
    the verdict implies is covered by construction only while some edit's
    verdict reaches it; one no operator owes stays uncovered."""
    return frozenset(
        kind
        for kind in _derived_kinds()
        if any(kind in op_derived_kinds(op) for op in _operators())
    )


def declared_kinds() -> dict[str, frozenset[str]]:
    """ChangeKind name -> the operators that declare it."""
    from abiforge.operators import OPERATORS

    out: dict[str, set[str]] = {}
    for op in OPERATORS:
        for kind in op_declared_kinds(op):
            out.setdefault(kind, set()).add(op.name)
    return {k: frozenset(v) for k, v in sorted(out.items())}


# --- records ------------------------------------------------------------------


@dataclass(frozen=True)
class CellRecord:
    """One cell's coverage facts, as written by a corpus test."""

    tier: str
    cell: str
    op: str
    seed: str
    config: str
    status: str  # "ran" | "not-applicable" | "unavailable"
    cls: str = ""  # reconciled class (or "known-xfail" in the default matrix)
    verdict: str = ""
    produced: tuple[str, ...] = ()
    expected: tuple[str, ...] = ()
    points: tuple[str, ...] = ()

    @property
    def ran(self) -> bool:
        return self.status == "ran"

    @property
    def verified(self) -> frozenset[str]:
        """Expected kinds abicheck produced, in a cell that reconciled as
        passing. A derived kind (``DERIVED_KINDS``) is not coverage."""
        return self._verified_all - _derived_kinds()

    @property
    def derived_verified(self) -> frozenset[str]:
        """The derived kinds this passing cell owed and abicheck produced."""
        return self._verified_all & _derived_kinds()

    @property
    def _verified_all(self) -> frozenset[str]:
        if not self.ran or self.cls not in PASSING_CLASSES:
            return frozenset()
        return frozenset(self.expected) & frozenset(self.produced)

    @property
    def incidental(self) -> frozenset[str]:
        return frozenset(self.produced) - frozenset(self.expected)

    def to_json(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict[str, object]) -> CellRecord:
        known = {f for f in cls.__dataclass_fields__}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"unknown coverage record fields {sorted(unknown)}")
        kw = {
            k: tuple(v) if isinstance(v, list) else v  # type: ignore[arg-type]
            for k, v in data.items()
        }
        return cls(**kw)  # type: ignore[arg-type]


def make_record(
    tier: str,
    op: object,
    seed: str,
    config: Config,
    *,
    status: str,
    cls: str = "",
    verdict: str = "",
    produced: Iterable[str] = (),
    expectation: Expectation | None = None,
    source: str = "",
    ran_consumers: frozenset[str] = frozenset(),
) -> CellRecord:
    """One cell's record. *source* names the test that ran it when two tests
    of one tier can run the same (op, seed, config) cell, so their records
    stay distinct; *ran_consumers* are the (build, consumer) pairs that ran
    (``ran_consumers(exec_truth)``), the only ones that cover a space point."""
    from abiforge.operators import EditOp

    assert isinstance(op, EditOp)
    exp = expectation if expectation is not None else op.expect(config)
    ran = status == "ran"
    return CellRecord(
        tier=tier,
        cell=f"{source + ':' if source else ''}{op.name}-{seed}-{config.id}",
        op=op.name,
        seed=seed,
        config=config.id,
        status=status,
        cls=cls,
        verdict=verdict,
        produced=tuple(sorted(produced)),
        expected=tuple(sorted(expectation_kinds(exp))),
        points=tuple(p.id for p in cell_points(op, config, ran_consumers))
        if ran
        else (),
    )


def ran_consumers(exec_truth: ExecTruth | None) -> frozenset[str]:
    """The consumers whose (build, consumer) pair ran, from a cell's oracle.

    No oracle (``None``) or an unavailable one (a non-ELF host: no pairs at
    all) is no execution evidence, so no consumer counts.
    """
    if exec_truth is None or not exec_truth.available:
        return frozenset()
    return frozenset(p.consumer for p in exec_truth.pairs if p.status == "ran")


def write_record(
    record: CellRecord, out_dir: str | os.PathLike[str] | None = None
) -> Path | None:
    """Write *record* under ``$ABICHECK_EDIT_CORPUS_COVERAGE_DIR`` (or *out_dir*)."""
    target = out_dir or os.environ.get(COVERAGE_DIR_ENV)
    if not target:
        return None
    root = Path(target)
    root.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(f"{record.tier}\0{record.cell}".encode()).hexdigest()[:16]
    path = root / f"{record.tier}-{digest}.json"
    tmp = path.with_suffix(f".tmp{os.getpid()}")
    tmp.write_text(
        json.dumps(record.to_json(), sort_keys=True) + "\n", encoding="utf-8"
    )
    tmp.replace(path)
    return path


def load_records(paths: Sequence[str | os.PathLike[str]]) -> list[CellRecord]:
    out: list[CellRecord] = []
    for raw in paths:
        p = Path(raw)
        files = sorted(p.rglob("*.json")) if p.is_dir() else [p]
        for f in files:
            out.append(CellRecord.from_json(json.loads(f.read_text(encoding="utf-8"))))
    return out


# --- the report ---------------------------------------------------------------


@dataclass
class TierCoverage:
    tier: str
    cells: int = 0
    ran: int = 0
    skipped: int = 0
    passing: int = 0
    produced: set[str] = field(default_factory=set)
    verified: set[str] = field(default_factory=set)
    expected: set[str] = field(default_factory=set)
    # kind -> how many ran cells produced it without expecting it
    incidental: dict[str, int] = field(default_factory=dict)
    # derived kind -> how many passing cells owed and produced it
    derived: dict[str, int] = field(default_factory=dict)
    points: set[str] = field(default_factory=set)


def _by_tier(records: Iterable[CellRecord]) -> dict[str, TierCoverage]:
    tiers: dict[str, TierCoverage] = {}
    for r in records:
        t = tiers.setdefault(r.tier, TierCoverage(r.tier))
        t.cells += 1
        t.expected |= set(r.expected)
        if not r.ran:
            t.skipped += 1
            continue
        t.ran += 1
        t.passing += r.cls in PASSING_CLASSES
        t.produced |= set(r.produced)
        t.verified |= r.verified
        for kind in r.derived_verified:
            t.derived[kind] = t.derived.get(kind, 0) + 1
        for kind in r.incidental:
            t.incidental[kind] = t.incidental.get(kind, 0) + 1
        t.points |= set(r.points)
    return dict(sorted(tiers.items()))


def _pct(n: int, d: int) -> float:
    return round(100.0 * n / d, 1) if d else 0.0


def report(
    records: Sequence[CellRecord],
    ledger: dict | None = None,
    check_tiers: Sequence[str] = (),
) -> dict[str, object]:
    """Aggregate *records* into the coverage report (JSON-serializable).

    ``floor_problems`` is computed only for *check_tiers*: only a merged run
    can be held to a floor, so a single shard's report never lists drops.
    """
    scope_set = in_scope()
    declared = set(declared_kinds())
    tiers = _by_tier(records)
    out_tiers: dict[str, object] = {}
    for name, t in tiers.items():
        verified_in = t.verified & scope_set
        out_tiers[name] = {
            "cells": t.cells,
            "ran": t.ran,
            "skipped": t.skipped,
            "passing": t.passing,
            "verified": sorted(t.verified),
            "verified_in_scope": len(verified_in),
            "verified_pct_of_scope": _pct(len(verified_in), len(scope_set)),
            "expected_not_verified": sorted(t.expected - t.verified),
            # Produced but not expected: a possible false positive or a
            # missing expectation. Listed, never counted as coverage.
            "produced_not_expected": dict(sorted(t.incidental.items())),
            # Derived kinds (owed by the verdict, not the edit): cells that
            # produced them. Not coverage.
            "derived_verified_cells": dict(sorted(t.derived.items())),
            "space": space_coverage(point_from_id(p) for p in t.points),
        }
    all_points = {p for t in tiers.values() for p in t.points}
    out: dict[str, object] = {
        "scope": scope_report(),
        "scope_size": len(scope_set),
        "declared_in_scope": len(declared & scope_set),
        "declared_pct_of_scope": _pct(len(declared & scope_set), len(scope_set)),
        "declared_out_of_scope": sorted(declared - scope_set),
        "derived": {
            kind: sum(kind in op_derived_kinds(op) for op in _operators())
            for kind in sorted(_derived_kinds())
        },
        "uncovered_in_scope": sorted(scope_set - declared),
        "tiers": out_tiers,
        "space": space_coverage(point_from_id(p) for p in all_points),
    }
    if ledger is not None and check_tiers:
        out["floor_problems"] = {
            name: check_floor(records, ledger, name) for name in check_tiers
        }
    return out


def _verified(records: Sequence[CellRecord], tier: str) -> set[str]:
    """Every kind any cell of *tier* verified."""
    return set().union(*(r.verified for r in records if r.tier == tier))


def check_floor(records: Sequence[CellRecord], ledger: dict, tier: str) -> list[str]:
    """Floor kinds this run of *tier* failed to verify.

    *records* must be the whole tier run, every shard merged. One shard cannot
    be judged on its own: a kind its cells expect may be verified only by a
    cell in another shard (a skipped or known-xfail cell, or an ``any_of``
    alternative the cell did not produce, still lists it as expected).
    """
    floor = set(ledger.get("verified_floor", {}).get(tier, ()))
    if not floor:
        return []
    return [
        f"{kind}: in the {tier} verified floor, not verified by this run"
        for kind in sorted(floor - _verified(records, tier))
    ]


def _scope_lines(rep: dict) -> list[str]:
    sc = rep["scope"]
    lines = [
        "",
        f"<details><summary>Scope: {sc['in_scope']} of {sc['total']} ChangeKinds, by bucket</summary>",
        "",
        "| Bucket | In scope | Kinds | Why |",
        "|---|:---:|---:|---|",
    ]
    for bucket_id, b in sc["buckets"].items():
        mark = "yes" if b["in_scope"] else "no"
        lines.append(f"| `{bucket_id}` | {mark} | {b['count']} | {b['reason']} |")
    return [*lines, "", "</details>"]


def _produced_not_expected_lines(rep: dict) -> list[str]:
    rows = [
        (tier, kind, n)
        for tier, t in rep["tiers"].items()
        for kind, n in t["produced_not_expected"].items()
    ]
    if not rows:
        return []
    lines = [
        "",
        "<details><summary>Produced but not expected (possible false positives or "
        f"missing expectations; not coverage): {len(rows)}</summary>",
        "",
        "| Tier | Kind | Cells |",
        "|---|---|---:|",
    ]
    lines += [
        f"| {tier} | `{kind}` | {n} |"
        for tier, kind, n in sorted(rows, key=lambda r: (r[0], -r[2], r[1]))
    ]
    return [*lines, "", "</details>"]


def _edit_class_lines(sp: dict) -> list[str]:
    """The edit-class model: every class, its operators, and the gaps."""
    classes = sp.get("edit_classes") or {}
    total = sum(len(c) for c in classes.values())
    gaps = sp.get("edit_classes_without_operator") or []
    if not total:
        return []
    lines = [
        "",
        f"Edit classes in the model (`abiforge/model/edit_model.py`): **{total}**; "
        f"with a generating operator: **{total - len(gaps)}**; "
        f"with none (uncovered gaps, still in the denominator): **{len(gaps)}**.",
        "",
        "<details><summary>Edit classes</summary>",
        "",
        "| Class | Operators | Points covered / feasible |",
        "|---|---|---:|",
    ]
    for con, rows in classes.items():
        for cls, row in rows.items():
            ops = ", ".join(f"`{o}`" for o in row["operators"]) or "**none (gap)**"
            lines.append(
                f"| `{con}/{cls}` | {ops} | {row['covered']} / {row['feasible']} |"
            )
    return [*lines, "", "</details>"]


def summary_markdown(rep: dict, title: str = "Edit corpus coverage") -> str:
    lines = [f"### {title}", ""]
    lines.append(
        f"ChangeKinds in scope (Linux ELF + DWARF + headers): **{rep['scope_size']}**; "
        f"declared by operators: **{rep['declared_in_scope']}** ({rep['declared_pct_of_scope']}%)."
    )
    derived = rep.get("derived") or {}
    if derived:
        lines.append(
            "Derived (owed by a cell's verdict, not by its edit; not counted): "
            + ", ".join(f"`{k}` by {n} operators" for k, n in derived.items())
            + "."
        )
    lines += [
        "",
        "| Tier | Cells ran / recorded | Passing | Verified kinds (in scope) | % of scope | Space points covered / feasible |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name, t in rep["tiers"].items():  # type: ignore[union-attr]
        sp = t["space"]
        lines.append(
            f"| {name} | {t['ran']} / {t['cells']} | {t['passing']} | {t['verified_in_scope']} "
            f"| {t['verified_pct_of_scope']}% | {sp['covered']} / {sp['feasible']} "
            f"({_pct(sp['covered'], sp['feasible'])}%) |"
        )
    lines += _scope_lines(rep) + _produced_not_expected_lines(rep)
    sp = rep["space"]
    lines += [
        "",
        f"Language space, all tiers: {sp['covered']} / {sp['feasible']} feasible points "  # type: ignore[index]
        f"({_pct(sp['covered'], sp['feasible'])}%; {sp['total']} points before infeasible ones are removed).",
        "",
    ]  # type: ignore[index]
    lines += [
        "| Axis | Values exercised | Thinnest value (covered / feasible) |",
        "|---|---:|---|",
    ]
    for axis, rows in sp["per_axis"].items():  # type: ignore[index]
        hit = sum(1 for r in rows.values() if r["covered"])
        thin = min(
            rows.items(), key=lambda kv: (kv[1]["covered"] / kv[1]["feasible"], kv[0])
        )
        lines.append(
            f"| {axis} | {hit} / {len(rows)} | `{thin[0]}` ({thin[1]['covered']} / {thin[1]['feasible']}) |"
        )
    if sp["top_uncovered"]:  # type: ignore[index]
        lines += ["", "<details><summary>Top uncovered feasible points</summary>", ""]
        lines += [f"- `{p}`" for p in sp["top_uncovered"]]  # type: ignore[index]
        lines += ["", "</details>"]
    lines += _edit_class_lines(sp)
    if sp["covered_but_infeasible"]:  # type: ignore[index]
        lines += [
            "",
            "**Covered points the space model calls infeasible (model bug):**",
        ]
        lines += [f"- `{p}`" for p in sp["covered_but_infeasible"]]  # type: ignore[index]
    problems = rep.get("floor_problems") or {}
    flat = [p for ps in problems.values() for p in ps]  # type: ignore[union-attr]
    if flat:
        lines += ["", "**Verified-floor drops:**"] + [f"- {p}" for p in flat]
    return "\n".join(lines) + "\n"


# --- ledger -------------------------------------------------------------------


def load_ledger(path: Path | None = None) -> dict:
    return json.loads((path or LEDGER_PATH).read_text(encoding="utf-8"))


def declared_ledger(previous: dict | None = None) -> dict:
    """The ledger's declared side, recomputed; the verified floors are carried over."""
    scope_set = in_scope()
    declared = set(declared_kinds())
    return {
        "_comment": (
            "Ratchet for the edit corpus's ChangeKind coverage "
            "(abiforge/coverage.py). Regenerate the declared side with "
            "`python -m abiforge.coverage ledger --write`; raise a "
            "verified_floor from a run with `report DIR --raise-floor TIER`."
        ),
        "declared_covered": sorted(declared & scope_set),
        "declared_uncovered": sorted(scope_set - declared - derived_owed_kinds()),
        "derived_by_construction": sorted(derived_owed_kinds() & scope_set),
        # Every other catalog kind, so a kind in none of the three lists is
        # one added to the catalog after the ledger was written, and a kind
        # moved into scope by a bucket change is not mistaken for it.
        "out_of_scope": sorted(set(checker.kind_names()) - scope_set),
        "verified_floor": (previous or {}).get("verified_floor", {}),
        # Edit classes of the space model (``abiforge.model.edit_model``) that no
        # operator generates: uncovered gaps, kept in the denominator.
        "edit_classes_without_operator": list(
            space_model.edit_classes_without_operator()
        ),
    }


def _write_ledger(ledger: dict, path: Path | None = None) -> None:
    (path or LEDGER_PATH).write_text(
        json.dumps(ledger, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m abiforge.coverage")
    sub = ap.add_subparsers(dest="cmd", required=True)
    rp = sub.add_parser("report", help="aggregate coverage records")
    rp.add_argument("paths", nargs="+")
    rp.add_argument("--json", dest="json_out")
    rp.add_argument("--summary", help="append the Markdown summary to this file")
    rp.add_argument("--title", default="Edit corpus coverage")
    rp.add_argument(
        "--check-floor",
        action="append",
        default=[],
        metavar="TIER",
        help="fail if this tier's run verified less than its ledger floor, or a "
        "covered point is one the space model calls infeasible (PATHS must hold "
        "every shard's records)",
    )
    rp.add_argument(
        "--raise-floor",
        action="append",
        default=[],
        metavar="TIER",
        help="write this run's verified in-scope kinds into the ledger's floor",
    )
    lp = sub.add_parser(
        "ledger", help="check or rewrite the declared side of the ledger"
    )
    lp.add_argument("--write", action="store_true")
    args = ap.parse_args(argv)

    ledger = load_ledger() if LEDGER_PATH.exists() else {}
    if args.cmd == "ledger":
        fresh = declared_ledger(ledger)
        if args.write:
            _write_ledger(fresh)
            return 0
        same = all(fresh[k] == ledger.get(k) for k in LEDGER_DECLARED_KEYS)
        print("ledger is current" if same else "ledger is stale: run with --write")
        return 0 if same else 1

    records = load_records(args.paths)
    rep = report(records, ledger, args.check_floor)
    text = summary_markdown(rep, args.title)
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(rep, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as fh:
            fh.write(text)
    else:
        sys.stdout.write(text)
    if args.raise_floor:
        scope_set = in_scope()
        floors = ledger.setdefault("verified_floor", {})
        for tier in args.raise_floor:
            got = _verified(records, tier)
            floors[tier] = sorted(set(floors.get(tier, ())) | (got & scope_set))
        _write_ledger(ledger)
    problems = [
        p for tier in args.check_floor for p in check_floor(records, ledger, tier)
    ]
    if args.check_floor:
        # A point a cell covered that the model calls infeasible is a model bug.
        problems += [
            f"{p}: covered by a cell, but the space model calls it infeasible"
            for p in rep["space"]["covered_but_infeasible"]  # type: ignore[index]
        ]
    for p in problems:
        print(f"::error::{p}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
