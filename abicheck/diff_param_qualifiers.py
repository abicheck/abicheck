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

**This module holds the loop bodies, not the detectors.** The
``@registry.detector`` registrations stay in ``diff_symbols.py``, at the
exact source positions they have always occupied, and each hands this module
the already-selected public-function maps. That split is load-bearing, not
stylistic:

- **Registration order is user-visible.** ``registry.detector()`` stamps an
  incrementing counter and ``run_all()`` executes in that order, so it fixes
  the order findings appear in every JSON/text report. Registering these two
  from a new module moved ``param_restrict`` from index 16 to 5 and
  ``param_va_list`` from 20 to 6 — measured, not theorised (Codex review).
  Since a module split should change no behaviour at all, the registration
  had to stay put; only the bodies moved.
- **The maps come in as arguments** rather than this module importing
  ``diff_symbols._public_functions`` itself. A back-import would be a real
  cycle once ``diff_symbols`` imports this module, and the AI-readiness
  import-cycle gate walks *every* AST import — a function-local one included
  — so it would flag it. Taking ``dict[str, Function]`` keeps this module a
  leaf, which is the other remedy the root ``AGENTS.md`` names.
"""

from __future__ import annotations

from .checker_policy import ChangeKind
from .checker_types import Change
from .diff_helpers import make_change
from .model import Function


def param_restrict_changes(
    old_map: dict[str, Function], new_map: dict[str, Function]
) -> list[Change]:
    """``PARAM_RESTRICT_CHANGED`` for every parameter whose qualifier flipped.

    The evidence gates live with the registration in ``diff_symbols.py``, not
    here, since they are snapshot-level questions; by the time this runs both
    sides are known to be header-derived with reliable restrict facts.
    """
    changes: list[Change] = []
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


def param_va_list_changes(
    old_map: dict[str, Function], new_map: dict[str, Function]
) -> list[Change]:
    """``PARAM_BECAME_VA_LIST``/``PARAM_LOST_VA_LIST`` for flipped parameters.

    The evidence gates live with the registration in ``diff_symbols.py``, not
    here (same split as ``param_restrict_changes`` above): by the time this
    runs both sides are known to be header-derived, clang/hybrid-produced,
    and carry reliable va_list facts (G31 Phase C continued —
    ``dumper_clang_qualifiers._clang_param_is_va_list``, x86-64 System V
    spelling only; castxml never populates this fact). See
    ``diff_symbols._diff_param_va_list``'s own docstring for why this one is
    NOT symmetric across producers the way ``param_restrict`` is.
    """
    changes: list[Change] = []
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
