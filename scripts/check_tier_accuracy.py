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

"""Per-evidence-tier accuracy gate — *how much each level buys*.

The public-surface FP-rate gate (``check_fp_rate.py``) proves the tool is
correct with **full** evidence. This gate answers the complementary question the
FP gate cannot: *what does each successive evidence layer contribute to
accuracy, and where are the lower layers demonstrably insufficient?*

Method: one labelled logical change per case, authored at full fidelity, then
**projected down** to what each lower tier would actually observe and run
through ``compare``:

* **L0** — exported symbols only (stripped binary): no type layout, no
  signatures, no header scoping.
* **L1** — + debug info (DWARF): type layout + signatures, but no public/private
  header scoping.
* **L2** — + public headers: header-derived visibility, provenance, and
  reachability scoping (``scope_to_public_surface=True``).
* **L3** — + build context: stdlib/toolchain/flags (``build_mode``).

Each verdict is collapsed to a 3-band ordinal severity — ``non-breaking`` (0) /
``risk`` (1) / ``breaking`` (2) — and compared to the case's ground-truth band:

* a tier that reports **below** the truth **under-calls** it — a *false
  negative* family error (the layer is **insufficient** to see the problem);
* a tier that reports **above** the truth **over-calls** it — a *false positive*
  family error (the layer cries wolf because it lacks the context to scope).

The two phenomena this makes measurable:

1. **Each higher level reduces false positives.** Over-calls introduced by a
   layer that sees layout but not scope (classically L1) are removed by the next
   layer that adds scope (L2). ``fp_removed_by_transition`` counts exactly that.
2. **Lower levels are insufficient.** The under-call (FN) cases are the
   representative examples that a stripped binary / debug-only scan *cannot*
   catch a real ABI/API break that headers (or build context) reveal.

Gates (both deterministic on this synthetic corpus):

* **top-tier correctness** — every case is correct at its own top tier
  (baseline 0 mismatches);
* **under-call monotonicity** — a case never under-calls at a higher tier once a
  lower tier already called it right (more evidence never *hides* a real break;
  the authority rule, ADR-028 D3).

Run locally: ``python scripts/check_tier_accuracy.py`` (``--markdown`` / ``--json``
emit the per-tier matrix for a CI step-summary / trend artifact).
"""

from __future__ import annotations

import copy
import sys
from collections.abc import Callable
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from abicheck.build_mode import BuildMode, StdlibFamily  # noqa: E402
from abicheck.checker import Verdict, compare  # noqa: E402
from abicheck.model import (  # noqa: E402
    AbiSnapshot,
    EnumMember,
    EnumType,
    Function,
    Param,
    RecordType,
    ScopeOrigin,
    TypeField,
    Visibility,
)


class Tier(IntEnum):
    """Evidence tiers, ordered by how much each newly reveals."""

    L0 = 0  # exported symbols only
    L1 = 1  # + debug info (layout + signatures)
    L2 = 2  # + public headers (scoping)
    L3 = 3  # + build context (build_mode)


ALL_TIERS: tuple[Tier, ...] = (Tier.L0, Tier.L1, Tier.L2, Tier.L3)


# Verdict → 3-band ordinal severity. COMPATIBLE collapses with NO_CHANGE so the
# gate is not sensitive to the (noise-level) distinction between "identical" and
# "compatibly changed"; the meaningful bands are non-breaking / risk / breaking.
_BAND: dict[Verdict, int] = {
    Verdict.NO_CHANGE: 0,
    Verdict.COMPATIBLE: 0,
    Verdict.COMPATIBLE_WITH_RISK: 1,
    Verdict.API_BREAK: 2,
    Verdict.BREAKING: 2,
}
_BAND_NAME = {0: "non-breaking", 1: "risk", 2: "breaking"}


# ── snapshot builders (full fidelity = what the top tier observes) ───────────


def _fn(
    name,
    *,
    ret="void",
    params=(),
    vis=Visibility.PUBLIC,
    origin=ScopeOrigin.PUBLIC_HEADER,
    mangled=None,
) -> Function:
    return Function(
        name=name,
        mangled=mangled or f"_Z{len(name)}{name}",
        return_type=ret,
        params=[Param(name=f"a{i}", type=t) for i, t in enumerate(params)],
        visibility=vis,
        origin=origin,
    )


