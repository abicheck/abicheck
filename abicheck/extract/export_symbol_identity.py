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

"""``EntityId``-construction helper shared by every export-table-only
producer -- ELF (``dumper_elf_fallback.py``), Mach-O and PE
(``dumper.py``'s own header-less ``_dump_macho``/``_dump_pe`` branches)
(ADR-063 Phase 2).

Each of these producers only ever constructs a ``Function``/``Variable``
FROM an actual, observed dynamic-export-table entry -- unlike a header-AST/
DWARF producer's own ``mangled = linkage_name or name`` fallback (a real
non-distinguishing spelling used when no genuine linkage evidence exists at
all), there is no "no real distinguishing spelling" case here to guard
against: the raw exported string is always real, observed evidence.

The one open question each of these producers shares is whether that raw
string is *genuinely mangled* (``entity_id``'s "mangled" branch, scope-free,
unique by construction) or a plain, unmangled export (the "extern_c" branch,
matching what a header-AST/DWARF producer would resolve the identical
genuinely-extern-"C" declaration to). Export-table-only evidence carries no
signal beyond the string's own shape to answer that -- a real, explicitly-
relabeled export (e.g. GCC/Clang's ``asm("custom_name")``, or an MSVC
``/EXPORT:custom_name=realname`` alias) is structurally indistinguishable
from a genuine plain-C/extern-"C" export; both are just an identifier with
no mangling prefix. This is a genuinely irreducible ambiguity (confirmed
against real DWARF evidence too -- see ``extract/dwarf_scope.
function_entity_id``'s own docstring for the identical conclusion reached
from a *different* evidence source), not a heuristic waiting on a smarter
check: every producer here defaults to the structural-mangling-prefix
gate, matching the two header-AST backends' own convention for the
overwhelmingly common (non-relabeled) case, with the relabeled case an
accepted, documented residual gap.

Also builds the full ``Function``/``Variable`` objects for the common
shape all three producers share (name/mangled/return_type/visibility/
is_extern_c/entity_id) -- one construction site instead of three
independently-drifting copies.

Leaf module: depends only on ``model``/``model.identity`` (allowed:
``extract -> model``, ADR-061).
"""

from __future__ import annotations

import re

from ..model import Function, Variable, Visibility
from ..model.identity import entity_id_for_function, entity_id_for_variable

__all__ = [
    "itanium_export_function",
    "itanium_export_mangled_name",
    "itanium_export_variable",
    "msvc_export_function",
    "msvc_export_mangled_name",
]

# 32-bit x86 PE/COFF's C-calling-convention export decoration -- distinct
# from (and orthogonal to) C++ name mangling: __stdcall appends "@N" (N =
# argument-list size in bytes) to a leading-underscore-prefixed name,
# __fastcall does the same but with a leading "@" instead of "_", and plain
# __cdecl gets only the leading underscore. This is *exclusive* to 32-bit
# x86 (`IMAGE_FILE_MACHINE_I386`) -- x64/ARM/ARM64 PE never decorate a C
# export at all, so a leading underscore there is part of the real,
# undecorated source name (Codex review, PR #1015: an x64 `_secret` export
# is a real, distinct symbol, not a decorated `secret`) and stripping it
# unconditionally would misclassify it and collide it with an unrelated
# real `secret` export. A raw export string here is real, observed
# evidence -- like this module's other builders -- but unlike a mangled
# name, it is not the identity itself on 32-bit x86: the header-AST
# producer's own EntityId for the identical declaration carries the
# undecorated name as its leaf (its own AST read never sees the linker's
# decoration), so an un-decorated leaf is required there for the two
# evidence modes to agree, not merely cosmetic.
_PE_FASTCALL_DECORATION_RE = re.compile(r"^@(.+)@\d+$")
_PE_STDCALL_DECORATION_RE = re.compile(r"^_(.+)@\d+$")


def _strip_pe_c_decoration(sym: str) -> str:
    """Undo 32-bit x86 PE/COFF's __stdcall/__fastcall/__cdecl export
    decoration -- see this module's own comment above for the "why". Only
    meaningful for an already-extern-"C"-classified export (a mangled C++
    name is never decorated this way); leaves anything not matching one of
    the three shapes unchanged.
    """
    match = _PE_FASTCALL_DECORATION_RE.match(sym)
    if match:
        return match.group(1)
    match = _PE_STDCALL_DECORATION_RE.match(sym)
    if match:
        return match.group(1)
    if sym.startswith("_"):
        return sym[1:]
    return sym


