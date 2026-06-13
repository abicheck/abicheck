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
from abicheck.checker import Verdict, compare  # noqa: E402
from abicheck.model import (  # noqa: E402
    AbiSnapshot,
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


def _fn(name, *, ret="void", params=(), vis=Visibility.PUBLIC,
        origin=ScopeOrigin.UNKNOWN) -> Function:
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


def _snap(version, *, functions=(), types=(), enums=(), variables=(),
          build_mode=None) -> AbiSnapshot:
    return AbiSnapshot(
        library="libfp", version=version,
        functions=list(functions), types=list(types), enums=list(enums),
        variables=list(variables), build_mode=build_mode,
    )


def _bm(stdlib: StdlibFamily) -> BuildMode:
    return BuildMode(stdlib=stdlib)


def _var(name, *, type="int", vis=Visibility.PUBLIC,
         origin=ScopeOrigin.UNKNOWN) -> Variable:
    return Variable(
        name=name, mangled=f"_ZV{len(name)}{name}", type=type,
        visibility=vis, origin=origin,
    )


@dataclass(frozen=True)
class Case:
    name: str
    internal_noise: bool  # True ⇒ must scope to non-breaking; False ⇒ must stay breaking
    build: Callable[[], tuple[AbiSnapshot, AbiSnapshot]]


# --- internal-noise cases (a breaking verdict here is a FALSE POSITIVE) -------

def _internal_struct_size() -> tuple[AbiSnapshot, AbiSnapshot]:
    # InternalCache is referenced by no public API → its layout change is noise.
    old = _snap("1", functions=[_fn("api", ret="Result *")],
                types=[_rec("Result", size=64), _rec("InternalCache", size=64)])
    new = _snap("2", functions=[_fn("api", ret="Result *")],
                types=[_rec("Result", size=64), _rec("InternalCache", size=128)])
    return old, new


def _elf_only_function_removed() -> tuple[AbiSnapshot, AbiSnapshot]:
    old = _snap("1", functions=[_fn("api"), _fn("helper", vis=Visibility.ELF_ONLY)])
    new = _snap("2", functions=[_fn("api")])
    return old, new


def _internal_field_reordered() -> tuple[AbiSnapshot, AbiSnapshot]:
    # A struct nobody public reaches: reordering its fields is invisible to ABI.
    old = _snap("1", functions=[_fn("api")],
                types=[_rec("InternalCache", size=128,
                            fields=[("a", "int"), ("b", "long")])])
    new = _snap("2", functions=[_fn("api")],
                types=[_rec("InternalCache", size=128,
                            fields=[("b", "long"), ("a", "int")])])
    return old, new


def _hidden_function_signature_changed() -> tuple[AbiSnapshot, AbiSnapshot]:
    # A hidden-visibility helper is not part of the exported surface, so a
    # parameter change to it must not be reported as a public break.
    old = _snap("1", functions=[
        _fn("api"),
        _fn("helper", params=("int",), vis=Visibility.HIDDEN),
    ])
    new = _snap("2", functions=[
        _fn("api"),
        _fn("helper", params=("long long",), vis=Visibility.HIDDEN),
    ])
    return old, new


def _private_header_type_change() -> tuple[AbiSnapshot, AbiSnapshot]:
    # A type whose provenance is a private header (origin set as if dumped with a
    # public-header set) — demoted with the private-header reason.
    old = _snap("1", functions=[_fn("api", ret="Result *", origin=ScopeOrigin.PUBLIC_HEADER)],
                types=[_rec("Result", size=64, origin=ScopeOrigin.PUBLIC_HEADER),
                       _rec("PrivThing", size=64, origin=ScopeOrigin.PRIVATE_HEADER)])
    new = _snap("2", functions=[_fn("api", ret="Result *", origin=ScopeOrigin.PUBLIC_HEADER)],
                types=[_rec("Result", size=64, origin=ScopeOrigin.PUBLIC_HEADER),
                       _rec("PrivThing", size=128, origin=ScopeOrigin.PRIVATE_HEADER)])
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
        types=[_rec("InternalCache", size=192,
                    fields=[("data", "std::vector<int>")])],
        build_mode=_bm(StdlibFamily.LIBSTDCXX),
    )
    new = _snap(
        "2",
        functions=[_fn("api")],
        types=[_rec("InternalCache", size=256,
                    fields=[("data", "std::vector<int>")])],
        build_mode=_bm(StdlibFamily.LIBSTDCXX),
    )
    return old, new


# --- real-break cases (a non-breaking verdict here is a FALSE NEGATIVE) -------

