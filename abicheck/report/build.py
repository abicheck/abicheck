# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""The one shared report-document build entry point (ADR-061 Phase 2 gap C).

Before this module, every output format (JSON, Markdown, HTML, SARIF,
JUnit) independently walked a completed :class:`~abicheck.checker_types.
DiffResult` through its own ``_add_*``/``compute_*`` pipeline before
freezing a :class:`~abicheck.report.document.ReportDocument` -- six
separately-decided projections of the same evaluation, rather than one
shared decided document each format purely projects.

:func:`build_report_document` is the single choke point: called once per
top-level render (see ``abicheck.service_render.render_output``), it
performs every fact/decision computation exactly once and returns the frozen
document every enabled format then reads from. A format needing data this
document does not yet carry should get that data added *here*, never
recomputed locally in the format's own renderer.

This is the ``report_mode="full"`` build formerly inlined in
``abicheck.reporter.to_json`` -- moved here (rather than kept as a sibling
function in ``reporter.py``) purely to stay clear of that file's ADR-061
``no_growth`` architecture-debt baseline (``architecture/debt.yaml``); the
logic is otherwise unchanged from what ``to_json`` used to run inline. This
module imports ``reporter.py``'s private ``_add_*``/``_build_*`` helpers
statically (function-local, to avoid a module-level import-time ordering
issue, but a real, named import all the same); ``reporter.to_json`` reaches
back into *this* module through ``importlib`` instead of a static
``from .report.build import ...``, so the pair never forms a real import
cycle -- only one direction is a static edge.

Scope note (gap C closure package 3): every format now projects **one**
document, not one document per format. :func:`build_report_envelope` is the
call that makes that true -- it resolves the severity gate, the per-finding
verdict/category set and this document once, above format selection, and
hands the resulting :class:`~abicheck.report.envelope.ReportEnvelope` to
``service_render.render_envelope``. It lives here, next to the document build
it composes, rather than in ``report/envelope.py`` (see that module's own
docstring: an ``envelope -> build`` import edge would close a real cycle
through ``reporter.py``). ``--stat``/``oneline`` and the ``leaf``/
``root-cause`` views remain separate documents by design -- see
``docs/contribute/adr/061-responsibility-package-architecture.md``'s gap C
status note and ``docs/contribute/plans/
duplication-and-convergence-assessment.md``'s Phase 4 note.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..checker_types import Change
from .document import ReportDocument
from .envelope import RenderOptions, ReportEnvelope
from .finding import build_report_findings

if TYPE_CHECKING:
    from ..checker_types import DiffResult
    from ..model import AbiSnapshot
    from ..policy.severity import GateDecision
    from ..workflows.gate import SeverityConfig


