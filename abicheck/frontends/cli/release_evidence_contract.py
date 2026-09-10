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
aggregation site and its rendering site.

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
    "evidence_contract_notice",
    "release_evidence_contract_contribution",
    "report_and_exit_on_evidence_contract",
]


def release_evidence_contract_contribution(
    library_results: Sequence[object],
) -> int:
    """The release's own contribution: ``max()`` over every member's.

    One member short of the pinned rung makes the release report the axis,
    the same rule the sibling contract-coverage floor already applies. ``0``
    for a run with no ``--depth`` pin, so every pre-existing invocation is
    unchanged.
    """
    return max(
        (
            contribution
            for entry in library_results
            if isinstance(entry, dict)
            and isinstance(
                contribution := entry.get("evidence_contract_error_contribution", 0),
                int,
            )
        ),
        default=0,
    )


def evidence_contract_notice(
    library_results: Sequence[object], contribution: int
) -> str | None:
    """The stderr line explaining a release's exit 7, or ``None``.

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


def report_and_exit_on_evidence_contract(
    library_results: Sequence[object],
    contribution: int,
    *,
    worst_verdict: str,
) -> None:
    """Emit the notice and exit *contribution*, unless something outranks it.

    Called ahead of ``_exit_compare_release`` rather than as another
    parameter to it, because this is an *abort* axis, not one more floor to
    fold: a run whose pinned evidence contract was not met never established
    what changed, so ADR-064 puts it above the verdict mapping and above
    ``--fail-on-removed-library``'s exit 8. Keeping it out of that function
    also keeps the three genuine 0/1 floors it merges into one variable
    (coverage, incomplete scope, no-comparison-completed) actually alike.

    The one thing that does outrank it is ``not_comparable``: the scalar
    resolver takes a plain ``max()`` over contributions, where 16 beats 7,
    so this returns and lets ``_exit_compare_release`` reach its own 16.
    Returns without exiting when *contribution* is ``0`` -- every run
    without a ``--depth`` pin.
    """
    import sys

    notice = evidence_contract_notice(library_results, contribution)
    if notice is None:
        return
    import click

    click.echo(notice, err=True)
    if worst_verdict == "not_comparable":
        return
    sys.exit(contribution)