def _rec(name, *, size=64, fields=(), origin=ScopeOrigin.PUBLIC_HEADER) -> RecordType:
    return RecordType(
        name=name,
        kind="struct",
        size_bits=size,
        fields=[TypeField(name=n, type=t) for n, t in fields],
        origin=origin,
    )


def _enum(name, members, *, origin=ScopeOrigin.PUBLIC_HEADER) -> EnumType:
    return EnumType(
        name=name,
        members=[EnumMember(name=n, value=v) for n, v in members],
        origin=origin,
    )


def _snap(version, *, functions=(), types=(), enums=(), build_mode=None) -> AbiSnapshot:
    return AbiSnapshot(
        library="libtier",
        version=version,
        functions=list(functions),
        types=list(types),
        enums=list(enums),
        from_headers=True,
        build_mode=build_mode,
    )


# ── tier projection ──────────────────────────────────────────────────────────


def project(snap: AbiSnapshot, tier: Tier) -> AbiSnapshot:
    """Degrade a full-fidelity snapshot to what *tier* would actually observe.

    Pure (operates on a deep copy). L0 strips all type/signature/header
    evidence (a stripped binary sees only symbols); L1 restores layout and
    signatures but no header scoping; L2 restores header visibility/provenance;
    L3 restores build context. The result is fed to ``compare`` with scoping
    enabled from L2 up.
    """
    s = copy.deepcopy(snap)
    if tier <= Tier.L1:
        # No headers: strip header-derived visibility/provenance so nothing is
        # scoped as public/private (matches a DWARF/symbols-only dump).
        for f in s.functions:
            f.visibility = Visibility.ELF_ONLY
            f.origin = ScopeOrigin.UNKNOWN
        for v in s.variables:
            v.visibility = Visibility.ELF_ONLY
            v.origin = ScopeOrigin.UNKNOWN
        for t in s.types:
            t.origin = ScopeOrigin.UNKNOWN
        for e in s.enums:
            e.origin = ScopeOrigin.UNKNOWN
        s.from_headers = False
    if tier == Tier.L0:
        # Stripped binary: only symbol identity survives — no layout, no
        # signatures, no types/enums/typedefs, and variables degrade to a bare
        # symbol (no type/const/value evidence). Anything richer would let
        # compare() overstate what a stripped binary can see for a type-, enum-,
        # typedef- or variable-axis case (Codex review #487).
        s.types = []
        s.enums = []
        s.typedefs = {}
        for f in s.functions:
            f.return_type = "?"
            f.params = []
        for v in s.variables:
            v.type = "?"
            v.is_const = False
            v.value = None
        s.elf_only_mode = True
    if tier < Tier.L3:
        # Build context (stdlib/toolchain/flags) only exists at L3.
        s.build_mode = None
    return s


def verdict_at(old_full: AbiSnapshot, new_full: AbiSnapshot, tier: Tier) -> Verdict:
    """Run ``compare`` on the tier-projected pair (scoping from L2 up)."""
    return compare(
        project(old_full, tier),
        project(new_full, tier),
        scope_to_public_surface=tier >= Tier.L2,
    ).verdict


def band_at(old_full: AbiSnapshot, new_full: AbiSnapshot, tier: Tier) -> int:
    return _BAND[verdict_at(old_full, new_full, tier)]


# ── corpus ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TierCase:
    name: str
    axis: str
    expected_band: int  # ground-truth severity band (0/1/2)
    top_tier: Tier  # highest tier that must reach expected_band
    build: Callable[[], tuple[AbiSnapshot, AbiSnapshot]]


# --- breaking (band 2): representative low-level INSUFFICIENCY (under-call) ----


def _public_struct_size_changed():
    old = _snap("1", functions=[_fn("api", ret="W *")], types=[_rec("W", size=64)])
    new = _snap("2", functions=[_fn("api", ret="W *")], types=[_rec("W", size=128)])
    return old, new


def _public_struct_field_type_changed():
    old = _snap(
        "1",
        functions=[_fn("api", ret="W *")],
        types=[_rec("W", size=64, fields=[("x", "int")])],
    )
    new = _snap(
        "2",
        functions=[_fn("api", ret="W *")],
        types=[_rec("W", size=64, fields=[("x", "float")])],
    )
    return old, new


def _c_param_widened():
    # A C function (no mangling): the exported symbol name is identical, so L0
    # is blind to the parameter change — only a signature (L1) reveals it.
    old = _snap("1", functions=[_fn("capi", mangled="capi", params=("int",))])
    new = _snap("2", functions=[_fn("capi", mangled="capi", params=("long long",))])
    return old, new