def build_report_document(
    result: DiffResult,
    *,
    show_only: str | None = None,
    show_impact: bool = False,
    severity_config: SeverityConfig | None = None,
    require_complete_analysis: bool = False,
    include_exit_decision: bool = True,
    contract_evaluation: bool = False,
    gate: GateDecision | None = None,
) -> ReportDocument:
    """Build the one canonical, format-neutral ``report_mode="full"`` document.

    Every keyword mirrors ``abicheck.service_render.render_output``'s own
    format-neutral options (``show_only``/``show_impact``/
    ``severity_config``/``require_complete_analysis``/
    ``include_exit_decision``/``contract_evaluation``); a genuinely
    format-specific presentation flag (HTML's ``demangle``, Markdown's
    ``show_recommendation``) is not a parameter here -- it belongs to the
    format's own render step, applied to a *projection* of this document,
    never to a second, differently-decided build.

    *gate* (ADR-061 gap C) is the severity ``GateDecision``
    :func:`build_report_envelope` below already resolved
    for this render, so the envelope and this document cannot each resolve
    one. Passing ``None`` (the default) resolves it here, exactly as before.
    That default is unambiguous rather than a missing sentinel:
    ``gate_decision_for_result`` returns ``None`` *if and only if*
    ``severity_config`` is ``None``, so a real resolved gate is never ``None``
    while a gate is configured -- "not supplied" and "resolved to no gate"
    can only coincide in the one case where both answers are identical. That
    same equivalence is why the fallback below is skipped outright when
    ``severity_config is None``: resolving there could only ever return the
    ``None`` this already holds.
    """
    # Static import of `reporter.py`'s own privately-defined helpers --
    # this is the one direction of the report.build <-> reporter dependency
    # that stays a real import; `reporter.to_json` reaches back into this
    # module through `importlib` instead, so the pair never forms an
    # actual import cycle (see `reporter.to_json`'s own comment).
    from ..policy.gate_decision import gate_decision_for_result
    from ..reporter import (
        _SCOPED_GATE_HELPERS,
        _add_abi_surface_breakdown,
        _add_changes_block,
        _add_confidence_evidence,
        _add_detectors,
        _add_evidence_fields,
        _add_policy_overrides,
        _add_reconciled,
        _add_show_only_filter,
        _add_suppression,
        _add_surface_scope,
        _add_trailing_fields,
        _build_json_base,
        _build_severity_json,
        _displayed_with_scoped_only,
        _suppress_dangling_correlation_notes,
        apply_show_only,
    )
    from ..reporter_contract_blocks import (
        add_contract_context as _add_contract_context,
        build_report_document_with_side_facts,
    )
    from .disposition_audit import add_disposition_audit as _add_disposition_audit
    from .finding_evolution import add_finding_evolution as _add_finding_evolution
    from .pattern_preprocessor_scan import (
        add_pattern_preprocessor_scan as _add_pattern_preprocessor_scan,
    )
    from .surface_changes import add_surface_changes as _add_surface_changes

    changes = list(result.changes)
    if show_only:
        changes = apply_show_only(
            changes,
            show_only,
            policy=result.policy,
            kind_sets=result._effective_kind_sets(),
            policy_file=result.policy_file,
        )
        changes = _suppress_dangling_correlation_notes(changes)

    d = _build_json_base(result)
    _add_abi_surface_breakdown(d, result)
    _add_evidence_fields(d, result)
    effective_policy = result.policy or "strict_abi"
    d["policy"] = effective_policy
    eff_sets = result._effective_kind_sets()

    if show_only:
        _add_show_only_filter(d, result, changes, show_only)

    # Severity-categorized summary when severity config is provided
    if gate is None and severity_config is not None:
        gate = gate_decision_for_result(result, severity_config)
    if gate is not None:
        assert severity_config is not None  # gate is None otherwise
        d["severity"] = _build_severity_json(
            changes,
            severity_config,
            gate=gate,
            policy=result.policy,
            kind_sets=eff_sets,
            policy_file=result.policy_file,
        )

    _add_changes_block(
        d,
        result,
        changes,
        effective_policy,
        eff_sets,
        show_only,
        severity_config=severity_config,
    )
    _add_suppression(d, result)
    _add_disposition_audit(d, result, severity_config)
    _add_surface_changes(d, result, changes)
    _add_finding_evolution(d, result)
    _add_pattern_preprocessor_scan(d, result)
    _add_surface_scope(d, result)
    _add_reconciled(d, result)
    _add_contract_context(
        d,
        result,
        _displayed_with_scoped_only(result, changes, show_only),
        require_complete_analysis=require_complete_analysis,
        severity_config=severity_config,
        include_exit_decision=include_exit_decision,
    )
    _add_detectors(d, result)
    _add_confidence_evidence(d, result)
    _add_policy_overrides(d, result)
    _add_trailing_fields(d, result, show_impact, show_only)
    return build_report_document_with_side_facts(
        d,
        result,
        helpers=_SCOPED_GATE_HELPERS,
        severity_config=severity_config,
        gate=gate,
        show_only=show_only,
        contract_evaluation=contract_evaluation,
    )


def _snapshot_change(change: Change) -> Change:
    """A fully independent copy of a single finding.

    ``Change`` is an ordinary mutable dataclass, not a value type -- pattern
    modulation legitimately sets ``effective_verdict`` on one *during*
    ``compare()``, for one concrete example. A shared ``Change`` instance
    reassigned after this envelope was built (its ``effective_verdict``, or
    any other field) would still be classified from its pre-mutation value
    by ``document``/``findings`` (frozen at that value) while a projection
    that classifies straight from ``envelope.result`` would read the new
    one -- the same class of disagreement :func:`_snapshot_diff_result`
    closes for the containing lists, one level down.

    A full ``copy.deepcopy`` rather than a shallow copy of just the
    top-level list fields: several fields nest a mutable container inside
    another (``impact_proof_path: list[dict[str, object]]``,
    ``impact_alternative_paths: list[list[dict[str, object]]]``) -- a
    shallow per-field list copy decouples the outer list but still shares
    the dicts inside it, so mutating ``impact_proof_path[0]["label"]`` after
    construction would reach the envelope exactly as reassigning
    ``effective_verdict`` did before this fix (Codex review, fresh
    evidence). Every field ``Change`` actually carries is plain,
    self-contained data (strings, enums, nested frozen dataclasses like
    ``ImpactAssessment``, dicts/lists of the same) -- nothing here holds a
    reference to another large shared object (an ``AbiSnapshot``, a
    ``PolicyFile``) that a deep copy would wastefully duplicate.
    """
    import copy

    return copy.deepcopy(change)


