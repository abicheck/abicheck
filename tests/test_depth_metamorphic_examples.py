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

"""Depth metamorphic relation over the whole ``catalog/cases`` corpus.

Bug class ``evidence.depth_projection_drops_lower_layer_detector_input``
(H2a): ``--depth headers`` cleared ``surface_graph`` and so starved
``TemplateInstanceAbsence`` of its L2 input, *inventing* ``*_removed``
findings a deeper run rejects. The general invariant: projecting to a lower
evidence depth only removes evidence, so it may lose findings but must never
invent a removal. For every case, the removal-type kinds found at a lower
depth are a subset of those found at the next higher depth.

The oracle is the higher-depth run of the same two snapshots, never the
projection's own bookkeeping. The only exceptions are kinds in
:data:`DEPTH_ONLY_KINDS`, each a deliberate rung-specific *spelling* of a
finding the higher rung reports under another kind -- each with its reason.

Integration-marked: each case is compiled and dumped with castxml once, then
compared at all four rungs in-process.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from abicheck.checker import compare
from abicheck.checker_policy import COMPATIBLE_KINDS
from abicheck.evidence_depth import DEPTH_RANK
from abicheck.policy.depth_projection import project_pair_to_depth
from tests.test_example_autodiscovery import (
    _ALL_CASES,
    BUILD_INFO_CASES,
    CURRENT_PLATFORM,
    PLATFORMS,
    REQUIRES_FEATURE,
    SOURCE_SMOKE_CASES,
    _build_libs_for_case,
    _find_sources,
    _header_list,
)

#: Removal-type kinds a lower rung may report although the next rung does
#: not, each because it is that rung's own spelling of a finding the higher
#: rung reports under a different kind (not because the lower rung knows
#: more). ``{kind: reason}``.
DEPTH_ONLY_KINDS: dict[str, str] = {
    "func_removed_elf_only": (
        "binary rung: every declaration is ELF_ONLY, so a removed function "
        "is spelled func_removed_elf_only; headers spell it func_removed"
    ),
    "var_removed_elf_only": (
        "binary rung: every declaration is ELF_ONLY, so a removed variable "
        "is spelled var_removed_elf_only; headers spell it var_removed"
    ),
    "struct_field_removed": (
        "binary rung: the DWARF layout diff's spelling of a removed field; "
        "with header types kept, the type diff spells it type_field_removed"
    ),
}

#: Rungs, shallowest first (derived, not restated).
_DEPTHS = tuple(sorted(DEPTH_RANK, key=DEPTH_RANK.__getitem__))


#: Compatible removals (``imported_symbol_removed``, ...) cannot become a
#: false break, the failure this relation guards; they are out of scope.
_COMPATIBLE = frozenset(k.value for k in COMPATIBLE_KINDS)

#: ``(kind, symbol)`` of one finding.
Finding = tuple[str, str]


def _findings(changes: list) -> set[Finding]:
    return {(c.kind.value, c.symbol) for c in changes}


def _unqualified(symbol: str) -> str:
    """*symbol* without its leading scope (``ns::T<ns::U>`` -> ``T<ns::U>``):
    the header model names a type by its unqualified name, a debug layout by
    its qualified one."""
    head, angle, args = symbol.partition("<")
    return head.rpartition("::")[2] + angle + args


def depth_violations(
    by_depth: dict[str, set[Finding]],
) -> list[tuple[str, str, set[Finding]]]:
    """``(lower, higher, invented removals)`` for each adjacent rung pair:
    the removal findings the lower rung reports on a symbol the higher rung
    reports *nothing* about. A higher rung that judges the same symbol under
    another kind (``func_static_changed`` for a binary-rung ``func_removed``
    of the old mangled name), or the same type under its unqualified name
    (the binary rung's debug layout spells ``ns::T``, the header model
    ``T``), is a rung-specific spelling of one finding, not an invention."""
    out = []
    for lo, hi in zip(_DEPTHS, _DEPTHS[1:], strict=False):
        judged = {sym for _, sym in by_depth[hi]}
        judged |= {_unqualified(sym) for sym in judged}
        extra = {
            (kind, sym)
            for kind, sym in by_depth[lo]
            if "removed" in kind
            and kind not in DEPTH_ONLY_KINDS
            and kind not in _COMPATIBLE
            and (kind, sym) not in by_depth[hi]
            and sym not in judged
            and _unqualified(sym) not in judged
        }
        if extra:
            out.append((lo, hi, extra))
    return out


def test_depth_violations_flags_an_invented_lower_rung_removal() -> None:
    """The relation's own checker, on a hand-made ladder (no build needed)."""
    ladder = {d: {("func_removed", "f")} for d in _DEPTHS}
    assert depth_violations(ladder) == []
    ladder["headers"] = {("func_removed", "f"), ("type_removed", "T")}
    assert depth_violations(ladder) == [("headers", "build", {("type_removed", "T")})]
    # Another kind on the same symbol at the higher rung: a spelling, not
    # an invention.
    ladder["headers"] = {("func_removed", "f"), ("func_removed", "g")}
    ladder["build"] = ladder["source"] = {
        ("func_removed", "f"),
        ("func_static_changed", "g"),
    }
    assert depth_violations(ladder) == []
    ladder["headers"] = {("func_removed", "f"), ("imported_symbol_removed", "i")}
    assert depth_violations(ladder) == []
    ladder["headers"] = {("func_removed", "f"), ("func_removed_elf_only", "h")}
    assert depth_violations(ladder) == []
    # A qualified debug spelling of a type the higher rung names unqualified.
    ladder = {d: {("func_removed", "f"), ("type_removed", "T<ns::U>")} for d in _DEPTHS}
    ladder["binary"] = {("func_removed", "f"), ("type_removed", "ns::T<ns::U>")}
    assert depth_violations(ladder) == []
    ladder["binary"] = {("func_removed", "f"), ("type_removed", "ns::Other")}
    assert depth_violations(ladder) == [
        ("binary", "headers", {("type_removed", "ns::Other")})
    ]


