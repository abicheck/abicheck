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

"""The release-level public-surface / reconciliation report section.

One product contract, many providers -- so the release report gains one
section stating the contract's own reconciliation, and the per-library
tables stop carrying a copy of it.

Two halves, matching this package's own rule (``abicheck/report/
AGENTS.md``): :func:`compute_release_public_surface` returns a frozen
struct of plain values and decides everything;
:func:`render_release_public_surface_markdown` formats it and decides
nothing. Every format consumes the same struct -- JSON and HTML read its
:meth:`ReleasePublicSurfaceTerms.to_dict`, Markdown the renderer below --
so no format can state a different number than another.

:func:`dedupe_shared_member_findings` is the other half of "rendered once":
a finding several members report *identically* is the same product-level
fact observed from several members (a changed public type is the canonical
case), so it is rendered once, at release level, naming every affected
member -- instead of once per DSO. It deliberately operates on the
already-projected per-member finding dicts and changes **no** verdict,
count or exit code: each member's own numbers still state that the member is
affected, so nothing is hidden or downgraded; only the duplicated *evidence*
stops being emitted N times. A finding only one member reports is left
exactly where it is, which is why a genuine per-library binary finding is
untouched by construction -- another member cannot report it identically
without also having it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..policy.release_contract_reconciliation import (
    ReleaseReconciliation,
    SideReconciliation,
)

#: Bumped when this section's own wire shape changes.
RELEASE_PUBLIC_SURFACE_SECTION_VERSION = 1

#: Fields identifying one projected member finding. Identity, not display:
#: two members reporting the same kind about the same symbol with the same
#: old/new values have observed one fact. ``description`` is deliberately
#: excluded -- it can name the member's own library file, which would make
#: every clone unique and defeat the fold.
_FINDING_IDENTITY_FIELDS = ("kind", "symbol", "old_value", "new_value")


@dataclass(frozen=True)
class SharedFinding:
    """One product-level finding several members reported identically."""

    kind: str
    symbol: str
    old_value: str | None
    new_value: str | None
    description: str
    source_location: str | None
    affected_libraries: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        out: dict[str, object] = {
            "kind": self.kind,
            "symbol": self.symbol,
            "description": self.description,
            "affected_libraries": list(self.affected_libraries),
        }
        if self.old_value is not None:
            out["old_value"] = self.old_value
        if self.new_value is not None:
            out["new_value"] = self.new_value
        if self.source_location is not None:
            out["source_location"] = self.source_location
        return out


#: Default for :attr:`ReleasePublicSurfaceTerms.markdown_item_limit`.
MARKDOWN_ITEMS_PER_LIST = 50


def _omitted_line(total: int, shown: int) -> list[str]:
    """The "+N more" line for a list cut at *shown* of *total*, or nothing."""
    if total <= shown:
        return []
    return [
        f"- … and {total - shown} more (the full list is in the JSON report, "
        "`-o json=...`)"
    ]


@dataclass(frozen=True)
class ReleasePublicSurfaceTerms:
    """Everything the release public-surface section states, as plain values."""

    version: int = RELEASE_PUBLIC_SURFACE_SECTION_VERSION
    #: Per-side reconciliation records (``old`` absent with no baseline).
    sides: Mapping[str, Mapping[str, object]] = field(default_factory=dict)
    #: Release-level ``public_not_exported`` findings, evolution-stated.
    missing_exports: tuple[Mapping[str, object], ...] = ()
    #: Product-level findings folded out of the per-library tables.
    shared_findings: tuple[SharedFinding, ...] = ()
    #: ``{member: count}`` of exports no public header declares.
    undocumented_exports_by_member: Mapping[str, int] = field(default_factory=dict)
    #: The acquisition ledger's own counts -- the instrumentation that shows
    #: one header acquisition per side rather than one per member.
    acquisition: Mapping[str, object] = field(default_factory=dict)
    coverage_warnings: tuple[str, ...] = ()
    evaluated: bool = False
    #: How many entries of each per-symbol list the Markdown section spells
    #: out. A real product carries tens of thousands of release-level
    #: findings; rendering every one made this section alone megabytes of
    #: Markdown nobody reads. The JSON keeps the full lists; this only
    #: bounds a human-facing projection. Not part of ``to_dict``.
    markdown_item_limit: int = MARKDOWN_ITEMS_PER_LIST

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "version": self.version,
            "evaluated": self.evaluated,
            "sides": {name: dict(rec) for name, rec in sorted(self.sides.items())},
            "missing_exports": [dict(f) for f in self.missing_exports],
            "shared_findings": [f.to_dict() for f in self.shared_findings],
            "undocumented_exports_by_member": dict(
                sorted(self.undocumented_exports_by_member.items())
            ),
            "acquisition": dict(self.acquisition),
        }
        if self.coverage_warnings:
            out["coverage_warnings"] = list(self.coverage_warnings)
        return out


def _finding_dict(change: Any) -> dict[str, object]:
    """Project one release-level :class:`Change` for the report."""
    out: dict[str, object] = {
        "kind": change.kind.value,
        "symbol": change.symbol,
        "description": change.description,
        "scope": "release",
    }
    if change.old_value is not None:
        out["old_value"] = change.old_value
    if change.source_location is not None:
        out["source_location"] = change.source_location
    evolution = getattr(change, "cross_source_evolution", None)
    if evolution is not None:
        out["cross_source_evolution"] = getattr(evolution, "value", str(evolution))
    return out


def compute_release_public_surface(
    reconciliation: ReleaseReconciliation | None,
    *,
    acquisition: Mapping[str, object] | None = None,
    shared_findings: Sequence[SharedFinding] = (),
) -> ReleasePublicSurfaceTerms:
    """Assemble the section from an already-computed reconciliation."""
    if reconciliation is None:
        return ReleasePublicSurfaceTerms(
            acquisition=dict(acquisition or {}),
            shared_findings=tuple(shared_findings),
            evaluated=False,
        )
    sides: dict[str, Mapping[str, object]] = {}
    for side in (reconciliation.old, reconciliation.new):
        if isinstance(side, SideReconciliation):
            sides[side.side] = side.to_dict()
    return ReleasePublicSurfaceTerms(
        sides=sides,
        missing_exports=tuple(_finding_dict(c) for c in reconciliation.findings),
        shared_findings=tuple(shared_findings),
        undocumented_exports_by_member=dict(
            reconciliation.undocumented_exports_by_member
        ),
        acquisition=dict(acquisition or {}),
        coverage_warnings=tuple(reconciliation.coverage_warnings),
        evaluated=reconciliation.evaluated,
    )


def render_release_public_surface_markdown(terms: ReleasePublicSurfaceTerms) -> str:
    """Format *terms*. Decides nothing -- every number comes from the struct."""
    # Deliberately *not* gated on `evaluated`: a side whose surface could not
    # be acquired is unevaluated by definition, and returning "" for it would
    # render a failed extractor as silence -- the inversion this whole model
    # refuses (the JSON already carries `surface_resolvable: false` and the
    # reason; a Markdown reader must not be the one consumer who sees
    # nothing). Empty only when there is genuinely nothing to state.
    if not terms.sides and not terms.shared_findings:
        return ""
    lines: list[str] = ["", "## Release public surface", ""]
    for name in ("old", "new"):
        rec = terms.sides.get(name)
        if rec is None:
            continue
        if not rec.get("surface_resolvable"):
            reason = rec.get("coverage_reason") or "not acquired"
            lines.append(f"- **{name}**: public surface unresolved — {reason}")
            continue
        lines.append(
            f"- **{name}**: "
            f"{rec['public_declarations_with_export_obligation']} public declaration(s) "
            f"with an export obligation, "
            f"{rec['satisfied_by_bundle_exports']} satisfied by bundle exports, "
            f"{len(rec['missing_from_bundle'])} missing, "  # type: ignore[arg-type]
            f"{len(rec['unresolved_under_incomplete_coverage'])} unresolved; "  # type: ignore[arg-type]
            f"{rec['exports_total']} export(s) total, "
            f"{rec['exports_declared_in_headers']} declared in public headers, "
            f"{rec['exports_not_declared_in_headers']} not declared; "
            f"coverage {'complete' if rec['coverage_complete'] else 'INCOMPLETE'}"
        )
    if terms.missing_exports:
        lines += ["", "### Declarations no bundle member exports", ""]
        limit = terms.markdown_item_limit
        for finding in terms.missing_exports[:limit]:
            state = finding.get("cross_source_evolution")
            suffix = f" ({state})" if state else ""
            lines.append(f"- `{finding['symbol']}`{suffix} — {finding['description']}")
        lines += _omitted_line(len(terms.missing_exports), limit)
    if terms.shared_findings:
        lines += [
            "",
            "### Product-level findings (reported once, not per library)",
            "",
        ]
        limit = terms.markdown_item_limit
        for shared in terms.shared_findings[:limit]:
            libs = ", ".join(shared.affected_libraries)
            lines.append(
                f"- `{shared.symbol}` [{shared.kind}] — {shared.description} "
                f"(affects: {libs})"
            )
        lines += _omitted_line(len(terms.shared_findings), limit)
    if terms.coverage_warnings:
        lines += ["", "### Coverage", ""]
        lines += [f"- {w}" for w in terms.coverage_warnings]
    if terms.acquisition:
        lines += [
            "",
            f"Header acquisitions: {terms.acquisition.get('acquisitions', 0)} "
            f"(reused {terms.acquisition.get('reuses', 0)} time(s)).",
        ]
    return "\n".join(lines) + "\n"


def _identity(finding: Mapping[str, object]) -> tuple[object, ...]:
    return tuple(finding.get(name) for name in _FINDING_IDENTITY_FIELDS)


@dataclass(frozen=True)
class SharedFindingFold:
    """The result of folding identically-reported member findings."""

    shared: tuple[SharedFinding, ...] = ()
    #: ``{library: number of findings folded out of its table}``.
    folded_by_library: Mapping[str, int] = field(default_factory=dict)


def dedupe_shared_member_findings(
    library_results: Sequence[dict[str, Any]],
) -> SharedFindingFold:
    """Fold findings several members report identically into one each.

    Mutates each member entry's ``findings``/``findings_view`` list in place
    (removing the promoted duplicates and recording
    ``product_level_findings``) and returns the promoted set with full
    member attribution. Counts, verdicts and exit codes are untouched by
    design -- see this module's docstring.

    Deterministic: members are visited in their existing report order (which
    the release fan-out already fixes to ``matched_keys`` order regardless
    of completion timing), and the promoted findings are ordered by
    identity, so a parallel and a sequential run of the same release fold
    identically.
    """
    occurrences: dict[tuple[object, ...], list[str]] = {}
    representative: dict[tuple[object, ...], Mapping[str, object]] = {}
    for entry in library_results:
        library = str(entry.get("library", ""))
        for finding in entry.get("findings") or ():
            if not isinstance(finding, Mapping):
                continue
            key = _identity(finding)
            occurrences.setdefault(key, []).append(library)
            representative.setdefault(key, finding)

    promoted = {key for key, libs in occurrences.items() if len(set(libs)) > 1}
    if not promoted:
        return SharedFindingFold()

    shared: list[SharedFinding] = []
    for key in sorted(promoted, key=lambda k: tuple(str(part) for part in k)):
        rep = representative[key]
        shared.append(
            SharedFinding(
                kind=str(rep.get("kind", "")),
                symbol=str(rep.get("symbol", "")),
                old_value=(
                    None if rep.get("old_value") is None else str(rep["old_value"])
                ),
                new_value=(
                    None if rep.get("new_value") is None else str(rep["new_value"])
                ),
                description=str(rep.get("description", "")),
                source_location=(
                    None
                    if rep.get("source_location") is None
                    else str(rep["source_location"])
                ),
                affected_libraries=tuple(sorted(set(occurrences[key]))),
            )
        )

    folded_by_library: dict[str, int] = {}
    for entry in library_results:
        library = str(entry.get("library", ""))
        for field_name in ("findings", "findings_view"):
            current = entry.get(field_name)
            if not isinstance(current, list):
                continue
            kept = [
                finding
                for finding in current
                if not (isinstance(finding, Mapping) and _identity(finding) in promoted)
            ]
            removed = len(current) - len(kept)
            if removed:
                entry[field_name] = kept
                if field_name == "findings":
                    folded_by_library[library] = (
                        folded_by_library.get(library, 0) + removed
                    )
                    entry["product_level_findings"] = folded_by_library[library]
    return SharedFindingFold(
        shared=tuple(shared),
        folded_by_library=dict(sorted(folded_by_library.items())),
    )


def assemble_release_public_surface(
    stage: Any,
    library_results: Sequence[dict[str, Any]],
) -> ReleasePublicSurfaceTerms:
    """Fold duplicated member findings and assemble the section.

    *stage* is ``workflows.release_public_surface.ReleaseSurfaceStage``
    (duck-typed rather than imported, keeping this renderer's own imports
    inward-only). The fold runs unconditionally -- it is a reporting-layer
    de-duplication that changes no count, verdict or exit code, so it is
    correct for a release whose surface stage did not run either.
    """
    fold = dedupe_shared_member_findings(library_results)
    return compute_release_public_surface(
        getattr(stage, "reconciliation", None),
        acquisition=stage.ledger.to_dict() if stage is not None else {},
        shared_findings=fold.shared,
    )