def _c_return_type_changed():
    old = _snap("1", functions=[_fn("capi", mangled="capi", ret="int")])
    new = _snap("2", functions=[_fn("capi", mangled="capi", ret="long long")])
    return old, new


def _public_enum_value_changed():
    # An enum a public API takes by value: re-valuing a member is a real API
    # break, but enum *values* are invisible at L0 (symbols only).
    old = _snap(
        "1",
        functions=[_fn("api", params=("Mode",))],
        enums=[_enum("Mode", [("A", 0), ("B", 1)])],
    )
    new = _snap(
        "2",
        functions=[_fn("api", params=("Mode",))],
        enums=[_enum("Mode", [("A", 0), ("B", 9)])],
    )
    return old, new


# --- non-breaking (band 0): FALSE POSITIVE removed by the scoping layer --------


def _internal_struct_size_changed():
    # Layout change on a type no public API reaches: invisible at L0 (blind),
    # an over-call (FP) at L1 (sees layout, no scope), removed at L2 (scoped).
    old = _snap("1", functions=[_fn("api")], types=[_rec("Internal", size=64)])
    new = _snap("2", functions=[_fn("api")], types=[_rec("Internal", size=128)])
    return old, new


def _internal_field_type_changed():
    # An internal struct's field type changes (int -> long long): a real layout
    # change that L1 sees and over-calls (FP), and L2 scopes out as internal —
    # the non-breaking counterpart that exercises the L1->L2 scoping guard on the
    # struct-layout axis. (A same-size field *reorder* emits no finding at all, so
    # it would be correct at every tier and guard nothing — Codex review #487.)
    old = _snap(
        "1",
        functions=[_fn("api")],
        types=[_rec("Internal", size=128, fields=[("a", "int"), ("b", "long")])],
    )
    new = _snap(
        "2",
        functions=[_fn("api")],
        types=[_rec("Internal", size=128, fields=[("a", "long long"), ("b", "long")])],
    )
    return old, new


def _elf_only_helper_param_changed():
    # An exported-but-not-public helper (ELF_ONLY, absent from headers): its
    # signature change is real churn but not a public-contract break. L1
    # over-calls it (no scope); L2 scopes it out.
    old = _snap(
        "1",
        functions=[
            _fn("api"),
            _fn("helper", mangled="helper", params=("int",),
                vis=Visibility.ELF_ONLY, origin=ScopeOrigin.UNKNOWN),
        ],
    )
    new = _snap(
        "2",
        functions=[
            _fn("api"),
            _fn("helper", mangled="helper", params=("long long",),
                vis=Visibility.ELF_ONLY, origin=ScopeOrigin.UNKNOWN),
        ],
    )
    return old, new


def _internal_enum_value_changed():
    old = _snap("1", functions=[_fn("api")], enums=[_enum("IMode", [("A", 0), ("B", 1)])])
    new = _snap("2", functions=[_fn("api")], enums=[_enum("IMode", [("A", 0), ("B", 9)])])
    return old, new


# --- risk (band 1): only build context (L3) surfaces ANY signal ---------------


def _cross_stdlib_same_size():
    # A public type embeds std::vector by value; the two builds use *different*
    # stdlib implementations but the owner size happens to be identical. No
    # artifact tier (L0/L1/L2) can see a difference — only L3 build context
    # reveals the cross-implementation deployment risk.
    old = _snap(
        "1",
        functions=[_fn("mk", ret="Buf *")],
        types=[_rec("Buf", size=192, fields=[("d", "std::vector<int>")])],
        build_mode=BuildMode(stdlib=StdlibFamily.LIBSTDCXX),
    )
    new = _snap(
        "2",
        functions=[_fn("mk", ret="Buf *")],
        types=[_rec("Buf", size=192, fields=[("d", "std::vector<int>")])],
        build_mode=BuildMode(stdlib=StdlibFamily.LIBCXX),
    )
    return old, new


