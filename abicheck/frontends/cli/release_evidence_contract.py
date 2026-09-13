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

"""ADR-064's evidence-contract axis (exit 7) at release cardinality.

A pinned ``--depth build``/``--depth source`` that a member's own resolved
evidence never reached is recorded, not raised
(:mod:`abicheck.policy.depth_evidence_contract`) -- so a directory/package
``compare`` has to do two things a single-pair one gets for free: fold every
member's contribution into the release's exit code, and say why it exited.
Both live here, because they are one responsibility (this axis, at this
cardinality) that was otherwise split across the release engine's
aggregation site and its rendering site. The *decision* deliberately does
not: it is fed into `_exit_compare_release` and into the persisted ``exit``
block from the same aggregate, so the process exit and the report cannot
disagree -- an earlier revision of this module exited here directly, after
the summary had already been rendered, and produced a run that exited 7
while its own JSON said ``exit.code: 0`` (Codex review).

The second half is not cosmetic. The fan-out discards each member's
``DiffResult`` before the note ``record_depth_evidence_contract_error``
produces would be rendered, so without :func:`evidence_contract_notice` a
release exits 7 with nothing on stderr at all. The note also carries
different *remediation* than the scalar one: that one points at
``--build-info old=/new=`` on ``compare``, which a directory operand rejects
outright (``cli_resolve._reject_evidence_flags_for_set_inputs``), so
following it is itself a usage error -- the bug class
``cli_surface.retired_spelling_in_remediation`` names exactly that failure.
"""

from __future__ import annotations

from collections.abc import Sequence

__all__ = [
    "evidence_contract_error_entries",
    "evidence_contract_notice",
    "release_evidence_contract_contribution",
]


def release_evidence_contract_contribution(
    library_results: Sequence[object],
) -> int:
    """The release's own contribution -- see the policy owner of the same name.

    Re-exported here so this module reads as the one place the release's
    evidence-contract axis is handled; the fold itself belongs to `policy`,
    which is also where the report's `ExitDecision` derives it.
    """
    from ...workflows.gate import release_evidence_contract_contribution as _fold

    return _fold(list(library_results))  # type: ignore[arg-type]


def evidence_contract_notice(
    library_results: Sequence[object], contribution: int
) -> str | None:
    """The stderr line explaining this axis's contribution, or ``None``.

    Names the members that fell short and the remediation that actually
    works on this operand -- see the module docstring for why it cannot
    just reuse the scalar note's wording.
    """
    if not contribution:
        return None
    short = sorted(
        str(entry.get("library"))
        for entry in library_results
        if isinstance(entry, dict)
        and entry.get("evidence_contract_error_contribution", 0)
    )
    return (
        "Requested --depth evidence was not reached in: "
        + ", ".join(short)
        + f". Contributes {contribution} to the release exit code (ADR-064 "
        "evidence-contract axis). Inline --sources/--build-info are not "
        "accepted for a directory/package compare, so pre-dump each member "
        "with `dump --sources`/`--build-info` and compare the snapshot "
        "directories, or compare the library individually."
    )


def evidence_contract_error_entries(
    library_results: Sequence[object],
) -> list[dict[str, object]]:
    """``{"library", "error"}`` entries for members short of the pinned rung.

    Fed to ``junit_report.to_junit_xml_multi``'s ``error_libraries``, which
    renders one ``<testsuite>`` with an ``<error>`` testcase each. Without
    this, a release that exits 7 rendered a JUnit document with
    ``failures="0" errors="0"`` -- the process failed while a report-driven
    dashboard read the run as successful (Codex review). The XML is written
    before the exit is taken, so the renderer has to carry the fact itself,
    exactly as the JSON ``exit`` block does.
    """
    return [
        {
            "library": str(entry.get("library")),
            "error": (
                "Requested --depth evidence was not reached for this member; "
                "its findings rest on shallower evidence than was asked for. "
                "Contributes 7 to the release exit code (ADR-064 "
                "evidence-contract axis)."
            ),
        }
        for entry in library_results
        if isinstance(entry, dict) and entry.get("evidence_contract_error_contribution")
    ]
