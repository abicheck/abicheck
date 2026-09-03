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

from dataclasses import dataclass
from typing import Any

from .report.scoped_gate import (
    _SEVERITY_TO_SUMMARY_BUCKET as _SEVERITY_TO_SUMMARY_BUCKET,
)
from .service_render import ONELINE_FORMAT


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
    always-unfiltered, e.g. the ``--write`` render, which ignores the
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
    if fmt == ONELINE_FORMAT:
        # The built-in `quick` --profile's output. Unlike markdown/text/
        # review's into_text (which appends a scoped section after the
        # existing report), the incoming `text` here is the *unscoped*
        # one-liner and must be replaced outright, not appended to --
        # appending would break the one-line contract --profile quick
        # exists to guarantee, and leaving it as the leading line would
        # print the wrong (full-library) verdict/counts next to a process
        # exit code computed from the scoped result (Codex review, fresh
        # evidence). Imported rather than duplicated as a literal --
        # cli_compare_helpers.py already imports the same constant, so the
        # leaf-module-independence argument contract_coverage_exit.py's own
        # `_ONELINE_FORMAT` duplicate makes doesn't hold here (CodeRabbit
        # review).
        return fold.into_oneline()
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

    # ── One-line (service_render.ONELINE_FORMAT, the built-in `quick`
    #    --profile's output) ──────────────────────────────────────────────
    #
    # JSON's own fold used to live here too, as `into_json` +
    # `_swap_in_scoped_severity`/`_swap_in_scoped_run_outcome`/
    # `_fold_findings_into_changes`/`_fold_findings_into_stat_summary` --
    # see :mod:`abicheck.report.scoped_gate` (ADR-061 Phase 2 item 5),
    # which now applies the identical fold natively, before rendering,
    # inside `reporter.to_json`/`to_stat_json`.

    def into_oneline(self) -> str:
        """The one-line summary, but for the *scoped* gate.

        CLI cleanup phase two, PR 1 (Codex review, fresh evidence): before
        this method existed, `--profile quick` combined with `--used-by`/
        `--required-symbol` fell through `_fold_scoped_compat_into_text`'s
        dispatch untouched (only json/markdown/text/review were handled),
        so the printed one-liner showed the full-library verdict/counts even
        though the process actually exits on the *scoped* result -- e.g.
        removing an irrelevant symbol while breaking a required one could
        print "BREAKING: 1 breaking" while exiting 0, or the reverse.

        The verdict/exit-code halves reuse `to_stat_json` itself, called with
        this fold's own `show_only`/`contract_evaluation` -- the exact same
        scoped-verdict-swap and severity-recompute logic the JSON path
        already uses (`report.scoped_gate.apply_scoped_gate`, folded in
        natively before `to_stat_json` ever returns) and this module's own
        tests already cover. The *counts* are deliberately NOT read from
        that payload's `summary`, though (Codex review, fresh evidence,
        second round): `apply_scoped_gate`'s own
        `_fold_findings_into_stat_summary` ADDS scoped-only contributions on
        top of the full-library counts rather than replacing them -- correct
        for JSON, which also carries `changes`/`full_summary` so a reader
        can see *why* an unrelated full-library finding is still counted
        despite a COMPATIBLE scoped verdict. The one-line format has no such
        context: a bare "COMPATIBLE: 1 breaking" with nothing explaining the
        1 reads as a genuine contradiction, not a nuance. So the counts here
        are recomputed from scratch out of *only* the findings that actually
        decide the scoped verdict/exit code: `_scoped_gate_findings()`'s
        `scoped_only` changes and `missing_labels`, mirroring
        `_fold_findings_into_stat_summary`'s own per-finding severity-bucket
        tally -- PLUS (Codex review, fresh evidence, third round) every
        entry of `result.changes` itself that the scoped gate also counts.
        `scoped_only`/`missing_labels` cover only the *synthesized*
        scoped-relevant findings (e.g. a missing required symbol, or a
        `PE_ORDINAL_RETARGETED` with no backing full-library `Change`) --
        the ordinary case, a real full-library finding that is ALSO
        scoped-relevant (e.g. removing a function `--required-symbol`
        itself names), lives in `result.changes` and is marked relevant via
        `result.scoped_relevant_finding_ids` (the same set
        `sarif.py`/`junit_report.py`'s own scoped-gate folds already read).
        Omitting it reproduced exactly the bug the earlier revision of this
        fix was written to close, just for the far more common shape of
        input: an ordinary in-scope removal exiting 4 while printing
        "no changes (0 total)".
        """
        import json

        from .checker_policy import EvidenceStatus
        from .report.render_text import format_stat_line
        from .reporter import _change_to_dict, _finding_id, to_stat_json
        from .reporter_markdown import _VERDICT_LABEL

        folded = json.loads(
            to_stat_json(
                self.result,
                severity_config=self.severity_config,
                show_only=self.show_only,
                contract_evaluation=self.contract_evaluation,
            )
        )
        verdict_value = folded.get("verdict") or _VERDICT_LABEL[self.result.verdict]
        gate_note = ""
        severity_block = folded.get("severity")
        if isinstance(severity_block, dict):
            # Read back the already-scoped-swapped block
            # `report.scoped_gate._swap_in_scoped_severity` produced (when
            # the run's exit-code scheme is severity-aware) instead of
            # recomputing from `self.result.changes` (full-library) here --
            # recomputing would reproduce, for this one-line path, exactly
            # the full-vs-scoped severity mismatch that function exists to
            # fix for the JSON path.
            exit_code = severity_block.get("exit_code", 0)
            gate_note = (
                f" [gate: FAIL (exit {exit_code})]" if exit_code else " [gate: PASS]"
            )

        # Deliberately unfiltered by self.show_only (Codex review, fresh
        # evidence): self._scoped_gate_findings() applies --show-only to
        # scoped_only/missing_labels for *display* purposes (so markdown/
        # json don't re-surface an excluded finding), but this method's own
        # counts must track what actually decided the scoped verdict/exit
        # code, which --show-only never changes -- `blocks` above (and the
        # exit code read from `severity_block`) are already computed from
        # the unfiltered set. Filtering scoped_only/missing_labels here but
        # not relevant_in_changes below let a --show-only run print "no
        # changes (0 total)" while still exiting non-zero on a synthesized
        # scoped-only break (e.g. PE_ORDINAL_RETARGETED) that show_only
        # happened to exclude from the display list.
        from .reporter import _resolve_scoped_gate_findings

        scoped_only, missing_labels, blocks, _missing_kind = (
            _resolve_scoped_gate_findings(self.result, self.severity_config, None)
        )
        relevant_ids = (
            getattr(self.result, "scoped_relevant_finding_ids", None) or frozenset()
        )
        relevant_in_changes = [
            c for c in self.result.changes if _finding_id(c) in relevant_ids
        ]
        counts = {
            "breaking": 0,
            "source_breaks": 0,
            "risk_changes": 0,
            "compatible_additions": 0,
        }
        eff_sets = self.result._effective_kind_sets()

        def _tally(c: Any, *, consumer_proven: bool) -> None:
            entry = _change_to_dict(
                c,
                policy=self.result.policy or "strict_abi",
                kind_sets=eff_sets,
                policy_file=self.result.policy_file,
                severity_config=self.severity_config,
                evidence_status_override=(
                    EvidenceStatus.CONSUMER_PROVEN if consumer_proven else None
                ),
            )
            severity = entry.get("severity")
            bucket = (
                _SEVERITY_TO_SUMMARY_BUCKET.get(severity)
                if isinstance(severity, str)
                else None
            )
            if bucket:
                counts[bucket] += 1

        for c in relevant_in_changes:
            # A real full-library finding, not synthesized -- its own
            # already-computed severity applies, same as every other
            # `result.changes` entry.
            _tally(c, consumer_proven=False)
        for c in scoped_only:
            _tally(c, consumer_proven=True)
        for _label in missing_labels:
            bucket = _SEVERITY_TO_SUMMARY_BUCKET["breaking" if blocks else "compatible"]
            counts[bucket] += 1
        return format_stat_line(
            str(verdict_value),
            breaking=counts["breaking"],
            source_breaks=counts["source_breaks"],
            risk_count=counts["risk_changes"],
            compatible_additions=counts["compatible_additions"],
            total_changes=sum(counts.values()),
            redundant_count=0,
            gate_note=gate_note,
        )

    # ── markdown / text / review ───────────────────────────

    def _scoped_verdict_header(self) -> list[str]:
        """The "scoped verdict disagrees with the headline" note, or ``[]``.

        The exit code is computed from the *scoped* result (ADR-043
        worst-wins), which can disagree with the full-library verdict this
        report's own headline already rendered above -- state which one is
        authoritative for CI instead of leaving the two to silently disagree
        (Codex review). Under a severity scheme the exit code is NOT a fixed
        BREAKING->4/API_BREAK->2 mapping of scoped_verdict -- e.g.
        ``--severity-preset info-only`` can floor it at 0 even for a BREAKING
        scoped verdict -- so state the actual computed value/scheme instead of
        asserting the exit code "reflects" the scoped verdict, which would be
        false in that case (Codex review follow-up).
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
        scoped_exit_code = getattr(self.result, "scoped_exit_code", None)
        scoped_exit_code_scheme = getattr(self.result, "scoped_exit_code_scheme", None)
        exit_note = (
            f"the CLI process exits {scoped_exit_code} under the "
            f"{scoped_exit_code_scheme} exit-code scheme for this run"
            if scoped_exit_code is not None
            else "this is what the exit code reflects"
        )
        return [
            f"**Scoped verdict: {scoped_verdict_value}** "
            f"({exit_note}; the full library verdict above is "
            f"{full_verdict_value}).",
            "",
        ]

    def _used_by_lines(self) -> list[str]:
        """Per-application ``--used-by`` summaries, naming the missing symbols.

        Names the actual missing symbols/versions, not just their count (Codex
        review) -- a human reading the default text report otherwise has no way
        to tell *which* symbol broke this app without re-running with
        ``--format json``.
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
#: because the fold accepts it, not because ``compare --format`` offers it.
_USE_CASE_IMPACT_BEARING_FORMATS = frozenset({"json", "markdown", "text", "review"})


def format_carries_use_case_impact(fmt: str | None) -> bool:
    """Would a report rendered as *fmt* carry the use-case attribution?

    Asked once per rendered output, so ``compare``'s preflight can reject
    ``--use-cases`` on the real condition -- *no* output carries it -- rather
    than on the primary format alone. A ``--format html --write json=PATH``
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
        # to --format json.
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
