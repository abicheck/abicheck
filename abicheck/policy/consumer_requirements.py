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

"""Evaluate a consumer's requirements against a library's exports (ADR-005).

Pure: every function here takes typed facts
(:mod:`abicheck.model.consumer_requirements`) and library-diff ``Change``
objects and returns findings -- no file is read. ``extract`` produces the
facts and ``workflows.consumer_scope`` wires the two together.
"""

from __future__ import annotations

import logging
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..diff_helpers import make_change
from ..model.change import Change
from ..model.change_catalog.kinds import ChangeKind
from ..model.consumer_requirements import (
    AppRequirements,
    ConsumerImportFacts,
    LibraryExportFacts,
    pe_import_ordinal,
)
from ..model.evidence_status import is_cross_source_resolved
from ..model.name_decoration import elf_version
from .classification import Verdict, compute_verdict

if TYPE_CHECKING:
    from ..policy_file import PolicyFile

log = logging.getLogger(__name__)


def change_covers_symbol(change: Change, symbol: str) -> bool:
    """Does *change* already account for *symbol* (exact, demangled, or via
    ``affected_symbols``)?"""
    if change.symbol == symbol:
        return True
    from ..demangle import demangle as _demangle_symbol

    plain = _demangle_symbol(change.symbol)
    if plain and plain == symbol:
        return True
    return bool(change.affected_symbols and symbol in change.affected_symbols)


def is_relevant_to_app(change: Change, app: AppRequirements) -> bool:
    """Does this change affect a symbol the application uses?

    FIX-A Part 3: handles two symbol format mismatches:
    1. change.symbol may be C++-mangled while app uses plain C names
    2. change.affected_symbols now includes both mangled and demangled names
    """
    if change.symbol in app.undefined_symbols:
        return True

    # change.symbol may be C++-mangled (e.g. "_Z3addii") while the app uses
    # the plain C linker name (e.g. "add").
    from ..demangle import demangle as _demangle_symbol

    plain = _demangle_symbol(change.symbol)
    if plain and plain != change.symbol and plain in app.undefined_symbols:
        return True

    # Type change affecting app's symbols (via affected_symbols enrichment).
    if change.affected_symbols:
        if app.undefined_symbols & set(change.affected_symbols):
            return True

    # ELF SONAME changes affect consumers that record the old SONAME in
    # DT_NEEDED: even with the same exported symbols, the dynamic loader may
    # fail unless the old SONAME remains available.
    if change.kind == ChangeKind.SONAME_CHANGED:
        return bool(change.old_value and change.old_value in app.needed_libs)

    # Mach-O compat version change affects all consumers
    if change.kind == ChangeKind.COMPAT_VERSION_CHANGED:
        return True

    # Symbol version removal for a version the app requires: change.symbol
    # is the version tag (e.g. "FOO_1.0"), app.required_versions maps
    # version_tag -> library_soname.
    if change.kind == ChangeKind.SYMBOL_VERSION_DEFINED_REMOVED:
        if change.symbol in app.required_versions:
            return True

    return False


def partition_app_changes(
    changes: Iterable[Change],
    app_reqs: AppRequirements,
) -> tuple[list[Change], list[Change]]:
    """Split *changes* into (relevant-to-app, irrelevant-to-app)."""
    relevant: list[Change] = []
    irrelevant: list[Change] = []
    for change in changes:
        (relevant if is_relevant_to_app(change, app_reqs) else irrelevant).append(
            change
        )
    return relevant, irrelevant


def uncovered_missing_symbols(
    missing: Iterable[str],
    relevant_changes: Iterable[Change],
) -> list[str]:
    """*missing* entries not already represented by a *relevant_changes* Change.

    A required symbol that was removed shows up twice in a scoped result:
    once in ``missing_symbols``/``missing_entrypoints`` (absent from the new
    export table) and once as the diff Change that actually removed it (e.g.
    ``FUNC_REMOVED``) in ``breaking_for_app``/``breaking_for_host``. Callers
    that derive a severity-scheme finding count from both must not count
    that as two ABI breaks — this is the missing-symbol side of that dedup
    (Codex review): only symbols with no matching Change are genuinely
    "extra" (e.g. a symbol dropped for a reason the diff itself never
    surfaced as a Change, such as a versioned-symbol default retarget).
    """
    changes = list(relevant_changes)
    return [m for m in missing if not any(change_covers_symbol(c, m) for c in changes)]