def _snapshot_diff_result(result: DiffResult) -> DiffResult:
    """Return a copy of *result* with every list/tuple-of-``Change`` replaced.

    :class:`ReportEnvelope` exists so a decision made once cannot drift by
    the time a later projection reads it -- but ``DiffResult`` itself is an
    ordinary mutable dataclass (``contract_pipeline``/``post_manifest``
    legitimately append to ``.changes`` *during* ``compare()``, and
    ``cli_scan_baseline`` reassigns it afterward for its own filtering
    pass). A caller handing this same, still-live object to a later,
    unrelated mutation after the envelope was built would otherwise
    desynchronize ``document``/``findings``/``gate`` (built from the
    original list) from any projection that reads ``envelope.result``
    directly for presentation (HTML's/JUnit's bucketing, SARIF's rule
    catalog) -- the exact case ``report/AGENTS.md``'s immutability
    contract for this envelope forbids. Every ``Change`` element is itself
    snapshotted too (:func:`_snapshot_change`) -- copying only the
    containers and leaving the mutable ``Change`` objects inside shared by
    reference closes the container-level version of this bug but not the
    element-level one (a caller reassigning ``change.effective_verdict``
    after construction, reported as a follow-up finding on this same fix).

    ``copy.copy`` (not ``dataclasses.replace``) on purpose: some scoping
    passes (``cli_helpers_compare.py``'s ``result.scoped_only_changes =
    ...``) attach attributes that are not declared ``DiffResult`` fields at
    all; ``dataclasses.replace`` reconstructs the object through
    ``__init__`` and would silently drop them, while ``copy.copy`` carries
    every attribute in ``__dict__``, declared or not. A list-valued
    attribute gets a fresh list (with every ``Change`` element replaced by
    its own snapshot; a non-``Change`` element is shared as before -- e.g.
    ``coverage_warnings``' plain strings need no copy of their own). A
    tuple-valued attribute (e.g. ``scoped_only_changes``) is already immune
    to in-place *container* mutation, but still needs its own ``Change``
    elements replaced the same way. A dict-valued attribute (e.g.
    ``comparability_assurance``, read straight off ``envelope.result`` by
    HTML's/Markdown's comparability section -- CodeRabbit review) gets a
    fresh dict for the same reason a list does.

    Every ``Change`` this replaces is tracked in an ``id(original) ->
    replacement`` map, then used to remap ``result.disposition_ledger``
    (:func:`_remap_disposition_ledger`) -- that ledger's every consumer
    keys strictly on ``id(change)`` (Codex review, fresh evidence:
    ``report/disposition_audit.py``'s own ``ledger_for(result,
    severity_config)`` call, on this same snapshot, otherwise reads every
    gating record as outside its gate's ``severity_input``, since none of
    the ledger's recorded identities match these new objects).

    ``policy_file`` gets the same deep copy: it is a custom, mutable
    object (not a list/tuple/dict this loop otherwise catches), and more
    than one format calls ``effective_verdict_for_change``/
    ``classify_effective_change`` straight against ``envelope.result.
    policy_file`` for its own classification -- HTML's own
    ``compatibility_metrics`` call among them (Codex review, fresh
    evidence: a `PolicyFile.overrides`` mutation after construction moved
    HTML's binary-compatibility percentage while ``document``/``findings``
    stayed at their frozen values).
    """
    import copy

    snapshot = copy.copy(result)
    identity_map: dict[int, Change] = {}

    def _snapshot_maybe_change(value: object) -> object:
        if isinstance(value, Change):
            new_value = _snapshot_change(value)
            identity_map[id(value)] = new_value
            return new_value
        return value

    for name, value in vars(result).items():
        if isinstance(value, list):
            setattr(snapshot, name, [_snapshot_maybe_change(v) for v in value])
        elif isinstance(value, tuple) and any(isinstance(v, Change) for v in value):
            setattr(snapshot, name, tuple(_snapshot_maybe_change(v) for v in value))
        elif isinstance(value, dict):
            setattr(snapshot, name, dict(value))
    if snapshot.policy_file is not None:
        snapshot.policy_file = copy.deepcopy(snapshot.policy_file)
    ledger = getattr(snapshot, "disposition_ledger", None)
    if ledger is not None:
        snapshot.disposition_ledger = _remap_disposition_ledger(ledger, identity_map)
    return snapshot


