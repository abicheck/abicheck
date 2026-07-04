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

"""False-positive / false-negative rate gate for public-header surface scoping.

ADR-024 §"Validation & testing strategy" §7 asks for an FP-rate gate "analogous
to the mypy baseline gate": track the count on a benchmark corpus and fail CI on
regression. This is the scoping-focused, build-free counterpart — a curated
corpus of synthetic ``(old, new)`` snapshot pairs, each labelled with its
ground-truth intent, run through ``compare(..., scope_to_public_surface=True)``:

* **internal-noise** cases (a change to a private/internal entity) must scope to
  a non-breaking verdict — a breaking verdict here is a **false positive**;
* **real-break** cases (a change to the public surface) must stay breaking —
  a non-breaking verdict here is a **false negative**.

The gate fails if either count drifts above its documented baseline. Both
baselines are **0**: the corpus is chosen so a correct implementation has a
clean sheet. Run locally with ``python scripts/check_fp_rate.py``.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from abicheck.build_mode import BuildMode, StdlibFamily  # noqa: E402
from abicheck.buildsource.build_evidence import BuildEvidence, BuildOption  # noqa: E402
from abicheck.buildsource.crosscheck import run_crosschecks  # noqa: E402
from abicheck.buildsource.pack import BuildSourcePack  # noqa: E402
from abicheck.buildsource.source_abi import SourceAbiSurface  # noqa: E402
from abicheck.buildsource.source_graph import (  # noqa: E402
    GraphEdge,
    GraphNode,
    SourceGraphSummary,
)
from abicheck.checker import Verdict, compare  # noqa: E402
from abicheck.checker_policy import ChangeKind  # noqa: E402
from abicheck.elf_metadata import ElfMetadata, ElfSymbol  # noqa: E402
from abicheck.model import (  # noqa: E402
    AbiSnapshot,
    EnumMember,
    EnumType,
    Function,
    Param,
    RecordType,
    ScopeOrigin,
    TypeField,
    Variable,
    Visibility,
)

# Verdicts that mean "this is a public-ABI break".
_BREAKING_VERDICTS = {Verdict.API_BREAK, Verdict.BREAKING}

# Documented baselines (see ADR-024 §7). Both are 0 — the corpus is built so a
# correct scoping implementation produces neither a false positive nor a false
# negative. Raise deliberately (with justification) only if the corpus changes.
FP_BASELINE = 0
FN_BASELINE = 0


def _fn(
    name, *, ret="void", params=(), vis=Visibility.PUBLIC, origin=ScopeOrigin.UNKNOWN
) -> Function:
    return Function(
        name=name,
        mangled=f"_Z{len(name)}{name}",
        return_type=ret,
        params=[Param(name=f"a{i}", type=t) for i, t in enumerate(params)],
        visibility=vis,
        origin=origin,
    )


def _rec(name, *, size=64, fields=(), origin=ScopeOrigin.UNKNOWN) -> RecordType:
    return RecordType(
        name=name,
        kind="struct",
        size_bits=size,
        fields=[TypeField(name=n, type=t) for n, t in fields],
        origin=origin,
    )


def _opaque(name, *, size=64) -> RecordType:
    """An opaque handle: forward-declared to consumers, so its (internal) layout
    is not observable through a pointer — the by-value-vs-pointer precision axis
    that scoping delegates to the downstream opaque filter (ADR-024 §D3)."""
    return RecordType(name=name, kind="struct", size_bits=size, is_opaque=True)


def _enum(name, members, *, underlying="int", origin=ScopeOrigin.UNKNOWN) -> EnumType:
    return EnumType(
        name=name,
        members=[EnumMember(name=n, value=v) for n, v in members],
        underlying_type=underlying,
        origin=origin,
    )


def _snap(
    version,
    *,
    functions=(),
    types=(),
    enums=(),
    variables=(),
    typedefs=None,
    build_mode=None,
) -> AbiSnapshot:
    snap = AbiSnapshot(
        library="libfp",
        version=version,
        functions=list(functions),
        types=list(types),
        enums=list(enums),
        variables=list(variables),
        build_mode=build_mode,
    )
    if typedefs:
        snap.typedefs = dict(typedefs)
    return snap


def _bm(stdlib: StdlibFamily) -> BuildMode:
    return BuildMode(stdlib=stdlib)


def _var(
    name, *, type="int", vis=Visibility.PUBLIC, origin=ScopeOrigin.UNKNOWN
) -> Variable:
    return Variable(
        name=name,
        mangled=f"_ZV{len(name)}{name}",
        type=type,
        visibility=vis,
        origin=origin,
    )


@dataclass(frozen=True)
class Case:
    name: str
    internal_noise: (
        bool  # True ⇒ must scope to non-breaking; False ⇒ must stay breaking
    )
    build: Callable[[], tuple[AbiSnapshot, AbiSnapshot]]


# --- internal-noise cases (a breaking verdict here is a FALSE POSITIVE) -------


def _internal_struct_size() -> tuple[AbiSnapshot, AbiSnapshot]:
    # InternalCache is referenced by no public API → its layout change is noise.
    old = _snap(
        "1",
        functions=[_fn("api", ret="Result *")],
        types=[_rec("Result", size=64), _rec("InternalCache", size=64)],
    )
    new = _snap(
        "2",
        functions=[_fn("api", ret="Result *")],
        types=[_rec("Result", size=64), _rec("InternalCache", size=128)],
    )
    return old, new


def _elf_only_function_removed() -> tuple[AbiSnapshot, AbiSnapshot]:
    old = _snap("1", functions=[_fn("api"), _fn("helper", vis=Visibility.ELF_ONLY)])
    new = _snap("2", functions=[_fn("api")])
    return old, new


def _internal_field_reordered() -> tuple[AbiSnapshot, AbiSnapshot]:
    # A struct nobody public reaches: reordering its fields is invisible to ABI.
    old = _snap(
        "1",
        functions=[_fn("api")],
        types=[_rec("InternalCache", size=128, fields=[("a", "int"), ("b", "long")])],
    )
    new = _snap(
        "2",
        functions=[_fn("api")],
        types=[_rec("InternalCache", size=128, fields=[("b", "long"), ("a", "int")])],
    )
    return old, new


def _hidden_function_signature_changed() -> tuple[AbiSnapshot, AbiSnapshot]:
    # A hidden-visibility helper is not part of the exported surface, so a
    # parameter change to it must not be reported as a public break.
    old = _snap(
        "1",
        functions=[
            _fn("api"),
            _fn("helper", params=("int",), vis=Visibility.HIDDEN),
        ],
    )
    new = _snap(
        "2",
        functions=[
            _fn("api"),
            _fn("helper", params=("long long",), vis=Visibility.HIDDEN),
        ],
    )
    return old, new


def _private_header_type_change() -> tuple[AbiSnapshot, AbiSnapshot]:
    # A type whose provenance is a private header (origin set as if dumped with a
    # public-header set) — demoted with the private-header reason.
    old = _snap(
        "1",
        functions=[_fn("api", ret="Result *", origin=ScopeOrigin.PUBLIC_HEADER)],
        types=[
            _rec("Result", size=64, origin=ScopeOrigin.PUBLIC_HEADER),
            _rec("PrivThing", size=64, origin=ScopeOrigin.PRIVATE_HEADER),
        ],
    )
    new = _snap(
        "2",
        functions=[_fn("api", ret="Result *", origin=ScopeOrigin.PUBLIC_HEADER)],
        types=[
            _rec("Result", size=64, origin=ScopeOrigin.PUBLIC_HEADER),
            _rec("PrivThing", size=128, origin=ScopeOrigin.PRIVATE_HEADER),
        ],
    )
    return old, new


def _same_stdlib_internal_stl_churn() -> tuple[AbiSnapshot, AbiSnapshot]:
    # Same stdlib family on both sides (libstdc++ → libstdc++): the comparison is
    # NOT cross-implementation, so std:: layout stays filtered as toolchain noise
    # and an internal, unreachable type embedding it produces no public break.
    # Guards that the cross-implementation filter relaxation did not regress the
    # ordinary same-toolchain path into emitting STL-layout false positives.
    old = _snap(
        "1",
        functions=[_fn("api")],
        types=[_rec("InternalCache", size=192, fields=[("data", "std::vector<int>")])],
        build_mode=_bm(StdlibFamily.LIBSTDCXX),
    )
    new = _snap(
        "2",
        functions=[_fn("api")],
        types=[_rec("InternalCache", size=256, fields=[("data", "std::vector<int>")])],
        build_mode=_bm(StdlibFamily.LIBSTDCXX),
    )
    return old, new


# --- enum-reachability + pointer/opaque precision (internal-noise) ------------
# These lock in the by-value-vs-pointer (ADR-024 §D3) and enum/typedef
# reachability behaviour the corpus previously left uncovered (see the NOTE
# below). Each is a change the current implementation already scopes correctly;
# they guard against a future regression, not assert a behaviour change.


def _internal_enum_value_changed() -> tuple[AbiSnapshot, AbiSnapshot]:
    # An enum no public API references: re-valuing a member is invisible to the
    # exported surface, so it must not raise a public break.
    old = _snap("1", functions=[_fn("api")], enums=[_enum("InternalMode", [("A", 0), ("B", 1)])])
    new = _snap("2", functions=[_fn("api")], enums=[_enum("InternalMode", [("A", 0), ("B", 5)])])
    return old, new


def _internal_enum_member_appended() -> tuple[AbiSnapshot, AbiSnapshot]:
    # Appending a member to an unreferenced internal enum is non-observable.
    old = _snap("1", functions=[_fn("api")], enums=[_enum("InternalMode", [("A", 0), ("B", 1)])])
    new = _snap(
        "2",
        functions=[_fn("api")],
        enums=[_enum("InternalMode", [("A", 0), ("B", 1), ("C", 2)])],
    )
    return old, new


def _enum_reached_only_via_internal_struct() -> tuple[AbiSnapshot, AbiSnapshot]:
    # The enum is used only by an internal (unreachable) struct's field, so the
    # transitive closure never reaches it from a public root.
    old = _snap(
        "1",
        functions=[_fn("api")],
        types=[_rec("InternalCfg", size=64, fields=[("mode", "Mode")])],
        enums=[_enum("Mode", [("A", 0), ("B", 1)])],
    )
    new = _snap(
        "2",
        functions=[_fn("api")],
        types=[_rec("InternalCfg", size=64, fields=[("mode", "Mode")])],
        enums=[_enum("Mode", [("A", 0), ("B", 9)])],
    )
    return old, new


def _opaque_handle_pointer_only_size() -> tuple[AbiSnapshot, AbiSnapshot]:
    # A public API returns a pointer to an *opaque* handle whose internal size
    # changes. Consumers only ever hold the pointer (they cannot see the layout),
    # so this is not a public break — the by-value-vs-pointer precision the
    # opaque filter supplies (ADR-024 §D3).
    old = _snap("1", functions=[_fn("api", ret="OpaqueH *")], types=[_opaque("OpaqueH", size=64)])
    new = _snap("2", functions=[_fn("api", ret="OpaqueH *")], types=[_opaque("OpaqueH", size=128)])
    return old, new


# --- real-break cases (a non-breaking verdict here is a FALSE NEGATIVE) -------


def _public_struct_size() -> tuple[AbiSnapshot, AbiSnapshot]:
    old = _snap(
        "1", functions=[_fn("api", ret="Result *")], types=[_rec("Result", size=64)]
    )
    new = _snap(
        "2", functions=[_fn("api", ret="Result *")], types=[_rec("Result", size=128)]
    )
    return old, new


def _public_function_removed() -> tuple[AbiSnapshot, AbiSnapshot]:
    old = _snap("1", functions=[_fn("api"), _fn("also_public")])
    new = _snap("2", functions=[_fn("api")])
    return old, new


def _public_param_type_changed() -> tuple[AbiSnapshot, AbiSnapshot]:
    old = _snap("1", functions=[_fn("api", params=("int",))])
    new = _snap("2", functions=[_fn("api", params=("long long",))])
    return old, new


def _leaked_internal_via_public_api() -> tuple[AbiSnapshot, AbiSnapshot]:
    # Reachability keeps a type used by a public function in-surface (anti-hiding):
    # changing it is observable to consumers even if it "looks" internal.
    old = _snap(
        "1",
        functions=[_fn("api", ret="Widget *")],
        types=[
            _rec("Widget", size=64, fields=[("impl", "Pixels")]),
            _rec("Pixels", size=64),
        ],
    )
    new = _snap(
        "2",
        functions=[_fn("api", ret="Widget *")],
        types=[
            _rec("Widget", size=64, fields=[("impl", "Pixels")]),
            _rec("Pixels", size=128),
        ],
    )
    return old, new


def _public_return_type_changed() -> tuple[AbiSnapshot, AbiSnapshot]:
    old = _snap("1", functions=[_fn("api", ret="int")])
    new = _snap("2", functions=[_fn("api", ret="long long")])
    return old, new


def _public_variable_removed() -> tuple[AbiSnapshot, AbiSnapshot]:
    # An exported data symbol disappearing breaks consumers that link it.
    old = _snap("1", functions=[_fn("api")], variables=[_var("g_config")])
    new = _snap("2", functions=[_fn("api")])
    return old, new


def _cross_stdlib_embedded_layout_diverges() -> tuple[AbiSnapshot, AbiSnapshot]:
    # The canonical std::vector trap: a public type embeds a std:: container by
    # value, and the two builds use *different* stdlib implementations
    # (libstdc++ → libc++). Across implementations that embedded type is laid out
    # differently — a real, cross-impl ABI break that must stay breaking. The
    # stdlib implementation detector fail-closes to BREAKING when layout evidence
    # shows a public owner type embedding std:: by value, even if the owner size
    # itself happens not to change.
    old = _snap(
        "1",
        functions=[_fn("make_buffer", ret="Buffer *")],
        types=[_rec("Buffer", size=192, fields=[("data", "std::vector<int>")])],
        build_mode=_bm(StdlibFamily.LIBSTDCXX),
    )
    new = _snap(
        "2",
        functions=[_fn("make_buffer", ret="Buffer *")],
        types=[_rec("Buffer", size=256, fields=[("data", "std::vector<int>")])],
        build_mode=_bm(StdlibFamily.LIBCXX),
    )
    return old, new


# --- enum-reachability + pointer precision (real-break counterparts) ----------
# The FN sentinels for the same axes: a change that *is* observable through the
# public surface must stay breaking, so the internal-noise cases above can never
# be widened into hiding a real break.


def _public_enum_value_changed() -> tuple[AbiSnapshot, AbiSnapshot]:
    # A public API takes the enum by value: re-valuing a member changes the
    # meaning of the argument every caller passes — a real API break.
    old = _snap(
        "1",
        functions=[_fn("api", params=("PublicMode",))],
        enums=[_enum("PublicMode", [("A", 0), ("B", 1)])],
    )
    new = _snap(
        "2",
        functions=[_fn("api", params=("PublicMode",))],
        enums=[_enum("PublicMode", [("A", 0), ("B", 5)])],
    )
    return old, new


def _enum_reached_via_public_struct_field() -> tuple[AbiSnapshot, AbiSnapshot]:
    # The enum is a field of a public (returned-by-pointer) struct, so it is
    # reachable through the closure and its value change stays breaking.
    old = _snap(
        "1",
        functions=[_fn("api", ret="Cfg *")],
        types=[_rec("Cfg", size=64, fields=[("mode", "Mode")])],
        enums=[_enum("Mode", [("A", 0), ("B", 1)])],
    )
    new = _snap(
        "2",
        functions=[_fn("api", ret="Cfg *")],
        types=[_rec("Cfg", size=64, fields=[("mode", "Mode")])],
        enums=[_enum("Mode", [("A", 0), ("B", 9)])],
    )
    return old, new


def _enum_reached_via_public_typedef() -> tuple[AbiSnapshot, AbiSnapshot]:
    # A public API references the enum through a typedef alias; the closure
    # follows typedef targets, so the value change stays in-surface.
    old = _snap(
        "1",
        functions=[_fn("api", params=("ModeAlias",))],
        enums=[_enum("Mode", [("A", 0), ("B", 1)])],
        typedefs={"ModeAlias": "Mode"},
    )
    new = _snap(
        "2",
        functions=[_fn("api", params=("ModeAlias",))],
        enums=[_enum("Mode", [("A", 0), ("B", 9)])],
        typedefs={"ModeAlias": "Mode"},
    )
    return old, new


def _defined_pointer_only_type_size() -> tuple[AbiSnapshot, AbiSnapshot]:
    # The counterpart to the opaque handle: the pointed-to type is *fully
    # defined* in the public surface, so a consumer can dereference/allocate it
    # by value — its size change is observable and must stay breaking. This is
    # why scoping must NOT blanket-demote pointer-only-reached types.
    old = _snap(
        "1",
        functions=[_fn("api", ret="Defined *")],
        types=[_rec("Defined", size=64, fields=[("x", "int")])],
    )
    new = _snap(
        "2",
        functions=[_fn("api", ret="Defined *")],
        types=[_rec("Defined", size=128, fields=[("x", "int")])],
    )
    return old, new


# --- versioned-symbol-scheme + multi-.so-bundle shapes (field-eval F2) --------
# The eval surfaced two real-world shapes the gate didn't cover: the
# versioned-symbol scheme (ICU `u_*_75`->`_78`, P08) and multi-.so bundles whose
# sibling libraries carry a SONAME version (P20). Each is added as a labelled
# pair so the public-surface scoping stays honest on them — baselines stay 0/0.


def _versioned_scheme_internal_churn() -> tuple[AbiSnapshot, AbiSnapshot]:
    # A version bump renames the *internal* (ELF-only) helpers `u_*_75`->`u_*_78`
    # while the public api is stable. The churn is not on the exported surface, so
    # scoping must keep it non-breaking — a breaking verdict here would be a false
    # positive on every routine versioned-scheme upgrade.
    old = _snap(
        "1",
        functions=[_fn("public_api")]
        + [_fn(f"u_{b}_75", vis=Visibility.ELF_ONLY) for b in ("a", "b", "c", "d")],
    )
    new = _snap(
        "2",
        functions=[_fn("public_api")]
        + [_fn(f"u_{b}_78", vis=Visibility.ELF_ONLY) for b in ("a", "b", "c", "d")],
    )
    return old, new


def _bundle_sibling_soname_churn() -> tuple[AbiSnapshot, AbiSnapshot]:
    # A multi-.so bundle (P20): the public library's api is stable, but symbols
    # from a private sibling library carrying its SONAME version (`_1_5_5` ->
    # `_1_5_7`) churn. Pairing across the bundle must not turn sibling-internal
    # (hidden) churn into a public break.
    old = _snap(
        "1",
        functions=[_fn("zstd_compress")]
        + [_fn(f"pool_{b}_1_5_5", vis=Visibility.HIDDEN) for b in ("a", "b", "c")],
    )
    new = _snap(
        "2",
        functions=[_fn("zstd_compress")]
        + [_fn(f"pool_{b}_1_5_7", vis=Visibility.HIDDEN) for b in ("a", "b", "c")],
    )
    return old, new


def _versioned_scheme_public_churn() -> tuple[AbiSnapshot, AbiSnapshot]:
    # The same `_75`->`_78` bump on the *public* surface genuinely removes every
    # exported symbol (renamed). Scoping must keep it breaking: the
    # versioned-scheme advisory/collapse is opt-in and must never silently hide a
    # real public removal under scoping.
    old = _snap("1", functions=[_fn(f"u_{b}_75") for b in ("a", "b", "c", "d")])
    new = _snap("2", functions=[_fn(f"u_{b}_78") for b in ("a", "b", "c", "d")])
    return old, new


# NOTE on corpus scope: every case here is one the *current* implementation
# already gets right, so a correct build keeps a clean 0/0 sheet (the gate's
# core invariant).
#
# The enum-reachability and pointer/opaque-precision cases below were once left
# out (their "correct" verdict was thought ambiguous). They are now covered with
# *both* polarities each — verified against the current implementation — so the
# gate guards them as regressions rather than asserting a behaviour change:
#   * internal (unreferenced) enum value / appended-member changes scope out;
#     the same change on a public-reachable enum stays breaking (enum
#     reachability now closes through struct fields and typedefs just like
#     struct reachability);
#   * a pointer-only-reached *opaque* handle's internal size change scopes out
#     (the by-value-vs-pointer precision the opaque filter supplies, ADR-024
#     §D3), while a pointer-only-reached *fully-defined* type's size change stays
#     breaking (a consumer can dereference/allocate it by value).
# One tempting case is still deliberately excluded: appending a field to a public
# struct is often a *compatible* extension, so it is not an unambiguous
# real-break — track it as detector/scoping work, not an FP-gate corpus entry.
CORPUS: list[Case] = [
    Case("internal_struct_size", True, _internal_struct_size),
    Case("elf_only_function_removed", True, _elf_only_function_removed),
    Case("internal_field_reordered", True, _internal_field_reordered),
    Case("hidden_function_signature_changed", True, _hidden_function_signature_changed),
    Case("private_header_type_change", True, _private_header_type_change),
    Case("same_stdlib_internal_stl_churn", True, _same_stdlib_internal_stl_churn),
    # enum reachability + pointer/opaque precision — internal-noise polarity.
    Case("internal_enum_value_changed", True, _internal_enum_value_changed),
    Case("internal_enum_member_appended", True, _internal_enum_member_appended),
    Case("enum_reached_only_via_internal_struct", True, _enum_reached_only_via_internal_struct),
    Case("opaque_handle_pointer_only_size", True, _opaque_handle_pointer_only_size),
    # field-eval F2: versioned-symbol scheme (P08) + multi-.so bundle (P20).
    Case("versioned_scheme_internal_churn", True, _versioned_scheme_internal_churn),
    Case("bundle_sibling_soname_churn", True, _bundle_sibling_soname_churn),
    # Cross-implementation stdlib: one real-break + one internal-noise guard.
    # The full breadth (libc++ ABI version, MSVC↔libstdc++, pointer-held-is-safe,
    # the symbol-only fallback and false-positive guards) lives in the detector's
    # unit tests (tests/test_diff_stdlib_impl.py); the corpus keeps only the two
    # minimal FP/FN sentinels for the public-surface scoping gate.
    Case(
        "cross_stdlib_embedded_layout_diverges",
        False,
        _cross_stdlib_embedded_layout_diverges,
    ),
    Case("public_struct_size", False, _public_struct_size),
    Case("public_function_removed", False, _public_function_removed),
    Case("public_param_type_changed", False, _public_param_type_changed),
    Case("public_return_type_changed", False, _public_return_type_changed),
    Case("public_variable_removed", False, _public_variable_removed),
    Case("leaked_internal_via_public_api", False, _leaked_internal_via_public_api),
    Case("versioned_scheme_public_churn", False, _versioned_scheme_public_churn),
    # enum reachability + pointer precision — real-break (FN sentinel) polarity.
    Case("public_enum_value_changed", False, _public_enum_value_changed),
    Case("enum_reached_via_public_struct_field", False, _enum_reached_via_public_struct_field),
    Case("enum_reached_via_public_typedef", False, _enum_reached_via_public_typedef),
    Case("defined_pointer_only_type_size", False, _defined_pointer_only_type_size),
]


# --------------------------------------------------------------------------- #
# Cross-source validation corpus (ADR-035 D4 / G19.2 promotion gate).
#
# The intra-version cross-checks (buildsource/crosscheck.py) stay advisory until
# a check earns its FP-rate-gate corpus (ADR-035 D4 / plan Phase 2 tail). This is
# that corpus: each labelled *single merged snapshot* is run through
# ``run_crosschecks`` and the target check must
#   * fire on a genuine hygiene issue (``should_fire=True``; a miss is a FN), and
#   * stay silent on a clean snapshot (``should_fire=False``; a stray finding is
#     a FP).
# Both baselines are 0 — the corpus is chosen so the current engine is clean. A
# check may be promoted to gating (``--crosscheck KEY=error``) only once it is
# represented here with both polarities.
# --------------------------------------------------------------------------- #


def _hsnap(**kw) -> AbiSnapshot:
    """A header-backed snapshot (provenance resolvable) for the cross-checks."""
    kw.setdefault("library", "libcc")
    kw.setdefault("version", "1")
    kw.setdefault("from_headers", True)
    return AbiSnapshot(**kw)


def _elf(*names: str) -> ElfMetadata:
    return ElfMetadata(symbols=[ElfSymbol(name=n) for n in names])


def _pub_fn(name, mangled, **kw) -> Function:
    return Function(
        name=name,
        mangled=mangled,
        return_type=kw.pop("ret", "void"),
        origin=kw.pop("origin", ScopeOrigin.PUBLIC_HEADER),
        **kw,
    )


def _abi_pack(*flags: str) -> BuildSourcePack:
    be = BuildEvidence(
        build_options=[BuildOption(key=k, value="1", abi_relevant=True) for k in flags]
    )
    return BuildSourcePack(root="", build_evidence=be)


# -- exported_not_public ----------------------------------------------------- #


def _cc_exported_not_public_fire() -> AbiSnapshot:
    snap = _hsnap(elf=_elf("_Z3apiv", "_Z6secretv"))
    snap.functions = [
        _pub_fn("api", "_Z3apiv"),
        _pub_fn("secret", "_Z6secretv", origin=ScopeOrigin.EXPORT_ONLY),
    ]
    return snap


def _cc_exported_not_public_clean() -> AbiSnapshot:
    snap = _hsnap(elf=_elf("_Z3apiv"))
    snap.functions = [_pub_fn("api", "_Z3apiv")]
    return snap


# -- public_not_exported ----------------------------------------------------- #


def _cc_public_not_exported_fire() -> AbiSnapshot:
    # A public decl promising _Z7missingv that the binary does not export.
    snap = _hsnap(elf=_elf("_Z3apiv"))
    snap.functions = [_pub_fn("api", "_Z3apiv"), _pub_fn("missing", "_Z7missingv")]
    return snap


def _cc_public_not_exported_clean() -> AbiSnapshot:
    snap = _hsnap(elf=_elf("_Z3apiv"))
    snap.functions = [_pub_fn("api", "_Z3apiv")]
    return snap


# -- header_build_context_mismatch ------------------------------------------- #


def _cc_header_build_context_mismatch_fire() -> AbiSnapshot:
    snap = _hsnap(build_source=_abi_pack("glibcxx_use_cxx11_abi"))
    snap.parsed_with_build_context = False
    return snap


def _cc_header_build_context_mismatch_clean() -> AbiSnapshot:
    snap = _hsnap(build_source=_abi_pack("glibcxx_use_cxx11_abi"))
    snap.parsed_with_build_context = True
    return snap


# -- private_header_leak ----------------------------------------------------- #


def _cc_private_header_leak_fire() -> AbiSnapshot:
    snap = _hsnap(elf=_elf("_Z3usev"))
    snap.functions = [_pub_fn("use", "_Z3usev", ret="Impl *")]
    snap.types = [
        RecordType(name="Impl", kind="struct", origin=ScopeOrigin.PRIVATE_HEADER)
    ]
    return snap


def _cc_private_header_leak_clean() -> AbiSnapshot:
    snap = _hsnap(elf=_elf("_Z3usev"))
    snap.functions = [_pub_fn("use", "_Z3usev", ret="Widget *")]
    snap.types = [
        RecordType(name="Widget", kind="struct", origin=ScopeOrigin.PUBLIC_HEADER)
    ]
    return snap


# -- odr_type_variant -------------------------------------------------------- #


def _cc_odr_type_variant_fire() -> AbiSnapshot:
    surface = SourceAbiSurface(
        odr_conflicts=[
            {
                "qualified_name": "ns::Widget",
                "header": "widget.h",
                "old_type_hash": "a",
                "new_type_hash": "b",
            }
        ]
    )
    return _hsnap(build_source=BuildSourcePack(root="", source_abi=surface))


def _cc_odr_type_variant_clean() -> AbiSnapshot:
    return _hsnap(build_source=BuildSourcePack(root="", source_abi=SourceAbiSurface()))


# -- public_to_internal_dependency ------------------------------------------- #


def _cc_public_to_internal_dependency_fire() -> AbiSnapshot:
    g = SourceGraphSummary(
        nodes=[
            GraphNode(
                id="decl://pub",
                kind="source_decl",
                label="pubFn",
                attrs={"visibility": "public_header"},
            ),
            GraphNode(
                id="decl://impl",
                kind="source_decl",
                label="implHelper",
                attrs={"visibility": "source"},
            ),
        ],
        edges=[GraphEdge(src="decl://pub", dst="decl://impl", kind="DECL_CALLS_DECL")],
    )
    return _hsnap(build_source=BuildSourcePack(root="", source_graph=g))


def _cc_public_to_internal_dependency_clean() -> AbiSnapshot:
    g = SourceGraphSummary(
        nodes=[
            GraphNode(
                id="decl://pub",
                kind="source_decl",
                label="pubFn",
                attrs={"visibility": "public_header"},
            ),
            GraphNode(
                id="decl://pub2",
                kind="source_decl",
                label="otherPub",
                attrs={"visibility": "public_header"},
            ),
        ],
        edges=[GraphEdge(src="decl://pub", dst="decl://pub2", kind="DECL_CALLS_DECL")],
    )
    return _hsnap(build_source=BuildSourcePack(root="", source_graph=g))


# -- unversioned_exported_symbol (ADR-035 D8) -------------------------------- #


def _cc_unversioned_exported_symbol_fire() -> AbiSnapshot:
    elf = ElfMetadata(
        symbols=[
            ElfSymbol(name="_Z3apiv", version="FOO_1.0"),
            ElfSymbol(name="_Z6legacyv", version=""),
        ],
        versions_defined=["FOO_1.0"],
    )
    return _hsnap(elf=elf)


def _cc_unversioned_exported_symbol_clean() -> AbiSnapshot:
    # No versioning scheme → nothing to flag.
    elf = ElfMetadata(
        symbols=[ElfSymbol(name="_Z3apiv", version="")], versions_defined=[]
    )
    return _hsnap(elf=elf)


# -- rtti_for_internal_type (ADR-035 D8) ------------------------------------- #


def _cc_rtti_for_internal_type_fire() -> AbiSnapshot:
    snap = _hsnap(elf=_elf("_ZTI8Internal", "_Z3apiv"))
    snap.functions = [_pub_fn("api", "_Z3apiv")]
    snap.types = [
        RecordType(name="Internal", kind="class", origin=ScopeOrigin.PRIVATE_HEADER)
    ]
    return snap


def _cc_rtti_for_internal_type_clean() -> AbiSnapshot:
    snap = _hsnap(elf=_elf("_ZTI6Widget", "_Z3apiv"))
    snap.functions = [_pub_fn("api", "_Z3apiv")]
    snap.types = [
        RecordType(name="Widget", kind="class", origin=ScopeOrigin.PUBLIC_HEADER)
    ]
    return snap


@dataclass(frozen=True)
class CrosscheckCase:
    name: str
    kind: ChangeKind  # the ChangeKind the case targets
    should_fire: bool  # True ⇒ must be flagged (miss = FN); False ⇒ clean (hit = FP)
    build: Callable[[], AbiSnapshot]


CROSSCHECK_CORPUS: list[CrosscheckCase] = [
    CrosscheckCase(
        "exported_not_public_fire",
        ChangeKind.EXPORTED_NOT_PUBLIC,
        True,
        _cc_exported_not_public_fire,
    ),
    CrosscheckCase(
        "exported_not_public_clean",
        ChangeKind.EXPORTED_NOT_PUBLIC,
        False,
        _cc_exported_not_public_clean,
    ),
    CrosscheckCase(
        "public_not_exported_fire",
        ChangeKind.PUBLIC_NOT_EXPORTED,
        True,
        _cc_public_not_exported_fire,
    ),
    CrosscheckCase(
        "public_not_exported_clean",
        ChangeKind.PUBLIC_NOT_EXPORTED,
        False,
        _cc_public_not_exported_clean,
    ),
    CrosscheckCase(
        "header_build_context_mismatch_fire",
        ChangeKind.HEADER_BUILD_CONTEXT_MISMATCH,
        True,
        _cc_header_build_context_mismatch_fire,
    ),
    CrosscheckCase(
        "header_build_context_mismatch_clean",
        ChangeKind.HEADER_BUILD_CONTEXT_MISMATCH,
        False,
        _cc_header_build_context_mismatch_clean,
    ),
    CrosscheckCase(
        "private_header_leak_fire",
        ChangeKind.PRIVATE_HEADER_LEAK,
        True,
        _cc_private_header_leak_fire,
    ),
    CrosscheckCase(
        "private_header_leak_clean",
        ChangeKind.PRIVATE_HEADER_LEAK,
        False,
        _cc_private_header_leak_clean,
    ),
    CrosscheckCase(
        "odr_type_variant_fire",
        ChangeKind.ODR_TYPE_VARIANT,
        True,
        _cc_odr_type_variant_fire,
    ),
    CrosscheckCase(
        "odr_type_variant_clean",
        ChangeKind.ODR_TYPE_VARIANT,
        False,
        _cc_odr_type_variant_clean,
    ),
    CrosscheckCase(
        "public_to_internal_dependency_fire",
        ChangeKind.PUBLIC_TO_INTERNAL_DEPENDENCY,
        True,
        _cc_public_to_internal_dependency_fire,
    ),
    CrosscheckCase(
        "public_to_internal_dependency_clean",
        ChangeKind.PUBLIC_TO_INTERNAL_DEPENDENCY,
        False,
        _cc_public_to_internal_dependency_clean,
    ),
    CrosscheckCase(
        "unversioned_exported_symbol_fire",
        ChangeKind.UNVERSIONED_EXPORTED_SYMBOL,
        True,
        _cc_unversioned_exported_symbol_fire,
    ),
    CrosscheckCase(
        "unversioned_exported_symbol_clean",
        ChangeKind.UNVERSIONED_EXPORTED_SYMBOL,
        False,
        _cc_unversioned_exported_symbol_clean,
    ),
    CrosscheckCase(
        "rtti_for_internal_type_fire",
        ChangeKind.RTTI_FOR_INTERNAL_TYPE,
        True,
        _cc_rtti_for_internal_type_fire,
    ),
    CrosscheckCase(
        "rtti_for_internal_type_clean",
        ChangeKind.RTTI_FOR_INTERNAL_TYPE,
        False,
        _cc_rtti_for_internal_type_clean,
    ),
]

# Promotion-gate baselines (ADR-035 D4). Both 0 — the corpus is clean today.
CC_FP_BASELINE = 0
CC_FN_BASELINE = 0


def evaluate_crosschecks(corpus: list[CrosscheckCase] | None = None) -> Outcome:
    """Run the cross-check corpus and collect FP / FN case names."""
    corpus = corpus if corpus is not None else CROSSCHECK_CORPUS
    fp: list[str] = []
    fn: list[str] = []
    for case in corpus:
        result = run_crosschecks(case.build())
        fired = any(c.kind == case.kind for c in result.findings)
        if case.should_fire and not fired:
            fn.append(case.name)
        elif not case.should_fire and fired:
            fp.append(case.name)
    return Outcome(false_positives=fp, false_negatives=fn)


@dataclass
class Outcome:
    false_positives: list[str]
    false_negatives: list[str]


def evaluate(corpus: list[Case] = CORPUS) -> Outcome:
    """Run the corpus under scoping and collect FP / FN case names."""
    fp: list[str] = []
    fn: list[str] = []
    for case in corpus:
        old, new = case.build()
        result = compare(old, new, scope_to_public_surface=True)
        is_breaking = result.verdict in _BREAKING_VERDICTS
        if case.internal_noise and is_breaking:
            fp.append(case.name)
        elif not case.internal_noise and not is_breaking:
            fn.append(case.name)
    return Outcome(false_positives=fp, false_negatives=fn)


# --------------------------------------------------------------------------- #
# Per-axis accuracy breakdown (trend reporting).
#
# The single 0/0 headline proves "no regression" but hides *which* accuracy axis
# a higher evidence layer bought. Tagging each case with the scoping axis it
# exercises lets CI archive the per-axis case counts (like ``eval/`` archives its
# results) and read the trend over releases: when a new layer/detector removes a
# class of false positive, the matching axis grows a covered case here.
# --------------------------------------------------------------------------- #

CASE_CATEGORY: dict[str, str] = {
    # struct/record layout reachability
    "internal_struct_size": "struct-layout",
    "internal_field_reordered": "struct-layout",
    "public_struct_size": "struct-layout",
    "leaked_internal_via_public_api": "struct-layout",
    # symbol linkage / visibility
    "elf_only_function_removed": "symbol-linkage",
    "hidden_function_signature_changed": "symbol-linkage",
    "public_function_removed": "symbol-linkage",
    "public_param_type_changed": "symbol-linkage",
    "public_return_type_changed": "symbol-linkage",
    "public_variable_removed": "symbol-linkage",
    # declaration provenance (private/system header)
    "private_header_type_change": "provenance-header",
    # stdlib implementation attribution
    "same_stdlib_internal_stl_churn": "stdlib-impl",
    "cross_stdlib_embedded_layout_diverges": "stdlib-impl",
    # versioned-symbol scheme / multi-.so bundle
    "versioned_scheme_internal_churn": "versioned-scheme",
    "bundle_sibling_soname_churn": "versioned-scheme",
    "versioned_scheme_public_churn": "versioned-scheme",
    # enum reachability closure
    "internal_enum_value_changed": "enum-reachability",
    "internal_enum_member_appended": "enum-reachability",
    "enum_reached_only_via_internal_struct": "enum-reachability",
    "public_enum_value_changed": "enum-reachability",
    "enum_reached_via_public_struct_field": "enum-reachability",
    "enum_reached_via_public_typedef": "enum-reachability",
    # by-value-vs-pointer / opaque-handle precision
    "opaque_handle_pointer_only_size": "pointer-opaque",
    "defined_pointer_only_type_size": "pointer-opaque",
}


def uncategorized_cases() -> list[str]:
    """Corpus cases lacking a CASE_CATEGORY axis tag (would fall into the
    ``uncategorized`` bucket in the trend breakdown). Enforced from ``main()``
    and mirrored by ``test_every_case_carries_a_category_tag`` — not an
    import-time ``assert`` (a module side effect, stripped under ``python -O``;
    scripts/CLAUDE.md forbids import-time behaviour)."""
    return sorted({c.name for c in CORPUS} - set(CASE_CATEGORY))


def _category_of(name: str) -> str:
    return CASE_CATEGORY.get(name, "uncategorized")


def category_breakdown(
    outcome: Outcome | None = None, corpus: list[Case] = CORPUS
) -> dict[str, dict[str, int]]:
    """Per-axis case counts + FP/FN tally for trend archiving.

    Each axis maps to ``{cases, internal_noise, real_break, false_positives,
    false_negatives}``. Sorted by axis name so the JSON is diff-stable across
    runs (CI can archive it and compare release-over-release).
    """
    outcome = outcome or evaluate(corpus)
    fp_set = set(outcome.false_positives)
    fn_set = set(outcome.false_negatives)
    out: dict[str, dict[str, int]] = {}
    for case in corpus:
        cat = _category_of(case.name)
        row = out.setdefault(
            cat,
            {
                "cases": 0,
                "internal_noise": 0,
                "real_break": 0,
                "false_positives": 0,
                "false_negatives": 0,
            },
        )
        row["cases"] += 1
        row["internal_noise" if case.internal_noise else "real_break"] += 1
        if case.name in fp_set:
            row["false_positives"] += 1
        if case.name in fn_set:
            row["false_negatives"] += 1
    return dict(sorted(out.items()))


def metrics(outcome: Outcome | None = None) -> dict[str, object]:
    """ADR-033 D9 metrics for the FP-rate gate — counts and deltas vs baseline.

    ``false_positive_delta_vs_baseline`` / ``false_negative_delta_vs_baseline``
    are the ADR-033 D9 signals: 0 means on-baseline, positive means a regression.
    ``by_category`` is the per-axis breakdown for trend archiving (see
    :func:`category_breakdown`).
    """
    outcome = outcome or evaluate()
    n_fp, n_fn = len(outcome.false_positives), len(outcome.false_negatives)
    cc = evaluate_crosschecks()
    cc_fp, cc_fn = len(cc.false_positives), len(cc.false_negatives)
    return {
        "cases": len(CORPUS),
        "false_positives": n_fp,
        "false_negatives": n_fn,
        "false_positive_delta_vs_baseline": n_fp - FP_BASELINE,
        "false_negative_delta_vs_baseline": n_fn - FN_BASELINE,
        "crosscheck_cases": len(CROSSCHECK_CORPUS),
        "crosscheck_false_positives": cc_fp,
        "crosscheck_false_negatives": cc_fn,
        "crosscheck_false_positive_delta_vs_baseline": cc_fp - CC_FP_BASELINE,
        "crosscheck_false_negative_delta_vs_baseline": cc_fn - CC_FN_BASELINE,
        "by_category": category_breakdown(outcome),
    }


def render_markdown(m: dict[str, object]) -> str:
    """Render the per-axis accuracy breakdown as a Markdown table.

    Suitable for a GitHub step summary so the FP/FN trend can be read per axis
    over releases, not just as a single pass/fail. The headline line carries the
    delta-vs-baseline so a regression is legible at a glance.
    """
    by_cat = m["by_category"]
    assert isinstance(by_cat, dict)
    lines = [
        f"### FP-rate gate — {m['cases']} cases, "
        f"{m['false_positives']} FP / {m['false_negatives']} FN "
        f"(Δ baseline: {m['false_positive_delta_vs_baseline']:+d} FP, "
        f"{m['false_negative_delta_vs_baseline']:+d} FN)",
        "",
        "| Axis | Cases | Internal-noise | Real-break | FP | FN |",
        "|------|------:|---------------:|-----------:|---:|---:|",
    ]
    for axis, row in by_cat.items():
        lines.append(
            f"| {axis} | {row['cases']} | {row['internal_noise']} | "
            f"{row['real_break']} | {row['false_positives']} | {row['false_negatives']} |"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Public-surface FP-rate gate.")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the ADR-033 D9 metrics (counts + delta-vs-baseline + "
        "per-axis by_category breakdown) as JSON — archive it for trend tracking.",
    )
    parser.add_argument(
        "--markdown",
        action="store_true",
        help="Print the per-axis accuracy breakdown as a Markdown table "
        "(for a CI step summary / release-over-release trend).",
    )
    args = parser.parse_args(argv)

    missing_axis = uncategorized_cases()
    if missing_axis:
        print(
            f"ERROR: FP-rate corpus cases missing a CASE_CATEGORY axis tag: {missing_axis}",
            file=sys.stderr,
        )
        return 1

    outcome = evaluate()
    cc_outcome = evaluate_crosschecks()
    m = metrics(outcome)
    n_fp, n_fn = m["false_positives"], m["false_negatives"]
    cc_fp, cc_fn = m["crosscheck_false_positives"], m["crosscheck_false_negatives"]

    if args.json:
        import json

        print(json.dumps(m, indent=2))
    elif args.markdown:
        print(render_markdown(m))
    else:
        print(
            f"FP-rate gate: {len(CORPUS)} cases — {n_fp} false positive(s), {n_fn} false negative(s)"
        )
        if outcome.false_positives:
            print(
                f"  false positives (internal noise reported as breaking): {outcome.false_positives}"
            )
        if outcome.false_negatives:
            print(
                f"  false negatives (real break scoped away):               {outcome.false_negatives}"
            )
        print(
            "  delta vs baseline: "
            f"false_positive_delta={m['false_positive_delta_vs_baseline']}, "
            f"false_negative_delta={m['false_negative_delta_vs_baseline']}"
        )
        print(
            f"Cross-check gate: {len(CROSSCHECK_CORPUS)} cases — "
            f"{cc_fp} false positive(s), {cc_fn} false negative(s)"
        )
        if cc_outcome.false_positives:
            print(
                f"  false positives (clean snapshot flagged):  {cc_outcome.false_positives}"
            )
        if cc_outcome.false_negatives:
            print(
                f"  false negatives (hygiene issue missed):    {cc_outcome.false_negatives}"
            )

    # In --json mode the error lines go to stderr so stdout stays a single valid
    # JSON document even on a gate failure (the case CI most needs to parse).
    err = sys.stderr if args.json else sys.stdout
    failed = False
    if n_fp > FP_BASELINE:
        print(f"ERROR: false positives {n_fp} exceed baseline {FP_BASELINE}", file=err)
        failed = True
    if n_fn > FN_BASELINE:
        print(f"ERROR: false negatives {n_fn} exceed baseline {FN_BASELINE}", file=err)
        failed = True
    if cc_fp > CC_FP_BASELINE:
        print(
            f"ERROR: cross-check false positives {cc_fp} exceed baseline {CC_FP_BASELINE}",
            file=err,
        )
        failed = True
    if cc_fn > CC_FN_BASELINE:
        print(
            f"ERROR: cross-check false negatives {cc_fn} exceed baseline {CC_FN_BASELINE}",
            file=err,
        )
        failed = True
    if not failed and not args.json:
        print("FP-rate gate: OK (within baseline)")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
