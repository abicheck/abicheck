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

"""ELF symbol-versioning and per-symbol metadata diff detectors.

Split from ``diff_platform.py`` to keep that module under the AI-readiness
file-size soft cap. This module is a leaf — it must not import from
``diff_platform``. The helpers/detectors are re-exported back from
``diff_platform`` so existing imports keep working.
"""

from __future__ import annotations

import re
from typing import Any

from .checker_policy import ChangeKind
from .checker_types import Change
from .diff_helpers import make_change
from .elf_metadata import SymbolBinding, SymbolType
from .model import AbiSnapshot

# Module-level constant: ELF visibility values that form the default<->protected pair (case51).
_ELF_VIS_PROTECTED_PAIR: frozenset[str] = frozenset({"default", "protected"})


def _is_const_unbounded_string_object(snap: AbiSnapshot, sym_name: str) -> bool:
    """Return True for header-visible const char string objects without a bound."""
    if not snap.from_headers or snap.from_headers_inferred:
        return False
    var = snap.variable_map.get(sym_name)
    if var is None:
        var = next(
            (candidate for candidate in snap.variables if candidate.name == sym_name),
            None,
        )
    if var is None:
        return False
    if not var.is_const:
        return False
    typ = re.sub(r"\s+", " ", var.type.replace("const char", "char const")).strip()
    return typ in {"char const[]", "char const []", "const char[]", "const char []"}


def _is_internal_data_symbol(name: str) -> bool:
    """True if an exported *data* symbol name looks reserved/internal.

    A leading underscore on a file/global-scope identifier is reserved for the
    implementation (C standard) and is the convention real libraries use for
    private exported data (``_XkeyTable``, ``_pcre2_ucd_records_8``,
    ``_UCD_accessors``, ``_rl_*``). Such symbols are not part of the intended
    public ABI, but exported data still participates in the dynamic ABI, so the
    kind remains breaking by default. Linker artifacts (``_init``/``_edata``/…)
    are filtered earlier.

    Mangled C++ (``_Z…`` / ``__Z…``) symbols are excluded: their leading
    underscore is part of the Itanium mangling, not a reserved-identifier
    marker — they denote real (public) C++ objects whose size change IS a break.
    """
    if name.startswith(("_Z", "__Z")):
        return False
    return name.startswith("_")


_UNPARSEABLE_VERSION: tuple[int, ...] = (2**31,)
"""Sentinel returned by :func:`_parse_abi_version_tag` for non-numeric tags
like ``GLIBC_PRIVATE``.  Sorts *above* any real version so that a new
non-numeric requirement is always treated as potentially BREAKING — never
silently COMPAT."""


def _parse_abi_version_tag(ver: str) -> tuple[int, ...]:
    """Parse a versioned symbol tag like ``GLIBC_2.34`` or ``GLIBCXX_3.4.19``
    into a comparable integer tuple.

    Only the numeric suffix after the last ``_`` is used:
    ``GLIBC_2.34`` → ``(2, 34)``, ``GLIBCXX_3.4.19`` → ``(3, 4, 19)``.

    Returns :data:`_UNPARSEABLE_VERSION` for non-numeric tags such as
    ``GLIBC_PRIVATE`` — a very large sentinel that always compares as newer
    than any real version, so such tags are conservatively treated as BREAKING.
    """
    parts = ver.rsplit("_", 1)
    numeric = parts[-1] if len(parts) > 1 else ver
    result = tuple(int(x) for x in numeric.split(".") if x.isdigit())
    return result if result else _UNPARSEABLE_VERSION


