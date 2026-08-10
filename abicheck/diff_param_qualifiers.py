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

"""Parameter *qualifier* detectors — ``restrict`` and ``va_list``.

Split out of ``diff_symbols.py``, which sits at the 2000-line hard cap the
AI-readiness gate enforces. The two detectors here share one shape that makes
them a natural pair: each compares a per-parameter **bool** whose ``False``
means both "not qualified" and "this producer never collected the fact", so
each needs an evidence gate rather than a bare value comparison — the same
distinction ``diff_default_value_reliability.py`` draws for ``Param.default``.

Import direction: this module imports from ``diff_symbols`` (for the shared
``_public_functions``/``_both_header_aware`` helpers), never the reverse —
the same direction ``diff_filtering.py``/``diff_types.py`` already take.
Registration happens through ``checker.py``'s detector-import block, not
through a back-import from ``diff_symbols``, so no cycle is introduced.
"""

from __future__ import annotations

from .checker_policy import ChangeKind
from .checker_types import Change
from .detector_registry import registry
from .diff_helpers import make_change
from .diff_symbols import _both_header_aware, _public_functions
from .model import AbiSnapshot


@registry.detector("param_restrict")
def _diff_param_restrict(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect restrict qualifier changes on parameters (ABICC: Parameter_Became_Restrict).

    Header-tier only, and gated at the snapshot level for the same reason
    ``param_defaults`` is (G31 Phase C). ``Param.is_restrict`` is a plain
    bool with no "not collected" state, and it is populated ONLY by the two
    header-AST backends — DWARF, PDB, and the symbol-table paths never set
    it at all — so a side that was not (confirmed) parsed from headers reads
    as "no parameter is restrict-qualified" rather than "unknown", and
    comparing it against a header-parsed side reports every real ``restrict``
    as removed/added purely from an evidence-tier difference.

    Additionally declined when either side's restrict facts are marked
    unreliable (``AbiSnapshot.clang_restrict_facts_reliable``): the
    direct-clang backend populated this fact for the first time in schema
    v22, so a persisted pre-v22 clang/hybrid baseline's blanket ``False``
    is real-but-WRONG data — indistinguishable by value from a genuinely
    unqualified parameter, exactly like the pre-v21 clang vtable case.

    Two *reliable* header sides may safely be compared across producers:
    since v22 both backends populate this fact, and unlike ``Param.default``
    its value representation is directly cross-comparable (a plain bool,
    not a backend-specific encoding), so no same-producer check is needed —
    the same reasoning ``fact_provenance.both_known_backed_fact`` encodes
    for ``deprecated``/``is_scoped``.
    """
    if not _both_header_aware(old, new):
        return []
    if not (old.clang_restrict_facts_reliable and new.clang_restrict_facts_reliable):
        return []
    changes: list[Change] = []
    old_map = _public_functions(old)
    new_map = _public_functions(new)

    for mangled, f_old in old_map.items():
        f_new = new_map.get(mangled)
        if f_new is None:
            continue
        for i, (p_old, p_new) in enumerate(zip(f_old.params, f_new.params)):
            if p_old.is_restrict != p_new.is_restrict:
                direction = "added" if p_new.is_restrict else "removed"
                changes.append(
                    make_change(
                        ChangeKind.PARAM_RESTRICT_CHANGED,
                        symbol=mangled,
                        name=f_old.name,
                        detail=direction,
                        old=str(p_old.name or i),
                        old_value=f"restrict={p_old.is_restrict}",
                        new_value=f"restrict={p_new.is_restrict}",
                    )
                )
    return changes


@registry.detector("param_va_list")
def _diff_param_va_list(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect va_list parameter changes (ABICC: Parameter_Became_VaList/Non_VaList).

    Unlike ``param_restrict`` above, this one carries no evidence gate — and
    needs none today for a reason worth stating rather than leaving implicit:
    **no producer on any layer populates** ``Param.is_va_list`` (see
    ``docs/reference/header-backend-capabilities.md``'s Known gaps), so both
    sides are always ``False`` and this detector cannot fire on real input.
    Should a backend start extracting it, it inherits ``param_restrict``'s
    problem exactly — a cross-producer or legacy-baseline comparison reading
    a blanket ``False`` as "not a va_list" — and will need the same gate.
    """
    changes: list[Change] = []
    old_map = _public_functions(old)
    new_map = _public_functions(new)

    for mangled, f_old in old_map.items():
        f_new = new_map.get(mangled)
        if f_new is None:
            continue
        for i, (p_old, p_new) in enumerate(zip(f_old.params, f_new.params)):
            if not p_old.is_va_list and p_new.is_va_list:
                changes.append(
                    make_change(
                        ChangeKind.PARAM_BECAME_VA_LIST,
                        symbol=mangled,
                        name=f_old.name,
                        detail=str(p_old.name or i),
                        old_value=p_old.type,
                        new_value="va_list",
                    )
                )
            elif p_old.is_va_list and not p_new.is_va_list:
                changes.append(
                    make_change(
                        ChangeKind.PARAM_LOST_VA_LIST,
                        symbol=mangled,
                        name=f_old.name,
                        detail=str(p_old.name or i),
                        old_value="va_list",
                        new_value=p_new.type,
                    )
                )
    return changes
