# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Build the completed :class:`ReportEnvelope` a renderer now requires.

``reporter_markdown``'s review digest and ``--stat`` line read every decision
they state -- the gate, the disposition audit, the per-finding verdicts -- from
the envelope (ADR-061 gap C) rather than resolving them themselves. A test that
renders one of those views directly builds the envelope here, exactly as
``service_render.render_output`` does, so the assertion exercises the same path
production takes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from abicheck.checker import Change, DiffResult, Verdict
from abicheck.model import AbiSnapshot
from abicheck.report.dispatch_markdown import to_review_digest
from abicheck.report.document_builder import build_report_envelope
from abicheck.report.envelope import RenderOptions, ReportEnvelope
from abicheck.report.render_markdown_document import build_review_digest_document
from abicheck.reporter import to_stat
from abicheck.reporter_markdown import compute_review_digest

if TYPE_CHECKING:
    from abicheck.checker import Change, DiffResult, Verdict
    from abicheck.policy.severity import SeverityConfig


def envelope_for(
    result: DiffResult,
    *,
    severity_config: SeverityConfig | None = None,
) -> ReportEnvelope:
    """The completed envelope for *result*, under *severity_config* when given."""
    snap = AbiSnapshot(library="libtest.so.1", version="1.0")
    return build_report_envelope(
        result,
        snap,
        snap,
        options=RenderOptions(),
        severity_config=severity_config,
    )


def review_digest(
    result: DiffResult, *, severity_config: SeverityConfig | None = None
) -> str:
    """``to_review_digest`` for *result*, rendered from its own envelope."""
    return to_review_digest(
        result, envelope=envelope_for(result, severity_config=severity_config)
    )


def stat_line(
    result: DiffResult, *, severity_config: SeverityConfig | None = None
) -> str:
    """``to_stat`` (the ``--stat``/``oneline`` line) for *result*."""
    return to_stat(
        result, envelope=envelope_for(result, severity_config=severity_config)
    )


def review_document(result: DiffResult):  # type: ignore[no-untyped-def]
    """The review digest's ``ReportDocument`` for *result*."""
    return build_review_digest_document(result, envelope=envelope_for(result))


def compute_review(result: DiffResult):  # type: ignore[no-untyped-def]
    """The review digest's structured intermediate for *result*."""
    return compute_review_digest(result, envelope=envelope_for(result))


def make_result(verdict: Verdict, changes=None) -> DiffResult:
    return DiffResult(
        old_version="1.0",
        new_version="2.0",
        library="libtest.so.1",
        changes=changes or [],
        verdict=verdict,
    )


def make_diff(
    changes: list[Change] | None = None,
    verdict: Verdict = Verdict.NO_CHANGE,
    redundant_count: int = 0,
    redundant_changes: list[Change] | None = None,
    suppressed_count: int = 0,
    suppression_file_provided: bool = False,
    suppressed_changes: list[Change] | None = None,
    scope_to_public_surface: bool = False,
    out_of_surface_count: int = 0,
    out_of_surface_changes: list[Change] | None = None,
) -> DiffResult:
    """Build a minimal DiffResult for testing."""
    return DiffResult(
        old_version="1.0",
        new_version="2.0",
        library="libtest.so",
        changes=changes or [],
        verdict=verdict,
        redundant_count=redundant_count,
        redundant_changes=redundant_changes or [],
        suppressed_count=suppressed_count,
        suppression_file_provided=suppression_file_provided,
        suppressed_changes=suppressed_changes or [],
        scope_to_public_surface=scope_to_public_surface,
        out_of_surface_count=out_of_surface_count,
        out_of_surface_changes=out_of_surface_changes or [],
    )
