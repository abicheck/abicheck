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

"""Pair a non-template function's removal with the addition that replaces it
when only a parameter type changed its mangling.

A function's Itanium symbol encodes its parameter types. When a parameter's
class template changes (``basic_vec<float, simd_fixed_size_abi<8, ...>>`` to
``... <16, ...>``), the old symbol disappears and a new one appears under the
same declared name. Matching is by exact symbol, so the two stay a
``FUNC_REMOVED`` and a ``FUNC_ADDED``. Read together they are one change in
one function's signature: ``FUNC_PARAMS_CHANGED``, with both spellings
recorded.

The pairing is deliberately narrow, because overloads are legitimate:

* only a function whose own name carries no template argument list
  (``lib::scale``, not ``lib::scale<int>``): a function template's
  instantiations are a family, and :mod:`abicheck.diff_templates` reads
  them as one;
* only when exactly one removed and one added symbol share the declared name,
  and no other function on either side has it, so a removed ``f(int)``
  beside a surviving ``f(double)`` is never read as a parameter change;
* only when the trailing qualifiers (``const``, ref-qualifier) agree, so a
  cv-qualification change keeps its own detector.

A replacement the rule cannot prove stays the removal and addition it was.
A pair it does prove is one finding, and the verdict does not change: the
old symbol is gone, which was already BREAKING.
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

from ..demangle import demangle
from ..diff_helpers import make_change
from ..model.change_catalog.kinds import ChangeKind
from ..model.export_entity_name import entity_name_components, has_synthetic_component
from .template_surface import qualified_name_table

if TYPE_CHECKING:
    from ..model import AbiSnapshot
    from ..model.change import Change

__all__ = ["pair_parameter_replacements"]

_REMOVAL_KINDS = frozenset({ChangeKind.FUNC_REMOVED, ChangeKind.FUNC_REMOVED_ELF_ONLY})


def _declaration_parts(mangled: str) -> tuple[str, str, str] | None:
    """``(declared name, parameter list, trailing qualifiers)`` of a plain
    non-template function, or ``None`` when the symbol is not one.

    The name comes from the Itanium parse, which stops before the parameter
    list, so it cannot contain one. A template argument list in the name,
    or a constructor, destructor, operator or substitution (none has a
    unique spelling to pair on), makes no claim.
    """
    parsed = entity_name_components(mangled)
    if parsed is None or parsed.template_positions or has_synthetic_component(parsed):
        return None
    text = demangle(mangled)
    if not text:
        return None
    open_at = text.find("(")
    close_at = text.rfind(")")
    if open_at <= 0 or close_at < open_at:
        return None
    return text[:open_at], text[open_at : close_at + 1], text[close_at + 1 :]


def pair_parameter_replacements(
    changes: list[Change], old: AbiSnapshot, new: AbiSnapshot
) -> list[Change]:
    """*changes* with each provable removal/addition pair replaced by one
    ``FUNC_PARAMS_CHANGED``. Every other change passes through unchanged and
    in order; a pair is replaced only when all of its evidence is in hand.
    """
    removed = {c.symbol: c for c in changes if c.kind in _REMOVAL_KINDS and c.symbol}
    added = {c.symbol: c for c in changes if c.kind is ChangeKind.FUNC_ADDED}
    if not removed or not added:
        return changes

    old_fns = old.function_map
    new_fns = new.function_map
    old_names = qualified_name_table(old)
    new_names = qualified_name_table(new)
    removed_by_stem: dict[str, list[str]] = {}
    for symbol in removed:
        fn = old_fns.get(symbol)
        if fn is not None:
            stem = old_names.get(id(fn), fn.name).partition("(")[0]
            removed_by_stem.setdefault(stem, []).append(symbol)
    added_by_stem: dict[str, list[str]] = {}
    for symbol in added:
        fn = new_fns.get(symbol)
        if fn is not None:
            stem = new_names.get(id(fn), fn.name).partition("(")[0]
            added_by_stem.setdefault(stem, []).append(symbol)
    # Only a stem with one removal and one addition can pair, so the whole-side
    # count below runs solely for those: a comparison with no such stem pays
    # nothing beyond the two small lists above.
    paired_stems = {
        stem
        for stem, symbols in removed_by_stem.items()
        if len(symbols) == 1 and len(added_by_stem.get(stem, ())) == 1
    }
    if not paired_stems:
        return changes
    old_counts = Counter(
        old_names.get(id(fn), fn.name).partition("(")[0]
        for fn in old_fns.values()
        if old_names.get(id(fn), fn.name).partition("(")[0] in paired_stems
    )
    new_counts = Counter(
        new_names.get(id(fn), fn.name).partition("(")[0]
        for fn in new_fns.values()
        if new_names.get(id(fn), fn.name).partition("(")[0] in paired_stems
    )

    replaced: dict[str, Change] = {}
    consumed_added: set[str] = set()
    for old_symbol, removal in removed.items():
        old_fn = old_fns.get(old_symbol)
        if old_fn is None:
            continue
        stem = old_names.get(id(old_fn), old_fn.name).partition("(")[0]
        candidates = added_by_stem.get(stem, [])
        if stem not in paired_stems or old_counts[stem] != 1 or new_counts[stem] != 1:
            continue
        new_symbol = candidates[0]
        old_parts = _declaration_parts(old_symbol)
        new_parts = _declaration_parts(new_symbol)
        if old_parts is None or new_parts is None:
            continue
        if old_parts[2] != new_parts[2] or old_parts[1] == new_parts[1]:
            continue
        replaced[old_symbol] = make_change(
            ChangeKind.FUNC_PARAMS_CHANGED,
            symbol=old_symbol,
            name=old_fn.name,
            old=old_parts[1],
            new=new_parts[1],
            description=(
                f"Parameters changed: {old_parts[0]}{old_parts[1]} -> "
                f"{new_parts[1]} (replacement symbol {new_symbol})"
            ),
            entity_id=removal.entity_id,
        )
        consumed_added.add(new_symbol)
    if not replaced:
        return changes

    result: list[Change] = []
    for change in changes:
        if change.kind in _REMOVAL_KINDS and change.symbol in replaced:
            result.append(replaced[change.symbol])
        elif change.kind is ChangeKind.FUNC_ADDED and change.symbol in consumed_added:
            continue
        else:
            result.append(change)
    return result
