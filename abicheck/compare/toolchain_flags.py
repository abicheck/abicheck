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

"""Diffs of the ABI-relevant compiler flags a binary's DWARF records.

``dwarf_advanced`` extracts each compilation unit's ``DW_AT_producer`` flags
into ``ToolchainInfo`` (``abi_flags``, ``vector_abi_flags``, ``wchar_flags``);
these diffs compare them, one family per sub-diff, and return the same
``(kind, symbol, description, old_value, new_value)`` tuples
``dwarf_advanced.diff_advanced_dwarf`` returns for the DIE-derived families.
``checker._diff_advanced_dwarf`` maps both onto ``Change`` objects.

Plain-``char`` signedness has its own kind (``char_signedness_changed``) and is
left out of the generic ``toolchain_flag_drift``, so one flip is reported once.
"""

from __future__ import annotations

from typing import Any

from ..model.change import Change
from ..model.change_catalog.kinds import ChangeKind
from ..model.change_catalog.registry import Verdict
from ..model.dwarf_facts import AdvancedDwarfMetadata

__all__ = [
    "advanced_change_grade",
    "advanced_dwarf_changes",
    "advanced_dwarf_kind_map",
    "toolchain_flag_changes",
]


def advanced_dwarf_kind_map() -> dict[str, ChangeKind]:
    """Every kind the advanced-DWARF detector emits, DIE- and flag-derived, by value.

    A function rather than a constant so ``scripts/gen_detector_kind_map.py``'s
    call walk attributes these kinds to the detector that calls it.
    """
    return {
        k.value: k
        for k in (
            ChangeKind.CALLING_CONVENTION_CHANGED,
            ChangeKind.VALUE_ABI_TRAIT_CHANGED,
            ChangeKind.STRUCT_RETURN_CONVENTION_CHANGED,
            ChangeKind.STRUCT_PACKING_CHANGED,
            ChangeKind.TOOLCHAIN_FLAG_DRIFT,
            ChangeKind.VECTOR_ABI_CHANGED,
            ChangeKind.WCHAR_MODEL_CHANGED,
            ChangeKind.CHAR_SIGNEDNESS_CHANGED,
        )
    }


#: Flags that only choose which vector math library the binary itself calls
#: (gcc ``-mveclibabi=``, clang ``-fveclib=``). They change no entry point the
#: binary exports; a real effect shows as new imports, reported on their own.
#: Intel's ``-vecabi=`` instead changes the ABI of the binary's own exported
#: SIMD clones, which is the break the kind's default names.
_CALLEE_LIBRARY_VECTOR_FLAGS = ("-mveclibabi=", "-fveclib=")


def advanced_change_grade(
    kind: str, old: str | None, new: str | None
) -> dict[str, Any]:
    """``Change`` fields that lower an advanced-DWARF finding below its default.

    A ``vector_abi_changed`` confined to callee-library selection flags is a
    risk, not a break (disclosed via ``modulation_reason``, ADR-025 D4.1).
    """
    if kind != "vector_abi_changed":
        return {}
    changed = set((old or "").split(",")) ^ set((new or "").split(","))
    changed.discard("")
    if not changed or not all(
        f.startswith(_CALLEE_LIBRARY_VECTOR_FLAGS) for f in changed
    ):
        return {}
    return {
        "effective_verdict": Verdict.COMPATIBLE_WITH_RISK,
        "modulation_reason": "vector_library_selection_only",
        "modulation_rule": "vector_abi_changed.callee_library_flags",
    }


def advanced_dwarf_changes(
    rows: list[tuple[str, str, str, str | None, str | None]],
) -> list[Change]:
    """The advanced-DWARF ``(kind, symbol, description, old, new)`` rows as
    ``Change`` objects, each graded by :func:`advanced_change_grade`; a row of
    a kind this map does not own is dropped."""
    kinds = advanced_dwarf_kind_map()
    return [
        Change(
            kind=kinds[kind],
            symbol=sym,
            description=desc,
            old_value=old,
            new_value=new,
            **advanced_change_grade(kind, old, new),
        )
        for kind, sym, desc, old, new in rows
        if kind in kinds
    ]


