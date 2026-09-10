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

"""How one failed release member becomes that member's own result entry.

A directory/package ``compare`` never aborts on a single member: whatever
went wrong for that library becomes its entry in the release's result list,
and the release keeps going. *Which* entry, though, is a real classification
with four distinct outcomes and three different axes behind them --
``not_comparable`` (ADR-050 D2's profile/scope contract), ``unsupported``
(ADR-065 D6's incompleteness signal), and ``ERROR`` (an operational failure
floored to exit 4) -- plus, for one of them, a report document written to
``--output-dir``.

Extracted from :func:`abicheck.cli_compare_release_pairwise._compare_one_library`,
whose ``try`` body is the comparison and whose ``except`` cascade was this.
The seam is a real responsibility boundary, not a line-count trick: nothing
here needs the comparison's ~30 parameters, and the cascade's ordering
constraints (``UnsupportedArtifactError`` before its ``ValidationError``
base, both before the generic catch) are a property of the exception
taxonomy, which is what this module is about. It also gives the
depth-shortfall guidance one place to be attached rather than a branch
inside a 200-line function.
"""

from __future__ import annotations

import json
from pathlib import Path

import click

from ...errors import (
    IncompatibleSnapshotSchemaError,
    ProfileMismatchError,
    ScopeMismatchError,
    UnsupportedArtifactError,
    ValidationError,
)

__all__ = ["member_error_entry"]


def _write_not_comparable_report(
    output_dir: Path,
    old_path: Path,
    old_version: str,
    new_version: str,
    kind: str,
    reason: str,
) -> None:
    """Write this member's own ``--output-dir`` report for a contract mismatch."""
    # Local imports: this module is reached only on a member failure, so the
    # report machinery is not paid for on the success path. The writer comes
    # from `frontends.cli.runtime`, its real owner -- `abicheck.cli`
    # re-exports the same function, but importing it from there would pull a
    # command-registration edge into this leaf and grow the baselined CLI
    # SCC (`scripts/check_ai_readiness.py`'s `IMPORT_CYCLE_ALLOWLIST`).
    from ...report.not_comparable import OperationalStatus, not_comparable_document
    from ...schemas import REPORT_SCHEMA_VERSION
    from .runtime import _safe_write_output

    doc = not_comparable_document(
        old_path.name,
        old_version,
        new_version,
        kind,
        reason,
        report_schema_version=REPORT_SCHEMA_VERSION,
        operational=OperationalStatus.NOT_COMPARABLE,
    ).to_mapping()
    _safe_write_output(output_dir / f"{old_path.stem}.json", json.dumps(doc, indent=2))


def member_error_entry(
    exc: BaseException,
    *,
    old_path: Path,
    old_version: str,
    new_version: str,
    output_dir: Path | None,
    depth: str | None,
) -> dict[str, object]:
    """Classify *exc* into this member's release result entry.

    The order below is load-bearing and each step says why it sits where it
    does; it is the same order the inline cascade this replaces had.
    """
    if isinstance(exc, ProfileMismatchError | ScopeMismatchError):
        # ADR-050 D2 — before the generic case. This library's old/new DSOs
        # were not extracted under a comparable profile/scope contract: a
        # distinct, expected outcome (not an abicheck bug), so it gets its
        # own "not_comparable" verdict string instead of the "ERROR"/exit-4
        # bucket a genuine crash uses — see `_RELEASE_VERDICT_ORDER`'s
        # dedicated rank for it.
        kind = (
            "profile_mismatch"
            if isinstance(exc, ProfileMismatchError)
            else "scope_mismatch"
        )
        if output_dir:
            _write_not_comparable_report(
                output_dir, old_path, old_version, new_version, kind, str(exc)
            )
        return {
            "library": old_path.name,
            "verdict": "not_comparable",
            "reason": str(exc),
        }
    if isinstance(exc, IncompatibleSnapshotSchemaError | UnsupportedArtifactError):
        # ADR-065 D6: an artifact this build cannot analyze at all (a stored
        # snapshot newer than this reader, a container format with no
        # backend) is `unsupported` -- an incompleteness signal on the scope
        # axis, not an operational `ERROR` crash floored to exit 4.
        return {"library": old_path.name, "verdict": "unsupported", "reason": str(exc)}
    if isinstance(exc, click.ClickException | click.UsageError):
        return {
            "library": old_path.name,
            "verdict": "ERROR",
            "error": exc.format_message(),
        }
    if isinstance(exc, ValidationError):
        # After `UnsupportedArtifactError` above, which subclasses this. A
        # per-member `--depth` floor failure lands here
        # (`enforce_requested_depth`, run for this pair by
        # `resolve_compare_request` exactly as for a single-pair compare),
        # and it is the one failure that can carry guidance the generic case
        # cannot -- see `cli_compare_options.set_input_depth_shortfall_message`.
        from ...cli_compare_options import set_input_depth_shortfall_message

        return {
            "library": old_path.name,
            "verdict": "ERROR",
            "error": set_input_depth_shortfall_message(str(exc), depth),
        }
    return {"library": old_path.name, "verdict": "ERROR", "error": str(exc)}
