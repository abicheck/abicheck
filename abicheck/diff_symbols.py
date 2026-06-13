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

"""Symbol-level ABI diff detectors (functions, variables, parameters)."""
from __future__ import annotations

import bisect
import logging
import re
from functools import lru_cache
from typing import Any

from .binary_fingerprint import (
    _MIN_SYMBOL_SIZE,
    FunctionFingerprint,
    match_renamed_functions,
)
from .checker_policy import ChangeKind
from .checker_types import Change
from .detector_registry import registry
from .diff_cxx_rules import (
    old_virtual_signatures,
    owner_class_of,
    virtual_method_addition,
)
from .diff_helpers import bool_transition, diff_by_key
from .elf_metadata import SymbolType
from .elf_symbol_filter import (
    FUNCTION_SYMBOL_TYPES,
    exported_symbol_names,
    is_abi_relevant_elf_symbol,
)
from .model import (
    AbiSnapshot,
    Function,
    Param,
    Variable,
    Visibility,
    canonicalize_type_name,
    cv_qualifiers_only_differ,
    is_abi_surface_type_name,
    is_cxx_runtime_library,
    stdlib_namespaces_excluded,
)

_log = logging.getLogger(__name__)

# Visibility levels that constitute the public ABI surface.
_PUBLIC_VIS = (Visibility.PUBLIC, Visibility.ELF_ONLY)

# Itanium RTTI artifact prefixes (typeinfo, typeinfo-name, vtable, VTT) followed
# immediately by ``Z`` — the Itanium "local-name" production ``Z <encoding> E``.
# An RTTI symbol of this shape belongs to a *function-local* type (a lambda
# closure, or any class/struct declared inside a function body). Such a type can
# never be named in a public header, so the presence/absence of its typeinfo is
# build-dependent churn, not a public-ABI break. Filtering it here mirrors how
# anonymous/lambda *types* are excluded from type diffing (model.is_non_abi_surface_type).
_LOCAL_RTTI_PREFIXES = ("_ZTIZ", "_ZTSZ", "_ZTVZ", "_ZTTZ")


# Sentinel the dumper writes for the type/return type of a symbol whose
# signature is unknown — e.g. an ELF export from a stripped binary with no DWARF
# or header info. Diffing a known type against "?" yields a phantom change
# ("void → ?"), so type-bearing comparisons must treat "?" as "no evidence".
_UNKNOWN_TYPE = "?"


def _type_unknown(type_name: str | None) -> bool:
    return type_name is None or type_name.strip() == _UNKNOWN_TYPE


def _is_stripped_symbols_only(snap: AbiSnapshot) -> bool:
    """True when *snap* is a stripped, symbols-only dump: it exports symbols but
    carries no type-level evidence (no records/enums/typedefs, no DWARF content)
    and was flagged ``elf_only_mode`` by the dumper.

    Used to gate *parameter* comparison (RD2-5; Codex reviews on PR #275). The
    bare ``"?"`` sentinel is **not** a reliable per-function signal — castxml and
    dwarf_snapshot also emit ``"?"`` for an individually unresolved return/param
    while resolving the rest — so an empty parameter list only means "unknown
    params" when the whole snapshot is a symbols-only stub. In a real
    DWARF/header snapshot an empty list means "takes no arguments", and changes
    like ``f(void)`` → ``f(int)`` must still be diffed.
    """
    if not getattr(snap, "elf_only_mode", False):
        return False
    if snap.types or snap.enums or snap.typedefs:
        return False
    dwarf = getattr(snap, "dwarf", None)
    if dwarf is not None and (dwarf.structs or dwarf.enums):
        return False
    return bool(snap.functions or snap.variables)


def _is_local_type_rtti(mangled: str) -> bool:
    """True for typeinfo/vtable symbols of a function-local type (e.g. a lambda).

    Regression: RD2-4 (validation) — protobuf patch releases churn
    ``_ZTIZN…EUl…E_`` / ``_ZTSZN…`` typeinfo symbols for anonymous lambdas nested
    in ``Printer::WithDefs/WithVars``; they were scored as public ``var_removed``
    and drove a false ``BREAKING`` verdict on an ABI-compatible bump.
    """
    return mangled.startswith(_LOCAL_RTTI_PREFIXES)


def _should_filter_transitive_runtime_symbols(snap: AbiSnapshot) -> bool:
    """Return True when transitive C++ runtime symbols should be filtered.

    Returns False when ``snap.library`` or the ELF SONAME identifies *snap* as
    the C++ runtime itself, where runtime-owned symbols are the inspected ABI.
    """
    elf = getattr(snap, "elf", None)
    return not (
        is_cxx_runtime_library(snap.library)
        or is_cxx_runtime_library(getattr(elf, "soname", ""))
    )


def _public_functions(snap: AbiSnapshot) -> dict[str, Function]:
    """Return public/ELF-only functions from *snap*.

    When ELF dynamic-symbol evidence is available, narrow the DWARF-derived
    public set to names that are actually exported (or explicitly ``= delete``,
    so an API becoming deleted stays observable). This keeps transitive
    runtime/stdlib subprograms that slipped into the DWARF DIEs out of the diff.

    The narrowing only happens when exports are present: a snapshot with no ELF
    symbol table (``elf`` absent/empty) keeps the full DWARF set untouched.

    Caveat: this trusts the ELF symbol table to be reasonably complete. A
    *partially* captured table (e.g. only a stripped ``.symtab`` subset) could in
    theory hide a genuine removal — but DWARF-primary snapshots carry the full
    ``.dynsym``, so in practice the export set is authoritative here.
    """
    filter_transitive_runtime_symbols = _should_filter_transitive_runtime_symbols(snap)
    funcs = {
        k: v for k, v in snap.function_map.items()
        if (
            v.visibility in _PUBLIC_VIS
            and (
                v.visibility != Visibility.ELF_ONLY
                or is_abi_relevant_elf_symbol(
                    k,
                    filter_transitive_runtime_symbols=filter_transitive_runtime_symbols,
                )
            )
        )
    }
    elf = getattr(snap, "elf", None)
    if elf is None or not getattr(elf, "symbols", None):
        return funcs
    exported = exported_symbol_names(
        elf,
        FUNCTION_SYMBOL_TYPES,
        abi_relevant_only=True,
        filter_transitive_runtime_symbols=filter_transitive_runtime_symbols,
    )
    return {
        k: v for k, v in funcs.items()
        if k in exported or (v.is_deleted and not v.deleted_from_dwarf)
    }


def _public_variables(snap: AbiSnapshot) -> dict[str, Variable]:
    """Return public/ELF-only variables from *snap*.

    Excludes RTTI/vtable symbols of function-local types (lambda closures and
    other in-function types): they are not nameable public ABI and only churn
    across builds (RD2-4).
    """
    filter_transitive_runtime_symbols = _should_filter_transitive_runtime_symbols(snap)
    return {
        k: v for k, v in snap.variable_map.items()
        if (
            v.visibility in _PUBLIC_VIS
            and (
                v.visibility != Visibility.ELF_ONLY
                or is_abi_relevant_elf_symbol(
                    k,
                    filter_transitive_runtime_symbols=filter_transitive_runtime_symbols,
                )
            )
            and not _is_local_type_rtti(k)
        )
    }



def _format_params(params: list[Param]) -> str:
    """Format a parameter list as a human-readable string.

    ``Param.type`` already carries pointer/reference sigils (e.g. ``int *``,
    ``Foo &``), so we use it directly — appending ``_KIND_SUFFIX`` would
    duplicate them.
    """
    parts = [p.type for p in params]
    return ", ".join(parts) if parts else "(none)"


def _check_removed_function(
    mangled: str, f_old: Function, new_all: dict[str, Function],
    elf_only_mode: bool,
) -> Change:
    """Create a Change for a function that was removed or hidden."""
    f_hidden = new_all.get(mangled)
    if (
        f_hidden is not None
        and f_hidden.visibility == Visibility.HIDDEN
        and not (elf_only_mode and f_old.visibility == Visibility.ELF_ONLY)
    ):
        return Change(
            kind=ChangeKind.FUNC_VISIBILITY_CHANGED,
            symbol=mangled,
            description=f"Function visibility changed to hidden: {f_old.name}",
            old_value=f_old.visibility.value,
            new_value=f_hidden.visibility.value,
        )
    removed_kind = (
        ChangeKind.FUNC_REMOVED_ELF_ONLY
        if (elf_only_mode and f_old.visibility == Visibility.ELF_ONLY)
        else ChangeKind.FUNC_REMOVED
    )
    return Change(
        kind=removed_kind,
        symbol=mangled,
        description=f"{f_old.visibility.value.capitalize()} function removed: {f_old.name}",
        old_value=f_old.name,
    )


# Integer spellings whose width is *fixed* regardless of data model, mapped to
# (bit-width, is_signed). A name-only change between two spellings with the same
# representation is not a binary ABI break — storage and calling convention are
# identical.
_FIXED_SCALAR_REPR: dict[str, tuple[object, bool]] = {
    "int": (32, True), "signed int": (32, True), "signed": (32, True),
    "int32_t": (32, True),
    "unsigned int": (32, False), "unsigned": (32, False), "uint32_t": (32, False),
    "long long": (64, True), "long long int": (64, True),
    "signed long long": (64, True), "int64_t": (64, True),
    "unsigned long long": (64, False), "long long unsigned int": (64, False),
    "uint64_t": (64, False),
    "short": (16, True), "short int": (16, True), "int16_t": (16, True),
    "unsigned short": (16, False), "short unsigned int": (16, False),
    "uint16_t": (16, False),
    "signed char": (8, True), "int8_t": (8, True),
    "unsigned char": (8, False), "uint8_t": (8, False),
}
# Data-model-dependent spellings. On LP64 (Linux/macOS 64-bit) the ``long``
# family and the pointer-width types are all 64-bit; on ILP32 they are all
# 32-bit; on LLP64 (Windows) ``long`` is 32-bit while the pointer-width types
# stay 64-bit. The snapshot does not record target bitness, so for non-LLP64
# targets we cannot tell LP64 from ILP32 — but the ``long`` family and the
# pointer-width family *co-vary* there (both equal the pointer size), so they
# are equivalent to each other yet NOT to a fixed-width spelling (e.g. ``long``
# vs ``long long`` is a real width change on ILP32 and must not be suppressed).
# A shared ``"long"`` width sentinel captures exactly that: it is equal to
# itself (same sign) but never to a concrete bit-width, so ``size_t`` ↔
# ``unsigned long`` is suppressed on non-LLP64 while ``int`` ↔ ``long`` and
# ``long`` ↔ ``long long`` stay reportable everywhere.
_LONG_SIGNED_SPELLINGS = frozenset({"long", "long int", "signed long"})
_LONG_UNSIGNED_SPELLINGS = frozenset({"unsigned long", "long unsigned int"})
_PTR_SIGNED_SPELLINGS = frozenset({"ssize_t", "ptrdiff_t", "intptr_t"})
_PTR_UNSIGNED_SPELLINGS = frozenset({"size_t", "uintptr_t"})

