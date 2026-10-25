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

"""Thin renderers of the one release report (:mod:`.release_document`).

Every function here reads a finished :class:`~.release_document.ReleaseReport`
or :class:`~.release_document.StoredReleaseReport` and formats it: no
verdict, gate, filter or count is resolved here. :func:`render_release_text`
is the one per-format dispatch for the live release; the stored operand has
its own two renderers because its document shape differs.

*demangle* applies to the human formats only (Markdown); JSON/JUnit keep the
raw mangled symbol a machine consumer matches on, as single-pair ``compare``
does. JUnit takes no impact view, also matching single-pair ``compare``: an
impact table is prose with no ``<testsuite>``/``<testcase>`` projection.
"""

from __future__ import annotations

import json

from .release_document import (
    ReleaseReport,
    StoredReleaseReport,
    release_json_document,
    stored_release_json_document,
)

__all__ = [
    "render_release_json",
    "render_release_junit",
    "render_release_markdown_report",
    "render_release_text",
    "render_stored_release_json",
    "render_stored_release_markdown",
]

_VERDICT_EMOJI = {
    "NO_CHANGE": "✅",
    "COMPATIBLE": "✅",
    "COMPATIBLE_WITH_RISK": "⚠️",
    "API_BREAK": "⚠️",
    "BREAKING": "❌",
    "ERROR": "💥",
    "not_comparable": "❓",
    "unsupported": "🚫",
    "failed": "💥",
}


def render_release_json(report: ReleaseReport) -> str:
    """The release JSON text: the document, serialized."""
    return json.dumps(release_json_document(report).to_mapping(), indent=2)


def render_release_junit(report: ReleaseReport) -> str:
    """The release JUnit XML.

    Release-global matrix findings ride in as their own synthetic testsuite;
    error members (``report.junit_error_libraries``) get an error suite each;
    the release-wide deployment digest is a release-level property, so a
    release with zero completed pairs still carries it.
    """
    from ..checker_types import DiffResult
    from ..junit_report import to_junit_xml_multi
    from .junit_inventory import JunitOldOperand

    ev = report.evaluation
    pairs: list[tuple[DiffResult, JunitOldOperand]] = list(ev.diff_pairs or [])
    if ev.matrix_result is not None:
        pairs.append((ev.matrix_result, None))
    error_libs = report.junit_error_libraries
    return to_junit_xml_multi(
        pairs,
        show_only=report.show_only,
        severity_config=ev.severity_config,
        error_libraries=error_libs if error_libs else None,
        comparison_scope=ev.scope_terms.section,
        env_matrix_source_sha256=ev.env_matrix_source_sha256,
    )


def _markdown_body(report: ReleaseReport) -> str:
    from .comparison_scope import render_comparison_scope_markdown
    from .disposition_audit import (
        DispositionAudit,
        render_disposition_audit_section,
    )
    from .release_display_limits import MAX_RELEASE_FINDINGS_PER_LIBRARY
    from .release_public_api import render_release_public_api_markdown
    from .release_public_surface_section import render_release_public_surface_markdown
    from .release_surface_changes import render_release_surface_lines
    from .render_release_markdown import (
        _release_md_bundle_findings,
        _release_md_changed_libraries,
        _release_md_coverage_warnings,
        _release_md_disposed_findings,
        _release_md_evidence_contract,
        _release_md_libraries_table,
        _release_md_matrix_findings,
        _release_md_pattern_modulations,
        release_md_library_findings,
    )
    from .view import render_show_only_cli_hint

    ev = report.evaluation
    library_results = ev.library_results
    scope_section = ev.scope_terms.section
    worst_verdict = ev.worst_verdict
    verdict_cell = f"{_VERDICT_EMOJI.get(worst_verdict, '?')} `{worst_verdict}`"
    if scope_section is not None and scope_section.get("no_comparison_completed"):
        verdict_cell = "🛑 no comparison completed"
    elif (
        scope_section is not None and scope_section.get("completeness") == "incomplete"
    ):
        verdict_cell += " (compared members only — scope incompletely checked)"
    lines: list[str] = [
        "# ABI Release Comparison",
        "",
        "| | |",
        "|---|---|",
        f"| **Old** | `{ev.old_dir}` |",
        f"| **New** | `{ev.new_dir}` |",
        f"| **Verdict** | {verdict_cell} |",
    ]
    bundle_result = ev.bundle_result
    bundle_count = len(report.display_bundle_findings)
    if bundle_result is not None:
        bundle_em = _VERDICT_EMOJI.get(bundle_result.bundle_verdict.value, "?")
        lines.append(
            f"| **Bundle** | {bundle_em} `{bundle_result.bundle_verdict.value}` "
            f"({bundle_count} cross-library finding{'s' if bundle_count != 1 else ''}) |",
        )
    if ev.env_matrix_source_sha256 is not None:
        lines.append("")
        lines.append(f"- Deployment floor digest: `{ev.env_matrix_source_sha256}`")
    if report.show_only and report.filtered_counts is not None:
        counts = report.filtered_counts
        cli_hint = render_show_only_cli_hint(report.show_only)
        lines.append("")
        lines.append(
            f"> Filtered by: `{cli_hint}` "
            f"({counts['displayed']} of {counts['total']} findings shown)"
        )
        lines.append("")
    if scope_section is not None:
        lines += render_comparison_scope_markdown(scope_section)
    lines += _release_md_libraries_table(
        report.display_library_results, _VERDICT_EMOJI, library_results
    )
    if not report.show_only:
        # Unfiltered by construction; a `--view show=` render lists only the
        # findings its filter kept, so this section would contradict it.
        lines += render_release_public_api_markdown(report.display_library_results)
    lines += _release_md_coverage_warnings(library_results)
    lines += _release_md_disposed_findings(library_results)
    lines += _release_md_pattern_modulations(library_results)
    lines += _release_md_evidence_contract(library_results)
    lines += _release_md_changed_libraries(
        ev.removed_keys, ev.added_keys, dict(ev.old_map), dict(ev.new_map)
    )
    lines += release_md_library_findings(
        report.display_library_results,
        display_cap=MAX_RELEASE_FINDINGS_PER_LIBRARY,
    )
    lines += _release_md_bundle_findings(bundle_result, report.display_bundle_findings)
    lines += _release_md_matrix_findings(
        ev.matrix_result, report.display_matrix_changes
    )
    if ev.public_surface is not None:
        public_surface_markdown = render_release_public_surface_markdown(
            ev.public_surface
        )
        if public_surface_markdown:
            lines.append(public_surface_markdown)
    if not report.show_only:
        # Like the public-API section above: a `--show-only` view lists only
        # the findings its filter kept, so an unfiltered surface section would
        # contradict it. The JSON block stays full (raw-versus-effective).
        lines += render_release_surface_lines(report.surface_changes)
    lines += render_disposition_audit_section(
        DispositionAudit.from_dict(report.disposition_audit)
    )
    return "\n".join(lines)


