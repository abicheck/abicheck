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

"""Each extractor's answer to the three surface facts — and only to the
ones it actually observed.

``model/surface_facts.py`` owns what the three facts *mean*; this module
owns what each producer is entitled to assert about them. The rule every
helper here follows is the one the old :class:`Visibility` enum could not:
**a producer states a fact only for the evidence it holds, and leaves the
rest unknown**. A header-AST backend saw headers, so it may answer (a); it
saw an export table only if the dump had a binary, so (c) is unknown for a
header-only dump. A DWARF/export-table extractor never looked at a header
at all, so (a) stays unknown there — never ``False``, which would turn
"we have no header evidence" into "this entity is not declared".

Public-contract membership (b) is mostly *not* answered here: it depends
on the run's scope selection, which these extractors do not see. The one
positive assertion a header backend may make without a declared public
set is "the binary exports it", and ``provenance.tag_provenance`` later
adds the stronger, scope-aware ``PUBLIC_HEADER`` evidence when a public
header set was actually supplied.
"""

from __future__ import annotations

from typing import Any

from ..model import Fact
from ..model.availability import FactStatus
from ..model.export_index import ExportMatch, ExportTableState
from ..model.surface_facts import export_match_diagnostic

__all__ = [
    "binary_exported_fact_for",
    "debug_info_surface_facts",
    "export_table_surface_facts",
    "header_ast_surface_facts",
    "reconcile_export_absence",
    "unread_export_fact",
]


def binary_exported_fact_for(
    exported: bool | ExportMatch, *, producer: str | None
) -> Fact[bool]:
    """(c) from an export lookup's answer.

    An :class:`ExportMatch` is the tiered answer every producer now computes
    through :func:`~abicheck.model.export_index.match_export`: only
    ``DYNAMIC`` -- the ``exports`` join's own rule -- is ``PRESENT(True)``,
    ``ABSENT`` is ``PRESENT(False)``, and every weaker tier (``.symtab``-only,
    bare-name alias, demangled-name match) is ``PARTIAL(True)`` carrying a
    named diagnostic. Truthiness is unchanged -- each weaker tier was already
    read as exported -- but the fact can no longer claim what the join
    denies (ADR-063, 2026-09-24 note). A bare ``bool`` is kept for a caller
    that has no symbol table to tier against (an export-table-synthesized
    entry is ``DYNAMIC`` by construction).
    """
    if isinstance(exported, ExportMatch):
        if exported is ExportMatch.DYNAMIC:
            return Fact.present(True, producer=producer)
        if exported is ExportMatch.ABSENT:
            return Fact.present(False, producer=producer)
        return Fact.partial(True, export_match_diagnostic(exported), producer=producer)
    return Fact.present(exported, producer=producer)


def header_ast_surface_facts(
    *,
    exported: bool | ExportMatch | None,
    judged_public: bool = False,
    producer: str | None = None,
) -> dict[str, Any]:
    """Facts for a declaration a header-AST backend parsed out of a header.

    *exported*: ``True``/``False`` when a real export table was consulted,
    ``None`` for a header-only dump with no binary at all — in which case
    (c) is unknown rather than ``False``, since there is no artifact that
    could have exported anything.

    *judged_public*: the backend resolved this declaration's visibility to
    ``PUBLIC`` on evidence **other than** the export lookup. Two real
    fallbacks do that, and both are contract judgements rather than
    observations of the binary: a customisation point object that was never
    ODR-used has no emitted symbol at all
    (``dumper_castxml._variable_visibility``), and a constructor/destructor
    with no contrary attribute is "declared public without contrary
    evidence" (``_ctor_or_dtor_visibility``). Recording only the export
    lookup for those left (b) unknown *and* (c) confirmed false, which made
    ``in_public_surface`` answer ``False`` and silently dropped the CPO from
    ``detect_cpo_kind_changed`` (CodeRabbit review). A pre-v46 snapshot did
    not have that problem, because the bridge reads (b) straight off the
    ``PUBLIC`` the fallback stored — so this is what keeps a fresh dump
    agreeing with a stored one.

    Deliberately ``PARTIAL``: it is a judgement the producer derived from
    its own attribute evidence, not a public-header set it was handed.
    """
    # (b) keeps reading any non-absent tier as export evidence, exactly as
    # before the tiers were explicit: narrowing it to `DYNAMIC` would change
    # which declarations `has_observed_contract_evidence` protects, i.e. move
    # findings, and that is a separate decision from making (c) honest.
    exported_any: bool | None = (
        exported is not ExportMatch.ABSENT
        if isinstance(exported, ExportMatch)
        else exported
    )
    return {
        "declared_in_headers_fact": Fact.present(True, producer=producer),
        # A confirmed export is positive evidence of contract membership
        # even with no public-header set declared. Its absence is *not*
        # the negative: an unexported inline function declared in a public
        # header is an ordinary member of the promised surface, so this
        # stays unknown until `tag_provenance` can answer from a real
        # scope selection.
        "in_public_contract_fact": (
            Fact.present(True, producer=producer)
            if exported_any
            else Fact.partial(
                True,
                "backend resolved visibility to public without export evidence",
                producer=producer,
            )
            if judged_public
            else Fact.not_collected(
                "no public-header set declared and no export evidence",
                producer=producer,
            )
        ),
        "binary_exported_fact": (
            Fact.not_collected("no binary in this dump", producer=producer)
            if exported is None
            else binary_exported_fact_for(exported, producer=producer)
        ),
    }


