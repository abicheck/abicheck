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

"""Hidden-friend (in-class ``friend`` declaration) diff detectors.

Split out of ``diff_symbols.py`` (which sits at the AI-readiness file-size
hard cap) rather than grown in place — see ``AGENTS.md`` "Files that are
large".
"""

from __future__ import annotations

from collections.abc import Mapping

from .compare.function_signature import (
    FunctionSignature,
    FunctionSignatureIndex,
    function_signature_index,
    hidden_friend_changes,
)
from .diff_helpers import make_change
from .model import Function
from .model.change import Change
from .model.change_catalog.kinds import ChangeKind


def diff_inline_hidden_friends(
    old_all: Mapping[str, Function],
    new_all: Mapping[str, Function],
    old_public: Mapping[str, Function],
    new_public: Mapping[str, Function],
    signatures: tuple[FunctionSignatureIndex, FunctionSignatureIndex] | None = None,
) -> list[Change]:
    """Pick up hidden-friend transitions that the public-symbol diff misses.

    Inline-defined hidden friends never appear in the .so dynsym (the
    compiler emits them as `linkonce_odr`, often inlined into callers).
    They show up in the castxml snapshot with ``visibility=HIDDEN`` and
    ``is_hidden_friend=True``, so the public-symbol diff (which only
    matches on *old_public*/*new_public*, i.e. ``_public_functions()``'s
    PUBLIC/ELF_ONLY filter) never even considers them. This pass compares
    across the full function map (*old_all*/*new_all*) instead, so it
    covers three shapes:

    * present only in *old_all* — removed together with its symbol.
    * present only in *new_all* — added together with its symbol.
    * present (same mangled key) in both — the friend keeps its symbol
      identity but may still flip ``is_hidden_friend`` with no change to
      its signature (e.g. an in-class ``friend`` declaration pulled out
      to file scope, or vice versa, which preserves the mangled name
      since a hidden friend already mangles under its enclosing
      namespace, not the class). When at least one side is HIDDEN this
      transition would otherwise never be observed — the sibling
      paired hidden-friend check (``compare/function_signature.py``) only runs on pairs matched from
      *old_public*/*new_public* — so it is checked here too, but only
      when the pair was NOT already covered by that public-symbol
      pairing (both sides public), to avoid emitting it twice (Codex
      review).

    The hidden-friend facts are read through each side's ``SemanticIR``
    (*signatures*, ADR-063 6B function-qualifier cohort); without one, each
    side is projected from its own functions with the same formula.
    """
    if signatures is None:
        signatures = (
            function_signature_index(None, old_all.values()),
            function_signature_index(None, new_all.values()),
        )
    old_index, new_index = signatures

    def facts(index: FunctionSignatureIndex, fn: Function) -> FunctionSignature:
        return index.signature_for(fn)

    changes: list[Change] = []
    for mangled, f_old in old_all.items():
        f_new = new_all.get(mangled)
        old_sig = facts(old_index, f_old)
        if f_new is None:
            if old_sig.is_hidden_friend:
                changes.append(
                    make_change(
                        ChangeKind.HIDDEN_FRIEND_REMOVED,
                        symbol=mangled,
                        old=f_old.name,
                        caused_by_type=old_sig.hidden_friend_owner,
                        entity_id=f_old.entity_id,
                    )
                )
            continue
        if mangled in old_public and mangled in new_public:
            continue
        changes.extend(
            hidden_friend_changes(
                mangled,
                f_old.name,
                old_sig,
                facts(new_index, f_new),
                f_old.entity_id or f_new.entity_id,
            )
        )
    for mangled, f_new in new_all.items():
        if mangled in old_all:
            continue
        new_sig = facts(new_index, f_new)
        if new_sig.is_hidden_friend:
            changes.append(
                make_change(
                    ChangeKind.HIDDEN_FRIEND_ADDED,
                    symbol=mangled,
                    new=f_new.name,
                    caused_by_type=new_sig.hidden_friend_owner,
                    entity_id=f_new.entity_id,
                )
            )
    return changes
