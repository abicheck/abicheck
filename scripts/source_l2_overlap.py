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

"""Assess the L4 / L2 overlap for the Phase 8 diffing merge
(``docs/contribute/plans/l4-l2-extraction-convergence.md``).

Two sides, each derived from the source with the static closure that
``gen_detector_kind_map`` already uses (AST over first-party code, no code run):

* **L4** -- the kinds reachable from
  ``abicheck.buildsource.source_diff.diff_source_abi``.
* **L2** -- the kinds reachable from every registered detector whose function
  lives in ``abicheck.diff_types*`` or ``abicheck.diff_symbols*``.

Every kind either side can emit must have one row in :data:`CLASSIFICATION`:

* ``L4_ONLY`` -- the L4 fact has no L2 counterpart (macro values, body
  fingerprints, template patterns, ODR, provenance, coverage). Not duplication.
* ``L2_ONLY`` -- only the L2 detectors emit it. Listed explicitly so that a new
  L2 kind fails the test until someone decides its tier.
* ``DUPLICATE`` -- the same ``ChangeKind`` is emitted by both tiers for the same
  underlying change.
* ``NEAR_DUPLICATE`` -- different kinds, same underlying change; ``counterpart``
  names the other side's kind.

The classification is human-judged, from reading both emitters; the derivation
only says what *can* be emitted. ``tests/test_source_l2_overlap.py`` fails when a
kind is not classified, when a row names a kind neither side emits, or when a
row's label contradicts the derivation.

Usage::

    python scripts/source_l2_overlap.py              # markdown table
    python scripts/source_l2_overlap.py --unclassified  # stubs for new kinds
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))
from gen_detector_kind_map import _PACKAGE_ROOT, _closure, _Source  # noqa: E402

L4_ENTRY = ("abicheck.buildsource.source_diff", "diff_source_abi")
L2_MODULE_PREFIXES = ("abicheck.diff_types", "abicheck.diff_symbols")

L4_ONLY = "L4_ONLY"
L2_ONLY = "L2_ONLY"
DUPLICATE = "DUPLICATE"
NEAR_DUPLICATE = "NEAR_DUPLICATE"
LABELS = frozenset({L4_ONLY, L2_ONLY, DUPLICATE, NEAR_DUPLICATE})


@dataclass(frozen=True)
class Row:
    label: str
    counterpart: str = ""
    note: str = ""


# Kinds only the L2 detectors emit: no L4 emitter exists for them, so they are
# not duplicated by the source replay. Explicit, so a new L2 kind must be listed
# here (and so classified) before the gate passes. Regenerate the stub list with
# ``--unclassified``.
L2_ONLY_KINDS: frozenset[str] = frozenset(
    {
        "ANON_FIELD_CHANGED",
        "BASE_CLASS_POSITION_CHANGED",
        "BASE_CLASS_VIRTUAL_CHANGED",
        "CALLING_CONVENTION_CHANGED",
        "CONSTANT_ADDED",
        "CONSTANT_REMOVED",
        "CTOR_EXPLICIT_ADDED",
        "CTOR_EXPLICIT_REMOVED",
        "CTOR_OVERLOAD_AMBIGUITY_RISK",
        "ENUM_BECAME_SCOPED",
        "ENUM_DEPRECATED_ADDED",
        "ENUM_DEPRECATED_REMOVED",
        "ENUM_LAST_MEMBER_VALUE_CHANGED",
        "ENUM_LOST_SCOPED",
        "ENUM_MEMBER_ADDED",
        "ENUM_MEMBER_REMOVED",
        "ENUM_MEMBER_RENAMED",
        "ENUM_MEMBER_VALUE_CHANGED",
        "ENUM_UNDERLYING_SIZE_CHANGED",
        "FIELD_ACCESS_CHANGED",
        "FIELD_BECAME_CONST",
        "FIELD_BECAME_MUTABLE",
        "FIELD_BECAME_VOLATILE",
        "FIELD_BITFIELD_CHANGED",
        "FIELD_DEFAULT_INITIALIZER_CHANGED",
        "FIELD_DEFAULT_INITIALIZER_REMOVED",
        "FIELD_DEPRECATED_ADDED",
        "FIELD_DEPRECATED_REMOVED",
        "FIELD_LOST_CONST",
        "FIELD_LOST_MUTABLE",
        "FIELD_LOST_VOLATILE",
        "FIELD_RENAMED",
        "FLEXIBLE_ARRAY_MEMBER_CHANGED",
        "FUNC_ADDED",
        "FUNC_BECAME_INLINE",
        "FUNC_CONTRACT_ATTRIBUTE_ADDED",
        "FUNC_CONTRACT_ATTRIBUTE_REMOVED",
        "FUNC_CV_CHANGED",
        "FUNC_DELETED",
        "FUNC_DELETED_DWARF",
        "FUNC_DEPRECATED_ADDED",
        "FUNC_DEPRECATED_REMOVED",
        "FUNC_EXCEPTION_SPEC_CHANGED",
        "FUNC_EXPORT_ADDED",
        "FUNC_LANGUAGE_LINKAGE_CHANGED",
        "FUNC_LIKELY_RENAMED",
        "FUNC_LOST_INLINE",
        "FUNC_NOEXCEPT_ADDED",
        "FUNC_NOEXCEPT_REMOVED",
        "FUNC_OVERRIDE_SPECIFIER_ADDED",
        "FUNC_OVERRIDE_SPECIFIER_REMOVED",
        "FUNC_PARAMS_CHANGED",
        "FUNC_PURE_VIRTUAL_ADDED",
        "FUNC_REF_QUAL_CHANGED",
        "FUNC_REMOVED",
        "FUNC_REMOVED_ELF_ONLY",
        "FUNC_RETURN_CHANGED",
        "FUNC_RETURN_POINTEE_QUALIFIER_ADDED",
        "FUNC_RETURN_POINTEE_QUALIFIER_CHANGED",
        "FUNC_RETURN_POINTEE_QUALIFIER_REMOVED",
        "FUNC_STATIC_CHANGED",
        "FUNC_VARIADIC_ADDED",
        "FUNC_VARIADIC_REMOVED",
        "FUNC_VIRTUAL_ADDED",
        "FUNC_VIRTUAL_BECAME_PURE",
        "FUNC_VIRTUAL_REMOVED",
        "FUNC_VISIBILITY_CHANGED",
        "HIDDEN_FRIEND_ADDED",
        "HIDDEN_FRIEND_REMOVED",
        "METHOD_ACCESS_CHANGED",
        "OVERLOAD_ADDED",
        "PARAM_BECAME_VA_LIST",
        "PARAM_DEFAULT_VALUE_CHANGED",
        "PARAM_DEFAULT_VALUE_REMOVED",
        "PARAM_LOST_VA_LIST",
        "PARAM_POINTEE_QUALIFIER_ADDED",
        "PARAM_POINTEE_QUALIFIER_CHANGED",
        "PARAM_POINTER_LEVEL_CHANGED",
        "PARAM_RENAMED",
        "PARAM_RESTRICT_ADDED",
        "PARAM_RESTRICT_CHANGED",
        "REMOVED_CONST_OVERLOAD",
        "RETURN_POINTER_LEVEL_CHANGED",
        "SOURCE_LEVEL_KIND_CHANGED",
        "SYMBOL_RENAMED_BATCH",
        "TYPEDEF_VERSION_SENTINEL",
        "TYPE_ADDED",
        "TYPE_ALIGNMENT_CHANGED",
        "TYPE_BASE_CHANGED",
        "TYPE_BECAME_ABSTRACT",
        "TYPE_BECAME_FINAL",
        "TYPE_BECAME_OPAQUE",
        "TYPE_DEPRECATED_ADDED",
        "TYPE_DEPRECATED_REMOVED",
        "TYPE_FIELD_ADDED",
        "TYPE_FIELD_ADDED_COMPATIBLE",
        "TYPE_FIELD_OFFSET_CHANGED",
        "TYPE_FIELD_REMOVED",
        "TYPE_FIELD_TYPE_CHANGED",
        "TYPE_KIND_CHANGED",
        "TYPE_LOST_ABSTRACT",
        "TYPE_LOST_FINAL",
        "TYPE_REMOVED",
        "TYPE_SIZE_CHANGED",
        "TYPE_VTABLE_CHANGED",
        "UNION_FIELD_ADDED",
        "UNION_FIELD_REMOVED",
        "UNION_FIELD_TYPE_CHANGED",
        "USED_RESERVED_FIELD",
        "VAR_ACCESS_CHANGED",
        "VAR_ACCESS_WIDENED",
        "VAR_ADDED",
        "VAR_ALIGNMENT_CHANGED",
        "VAR_BECAME_CONST",
        "VAR_DEPRECATED_ADDED",
        "VAR_DEPRECATED_REMOVED",
        "VAR_EXPORT_ADDED",
        "VAR_LOST_CONST",
        "VAR_REMOVED",
        "VAR_TYPE_CHANGED",
        "VAR_VALUE_CHANGED",
        "VAR_VISIBILITY_CHANGED",
        "VIRTUAL_METHOD_ADDED",
    }
)

# Hand-judged rows for every kind the L4 side emits, plus the L2 counterparts
# that overlap it. Notes say *why* the row has its label, from the emitters.
_JUDGED: dict[str, Row] = {
    "CONCEPT_TIGHTENED": Row(
        L4_ONLY, note="concept constraint; L2 has no concept facts"
    ),
    "CONSTEXPR_VALUE_CHANGED": Row(
        NEAR_DUPLICATE,
        "CONSTANT_CHANGED",
        "namespace-scope constexpr value; L4 via _diff_declarations, L2 via "
        "parse_constants + compare.constants",
    ),
    "CONSTANT_CHANGED": Row(
        NEAR_DUPLICATE,
        "CONSTEXPR_VALUE_CHANGED",
        "same const/constexpr value change as the L4 kind",
    ),
    "DEFAULT_ARGUMENT_CHANGED": Row(
        L4_ONLY, note="parameter default; L2 has no parameter-default kind"
    ),
    "GENERATED_HEADER_CHANGED": Row(L4_ONLY, note="generated-header provenance"),
    "INLINE_BODY_CHANGED": Row(
        L4_ONLY, note="body fingerprint; DWARF/symbols carry no body"
    ),
    "INLINE_FUNCTION_REMOVED": Row(
        DUPLICATE,
        note="header-only inline removed: L4 _diff_inline_bodies and L2 "
        "_check_removed_function (export confirmed absent) both emit it",
    ),
    "ODR_SOURCE_CONFLICT": Row(L4_ONLY, note="ODR check across source definitions"),
    "PUBLIC_MACRO_REMOVED": Row(L4_ONLY, note="macros are not in the L2 model"),
    "PUBLIC_MACRO_VALUE_CHANGED": Row(
        L4_ONLY, note="macro replacement text; L2 has no macro kind"
    ),
    "PUBLIC_TYPEDEF_REMOVED": Row(
        NEAR_DUPLICATE, "TYPEDEF_REMOVED", "public typedef removed from the headers"
    ),
    "TYPEDEF_REMOVED": Row(
        NEAR_DUPLICATE,
        "PUBLIC_TYPEDEF_REMOVED",
        "same typedef removal, L2 compare.typedefs",
    ),
    "PUBLIC_TYPEDEF_TARGET_CHANGED": Row(
        NEAR_DUPLICATE, "TYPEDEF_BASE_CHANGED", "typedef resolves to a different type"
    ),
    "TYPEDEF_BASE_CHANGED": Row(
        NEAR_DUPLICATE,
        "PUBLIC_TYPEDEF_TARGET_CHANGED",
        "same typedef retarget, L2 compare.typedefs",
    ),
    "SOURCE_BINARY_PROVENANCE_MISMATCH": Row(
        L4_ONLY, note="source vs binary provenance check"
    ),
    "SOURCE_DECL_BINARY_SYMBOL_MISMATCH": Row(
        L4_ONLY, note="declaration without its binary symbol"
    ),
    "SOURCE_FACT_COVERAGE_INCOMPLETE": Row(L4_ONLY, note="fact-coverage meta finding"),
    "TEMPLATE_BODY_CHANGED": Row(L4_ONLY, note="template body fingerprint"),
    "UNINSTANTIATED_TEMPLATE_REMOVED": Row(
        L4_ONLY,
        note="template with no instantiation has no binary symbol for L2 to see",
    ),
}

CLASSIFICATION: dict[str, Row] = {
    **_JUDGED,
    **{kind: Row(L2_ONLY) for kind in L2_ONLY_KINDS},
}


def derive_sides() -> tuple[frozenset[str], frozenset[str]]:
    """``(L4 kind names, L2 kind names)`` from the static closure."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from abicheck.detector_registry import registry

    registry.ensure_loaded()
    source = _Source(_PACKAGE_ROOT)
    l4 = _closure(source, L4_ENTRY)
    l2: set[str] = set()
    for entry in registry._detectors:  # noqa: SLF001 - the registry's own list
        module = entry.fn.__module__
        if module.startswith(L2_MODULE_PREFIXES):
            l2 |= _closure(source, (module, entry.fn.__name__))
    return frozenset(l4), frozenset(l2)


