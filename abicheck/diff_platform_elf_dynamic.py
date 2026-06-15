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