def _diff_elf_symbol_versioning(old_elf: Any, new_elf: Any) -> list[Change]:
    changes: list[Change] = []
    old_def = set(old_elf.versions_defined)
    new_def = set(new_elf.versions_defined)
    for ver in sorted(old_def - new_def):
        if _is_unattached_private_version_node(old_elf, ver):
            continue
        changes.append(
            make_change(
                ChangeKind.SYMBOL_VERSION_DEFINED_REMOVED,
                symbol=ver,
                old=ver,
            )
        )
    for ver in sorted(new_def - old_def):
        changes.append(
            make_change(
                ChangeKind.SYMBOL_VERSION_DEFINED_ADDED,
                symbol=ver,
                new=ver,
            )
        )

    all_req_libs = set(old_elf.versions_required) | set(new_elf.versions_required)
    for lib in sorted(all_req_libs):
        old_vers = set(old_elf.versions_required.get(lib, []))
        new_vers = set(new_elf.versions_required.get(lib, []))
        # The old maximum requirement for this lib — anything added that
        # is *older* than this maximum is not a new constraint on the caller.
        # If the lib is entirely new (not in old at all), its version
        # requirements are already captured by needed_added → COMPATIBLE.
        lib_is_new = lib not in old_elf.versions_required and lib not in getattr(
            old_elf, "needed", []
        )

        # Compute old max PER VERSION-TAG PREFIX (e.g. "GLIBC", "GLIBCXX", "CXXABI")
        # to avoid cross-namespace bleed: GLIBCXX_3.4.32 must not suppress a
        # genuinely newer CXXABI_1.3.14 requirement.
        def _old_max_for_prefix(
            prefix: str, _old_vers: set[str] = old_vers
        ) -> tuple[int, ...]:  # pylint: disable=dangerous-default-value
            matching = [
                _parse_abi_version_tag(v)
                for v in _old_vers
                if v.startswith(prefix + "_")
            ]
            return max(matching, default=(0,))

        for ver in sorted(new_vers - old_vers):
            ver_tuple = _parse_abi_version_tag(ver)
            prefix = ver.rsplit("_", 1)[0] if "_" in ver else ver
            old_max = _old_max_for_prefix(prefix)
            if lib_is_new or ver_tuple <= old_max:
                # Either the whole lib is new (covered by needed_added), or the
                # added requirement is not newer than the old max — COMPATIBLE.
                changes.append(
                    make_change(
                        ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED_COMPAT,
                        symbol=ver,
                        name=ver,
                        detail=lib,
                        new_value=f"{lib}:{ver}",
                    )
                )
            else:
                # Genuinely newer requirement — callers on older runtimes will fail.
                changes.append(
                    make_change(
                        ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED,
                        symbol=ver,
                        name=ver,
                        detail=lib,
                        new_value=f"{lib}:{ver}",
                    )
                )
        for ver in sorted(old_vers - new_vers):
            changes.append(
                make_change(
                    ChangeKind.SYMBOL_VERSION_REQUIRED_REMOVED,
                    symbol=ver,
                    name=ver,
                    detail=lib,
                    old_value=f"{lib}:{ver}",
                )
            )
    return changes


def _is_unattached_private_version_node(elf: Any, version: str) -> bool:
    """Return True for private version-script marker nodes with no exports.

    Thin wrapper around the canonical helper in :mod:`diff_versioning` so the
    version-def removal path and the version-script-missing path agree on what
    counts as an unattached private marker.
    """
    from .diff_versioning import _is_unattached_private_version_node as _impl

    return _impl(elf, version)


def _diff_elf_symbol_metadata(
    old: AbiSnapshot | Any,
    new: AbiSnapshot | Any,
    old_elf: Any | None = None,
    new_elf: Any | None = None,
) -> list[Change]:
    if old_elf is None and new_elf is None:
        old_elf = old
        new_elf = new
        old = AbiSnapshot(library="", version="")
        new = AbiSnapshot(library="", version="")
    assert old_elf is not None
    assert new_elf is not None
    changes: list[Change] = []
    old_syms = old_elf.symbol_map
    new_syms = new_elf.symbol_map

    for sym_name, s_old in old_syms.items():
        s_new = new_syms.get(sym_name)
        if s_new is None:
            continue
        changes.extend(_diff_elf_symbol_pair(old, new, sym_name, s_old, s_new))

    for sym_name, s_new in new_syms.items():
        if s_new.sym_type != SymbolType.COMMON:
            continue
        old_common = old_syms.get(sym_name)
        if old_common is None or old_common.sym_type != SymbolType.COMMON:
            changes.append(
                make_change(
                    ChangeKind.COMMON_SYMBOL_RISK,
                    symbol=sym_name,
                    name=sym_name,
                )
            )

    changes.extend(_check_gained_gnu_unique(old_syms, new_syms))
    return changes


