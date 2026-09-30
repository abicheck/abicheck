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

"""Per-declaration availability gate for a two-sided fact (ADR-063 5B).

A header-tier detector may compare a ``Fact[T]``-bridged field only when
*both* sides state it ``PRESENT``: any other status means the producer did
not establish the value, and the legacy field then holds a resting default
(``None``/``False``) that is indistinguishable from a real "not deprecated"/
"not scoped". This replaced the per-declaration ``fact_provenance`` lookups
the seven header facts used to be gated on (``RecordType``/``EnumType``/
``Function``/``Variable``/``TypeField.deprecated``, ``EnumType.is_scoped``,
``TypeField.default``): every place that answer used to be derived from now
writes it into the status instead --

- a fresh dump states it per declaration (castxml, clang, hybrid merge);
- a non-header, DWARF, PDB or BTF/CTF producer states ``NOT_COLLECTED``/
  ``UNSUPPORTED``;
- a stored document is corrected on load by ``storage.fact_backfill``
  (non-header or unrecognized producers, a pre-v19 clang document, and a
  legacy hybrid declaration no backend recorded all downgrade to
  ``NOT_COLLECTED``).

A decline is recorded for T9's accounting (``declined_comparisons``) only
when it is informative: one side established the fact and the other did
not, or a side reports ``FAILED``/``PARTIAL``. Two sides that both never
collected the fact (an ELF-only run) are the run's evidence level, not a
per-entity decline, and recording each would bury the real ones.

Whether two *present* values are cross-comparable is a separate question
this gate does not answer -- ``TypeField.default``'s representation still
differs by producer, and its detector keeps that check.
"""

from __future__ import annotations

from typing import Any

from ..model import FactStatus
from .declined_comparisons import record_declined

_LOUD = frozenset({FactStatus.FAILED, FactStatus.PARTIAL})


def both_facts_present(old_obj: Any, new_obj: Any, field: str, entity: str) -> bool:
    """True when ``<field>_fact`` is ``PRESENT`` on both *old_obj* and *new_obj*.

    Otherwise returns False and, when informative (see module docstring),
    records a decline for *entity*.
    """
    old_status = getattr(old_obj, f"{field}_fact").status
    new_status = getattr(new_obj, f"{field}_fact").status
    if old_status is FactStatus.PRESENT and new_status is FactStatus.PRESENT:
        return True
    if (
        FactStatus.PRESENT in (old_status, new_status)
        or {old_status, new_status} & _LOUD
    ):
        record_declined(
            entity,
            f"{field} fact {old_status.value} (old) / {new_status.value} (new)",
        )
    return False