CORPUS: list[TierCase] = [
    # breaking — low-level insufficiency (under-call at L0, caught at L1+)
    TierCase("public_struct_size_changed", "struct-layout", 2, Tier.L2, _public_struct_size_changed),
    TierCase("public_struct_field_type_changed", "struct-layout", 2, Tier.L2, _public_struct_field_type_changed),
    TierCase("c_param_widened", "symbol-signature", 2, Tier.L2, _c_param_widened),
    TierCase("c_return_type_changed", "symbol-signature", 2, Tier.L2, _c_return_type_changed),
    TierCase("public_enum_value_changed", "enum-reachability", 2, Tier.L2, _public_enum_value_changed),
    # non-breaking — false positive removed by the scoping layer (L2)
    TierCase("internal_struct_size_changed", "struct-layout", 0, Tier.L2, _internal_struct_size_changed),
    TierCase("internal_field_type_changed", "struct-layout", 0, Tier.L2, _internal_field_type_changed),
    TierCase("elf_only_helper_param_changed", "symbol-signature", 0, Tier.L2, _elf_only_helper_param_changed),
    TierCase("internal_enum_value_changed", "enum-reachability", 0, Tier.L2, _internal_enum_value_changed),
    # risk — only build context (L3) surfaces any signal
    TierCase("cross_stdlib_same_size", "stdlib-impl", 1, Tier.L3, _cross_stdlib_same_size),
]


# ── evaluation ────────────────────────────────────────────────────────────────


@dataclass
class CaseTrajectory:
    name: str
    axis: str
    expected_band: int
    top_tier: Tier
    bands: dict[Tier, int]  # tier -> observed band, for *every* tier in ALL_TIERS

    def outcome(self, tier: Tier) -> str:
        """``correct`` / ``under`` (FN family) / ``over`` (FP family) at *tier*."""
        b = self.bands[tier]
        if b == self.expected_band:
            return "correct"
        return "under" if b < self.expected_band else "over"

    def tiers(self) -> list[Tier]:
        return list(ALL_TIERS)


def evaluate(corpus: list[TierCase] = CORPUS) -> list[CaseTrajectory]:
    out: list[CaseTrajectory] = []
    for case in corpus:
        old, new = case.build()
        # Evaluate every case at *every* tier — L3 is a superset projection of
        # L2 for these snapshots, so an L2-top case must still be correct at L3;
        # capping at top_tier would omit it from the L3 gate and let a future
        # build-context regression slip through (Codex review #487).
        bands = {t: band_at(old, new, t) for t in ALL_TIERS}
        out.append(
            CaseTrajectory(case.name, case.axis, case.expected_band, case.top_tier, bands)
        )
    return out


def top_tier_mismatches(trajs: list[CaseTrajectory]) -> list[str]:
    """Cases the tool gets wrong at their top tier *or any tier above it*.

    A case must reach its ground-truth band once it has ``top_tier`` evidence and
    keep it with any additional evidence (a superset projection cannot lose a
    verdict). Checking every tier >= top_tier — not just top_tier itself —
    guarantees an L2-correct case is also verified at L3."""
    bad: list[str] = []
    for t in trajs:
        if any(
            tier >= t.top_tier and t.outcome(tier) != "correct" for tier in ALL_TIERS
        ):
            bad.append(t.name)
    return bad


def under_call_monotonicity_violations(trajs: list[CaseTrajectory]) -> list[str]:
    """Cases that under-call at a higher tier after a lower tier already called
    it right — i.e. more evidence *hid* a real problem (authority-rule breach)."""
    bad: list[str] = []
    for t in trajs:
        seen_ok = False
        for tier in t.tiers():
            oc = t.outcome(tier)
            if oc == "under" and seen_ok:
                bad.append(t.name)
                break
            if oc != "under":
                seen_ok = True
    return bad


def per_tier_counts(trajs: list[CaseTrajectory]) -> dict[str, dict[str, int]]:
    """Per-tier {over, under, correct, n} over the whole corpus (every case is
    evaluated at every tier)."""
    out: dict[str, dict[str, int]] = {}
    for tier in ALL_TIERS:
        if not trajs:
            continue
        out[tier.name] = {
            "n": len(trajs),
            "correct": sum(1 for t in trajs if t.outcome(tier) == "correct"),
            "over": sum(1 for t in trajs if t.outcome(tier) == "over"),
            "under": sum(1 for t in trajs if t.outcome(tier) == "under"),
        }
    return out


def resolved_by_transition(trajs: list[CaseTrajectory]) -> dict[str, dict[str, list[str]]]:
    """For each L(i)->L(i+1) transition, which cases had an over-call (FP) or
    under-call (FN) at L(i) that became correct at L(i+1). This is the direct
    "how much each next level removes" measurement."""
    out: dict[str, dict[str, list[str]]] = {}
    for i in range(len(ALL_TIERS) - 1):
        lo, hi = ALL_TIERS[i], ALL_TIERS[i + 1]
        fp: list[str] = []
        fn: list[str] = []
        for t in trajs:
            lo_oc, hi_oc = t.outcome(lo), t.outcome(hi)
            if lo_oc == "over" and hi_oc == "correct":
                fp.append(t.name)
            elif lo_oc == "under" and hi_oc == "correct":
                fn.append(t.name)
        if fp or fn:
            out[f"{lo.name}->{hi.name}"] = {"fp_removed": fp, "fn_removed": fn}
    return out