def _check_gained_gnu_unique(
    old_syms: dict[str, Any], new_syms: dict[str, Any]
) -> list[Change]:
    """Report a release newly gaining STB_GNU_UNIQUE exports (G23-A4).

    The per-symbol transition detector (``_check_binding_change``) only sees
    symbols present on both sides, so a *newly-added* unique export — the common
    case when a library first turns on ``-fgnu-unique`` — would otherwise be
    reported as a plain compatible addition, missing the dlclose-inhibition /
    process-wide-uniqueness risk. Because enabling the flag flips many symbols at
    once, this fires **once** per release, at the library level: only when the
    old side exported *no* unique symbols and the new side introduces one.
    """
    if not old_syms:
        # Empty baseline symbol table = the old side never captured ELF (header-
        # only / legacy / parse-failed), so the old binding is *unknown*, not
        # proven absent. Firing here would flag every pre-existing GNU_UNIQUE
        # export as newly introduced.
        return []
    if any(s.binding == SymbolBinding.UNIQUE for s in old_syms.values()):
        return []  # already non-unloadable; adding more changes nothing
    added_unique = sorted(
        name
        for name, s in new_syms.items()
        if s.binding == SymbolBinding.UNIQUE and name not in old_syms
    )
    if not added_unique:
        return []
    first = added_unique[0]
    suffix = f" (+{len(added_unique) - 1} more)" if len(added_unique) > 1 else ""
    return [
        make_change(
            ChangeKind.SYMBOL_BINDING_BECAME_UNIQUE,
            symbol=first,
            name=f"{first}{suffix}",
            old="(no GNU_UNIQUE exports)",
            new=f"{len(added_unique)} GNU_UNIQUE export(s)",
        )
    ]


def _check_ifunc_type_change(sym_name: str, s_old: Any, s_new: Any) -> list[Change]:
    """Detect IFUNC introduction, removal, or generic symbol-type change."""
    if s_old.sym_type != SymbolType.IFUNC and s_new.sym_type == SymbolType.IFUNC:
        return [
            make_change(
                ChangeKind.IFUNC_INTRODUCED,
                symbol=sym_name,
                name=sym_name,
                old_value=s_old.sym_type.value,
                new_value="ifunc",
            )
        ]
    if s_old.sym_type == SymbolType.IFUNC and s_new.sym_type != SymbolType.IFUNC:
        return [
            make_change(
                ChangeKind.IFUNC_REMOVED,
                symbol=sym_name,
                name=sym_name,
                old_value="ifunc",
                new_value=s_new.sym_type.value,
            )
        ]
    if s_old.sym_type != s_new.sym_type:
        return [
            make_change(
                ChangeKind.SYMBOL_TYPE_CHANGED,
                symbol=sym_name,
                name=sym_name,
                old=s_old.sym_type.value,
                new=s_new.sym_type.value,
            )
        ]
    return []


def _check_binding_change(sym_name: str, s_old: Any, s_new: Any) -> list[Change]:
    """Detect symbol binding changes (GLOBAL↔WEAK, GNU_UNIQUE transitions)."""
    if s_old.binding == s_new.binding:
        return []
    # STB_GNU_UNIQUE transitions carry distinct loader semantics (process-wide
    # uniqueness + dlclose inhibition), so route them to dedicated kinds rather
    # than the generic GLOBAL/WEAK strengthen/weaken pair (G23-A4).
    if s_new.binding == SymbolBinding.UNIQUE and s_old.binding != SymbolBinding.UNIQUE:
        kind = ChangeKind.SYMBOL_BINDING_BECAME_UNIQUE
    elif s_old.binding == SymbolBinding.UNIQUE and s_new.binding != SymbolBinding.UNIQUE:
        kind = ChangeKind.SYMBOL_BINDING_LOST_UNIQUE
    else:
        is_weakening = (
            s_old.binding == SymbolBinding.GLOBAL and s_new.binding == SymbolBinding.WEAK
        )
        kind = (
            ChangeKind.SYMBOL_BINDING_CHANGED
            if is_weakening
            else ChangeKind.SYMBOL_BINDING_STRENGTHENED
        )
    return [
        make_change(
            kind,
            symbol=sym_name,
            name=sym_name,
            old=s_old.binding.value,
            new=s_new.binding.value,
        )
    ]


def _check_elf_visibility_change(sym_name: str, s_old: Any, s_new: Any) -> list[Change]:
    """Detect ELF st_other visibility transitions among exported visibilities.

    HIDDEN/INTERNAL transitions are already caught by FUNC_VISIBILITY_CHANGED or
    FUNC_REMOVED (symbol disappears from exported set). Only emit for transitions
    among exported visibilities (DEFAULT↔PROTECTED).
    """
    if s_old.visibility == s_new.visibility:
        return []
    old_vis = s_old.visibility
    new_vis = s_new.visibility
    if old_vis in ("hidden", "internal") or new_vis in ("hidden", "internal"):
        return []
    return [
        make_change(
            ChangeKind.SYMBOL_ELF_VISIBILITY_CHANGED,
            symbol=sym_name,
            name=sym_name,
            old=old_vis,
            new=new_vis,
        )
    ]


