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

"""Markdown report-mode dispatch: ``to_markdown``/``to_review_digest`` and
the per-mode glue (``_to_markdown_leaf``/``_to_markdown_root_cause``/
``_markdown_alternate_rendering``) that route a ``DiffResult`` to the right
``build_*_document``/``render_*_document`` pair.

ADR-063 T10: these five functions used to live in ``reporter_markdown.py``
and reach ``render_markdown_document.py``/``render_markdown_alternate.py``
via function-local imports. That made ``reporter_markdown`` depend on this
package while every ``compute_*``/row-shape helper those two modules need
still lives in ``reporter_markdown`` -- a static import in either direction
closed a real cycle, worked around by resolving ``reporter_markdown`` via
``importlib`` from the ``report/`` side (``render_markdown_document.
_reporter_markdown()``/``render_markdown_alternate.py``'s own copy).

Moving the five dispatch functions here instead removes the back-edge
outright: ``reporter_markdown.py`` no longer imports anything from
``report/`` at all, so ``report/render_markdown_document.py`` and
``report/render_markdown_alternate.py`` can import it back statically, and
this module can import both of those plus ``reporter_markdown`` (all
one-directional, ordinary downward edges -- nothing here is imported by any
of the three). ``reporter_markdown.py`` keeps a compatibility shim
(``__getattr__``) for the old import path, the same pattern
``cli_buildsource.py``'s own tail already uses when a helper moves out from
under a re-exporting module (see root `AGENTS.md`'s "Moving helpers out of
a module that re-exports them?").

Every view's byte-for-byte output is unchanged by this move -- the same
golden suites (`tests/test_golden_output.py`, `tests/test_golden_root_
cause.py`, `tests/test_golden_review_digest.py`) still pin it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..checker_types import DiffResult
    from ..severity import SeverityConfig
    from .document import ReportDocument
    from .envelope import ReportEnvelope


def to_markdown(
    result: DiffResult,
    *,
    show_only: str | None = None,
    report_mode: str = "full",
    show_impact: bool = False,
    severity_config: SeverityConfig | None = None,
    show_recommendation: bool = False,
    demangle: bool = False,
    contract_evaluation: bool = False,
    report_document: ReportDocument | None = None,
    envelope: ReportEnvelope | None = None,
) -> str:
    """See :func:`~abicheck.report.render_markdown_document.
    build_markdown_document`'s own docstring for *report_document*/*envelope*
    (ADR-061 gap C) -- forwarded unchanged to the full-mode
    (``report_mode="full"``) document build only; ``--stat`` and the
    ``leaf``/``root-cause`` alternate views ignore both, since those stay
    their own separate documents."""

    # Human-facing only: optionally demangle Itanium C++ symbols in the rendered
    # output. Machine formats (JSON/SARIF/JUnit) keep the raw mangled symbols.
    def _out(text: str) -> str:
        if not demangle:
            return text
        from ..demangle import demangle_text

        return demangle_text(text)

    alternate = _markdown_alternate_rendering(
        result,
        report_mode=report_mode,
        show_impact=show_impact,
        show_only=show_only,
        show_recommendation=show_recommendation,
        severity_config=severity_config,
        contract_evaluation=contract_evaluation,
    )
    if alternate is not None:
        return _out(alternate)

    # ADR-061 Phase 2 item 1: the default (full-mode) view crosses the
    # canonical ReportDocument boundary via report/render_markdown_document.py
    # -- the same fact/formatting split JSON/SARIF/JUnit/--stat/HTML already
    # use. `demangle` is applied inside the document renderer itself (the
    # document's own "demangle" field), not by this function's `_out`.
    from .render_markdown_document import (
        build_markdown_document,
        render_markdown_document,
    )

    return render_markdown_document(
        build_markdown_document(
            result,
            show_only=show_only,
            show_impact=show_impact,
            severity_config=severity_config,
            show_recommendation=show_recommendation,
            demangle=demangle,
            report_document=report_document,
            envelope=envelope,
        )
    )


def _markdown_alternate_rendering(
    result: DiffResult,
    *,
    report_mode: str,
    show_impact: bool,
    show_only: str | None,
    show_recommendation: bool,
    severity_config: Any,
    contract_evaluation: bool,
) -> str | None:
    """Render one of the non-default markdown views, or ``None`` for the default.

    The ``leaf`` / ``root-cause`` report modes each produce a complete document
    of their own; the caller returns it as-is (after its own demangling pass)
    rather than continuing into the full report.

    A ``stat`` parameter used to select ``reporter_markdown.to_stat`` here. It
    was a dispatch flag, not a rendering option -- "ignore every other argument
    and render a different document" -- and the one-line summary has its own
    public spelling (``render_output(fmt="oneline")``, or ``to_stat``
    directly). Call the function you want.
    """
    if report_mode == "root-cause":
        return _to_markdown_root_cause(
            result,
            show_only=show_only,
            show_recommendation=show_recommendation,
            show_impact=show_impact,
            severity_config=severity_config,
            contract_evaluation=contract_evaluation,
        )
    return None


def _to_markdown_root_cause(
    result: DiffResult,
    show_only: str | None = None,
    show_recommendation: bool = False,
    show_impact: bool = False,
    *,
    severity_config: SeverityConfig | None = None,
    contract_evaluation: bool = False,
) -> str:
    """``--report-mode root-cause`` markdown rendering (G29 Phase 3 slice 4, ADR-052).

    Groups findings under one heading per root cause instead of full mode's
    severity-bucketed sections -- root-cause mode's point is "what's the
    minimal set of things that actually broke", not "what severity bucket
    does each finding independently fall into".

    ADR-061 Phase 2 item 1: crosses the canonical ``ReportDocument`` boundary
    via ``report/render_markdown_alternate.py``, the same fact/formatting
    split JSON/SARIF/JUnit/``--stat``/HTML/full-mode markdown already use.
    """
    from .render_markdown_alternate import (
        build_root_cause_document,
        render_root_cause_document,
    )

    return render_root_cause_document(
        build_root_cause_document(
            result,
            show_only=show_only,
            show_recommendation=show_recommendation,
            show_impact=show_impact,
            severity_config=severity_config,
            contract_evaluation=contract_evaluation,
        )
    )


def to_review_digest(
    result: DiffResult,
    *,
    severity_config: SeverityConfig | None = None,
    report_document: ReportDocument | None = None,
    envelope: ReportEnvelope | None = None,
) -> str:
    """Compact GitHub-facing review digest (Markdown).

    A single, reviewer-oriented summary suitable for a job summary
    ($GITHUB_STEP_SUMMARY) or a PR comment body: verdict + merge effect, a
    counts table that separates breaking / API / risk / public additions /
    filtered-internal, the release recommendation, a manual-review banner when
    public-header scoping fell back (issue #235), and the top impacted symbols.
    Distinct from to_markdown (the full report) — this is the "presentation"
    layer over the same machine-readable decision contract. ADR-061 Phase 2
    item 1: crosses the canonical ``ReportDocument`` boundary via
    ``report/render_markdown_document.py`` — the same fact/formatting split
    JSON/SARIF/JUnit/``--stat``/HTML already use.

    *report_document* (ADR-061 gap C) is the one shared
    ``report_mode="full"`` document ``service_render.render_output``'s
    ``review`` branch builds once via ``report.build.build_report_document``
    and forwards here -- see ``build_review_digest_document``'s own
    docstring for exactly what it is reused for. *envelope* is the completed
    ``ReportEnvelope`` that document belongs to; it additionally supplies the
    digest's per-finding verdicts, so its "impacted symbols" list reads the
    same resolution every other format does instead of making its own.
    """
    from .render_markdown_document import (
        build_review_digest_document,
        render_review_digest_document,
    )

    return render_review_digest_document(
        build_review_digest_document(
            result,
            severity_config=severity_config,
            report_document=report_document,
            envelope=envelope,
        )
    )
