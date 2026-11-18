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

"""ADR-094 D9's shadow ``call`` axis over a real comparison's findings.

For every function a signature finding names (``func_params_changed``,
``func_return_changed``), :func:`call_axis_shadow` reduces the old and new
declarations to :class:`~.call_interface.CallSignature` and records
:func:`~.call_interface.assess_call_interface`'s answer. It is a shadow:
nothing here reads or changes a finding, a verdict or an exit code; the
workflow and report layers decide what to do with the records.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from ..model.call_interface import SYSV_AMD64
from ..model.change import Change
from ..model.change_catalog.kinds import ChangeKind
from ..model.snapshot import AbiSnapshot
from .call_interface import (
    CallAxis,
    assess_call_interface,
    call_signature_of,
    linkage_evidence_reliable,
)

__all__ = [
    "SIGNATURE_KINDS",
    "CallAxisRecord",
    "abi_of",
    "call_axis_shadow",
]

#: The findings whose function's call axis is computed.
SIGNATURE_KINDS: frozenset[ChangeKind] = frozenset(
    {ChangeKind.FUNC_PARAMS_CHANGED, ChangeKind.FUNC_RETURN_CHANGED}
)


@dataclass(frozen=True, slots=True)
class CallAxisRecord:
    """The shadow ``call`` axis for one function named by a signature finding."""

    symbol: str
    kinds: tuple[ChangeKind, ...]
    abi: str
    axis: CallAxis


def abi_of(snapshot: AbiSnapshot) -> str:
    """The calling-convention ABI a snapshot's platform facts establish.

    Only a 64-bit x86-64 ELF maps to SysV AMD64; anything else (x32, another
    machine, PE, Mach-O, or no binary facts at all) is ``""`` -- unmodelled,
    so the axis is ``unknown``.
    """
    elf = snapshot.elf
    if (
        snapshot.platform in (None, "elf")
        and elf is not None
        and elf.machine == "EM_X86_64"
        and elf.elf_class == 64
    ):
        return SYSV_AMD64
    return ""


def call_axis_shadow(
    old: AbiSnapshot, new: AbiSnapshot, changes: Iterable[Change]
) -> list[CallAxisRecord]:
    """One :class:`CallAxisRecord` per function a signature finding names.

    Both sides must establish the same ABI; a pair whose platforms disagree
    is assessed under ``""`` (unmodelled). A function missing from either
    side's function map is skipped: there is no pair of declarations to
    assess.
    """
    kinds_by_symbol: dict[str, set[ChangeKind]] = {}
    for change in changes:
        if change.kind in SIGNATURE_KINDS:
            kinds_by_symbol.setdefault(change.symbol, set()).add(change.kind)
    if not kinds_by_symbol:
        return []
    old_abi, new_abi = abi_of(old), abi_of(new)
    abi = old_abi if old_abi == new_abi else ""
    old_reliable = linkage_evidence_reliable(old)
    new_reliable = linkage_evidence_reliable(new)
    old_fns, new_fns = old.function_map, new.function_map
    records: list[CallAxisRecord] = []
    for symbol in sorted(kinds_by_symbol):
        old_fn, new_fn = old_fns.get(symbol), new_fns.get(symbol)
        if old_fn is None or new_fn is None:
            continue
        axis = assess_call_interface(
            call_signature_of(old_fn, linkage_reliable=old_reliable),
            call_signature_of(new_fn, linkage_reliable=new_reliable),
            abi,
        )
        records.append(
            CallAxisRecord(symbol, tuple(sorted(kinds_by_symbol[symbol])), abi, axis)
        )
    return records
