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
* only when exactly one removed and one added symbol share the declaration
  key, and no other function on either side has it, so a removed ``f(int)``
  beside a surviving ``f(double)`` is never read as a parameter change;
* only when the trailing qualifiers (``const``, ref-qualifier) agree, so a
  cv-qualification change keeps its own detector;
* only when both sides carry the same kind of evidence: both header-parsed
  or neither.

Without headers the declaration key is the declared name: by its spelling a
replaced symbol is all the binary shows. With headers on both sides the key
is the *header declaration* -- the declared name, every parameter's and the
return type's spelling as written in the header, and the declaring header --
and the pair must also show why the mangling moved: some parameter, spelled
the same, resolves to a different type (its resolved record/enum differs, or
it names a typedef whose target changed). That is ``V scale(V, float)`` with
``using V = vec<8>`` retargeted to ``vec<16>``: the same declaration whose
parameter type changed. A declaration whose written signature changed is
not paired, so a parameter retyped to an internal type stays a removal from
the public contract (``func_removed``, the documented ground truth of
case189); nor is one whose header evidence shows no resolved change at all,
so a renumbered lambda closure in the mangling stays the removal of the old
exported key (``tests/test_closure_param_churn_pipeline.py``).

A replacement the rule cannot prove stays the removal and addition it was.
A pair it does prove is one finding, and the verdict does not change: the
old symbol is gone, which was already BREAKING.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Hashable, Mapping
from typing import TYPE_CHECKING

from ..demangle import demangle
from ..diff_helpers import make_change, typedef_diff_maps
from ..model.change_catalog.kinds import ChangeKind
from ..model.export_entity_name import entity_name_components, has_synthetic_component
from .template_surface import qualified_name_table

if TYPE_CHECKING:
    from ..model import AbiSnapshot, Function
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


def _return_types_disagree(old_fn: Function, new_fn: Function) -> bool:
    """Whether both sides establish a return type and the two differ. The
    removal would not show that change, so such a pair stays a removal and an
    addition. An unknown return type on either side does not block pairing."""
    return bool(
        old_fn.return_type
        and new_fn.return_type
        and old_fn.return_type != new_fn.return_type
    )


def _declaring_header(fn: Function) -> str:
    """Basename of the header *fn* is declared in (``""`` when unknown): the
    two sides are parsed from different trees, so only the name compares."""
    path = (fn.source_location or "").rpartition(":")[0] or (fn.source_location or "")
    return path.replace("\\", "/").rpartition("/")[2]


def _declaration_key(fn: Function, stem: str, from_headers: bool) -> Hashable:
    """What identifies *fn*'s declaration across versions: the declared name
    alone without headers; with them, the header declaration as written."""
    if not from_headers:
        return stem
    return (
        stem,
        tuple(p.type for p in fn.params),
        fn.return_type,
        _declaring_header(fn),
    )


class _HeaderEvidence:
    """What the two header-parsed sides say a parameter spelling resolves to:
    the typedef maps, and each named record's layout (built on first use)."""

    def __init__(self, old: AbiSnapshot, new: AbiSnapshot) -> None:
        self._sides = (old, new)
        self._typedefs = typedef_diff_maps(old, new)
        self._layouts: list[dict[str, list[tuple[int | None, int | None]]]] = []

    def typedef_target_changed(self, spelling: str, stem: str) -> bool:
        """Whether *spelling* names a typedef, visible from *stem*'s scope,
        whose target differs between the sides. The innermost scope that
        declares the name on either side is the one the spelling resolves to."""
        # The one name left once cv and declarator sigils are stripped, as
        # typedef_respelling._core_leaf reads it, but with its scope kept.
        tokens = spelling.replace("*", " ").replace("&", " ").split()
        core = [t for t in tokens if t not in ("const", "volatile")]
        if len(core) != 1:
            return False
        name = core[0]
        scopes = stem.split("::")[:-1]
        old_map, new_map = self._typedefs
        for depth in range(len(scopes), -1, -1):
            candidate = "::".join([*scopes[:depth], name])
            if candidate in old_map or candidate in new_map:
                return (
                    candidate in old_map
                    and candidate in new_map
                    and old_map[candidate] != new_map[candidate]
                )
        return False

    def record_layout_changed(self, identity: str) -> bool:
        """Whether the one record named *identity* on each side has a known
        size or alignment that differs: the same spelling now resolves to a
        different type (a default template argument chosen per target, say)."""
        if not self._layouts:
            for side in self._sides:
                layouts: dict[str, list[tuple[int | None, int | None]]] = {}
                for record in side.declarations.types:
                    layouts.setdefault(record.qualified_name or record.name, []).append(
                        (record.size_bits, record.alignment_bits)
                    )
                self._layouts.append(layouts)
        old_rec, new_rec = (layouts.get(identity, ()) for layouts in self._layouts)
        if len(old_rec) != 1 or len(new_rec) != 1:
            return False
        return any(
            o is not None and n is not None and o != n
            for o, n in zip(old_rec[0], new_rec[0], strict=True)
        )


