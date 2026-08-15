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

"""Attribute a real comparison's findings to a use-case manifest.

``compare --use-cases MANIFEST``'s engine. The attribution itself is
:func:`~abicheck.impact.use_cases.explain_use_case_impact`; what lives here
is the two-sided part around it -- resolving each side's own source graph,
unioning the two attributions, and shaping the result into the report block
``reporter``/``cli_compare_render`` emit.

Two-sided by construction, which is why this is a comparison-time concern
and not a manifest-validation one: a symbol *added* on the NEW side never
existed in OLD's graph at all, so attributing only against OLD would read
every addition as unattributed regardless of whether the NEW side's own
graph proves it reachable. Each side is explained against its own graph and
the two are unioned per symbol -- the same asymmetry, and the same fix,
``post_processing_reachability.MarkReachability`` applies to its own
``old_paths``/``new_paths`` merge.

Attribution is evidence, never speculation: a symbol absent from the mapping
is not evidence that no use case touches it, only that no declared
entrypoint could be *shown* to reach it (see ``explain_use_case_impact``'s
own contract, and the two structural limitations it documents -- a
type-shaped change can never be named, and only a consumer-compiled
entrypoint walks past its own declaration). ``unattributed_changes`` counts
exactly that, so a report never silently implies the remainder is unaffected.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ..checker_types import Change
    from ..model import AbiSnapshot
    from .use_cases import UseCaseDefinition, UseCaseResolution


@dataclass(frozen=True)
class UseCaseChange:
    """One finding named by one use case.

    *finding_id* is the report's own ``finding_id``
    (:func:`~abicheck.finding_identity.report_finding_id`), not a third
    descriptive field: ``(symbol, kind)`` does not identify a finding. One
    symbol can emit the same ``ChangeKind`` more than once -- two parameters
    of one function each changing pointer depth is the case that function's
    own docstring names -- so without it two distinct findings collapse to
    identical rows, and a consumer can neither tell them apart nor join them
    back to the report's ``findings`` array (Codex review). It is also what
    makes :meth:`UseCaseImpact.restricted_to` exact.
    """

    symbol: str
    kind: str
    finding_id: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "symbol": self.symbol,
            "kind": self.kind,
            "finding_id": self.finding_id,
        }


@dataclass(frozen=True)
class UseCaseImpact:
    """The ``use_case_impact`` report block.

    *resolutions* is the manifest's own entrypoint resolution, unioned
    across both sides' graphs the same way the attribution below is (what
    ``project validate-use-cases`` reports against one graph on its own);
    *by_use_case* is this comparison's findings attributed to the use cases
    whose entrypoints reach them; *unattributed_changes* is how many
    findings no declared entrypoint could be shown to reach.
    """

    manifest: str
    use_case_count: int
    total_changes: int
    unattributed_changes: int
    resolutions: tuple[UseCaseResolution, ...] = ()
    by_use_case: dict[str, tuple[UseCaseChange, ...]] = field(default_factory=dict)

    def restricted_to(self, changes: Sequence[Change]) -> UseCaseImpact:
        """This attribution as it applies to *changes* alone.

        ``--show-only`` filters what the report displays, and the attribution
        was built from every finding -- so the block listed changes absent
        from the findings section beside it, resurfacing exactly what the user
        filtered out (Codex review). Projecting onto the displayed set keeps
        the two halves of one report describing the same findings, the way
        ``_fold_scoped_compat_into_text`` already filters its own scoped-only
        changes.

        Exact rather than approximate because the projection joins on
        ``finding_id``: joining on ``(symbol, kind)`` would keep or drop both
        of two same-symbol same-kind findings together. Counters are
        recomputed against *changes*, so ``total_changes`` and
        ``unattributed_changes`` describe what the reader is looking at
        instead of a set they cannot see.
        """
        from ..finding_identity import report_finding_id

        displayed = {report_finding_id(c) for c in changes}
        by_use_case = {
            name: kept
            for name, entries in self.by_use_case.items()
            if (kept := tuple(c for c in entries if c.finding_id in displayed))
        }
        attributed = {c.finding_id for entries in by_use_case.values() for c in entries}
        return UseCaseImpact(
            manifest=self.manifest,
            use_case_count=self.use_case_count,
            total_changes=len(changes),
            unattributed_changes=sum(
                1 for c in changes if report_finding_id(c) not in attributed
            ),
            resolutions=self.resolutions,
            by_use_case=by_use_case,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest": self.manifest,
            "use_case_count": self.use_case_count,
            "total_changes": self.total_changes,
            "unattributed_changes": self.unattributed_changes,
            "use_cases": [
                {
                    "use_case": r.use_case,
                    "resolved_entrypoints": list(r.resolved_entrypoints),
                    "unresolved_entrypoints": list(r.unresolved_entrypoints),
                    "tests": list(r.tests),
                }
                for r in self.resolutions
            ],
            "by_use_case": {
                name: [c.to_dict() for c in changes]
                for name, changes in self.by_use_case.items()
            },
        }


def _source_graph(snapshot: AbiSnapshot | None) -> Any:
    return getattr(getattr(snapshot, "build_source", None), "source_graph", None)


def _coalesce_definitions(
    definitions: Sequence[UseCaseDefinition],
) -> tuple[UseCaseDefinition, ...]:
    """One entry per *use case*, not per manifest list item.

    A manifest may legitimately split one use case across several entries,
    and :func:`explain_use_case_impact` already unions their entrypoints by
    name -- so attribution lands under a single key while the count and the
    resolutions were computed per entry. The report then claimed
    ``use_case_count: 2`` for one use case, emitted a duplicate ``use_cases``
    row, and the text renderer printed the same combined findings under both
    (Codex review). Coalescing here is what makes those three agree with the
    attribution model rather than with the file's line count.

    First-appearance order is kept for the use cases and for each one's
    entrypoints and tests, since that is the order a manifest author reads
    their own file in; a repeat within one use case is dropped rather than
    listed twice.
    """
    from .use_cases import UseCaseDefinition as _Definition

    entrypoints: dict[str, list[str]] = {}
    tests: dict[str, list[str]] = {}
    for d in definitions:
        for name, bucket in ((d.entrypoints, entrypoints), (d.tests, tests)):
            seen = bucket.setdefault(d.use_case, [])
            seen.extend(v for v in name if v not in seen)
    return tuple(
        _Definition(
            use_case=use_case,
            entrypoints=tuple(values),
            tests=tuple(tests.get(use_case, ())),
        )
        for use_case, values in entrypoints.items()
    )


def _merge_resolutions(
    definitions: Sequence[UseCaseDefinition],
    old: Sequence[UseCaseResolution],
    new: Sequence[UseCaseResolution],
) -> tuple[UseCaseResolution, ...]:
    """Union two sides' entrypoint resolutions, resolved-wins per entrypoint.

    The same asymmetry the module docstring describes for attribution
    reaches resolution too, and reporting only OLD's answer contradicts the
    attribution sitting next to it: an entrypoint introduced on the NEW side
    resolves only against NEW's graph, so an OLD-only resolution lists it as
    unresolved beside a finding this run attributed *through* it -- and when
    only NEW carries a graph, lists nothing at all while ``by_use_case`` is
    populated. An entrypoint is therefore unresolved only when neither side
    could resolve it. Declared order is preserved the way
    :func:`resolve_use_case_entrypoints` preserves it -- by re-partitioning
    each definition's own ``entrypoints`` tuple, not by concatenating a side's
    two result tuples, which has already lost the interleaving between them.
    """
    from .use_cases import UseCaseResolution

    if not old:
        return tuple(new)
    if not new:
        return tuple(old)
    resolved_anywhere = {
        (r.use_case, entry)
        for side in (old, new)
        for r in side
        for entry in r.resolved_entrypoints
    }
    return tuple(
        UseCaseResolution(
            use_case=d.use_case,
            resolved_entrypoints=tuple(
                e for e in d.entrypoints if (d.use_case, e) in resolved_anywhere
            ),
            unresolved_entrypoints=tuple(
                e for e in d.entrypoints if (d.use_case, e) not in resolved_anywhere
            ),
            tests=d.tests,
        )
        for d in definitions
    )


def build_use_case_impact(
    definitions: Sequence[UseCaseDefinition],
    old_snapshot: AbiSnapshot,
    new_snapshot: AbiSnapshot,
    changes: Sequence[Change],
    *,
    manifest: str,
) -> UseCaseImpact | None:
    """Attribute *changes* to *definitions*, or ``None`` with no source graph.

    ``None`` rather than an empty block when neither snapshot carries a
    source graph: there is nothing to resolve entrypoints against, so an
    emitted block would read as "no use case is affected" for a run that
    never looked. The caller turns that into its own usage error or silence,
    as fits its surface.
    """
    from ..finding_identity import report_finding_id
    from .use_cases import explain_use_case_impact, resolve_use_case_entrypoints

    old_graph = _source_graph(old_snapshot)
    new_graph = _source_graph(new_snapshot)
    if old_graph is None and new_graph is None:
        return None

    # Per use case, not per manifest entry -- see `_coalesce_definitions`.
    # `explain_use_case_impact` below is given the coalesced list too, so the
    # attribution and the resolutions describe the same set.
    definitions = _coalesce_definitions(definitions)

    resolutions = _merge_resolutions(
        definitions,
        (
            resolve_use_case_entrypoints(list(definitions), old_graph)
            if old_graph is not None
            else []
        ),
        (
            resolve_use_case_entrypoints(list(definitions), new_graph)
            if new_graph is not None
            else []
        ),
    )
    symbols = {c.symbol for c in changes if c.symbol}
    mapping_old = (
        explain_use_case_impact(list(definitions), old_graph, symbols)
        if old_graph is not None
        else {}
    )
    mapping_new = (
        explain_use_case_impact(list(definitions), new_graph, symbols)
        if new_graph is not None
        else {}
    )
    named: dict[str, tuple[str, ...]] = {}
    for symbol in symbols:
        names = set(mapping_old.get(symbol, ())) | set(mapping_new.get(symbol, ()))
        if names:
            named[symbol] = tuple(sorted(names))

    by_use_case: dict[str, list[UseCaseChange]] = {}
    unattributed = 0
    for change in changes:
        reached = named.get(change.symbol, ())
        for use_case in reached:
            by_use_case.setdefault(use_case, []).append(
                UseCaseChange(
                    symbol=change.symbol,
                    kind=change.kind.value,
                    finding_id=report_finding_id(change),
                )
            )
        if not reached:
            unattributed += 1

    return UseCaseImpact(
        manifest=manifest,
        use_case_count=len(definitions),
        total_changes=len(changes),
        unattributed_changes=unattributed,
        resolutions=resolutions,
        by_use_case={k: tuple(v) for k, v in by_use_case.items()},
    )


def render_use_case_impact_lines(impact: UseCaseImpact) -> list[str]:
    """The human-facing section for a text/markdown report."""
    lines = ["", f"Use-case impact ({impact.manifest}):"]
    for resolution in impact.resolutions:
        changes = impact.by_use_case.get(resolution.use_case, ())
        lines.append(f"  {resolution.use_case}: {len(changes)} change(s)")
        for change in changes:
            lines.append(f"    - {change.symbol} ({change.kind})")
        if resolution.unresolved_entrypoints:
            lines.append(
                "    unresolved entrypoints: "
                + ", ".join(resolution.unresolved_entrypoints)
            )
    lines.append(
        f"  {impact.unattributed_changes} of {impact.total_changes} change(s) "
        "reached by no declared entrypoint (absence of proof, not proof of "
        "absence)."
    )
    return lines


def add_use_case_impact(
    d: dict[str, Any], result: Any, displayed: Sequence[Change] | None = None
) -> None:
    """Emit ``compare --use-cases``'s block into a JSON report dict.

    Lives here rather than in ``reporter.py`` for the ordinary reason that
    module's helpers keep moving out: it is at the 2000-line hard cap.

    Omitted entirely without the flag rather than emitted empty: an empty
    block would read as "no use case is affected" for a run that never
    resolved a manifest.

    *displayed*, when given, is the change list this report actually shows
    (``--show-only`` already applied); the block is projected onto it so it
    cannot name a finding the reader cannot see. ``None`` means "everything
    is displayed" and emits the block unchanged -- distinct from an empty
    list, which really is a report showing nothing.
    """
    impact = getattr(result, "use_case_impact", None)
    if isinstance(impact, UseCaseImpact):
        if displayed is not None:
            impact = impact.restricted_to(displayed)
        d["use_case_impact"] = impact.to_dict()
