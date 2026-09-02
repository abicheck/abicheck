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

"""Typed ``ScopePath``/``EntityId`` construction for DWARF (ADR-063 Phase 2).

``dwarf_snapshot.py``'s DIE walk already decides, per DIE, whether a scope is
entered and under which bare name -- this module answers the strictly richer
question a flat ``"::"``-joined spelling throws away, the same job
``extract.headers.clang.scope``/``extract.headers.castxml.scope`` do for the
two header-AST backends: *which kind* of scope a DIE introduces, and what
``EntityId`` its own declaration resolves to. Reusing
``extract.headers.scope_segments``'s shared segment constructors, not
reinventing them, so all three producers agree on one mapping from "what the
parser said" to "which segment type" (that module's own docstring).

Two DWARF-specific gaps from the two header-AST backends, both accepted
limitations rather than silent bugs -- see each function's own docstring for
the "why":

* an anonymous namespace (``namespace { ... }``, no ``DW_AT_name``) gets no
  scope segment at all here, matching this walker's own pre-existing flat-
  scope behavior (its members were always treated as declared directly in
  the parent scope);
* a nested anonymous struct/union/enum never reaches an
  ``EntityId`` of its own either -- ``dwarf_snapshot.py``'s own
  ``_process_record_type``/``_process_enum`` already skip an unnamed
  record/enum DIE unless it is reached through a typedef (``typedef struct {
  ... } Point;``), which borrows the typedef's own name, so ``Anonymous``
  segments are never constructed by this module at all.

Leaf module: depends on ``model`` and ``extract.headers.scope_segments``
(allowed: ``extract -> model``, ADR-061), plus ``dwarf_utils`` for attribute
reads -- nothing above. Lives under ``extract/`` rather than at the package
root, per ADR-061 D9's task routing table, even though its one caller today
(``dwarf_snapshot.py``) is itself a legacy root module.
"""

from __future__ import annotations

from typing import Any

from ..dwarf_utils import attr_bool as _attr_bool, attr_str as _attr_str
from ..model.identity import (
    EntityId,
    ScopePath,
    ScopeSegment,
    entity_id_for_function,
    entity_id_for_variable,
)
from .headers.scope_segments import (
    namespace_segment as _namespace_segment,
    record_segment as _record_segment,
)

__all__ = [
    "function_entity_id",
    "namespace_scope_segment",
    "record_scope_segment",
    "variable_entity_id",
]


def namespace_scope_segment(die: Any, die_name: str) -> ScopeSegment:
    """The scope segment a named ``DW_TAG_namespace`` DIE contributes.

    DWARF5's ``DW_AT_export_symbols`` is the one signal distinguishing an
    inline namespace from an ordinary one -- mirroring
    ``extract.headers.clang.scope``'s own ``isInline`` read for a
    ``NamespaceDecl``. An anonymous namespace (no *die_name*) is the
    caller's concern, not this function's: see this module's own docstring
    for why DWARF's walker gives one no segment at all.
    """
    return _namespace_segment(
        die_name, is_inline=_attr_bool(die, "DW_AT_export_symbols")
    )


def record_scope_segment(die_name: str, access: str) -> ScopeSegment:
    """The scope segment a named record (struct/class/union) DIE contributes.

    *access* is this record's own access specifier within ITS parent (not
    its members' default access, a different question
    ``dwarf_records.default_member_access_for_tag`` answers) --
    non-identity payload only (``Record.access`` is ``compare=False``), so a
    caller that could not determine a real ``DW_AT_accessibility`` safely
    passes the ``NO_ACCESS`` default rather than needing the enclosing DIE's
    own tag.
    """
    return _record_segment(die_name, access=access)


def function_entity_id(
    scope_path: ScopePath,
    name: str,
    die: Any,
    mangled: str,
    is_extern_c: bool,
    param_types: tuple[str, ...],
) -> EntityId:
    """``EntityId`` for a ``DW_TAG_subprogram`` DIE's declaration.

    ``dwarf_snapshot.py``'s own ``mangled`` is already ``linkage_name or
    name`` (a fallback bare spelling when no real ``DW_AT_linkage_name``
    exists) -- offered to ``entity_id_for_function`` as a genuine mangling
    whenever a real ``DW_AT_linkage_name``/``DW_AT_MIPS_linkage_name`` was
    present, unconditionally on *is_extern_c*. Deliberately NOT the two
    header-AST backends' own ``raw_mangled is not None and not is_extern_c``
    gate: their ``is_extern_c`` is a real language-linkage signal read off
    the AST node itself, so it is trustworthy enough to override a
    genuinely-present mangled spelling; DWARF's own *is_extern_c* here is
    only ``not mangled.startswith("_Z")`` -- a real, explicitly-linked
    non-Itanium name (e.g. ``int f(int) asm("custom_name")`` on an ordinary
    C++ function) would satisfy that heuristic while still being a
    genuinely distinct, real linkage name, and gating on it would wrongly
    collapse the function onto the scope-free ``extern_c`` tag instead of
    identifying it by its own real (if unusually-spelled) linkage name --
    exactly the cross-backend divergence a header-AST dump of the identical
    declaration would not produce (Codex review, PR #1015). A genuinely
    extern-"C" function has no distinct linkage name to mangle at all, so
    GCC/Clang emit no ``DW_AT_linkage_name`` for it -- *has_linkage_name*
    being ``False`` is already the correct signal for that case, without
    needing the unreliable prefix check at all; ``entity_id_for_function``'s
    own contract makes this safe regardless (*mangled_name*, when present,
    wins outright over *is_extern_c*). *is_extern_c* is still passed
    through for the one case it remains meaningful: no real linkage name at
    all, where the constructor falls back to its own extern-"C" tag.

    DWARF does not carry a method's own cv-qualification, ref-qualifier, or
    variadic-ness here (unlike the two header-AST backends' own AST-node
    reads) -- a known, narrower-than-header-AST gap that matters only for
    the rare non-extern-"C" function with no real ``DW_AT_linkage_name`` at
    all (the "sig" fallback branch); every ordinary mangled C++ overload is
    unaffected, since the mangled branch ignores these entirely.
    """
    has_linkage_name = bool(
        _attr_str(die, "DW_AT_linkage_name")
        or _attr_str(die, "DW_AT_MIPS_linkage_name")
    )
    return entity_id_for_function(
        scope_path,
        name,
        mangled_name=(mangled if has_linkage_name else None),
        is_extern_c=is_extern_c,
        param_types=param_types,
        is_const=False,
        is_volatile=False,
        is_variadic=None,
    )


def variable_entity_id(
    scope_path: ScopePath, name: str, mangled: str, has_linkage_name: bool
) -> EntityId:
    """``EntityId`` for a ``DW_TAG_variable`` DIE's declaration.

    Same "a real linkage name always wins, regardless of its own spelling"
    rule :func:`function_entity_id` uses (see that function's own docstring
    for the full reasoning) -- *has_linkage_name* alone gates the mangled
    branch; a variable with no real ``DW_AT_linkage_name`` at all falls back
    to the extern-"C"-like branch, derived from *mangled*'s own spelling
    since ``entity_id_for_variable`` has no dedicated *is_extern_c* concept
    of its own (unlike ``Function.is_extern_c``).
    """
    is_extern_c_like = not mangled.startswith("_Z")
    return entity_id_for_variable(
        scope_path,
        name,
        mangled_name=(mangled if has_linkage_name else None),
        is_extern_c=is_extern_c_like,
    )
