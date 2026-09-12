#!/usr/bin/env python3
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
"""The composite Action's JSON-report reader -- one schema-aware
implementation, called from ``action/run.sh``'s ``_report_query``.

**Why this is a file and not a heredoc.** Every query below used to live as
~300 lines of Python embedded in ``run.sh``. Embedded, it was unreachable
from ``pytest``/``mypy``/``ruff``, so each query's contract could only be
exercised through a full bash-level Action run -- which is why the
``# cli-mirror:`` comments scattered through ``run.sh`` drifted far enough
to cite modules that no longer exist (``scan_engine``, ``cli_scan.py``,
both deleted with ADR-068's retirement of ``scan``). A first-party file
under ``action/`` (a ``FIRST_PARTY_PY_ROOTS`` tree) gets the repo's own
gates applied to it, and the pure functions here are directly testable
against adversarially-generated documents.

**Why it lives in ``action/`` rather than under ``abicheck/``.** This
reader must version with the *Action*, not with the installed package. A
workflow may pin ``abicheck-version:`` to any released version while using
a newer Action (or the reverse), so importing the reader out of
site-packages would make the boundary's behavior depend on a version the
Action does not control. ``run.sh`` invokes this by path under
``$ACTION_PATH``, the same way ``actions/check-target/run.sh`` already
invokes its own ``report_envelope.py``.

**Answer contract (exit codes).** ``run.sh``'s callers depend on these:

* ``0`` -- the query is answered; the answer is on stdout (possibly empty,
  when empty *is* the answer). ``report_validity`` always takes this path,
  whatever it finds.
* ``1`` -- "cannot tell": the field is absent, or this report shape does
  not carry the axis at all. Every caller treats this as "no signal" and
  falls back to its own derivation. **Unchanged from the heredoc**, on
  purpose: an unreadable report answering "not gated" here is what ADR-063
  Track T8 deliberately chose over reconstructing the axis from the CLI's
  own stderr prose, which a PR-controlled build script was shown able to
  forge. The fix for the truthfulness gap that leaves is not to make this
  fail open into a *failure* claim -- it is ``report_validity`` below,
  which lets ``run.sh`` establish separately that a report it required was
  never readable in the first place.
* ``2`` -- unknown query name (a bug in ``run.sh``, never in user input:
  queries are named, never passed as expressions, so a caller cannot
  inject one).

**One query was dropped in the extraction**, not carried over:
``assurance_status``. ``run.sh`` asks for no such query -- the heredoc's own
comment already described it as retired, superseded by
``assurance_contribution`` (which answers the axis directly rather than
requiring the caller to interpret a status string). Carrying dead surface into
a new module is how two ways to ask one question start; the executable check
that it stays dead is ``tests/test_action_report_query.py``'s
``test_every_query_run_sh_asks_is_answered``, which fails on a query in either
direction of the mismatch.

**Two report shapes.** ``compare`` writes its gate and ledgers at the
document root; several nest the comparison summary under ``diff``. Every
query consults both, via :func:`_either`.
"""

from __future__ import annotations

import json
import sys
from typing import Any

#: :func:`classify_document`'s answers, and the vocabulary
#: ``run.sh``'s ``_report_validity`` branches on. Exactly one describes any
#: given path, and only ``VALIDITY_OK`` means "a document worth reading".
VALIDITY_OK = "ok"
#: No file at that path, or a zero-byte one. The ordinary shape of "the run
#: died before writing a report".
VALIDITY_ABSENT = "absent"
#: The path exists but could not be read (permissions, a directory, a
#: decoding error).
VALIDITY_UNREADABLE = "unreadable"
#: Read fine, but is not JSON.
VALIDITY_UNPARSEABLE = "unparseable"
#: Valid JSON that is not an object -- a list, a bare string, ``null``.
VALIDITY_NOT_OBJECT = "not_object"
#: An empty object. Called out separately from ``not_object`` because it is
#: a *known* artifact rather than a corruption: a PR-comment re-run can
#: leave ``{}`` in ``PR_JSON`` when the primary run wrote no report, and a
#: macOS CI lane really did observe the final gate consulting one and
#: dropping an axis the dispatch had already announced (see
#: ``scope_contribution``'s own comment in this file).
VALIDITY_EMPTY = "empty"
#: A non-empty JSON object that carries no abicheck result at all --
#: ``{"error": "write interrupted"}``, or a lone ``report_schema_version``.
#: The generalization of ``empty``: "the document parsed" and "the document
#: holds a result" are different questions, and treating a non-empty mapping
#: as a result meant every verdict reader answered empty and the caller fell
#: back to a clean claim -- the same defect ``empty`` was added for, one step
#: up (Codex review, P2).
VALIDITY_NO_RESULT = "no_result"