def _public_struct_size() -> tuple[AbiSnapshot, AbiSnapshot]:
    old = _snap("1", functions=[_fn("api", ret="Result *")], types=[_rec("Result", size=64)])
    new = _snap("2", functions=[_fn("api", ret="Result *")], types=[_rec("Result", size=128)])
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
    old = _snap("1", functions=[_fn("api", ret="Widget *")],
                types=[_rec("Widget", size=64, fields=[("impl", "Pixels")]),
                       _rec("Pixels", size=64)])
    new = _snap("2", functions=[_fn("api", ret="Widget *")],
                types=[_rec("Widget", size=64, fields=[("impl", "Pixels")]),
                       _rec("Pixels", size=128)])
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
    # differently, so the public *owner* type's size diverges — a real, cross-impl
    # ABI break that must stay breaking. The owner type (Buffer) is non-std:: and
    # is never filtered, so its TYPE_SIZE_CHANGED is caught through the ordinary
    # path; the cross-implementation build_mode adds the RISK build-mode finding.
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


# NOTE on corpus scope: every case here is one the *current* implementation
# already gets right, so a correct build keeps a clean 0/0 sheet (the gate's
# core invariant). Two tempting cases were deliberately left out because their
# "correct" verdict is genuinely ambiguous and would make this gate assert a
# behaviour change rather than guard a regression:
#   * an internal (unreferenced) enum value change — enum reachability scoping
#     is coarser than struct reachability;
#   * appending a field to a public struct — often a *compatible* extension, so
#     it is not an unambiguous real-break.
# Track those as detector/scoping work, not as FP-gate corpus entries.
CORPUS: list[Case] = [
    Case("internal_struct_size", True, _internal_struct_size),
    Case("elf_only_function_removed", True, _elf_only_function_removed),
    Case("internal_field_reordered", True, _internal_field_reordered),
    Case("hidden_function_signature_changed", True, _hidden_function_signature_changed),
    Case("private_header_type_change", True, _private_header_type_change),
    Case("same_stdlib_internal_stl_churn", True, _same_stdlib_internal_stl_churn),
    # Cross-implementation stdlib: one real-break + one internal-noise guard.
    # The full breadth (libc++ ABI version, MSVC↔libstdc++, pointer-held-is-safe,
    # the symbol-only fallback and false-positive guards) lives in the detector's
    # unit tests (tests/test_diff_stdlib_impl.py); the corpus keeps only the two
    # minimal FP/FN sentinels for the public-surface scoping gate.
    Case("cross_stdlib_embedded_layout_diverges", False,
         _cross_stdlib_embedded_layout_diverges),
    Case("public_struct_size", False, _public_struct_size),
    Case("public_function_removed", False, _public_function_removed),
    Case("public_param_type_changed", False, _public_param_type_changed),
    Case("public_return_type_changed", False, _public_return_type_changed),
    Case("public_variable_removed", False, _public_variable_removed),
    Case("leaked_internal_via_public_api", False, _leaked_internal_via_public_api),
]


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


def metrics(outcome: Outcome | None = None) -> dict[str, int]:
    """ADR-033 D9 metrics for the FP-rate gate — counts and deltas vs baseline.

    ``false_positive_delta_vs_baseline`` / ``false_negative_delta_vs_baseline``
    are the ADR-033 D9 signals: 0 means on-baseline, positive means a regression.
    """
    outcome = outcome or evaluate()
    n_fp, n_fn = len(outcome.false_positives), len(outcome.false_negatives)
    return {
        "cases": len(CORPUS),
        "false_positives": n_fp,
        "false_negatives": n_fn,
        "false_positive_delta_vs_baseline": n_fp - FP_BASELINE,
        "false_negative_delta_vs_baseline": n_fn - FN_BASELINE,
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Public-surface FP-rate gate.")
    parser.add_argument(
        "--json", action="store_true",
        help="Emit the ADR-033 D9 metrics (counts + delta-vs-baseline) as JSON.",
    )
    args = parser.parse_args(argv)

    outcome = evaluate()
    m = metrics(outcome)
    n_fp, n_fn = m["false_positives"], m["false_negatives"]

    if args.json:
        import json
        print(json.dumps(m, indent=2))
    else:
        print(f"FP-rate gate: {len(CORPUS)} cases — {n_fp} false positive(s), {n_fn} false negative(s)")
        if outcome.false_positives:
            print(f"  false positives (internal noise reported as breaking): {outcome.false_positives}")
        if outcome.false_negatives:
            print(f"  false negatives (real break scoped away):               {outcome.false_negatives}")
        print(
            "  delta vs baseline: "
            f"false_positive_delta={m['false_positive_delta_vs_baseline']}, "
            f"false_negative_delta={m['false_negative_delta_vs_baseline']}"
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
    if not failed and not args.json:
        print("FP-rate gate: OK (within baseline)")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