# The words that make up a C integer built-in's declaration specifiers. A
# spelling composed *only* of these can be reordered freely by the language
# (``unsigned long int`` ≡ ``long unsigned int`` ≡ ``unsigned long``), and
# different toolchains/headers emit different orderings, so they are normalized
# to one canonical form before lookup. Typedefs (``size_t``) and fixed-width
# names (``uint32_t``) contain other words and pass through unchanged.
_INT_SPECIFIER_WORDS = frozenset({"signed", "unsigned", "short", "long", "int", "char"})


def _canonical_int_spelling(t: str) -> str:
    """Canonicalize a bare integer built-in spelling (specifier order and the
    redundant trailing ``int`` are not significant), or return ``t`` unchanged
    when it is not a pure specifier spelling (typedef, fixed-width, …)."""
    words = t.split()
    if not words or any(w not in _INT_SPECIFIER_WORDS for w in words):
        return t
    unsigned = "unsigned" in words
    if "char" in words:
        if unsigned:
            return "unsigned char"
        if "signed" in words:
            return "signed char"
        return t  # bare ``char`` — sign is implementation-defined, leave as-is
    if "short" in words:
        return "unsigned short" if unsigned else "short"
    longs = words.count("long")
    if longs >= 2:
        return "unsigned long long" if unsigned else "long long"
    if longs == 1:
        return "unsigned long" if unsigned else "long"
    return "unsigned int" if unsigned else "int"


def _scalar_repr(type_name: str, is_llp64: bool) -> tuple[object, bool] | None:
    """Map a *bare* integer spelling to (width, is_signed), or None.

    Width is an ``int`` (fixed bit count) or one of two abstract sentinels for
    data-model-dependent spellings whose absolute width the snapshot does not
    record:

    * ``"ptr"`` — pointer-width types (``size_t``, ``ptrdiff_t``, …). Their
      absolute width is unknown (64-bit on LP64/LLP64, 32-bit on ILP32 and
      32-bit Windows), so they must never be equated with a *fixed* width such
      as ``uint64_t``. Used on every platform.
    * ``"long"`` — the ``long`` family on LLP64 only, where ``long`` is 32-bit
      and thus a distinct representation from the 64-bit pointer-width types.

    On non-LLP64 the ``long`` family co-varies with the pointer-width types
    (``size_t`` *is* ``unsigned long`` there), so it shares the ``"ptr"``
    sentinel — making ``size_t`` ↔ ``unsigned long`` a non-break while keeping
    ``long`` ↔ ``long long`` (sentinel vs fixed 64) reportable. Neither
    sentinel ever equals a fixed width, so a distinct built-in change such as
    ``int`` vs ``long`` is reported even where the widths coincide. Returns
    None for anything that is not a plain integer scalar (pointers, references,
    templates, cv-qualified or unknown spellings).
    """
    t = " ".join(type_name.split())
    if not t or any(c in t for c in "*&<>([,") or "const" in t or "volatile" in t:
        return None
    # Fold legal specifier-order variants (``unsigned long int`` -> ``unsigned
    # long``) so a toolchain's spelling choice is not mistaken for an ABI change.
    t = _canonical_int_spelling(t)
    fixed = _FIXED_SCALAR_REPR.get(t)
    if fixed is not None:
        return fixed
    # The ``long`` family is its own distinct built-in. On LLP64 it is 32-bit
    # and must stay distinct from both fixed widths and the 64-bit pointer-width
    # types, so it gets its own ``"long"`` sentinel. Elsewhere it co-varies with
    # the pointer-width types and shares the ``"ptr"`` sentinel.
    if t in _LONG_SIGNED_SPELLINGS:
        return ("long", True) if is_llp64 else ("ptr", True)
    if t in _LONG_UNSIGNED_SPELLINGS:
        return ("long", False) if is_llp64 else ("ptr", False)
    # Pointer-width typedefs have an unknown absolute width on every platform
    # (64-bit on LP64/LLP64, 32-bit on ILP32 and 32-bit Windows), so they map to
    # the ``"ptr"`` sentinel and are never equated with a fixed width such as
    # ``uint64_t``.
    if t in _PTR_SIGNED_SPELLINGS:
        return ("ptr", True)
    if t in _PTR_UNSIGNED_SPELLINGS:
        return ("ptr", False)
    return None


def _abi_equivalent_scalar(old_type: str, new_type: str, is_llp64: bool) -> bool:
    """Whether two integer spellings have identical binary representation.

    True only when both resolve to the same width *and* signedness on the
    target data model — i.e. the change is a spelling/typedef difference, not a
    binary ABI break (e.g. ``size_t`` ↔ ``unsigned long``). A signedness
    difference (``long`` ↔ ``unsigned long``) is not equivalent, and a
    data-model-dependent spelling is never equated with a fixed width
    (``long`` ↔ ``long long`` stays a reportable change, since it is a real
    width change on ILP32 and the snapshot does not record target bitness).
    """
    old_r = _scalar_repr(old_type, is_llp64)
    return old_r is not None and old_r == _scalar_repr(new_type, is_llp64)


def _check_return_type_change(
    mangled: str, f_old: Function, f_new: Function, *, is_llp64: bool = False,
) -> list[Change]:
    """Emit a change if the return type was modified."""
    # RD2-5: a stripped side reports return_type "?"; that is unknown, not a change.
    if _type_unknown(f_old.return_type) or _type_unknown(f_new.return_type):
        return []
    if canonicalize_type_name(f_old.return_type) == canonicalize_type_name(f_new.return_type):
        return []
    # A pointee/by-value const-or-volatile qualification change (e.g.
    # ``char *`` -> ``const char *``) does not change the return register or
    # calling convention; it is a source/API-signature difference, not a
    # binary ABI break (ISSUE-29/52: libuv/Wayland const-pointer churn).
    if cv_qualifiers_only_differ(f_old.return_type, f_new.return_type):
        return []
    # A name-only change between ABI-equivalent integer spellings (e.g.
    # long -> long long, size_t -> unsigned long on LP64) is not a binary ABI
    # break: same width, signedness, and calling convention.
    if _abi_equivalent_scalar(f_old.return_type, f_new.return_type, is_llp64):
        return []
    return [Change(
        kind=ChangeKind.FUNC_RETURN_CHANGED,
        symbol=mangled,
        description=f"Return type changed: {f_old.name}",
        old_value=f_old.return_type,
        new_value=f_new.return_type,
    )]


def _params_differ(p_old: Param, p_new: Param, is_llp64: bool) -> bool:
    """Whether two positionally-matched parameters differ in an ABI-relevant way."""
    if _type_unknown(p_old.type) or _type_unknown(p_new.type):
        return False  # diffing a known type against unknown is meaningless
    if p_old.kind != p_new.kind:
        return True
    if canonicalize_type_name(p_old.type) == canonicalize_type_name(p_new.type):
        return False
    # A pointee/by-value const-or-volatile qualification change (e.g.
    # ``wl_display *`` -> ``const wl_display *``) leaves the parameter's
    # calling convention and binary layout identical — it is source/API churn,
    # not a binary ABI break (ISSUE-29/52).
    if cv_qualifiers_only_differ(p_old.type, p_new.type):
        return False
    # Same kind, different spelling: not a change if the integer types are
    # ABI-equivalent (long -> long long, size_t -> unsigned long on LP64).
    return not _abi_equivalent_scalar(p_old.type, p_new.type, is_llp64)


def _check_params_change(
    mangled: str, f_old: Function, f_new: Function, *,
    params_unconfirmed: bool = False, is_llp64: bool = False,
) -> list[Change]:
    """Emit a change if the parameter list was modified."""
    # RD2-5: suppress only when one side is a stripped symbols-only stub (its
    # empty param list is "unknown", not "zero args"). Otherwise compare
    # position-by-position, ignoring only the individual parameters whose type is
    # the unresolved "?" sentinel — diffing a known type against unknown is
    # meaningless, but an unrelated unknown must not mask a real change on a
    # fully-known parameter (e.g. f(?, int) -> f(?, long)). Parameter *count*
    # changes are always real in a resolved snapshot (Codex reviews, PR #275).
    if params_unconfirmed:
        return []
    changed: bool
    if len(f_old.params) != len(f_new.params):
        changed = True
    else:
        changed = any(
            _params_differ(p_old, p_new, is_llp64)
            for p_old, p_new in zip(f_old.params, f_new.params)
        )
    if not changed:
        return []
    return [Change(
        kind=ChangeKind.FUNC_PARAMS_CHANGED,
        symbol=mangled,
        description=f"Parameters changed: {f_old.name}",
        old_value=_format_params(f_old.params),
        new_value=_format_params(f_new.params),
    )]


def _check_ref_qualifier_change(mangled: str, f_old: Function, f_new: Function) -> list[Change]:
    """Emit a change if the ref-qualifier (&/&&) was modified."""
    old_rq = f_old.ref_qualifier or ""
    new_rq = f_new.ref_qualifier or ""
    if old_rq == new_rq:
        return []
    return [Change(
        kind=ChangeKind.FUNC_REF_QUAL_CHANGED,
        symbol=mangled,
        description=f"Ref-qualifier changed: {f_old.name} ({old_rq!r} → {new_rq!r})",
        old_value=old_rq or "(none)",
        new_value=new_rq or "(none)",
    )]


