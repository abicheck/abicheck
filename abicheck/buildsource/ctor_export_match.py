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
"""

from __future__ import annotations

from ..diff_cxx_rules import itanium_scope_components
from ..dumper_castxml import (
    SYNTHETIC_CTOR_KEY_PREFIX,
    is_synthetic_ctor_key,
    is_synthetic_dtor_key,
)

_CTOR_MARKER = "{ctor}"
_DTOR_MARKER = "{dtor}"


def build_ctor_dtor_owner_index(exported: set[str]) -> dict[str, str]:
    """Owner scope (Itanium-demangled, ``"::"``-joined) -> ``"ctor"``/
    ``"dtor"``/``"both"``, for every export a ctor/dtor marker was found in.

    Built once per link, mirroring how :func:`~.source_link._build_export_index`
    already indexes ``exported`` up front rather than re-scanning it per entity.
    """
    index: dict[str, str] = {}
    for sym in exported:
        comps = itanium_scope_components(sym)
        if not comps or len(comps) < 2:
            continue
        marker = comps[-1]
        if marker == _CTOR_MARKER:
            kind = "ctor"
        elif marker == _DTOR_MARKER:
            kind = "dtor"
        else:
            continue
        owner = "::".join(comps[:-1])
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
