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

"""Join a constructor/destructor *export* onto the *declarations* two
snapshots carry, with explicit resolution states.

One question, asked by ``compare/undeclared_exports.py``'s removal half and
answerable only here: **is this lost export already the declaration-aware
diff's business, and is losing it provably no obligation to any already-linked
consumer?** Those are two separate facts and this module keeps them separate
-- ``join`` reports how well the owner resolved, ``covered`` reports whether
both facts held.

Why it is not a predicate inside the detector. The predicate it replaces
asked ``snapshot.type_by_name(components[-2])`` -- the owner's *bare*
rightmost component -- and ``type_by_name`` is keyed by ``RecordType.name``,
which every header backend fills with the **unqualified** spelling
(measured: a real ``g++``/castxml dump of ``namespace api { struct Base; }``
indexes the type as ``"Base"``, never ``"api::Base"``). So the qualified
candidate it tried first could never match anything, the bare one always
decided, and two classes named ``Base`` in different namespaces share one
index entry (first-wins, the loser logged as a dropped duplicate). A lost
``other::Base::Base()`` export was therefore suppressed by the existence of
an unrelated ``api::Base``. Resolving through the *declaration* index
instead -- whose ctor/dtor placeholder keys are fully qualified
(``__abicheck_ctor__api::Base(int)``, ``~api::Base``) -- makes the join exact
and ambiguity-visible rather than silently first-wins.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from ..model.owner_recovery import itanium_special_member_owner

if TYPE_CHECKING:
    from ..model import AbiSnapshot
    from ..model.declarations import Function

#: The synthetic ``function_map`` key prefix both header backends use for a
#: constructor. One source declaration corresponds to several ABI symbols
#: (C1/C2/C3), so no single Itanium mangling is "the" one and the backends
#: key the declaration under this placeholder instead.
_CTOR_PLACEHOLDER_PREFIX = "__abicheck_ctor__"
#: The destructor counterpart (``~api::Widget``), for the same reason (D0/D1/D2).
_DTOR_PLACEHOLDER_PREFIX = "~"


class OwnerJoin(Enum):
    """How well a ctor/dtor export's owning declaration resolved.

    ``UNSUPPORTED`` and ``UNRESOLVED`` are deliberately distinct: the first
    says this structural parser reached no verdict about the *name*, the
    second says it did and the snapshots declare no such owner. Neither is a
    licence to conclude anything -- both leave the finding reported, per this
    repository's unresolved-coverage semantics.
    """

    UNIQUE = "unique"
    AMBIGUOUS = "ambiguous"
    UNRESOLVED = "unresolved"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class SpecialMemberCoverage:
    """The join's full result, reason code included.

    ``covered`` is the only field a caller may act on to *drop* a finding,
    and it is never true unless ``join`` is :attr:`OwnerJoin.UNIQUE`. The
    remaining fields exist so a per-record classification table can state
    *why* each observed export loss was kept or handed over, rather than
    reporting a bare boolean nobody can audit.
    """

    join: OwnerJoin
    reason: str
    owner: str | None = None
    member: str | None = None
    templated: bool = False
    inherited: bool = False
    covered: bool = False


def _strip_template_arguments(qualified: str) -> str:
    """``api::Box<int>`` -> ``api::Box``; angle-depth aware, so a nested
    ``Box<Pair<int, char>>`` reduces in one pass and an ``operator<``
    spelling (which a ctor/dtor placeholder key can never carry) is simply
    never reached."""
    out: list[str] = []
    depth = 0
    for ch in qualified:
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth = max(0, depth - 1)
        elif depth == 0:
            out.append(ch)
    return "".join(out)


def _placeholder_owner(mangled_key: str) -> tuple[str, str] | None:
    """``(member, template-stripped qualified owner)`` for a ctor/dtor
    placeholder key, or ``None`` when *mangled_key* is an ordinary mangling."""
    if mangled_key.startswith(_CTOR_PLACEHOLDER_PREFIX):
        rest = mangled_key[len(_CTOR_PLACEHOLDER_PREFIX) :]
        depth = 0
        for idx, ch in enumerate(rest):
            if ch == "<":
                depth += 1
            elif ch == ">":
                depth = max(0, depth - 1)
            elif ch == "(" and depth == 0:
                rest = rest[:idx]
                break
        return "{ctor}", _strip_template_arguments(rest)
    if mangled_key.startswith(_DTOR_PLACEHOLDER_PREFIX):
        return "{dtor}", _strip_template_arguments(
            mangled_key[len(_DTOR_PLACEHOLDER_PREFIX) :]
        )
    return None


def declared_special_members(
    snapshot: AbiSnapshot,
) -> dict[tuple[str, str], list[Function]]:
    """``(member, qualified owner)`` -> the declarations under that key.

    Built from ``function_map``'s placeholder keys, which are the only
    fully-qualified ctor/dtor evidence a header-aware snapshot carries.
    """
    index: dict[tuple[str, str], list[Function]] = {}
    for key, declaration in snapshot.function_map.items():
        parsed = _placeholder_owner(key)
        if parsed is None:
            continue
        member, owner = parsed
        if not owner:
            continue
        index.setdefault((member, owner), []).append(declaration)
    return index


def special_member_export_coverage(
    mangled: str, old: AbiSnapshot, new: AbiSnapshot
) -> SpecialMemberCoverage:
    """Whether losing the ctor/dtor export *mangled* is provably covered
    elsewhere, with the evidence that decided it.

    ``covered`` requires **all** of the following, and each one is a fact
    about evidence rather than a convention:

    1. The name parses and its leaf is a ctor/dtor (otherwise ``UNSUPPORTED``
       -- an Itanium special name such as ``_ZTV``/``_ZTI`` never reaches
       here, which is what keeps this disjoint from the vtable/RTTI export
       losses the detector exists to catch).
    2. The owner is **not** a template specialization. A specialization's
       exported definition can be the only one in the program: an
       ``extern template`` consumer deliberately does not instantiate, so an
       already-linked client holds a real undefined reference to a symbol the
       library alone provided. Measured on a native control -- localizing
       ``_ZN3api3BoxIiEC1Ev``, a ``W``/COMDAT symbol, made a client built
       against OLD fail to load against NEW. The owner path this parser
       recovers is template-argument-stripped by construction, so it can name
       only the primary template, and a primary template's declaration is not
       evidence about any one specialization's binary obligation.
    3. Exactly one declaration key matches, and it matches on **both** sides.
       A declaration that changed is the declaration-aware diff's business
       and it will say so under a kind that knows what changed; a declaration
       present on only one side is a declaration change, not emission churn.
    4. Every matching declaration on both sides is ``is_inline`` -- an
       in-class-defined or explicitly inline special member, whose definition
       every consumer's own translation unit can (and does) emit for itself.
       This is the one fact that separates the two shapes that look identical
       in the export table: a rebuild at a higher optimization level drops
       the out-of-line copy of an inline ctor and breaks nobody, while
       localizing an out-of-line ctor defined in the library's own source
       breaks every client that linked against it. Measured on a native
       control for that second shape too: with headers, source and SONAME
       unchanged and only a version script localizing ``_ZN3api6WidgetC1Ev``,
       a client built against OLD failed to load against NEW. ``is_inline``
       is asserted for *all* matching declarations because one mangling does
       not say which overload it belongs to -- requiring the weaker fact of
       every candidate is the direction that keeps a real loss reported.

    Anything short of all four leaves ``covered`` false, which reports the
    finding and lets public-surface scoping, contract evaluation, suppression
    and policy decide relevance -- the machinery that already owns that
    judgement for every other change.
    """
    owner = itanium_special_member_owner(mangled)
    if owner is None:
        return SpecialMemberCoverage(
            join=OwnerJoin.UNSUPPORTED, reason="not_a_parsed_special_member"
        )
    qualified = owner.qualified_owner
    base = SpecialMemberCoverage(
        join=OwnerJoin.UNRESOLVED,
        reason="",
        owner=qualified,
        member=owner.member,
        templated=owner.owner_carries_template_arguments,
        inherited=owner.inherited,
    )
    old_index = declared_special_members(old)
    new_index = declared_special_members(new)
    key = (owner.member, qualified)
    old_decls = old_index.get(key, [])
    new_decls = new_index.get(key, [])
    if not old_decls and not new_decls:
        # Distinguish "no such owner anywhere" from "several owners share
        # this bare tail" -- the second is what the replaced predicate did
        # silently, and it is never a resolution.
        tail = qualified.rpartition("::")[2]
        colliding = {
            declared_owner
            for index in (old_index, new_index)
            for (member, declared_owner) in index
            if member == owner.member and declared_owner.rpartition("::")[2] == tail
        }
        if len(colliding) >= 1:
            return _with(
                base, join=OwnerJoin.AMBIGUOUS, reason="bare_name_collision_only"
            )
        return _with(base, join=OwnerJoin.UNRESOLVED, reason="owner_not_declared")
    if not old_decls or not new_decls:
        return _with(
            base, join=OwnerJoin.UNIQUE, reason="owner_declared_on_one_side_only"
        )
    if owner.owner_carries_template_arguments:
        return _with(base, join=OwnerJoin.UNIQUE, reason="template_specialization")
    if not all(d.is_inline for d in (*old_decls, *new_decls)):
        return _with(base, join=OwnerJoin.UNIQUE, reason="out_of_line_definition")
    return _with(
        base,
        join=OwnerJoin.UNIQUE,
        reason="inline_declaration_unchanged_on_both_sides",
        covered=True,
    )


def _with(base: SpecialMemberCoverage, **changes: object) -> SpecialMemberCoverage:
    from dataclasses import replace

    return replace(base, **changes)  # type: ignore[arg-type]
