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
from ..model.graph_identity import closure_discriminator_free_identity
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
    if change.old_value is None or change.new_value is None:
        return False
    if change.old_value == change.new_value:
        return False
    return closure_discriminator_free_identity(
        change.old_value
    ) == closure_discriminator_free_identity(change.new_value)


def exported_function_keys(snapshot: AbiSnapshot) -> frozenset[str]:
    """The real Itanium keys of *snapshot*'s functions that the binary
    export table carries, read from the surface's own export fact
    (``binary_exported_fact``, ``PRESENT(True)`` only). Anything else --
    absent, partial, or never collected -- is not evidence, so it is not
    listed: fail closed."""
    return frozenset(
        f.mangled
        for f in snapshot.declarations.functions
        if f.mangled
        and binary_exported(f).status is FactStatus.PRESENT
        and binary_exported(f).value is True
    )


def drop_closure_respellings(
    changes: Iterable[Change],
    old: AbiSnapshot,
    new: AbiSnapshot,
) -> list[Change]:
    """Return *changes* without the closure-only re-spellings, in order.

    Nothing is recorded: a finding that is dropped here is one the spelling
    alone cannot distinguish from an unchanged declaration, not a fact the
    user needs to see. The exported-key condition is what makes that safe,
    and it is what the tests pin.
    """
    old_exported = exported_function_keys(old)
    new_exported = exported_function_keys(new)
    return [
        c for c in changes if not is_closure_respelling(c, old_exported, new_exported)
    ]