#: What a *verdict* can actually be read from -- the real question validity
#: has to answer, replacing an earlier presence-only key set.
#:
#: That earlier version accepted any document carrying one of a broad list of
#: keys, on the reasoning that "extra recognizers can only ever admit a
#: document -- they cannot mask a missing result". That reasoning was wrong,
#: and Codex review caught it: admitting a document is *precisely* what masks a
#: missing result, because admission is what licenses the COMPATIBLE
#: fallthrough in `run.sh`. ``{"findings": null}`` and ``{"no_baseline": true}``
#: both passed and published a clean verdict.
#:
#: So each rule below names a verdict source and requires it to be *readable*,
#: not merely present. The four shapes every emitter actually produces are
#: covered, and three of them legitimately carry ``verdict: null`` -- which is
#: why "non-empty string verdict" alone would fail working runs:
#:
#: * a two-sided ``compare`` report: ``verdict`` is a string from
#:   ``KNOWN_VERDICTS`` below (``reporter.py``), as is a release envelope's
#:   own sentinel. Membership, not non-emptiness: an arbitrary string is a
#:   document that parsed and still says nothing any caller can act on;
#: * a not-comparable report: ``verdict`` is ``null`` beside a ``reason``
#:   object (``report/not_comparable.py``);
#: * an audit-only report: ``verdict`` is ``null``, and the audit's own result
#:   is its ``findings`` **and** ``suppressed_findings`` arrays --
#:   ``report/no_baseline.py`` always emits both as lists, and **both** are
#:   required here rather than either (Codex review, P2). ``no_baseline_audit``
#:   reads an absent ``suppressed_findings`` as "nothing was suppressed", so
#:   accepting a document that omits it lets a run whose policy hid every
#:   finding publish ``AUDIT_CLEAN``. Absence cannot establish that policy hid
#:   nothing -- ADR-067's "record before disposing" applied to the reader;
#:
#: A ``libraries`` array is deliberately **not** a rule of its own. It was one,
#: and it was wrong in the way that matters here: no reader extracts a verdict
#: from ``libraries`` (neither ``compat_verdict`` nor ``run.sh``'s
#: ``_report_compat_verdict`` looks at it), so admitting a library-only document
#: licensed the COMPATIBLE fallthrough for a release whose members said
#: ``BREAKING`` -- and ``{"libraries": []}`` was admitted too (Codex review,
#: P2). Nor was a rollup the fix: ``_format_release_json`` emits a top-level
#: ``"verdict": worst_verdict`` -- already rolled up -- alongside
#: ``run_outcome`` on *every* release document, so a ``libraries`` array with no
#: readable verdict beside it is not a shape any emitter produces. The first two
#: rules already cover every real release envelope.
#:
#: ``run_outcome.compatibility`` is accepted as a verdict source in its own
#: right (ADR-063 D6 makes it the canonical one, and `_report_compat_verdict`
#: prefers it), so a document carrying that and no legacy ``verdict`` still
#: reads.


#: Every string a real emitter puts in a ``verdict`` slot, or in
#: ``run_outcome.compatibility``. Derived from the producing code, not invented
#: here:
#:
#: * ``abicheck.checker.Verdict`` -- the five compatibility tiers, used for
#:   both keys (ADR-063 D6 makes ``run_outcome.compatibility`` the canonical
#:   one, and it carries the same enum);
#: * ``cli_compare_release_helpers._RELEASE_VERDICT_ORDER`` plus
#:   ``workflows/release_scope.py`` -- a release envelope's per-library rollup
#:   adds ``ERROR``, ``not_comparable``, ``unsupported`` and ``failed``;
#: * ``reporter.py``'s appcompat document -- ``UNKNOWN`` when no verdict was
#:   computed.
#:
#: Checked rather than "any non-empty string", because the latter admitted
#: ``{"verdict": "write interrupted"}``: `_carries_a_result` called it a
#: readable result, `compat_verdict` returned the unknown value, and
#: `_resolve_clean_exit_verdict` -- which recognizes only the break and risk
#: tiers -- kept its initial COMPATIBLE (Codex review, P2). An unrecognized
#: verdict is exactly the "parsed cleanly, says nothing this can act on" case
#: ``no_result`` exists for.
#:
#: A *new* enum member must land here, not silently read as unusable, so
#: `tests/test_action_report_query.py` pins this set against the real enums.
KNOWN_VERDICTS = frozenset(
    {
        "NO_CHANGE",
        "COMPATIBLE",
        "COMPATIBLE_WITH_RISK",
        "API_BREAK",
        "BREAKING",
        "ERROR",
        "UNKNOWN",
        "not_comparable",
        "unsupported",
        "failed",
    }
)


