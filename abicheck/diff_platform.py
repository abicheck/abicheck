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

"Platform-specific ABI diff detectors (ELF, PE, Mach-O, DWARF)."
from __future__ import annotations

import re
from typing import Any

from .checker_policy import ChangeKind
from .checker_types import SYMBOL_VERSION_ALIAS_NOT_RETAINED_MARKER, Change
from .detector_registry import registry
from .diff_platform_templates import (
    _diff_template_inner_types as _diff_template_inner_types,
)
from .diff_platform_templates import (
    _extract_template_args as _extract_template_args,
)
from .diff_platform_templates import (
    _split_top_level_args as _split_top_level_args,
)
from .diff_platform_templates import (
    _template_outer as _template_outer,
)
from .diff_symbols import _public_functions, _should_filter_transitive_runtime_symbols
from .diff_types import _RESERVED_FIELD_RE
from .elf_metadata import SymbolBinding, SymbolType
from .elf_symbol_filter import is_abi_relevant_elf_symbol
from .model import (
    AbiSnapshot,
    Visibility,
    cv_qualifiers_only_differ,
    is_non_abi_surface_type,
    stdlib_namespaces_excluded,
)

# Module-level constant: ELF visibility values that form the default<->protected pair (case51).
_ELF_VIS_PROTECTED_PAIR: frozenset[str] = frozenset({"default", "protected"})


def _pe_export_id(e: Any) -> str:
    """Stable identity for a PE export: its name, or ``ordinal:N`` when nameless.

    Keying nameless (NONAME) exports by ordinal lets the retained-export checks
    still see an ordinal-only forwarder that is silently repointed.
    """
    return e.name if e.name else f"ordinal:{e.ordinal}"


# Data symbol types subject to copy relocations (OBJECT/COMMON).
_COPY_RELOC_TYPES = (SymbolType.OBJECT, SymbolType.COMMON)


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