def _resolve_size_change_kind(
    old: AbiSnapshot,
    new: AbiSnapshot,
    sym_name: str,
    s_old: Any,
    s_new: Any,
) -> ChangeKind | None:
    """Resolve the ChangeKind for a data-symbol size change, or None to suppress.

    Const string-like objects without a fixed header bound use
    SYMBOL_SIZE_CHANGED_CONST_OBJECT when growing; shrinking is suppressed because
    old consumers already sized from the smaller DSO.  Internal-looking symbols use
    SYMBOL_SIZE_CHANGED_INTERNAL.  All others use SYMBOL_SIZE_CHANGED.
    """
    if _is_const_unbounded_string_object(
        old, sym_name
    ) and _is_const_unbounded_string_object(new, sym_name):
        return (
            None
            if s_new.size <= s_old.size
            else ChangeKind.SYMBOL_SIZE_CHANGED_CONST_OBJECT
        )
    return (
        ChangeKind.SYMBOL_SIZE_CHANGED_INTERNAL
        if _is_internal_data_symbol(sym_name)
        else ChangeKind.SYMBOL_SIZE_CHANGED
    )


def _check_symbol_size_change(
    old: AbiSnapshot,
    new: AbiSnapshot,
    sym_name: str,
    s_old: Any,
    s_new: Any,
) -> list[Change]:
    """Detect exported data-symbol size changes (OBJECT/COMMON/TLS).

    Vtable (_ZTV) and typeinfo (_ZTI) object size changes are owned by
    diff_elf_layout.py, which decodes them into vtable-slot-count /
    inheritance-shape findings; typeinfo-name (_ZTS) size only tracks the
    mangled spelling and is not ABI-meaningful. Skip those here so the
    generic SYMBOL_SIZE_CHANGED does not double-emit. VTT (_ZTT) is NOT
    skipped: it is part of the construction ABI for virtual-base classes
    and has no dedicated detector, so it keeps generic size-change coverage.
    """
    if not (
        s_old.size > 0
        and s_new.size > 0
        and s_old.size != s_new.size
        and s_new.sym_type in (SymbolType.OBJECT, SymbolType.COMMON, SymbolType.TLS)
        and not sym_name.startswith(("_ZTV", "_ZTI", "_ZTS"))
    ):
        return []
    size_kind = _resolve_size_change_kind(old, new, sym_name, s_old, s_new)
    if size_kind is None:
        return []
    return [
        make_change(
            size_kind,
            symbol=sym_name,
            name=sym_name,
            old=str(s_old.size),
            new=str(s_new.size),
        )
    ]


def _check_func_visibility_protected(
    sym_name: str, s_old: Any, s_new: Any
) -> list[Change]:
    """Detect DEFAULT↔PROTECTED visibility changes for function symbols (case51).

    Data symbols with default→protected break copy relocations (real ABI break).
    Only for functions is this safely compatible (interposition semantics change only).
    """
    old_vis = getattr(s_old, "visibility", "default") or "default"
    new_vis = getattr(s_new, "visibility", "default") or "default"
    if old_vis == new_vis or {old_vis, new_vis} != _ELF_VIS_PROTECTED_PAIR:
        return []
    if getattr(s_old, "sym_type", None) != SymbolType.FUNC:
        return []
    return [
        make_change(
            ChangeKind.FUNC_VISIBILITY_PROTECTED_CHANGED,
            symbol=sym_name,
            name=sym_name,
            old=old_vis,
            new=new_vis,
        )
    ]


def _diff_elf_symbol_pair(
    old: AbiSnapshot,
    new: AbiSnapshot,
    sym_name: str,
    s_old: Any,
    s_new: Any,
) -> list[Change]:
    changes: list[Change] = []
    changes.extend(_check_ifunc_type_change(sym_name, s_old, s_new))
    changes.extend(_check_binding_change(sym_name, s_old, s_new))
    changes.extend(_check_elf_visibility_change(sym_name, s_old, s_new))
    changes.extend(_check_symbol_size_change(old, new, sym_name, s_old, s_new))
    changes.extend(_check_func_visibility_protected(sym_name, s_old, s_new))
    return changes