#: The subset of :data:`KNOWN_VERDICTS` that is **not** a compatibility result.
#:
#: Each is a real emitter value, which is why it must stay in
#: ``KNOWN_VERDICTS`` -- a release document reporting one is a valid document
#: and must not read as ``no_result``. But none of them answers "is the new
#: version compatible": they report a crash, a comparability refusal
#: (ADR-050 D2), an artifact this build cannot analyze, or a member whose
#: capture failed.
#:
#: The engine draws the same line for the same reason, which is what this set
#: mirrors rather than invents: ``cli_compare_release_helpers.
#: _release_completed_compatibility_verdict`` excludes exactly these four from
#: ``run_outcome.compatibility`` because that axis is separate from the
#: release's own rolled-up ``verdict``, where an operational failure
#: deliberately dominates. ``UNKNOWN`` joins them from a different emitter --
#: ``reporter.py``'s appcompat document, where it means "no verdict was
#: computed".
#:
#: Why it matters here: ``_resolve_clean_exit_verdict`` acts on the break and
#: risk tiers and otherwise keeps its initial ``COMPATIBLE``, so admitting
#: these into the vocabulary without naming them made `{"verdict": "ERROR"}`
#: at exit 0 publish a clean compatibility claim (Codex review, P2, which
#: reproduced `ERROR COMPATIBLE 0`).
OPERATIONAL_VERDICTS = frozenset(
    {"ERROR", "not_comparable", "unsupported", "failed", "UNKNOWN"}
)


def _is_known_verdict(value: object) -> bool:
    return isinstance(value, str) and value in KNOWN_VERDICTS


def _carries_a_result(document: dict[str, Any]) -> bool:
    """Whether a verdict can actually be read out of *document*.

    See the commentary above for each rule's emitter and for why presence
    alone is not enough.
    """
    for source in (document, _nested(document)):
        if _is_known_verdict(source.get("verdict")):
            return True
        outcome = source.get("run_outcome")
        if isinstance(outcome, dict) and _is_known_verdict(
            outcome.get("compatibility")
        ):
            return True
    # The two shapes whose `verdict` is null by design. Checked at the root
    # only: each is a whole-document shape, not something nested under `diff`.
    #
    # `verdict is None` is required, not merely present: without it these
    # structural rules re-admitted exactly what the vocabulary check above
    # rejects -- `{"verdict": "write interrupted", "reason": {}}` bypassed it on
    # the strength of the `reason` object alone, `_report_compat_verdict`
    # returned the unusable string, and the COMPATIBLE fallthrough stood
    # (Codex review, P2). Both emitters write a literal `null` there, so this is
    # faithful to them rather than stricter.
    if document.get("verdict") is None and isinstance(document.get("reason"), dict):
        return True
    return (
        document.get("verdict") is None
        and document.get("no_baseline") is True
        and all(
            isinstance(document.get(key), list)
            for key in ("findings", "suppressed_findings")
        )
    )


#: Per *version sequence*, the first version whose documents owe an
#: assurance contribution alongside an assurance block.
#:
#: Three independent sequences reach this reader and they must never be
#: compared against each other's thresholds (Codex review, P2, twice):
#:
#: * a two-sided ``compare`` document carries ``report_schema_version`` and
#:   first emitted ``analysis_assurance_exit_contribution`` at 2.40
#:   (``abicheck/schemas/__init__.py``);
#: * an audit-only (``--no-baseline``) document carries
#:   ``audit_report_schema_version`` on its own 1.x sequence
#:   (``abicheck/report/no_baseline_document.py``) and has carried
#:   ``exit_axes.analysis_assurance`` since its first released version, 1.1 --
#:   1.0 was never released;
#: * a directory/package release document carries ``release_schema_version``
#:   (``abicheck/schemas/release_schema.py``), whose 1.3 landed ADR-071's
#:   paired top-level ``analysis_assurance`` block and
#:   ``analysis_assurance_exit_contribution``.
#:
#: Both non-compare sequences are numerically below ``(2, 40)`` at every real
#: version, so measuring either against the compare threshold makes those
#: documents look older than a field they carry -- and a lost contribution then
#: reads as "legacy, accept". The audit sequence was missed first and the
#: release sequence second; a new report shape with its own version key needs
#: an entry here, not a fallback.
ASSURANCE_CONTRIBUTION_SINCE = {
    "report_schema_version": (2, 40),
    "audit_report_schema_version": (1, 1),
    "release_schema_version": (1, 3),
}


class CannotTell(Exception):
    """The query has no answer in this document (exit 1)."""


