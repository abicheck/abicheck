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

"""Which type identities a finding can be about (ADR-049 contract relevance).

One responsibility, moved out of ``contract_evaluation.py`` so that module
stops growing: turning a :class:`~abicheck.checker_types.Change` into the
set of record/enum spellings a surface membership check should test. Both
contract domains consult :func:`type_candidates` (``public`` against
``exact_type_identities``, ``exports`` against its own closure);
:func:`qualified_type_candidates` adds the matched record's own qualified
identity for the ``public`` domain's exact check (schema v54 work, closing
the ``ambiguous_namespaced_leaf`` gap). ``contract_replay`` reads the
per-kind symbol shapes from here too.
"""

from __future__ import annotations

from ..model.change import Change
from ..model.change_catalog.kinds import ChangeKind
from ..model.type_identifiers import type_identifiers as _type_identifiers
from ..surface import _MEMBER_LEVEL_TYPE_KIND_NAMES, _TYPE_LEVEL_KIND_NAMES

# Which spelling a member-level finding's `symbol` actually carries. Verified
# producer by producer (Codex review): `diff_types.py`'s field/union families
# and `field_bitfield_changed` record the owning *type* alone with the member
# in `detail` (`symbol=name`), while its enum families and
# `diff_platform.py`'s `struct_field_*` record owner *and* member
# (`symbol=f"{name}::{mname}"`). Built from real `ChangeKind` members so a
# stale slug fails at import instead of silently taking the wrong branch.
OWNER_IS_SYMBOL_KINDS: frozenset[str] = frozenset(
    k.value
    for k in (
        ChangeKind.TYPE_FIELD_ADDED,
        ChangeKind.TYPE_FIELD_ADDED_COMPATIBLE,
        ChangeKind.TYPE_FIELD_OFFSET_CHANGED,
        ChangeKind.TYPE_FIELD_REMOVED,
        ChangeKind.TYPE_FIELD_TYPE_CHANGED,
        ChangeKind.UNION_FIELD_ADDED,
        ChangeKind.UNION_FIELD_REMOVED,
        ChangeKind.UNION_FIELD_TYPE_CHANGED,
        ChangeKind.FIELD_BITFIELD_CHANGED,
    )
)

OWNER_PLUS_MEMBER_KINDS: frozenset[str] = frozenset(
    k.value
    for k in (
        ChangeKind.ENUM_MEMBER_ADDED,
        ChangeKind.ENUM_MEMBER_REMOVED,
        ChangeKind.ENUM_MEMBER_VALUE_CHANGED,
        ChangeKind.ENUM_LAST_MEMBER_VALUE_CHANGED,
        ChangeKind.STRUCT_FIELD_OFFSET_CHANGED,
        ChangeKind.STRUCT_FIELD_REMOVED,
        ChangeKind.STRUCT_FIELD_TYPE_CHANGED,
    )
)

assert OWNER_IS_SYMBOL_KINDS.isdisjoint(OWNER_PLUS_MEMBER_KINDS), (
    "a member-level kind cannot carry both symbol shapes"
)
# A kind added to surface.py's member-level set but not classified here would
# silently fall through to the generic `_type_identifiers` path and lose its
# owner, so fail loudly at import instead (the same discipline as this
# module's terminal/weak surface-reason assertion above).
assert (OWNER_IS_SYMBOL_KINDS | OWNER_PLUS_MEMBER_KINDS) == frozenset(
    _MEMBER_LEVEL_TYPE_KIND_NAMES
), "every _MEMBER_LEVEL_TYPE_KIND_NAMES member must declare its symbol shape"


def type_candidates(change: Change) -> set[str]:
    """Type names *change* could be about, mirroring ``classify_change_surface``'s
    own candidate derivation (never a naive raw-string comparison: a raw
    ``caused_by_type`` spelling ``"const Foo *"`` would not literal-match a
    bare ``"Foo"`` entry).

    A member-level finding (``_MEMBER_LEVEL_TYPE_KIND_NAMES``) must resolve to
    its *owner* type -- but the producers in that one kind set disagree on
    whether ``symbol`` already names the member, so **both** readings are
    offered as candidates (Codex review, confirmed against ``diff_types.py``):

    - :data:`OWNER_PLUS_MEMBER_KINDS` record ``symbol=f"{name}::{mname}"``,
      so the owner is the ``"::"``-stripped prefix. Passing the full spelling
      to :func:`_type_identifiers` would yield ``{"ns::Mode::X", "X"}``, never
      the owner that is the real type-universe entry.
    - :data:`OWNER_IS_SYMBOL_KINDS` record ``symbol=name`` -- the owning
      *type* alone, with the member name in ``detail``. Stripping there turns
      ``"ns::Foo"`` into the namespace fragment ``"ns"``, losing the owner
      entirely and leaving a genuinely public field change unconfirmed.

    The shape is selected **per kind** (:data:`OWNER_PLUS_MEMBER_KINDS` vs
    :data:`OWNER_IS_SYMBOL_KINDS`), not guessed. Offering *both* readings --
    which an earlier revision did -- over-corrects in the other direction: for
    a nested ``"Outer::Helper"`` field finding the stripped ``"Outer"`` may
    well be inside the closure while ``Outer::Helper`` is not, which would
    confirm a finding about the nested type on its parent's membership (Codex
    review).
    """
    sym = change.symbol or ""
    if sym and "::" in sym:
        if change.kind.value in OWNER_PLUS_MEMBER_KINDS:
            return {sym.rsplit("::", 1)[0]} | _type_identifiers(change.caused_by_type)
        if change.kind.value in OWNER_IS_SYMBOL_KINDS:
            return {sym} | _type_identifiers(change.caused_by_type)
    return _type_identifiers(sym) | _type_identifiers(change.caused_by_type)


def qualified_type_candidates(change: Change) -> set[str]:
    """The matched record/enum's own qualified identity, for a type finding.

    A type-level finding's ``symbol`` is the record's bare ``name``
    (``Cache``) -- castxml/clang record the leaf there and the scope
    separately -- so when two namespaces share the leaf, the symbol alone
    cannot say which record changed, and :func:`type_candidates` (built
    from that spelling) never reaches ``ns1::Cache``. The detector that
    matched the old/new pair *does* know: ``compare/record_layout.py``,
    ``diff_types.py``'s field families and the vtable/layout detectors stamp
    ``Change.qualified_name`` from the matched ``RecordType`` itself.

    Offered only as a candidate against ``exact_type_identities``, never
    ``public_types``: an exact identity is one the ambiguity-vetoing walk
    reached unambiguously, so a match confirms *this* record and not a
    same-leaf sibling. A ``qualified_name`` that names something else
    entirely (the enrichment pass can copy a same-spelled function's name
    onto a finding -- ``diff_filtering._qualified_name_for_change``) is no
    record identity at all and simply matches nothing: a miss, never a
    false confirmation.

    For an owner-plus-member kind the qualified name carries the member
    too (``ns::Mode::X``), so its owner prefix is the candidate -- the same
    per-kind shape :func:`type_candidates` applies to ``symbol``.
    """
    if change.kind.value not in _TYPE_LEVEL_KIND_NAMES:
        return set()
    qualified = change.qualified_name or ""
    if not qualified:
        return set()
    if change.kind.value in OWNER_PLUS_MEMBER_KINDS:
        return {qualified.rsplit("::", 1)[0]} if "::" in qualified else set()
    return {qualified}