def _check_linkage_change(mangled: str, f_old: Function, f_new: Function) -> list[Change]:
    """Emit a change if the language linkage (extern \"C\" ↔ C++) was modified."""
    if f_old.is_extern_c == f_new.is_extern_c:
        return []
    old_linkage = 'extern "C"' if f_old.is_extern_c else "C++"
    new_linkage = 'extern "C"' if f_new.is_extern_c else "C++"
    return [Change(
        kind=ChangeKind.FUNC_LANGUAGE_LINKAGE_CHANGED,
        symbol=mangled,
        description=f"Language linkage changed: {f_old.name} ({old_linkage} → {new_linkage})",
        old_value=old_linkage,
        new_value=new_linkage,
    )]


def _check_noexcept_change(mangled: str, f_old: Function, f_new: Function) -> list[Change]:
    """Emit a change if the noexcept specifier was added or removed."""
    return bool_transition(
        f_old.is_noexcept, f_new.is_noexcept, mangled,
        added=(ChangeKind.FUNC_NOEXCEPT_ADDED, f"noexcept specifier added: {f_old.name}"),
        removed=(ChangeKind.FUNC_NOEXCEPT_REMOVED, f"noexcept specifier removed: {f_old.name}"),
    )


def _check_virtual_change(mangled: str, f_old: Function, f_new: Function) -> list[Change]:
    """Emit a change if the virtual specifier was added or removed."""
    return bool_transition(
        f_old.is_virtual, f_new.is_virtual, mangled,
        added=(ChangeKind.FUNC_VIRTUAL_ADDED, f"Function became virtual: {f_old.name}"),
        removed=(ChangeKind.FUNC_VIRTUAL_REMOVED, f"Function is no longer virtual: {f_old.name}"),
    )


def _check_hidden_friend_change(mangled: str, f_old: Function, f_new: Function) -> list[Change]:
    """Emit a change if the hidden-friend status transitioned.

    Hidden-friend transitions: an in-class ``friend`` declaration was
    added or removed across versions. Tri-state — skip when either
    side's snapshot did not record the flag (e.g. DWARF-only path or
    an older snapshot). The matched-mangled iteration here handles
    the case where the friend has an out-of-line definition (i.e.
    a real symbol). Inline-only hidden friends never appear here
    because they have no symbol on either side; those transitions
    are picked up by ``_check_hidden_friend_additions_removals``
    below by matching on (name, params) rather than mangled name.
    """
    return bool_transition(
        f_old.is_hidden_friend, f_new.is_hidden_friend, mangled,
        skip_none=True,
        added=(ChangeKind.HIDDEN_FRIEND_ADDED, f"Function became an in-class friend declaration: {f_old.name}"),
        added_values=("non-friend", "hidden friend"),
        removed=(ChangeKind.HIDDEN_FRIEND_REMOVED, f"Function is no longer an in-class friend declaration: {f_old.name}"),
        removed_values=("hidden friend", "non-friend"),
    )


def _check_explicit_change(mangled: str, f_old: Function, f_new: Function) -> list[Change]:
    """Emit a change if the explicit specifier was added or removed.

    Tri-state: only fire when BOTH sides record explicit data. None means
    the dumper/loader couldn't determine it — typically an older snapshot
    that predates the field, or a Function/Destructor where ``explicit`` is
    N/A. Skipping in that case avoids false API_BREAK findings produced
    purely by snapshot schema evolution.
    """
    return bool_transition(
        f_old.is_explicit, f_new.is_explicit, mangled,
        skip_none=True,
        added=(ChangeKind.CTOR_EXPLICIT_ADDED, f"Constructor/conversion gained `explicit` specifier: {f_old.name}"),
        added_values=("implicit", "explicit"),
        removed=(ChangeKind.CTOR_EXPLICIT_REMOVED, f"Constructor/conversion lost `explicit` specifier: {f_old.name}"),
        removed_values=("explicit", "implicit"),
    )


def _check_function_signature(
    mangled: str, f_old: Function, f_new: Function, *,
    params_unconfirmed: bool = False, is_llp64: bool = False,
) -> list[Change]:
    """Compare signatures and qualifiers of two matched functions."""
    changes: list[Change] = []
    changes.extend(_check_return_type_change(mangled, f_old, f_new, is_llp64=is_llp64))
    changes.extend(_check_params_change(
        mangled, f_old, f_new, params_unconfirmed=params_unconfirmed, is_llp64=is_llp64))
    changes.extend(_check_ref_qualifier_change(mangled, f_old, f_new))
    changes.extend(_check_linkage_change(mangled, f_old, f_new))
    changes.extend(_check_noexcept_change(mangled, f_old, f_new))
    changes.extend(_check_virtual_change(mangled, f_old, f_new))
    changes.extend(_check_hidden_friend_change(mangled, f_old, f_new))
    changes.extend(_check_explicit_change(mangled, f_old, f_new))
    return changes


def _check_inline_transitions(
    old_map: dict[str, Function], new_map: dict[str, Function],
    new_snapshot: AbiSnapshot,
) -> list[Change]:
    """Detect inline/non-inline transitions for functions present in both snapshots."""
    changes: list[Change] = []
    for mangled in set(old_map) & set(new_map):
        f_old = old_map[mangled]
        f_new = new_map[mangled]
        if not f_old.is_inline and f_new.is_inline:
            new_elf = new_snapshot.elf
            still_exported = (
                new_elf is not None
                and any(s.name == mangled for s in new_elf.symbols)
            )
            changes.append(Change(
                kind=ChangeKind.FUNC_BECAME_INLINE,
                symbol=mangled,
                description=(
                    f"Function became inline, symbol still exported: {f_old.name}"
                    if still_exported
                    else f"Function became inline (symbol may be removed from DSO): {f_old.name}"
                ),
                old_value="non-inline",
                new_value="inline",
            ))
        elif f_old.is_inline and not f_new.is_inline:
            changes.append(Change(
                kind=ChangeKind.FUNC_LOST_INLINE,
                symbol=mangled,
                description=f"Function lost inline attribute (now has external linkage): {f_old.name}",
                old_value="inline",
                new_value="non-inline",
            ))
    return changes


def _match_old_function(
    mangled: str,
    f_old: Function,
    new_map: dict[str, Function],
    new_by_name: dict[str, list[Function]],
    new_all: dict[str, Function],
    matched_by_name: set[str],
    elf_only_mode: bool,
    params_unconfirmed: bool = False,
    is_llp64: bool = False,
) -> list[Change]:
    """Classify a single old function: matched by mangled, extern-C fallback, or removed."""
    if mangled in new_map:
        return list(_check_function_signature(
            mangled, f_old, new_map[mangled],
            params_unconfirmed=params_unconfirmed, is_llp64=is_llp64))

    # A function that still exists on the new side but is ``= delete``'d is a
    # deletion, not a removal: _detect_newly_deleted_functions reports it once
    # as FUNC_DELETED / FUNC_DELETED_DWARF from the full function map. When a
    # DWARF-deleted member also drops out of .dynsym, _public_functions excludes
    # it from new_map (it is no longer exported), so without this guard the old
    # exported peer would additionally be flagged FUNC_REMOVED here, double-
    # reporting the same symbol. The castxml-deleted path keeps such functions
    # in new_map and is matched above; this aligns the deleted_from_dwarf path.
    f_new_all = new_all.get(mangled)
    if (
        f_new_all is not None
        and f_new_all.is_deleted
        and f_new_all.visibility in _PUBLIC_VIS
    ):
        return []

    # Fallback by plain name when either side uses extern "C".
    # The name->Function mapping is a MULTIMAP: only fall back when there is
    # EXACTLY ONE extern-C candidate for this name, to avoid mis-pairing
    # overloaded or templated functions that share a display name.
    candidates = new_by_name.get(f_old.name, [])
    extern_c_candidates = [f for f in candidates if f.is_extern_c]
    if f_old.is_extern_c:
        # Old side is extern "C": match against the unique new extern-C peer.
        extern_c_candidates = candidates  # any single candidate is acceptable
    if len(extern_c_candidates) == 1:
        f_new = extern_c_candidates[0]
        result = list(_check_function_signature(
            f_old.name, f_old, f_new,
            params_unconfirmed=params_unconfirmed, is_llp64=is_llp64))
        matched_by_name.add(f_old.name)
        return result

    return [_check_removed_function(mangled, f_old, new_all, elf_only_mode)]


def _detect_newly_deleted_functions(
    old_all: dict[str, Function],
    new_all: dict[str, Function],
    old_snapshot: AbiSnapshot,
    new_snapshot: AbiSnapshot,
) -> list[Change]:
    """Detect functions that gained ``= delete`` between snapshots.

    FUNC_DELETED: detected via castxml is_deleted attribute (header analysis).
    FUNC_DELETED_DWARF: detected via DWARF DW_AT_deleted attribute (binary analysis).

    Only ABI-visible (PUBLIC / ELF_ONLY) functions are reported; hidden or
    internal functions are not part of the public ABI surface and must not
    produce spurious BREAKING findings.
    """
    changes: list[Change] = []
    new_elf = getattr(new_snapshot, "elf", None)
    exported = exported_symbol_names(new_elf, FUNCTION_SYMBOL_TYPES)
    old_exported = exported_symbol_names(
        getattr(old_snapshot, "elf", None), FUNCTION_SYMBOL_TYPES
    )
    # Whether the new side has an ELF symbol table at all. This tells "no ELF
    # evidence available" apart from "ELF table present but this function is not
    # exported": when a table exists, an empty *function* export set (e.g. the
    # library exports only data, or every function is hidden) is authoritative —
    # a DWARF-only DW_AT_deleted internal member is genuinely not exported and
    # must not be reported. Keying on ``exported`` truthiness instead would only
    # apply the filter when some *other* function happened to be exported.
    has_elf_symbol_table = bool(getattr(new_elf, "symbols", None))
    for mangled, f_new in new_all.items():
        if not f_new.is_deleted:
            continue
        # Suppress only a *genuinely internal* DWARF-deleted member: one that the
        # new ELF table proves is not exported AND that was not exported in the
        # old library either. A function that *was* an old export and is now
        # ``= delete``'d + dropped from .dynsym is a real deletion of a public
        # API and must still be reported (the removal-side path defers to this
        # detector for it, so suppressing here would drop the finding entirely).
        if (
            f_new.deleted_from_dwarf
            and has_elf_symbol_table
            and mangled not in exported
            and mangled not in old_exported
        ):
            continue
        # Skip functions that are not part of the public ABI surface.
        if f_new.visibility not in _PUBLIC_VIS:
            continue
        f_old_any = old_all.get(mangled)
        if f_old_any is not None and not f_old_any.is_deleted:
            kind = (
                ChangeKind.FUNC_DELETED_DWARF
                if f_new.deleted_from_dwarf
                else ChangeKind.FUNC_DELETED
            )
            changes.append(Change(
                kind=kind,
                symbol=mangled,
                description=f"Function explicitly deleted (= delete): {f_new.name}",
                old_value="callable",
                new_value="deleted",
            ))
    return changes


