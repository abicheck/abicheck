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

"""Which evidence *depth* clears a false positive? — a runnable demonstration.

This answers a specific question: *are there changes that a lower depth
(``binary`` / ``headers``) flags as a break — a **false positive** — that only a
**higher** depth (``build`` / ``source``) can clear?*

The short answer abicheck's architecture gives is nuanced, and this script makes
it concrete and executable:

1. **Most** false positives are cleared at the **headers** depth (L2) by
   public/internal *scoping* — layout churn on a type no public API reaches is
   flagged the moment a depth can see layout (``binary`` with sizes / ``headers``)
   and scoped out the moment a depth can see the public surface (``headers``).
   That is the ``internal_struct_churn`` case below.

2. There **is** a genuine class a depth *above* headers must clear:
   **build-context / preprocessor divergence**. When a public struct's field is
   guarded by ``#ifdef`` and the shipped build fixes that macro, the header AST
   parsed *context-free* (no compile database) computes a **different layout**
   than what was actually built — a phantom break. Only the ``build`` depth,
   which carries the real ``-D`` flags, resolves both sides to the same true ABI
   and clears the false positive. That is the ``preproc_conditional_field`` case.

3. A **pure source-only** clear — headers *and* build both false-positive, only
   source proves it safe — does **not** occur for a shipped-ABI *compare* verdict
   in the current model, because build/source evidence is *corroborating*: it
   adds breaks (cuts false negatives) and refines reachability, but it does not
   silently overturn an artifact-proven layout break (the *authority rule*,
   ADR-028 D3). ``detail_type_via_pointer`` shows the header depth already
   declining to over-call the near-miss, so there is nothing left for source to
   clear.

Unlike ``scripts/check_tier_accuracy.py`` — which *projects* one full-fidelity
snapshot down to what each depth sees by **removing** evidence — this script
lets each depth observe its **own** ``(old, new)`` pair. That models the honest
fact that a lower depth does not merely see *less*: parsed context-free, headers
can see a *distorted* picture (the phantom ``#ifdef`` layout) that more evidence
corrects. Each pair is fed to the real :func:`abicheck.checker.compare`, so every
verdict below is the tool's actual output, not a hand-asserted label.

Run: ``python validation/scripts/fp_depth_demo.py`` (pure Python; no compiler,
castxml, or network). ``--markdown`` emits the matrix for a report.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from abicheck.checker import Verdict, compare  # noqa: E402
from abicheck.model import (  # noqa: E402
    AbiSnapshot,
    Function,
    RecordType,
    ScopeOrigin,
    TypeField,
    Visibility,
)

# The five collection depths the ``abicheck scan --depth`` dial exposes, and the
# evidence layer each one reaches (see docs/concepts/evidence-and-detectability).
DEPTHS: tuple[str, ...] = ("binary", "headers", "build", "source", "full")
DEPTH_LAYER = {
    "binary": "L0",
    "headers": "L2",
    "build": "L3",
    "source": "L4",
    "full": "L5",
}
# Scoping (public/internal surface resolution) becomes available once headers
# are present — exactly the depths at/above "headers".
_SCOPED_DEPTHS = frozenset({"headers", "build", "source", "full"})

# Verdict -> 3-band ordinal severity, matching check_tier_accuracy.py so the two
# gates speak the same language: non-breaking (0) / risk (1) / breaking (2).
_BAND: dict[Verdict, int] = {
    Verdict.NO_CHANGE: 0,
    Verdict.COMPATIBLE: 0,
    Verdict.COMPATIBLE_WITH_RISK: 1,
    Verdict.API_BREAK: 2,
    Verdict.BREAKING: 2,
}
_BAND_NAME = {0: "non-breaking", 1: "risk", 2: "breaking"}


# ── snapshot builders ─────────────────────────────────────────────────────────


def _rec(name, n_fields, *, field_type="int", origin=ScopeOrigin.PUBLIC_HEADER):
    """A struct with *n_fields* int fields (32 bits each) unless *field_type* set."""
    return RecordType(
        name=name,
        kind="struct",
        size_bits=32 * n_fields,
        fields=[TypeField(name=f"f{i}", type=field_type) for i in range(n_fields)],
        origin=origin,
    )


def _fn(name, ret):
    return Function(
        name=name,
        mangled=name,
        return_type=ret,
        params=[],
        visibility=Visibility.PUBLIC,
        origin=ScopeOrigin.PUBLIC_HEADER,
    )


def _snap(version, *, types=(), functions=(), elf_only=False):
    return AbiSnapshot(
        library="libdepth",
        version=version,
        types=list(types),
        functions=list(functions),
        from_headers=not elf_only,
        elf_only_mode=elf_only,
    )


# ── the demonstration corpus ──────────────────────────────────────────────────
#
# Each case supplies, per depth, the (old, new) pair that depth *observes*. A
# depth that observes nothing relevant (e.g. binary cannot see a header-only
# construct) returns two identical snapshots and thus a quiet verdict.


@dataclass(frozen=True)
class DepthCase:
    name: str
    axis: str
    truth: int  # ground-truth band (0/1/2)
    cleared_at: str  # the weakest depth that first reaches the truth
    observe: Callable[[str], tuple[AbiSnapshot, AbiSnapshot]]


def _preproc_conditional_field(depth):
    """v1 declares field ``b`` unconditionally; v2 wraps it in ``#ifdef KEEP_B``.
    Both releases are shipped built *with* ``-DKEEP_B``, so the true, shipped ABI
    is identical (``{a, b}`` in both) — the correct verdict is non-breaking.

    - binary  (L0): a stripped binary has *exported symbols only, no type layout*.
      Both releases were built identically, so their symbol tables are the same
      -> the binary depth sees no change and is correct. It is *blind* to the
      header-conditional layout, and that blindness happens to give the right
      answer here (as L0 does for internal churn) — it does **not** observe a
      shrink, so we must not fabricate one (Codex review #498).
    - headers (L2): castxml parses the header context-free (``KEEP_B`` undefined),
      so v1's ``S`` is ``{a, b}`` but v2's ``S`` is ``{a}`` -> phantom
      ``type_field_removed`` (FALSE POSITIVE — a real break that isn't). This is
      the header AST modelling a layout that was never built.
    - build   (L3): the compile database carries ``-DKEEP_B``; both sides now
      parse to ``{a, b}`` -> the phantom vanishes, verdict is correct. This does
      not overturn an artifact-proven break (there was none — the binary was
      blind); it corrects the context-free header parse. Authority rule intact.
    - source/full: strictly more evidence than build -> stays correct.
    """
    kept = _snap("1", types=[_rec("S", 2)], functions=[_fn("use", "S *")])
    if depth == "binary":
        # Stripped binary: exported symbols only, no type layout. Both releases
        # built identically -> identical symbol view -> no change. (No injected
        # RecordType: L0 cannot see the header-conditional field at all.)
        both = _snap("1", functions=[_fn("use", "S *")], elf_only=True)
        return both, both
    if depth == "headers":
        # Context-free castxml parse: KEEP_B undefined -> v2 drops field b, while
        # v1 declared it unconditionally. The asymmetry is the phantom diff.
        old = _snap("1", types=[_rec("S", 2)], functions=[_fn("use", "S *")])
        new = _snap("2", types=[_rec("S", 1)], functions=[_fn("use", "S *")])
        return old, new
    # build / source / full: compile DB supplies -DKEEP_B -> true, unchanged ABI.
    return kept, kept


def _internal_struct_churn(depth):
    """Layout churn on a struct **no public API reaches** — the classic FP that
    the *headers* depth (not build/source) clears by scoping.

    - binary: no layout visible -> quiet (blind, not accurate).
    - headers+: the type is unreachable from any public function, so scoping
      drops it -> correct at every scoped depth. Build/source add nothing here;
      this is the contrast to ``preproc_conditional_field``.
    """
    old = _snap("1", types=[_rec("Internal", 2)], functions=[_fn("api", "void")])
    new = _snap("2", types=[_rec("Internal", 4)], functions=[_fn("api", "void")])
    if depth == "binary":
        # Stripped binary sees only symbols; the internal type carries no export.
        return _snap("1", elf_only=True), _snap("2", elf_only=True)
    return old, new


def _real_public_break(depth):
    """A genuine public break (a reachable struct's field *type* changes) — the
    control: no depth may clear it, and no depth above binary may miss it."""
    old = _snap(
        "1",
        types=[_rec("W", 2, origin=ScopeOrigin.PUBLIC_HEADER)],
        functions=[_fn("mk", "W")],
    )
    new = _snap(
        "2",
        types=[
            RecordType(
                name="W",
                kind="struct",
                size_bits=64,
                fields=[
                    TypeField(name="f0", type="float"),
                    TypeField(name="f1", type="int"),
                ],
                origin=ScopeOrigin.PUBLIC_HEADER,
            )
        ],
        functions=[_fn("mk", "W")],
    )
    if depth == "binary":
        # Field *type* change is layout-invisible in a stripped binary (same
        # size, same symbol) -> under-call (a real FN at L0).
        return _snap("1", elf_only=True), _snap("2", elf_only=True)
    return old, new


def _detail_type_via_pointer(depth):
    """A ``detail`` type reached only through a public pointer return, growing by
    an appended field. abicheck's *headers* depth already declines to over-call
    this (an appended field behind a pointer is compatible), so there is no false
    positive left for source to clear — the honest negative result."""
    old = _snap(
        "1",
        types=[_rec("Impl", 1, origin=ScopeOrigin.PRIVATE_HEADER)],
        functions=[_fn("make", "Impl *")],
    )
    new = _snap(
        "2",
        types=[_rec("Impl", 2, origin=ScopeOrigin.PRIVATE_HEADER)],
        functions=[_fn("make", "Impl *")],
    )
    if depth == "binary":
        return _snap("1", elf_only=True), _snap("2", elf_only=True)
    return old, new


CORPUS: list[DepthCase] = [
    # The headline: a false positive only BUILD context can clear.
    DepthCase(
        "preproc_conditional_field",
        "build-context",
        0,
        "build",
        _preproc_conditional_field,
    ),
    # Contrast: a false positive the HEADERS depth clears (scoping) — build/source
    # add nothing.
    DepthCase("internal_struct_churn", "scoping", 0, "headers", _internal_struct_churn),
    # The honest negative: no false positive survives headers, so nothing is left
    # for source to clear.
    DepthCase(
        "detail_type_via_pointer",
        "reachability",
        0,
        "headers",
        _detail_type_via_pointer,
    ),
    # Control: a real break — must be caught, never cleared, from headers up.
    DepthCase("real_public_break", "struct-layout", 2, "headers", _real_public_break),
]


# ── evaluation ────────────────────────────────────────────────────────────────


def band_at(case: DepthCase, depth: str) -> int:
    old, new = case.observe(depth)
    verdict = compare(old, new, scope_to_public_surface=depth in _SCOPED_DEPTHS).verdict
    return _BAND[verdict]


def outcome(case: DepthCase, depth: str) -> str:
    """``correct`` / ``FP`` (over-call) / ``FN`` (under-call) at *depth*."""
    b = band_at(case, depth)
    if b == case.truth:
        return "correct"
    return "FN" if b < case.truth else "FP"


def evaluate() -> dict[str, dict[str, str]]:
    return {c.name: {d: outcome(c, d) for d in DEPTHS} for c in CORPUS}


def fp_cleared_by_depth() -> dict[str, list[str]]:
    """For each adjacent depth transition, cases whose FP at the weaker depth
    becomes correct at the stronger one — the direct 'what this depth clears'."""
    out: dict[str, list[str]] = {}
    for lo, hi in zip(DEPTHS, DEPTHS[1:]):
        cleared = [
            c.name
            for c in CORPUS
            if outcome(c, lo) == "FP" and outcome(c, hi) == "correct"
        ]
        if cleared:
            out[f"{lo}->{hi}"] = cleared
    return out


_CELL = {"correct": "✓", "FP": "FP", "FN": "FN"}


def render(markdown: bool) -> str:
    results = evaluate()
    lines: list[str] = []
    if markdown:
        lines += [
            "### False positives by evidence depth — what each depth clears",
            "",
            "`FP` = depth over-calls (flags a break that isn't); `FN` = depth "
            "under-calls (misses a real break). Truth is the ground-truth band.",
            "",
            "| Case | Axis | Truth | " + " | ".join(DEPTHS) + " |",
            "|------|------|-------|" + "|".join([":--:"] * len(DEPTHS)) + "|",
        ]
        for c in CORPUS:
            cells = " | ".join(_CELL[results[c.name][d]] for d in DEPTHS)
            lines.append(f"| {c.name} | {c.axis} | {_BAND_NAME[c.truth]} | {cells} |")
        cleared = fp_cleared_by_depth()
        if cleared:
            lines += ["", "**False positives cleared by each depth:**"]
            for trans, names in cleared.items():
                lines.append(
                    f"- `{trans}`: {len(names)} FP cleared ({', '.join(names)})"
                )
    else:
        header = f"{'case':<28} {'truth':<12} " + " ".join(f"{d:>7}" for d in DEPTHS)
        lines.append(header)
        lines.append("-" * len(header))
        for c in CORPUS:
            row = f"{c.name:<28} {_BAND_NAME[c.truth]:<12} " + " ".join(
                f"{_CELL[results[c.name][d]]:>7}" for d in DEPTHS
            )
            lines.append(row)
        lines.append("")
        for trans, names in fp_cleared_by_depth().items():
            lines.append(f"  {trans}: cleared {len(names)} FP ({', '.join(names)})")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--markdown", action="store_true", help="Emit the matrix as Markdown."
    )
    args = ap.parse_args(argv)
    print(render(args.markdown))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