def _remap_disposition_ledger(ledger: object, mapping: dict[int, Change]) -> object:
    """A copy of *ledger* with every ``id(change)``-keyed identity remapped.

    ``policy.disposition_ledger.DispositionLedger`` keys every one of its
    lookups (``with_gate``'s ``severity_input`` test, ``index_for``/
    ``record_for``/``rule_for``) on ``id(change)`` against the objects it
    was recorded with -- see that class's own docstrings. This module
    replaces every recorded ``Change`` with an independent copy
    (:func:`_snapshot_change`/:func:`_snapshot_diff_result`), which
    otherwise desynchronizes those lookups from this ledger's *own*
    ``_anchors``/``_seen_ids``, silently answering every one of them
    "unrecorded". Reaches into the ledger's own private state (rather than
    adding a public method there) because that module carries an
    ``architecture/debt.yaml`` ``no_growth`` baseline this PR does not own
    and is already at, with no headroom for a new method; every attribute
    name here is the one that class's own docstrings already document.
    """
    import copy as _copy

    remapped = _copy.copy(ledger)
    remapped._records = list(ledger._records)  # type: ignore[attr-defined]
    remapped._seen_keys = dict(ledger._seen_keys)  # type: ignore[attr-defined]
    remapped._anchors = [mapping.get(id(a), a) for a in ledger._anchors]  # type: ignore[attr-defined]
    remapped._aliases = [mapping.get(id(a), a) for a in ledger._aliases]  # type: ignore[attr-defined]
    remapped._seen_ids = {  # type: ignore[attr-defined]
        (id(mapping[old_id]) if old_id in mapping else old_id): idx
        for old_id, idx in ledger._seen_ids.items()  # type: ignore[attr-defined]
    }
    return remapped


def build_report_envelope(
    result: DiffResult,
    old: AbiSnapshot,
    new: AbiSnapshot | None = None,
    *,
    options: RenderOptions | None = None,
    severity_config: SeverityConfig | None = None,
) -> ReportEnvelope:
    """Finalize every report decision for *result*, once, before format selection.

    This is the only place the three shared resolutions run for a render:
    ``gate_decision_for_result`` once, ``build_report_findings`` once per
    change, and ``build_report_document`` once -- the gate resolved *first*
    and handed to the document build, so the document's own ``severity``
    block and the decision object SARIF's and HTML's gate blocks read are
    literally the same object rather than two calls that agree.

    *result* is snapshotted first (:func:`_snapshot_diff_result`) so nothing
    a caller does to the object it passed in after this call returns can
    ever reach the envelope -- every decision below, and every projection
    that reads ``envelope.result`` directly, is computed from that one
    frozen-in-effect copy. *old*/*new* get the identical treatment: they are
    the public multi-format workflow's own retained operands, read directly
    by more than one projection (HTML's/JSON's version and dependency-info
    fields, JUnit's unchanged-testcase set) that ``build_report_document``
    itself never touches -- reassigning ``old.version`` or mutating
    ``old.functions`` after this call returned would otherwise desynchronize
    exactly those projections from one another (Codex review, fresh
    evidence).
    """
    import copy

    from ..policy.gate_decision import gate_decision_for_result

    result = _snapshot_diff_result(result)
    old = copy.deepcopy(old)
    new = copy.deepcopy(new) if new is not None else None
    opts = options if options is not None else RenderOptions()
    gate = gate_decision_for_result(result, severity_config)
    document = build_report_document(
        result,
        show_only=opts.show_only,
        show_impact=opts.show_impact,
        severity_config=severity_config,
        require_complete_analysis=opts.require_complete_analysis,
        contract_evaluation=opts.contract_evaluation,
        gate=gate,
    )
    kind_sets = result._effective_kind_sets()
    findings = build_report_findings(
        result.changes,
        policy=result.policy,
        kind_sets=kind_sets,
        policy_file=result.policy_file,
    )
    scoped_only = tuple(getattr(result, "scoped_only_changes", ()) or ())
    scoped_only_findings = (
        build_report_findings(
            list(scoped_only),
            policy=result.policy,
            kind_sets=kind_sets,
            policy_file=result.policy_file,
        )
        if scoped_only
        else ()
    )
    return ReportEnvelope(
        result=result,
        old=old,
        new=new,
        options=opts,
        severity_config=severity_config,
        document=document,
        findings=findings,
        gate=gate,
        scoped_only_findings=scoped_only_findings,
    )
