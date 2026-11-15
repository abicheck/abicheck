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

"""Field renames inside a record, read off the change list.

``diff_cpp_patterns.detect_inline_body_renamed_member`` looks for a member of
an internal (pimpl) record that was renamed while a public inline accessor
still reads it. A rename shows either as ``field_renamed`` or as a removed
field paired with an added one; these collectors return both shapes as
``(record_name, old_field, new_field)`` triples.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from ..model.change import Change
from ..model.change_catalog.kinds import ChangeKind

__all__ = ["collect_field_rename_candidates", "collect_paired_field_candidates"]


def collect_field_rename_candidates(
    changes: Iterable[Change],
    namespaces: tuple[str, ...],
) -> list[tuple[str, str, str]]:
    """Return ``(record_name, old_field, new_field)`` triples from FIELD_RENAMED changes
    whose record belongs to an internal namespace.

    FIELD_RENAMED's ``symbol`` is the (possibly namespace-qualified) record
    name itself — e.g. ``mylib::detail::descriptor_impl`` — not a
    ``Record::field`` pair; the field names live in ``old_value``/
    ``new_value``. Splitting on the last ``::`` here used to strip the record
    name down to its enclosing namespace (or drop it to ``""`` when
    unqualified), so ``is_internal_type`` never matched and no candidate was
    ever collected — this detector never actually fired (case89).
    """
    del namespaces  # the name is checked at emission (PIMPL_RENAMED_MEMBER)
    candidates: list[tuple[str, str, str]] = []
    for ch in changes:
        if ch.kind != ChangeKind.FIELD_RENAMED:
            continue
        record_name = ch.symbol
        if ch.old_value and ch.new_value:
            candidates.append((record_name, str(ch.old_value), str(ch.new_value)))
    return candidates


#: The field-delta kinds a record's member rename can show as.
_FIELD_REMOVED_KINDS = frozenset(
    {
        ChangeKind.TYPE_FIELD_REMOVED,
        ChangeKind.STRUCT_FIELD_REMOVED,
        ChangeKind.UNION_FIELD_REMOVED,
    }
)
_FIELD_ADDED_KINDS = frozenset(
    {
        ChangeKind.TYPE_FIELD_ADDED,
        ChangeKind.TYPE_FIELD_ADDED_COMPATIBLE,
        ChangeKind.UNION_FIELD_ADDED,
    }
)


def collect_paired_field_candidates(
    changes: Iterable[Change],
    namespaces: tuple[str, ...],
) -> list[tuple[str, str, str]]:
    """Synthesise ``(record_name, old_field, new_field)`` triples from paired
    field removals and additions on the same internal type.

    Covers the case where the AST emitter doesn't produce a FIELD_RENAMED
    but does produce paired field deltas (a "modernize naming" refactor).
    An addition is any of the field-added kinds: a renamed member that lands
    at the end of the record reads ``type_field_added_compatible``, and is
    still the new half of the rename.
    """
    del namespaces  # the name is checked at emission (PIMPL_RENAMED_MEMBER)
    by_internal: dict[str, tuple[list[str], list[str]]] = defaultdict(lambda: ([], []))
    for ch in changes:
        if ch.kind not in _FIELD_REMOVED_KINDS and ch.kind not in _FIELD_ADDED_KINDS:
            continue
        # `symbol` for these is typically "Type::field_name"
        if "::" not in ch.symbol:
            continue
        rec, fld = ch.symbol.rsplit("::", 1)
        removed_list, added_list = by_internal[rec]
        if ch.kind in _FIELD_REMOVED_KINDS:
            removed_list.append(fld)
        else:
            added_list.append(fld)

    candidates: list[tuple[str, str, str]] = []
    for rec, (removed, added) in by_internal.items():
        # Pair positionally — equal count is the strongest hint of a rename batch.
        if removed and added and len(removed) == len(added):
            for old_field, new_field in zip(removed, added, strict=False):
                candidates.append((rec, old_field, new_field))
    return candidates
