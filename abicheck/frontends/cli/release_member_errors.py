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
constraints (``UnsupportedArtifactError`` before the generic catch) are a
property of the exception taxonomy, which is what this module is about,
not of the comparison's own arguments.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import click

from ...errors import (
    IncompatibleSnapshotSchemaError,
    ProfileMismatchError,
    ScopeMismatchError,
    UnsupportedArtifactError,
)

__all__ = ["member_dispatch_failure_entry", "member_error_entry"]

#: Where an unexpected member failure's traceback goes. A release never
#: aborts on one member, so the traceback cannot propagate to the terminal
#: the way an ordinary crash would -- without this the *only* surviving
#: trace of an internal defect is ``str(exc)``, and for a bare ``KeyError``
#: that is a tuple with no type name and no origin. A real six-member
#: oneDAL run reported exactly ``(138821602538640, 'const dense&', 0, 12)``
#: and nothing else, which identified neither the failing subsystem nor the
#: exception class. Logging keeps the release's own output unchanged for
#: every existing consumer while making the failure diagnosable with
#: ``logging.basicConfig(level=logging.DEBUG)`` or any handler the embedder
#: already has.
_log = logging.getLogger("abicheck.release")


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
        # A stated CLI-level failure: its own message is the diagnostic, and
        # it carries no internal traceback worth logging.
        return {
            "library": old_path.name,
            "verdict": "ERROR",
            "error": exc.format_message(),
            "error_type": type(exc).__name__,
        }
    # Anything reaching here is an *unexpected* failure -- an abicheck
    # defect until shown otherwise -- so it keeps its identity: the member
    # it belongs to, the exception class, and a full traceback on the
    # diagnostic channel. ``str(exc)`` alone is not an account of an
    # internal failure; ``KeyError``'s is its bare argument.
    #
    # The classification itself is deliberately unchanged: ADR-063 D6's
    # `OperationalStatus.EXTRACTION_ERROR` is already defined as "a library
    # failed to dump/extract/compare", which covers an internal comparison
    # failure. Renaming it would be a schema change bought for nothing.
    _log.exception(
        "unexpected failure comparing release member %s", old_path.name, exc_info=exc
    )
    return {
        "library": old_path.name,
        "verdict": "ERROR",
        "error": str(exc),
        "error_type": type(exc).__name__,
    }


def member_dispatch_failure_entry(
    exc: BaseException, library_name: str
) -> dict[str, object]:
    """This member's entry for a failure that escaped :func:`member_error_entry`.

    The release fan-out has two exception boundaries. The inner one wraps the
    comparison body and reaches :func:`member_error_entry` with the member's
    full identity. This is the outer one: a failure reaching it escaped the
    worker's own classification -- in the dispatch, in the context copy, or out
    of the classification itself -- so only the library's name is available,
    but the diagnostic obligation is identical. It lives here, next to the
    classification it backs up, rather than inline in the executor loop.

    It echoes to stderr as well as logging, because this boundary's message is
    what a user watching a long release run actually sees; the inner one's
    outcome is carried in the report.
    """
    _log.exception(
        "unexpected failure dispatching release member %s", library_name, exc_info=exc
    )
    click.echo(f"Error comparing {library_name}: {type(exc).__name__}: {exc}", err=True)
    return {
        "library": library_name,
        "verdict": "ERROR",
        "error": str(exc),
        "error_type": type(exc).__name__,
    }