def scope_requirements_to_library(
    consumer: ConsumerImportFacts,
    old_lib: LibraryExportFacts,
) -> AppRequirements:
    """The consumer's requirements narrowed to what *old_lib* actually exports.

    ELF consumers with many dependencies over-collect: a symbol whose origin
    can't be attributed from version data is kept. Normalising version
    suffixes and intersecting with the old library's exports avoids false
    positives from unrelated dependencies. Non-ELF inputs are returned as is.
    """
    reqs = consumer.requirements
    if consumer.binary_format != "elf" or old_lib.binary_format != "elf":
        return reqs
    symbols = {elf_version.unversioned_name(s) for s in reqs.undefined_symbols}
    old_exports = old_lib.unversioned_exports or frozenset()
    if old_exports:
        scoped = {s for s in symbols if s in old_exports}
        if len(symbols) > len(scoped):
            log.debug(
                "appcompat scoped %d symbols to target library exports (%s)",
                len(symbols) - len(scoped),
                old_lib.label,
            )
        symbols = scoped
    else:
        log.debug(
            "appcompat scoping skipped: no exports parsed for target library (%s)",
            old_lib.label,
        )
    return AppRequirements(
        needed_libs=reqs.needed_libs,
        undefined_symbols=symbols,
        required_versions=reqs.required_versions,
    )


def missing_app_versions(
    app_reqs: AppRequirements, new_lib: LibraryExportFacts
) -> list[str]:
    """ELF version tags required by the app but absent from the new library."""
    if new_lib.versions_defined is None:
        return []
    return [v for v in app_reqs.required_versions if v not in new_lib.versions_defined]


def resolve_pe_ordinal_imports(
    app_reqs: AppRequirements,
    old_lib: LibraryExportFacts,
    new_lib: LibraryExportFacts,
) -> tuple[set[str], list[Change], set[str]]:
    """Resolve the app's ordinal-only PE imports against old/new export tables.

    A PE consumer that imports by ordinal never names its target function —
    the import is recorded as ``"ordinal:N"``, which never matches a name in
    the new export set. Cross-referencing the ordinal against both DLLs'
    export directories:

    * ordinal still exists and names the SAME function → satisfied; excluded
      from the generic missing-symbols check.
    * ordinal still exists but now names a DIFFERENT function → the app
      silently calls the wrong function with no link/load error — reported
      as PE_ORDINAL_RETARGETED rather than a generic missing symbol.
    * ordinal no longer exists at all → left in the generic missing-symbols
      set (genuinely dropped).
    * ordinal did not exist in the OLD library either → not attributable to
      this version change; left alone.

    Returns (resolved_requirement_strings, retargeted_changes,
    resolved_export_names). ``resolved_export_names`` carries the old/new
    export name(s) behind each resolved ordinal so the caller can fold them
    into the relevance check: an ordinal-only consumer has no name of its own
    to match a library diff finding for the *named* export it resolves to.
    """
    ordinal_reqs = {
        s: n
        for s in app_reqs.undefined_symbols
        if (n := pe_import_ordinal(s)) is not None
    }
    old_by_ordinal = old_lib.exports_by_ordinal
    new_by_ordinal = new_lib.exports_by_ordinal
    if (
        not ordinal_reqs
        or old_lib.binary_format != "pe"
        or old_by_ordinal is None
        or new_by_ordinal is None
    ):
        return set(), [], set()

    resolved: set[str] = set()
    retargeted: list[Change] = []
    export_names: set[str] = set()
    for req, ordinal in sorted(ordinal_reqs.items()):
        old_name = old_by_ordinal.get(ordinal)
        if old_name is None or ordinal not in new_by_ordinal:
            continue
        new_name = new_by_ordinal[ordinal]
        resolved.add(req)
        if old_name:
            export_names.add(old_name)
        if new_name:
            export_names.add(new_name)
        if new_name != old_name:
            retargeted.append(
                make_change(
                    ChangeKind.PE_ORDINAL_RETARGETED,
                    symbol=req,
                    name=f"ordinal {ordinal}",
                    old=old_name or "(unnamed)",
                    new=new_name or "(unnamed)",
                )
            )
    return resolved, retargeted, export_names


