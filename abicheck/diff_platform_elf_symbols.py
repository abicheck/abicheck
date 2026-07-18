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
from .diff_versioning import (  # noqa: F401 — re-exported for existing callers
    _UNPARSEABLE_VERSION as _UNPARSEABLE_VERSION,
    _parse_abi_version_tag as _parse_abi_version_tag,
)
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


# Canonical version-tag parsing lives in diff_versioning (imported at the top
# of this module); the private names stay re-exported here for existing
# callers/tests.


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
            if ver == "GLIBC_ABI_DT_RELR" and _dt_relr_introduced(old_elf, new_elf):
                # glibc's synthetic marker for packed relative relocations.
                # The dedicated DT_RELR_INTRODUCED finding names the root cause
                # (linker `-z pack-relative-relocs`) and the actual floor
                # (glibc ≥ 2.36); surfacing the marker here as an unparseable
                # version requirement would just re-report it cryptically.
                continue
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
        if not lib_is_new:
            changes.extend(
                _runtime_floor_changes(lib, old_vers, new_vers, new_elf)
            )
    return changes


def _dt_relr_introduced(old_elf: Any, new_elf: Any) -> bool:
    """Deferred import wrapper — see diff_platform_elf_dynamic for semantics."""
    from .diff_platform_elf_dynamic import dt_relr_introduced

    return dt_relr_introduced(old_elf, new_elf)


def _max_parseable_tag(vers: set[str], prefix: str) -> tuple[tuple[int, ...], str]:
    """Return ``(max_version_tuple, its_tag)`` among parseable *prefix* tags.

    Non-numeric tags (``GLIBC_PRIVATE``, ``GLIBC_ABI_DT_RELR``) are excluded —
    they are markers, not deployment floors. ``((0,), "")`` when none parse.
    """
    best: tuple[int, ...] = (0,)
    best_tag = ""
    for v in vers:
        if not v.startswith(prefix + "_"):
            continue
        t = _parse_abi_version_tag(v)
        if t == _UNPARSEABLE_VERSION:
            continue
        if t > best:
            best, best_tag = t, v
    return best, best_tag


def _floor_evidence_symbols(
    new_elf: Any, lib: str, prefix: str, old_max: tuple[int, ...]
) -> list[str]:
    """Imported symbols whose required *prefix* version exceeds *old_max*.

    These are the imports that pulled the deployment floor up — the actionable
    part of the finding (``__libc_start_main`` alone means a pure relink
    artifact; a real API symbol means new runtime functionality is used).
    """
    names: set[str] = set()
    for imp in getattr(new_elf, "imports", []) or []:
        ver = getattr(imp, "version", "") or ""
        if not ver.startswith(prefix + "_"):
            continue
        soname = getattr(imp, "version_soname", "") or ""
        if soname and soname != lib:
            continue
        t = _parse_abi_version_tag(ver)
        if t == _UNPARSEABLE_VERSION or t <= old_max:
            continue
        names.add(f"{getattr(imp, 'name', '')}@{ver}")
    return sorted(names)