def debug_info_surface_facts(
    *, exported: bool | ExportMatch, producer: str | None = "dwarf"
) -> dict[str, Any]:
    """Facts for a declaration recovered from debug info (DWARF/BTF/CTF).

    Debug info records where a definition was compiled, which is not
    evidence about the *available headers* a consumer compiles against —
    so (a) stays unknown. (c) is real: the extractor checked the dynamic
    export set to admit the entity in the first place.

    *exported* must be that **real lookup's** answer, not a re-derivation
    from the record's legacy ``visibility``. They are not the same for one
    case that matters: a ``DW_AT_deleted`` subprogram deliberately bypasses
    the admission check and keeps a synthetic ``Visibility.PUBLIC`` so the
    deleted declaration stays available for cross-reference — while having,
    by construction, no symbol in the binary at all. Reading the enum there
    would record an export that does not exist, and every consumer of this
    fact (``binary_exported``, bundle signature evidence, the report's
    ``surface_facts``) would trust it (Codex review, P2).
    """
    return {
        "declared_in_headers_fact": Fact.not_collected(
            "debug info carries no public-header evidence", producer=producer
        ),
        "in_public_contract_fact": (
            Fact.present(True, producer=producer)
            if (
                exported is not ExportMatch.ABSENT
                if isinstance(exported, ExportMatch)
                else exported
            )
            else Fact.not_collected(
                "no public-header set declared and no export evidence",
                producer=producer,
            )
        ),
        "binary_exported_fact": binary_exported_fact_for(exported, producer=producer),
    }


def export_table_surface_facts(
    *, headers_parsed: bool = False, producer: str | None = None
) -> dict[str, Any]:
    """Facts for an entry synthesized from an export table alone.

    *headers_parsed* says whether this run parsed headers at all. Only
    then is "absent from the headers" an observation; without it, (a) is
    unknown — the whole point of the split, since a headerless dump must
    not start claiming every symbol is undeclared.
    """
    return {
        "declared_in_headers_fact": (
            Fact.present(False, producer=producer)
            if headers_parsed
            else Fact.not_collected("no headers parsed in this dump", producer=producer)
        ),
        "in_public_contract_fact": Fact.not_collected(
            "export-table entry with no contract evidence", producer=producer
        ),
        "binary_exported_fact": Fact.present(True, producer=producer),
    }


def unread_export_fact(
    state: ExportTableState, *, producer: str | None
) -> Fact[bool] | None:
    """(c) for a declaration an export lookup answered ``ABSENT``, given
    whether the table was read -- ``None`` when the table was read, i.e.
    ``ABSENT`` is a real observation and ``PRESENT(False)`` stands."""
    if state is ExportTableState.READ:
        return None
    if state is ExportTableState.NO_BINARY:
        return Fact.not_collected("no binary in this dump", producer=producer)
    return Fact.failed(
        "export table not read (no parse recorded); absence not established",
        producer=producer,
    )


def reconcile_export_absence(snapshot: Any, state: ExportTableState) -> int:
    """Withdraw every confirmed export absence *snapshot* records when its
    export table was not actually read.

    One post-extraction choke point rather than a flag threaded into each
    producer: the castxml, clang and DWARF producers (and a multi-TU
    manifest dump, and the hybrid merge) all look declarations up in the one
    table the format builder read, so whether that table was read is a fact
    about the dump, decided once, here, from the read. Only a
    ``PRESENT(False)``/``PARTIAL(False)`` answer is rewritten; a positive
    match cannot come out of an unread table, and an already-unknown fact
    stays as it was. Returns how many facts were withdrawn.
    """
    if state is ExportTableState.READ:
        return 0
    withdrawn = 0
    for decl in (*snapshot.functions, *snapshot.variables):
        fact = getattr(decl, "binary_exported_fact", None)
        if (
            fact is None
            or fact.status not in (FactStatus.PRESENT, FactStatus.PARTIAL)
            or fact.value is not False
        ):
            continue
        replacement = unread_export_fact(state, producer=fact.producer)
        if replacement is not None:
            decl.binary_exported_fact = replacement
            withdrawn += 1
    return withdrawn
