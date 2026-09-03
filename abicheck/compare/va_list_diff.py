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

"""``Param.is_va_list`` raw-change identification (ADR-063 Phase 5B).

Split out of ``diff_param_qualifiers.param_va_list_changes`` (Codex review,
PR #1033: the same "identifying a raw change belongs in `compare/`"
routing this PR's `base_class_diff.py` was split out for) — its own
detector registration and evidence-reliability gate stay in
``diff_symbols.py``/``diff_param_qualifiers.py``; only the per-parameter
``FactStatus``-aware comparison loop moved.
"""

from __future__ import annotations

from ..checker_types import Change
from ..diff_helpers import make_change
from ..finding_identity_ctor_dtor import iter_matched_function_pairs
from ..model import Function
from ..model.change_catalog.kinds import ChangeKind
from .fact_comparison import compare_facts


def diff_va_list_params(
    old_map: dict[str, Function], new_map: dict[str, Function]
) -> list[Change]:
    """``PARAM_BECAME_VA_LIST``/``PARAM_LOST_VA_LIST`` for flipped parameters.

    The snapshot-level ``clang_va_list_facts_reliable`` gate the caller
    applies before this runs only says the *producer* is trustworthy when
    it ran — it does not guarantee ``is_va_list_fact`` reached ``PRESENT``
    for *this specific* parameter (a per-function extraction failure still
    leaves an individual ``Param.is_va_list_fact`` at ``NOT_COLLECTED``/
    ``FAILED``). Each pair is gated through :func:`~.fact_comparison.
    compare_facts` rather than the old present-or-``False`` collapse: a
    parameter whose evidence is incomplete on either side is skipped
    instead of silently read as "confirmed not ``va_list``", which could
    otherwise fabricate a ``PARAM_BECAME_VA_LIST``/``PARAM_LOST_VA_LIST``
    finding against a real ``va_list`` parameter on the other side purely
    from that side's own capture gap.
    """
    changes: list[Change] = []
    for mangled, f_old, f_new in iter_matched_function_pairs(old_map, new_map):
        for i, (p_old, p_new) in enumerate(zip(f_old.params, f_new.params)):
            va_list_cmp = compare_facts(
                p_old.is_va_list_fact, p_new.is_va_list_fact, False
            )
            if not va_list_cmp.is_comparable:
                continue
            old_is_va_list = bool(va_list_cmp.old_value)
            new_is_va_list = bool(va_list_cmp.new_value)
            if not old_is_va_list and new_is_va_list:
                changes.append(
                    make_change(
                        ChangeKind.PARAM_BECAME_VA_LIST,
                        symbol=mangled,
                        name=f_old.name,
                        detail=str(p_old.name or i),
                        old_value=p_old.type,
                        new_value="va_list",
                        entity_id=f_old.entity_id or f_new.entity_id,
                    )
                )
            elif old_is_va_list and not new_is_va_list:
                changes.append(
                    make_change(
                        ChangeKind.PARAM_LOST_VA_LIST,
                        symbol=mangled,
                        name=f_old.name,
                        detail=str(p_old.name or i),
                        old_value="va_list",
                        new_value=p_new.type,
                        entity_id=f_old.entity_id or f_new.entity_id,
                    )
                )
    return changes