def _runtime_floor_changes(
    lib: str, old_vers: set[str], new_vers: set[str], new_elf: Any
) -> list[Change]:
    """Synthesize per-prefix RUNTIME_FLOOR_RAISED headline findings.

    The per-symbol ``SYMBOL_VERSION_REQUIRED_ADDED`` findings enumerate every
    new version node; this rolls them up to the root cause a maintainer acts
    on: "the minimum runtime this binary loads against rose from X to Y" —
    one finding per (provider lib, version-tag prefix), carrying the imported
    symbols that raised the floor as evidence. Emitted alongside (not instead
    of) the per-node findings, mirroring the dual-ABI-flip collapse pattern.
    """
    changes: list[Change] = []
    prefixes = {
        v.rsplit("_", 1)[0] for v in (old_vers | new_vers) if "_" in v
    }
    for prefix in sorted(prefixes):
        old_max, old_tag = _max_parseable_tag(old_vers, prefix)
        new_max, new_tag = _max_parseable_tag(new_vers, prefix)
        # Only a *raise* of an existing floor reports. A prefix appearing from
        # nothing is already covered by the per-node findings, and a lowered
        # floor broadens compatibility.
        if old_max == (0,) or new_max <= old_max:
            continue
        evidence = _floor_evidence_symbols(new_elf, lib, prefix, old_max)
        sample = ", ".join(evidence[:5])
        if len(evidence) > 5:
            sample += f" (+{len(evidence) - 5} more)"
        changes.append(
            make_change(
                ChangeKind.RUNTIME_FLOOR_RAISED,
                symbol=f"{lib}:{prefix}",
                name=sample or "(no import evidence captured)",
                detail=lib,
                old=old_tag,
                new=new_tag,
                affected_symbols=evidence or None,
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
        # Direct-ElfMetadata call convention: the caller handed us a real ELF
        # object, so treat the baseline symbol table as captured.
        old_captured = True
        old = AbiSnapshot(library="", version="")
        new = AbiSnapshot(library="", version="")
    else:
        # Whether the OLD side actually captured an ELF symbol table. `_diff_elf`
        # substitutes an empty ElfMetadata() when old.elf is None (header-only /
        # legacy / parse-failed baseline), which is indistinguishable at the
        # symbol-map level from a real DSO that exports nothing — but only the
        # snapshot knows which. A genuinely-empty captured table proves the
        # absence of prior GNU_UNIQUE exports; an absent one does not.
        old_captured = getattr(old, "elf", None) is not None
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

    changes.extend(
        _check_gained_gnu_unique(old_syms, new_syms, old_captured=old_captured)
    )
    return changes


def _check_gained_gnu_unique(
    old_syms: dict[str, Any],
    new_syms: dict[str, Any],
    *,
    old_captured: bool = True,
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
    if not old_captured:
        # The old side never captured an ELF symbol table (header-only / legacy /
        # parse-failed baseline), so the prior binding is *unknown*, not proven
        # absent. Firing here would flag every pre-existing GNU_UNIQUE export as
        # newly introduced. A genuinely-empty *captured* table (old_captured=True,
        # old_syms empty) does prove absence, so it falls through and reports.
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

    Vtable (_ZTV), typeinfo (_ZTI), and VTT (_ZTT) object size changes are owned
    by diff_elf_layout.py, which decodes them into vtable-slot-count /
    inheritance-shape / construction-scaffolding findings (`vtable_slot_count_changed`,
    `rtti_inheritance_changed`, `vtt_slot_count_changed`); typeinfo-name (_ZTS)
    size only tracks the mangled spelling and is not ABI-meaningful. Skip all
    four here so the generic SYMBOL_SIZE_CHANGED does not double-emit alongside
    the dedicated finding (G23 B1 added the _ZTT detector).
    """
    if not (
        s_old.size > 0
        and s_new.size > 0
        and s_old.size != s_new.size
        and s_new.sym_type in (SymbolType.OBJECT, SymbolType.COMMON, SymbolType.TLS)
        and not sym_name.startswith(("_ZTV", "_ZTI", "_ZTS", "_ZTT"))
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


def _declared_alignment_bits(snap: AbiSnapshot, sym_name: str) -> int | None:
    """Return the DWARF/header-declared alignment (bits) for a variable, if known."""
    var = snap.var_by_mangled(sym_name)
    return var.alignment_bits if var is not None else None


def _check_object_alignment_reduced(
    old: AbiSnapshot, new: AbiSnapshot, sym_name: str, s_old: Any, s_new: Any
) -> list[Change]:
    """Detect exported data objects whose address alignment dropped.

    Alignment is derived from st_value (power-of-two factor, page-capped) at
    parse time; 0 means unknown (legacy snapshot), so both sides must carry a
    positive value. Only data objects matter (copy relocations / aligned data
    access); function alignment is a codegen artifact. COMMON (tentative
    definition) exports are copy-relocation data too — the size detector
    already treats them as data objects, so they are included here. Only the
    reduction direction is a hazard — a stricter alignment satisfies every old
    consumer.

    Compiler-emitted RTTI/vtable objects (_ZTV, _ZTI, _ZTS, _ZTT) are skipped —
    their st_value alignment is a linker-placement artifact of the mangled-name
    string length and neighbouring symbol layout, not a declared data-object
    alignment, so a "reduction" there is noise rather than an ABI hazard. This
    mirrors _check_symbol_size_change, which excludes the same four prefixes as
    not ABI-meaningful (their real shape changes are owned by diff_elf_layout).

    st_value-derived alignment is address-placement evidence, not a declared
    one: adding an unrelated neighbouring global can shift a symbol's link-time
    address (and therefore its apparent low-bit alignment) with no change to
    its actual declared alignment at all. When DWARF/header evidence is
    available on both sides (the variable's ``alignment_bits``), that is
    authoritative — the address-derived drop only stands if the declared
    alignment also decreased; an unchanged or *increased* declaration means
    the drop is placement noise and must be suppressed (a genuine
    declared-alignment change is instead owned by VAR_ALIGNMENT_CHANGED in
    diff_symbols.py). Falls back to the weak address-derived signal only when
    no declared-alignment evidence is available for corroboration (e.g.
    symbols-only / stripped-without-headers snapshots, or -- a known,
    documented gap, see dumper_clang.py's module docstring -- headers parsed
    via the clang backend, which cannot compute a plain variable's natural
    type alignment the way castxml's real compiler output can).
    """
    if s_new.sym_type not in (SymbolType.OBJECT, SymbolType.COMMON, SymbolType.TLS):
        return []
    if sym_name.startswith(("_ZTV", "_ZTI", "_ZTS", "_ZTT")):
        return []
    old_align = getattr(s_old, "value_alignment", 0)
    new_align = getattr(s_new, "value_alignment", 0)
    if not (old_align > 0 and new_align > 0 and new_align < old_align):
        return []
    declared_old = _declared_alignment_bits(old, sym_name)
    declared_new = _declared_alignment_bits(new, sym_name)
    if (
        declared_old is not None
        and declared_new is not None
        and not (declared_new < declared_old)
    ):
        return []
    return [
        make_change(
            ChangeKind.EXPORTED_OBJECT_ALIGNMENT_REDUCED,
            symbol=sym_name,
            name=sym_name,
            old=str(old_align),
            new=str(new_align),
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
    changes.extend(_check_object_alignment_reduced(old, new, sym_name, s_old, s_new))
    return changes


# ── Import-surface and allocator-replacement detectors (coverage extension) ──

#: Itanium manglings of the *global* (non-placement, non-member) operator
#: new/delete family: _Znw/_Zna = operator new/new[], _Zdl/_Zda = delete/
#: delete[] (plain, aligned, nothrow, and sized variants all share these
#: prefixes). Member operators mangle inside their class (_ZN...) and never
#: start with these tokens.
_ALLOCATOR_MANGLING_PREFIXES = ("_Znw", "_Zna", "_Zdl", "_Zda")

#: Placement new/delete forms, which are NOT replaceable global allocation
#: functions — they only serve explicit placement call sites, so their
#: presence flipping is not an allocator-contract change. Placement new is
#: ``operator new(size_t, void*)`` → ``_Zn[wa][jmy]Pv``; placement delete is
#: ``operator delete(void*, void*)`` → ``_Zd[la]Pv{S_|Pv}`` (the second void*
#: is spelled ``S_`` by Itanium substitution, or ``Pv`` unsubstituted). Sized
#: (``_ZdlPvm``), aligned (``…St11align_val_t``) and nothrow delete keep a
#: non-pointer second parameter and are correctly left in the replaceable set.
_PLACEMENT_OPERATOR_RE = re.compile(r"^_Zn[wa][jmy]Pv|^_Zd[la]Pv(?:S_|Pv)")


def _diff_elf_import_set(old_elf: Any, new_elf: Any) -> list[Change]:
    """Diff the undefined-symbol (import) surface of the two binaries.

    A newly-imported symbol is a new obligation on the consumer's link
    environment — if no loaded dependency provides it, the dynamic linker
    fails. Weak imports are skipped in the added direction (they resolve to
    null rather than failing). Gated on captured ELF identity on both sides
    (not on the lists being non-empty): a parsed ELF with zero undefined
    symbols is real evidence of "imports nothing", so gaining a first import
    or dropping the last one must still report; only a header-only or legacy
    baseline (no parsed identity) is skipped.
    """
    from .diff_platform_elf_dynamic import _both_captured_elf_identity

    if not _both_captured_elf_identity(old_elf, new_elf):
        return []
    old_imports = getattr(old_elf, "imports", None) or []
    new_imports = getattr(new_elf, "imports", None) or []

    changes: list[Change] = []
    old_names = {i.name for i in old_imports}
    new_names = {i.name for i in new_imports}
    new_by_name = {i.name: i for i in new_imports}
    old_by_name = {i.name: i for i in old_imports}

    for name in sorted(new_names - old_names):
        imp = new_by_name[name]
        if imp.binding == SymbolBinding.WEAK:
            continue
        version = getattr(imp, "version", "")
        detail = f"@{version}" if version else ""
        changes.append(
            make_change(
                ChangeKind.IMPORTED_SYMBOL_ADDED,
                symbol=name,
                name=name,
                detail=detail,
                new_value=f"{name}{detail}",
            )
        )
    for name in sorted(old_names - new_names):
        version = getattr(old_by_name.get(name), "version", "")
        detail = f"@{version}" if version else ""
        changes.append(
            make_change(
                ChangeKind.IMPORTED_SYMBOL_REMOVED,
                symbol=name,
                name=name,
                detail=detail,
                old_value=f"{name}{detail}",
            )
        )
    # A persisting import that goes weak → strong becomes a new hard obligation:
    # the weak form resolved to null when unsatisfied, the strong form makes the
    # loader fail. The name-only add/remove loops miss it (same name both sides),
    # so surface it as an added import.
    for name in sorted(old_names & new_names):
        old_imp = old_by_name.get(name)
        new_imp = new_by_name.get(name)
        if (
            getattr(old_imp, "binding", None) == SymbolBinding.WEAK
            and getattr(new_imp, "binding", None) != SymbolBinding.WEAK
        ):
            version = getattr(new_imp, "version", "")
            ver = f"@{version}" if version else ""
            changes.append(
                make_change(
                    ChangeKind.IMPORTED_SYMBOL_ADDED,
                    symbol=name,
                    name=name,
                    detail=f"{ver} (weak→strong)",
                    new_value=f"{name}{ver}",
                )
            )
    return changes


def _diff_allocator_replacement(old_elf: Any, new_elf: Any) -> list[Change]:
    """Detect a library starting/stopping to export global operator new/delete.

    These symbols interpose allocation for the whole process, so their
    presence flipping is a loader-level contract change even though the
    symbol-level diff also reports the individual adds/removes. Fires once per
    direction at the library level. Gated on captured ELF identity, not on the
    symbol lists being non-empty — an export table that is empty on one side
    is still evidence about which allocator symbols it (doesn't) export.
    """
    from .diff_platform_elf_dynamic import _both_captured_elf_identity

    if not _both_captured_elf_identity(old_elf, new_elf):
        return []
    old_syms = getattr(old_elf, "symbols", None) or []
    new_syms = getattr(new_elf, "symbols", None) or []
    def _is_global_allocator(name: str) -> bool:
        return name.startswith(_ALLOCATOR_MANGLING_PREFIXES) and not (
            _PLACEMENT_OPERATOR_RE.match(name)
        )

    old_alloc = sorted(s.name for s in old_syms if _is_global_allocator(s.name))
    new_alloc = sorted(s.name for s in new_syms if _is_global_allocator(s.name))
    if bool(old_alloc) == bool(new_alloc):
        return []
    if new_alloc:
        first = new_alloc[0]
        suffix = f" (+{len(new_alloc) - 1} more)" if len(new_alloc) > 1 else ""
        return [
            make_change(
                ChangeKind.ALLOCATOR_REPLACEMENT_ADDED,
                symbol=first,
                detail=f"{first}{suffix}",
                new_value=str(len(new_alloc)),
            )
        ]
    first = old_alloc[0]
    suffix = f" (+{len(old_alloc) - 1} more)" if len(old_alloc) > 1 else ""
    return [
        make_change(
            ChangeKind.ALLOCATOR_REPLACEMENT_REMOVED,
            symbol=first,
            detail=f"{first}{suffix}",
            old_value=str(len(old_alloc)),
        )
    ]