def metrics(trajs: list[CaseTrajectory] | None = None) -> dict[str, object]:
    trajs = trajs or evaluate()
    return {
        "cases": len(trajs),
        "top_tier_mismatches": top_tier_mismatches(trajs),
        "under_call_monotonicity_violations": under_call_monotonicity_violations(trajs),
        "per_tier": per_tier_counts(trajs),
        "resolved_by_transition": resolved_by_transition(trajs),
    }


_CELL = {"correct": "✓", "over": "FP", "under": "FN"}


def render_markdown(trajs: list[CaseTrajectory] | None = None) -> str:
    trajs = trajs or evaluate()
    lines = [
        "### Per-tier accuracy — what each evidence level buys",
        "",
        "`FN` = tier under-calls the truth (layer insufficient); "
        "`FP` = tier over-calls (layer lacks scope). Every case is evaluated at "
        "every tier.",
        "",
        "| Case | Axis | Truth | L0 | L1 | L2 | L3 |",
        "|------|------|-------|:--:|:--:|:--:|:--:|",
    ]
    for t in trajs:
        cells = [_CELL[t.outcome(tier)] for tier in ALL_TIERS]
        lines.append(
            f"| {t.name} | {t.axis} | {_BAND_NAME[t.expected_band]} | "
            + " | ".join(cells)
            + " |"
        )
    counts = per_tier_counts(trajs)
    lines += ["", "| Tier | Cases | Correct | FP (over) | FN (under) |",
              "|------|------:|--------:|----------:|-----------:|"]
    for tier in ALL_TIERS:
        r = counts.get(tier.name)
        if r:
            lines.append(
                f"| {tier.name} | {r['n']} | {r['correct']} | {r['over']} | {r['under']} |"
            )
    resolved = resolved_by_transition(trajs)
    if resolved:
        lines += ["", "**Errors resolved by each level:**"]
        for trans, d in resolved.items():
            parts = []
            if d["fp_removed"]:
                parts.append(f"{len(d['fp_removed'])} FP removed ({', '.join(d['fp_removed'])})")
            if d["fn_removed"]:
                parts.append(f"{len(d['fn_removed'])} FN caught ({', '.join(d['fn_removed'])})")
            lines.append(f"- `{trans}`: " + "; ".join(parts))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Per-evidence-tier accuracy gate.")
    parser.add_argument("--json", action="store_true", help="Emit the tier metrics as JSON.")
    parser.add_argument("--markdown", action="store_true", help="Emit the per-tier matrix as Markdown.")
    args = parser.parse_args(argv)

    trajs = evaluate()
    m = metrics(trajs)

    if args.json:
        import json

        print(json.dumps(m, indent=2, default=str))
    elif args.markdown:
        print(render_markdown(trajs))
    else:
        print(f"Per-tier accuracy gate: {len(trajs)} cases")
        for tier in ALL_TIERS:
            r = m["per_tier"].get(tier.name)  # type: ignore[union-attr]
            if r:
                print(
                    f"  {tier.name}: {r['correct']}/{r['n']} correct, "
                    f"{r['over']} FP (over-call), {r['under']} FN (under-call)"
                )
        for trans, d in m["resolved_by_transition"].items():  # type: ignore[union-attr]
            print(
                f"  {trans}: removed {len(d['fp_removed'])} FP, caught {len(d['fn_removed'])} FN"
            )

    err = sys.stderr if args.json else sys.stdout
    failed = False
    mismatches = m["top_tier_mismatches"]
    violations = m["under_call_monotonicity_violations"]
    if mismatches:
        print(f"ERROR: cases wrong at their top tier (should be 0): {mismatches}", file=err)
        failed = True
    if violations:
        print(
            "ERROR: under-call monotonicity violated — a higher tier hid a break "
            f"a lower tier caught: {violations}",
            file=err,
        )
        failed = True
    if not failed and not args.json:
        print("Per-tier accuracy gate: OK (top-tier correct, under-call monotonic)")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