@registry.detector("functions")
def _diff_functions(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    elf_only_mode = getattr(old, "elf_only_mode", False)
    # RD2-5: when one side is a stripped symbols-only stub, its parameter lists
    # are unknown (not "zero args"), so parameter diffs are unconfirmed.
    params_unconfirmed = _is_stripped_symbols_only(old) or _is_stripped_symbols_only(new)
    # LLP64 (Windows/PE): ``long`` is 32-bit, so e.g. long<->long long is a real
    # width change there; under LP64 (ELF/Mach-O) it is not. Resolves the
    # data-model-dependent integer ABI-equivalence checks below.
    is_llp64 = "pe" in (getattr(old, "platform", None), getattr(new, "platform", None))
    changes: list[Change] = []
    old_map = _public_functions(old)
    new_map = _public_functions(new)

    # Lookups for the virtual-method-addition check below: type records, the
    # old surface's scope-qualified owner classes (disambiguates same-leaf
    # classes across namespaces), and per-class virtual signatures (to skip
    # inherited overrides). See ``virtual_method_addition``.
    old_types = {t.name: t for t in old.types}
    new_types = {t.name: t for t in new.types}
    old_owner_classes = {
        owner for f in old_map.values()
        if (owner := owner_class_of(f)) is not None
    }
    old_virtual_sigs = old_virtual_signatures(old.function_map.values())

    # Build a lookup of ALL functions in new snapshot (including hidden).
    new_all = new.function_map

    # Build secondary index by plain name for extern-C fallback matching when
    # mangled names differ due to C/C++ compilation mode mismatch.
    # Use a multimap (name -> list) so overloaded/templated functions sharing a
    # display name are not silently collapsed to one candidate.
    new_by_name: dict[str, list[Function]] = {}
    for f in new_map.values():
        new_by_name.setdefault(f.name, []).append(f)
    matched_by_name: set[str] = set()

    for mangled, f_old in old_map.items():
        changes.extend(
            _match_old_function(
                mangled, f_old, new_map, new_by_name, new_all, matched_by_name,
                elf_only_mode, params_unconfirmed, is_llp64,
            )
        )

    for mangled, f_new in new_map.items():
        if mangled not in old_map and f_new.name not in matched_by_name:
            virtual_break = virtual_method_addition(
                f_new, old_owner_classes, old_types, new_types, old_virtual_sigs)
            changes.append(virtual_break if virtual_break is not None else Change(
                kind=ChangeKind.FUNC_ADDED,
                symbol=mangled,
                description=f"New public function: {f_new.name}",
                new_value=f_new.name,
            ))

    old_all = old.function_map
    new_all_map = new.function_map
    changes.extend(_detect_newly_deleted_functions(old_all, new_all_map, old, new))

    # FUNC_BECAME_INLINE / FUNC_LOST_INLINE: detect inline↔non-inline transitions
    changes.extend(_check_inline_transitions(old_map, new_map, new))

    # HIDDEN_FRIEND_ADDED / HIDDEN_FRIEND_REMOVED for the inline-only case.
    # Inline hidden friends have no external symbol (visibility=HIDDEN) so
    # the public-symbol diff above does not see them. Match across versions
    # by mangled name across the FULL function map (not just public).
    changes.extend(_diff_inline_hidden_friends(old_all, new_all_map))

    return changes


def _diff_inline_hidden_friends(
    old_all: dict[str, Function], new_all: dict[str, Function],
) -> list[Change]:
    """Pick up hidden-friend additions/removals that have no public symbol.

    Inline-defined hidden friends never appear in the .so dynsym (the
    compiler emits them as `linkonce_odr`, often inlined into callers).
    They show up in the castxml snapshot with ``visibility=HIDDEN`` and
    ``is_hidden_friend=True``. The public-symbol diff above skips them.
    This pass compares across the full function map and only fires for
    functions that are flagged as hidden friends on one side.
    """
    changes: list[Change] = []
    for mangled, f_old in old_all.items():
        if not f_old.is_hidden_friend:
            continue
        if mangled in new_all:
            continue
        changes.append(Change(
            kind=ChangeKind.HIDDEN_FRIEND_REMOVED,
            symbol=mangled,
            description=f"Hidden friend declaration removed: {f_old.name}",
            old_value=f_old.name,
        ))
    for mangled, f_new in new_all.items():
        if not f_new.is_hidden_friend:
            continue
        if mangled in old_all:
            continue
        changes.append(Change(
            kind=ChangeKind.HIDDEN_FRIEND_ADDED,
            symbol=mangled,
            description=f"Hidden friend declaration added: {f_new.name}",
            new_value=f_new.name,
        ))
    return changes


def _check_variable(mangled: str, v_old: Variable, v_new: Variable) -> list[Change]:
    """Compare a matched pair of public variables."""
    # RD2-5: a stripped side reports type "?"; unknown is not a type change.
    if _type_unknown(v_old.type) or _type_unknown(v_new.type):
        return []
    if canonicalize_type_name(v_old.type) != canonicalize_type_name(v_new.type):
        return [Change(
            kind=ChangeKind.VAR_TYPE_CHANGED,
            symbol=mangled,
            description=f"Variable type changed: {v_old.name}",
            old_value=v_old.type, new_value=v_new.type,
        )]
    # const-qualification transitions only matter when the type is unchanged.
    return bool_transition(
        v_old.is_const, v_new.is_const, mangled,
        added=(ChangeKind.VAR_BECAME_CONST, f"Variable became const-qualified: {v_old.name} (writes now → SIGSEGV)"),
        added_values=("non-const", "const"),
        removed=(ChangeKind.VAR_LOST_CONST, f"Variable lost const qualifier: {v_old.name} (ODR / inlining break)"),
        removed_values=("const", "non-const"),
    )


def _var_removed(mangled: str, v_old: Variable) -> list[Change]:
    return [Change(
        kind=ChangeKind.VAR_REMOVED,
        symbol=mangled,
        description=f"Public variable removed: {v_old.name}",
    )]


def _var_added(mangled: str, v_new: Variable) -> list[Change]:
    return [Change(
        kind=ChangeKind.VAR_ADDED,
        symbol=mangled,
        description=f"New public variable: {v_new.name}",
    )]


@registry.detector("variables")
def _diff_variables(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    return diff_by_key(
        _public_variables(old),
        _public_variables(new),
        on_removed=_var_removed,
        on_added=_var_added,
        on_common=_check_variable,
    )


def _both_header_aware(old: AbiSnapshot, new: AbiSnapshot) -> bool:
    """True only when BOTH snapshots carry *confirmed* header-tier evidence.

    ``from_headers_inferred`` is set when a legacy snapshot (one that predates
    the explicit ``from_headers`` key) is rehydrated and its header-awareness was
    only *guessed* — such a side may lack default-argument/constant data without
    it meaning "removed". Header-only detectors must require non-inferred header
    evidence on both sides so a mixed/legacy comparison never manufactures false
    ``*_REMOVED`` findings.
    """
    return (
        old.from_headers and not old.from_headers_inferred
        and new.from_headers and not new.from_headers_inferred
    )


@registry.detector("param_defaults")
def _diff_param_defaults(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect parameter default value changes/removals.

    Header-tier only: default-argument values are populated solely from castxml
    header parsing. If either side was NOT (confirmed) parsed from headers
    (DWARF/symbols mode, or a legacy/inferred headerless snapshot),
    ``Param.default`` is ``None`` only because the value is *unavailable*, not
    removed — comparing would report every defaulted parameter as
    ``PARAM_DEFAULT_VALUE_REMOVED``. Skip unless both sides are header-aware.
    """
    if not _both_header_aware(old, new):
        return []
    changes: list[Change] = []
    old_map = _public_functions(old)
    new_map = _public_functions(new)

    for mangled, f_old in old_map.items():
        f_new = new_map.get(mangled)
        if f_new is None:
            continue
        # Compare parameter defaults pairwise
        for i, (p_old, p_new) in enumerate(zip(f_old.params, f_new.params)):
            if p_old.default is not None and p_new.default is None:
                changes.append(Change(
                    kind=ChangeKind.PARAM_DEFAULT_VALUE_REMOVED,
                    symbol=mangled,
                    description=f"Parameter default removed: {f_old.name} param {p_old.name or i}",
                    old_value=p_old.default,
                    new_value=None,
                ))
            elif p_old.default is not None and p_new.default is not None and p_old.default != p_new.default:
                changes.append(Change(
                    kind=ChangeKind.PARAM_DEFAULT_VALUE_CHANGED,
                    symbol=mangled,
                    description=f"Parameter default changed: {f_old.name} param {p_old.name or i}",
                    old_value=p_old.default,
                    new_value=p_new.default,
                ))

    return changes


@registry.detector("param_renames")
def _diff_param_renames(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect parameter renames (same type+position, different name)."""
    changes: list[Change] = []
    # Require *explicit* header provenance on both sides. A legacy snapshot
    # predating the from_headers key has it inferred from a populated surface,
    # which a DWARF-only dump also satisfies — trusting that inference here
    # reintroduces PARAM_RENAMED/API_BREAK false positives on DWARF baselines.
    if not (old.from_headers and new.from_headers):
        return changes
    if old.from_headers_inferred or new.from_headers_inferred:
        return changes
    old_map = _public_functions(old)
    new_map = _public_functions(new)

    for mangled, f_old in old_map.items():
        f_new = new_map.get(mangled)
        if f_new is None:
            continue
        for i, (p_old, p_new) in enumerate(zip(f_old.params, f_new.params)):
            if p_old.type == p_new.type and p_old.name and p_new.name and p_old.name != p_new.name:
                changes.append(Change(
                    kind=ChangeKind.PARAM_RENAMED,
                    symbol=mangled,
                    description=f"Parameter renamed: {f_old.name} param {i}: {p_old.name} → {p_new.name}",
                    old_value=p_old.name,
                    new_value=p_new.name,
                ))

    return changes


@registry.detector("pointer_levels")
def _diff_pointer_levels(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect pointer level changes in params and return types."""
    changes: list[Change] = []
    old_map = _public_functions(old)
    new_map = _public_functions(new)
    # RD2-5: param depths from a stripped symbols-only stub default to 0 and
    # would read as phantom level changes; suppress them. The return depth is
    # guarded independently by the unknown-return ("?") check below.
    params_unconfirmed = _is_stripped_symbols_only(old) or _is_stripped_symbols_only(new)

    for mangled, f_old in old_map.items():
        f_new = new_map.get(mangled)
        if f_new is None:
            continue

        return_known = not (
            _type_unknown(f_old.return_type) or _type_unknown(f_new.return_type)
        )
        # Return pointer depth
        if return_known and f_old.return_pointer_depth != f_new.return_pointer_depth and (
            f_old.return_pointer_depth > 0 or f_new.return_pointer_depth > 0
        ):
            changes.append(Change(
                kind=ChangeKind.RETURN_POINTER_LEVEL_CHANGED,
                symbol=mangled,
                description=f"Return pointer level changed: {f_old.name} (depth {f_old.return_pointer_depth} → {f_new.return_pointer_depth})",
                old_value=str(f_old.return_pointer_depth),
                new_value=str(f_new.return_pointer_depth),
            ))

        if params_unconfirmed:
            continue

        # Param pointer depths
        for i, (p_old, p_new) in enumerate(zip(f_old.params, f_new.params)):
            # Skip individually unresolved params ("?"): depth falls back to 0
            # and would read as a phantom level change (matches _check_params_change).
            if _type_unknown(p_old.type) or _type_unknown(p_new.type):
                continue
            if p_old.pointer_depth != p_new.pointer_depth and (
                p_old.pointer_depth > 0 or p_new.pointer_depth > 0
            ):
                changes.append(Change(
                    kind=ChangeKind.PARAM_POINTER_LEVEL_CHANGED,
                    symbol=mangled,
                    description=f"Parameter pointer level changed: {f_old.name} param {p_old.name or i} (depth {p_old.pointer_depth} → {p_new.pointer_depth})",
                    old_value=str(p_old.pointer_depth),
                    new_value=str(p_new.pointer_depth),
                ))

    return changes


def _is_access_narrowing(old_access: Any, new_access: Any) -> bool:
    """Return True if the access level transition is narrowing (breaking).

    Narrowing = less accessible: public→protected, public→private, protected→private.
    Widening (e.g., private→public) is backward-compatible and should NOT be flagged.
    """
    from .model import AccessLevel
    _RANK = {AccessLevel.PUBLIC: 0, AccessLevel.PROTECTED: 1, AccessLevel.PRIVATE: 2}  # pylint: disable=invalid-name
    return _RANK.get(new_access, 0) > _RANK.get(old_access, 0)


def _check_method_access_changes(
    old_map: dict[str, Function],
    new_map: dict[str, Function],
) -> list[Change]:
    """Emit METHOD_ACCESS_CHANGED for narrowing method access transitions."""
    changes: list[Change] = []
    for mangled, f_old in old_map.items():
        f_new = new_map.get(mangled)
        if f_new is None:
            continue
        if f_old.access != f_new.access and _is_access_narrowing(f_old.access, f_new.access):
            changes.append(Change(
                kind=ChangeKind.METHOD_ACCESS_CHANGED,
                symbol=mangled,
                description=f"Method access level narrowed: {f_old.name} ({f_old.access.value} → {f_new.access.value})",
                old_value=f_old.access.value,
                new_value=f_new.access.value,
            ))
    return changes


def _check_field_access_changes(
    old_types: dict[str, Any],
    new_types: dict[str, Any],
) -> list[Change]:
    """Emit FIELD_ACCESS_CHANGED for narrowing field access transitions."""
    changes: list[Change] = []
    for name, t_old in old_types.items():
        t_new = new_types.get(name)
        if t_new is None:
            continue
        old_fields = {f.name: f for f in t_old.fields}
        new_fields = {f.name: f for f in t_new.fields}
        for fname, f_old_f in old_fields.items():
            f_new_f = new_fields.get(fname)
            if f_new_f is None:
                continue
            if f_old_f.access != f_new_f.access and _is_access_narrowing(f_old_f.access, f_new_f.access):
                changes.append(Change(
                    kind=ChangeKind.FIELD_ACCESS_CHANGED,
                    symbol=name,
                    description=f"Field access level narrowed: {name}::{fname} ({f_old_f.access.value} → {f_new_f.access.value})",
                    old_value=f_old_f.access.value,
                    new_value=f_new_f.access.value,
                ))
    return changes


@registry.detector("access_levels")
def _diff_access_levels(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect narrowing access level changes on methods and fields.

    Only flags narrowing transitions (public→protected/private, protected→private).
    Widening (e.g., private→public) is backward-compatible and not reported.
    """
    changes: list[Change] = []
    changes.extend(_check_method_access_changes(_public_functions(old), _public_functions(new)))
    excl = stdlib_namespaces_excluded(old, new)
    old_types = {t.name: t for t in old.types if not t.is_union and is_abi_surface_type_name(t.name, exclude_stdlib=excl)}
    new_types = {t.name: t for t in new.types if not t.is_union and is_abi_surface_type_name(t.name, exclude_stdlib=excl)}
    changes.extend(_check_field_access_changes(old_types, new_types))
    return changes


def _is_anon_field(f: Any) -> bool:
    """Return True for compiler-generated anonymous/unnamed fields."""
    return not f.name or f.name.startswith("__anon")


def _check_anon_field_at_offset(
    name: str,
    offset: int,
    f_old: Any,
    new_by_offset: dict[int, Any],
) -> Change | None:
    """Compare a single anonymous field (by offset) to what the new type has."""
    f_new = new_by_offset.get(offset)
    if f_new is None:
        return Change(
            kind=ChangeKind.ANON_FIELD_CHANGED,
            symbol=name,
            description=f"Anonymous field removed at offset {offset} in {name}",
            old_value=f_old.type,
        )
    if f_old.type != f_new.type:
        return Change(
            kind=ChangeKind.ANON_FIELD_CHANGED,
            symbol=name,
            description=f"Anonymous field type changed at offset {offset} in {name}",
            old_value=f_old.type,
            new_value=f_new.type,
        )
    return None


def _anon_fields_by_offset(fields: list[Any]) -> dict[int, Any]:
    """Index anonymous fields (no name or __anon prefix) by their bit offset."""
    return {f.offset_bits: f for f in fields if _is_anon_field(f) and f.offset_bits is not None}


def _check_anon_fields_for_type(name: str, t_old: Any, t_new: Any) -> list[Change]:
    """Compare anonymous fields by offset for a single matched type pair."""
    old_by_offset = _anon_fields_by_offset(t_old.fields)
    new_by_offset = _anon_fields_by_offset(t_new.fields)

    if not old_by_offset and not new_by_offset:
        return []

    changes: list[Change] = []
    for offset, f_old in old_by_offset.items():
        ch = _check_anon_field_at_offset(name, offset, f_old, new_by_offset)
        if ch is not None:
            changes.append(ch)
    return changes


@registry.detector("anon_fields")
def _diff_anon_fields(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect changes in anonymous struct/union members."""
    changes: list[Change] = []
    excl = stdlib_namespaces_excluded(old, new)
    old_map = {t.name: t for t in old.types if is_abi_surface_type_name(t.name, exclude_stdlib=excl)}
    new_map = {t.name: t for t in new.types if is_abi_surface_type_name(t.name, exclude_stdlib=excl)}

    for name, t_old in old_map.items():
        t_new = new_map.get(name)
        if t_new is None:
            continue
        changes.extend(_check_anon_fields_for_type(name, t_old, t_new))

    return changes


def _find_rename_pairs(
    removed: set[str],
    added: set[str],
    old_map: dict[str, Function],
    new_map: dict[str, Function],
) -> list[tuple[str, str]]:
    """Return (old_name, new_name) pairs where new_name has a common prefix added to old_name.

    The match condition is ``a_name.endswith(r_name)`` with ``a_name`` strictly
    longer (a prefix was prepended). The old ``endswith("_" + r_name)`` branch
    was redundant — any name ending with ``"_" + r_name`` already ends with
    ``r_name``. To avoid the O(removed × added) cross-product, index the added
    names *reversed* so the suffix test becomes a prefix lookup: a binary search
    locates the contiguous block of reversed added names that start with the
    reversed removed name. Both ``removed`` and the reversed index are iterated
    in sorted order, so the result is deterministic.
    """
    rev_index = sorted((new_map[a_sym].name[::-1], new_map[a_sym].name) for a_sym in added)
    rev_keys = [k for k, _ in rev_index]
    pairs: list[tuple[str, str]] = []
    for r_sym in sorted(removed):
        r_name = old_map[r_sym].name
        rk = r_name[::-1]
        i = bisect.bisect_left(rev_keys, rk)
        while i < len(rev_keys) and rev_keys[i].startswith(rk):
            a_name = rev_index[i][1]
            if len(a_name) > len(r_name):
                pairs.append((r_name, a_name))
                break
            i += 1
    return pairs


def _emit_batch_rename(rename_pairs: list[tuple[str, str]]) -> list[Change]:
    """Emit a SYMBOL_RENAMED_BATCH change if all pairs share a single common prefix."""
    if len(rename_pairs) < 2:
        return []
    prefixes = {new_name[: new_name.rfind(old_name)] for old_name, new_name in rename_pairs}
    if len(prefixes) != 1:
        return []
    prefix = prefixes.pop()
    pair_desc = ", ".join(f"{o} → {n}" for o, n in rename_pairs[:5])
    if len(rename_pairs) > 5:
        pair_desc += f", ... ({len(rename_pairs)} total)"
    return [Change(
        kind=ChangeKind.SYMBOL_RENAMED_BATCH,
        symbol=f"batch_rename:{prefix}*",
        description=(
            f"Batch symbol rename detected (namespace refactoring): "
            f"prefix '{prefix}' added to {len(rename_pairs)} symbols ({pair_desc})"
        ),
        old_value=", ".join(o for o, _ in rename_pairs),
        new_value=", ".join(n for _, n in rename_pairs),
    )]


@registry.detector("symbol_renames")
def _diff_symbol_renames(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect batch symbol renames (namespace refactoring).

    When multiple symbols are removed and corresponding prefixed versions are
    added (e.g. ``init`` → ``mylib_init``), this indicates a namespace
    refactoring that breaks all existing consumers.

    Heuristic: if 2+ removed symbols each have a matching added symbol where
    the added name ends with the removed name (common prefix pattern), emit
    a SYMBOL_RENAMED_BATCH change.
    """
    old_map = _public_functions(old)
    new_map = _public_functions(new)

    removed = set(old_map.keys()) - set(new_map.keys())
    added = set(new_map.keys()) - set(old_map.keys())

    if len(removed) < 2 or not added:
        return []

    rename_pairs = _find_rename_pairs(removed, added, old_map, new_map)
    return _emit_batch_rename(rename_pairs)


@registry.detector("param_restrict")
def _diff_param_restrict(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect restrict qualifier changes on parameters (ABICC: Parameter_Became_Restrict)."""
    changes: list[Change] = []
    old_map = _public_functions(old)
    new_map = _public_functions(new)

    for mangled, f_old in old_map.items():
        f_new = new_map.get(mangled)
        if f_new is None:
            continue
        for i, (p_old, p_new) in enumerate(zip(f_old.params, f_new.params)):
            if p_old.is_restrict != p_new.is_restrict:
                direction = "added" if p_new.is_restrict else "removed"
                changes.append(Change(
                    kind=ChangeKind.PARAM_RESTRICT_CHANGED,
                    symbol=mangled,
                    description=f"Parameter restrict qualifier {direction}: {f_old.name} param {p_old.name or i}",
                    old_value=f"restrict={p_old.is_restrict}",
                    new_value=f"restrict={p_new.is_restrict}",
                ))
    return changes


@registry.detector("param_va_list")
def _diff_param_va_list(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect va_list parameter changes (ABICC: Parameter_Became_VaList/Non_VaList)."""
    changes: list[Change] = []
    old_map = _public_functions(old)
    new_map = _public_functions(new)

    for mangled, f_old in old_map.items():
        f_new = new_map.get(mangled)
        if f_new is None:
            continue
        for i, (p_old, p_new) in enumerate(zip(f_old.params, f_new.params)):
            if not p_old.is_va_list and p_new.is_va_list:
                changes.append(Change(
                    kind=ChangeKind.PARAM_BECAME_VA_LIST,
                    symbol=mangled,
                    description=f"Parameter became va_list: {f_old.name} param {p_old.name or i}",
                    old_value=p_old.type,
                    new_value="va_list",
                ))
            elif p_old.is_va_list and not p_new.is_va_list:
                changes.append(Change(
                    kind=ChangeKind.PARAM_LOST_VA_LIST,
                    symbol=mangled,
                    description=f"Parameter was va_list, now fixed: {f_old.name} param {p_old.name or i}",
                    old_value="va_list",
                    new_value=p_new.type,
                ))
    return changes


@registry.detector("constants")
def _diff_constants(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect preprocessor / const-constant changes (ABICC: Changed/Added/Removed_Constant).

    Header-tier only: ``AbiSnapshot.constants`` is populated solely from castxml
    header parsing. If either side was NOT (confirmed) parsed from headers
    (DWARF/symbols mode, a snapshot taken before constant extraction, or a
    legacy/inferred headerless snapshot), its ``constants`` map is empty only
    because the data is *unavailable* — comparing would report every constant as
    removed (or added, depending on direction). Skip unless both sides are
    header-aware.
    """
    if not _both_header_aware(old, new):
        return []
    changes: list[Change] = []
    old_consts = old.constants
    new_consts = new.constants

    for name, old_val in old_consts.items():
        new_val = new_consts.get(name)
        if new_val is None:
            changes.append(Change(
                kind=ChangeKind.CONSTANT_REMOVED,
                symbol=name,
                description=f"Preprocessor constant removed: {name}",
                old_value=old_val,
            ))
        elif new_val != old_val:
            changes.append(Change(
                kind=ChangeKind.CONSTANT_CHANGED,
                symbol=name,
                description=f"Preprocessor constant value changed: {name} ({old_val!r} → {new_val!r})",
                old_value=old_val,
                new_value=new_val,
            ))

    for name, new_val in new_consts.items():
        if name not in old_consts:
            changes.append(Change(
                kind=ChangeKind.CONSTANT_ADDED,
                symbol=name,
                description=f"New preprocessor constant: {name}",
                new_value=new_val,
            ))
    return changes


@registry.detector("var_access")
def _diff_var_access(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect global data access level changes (ABICC: Global_Data_Became_Private/Protected/Public)."""
    changes: list[Change] = []
    old_map = _public_variables(old)
    new_map = _public_variables(new)

    for mangled, v_old in old_map.items():
        v_new = new_map.get(mangled)
        if v_new is None:
            continue
        if v_old.access != v_new.access:
            if _is_access_narrowing(v_old.access, v_new.access):
                changes.append(Change(
                    kind=ChangeKind.VAR_ACCESS_CHANGED,
                    symbol=mangled,
                    description=f"Variable access level narrowed: {v_old.name} ({v_old.access.value} → {v_new.access.value})",
                    old_value=v_old.access.value,
                    new_value=v_new.access.value,
                ))
            else:
                changes.append(Change(
                    kind=ChangeKind.VAR_ACCESS_WIDENED,
                    symbol=mangled,
                    description=f"Variable access level widened: {v_old.name} ({v_old.access.value} → {v_new.access.value})",
                    old_value=v_old.access.value,
                    new_value=v_new.access.value,
                ))
    return changes


_FUNC_LIKE_TYPES = frozenset({SymbolType.FUNC, SymbolType.IFUNC, SymbolType.NOTYPE})

# Minimum shared leading/trailing run (in characters) between two unqualified
# leaf names for a *hash-less* (size-only / fuzzy) match to count as a rename.
# When no code hash is available — the only mode the snapshot/elf_only path can
# reach — a "rename" is inferred purely from a coincidental symbol-size
# collision, which on a large library pairs completely unrelated functions that
# merely share a byte size (observed on real libLLVM diffs: e.g. fixupIndexV4 ->
# SmallVectorImpl<...>). A genuine rename or namespace relocation keeps a
# substantial common prefix or suffix token in the *unqualified* leaf name
# (foo_v1->foo_v2, old_only->new_only), whereas distinct leaves — even under a
# shared scope (Class::get vs Class::set, ::begin vs ::end, get<int> vs
# set<int>) — share at most one or two incidental characters. Requiring a
# >=3-char shared affix cleanly separates the two on measured data (genuine
# renames share 4-20, unrelated pairs 0-2).
_RENAME_MIN_SHARED_AFFIX = 3

# The C++ ``operator`` keyword as a whole token: not preceded or followed by an
# identifier character, so substrings like ``cooperator`` or ``operator_v1``
# (ordinary identifiers) and ``myoperator::foo`` (operator inside a qualifier)
# are not mistaken for an operator function name.
_OPERATOR_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_])operator(?![A-Za-z0-9_])")

# Itanium constructor/destructor variant codes: ``C1``/``C2``/``C3`` (complete /
# base / allocating constructor) and ``D0``/``D1``/``D2`` (deleting / complete /
# base destructor). These variants demangle to the *same* leaf yet are distinct
# exported symbols. A ``<ctor-dtor-name>`` is a real grammar production — it is
# NOT a length-prefixed ``<source-name>`` — so it must be located by parsing the
# nested-name's length-prefixed components, not by substring search (an ordinary
# identifier such as ``fooC1E`` would otherwise match).
_CTOR_DTOR_CODE_RE = re.compile(r"^(C[123]|D[012])E")


def _ctor_dtor_variant(symbol: str) -> str | None:
    """Return the Itanium ctor/dtor variant code (e.g. ``C1``) for a mangled
    name, or None when the symbol is not a constructor/destructor.

    Parses the ``_ZN`` nested-name: skips implicit-object cv/ref qualifiers,
    consumes the ``<len><identifier>`` ``<source-name>`` components (skipping any
    balanced ``I…E`` ``<template-args>`` block that follows a templated class
    name), then checks whether the remainder *begins* with a ``<ctor-dtor-name>``
    code. This distinguishes a real constructor (``_ZN6WidgetC1Ev`` -> ``C1``,
    ``_ZN3FooIiEC1Ev`` = ``Foo<int>::Foo()`` -> ``C1``, ``_ZN3FooI3ErrEC1Ev`` =
    ``Foo<Err>::Foo()`` -> ``C1``) from an ordinary member whose identifier
    merely contains the characters (``_ZN1A6fooC1EEv`` = ``A::fooC1E()`` ->
    None). Encodings this simple parser does not model (exotic template
    arguments) yield None — safe, since the only consequence is not suppressing
    a (rare) templated-ctor variant pair.
    """
    if not symbol.startswith("_ZN"):
        return None
    i = 3
    # Skip implicit-object cv-/ref-qualifiers (K const, V volatile, r restrict,
    # R lvalue-ref, O rvalue-ref).
    while i < len(symbol) and symbol[i] in "KVrRO":
        i += 1
    # Consume <prefix> components: <source-name> (<decimal-length><identifier>),
    # each optionally followed by a <template-args> block ``I…E``. A templated
    # class name (``_ZN3FooIiEC1Ev``) places the args before the ctor/dtor code.
    while i < len(symbol):
        if symbol[i].isdigit():
            i = _skip_source_name(symbol, i)
            if i < 0:
                return None  # malformed length — bail out
        elif symbol[i] == "I":
            i = _skip_template_args(symbol, i)
            if i < 0:
                return None  # unbalanced / unmodeled — bail out
        elif symbol[i] == "S":
            # A standard/standard-library substitution can open the prefix, e.g.
            # ``_ZNSt6vectorIiEC1Ev`` (St = std::) — consume it before the
            # source-name components so the ctor/dtor code is still found.
            i = _skip_substitution(symbol, i)
        elif symbol[i] == "B":
            # ABI-tag component ``B<source-name>`` on the class name, e.g.
            # ``_ZN3FooB1xC1Ev`` (Foo[abi:x]). Consume it so the ctor/dtor code
            # that follows is still reached.
            i += 1
            if i < len(symbol) and symbol[i].isdigit():
                i = _skip_source_name(symbol, i)
                if i < 0:
                    return None  # malformed ABI tag — bail out
            else:
                break  # not a well-formed ABI tag
        else:
            break
    m = _CTOR_DTOR_CODE_RE.match(symbol[i:])
    return m.group(1) if m else None


def _skip_source_name(symbol: str, i: int) -> int:
    """Skip an Itanium ``<source-name>`` (``<decimal-length><identifier>``)
    starting at ``symbol[i]``; return the index past it, or -1 if malformed."""
    j = i
    while j < len(symbol) and symbol[j].isdigit():
        j += 1
    remaining, length = len(symbol) - j, 0
    for c in symbol[i:j]:
        if (length := (length * 10) + (ord(c) - ord("0"))) > remaining:
            return -1
    return j + length


def _skip_substitution(symbol: str, i: int) -> int:
    """Skip an Itanium ``<substitution>`` starting at ``symbol[i]`` (an ``S``);
    return the index past it.

    Handles ``S_``, ``S<seq-id>_`` (seq-id is base-36 ``[0-9A-Z]``), and the
    special two-character abbreviations (``St`` std, ``Ss`` std::string, ``Sa``,
    ``Sb``, ``Si``, ``So``, ``Sd``). Consuming it whole keeps any digits in a
    seq-id from being misread as a ``<source-name>`` length.
    """
    n = len(symbol)
    i += 1  # consume 'S'
    if i < n and (symbol[i].isdigit() or symbol[i].isupper()):
        while i < n and symbol[i] != "_":
            i += 1
        return i + 1  # consume the closing '_'
    return i + 1  # special two-char abbreviation (St, Ss, …) or bare 'S_'


def _skip_template_args(symbol: str, i: int) -> int:
    """Skip a balanced Itanium ``<template-args>`` block (``I…E``) starting at
    ``symbol[i]`` (an ``I``); return the index past the matching ``E``, or -1.

    The block content must be parsed, not merely scanned for ``E``: a
    length-prefixed ``<source-name>`` argument (``Foo<Err>`` = ``...I3ErrE...``)
    contains an ``E`` *inside* its identifier that would otherwise close the
    block early, and an expr-primary literal (``Foo<5>`` = ``...ILi5EE...``)
    carries its own terminating ``E``. So source-names, substitutions, and
    literals are consumed whole; only ``I``/``N``/``F`` openers and their ``E``
    terminators move the nesting depth. Constructs this does not model yield -1.
    """
    n = len(symbol)
    depth = 0
    while i < n:
        c = symbol[i]
        if c.isdigit():
            # <source-name>: consume the identifier whole so its characters
            # (which may include E/I/N/F/L) are not read as structure.
            i = _skip_source_name(symbol, i)
            if i < 0:
                return -1
        elif c == "S":
            # <substitution>: consume whole so its digits are not mistaken for a
            # source-name length.
            i = _skip_substitution(symbol, i)
        elif c == "L":
            # <expr-primary> literal: ``L<type><value>E``. Scan to its own
            # terminating ``E`` literally — its value digits are not lengths.
            i += 1
            while i < n and symbol[i] != "E":
                i += 1
            if i >= n:
                return -1
            i += 1  # consume the literal's 'E'
        elif c in "INF":
            depth += 1
            i += 1
        elif c == "E":
            depth -= 1
            i += 1
            if depth == 0:
                return i
        else:
            i += 1
    return -1  # unbalanced


def _unqualified_name(symbol: str) -> str:
    """Extract the unqualified (leaf) function name from a symbol, robustly.

    Matching-safe alternative to ``demangle.base_name`` (which is documented
    display-only and mis-parses operators / templates). Demangles when a
    demangler is available, then, using *bracket-depth tracking* so that ``::``,
    ``(`` and spaces inside template arguments are ignored:

    * keeps the whole ``operator...`` token intact;
    * drops the parameter list;
    * drops the namespace/class qualifier (segment after the last top-level
      ``::``);
    * drops a leading return type (global function templates demangle as
      ``ret name<args>(...)``).

    Trailing template arguments are *kept*: a specialization like ``foo<int>``
    is a distinct ABI symbol from ``foo<long>``, so they must not collapse to a
    shared leaf (that would mis-report a specialization swap as a rename).
    """
    from .demangle import demangle

    return _unqualified_name_of(demangle(symbol) or symbol)


def _unwrap_funcptr_declarator(s: str) -> str:
    """Unwrap a function-pointer/-reference *return* declarator so the real
    function name is visible.

    A C++ function that returns a function pointer demangles to declarator
    syntax — ``RET (*name(args))(fnptr-args)``, e.g. ``int (*foo<int>())()`` —
    where the first top-level ``(`` opens the declarator group, *not* the
    parameter list. Left as-is, leaf extraction would stop at that ``(`` and
    collapse the name to the return type. When ``s`` has this shape (the first
    top-level ``(`` is immediately followed by ``*``/``&``), return the inner
    ``name(args)`` so the normal leaf/parameter logic sees the real name;
    otherwise return ``s`` unchanged. Ordinary parameter lists (whose first char
    is a type or ``)``, never ``*``/``&`` at the very front) are left intact, as
    are functions that merely *take* a function-pointer parameter.
    """
    depth = 0  # <> template depth — ignore '(' inside template arguments
    for i, ch in enumerate(s):
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth = max(0, depth - 1)
        elif ch == "(" and depth == 0:
            j = i + 1
            while j < len(s) and s[j] == " ":
                j += 1
            if j >= len(s) or s[j] not in "*&":
                return s  # ordinary parameter list, not a pointer declarator
            # Find the ')' matching this declarator-group '(' (bracket-aware).
            close = _match_declarator_group(s, i)
            if close is None:
                return s  # unbalanced — leave alone
            return s[i + 1:close].lstrip("*& ")
    return s


def _match_declarator_group(s: str, open_idx: int) -> int | None:
    """Return the index of the ``)`` matching the ``(`` at *open_idx*, or None.

    Bracket-aware: ``(``/``)`` nested inside template arguments (``<...>``) do
    not affect the paren depth.
    """
    pdepth = 0
    tdepth = 0
    for k in range(open_idx, len(s)):
        c = s[k]
        if c == "<":
            tdepth += 1
        elif c == ">":
            tdepth = max(0, tdepth - 1)
        elif c == "(" and tdepth == 0:
            pdepth += 1
        elif c == ")" and tdepth == 0:
            pdepth -= 1
            if pdepth == 0:
                return k
    return None


def _unqualified_name_of(s: str) -> str:
    """Leaf-name core of ``_unqualified_name`` operating on an already-demangled
    (or raw, when no demangler is available) string. Split out so callers that
    need both the leaf and the parameter signature can demangle once."""
    s = _unwrap_funcptr_declarator(s)
    # An operator name encodes punctuation (``<<``, ``()``, ``[]``) that defeats
    # bracket tracking, so handle it first: keep everything from the ``operator``
    # token to the end. It is stable and symmetric, which is all the matcher
    # needs. Match ``operator`` only as a whole token so ordinary identifiers
    # that merely contain the substring (``cooperator``, ``operator_v1``) are
    # not misclassified.
    op = _OPERATOR_TOKEN_RE.search(s)
    if op is not None:
        return s[op.start():].strip()
    s = _truncate_at_param_list(s)
    s = _after_last_top_level_scope(s).strip()
    s = _drop_leading_return_type(s)
    return s.strip()


def _truncate_at_param_list(s: str) -> str:
    """Drop everything from the parameter-list ``(`` at template depth 0 on."""
    depth = 0
    for i, ch in enumerate(s):
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth = max(0, depth - 1)
        elif ch == "(" and depth == 0:
            return s[:i]
    return s


def _after_last_top_level_scope(s: str) -> str:
    """Return the segment after the last ``::`` that sits at template depth 0."""
    depth = 0
    last = 0
    i = 0
    while i < len(s) - 1:
        ch = s[i]
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth = max(0, depth - 1)
        elif ch == ":" and s[i + 1] == ":" and depth == 0:
            last = i + 2
            i += 2
            continue
        i += 1
    return s[last:]


def _drop_leading_return_type(s: str) -> str:
    """Drop a leading return type by taking the part after the last top-level
    space (e.g. ``void get<int>`` -> ``get<int>``)."""
    depth = 0
    sp = -1
    for i, ch in enumerate(s):
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth = max(0, depth - 1)
        elif ch == " " and depth == 0:
            sp = i
    if sp != -1:
        return s[sp + 1:]
    return s


def _strip_template_args(leaf: str) -> str:
    """Drop trailing template arguments from a leaf (``get<int>`` -> ``get``)."""
    if leaf.endswith(">"):
        depth = 0
        for i in range(len(leaf) - 1, -1, -1):
            if leaf[i] == ">":
                depth += 1
            elif leaf[i] == "<":
                depth -= 1
                if depth == 0:
                    return leaf[:i]
    return leaf


def _shared_affix_len(a: str, b: str) -> int:
    """Length of the longer of the common leading / common trailing run."""
    def common_prefix(x: str, y: str) -> int:
        n = 0
        for cx, cy in zip(x, y):
            if cx != cy:
                break
            n += 1
        return n
    return max(common_prefix(a, b), common_prefix(a[::-1], b[::-1]))


def _param_signature(symbol: str) -> str:
    """The parameter-list portion of a symbol (``foo(int)`` -> ``(int)``).

    Empty when there is no parameter list — a plain C symbol, a variable, or a
    mangled C++ symbol with no demangler available. A genuine rename or
    namespace relocation keeps the parameters; a parameter change is a distinct
    ABI symbol, so comparing this lets the gate reject ``foo(int)`` -> ``foo(long)``.
    """
    from .demangle import demangle

    return _param_signature_of(demangle(symbol) or symbol)


def _param_signature_of(s: str) -> str:
    """Parameter-signature core of ``_param_signature`` operating on an
    already-demangled (or raw) string."""
    s = _unwrap_funcptr_declarator(s)
    depth = 0
    for i, ch in enumerate(s):
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth = max(0, depth - 1)
        elif ch == "(" and depth == 0:
            return s[i:]
    return ""


def _return_type_of(s: str) -> str:
    """The leading return type of a demangled name, or "" when there is none.

    A return type appears in demangled output only when it is part of the
    mangled ABI symbol — chiefly C++ function-template instantiations
    (``int foo<int>()``) — so for ordinary functions this is empty and the
    comparison in ``_plausible_rename`` is a no-op. It is the run before the
    last top-level space that precedes the (qualified) function name, with
    template ``<…>`` and ``::`` kept intact (``unsigned int foo<int>()`` ->
    ``unsigned int``; ``std::vector<int> bar()`` -> ``std::vector<int>``).
    """
    s = _unwrap_funcptr_declarator(s)
    if _OPERATOR_TOKEN_RE.search(s):
        return ""  # operator spellings carry no separable leading return type
    # Truncate at the parameter-list '(' at template depth 0.
    depth = 0
    for i, ch in enumerate(s):
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth = max(0, depth - 1)
        elif ch == "(" and depth == 0:
            s = s[:i]
            break
    # The return type, if any, is everything before the last top-level space.
    depth = 0
    sp = -1
    for i, ch in enumerate(s):
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth = max(0, depth - 1)
        elif ch == " " and depth == 0:
            sp = i
    return s[:sp].strip() if sp != -1 else ""


@lru_cache(maxsize=65536)
def _rename_name_parse(name: str) -> tuple[str | None, str, str, str]:
    """Per-name pieces used by :func:`_plausible_rename`, demangled once.

    Returns ``(ctor_dtor_variant, leaf, param_signature, return_type)``. The
    name-similarity gate compares every removed symbol against every size-
    eligible added one, so the same name is parsed many times; caching the
    per-name derivation keeps that gate from re-demangling and re-parsing the
    same symbol on each pair (the dominant cost of rename detection on large
    ELF-only libraries). Bounded so it cannot grow without limit.
    """
    from .demangle import demangle

    d = demangle(name) or name
    return (
        _ctor_dtor_variant(name),
        _unqualified_name_of(d),
        _param_signature_of(d),
        _return_type_of(d),
    )


def _plausible_rename(old_name: str, new_name: str) -> bool:
    """Whether two symbol names are similar enough to credibly be a rename.

    Compares the *unqualified* leaf names (see ``_unqualified_name``). A rename
    or namespace relocation keeps the leaf name (identical leaf, template
    arguments included) or a substantial common prefix/suffix token **and** the
    same parameter list; unrelated functions that merely share a byte size are
    rejected. Rejected cases include different methods under a common scope
    (``Class::get`` vs ``Class::set``), different template specializations of
    one name (``foo<int>`` vs ``foo<long>``), and same-name parameter changes
    (``foo(int)`` vs ``foo(long)``) — all of which are distinct ABI symbols.
    Used only to gate hash-less matches, where size alone is not evidence of
    identity.
    """
    if old_name == new_name:
        return True
    # Itanium ctor/dtor variants (C1/C2/C3, D0/D1/D2) demangle to the same leaf
    # but are distinct exported symbols. A pair is a plausible ctor/dtor rename
    # only when BOTH sides are the *same* variant (a genuine relocation keeps
    # it). Any mismatch is rejected: differing variants (complete-object C1 vs
    # base-object C2), and — crucially — a one-sided match where only one side
    # is a ctor/dtor (e.g. removed ctor ``A::A()`` vs added ordinary method
    # ``B::A()`` both reduce to leaf ``A()``), since a constructor ABI symbol
    # cannot be satisfied by an ordinary member. (Checked on the raw mangled
    # name, so it catches the case the demangler collapses to an identical leaf.)
    ov, a, pa, ra = _rename_name_parse(old_name)
    nv, b, pb, rb = _rename_name_parse(new_name)
    if (ov is not None or nv is not None) and ov != nv:
        return False
    # Undemangleable mangled names: when no demangler is available the leaf is
    # the raw Itanium spelling, whose shared boilerplate (``_ZN``, type codes,
    # …) would inflate the affix score and pair unrelated symbols. Demangling is
    # optional for this package, so treat such names conservatively — accept
    # only an exact match (rejected here, since removed/added names differ).
    if a.startswith("_Z") or b.startswith("_Z"):
        return a == b
    # Operator leaves include their parameters and share the literal
    # ``operator`` token; a destructor leaf (``~Widget``) shares the class name
    # with that class's constructor leaf (``Widget``). For both, an affix match
    # would pair genuinely different ABI functions (operator+ vs operator-, ctor
    # vs dtor), so accept only an exact leaf match.
    for leaf in (a, b):
        if _OPERATOR_TOKEN_RE.match(leaf) is not None or leaf.startswith("~"):
            return a == b
    # A rename/relocation preserves the full signature: parameters AND — for
    # the function templates whose mangling encodes it — the return type. A
    # change to either is a distinct ABI symbol (foo(int) -> foo(long), or
    # int foo<int>() -> long foo<int>()), not a rename. Ordinary (non-template)
    # functions demangle without a return type, so that check is a no-op there.
    sig_match = pa == pb and ra == rb
    if a == b:
        # Same unqualified name + template args: a rename only if the signature
        # also matches (else it is a signature change).
        return sig_match
    base_a = _strip_template_args(a)
    base_b = _strip_template_args(b)
    # Same base name but different leaves means the template arguments differ:
    # distinct specializations are distinct ABI symbols, not a rename — a
    # consumer of foo<int> still fails to link against foo<long>.
    if base_a == base_b:
        return False
    return sig_match and _shared_affix_len(base_a, base_b) >= _RENAME_MIN_SHARED_AFFIX


def _fingerprints_from_elf(snap: AbiSnapshot) -> dict[str, FunctionFingerprint]:
    """Build FunctionFingerprint dict from ELF metadata (size-only, no code hash).

    Uses ElfSymbol.size from .dynsym to create fingerprints for rename matching.
    Includes FUNC, IFUNC, and NOTYPE symbols — matching dumper.py's
    ``exported_dynamic_funcs`` categorization for elf_only_mode snapshots.
    Code hashing requires the binary file and is handled by
    ``binary_fingerprint.compute_function_fingerprints()`` when a path is available.
    """
    if snap.elf is None:
        return {}
    filter_transitive_runtime_symbols = _should_filter_transitive_runtime_symbols(snap)
    result: dict[str, FunctionFingerprint] = {}
    for sym in snap.elf.symbols:
        if sym.sym_type not in _FUNC_LIKE_TYPES:
            continue
        if not is_abi_relevant_elf_symbol(
            sym.name,
            filter_transitive_runtime_symbols=filter_transitive_runtime_symbols,
        ):
            continue
        if sym.size < _MIN_SYMBOL_SIZE:
            continue
        result[sym.name] = FunctionFingerprint(
            name=sym.name,
            size=sym.size,
            code_hash="",  # no code hash from metadata alone
        )
    return result


@registry.detector(
    "fingerprint_renames",
    requires_support=lambda o, n: (
        o.elf is not None and n.elf is not None
        and (o.elf_only_mode or n.elf_only_mode),
        "requires ELF metadata in elf_only_mode",
    ),
)
def _diff_fingerprint_renames(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect likely function renames using binary fingerprint matching.

    Only runs in elf_only_mode (stripped binaries without debug info or headers),
    where rename churn is most problematic.  Uses function code size from
    ELF .dynsym to find removed+added pairs that likely represent the same
    function under a different name.

    Fires when *either* snapshot is elf_only — the rename churn problem exists
    even if only one side is stripped.
    """
    changes: list[Change] = []

    old_fps = _fingerprints_from_elf(old)
    new_fps = _fingerprints_from_elf(new)

    if not old_fps or not new_fps:
        return changes

    old_elf = getattr(old, "elf", None)
    new_elf = getattr(new, "elf", None)
    old_filter_transitive_runtime_symbols = _should_filter_transitive_runtime_symbols(old)
    new_filter_transitive_runtime_symbols = _should_filter_transitive_runtime_symbols(new)
    old_exported_funcs = {
        sym.name
        for sym in (old_elf.symbols if old_elf is not None else [])
        if sym.sym_type in _FUNC_LIKE_TYPES
        and is_abi_relevant_elf_symbol(
            sym.name,
            filter_transitive_runtime_symbols=old_filter_transitive_runtime_symbols,
        )
    }
    new_exported_funcs = {
        sym.name
        for sym in (new_elf.symbols if new_elf is not None else [])
        if sym.sym_type in _FUNC_LIKE_TYPES
        and is_abi_relevant_elf_symbol(
            sym.name,
            filter_transitive_runtime_symbols=new_filter_transitive_runtime_symbols,
        )
    }
    retained_exported_funcs = old_exported_funcs & new_exported_funcs
    old_fps = {
        name: fp
        for name, fp in old_fps.items()
        if name not in retained_exported_funcs
    }
    new_fps = {
        name: fp
        for name, fp in new_fps.items()
        if name not in retained_exported_funcs
    }
    if not old_fps or not new_fps:
        return changes

    # Matches in this path are hash-less (size-only), inferred from symbol size
    # alone since _fingerprints_from_elf has no code bytes. Pass the name-
    # similarity predicate into the matcher so it participates in candidate
    # *selection*: a coincidental same-size symbol can neither be reported as a
    # rename nor greedily consume a partner that a plausible rename should claim.
    candidates = match_renamed_functions(old_fps, new_fps, name_filter=_plausible_rename)
    for c in candidates:
        conf_pct = int(c.confidence * 100)
        changes.append(Change(
            kind=ChangeKind.FUNC_LIKELY_RENAMED,
            symbol=c.old_name,
            description=(
                f"Function likely renamed: {c.old_name} → {c.new_name} "
                f"(size={c.old_fingerprint.size}B, confidence={conf_pct}%)"
            ),
            old_value=c.old_name,
            new_value=c.new_name,
        ))

    if candidates:
        _log.info(
            "Fingerprint rename detection: %d candidate(s) found",
            len(candidates),
        )

    return changes
