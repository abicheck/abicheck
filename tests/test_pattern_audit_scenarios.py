# Copyright 2026 Nikolay Petrov
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Audit-driven scenarios for ABI/API compatibility patterns.

This file is the executable artefact of an audit that reviewed candidate
new example cases against the existing catalogue (``examples/case*`` and
the ChangeKind enum in ``checker_policy.py``). Its purpose is twofold:

1. Records — case by case — which proposed scenarios are **duplicates**
   of existing examples (so we don't pad the catalogue) and which are
   **genuinely novel** failure modes.

2. For each genuinely-novel scenario, builds synthetic ``AbiSnapshot``
   pairs (no compiler / castxml / libabigail required) and asserts that
   ``abicheck.checker.compare`` emits the expected finding — or, where
   it does not, marks the test ``xfail`` so the policy gap is visible
   and tracked.

The scenarios in this file were collected while reviewing public C/C++
headers from several real-world libraries (BLAS-family, deep-learning
runtimes, kernel ABI interfaces). The patterns themselves are generic;
the synthetic snapshots use neutral identifiers (``libfoo``,
``library_version_t``, ``post_op_kind``) so the audit reads as
library-agnostic.

Cross-references:
    * ``abicheck/checker_policy.py``       — ``ChangeKind`` enum
    * ``examples/ground_truth.json``       — existing example catalogue
    * ``abicheck/internal_leak.py``        — PR #238 (``detail::`` leak)
    * ``tests/test_internal_leak.py``      — companion test style
    * ``tests/test_cpp_pattern_detectors.py``   — companion test style

---------------------------------------------------------------------------

Coverage matrix — proposed-vs-existing comparison
---------------------------------------------------------------------------

Each row was evaluated against the 91 example cases in ``examples/`` and
the ~145 ChangeKinds in ``checker_policy.py``. "Duplicate" means the same
detection path is already exercised end-to-end by an existing case;
"novel" means the failure mode is qualitatively distinct.

Pattern                                                | Existing coverage                                 | Verdict
------------------------------------------------------ | ------------------------------------------------- | -------
``foo_v1`` removed when ``foo_v2`` ships                | ``func_removed`` (case01, case12)                 | DUPLICATE
Public arg-slot ``#define`` renumbered                  | ``CONSTANT_CHANGED`` ChangeKind — no example      | novel (no example)
Runtime sentinel ``#define`` value changed              | Same ``CONSTANT_CHANGED`` — no example            | novel (variant)
Fixed-array-size macro bumped (e.g. ``MAX_NDIMS``)      | ``CONSTANT_CHANGED`` + ``type_size_changed``      | DUPLICATE (composition)
Info struct field appended (returned by ``const T*``)   | case62 covers opaque; case07/14 cover by-value    | novel (asymmetric)
Enum ``_max`` sentinel widened (forces wider storage)   | case57 ``enum_underlying_size_changed``           | DUPLICATE
Conditional enum member under ``#if EXPERIMENTAL_X``    | environment_matrix exists, no example             | novel (build skew)
Internal value range (``1 << 12``) shifts & leaks       | ``INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API`` is *types* | novel (value-range companion)
Function-pointer typedef signature drifts               | function-pointer typedef changes are covered      | DUPLICATE
Static class-member destructor turned extern            | case59 ``func_became_inline``                     | DUPLICATE
Post-op kind appended (forward-incompat for old reader) | case81 (reassignment), case25 (member added)      | novel (forward-asym)
Build-config flag toggles inline struct field           | Same as "conditional enum"                        | DUPLICATE-of-novel
Defaulted template parameter *added*                    | case87 covers default arg *value* change          | novel (marginal)

→ Five genuinely-novel scenarios are exercised below:

   S1. PUBLIC_MACRO_RUNTIME_SLOT_RENUMBERED     (uses CONSTANT_CHANGED; gap = no example)
   S2. POINTER_RETURNED_INFO_STRUCT_APPENDED    (uses TYPE_FIELD_ADDED + needs overlay)
   S3. FEATURE_MACRO_GATED_ENUM_SKEW            (uses ENUM_MEMBER_ADDED + matrix awareness)
   S4. INTERNAL_VALUE_RANGE_LEAKS_VIA_PUBLIC_API (NEW ChangeKind required)
   S5. FORWARD_COMPAT_TAG_APPENDED              (uses ENUM_MEMBER_ADDED; severity asym)

The tests below construct synthetic snapshots for each, run
``compare(...)``, and assert what the current tool catches. Gaps are
``xfail``-marked with a clear reason so they show up in the failure
report without breaking the suite.

Honest assessment (recorded here for future contributors): of the five
"novel" scenarios above, only **S2** is a strong candidate for a new
example case (e.g. ``case90_info_struct_returned_by_pointer_appended``)
because it exercises a layout-change-via-pointer-return path that no
existing case demonstrates. **S4** would also need a new example, but
only after the corresponding ChangeKind and detector land. The other
three are best served by this regression net plus follow-up policy
work, not by new example directories.
"""
from __future__ import annotations

import pytest

from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind
from abicheck.model import (
    AbiSnapshot,
    EnumMember,
    EnumType,
    Function,
    Param,
    RecordType,
    TypeField,
    Variable,
    Visibility,
)

# ---------------------------------------------------------------------------
# Snapshot helpers — mirror style from tests/test_internal_leak.py
# ---------------------------------------------------------------------------


def _snap(
    *,
    library: str = "libfoo.so.1",
    version: str = "1.0",
    functions: list[Function] | None = None,
    variables: list[Variable] | None = None,
    types: list[RecordType] | None = None,
    enums: list[EnumType] | None = None,
    constants: dict[str, str] | None = None,
    typedefs: dict[str, str] | None = None,
) -> AbiSnapshot:
    return AbiSnapshot(
        library=library,
        version=version,
        functions=list(functions or []),
        variables=list(variables or []),
        types=list(types or []),
        enums=list(enums or []),
        constants=dict(constants or {}),
        typedefs=dict(typedefs or {}),
    )


def _public_fn(
    name: str,
    *,
    mangled: str | None = None,
    ret: str = "void",
    params: list[tuple[str, str]] | None = None,
    is_extern_c: bool = False,
) -> Function:
    return Function(
        name=name,
        mangled=mangled or name,
        return_type=ret,
        params=[Param(name=n, type=t) for n, t in (params or [])],
        visibility=Visibility.PUBLIC,
        is_extern_c=is_extern_c,
    )


def _kinds(result: object) -> set[ChangeKind]:
    return {c.kind for c in result.changes}  # type: ignore[attr-defined]


# ===========================================================================
# Confirmations — proposed-but-not-novel scenarios
#
# These tests prove the duplicate classifications above by asserting the
# tool already catches the scenario with an *existing* ChangeKind. They
# also serve as regressions: if we ever drop these detection paths the
# duplicates list becomes outdated.
# ===========================================================================


class TestDuplicateScenarios:
    """Sanity-check that the 'DUPLICATE' rows in the coverage matrix really
    are covered by current detection paths.
    """

    def test_versioned_function_v1_removed_is_just_func_removed(self) -> None:
        """``foo_create`` deprecated when ``foo_create_v2`` ships: the
        suffix is naming convention; detection is plain ``func_removed``
        and is exercised by case01.
        """
        old = _snap(
            functions=[
                _public_fn("foo_create", is_extern_c=True),
                _public_fn("foo_create_v2", is_extern_c=True),
            ],
        )
        new = _snap(
            functions=[
                _public_fn("foo_create_v2", is_extern_c=True),
            ],
        )
        kinds = _kinds(compare(old, new))
        assert ChangeKind.FUNC_REMOVED in kinds, (
            "expected plain func_removed — versioned-suffix is not a "
            "distinct ChangeKind"
        )

    def test_max_ndims_bump_is_constant_change_plus_struct_grow(self) -> None:
        """``#define MAX_NDIMS 12`` → ``16`` plus the embedding struct
        growing — both fire via existing kinds, no new case needed."""
        old = _snap(
            constants={"MAX_NDIMS": "12"},
            types=[
                RecordType(
                    name="memory_desc_t",
                    kind="struct",
                    size_bits=12 * 64,
                    fields=[TypeField(name="dims", type="int64_t[12]", offset_bits=0)],
                ),
            ],
        )
        new = _snap(
            constants={"MAX_NDIMS": "16"},
            types=[
                RecordType(
                    name="memory_desc_t",
                    kind="struct",
                    size_bits=16 * 64,
                    fields=[TypeField(name="dims", type="int64_t[16]", offset_bits=0)],
                ),
            ],
        )
        kinds = _kinds(compare(old, new))
        assert ChangeKind.CONSTANT_CHANGED in kinds
        assert (
            ChangeKind.TYPE_SIZE_CHANGED in kinds
            or ChangeKind.STRUCT_SIZE_CHANGED in kinds
        )

    def test_enum_max_sentinel_widened_is_underlying_size_changed(self) -> None:
        """``format_kind_max = 0x7fff`` lifted to a value that no longer
        fits in 16 bits. case57 already covers this exact progression —
        the enum underlying type widens and every embedding struct
        re-lays out."""
        old = _snap(
            enums=[EnumType(
                name="format_kind_t",
                underlying_type="int",
                members=[
                    EnumMember("format_kind_undef", 0),
                    EnumMember("format_blocked", 2),
                    EnumMember("format_kind_max", 0x7fff),
                ],
            )],
        )
        new = _snap(
            enums=[EnumType(
                name="format_kind_t",
                underlying_type="long",   # widened
                members=[
                    EnumMember("format_kind_undef", 0),
                    EnumMember("format_blocked", 2),
                    EnumMember("format_kind_max", 0x7fff_ffff_ffff_ffff),
                ],
            )],
        )
        kinds = _kinds(compare(old, new))
        # Either the underlying-size detector or the last-member-value
        # detector should fire — both are existing kinds.
        assert (
            ChangeKind.ENUM_UNDERLYING_SIZE_CHANGED in kinds
            or ChangeKind.ENUM_LAST_MEMBER_VALUE_CHANGED in kinds
        )


# ===========================================================================
# S1 — Macro renumbered (public arg-slot identifiers)
# ===========================================================================
#
# Many runtime APIs expose a family of ``#define LIB_ARG_*`` numeric
# "slot" identifiers (think of any execution model where the caller
# passes a list of ``{int slot, void *value}`` to a "primitive execute"
# function). They are part of the runtime contract — the in-memory
# arg-list keys every operation. Renumbering them is silently
# catastrophic.
#
# Detection exists (CONSTANT_CHANGED), but the catalogue has no example
# making the "macro-used-as-runtime-slot-ID" failure mode concrete.


class TestS1MacroRuntimeSlotRenumbered:
    def test_arg_slot_macro_renumbered_is_caught(self) -> None:
        """The minimum signal: same name, different integer value."""
        old = _snap(constants={
            "LIB_ARG_SRC_0": "1",
            "LIB_ARG_DST_0": "17",
            "LIB_ARG_WEIGHTS_0": "33",
        })
        new = _snap(constants={
            "LIB_ARG_SRC_0": "2",  # <-- silently renumbered
            "LIB_ARG_DST_0": "17",
            "LIB_ARG_WEIGHTS_0": "33",
        })
        kinds = _kinds(compare(old, new))
        assert ChangeKind.CONSTANT_CHANGED in kinds, (
            "tool must flag a #define numeric value drift on a public "
            "LIB_ARG_* slot identifier"
        )

    def test_argument_slot_removed(self) -> None:
        """``LIB_ARG_WEIGHTS_0`` removed in a hypothetical cleanup. Old
        consumers passed integer 33 to the execute call; new library
        may now interpret 33 as something else."""
        old = _snap(constants={
            "LIB_ARG_SRC_0": "1",
            "LIB_ARG_WEIGHTS_0": "33",
        })
        new = _snap(constants={"LIB_ARG_SRC_0": "1"})
        kinds = _kinds(compare(old, new))
        assert ChangeKind.CONSTANT_REMOVED in kinds

    def test_runtime_sentinel_macro_changed(self) -> None:
        """``LIB_RUNTIME_DIM_VAL = INT64_MIN`` is a typical sentinel
        meaning "this dim is filled in at execution time". Changing the
        value breaks every consumer that hard-coded the magic number
        into a struct field or compared against it."""
        old = _snap(constants={
            "LIB_RUNTIME_DIM_VAL": "(-9223372036854775807LL - 1)",
            "LIB_RUNTIME_SIZE_VAL": "((size_t)(-9223372036854775807LL - 1))",
        })
        new = _snap(constants={
            "LIB_RUNTIME_DIM_VAL": "(-2147483648)",  # narrowed to int32
            "LIB_RUNTIME_SIZE_VAL": "((size_t)(-9223372036854775807LL - 1))",
        })
        kinds = _kinds(compare(old, new))
        assert ChangeKind.CONSTANT_CHANGED in kinds


# ===========================================================================
# S2 — Pointer-returned info struct grew
# ===========================================================================
#
# ``const library_version_t* library_version(void)`` — the library owns
# the struct (static const), so appending a field is:
#   * BACKWARD-compat for old consumers vs new lib (reads first N bytes)
#   * FORWARD-incompat for new consumers vs old lib (reads past end)
#
# Distinct from:
#   * case62  — fully opaque struct (no field visibility at all)
#   * case07/14 — by-value/heap struct (caller allocates → undersized)
#   * case48  — leaf-embedded struct propagated through container
#
# What the tool *should* catch today: at minimum
# ``TYPE_FIELD_ADDED`` / ``TYPE_SIZE_CHANGED`` /
# ``STRUCT_SIZE_CHANGED`` fires. Whether it is severity-asymmetric
# (forward-incompat) is an open question for follow-up policy work.
#
# Of all five scenarios in this file, S2 is the strongest candidate
# for a dedicated example case (``case90_*``) — it's the only one
# whose failure shape is not already exemplified somewhere in
# examples/.


def _version_struct(fields: list[tuple[str, str]], size_bits: int) -> RecordType:
    offset = 0
    members: list[TypeField] = []
    for name, ty in fields:
        # rough sizing: ints/unsigneds = 32, pointers = 64
        bits = 64 if "*" in ty else 32
        members.append(TypeField(name=name, type=ty, offset_bits=offset))
        offset += bits
    return RecordType(
        name="library_version_t",
        kind="struct",
        size_bits=size_bits,
        fields=members,
    )


class TestS2PointerReturnedInfoStructAppended:
    def test_field_appended_is_flagged(self) -> None:
        old_struct = _version_struct(
            [
                ("major", "int"),
                ("minor", "int"),
                ("patch", "int"),
                ("hash", "const char *"),
                ("cpu_runtime", "unsigned"),
                ("gpu_runtime", "unsigned"),
            ],
            size_bits=32 + 32 + 32 + 64 + 32 + 32,
        )
        new_struct = _version_struct(
            [
                ("major", "int"),
                ("minor", "int"),
                ("patch", "int"),
                ("hash", "const char *"),
                ("cpu_runtime", "unsigned"),
                ("gpu_runtime", "unsigned"),
                ("threadpool_runtime", "unsigned"),  # <-- appended
            ],
            size_bits=32 + 32 + 32 + 64 + 32 + 32 + 32,
        )
        old = _snap(
            types=[old_struct],
            functions=[_public_fn(
                "library_version",
                ret="const library_version_t *",
                is_extern_c=True,
            )],
        )
        new = _snap(
            types=[new_struct],
            functions=[_public_fn(
                "library_version",
                ret="const library_version_t *",
                is_extern_c=True,
            )],
        )
        kinds = _kinds(compare(old, new))
        # At least one of: field-add or size-change must surface.
        assert any(k in kinds for k in (
            ChangeKind.TYPE_FIELD_ADDED,
            ChangeKind.TYPE_FIELD_ADDED_COMPATIBLE,
            ChangeKind.TYPE_SIZE_CHANGED,
            ChangeKind.STRUCT_SIZE_CHANGED,
        )), (
            "appending a field to an info struct returned by pointer "
            "must surface as at least a field-add or size-change finding"
        )

    @pytest.mark.xfail(
        reason=(
            "Severity asymmetry not yet modelled: appending to a "
            "library-owned, pointer-returned info struct is forward-"
            "incompatible only (old consumer vs new lib = safe; new "
            "consumer vs old lib = read-past-end). abicheck currently "
            "reports a single severity. Tracked as gap S2-policy."
        ),
        strict=False,
    )
    def test_severity_should_reflect_forward_only_break(self) -> None:
        # Sentinel test — when the policy lands, the change should carry
        # an asymmetric severity (info or risk for backward path, error
        # for forward path). For now we record the gap.
        old = _snap(types=[_version_struct(
            [("major", "int"), ("minor", "int")], size_bits=64,
        )])
        new = _snap(types=[_version_struct(
            [("major", "int"), ("minor", "int"), ("flavor", "unsigned")],
            size_bits=96,
        )])
        result = compare(old, new)
        # When implemented, the relevant Change should carry an asymmetric
        # severity hint (e.g. description mentioning "forward-incompatible").
        hits = [c for c in result.changes if "forward" in (c.description or "").lower()]
        assert hits, "no asymmetric forward-only severity hint emitted yet"


# ===========================================================================
# S3 — Feature-macro-gated enum member skew
# ===========================================================================
#
# A common pattern in libraries with experimental features:
#
#   #if defined(LIB_EXPERIMENTAL_GROUPED)
#       sparse_grouped,
#   #endif
#
# Two builds of the same source produce different ABIs. The library is
# usually built with the macro defined; downstream consumers may parse
# headers with it undefined (or vice versa).


class TestS3FeatureMacroGatedEnumSkew:
    def test_member_present_only_in_one_snapshot(self) -> None:
        """Old snapshot was dumped with -DLIB_EXPERIMENTAL_GROUPED, new
        snapshot without — ``sparse_grouped`` enumerator disappears."""
        old = _snap(
            enums=[EnumType(
                name="sparse_encoding_t",
                members=[
                    EnumMember("sparse_encoding_undef", 0),
                    EnumMember("sparse_csr", 1),
                    EnumMember("sparse_packed", 2),
                    EnumMember("sparse_coo", 3),
                    EnumMember("sparse_grouped", 4),  # gated
                ],
            )],
            constants={"LIB_EXPERIMENTAL_GROUPED": "1"},
        )
        new = _snap(
            enums=[EnumType(
                name="sparse_encoding_t",
                members=[
                    EnumMember("sparse_encoding_undef", 0),
                    EnumMember("sparse_csr", 1),
                    EnumMember("sparse_packed", 2),
                    EnumMember("sparse_coo", 3),
                ],
            )],
            constants={},
        )
        kinds = _kinds(compare(old, new))
        assert ChangeKind.ENUM_MEMBER_REMOVED in kinds, (
            "removing an enum member must fire regardless of *why* it "
            "disappeared — a feature-macro toggle is no excuse for ABI skew"
        )
        # Also the gating macro change should be visible.
        assert ChangeKind.CONSTANT_REMOVED in kinds

    @pytest.mark.xfail(
        reason=(
            "Build-config-skew is not yet modelled as a first-class "
            "finding. We currently emit ENUM_MEMBER_REMOVED + "
            "CONSTANT_REMOVED but do not correlate the two as 'same "
            "source, different -D'. Tracked as gap S3-policy."
        ),
        strict=False,
    )
    def test_member_disappearance_is_correlated_with_macro_change(self) -> None:
        # Aspirational: a follow-up detector should notice that the
        # disappearing enum member coincides with a feature-gating macro
        # also disappearing, and emit a grouped "build-config skew"
        # overlay rather than two unrelated findings.
        old = _snap(
            enums=[EnumType("E", members=[
                EnumMember("a", 0), EnumMember("b", 1),
            ])],
            constants={"FEATURE_FOO": "1"},
        )
        new = _snap(
            enums=[EnumType("E", members=[EnumMember("a", 0)])],
            constants={},
        )
        result = compare(old, new)
        kinds = _kinds(result)
        # Look for an overlay (none exists yet).
        feature_gated = any(
            "feature" in (c.description or "").lower()
            and "gat" in (c.description or "").lower()
            for c in result.changes
        )
        assert feature_gated, (
            "expected a grouped 'feature-gated' overlay finding"
        )
        assert kinds  # silence unused


# ===========================================================================
# S4 — Internal value range leaks via public API
# ===========================================================================
#
# PR #238 covers internal *types* leaking via inheritance / embedding.
# Its companion is *values*: a library that defines internal-only
# enum constants packed into the same numeric range as a public enum:
#
#     const alg_kind_t internal_only_start = (alg_kind_t)(1 << 12);
#     const alg_kind_t eltwise_stochastic_round =
#         (alg_kind_t)(internal_only_start + 1);
#
# If a public API returns or accepts ``alg_kind_t`` and the internal
# range shifts (e.g. ``1 << 12 → 1 << 13``), a value that used to mean
# "stochastic round" now means a different op silently.


class TestS4InternalValueRangeLeak:
    def test_constant_change_is_at_least_caught(self) -> None:
        """Minimal floor: even without a dedicated overlay, the constant
        diff must fire."""
        old = _snap(constants={
            "internal_only_start": "4096",  # 1 << 12
            "eltwise_stochastic_round": "4097",
        })
        new = _snap(constants={
            "internal_only_start": "8192",  # 1 << 13
            "eltwise_stochastic_round": "8193",
        })
        kinds = _kinds(compare(old, new))
        assert ChangeKind.CONSTANT_CHANGED in kinds

    @pytest.mark.xfail(
        reason=(
            "No dedicated 'internal value range leaks via public API' "
            "ChangeKind yet. PR #238 added the type-leak counterpart; "
            "the value-leak companion needs a new ChangeKind "
            "INTERNAL_VALUE_RANGE_LEAKS_VIA_PUBLIC_API and a detector "
            "that joins (a) public function with parameter / return of "
            "the affected enum-or-int type with (b) an internal-named "
            "constant whose value changed. Tracked as gap S4."
        ),
        strict=False,
    )
    def test_internal_value_range_leak_overlay(self) -> None:
        # Public function accepts the value, internal constant changes.
        old = _snap(
            functions=[_public_fn(
                "lib_attr_set_post_ops_eltwise",
                ret="lib_status_t",
                params=[("alg_kind", "lib_alg_kind_t")],
                is_extern_c=True,
            )],
            constants={"internal_only_start": "4096"},
        )
        new = _snap(
            functions=[_public_fn(
                "lib_attr_set_post_ops_eltwise",
                ret="lib_status_t",
                params=[("alg_kind", "lib_alg_kind_t")],
                is_extern_c=True,
            )],
            constants={"internal_only_start": "8192"},
        )
        result = compare(old, new)
        assert any(
            "internal" in (c.description or "").lower()
            and ("value" in (c.description or "").lower()
                 or "range" in (c.description or "").lower())
            and c.symbol == "lib_attr_set_post_ops_eltwise"
            for c in result.changes
        ), "expected an overlay tying the internal-range shift to the public API"


# ===========================================================================
# S5 — Forward-compat tag appended (serialization / post-op kind)
# ===========================================================================
#
# Distinct from case81 (tag *reassignment* — silently misinterpreted) and
# case25 (plain enum member added — flagged as compatible).
#
# In a post-op kind list, appending a new ``BINARY_V2`` is
# backward-compatible for the *library* (old data fed to new lib parses
# fine) but forward-incompatible for the *application* (data produced
# by new lib fed to old lib trips an unknown-kind path).


class TestS5ForwardCompatTagAppended:
    def test_appended_serialization_tag_is_at_least_caught(self) -> None:
        """Minimum floor: a new enumerator must surface as
        ``ENUM_MEMBER_ADDED`` (existing kind)."""
        old = _snap(enums=[EnumType(
            name="post_op_kind",
            members=[
                EnumMember("post_op_undef", 0),
                EnumMember("post_op_sum", 1),
                EnumMember("post_op_eltwise", 2),
                EnumMember("post_op_binary", 3),
            ],
        )])
        new = _snap(enums=[EnumType(
            name="post_op_kind",
            members=[
                EnumMember("post_op_undef", 0),
                EnumMember("post_op_sum", 1),
                EnumMember("post_op_eltwise", 2),
                EnumMember("post_op_binary", 3),
                EnumMember("post_op_binary_v2", 4),  # <-- new
            ],
        )])
        kinds = _kinds(compare(old, new))
        assert ChangeKind.ENUM_MEMBER_ADDED in kinds

    def test_pure_append_does_not_trigger_reassignment_finding(self) -> None:
        """Pure append (no existing value changed): the checker should
        report ``ENUM_MEMBER_ADDED`` but NOT
        ``ENUM_MEMBER_VALUE_CHANGED`` — the latter is what
        case81-style silent reassignment looks like."""
        old = _snap(enums=[EnumType(
            name="post_op_kind",
            members=[
                EnumMember("post_op_sum", 1),
                EnumMember("post_op_eltwise", 2),
            ],
        )])
        new = _snap(enums=[EnumType(
            name="post_op_kind",
            members=[
                EnumMember("post_op_sum", 1),
                EnumMember("post_op_eltwise", 2),
                EnumMember("post_op_binary", 3),  # appended only
            ],
        )])
        kinds = _kinds(compare(old, new))
        assert ChangeKind.ENUM_MEMBER_ADDED in kinds
        assert ChangeKind.ENUM_MEMBER_VALUE_CHANGED not in kinds, (
            "pure append must not surface as a value-change finding"
        )

    def test_reassigning_existing_id_during_append_is_caught(self) -> None:
        """The regression this case is really guarding against: someone
        appends a new tag AND quietly renumbers an old one. The
        renumbering must surface as case81-shaped
        ``ENUM_MEMBER_VALUE_CHANGED`` (or a value-changed equivalent),
        independently of the append being flagged as ``_ADDED``."""
        old = _snap(enums=[EnumType(
            name="post_op_kind",
            members=[
                EnumMember("post_op_sum", 1),
                EnumMember("post_op_eltwise", 2),
            ],
        )])
        new = _snap(enums=[EnumType(
            name="post_op_kind",
            members=[
                EnumMember("post_op_sum", 1),
                EnumMember("post_op_eltwise", 3),   # <-- silently renumbered
                EnumMember("post_op_binary", 2),  # took eltwise's slot
            ],
        )])
        kinds = _kinds(compare(old, new))
        assert ChangeKind.ENUM_MEMBER_VALUE_CHANGED in kinds, (
            "renumbering an existing enum member during an 'append' must "
            "still surface — this is the case81 failure mode"
        )

    @pytest.mark.xfail(
        reason=(
            "Forward-compat asymmetry: a new tag is safe for "
            "backward-compat (old data → new lib) but breaks "
            "forward-compat (new data → old lib). abicheck reports a "
            "single severity. Tracked as gap S5-policy (related to S2)."
        ),
        strict=False,
    )
    def test_forward_compat_severity_hint(self) -> None:
        old = _snap(enums=[EnumType("K", members=[
            EnumMember("a", 0), EnumMember("b", 1),
        ])])
        new = _snap(enums=[EnumType("K", members=[
            EnumMember("a", 0), EnumMember("b", 1), EnumMember("c", 2),
        ])])
        result = compare(old, new)
        assert any(
            "forward" in (c.description or "").lower()
            for c in result.changes
        )


# ===========================================================================
# Meta-test — keep the coverage matrix honest.
# ===========================================================================


class TestCatalogueIntegrity:
    def test_referenced_change_kinds_all_exist(self) -> None:
        """Every ChangeKind referenced explicitly in the coverage matrix
        must still exist in the enum — guards against rename drift.

        Compares by name (not by attribute) so a rename surfaces here as
        a clear missing-member failure rather than as an import error
        somewhere else.
        """
        referenced_names = {
            "FUNC_REMOVED",
            "CONSTANT_CHANGED",
            "CONSTANT_REMOVED",
            "CONSTANT_ADDED",
            "ENUM_MEMBER_ADDED",
            "ENUM_MEMBER_REMOVED",
            "ENUM_LAST_MEMBER_VALUE_CHANGED",
            "ENUM_UNDERLYING_SIZE_CHANGED",
            "TYPE_FIELD_ADDED",
            "TYPE_FIELD_ADDED_COMPATIBLE",
            "TYPE_SIZE_CHANGED",
            "STRUCT_SIZE_CHANGED",
            "INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API",
        }
        existing_names = {k.name for k in ChangeKind}
        missing = referenced_names - existing_names
        assert not missing, (
            f"coverage matrix references ChangeKind names that no "
            f"longer exist in the enum: {sorted(missing)}"
        )

    def test_no_proposed_new_kinds_silently_shipped(self) -> None:
        """The matrix claims S4 needs a NEW ChangeKind
        (``INTERNAL_VALUE_RANGE_LEAKS_VIA_PUBLIC_API``). If someone adds
        that enum value without updating this test, the xfail above
        should be promoted to a real assertion."""
        proposed = "INTERNAL_VALUE_RANGE_LEAKS_VIA_PUBLIC_API"
        existing_names = {k.name for k in ChangeKind}
        if proposed in existing_names:
            pytest.fail(
                f"{proposed} now exists in ChangeKind — convert the "
                f"xfail in TestS4InternalValueRangeLeak to a strict "
                f"assertion."
            )
