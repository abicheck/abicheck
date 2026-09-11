# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""One completed evaluation -> one envelope -> every format (ADR-061 gap C).

``report/build.py`` already gave every output format one shared *document*
build. What it did not give them was one shared *build call*:
``service_render.render_output`` still dispatched the ``DiffResult``, the
snapshots, the severity configuration and the presentation options down six
independent branches, each of which called ``build_report_document`` itself
and then re-resolved whatever else it needed (the severity
:class:`~abicheck.policy.severity.GateDecision` in SARIF's and HTML's case,
the per-``Change`` :class:`~abicheck.report.finding.ReportFinding` set in
HTML's, Markdown's and JUnit's). Six separately-built projections wrapped in
the same immutable type are not proof that they cannot disagree -- that is
exactly what ADR-061's gap C says, and what
``docs/contribute/plans/duplication-and-convergence-assessment.md`` Phase 4's
``ReportEnvelope`` names as the missing single document.

:class:`ReportEnvelope` is that document. Its builder,
``report.build.build_report_envelope``, runs **once per completed evaluation,
above format selection**, and resolves every decision a format could
otherwise reach for on its own:

* the shared ``report_mode="full"`` :class:`~abicheck.report.document.
  ReportDocument` (compatibility summary, changes block, suppression and
  disposition audit, surface/scope blocks, contract context, assurance and
  the persisted exit decision) -- one call to ``build_report_document``;
* the severity :class:`~abicheck.policy.severity.GateDecision` -- one call to
  ``policy.gate_decision.gate_decision_for_result``;
* one :class:`~abicheck.report.finding.ReportFinding` per ``Change``
  (verdict + issue category) -- one pass of ``build_report_findings``.

Every format then *projects* that envelope. A projection may still arrange
presentation however its format requires; it may not be an independent
authority for any decision above.

Two deliberate scope notes, both pre-existing ADR-061 decisions rather than
new ones:

* ``--stat``/``oneline`` is a summary-only document with no shared-document
  counterpart, and Markdown's/JSON's ``leaf``/``root-cause`` views are
  genuinely separate documents (ADR-061 Phase 2's own scope decision). The
  ``--stat``/``oneline`` short-circuit therefore runs *before* the envelope
  is built; the two alternate views are built by their own format branch
  from the same envelope's ``result``. The shared document is built eagerly
  for those two views even though they do not read it -- the envelope's
  whole point is that its decisions are final before a format is chosen, and
  a lazily-built field would trade that invariant for work avoided on a
  non-default report mode. That is a real, measured cost, recorded here
  rather than left to be rediscovered: on a 350-change comparison a
  ``--report-mode leaf`` Markdown render goes from ~1ms to ~7ms and a
  ``root-cause`` JSON render from ~7ms to ~14ms, since both now also build
  the shared document; full-mode JSON pays ~2ms for the per-finding
  resolution the formats that need it read. All of it is a fixed cost per
  *render*, against extraction and comparison that dominate a real run by
  two to three orders of magnitude.
* the *process* exit fold (``cli._exit_with_severity_or_verdict``) stays in
  ``frontends``: the envelope carries the exit decision the report publishes
  (inside the shared document), not the code the CLI exits with.

This module is deliberately a **leaf** -- the envelope type, the presentation
options, and the two ``resolved_*`` accessors, nothing that builds anything.
The builder lives in ``report/build.py`` beside the document build it
composes, because ``build.py`` reaches back into the still-flat
``reporter.py`` for its ``_add_*`` helpers, and ``reporter.py`` reaches
``report/dispatch_markdown.py``, which needs this envelope type: an
``envelope -> build`` edge would close that path into a real import cycle
(``scripts/check_ai_readiness.py``'s ``import-cycle-growth``, which the
repository forbids unblocking by allowlist). Every renderer imports the type
from here and the builder from there.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING

from .finding import ReportFinding, build_report_findings

if TYPE_CHECKING:
    from ..checker_types import Change, DiffResult
    from ..model import AbiSnapshot
    from ..policy.severity import GateDecision, SeverityConfig
    from .document import ReportDocument


@dataclass(frozen=True, slots=True)
class RenderOptions:
    """The format-neutral presentation options one render was asked for.

    Every field here is an option ``service_render.render_output`` accepts
    that is *not* a decision: it selects what a projection shows or how it
    spells it, never what the comparison concluded. ``show_only`` and
    ``show_impact`` sit here (rather than being resolved away) because they
    are display filters by definition -- ``gate_decision_for_result`` is
    documented as always scoring the full, unfiltered change set precisely so
    a display filter can never move a gate.
    """

    show_only: str | None = None
    report_mode: str = "full"
    show_impact: bool = False
    demangle: bool = False
    follow_deps: bool = False
    show_recommendation: bool = False
    require_complete_analysis: bool = False
    contract_evaluation: bool = False


@dataclass(frozen=True, slots=True)
class ReportEnvelope:
    """One completed evaluation, with every report decision already made.

    Construct it through :func:`build_report_envelope`; a projection reads
    it and never rebuilds any part of it.
    """

    result: DiffResult
    old: AbiSnapshot
    new: AbiSnapshot | None
    options: RenderOptions
    severity_config: SeverityConfig | None
    #: The one shared ``report_mode="full"`` document every format projects.
    document: ReportDocument
    #: One resolved verdict/category per change in ``result.changes``.
    findings: tuple[ReportFinding, ...]
    #: The severity gate decision, or ``None`` under the legacy scheme.
    gate: GateDecision | None
    #: The date ``build_report_envelope`` resolved every finding above
    #: against. A dated ``PolicyFile.reclassify`` rule's expiry is checked
    #: against *this* date wherever this envelope still needs to resolve a
    #: finding on demand (:meth:`_resolve`) -- never a fresh ``date.today()``
    #: read at render time, which could disagree with the findings above if
    #: the rule expires between construction and render (Codex review,
    #: fresh evidence).
    resolved_today: date
    #: Findings for the scoped-only changes a ``--used-by``/
    #: ``--required-symbol`` pass synthesized outside ``result.changes``
    #: (JUnit folds these into its own testcase tree; no other format does).
    scoped_only_findings: tuple[ReportFinding, ...] = ()
    #: Findings for ``result.suppressed_changes`` -- SARIF's own
    #: ``suppressions`` array registers every one of them, which otherwise
    #: made a suppressed-finding render fall through ``findings_for``'s
    #: per-change fallback on every single suppressed change, in a renderer
    #: an envelope is supposed to make read-only (Codex review, fresh
    #: evidence).
    suppressed_findings: tuple[ReportFinding, ...] = ()
    #: Cached ``id(change) -> finding`` index over all three tuples above,
    #: built on first use within one render. Valid only while those exact
    #: ``Change`` objects are alive -- see ``report/finding.py``'s module
    #: docstring on why a ``Change``-keyed cache is not an option.
    _index: dict[int, ReportFinding] = field(
        default_factory=dict, repr=False, compare=False
    )

    def _by_change_id(self) -> dict[int, ReportFinding]:
        """The live ``id(change) -> ReportFinding`` cache, built on first use.

        Deliberately not public: it is this envelope's own mutable cache, and
        a caller that wants an index of the *findings* should build its own
        from :func:`~abicheck.report.finding.findings_by_change_id` (which
        returns a fresh dict) rather than hold a reference to this one.
        """
        if not self._index:
            for finding in (
                *self.findings,
                *self.scoped_only_findings,
                *self.suppressed_findings,
            ):
                self._index[id(finding.change)] = finding
        return self._index

    def findings_for(self, changes: Sequence[Change]) -> tuple[ReportFinding, ...]:
        """Findings for *changes*, a format's own (possibly filtered) sequence.

        Every change the completed evaluation already resolved is *read* from
        this envelope, never re-decided. The one case that cannot be is a
        change object the display layer itself created:
        ``report_correlation._suppress_dangling_correlation_notes`` hands a
        renderer shallow *copies* of the changes whose correlation note would
        dangle under ``--show-only``, and a copy is a different object with no
        entry in the index. Those are resolved individually through the same
        canonical ``build_report_findings`` primitive, with the same policy
        inputs this envelope resolved everything else with -- so a copy can
        never be classified by a different rule set than its original.
        """
        index = self._by_change_id()
        resolved: list[ReportFinding] = []
        for change in changes:
            known = index.get(id(change))
            resolved.append(known if known is not None else self._resolve(change))
        return tuple(resolved)

    def _resolve(self, change: Change) -> ReportFinding:
        return build_report_findings(
            [change],
            policy=self.result.policy,
            kind_sets=self.result._effective_kind_sets(),
            policy_file=self.result.policy_file,
            today=self.resolved_today,
        )[0]


def resolved_gate(
    envelope: ReportEnvelope | None,
    result: DiffResult,
    severity_config: SeverityConfig | None,
) -> GateDecision | None:
    """The envelope's already-resolved gate, or one resolved for a direct caller.

    The additive shape every renderer's new ``envelope`` parameter uses: a
    render driven by :func:`build_report_envelope` reads the decision the
    envelope already made; a direct Tier-2/test caller with no envelope keeps
    the prior behaviour (one ``gate_decision_for_result`` call of its own).
    ``None`` is a real gate value ("no severity scheme in effect"), which is
    why the presence of the *envelope* is what selects the branch -- a plain
    ``gate or compute()`` default would silently re-resolve every unconfigured
    run, which is exactly the per-renderer re-derivation this closes.
    """
    if envelope is not None:
        return envelope.gate
    from ..policy.gate_decision import gate_decision_for_result

    return gate_decision_for_result(result, severity_config)


def resolved_document(
    envelope: ReportEnvelope | None,
    report_document: ReportDocument | None,
) -> ReportDocument | None:
    """The envelope's shared document, else the caller's own ``report_document``.

    The envelope wins: when both are supplied, the caller is inside an
    envelope-driven render and the envelope's document *is* the shared one.
    """
    return envelope.document if envelope is not None else report_document


def env_matrix_digest_reusing_document(
    result: DiffResult,
    document: ReportDocument | None,
) -> str | None:
    """The env-matrix deployment-floor digest, reused from a shared
    ``ReportDocument`` when one exists, mirroring
    ``disposition_audit.disposition_audit_dict_reusing_document``.

    The document already froze ``env_matrix_source_sha256`` off ``result`` at
    build time (``build_report_document``/``build_html_document``), so a
    renderer holding an already-resolved document (via ``resolved_document``)
    must read the digest from there rather than re-reading the *mutable*
    ``result`` a second time -- a mutation to
    ``result.env_matrix_source_sha256`` after the document/envelope was built
    must never leak into a still-later render of that same document (Codex
    review, fresh evidence -- SARIF's and JUnit's own projections were still
    reading ``result`` directly after every other format was fixed for this
    exact class). A direct caller with no document at all (no envelope, no
    ``report_document``) keeps the prior, independent behaviour of reading
    *result* straight.
    """
    if document is None:
        return result.env_matrix_source_sha256
    value = document.to_mapping().get("env_matrix_source_sha256")
    return value if isinstance(value, str) else None
