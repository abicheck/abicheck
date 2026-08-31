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

"""Rescue a castxml L4 synthetic ctor/dtor key against a real export table
(Codex review, PR #930).

``source_link._route_declaration`` gives a ``compiler_generated`` entity one
export-match attempt before dropping it, so an ODR-used implicit special
member with a real weak export is preserved rather than lost. That match is
``entity.mangled_name or entity.qualified_name`` against the export set --
correct when castxml recorded a real Itanium mangled name (an
``OperatorMethod`` like copy/move ``operator=`` always does), but a
constructor or destructor whose real mangled name castxml omitted carries a
*synthetic* key instead (``dumper_castxml.SYNTHETIC_CTOR_KEY_PREFIX``/
``is_synthetic_ctor_key``, ``is_synthetic_dtor_key``) -- a stable per-overload
identity, never a real ABI symbol, so it can never match a real export by
that direct comparison. This module gives that case a second, narrower
attempt: does the class the synthetic key names have *any* exported ctor/dtor
at all, per :func:`itanium_scope_components`'s own structural (not
demangler-shelling-out) Itanium parsing -- which already handles a ctor/dtor
mangled name's scope prefix correctly, confirmed empirically
(``_ZN6WidgetC1ERKS_`` -> ``["Widget", "{ctor}"]``).

Deliberately conservative in one direction: a templated owner (its castxml-
recorded scope spelling embeds ``"<...>"``, C++ syntax) is never matched here.
``itanium_scope_components``'s own scope spelling for a template instantiation
is the raw mangled template-argument encoding (``"BoxIiE"`` for ``Box<int>``),
which does not textually agree with castxml's own spelled form -- and this
codebase's own history (see AGENTS.md's "linkage-blind-removal"/type-identity
"Known gaps" entries) shows that reconciling two independently-spelled
identities via a partial/coincidental match is exactly the class of bug that
took multiple review rounds to find and revert elsewhere. Left as a
documented, accepted residual rather than attempted reactively here -- a
templated class's synthetic-keyed ctor/dtor still falls back to the original
"no export table visibility, drop it" behavior.

A second, accepted residual (Codex review): the rescue is class-level, not
per-overload -- ``synthetic_key_owner_has_export`` asks "does this owner have
*any* matching ctor/dtor export at all", not "does *this specific* candidate
have one". A class whose only real export is (say) its implicit copy
constructor still keeps its default/move-constructor candidates too, each
recorded reachable-but-unmatched rather than dropped, and the real export
itself stays unmatched in ``symbols_without_decl``. Resolving the actual
overload-to-export mapping needs decoding an Itanium ctor/dtor's mangled
parameter types structurally and comparing them against castxml's own
spelled parameter list -- a materially larger parser than this module's
owner-scope matching, not attempted here. Keeping an unresolved candidate
visible (rather than silently dropping it) is still the safer of the two
failure modes per ADR-028 D3's "never silently delete a genuine
declaration" rule.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import TYPE_CHECKING

from ..dumper_castxml import (
    SYNTHETIC_CTOR_KEY_PREFIX,
    is_synthetic_ctor_key,
    is_synthetic_dtor_key,
)
from ..model.mangled_name import itanium_scope_components

if TYPE_CHECKING:
    from .source_abi import SourceEntity

_CTOR_MARKER = "{ctor}"
_DTOR_MARKER = "{dtor}"
#: `itanium_scope_components` renders an ABI-tagged component (a real GCC/Clang
#: `__attribute__((abi_tag("v1")))`) as `"Widget[abi:v1]"`, but castxml's own
#: synthetic ctor/dtor key encodes only the plain source-level class name it
#: parsed -- it never carries an ABI-tag suffix. Stripped before indexing/
#: lookup so an ABI-tagged public class's real ctor/dtor exports still match
#: (Codex review, PR #930): the tag versions one logical class's ABI, it does
#: not name a different class castxml would have spelled differently.
_ABI_TAG_RE = re.compile(r"\[abi:[^\]]*\]")

#: MSVC's operator codes for a plain (non-clone) constructor/destructor
#: (``??0Widget@@...`` / ``??1Widget@@...``). ``msvc_scope_components``
#: deliberately excludes these -- its own docstring notes the "name" slot
#: for a special member is an operator code, not a plain identifier, so its
#: generic leaf/scope split does not apply -- so this module parses them
#: directly rather than reusing that function. Vector/scalar deleting
#: destructors (``??_E``/``??_G``) and other clone forms are left
#: unrecognized, the same conservative-miss bias this module already takes
#: for a templated Itanium owner (see below).
_MSVC_CTOR_OP = "??0"
_MSVC_DTOR_OP = "??1"


def _msvc_owner(mangled: str, op: str) -> str | None:
    """Owner scope (``"::"``-joined) of an MSVC-mangled plain ctor/dtor, or
    ``None`` if *mangled* isn't one -- mirrors ``msvc_scope_components``'s
    own component-validity checks (reject a template/anonymous-namespace/
    backreference component) for the class-name chain after *op*."""
    if not mangled.startswith(op):
        return None
    rest = mangled[len(op) :]
    idx = rest.find("@@")
    if idx == -1:
        return None
    head = rest[:idx]
    if not head:
        return None
    parts = head.split("@")
    if any(not p or p.startswith("?") or p.isdigit() for p in parts):
        return None
    return "::".join(reversed(parts))


def build_ctor_dtor_owner_index(exported: set[str]) -> dict[str, str]:
    """Owner scope (demangled, ``"::"``-joined, ABI tags stripped) ->
    ``"ctor"``/``"dtor"``/``"both"``, for every export an Itanium or MSVC
    ctor/dtor marker was found in.

    Built once per link, mirroring how :func:`~.source_link._build_export_index`
    already indexes ``exported`` up front rather than re-scanning it per entity.
    """
    index: dict[str, str] = {}
    for sym in exported:
        owner: str | None
        comps = itanium_scope_components(sym)
        if comps and len(comps) >= 2:
            marker = comps[-1]
            if marker == _CTOR_MARKER:
                kind = "ctor"
            elif marker == _DTOR_MARKER:
                kind = "dtor"
            else:
                continue
            owner = _ABI_TAG_RE.sub("", "::".join(comps[:-1]))
        else:
            owner = _msvc_owner(sym, _MSVC_CTOR_OP)
            if owner is not None:
                kind = "ctor"
            else:
                owner = _msvc_owner(sym, _MSVC_DTOR_OP)
                if owner is not None:
                    kind = "dtor"
                else:
                    continue
        existing = index.get(owner)
        index[owner] = "both" if existing and existing != kind else kind
    return index


def synthetic_key_owner_has_export(key: str, owner_index: dict[str, str]) -> bool:
    """Whether *key* (a castxml synthetic ctor/dtor key) names a class that
    ``owner_index`` (see :func:`build_ctor_dtor_owner_index`) records a
    matching real export for.

    Always ``False`` for a templated owner -- see the module docstring for
    why that direction is a deliberate, documented residual, not a bug.
    """
    if is_synthetic_ctor_key(key):
        owner = key[len(SYNTHETIC_CTOR_KEY_PREFIX) :].split("(", 1)[0]
        want = "ctor"
    elif is_synthetic_dtor_key(key):
        owner = key[1:]
        want = "dtor"
    else:
        return False
    if "<" in owner:
        return False
    found = owner_index.get(owner)
    return found in (want, "both")


def should_drop_generated_candidate(
    export_sym: str, primary: str, exported: set[str], owner_index: dict[str, str]
) -> bool:
    """Whether a ``compiler_generated`` entity with no direct export match

    should be dropped, given a real (non-empty) export set. Shared by
    ``source_link._route_declaration`` (first link) and
    ``source_link.relink_surface_exports`` (the parallel-baseline/Flow-2
    relink, once the export table becomes known) so the two apply the
    identical rule rather than the relink path silently keeping a candidate
    the first link would have dropped (Codex review, PR #930).

    Known, accepted residual (Codex review): ``bool(exported)`` is the only
    signal available here for "has the export table actually been
    resolved" -- ``link_source_abi``/``relink_surface_exports`` accept a
    bare ``Iterable[str]`` with no separate resolved/unresolved flag, so a
    *genuinely* zero-export dynamic library (an unusual but real shape --
    e.g. an executable with no public symbols, or a `.so` whose every
    export was stripped/LTO-eliminated) is indistinguishable from "the
    binary side hasn't been linked yet" and is treated the same way: every
    generated candidate is kept rather than dropped. Closing this needs a
    real tri-state export-resolution signal threaded through both
    functions' public signatures and every one of their callers -- a
    genuine API-shape change, not a follow-up to this predicate.
    """
    return (
        not primary
        and bool(exported)
        and not synthetic_key_owner_has_export(export_sym, owner_index)
    )


def drop_unmatched_generated_declarations(
    declarations: list[SourceEntity],
    mapping: dict[str, str],
    exported: set[str],
    owner_index: dict[str, str],
) -> list[SourceEntity]:
    """Remove a ``compiler_generated`` declaration still unmatched after
    every matching tier -- exact/ctor-fold, the ctor/dtor owner-index
    rescue, *and* ``source_link._demangled_rematch``'s second-tier
    substitution/ABI-tag-drift fallback -- has run.

    Deliberately run once, after all three tiers, rather than inline
    during routing: a ``compiler_generated`` entity previously never
    reached ``reachable_declarations`` at all when it missed the first,
    exact-match tier, so `_demangled_rematch` (which only rematches
    entities already in that list) could never rescue it the way it
    already rescues an ordinary declaration whose mangled spelling
    differs only by ABI-tag/substitution drift from its real export
    (Codex review, PR #930 -- e.g. castxml's `_ZN1AaSERKS_` vs. the export
    `_ZN1AaSERK1A`, equivalent once demangled). Pops a dropped entity's
    key out of *mapping* too, so a caller's `decls_without_symbol`
    computed from it doesn't still list the removed entity.
    """
    kept: list[SourceEntity] = []
    for entity in declarations:
        if entity.ownership.get("compiler_generated") != "true":
            kept.append(entity)
            continue
        key = entity.identity()
        primary = mapping.get(key, "") if key else ""
        export_sym = entity.mangled_name or entity.qualified_name
        if should_drop_generated_candidate(export_sym, primary, exported, owner_index):
            if key:
                mapping.pop(key, None)
            continue
        kept.append(entity)
    return kept


def rematch_declarations(
    declarations: list[SourceEntity],
    exported: set[str],
    export_index: dict[str, list[str]],
    exact_index: dict[str, str],
    owner_index: dict[str, str],
    match_export: Callable[[str, set[str], dict[str, list[str]], dict[str, str]], tuple[str, list[str]]],
) -> tuple[list[SourceEntity], dict[str, str], set[str], dict[str, str]]:
    """Re-derive decl -> export mapping for a relink. Every declaration is
    kept here, generated candidates included -- the caller must apply
    :func:`drop_unmatched_generated_declarations` itself, *after* its own
    demangled-identity second-tier rematch, not this function (Codex
    review, PR #930: dropping inline here, before that second tier runs,
    denied a generated candidate the identical ABI-tag/substitution-drift
    rescue an ordinary declaration already gets).

    Split out of ``source_link.relink_surface_exports`` (that file's own
    no-growth line budget) rather than duplicated.

    Returns ``(kept_declarations, mapping, matched_symbols, identity_to_qname)``.
    """
    kept: list[SourceEntity] = []
    mapping: dict[str, str] = {}
    matched: set[str] = set()
    identity_to_qname: dict[str, str] = {}
    for entity in declarations:
        export_sym = entity.mangled_name or entity.qualified_name
        primary, variants = match_export(export_sym, exported, export_index, exact_index)
        kept.append(entity)
        key = entity.identity()
        if not key:
            continue
        identity_to_qname[key] = entity.qualified_name or key
        mapping[key] = primary if primary else mapping.get(key, "")
        if primary:
            matched.update(variants)
    return kept, mapping, matched, identity_to_qname
