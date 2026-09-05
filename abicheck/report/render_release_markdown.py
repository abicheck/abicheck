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

from pathlib import Path
from typing import cast

from ..bundle import render_bundle_findings_markdown
from ..bundle_models import BundleDiffResult
from ..checker_types import DiffResult

__all__ = [
    "_release_md_bundle_findings",
    "_release_md_changed_libraries",
    "_release_md_coverage_warnings",
    "_release_md_libraries_table",
    "_release_md_matrix_findings",
]


def _release_md_libraries_table(
    library_results: list[dict[str, object]],
    emoji: dict[str, str],
) -> list[str]:
    """Markdown per-library results table."""
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


def _release_md_changed_libraries(
    removed_keys: list[str],
    added_keys: list[str],
    old_map: dict[str, Path],
    new_map: dict[str, Path],
) -> list[str]:  # Markdown sections listing removed/added libraries.
    lines: list[str] = []
    if removed_keys:
        lines += ["", "## ⚠️ Removed Libraries", ""]
        lines += [f"- `{old_map[k].name}`" for k in removed_keys]
    if added_keys:
        lines += ["", "## ℹ️ Added Libraries", ""]
        lines += [f"- `{new_map[k].name}`" for k in added_keys]
    return lines


def _release_md_bundle_findings(bundle_result: BundleDiffResult | None) -> list[str]:
    """Markdown section for cross-library (bundle) findings. G38 P0-D: a partial ``analysis_errors`` warning is rendered even when ``bundle_findings`` is empty -- an empty finding list after a raised exception means "nothing was checked", not "nothing was found", and a reader must not conflate the two."""
    lines: list[str] = []
    if bundle_result is not None and bundle_result.analysis_errors:
        lines += ["", "## ⚠️ Bundle Analysis Warnings", ""]
        lines += [f"- {msg}" for msg in bundle_result.analysis_errors]
    if bundle_result is None or not bundle_result.bundle_findings:
        return lines
    lines += [
        "",
        "## 🔗 Bundle (Cross-Library) Findings",
        "",
        *render_bundle_findings_markdown(bundle_result.bundle_findings),
    ]
    return lines


def _release_md_matrix_findings(matrix_result: DiffResult | None) -> list[str]:
    """Markdown section for build-configuration (matrix) findings."""
    if matrix_result is None or not matrix_result.changes:
        return []
    lines = ["", "## 🛠️ Build-Configuration (Matrix) Findings", ""]
    for c in matrix_result.changes:
        lines.append(
            f"- **{c.kind.value}**" + (f" — `{c.symbol}`" if c.symbol else ""),
        )
        lines.append(f"  - {c.description}")
    return lines