def symbol_coverage(
    new_exports: Collection[str],
    required_count: int,
    missing_count: int,
) -> float:
    """Percentage of required symbols still available in the new library.

    An empty export set is "no evidence": coverage reads 0% when anything
    was required.
    """
    if not new_exports:
        return 0.0 if required_count > 0 else 100.0
    if required_count == 0:
        return 100.0
    return (required_count - missing_count) / required_count * 100.0


def appcompat_verdict(
    missing_symbols: list[str],
    missing_versions: list[str],
    relevant_changes: list[Change],
    required_count: int,
    policy: str,
    policy_file: PolicyFile | None,
) -> Verdict:
    """The consumer-scoped compatibility verdict.

    A ``RESOLVED`` cross-source finding (present on OLD, fixed on NEW; still
    visible in ``diff.changes`` per plan F-9) that names a symbol this
    consumer imports stays in the relevant list for display/audit, but must
    not score the verdict -- the same exclusion
    ``checker._verdict_scored_population`` applies to the whole-library
    verdict (Codex review, PR #1172, round 20).
    """
    if missing_symbols or missing_versions:
        return Verdict.BREAKING
    verdict_scored = [c for c in relevant_changes if not is_cross_source_resolved(c)]
    if verdict_scored:
        if policy_file is not None:
            return policy_file.compute_verdict(verdict_scored)
        return compute_verdict(verdict_scored, policy=policy)
    return Verdict.COMPATIBLE if required_count > 0 else Verdict.NO_CHANGE


@dataclass(frozen=True)
class ConsumerRequirementEvaluation:
    """Requirements vs. exports for one consumer, before any overlay finding.

    *missing_symbols* is the raw (pre-suppression) list, sorted; *coverage*
    is computed from it, since it is an objective fact about the export
    table rather than a gate.
    """

    requirements: AppRequirements
    missing_symbols: list[str]
    missing_versions: list[str]
    relevant: list[Change]
    irrelevant: list[Change]
    coverage: float

    @property
    def required_count(self) -> int:
        return len(self.requirements.undefined_symbols)


def evaluate_consumer_requirements(
    consumer: ConsumerImportFacts,
    old_lib: LibraryExportFacts,
    new_lib: LibraryExportFacts,
    changes: Iterable[Change],
) -> ConsumerRequirementEvaluation:
    """Intersect one consumer's requirements with a library update.

    *changes* is the already-computed old→new library diff. Relevant changes
    are those touching a required symbol, version or needed SONAME, plus a
    ``PE_ORDINAL_RETARGETED`` finding per ordinal import that now names a
    different export.
    """
    reqs = scope_requirements_to_library(consumer, old_lib)
    resolved_ordinals, ordinal_retargets, resolved_names = resolve_pe_ordinal_imports(
        reqs, old_lib, new_lib
    )
    missing = sorted(
        s
        for s in reqs.undefined_symbols
        if s not in new_lib.export_names and s not in resolved_ordinals
    )
    # An ordinal-only import carries no name of its own, so a diff finding for
    # the named export the ordinal resolves to would otherwise read as
    # irrelevant; layer the resolved names into a relevance-only view.
    relevance_reqs = reqs
    if resolved_names:
        relevance_reqs = AppRequirements(
            needed_libs=reqs.needed_libs,
            undefined_symbols=reqs.undefined_symbols | resolved_names,
            required_versions=reqs.required_versions,
        )
    relevant, irrelevant = partition_app_changes(changes, relevance_reqs)
    return ConsumerRequirementEvaluation(
        requirements=reqs,
        missing_symbols=missing,
        missing_versions=missing_app_versions(reqs, new_lib),
        relevant=relevant + ordinal_retargets,
        irrelevant=irrelevant,
        coverage=symbol_coverage(
            new_lib.export_names, len(reqs.undefined_symbols), len(missing)
        ),
    )
