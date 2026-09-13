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

"""Scoped-gate (``--used-by``/``--required-symbol(s)``) summary fold-in for
the ``compare`` command's rendered report text.

Size-split from :mod:`abicheck.cli_compare_helpers` (AI-readiness file-size
cap). These functions only reach leaf report-formatting modules
(:mod:`abicheck.reporter`, :mod:`abicheck.reporter_markdown`,
:mod:`abicheck.severity`, :mod:`abicheck.checker_policy`) -- none of them
touch ``cli_dump_helpers``/``cli_buildsource_helpers``, so this module does
not join the CLI-registration import-cycle SCC those do (CLAUDE.md "What NOT
to do": extending ``IMPORT_CYCLE_ALLOWLIST`` needs an ADR, so the split
boundary was chosen specifically to avoid needing one).

ADR-061 Phase 2 item 5 (post-render mutation): the module used to also host
``_fold_evidence_depth_into_json`` (moved to a real ``DiffResult`` field,
``cli_compare_helpers`` computes it before rendering now),
:func:`_fold_suppression_audit_into_text`'s own JSON branch (moved into
``reporter.to_json``'s document construction, since
``result.suppression_audit`` was already attached before rendering here
too), and :func:`_fold_scoped_compat_into_text`'s own JSON branch (moved to
:func:`abicheck.report.scoped_gate.apply_scoped_gate`, called from inside
``reporter.to_json``/``to_stat_json`` themselves once those gained a real
``contract_evaluation`` parameter -- ``severity_config``/``show_only`` were
already threaded, and the remaining inputs were already plain ``DiffResult``
attributes). All three were "inject an already-known fact" cases; the
scoped-gate one additionally re-derived already-built document sections
(``summary``/``severity``/``root_causes``), which is why it needed
restructuring ``reporter.py``'s JSON builders rather than a plain fold-in
move -- see :mod:`abicheck.report.scoped_gate`'s own docstring. JSON output
is therefore no longer folded by this module at all: ``fmt == "json"`` falls
through :func:`_fold_scoped_compat_into_text` untouched.

The markdown/text/review appends run *after* ``to_markdown``'s own single,
whole-report ``_out()`` demangle pass, so their own content was never
demangled under ``--demangle`` -- including :func:`_fold_scoped_compat_into_
text`'s own ``into_text`` append (missing symbol/entrypoint names, scoped-
only change descriptions), which renders the same kind of mangled C++ name
as the other two. Rather than moving these fold-ins earlier (which would
either change that pass's scope for every other section too, or reproduce
the identical post-render append one file over), all three of
:func:`_fold_scoped_compat_into_text`, :func:`_fold_suppression_audit_into_
text`, and :func:`_fold_use_case_impact_into_text` now accept their own
``demangle`` flag and demangle only the section(s) they add -- never *text*
itself -- via the same ``demangle_text`` helper ``to_markdown``'s ``_out()``
calls. This closes this half of item 5 without changing where in the
pipeline any fold-in runs. ``into_json``/``into_oneline`` are unaffected --
JSON/one-line output carries raw symbol data by design.
:func:`_fold_suppression_audit_into_text` demangles less than the other
two: a rule's own :func:`~abicheck.reporter_contract_blocks.
suppression_rule_label` selector echo is never demangled, only the
free-standing symbol prose around it -- see that function's own docstring
for why (two distinct selectors can demangle to the same display string).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

from .errors import ProfileMismatchError, ScopeMismatchError
from .frontends.cli.runtime import _write_or_echo
from .service_render import ONELINE_FORMAT as _ONELINE_FORMAT

if TYPE_CHECKING:
    from .model import AbiSnapshot


def _fold_scoped_compat_into_text(
    text: str,
    fmt: str,
    result: Any,
    severity_config: Any = None,
    show_only: str | None = None,
    report_mode: str = "full",
    contract_evaluation: bool = False,
    *,
    demangle: bool = False,
) -> str:
    """Fold ``--used-by``/``--required-symbol(s)`` summaries into the rendered text.

    JSON is left untouched here (``fmt == "json"`` falls straight through to
    the final ``return text``): ``reporter.to_json``/``to_stat_json`` already
    build the scoped-aware payload natively, via
    :func:`abicheck.report.scoped_gate.apply_scoped_gate`, before this
    function ever sees the rendered text (ADR-061 Phase 2 item 5). Other
    text-based formats still get a small appended section here. Binary/
    structured formats (sarif, junit, html) are left untouched -- the full
    verdict they already carry stays authoritative for those consumers.

    *severity_config* (when the run used a severity scheme) decides whether a
    synthesized missing-contract entry is itself blocking, mirroring
    ``sarif._missing_contract_result``/``junit_report``'s severity-aware
    missing-contract handling.

    *show_only*, when given, filters ``scoped_only_changes`` before they are
    folded into the markdown/text/review append below -- ``to_json``/
    ``to_markdown`` already filtered ``result.changes`` by the same tokens
    upstream, so leaving the scoped-only fold-in unfiltered would let a
    `--show-only` run re-surface a finding it explicitly excluded (Codex
    review, mirrors the identical ``sarif.to_sarif`` fix and
    ``apply_scoped_gate``'s own JSON-side use of the same parameter). Pass
    ``None`` (the default) for a render that is deliberately
    always-unfiltered, e.g. the ``-o`` render, which ignores the
    primary format's own ``--show-only``.

    *report_mode* ``"root-cause"`` skips the markdown/text
    "## Additional scoped-gate findings" append (Codex review):
    ``reporter_markdown._to_markdown_root_cause`` already merges
    ``scoped_only``/``missing_labels`` into its own root-cause groups in that
    mode, so appending them again here would duplicate every scoped finding
    that also shows up grouped under its matching ``### root`` heading.
    ``review`` format ignores ``report_mode`` entirely (no root-cause
    rendering exists for it), so it always gets the appended section
    regardless of this parameter's value.

    *contract_evaluation* (ADR-049 Phase 3), when ``True``, stamps each
    synthesized missing-contract-label dict entry built below with the
    explicit-scope ``IN_CONTRACT`` decision via
    ``contract_evaluation.stamp_explicit_scope_contract_evaluation`` --
    mirroring the MCP ``abi_compare`` tool's identical stamping of its own
    missing-label entries. The ``scoped_only`` ``Change`` objects folded in
    below need no separate stamping here: the caller
    (``cli_compare_helpers.run_compare``) already stamps them (and any
    matching ``result.changes`` entry) in place before this function runs,
    so ``_change_to_dict`` picks up the decision for free the same way it
    does for any other already-stamped ``Change``.

    *demangle*, when True, demangles the ``into_text`` append (missing
    symbol/entrypoint names, scoped-only change descriptions) the same way
    ``reporter_markdown.to_markdown``'s own ``_out()`` pass demangles
    everything rendered before this fold-in runs (ADR-061 Phase 2 item 5) --
    this section runs after that pass and previously stayed mangled
    regardless of ``--demangle``. Never applied to ``into_json``/
    ``into_oneline``, which carry raw symbol data by design.
    """
    used_by = getattr(result, "used_by", None)
    required_symbols = getattr(result, "required_symbols", None)
    if used_by is None and required_symbols is None:
        return text
    fold = _ScopedFold(
        result=result,
        severity_config=severity_config,
        show_only=show_only,
        report_mode=report_mode,
        contract_evaluation=contract_evaluation,
        used_by=used_by,
        required_symbols=required_symbols,
        demangle=demangle,
    )
    if fmt in ("markdown", "text", "review"):
        return fold.into_text(text, fmt)
    # ONELINE_FORMAT (`-o oneline=...`) falls through unchanged (workstream
    # D-S1): the incoming `text` already states the full-library
    # verdict/counts the process actually exits on, and a supplied
    # consumer's own result no longer needs to replace it -- the one-line
    # contract `oneline` guarantees has no room for an appended consumer
    # breakdown either, so it is left to the fuller markdown/text/review/JSON
    # reports.
    return text


@dataclass(frozen=True)
class _ScopedFold:
    """One scoped-gate fold-in: the inputs, and the per-format renderings.

    A class rather than one branching function (CodeFactor: complex method) so
    the JSON payload rewrite, the ``--stat`` summary-only variant, and the
    markdown/text/review append are separate, individually readable steps that
    share these inputs instead of threading eight parameters through each other.
    Every method is a pure function of these fields plus the text handed in.
    """

    result: Any
    severity_config: Any
    show_only: str | None
    report_mode: str
    contract_evaluation: bool
    used_by: Any
    required_symbols: Any
    demangle: bool = False

    @property
    def scoped_verdict_value(self) -> Any:
        """The scoped verdict as its plain value (an enum's ``.value`)."""
        scoped_verdict = getattr(self.result, "scoped_verdict", None)
        return getattr(scoped_verdict, "value", scoped_verdict)

    def _scoped_gate_findings(self) -> tuple[Any, Any, bool, Any]:
        """The scoped-only changes, missing-contract labels, and gate blocking
        decision this run's scoped gate actually rests on."""
        from .reporter import _resolve_scoped_gate_findings

        return _resolve_scoped_gate_findings(
            self.result, self.severity_config, self.show_only
        )

    # Workstream D-S1 (vision-api-abi-evolution.md "D. Optional
    # prebuilt-consumer lifecycle"): `service_render.ONELINE_FORMAT`
    # (`-o oneline=...`) is no longer replaced outright by
    # a scoped one-liner -- the process's own exit code and verdict always
    # come from the full-library result, so the plain one-liner
    # `_fold_scoped_compat_into_text` was handed already states it correctly.
    # `into_oneline` (the scoped-replacement renderer this used to dispatch
    # to) was removed with it; see this module's git history for the prior
    # design.

    # ── markdown / text / review ───────────────────────────

    def _scoped_verdict_header(self) -> list[str]:
        """The "consumer's own scoped verdict differs from the headline" note, or ``[]``.

        Workstream D-S1 (vision-api-abi-evolution.md "D. Optional
        prebuilt-consumer lifecycle"): a supplied consumer's own result is
        *informational* -- it enriches the full-library verdict this report's
        headline already rendered above, and never substitutes for it. The
        CLI process's own exit code always comes from that full-library
        verdict (or the resolved severity config), regardless of whether this
        consumer's own scoped assessment agrees. This note exists purely so a
        reader can see the two differ; it must never claim the scoped number
        is what the process exits with.
        """
        scoped_verdict_value = self.scoped_verdict_value
        full_verdict_value = getattr(
            getattr(self.result, "verdict", None), "value", None
        )
        if (
            scoped_verdict_value is None
            or full_verdict_value is None
            or scoped_verdict_value == full_verdict_value
        ):
            return []
        return [
            f"**Consumer-scoped verdict: {scoped_verdict_value}** "
            "(informational only -- this run's compatibility verdict and "
            f"exit code are always based on the full library verdict, "
            f"{full_verdict_value}).",
            "",
        ]

    def _used_by_lines(self) -> list[str]:
        """Per-application ``--used-by`` summaries, naming the missing symbols.

        Names the actual missing symbols/versions, not just their count (Codex
        review) -- a human reading the default text report otherwise has no way
        to tell *which* symbol broke this app without re-running with
        ``-o json=...``.
        """
        if self.used_by is None:
            return []
        lines = ["## Scoped to --used-by applications"]
        for summary in self.used_by:
            lines.append(
                f"- {summary['app']}: {summary['verdict']} "
                f"(missing {len(summary['missing_symbols'])} symbol(s), "
                f"{len(summary['missing_versions'])} version(s), "
                f"{summary['relevant_change_count']} relevant change(s))"
            )
            lines.extend(
                f"  - missing symbol: `{sym}`" for sym in summary["missing_symbols"]
            )
            lines.extend(
                f"  - missing version: `{ver}`" for ver in summary["missing_versions"]
            )
        return lines

    def _required_symbols_lines(self) -> list[str]:
        """The ``--required-symbol(s)`` entrypoint-contract summary."""
        required_symbols = self.required_symbols
        if required_symbols is None:
            return []
        lines = [
            "## Scoped to --required-symbol(s) contract",
            f"- verdict: {required_symbols['verdict']} "
            f"(missing {len(required_symbols['missing_entrypoints'])} of "
            f"{len(required_symbols['required_entrypoints'])} required "
            f"entrypoint(s))",
        ]
        lines.extend(
            f"  - missing entrypoint: `{entrypoint}`"
            for entrypoint in required_symbols["missing_entrypoints"]
        )
        return lines

    def _missing_label_line(self, label: str, severity_tag: str) -> str:
        """One "required but missing" contract-label line.

        ADR-049 Phase 3 (Codex review, fresh evidence): a missing-contract
        label is a bare string here, not the stamped dict entry the JSON branch
        builds -- without the contract suffix, the default
        markdown/text/review report (unlike JSON) silently dropped the contract
        decision for this exact finding shape.
        """
        line = (
            f"- `{label}` is required but missing from the new library ({severity_tag})"
        )
        if not self.contract_evaluation:
            return line
        from .contract_scoped_promotion import (
            stamp_explicit_scope_contract_evaluation,
        )

        label_decision: dict[str, object] = {}
        stamp_explicit_scope_contract_evaluation(label_decision)
        return line + (
            f" [contract: {label_decision['contract_relevance']} "
            f"({label_decision['contract_reason_code']}), "
            f"assurance: {label_decision['contract_assurance']}]"
        )

    @staticmethod
    def _scoped_only_line(c: Any) -> str:
        """One scoped-only ``Change`` line, rendering any contract stamp it has.

        ``scoped_only`` changes are already stamped by
        ``stamp_scoped_result_findings`` upstream when ``contract_evaluation``
        was requested -- render what's already there instead of re-deriving it.
        """
        line = f"- {c.kind.value}: {c.description}"
        relevance = getattr(c, "contract_relevance", None)
        if relevance is None:
            return line
        assurance = getattr(c, "contract_assurance", None)
        line += (
            f" [contract: {relevance.value} "
            f"({getattr(c, 'contract_reason_code', None)})"
        )
        if assurance is not None:
            line += f", assurance: {assurance.value}"
        return line + "]"

    def _scoped_gate_finding_lines(self, fmt: str) -> list[str]:
        """Scoped-only changes and uncovered missing-contract labels.

        These are relevant to the scoped gate but never land in
        ``result.changes`` -- name them here too, mirroring the
        JSON/SARIF/JUnit fold-in, so a text/markdown/review reader sees the
        same actionable findings a JSON consumer would (Codex review). Skipped
        for markdown/text root-cause mode: ``_to_markdown_root_cause`` already
        merged these into its own root-cause groups, so appending them again
        here would duplicate every scoped finding that correlates with an
        existing group (Codex review follow-up).
        """
        if self.report_mode == "root-cause" and fmt in ("markdown", "text"):
            return []
        scoped_only, missing_labels, blocks, _missing_kind = (
            self._scoped_gate_findings()
        )
        if not scoped_only and not missing_labels:
            return []
        severity_tag = "breaking" if blocks else "compatible"
        return [
            "## Additional scoped-gate findings",
            *(
                self._missing_label_line(label, severity_tag)
                for label in missing_labels
            ),
            *(self._scoped_only_line(c) for c in scoped_only),
        ]

    def into_text(self, text: str, fmt: str) -> str:
        """Append the scoped-gate sections to a markdown/text/review report.

        The report itself is left exactly as rendered; what precedes/follows
        it is a scoped-verdict header (only when the two verdicts disagree,
        prepended) and the per-consumer summaries plus the scoped gate's own
        findings (appended) -- both demangled under ``self.demangle`` the
        same way ``to_markdown``'s own ``_out()`` pass demangles everything
        rendered before this fold-in runs (ADR-061 Phase 2 item 5): missing
        symbol/entrypoint names and scoped-only change descriptions can be
        mangled C++ names, same as anything else in the report. *text*
        itself is never re-demangled here.
        """
        header_lines = self._scoped_verdict_header()
        footer_lines = [
            "",
            *self._used_by_lines(),
            *self._required_symbols_lines(),
            *self._scoped_gate_finding_lines(fmt),
        ]
        if self.demangle:
            from .demangle import demangle_text

            if header_lines:
                header_lines = demangle_text("\n".join(header_lines)).split("\n")
            footer_lines = demangle_text("\n".join(footer_lines)).split("\n")
        return "\n".join([*header_lines, text, *footer_lines])


#: The formats a ``--use-cases`` attribution actually reaches a reader
#: through. Two mechanisms, one set: the JSON paths emit ``use_case_impact``
#: straight off the ``DiffResult`` (``reporter._add_use_case_impact``), and
#: the three text-shaped formats get the section folded in by
#: :func:`_fold_use_case_impact_into_text` below. sarif/junit/html render
#: from the same attributed result and carry none of it. ``text`` is here
#: because the fold accepts it, not because ``compare -o`` offers it.
_USE_CASE_IMPACT_BEARING_FORMATS = frozenset({"json", "markdown", "text", "review"})


def format_carries_use_case_impact(fmt: str | None) -> bool:
    """Would a report rendered as *fmt* carry the use-case attribution?

    Asked once per rendered output, so ``compare``'s preflight can reject
    ``--use-cases`` on the real condition -- *no* output carries it -- rather
    than on the primary format alone. A ``-o html=... -o json=PATH``
    run renders the secondary from the same attributed result, at
    ``report_mode="full"``, so the attribution does reach the caller and
    rejecting it was arbitrary (Codex review); the primary's own error
    message had already been proposing that exact arrangement.

    Note the quantifier is the caller's, and it is the opposite of
    :func:`contract_coverage_exit.report_carries_the_ledger`'s: that one asks
    whether *every* output states the ledger, because it is deciding whether
    a stderr notice would be a redundant second copy. This one feeds an
    *any* -- one output carrying the block is enough for the run to be
    meaningful.

    No ``stat`` parameter (CLI cleanup phase two, PR 1): the internal
    one-line format (``service_render.ONELINE_FORMAT``) is simply not in
    :data:`_USE_CASE_IMPACT_BEARING_FORMATS`, so ``fmt in
    _USE_CASE_IMPACT_BEARING_FORMATS`` already answers correctly for it with
    no separate boolean needed.
    """
    if fmt is None:
        return False
    return fmt in _USE_CASE_IMPACT_BEARING_FORMATS


def _fold_use_case_impact_into_text(
    text: str,
    fmt: str,
    result: Any,
    show_only: str | None = None,
    *,
    demangle: bool = False,
) -> str:
    """Fold ``compare --use-cases``'s attribution into the rendered report.

    Markdown/text/review only. The JSON paths already carry the block --
    ``reporter._add_use_case_impact`` emits it straight off
    ``DiffResult.use_case_impact`` -- so folding it again here would write
    the key twice; the structured formats (sarif, junit, html) are left
    untouched, the same scope boundary
    :func:`_fold_suppression_audit_into_text` uses.

    *show_only* projects the attribution onto the findings the report above
    it actually lists, so the section cannot resurface a change the filter
    removed (Codex review) -- the same thing
    :func:`_fold_scoped_compat_into_text` does for its own scoped-only
    changes, and the same projection the JSON paths apply.

    *demangle*, when True, demangles this function's own appended section
    the same way ``reporter_markdown.to_markdown``'s single whole-report
    ``_out()`` pass demangles everything rendered *before* this fold-in runs
    (ADR-061 Phase 2 item 5) -- this section runs after that pass, on text
    it never saw, so without this it silently stayed mangled under
    ``--demangle``. Only the newly-appended lines are demangled, never
    *text* itself (already correctly demangled or not by the caller).
    """
    from .impact.use_case_impact import UseCaseImpact, render_use_case_impact_lines

    impact = getattr(result, "use_case_impact", None)
    if not isinstance(impact, UseCaseImpact) or fmt not in (
        "markdown",
        "text",
        "review",
    ):
        return text
    if show_only:
        from .reporter import apply_show_only
        from .root_cause_evidence import scoped_only_changes_filtered

        # Every argument the JSON paths pass, so a policy-sensitive token
        # ("breaking") filters identically in both renders rather than
        # against the default policy here. Scoped-only findings are appended
        # through the shared filter for the same reason the JSON paths' own
        # `_displayed_with_scoped_only` does: this fold runs alongside
        # `_fold_scoped_compat_into_text`, which puts them in the findings
        # list above, so projecting onto `result.changes` alone would drop
        # every attribution that landed on one.
        impact = impact.restricted_to(
            apply_show_only(
                list(result.changes),
                show_only,
                policy=result.policy,
                kind_sets=result._effective_kind_sets(),
                policy_file=result.policy_file,
            )
            + scoped_only_changes_filtered(result, show_only)
        )
    appended = "\n".join(render_use_case_impact_lines(impact))
    if demangle:
        from .demangle import demangle_text

        appended = demangle_text(appended)
    return "\n".join([text, appended])


def _fold_suppression_audit_into_text(
    text: str, fmt: str, audit: Any, *, demangle: bool = False
) -> str:
    """Fold a ``--audit-suppressions`` ``SuppressionAudit`` into a rendered
    markdown/text/review report.

    Markdown/text/review get an appended ``## Suppression Audit`` section.
    Binary/structured formats (sarif, junit, html) are left untouched -- the
    same scope boundary :func:`_fold_scoped_compat_into_text` already uses.

    ADR-061 Phase 2 item 5: JSON is no longer folded here -- ``result.
    suppression_audit`` (the same *audit* object this function receives) is
    now a real ``DiffResult`` field, attached before rendering, and
    ``reporter.to_json``'s JSON builders emit the ``suppression_audit`` key
    directly from it. This function's remaining job -- appending a section
    to already-rendered markdown/text/review text -- stays post-render
    because ``to_markdown``'s single whole-report ``_out()`` demangle pass
    already ran by the time this appends. *demangle*, when True, demangles
    the free-standing symbol prose this section adds (``audit.summary()``,
    a high-risk match's own ``{kind}: {symbol}`` tail) the same way
    ``_out()`` demangles everything rendered before this fold-in runs --
    but never a rule's own :func:`_suppression_rule_label` output (Codex
    review: two distinct selectors, e.g. Itanium C1/C2 constructor
    variants, can demangle to the identical display string, which would
    silently defeat that label's whole reason to exist -- disambiguating
    rules a bare selector alone cannot).
    """
    if audit is None:
        return text

    from .reporter_contract_blocks import (
        suppression_rule_label as _suppression_rule_label,
    )

    def _maybe_demangle(s: str) -> str:
        if not demangle:
            return s
        from .demangle import demangle_text

        return demangle_text(s)

    if fmt in ("markdown", "text", "review"):
        lines = ["", "## Suppression Audit", "", _maybe_demangle(audit.summary())]
        # audit.summary() only reports the stale-rule count (Codex review,
        # fresh evidence: its own per-rule detail lines named a rule by only
        # its first selector, misidentifying two rules that share it but
        # differ on another, e.g. the same symbol with a different
        # change_kind) -- render each stale rule explicitly with the fully
        # disambiguated label, the same as high-risk/expired/near-expiry
        # below.
        if audit.stale_rules:
            lines.append("")
            lines.append("Stale rules (matched nothing):")
            for i, rule in enumerate(audit.stale_rules):
                lines.append(f"- `{_suppression_rule_label(rule, i)}`")
        if audit.high_risk_matches:
            lines.append("")
            lines.append("High-risk matches (suppressed a BREAKING change):")
            for i, (rule, change) in enumerate(audit.high_risk_matches):
                lines.append(
                    f"- `{_suppression_rule_label(rule, i)}` suppressed "
                    f"{change.kind.value}: {_maybe_demangle(change.symbol)}"
                )
        # audit.summary() only reports counts for these two buckets (Codex
        # review, fresh evidence) -- the JSON branch above already names each
        # rule, so the default markdown/text/review report was the one place
        # a reader couldn't tell *which* rule needs action without switching
        # to -o json=....
        if audit.expired_rules:
            lines.append("")
            lines.append("Expired rules:")
            for i, rule in enumerate(audit.expired_rules):
                lines.append(f"- `{_suppression_rule_label(rule, i)}`")
        if audit.near_expiry_rules:
            lines.append("")
            lines.append("Rules expiring soon:")
            for i, rule in enumerate(audit.near_expiry_rules):
                lines.append(f"- `{_suppression_rule_label(rule, i)}`")
        return "\n".join([text, "\n".join(lines)])

    return text


# ADR-068 §3 #19/§4 (Phase 4 commit 2, Codex review): the abort-report
# renderers below moved here from cli_compare_helpers.py for the identical
# reason every other function in this module did -- that file sits at the
# AI-readiness gate's 2000-line hard cap (no allowlist mechanism there), so
# new lines must land in a sibling module, not grow it. Unlike this module's
# other functions (fold text into an already-computed report), these render
# a *whole* report for a run that produced no DiffResult at all (a
# comparability-gate refusal or a budget-overflow abort) -- distinct enough
# to warrant its own docstring note here rather than reading as scope creep
# on the "fold-in" name.


def _report_not_comparable(
    exc: ProfileMismatchError | ScopeMismatchError,
    old: AbiSnapshot,
    new: AbiSnapshot,
    *,
    fmt: str,
    output: Path | None,
    secondary_writes: tuple[tuple[str, Path], ...] = (),
) -> None:
    """Surface an ADR-050 D2 comparability-gate hard failure to the user.

    ``checker.compare``'s gate raises before any ``diff_*`` module runs, so
    there is no ``DiffResult`` for any renderer to work with — unlike an
    ordinary verdict, this cannot be formatted the way a completed comparison
    would be. ``-o json=...`` gets the schema-conformant ``{"verdict":
    null, "reason": {...}}`` document (schema 2.17,
    ``compare_report.schema.json``); ``sarif``/``junit`` get a real,
    spec-conformant document of their own (a failed-invocation SARIF run /
    an errored JUnit testcase — both formats have a genuine, standard way to
    represent "the run didn't complete", distinct from "zero findings") via
    :func:`sarif.to_sarif_not_comparable`/
    :func:`junit_report.to_junit_xml_not_comparable`, so CI tooling
    consuming those artifacts sees the failure instead of a missing file.
    ``markdown``/``html``/``review``/``text``/``oneline`` get the same clear
    stderr message a ``click.UsageError`` would produce, plus the small
    dedicated refusal document :func:`_report_run_aborted` renders for them
    (PR #1180) -- a human-facing target must not stay silently absent, or
    keep a stale prior-run document, on a refused run either.

    *secondary_writes* -- every additional ``-o FORMAT=DEST``/``--write``
    target the invocation requested, forwarded to :func:`_report_run_aborted`
    exactly as every other abort path through it already does
    (``cli_compare_helpers.run_compare``'s budget-overflow abort,
    ``compare_no_baseline``). Leaving it at the empty default -- this
    caller's behavior until now -- meant a run refused on comparability
    wrote only its PRIMARY output: ``--format review -o report.md --write
    json=report.json`` produced the human-facing refusal and no
    ``report.json`` at all, so a CI wrapper expecting that sidecar reported
    a generic "missing comparison report" error instead of the real
    operational outcome (``verdict: null`` plus the structured refusal
    reason the JSON renderer here already produces). Every requested output
    must describe the same outcome -- a refusal included -- not only a
    completed comparison.
    """
    kind = "profile_mismatch" if isinstance(exc, ProfileMismatchError) else "scope_mismatch"
    message = str(exc)
    click.echo(
        f"Error: '{old.library}' old={old.version!r} new={new.version!r} are not "
        f"comparable: {message}\n"
        "The two snapshots were not extracted under a comparable profile/scope "
        "contract (ADR-050 D1/D2), so no verdict was produced. Pass "
        '--diagnostic-comparison to force a tentative diff (stamped '
        'assurance: "none") if you understand the risk.',
        err=True,
    )
    from .report.not_comparable import OperationalStatus

    _report_run_aborted(
        kind, message, old.library, old.version, new.version,
        fmt=fmt, output=output, secondary_writes=secondary_writes,
        operational=OperationalStatus.NOT_COMPARABLE,
    )


def _report_run_aborted(
    kind: str,
    message: str,
    library: str,
    old_version: str,
    new_version: str,
    *,
    fmt: str,
    output: Path | None,
    operational: Any,
    secondary_writes: tuple[tuple[str, Path], ...] = (),
) -> None:
    """Render an aborted-run refusal report -- generalizes :func:`_report_not_comparable`.

    Takes plain strings, not an ``AbiSnapshot`` pair: unlike the
    comparability-gate refusal above, a budget-overflow abort (ADR-068 §3
    #19) can happen *before* either side ever resolves to a snapshot at
    all -- there is nothing to read ``.library``/``.version`` off yet, so
    every caller passes what it has (a resolved snapshot's real fields, or
    the raw operand paths' names when resolution itself never completed).

    ADR-068 §3 #19: the budget-overflow abort (``--budget``, exit 5) shares
    the identical shape ``_report_not_comparable`` already established for
    ADR-050's comparability refusal (exit 6) -- no ``DiffResult`` exists in
    either case, so the same schema-conformant JSON refusal document
    (``report/not_comparable.py``, already parameterized by
    :class:`~abicheck.policy.outcome.OperationalStatus`) and the same
    generic SARIF/JUnit "run did not complete" renderers apply verbatim,
    just with a different *kind*/*operational* pair. Split out so a second
    abort axis does not have to duplicate the format dispatch.

    *secondary_writes* (Codex review, fresh evidence, PR #1178): ``compare
    -o fmt=path`` is repeatable and, on a normal run, every one of them
    renders the same already-computed result -- an abort must do the same
    for every configured target, not just the primary ``fmt``/``output``,
    or a ``-o json=report.json`` consumer silently gets no file at all
    on exit 5 instead of the same structured refusal the primary format got.

    markdown/text/review/html (Codex review, fresh evidence, PR #1180,
    "Render aborts for every accepted output format") get a small dedicated
    refusal document too, via :func:`_render_run_aborted_text`/
    :func:`_render_run_aborted_html` -- an ``-o out.md``/``--write
    html=out.html`` target must not stay silently absent, or worse keep a
    stale prior-run document, on an aborted run.
    """
    refusal = (library, old_version, new_version, kind, message)

    def _render_one(target_fmt: str, target_output: Path | None) -> None:
        if target_fmt == "json":
            from .report.not_comparable import render_not_comparable_json
            from .schemas import REPORT_SCHEMA_VERSION

            _write_or_echo(
                target_output,
                render_not_comparable_json(
                    *refusal,
                    report_schema_version=REPORT_SCHEMA_VERSION,
                    operational=operational,
                ),
            )
        elif target_fmt == "sarif":
            from .report.render_json import render_mapping_as_json
            from .sarif import to_sarif_not_comparable

            _write_or_echo(
                target_output, render_mapping_as_json(to_sarif_not_comparable(*refusal))
            )
        elif target_fmt == "junit":
            from .junit_report import to_junit_xml_not_comparable

            xml = to_junit_xml_not_comparable(
                library, old_version, new_version, kind, message
            )
            _write_or_echo(target_output, xml)
        elif target_fmt in ("markdown", "text", "review"):
            # Codex review, PR #1180, fresh evidence ("Render aborts for
            # every accepted output format"): a `-o out.md`/`--write
            # markdown=out.md` target must not stay silently absent (or,
            # worse, keep a stale prior-run document) on an aborted run --
            # a scripted consumer that only checks the file's existence/
            # mtime would otherwise read a stale success report instead of
            # the abort.
            _write_or_echo(target_output, _render_run_aborted_text(*refusal))
        elif target_fmt == "html":
            _write_or_echo(target_output, _render_run_aborted_html(*refusal))
        elif target_fmt == _ONELINE_FORMAT:
            # Codex review, fresh evidence ("Render budget aborts in
            # oneline format"): -o oneline=... is a real, separate
            # primary format (service_render.ONELINE_FORMAT) the branch
            # above never matched -- same "must not stay silently absent"
            # reasoning, in oneline's own single-line shape.
            _write_or_echo(
                target_output,
                f"{library}: comparison aborted ({kind}): {message}",
            )

    _render_one(fmt, output)
    for secondary_fmt, secondary_output in secondary_writes:
        _render_one(secondary_fmt, secondary_output)


def _render_run_aborted_text(
    library: str, old_version: str, new_version: str, kind: str, message: str
) -> str:
    """A minimal human-readable refusal document for markdown/text/review.

    Deliberately not the full ``ReportDocument`` machinery those formats
    normally render through -- there is no ``DiffResult`` here to project,
    the same reason :func:`_report_run_aborted`'s JSON/SARIF/JUnit branches
    each build a dedicated, schema-conformant refusal document rather than
    reusing the ordinary renderer.
    """
    return (
        f"# {library}: comparison aborted\n\n"
        f"old={old_version!r} new={new_version!r}\n\n"
        f"**{kind}**: {message}\n\n"
        "No verdict was produced.\n"
    )


def _render_run_aborted_html(
    library: str, old_version: str, new_version: str, kind: str, message: str
) -> str:
    """The ``html`` sibling of :func:`_render_run_aborted_text`."""
    import html as _html

    return (
        "<!doctype html><html><body>"
        f"<h1>{_html.escape(library)}: comparison aborted</h1>"
        f"<p>old='{_html.escape(old_version)}' new='{_html.escape(new_version)}'</p>"
        f"<p><strong>{_html.escape(kind)}</strong>: {_html.escape(message)}</p>"
        "<p>No verdict was produced.</p>"
        "</body></html>"
    )


def _exit_on_budget_overflow(
    exc: Exception,
    budget: str | None,
    label: str,
    library: str,
    old_version: str,
    new_version: str,
    *,
    fmt: str,
    output: Path | None,
    secondary_writes: tuple[tuple[str, Path], ...] = (),
) -> None:
    """Render ADR-068 §3 #19's budget-overflow abort (exit 5) and exit.

    Shared by every ``deadline.DeadlineExceeded`` catch site in
    ``cli_compare_helpers.run_compare`` (Codex review: a bare ``sys.exit(5)``
    bypassed report rendering entirely, so ``-o json=...``/``-o``/
    ``-o`` silently produced nothing on overflow -- the same generic
    aborted-run document :func:`_report_run_aborted` already gives the
    comparability-gate refusal). *label* is just the stderr message's own
    naming of what was being compared (raw operand paths before resolution,
    or the resolved library name after); *library*/*old_version*/
    *new_version* feed the structured report the same way. *secondary_writes*
    (Codex review, fresh evidence, PR #1178) is ``-o``'s own repeatable
    fmt/path pairs, forwarded so an abort renders to every configured target,
    not just the primary one.
    """
    click.echo(
        f"Error: --budget {budget!r} exceeded while comparing {label}: {exc}. "
        "Pin a shallower --depth or raise the budget; a budget never "
        "silently narrows evidence.",
        err=True,
    )
    from .report.not_comparable import OperationalStatus

    _report_run_aborted(
        "budget_overflow", str(exc), library, old_version, new_version,
        fmt=fmt, output=output, operational=OperationalStatus.BUDGET_OVERFLOW,
        secondary_writes=secondary_writes,
    )
    sys.exit(5)
