# Copyright 2026 Nikolay Petrov
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

"""The ELF-only variant relation between ``ChangeKind`` values.

A removal or addition that was detected from the ELF export table alone (no
header declaration to name it) is classified with an ``*_elf_only`` kind
instead of the base kind: ``FUNC_REMOVED`` vs ``FUNC_REMOVED_ELF_ONLY``
depends on whether headers were supplied to the run, not on the change itself
(see :mod:`abicheck.model.surface_facts`). A selector that names the base kind
(``change_kind: func_removed``) must therefore match the variant too, or its
acceptance would depend on the evidence supplied (AGENTS.md "Policy decides
acceptance, not facts").

The pairs are *derived* from the catalog, never listed by hand: a kind whose
name is ``<base>_elf_only`` for another ``ChangeKind`` ``<base>`` is that
base's variant. The rule is one-directional. A selector naming the variant
still matches only the variant, so a deliberately ELF-only waiver stays narrow.

This module is the one owner of the relation; every matcher that compares a
selector's ``change_kind`` against an observed kind routes through
:func:`kind_match_set` (suppression, reclassification, acknowledgment, and the
rule-inventory bookkeeping that mirrors them).
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from .kinds import ChangeKind

ELF_ONLY_SUFFIX = "_elf_only"


def _derive_elf_only_variants(values: frozenset[str]) -> Mapping[str, str]:
    """``{base: base_elf_only}`` for every ``*_elf_only`` kind whose base is a kind."""
    variants: dict[str, str] = {}
    for value in values:
        if not value.endswith(ELF_ONLY_SUFFIX):
            continue
        base = value[: -len(ELF_ONLY_SUFFIX)]
        if base in values:
            variants[base] = value
    return MappingProxyType(dict(sorted(variants.items())))


#: ``{base kind value: its ELF-only variant value}``, derived from ``ChangeKind``.
ELF_ONLY_VARIANT_OF: Mapping[str, str] = _derive_elf_only_variants(
    frozenset(kind.value for kind in ChangeKind)
)


def kind_match_set(kind: str) -> frozenset[str]:
    """The observed kind values a selector naming *kind* matches.

    ``{kind}`` for a kind with no ELF-only variant, and ``{kind, variant}``
    for a base kind that has one. A kind that is itself a variant (or any
    unrecognised spelling) matches only itself.
    """
    variant = ELF_ONLY_VARIANT_OF.get(kind)
    if variant is None:
        return frozenset({kind})
    return frozenset({kind, variant})


def matched_via_variant(selector_kind: str | None, observed_kind: str) -> bool:
    """True when *observed_kind* matched *selector_kind* only through its variant.

    The audit uses this to say *how* a rule matched (ADR-067 record-before-
    disposing): a ``func_removed`` rule that hid a ``func_removed_elf_only``
    finding must read as such, not as an exact-kind match.
    """
    if selector_kind is None or observed_kind == selector_kind:
        return False
    return observed_kind in kind_match_set(selector_kind)
