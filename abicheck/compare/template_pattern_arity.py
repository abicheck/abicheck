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

"""Mandatory template arity of a class-template *pattern* record.

A backend that models a class template as its pattern (the clang and
gcc-plugin header backends: ``KMb`` with ``is_template_pattern``) records no
``KMb<int>`` instantiation record whose spelled arity could be counted, so
the recorded parameter defaults (``RecordType.template_param_defaults_fact``,
ADR-085 D1) are the only witness of how many arguments a use must spell.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from abicheck.compare.template_surface import qualified_name_lookup, strip_template_args
from abicheck.model.availability import FactStatus
from abicheck.model.source_contract_evidence import ContractEvidenceByName
from abicheck.model.vocabulary import ScopeOrigin

if TYPE_CHECKING:
    from abicheck.model import AbiSnapshot, Function, RecordType


def pattern_mandatory_arity(rec: RecordType) -> int | None:
    """The number of *rec*'s template parameters with no default, or ``None``
    when *rec* is no pattern or its defaults were not recorded (castxml).

    Defaults are trailing, so that count is exactly the arguments every use
    must spell. Known limit: the fact does not mark a parameter pack, which
    reads as a parameter with no default, so appending ``class... Ts`` counts
    as one more mandatory argument.
    """
    if not rec.is_template_pattern:
        return None
    fact = rec.template_param_defaults_fact
    if fact is None or fact.status != FactStatus.PRESENT or fact.value is None:
        return None
    return sum(1 for d in fact.value if d is None)


def count_top_level_template_args(name: str) -> int | None:
    """Count the top-level template arguments in ``name<...>``; ``None`` with no
    ``<``. Commas inside ``(...)``/``[...]`` (a function type's parameters) and
    the ``>`` of a ``->`` arrow are not argument separators.

    ``Foo<int, std::pair<int, char>>`` → 2, ``std::function<void(int, int)>`` → 1.
    """
    if "<" not in name:
        return None
    depth = group = args = 0
    for i, ch in enumerate(name):
        if ch == ">" and name[i - 1 : i] == "-":
            continue
        if ch in "([":
            group += 1
        elif ch in ")]":
            group -= 1
        elif ch == "<":
            depth += 1
        elif ch == ">":
            depth = max(depth - 1, 0)
        elif ch == "," and depth == 1 and group == 0:
            args += 1
    return args + 1


class ArityEvidence(StrEnum):
    """What an arity observation counts -- two different quantities.

    ``PATTERN`` is the number of parameters with no default (a lower bound on
    what any use spells); ``INSTANTIATION`` is how many arguments one concrete
    ``Foo<int, int>`` spells, defaults included. The two are never compared
    with each other: ``Foo<T, U = int>`` is 1 by pattern and 2 by instantiation.
    """

    PATTERN = "pattern"
    INSTANTIATION = "instantiation"


@dataclass
class ArityObservations:
    """Per template identity, the arities observed under each evidence kind."""

    arities: dict[str, dict[ArityEvidence, set[int]]] = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(set))
    )
    # Which declaration kinds ("function"/"type") contributed each identity.
    sources: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    is_public: ContractEvidenceByName = field(default_factory=ContractEvidenceByName)

    def add(self, stem: str, kind: ArityEvidence, arity: int, source: str) -> None:
        self.arities[stem][kind].add(arity)
        self.sources[stem].add(source)


def _record_identity(rec: RecordType) -> str:
    """The namespace-qualified spelling of *rec* (``a::Box``), falling back to
    the bare ``name`` when the backend recorded no qualification."""
    return rec.qualified_name or rec.name


def collect_arity_observations(
    snap: AbiSnapshot, funcs: list[Function]
) -> ArityObservations:
    """Every template arity *snap* witnesses, keyed by qualified template stem."""
    obs = ArityObservations()
    name_of, hdr = qualified_name_lookup(snap), bool(snap.from_headers)
    for f in funcs:
        qname = name_of(f)
        arity = count_top_level_template_args(qname)
        if arity is None:
            continue
        stem = strip_template_args(qname)
        obs.add(stem, ArityEvidence.INSTANTIATION, arity, "function")
        # ADR-084: an export-only function proves no public contract.
        obs.is_public.add(stem, f, header_parsed=hdr)
    for t in snap.declarations.types:
        ident = _record_identity(t)
        arity = pattern_mandatory_arity(t)
        kind = ArityEvidence.PATTERN
        if arity is None:
            arity, kind = (
                count_top_level_template_args(ident),
                ArityEvidence.INSTANTIATION,
            )
        if arity is None:
            continue
        stem = strip_template_args(ident)
        obs.add(stem, kind, arity, "type")
        if t.origin == ScopeOrigin.PUBLIC_HEADER:
            obs.is_public.confirm(stem)
    return obs


def comparable_min_arities(
    old: ArityObservations, new: ArityObservations, stem: str
) -> tuple[int, int] | None:
    """``(old_min, new_min)`` for *stem* under the strongest evidence kind both
    sides recorded, or ``None`` when the sides share none (unknown, not a break).

    A pattern's mandatory count is preferred: a growing instantiation minimum
    can also mean only that the library stopped exporting its smallest use.
    """
    old_kinds, new_kinds = old.arities.get(stem, {}), new.arities.get(stem, {})
    for kind in (ArityEvidence.PATTERN, ArityEvidence.INSTANTIATION):
        if old_kinds.get(kind) and new_kinds.get(kind):
            return min(old_kinds[kind]), min(new_kinds[kind])
    return None