def itanium_export_mangled_name(sym: str) -> str | None:
    """The genuine ``mangled_name`` to offer ``entity_id_for_function``/
    ``entity_id_for_variable`` for a raw ELF/Mach-O dynamic-export-table
    entry, keyed on the Itanium ``_Z`` mangling prefix -- see this module's
    own docstring for the full "why". ``None`` (take the extern-"C"
    branch) for anything not ``_Z``-prefixed, including a genuine plain-C
    export and a relabeled one alike.
    """
    return sym if sym.startswith("_Z") else None


def msvc_export_mangled_name(sym: str) -> str | None:
    """The PE/COFF counterpart of :func:`itanium_export_mangled_name`.

    A PE/COFF export's mangling convention depends on which toolchain
    produced the DLL, not just the container format: MSVC uses its own
    ``?``-prefixed scheme, but a MinGW/GCC-built DLL's C++ exports are
    Itanium-mangled (``_Z...``), same as ELF/Mach-O -- both are real,
    observed PE export tables this codebase supports (see the MinGW PE
    lane referenced by ``AGENTS.md``). Recognize either prefix so a
    MinGW DLL's headerless dump agrees with its header-backed dump's own
    mangled-branch ``EntityId`` instead of silently falling back to the
    extern-"C" branch for every MinGW C++ export.
    """
    if sym.startswith("?") or sym.startswith("_Z"):
        return sym
    return None


def itanium_export_function(name: str) -> Function:
    """An export-table-only ``Function`` for an ELF/Mach-O *name* already
    normalized to its bare exported spelling (Mach-O's own leading
    underscore stripped by the caller) -- shared by
    ``dumper_elf_fallback.py`` and ``dumper.py``'s Mach-O export-only path.
    """
    is_extern_c = not name.startswith("_Z")
    return Function(
        name=name,
        mangled=name,
        return_type="?",
        # ELF_ONLY: marks symbols as export-table-only (no header
        # confirmation), so the checker can distinguish a binary-only
        # removal as FUNC_REMOVED_ELF_ONLY.
        visibility=Visibility.ELF_ONLY,
        is_extern_c=is_extern_c,
        entity_id=entity_id_for_function(
            (),
            name,
            mangled_name=itanium_export_mangled_name(name),
            is_extern_c=is_extern_c,
        ),
    )


def itanium_export_variable(name: str) -> Variable:
    """The :func:`itanium_export_function` counterpart for a variable."""
    is_extern_c = not name.startswith("_Z")
    return Variable(
        name=name,
        mangled=name,
        type="?",
        visibility=Visibility.ELF_ONLY,
        entity_id=entity_id_for_variable(
            (),
            name,
            mangled_name=itanium_export_mangled_name(name),
            is_extern_c=is_extern_c,
        ),
    )


def msvc_export_function(sym: str, *, is_x86_32: bool = False) -> Function:
    """The PE/COFF counterpart of :func:`itanium_export_function`.

    Handles both PE mangling conventions in use -- see
    :func:`msvc_export_mangled_name` for why a bare ``?``-prefix check
    alone misses MinGW/GCC's Itanium-mangled C++ exports -- and, for the
    extern-"C" branch only, undoes 32-bit x86's __stdcall/__fastcall/
    __cdecl export decoration before building the identity (see
    :func:`_strip_pe_c_decoration`'s own docstring for why the *identity*,
    not ``Function.name``/``mangled``, is what must be undecorated here).

    *is_x86_32* -- the caller's own ``PeMetadata.machine ==
    "IMAGE_FILE_MACHINE_I386"`` read -- gates the decoration strip: it is a
    32-bit-x86-only linker convention, so it must default to *not*
    stripping (every other PE machine type keeps a leading underscore as
    real, undecorated source-name evidence).
    """
    is_extern_c = not (sym.startswith("?") or sym.startswith("_Z"))
    leaf_name = _strip_pe_c_decoration(sym) if (is_extern_c and is_x86_32) else sym
    return Function(
        name=sym,
        mangled=sym,
        return_type="?",
        visibility=Visibility.ELF_ONLY,
        is_extern_c=is_extern_c,
        entity_id=entity_id_for_function(
            (), leaf_name, mangled_name=msvc_export_mangled_name(sym), is_extern_c=is_extern_c
        ),
    )