def side_of(kind: str, l4: frozenset[str], l2: frozenset[str]) -> str:
    if kind in l4 and kind in l2:
        return "both"
    return "L4" if kind in l4 else "L2"


def problems(l4: frozenset[str], l2: frozenset[str]) -> list[str]:
    """Every way the classification and the derivation disagree (empty = clean)."""
    out: list[str] = []
    emitted = l4 | l2
    for kind in sorted(emitted - CLASSIFICATION.keys()):
        out.append(f"unclassified: {kind} ({side_of(kind, l4, l2)})")
    for kind in sorted(CLASSIFICATION.keys() - emitted):
        out.append(f"stale row, neither side emits it: {kind}")
    for kind, row in sorted(CLASSIFICATION.items()):
        if kind not in emitted:
            continue
        if row.label not in LABELS:
            out.append(f"{kind}: unknown label {row.label!r}")
        side = side_of(kind, l4, l2)
        if row.label == L4_ONLY and side != "L4":
            out.append(f"{kind}: L4_ONLY but derivation says {side}")
        if row.label == L2_ONLY and side != "L2":
            out.append(f"{kind}: L2_ONLY but derivation says {side}")
        if row.label == DUPLICATE and side != "both":
            out.append(f"{kind}: DUPLICATE but derivation says {side}")
        if row.label == NEAR_DUPLICATE:
            back = CLASSIFICATION.get(row.counterpart)
            if back is None or back.label != NEAR_DUPLICATE or back.counterpart != kind:
                out.append(
                    f"{kind}: NEAR_DUPLICATE counterpart {row.counterpart!r} does not point back"
                )
            elif side_of(row.counterpart, l4, l2) == side:
                out.append(f"{kind}: NEAR_DUPLICATE counterpart is on the same side")
    return out


