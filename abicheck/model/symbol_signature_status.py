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

"""The two answers a bundle signature check asks about one symbol.

Its own module rather than a block inside ``bundle_models.py`` for the
reason ADR-061 gives: a value type every stage agrees on belongs in
``model/``, the innermost ring. It moved here when adding the shared-value
table pushed ``bundle_models.py`` past the architecture gate's 800-line
production ceiling -- and AGENTS.md is explicit that the way to shrink such
a file is to give a responsibility a proper owner, never to trim it to fit
or to record a debt baseline. This type and the four values it can hold are
exactly one responsibility.

``bundle_models`` re-exports both names, so
``from abicheck.bundle_models import SymbolSignatureStatus`` still
resolves and no existing caller changes.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["SymbolSignatureStatus", "symbol_signature_status"]


@dataclass(frozen=True, slots=True)
class SymbolSignatureStatus:
    """The two answers a bundle signature check asks about one symbol.

    Both are plain booleans deliberately: every tri-state and
    legacy/unknown distinction the underlying predicates draw
    (``Fact``-backed export evidence versus a pre-split snapshot's
    conflated ``Visibility``, an uncaptured ``is_variadic`` or
    ``contract_attributes``, an unresolved type spelling) is resolved
    *while the full snapshot is alive*, by those predicates, into the same
    yes/no the consumer would have got from the snapshot itself. Storing
    them unresolved would mean keeping the declarations that carry them,
    which is the retention this type exists to remove.

    A symbol absent from the mapping is one that was in neither
    ``function_map`` nor ``variable_map``: both predicates answer ``False``
    for that case, so absence and ``(False, False)`` are the same answer,
    and the mapping holds only symbols the snapshot actually declared.
    """

    exported: bool
    evidence_sufficient: bool


#: Every value this type can ever hold. Two booleans admit exactly four
#: combinations, so the whole value space is enumerable and is built once
#: here rather than one object per symbol.
_SYMBOL_SIGNATURE_STATUSES: dict[tuple[bool, bool], SymbolSignatureStatus] = {
    (exported, evidence_sufficient): SymbolSignatureStatus(
        exported=exported, evidence_sufficient=evidence_sufficient
    )
    for exported in (False, True)
    for evidence_sufficient in (False, True)
}


def symbol_signature_status(
    *, exported: bool, evidence_sufficient: bool
) -> SymbolSignatureStatus:
    """The shared :class:`SymbolSignatureStatus` for this pair of answers.

    The type is frozen, slotted, and carries two booleans, so it has
    exactly four inhabitants -- yet the caller allocates one per symbol.
    Measured on a real oneDAL release: 48 bytes per instance against
    87,728 symbols is 4.02 MiB for one library, and the release fan-out
    retains a mapping per matched member, so roughly 24 MiB across six.
    That is about 1% of a measured ~2.3 GiB peak: this is here because it
    is free and provably safe, **not** because it addresses the memory
    problem -- the member-concurrency measurement owns that.

    Sharing is safe precisely because the type is frozen: no consumer can
    mutate one instance into another's value, and none distinguishes two
    equal statuses by identity (checked across every reader, not assumed).
    Equality and hashing are unchanged; only `is` becomes true more often,
    which is a widening no reader depends on either way.

    The arguments are coerced with :func:`bool` rather than used as-is.
    The fields are annotated ``bool`` and every predicate feeding this is
    documented to answer yes/no, so a truthy non-``bool`` leaking through
    would have been stored verbatim before and silently violated that
    annotation; normalizing here keeps the four shared values genuinely
    interchangeable with a directly-constructed one.
    """
    return _SYMBOL_SIGNATURE_STATUSES[(bool(exported), bool(evidence_sufficient))]
