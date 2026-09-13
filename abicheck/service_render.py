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

"""Output rendering for a :class:`~abicheck.checker_types.DiffResult`.

Extracted from :mod:`abicheck.service` so that module stays under the
AI-readiness size cap. This is a leaf module: it does not import
``abicheck.service`` and is re-exported there for backward compatibility.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .errors import ValidationError
from .model import AbiSnapshot
from .report.build import build_report_envelope
from .report.envelope import RenderOptions, ReportEnvelope
from .reporter import (
    to_json,
    to_markdown,
    to_stat as to_stat,  # re-exported; see service.py's own note
    to_stat_json as to_stat_json,  # likewise
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from .checker_types import DiffResult

    # ADR-061: this module is classified `frontends`, which may not import
    # `policy` (where `severity.py`/`SeverityConfig` now physically live,
    # `abicheck/policy/severity.py`) directly -- `workflows.gate` is the
    # existing re-export facade `frontends`-classified callers already
    # route policy-owned exit-decision types through (its own docstring:
    # "the one place a frontend gets its process response").
    from .workflows.gate import SeverityConfig

#: ``fmt`` value for :func:`render_output` — a one-line human summary.
#: A public ``-o oneline=...`` choice on ``compare`` (CLI cleanup phase
#: two, PR 1 removed the old ``--stat`` boolean threaded through every
#: renderer; this is its sole surviving replacement. It was reachable only
#: via the built-in ``quick`` ``--profile`` injecting ``fmt="oneline"``
#: until `one-comparison-product.md` Phase 7e removed ``--profile``
#: outright and promoted this value to a first-class ``--format`` choice
#: instead of losing the capability). Kept as a plain ``fmt`` value rather
#: than a revived boolean so it flows through the one existing dispatch
#: this function already has, instead of re-introducing a second,
#: orthogonal axis every caller down the stack has to thread separately.
ONELINE_FORMAT = "oneline"


def render_output(
    fmt: str,
    result: DiffResult,
    old: AbiSnapshot,
    new: AbiSnapshot | None = None,
    *,
    follow_deps: bool = False,
    show_only: str | None = None,
    report_mode: str = "full",
    show_impact: bool = False,
    severity_config: SeverityConfig | None = None,
    demangle: bool = False,
    contract_evaluation: bool = False,
    show_recommendation: bool = True,
    require_complete_analysis: bool = False,
) -> str:
    """Render comparison result in the requested output format.

    Supported formats: ``'json'``, ``'markdown'``, ``'sarif'``, ``'html'``,
    ``'junit'``, ``'review'``, and :data:`ONELINE_FORMAT` (``'oneline'``),
    a public ``--format`` choice on ``compare``.

    ``demangle`` only affects human-facing formats (markdown, review, html);
    machine formats (json/sarif/junit) always keep raw mangled symbols so
    downstream tooling can match on them. This function's own default
    (``False``) is for a direct Tier-2 caller with no CLI in front of it;
    the CLI itself resolves the per-format default via
    ``cli_compare_options._resolve_demangle`` before calling here.

    The release recommendation is included in every human-facing format
    (markdown/review) and in JSON's own ``summary`` block. ``show_recommendation``
    defaults to ``True`` -- the behavior every real consumer gets -- and remains
    an explicit opt-out for a caller that wants the section suppressed.

    It used to default to ``False``, with the CLI wrapper passing ``True`` to
    compensate. That made this function's own docstring false for the audience
    it was written for: the CLI's recommendation was unconditional, but a
    Tier-2 caller omitting the keyword silently got no recommendation section
    at all. Two front ends disagreeing about the default *is* the divergence,
    so the default now states the shipped behavior and a suppressing caller
    says so.

    A ``stat`` parameter used to stand in for the removed ``--stat`` flag,
    reproducing its format-dependent dispatch: ``to_stat_json`` for
    ``fmt="json"``, real JUnit XML for ``fmt="junit"`` (never short-circuited),
    and ``to_stat`` for everything else. It was a dispatch flag rather than a
    rendering option -- "ignore the format I passed and render a different
    document" -- and each of its three outcomes already has a direct public
    spelling: :data:`ONELINE_FORMAT` for the one-line summary,
    :func:`~abicheck.reporter.to_stat_json` for the summary-only JSON, and
    plain ``fmt="junit"`` for JUnit. It also sat *above*
    ``_reject_unsupported_format``, so ``render_output("nonsense", stat=True)``
    returned a summary instead of raising :class:`ValidationError`. Call the
    function or format you want.

    ADR-061 gap C / duplication-and-convergence Phase 4: apart from the two
    ``--stat``/``oneline`` short-circuits below (a summary-only document with
    no shared-document counterpart -- see ``report/envelope.py``'s own scope
    note), this function decides nothing per format. It builds **one**
    :class:`~abicheck.report.envelope.ReportEnvelope` -- the shared document
    (compatibility, assurance, scope, dispositions, the consumer-scoped gate
    facts and the persisted exit decision), the severity ``GateDecision``, and
    the per-finding verdict/category set, all final -- and then hands it to
    :func:`render_envelope`, which selects a pure projection. No projection
    re-runs extraction, policy evaluation or gate resolution.

    Raises:
        ValidationError: For unrecognised output format.
    """
    if fmt == ONELINE_FORMAT:
        return to_stat(result, severity_config=severity_config)

    _reject_unsupported_format(fmt)
    _reject_unsupported_report_mode(report_mode)
    envelope = build_report_envelope(
        result,
        old,
        new,
        options=RenderOptions(
            show_only=show_only,
            report_mode=report_mode,
            show_impact=show_impact,
            demangle=demangle,
            follow_deps=follow_deps,
            show_recommendation=show_recommendation,
            require_complete_analysis=require_complete_analysis,
            contract_evaluation=contract_evaluation,
        ),
        severity_config=severity_config,
    )
    return render_envelope(fmt, envelope)


#: The formats :func:`render_envelope` projects. ``oneline``/``--stat`` is not
#: here: it short-circuits above, being a summary-only document rather than a
#: projection of the shared one (``report/envelope.py``'s scope note).
_SUPPORTED_FORMATS = frozenset(
    {"json", "sarif", "html", "junit", "markdown", "md", "review"}
)


def _reject_unsupported_format(fmt: str) -> None:
    if fmt not in _SUPPORTED_FORMATS:
        raise ValidationError(
            f"Unsupported output format: {fmt!r} (expected one of {sorted(_SUPPORTED_FORMATS)})"
        )


#: Report modes this function renders. Plan slice 7o retired ``"leaf"``
#: against ``"root-cause"`` on a 129-pair measurement; kept here as a named
#: retirement rather than simply absent because a *silent* fall-through is
#: what the check below exists to stop.
_SUPPORTED_REPORT_MODES: frozenset[str] = frozenset({"full", "impact", "root-cause"})
_RETIRED_REPORT_MODES: dict[str, str] = {
    "leaf": (
        "use report_mode='root-cause': measured over 129 real library pairs, "
        "the two exposed the identical finding set in every one of the 93 "
        "with findings, and 'leaf' rendered an empty headline section in 40 "
        "of them"
    ),
}


def _reject_unsupported_report_mode(report_mode: str) -> None:
    """Reject a retired or unknown ``report_mode`` at the public boundary.

    Codex review, PR #1284: retiring ``leaf`` from the Click parser does not
    retire the *documented Python* rendering path. A typed-API caller passing
    ``report_mode="leaf"`` to :func:`render_output` would otherwise fall
    through to a full report -- a silently different document shape, which is
    strictly worse than an error, since the caller keeps rendering and never
    learns the mode is gone. ``abicheck/AGENTS.md`` treats the typed API as
    public surface, so the retirement is enforced where that surface is, not
    only where Click is.
    """
    if report_mode in _RETIRED_REPORT_MODES:
        raise ValidationError(
            f"report_mode={report_mode!r} was retired (plan slice 7o): "
            f"{_RETIRED_REPORT_MODES[report_mode]}"
        )
    if report_mode not in _SUPPORTED_REPORT_MODES:
        raise ValidationError(
            f"Unsupported report mode: {report_mode!r} "
            f"(expected one of {sorted(_SUPPORTED_REPORT_MODES)})"
        )


def render_envelope(fmt: str, envelope: ReportEnvelope) -> str:
    """Project one already-completed *envelope* into *fmt*.

    ADR-061 gap C's "one result, one document, several projections" is this
    function's contract: every decision *fmt* could need was made by
    :func:`~abicheck.report.build.build_report_envelope` before this call,
    so rendering the same envelope into several formats, in any order, can
    only produce the same semantic content each time -- no projection re-runs
    extraction, policy evaluation, or gate resolution.

    Public alongside :func:`render_output` because "render this one completed
    evaluation into N formats" is a real caller shape (a CI run writing JSON
    *and* a SARIF artifact *and* a job-summary digest); going through
    ``render_output`` N times would rebuild the same envelope N times.

    Raises:
        ValidationError: For unrecognised output format.
    """
    _reject_unsupported_format(fmt)
    projection: Callable[[ReportEnvelope], str] = _PROJECTIONS[fmt]
    return projection(envelope)


def _project_json(envelope: ReportEnvelope) -> str:
    """JSON, rendered from the shared document in full mode.

    ``leaf``/``root-cause`` JSON stays its own separate document build (the
    same ADR-061 Phase 2 scope decision Markdown's alternate views record) --
    a different report, not a different rendering of this one.
    """
    opts = envelope.options
    return _render_json_output(
        envelope.result,
        envelope.old,
        envelope.new,
        follow_deps=opts.follow_deps,
        show_only=opts.show_only,
        report_mode=opts.report_mode,
        show_impact=opts.show_impact,
        severity_config=envelope.severity_config,
        require_complete_analysis=opts.require_complete_analysis,
        contract_evaluation=opts.contract_evaluation,
        envelope=envelope,
    )


def _project_sarif(envelope: ReportEnvelope) -> str:
    """SARIF 2.1.0.

    Gap-C disposition for SARIF's own remaining facts: the rule catalog
    (``rules_seen``), the per-result ``level``/``location`` derivation and the
    root-cause grouping are **presentation** -- a SARIF-shaped arrangement of
    findings this envelope already decided, with no counterpart in any other
    format's shape. Its invocation ``exitCode``/``exitCodeDescription`` was
    not: computing an exit code is a decision, so it moved to
    ``report/sarif_invocation.py`` and reads the envelope's gate instead of
    resolving one of its own (``report/AGENTS.md``: "renderers do not own
    process exit behavior").
    """
    from .sarif import to_sarif_str

    return to_sarif_str(
        envelope.result,
        show_only=envelope.options.show_only,
        report_mode=envelope.options.report_mode,
        severity_config=envelope.severity_config,
        envelope=envelope,
    )


def _project_html(envelope: ReportEnvelope) -> str:
    """HTML.

    Gap-C disposition for HTML's own remaining facts: the ``removed``/
    ``added``/``changed`` bucketing, the per-section ``ChangeRow`` tables and
    ``compat_html``'s ABICC severity-band layout are **presentation** -- they
    arrange findings, and their already-resolved verdicts, into an HTML page's
    sections; no other format has that shape. The two that were decisions --
    the CI-gate card's gate and each row's verdict -- now read the envelope's
    ``gate`` and ``findings``.
    """
    from .html_report import generate_html_report

    return generate_html_report(
        envelope.result,
        lib_name=envelope.old.library,
        old_version=envelope.old.version,
        new_version=envelope.new.version if envelope.new else "new",
        old_symbol_count=envelope.result.old_symbol_count,
        show_only=envelope.options.show_only,
        show_impact=envelope.options.show_impact,
        severity_config=envelope.severity_config,
        demangle=envelope.options.demangle,
        envelope=envelope,
    )


def _project_junit(envelope: ReportEnvelope) -> str:
    """JUnit XML.

    Gap-C disposition for JUnit's own remaining facts: the symbol/testcase
    tree and the root-cause grouping are **presentation** (a JUnit-shaped
    arrangement of the same findings). Its per-finding verdict/category
    resolution was *not* -- it is the same decision every other format reads
    -- and now comes from the envelope, including for the scoped-only changes
    JUnit folds in beyond ``result.changes``, which the envelope resolves too
    rather than leaving JUnit to assemble its own policy inputs.
    """
    from .junit_report import to_junit_xml

    return to_junit_xml(
        envelope.result,
        envelope.old,
        show_only=envelope.options.show_only,
        severity_config=envelope.severity_config,
        report_mode=envelope.options.report_mode,
        envelope=envelope,
    )


def _project_review(envelope: ReportEnvelope) -> str:
    """The compact review digest (unconditional-recommendation Markdown)."""
    from .reporter import to_review_digest

    return _demangled(
        to_review_digest(
            envelope.result,
            severity_config=envelope.severity_config,
            envelope=envelope,
        ),
        envelope,
    )


def _project_markdown(envelope: ReportEnvelope) -> str:
    """The full Markdown report (and its ``md`` alias).

    ``show_recommendation`` stays this projection's own presentation option,
    read off the envelope. It defaults to ``True`` in both
    :class:`~abicheck.report.envelope.RenderOptions` and ``render_output``
    above, so building an envelope directly and calling ``render_output``
    with the option omitted produce byte-identical output -- the two used to
    disagree (``False`` here, ``True`` there), which meant a direct Tier-2
    envelope caller silently lost the Release Recommendation section that
    every real consumer gets.

    Gap-C disposition for Markdown's own remaining facts: ``severity_groups``'
    headed-section grouping is **presentation** over already-classified
    findings, and the ``leaf``/``root-cause`` views are separate documents by
    design (ADR-061 Phase 2's scope decision). Both are arrangements, not
    second opinions; neither classifies anything the envelope did not decide.
    """
    opts = envelope.options
    md = to_markdown(
        envelope.result,
        show_only=opts.show_only,
        report_mode=opts.report_mode,
        show_impact=opts.show_impact,
        severity_config=envelope.severity_config,
        show_recommendation=opts.show_recommendation,
        contract_evaluation=opts.contract_evaluation,
        envelope=envelope,
    )
    if opts.follow_deps and (
        envelope.old.dependency_info or (envelope.new and envelope.new.dependency_info)
    ):
        md += _render_deps_section_md(envelope.old, envelope.new)
    return _demangled(md, envelope)


def _demangled(text: str, envelope: ReportEnvelope) -> str:
    """Apply the human-facing ``demangle`` presentation option to *text*."""
    if not envelope.options.demangle:
        return text
    from .demangle import demangle_text

    return demangle_text(text)


_PROJECTIONS: dict[str, Callable[[ReportEnvelope], str]] = {
    "json": _project_json,
    "sarif": _project_sarif,
    "html": _project_html,
    "junit": _project_junit,
    "review": _project_review,
    "markdown": _project_markdown,
    "md": _project_markdown,
}


def _render_json_output(
    result: DiffResult,
    old: AbiSnapshot,
    new: AbiSnapshot | None,
    *,
    follow_deps: bool,
    show_only: str | None,
    report_mode: str,
    show_impact: bool,
    severity_config: SeverityConfig | None,
    require_complete_analysis: bool = False,
    contract_evaluation: bool = False,
    envelope: ReportEnvelope | None = None,
) -> str:
    """Render comparison result as JSON, optionally including dependency info.

    *envelope* (ADR-061 gap C) is the one completed envelope this render is a
    projection of; its shared document *is* the full-mode JSON report. A
    direct caller with no envelope (this function is re-exported from
    ``abicheck.service``) keeps the prior behaviour and builds one document of
    its own -- the same additive shape every other renderer's ``envelope``
    parameter uses.
    """
    if report_mode == "full":
        # ADR-061 Phase 2 gap C: the full-mode JSON report is built through
        # the one shared document choke point (report.build.
        # build_report_document) rather than to_json's own independent
        # dict-building pass -- see report/build.py's module docstring.
        from .report.render_json import render_json

        if envelope is not None:
            doc = envelope.document
        else:
            from .report.build import build_report_document

            doc = build_report_document(
                result,
                show_only=show_only,
                show_impact=show_impact,
                severity_config=severity_config,
                require_complete_analysis=require_complete_analysis,
                contract_evaluation=contract_evaluation,
            )
        base = render_json(doc)
    else:
        base = to_json(
            result,
            show_only=show_only,
            report_mode=report_mode,
            show_impact=show_impact,
            severity_config=severity_config,
            require_complete_analysis=require_complete_analysis,
            contract_evaluation=contract_evaluation,
        )
    if follow_deps and (old.dependency_info or (new and new.dependency_info)):
        import json
        from dataclasses import asdict

        d = json.loads(base)
        if old.dependency_info:
            d["old_dependency_info"] = asdict(old.dependency_info)
        if new and new.dependency_info:
            d["new_dependency_info"] = asdict(new.dependency_info)
        return json.dumps(d, indent=2)
    return base


def _render_deps_section_md(old: AbiSnapshot, new: AbiSnapshot | None) -> str:
    """Append dependency summary section to markdown output."""
    lines: list[str] = ["", "## Dependency Analysis", ""]

    for label, snap in [("Old", old), ("New", new)]:
        if snap is None or snap.dependency_info is None:
            continue
        info = snap.dependency_info
        lines.append(f"### {label} version (`{snap.version}`)")
        lines.append("")

        if info.nodes:
            lines.append(f"**Dependencies**: {len(info.nodes)} resolved DSOs")
            for node in info.nodes:
                raw_depth = node.get("depth", 0)
                depth = raw_depth if isinstance(raw_depth, int) else 0
                indent = "  " * depth
                reason = node.get("resolution_reason", "")
                lines.append(f"  {indent}- `{node.get('soname', '?')}` ({reason})")
            lines.append("")

        if info.bindings_summary:
            lines.append("**Bindings**:")
            for status, count in sorted(info.bindings_summary.items()):
                lines.append(f"  - `{status}`: {count}")
            lines.append("")

        if info.unresolved:
            lines.append("**Unresolved libraries**:")
            for u in info.unresolved:
                lines.append(
                    f"  - `{u.get('soname', '?')}` needed by `{u.get('consumer', '?')}`"
                )
            lines.append("")

        if info.missing_symbols:
            lines.append(f"**Missing symbols**: {len(info.missing_symbols)}")
            for ms in info.missing_symbols[:10]:
                ver = f"@{ms['version']}" if ms.get("version") else ""
                lines.append(f"  - `{ms['symbol']}{ver}`")
            if len(info.missing_symbols) > 10:
                lines.append(f"  - ... +{len(info.missing_symbols) - 10} more")
            lines.append("")

    return "\n".join(lines)