@registry.detector("elf")
def _diff_elf(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """ELF-only detectors (Sprint 2): no debug info required."""
    from .diff_versioning import (
        detect_version_node_changes,
        detect_version_script_missing,
    )
    from .elf_metadata import ElfMetadata

    o: ElfMetadata = getattr(old, "elf", None) or ElfMetadata()
    n: ElfMetadata = getattr(new, "elf", None) or ElfMetadata()
    changes: list[Change] = []
    changes.extend(_diff_elf_dynamic_section(o, n))
    # Version node graph diff runs before basic version-def diff so that
    # the more specific SYMBOL_VERSION_NODE_REMOVED wins during cross-
    # detector deduplication over the simpler SYMBOL_VERSION_DEFINED_REMOVED.
    changes.extend(detect_version_node_changes(o, n))
    changes.extend(_diff_elf_symbol_versioning(o, n))
    changes.extend(_diff_elf_symbol_metadata(old, new, o, n))
    changes.extend(_diff_visibility_leak(old, new))
    changes.extend(_diff_leaked_dependency_symbols(o, n))
    changes.extend(detect_version_script_missing(o, n))
    return changes


@registry.detector(
    "pe",
    requires_support=lambda o, n: (
        o.pe is not None and n.pe is not None,
        "missing PE metadata",
    ),
)
def _diff_pe(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """PE-specific detectors for Windows DLL ABI changes."""
    from .pe_metadata import PeMetadata

    o: PeMetadata = getattr(old, "pe", None) or PeMetadata()
    n: PeMetadata = getattr(new, "pe", None) or PeMetadata()
    changes: list[Change] = []

    # Export deltas from PE metadata can overlap with _diff_functions() when
    # the same symbols are present in snapshot.functions. Keep PE signal, but
    # deduplicate per symbol so we don't double-report while still preserving
    # metadata-only changes that function model may miss.
    old_ids = {(e.name if e.name else f"ordinal:{e.ordinal}") for e in o.exports}
    new_ids = {(e.name if e.name else f"ordinal:{e.ordinal}") for e in n.exports}
    old_fn_names = {f.name for f in old.functions if f.name}
    new_fn_names = {f.name for f in new.functions if f.name}

    removed_kind = (
        ChangeKind.FUNC_REMOVED_ELF_ONLY
        if getattr(old, "elf_only_mode", False) and getattr(new, "elf_only_mode", False)
        else ChangeKind.FUNC_REMOVED
    )
    for eid in sorted(old_ids - new_ids):
        if eid in old_fn_names:
            continue
        changes.append(Change(
            kind=removed_kind,
            symbol=eid,
            description=f"export removed from DLL: {eid}",
        ))

    for eid in sorted(new_ids - old_ids):
        if eid in new_fn_names:
            continue
        changes.append(Change(
            kind=ChangeKind.FUNC_ADDED,
            symbol=eid,
            description=f"new export in DLL: {eid}",
        ))

    # Ordinal / forwarder stability for exports retained across versions.
    # These are metadata-only signals (the export id — name when present, else
    # ordinal — is unchanged, so the add/remove loops above and _diff_functions()
    # never see them) and are keyed by that same id, so they cannot double-count.
    # Keying by ordinal for nameless exports means an ordinal-only forwarder that
    # is silently repointed to a different target is still caught.
    old_by_id: dict[str, Any] = {}
    for e in o.exports:
        old_by_id.setdefault(_pe_export_id(e), e)
    new_by_id: dict[str, Any] = {}
    for e in n.exports:
        new_by_id.setdefault(_pe_export_id(e), e)

    for eid in sorted(old_by_id.keys() & new_by_id.keys()):
        oe = old_by_id[eid]
        ne = new_by_id[eid]
        label = oe.name or eid
        # NOTE: we deliberately do NOT flag a named export whose ordinal merely
        # shifted. PE ordinals are auto-assigned sequentially, so inserting or
        # removing any export renumbers everything after it — a benign, common
        # occurrence in additive releases. The genuinely breaking case (an
        # ordinal-only / NONAME export bound purely by ordinal) carries no name,
        # so it is keyed by ``ordinal:N`` and a changed ordinal already surfaces
        # as a remove+add above.
        #
        # Forwarder repoint: the export resolves to a different DLL!Symbol target.
        # Applies to both named and ordinal-only exports.
        if oe.forwarder != ne.forwarder and (oe.forwarder or ne.forwarder):
            changes.append(Change(
                kind=ChangeKind.PE_FORWARDER_CHANGED,
                symbol=label,
                old_value=oe.forwarder or "(direct export)",
                new_value=ne.forwarder or "(direct export)",
                description=(
                    f"export '{label}' forwarder changed: "
                    f"{oe.forwarder or '(direct export)'} → "
                    f"{ne.forwarder or '(direct export)'}"
                ),
            ))

    # Architecture drift — a DLL that changes machine type is a different binary
    # contract entirely (e.g. AMD64 → ARM64).
    if o.machine and n.machine and o.machine != n.machine:
        changes.append(Change(
            kind=ChangeKind.PE_MACHINE_CHANGED,
            symbol="PE_HEADER",
            old_value=o.machine,
            new_value=n.machine,
            description=f"PE machine/architecture changed: {o.machine} → {n.machine}",
        ))

    # Detect changed import dependencies
    old_deps = set(o.imports.keys())
    new_deps = set(n.imports.keys())
    for dep in sorted(old_deps - new_deps):
        changes.append(Change(
            kind=ChangeKind.NEEDED_REMOVED,
            symbol=dep,
            description=f"import dependency removed: {dep}",
        ))
    for dep in sorted(new_deps - old_deps):
        changes.append(Change(
            kind=ChangeKind.NEEDED_ADDED,
            symbol=dep,
            description=f"new import dependency: {dep}",
        ))

    return changes


def _diff_macho_exports(
    old: AbiSnapshot, new: AbiSnapshot, o: Any, n: Any,
) -> list[Change]:
    """Compute export-level delta between old and new Mach-O metadata."""
    changes: list[Change] = []
    old_names = {e.name for e in o.exports if e.name}
    new_names = {e.name for e in n.exports if e.name}
    old_fn_names = {f.name for f in old.functions if f.name}
    new_fn_names = {f.name for f in new.functions if f.name}

    removed_kind = (
        ChangeKind.FUNC_REMOVED_ELF_ONLY
        if getattr(old, "elf_only_mode", False) and getattr(new, "elf_only_mode", False)
        else ChangeKind.FUNC_REMOVED
    )
    for name in sorted(old_names - new_names):
        if name in old_fn_names:
            continue
        changes.append(Change(
            kind=removed_kind,
            symbol=name,
            description=f"export removed from dylib: {name}",
        ))

    for name in sorted(new_names - old_names):
        if name in new_fn_names:
            continue
        changes.append(Change(
            kind=ChangeKind.FUNC_ADDED,
            symbol=name,
            description=f"new export in dylib: {name}",
        ))
    return changes


@registry.detector(
    "macho",
    requires_support=lambda o, n: (
        o.macho is not None and n.macho is not None,
        "missing Mach-O metadata",
    ),
)
def _diff_macho(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Mach-O-specific detectors for macOS dylib ABI changes."""
    from .macho_metadata import MachoMetadata

    o: MachoMetadata = getattr(old, "macho", None) or MachoMetadata()
    n: MachoMetadata = getattr(new, "macho", None) or MachoMetadata()
    changes: list[Change] = []

    # Export deltas from Mach-O metadata can overlap with _diff_functions().
    # Deduplicate per symbol to avoid double-reporting, but keep metadata-only
    # changes that function model may miss.
    if o.exports or n.exports:
        changes.extend(_diff_macho_exports(old, new, o, n))

    # Install name change (equivalent of SONAME change)
    if o.install_name != n.install_name and (o.install_name or n.install_name):
        changes.append(Change(
            kind=ChangeKind.SONAME_CHANGED,
            symbol="LC_ID_DYLIB",
            old_value=o.install_name,
            new_value=n.install_name,
            description=f"install name changed: {o.install_name} → {n.install_name}",
        ))

    # Architecture drift — only breaking when an architecture slice that used to
    # ship is GONE. Adding slices (single-arch → universal) keeps old clients
    # loadable, so a superset is not a break. ``cpu_types`` carries every slice
    # of a fat/universal binary; fall back to the single selected ``cpu_type``
    # for snapshots that predate that field. NOTE: that fallback is lossy — an
    # *old* snapshot serialized before ``cpu_types`` existed records only its
    # selected slice, so dropping a non-selected slice of a then-universal
    # binary can go unseen. Re-dumping the old binary restores full detection.
    old_arches = set(getattr(o, "cpu_types", None) or ()) or ({o.cpu_type} if o.cpu_type else set())
    new_arches = set(getattr(n, "cpu_types", None) or ()) or ({n.cpu_type} if n.cpu_type else set())
    removed_arches = old_arches - new_arches
    if old_arches and new_arches and removed_arches:
        changes.append(Change(
            kind=ChangeKind.MACHO_CPU_TYPE_CHANGED,
            symbol="MACHO_HEADER",
            old_value=", ".join(sorted(old_arches)),
            new_value=", ".join(sorted(new_arches)),
            description=(
                "Mach-O architecture slice removed: "
                f"{', '.join(sorted(removed_arches))} no longer present "
                f"({', '.join(sorted(old_arches))} → {', '.join(sorted(new_arches))}); "
                "existing clients of the dropped arch can no longer load the dylib"
            ),
        ))

    # Compatibility version change (LC_ID_DYLIB compat_version — binary contract)
    if o.compat_version != n.compat_version and (o.compat_version or n.compat_version):
        changes.append(Change(
            kind=ChangeKind.COMPAT_VERSION_CHANGED,
            symbol="compat_version",
            old_value=o.compat_version,
            new_value=n.compat_version,
            description=f"compatibility version changed: {o.compat_version} → {n.compat_version}",
        ))

    # Detect dependency changes
    old_deps = set(o.dependent_libs)
    new_deps = set(n.dependent_libs)
    for dep in sorted(old_deps - new_deps):
        changes.append(Change(
            kind=ChangeKind.NEEDED_REMOVED,
            symbol=dep,
            description=f"dependency removed: {dep}",
        ))
    for dep in sorted(new_deps - old_deps):
        changes.append(Change(
            kind=ChangeKind.NEEDED_ADDED,
            symbol=dep,
            description=f"new dependency: {dep}",
        ))

    # Detect re-exported dylib changes (LC_REEXPORT_DYLIB)
    old_reexports = set(o.reexported_libs)
    new_reexports = set(n.reexported_libs)
    for lib in sorted(old_reexports - new_reexports):
        changes.append(Change(
            kind=ChangeKind.NEEDED_REMOVED,
            symbol=lib,
            description=f"re-exported dylib removed: {lib}",
        ))
    for lib in sorted(new_reexports - old_reexports):
        changes.append(Change(
            kind=ChangeKind.NEEDED_ADDED,
            symbol=lib,
            description=f"new re-exported dylib: {lib}",
        ))

    return changes




_INTERNAL_NAME_PATTERNS = (
    "internal",
    "helper",
    "_impl",
    "detail",
    "private",
    "__",
    "_priv",
    "_int_",
    "_do_",
    "_handle_",
)


def _looks_internal(name: str) -> bool:
    """Heuristic: True if symbol name looks like internal implementation detail."""
    lower = name.lower()
    return any(pat in lower for pat in _INTERNAL_NAME_PATTERNS)


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


def _diff_visibility_leak(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect old-library visibility leaks (ELF-only internal symbols exported)."""
    del new  # detector is intentionally old-library-only
    if not getattr(old, "elf_only_mode", False):
        return []

    filter_transitive_runtime_symbols = _should_filter_transitive_runtime_symbols(old)
    leaked = [
        f for f in old.functions
        if (
            f.visibility == Visibility.ELF_ONLY
            and is_abi_relevant_elf_symbol(
                f.name,
                filter_transitive_runtime_symbols=filter_transitive_runtime_symbols,
            )
            and _looks_internal(f.name)
        )
    ]
    if not leaked:
        return []

    names = ", ".join(f.name for f in leaked[:5])
    suffix = f" (+{len(leaked) - 5} more)" if len(leaked) > 5 else ""
    return [Change(
        kind=ChangeKind.VISIBILITY_LEAK,
        symbol="<visibility>",
        description=(
            f"Old library exports {len(leaked)} internal-looking symbol(s) without "
            f"-fvisibility=hidden (bad practice — accidental ABI surface enlargement): "
            f"{names}{suffix}"
        ),
        old_value=str(len(leaked)),
    )]


def _diff_leaked_dependency_symbols(old_elf: Any, new_elf: Any) -> list[Change]:
    """Detect symbols that were added or removed and appear to originate from a dependency.

    When a symbol exported by this library was detected as likely originating from
    a dependency (libstdc++, libgcc, libc, …), any *addition* or *removal* of that
    symbol gets annotated as ``SYMBOL_LEAKED_FROM_DEPENDENCY_CHANGED``.

    Symbols that exist in both old and new with the same origin are intentionally
    **not** re-emitted here — ``_diff_elf_symbol_metadata`` already covers changes
    to the symbol's type/binding/size and emits its own Change records.  Emitting a
    second Change for the same symbol from both detectors would produce contradictory
    messages (one BREAKING, one RISK) for the same event.

    This is a real ABI fact — the library is leaking dependency symbols into its
    public ABI surface — but the verdict is ``COMPATIBLE_WITH_RISK`` rather than
    ``BREAKING``, because direct consumers of this library typically resolve those
    symbols through the dependency directly and are not affected by the leak.

    The risk is that on other systems with a different version of the dependency
    the leaked symbols may differ, causing failures.

    Consider applying ``-fvisibility=hidden`` to prevent this.
    """
    changes: list[Change] = []
    old_syms = old_elf.symbol_map
    new_syms = new_elf.symbol_map

    # Symbols that were *removed* (present in old, absent in new)
    for sym_name, s_old in old_syms.items():
        if sym_name in new_syms:
            # Symbol still exists — skip to avoid double-annotation with
            # _diff_elf_symbol_metadata which handles changed symbols.
            continue
        origin = s_old.origin_lib
        if origin is None:
            continue
        changes.append(Change(
            kind=ChangeKind.SYMBOL_LEAKED_FROM_DEPENDENCY_CHANGED,
            symbol=sym_name,
            description=(
                f"Symbol '{sym_name}' was removed but appears to originate from "
                f"'{origin}' (a dependency of this library). This is a real ABI "
                f"change — the library is leaking dependency symbols into its public "
                f"ABI surface. Consider applying -fvisibility=hidden."
            ),
            old_value=origin,
            new_value=None,
        ))

    # Symbols that were *added* (absent in old, present in new with origin_lib)
    for sym_name, s_new in new_syms.items():
        if sym_name in old_syms:
            continue  # Already present in old — not a pure addition
        if s_new.origin_lib is None:
            continue
        changes.append(Change(
            kind=ChangeKind.SYMBOL_LEAKED_FROM_DEPENDENCY_CHANGED,
            symbol=sym_name,
            description=(
                f"Symbol '{sym_name}' was added but appears to originate from "
                f"'{s_new.origin_lib}' (a dependency of this library). This is a real "
                f"ABI change — the library is leaking dependency symbols into its public "
                f"ABI surface. Consider applying -fvisibility=hidden."
            ),
            old_value=None,
            new_value=s_new.origin_lib,
        ))

    return changes


def _diff_elf_dynamic_section(old_elf: Any, new_elf: Any) -> list[Change]:
    changes: list[Change] = []
    # Emit SONAME_CHANGED only when old library HAD a SONAME (non-empty) and it
    # changed or was removed. Adding a SONAME (empty/None → value) is a compatible
    # improvement and must not be flagged as breaking.
    if old_elf.soname and old_elf.soname != new_elf.soname:
        changes.append(Change(
            kind=ChangeKind.SONAME_CHANGED,
            symbol="DT_SONAME",
            description=f"SONAME changed: {old_elf.soname!r} → {new_elf.soname!r}",
            old_value=old_elf.soname,
            new_value=new_elf.soname,
        ))
    elif not old_elf.soname and new_elf.soname:
        changes.append(Change(
            kind=ChangeKind.SONAME_MISSING,
            symbol="DT_SONAME",
            description=(
                f"Old library has no SONAME (bad practice — packaging/ldconfig will fail); "
                f"new library correctly defines SONAME {new_elf.soname!r}"
            ),
            old_value="",
            new_value=new_elf.soname,
        ))
    changes.extend(_diff_needed_libraries(old_elf.needed, new_elf.needed))
    if old_elf.rpath != new_elf.rpath:
        changes.append(Change(
            kind=ChangeKind.RPATH_CHANGED,
            symbol="DT_RPATH",
            description=f"RPATH changed: {old_elf.rpath!r} → {new_elf.rpath!r}",
            old_value=old_elf.rpath,
            new_value=new_elf.rpath,
        ))
    if old_elf.runpath != new_elf.runpath:
        changes.append(Change(
            kind=ChangeKind.RUNPATH_CHANGED,
            symbol="DT_RUNPATH",
            description=f"RUNPATH changed: {old_elf.runpath!r} → {new_elf.runpath!r}",
            old_value=old_elf.runpath,
            new_value=new_elf.runpath,
        ))

    # PT_GNU_STACK executable stack detection (security bad practice).
    # Report ONLY the regression direction (stack becomes executable); making
    # the stack non-executable is a hardening improvement, not a finding — and
    # emitting it would let the shipped `security` policy fail an improvement.
    old_exec = getattr(old_elf, "has_executable_stack", False)
    new_exec = getattr(new_elf, "has_executable_stack", False)
    if new_exec and not old_exec:
        changes.append(Change(
            kind=ChangeKind.EXECUTABLE_STACK,
            symbol="PT_GNU_STACK",
            description="Executable stack detected: library linked with -Wl,-z,execstack — NX protection disabled (security risk)",
            old_value="RW",
            new_value="RWE",
        ))
    elif old_exec and not new_exec:
        # Improvement direction — a distinct kind so the `security` policy can
        # gate the regression (executable_stack) without failing this fix.
        changes.append(Change(
            kind=ChangeKind.EXECUTABLE_STACK_REMOVED,
            symbol="PT_GNU_STACK",
            description="Executable stack removed: library now uses a non-executable stack — NX protection restored (good practice)",
            old_value="RWE",
            new_value="RW",
        ))

    changes.extend(_diff_security_hardening(old_elf, new_elf))

    return changes


#: RELRO levels ordered weakest → strongest, for regression detection.
_RELRO_RANK: dict[str, int] = {"none": 0, "partial": 1, "full": 2}


def _diff_security_hardening(old_elf: Any, new_elf: Any) -> list[Change]:
    """Detect checksec-style hardening regressions between two ELF snapshots.

    Only *weakening* transitions are reported (a release that improves
    hardening is not a finding). All kinds are RISK by default; the shipped
    ``security`` policy gates them to break.
    """
    changes: list[Change] = []

    old_relro = getattr(old_elf, "relro", "none")
    new_relro = getattr(new_elf, "relro", "none")
    if _RELRO_RANK.get(new_relro, 0) < _RELRO_RANK.get(old_relro, 0):
        changes.append(Change(
            kind=ChangeKind.RELRO_WEAKENED,
            symbol="GNU_RELRO",
            description=f"RELRO weakened: {old_relro} → {new_relro}",
            old_value=old_relro,
            new_value=new_relro,
        ))

    if getattr(old_elf, "is_pie", False) and not getattr(new_elf, "is_pie", False):
        changes.append(Change(
            kind=ChangeKind.PIE_DISABLED,
            symbol="DF_1_PIE",
            description="PIE disabled: executable is no longer position-independent (ASLR defeated)",
            old_value="PIE",
            new_value="no-PIE",
        ))

    if getattr(old_elf, "has_stack_canary", False) and not getattr(new_elf, "has_stack_canary", False):
        changes.append(Change(
            kind=ChangeKind.STACK_CANARY_REMOVED,
            symbol="__stack_chk_fail",
            description="Stack canary removed: -fstack-protector no longer referenced",
            old_value="canary",
            new_value="none",
        ))

    if getattr(old_elf, "has_fortify_source", False) and not getattr(new_elf, "has_fortify_source", False):
        changes.append(Change(
            kind=ChangeKind.FORTIFY_SOURCE_WEAKENED,
            symbol="_FORTIFY_SOURCE",
            description="FORTIFY_SOURCE weakened: fortified libc wrappers (*_chk) no longer referenced",
            old_value="fortified",
            new_value="none",
        ))

    if not getattr(old_elf, "has_writable_executable_segment", False) and getattr(new_elf, "has_writable_executable_segment", False):
        changes.append(Change(
            kind=ChangeKind.WRITABLE_EXECUTABLE_SEGMENT,
            symbol="PT_LOAD",
            description="Writable + executable segment introduced (W^X violation)",
            old_value="W^X",
            new_value="W+X",
        ))

    return changes


def _diff_needed_libraries(old_needed: list[str], new_needed: list[str]) -> list[Change]:
    changes: list[Change] = []
    old_set = set(old_needed)
    new_set = set(new_needed)
    for lib in sorted(new_set - old_set):
        changes.append(Change(
            kind=ChangeKind.NEEDED_ADDED,
            symbol="DT_NEEDED",
            description=f"New dependency added: {lib}",
            new_value=lib,
        ))
    for lib in sorted(old_set - new_set):
        changes.append(Change(
            kind=ChangeKind.NEEDED_REMOVED,
            symbol="DT_NEEDED",
            description=f"Dependency removed: {lib}",
            old_value=lib,
        ))
    return changes


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
        changes.append(Change(
            kind=ChangeKind.SYMBOL_VERSION_DEFINED_REMOVED,
            symbol=ver,
            description=f"Symbol version removed: {ver}",
            old_value=ver,
        ))
    for ver in sorted(new_def - old_def):
        changes.append(Change(
            kind=ChangeKind.SYMBOL_VERSION_DEFINED_ADDED,
            symbol=ver,
            description=f"Symbol version definition added: {ver}",
            new_value=ver,
        ))

    all_req_libs = set(old_elf.versions_required) | set(new_elf.versions_required)
    for lib in sorted(all_req_libs):
        old_vers = set(old_elf.versions_required.get(lib, []))
        new_vers = set(new_elf.versions_required.get(lib, []))
        # The old maximum requirement for this lib — anything added that
        # is *older* than this maximum is not a new constraint on the caller.
        # If the lib is entirely new (not in old at all), its version
        # requirements are already captured by needed_added → COMPATIBLE.
        lib_is_new = lib not in old_elf.versions_required and lib not in getattr(old_elf, "needed", [])

        # Compute old max PER VERSION-TAG PREFIX (e.g. "GLIBC", "GLIBCXX", "CXXABI")
        # to avoid cross-namespace bleed: GLIBCXX_3.4.32 must not suppress a
        # genuinely newer CXXABI_1.3.14 requirement.
        def _old_max_for_prefix(prefix: str, _old_vers: set[str] = old_vers) -> tuple[int, ...]:  # pylint: disable=dangerous-default-value
            matching = [_parse_abi_version_tag(v) for v in _old_vers
                        if v.startswith(prefix + "_")]
            return max(matching, default=(0,))

        for ver in sorted(new_vers - old_vers):
            ver_tuple = _parse_abi_version_tag(ver)
            prefix = ver.rsplit("_", 1)[0] if "_" in ver else ver
            old_max = _old_max_for_prefix(prefix)
            if lib_is_new or ver_tuple <= old_max:
                # Either the whole lib is new (covered by needed_added), or the
                # added requirement is not newer than the old max — COMPATIBLE.
                changes.append(Change(
                    kind=ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED_COMPAT,
                    symbol=ver,
                    description=(
                        f"New symbol version requirement: {ver} (from {lib})"
                        f" — not newer than previous max, backward-compatible"
                    ),
                    new_value=f"{lib}:{ver}",
                ))
            else:
                # Genuinely newer requirement — callers on older runtimes will fail.
                changes.append(Change(
                    kind=ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED,
                    symbol=ver,
                    description=f"New symbol version requirement: {ver} (from {lib})",
                    new_value=f"{lib}:{ver}",
                ))
        for ver in sorted(old_vers - new_vers):
            changes.append(Change(
                kind=ChangeKind.SYMBOL_VERSION_REQUIRED_REMOVED,
                symbol=ver,
                description=f"Symbol version requirement removed: {ver} (from {lib})",
                old_value=f"{lib}:{ver}",
            ))
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
            changes.append(Change(
                kind=ChangeKind.COMMON_SYMBOL_RISK,
                symbol=sym_name,
                description=f"Exported STT_COMMON symbol: {sym_name} (resolution depends on linker/loader)",
            ))
    return changes


def _diff_elf_symbol_pair(
    old: AbiSnapshot,
    new: AbiSnapshot,
    sym_name: str,
    s_old: Any,
    s_new: Any,
) -> list[Change]:
    changes: list[Change] = []
    if s_old.sym_type != SymbolType.IFUNC and s_new.sym_type == SymbolType.IFUNC:
        changes.append(Change(
            kind=ChangeKind.IFUNC_INTRODUCED,
            symbol=sym_name,
            description=f"Symbol became GNU_IFUNC: {sym_name}",
            old_value=s_old.sym_type.value,
            new_value="ifunc",
        ))
    elif s_old.sym_type == SymbolType.IFUNC and s_new.sym_type != SymbolType.IFUNC:
        changes.append(Change(
            kind=ChangeKind.IFUNC_REMOVED,
            symbol=sym_name,
            description=f"Symbol no longer GNU_IFUNC: {sym_name}",
            old_value="ifunc",
            new_value=s_new.sym_type.value,
        ))
    elif s_old.sym_type != s_new.sym_type:
        changes.append(Change(
            kind=ChangeKind.SYMBOL_TYPE_CHANGED,
            symbol=sym_name,
            description=f"Symbol type changed: {sym_name} ({s_old.sym_type.value} → {s_new.sym_type.value})",
            old_value=s_old.sym_type.value,
            new_value=s_new.sym_type.value,
        ))

    if s_old.binding != s_new.binding:
        is_weakening = s_old.binding == SymbolBinding.GLOBAL and s_new.binding == SymbolBinding.WEAK
        kind = ChangeKind.SYMBOL_BINDING_CHANGED if is_weakening else ChangeKind.SYMBOL_BINDING_STRENGTHENED
        changes.append(Change(
            kind=kind,
            symbol=sym_name,
            description=f"Symbol binding changed: {sym_name} ({s_old.binding.value} → {s_new.binding.value})",
            old_value=s_old.binding.value,
            new_value=s_new.binding.value,
        ))

    # ELF st_other visibility transition (DEFAULT↔PROTECTED↔HIDDEN↔INTERNAL)
    if s_old.visibility != s_new.visibility:
        old_vis = s_old.visibility
        new_vis = s_new.visibility
        # HIDDEN/INTERNAL transitions are already caught by FUNC_VISIBILITY_CHANGED
        # or FUNC_REMOVED (symbol disappears from exported set). Only emit for
        # transitions among exported visibilities (DEFAULT↔PROTECTED).
        if old_vis not in ("hidden", "internal") and new_vis not in ("hidden", "internal"):
            changes.append(Change(
                kind=ChangeKind.SYMBOL_ELF_VISIBILITY_CHANGED,
                symbol=sym_name,
                description=f"ELF visibility changed: {sym_name} ({old_vis} → {new_vis})",
                old_value=old_vis,
                new_value=new_vis,
            ))

    if (
        s_old.size > 0
        and s_new.size > 0
        and s_old.size != s_new.size
        and s_new.sym_type in (SymbolType.OBJECT, SymbolType.COMMON, SymbolType.TLS)
        # Vtable (_ZTV) and typeinfo (_ZTI) object size changes are owned by
        # diff_elf_layout.py, which decodes them into vtable-slot-count /
        # inheritance-shape findings; typeinfo-name (_ZTS) size only tracks the
        # mangled spelling and is not ABI-meaningful. Skip those here so the
        # generic SYMBOL_SIZE_CHANGED does not double-emit. VTT (_ZTT) is NOT
        # skipped: it is part of the construction ABI for virtual-base classes
        # and has no dedicated detector, so it keeps generic size-change coverage.
        and not sym_name.startswith(("_ZTV", "_ZTI", "_ZTS"))
    ):
        # Use a distinct kind for internal-looking (reserved/underscore-prefixed)
        # exported data symbols so policy files can target them, but keep the
        # default severity breaking: exported OBJECT/COMMON/TLS size changes can
        # break copy relocations or direct data consumers regardless of spelling.
        # Const string-like objects without a fixed header bound get their own
        # kind (SYMBOL_SIZE_CHANGED_CONST_OBJECT) when growing (shrinking is
        # filtered because old consumers already sized from the smaller DSO).
        if (
            _is_const_unbounded_string_object(old, sym_name)
            and _is_const_unbounded_string_object(new, sym_name)
        ):
            if s_new.size <= s_old.size:
                size_kind = None
            else:
                size_kind = ChangeKind.SYMBOL_SIZE_CHANGED_CONST_OBJECT
        else:
            size_kind = (
                ChangeKind.SYMBOL_SIZE_CHANGED_INTERNAL
                if _is_internal_data_symbol(sym_name)
                else ChangeKind.SYMBOL_SIZE_CHANGED
            )
        if size_kind is not None:
            changes.append(Change(
                kind=size_kind,
                symbol=sym_name,
                description=f"Symbol size changed: {sym_name} ({s_old.size} → {s_new.size} bytes)",
                old_value=str(s_old.size),
                new_value=str(s_new.size),
            ))

    # case51: ELF visibility default→protected (or vice-versa) — function symbols only.
    # Data symbols with default→protected break copy relocations (real ABI break).
    # Only for functions is this safely compatible (interposition semantics change only).
    old_vis = getattr(s_old, "visibility", "default") or "default"
    new_vis = getattr(s_new, "visibility", "default") or "default"
    if (
        old_vis != new_vis
        and {old_vis, new_vis} == _ELF_VIS_PROTECTED_PAIR
        and getattr(s_old, "sym_type", None) == SymbolType.FUNC
    ):
        changes.append(Change(
            kind=ChangeKind.FUNC_VISIBILITY_PROTECTED_CHANGED,
            symbol=sym_name,
            description=(
                f"ELF symbol visibility changed: {sym_name} "
                f"({old_vis} → {new_vis}); symbol still exported, "
                f"interposition semantics changed"
            ),
            old_value=old_vis,
            new_value=new_vis,
        ))

    return changes


# ── Gap analysis: new ELF-level detectors ─────────────────────────────────────

@registry.detector("tls_checks")
def _diff_tls_symbols(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect size changes for exported TLS (thread-local) symbols."""
    from .elf_metadata import ElfMetadata

    o: ElfMetadata = getattr(old, "elf", None) or ElfMetadata()
    n: ElfMetadata = getattr(new, "elf", None) or ElfMetadata()
    changes: list[Change] = []

    old_syms = o.symbol_map
    new_syms = n.symbol_map

    for sym_name, s_old in old_syms.items():
        if s_old.sym_type != SymbolType.TLS:
            continue
        s_new = new_syms.get(sym_name)
        if s_new is None or s_new.sym_type != SymbolType.TLS:
            continue
        if s_old.size > 0 and s_new.size > 0 and s_old.size != s_new.size:
            changes.append(Change(
                kind=ChangeKind.TLS_VAR_SIZE_CHANGED,
                symbol=sym_name,
                description=(
                    f"TLS variable size changed: {sym_name} "
                    f"({s_old.size} → {s_new.size} bytes)"
                ),
                old_value=str(s_old.size),
                new_value=str(s_new.size),
            ))

    return changes


@registry.detector("protected_visibility")
def _diff_protected_visibility(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect DEFAULT ↔ PROTECTED visibility changes for non-function symbols.

    Function DEFAULT↔PROTECTED is already handled by func_visibility_protected_changed.
    This detector covers data/object symbols where the change can break copy relocations.
    """
    from .elf_metadata import ElfMetadata

    o: ElfMetadata = getattr(old, "elf", None) or ElfMetadata()
    n: ElfMetadata = getattr(new, "elf", None) or ElfMetadata()
    changes: list[Change] = []

    for sym_name, s_old in o.symbol_map.items():
        s_new = n.symbol_map.get(sym_name)
        if s_new is None:
            continue
        old_vis = s_old.visibility or "default"
        new_vis = s_new.visibility or "default"
        if old_vis == new_vis:
            continue
        if {old_vis, new_vis} != _ELF_VIS_PROTECTED_PAIR:
            continue
        # Only report for actual data symbols (OBJECT/COMMON) where copy
        # relocations are a concern.  Function symbols are already covered by
        # func_visibility_protected_changed; TLS/IFUNC/other types don't use
        # copy relocations, so DEFAULT↔PROTECTED is benign for them.
        if s_old.sym_type not in _COPY_RELOC_TYPES or s_new.sym_type not in _COPY_RELOC_TYPES:
            continue
        changes.append(Change(
            kind=ChangeKind.PROTECTED_VISIBILITY_CHANGED,
            symbol=sym_name,
            description=(
                f"Data symbol visibility changed: {sym_name} "
                f"({old_vis} → {new_vis}); may break copy relocations"
            ),
            old_value=old_vis,
            new_value=new_vis,
        ))

    return changes


@registry.detector("symbol_version_alias")
def _diff_symbol_version_aliases(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect default symbol version alias changes.

    When a symbol's default version changes (e.g. foo@@VER_1.0 → foo@@VER_2.0)
    without retaining the old version as a non-default alias, old binaries
    requesting the previous default may fail.
    """
    from .elf_metadata import ElfMetadata

    o: ElfMetadata = getattr(old, "elf", None) or ElfMetadata()
    n: ElfMetadata = getattr(new, "elf", None) or ElfMetadata()
    changes: list[Change] = []

    # Build maps of symbol_name → (version, is_default) for versioned symbols
    old_default_ver: dict[str, str] = {}
    new_default_ver: dict[str, str] = {}
    new_all_vers: dict[str, set[str]] = {}

    for s in o.symbols:
        if s.version and s.is_default:
            old_default_ver[s.name] = s.version
    for s in n.symbols:
        if s.version:
            new_all_vers.setdefault(s.name, set()).add(s.version)
            if s.is_default:
                new_default_ver[s.name] = s.version

    for sym_name, old_ver in old_default_ver.items():
        new_ver = new_default_ver.get(sym_name)
        if new_ver is None or new_ver == old_ver:
            continue
        # Default version changed — check if old version is retained as alias
        retained = old_ver in new_all_vers.get(sym_name, set())
        desc = (
            f"Default symbol version changed: {sym_name} "
            f"(@@{old_ver} → @@{new_ver})"
        )
        if not retained:
            desc += f" — {SYMBOL_VERSION_ALIAS_NOT_RETAINED_MARKER}"
        changes.append(Change(
            kind=ChangeKind.SYMBOL_VERSION_ALIAS_CHANGED,
            symbol=sym_name,
            description=desc,
            old_value=old_ver,
            new_value=new_ver,
        ))

    return changes


@registry.detector("glibcxx_dual_abi")
def _diff_glibcxx_dual_abi(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect mass symbol churn caused by libstdc++ dual ABI toggles.

    When _GLIBCXX_USE_CXX11_ABI is flipped, symbols containing std::string
    and std::list change their mangling (e.g. std::__cxx11::basic_string vs
    std::basic_string). This detector identifies this pattern and emits a
    single diagnostic instead of hundreds of individual add/remove reports.
    """
    changes: list[Change] = []
    old_map = {f.mangled: f for f in old.functions if f.visibility in (Visibility.PUBLIC, Visibility.ELF_ONLY)}
    new_map = {f.mangled: f for f in new.functions if f.visibility in (Visibility.PUBLIC, Visibility.ELF_ONLY)}

    removed = set(old_map.keys()) - set(new_map.keys())
    added = set(new_map.keys()) - set(old_map.keys())

    if len(removed) < 5 or len(added) < 5:
        return changes

    # Detect dual ABI markers in removed/added symbols
    _CXX11_ABI_MARKERS = ("__cxx11", "cxx11")
    removed_with_marker = sum(1 for s in removed if any(m in s for m in _CXX11_ABI_MARKERS))
    added_with_marker = sum(1 for s in added if any(m in s for m in _CXX11_ABI_MARKERS))

    # Pattern 1: Old has __cxx11 symbols, new doesn't (ABI=1 → ABI=0)
    # Pattern 2: Old lacks __cxx11, new has them (ABI=0 → ABI=1)
    total_churn = len(removed) + len(added)
    marker_churn = removed_with_marker + added_with_marker

    if marker_churn > 0 and marker_churn >= total_churn * 0.3:
        direction = (
            "CXX11 ABI → legacy ABI"
            if removed_with_marker > added_with_marker
            else "legacy ABI → CXX11 ABI"
        )
        changes.append(Change(
            kind=ChangeKind.GLIBCXX_DUAL_ABI_FLIP_DETECTED,
            symbol="__glibcxx_dual_abi",
            description=(
                f"libstdc++ dual ABI flip detected ({direction}): "
                f"{marker_churn} of {total_churn} churned symbols contain "
                f"CXX11 ABI markers; likely caused by _GLIBCXX_USE_CXX11_ABI toggle"
            ),
            old_value=f"{removed_with_marker} removed with marker",
            new_value=f"{added_with_marker} added with marker",
        ))

    return changes


@registry.detector("inline_namespace")
def _diff_inline_namespace(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect symbols that moved between inline namespaces (e.g. v1:: → v2::).

    Uses demangled function names to identify namespace-only changes where the
    function signature is otherwise identical.
    """
    import re

    changes: list[Change] = []
    old_map = {f.mangled: f for f in old.functions if f.visibility in (Visibility.PUBLIC, Visibility.ELF_ONLY)}
    new_map = {f.mangled: f for f in new.functions if f.visibility in (Visibility.PUBLIC, Visibility.ELF_ONLY)}

    removed = set(old_map.keys()) - set(new_map.keys())
    added = set(new_map.keys()) - set(old_map.keys())

    if not removed or not added:
        return changes

    # Build lookup by demangled name with versioned namespace stripped.
    # Matches Itanium-style ::v1::, ::__v2:: AND libc++-style ::__1::, ::__2::
    # Anchored to :: on both sides to avoid matching inside identifiers.
    _INLINE_NS_RE = re.compile(r'::(?:__)?(?:v)?\d+::')

    from .demangle import demangle_batch

    # In elf_only mode Function.name may still be mangled; demangle in batch to
    # make namespace-move detection robust across dump modes.
    _all_mangled = [m for m in (removed | added) if m.startswith("_Z")]
    _demangled = demangle_batch(_all_mangled)

    def _func_name_for_matching(mangled: str, func_name: str) -> str:
        if "::" in func_name:
            return func_name
        return _demangled.get(mangled, func_name)

    def _strip_inline_ns(name: str) -> str:
        return _INLINE_NS_RE.sub("::", name)

    # Index ALL removed symbols by stripped name (not just those with a
    # namespace match) so that unversioned→versioned moves are caught too.
    removed_by_stripped: dict[str, list[str]] = {}
    for m in removed:
        f = old_map[m]
        match_name = _func_name_for_matching(m, f.name)
        stripped = _strip_inline_ns(match_name)
        removed_by_stripped.setdefault(stripped, []).append(m)

    matched_count = 0
    for m in added:
        f = new_map[m]
        new_name = _func_name_for_matching(m, f.name)
        stripped = _strip_inline_ns(new_name)
        if stripped in removed_by_stripped:
            # Only count as a move if at least one side had an inline namespace
            old_m = removed_by_stripped[stripped][0]
            old_name = _func_name_for_matching(old_m, old_map[old_m].name)
            if stripped != new_name or stripped != old_name:
                matched_count += 1

    # Only emit if we find a pattern of namespace-version moves (2+ symbols)
    if matched_count >= 2:
        changes.append(Change(
            kind=ChangeKind.INLINE_NAMESPACE_MOVED,
            symbol="__inline_namespace_move",
            description=(
                f"Inline namespace move detected: {matched_count} symbols "
                f"appear to have moved between inline namespace versions "
                f"(e.g. ::v1:: → ::v2::); mangled names changed"
            ),
            old_value=f"{matched_count} symbols in old namespace",
            new_value=f"{matched_count} symbols in new namespace",
        ))

    return changes


@registry.detector("vtable_identity")
def _diff_vtable_identity(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect vtable/typeinfo symbol identity changes while class layout is stable.

    When visibility or version-script rules change, vtable and typeinfo symbols
    may get different mangled names or versions even though the class layout
    hasn't changed. This breaks cross-DSO RTTI and exception handling.
    """
    from .elf_metadata import ElfMetadata

    o: ElfMetadata = getattr(old, "elf", None) or ElfMetadata()
    n: ElfMetadata = getattr(new, "elf", None) or ElfMetadata()
    changes: list[Change] = []

    # Find vtable/typeinfo symbols by mangling convention (_ZTV, _ZTI, _ZTS)
    _RTTI_PREFIXES = ("_ZTV", "_ZTI", "_ZTS")

    old_filter_transitive_runtime_symbols = _should_filter_transitive_runtime_symbols(old)
    new_filter_transitive_runtime_symbols = _should_filter_transitive_runtime_symbols(new)
    old_rtti = {
        s.name for s in o.symbols
        if any(s.name.startswith(p) for p in _RTTI_PREFIXES)
        and is_abi_relevant_elf_symbol(
            s.name,
            filter_transitive_runtime_symbols=old_filter_transitive_runtime_symbols,
        )
    }
    new_rtti = {
        s.name for s in n.symbols
        if any(s.name.startswith(p) for p in _RTTI_PREFIXES)
        and is_abi_relevant_elf_symbol(
            s.name,
            filter_transitive_runtime_symbols=new_filter_transitive_runtime_symbols,
        )
    }

    removed_rtti = old_rtti - new_rtti
    added_rtti = new_rtti - old_rtti
    common_rtti = old_rtti & new_rtti

    if not removed_rtti and not added_rtti and not common_rtti:
        return changes

    # Use compound (prefix, type_hash) keys so _ZTV and _ZTI for the same
    # type are tracked independently — they are different RTTI artefacts.
    def _rtti_key(sym: str) -> tuple[str, str]:
        for p in _RTTI_PREFIXES:
            if sym.startswith(p):
                return (p, sym[len(p):])
        return ("", sym)

    removed_keys = {_rtti_key(s) for s in removed_rtti}
    added_keys = {_rtti_key(s) for s in added_rtti}

    # Same (prefix, type_hash) in both removed and added → identity changed
    # (e.g. _ZTVFoo@@V1 removed, _ZTVFoo@@V2 added — same prefix + type)
    identity_changed = removed_keys & added_keys if (removed_rtti and added_rtti) else set()
    if identity_changed:
        for rkey in sorted(identity_changed):
            prefix, type_hash = rkey
            old_sym = prefix + type_hash
            new_sym = prefix + type_hash  # same name, but different properties
            # Reconstruct from actual removed/added sets for accuracy
            actual_old = next((s for s in removed_rtti if _rtti_key(s) == rkey), old_sym)
            actual_new = next((s for s in added_rtti if _rtti_key(s) == rkey), new_sym)
            changes.append(Change(
                kind=ChangeKind.VTABLE_SYMBOL_IDENTITY_CHANGED,
                symbol=actual_old,
                description=(
                    f"RTTI/vtable symbol identity changed: {actual_old} → {actual_new}; "
                    f"may break cross-DSO RTTI and exception handling"
                ),
                old_value=actual_old,
                new_value=actual_new,
            ))

    # Also check existing RTTI symbols for visibility or version changes
    if common_rtti:
        for sym_name in common_rtti:
            s_old = o.symbol_map.get(sym_name)
            s_new = n.symbol_map.get(sym_name)
            if not s_old or not s_new:
                continue
            old_vis = s_old.visibility or "default"
            new_vis = s_new.visibility or "default"
            vis_changed = old_vis != new_vis
            ver_changed = (s_old.version != s_new.version) or (s_old.is_default != s_new.is_default)
            if vis_changed or ver_changed:
                detail_parts = []
                if vis_changed:
                    detail_parts.append(f"visibility {old_vis} → {new_vis}")
                if ver_changed:
                    old_v = s_old.version or "(none)"
                    new_v = s_new.version or "(none)"
                    detail_parts.append(f"version {old_v} → {new_v}")
                detail = ", ".join(detail_parts)
                changes.append(Change(
                    kind=ChangeKind.VTABLE_SYMBOL_IDENTITY_CHANGED,
                    symbol=sym_name,
                    description=(
                        f"RTTI/vtable symbol changed: {sym_name} "
                        f"({detail}); may break cross-DSO RTTI"
                    ),
                    old_value=old_vis if vis_changed else (s_old.version or "(none)"),
                    new_value=new_vis if vis_changed else (s_new.version or "(none)"),
                ))

    return changes


@registry.detector("abi_surface")
def _diff_abi_surface(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect dramatic ABI surface growth or shrinkage.

    A large increase in exported symbols may indicate a lost -fvisibility=hidden.
    A large decrease may indicate an overly aggressive version script.
    """
    from .elf_metadata import ElfMetadata

    o: ElfMetadata = getattr(old, "elf", None) or ElfMetadata()
    n: ElfMetadata = getattr(new, "elf", None) or ElfMetadata()
    changes: list[Change] = []

    old_count = len(o.symbols)
    new_count = len(n.symbols)

    if old_count < 10:
        return changes  # too few symbols to judge

    ratio = new_count / old_count if old_count > 0 else 0
    delta = new_count - old_count

    # Thresholds: >2x growth or <0.5x shrinkage with at least 50 symbol delta
    if abs(delta) >= 50 and (ratio > 2.0 or ratio < 0.5):
        direction = "grew" if delta > 0 else "shrank"
        changes.append(Change(
            kind=ChangeKind.ABI_SURFACE_EXPLOSION,
            symbol="__abi_surface",
            description=(
                f"ABI surface {direction} dramatically: "
                f"{old_count} → {new_count} exported symbols "
                f"({ratio:.1f}x); check -fvisibility=hidden and version scripts"
            ),
            old_value=str(old_count),
            new_value=str(new_count),
        ))

    return changes


# ── Sprint 3: DWARF-aware layout diff ────────────────────────────────────────

@registry.detector("dwarf")
def _diff_dwarf(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """DWARF-aware struct/enum layout detectors (Sprint 3).

    Requires binaries compiled with -g.

    Graceful degradation rules:
    - Neither side has DWARF → skip silently (no false positives)
    - Old has DWARF, new is stripped → emit DWARF_INFO_MISSING warning change
      so callers know the comparison is incomplete (not silently COMPATIBLE)
    - Only new has DWARF → can't compare without old baseline → skip

    Important: we diff only ABI-reachable types/enums discovered from the
    header model (castxml layer). This avoids flagging private implementation
    types present in DWARF but not in the public API surface.
    """
    import logging as _logging

    from .dwarf_metadata import DwarfMetadata

    _log = _logging.getLogger(__name__)

    o: DwarfMetadata = getattr(old, "dwarf", None) or DwarfMetadata()
    n: DwarfMetadata = getattr(new, "dwarf", None) or DwarfMetadata()

    if not o.has_dwarf and not n.has_dwarf:
        return []  # neither side has DWARF — nothing to compare

    if o.has_dwarf and not n.has_dwarf:
        _log.warning(
            "DWARF layout comparison skipped: new binary has no debug info. "
            "Recompile with -g to enable struct/enum ABI checks."
        )
        return [Change(
            kind=ChangeKind.DWARF_INFO_MISSING,
            symbol="<dwarf>",
            description=(
                "New binary has no DWARF debug info — struct/enum layout "
                "comparison was skipped. Recompile with -g to enable."
            ),
        )]

    def _allow_name(name: str, allowed: set[str]) -> bool:
        # Match by full name or by unqualified name (last component after ::)
        return name in allowed or name.split("::")[-1] in allowed

    # Collect opaque (forward-declared only) struct names from each side.
    # If a struct is opaque in *both* snapshots, its layout is not part of
    # the public ABI — callers never see the fields — so DWARF layout
    # changes should be suppressed.
    old_opaque = {t.name for t in old.types if getattr(t, "is_opaque", False)}
    new_opaque = {t.name for t in new.types if getattr(t, "is_opaque", False)}
    both_opaque = old_opaque & new_opaque

    allowed_structs: set[str] = (
        {t.name for t in old.types} | {t.name for t in new.types}
    ) - both_opaque
    allowed_enums: set[str] = {
        e.name for e in old.enums
    } | {
        e.name for e in new.enums
    }

    # If the header model is absent (no castxml data), fall back to comparing
    # all DWARF types — this preserves compatibility when running DWARF-only.
    if allowed_structs:
        o_structs = {k: v for k, v in o.structs.items() if _allow_name(k, allowed_structs)}
        n_structs = {k: v for k, v in n.structs.items() if _allow_name(k, allowed_structs)}
    else:
        o_structs = o.structs
        n_structs = n.structs

    if allowed_enums:
        o_enums = {k: v for k, v in o.enums.items() if _allow_name(k, allowed_enums)}
        n_enums = {k: v for k, v in n.enums.items() if _allow_name(k, allowed_enums)}
    else:
        o_enums = o.enums
        n_enums = n.enums

    # Drop non-ABI types from the DWARF layout maps. The header-scoped branch
    # above only filters when a castxml model is present; in DWARF-only mode (no
    # headers) it falls back to ALL DWARF types, which leaks
    # std::/__gnu_cxx::/<lambda>/compiler-internal records into the struct & enum
    # layout detectors (STRUCT_FIELD_REMOVED etc.). This is the same surface gate
    # the type differ uses (model.stdlib_namespaces_excluded), so every detector
    # that consumes DWARF types agrees on what counts as ABI surface.
    #
    # The filter ALWAYS runs: when the inspected DSO *is* the C++ runtime,
    # ``excl`` is False so std::/__gnu_cxx:: records are kept (they ARE its
    # surface), but anonymous/lambda and compiler-internal types are still
    # dropped — those are never stable ABI even for the runtime itself.
    excl = stdlib_namespaces_excluded(old, new)
    o_structs = {k: v for k, v in o_structs.items() if not is_non_abi_surface_type(k, exclude_stdlib_namespaces=excl)}
    n_structs = {k: v for k, v in n_structs.items() if not is_non_abi_surface_type(k, exclude_stdlib_namespaces=excl)}
    o_enums = {k: v for k, v in o_enums.items() if not is_non_abi_surface_type(k, exclude_stdlib_namespaces=excl)}
    n_enums = {k: v for k, v in n_enums.items() if not is_non_abi_surface_type(k, exclude_stdlib_namespaces=excl)}

    filtered_old = DwarfMetadata(structs=o_structs, enums=o_enums, has_dwarf=o.has_dwarf)
    filtered_new = DwarfMetadata(structs=n_structs, enums=n_enums, has_dwarf=n.has_dwarf)

    changes: list[Change] = []
    changes.extend(_diff_struct_layouts(filtered_old, filtered_new))
    changes.extend(_diff_enum_layouts(filtered_old, filtered_new))
    return changes


# Synthesized placeholder names for anonymous/unnamed aggregate member types,
# which differ across DWARF / castxml / PDB readers (``<unnamed-tag>``,
# ``<unnamed-type-u>``, ``<anonymous union>``, ``<unnamed struct at …>``, …).
# The aggregate *kind* (when the placeholder names one) is captured so a real
# union→struct change is preserved while the unstable identifier suffix is not.
_ANON_TYPE_RE = re.compile(
    r"<\s*(?:unnamed|anonymous)(?:\s+(union|struct|class|enum)\b)?", re.IGNORECASE
)


def _normalize_type_name(name: str) -> str:
    """Normalize a C/C++ type name for stable DWARF↔castxml comparison.

    Strips leading/trailing whitespace, CV-qualifiers, pointer/reference
    decorations, and 'struct'/'class'/'union' tag keywords so that semantically
    equivalent names compare equal regardless of DWARF vs castxml source:

    Examples::

        "struct Foo"     → "Foo"
        "const struct Foo *" → "Foo"
        "class Bar &"    → "Bar"
        "union U"        → "U"
        "int"            → "int"   (unchanged)

    Note: this normalizer is intentionally lossy for comparison purposes only.
    The original type names are still preserved in Change.old_value/new_value.
    """
    import re as _re
    s = name.strip()
    # Remove trailing pointer/reference decorators and CV-qualifiers
    s = _re.sub(r"[\s*&]+$", "", s).strip()
    # Remove leading CV-qualifiers
    s = _re.sub(r"^(const|volatile)(\s+(const|volatile))?\s+", "", s).strip()
    # Remove struct/class/union tag keyword, remembering it: for an anonymous
    # placeholder spelled with a *leading* tag ("union <anonymous>") the tag
    # carries the aggregate kind, which must survive the collapse below.
    lead = _re.match(r"^(struct|class|union)\s+", s)
    lead_kind = lead.group(1) if lead else None
    if lead:
        s = s[lead.end():].strip()
    # Anonymous/unnamed member types have no stable *name* across DWARF / castxml
    # / PDB extraction — the same anonymous union can be spelled "<unnamed-tag>"
    # by one reader and "Parent::<unnamed-type-u>" by another (observed on the
    # Windows SDK _TP_CALLBACK_ENVIRON_V3::u between two MSVC builds). Collapse
    # those placeholders to a token keyed on the aggregate *kind* — taken from
    # the placeholder itself ("<anonymous union>") or the leading tag ("union
    # <anonymous>") — so the unstable identifier suffix no longer drives a false
    # positive while a genuine kind change (anonymous union → anonymous struct)
    # is still reported. Size drift remains caught by the separate byte_size
    # comparison.
    anon = _ANON_TYPE_RE.search(s)
    if anon is not None:
        kind = anon.group(1) or lead_kind
        return f"<anonymous {kind.lower()}>" if kind else "<anonymous>"
    return s


def _diff_struct_layouts(o: object, n: object) -> list[Change]:
    from .dwarf_metadata import FieldInfo, StructLayout

    old_structs: dict[str, StructLayout] = getattr(o, "structs", {})
    new_structs: dict[str, StructLayout] = getattr(n, "structs", {})
    changes: list[Change] = []

    for name, old_s in old_structs.items():
        if name not in new_structs:
            continue  # struct removed — caught by header-layer (castxml)

        new_s = new_structs[name]

        # 1. Total size
        if old_s.byte_size != new_s.byte_size:
            changes.append(Change(
                kind=ChangeKind.STRUCT_SIZE_CHANGED,
                symbol=name,
                description=(
                    f"Struct size changed: {name} "
                    f"({old_s.byte_size} → {new_s.byte_size} bytes)"
                ),
                old_value=str(old_s.byte_size),
                new_value=str(new_s.byte_size),
            ))

        # 2. Alignment (only when explicitly present in DWARF 5)
        if old_s.alignment and new_s.alignment and old_s.alignment != new_s.alignment:
            changes.append(Change(
                kind=ChangeKind.STRUCT_ALIGNMENT_CHANGED,
                symbol=name,
                description=(
                    f"Struct alignment changed: {name} "
                    f"({old_s.alignment} → {new_s.alignment})"
                ),
                old_value=str(old_s.alignment),
                new_value=str(new_s.alignment),
            ))

        # Build field maps
        old_fields = {f.name: f for f in old_s.fields}
        new_fields = {f.name: f for f in new_s.fields}

        # 3. Removed fields — check for reserved-field activations first
        removed_names = sorted(old_fields.keys() - new_fields.keys())
        added_names = new_fields.keys() - old_fields.keys()
        # Build added-field index by byte_offset for reserved-field matching
        added_by_offset: dict[int, FieldInfo] = {
            new_fields[fn].byte_offset: new_fields[fn]
            for fn in added_names
            if not _RESERVED_FIELD_RE.match(fn)
        }
        reserved_matched: set[str] = set()

        for fname in removed_names:
            if _RESERVED_FIELD_RE.match(fname):
                old_f = old_fields[fname]
                candidate = added_by_offset.get(old_f.byte_offset)
                if candidate is not None and not _RESERVED_FIELD_RE.match(candidate.name) and old_f.type_name == candidate.type_name:
                    changes.append(Change(
                        kind=ChangeKind.USED_RESERVED_FIELD,
                        symbol=name,
                        description=f"Reserved field put into use: {name}::{fname} → {candidate.name}",
                        old_value=fname,
                        new_value=candidate.name,
                    ))
                    reserved_matched.add(candidate.name)
                    continue
            changes.append(Change(
                kind=ChangeKind.STRUCT_FIELD_REMOVED,
                symbol=f"{name}::{fname}",
                description=f"Struct field removed: {name}::{fname}",
                old_value=f"{old_fields[fname].type_name}",
            ))

        # 4. Existing fields: offset and type changes
        for fname, old_f in old_fields.items():
            if fname not in new_fields:
                continue
            new_f = new_fields[fname]

            if old_f.byte_offset != new_f.byte_offset:
                changes.append(Change(
                    kind=ChangeKind.STRUCT_FIELD_OFFSET_CHANGED,
                    symbol=f"{name}::{fname}",
                    description=(
                        f"Field offset changed: {name}::{fname} "
                        f"(+{old_f.byte_offset} → +{new_f.byte_offset})"
                    ),
                    old_value=str(old_f.byte_offset),
                    new_value=str(new_f.byte_offset),
                ))

            # Field type drift:
            # - catches same-size type substitutions (int→float, Foo*→Bar*)
            # - strip "struct "/"class "/"union " prefixes for stable comparison
            # - still includes explicit size drift when known on both sides
            # A pointee/by-value cv-qualifier change (``char *`` ->
            # ``const char *``) keeps the field's size and offset identical, so
            # it is not a binary layout break (ISSUE-30/35/65: libuv
            # ``uv_cpu_info_s::model`` const-pointer churn). A genuine size
            # change is still reported via ``type_size_changed`` below.
            type_name_changed = (
                _normalize_type_name(old_f.type_name) != _normalize_type_name(new_f.type_name)
                and not cv_qualifiers_only_differ(old_f.type_name, new_f.type_name)
            )
            type_size_changed = (
                old_f.byte_size > 0
                and new_f.byte_size > 0
                and old_f.byte_size != new_f.byte_size
            )
            if type_name_changed or type_size_changed:
                changes.append(Change(
                    kind=ChangeKind.STRUCT_FIELD_TYPE_CHANGED,
                    symbol=f"{name}::{fname}",
                    description=(
                        f"Field type changed: {name}::{fname} "
                        f"{old_f.type_name}({old_f.byte_size}B) → "
                        f"{new_f.type_name}({new_f.byte_size}B)"
                    ),
                    old_value=old_f.type_name,
                    new_value=new_f.type_name,
                ))

    return changes


def _diff_enum_layouts(o: object, n: object) -> list[Change]:
    from .dwarf_metadata import EnumInfo

    old_enums: dict[str, EnumInfo] = getattr(o, "enums", {})
    new_enums: dict[str, EnumInfo] = getattr(n, "enums", {})
    changes: list[Change] = []

    for name, old_e in old_enums.items():
        if name not in new_enums:
            continue

        new_e = new_enums[name]

        # 1. Underlying size change (e.g. int8_t → int32_t)
        if old_e.underlying_byte_size != new_e.underlying_byte_size:
            changes.append(Change(
                kind=ChangeKind.ENUM_UNDERLYING_SIZE_CHANGED,
                symbol=name,
                description=(
                    f"Enum underlying type size changed: {name} "
                    f"({old_e.underlying_byte_size} → {new_e.underlying_byte_size} bytes)"
                ),
                old_value=str(old_e.underlying_byte_size),
                new_value=str(new_e.underlying_byte_size),
            ))

        # 2. Removed members — skip rename-only removals here.
        # A dedicated rename detector emits ENUM_MEMBER_RENAMED. Here we only
        # report truly removed values. Use one-to-one proof: a removal is a
        # rename candidate only when its value appears in exactly one new-only
        # member (CodeRabbit P1: avoid false suppression with alias-heavy enums).
        _removed_names = {m for m in old_e.members if m not in new_e.members}
        _added_names = {m for m in new_e.members if m not in old_e.members}
        # Build set of removed old-member names whose value uniquely maps to one new name
        _renamed_old: set[str] = set()
        _claimed_new: set[str] = set()
        for _rname in sorted(_removed_names):
            _rval = old_e.members[_rname]
            _candidates = [_n for _n in _added_names if new_e.members[_n] == _rval and _n not in _claimed_new]
            if len(_candidates) == 1:
                _renamed_old.add(_rname)
                _claimed_new.add(_candidates[0])
        for mname in sorted(_removed_names):
            if mname in _renamed_old:
                continue
            old_val = old_e.members[mname]
            changes.append(Change(
                kind=ChangeKind.ENUM_MEMBER_REMOVED,
                symbol=f"{name}::{mname}",
                description=f"Enum member removed: {name}::{mname}",
                old_value=str(old_val),
            ))

        # 3. Changed values
        # Sentinel detection: name-pattern based (*_last, *_max, *_count).
        # More robust than max-value heuristics for evolving enums.
        _SENTINEL_SUFFIXES = ("_last", "_max", "_count")

        def _is_sentinel_member(member_name: str) -> bool:
            n = member_name.lower()
            return n.endswith(_SENTINEL_SUFFIXES) or n in {"last", "max", "count"}

        for mname, old_val in old_e.members.items():
            if mname in new_e.members and new_e.members[mname] != old_val:
                kind = (
                    ChangeKind.ENUM_LAST_MEMBER_VALUE_CHANGED
                    if _is_sentinel_member(mname)
                    else ChangeKind.ENUM_MEMBER_VALUE_CHANGED
                )
                changes.append(Change(
                    kind=kind,
                    symbol=f"{name}::{mname}",
                    description=(
                        f"Enum member value changed: {name}::{mname} "
                        f"({old_val} → {new_e.members[mname]})"
                    ),
                    old_value=str(old_val),
                    new_value=str(new_e.members[mname]),
                ))

    return changes


# ── PR #89: ELF fallback for = delete (issue #100) ───────────────────────────

@registry.detector("elf_deleted_fallback")
def _diff_elf_deleted_fallback(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """ELF fallback for detecting implicitly-deleted / disappeared symbols.

    When castxml metadata does NOT mark a function as deleted (no ``deleted="1"``)
    but the symbol vanishes from the new library's ELF ``.dynsym`` while still
    being declared in the new snapshot's header model (i.e., it's not FUNC_REMOVED),
    this is strong evidence the function was deleted or made inline without proper
    annotation.

    Detection heuristic:
    1. Function is PUBLIC in old snapshot and present in old ELF ``.dynsym``.
    2. Function is still present in new snapshot (not FUNC_REMOVED) but
       absent from new ELF ``.dynsym``.
    3. Function is not already marked ``is_deleted=True`` (handled by FUNC_DELETED)
       and not already marked ``is_inline=True`` (handled by FUNC_BECAME_INLINE).

    Confidence: 0.75 (lower than FUNC_DELETED castxml path because we're inferring
    from ELF absence rather than explicit annotation).
    """
    changes: list[Change] = []

    old_elf = getattr(old, "elf", None)
    new_elf = getattr(new, "elf", None)

    # Need ELF data on both sides to compare symbol presence
    if old_elf is None or new_elf is None:
        return changes

    old_elf_names: set[str] = {s.name for s in old_elf.symbols}
    new_elf_names: set[str] = {s.name for s in new_elf.symbols}

    # Get all new-snapshot functions keyed by mangled name
    new_func_map = new.function_map

    old_pub = _public_functions(old)

    for mangled, f_old in old_pub.items():
        # Must be present in old ELF (this was a real exported symbol)
        if mangled not in old_elf_names:
            continue

        # Must NOT be present in new ELF (symbol disappeared)
        if mangled in new_elf_names:
            continue

        # Must still be declared in new snapshot (not simply FUNC_REMOVED)
        f_new = new_func_map.get(mangled)
        if f_new is None:
            continue  # Already caught by FUNC_REMOVED — don't double-report

        # Skip if already explicitly marked deleted (FUNC_DELETED handles it)
        if f_new.is_deleted:
            continue

        # NOTE: We intentionally do NOT skip inline transitions here.
        # When a function becomes inline AND its symbol vanishes from .dynsym,
        # this is a binary break for pre-compiled consumers. The
        # FUNC_BECAME_INLINE detector (API_BREAK) fires separately for the
        # source-level concern; this detector adds FUNC_DELETED_ELF_FALLBACK
        # (BREAKING) for the binary-level concern.

        # Skip if function moved to hidden visibility — FUNC_VISIBILITY_CHANGED handles it
        if getattr(f_new, "visibility", None) == Visibility.HIDDEN:
            continue

        # Symbol disappeared from ELF without explicit annotation — likely deleted
        changes.append(Change(
            kind=ChangeKind.FUNC_DELETED_ELF_FALLBACK,
            symbol=mangled,
            description=(
                f"Symbol disappeared from ELF .dynsym without explicit deletion marker: "
                f"{f_old.name} — was exported in old library, absent in new library's "
                f"dynamic symbol table while header still declares it"
            ),
            old_value="exported",
            new_value="absent_from_dynsym",
        ))

    return changes


# ── PR #89: Template inner-type deep analysis (issues #38 / #73) ─────────────
# Detectors moved to ``diff_platform_templates`` to keep this file under the
# AI-readiness file-size soft cap. Re-exported below so existing imports from
# ``abicheck.diff_platform`` keep working.
# Re-exports for backwards compatibility — see top-of-file imports.
