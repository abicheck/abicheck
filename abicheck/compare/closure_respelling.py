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

"""Drop signature findings that only re-spell a lambda closure's ordinal.

A non-constructor function whose parameter or return type is parameterized
over a local lambda closure (``raii_guard<(lambda:lib.h#1)>``) is reported
as ``func_params_changed``/``func_return_changed`` (or the template
equivalents) when an unrelated edit adds a lambda earlier in the same
header: the per-header ordinal of that closure moves from ``#1`` to ``#2``.
The linker symbol is unchanged, because the closure's Itanium encoding is
scoped to the function that spells it.

The spelling is not evidence on its own. The same spelling change also
appears when a closure's real encoding changes, which is a genuine break.
So a finding is dropped only when BOTH hold:

1. the two spellings differ only in closure discriminators
   (:func:`~abicheck.model.graph_identity.closure_discriminator_free_identity`),
2. the finding's symbol is a real Itanium key exported under that same
   spelling on both sides. A mangled key encodes every closure parameter's
   Itanium name, so a changed encoding changes the key and fails this test.

Anything else -- an unexported or ``extern`` symbol, a synthetic
constructor/destructor key, missing ELF evidence -- is left as the detector
made it. That is fail-closed, the same stance as
:func:`abicheck.diff_templates.demote_lambda_closure_unexported_findings`.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable

from ..model.availability import FactStatus
from ..model.change import Change
from ..model.change_catalog.kinds import ChangeKind
from ..model.graph_identity import (
    closure_discriminator_free_identity,
    closure_ordinal_keys,
)
from ..model.snapshot import AbiSnapshot
from ..model.surface_facts import binary_exported

#: Signature-level kinds whose recorded old/new spellings carry a closure
#: discriminator. Other kinds never embed a parameter/return spelling.
RESPELLING_KINDS: frozenset[ChangeKind] = frozenset(
    {
        ChangeKind.FUNC_PARAMS_CHANGED,
        ChangeKind.FUNC_RETURN_CHANGED,
        ChangeKind.TEMPLATE_PARAM_TYPE_CHANGED,
        ChangeKind.TEMPLATE_RETURN_TYPE_CHANGED,
    }
)


def is_closure_respelling(
    change: Change,
    old_exported: Collection[str],
    new_exported: Collection[str],
) -> bool:
    """Whether *change* only re-spells a closure discriminator on a symbol
    that is exported under the same real key on both sides."""
    if change.kind not in RESPELLING_KINDS:
        return False
    symbol = change.symbol or ""
    if not symbol or symbol not in old_exported or symbol not in new_exported:
        return False
    old, new = change.old_value, change.new_value
    if old is None or new is None or old == new:
        return False
    return closure_discriminator_free_identity(
        old
    ) == closure_discriminator_free_identity(new)


def exported_function_keys(
    snapshot: AbiSnapshot, wanted: Collection[str]
) -> frozenset[str]:
    """The keys among *wanted* that *snapshot*'s own functions carry and the
    binary export table carries too, read from the surface's export fact
    (``binary_exported_fact``, ``PRESENT(True)`` only). Anything else --
    absent, partial, or never collected -- is not evidence, so it is not
    listed: fail closed. Only *wanted* keys are looked up, so the fact is read
    once per candidate rather than once per function."""
    keys: set[str] = set()
    for f in snapshot.declarations.functions:
        if f.mangled not in wanted:
            continue
        fact = binary_exported(f)
        if fact.status is FactStatus.PRESENT and fact.value is True:
            keys.add(f.mangled)
    return frozenset(keys)


def closure_ordinal_map_is_uniform(candidates: Iterable[Change]) -> bool:
    """Whether the closure ordinals the candidate findings spell form one
    strictly increasing old-to-new map.

    An unrelated closure inserted above shifts every ordinal by the same
    amount: a uniform, order-preserving renumbering. Reordering two closures
    maps ``1 -> 2`` in one function and ``2 -> 1`` in another, and each
    finding looks like a shift on its own. So the map is built across all
    candidates together. A map that is not a function, or not strictly
    increasing, means the ordinals do not name the same closures on both
    sides, and nothing is dropped (fail closed).

    Only the candidates' own spellings are used. Other matched functions can
    carry an ordinal that is not renumbered consistently with the rest of the
    dump (an implicit copy constructor spells the guarded closure as ``#1`` on
    both sides), so they are not evidence about the mapping.
    """
    # One map per closure namespace (marker kind and header): the same ordinal
    # in two headers names two different closures.
    mappings: dict[tuple[str, str], dict[int, int]] = {}
    for change in candidates:
        old_keys = closure_ordinal_keys(change.old_value or "")
        new_keys = closure_ordinal_keys(change.new_value or "")
        if len(old_keys) != len(new_keys):
            return False
        for (kind_a, file_a, a), (kind_b, file_b, b) in zip(
            old_keys, new_keys, strict=True
        ):
            if (kind_a, file_a) != (kind_b, file_b):
                return False
            mapping = mappings.setdefault((kind_a, file_a), {})
            if mapping.setdefault(a, b) != b:
                return False
    for mapping in mappings.values():
        pairs = sorted(mapping.items())
        if not all(pairs[i][1] > pairs[i - 1][1] for i in range(1, len(pairs))):
            return False
    return True


def drop_closure_respellings(
    changes: Iterable[Change],
    old: AbiSnapshot,
    new: AbiSnapshot,
) -> list[Change]:
    """Return *changes* without the closure-only re-spellings, in order.

    A candidate must pass :func:`is_closure_respelling`, and the candidates
    together must map their closure ordinals uniformly
    (:func:`closure_ordinal_map_is_uniform`).
    If the uniform check fails, nothing is dropped. Nothing is recorded: a
    dropped finding is one the spelling alone cannot tell from an unchanged
    declaration, not a fact the user needs to see.
    """
    change_list = list(changes)
    if not any(c.kind in RESPELLING_KINDS for c in change_list):
        return change_list
    wanted = {c.symbol for c in change_list if c.kind in RESPELLING_KINDS and c.symbol}
    old_exported = exported_function_keys(old, wanted)
    new_exported = exported_function_keys(new, wanted)
    candidates = [
        c for c in change_list if is_closure_respelling(c, old_exported, new_exported)
    ]
    if not candidates or not closure_ordinal_map_is_uniform(candidates):
        return change_list
    dropped = {id(c) for c in candidates}
    return [c for c in change_list if id(c) not in dropped]