def render_release_markdown_report(report: ReleaseReport, *, demangle: bool) -> str:
    """The release Markdown document, with its demangle pass and the
    suppression audit appended (``finish_release_markdown``)."""
    from .render_release_markdown import finish_release_markdown

    return finish_release_markdown(
        _markdown_body(report), report.evaluation.library_results, demangle=demangle
    )


def render_release_text(fmt: str, report: ReleaseReport, *, demangle: bool) -> str:
    """Render the release *report* as *fmt*.

    ``html`` is rendered from the JSON document alone, never from live state;
    ``oneline`` is the release-cardinality ``--stat`` line; any other format
    falls back to Markdown.
    """
    if fmt == "html":
        from .render_release_html import render_release_html

        return render_release_html(json.loads(render_release_json(report)))
    if fmt == "oneline":
        from . import release_change_inventory as _inventory
        from .release_oneline import format_release_oneline, release_global_counts

        ev = report.evaluation
        return format_release_oneline(
            ev.worst_verdict,
            ev.library_results,
            severity_exit_code=ev.severity_exit_code,
            env_matrix_source_sha256=ev.env_matrix_source_sha256,
            # Bundle/matrix findings belong to no library but are folded
            # into the verdict -- see `release_global_counts`.
            release_global=release_global_counts(ev.bundle_result, ev.matrix_result),
            change_inventory=_inventory.release_inventory_counters(ev.library_results),
        )
    if fmt == "junit":
        return render_release_junit(report)
    if fmt == "sarif":
        from .render_release_sarif import render_release_sarif

        return render_release_sarif(report)
    if fmt == "review":
        from .render_release_sarif import render_release_review

        return render_release_review(json.loads(render_release_json(report)))
    if fmt == "json":
        return render_release_json(report)
    return render_release_markdown_report(report, demangle=demangle)


def render_stored_release_json(report: StoredReleaseReport) -> str:
    """The stored-``BundleFacts`` comparison's JSON text."""
    return json.dumps(stored_release_json_document(report).to_mapping(), indent=2)


def render_stored_release_markdown(report: StoredReleaseReport) -> str:
    """The stored-``BundleFacts`` comparison's Markdown text.

    Markdown is a human format, so it is demangled (``demangle_text``
    rewrites only Itanium tokens and keeps the exact spelling beside them).
    """
    from ..bundle import render_bundle_findings_markdown
    from ..demangle import demangle_text
    from .release_stored_scope import markdown_scope_lines
    from .release_stored_sections import public_surface_markdown_lines

    ev = report.evaluation
    result = ev.result
    new_label = "stored facts" if ev.new_is_stored else "release directory"
    lines = [
        "# Bundle-facts comparison",
        "",
        f"- OLD (stored facts): `{ev.old_facts_path}`",
        f"- NEW ({new_label}): `{ev.new_dir}`",
        f"- **Verdict:** `{report.reported_verdict}`",
        f"- Per-library verdict: `{result.per_library_verdict.value}`",
        f"- Bundle verdict: `{result.bundle_verdict.value}`",
        "",
    ]
    if result.env_matrix_source_sha256 is not None:
        lines.append(f"- Deployment floor digest: `{result.env_matrix_source_sha256}`")
        lines.append("")
    lines += markdown_scope_lines(ev.scope_terms)
    if result.analysis_errors:
        lines.append("## Bundle analysis errors")
        lines += [f"- {msg}" for msg in result.analysis_errors]
        lines.append("")
    lines.append("## Per-library results")
    lines.append("")
    lines.append("| Library | Verdict |")
    lines.append("|---|---|")
    for diff in result.per_library:
        lines.append(f"| {diff.library} | `{diff.verdict.value}` |")
    lines.append("")
    lines += public_surface_markdown_lines(result)
    lines.append("## Bundle findings")
    lines.append("")
    bundle_lines = render_bundle_findings_markdown(result.bundle_findings)
    lines += bundle_lines if bundle_lines else ["(none)"]
    from .markdown_budget import DEMANGLED_NAME_LIMIT

    return demangle_text("\n".join(lines) + "\n", max_demangled=DEMANGLED_NAME_LIMIT)