def render_markdown(l4: frozenset[str], l2: frozenset[str]) -> str:
    lines = [
        "| ChangeKind | Emitted by | Classification | Counterpart | Note |",
        "|---|---|---|---|---|",
    ]
    for kind in sorted(l4 | l2):
        row = CLASSIFICATION.get(kind, Row("UNCLASSIFIED"))
        lines.append(
            f"| `{kind}` | {side_of(kind, l4, l2)} | {row.label} | "
            f"`{row.counterpart}` | {row.note} |"
            if row.counterpart
            else f"| `{kind}` | {side_of(kind, l4, l2)} | {row.label} | | {row.note} |"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--unclassified",
        action="store_true",
        help="print L2_ONLY_KINDS entries for kinds neither classified nor overlapping",
    )
    args = parser.parse_args(argv)
    l4, l2 = derive_sides()
    if args.unclassified:
        for kind in sorted(l2 - l4 - CLASSIFICATION.keys()):
            print(f'    "{kind}",')
        return 0
    found = problems(l4, l2)
    print(render_markdown(l4, l2))
    print()
    print(
        f"L4 emits {len(l4)} kinds; L2 emits {len(l2)} kinds; overlap {len(l4 & l2)}."
    )
    for line in found:
        print(f"PROBLEM: {line}", file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())