# Plain ``char`` signedness flags (gcc records them in DW_AT_producer), and the
# psABI default per target (``_normalize_arch`` / pyelftools spellings).
_UNSIGNED_CHAR_FLAGS = frozenset({"-funsigned-char", "-fno-signed-char"})
_SIGNED_CHAR_FLAGS = frozenset({"-fsigned-char", "-fno-unsigned-char"})
_CHAR_SIGN_FLAGS = _UNSIGNED_CHAR_FLAGS | _SIGNED_CHAR_FLAGS
_DEFAULT_CHAR_SIGNED: dict[str, bool] = {
    "x64": True,
    "x86": True,
    "aarch64": False,
    "ARM": False,
    "PowerPC": False,
    "64-bit PowerPC": False,
    "IBM S/390": False,
    "RISC-V": False,
}


def _char_signedness(meta: AdvancedDwarfMetadata) -> tuple[bool, str] | None:
    """(plain char is signed, label) for one side, or None when unknown.

    An explicit flag decides; otherwise the target's psABI default, but only
    when the producer records its switches at all (gcc's default
    ``-grecord-gcc-switches``). Clang, or gcc with
    ``-gno-record-gcc-switches``, records none, so an absent flag there says
    nothing. Both spellings present (CUs built differently), an unknown
    target, or a producer that records no switches: None.
    """
    flags = meta.toolchain.abi_flags
    unsigned = bool(flags & _UNSIGNED_CHAR_FLAGS)
    signed = bool(flags & _SIGNED_CHAR_FLAGS)
    if unsigned and signed:
        return None
    if unsigned or signed:
        flag = sorted(flags & _CHAR_SIGN_FLAGS)[0]
        return (signed, f"{'signed' if signed else 'unsigned'} ({flag})")
    if not meta.toolchain.records_switches:
        return None
    default = _DEFAULT_CHAR_SIGNED.get(meta.target_arch)
    if default is None:
        return None
    sign = "signed" if default else "unsigned"
    return (default, f"{sign} (default for {meta.target_arch})")


def _diff_char_signedness(
    old_meta: AdvancedDwarfMetadata,
    new_meta: AdvancedDwarfMetadata,
) -> list[tuple[str, str, str, str | None, str | None]]:
    """Diff plain-``char`` signedness (-fsigned-char / -funsigned-char).

    ``char`` and ``unsigned char`` are distinct types: a plain-``char``
    parameter, member or return reinterprets the same bytes with the other
    sign, with no symbol-level signal. Reported only when both sides' setting
    is known (explicit, or the target's default).
    """
    old, new = _char_signedness(old_meta), _char_signedness(new_meta)
    if old is None or new is None or old[0] == new[0]:
        return []
    return [
        (
            "char_signedness_changed",
            "<char>",
            f"plain char signedness changed: {old[1]} → {new[1]}",
            old[1],
            new[1],
        )
    ]


def _diff_toolchain_flags(
    old_meta: AdvancedDwarfMetadata,
    new_meta: AdvancedDwarfMetadata,
) -> list[tuple[str, str, str, str | None, str | None]]:
    """Diff ABI-affecting compiler flags. Returns results list.

    Plain-char signedness flags are left to ``_diff_char_signedness``, which
    names them with their own kind, so one flip is never reported twice.
    """
    results: list[tuple[str, str, str, str | None, str | None]] = []
    old_flags = old_meta.toolchain.abi_flags - _CHAR_SIGN_FLAGS
    new_flags = new_meta.toolchain.abi_flags - _CHAR_SIGN_FLAGS
    removed_flags = old_flags - new_flags
    added_flags = new_flags - old_flags
    if removed_flags or added_flags:
        parts = []
        if added_flags:
            parts.append(f"added: {', '.join(sorted(added_flags))}")
        if removed_flags:
            parts.append(f"removed: {', '.join(sorted(removed_flags))}")
        results.append(
            (
                "toolchain_flag_drift",
                "<toolchain>",
                f"ABI-affecting compiler flags changed: {'; '.join(parts)}",
                ",".join(sorted(old_flags)) or None,
                ",".join(sorted(new_flags)) or None,
            )
        )
    return results


