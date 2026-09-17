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

"""The release (directory/package) Markdown report's per-section renderers.

Moved verbatim out of ``cli_compare_release_helpers.py`` (a ``no_growth``
module at its baseline) when ADR-065 S2 added the ``comparison_scope``
section; they take plain per-library result dicts, a
``BundleDiffResult``, or a ``DiffResult`` and return Markdown lines --
pure projections in this package's sense (``AGENTS.md``: format, decide
nothing). ``cli_compare_release_helpers._format_release_markdown`` is the
whole-document assembler and still imports them by their old names.

``_release_md_changed_libraries`` renders the **proven** removed/added
sets since S2 (ADR-065 D2): its callers pass
``ScopeAcquisitionRecord.proven_removed_members``' keys, never the raw
``unmatched_old`` set difference, which the ``comparison_scope`` section
reports under its own name.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

from ..bundle import render_bundle_findings_markdown
from ..bundle_models import BundleDiffResult
from ..checker_types import Change, DiffResult
from .release_change_inventory import release_inventory_counters
from .render_text import format_hygiene_note

__all__ = [
    "_release_md_bundle_findings",
    "_release_md_changed_libraries",
    "_release_md_coverage_warnings",
    "_release_md_evidence_contract",
    "_release_md_libraries_table",
    "_release_md_matrix_findings",
]


def _release_md_libraries_table(
    library_results: list[dict[str, object]],
    emoji: dict[str, str],
    all_library_results: Sequence[Mapping[str, object]] | None = None,
) -> list[str]:
    """Markdown per-library results table.

    *all_library_results* is the **unfiltered** member list, from which the
    change-versus-inventory split is folded here
    (``report.release_change_inventory.release_inventory_counters``). It is
    a separate parameter rather than read off *library_results* because
    that one may be a ``--show-only`` view: a filtered run's stated split
    must be the release's real one. When the release carries standing
    hygiene, a note below the table states it -- the same numbers the JSON
    ``change_inventory`` block and the oneline's hygiene clause carry, from
    the same fold, so the three renders of one release cannot disagree.

    The table's own columns are deliberately **unchanged**: ``Breaking``/
    ``Source``/``Risk``/``Additions`` have always been each member's
    inclusive finding counts, and a consumer reading them (or diffing two
    releases' tables) is entitled to that meaning. The split is stated
    beside them rather than silently subtracted out of them.
    """
    lines = [
        "",
        "## Libraries",
        "",
        "| Library | Verdict | Breaking | Source | Risk | Additions |",
        "|---|---|---|---|---|---|",
    ]
    for lib in library_results:
        em = emoji.get(str(lib["verdict"]), "?")
        lines.append(
            f"| `{lib['library']}` | {em} `{lib['verdict']}` "
            f"| {lib.get('breaking', '—')} | {lib.get('source_breaks', '—')} "
            f"| {lib.get('risk_changes', '—')} | {lib.get('compatible_additions', '—')} |"
        )
    change_inventory = (
        release_inventory_counters(all_library_results)
        if all_library_results is not None
        else None
    )
    note = format_hygiene_note(change_inventory)
    if note:
        observed = (
            change_inventory.get("compatibility_changes", 0) if change_inventory else 0
        )
        lines += [
            "",
            f"> Counts above are inclusive. Of them, {observed} "
            f"{'is' if observed == 1 else 'are'} a change this release "
            f"observed{note.replace('; hygiene:', '; the rest is standing inventory —', 1)}.",
        ]
    return lines


def _release_md_coverage_warnings(
    library_results: list[dict[str, object]],
) -> list[str]:
    """Per-library `coverage_warnings` (e.g. same-binary) -- absent when none carry any (Codex review: the release table alone omits this signal)."""
    entries = [
        f"- `{lib['library']}`: {w}"
        for lib in library_results
        for w in cast(list[str], lib.get("coverage_warnings") or [])
    ]
    return ["", "## ⚠️ Coverage Warnings", "", *entries] if entries else []


def _release_md_pattern_modulations(
    library_results: list[dict[str, object]],
) -> list[str]:
    """Which rule reclassified which finding, per library, in the artifact.

    ADR-027 pattern modulation is a *reclassification*, which ADR-067 names
    as a disposition — so a release whose breaking findings a rule demoted
    may not render a compatible Markdown report that names neither the rule
    nor the reason. The release fan-out carried this only as a private
    ``_pattern_modulations_text`` key that its caller pops and writes to
    stderr, so the requested artifact had nothing (Codex review, PR #1284);
    a terminal log is not a report, and retiring ``--view patterns`` removed
    the last way to ask for one.

    Reuses the scalar path's row renderer rather than re-spelling the table,
    so the two documents cannot disagree about a modulation's columns; only
    the per-library heading is added here. Absent unless a rule fired, which
    is every run with ADR-027's opt-in ``--pattern-verdicts`` off — i.e. the
    default — so no existing release report changes.
    """
    from .pattern_modulations_markdown import render_pattern_modulations_from_mapping

    out: list[str] = []
    for lib in library_results:
        # The renderer takes the modulation *list*, not the mapping holding
        # it -- passing `lib` iterates the dict's keys, which are strings,
        # and silently renders nothing (caught by verifying this path against
        # a real `PatternModulation` rather than trusting the call).
        rows = render_pattern_modulations_from_mapping(
            lib.get("pattern_modulations"), include_heading=False
        )
        if rows:
            out += ["", f"### `{lib['library']}`", *rows]
    return ["", "## 🎚️ Pattern-Modulated Findings", *out] if out else []


def _release_md_disposed_findings(
    library_results: list[dict[str, object]],
) -> list[str]:
    """What a rule or scoping decision disposed of, per library, by name.

    ADR-067's record-before-disposing rule applies to the artifact a user
    *requested*, not only to the terminal: a release that suppressed or
    scoped out its entire breaking set could otherwise render a passing
    Markdown report naming neither the findings nor the rules that hid them,
    with the detail only ever reaching a transient stderr echo (Codex review,
    PR #1284 -- raised twice, the second time observing that the structured
    blocks this slice added reached JSON and had no Markdown consumer).

    Reads the same per-library blocks the release JSON carries
    (``suppression``/``surface_scope``/``build_context_reconciled``, built by
    ``reporter.disposition_ledger_blocks``), so the two formats cannot
    disagree about what was disposed. Absent when nothing was, which keeps
    every release report produced without those settings unchanged. The
    aggregate ``disposition_audit`` section still renders below; it carries
    the *counts* and rule provenance, and this carries the findings those
    counts stand for.
    """
    # Two independent ways a cell can break this table, so both are handled:
    # a *raw* pipe in a free-form suppression reason or a symbol, escaped here
    # by `md_cell`; and a pipe that does not exist yet at this point because
    # the whole-document demangle pass introduces it later
    # (`Foo::operator|`), handled by that pass's own `escape_table_pipes`
    # (CodeRabbit review, PR #1284). Escaping here alone cannot cover the
    # second, which is the subtlety worth naming.
    from .markdown_text import md_cell

    rows: list[str] = []
    for lib in library_results:
        name = lib["library"]
        suppression = cast("dict[str, object]", lib.get("suppression") or {})
        for entry in cast(
            "list[dict[str, object]]", suppression.get("suppressed_changes") or []
        ):
            rule = cast("dict[str, object]", entry.get("rule") or {})
            why = rule.get("reason") or rule.get("id") or "suppressed"
            rows.append(
                f"| `{md_cell(name)}` | suppressed | `{md_cell(entry.get('kind', '?'))}` "
                f"| `{md_cell(entry.get('symbol', '?'))}` | {md_cell(why)} |"
            )
        scope = cast("dict[str, object]", lib.get("surface_scope") or {})
        for entry in cast(
            "list[dict[str, object]]", scope.get("out_of_surface_changes") or []
        ):
            why = entry.get("reason") or "outside the public surface"
            rows.append(
                f"| `{md_cell(name)}` | scoped out | `{md_cell(entry.get('kind', '?'))}` "
                f"| `{md_cell(entry.get('symbol', '?'))}` | {md_cell(why)} |"
            )
        # The third disposition, and the one this section shipped without:
        # a release can clear its entire breaking set through ADR-039
        # build-context reconciliation, with no suppression document and no
        # public-surface scoping in play at all, so neither loop above sees
        # anything (Codex review, PR #1284).
        reconciled = cast(
            "dict[str, object]", lib.get("build_context_reconciled") or {}
        )
        for entry in cast("list[dict[str, object]]", reconciled.get("changes") or []):
            why = entry.get("reason") or "reconciled against build context"
            rows.append(
                f"| `{md_cell(name)}` | reconciled | `{md_cell(entry.get('kind', '?'))}` "
                f"| `{md_cell(entry.get('symbol', '?'))}` | {md_cell(why)} |"
            )
    if not rows:
        return []
    return [
        "",
        "## 🔕 Disposed Findings",
        "",
        "Detected, then disposed of by a rule or by scoping. Listed because a "
        "passing release may not hide them.",
        "",
        "| Library | Disposition | Kind | Symbol | Rule / reason |",
        "|---|---|---|---|---|",
        *rows,
    ]


def _release_md_evidence_contract(
    library_results: list[dict[str, object]],
) -> list[str]:
    """Members whose pinned ``--depth`` rung their own evidence never reached.

    ADR-064's exit-7 axis, rendered because this document is written *before*
    the exit is taken: without it a release exiting 7 produced a Markdown
    report with no mention of why, the same way its JSON said `exit.code: 0`
    and its JUnit said `errors="0"` before those were fixed (Codex review).
    Absent when no member recorded the axis, so a run with no ``--depth`` pin
    is unchanged.

    States the axis's **contribution**, never "this release exits 7": a
    dominant axis can outrank it, and does -- a `not_comparable` member sends
    the same release to 16 (`policy.exit_decision_precedence.
    resolve_release_exit_decision`). Only the canonical `ExitDecision` knows
    the outcome; this section knows one input to it, and says only that
    (Codex review). Same phrasing the coverage-warnings section above uses,
    for the same reason.
    """
    affected = [
        f"- `{lib['library']}`"
        for lib in library_results
        if lib.get("evidence_contract_error_contribution")
    ]
    if not affected:
        return []
    return [
        "",
        "## ⚠️ Requested Evidence Depth Not Reached",
        "",
        *affected,
        "",
        "The pinned `--depth` rung was not met for the members above, so their "
        "findings rest on shallower evidence than was asked for. Contributes 7 "
        "to the release exit code (ADR-064 evidence-contract axis).",
    ]


def _release_md_changed_libraries(
    removed_keys: list[str],
    added_keys: list[str],
    old_map: dict[str, Path],
    new_map: dict[str, Path],
) -> list[str]:  # Markdown sections listing removed/added libraries.
    """The Markdown lines listing each library whose comparison reported changes."""
    lines: list[str] = []
    if removed_keys:
        lines += ["", "## ⚠️ Removed Libraries", ""]
        lines += [f"- `{old_map[k].name}`" for k in removed_keys]
    if added_keys:
        lines += ["", "## ℹ️ Added Libraries", ""]
        lines += [f"- `{new_map[k].name}`" for k in added_keys]
    return lines


def _release_md_bundle_findings(
    bundle_result: BundleDiffResult | None, findings: list[Any]
) -> list[str]:
    """Markdown section for cross-library (bundle) findings. G38 P0-D: a partial ``analysis_errors`` warning is rendered even when ``bundle_findings`` is empty -- an empty finding list after a raised exception means "nothing was checked", not "nothing was found", and a reader must not conflate the two.

    *findings* (Codex review, fresh evidence, PR #1154 follow-up: "Move
    release filtering out of the Markdown renderer") is *bundle_result*'s
    ``--view show=``-filtered finding list, already computed by the caller
    (``cli_compare_release_helpers.release_bundle_findings_for_view``) --
    this renderer only ever formats it. `report/AGENTS.md`'s renderer
    contract forbids a renderer from filtering findings itself (an earlier
    revision called that filter function from here directly); the caller
    computes the identical projection its own JSON rendering uses, so the
    two formats still can never disagree about which bundle findings a
    given ``show_only`` selection keeps -- only which layer decides that
    now differs.
    """
    lines: list[str] = []
    if bundle_result is not None and bundle_result.analysis_errors:
        lines += ["", "## ⚠️ Bundle Analysis Warnings", ""]
        lines += [f"- {msg}" for msg in bundle_result.analysis_errors]
    if not findings:
        return lines
    lines += [
        "",
        "## 🔗 Bundle (Cross-Library) Findings",
        "",
        *render_bundle_findings_markdown(findings),
    ]
    return lines


def _release_md_matrix_findings(
    matrix_result: DiffResult | None, changes: list[Change]
) -> list[str]:
    """Markdown section for build-configuration (matrix) findings.

    *changes* (Codex review, fresh evidence, PR #1154 follow-up: "Move
    release filtering out of the Markdown renderer") is *matrix_result*'s
    ``--view show=``-filtered change list, already computed by the caller
    (``cli_compare_release_helpers.release_matrix_changes_for_view``) --
    see :func:`_release_md_bundle_findings`'s own docstring for the full
    rationale; this renderer no longer calls that filter itself.
    """
    if matrix_result is None or not matrix_result.changes:
        return []
    if not changes:
        return []
    lines = ["", "## 🛠️ Build-Configuration (Matrix) Findings", ""]
    for c in changes:
        lines.append(
            f"- **{c.kind.value}**" + (f" — `{c.symbol}`" if c.symbol else ""),
        )
        lines.append(f"  - {c.description}")
    return lines
