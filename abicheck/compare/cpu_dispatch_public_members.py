# SPDX-License-Identifier: Apache-2.0
"""Which rolled-up CPU-dispatch removals were header-declared public API.

``detect_cpu_dispatch_isa_dropped`` (``diff_cpp_patterns``) rolls a family of
removed ``*_<isa>`` exports into one ``cpu_dispatch_isa_dropped`` finding.
The ISA token in a name is only a *naming* signal (the registered
``cpu_dispatch_isa`` name heuristic), and a name heuristic may lower the
grading of a removal only when the removal was never a promised entry point.
A member declared in the public headers *is* a promised entry point: a binary
compiled against that header and calling it directly fails to load once it is
gone, whatever dispatcher survives. This module answers that one question —
"was any removed member header-declared?" — from the old snapshot's function
model, so the grouping can keep naming the family once while grading it as the
binary break it is.

Two identity forms reach the roll-up: the ``Function.mangled`` key of the
reconciled function model, and a raw PE/Mach-O export id from the
export-table fallback (used when castxml's guessed mangling does not match the
real export, which marks every header function ``HIDDEN``). Only
``Visibility.ELF_ONLY`` means "exported but declared in no header"; ``PUBLIC``
and a mismatch-``HIDDEN`` header declaration both came from a header.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ..model import Function, Visibility
from ..model.change_catalog.registry import Verdict

PUBLIC_MEMBER_RULE = "cpu_dispatch_isa.public_member"


def _header_declared(fn: Function) -> bool:
    return fn.visibility != Visibility.ELF_ONLY


def _msvc_identifier(export_id: str) -> str:
    """Bare identifier of an MSVC-decorated export (``?ident@scope@@...``), else ``""``."""
    head, sep, _ = export_id.partition("@")
    return head[1:] if sep and head[:1] == "?" else ""


def header_declared_members(
    member_ids: Iterable[str],
    old_functions: Iterable[Function],
) -> list[str]:
    """Return the subset of *member_ids* declared in OLD's public headers.

    *member_ids* are the rolled-up removed identities (mangled names or raw
    export ids); *old_functions* is every function of the old snapshot.
    Itanium ids match ``Function.mangled`` (Mach-O adds one leading
    underscore); MSVC ids match by their bare identifier, the form
    ``diff_platform._pe_export_id`` emits.
    """
    mangled: set[str] = set()
    identifiers: set[str] = set()
    for fn in old_functions:
        if not _header_declared(fn):
            continue
        if fn.mangled:
            mangled |= {fn.mangled, f"_{fn.mangled}"}
        identifiers.add((fn.name or "").rsplit("::", 1)[-1])
    identifiers.discard("")
    return [
        mid
        for mid in member_ids
        if mid in mangled or (_msvc_identifier(mid) or None) in identifiers
    ]


def public_member_grade(public_members: list[str]) -> dict[str, Any]:
    """``Change`` fields grading a roll-up with header-declared members BREAKING.

    Empty when every removed member is export-only: the kind's own
    COMPATIBLE_WITH_RISK default then applies. The override is disclosed
    through ``modulation_reason``/``modulation_rule`` (ADR-025 D4.1).
    """
    if not public_members:
        return {}
    return {
        "effective_verdict": Verdict.BREAKING,
        "modulation_reason": "header_declared_member_removed",
        "modulation_rule": PUBLIC_MEMBER_RULE,
    }