def _diff_vector_abi_flags(
    old_meta: AdvancedDwarfMetadata,
    new_meta: AdvancedDwarfMetadata,
) -> list[tuple[str, str, str, str | None, str | None]]:
    """Diff vector-function (SIMD clone) ABI flags. Returns results list.

    A change in the vector-ABI flag set (-mveclibabi/-fveclib/-vecabi) means
    the vectorized call variants of functions resolve to a different ABI, which
    breaks callers that were compiled against the old vector entry points.
    """
    results: list[tuple[str, str, str, str | None, str | None]] = []
    old_flags = old_meta.toolchain.vector_abi_flags
    new_flags = new_meta.toolchain.vector_abi_flags
    if old_flags != new_flags:
        removed_flags = old_flags - new_flags
        added_flags = new_flags - old_flags
        parts = []
        if added_flags:
            parts.append(f"added: {', '.join(sorted(added_flags))}")
        if removed_flags:
            parts.append(f"removed: {', '.join(sorted(removed_flags))}")
        results.append(
            (
                "vector_abi_changed",
                "<vector-abi>",
                f"Vector-function (SIMD clone) ABI flags changed: {'; '.join(parts)}",
                ",".join(sorted(old_flags)) or None,
                ",".join(sorted(new_flags)) or None,
            )
        )
    return results


def _diff_wchar_flags(
    old_meta: AdvancedDwarfMetadata,
    new_meta: AdvancedDwarfMetadata,
) -> list[tuple[str, str, str, str | None, str | None]]:
    """Diff the -fshort-wchar / default wchar_t data-model flag.

    GCC/Clang document that objects built with and without -fshort-wchar are
    not binary compatible: the flag switches wchar_t between the platform
    default (commonly 4-byte signed on Linux/macOS) and a 2-byte unsigned
    type. Any public function or struct field carrying wchar_t changes size
    and signedness with no symbol-level signal, so this flags the compiler-
    flag cause for review.
    """
    old_short = "-fshort-wchar" in old_meta.toolchain.wchar_flags
    new_short = "-fshort-wchar" in new_meta.toolchain.wchar_flags
    if old_short == new_short:
        return []
    old_label = (
        "short (2-byte unsigned, -fshort-wchar)" if old_short else "default wchar_t"
    )
    new_label = (
        "short (2-byte unsigned, -fshort-wchar)" if new_short else "default wchar_t"
    )
    return [
        (
            "wchar_model_changed",
            "<wchar_t>",
            f"wchar_t model changed: {old_label} → {new_label}. Objects built with "
            "and without -fshort-wchar are not binary compatible for any public "
            "wchar_t parameter, field, or return value.",
            old_label,
            new_label,
        )
    ]


def toolchain_flag_changes(
    old_meta: AdvancedDwarfMetadata,
    new_meta: AdvancedDwarfMetadata,
) -> list[tuple[str, str, str, str | None, str | None]]:
    """Every flag-family change; ``[]`` unless both sides carry DWARF."""
    if not old_meta.has_dwarf or not new_meta.has_dwarf:
        return []
    return (
        _diff_toolchain_flags(old_meta, new_meta)
        + _diff_vector_abi_flags(old_meta, new_meta)
        + _diff_wchar_flags(old_meta, new_meta)
        + _diff_char_signedness(old_meta, new_meta)
    )
