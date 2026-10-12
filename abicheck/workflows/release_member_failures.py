# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""How one failed release member becomes that member's own result entry.

A directory/package ``compare`` never aborts on a single member: whatever
went wrong for that library becomes its entry in the release's result list,
and the release keeps going. *Which* entry is a real classification with
four outcomes and three axes behind them -- ``not_comparable`` (ADR-050 D2's
profile/scope contract), ``unsupported`` (ADR-065 D6's incompleteness
signal), and ``ERROR`` (an operational failure floored to exit 4).

Moved here from ``frontends/cli/release_member_errors.py`` with the member
fan-out it serves (:mod:`abicheck.workflows.release_members`). The one thing
that stayed with a front end -- writing a ``not_comparable`` member's own
``--output-dir`` document -- is the caller's
:meth:`~abicheck.workflows.release_compare_reporter.ReleaseCompareReporter.
persist_not_comparable` hook; the dispatch-failure echo is its ``notice``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ..errors import (
    IncompatibleSnapshotSchemaError,
    ProfileMismatchError,
    ScopeMismatchError,
    UnsupportedArtifactError,
)

__all__ = ["MemberFailure", "classify_member_failure", "dispatch_failure_entry"]

#: Where an unexpected member failure's traceback goes. A release never
#: aborts on one member, so the traceback cannot propagate to the terminal
#: the way an ordinary crash would -- without this the *only* surviving
#: trace of an internal defect is ``str(exc)``, and for a bare ``KeyError``
#: that is a tuple with no type name and no origin. A real six-member
#: oneDAL run reported exactly ``(138821602538640, 'const dense&', 0, 12)``
#: and nothing else. Logging keeps the release's own output unchanged while
#: making the failure diagnosable with any handler the embedder installs.
_log = logging.getLogger("abicheck.release")


@dataclass(frozen=True)
class MemberFailure:
    """A failed member's entry, plus the contract-mismatch kind
    (``profile_mismatch``/``scope_mismatch``) when it is ``not_comparable``."""

    entry: dict[str, object]
    not_comparable_kind: str | None = None


def _stated_message(exc: BaseException) -> str | None:
    """The message of an exception that *states* its own diagnostic -- a
    front end's usage/stated error (``click.ClickException`` and its
    subclasses carry ``format_message``). Duck-typed so this module stays
    frontend-free; such an error carries no internal traceback worth
    logging."""
    format_message = getattr(exc, "format_message", None)
    if callable(format_message):
        return str(format_message())
    return None


def classify_member_failure(exc: BaseException, *, library: str) -> MemberFailure:
    """Classify *exc* into member *library*'s release result entry.

    The order below is load-bearing: the contract mismatches and the
    unsupported artifacts must be recognized before the generic case.
    """
    if isinstance(exc, ProfileMismatchError | ScopeMismatchError):
        # ADR-050 D2: this library's old/new DSOs were not extracted under a
        # comparable profile/scope contract -- a distinct, expected outcome
        # (not an abicheck bug), ranked above ERROR by
        # `policy.release_outcome.RELEASE_VERDICT_ORDER`.
        kind = (
            "profile_mismatch"
            if isinstance(exc, ProfileMismatchError)
            else "scope_mismatch"
        )
        return MemberFailure(
            {"library": library, "verdict": "not_comparable", "reason": str(exc)},
            not_comparable_kind=kind,
        )
    if isinstance(exc, IncompatibleSnapshotSchemaError | UnsupportedArtifactError):
        # ADR-065 D6: an artifact this build cannot analyze at all is
        # `unsupported` -- an incompleteness signal on the scope axis, not an
        # operational `ERROR` floored to exit 4.
        return MemberFailure(
            {"library": library, "verdict": "unsupported", "reason": str(exc)}
        )
    stated = _stated_message(exc)
    if stated is not None:
        return MemberFailure(
            {
                "library": library,
                "verdict": "ERROR",
                "error": stated,
                "error_type": type(exc).__name__,
            }
        )
    # Anything reaching here is an *unexpected* failure -- an abicheck defect
    # until shown otherwise -- so it keeps its identity: the member, the
    # exception class, and a full traceback on the diagnostic channel.
    # ADR-063 D6's `OperationalStatus.EXTRACTION_ERROR` already covers an
    # internal comparison failure, so the classification is unchanged.
    _log.exception(
        "unexpected failure comparing release member %s", library, exc_info=exc
    )
    return MemberFailure(
        {
            "library": library,
            "verdict": "ERROR",
            "error": str(exc),
            "error_type": type(exc).__name__,
        }
    )


def dispatch_failure_entry(
    exc: BaseException, library: str
) -> tuple[dict[str, object], str]:
    """The entry for a failure that escaped :func:`classify_member_failure`,
    plus the one-line notice a user watching the run is owed.

    The fan-out has two exception boundaries. The inner one wraps the
    comparison and classifies with the member's full identity; a failure
    reaching this outer one escaped the worker itself (the dispatch, the
    context copy, or the classification), so only the name is known -- but
    the diagnostic obligation is identical.
    """
    _log.exception(
        "unexpected failure dispatching release member %s", library, exc_info=exc
    )
    entry: dict[str, object] = {
        "library": library,
        "verdict": "ERROR",
        "error": str(exc),
        "error_type": type(exc).__name__,
    }
    return entry, f"Error comparing {library}: {type(exc).__name__}: {exc}"
