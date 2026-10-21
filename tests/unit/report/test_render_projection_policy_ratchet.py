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

The same ratchet covers ``html_report.py``, the HTML compute half that every
``render_html*`` projection is fed from. It is a *source* module, not a
renderer, so it may read a ``DiffResult``; what it may not do is reach policy
on its own. Its policy-touching facts (impact text, active reclassify rules,
effective kind sets, the disposition audit and the versioning acceptance) now
resolve through ``report/`` owners. What remains is recorded below and shrinks
as those two legacy root modules move into ``report/``.

Recorded debt (tracked by ``report/AGENTS.md``'s "Prohibited
responsibilities"): both Markdown projections import ``effective_kind_sets``
and call ``compute_disposition_audit`` as a fallback for direct callers that
pass no envelope, and ``render_markdown_document`` resolves
``evidence_status_for_result``/``impact_for`` inside its row builder. These
are compute-side functions that still live in render modules.
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
    }
)
#: Disposition-audit *compute* entry points (the renderer should read the
#: document's ``disposition_audit`` instead of recomputing it).
_DISPOSITION_COMPUTE = frozenset({"compute_disposition_audit", "add_disposition_audit"})

#: Compute-half source module outside ``report/`` that the ratchet also covers.
_SOURCE_MODULES = ("html_report.py",)

_RECORDED_POLICY_IMPORTS: dict[str, frozenset[str]] = {
    "render_markdown_alternate.py": frozenset({"policy.evaluate"}),
    "render_markdown_document.py": frozenset(
        {"policy.evaluate", "policy.classification"}
    ),
    # Legacy root modules still holding the HTML compute's registry lookups
    # (``kind_str``/``category``/``severity``) and ``compatibility_metrics``.
    "html_report.py": frozenset({"report_classifications", "report_summary"}),
}
_RECORDED_DISPOSITION_COMPUTE: dict[str, frozenset[str]] = {
    "render_markdown_alternate.py": frozenset({"compute_disposition_audit"}),
    "render_markdown_document.py": frozenset({"compute_disposition_audit"}),
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
