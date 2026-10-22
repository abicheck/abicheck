"""Ratchet: report renderers (``report/render_*.py``) may not reach policy.

``test_render_html.py`` guards the HTML render modules with a whole-module
denylist. The Markdown projections were split later and still reach policy
in two places, so this test records exactly what they reach today and fails
on any change in either direction:

* a *new* policy or disposition-compute import in any ``render_*`` module
  fails, because a renderer must consume facts the compute half already
  decided (``report/AGENTS.md``, "Decides nothing");
* a *fixed* recorded import also fails, so the allowlist below can only
  shrink. Removing the entry is the signal the debt is paid.

The import list is what changes when a renderer starts deciding, so it is
the thing to assert on; the rendered text is identical either way.

The same ratchet covers the compute half modules (``html_report.py``,
``reporter.py``, ``reporter_markdown.py`` and ``report/markdown_compute.py``).
They are *source* modules, not renderers, so they may read a ``DiffResult``;
their kind sets, impact text, reclassify rules, exit codes and classification
resolve through ``report/policy_facts.py``. What remains recorded below is
the decision that still lives in the compute half and is not yet routed:
the severity categorisation in ``reporter_markdown.py``, the disposition-audit
fallbacks for direct callers that pass no envelope, and the type-only
``policy.severity`` annotations.
"""

from __future__ import annotations

import ast
from pathlib import Path

import abicheck.report as report_package

#: Top-level packages a renderer must not reach for decisions.
_POLICY_HEADS = frozenset(
    {
        "policy",
        "checker_policy",
        "report_classifications",
        "reclassify",
        "severity",
        "report_summary",
        "semver",
    }
)
#: Disposition-audit *compute* entry points (the renderer should read the
#: document's ``disposition_audit`` instead of recomputing it).
_DISPOSITION_COMPUTE = frozenset({"compute_disposition_audit", "add_disposition_audit"})

#: Compute-half source modules the ratchet also covers. ``reporter.py`` (the
#: ``--stat`` and root-cause JSON path), ``reporter_markdown.py`` (the Markdown
#: compute functions) and ``html_report.py`` (the HTML compute half) are source
#: modules outside ``report/``: they may read a ``DiffResult`` and take their
#: kind sets, run summary, release recommendation and decisions from
#: ``report/policy_facts.py``. ``report/markdown_compute.py`` is the compute
#: half of both Markdown projections: the render modules consume its document,
#: so the decisions it still makes are recorded here rather than inside them.
_SOURCE_MODULES = (
    "html_report.py",
    "reporter.py",
    "reporter_markdown.py",
    "report/markdown_compute.py",
)

_RECORDED_POLICY_IMPORTS: dict[str, frozenset[str]] = {
    # ``policy.severity`` here is the ``TYPE_CHECKING``-only ``SeverityConfig``
    # annotation: a type, not a decision. Nothing else in the module reaches policy.
    "reporter.py": frozenset({"policy.severity"}),
    # Two runtime decisions remain here, not in a renderer: the severity
    # categorisation behind ``compute_severity_sections`` (``policy.severity``'s
    # ``categorize_changes``/``gate_eligible_changes``) and the ``ADDITION_KINDS``
    # / ``INFORMATIONAL_KINDS`` sets of the compatible-quality list. The rest of
    # the module reaches policy through ``report/policy_facts.py`` (the
    # ``GateDecision``/``KindSets``/``SeverityConfig`` names are annotations).
    "reporter_markdown.py": frozenset({"policy.classification", "policy.severity"}),
}
_RECORDED_DISPOSITION_COMPUTE: dict[str, frozenset[str]] = {
    # The alternate-mode views (``--report-mode leaf``/``root-cause``) take no
    # envelope, so their preamble and root-cause builders compute the audit
    # directly. The full-mode builder reuses the envelope's document through
    # ``disposition_audit_dict_reusing_document``.
    "markdown_compute.py": frozenset({"compute_disposition_audit"}),
    # ``--stat`` and root-cause JSON still compute the disposition audit per call
    # rather than reading it from a shared document (no envelope reaches them).
    "reporter.py": frozenset({"add_disposition_audit"}),
    # Direct-caller fallbacks for the one-line view and the review digest; the
    # envelope-driven paths pass the audit in.
    "reporter_markdown.py": frozenset({"compute_disposition_audit"}),
}


def _render_modules() -> list[Path]:
    # ``abicheck.report`` is a namespace package: no ``__file__`` to walk from.
    package_dir = Path(next(iter(report_package.__path__)))
    return sorted(package_dir.glob("render_*.py"))


def _ratchet_modules() -> list[Path]:
    package_dir = Path(next(iter(report_package.__path__)))
    source_dir = package_dir.parent
    return [*_render_modules(), *(source_dir / name for name in _SOURCE_MODULES)]


def _observed() -> tuple[dict[str, frozenset[str]], dict[str, frozenset[str]]]:
    """Every import in every ratcheted module, module-level or function-local."""
    policy: dict[str, frozenset[str]] = {}
    disposition: dict[str, frozenset[str]] = {}
    for path in _ratchet_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        reached: set[str] = set()
        compute: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.module is None:
                continue
            dotted = node.module
            head = dotted.lstrip(".").split(".")[0]
            if head in _POLICY_HEADS:
                reached.add(dotted.lstrip("."))
            if dotted.lstrip(".").endswith("disposition_audit"):
                compute.update(
                    alias.name
                    for alias in node.names
                    if alias.name in _DISPOSITION_COMPUTE
                )
        if reached:
            policy[path.name] = frozenset(reached)
        if compute:
            disposition[path.name] = frozenset(compute)
    return policy, disposition


def test_render_modules_reach_policy_only_as_recorded() -> None:
    policy, _ = _observed()
    assert policy == _RECORDED_POLICY_IMPORTS, (
        "a report renderer's policy imports changed. A new one must be "
        "computed compute-side and carried in the ReportDocument; a fixed one "
        "must be removed from _RECORDED_POLICY_IMPORTS."
    )


def test_render_modules_call_disposition_compute_only_as_recorded() -> None:
    _, disposition = _observed()
    assert disposition == _RECORDED_DISPOSITION_COMPUTE, (
        "a report renderer recomputes the disposition audit. Read it from the "
        "shared ReportDocument instead, or update the recorded set if the "
        "fallback was removed."
    )


def test_render_html_modules_stay_policy_free() -> None:
    """The HTML render modules are already clean; they must never be added to
    the allowlist. ``html_report.py`` is a source module, asserted above."""
    policy, disposition = _observed()
    html_renderers = [name for name in policy if name.startswith("render_html")]
    assert html_renderers == []
    assert [name for name in disposition if name.startswith("render_html")] == []
