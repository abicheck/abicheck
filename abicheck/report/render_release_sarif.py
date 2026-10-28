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

"""The release fan-out's SARIF and ``review`` renderings.

SARIF is the existing single-pair owner (:func:`abicheck.sarif.to_sarif`)
applied to each release member's own ``DiffResult``, one SARIF run per
member, so every run keeps its own rule catalog and ``ruleIndex`` values.
Each result is stamped with ``properties.releaseMember``, the member whose
comparison produced it, so a code-scanning consumer can locate a finding in
the release. The release's own matrix findings are one more run.

``review`` is rendered from the release JSON document, not per member: the
single-pair digest needs a per-member ``ReportEnvelope`` (old and new
snapshots) that the release fan-out does not retain, and the reviewer-facing
verdict of a release is the release document's, not any one member's.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from ..sarif import to_sarif
from .markdown_text import md_text

if TYPE_CHECKING:
    from .release_document import ReleaseReport

__all__ = ["render_release_review", "render_release_sarif"]

#: Findings listed per member in the review; the rest are counted, not dropped.
_REVIEW_FINDINGS_PER_MEMBER = 10


def render_release_sarif(report: ReleaseReport) -> str:
    """The release SARIF log: one run per member, plus the matrix run."""
    ev = report.evaluation
    members: list[tuple[str, Any]] = [
        (result.library, result) for result, _old in (ev.diff_pairs or [])
    ]
    if ev.matrix_result is not None:
        members.append((ev.matrix_result.library, ev.matrix_result))

    runs: list[dict[str, Any]] = []
    envelope_keys: dict[str, Any] = {
        "$schema": "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json",
        "version": "2.1.0",
    }
    for member, result in members:
        doc = to_sarif(
            result,
            show_only=report.show_only,
            severity_config=ev.severity_config,
        )
        envelope_keys = {k: v for k, v in doc.items() if k != "runs"}
        for run in doc["runs"]:
            for sarif_result in run.get("results", []):
                sarif_result.setdefault("properties", {})["releaseMember"] = member
            runs.append(run)
    return json.dumps({**envelope_keys, "runs": runs}, indent=2)


def render_release_review(document: Mapping[str, Any]) -> str:
    """The release review digest, read from the release JSON document (the
    mapping ``render_release_json`` serializes, not live state)."""
    exit_info = document.get("exit") or {}
    lines = [
        "# Release review",
        "",
        f"- **Verdict:** `{document.get('verdict', '?')}`",
        f"- **Exit code:** `{exit_info.get('code', '?')}`",
    ]
    if "matrix_verdict" in document:
        lines.append(f"- **Matrix verdict:** `{document['matrix_verdict']}`")
    if "bundle_verdict" in document:
        lines.append(f"- **Bundle verdict:** `{document['bundle_verdict']}`")
    warnings = document.get("warnings") or []
    if warnings:
        lines += ["", "## Warnings", ""]
        lines += [f"- {w}" for w in warnings]

    lines += [
        "",
        "## Members",
        "",
        "| Library | Verdict | Breaking | Source breaks | Risk | Additions |",
        "|---|---|---:|---:|---:|---:|",
    ]
    libraries = [lib for lib in document.get("libraries", []) if isinstance(lib, dict)]
    for lib in libraries:
        lines.append(
            f"| {md_text(lib.get('library', '?'), code=True, table=True)} | `{lib.get('verdict', '?')}` "
            f"| {lib.get('breaking', 0)} | {lib.get('source_breaks', 0)} "
            f"| {lib.get('risk_changes', 0)} | {lib.get('compatible_additions', 0)} |"
        )

    breaking_members = [lib for lib in libraries if lib.get("breaking")]
    if breaking_members:
        lines += ["", "## Breaking findings", ""]
        for lib in breaking_members:
            findings = [f for f in lib.get("findings") or [] if isinstance(f, dict)]
            breaking = [f for f in findings if f.get("bucket") == "breaking"]
            lines.append(f"### {md_text(lib.get('library', '?'), code=True)}")
            lines.append("")
            for finding in breaking[:_REVIEW_FINDINGS_PER_MEMBER]:
                lines.append(
                    f"- {md_text(finding.get('kind', '?'), code=True)} "
                    f"{md_text(finding.get('symbol', ''), code=True)}"
                )
            hidden = len(breaking) - _REVIEW_FINDINGS_PER_MEMBER
            if hidden > 0:
                lines.append(f"- ... and {hidden} more")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"