class UnknownQuery(Exception):
    """No such query name (exit 2)."""


def parse_schema_version(raw: object) -> tuple[int, ...] | None:
    """``"2.40"`` -> ``(2, 40)``; ``None`` for anything unusable.

    Dotted-integer only. The version history runs one continuous sequence
    through 2.x, 3.x and 4.x, so a plain tuple comparison orders it
    correctly (``(2, 40) < (4, 4)``) -- which a float or string comparison
    would not (``"2.40" > "4.4"`` lexicographically, and ``2.40 > 2.4``
    numerically reverses the real order of 2.4 and 2.40).
    """
    if not isinstance(raw, str):
        return None
    parts = raw.split(".")
    if not all(part.isdigit() for part in parts) or not parts:
        return None
    return tuple(int(part) for part in parts)


def classify_document(path: str) -> tuple[str, dict[str, Any] | None]:
    """Read *path*, returning ``(validity_token, document_or_None)``.

    The document is returned alongside :data:`VALIDITY_OK` and, deliberately,
    alongside :data:`VALIDITY_EMPTY` and :data:`VALIDITY_NO_RESULT` -- see the
    comments on those two branches for why. Every other token returns ``None``,
    so a caller cannot read an absent or corrupt report as a real one, and
    :func:`main` hands any non-``None`` mapping to :func:`answer`.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except FileNotFoundError:
        return VALIDITY_ABSENT, None
    except OSError:
        return VALIDITY_UNREADABLE, None
    except UnicodeDecodeError:
        return VALIDITY_UNREADABLE, None
    if not text.strip():
        return VALIDITY_ABSENT, None
    try:
        document = json.loads(text)
    except ValueError:
        return VALIDITY_UNPARSEABLE, None
    if not isinstance(document, dict):
        return VALIDITY_NOT_OBJECT, None
    if not document:
        # Returned *with* the document, unlike every other non-OK token: an
        # empty object is a legal mapping, and every axis query below
        # already answers it correctly (an absent key is an absent key).
        # Withholding it here would change what those queries answer for a
        # `{}` report -- `severity_exit` in particular would stop answering
        # `0` and send the exit-1 dispatch down a different branch -- and
        # this refactor changes no axis answer. The emptiness is reported
        # through `report_validity` alone, which is what `run.sh` uses to
        # refuse to publish a verdict it cannot support.
        return VALIDITY_EMPTY, document
    if not _carries_a_result(document):
        # Same treatment, and for the same reason: the document is returned so
        # every axis query answers exactly what it answered before, and the
        # "no result" fact travels through `report_validity` alone.
        return VALIDITY_NO_RESULT, document
    return VALIDITY_OK, document


def _nested(report: dict[str, Any]) -> dict[str, Any]:
    value = report.get("diff")
    return value if isinstance(value, dict) else {}


def _assurance_contribution(report: dict[str, Any]) -> Any | None:
    """The analysis-assurance axis's own ``0``/``1`` fold, or ``None``.

    ``checker.compare``/``cli_compare_helpers`` compute this as 0 unless
    the *resolved* ``assurance.require_complete`` (CLI flag or config-file
    value alike, since the dedicated Action input's retirement) was true
    AND the run's own evidence was incomplete -- so no separate "was
    gating requested" signal is needed alongside it.

    Three shapes carry it, checked in order. The two-sided ``compare``
    document holds it at the root; an ``--against``-style document nests it
    under ``diff``; and an audit-only (``--no-baseline``) document
    (``report/no_baseline.py``) carries it under ``exit_axes`` instead,
    having no ``verdict``/gate namespace of its own to hang a top-level key
    from. Without that third lookup an audit-only run with
    ``assurance.require_complete: true`` read a missing key, answered "not
    gated", and the Action reported a plain ERROR instead of
    ANALYSIS_INCOMPLETE even though the CLI itself correctly exited 1.

    ``is None`` checks throughout rather than a zero-collapsing default, so
    a real ``0`` from an earlier shape short-circuits instead of falling
    through to a later one.
    """
    value = report.get("analysis_assurance_exit_contribution")
    if value is None:
        value = _nested(report).get("analysis_assurance_exit_contribution")
    if value is None:
        exit_axes = report.get("exit_axes")
        if isinstance(exit_axes, dict):
            value = exit_axes.get("analysis_assurance")
    return value


def _contribution_gates(value: object) -> bool:
    """Whether *value* is the axis's gating ``1``.

    ``str(value) == "1"`` and not ``value == 1``, deliberately. The field is
    specified as the integer 0 or 1, but a malformed report can hold
    anything, and this boundary has exactly one established answer for those
    cases: the heredoc printed the raw value and ``run.sh`` string-compared
    it against ``"1"``. Python's ``==`` disagrees with that comparison in
    both directions (``True == 1`` is true where ``"True" == "1"`` is not;
    ``"1" == 1`` is false where the strings match), so normalizing the value
    here would silently move the gate for malformed input -- in the
    *permissive* direction for ``True``. Stating the rule as the string
    comparison keeps one implementation shared by the query and
    :func:`assurance_axis` without changing the predicate.
    """
    return str(value) == "1"


def assurance_axis(report: dict[str, Any]) -> str:
    """Whether the analysis-assurance axis gated this run, *and how well we
    know it* -- the schema-aware half of the P0 truthfulness fix.

    ``_assurance_contribution`` alone cannot distinguish an older report
    that predates the field (2.40) from a current one that should carry it
    and does not. Collapsing both onto "not gated" is what let "I cannot
    establish that assurance failed" read as "the required assurance
    succeeded". Four answers, each a distinct fact:

    * ``gated`` -- contribution is 1. The axis fired.
    * ``not_gated`` -- contribution is 0. It did not, and the report says so.
    * ``absent_legacy_schema`` -- no contribution key, and nothing in the
      document establishes that one was owed. Honestly unknown; a caller may
      accept it, but must not call it success.
    * ``contradictory`` -- the document shows assurance *was* evaluated (see
      :func:`_assurance_was_evaluated` for the three placements) on a schema
      at or past its own sequence's threshold, and carries no contribution.
      The report is internally inconsistent, which is an invalid *result*,
      not a passing assurance check.

    **The contribution is not owed unconditionally**, and getting this wrong
    fails green runs rather than red ones. ``reporter.py`` emits
    ``analysis_assurance`` and ``analysis_assurance_exit_contribution`` under
    one ``if`` -- the block is attached only when the result carries a real
    ``AnalysisAssurance`` -- so a current-schema report legitimately omits
    *both*. An earlier draft of this function inferred the contradiction from
    the schema version alone, which flagged every such report; the test that
    pins an ordinary clean 4.4 report as acceptable is what caught it. The
    contradiction is the *pair* being broken, never a lone absence.
    """
    contribution = _assurance_contribution(report)
    if contribution is not None:
        return "gated" if _contribution_gates(contribution) else "not_gated"
    if not _assurance_was_evaluated(report):
        # No assurance was evaluated at all, so no contribution was owed.
        return "absent_legacy_schema"
    for key, threshold in ASSURANCE_CONTRIBUTION_SINCE.items():
        version = parse_schema_version(report.get(key))
        if version is not None:
            # Each key against its OWN sequence's threshold; see
            # `ASSURANCE_CONTRIBUTION_SINCE` for why mixing them is a bug.
            if version >= threshold:
                return "contradictory"
            return "absent_legacy_schema"
    return "absent_legacy_schema"


def _assurance_was_evaluated(report: dict[str, Any]) -> bool:
    """Whether this run evaluated analysis assurance at all.

    Three placements, because the shapes differ and a check that knew only
    the first let an audit report's lost contribution read as legacy:

    * a two-sided ``compare`` document's top-level ``analysis_assurance``
      block (``reporter.py``),
    * the same block under ``diff``, for the nested shape every other query
      reaches through ``_either``,
    * ``run_outcome.assurance`` -- the placement an audit-only document uses
      (``report/no_baseline.py`` nests the rollup there and puts the
      contribution under ``exit_axes`` instead), and which a ``compare``
      report also carries.

    ``run_outcome.assurance`` is ``None`` for any writer with no
    ``AnalysisAssurance`` rollup of its own, so a non-``None`` mapping there
    is the same positive signal the standalone block is.
    """
    for source in (report, _nested(report)):
        if isinstance(source.get("analysis_assurance"), dict):
            return True
        outcome = source.get("run_outcome")
        if isinstance(outcome, dict) and isinstance(outcome.get("assurance"), dict):
            return True
    return False


def answer(report: dict[str, Any], query: str, arg: str = "") -> str | None:
    """Answer *query* against an already-validated report document.

    Returns the answer as a string, or ``None`` for "answered, with no
    output at all" -- distinct from ``""``, which :func:`main` prints as a
    bare newline. Only the ``annotations`` query uses ``None``, for a report
    with no emittable entries: the heredoc looped over zero entries and
    printed nothing, and a spurious blank line from this boundary lands in
    the Action log as a stray workflow-command-shaped line.

    Raises :class:`CannotTell` for "no answer in this document" and
    :class:`UnknownQuery` for an unrecognized name.
    """
    nested = _nested(report)

    def _either(key: str, default: Any) -> Any:
        """The value from the compare shape, else the nested shape, else *default*."""
        value = report.get(key)
        if value is None:
            value = nested.get(key, default)
        return default if value is None else value

    def _severity() -> dict[str, Any]:
        # Compare keeps its gate at the document root; a severity-scheme
        # nested shape puts it under `diff`, exactly as it nests the
        # coverage ledger `_either` already reaches for. Root first so a
        # compare report is unaffected.
        block = report.get("severity")
        if not isinstance(block, dict):
            block = nested.get("severity")
        return block if isinstance(block, dict) else {}

    if query == "report_validity":
        # A document that reached here is loadable and non-empty by
        # construction -- `main` answers every other validity token without
        # ever calling this function.
        return VALIDITY_OK
    if query == "assurance_axis":
        return assurance_axis(report)
    if query == "no_baseline_audit":
        # `compare --no-baseline`'s own discriminator: this report's
        # top-level `verdict` is always null (no comparison ran at all), so
        # the `compat_verdict` query prints nothing for it and the exit-0
        # dispatch silently defaulted to VERDICT=COMPATIBLE -- "No binary
        # ABI break detected" for a run that never compared two builds, and
        # the same flattening for a risk-only audit that found something
        # but didn't gate on it. Answers "clean" (no findings), "findings"
        # (candidate-side findings present, none gated -- this branch is
        # only reached for a real exit-0 run), or CannotTell (not a
        # no-baseline audit report at all).
        #
        # A suppressed finding counts as "findings" too, not "clean":
        # `findings` alone omits anything a `--suppress` rule matched --
        # those move to `suppressed_findings`, not away entirely (ADR-067
        # "record before disposing") -- so a fully suppressed audit was
        # reporting AUDIT_CLEAN ("no candidate-side finding was detected")
        # when the run actually recorded one, just disposed of by policy
        # rather than absent.
        if report.get("no_baseline") is not True:
            # An empty answer at exit 0, not CannotTell: the heredoc's own
            # `if` simply fell through for a non-audit report, and callers
            # string-compare this against "clean"/"findings" rather than
            # checking a status. Kept byte-identical so the exit-0 dispatch
            # sees exactly what it saw before.
            return ""
        findings = report.get("findings")
        suppressed = report.get("suppressed_findings")
        has_findings = (isinstance(findings, list) and findings) or (
            isinstance(suppressed, list) and suppressed
        )
        return "findings" if has_findings else "clean"
    if query == "coverage_contribution":
        return str(_either("contract_coverage_exit_contribution", 0))
    if query == "assurance_contribution":
        # Answers the raw contribution when the report carries one, and
        # CannotTell when it does not -- where the heredoc printed a
        # defaulted `0` for the absent case. `_assurance_gated` tests for an
        # exact "1", which an empty answer fails identically, so the gate is
        # unchanged; what changes is that "the report did not say" is no
        # longer spelled the same way as "the report said no". The
        # classification of *why* it is absent belongs to `assurance_axis`.
        contribution = _assurance_contribution(report)
        if contribution is None:
            raise CannotTell
        # `str(...)`, matching `print(...)` exactly for every shape a report
        # can hold here -- see `_contribution_gates` for why the raw value
        # is passed through rather than normalized to 0/1.
        return str(contribution)
    if query == "severity_exit":
        # An absent `severity` block is the legacy scheme, whose exit codes
        # are 0/2/4 -- never 1 -- so the compatibility axis contributed 0 by
        # construction. Only an unreadable report is "cannot tell", and that
        # never reaches this function.
        return str(_severity().get("exit_code", 0))
    if query == "compat_verdict":
        # The *compatibility* axis's own verdict, which a severity scheme
        # never rewrites -- `compare` reports `result.verdict`
        # unconditionally, and the gate's own demotion of the exit code
        # never touches that label. It is therefore the only signal that
        # tells a genuinely clean run from a break the user chose not to
        # gate on.
        # cli-mirror: abicheck/policy/exit_decision.py::ExitDecision
        #
        # Read from an abicheck-native JSON report's own `verdict` key
        # alone. A SARIF `runs[0].properties.abiVerdict` fallback used to
        # live here too; ADR-063 Track T8 retired it with the rest of the
        # boundary's verdict reconstruction, and `_json_report_src` -- the
        # only source this reader is ever handed -- never yields a SARIF
        # document in the first place.
        return str(_either("verdict", "") or "")
    if query == "operational_verdict":
        # The report's verdict when it is an *operational* one rather than a
        # compatibility tier -- see `OPERATIONAL_VERDICTS`. Empty otherwise, so
        # a caller can branch on "did this report answer the compatibility
        # question at all" without keeping a second copy of the set.
        _verdict = str(_either("verdict", "") or "")
        return _verdict if _verdict in OPERATIONAL_VERDICTS else ""
    if query == "blocking_categories":
        return ", ".join(str(c) for c in (_severity().get("blocking_categories") or []))
    if query == "coverage_where":
        return ", ".join(
            sorted(
                {
                    "{}/{}".format(f.get("side"), f.get("provider"))
                    for f in (_either("contract_coverage_failures", []) or [])
                    if isinstance(f, dict)
                }
            )
        )
    if query == "scope_contribution":
        # ADR-065 S2 (D6/D7): the completeness axis's two 0/1 fold
        # participants, carried on a directory/package release report's
        # root `exit` block. Answered as their max -- the same "did this
        # axis contribute" answer `coverage_contribution` gives for its own.
        # A report carrying *neither* key has no scope axis to answer from
        # (an older abicheck, or a scalar report), so this is CannotTell
        # rather than a `0` that would read as "did not fire".
        #
        # The stored-baseline dispatch (`compare_bundle_facts.py`) emits no
        # root `exit` block; its `comparison_scope` section carries the same
        # two contributions under the `*_exit_contribution` names, so that
        # is the second source before "cannot tell".
        block = _either("exit", {})
        block = block if isinstance(block, dict) else {}
        scope = report.get("comparison_scope")
        scope = scope if isinstance(scope, dict) else {}
        root_keys = (
            "incomplete_scope_contribution",
            "no_comparison_completed_contribution",
        )
        section_keys = (
            "incomplete_scope_exit_contribution",
            "no_comparison_completed_exit_contribution",
        )
        if any(key in block for key in root_keys):
            source, keys = block, root_keys
        elif any(key in scope for key in section_keys):
            source, keys = scope, section_keys
        else:
            raise CannotTell
        return str(max(1 if source.get(key) == 1 else 0 for key in keys))
    if query == "scope_incomplete":
        # ADR-065 D6, informational: whether the report *recorded* an
        # incomplete scope at all, whatever it contributed -- under the
        # default `scope.on_incomplete: warn` both contributions are 0, and
        # the summary must still name what went unchecked rather than read
        # as a plain COMPATIBLE. Never a gate: `_scope_gated` alone decides
        # failure.
        scope = report.get("comparison_scope")
        scope = scope if isinstance(scope, dict) else {}
        outcome = _either("run_outcome", {})
        outcome = outcome if isinstance(outcome, dict) else {}
        if (
            scope.get("completeness") == "incomplete"
            or outcome.get("scope") == "incomplete"
        ):
            return "1"
        raise CannotTell
    if query == "scope_where":
        # What went unchecked, from the release report's `comparison_scope`
        # block -- the actionable half, the same way `coverage_where` names
        # the provider that fell short.
        scope = report.get("comparison_scope")
        scope = scope if isinstance(scope, dict) else {}
        parts = (
            ["no comparison completed"] if scope.get("no_comparison_completed") else []
        )
        parts.extend(
            _markdown_safe(name)
            for name in (scope.get("unchecked") or [])
            if isinstance(name, str)
        )
        return ", ".join(parts)
    if query == "assurance_notes":
        # `analysis_assurance.notes` -- same field name and shape at the
        # document root and under `diff`, read through the same `_either`
        # fallback the coverage/severity queries above already use.
        block = _either("analysis_assurance", {})
        notes = block.get("notes") if isinstance(block, dict) else None
        return "; ".join(str(note) for note in (notes or []))
    if query == "run_outcome":
        # ADR-063 Phase 7 (D6): the report's own `run_outcome` block -- the
        # canonical, already-folded `compatibility`/`gate`/`operational`
        # axes. *arg* names which axis to answer; an absent block, an
        # absent axis, or a non-string value (`compatibility`/`assurance`
        # are `null` on a report that never ran a real comparison) all
        # answer empty -- the same "cannot tell" contract every other query
        # follows, so a caller falls back to its own pre-existing
        # derivation (`compat_verdict`/`severity_exit`) rather than
        # misreading `null` as a real answer.
        outcome = _either("run_outcome", None)
        outcome = outcome if isinstance(outcome, dict) else {}
        value = outcome.get(arg)
        return value if isinstance(value, str) else ""
    if query == "annotations":
        lines = _annotations(report, additions=arg == "1")
        return "\n".join(lines) if lines else None
    raise UnknownQuery(query)


def _markdown_safe(text: object) -> str:
    """Flatten one PR-controlled name for interpolation into a Markdown span.

    Member names are PR-controlled file names and every sink interpolates
    this value inside a Markdown code span on one summary line, so each is
    flattened first: a line break, a control character, a backtick, or a
    table pipe in a file name must not terminate the span or forge a
    heading/row/verdict in ``$GITHUB_STEP_SUMMARY``.
    """
    out = []
    for char in str(text):
        if char in "`|":
            out.append("'" if char == "`" else "/")
        elif char in "\r\n\t\f\v" or ord(char) < 0x20 or ord(char) == 0x7F:
            out.append(" ")
        else:
            out.append(char)
    return " ".join("".join(out).split()) or "?"


#: Matches ``annotations.py``'s own ``_MAX_ANNOTATIONS`` -- GitHub Actions
#: caps visible annotations per step at roughly the same figure, and sorting
#: by severity first means a truncated tail is the least important one.
MAX_ANNOTATIONS = 50

#: Severity order for the annotation sort; also the set of levels accepted
#: at all, since an entry's own ``::LEVEL `` prefix must agree with it.
_ANNOTATION_ORDER = {"error": 0, "warning": 1, "notice": 2}


def _annotations(report: dict[str, Any], *, additions: bool) -> list[str]:
    """The report's persisted workflow-command annotations, filtered and ordered.

    Reads the persisted ``annotations`` array (schema 2.43/2.44) instead of
    relying on ``compare --annotate``'s own stderr rendering, so this works
    for BOTH a single-library compare (top-level ``annotations``) and a
    directory/package release compare (``libraries[].annotations``,
    flattened here across every library) uniformly.
    """
    entries = report.get("annotations")
    if not isinstance(entries, list):
        entries = []
        libraries = report.get("libraries")
        if isinstance(libraries, list):
            for library in libraries:
                if isinstance(library, dict) and isinstance(
                    library.get("annotations"), list
                ):
                    entries.extend(library["annotations"])
    kept = []
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("annotation"):
            continue
        level = entry.get("level")
        annotation = entry["annotation"]
        # `_json_report_src` can, in a rare failure-before-write case,
        # resolve to a JSON file this invocation never produced (a stale
        # --output-file/--write destination that already existed in the
        # checked-out tree before abicheck ran, e.g. one a PR author
        # committed). Printing `annotation` verbatim in that case would echo
        # an arbitrary, attacker-controlled workflow command -- including
        # one designed to smuggle a *different* command past this check via
        # an embedded newline (GitHub parses every stdout line as a
        # potential command). Never trust the string as-is: it must be a
        # single line (no embedded \n/\r), and its own `::LEVEL ` prefix
        # must agree with the entry's separately-typed `level` field --
        # exactly the shape `annotations._format_annotation()` always
        # produces. Anything else is dropped rather than printed.
        if (
            not isinstance(annotation, str)
            or not isinstance(level, str)
            or "\n" in annotation
            or "\r" in annotation
            or not annotation.startswith(f"::{level} ")
            or level not in _ANNOTATION_ORDER
        ):
            continue
        # `always_visible` is schema 2.44+; a report from an older abicheck
        # (this Action can be pinned to any released version) may carry
        # `annotations` without it -- degrade to "visible unless it's a
        # notice", the same rule `--annotate` (no `--annotate-additions`)
        # already applied before `always_visible` existed.
        visible = entry.get("always_visible", level != "notice")
        if additions or visible:
            kept.append(entry)
    kept.sort(key=lambda entry: _ANNOTATION_ORDER.get(str(entry.get("level")), 99))
    return [entry["annotation"] for entry in kept[:MAX_ANNOTATIONS]]


def main(argv: list[str]) -> int:
    """``report_query.py <report-path> <query> [arg]``."""
    if len(argv) < 3:
        return 2
    path, query = argv[1], argv[2]
    arg = argv[3] if len(argv) > 3 else ""
    validity, report = classify_document(path)
    if query == "report_validity":
        # Answered for every document, valid or not -- this query exists so
        # `run.sh` can establish that a report it required was unreadable,
        # which no other query can report without also changing the
        # deliberate exit-1 fallback every axis query depends on.
        print(validity)
        return 0
    if report is None:
        return 1
    try:
        printable = answer(report, query, arg)
        if printable is not None:
            print(printable)
    except CannotTell:
        return 1
    except UnknownQuery:
        return 2
    except Exception:
        # A malformed-but-loadable report can drive any query into a
        # TypeError (`severity.blocking_categories` holding an int, say).
        # The heredoc this replaced ran under `2>/dev/null` and so turned
        # every such crash into a bare exit 1, which every caller already
        # reads as "cannot tell" -- preserved here deliberately, and as a
        # return rather than a traceback so a corrupt report cannot spray
        # one into the Action log. `answer` is pure and holds no state, so
        # there is nothing to leave inconsistent.
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via subprocess
    raise SystemExit(main(sys.argv))
