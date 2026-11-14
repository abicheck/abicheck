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
real export, which marks every header function ``HIDDEN``). A member is
header-declared when its function's origin is ``ScopeOrigin.PUBLIC_HEADER``
(or, with no public-header set declared, ``UNKNOWN`` with a parsed header)
-- the provenance pass's verdict, which a mismatch-``HIDDEN`` header
declaration keeps. Visibility is not the test: a DWARF-only function reads
``PUBLIC`` with no header behind it.

A raw MSVC export id is matched to a header declaration by its full scope
qualified name (``?ident@scope@@...`` -> ``scope::ident``), never the bare
identifier. The decorated parameter list is not decoded, so the match is
claimed only when it is unambiguous: one header declaration and one removed
export under that qualified name. Several overloads read as distinct members
rather than borrowing each other's header declaration.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable
from typing import Any

from ..model import AbiSnapshot, Function, ScopeOrigin
from ..model.change_catalog.registry import Verdict
from ..model.mangled_name import msvc_scope_components
from .template_surface import qualified_name_lookup

PUBLIC_MEMBER_RULE = "cpu_dispatch_isa.public_member"


def _header_declared(fn: Function) -> bool:
    """Whether *fn* is a header declaration the caller handed us as API.

    ``PUBLIC_HEADER`` is the positive verdict when a public-header set was
    declared. Without one, every origin is ``UNKNOWN`` (origin
    classification is opt-in, ``ScopeOrigin``), yet a function the header
    backend parsed (it carries ``source_header``) is still declared in a
    supplied header: the run has no evidence that it is export-only, and a
    naming heuristic must not lower a removal's grade on missing evidence
    (weaker evidence narrows conclusions, it never upgrades to a safer
    claim). A DWARF-only function has no ``source_header`` and stays
    export-only, as does any declaration the provenance pass placed
    outside the public set.
    """
    if fn.origin == ScopeOrigin.PUBLIC_HEADER:
        return True
    return fn.origin == ScopeOrigin.UNKNOWN and bool(fn.source_header)


def _qualified_spelling(name: str) -> str:
    """*name* without a parameter list: ``ns::f(int)`` -> ``ns::f``."""
    return name.split("(", 1)[0].strip()


def _msvc_qualified(export_id: str) -> str | None:
    comps = msvc_scope_components(export_id)
    return "::".join(comps) if comps else None


def header_declared_members(
    member_ids: Iterable[str],
    old_functions: Iterable[Function],
    still_declared: Callable[[Function], bool] = lambda _fn: False,
) -> list[str]:
    """Return the subset of *member_ids* declared in OLD's public headers.

    *member_ids* are the rolled-up removed identities (mangled names or raw
    export ids); *old_functions* is every function of the old snapshot.
    Itanium ids match ``Function.mangled`` (Mach-O adds one leading
    underscore); MSVC ids match by qualified name when unambiguous (see the
    module docstring). A declaration *still_declared* accepts (its function
    is still declared in NEW's public headers, under a changed signature or
    mangling) is no removal: that change is reported by its own finding.
    """
    ids = list(member_ids)
    mangled: set[str] = set()
    declared_by_name: Counter[str] = Counter()
    for fn in old_functions:
        if not _header_declared(fn) or still_declared(fn):
            continue
        if fn.mangled:
            mangled |= {fn.mangled, f"_{fn.mangled}"}
        if fn.name:
            declared_by_name[_qualified_spelling(fn.name)] += 1
    msvc_name = {mid: _msvc_qualified(mid) for mid in ids if mid not in mangled}
    exports_by_name = Counter(n for n in msvc_name.values() if n)

    def public(mid: str) -> bool:
        if mid in mangled:
            return True
        qname = msvc_name.get(mid)
        return bool(
            qname and declared_by_name[qname] == 1 and exports_by_name[qname] == 1
        )

    return [mid for mid in ids if public(mid)]


def redeclared_in(old: AbiSnapshot, new: AbiSnapshot) -> Callable[[Function], bool]:
    """Whether an OLD function's qualified name is still declared in NEW's
    public headers: its signature or mangling changed (a header-only
    dependency's type it names did), it was not removed."""
    from ..diff_templates import function_overload_stem

    name_old, name_new = qualified_name_lookup(old), qualified_name_lookup(new)
    kept = {
        function_overload_stem(name_new(f))
        for f in new.declarations.functions
        if _header_declared(f)
    }
    return lambda fn: function_overload_stem(name_old(fn)) in kept


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
