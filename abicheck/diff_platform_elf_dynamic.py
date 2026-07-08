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

"""ELF dynamic-section, security-hardening, and dependency-leak diff detectors.

Split from ``diff_platform.py`` to keep that module under the AI-readiness
file-size soft cap. This module is a leaf — it must not import from
``diff_platform``. The helpers/detectors are re-exported back from
``diff_platform`` so existing imports keep working.
"""

from __future__ import annotations

from typing import Any

from .checker_policy import ChangeKind
from .checker_types import Change
from .diff_helpers import make_change
from .diff_symbols import _should_filter_transitive_runtime_symbols
from .elf_symbol_filter import is_abi_relevant_elf_symbol
from .model import AbiSnapshot, Visibility

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


def _diff_visibility_leak(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect old-library visibility leaks (ELF-only internal symbols exported)."""
    del new  # detector is intentionally old-library-only
    if not getattr(old, "elf_only_mode", False):
        return []

    filter_transitive_runtime_symbols = _should_filter_transitive_runtime_symbols(old)
    leaked = [
        f
        for f in old.functions
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
    return [
        make_change(
            ChangeKind.VISIBILITY_LEAK,
            symbol="<visibility>",
            name=f"{names}{suffix}",
            detail=str(len(leaked)),
            old_value=str(len(leaked)),
        )
    ]


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
        changes.append(
            make_change(
                ChangeKind.SYMBOL_LEAKED_FROM_DEPENDENCY_CHANGED,
                symbol=sym_name,
                description=(
                    f"Symbol '{sym_name}' was removed but appears to originate from "
                    f"'{origin}' (a dependency of this library). This is a real ABI "
                    f"change — the library is leaking dependency symbols into its public "
                    f"ABI surface. Consider applying -fvisibility=hidden."
                ),
                old_value=origin,
                new_value=None,
            )
        )

    # Symbols that were *added* (absent in old, present in new with origin_lib)
    for sym_name, s_new in new_syms.items():
        if sym_name in old_syms:
            continue  # Already present in old — not a pure addition
        if s_new.origin_lib is None:
            continue
        changes.append(
            make_change(
                ChangeKind.SYMBOL_LEAKED_FROM_DEPENDENCY_CHANGED,
                symbol=sym_name,
                description=(
                    f"Symbol '{sym_name}' was added but appears to originate from "
                    f"'{s_new.origin_lib}' (a dependency of this library). This is a real "
                    f"ABI change — the library is leaking dependency symbols into its public "
                    f"ABI surface. Consider applying -fvisibility=hidden."
                ),
                old_value=None,
                new_value=s_new.origin_lib,
            )
        )

    return changes


def _diff_elf_dynamic_section(old_elf: Any, new_elf: Any) -> list[Change]:
    changes: list[Change] = []
    # Emit SONAME_CHANGED only when old library HAD a SONAME (non-empty) and it
    # changed or was removed. Adding a SONAME (empty/None → value) is a compatible
    # improvement and must not be flagged as breaking.
    if old_elf.soname and old_elf.soname != new_elf.soname:
        changes.append(
            make_change(
                ChangeKind.SONAME_CHANGED,
                symbol="DT_SONAME",
                description=f"SONAME changed: {old_elf.soname!r} → {new_elf.soname!r}",
                old_value=old_elf.soname,
                new_value=new_elf.soname,
            )
        )
    elif not old_elf.soname and new_elf.soname:
        changes.append(
            make_change(
                ChangeKind.SONAME_MISSING,
                symbol="DT_SONAME",
                new=repr(new_elf.soname),
                old_value="",
                new_value=new_elf.soname,
            )
        )
    changes.extend(_diff_needed_libraries(old_elf.needed, new_elf.needed))
    if old_elf.rpath != new_elf.rpath:
        changes.append(
            make_change(
                ChangeKind.RPATH_CHANGED,
                symbol="DT_RPATH",
                old=repr(old_elf.rpath),
                new=repr(new_elf.rpath),
                old_value=old_elf.rpath,
                new_value=new_elf.rpath,
            )
        )
    if old_elf.runpath != new_elf.runpath:
        changes.append(
            make_change(
                ChangeKind.RUNPATH_CHANGED,
                symbol="DT_RUNPATH",
                old=repr(old_elf.runpath),
                new=repr(new_elf.runpath),
                old_value=old_elf.runpath,
                new_value=new_elf.runpath,
            )
        )

    # PT_GNU_STACK executable stack detection (security bad practice).
    # Report ONLY the regression direction (stack becomes executable); making
    # the stack non-executable is a hardening improvement, not a finding — and
    # emitting it would let the shipped `security` policy fail an improvement.
    old_exec = getattr(old_elf, "has_executable_stack", False)
    new_exec = getattr(new_elf, "has_executable_stack", False)
    if new_exec and not old_exec:
        changes.append(
            make_change(
                ChangeKind.EXECUTABLE_STACK,
                symbol="PT_GNU_STACK",
                old_value="RW",
                new_value="RWE",
            )
        )
    elif old_exec and not new_exec:
        # Improvement direction — a distinct kind so the `security` policy can
        # gate the regression (executable_stack) without failing this fix.
        changes.append(
            make_change(
                ChangeKind.EXECUTABLE_STACK_REMOVED,
                symbol="PT_GNU_STACK",
                old_value="RWE",
                new_value="RW",
            )
        )

    changes.extend(_diff_security_hardening(old_elf, new_elf))
    changes.extend(_diff_elf_identity(old_elf, new_elf))
    changes.extend(_diff_static_tls(old_elf, new_elf))
    changes.extend(_diff_gnu_property(old_elf, new_elf))

    return changes


def _diff_elf_identity(old_elf: Any, new_elf: Any) -> list[Change]:
    """Detect ELF header identity drift (G23-A3): machine, class, ABI flags, OS ABI.

    A machine/class/ABI-flags change means the two inputs are
    different-architecture or different-calling-convention images — the
    ELF-side counterpart to PE_MACHINE_CHANGED / MACHO_CPU_TYPE_CHANGED. Empty
    identity (e.g. an in-memory snapshot with no ELF parsed) is skipped so a
    missing-metadata side never fabricates a finding.
    """
    changes: list[Change] = []

    old_machine = getattr(old_elf, "machine", "")
    new_machine = getattr(new_elf, "machine", "")
    # Require BOTH sides to have captured ELF identity before comparing any of
    # it. A real parsed ELF always sets `machine`; a default / header-only /
    # parse-failed `ElfMetadata()` has machine="" but still carries the
    # `elf_class=64` default — comparing that against a real 32-bit ELF would
    # false-positive elf_class_changed. An unknown side is not a change.
    if not (old_machine and new_machine):
        return changes

    if old_machine != new_machine:
        changes.append(
            make_change(
                ChangeKind.ELF_MACHINE_CHANGED,
                symbol="ELF_HEADER",
                old=old_machine,
                new=new_machine,
                old_value=old_machine,
                new_value=new_machine,
            )
        )
        # Machine drift subsumes ABI-flag/class drift (flags are per-arch); a
        # cross-architecture pair has nothing further comparable.
        return changes

    old_class = getattr(old_elf, "elf_class", 0)
    new_class = getattr(new_elf, "elf_class", 0)
    if old_class and new_class and old_class != new_class:
        changes.append(
            make_change(
                ChangeKind.ELF_CLASS_CHANGED,
                symbol="ELF_HEADER",
                old=str(old_class),
                new=str(new_class),
                old_value=str(old_class),
                new_value=str(new_class),
            )
        )

    changes.extend(_diff_abi_flags(old_elf, new_elf))

    old_osabi = getattr(old_elf, "osabi", "")
    new_osabi = getattr(new_elf, "osabi", "")
    if (
        old_osabi
        and new_osabi
        and old_osabi != new_osabi
        and not (old_osabi in _BENIGN_OSABI and new_osabi in _BENIGN_OSABI)
    ):
        changes.append(
            make_change(
                ChangeKind.ELF_OSABI_CHANGED,
                symbol="ELF_HEADER",
                old=old_osabi,
                new=new_osabi,
                old_value=old_osabi,
                new_value=new_osabi,
            )
        )

    return changes


def _diff_abi_flags(old_elf: Any, new_elf: Any) -> list[Change]:
    """Compare the ABI-selecting e_flags bits (same-machine caller guarantee).

    For architectures the metadata parser knows how to decode (ARM/RISC-V/MIPS)
    the decoded ``abi_flags`` token set is diffed. For any other architecture
    both decoded sets are empty, so fall back to the raw ``e_flags`` word — e.g.
    PPC64 encodes its ELFv1/ELFv2 ABI version there — otherwise ABI-selecting
    drift on undecoded arches would never surface.
    """
    old_abi: frozenset[str] = getattr(old_elf, "abi_flags", frozenset())
    new_abi: frozenset[str] = getattr(new_elf, "abi_flags", frozenset())
    if old_abi != new_abi:
        return [
            make_change(
                ChangeKind.ELF_ABI_FLAGS_CHANGED,
                symbol="ELF_HEADER",
                old=", ".join(sorted(old_abi)) or "(none)",
                new=", ".join(sorted(new_abi)) or "(none)",
            )
        ]

    # Decoded tokens match (or both empty). Fall back to the raw e_flags word,
    # which catches ABI bits we don't decode — both undecoded architectures
    # (e.g. PPC64 ELFv1/ELFv2) and *extra* bits on partially-decoded ones
    # (e.g. a MIPS arch-level change that keeps the same ABI token).
    old_ef = getattr(old_elf, "e_flags", 0)
    new_ef = getattr(new_elf, "e_flags", 0)
    if old_ef != new_ef:
        return [
            make_change(
                ChangeKind.ELF_ABI_FLAGS_CHANGED,
                symbol="ELF_HEADER",
                old=hex(old_ef),
                new=hex(new_ef),
            )
        ]
    return []


#: OS-ABI values that are interchangeable on Linux. The GNU toolchain stamps a
#: binary ELFOSABI_GNU/LINUX (3) instead of ELFOSABI_SYSV/NONE (0) as a side
#: effect of using any GNU extension (IFUNC, STB_GNU_UNIQUE, …), so a SYSV↔GNU
#: transition is benign and must not be flagged (it routinely rides along with a
#: compatible change like adding an ifunc). Genuinely different OS ABIs
#: (FreeBSD, Solaris, …) still report.
_BENIGN_OSABI = frozenset({
    "ELFOSABI_SYSV",
    "ELFOSABI_NONE",
    "ELFOSABI_GNU",
    "ELFOSABI_LINUX",
})


def _diff_static_tls(old_elf: Any, new_elf: Any) -> list[Change]:
    """Detect DF_STATIC_TLS drift (G23-A1).

    Only report when the *new* side actually participates in TLS (defines or
    imports an STT_TLS symbol), so a TLS-free library that happens to flip the
    flag is never flagged. The removal (improvement) direction is a distinct
    COMPATIBLE kind so the security policy can gate the regression alone.
    """
    old_static = getattr(old_elf, "has_static_tls", False)
    new_static = getattr(new_elf, "has_static_tls", False)
    if old_static == new_static:
        return []
    if new_static and not old_static:
        if not getattr(new_elf, "has_tls_symbols", False):
            return []
        return [
            make_change(
                ChangeKind.STATIC_TLS_INTRODUCED,
                symbol="DF_STATIC_TLS",
                old_value="dynamic-tls",
                new_value="static-tls",
            )
        ]
    return [
        make_change(
            ChangeKind.STATIC_TLS_REMOVED,
            symbol="DF_STATIC_TLS",
            old_value="static-tls",
            new_value="dynamic-tls",
        )
    ]


#: GNU-property feature tokens grouped by the kind that reports their drift.
_CET_FEATURES = frozenset({"IBT", "SHSTK"})
_BRANCH_FEATURES = frozenset({"BTI", "PAC"})


def _diff_gnu_property(old_elf: Any, new_elf: Any) -> list[Change]:
    """Detect .note.gnu.property control-flow-protection drift (G23-A2).

    x86 CET (IBT/SHSTK) and AArch64 branch-protection (BTI/PAC) are reported
    separately. Both weakening (dropped feature) and improvement (added
    feature) directions are emitted, mirroring the executable-stack pair, so
    the security policy can gate weakening without failing an improvement.
    """
    old_props: frozenset[str] = getattr(old_elf, "gnu_properties", frozenset())
    new_props: frozenset[str] = getattr(new_elf, "gnu_properties", frozenset())
    if old_props == new_props:
        return []

    changes: list[Change] = []
    for feats, weakened, improved in (
        (_CET_FEATURES, ChangeKind.CET_PROTECTION_WEAKENED, ChangeKind.CET_PROTECTION_IMPROVED),
        (_BRANCH_FEATURES, ChangeKind.BRANCH_PROTECTION_WEAKENED, ChangeKind.BRANCH_PROTECTION_IMPROVED),
    ):
        old_f = old_props & feats
        new_f = new_props & feats
        if old_f == new_f:
            continue
        dropped = old_f - new_f
        symbol = ".note.gnu.property"
        if dropped:
            changes.append(
                make_change(
                    weakened,
                    symbol=symbol,
                    old=", ".join(sorted(old_f)) or "(none)",
                    new=", ".join(sorted(new_f)) or "(none)",
                )
            )
        else:
            changes.append(
                make_change(
                    improved,
                    symbol=symbol,
                    old=", ".join(sorted(old_f)) or "(none)",
                    new=", ".join(sorted(new_f)) or "(none)",
                )
            )
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
        changes.append(
            make_change(
                ChangeKind.RELRO_WEAKENED,
                symbol="GNU_RELRO",
                old=old_relro,
                new=new_relro,
            )
        )

    if getattr(old_elf, "is_pie", False) and not getattr(new_elf, "is_pie", False):
        changes.append(
            make_change(
                ChangeKind.PIE_DISABLED,
                symbol="DF_1_PIE",
                old_value="PIE",
                new_value="no-PIE",
            )
        )

    if getattr(old_elf, "has_stack_canary", False) and not getattr(
        new_elf, "has_stack_canary", False
    ):
        changes.append(
            make_change(
                ChangeKind.STACK_CANARY_REMOVED,
                symbol="__stack_chk_fail",
                old_value="canary",
                new_value="none",
            )
        )

    if getattr(old_elf, "has_fortify_source", False) and not getattr(
        new_elf, "has_fortify_source", False
    ):
        changes.append(
            make_change(
                ChangeKind.FORTIFY_SOURCE_WEAKENED,
                symbol="_FORTIFY_SOURCE",
                old_value="fortified",
                new_value="none",
            )
        )

    if not getattr(old_elf, "has_writable_executable_segment", False) and getattr(
        new_elf, "has_writable_executable_segment", False
    ):
        changes.append(
            make_change(
                ChangeKind.WRITABLE_EXECUTABLE_SEGMENT,
                symbol="PT_LOAD",
                old_value="W^X",
                new_value="W+X",
            )
        )

    return changes


def _diff_needed_libraries(
    old_needed: list[str], new_needed: list[str]
) -> list[Change]:
    changes: list[Change] = []
    old_set = set(old_needed)
    new_set = set(new_needed)
    for lib in sorted(new_set - old_set):
        changes.append(
            make_change(
                ChangeKind.NEEDED_ADDED,
                symbol="DT_NEEDED",
                description=f"New dependency added: {lib}",
                new_value=lib,
            )
        )
    for lib in sorted(old_set - new_set):
        changes.append(
            make_change(
                ChangeKind.NEEDED_REMOVED,
                symbol="DT_NEEDED",
                description=f"Dependency removed: {lib}",
                old_value=lib,
            )
        )
    return changes