#: Kinds the header model alone can establish (its own declarations, enum
#: members and underlying types), so dropping the debug layout must not lose
#: them. A kind only DWARF can see (member offsets, padding) is out of scope.
HEADER_OBSERVABLE_KINDS = frozenset(
    {
        "enum_underlying_size_changed",
        "enum_member_removed",
        "enum_member_value_changed",
        "enum_member_added",
        "func_removed",
        "var_removed",
        "func_params_changed",
        "func_return_changed",
    }
)


def lost_without_debug_layout(
    with_debug: set[Finding], without_debug: set[Finding]
) -> set[Finding]:
    """Header-observable findings the debug-layout run reports on a symbol
    the run with the layout dropped reports *nothing* about (bug class
    ``evidence.header_observable_fact_compared_only_from_debug_info``). The
    same symbol judged under another kind is a spelling, not a loss."""
    judged = {sym for _, sym in without_debug}
    judged |= {_unqualified(sym) for sym in judged}
    return {
        (kind, sym)
        for kind, sym in with_debug
        if kind in HEADER_OBSERVABLE_KINDS
        and sym not in judged
        and _unqualified(sym) not in judged
    }


def test_lost_without_debug_layout_flags_a_debug_only_header_fact() -> None:
    full = {("enum_underlying_size_changed", "E"), ("type_field_offset_changed", "S")}
    assert lost_without_debug_layout(full, set()) == {
        ("enum_underlying_size_changed", "E")
    }
    assert (
        lost_without_debug_layout(full, {("enum_underlying_size_changed", "E")})
        == set()
    )
    assert (
        lost_without_debug_layout(
            {("func_removed", "f")}, {("func_removed_elf_only", "f")}
        )
        == set()
    )


def test_depth_rungs_are_the_public_ladder() -> None:
    assert _DEPTHS == ("binary", "headers", "build", "source")


@pytest.mark.integration
@pytest.mark.model_warning_hygiene
@pytest.mark.parametrize("case_name", [c for c, _ in _ALL_CASES])
def test_lower_depth_never_invents_a_removal(
    case_name: str, tmp_path: Path, shared_cmake_build_dir: Path | None
) -> None:
    if CURRENT_PLATFORM not in PLATFORMS.get(case_name, [CURRENT_PLATFORM]):
        pytest.skip(f"{case_name}: not on {CURRENT_PLATFORM}")
    if case_name in BUILD_INFO_CASES or case_name in SOURCE_SMOKE_CASES:
        pytest.skip(f"{case_name}: needs the CLI build/source path")
    if not shutil.which("castxml"):
        pytest.skip("castxml not found in PATH")
    feature = REQUIRES_FEATURE.get(case_name)
    if feature is not None:
        from tests.feature_probe import compiler_supports

        if not compiler_supports(feature):
            pytest.skip(f"{case_name}: compiler lacks {feature!r}")

    import example_catalog  # on sys.path via test_example_autodiscovery

    from abicheck.dumper import dump

    case_dir = example_catalog.case_dir(case_name)
    v1_src, v2_src, v1_hdr, v2_hdr = _find_sources(case_dir)
    v1_lib, v2_lib = _build_libs_for_case(
        case_name, case_dir, tmp_path, shared_cmake_build_dir, v1_src, v2_src
    )
    old = dump(v1_lib, headers=_header_list(v1_hdr), version="v1")
    new = dump(v2_lib, headers=_header_list(v2_hdr), version="v2")

    kinds_by_depth: dict[str, set[Finding]] = {}
    for depth in _DEPTHS:
        p_old, p_new = project_pair_to_depth(old, new, depth)
        kinds_by_depth[depth] = _findings(compare(p_old, p_new).changes)

    assert depth_violations(kinds_by_depth) == [], kinds_by_depth

    # Second relation: the header model's own facts survive dropping the
    # debug layout (the same headers, DWARF read for presence only). A
    # binary-only case has no header model to keep them.
    if not v1_hdr or not v2_hdr:
        return
    old_l2 = dump(
        v1_lib, headers=_header_list(v1_hdr), version="v1", debug_presence_only=True
    )
    new_l2 = dump(
        v2_lib, headers=_header_list(v2_hdr), version="v2", debug_presence_only=True
    )
    with_debug = _findings(compare(old, new).changes)
    without_debug = _findings(compare(old_l2, new_l2).changes)
    assert lost_without_debug_layout(with_debug, without_debug) == set(), without_debug