def _resolved_parameter_changed(
    old_fn: Function, new_fn: Function, stem: str, evidence: _HeaderEvidence
) -> bool:
    """Whether a parameter written the same on both sides resolves to a
    different type: the header backend's resolved record/enum identities
    differ, the one record they name changed layout, or the spelling names a
    typedef whose target changed. Without one, nothing in the header
    declaration explains the changed mangling."""
    for old_p, new_p in zip(old_fn.params, new_fn.params, strict=True):
        old_ids, new_ids = old_p.type_identities_fact, new_p.type_identities_fact
        if (
            old_ids is not None
            and new_ids is not None
            and old_ids.is_present
            and new_ids.is_present
        ):
            if old_ids.value != new_ids.value:
                return True
            if any(evidence.record_layout_changed(i) for i in old_ids.value or ()):
                return True
        if evidence.typedef_target_changed(old_p.type, stem):
            return True
    return False


def pair_parameter_replacements(
    changes: list[Change], old: AbiSnapshot, new: AbiSnapshot
) -> list[Change]:
    """*changes* with each provable removal/addition pair replaced by one
    ``FUNC_PARAMS_CHANGED``. Every other change passes through unchanged and
    in order; a pair is replaced only when all of its evidence is in hand.
    """
    from_headers = old.from_headers
    if from_headers != new.from_headers:
        return changes
    removed = {c.symbol: c for c in changes if c.kind in _REMOVAL_KINDS and c.symbol}
    added = {c.symbol: c for c in changes if c.kind is ChangeKind.FUNC_ADDED}
    if not removed or not added:
        return changes

    old_fns = old.function_map
    new_fns = new.function_map
    old_names = qualified_name_table(old)
    new_names = qualified_name_table(new)

    def stem_of(fn: Function, names: Mapping[int, str]) -> str:
        return names.get(id(fn), fn.name).partition("(")[0]

    def key_of(fn: Function, names: Mapping[int, str]) -> Hashable:
        return _declaration_key(fn, stem_of(fn, names), from_headers)

    removed_by_key: dict[Hashable, list[str]] = {}
    for symbol in removed:
        fn = old_fns.get(symbol)
        if fn is not None:
            removed_by_key.setdefault(key_of(fn, old_names), []).append(symbol)
    added_by_key: dict[Hashable, list[str]] = {}
    for symbol in added:
        fn = new_fns.get(symbol)
        if fn is not None:
            added_by_key.setdefault(key_of(fn, new_names), []).append(symbol)
    # Only a key with one removal and one addition can pair, so the whole-side
    # count below runs solely for those: a comparison with no such key pays
    # nothing beyond the two small lists above.
    paired_keys = {
        key
        for key, symbols in removed_by_key.items()
        if len(symbols) == 1 and len(added_by_key.get(key, ())) == 1
    }
    if not paired_keys:
        return changes
    old_counts = Counter(
        key for fn in old_fns.values() if (key := key_of(fn, old_names)) in paired_keys
    )
    new_counts = Counter(
        key for fn in new_fns.values() if (key := key_of(fn, new_names)) in paired_keys
    )
    evidence = _HeaderEvidence(old, new) if from_headers else None

    replaced: dict[str, Change] = {}
    consumed_added: set[str] = set()
    for old_symbol, removal in removed.items():
        old_fn = old_fns.get(old_symbol)
        if old_fn is None:
            continue
        key = key_of(old_fn, old_names)
        if key not in paired_keys or old_counts[key] != 1 or new_counts[key] != 1:
            continue
        new_symbol = added_by_key[key][0]
        old_parts = _declaration_parts(old_symbol)
        new_parts = _declaration_parts(new_symbol)
        if old_parts is None or new_parts is None:
            continue
        if old_parts[2] != new_parts[2] or old_parts[1] == new_parts[1]:
            continue
        new_fn = new_fns.get(new_symbol)
        if new_fn is None or _return_types_disagree(old_fn, new_fn):
            continue
        if evidence is not None and not _resolved_parameter_changed(
            old_fn, new_fn, stem_of(old_fn, old_names), evidence
        ):
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
