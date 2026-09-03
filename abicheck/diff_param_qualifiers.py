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
  — so it would flag it. Taking ``dict[str, Function]`` keeps this module
  from depending on ``diff_symbols``, which is the other remedy the root
  ``AGENTS.md`` names. It DOES import
  :func:`~abicheck.finding_identity_ctor_dtor.iter_matched_function_pairs`
  (PR #761 finding 2) — ``finding_identity_ctor_dtor`` is itself a leaf
  (no import back to ``diff_symbols`` or this module), so that import adds
  no cycle; it is what lets a ctor/dtor pair reconciled across a
  synthetic-key format-drift also be visible to the ``restrict``/
  ``va_list`` per-parameter joins below, not just to
  ``diff_symbols._check_function_signature``.
"""

from __future__ import annotations

from .checker_policy import ChangeKind
from .checker_types import Change
from .compare.fact_comparison import compare_facts
from .diff_helpers import make_change
from .finding_identity_ctor_dtor import iter_matched_function_pairs
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
    for mangled, f_old, f_new in iter_matched_function_pairs(old_map, new_map):
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
                        entity_id=f_old.entity_id or f_new.entity_id,
                    )
                )
    return changes


def param_va_list_changes(
    old_map: dict[str, Function], new_map: dict[str, Function]
) -> list[Change]:
    """``PARAM_BECAME_VA_LIST``/``PARAM_LOST_VA_LIST`` for flipped parameters.

    The evidence gates live with the registration in ``diff_symbols.py``, not
    here (same split as ``param_restrict_changes`` above): by the time this
    runs both sides are known to be header-derived, **"clang"-produced
    specifically** (not "hybrid" — see below), and carry reliable va_list
    facts (G31 Phase C continued — ``dumper_clang_qualifiers.
    _clang_param_is_va_list``, x86-64 System V spelling only; castxml never
    populates this fact at all).

    **Unlike ``param_restrict_changes``, "hybrid" is excluded from the
    producer gate entirely, not merely reliability-gated** (Codex review,
    fresh evidence). ``dumper_hybrid._merge_functions`` keeps castxml's own
    ``params`` verbatim for every MATCHED function (parameters are never
    merged field-by-field — see ``Param.default``'s identical note in
    ``scripts/backend_capabilities.py``). For ``is_restrict``, that's safe:
    castxml IS a real producer of that fact, so a matched function's
    castxml-verbatim param still carries a genuine answer either way. For
    ``is_va_list``, castxml has NEVER populated it, so a matched function's
    param reads a permanent, version-independent ``False`` — not a legacy-
    baseline artifact ``clang_va_list_facts_reliable`` could catch, since
    it's just as false on a snapshot built with the current parser as an
    old one. The dangerous case: comparing two hybrid snapshots (or a
    hybrid against a clang one) where the SAME function's parser coverage
    differs between old and new — clang-only-appended in one snapshot,
    matched-by-both-and-therefore-blind in the other — would read a real,
    unchanged ``va_list`` parameter as added/removed purely from that
    coverage shift, not a real qualifier change. No per-function provenance
    exists today to distinguish a matched (blind) function's param from a
    clang-only (real) one, so the whole producer is excluded rather than
    guessed at; revisit once ``dumper_hybrid.py`` backfills ``is_va_list``
    per parameter the way it does for a handful of scalar record-layout
    attributes today.

    **ADR-063 Phase 5B:** the snapshot-level ``clang_va_list_facts_reliable``
    gate above only says the *producer* is trustworthy when it ran — it does
    not guarantee ``is_va_list_fact`` reached ``PRESENT`` for *this specific*
    parameter (a per-function extraction failure still leaves an individual
    ``Param.is_va_list_fact`` at ``NOT_COLLECTED``/``FAILED``). Each pair is
    gated through :func:`compare_facts` rather than the old
    present-or-``False`` collapse (``_fact_bool``, since removed from this
    module): a parameter whose evidence is incomplete on either side is
    skipped instead of silently read as "confirmed not ``va_list``", which
    could otherwise fabricate a ``PARAM_BECAME_VA_LIST``/
    ``PARAM_LOST_VA_LIST`` finding against a real ``va_list`` parameter on
    the other side purely from that side's own capture gap.
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
