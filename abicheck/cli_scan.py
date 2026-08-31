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

"""CLI — the deterministic ``scan`` orchestrator (ADR-035 D3, G19.3 / Phase 3).

``scan`` is a thin front-end over the existing ``dump``/``compare`` engine that
wires together the three ADR-035 pieces into one coverage-annotated report:

1. **classify** the PR's changed paths into a numeric risk score (``risk.py``);
2. run the **always-on tier** — the compiler-free lexical pattern pre-scan
   (``pattern_scan.py``, S3) and the intra-version cross-source checks
   (``crosscheck.py``, D4) — every time;
3. run the **pinned** evidence level (the ``--depth`` dial, resolved by
   ``scan_levels.py``; the deprecated ``--mode``/``--source-method`` aliases map
   onto it), POI-scoped to the changed paths, by collecting L3/L4/L5 inline at the
   matching ADR-033 D2 evidence mode;
4. if a ``--baseline`` is given, ``compare`` against it while keeping
   single-version cross-source checks advisory unless explicitly promoted;
5. emit **one** report stating, per layer/method, what ran vs. skipped (never a
   bare "source scan failed").

Determinism (ADR-035 D3): the level is fixed by the pinned ``--depth`` (or its
deprecated ``--mode``/``--source-method`` aliases); the risk score escalates the
level **only** when ``--depth`` is omitted (the ``auto`` default). ``--budget`` is
a failure guard on the chosen level — it never silently shrinks scope.

The authority rule (ADR-028 D3 / ADR-035 D1) is preserved: ``scan`` adds no new
authority — cross-source and pattern findings are ``RISK``/``API_BREAK`` only,
never ``BREAKING`` on their own.

Split out of :mod:`abicheck.cli` per the sibling-module pattern; imported for
side-effect at the bottom of :mod:`abicheck.cli` so ``@main.command`` runs.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import click

from .buildsource.crosscheck import (  # noqa: F401 - CrosscheckConfig/run_crosschecks re-exported for tests
    ALL_CHECKS,
    CrosscheckConfig,
    run_crosschecks,
)
from .buildsource.scan_levels import (
    EvidenceDepth,
    ScanMode,
    SourceMethod,
    SourceScope,
    level_to_collect_mode,
    resolve_level,
)
from .checker_policy import (  # noqa: F401 - re-export for tests
    API_BREAK_KINDS,
    BREAKING_KINDS,
)
from .cli import _safe_write_output, _setup_verbosity, main
from .cli_compare_options import _cli_flag, _warn_force_public_ignored
from .cli_options import (
    artifact_set_options,
    compile_context_options,
    env_matrix_option,
    lang_option,
    merge_compile_config,
    pack_option,
    policy_options,
    resolve_compile_context,
    resolve_contract_domain,
    resolve_contract_evaluation,
    scope_options,
    secondary_output_options,
    severity_options,
    split_sided_paths,
    verbose_option,
)
from .cli_params import DEPTH_PARAM, SIDED_PATH_PARAM, _load_suppression_and_policy
from .cli_scan_baseline import (
    _baseline_is_native_library,  # noqa: F401 - re-export for scan tests/service_scan
    _emit_estimate,  # noqa: F401 - re-export; --estimate CLI flag removed, kept for direct callers
    _expand_public_headers,  # noqa: F401 - re-export for tests
    _load_risk_rules,
    _public_provenance_set,
    _run_baseline_compare,  # noqa: F401 - re-export for scan tests
)
from .cli_scan_helpers import (  # noqa: F401 - coverage/depth helpers re-exported for tests
    _intrinsic_coverage,
    _l3_collected,
    _pack_coverage,
    _source_abi_coverage,
    _uses_debug_presence_only,
    l4_coverage_advisories,
    reject_incoherent_scan_operands as _reject_incoherent_scan_operands,
    reject_incoherent_scan_secondary_output as _reject_incoherent_secondary_output,
    render_baseline_lines,
    render_coverage_lines,
    render_crosscheck_lines,
    render_pattern_lines,
    render_preprocessor_lines,
    render_summary_lines,
    render_verdict_lines,
    resolve_effective_allow_query,
    scan_pattern_roots,
)
from .frontends.cli.help import scan_help_options

# The scan *engine* (classify → always-on tier → level → compare) lives in
# scan_engine.py, not here — this module is a thin Click front-end over it
# (ADR-037 D1: frontends depend on the engine, never the reverse).
# service_scan.run_scan imports the same symbols from the same module, so the
# CLI and the typed service API share one engine instead of the service
# reaching into a front-end module (see scan_engine.py's module docstring).
from .scan_engine import (  # noqa: F401 - several re-exported for tests/service_scan parity
    ScanCoreResult,
    ScanOutcome,
    _audit_exit_code,
    _BudgetOverflow,
    _build_new_snapshot,
    _build_scan_poi,
    _crosscheck_severity_exit,
    _EvidenceContractError,
    _load_exports_for_poi,
    run_scan_core,
)
from .workflows.extraction import (  # noqa: F401 - re-exported for tests
    build_points_of_interest,
    resolve_symbol_tus,
    run_preprocessor_scan,
    scan_files,
)
from .workflows.scan_config import RiskScore, score_changed_paths

if TYPE_CHECKING:
    from .workflows.scan_abort_result import ScanAbortAxis

#: Back-compat alias — the resolver moved to ``cli_options`` (ADR-037 D3: one
#: resolver shared by compare/dump/scan). Kept importable from here for existing
#: callers and ``tests/test_compile_context_parity.py``.
_merge_compile_config = merge_compile_config

#: Exit code for a ``--budget`` overflow (ADR-035 D3: a budget always fails,
#: never silently shrinks scope). Distinct from the verdict codes (0/2/4) and the
#: generic error code (1) so CI can tell a budget overflow from a real break.
_EXIT_BUDGET_OVERFLOW = 5

#: Suffixes ``time``-style duration strings accept (``15m``, ``900s``, ``1h``).
_DURATION_UNITS: dict[str, int] = {"s": 1, "m": 60, "h": 3600}

#: Valid per-check severity levels for ``--crosscheck KEY=LEVEL``. ``off`` removes
#: the check; the others keep it enabled (the label rides into the report).
_CROSSCHECK_LEVELS = frozenset({"off", "info", "warning", "error"})

#: ChangeKinds that ride the same advisory→gating promotion path as the
#: cross-checks but are NOT toggleable engine checks. Accepted as
#: ``--crosscheck KEY=LEVEL`` severity keys so a maintainer can promote them to
#: ``error`` to gate CI (ADR-035 D6), without being part of the on/off
#: ``ALL_CHECKS`` set.
#:
#: Only the ``--abi3`` **audit** finding is here: it is injected into
#: ``cc.findings`` (below), which is what ``_crosscheck_severity_exit`` inspects,
#: so promoting it actually gates. The other CPython kinds
#: (``python_abi3_dropped`` / ``python_gil_abi_changed`` /
#: ``python_abi3_floor_raised``) are **compare-time** — they only arise under
#: ``scan --baseline`` via ``_run_baseline_compare`` and live in the baseline
#: diff's ``DiffResult``, not ``cc.findings``. They therefore gate through the
#: *compare* verdict/severity path (like every other RISK kind), not this one;
#: adding them here would accept the flag but silently fail to honour it.
_PROMOTABLE_FINDING_KINDS = frozenset({"python_stable_abi_violation"})


def _parse_budget(value: str | None) -> float | None:
    """Parse a ``time``-style duration (``15m``/``900s``/``1h``) to seconds.

    A bare number is read as seconds. Returns ``None`` for an empty value; raises
    :class:`click.BadParameter` for an unparseable one.
    """
    if not value:
        return None
    raw = value.strip().lower()
    unit = 1
    if raw and raw[-1] in _DURATION_UNITS:
        unit = _DURATION_UNITS[raw[-1]]
        raw = raw[:-1]
    try:
        amount = float(raw)
    except ValueError as exc:
        raise click.BadParameter(
            f"invalid --budget {value!r}; use e.g. 15m, 900s, 1h"
        ) from exc
    if amount < 0:
        raise click.BadParameter(f"--budget must be non-negative, got {value!r}")
    return amount * unit


def _git_changed_paths(since: str, cwd: Path | None) -> list[str] | None:
    """Paths changed vs. a git ref via ``git diff --name-only`` (no shell).

    Returns the changed-path list on success (possibly **empty** for a no-op
    diff), or ``None`` when the seed could not be produced (missing git / non-repo
    / bad ref). The caller distinguishes the two: a successful empty diff is a
    valid "nothing changed" seed (auto → s0), whereas ``None`` means no seed and
    auto falls back to the mode preset (ADR-035 D7 / Codex review).
    """
    try:
        proc = subprocess.run(
            ["git", "diff", "--name-only", f"{since}...HEAD"],
            cwd=str(cwd) if cwd is not None else None,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        click.echo(f"warning: --since: could not run git diff: {exc}", err=True)
        return None
    if proc.returncode != 0:
        click.echo(
            f"warning: --since {since!r}: git diff failed "
            f"({proc.stderr.strip() or 'non-zero exit'}); scanning broadly.",
            err=True,
        )
        return None
    return [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]


def _parse_crosschecks(
    pairs: tuple[str, ...],
) -> tuple[frozenset[str], dict[str, str]]:
    """Parse ``--crosscheck KEY=LEVEL`` flags into ``(enabled, severities)``.

    Unknown keys / levels raise :class:`click.BadParameter`. A bare ``KEY`` (no
    ``=LEVEL``) enables the check at the default ``warning`` level. ``KEY=off``
    drops it from the enabled set. With no flags, every check runs (the engine's
    own default).
    """
    if not pairs:
        return frozenset(ALL_CHECKS), {}
    enabled = set(ALL_CHECKS)
    severities: dict[str, str] = {}
    for pair in pairs:
        key, sep, level = pair.partition("=")
        key = key.strip()
        level = level.strip().lower() if sep else "warning"
        if key not in ALL_CHECKS and key not in _PROMOTABLE_FINDING_KINDS:
            raise click.BadParameter(
                f"unknown cross-check {key!r}; choose from "
                f"{', '.join((*ALL_CHECKS, *sorted(_PROMOTABLE_FINDING_KINDS)))}"
            )
        if level not in _CROSSCHECK_LEVELS:
            raise click.BadParameter(
                f"invalid level {level!r} for {key!r}; "
                f"choose from {', '.join(sorted(_CROSSCHECK_LEVELS))}"
            )
        if level == "off":
            # A promotable finding kind is not part of the on/off enabled set
            # (it comes from the --abi3 audit, not a toggleable engine check), so
            # `off` only meaningfully applies to a real cross-check.
            enabled.discard(key)
        else:
            severities[key] = level
    return frozenset(enabled), severities


def _normalize_depth_inputs(
    depth: EvidenceDepth,
    headers: tuple[Path, ...],
    baseline_header: tuple[Path, ...],
    sources: Path | None,
    build_info: Path | None,
) -> tuple[tuple[Path, ...], tuple[Path, ...], Path | None, Path | None]:
    """Prune inputs that would collect evidence above the effective scan depth."""
    if depth is not EvidenceDepth.BINARY:
        return headers, baseline_header, sources, build_info
    return (), (), None, None


def _render_text(out: ScanOutcome, *, show_suppressed: bool = False) -> str:
    """Render the human-facing scan report by composing its section blocks."""
    lines: list[str] = []
    lines += render_summary_lines(out)
    lines += render_coverage_lines(out)
    lines += render_crosscheck_lines(out)
    lines += render_pattern_lines(out)
    lines += render_preprocessor_lines(out)
    lines += render_baseline_lines(out, show_suppressed=show_suppressed)
    lines += render_verdict_lines(out)
    return "\n".join(lines)


def _resolve_changed_seed(
    changed_paths_opt: tuple[str, ...],
    since: str | None,
    sources: Path | None,
) -> tuple[list[str], str, bool]:
    """Resolve the changed-path seed → ``(changed, changed_src, seeded)``.

    ``--changed-path`` wins; else ``--since`` via git; else none. ``seeded`` tracks
    whether a *valid* seed was produced — a successful empty diff (seeded, no
    paths) is distinct from a missing/failed seed (not seeded): the former lets
    auto pick s0 (no-op PR), the latter falls back to the broad mode preset
    (ADR-035 D7 / Codex review).
    """
    if changed_paths_opt:
        return list(changed_paths_opt), "--changed-path", True
    if since:
        git_changed = _git_changed_paths(since, sources)
        if git_changed is None:
            return [], f"--since {since} (seed failed; broad scope)", False
        return git_changed, f"--since {since}", True
    return [], "none (no diff seed; broad scope)", False


def _parse_abi3_floor(abi3: str | None) -> tuple[int, int] | None:
    """Parse the --abi3 target ``Py_LIMITED_API`` floor, or ``None`` when off.

    An invalid floor (non-3 major, implausible minor, trailing junk) is a usage
    error.
    """
    if abi3 is None:
        return None
    from . import stable_abi

    floor = stable_abi.parse_abi3_version(abi3)
    if floor is None:
        raise click.BadParameter(f"invalid --abi3 version: {abi3!r}")
    return floor


def _resolve_auto_source_method(
    sm: SourceMethod | None,
    dp: EvidenceDepth | None,
    mode_explicit: bool,
    seeded: bool,
    risk: RiskScore,
) -> tuple[SourceMethod | None, bool, Any]:
    """Opt an unpinned scan into risk-driven auto (ADR-037 D5).

    The unset dial means 'auto' — only when *nothing* was pinned (no --depth, no
    --source-method, no explicit --mode). auto uses the risk score ONLY when a
    valid diff seed was produced; a missing/failed seed falls back to the mode
    preset so a bad-ref CI run doesn't silently drop all L3-L5 evidence.
    """
    if sm is None and dp is None and not mode_explicit:
        sm = SourceMethod.AUTO
    is_auto = sm is SourceMethod.AUTO
    auto_method = risk.recommended_method if (is_auto and seeded) else None
    return sm, is_auto, auto_method


def _scan_explicit_flags(
    source_method: str | None,
    depth: str | None,
) -> tuple[bool, bool]:
    """The two deliberately-distinct 'explicit' notions (ADR-037), as a pair.

    ``level_explicit`` — consent to auto-run build.query (a non-auto
    --source-method, or --depth ONLY when no --source-method is given).
    ``pinned_explicit`` — the auto-strict evidence contract (an explicit --depth
    always pins, or a non-auto --source-method). --mode is never a pin.
    """
    sm_pin = source_method is not None and source_method != SourceMethod.AUTO.value
    level_explicit = sm_pin or (source_method is None and depth is not None)
    pinned_explicit = (depth is not None) or sm_pin
    return level_explicit, pinned_explicit


def _scan_pre_coverage_base_exit(outcome: ScanOutcome) -> int:
    """This run's compatibility exit code *before* the coverage floor was folded.

    The number ADR-049 §7's coverage diagnostic must explain itself against.
    Under a resolved ``severity`` scheme that base is the severity gate's own
    exit code -- which ``_run_baseline_compare`` publishes as
    ``diff_summary["severity"]["exit_code"]``, taken from the very
    ``compute_gate_decision`` result it also gated on -- and only otherwise the
    verdict's legacy ``{0,2,4}`` mapping. Read back rather than recomputed so
    the published explanation cannot disagree with the exit code the process
    actually returned (Codex review).
    """
    from .cli_compare_helpers import _verdict_exit_code

    summary = outcome.diff_summary or {}
    gate = summary.get("severity")
    if isinstance(gate, dict):
        code = gate.get("exit_code")
        if isinstance(code, int):
            return code
    return _verdict_exit_code(outcome.verdict)


def _render_scan_report_text(
    outcome: ScanOutcome, fmt: str, *, show_suppressed: bool = False
) -> str:
    return (
        json.dumps(outcome.to_dict(), indent=2)
        if fmt == "json"
        else _render_text(outcome, show_suppressed=show_suppressed)
    )


def _emit_scan_report(
    outcome: ScanOutcome,
    fmt: str,
    output: Path | None,
    *,
    show_suppressed: bool = False,
    secondary_fmt: str | None = None,
    secondary_output: Path | None = None,
) -> None:
    """Render the scan outcome, write/echo it, and exit non-zero on a verdict.

    ``secondary_fmt``/``secondary_output`` render the same already-computed
    ``outcome`` a second time to a second path -- e.g. a human ``--format
    text`` report alongside a ``--write json=scan.json`` artifact for
    tooling -- without a second scan (mirrors ``compare --write``;
    see the GitHub Action's own PR-comment renderer, which uses exactly this
    to avoid re-running a potentially --depth build/source-expensive scan
    just to get JSON out of a --format text invocation).
    """
    text = _render_scan_report_text(outcome, fmt, show_suppressed=show_suppressed)
    if output:
        _safe_write_output(output, text)
        click.echo(f"Report written to {output}", err=True)
    else:
        click.echo(text)

    if secondary_fmt is not None and secondary_output is not None:
        secondary_text = _render_scan_report_text(
            outcome, secondary_fmt, show_suppressed=show_suppressed
        )
        _safe_write_output(secondary_output, secondary_text)
        click.echo(f"Secondary report written to {secondary_output}", err=True)

    # ADR-049 §7: a coverage-gated exit must say so. `scan --format json`
    # carries the ledger in its own summary; every other renderer ignores
    # those keys, so without this the command prints "Verdict: NO_CHANGE"
    # and then fails with no explanation (Codex review). A secondary `text`
    # report has the identical gap even when the primary format is `json`
    # (Codex review, follow-up): the primary JSON carries the ledger, but the
    # secondary text file doesn't, and this stderr notice is the only place
    # that gap gets explained -- so it must fire whenever *either* renderer
    # in play is `text`, not only when the primary one is.
    if fmt != "json" or secondary_fmt == "text":
        from .workflows.gate import coverage_diagnostic_from_summary

        # `outcome.exit_code` has ALREADY had the coverage floor folded in
        # by `_run_baseline_compare`, so passing it would make the notice
        # say "contributes 1 to an exit that was already 1" for a run where
        # coverage is exactly what raised 0 to 1 (Codex review).
        #
        # The pre-coverage base is the *severity* gate's exit code when this
        # run resolved the severity scheme, and only otherwise the verdict's
        # own code. Re-deriving `_verdict_exit_code(verdict)` unconditionally
        # misreported the severity case: a COMPATIBLE_WITH_RISK diff promoted
        # to 2 by a config `severity.potential_breaking: error` alongside a coverage
        # failure exits 2, but the notice claimed coverage floored it to 1
        # (Codex review). `_run_baseline_compare` emits the gate block from
        # the same `compute_gate_decision` result it took its own base from,
        # so this reads back the number that actually gated, rather than a
        # second guess at it.
        notice = coverage_diagnostic_from_summary(
            outcome.diff_summary,
            base_exit=_scan_pre_coverage_base_exit(outcome),
        )
        if notice is not None:
            click.echo(notice, err=True)

    if outcome.exit_code != 0:
        sys.exit(outcome.exit_code)


def _emit_scan_abort_report(
    axis: ScanAbortAxis,
    fmt: str,
    output: Path | None,
    *,
    prior_decision: dict[str, object] | None = None,
    secondary_fmt: str | None = None,
    secondary_output: Path | None = None,
) -> None:
    """Give ``scan --format json`` a real report on a `_BudgetOverflow`/
    `_EvidenceContractError` abort, instead of empty stdout (ADR-064 stage
    1b, native-CLI half). Before this, a ``--format json`` invocation that
    aborted here produced no stdout content at all -- so a consumer parsing
    it as JSON was already broken; this only adds content where none
    existed, it does not change either abort's exit code or its existing
    stderr message. `--format text` is unchanged: `bo.message`/`ce.message`
    already read as the human-facing explanation, and there is no
    `ScanOutcome` to feed `_render_text` (most of its fields were never
    computed at this point) -- inventing prose for that gap is a separate,
    genuinely open design question ADR-064 leaves unresolved. Reuses
    `abicheck.workflows.scan_abort_result.scan_abort_result_fields`, the
    exact function the typed `ScanResult` API now builds its own
    `report["exit"]` from, so the CLI and library JSON payloads agree.

    *secondary_fmt*/*secondary_output* cover ``--format text --write
    json=...`` (Codex review, fresh evidence): the GitHub Action's own text
    primary + JSON secondary combination gets the same abort payload the
    secondary artifact would have carried had the scan completed, instead
    of a missing file just because the primary renderer wasn't JSON.
    """
    if fmt != "json" and secondary_fmt != "json":
        return
    from .workflows.scan_abort_result import scan_abort_result_fields

    report = scan_abort_result_fields(axis, prior_decision=prior_decision)["report"]
    text = json.dumps(report, indent=2)
    if fmt == "json":
        if output:
            _safe_write_output(output, text)
            click.echo(f"Report written to {output}", err=True)
        else:
            click.echo(text)
    if secondary_fmt == "json" and secondary_output:
        _safe_write_output(secondary_output, text)
        click.echo(f"Secondary report written to {secondary_output}", err=True)


def _resolve_artifact_set_paths(spec: tuple[str, ...]) -> tuple[list[Path], bool]:
    """``--artifact-set`` values → ``(paths, explicit)`` (ADR-056).

    ``spec`` is the tuple Click's repeatable ``--artifact-set`` collects (CLI
    cleanup phase two, PR 5 -- the comma-separated single-string form this
    replaced is gone, no alias). A single value naming a directory expands
    to every discoverable shared library in it (``explicit=False`` -- an
    unsupported file found this way is silently skipped, mirroring
    ``build_bundle_snapshot``'s directory-scan behavior); anything else is
    an explicit path list, one member per occurrence, every member of which
    must resolve (``explicit=True``, per :func:`bundle.discover_artifact_set`).
    """
    from .workflows.extraction import discover_shared_libraries

    if len(spec) == 1:
        candidate = Path(spec[0])
        if candidate.is_dir():
            return discover_shared_libraries(candidate), False
    paths: list[Path] = []
    for part in spec:
        p = Path(part)
        if not p.exists():
            raise click.UsageError(f"--artifact-set member not found: {part}")
        paths.append(p)
    return paths, True


def _render_member_findings_lines(result: Any) -> list[str]:
    """Render one artifact-set member's cross-check/pattern/preprocessor
    findings for text output (P2, Codex review): the artifact-set text
    report previously showed only ``path: verdict`` per member -- unlike the
    single-binary ``scan``'s richly-rendered report and the aggregate JSON's
    nested ``report``, it gave no finding descriptions or evidence
    explaining *why* a member was flagged, leaving CLI/Action-summary users
    unable to act on the result.

    Reuses the same section renderers the single-binary path uses
    (:func:`render_crosscheck_lines`/:func:`render_pattern_lines`/
    :func:`render_preprocessor_lines`) via a minimal attribute shim, since
    those renderers only ever read the report's already-plain-dict
    ``crosscheck``/``pattern_scan``/``preprocessor_scan`` keys -- not any
    behavior specific to the full :class:`~abicheck.scan_engine.ScanOutcome`
    object ``ScanArtifactResult.result.report`` was flattened from.
    """
    report = result.report or {}
    if not report:
        return []
    shim = SimpleNamespace(
        crosscheck=report.get("crosscheck") or {},
        crosscheck_severities=report.get("crosscheck_severities") or {},
        pattern=report.get("pattern_scan") or {},
        preprocessor=report.get("preprocessor_scan") or {},
        audit=report.get("mode") == "audit",
    )
    lines = render_crosscheck_lines(shim)
    lines += render_pattern_lines(shim)
    lines += render_preprocessor_lines(shim)
    return [f"  {ln}" if ln else "" for ln in lines]


def _render_artifact_set_text(result: Any) -> str:
    """Human-facing render of a :class:`ScanSetResult` (ADR-056).

    Reuses :func:`bundle.render_bundle_findings_markdown` (G34 Phase 4) for
    the bundle-findings section, the same helper
    ``cli_compare_release_helpers._release_md_bundle_findings`` calls for
    the two-sided ``compare``/release path — one rendering for
    :class:`bundle.BundleFinding`, regardless of which side produced it.
    """
    from .bundle import render_bundle_findings_markdown

    lines: list[str] = [
        f"Artifact-set scan verdict: {result.verdict} (exit {result.exit_code})",
        "",
        "Per-artifact results:",
    ]
    for member in result.per_artifact:
        lines.append(f"  {member.artifact}: {member.result.verdict}")
        lines.extend(_render_member_findings_lines(member.result))
    lines.append("")
    if result.bundle_incomplete:
        lines.append("Bundle analysis: incomplete (artifact-set discovery failed)")
    elif result.verdict == "BUDGET_OVERFLOW":
        # CodeRabbit review: run_scan_set() returns BUDGET_OVERFLOW before
        # ever calling audit_bundle() -- bundle_incomplete/bundle_verdict
        # stay at their ScanSetResult defaults (False/None), so without
        # this branch the report fell through to the else below and
        # printed the misleading "Bundle analysis: None (0 finding(s))"
        # instead of stating the bundle audit never ran.
        lines.append("Bundle analysis: not run (budget overflow)")
    else:
        lines.append(
            f"Bundle analysis: {result.bundle_verdict} "
            f"({len(result.bundle_findings)} finding(s))"
        )
        lines.extend(render_bundle_findings_markdown(result.bundle_findings))
    return "\n".join(lines)


_COMPARISON_ONLY_FLAGS = {
    "suppress": "--suppress",
    "policy_file_path": "--policy",
    "policy": "--policy",
    "scope_public_headers": "--scope-public-headers/--no-scope-public-headers",
    "severity_preset": "--severity-preset",
    "exit_code_scheme": "--exit-code-scheme",
    "pattern_verdicts": "--pattern-verdicts/--no-pattern-verdicts",
    "env_matrix_path": "--env-matrix",
    "contract_mode": "--contract",
    # ADR-049 D8: a pack's only application here is the baseline comparison's
    # policy file, so without one it would configure nothing.
    "pack_paths": "--pack",
    # The findings/suppressed cap only applies to the --against summary
    # `_baseline_summary` builds -- without a baseline there is no such
    # summary to cap.
    "max_findings": "--max-findings",
    # P0.4: the exit-code floor `--require-complete-analysis` imposes is
    # folded alongside `--against`'s own verdict/coverage exit code
    # (`_run_baseline_compare`) -- without a baseline there is no such
    # exit code to floor. `analysis_assurance` is still always computed and
    # available in --format json for an audit-only scan; only the flag that
    # makes it *gate* requires a comparison.
    "require_complete_analysis": "--require-complete-analysis",
}


def _reject_comparison_only_flags(*, no_baseline_reason: str) -> None:
    """Reject any flag from :data:`_COMPARISON_ONLY_FLAGS` given explicitly
    on the command line, for a scan that has no ``--against`` baseline to
    apply them to.

    Shared by both the ``--against``-less single-binary path and the
    ``--artifact-set`` path (always audit-only, ADR-056 D2 -- Codex review:
    the single-binary check alone left this validation reachable only via
    ``against is None``, which the ``--artifact-set`` branch's early
    ``return`` never passes through, so these flags were silently parsed,
    validated, and then discarded for a set instead of erroring).
    """
    ctx = click.get_current_context()
    explicit = [
        flag
        for dest, flag in _COMPARISON_ONLY_FLAGS.items()
        if ctx.get_parameter_source(dest) == click.core.ParameterSource.COMMANDLINE
    ]
    if explicit:
        noun = "flag" if len(explicit) == 1 else "flags"
        raise click.UsageError(
            f"{', '.join(explicit)} only take effect with --against (they "
            f"configure the baseline comparison); drop {'this' if len(explicit) == 1 else 'these'} "
            f"{noun} or {no_baseline_reason}."
        )


def _run_artifact_set(
    *,
    artifact_set: tuple[str, ...],
    dry_run: bool,
    bundle_system_providers: str,
    header_pairs: tuple[tuple[str, Path], ...],
    include_pairs: tuple[tuple[str, Path], ...],
    public_header_dirs: tuple[Path, ...],
    sources: Path | None,
    build_info: Path | None,
    build_config: Path | None,
    build_targets: tuple[str, ...],
    depth: str | None,
    since: str | None,
    changed_paths_opt: tuple[str, ...],
    budget: str | None,
    abi3: str | None,
    crosschecks: tuple[str, ...],
    risk_rules_path: Path | None,
    lang: str,
    allow_build_query: bool,
    fmt: str,
    output: Path | None,
    header_backend: str,
    sysroot: Path | None,
    nostdinc: bool,
    frontend_context: str,
    gcc_options: str | None = None,  # removed as a CLI flag, PR 5/5; internal-only
    compiler_path: str | None = None,
    compiler_prefix: str | None = None,
    compiler_option_tokens: tuple[str, ...] = (),
) -> None:
    """``scan --artifact-set`` (ADR-056/G34): audit a set of libraries as one,
    no old side. Discovers the set, scans each member (the same tier +
    pinned level a single-binary scan runs), adds one cross-library
    bundle-audit pass. ``--dry-run`` previews it (``frontends.cli.
    artifact_set_dry_run``).
    """
    from .bundle import ArtifactSetError, discover_artifact_set
    from .service import Budget, ScanRequest
    from .service_scan import run_scan_set

    paths, explicit = _resolve_artifact_set_paths(artifact_set)
    try:
        discovered = discover_artifact_set(paths, explicit=explicit)
    except ArtifactSetError as exc:
        raise click.UsageError(str(exc)) from exc
    if len(discovered) < 2:
        raise click.UsageError(
            "--artifact-set must resolve to 2 or more libraries "
            f"(found {len(discovered)})."
        )

    header_both, header_old, header_new = split_sided_paths(header_pairs)
    if header_old or header_new:
        raise click.UsageError(
            "--header old=/new= scoping is not supported with --artifact-set "
            "(there is no old side to scope to)."
        )
    include_both, include_old, include_new = split_sided_paths(include_pairs)
    if include_old or include_new:
        raise click.UsageError(
            "--include old=/new= scoping is not supported with --artifact-set "
            "(there is no old side to scope to)."
        )

    # L2 compile context (dump<->scan<->compare parity, ADR-037 D3): the same
    # resolver the single-binary path uses, so an --artifact-set cross-scan
    # doesn't silently parse headers against the host toolchain when the
    # caller explicitly selected a target sysroot/toolchain (Codex review).
    compile_context, includes_tuple = resolve_compile_context(
        click.get_current_context(),
        gcc_options=gcc_options,
        sysroot=sysroot,
        nostdinc=nostdinc,
        header_backend=header_backend,
        includes=tuple(include_both),
        build_config=build_config,
        sources=sources,
        frontend_context=frontend_context,
        compiler_path=compiler_path,
        compiler_prefix=compiler_prefix,
        compiler_option_tokens=compiler_option_tokens,
    )

    changed, changed_src, seeded = _resolve_changed_seed(
        changed_paths_opt, since, sources
    )
    budget_s = _parse_budget(budget)
    abi3_floor = _parse_abi3_floor(abi3)
    enabled_checks, severities = _parse_crosschecks(crosschecks)
    bsp = tuple(s.strip() for s in bundle_system_providers.split(",") if s.strip())

    req = ScanRequest(
        binaries=list(discovered.values()),
        headers=list(header_both),
        includes=list(includes_tuple),
        public_header_dirs=list(public_header_dirs),
        sources=sources,
        build_info=build_info,
        baseline=None,
        mode="audit",
        # Unset means 'auto' (ADR-037 D5): only an omitted --depth opts a
        # member into risk-driven method selection; a pinned --depth stays
        # deterministic (Codex review: was hard-coded to None).
        source_method=SourceMethod.AUTO.value if depth is None else None,
        depth=depth,
        changed_paths=changed,
        seeded=seeded,
        budget=Budget(total_timeout=budget_s),
        lang=lang,
        compile=compile_context,
        abi3_floor=abi3_floor,
        enabled_checks=enabled_checks,
        severities=severities,
        build_config=build_config,
        allow_build_query=allow_build_query,
        risk_rules_path=risk_rules_path,
        bundle_system_providers=bsp,
        changed_src=changed_src,
        build_targets=build_targets,
    )
    if dry_run:
        from .bundle import check_artifact_set_soname_collisions
        from .dry_run import emit_dry_run
        from .frontends.cli.artifact_set_dry_run import render_artifact_set_dry_run
        from .service_scan import estimate_artifact_set
        try:
            # run_scan_set() rejects an ambiguous duplicate-DT_SONAME set (exit
            # 64) and a malformed --risk-rules profile the same way -- fail
            # loud here too, not a "successful" preview of a rejected request.
            check_artifact_set_soname_collisions(discovered)
            totals, notes, blocker, unknown_layers = estimate_artifact_set(
                req, list(discovered.values())
            )
        except (ArtifactSetError, ValueError) as exc:
            raise click.UsageError(str(exc)) from exc
        emit_dry_run(
            render_artifact_set_dry_run(
                req,
                discovered=discovered,
                explicit=explicit,
                header_backend=header_backend,
                fmt=fmt,
                totals=totals,
                notes=notes,
                blocker=blocker,
                unknown_layers=unknown_layers,
            )
        )
    try:
        # ArtifactSetError (ambiguous duplicate-SONAME set) and ValueError
        # (malformed --risk-rules, service_scan.py is click-free) both
        # translate to a usage error here, not an unhandled traceback.
        result = run_scan_set(req)
    except (ArtifactSetError, ValueError) as exc:
        raise click.UsageError(str(exc)) from exc

    text = (
        json.dumps(result.to_dict(), indent=2)
        if fmt == "json"
        else _render_artifact_set_text(result)
    )
    if output:
        _safe_write_output(output, text)
        click.echo(f"Report written to {output}", err=True)
    else:
        click.echo(text)
    if result.exit_code != 0:
        sys.exit(result.exit_code)


def _resolve_scan_evaluation_config(
    *,
    against: Path | None, contract_evaluation: bool, pack_paths: tuple[Path, ...],
    policy: str, policy_file: Any,
    project_cfg: Any, cfg_path: Path | None, project_sha256: str | None,
    suppression: Any, suppress: Path | None, symbols_list: Any,
    resolved_cfg: Any = None,
) -> tuple[Any, Any, Any]:
    """Resolve this scan's ADR-049 configuration and fold in any ``--pack``.

    Returns ``(resolved_config, policy_file, resolved_cfg)`` -- ``(None,
    policy_file, resolved_cfg)`` when there is nothing to resolve, i.e. no
    ``--against`` to compare against or neither ``--contract`` nor ``--pack``
    given. *resolved_cfg* is the caller's own already-resolved
    ``resolve_compare_config`` severity/exit-code-scheme object (``scan_cmd``'s
    ``sev_config``/``resolved_exit_scheme`` source) -- passed through
    unchanged unless a selected gate pack contributes to it (see below).

    ADR-049 Phase 5's "same typed config", for the third and last front end.
    ``checker.compare`` can only claim ``API_REQUEST`` for arguments it was
    handed, so without this the scan's persisted ``evaluation_context`` carried
    the core verb's reconstruction rather than real D7 provenance -- the same
    defect already fixed for ``compare``. Resolved here because this is where
    the Click context is: which flags the user actually typed is a question
    only the front end can answer.

    CLI cleanup phase two, PR B: a ``kind: gate`` pack (``gate.exit_code_
    scheme``/``gate.severity.*``) is folded into *resolved_cfg* the same way
    ``compare``'s own ``resolve_and_apply`` folds one into its
    ``ResolvedCompareConfig`` -- ``gate_supported=True`` below, no longer
    rejected. A scan's exit code has honored ``--severity-preset``/
    ``--exit-code-scheme`` (direct CLI flags and ``.abicheck.yml``) since the
    fix that closed the "scan never consults severity" gap; a gate pack is
    just one more source for the same already-real gate, mirroring the
    release fan-out's identical slice 2 fold
    (``cli_compare_release_helpers.apply_release_gate_pack``).
    """
    if against is None or not (contract_evaluation or pack_paths):
        return None, policy_file, resolved_cfg
    resolved_config = None
    from .cli_scan_receipt import SCAN_CONFIG_PARAMS, resolve_scan_config
    from .compatibility_evaluation_resolver import (
        FieldResolutionError,
        PackConflictError,
    )
    from .errors import PackManifestError

    _ctx = click.get_current_context()
    _params = {name: _ctx.params.get(name) for name in SCAN_CONFIG_PARAMS}
    _typed = {
        name
        for name in SCAN_CONFIG_PARAMS
        if _ctx.get_parameter_source(name) == click.core.ParameterSource.COMMANDLINE
    }
    try:
        resolved_config = resolve_scan_config(
            _params,
            typed=_typed,
            project_cfg=project_cfg,
            project_path=cfg_path,
            project_sha256=project_sha256,
            policy_file=policy_file,
            suppression=suppression,
            suppress_path=suppress,
            symbols_list=symbols_list,
        )
        if pack_paths:
            # ADR-049 D8: a pack that reached the receipt and not the
            # engine is exactly what got the flag reverted once before, so
            # its contributions are folded into the policy file -- and,
            # since CLI cleanup phase two "PR B", into the resolved
            # severity/exit-code-scheme config -- the baseline comparison
            # runs with. `gate_supported=True`: a scan's exit code already
            # honors `--severity-preset`/`--exit-code-scheme` (direct CLI
            # flags and `.abicheck.yml`), so a gate pack is one more source
            # for that same real gate rather than a field with nowhere to
            # go.
            from .pack_application import (
                apply_to_compare_config,
                check_resolved_config_applies_packs,
                pack_application,
                policy_file_with_packs,
            )

            # Emptiness is not asked here: it is a property of the file,
            # and `load_selected_packs` -- which the resolution above
            # already went through -- rejects an empty manifest on the
            # very revision that configured this run. Asking it again
            # from a second read would only move that window, not close
            # it (Codex review).
            #
            # Everything else is asked of the resolution, not a second
            # read of the files -- same reasoning as the compare path:
            # the resolver already loaded its own copy, so re-reading
            # would validate a revision that is not the one configuring
            # the run (Codex review, raised for compare and then here).
            check_resolved_config_applies_packs(
                resolved_config,
                contract_evaluation=contract_evaluation,
            )
            application = pack_application(resolved_config, policy_file=policy_file)
            policy_file = policy_file_with_packs(
                policy_file, application, base_policy=policy,
            )
            # Same fold `compare`'s own `resolve_and_apply` applies to its
            # `ResolvedCompareConfig` -- a no-op unless a gate pack actually
            # supplied `gate.exit_code_scheme`/`gate.severity.*`, and only
            # ever reached for a field `resolve_compare_config` left at its
            # built-in default (the resolver already exempts anything the
            # CLI/profile/`.abicheck.yml` stated from pack assignment).
            if resolved_cfg is not None:
                resolved_cfg = apply_to_compare_config(resolved_cfg, application)
    except (FieldResolutionError, PackConflictError, PackManifestError) as exc:
        # A D7 same-tier conflict or a D8 pack conflict is a usage error,
        # exactly as it is for `compare` -- not a traceback out of a
        # command that had already validated its own flags.
        #
        # Narrower than `service_scan._scan_request_config`'s
        # `except (ValueError, PackManifestError)` on purpose, not by
        # oversight: the extra case that catch covers is an unknown base
        # policy, which reaches the resolver only as a free string on a
        # typed `ScanRequest`. Here `--policy` is a `click.Choice`, so
        # Click rejects an unknown base before this call. If either front
        # end gains a new failure mode, change both.
        raise click.UsageError(str(exc)) from exc
    return resolved_config, policy_file, resolved_cfg


def _discover_scan_project_config(
    build_config: Path | None, sources: Path | None, against: Path | None,
) -> tuple[Path | None, Any, str | None]:
    """Resolve the project config for this scan, with the digest that parsed it.

    Returns ``(cfg_path, project_cfg, sha256)``. An explicitly-bound
    ``--build-config`` that cannot be parsed is a usage error; an
    auto-discovered one is best-effort and degrades to a warning with
    ``cfg_path`` cleared, matching ``merge_compile_config``'s own convention --
    a config the user never explicitly bound to shouldn't fail a run it wasn't
    asked to affect.
    """
    from .workflows.extraction import discover_build_config

    explicit_config = build_config is not None
    cfg_path = build_config if explicit_config else discover_build_config(sources)
    if cfg_path is None and not explicit_config and against is not None:
        from .cli_helpers_compare import discover_project_config

        cfg_path = discover_project_config()
    project_cfg = None
    # ADR-049 D6: a project-derived receipt entry must name the path *and*
    # prove which revision supplied the value, from the same read that
    # parsed it -- so the digest comes from `load_build_config_with_digest`
    # rather than a second read at receipt time.
    _project_sha256: str | None = None
    if cfg_path is not None:
        try:
            from .workflows.extraction import load_build_config_with_digest

            project_cfg, _project_sha256 = load_build_config_with_digest(cfg_path)
        except ValueError as exc:
            if explicit_config:
                raise click.UsageError(
                    f"cannot parse build config {cfg_path}: {exc}"
                ) from exc
            # Auto-discovered (--sources root or cwd-upward): best-effort,
            # matching `merge_compile_config`'s own identical convention --
            # a config the user never explicitly bound to shouldn't fail a
            # run it wasn't asked to affect.
            click.echo(
                f"warning: could not parse auto-discovered {cfg_path}; "
                f"using CLI compile/comparison settings only ({exc}).",
                err=True,
            )
            cfg_path = None
    return cfg_path, project_cfg, _project_sha256


@main.command("scan")
@scan_help_options  # curated --help + full --help-all (G21.8 collapse M2)
@click.argument(
    "artifact", type=click.Path(exists=True, path_type=Path), required=False
)
@artifact_set_options
@click.option(
    "-H",
    "--header",
    "header_pairs",
    multiple=True,
    type=SIDED_PATH_PARAM,
    help="Public header file or directory (repeatable). Applies to the current "
    "ARTIFACT by default; scope to the --against side with an 'old=' prefix "
    "(e.g. --header old=old/include, --header new=new/include).",
)
@click.option(
    "-I",
    "--include",
    "include_pairs",
    multiple=True,
    type=SIDED_PATH_PARAM,
    help="Additional include directory for header parsing (repeatable). Same "
    "old=/new= side-aware scoping as --header.",
)
@click.option(
    "--public-header-dir",
    "public_header_dirs",
    multiple=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Directory whose headers are public for provenance classification "
    "(repeatable). Establishes the public/internal boundary so the leakage / "
    "RTTI / exported-vs-public cross-checks run instead of skipping. A directory "
    "passed via -H also counts; a lone -H umbrella *file* cannot establish a "
    "boundary, so origins stay UNKNOWN unless a directory is given.",
)
@click.option(
    "--sources",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Source tree (compile DB auto-discovered within it).",
)
@click.option(
    "--build-info",
    "build_info",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Out-of-tree build dir / compile_commands.json / pack supplying "
    "build context.",
)
@click.option(
    "--config",
    "build_config",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Trusted project .abicheck.yml (enables build.query, auto-run when "
    "an explicitly pinned deep level needs it). Also supplies scope/"
    "suppression settings (scope.public, scope.public_symbols, "
    "suppression.strict) the same way `compare --config` does (CLI flags "
    "override); auto-discovered upward from the current directory when "
    "omitted.",
)
@click.option(
    "--build-target",
    "build_targets",
    multiple=True,
    metavar="TARGET",
    help="Explicit build-system root target(s) to scope L3 evidence "
    "collection to, instead of a workspace-wide query (P0.2; Bazel "
    "only so far, e.g. '//:math'). Repeatable -- each root's transitive "
    "dependency closure is unioned. Same flag and semantics as "
    "`dump --build-target`; CLI equivalent of `.abicheck.yml` "
    "build.targets, overrides it when both are given. Without this, a "
    "multi-package workspace with fixture/test targets alongside the "
    "real library is collected in full, which can pollute L3 evidence "
    "(and diverge from a `dump --build-target`-scoped baseline) with "
    "unrelated compile units.",
)
@click.option(
    "--against",
    "against",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Previous native library or saved ABI dump to compare ARTIFACT "
    "against (a single file -- not a directory or package; for those use "
    "`abicheck compare OLD_PACKAGE NEW_PACKAGE`). Without --against, scan "
    "runs a one-build audit/hygiene/source consistency scan only; with it, "
    "scan also compares ARTIFACT against this (the two modes are not "
    "separate flags -- --against alone selects between them).",
)
@click.option(
    "--depth",
    "depth",
    type=DEPTH_PARAM,
    default=None,
    help="Evidence depth to collect -- the single dial, named by what you get: "
    "binary (symbols only), headers (+header AST), build (+build context), "
    "source (+source replay & call graph). Omit for 'auto' (risk-driven when a "
    "--since/--changed-path seed is present, else a sensible default). "
    "--depth source uses changed-path scope when --since/--changed-path is "
    "given, else the current library target -- never a zero-TU no-op.",
)
@click.option(
    "--since",
    "since",
    default=None,
    help="Focus the scan on files changed vs a git ref (e.g. origin/main).",
)
@click.option(
    "--changed-path",
    "changed_paths_opt",
    multiple=True,
    help="Changed path to focus the scan on (repeatable; alternative to --since).",
)
@click.option(
    "--budget",
    "budget",
    default=None,
    help="Time guard (e.g. 15m); FAILS on overflow, never shrinks scope.",
)
@click.option(
    "--max-findings",
    "max_findings",
    type=click.IntRange(min=1),
    default=None,
    help="With --against: cap on findings embedded in the summary's "
    "findings/suppressed lists (default 20, or $ABICHECK_MAX_BASELINE_FINDINGS "
    "when set). Raising it never changes the verdict/exit code -- only how "
    "much of the diff the always-on summary itemizes; --format json on the "
    "full `compare` command remains the way to see everything unconditionally. "
    "When truncated, `findings_truncated_kinds`/`suppressed_truncated_kinds` "
    "still report a kind -> count breakdown of what was cut.",
)
@click.option(
    "--require-complete-analysis", "require_complete_analysis",
    is_flag=True, default=False,
    help="With --against: fail the build when analysis_assurance.status is "
    "not 'complete', independent of the compatibility verdict. Contributes "
    "exit 1, folded with max the same way the --contract coverage axis is "
    "(ADR-049 Phase 7): it raises a clean 0 to 1 and never lowers a 2/4/5/6. "
    "Mirrors `compare --require-complete-analysis` (P0.4). Without the "
    "flag, analysis_assurance is still always computed and reported in "
    "--format json, it just never affects the exit code. See "
    "docs/reference/exit-codes.md.",
)
@click.option(
    "--abi3",
    "abi3",
    default=None,
    metavar="VERSION",
    help="Audit a CPython extension against a Py_LIMITED_API floor, e.g. `3.9`. "
    "Classifies the module's imported CPython C-API against the stable ABI and "
    "flags private/unstable imports and stable symbols newer than the floor as "
    "`python_stable_abi_violation` (advisory; gate with "
    "`--crosscheck python_stable_abi_violation=error`). Requires a CPython "
    "extension module as the --binary.",
)
@click.option(
    "--dry-run",
    "dry_run",
    is_flag=True,
    default=False,
    help="Resolve and validate the invocation -- classify inputs, resolve "
    "changed paths, show the audit checks and (if --against) the comparison "
    "that would run, and print projected per-layer cost -- without scanning. "
    "Writes nothing; incompatible with -o/--output.",
)
@click.option(
    "--crosscheck",
    "crosschecks",
    multiple=True,
    help="Per-check level KEY=LEVEL (off|info|warning|error); repeatable.",
)
@click.option(
    "--risk-rules",
    "risk_rules_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Override the risk_rules profile (YAML).",
)
@policy_options  # ADR-049 Phase 5: --against config-surface parity with `compare`
@scope_options  # (--policy/--policy-file/--suppress/--scope-public-headers)
@severity_options  # With --against: --severity-preset, mirrors `compare`
@click.option(
    "--exit-code-scheme",
    "exit_code_scheme",
    type=click.Choice(["auto", "legacy", "severity"], case_sensitive=True),
    default=None,
    help="With --against: exit-code scheme (mirrors `compare --exit-code-scheme`): "
    "'legacy' (0/2/4 verdict), 'severity' (per-category error levels), or 'auto' "
    "(severity when a severity setting is in effect, else legacy). Default: "
    "config's exit_code_scheme, else auto. Not demoted to hidden, mirroring "
    "`compare`'s own visible coarse override (ADR-040 D4).",
)
@click.option(
    "--pattern-verdicts/--no-pattern-verdicts",
    "pattern_verdicts",
    default=False,
    help="With --against: modulate verdicts with idiom/anti-pattern evidence "
    "(ADR-027, mirrors `compare --pattern-verdicts`): demote opaque-pointer/"
    "PIMPL-hidden layout changes and raise breaks when an opacity/handle "
    "guarantee is lost.",
)
@env_matrix_option  # ADR-020b: --env-matrix (runtime_floors contract), --against only
@click.option(
    "--contract",
    "contract_mode",
    type=click.Choice(["public", "exports", "all", "auto"]),
    default=None,
    help="With --against: which evidence domain each finding is judged "
    "against ('public' header-derived surface, 'exports' the binary's own "
    "export table plus its type closure, 'all' every entity, 'auto' let the "
    "D7 chain below an explicit CLI value decide) -- and the flag "
    "that turns the ADR-049 contract evaluator on; omit it and nothing about "
    "the run changes. The domain decides which findings "
    "compatibility policy scores, so it can change the verdict and the exit "
    "code, and it is also what the orthogonal contract-coverage axis is "
    "answered against (mirrors `compare --contract`).",
)
@pack_option  # ADR-049 D8: --pack (requires --against; see _COMPARISON_ONLY_FLAGS)
@lang_option
# --allow-build-query removed on `scan` (CLI audit PR 5/5): scan never reaches
# the ADR-032 QUERY_BUILD_SYSTEM gate dump's --dump-manifest uses, so it only
# suppressed one advisory note. `dump`'s own --allow-build-query is untouched.
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["text", "json"]),
    default="text",
    show_default=True,
    help="Output format.",
)
@click.option(
    "--show-suppressed",
    "show_suppressed",
    is_flag=True,
    default=False,
    help="With --against and --format text: itemize the suppressed findings "
    "(kind/symbol/location/rule) below the always-present `suppressed: N` "
    "count, the same way gating findings are itemized. --format json already "
    "lists every suppressed finding unconditionally in `diff.suppressed`; "
    "this flag only changes what the text renderer prints.",
)
@click.option(
    "-o",
    "--output",
    type=click.Path(path_type=Path),
    default=None,
    help="Write output to this path (default: stdout).",
)
@secondary_output_options(
    ["text", "json"],
    format_help="Emit a second output format from this same scan run, to its "
    "own file, without re-running it (e.g. a human --format text report "
    "alongside --write json=scan.json for tooling -- the GitHub Action's own "
    "PR-comment renderer uses exactly this to avoid a second, potentially "
    "--depth build/source-expensive scan for the default --format text "
    "invocation). FORMAT is one of {formats}; PATH must differ from "
    "--output/-o. Not supported with --artifact-set.",
)
@verbose_option
@compile_context_options()  # dump↔scan L2 compile-context parity (ADR-037 D3)
def scan_cmd(
    artifact: Path | None,
    artifact_set: tuple[str, ...],
    bundle_system_providers: str,
    header_pairs: tuple[tuple[str, Path], ...],
    include_pairs: tuple[tuple[str, Path], ...],
    public_header_dirs: tuple[Path, ...],
    sources: Path | None,
    build_info: Path | None,
    build_config: Path | None,
    build_targets: tuple[str, ...],
    against: Path | None,
    depth: str | None,
    since: str | None,
    changed_paths_opt: tuple[str, ...],
    budget: str | None,
    max_findings: int | None,
    require_complete_analysis: bool,
    abi3: str | None,
    dry_run: bool,
    crosschecks: tuple[str, ...],
    risk_rules_path: Path | None,
    suppress: Path | None,
    policy_file_path: Path | None,
    policy: str,
    scope_public_headers: bool,
    severity_preset: str | None,
    show_suppressed: bool,
    exit_code_scheme: str | None,
    pattern_verdicts: bool,
    env_matrix_path: Path | None,
    contract_mode: str | None,
    pack_paths: tuple[Path, ...],
    lang: str,
    fmt: str,
    output: Path | None,
    secondary_fmt: str | None,
    secondary_output: Path | None,
    verbose: bool,
    # --allow-build-query no longer exists as a scan CLI option (CLI audit
    # PR 5/5); this defaulted-False parameter stays only so
    # resolve_effective_allow_query/_run_artifact_set's own signatures don't
    # need to change (see cli_scan.py's --allow-build-query removal comment
    # above scan_cmd's decorators for why removing the flag was safe here).
    allow_build_query: bool = False,
    header_backend: str = "auto",
    gcc_options: str | None = None,
    compiler_path: str | None = None,
    compiler_prefix: str | None = None,
    compiler_option_tokens: tuple[str, ...] = (),
    sysroot: Path | None = None,
    nostdinc: bool = False,
    frontend_context: str = "host",
) -> None:
    """Deterministic source-intelligence scan (classify → always-on tier → level).

    One orchestrator over `dump`/`compare`: classifies the PR's changed paths,
    runs the always-on compiler-free pattern pre-scan and the intra-version
    cross-source checks, then runs the pinned evidence level (the `--depth`
    dial, or `auto` when omitted) and — when `--against` is given — compares
    ARTIFACT against it. Emits one coverage-annotated report. Absence of
    `--against` already means a one-build audit; it is not a separate mode flag.

    \b
    Exit codes (legacy scheme — the default):
      0  compatible (or advisory-only findings)
      1  incomplete contract coverage (ADR-049 Phase 7): the selected
         --contract domain's required evidence could not be closed.
         Orthogonal — folded with max, so it raises a clean 0 and never
         lowers a 2/4, and it never changes the compatibility verdict.
         Only reachable with --contract
      2  source-level / API break (incl. API_BREAK cross-source findings)
      4  ABI break (from the --against comparison)
      5  --budget overflow
      6  NOT_COMPARABLE (ADR-050 D2): ARTIFACT and --against were not
         extracted under a comparable profile/scope contract

    \b
    With --against, --severity-preset/--exit-code-scheme (or
    .abicheck.yml's severity:/exit_code_scheme) select the severity-aware
    scheme instead, exactly as for `compare`: the 0/2/4 codes above are then
    computed from the per-category error levels rather than the verdict, so
    --severity-preset info-only can exit 0 on a breaking comparison, and an
    error-level addition or quality finding can exit 1 on an otherwise
    compatible one. The report states which — a `severity gate:` line in the
    text output, a `diff.severity` block in --format json. 5/6 are unaffected
    (both are decided before the comparison runs).

    \b
    Examples:
      abicheck scan new/libfoo.so --header new/include \\
                    --sources . --against old/libfoo.abi.json
      abicheck scan libfoo.so --header include/
      abicheck scan new.so -H include/ --depth source --since origin/main
    """
    from .dry_run import reject_dry_run_with_output
    from .workflows.extraction import is_package

    _setup_verbosity(verbose)

    # ADR-056: --artifact-set is mutually exclusive with the positional
    # ARTIFACT, with --against (audit-only -- no old side for a set), and
    # --bundle-system-providers is meaningless without --artifact-set.
    #
    # --artifact-set is now a repeatable option (CLI cleanup phase two, PR
    # 5): `artifact_set` is the tuple Click collects, empty when unset, so
    # "supplied" is exactly `bool(artifact_set)` -- a bare `--artifact-set
    # ""` is still the truthy `("",)`, correctly "supplied" and rejected by
    # `reject_incoherent_scan_operands`'s own empty-member check. The old
    # comma-string form needed a `bool()`/`is not None` distinction here
    # because an empty *string* was falsy but not `None`, which is what let
    # ARTIFACT and an empty --artifact-set both pass exclusivity and
    # silently resolve to `Path("") == Path(".")` (CodeRabbit review,
    # historical) -- a tuple has no such falsy-but-present state.
    _reject_incoherent_scan_operands(
        artifact=artifact, artifact_set=artifact_set, against=against,
        bundle_system_providers=bundle_system_providers,
    )
    _reject_incoherent_secondary_output(
        dry_run=dry_run, output=output, secondary_fmt=secondary_fmt,
        secondary_output=secondary_output, artifact_set=artifact_set,
    )
    if artifact_set:
        reject_dry_run_with_output(dry_run, output)
        _reject_comparison_only_flags(no_baseline_reason="drop --artifact-set")
        _run_artifact_set(
            artifact_set=artifact_set,
            dry_run=dry_run,
            bundle_system_providers=bundle_system_providers,
            header_pairs=header_pairs,
            include_pairs=include_pairs,
            public_header_dirs=public_header_dirs,
            sources=sources,
            build_info=build_info,
            build_config=build_config,
            build_targets=build_targets,
            depth=depth,
            since=since,
            changed_paths_opt=changed_paths_opt,
            budget=budget,
            abi3=abi3,
            crosschecks=crosschecks,
            risk_rules_path=risk_rules_path,
            lang=lang,
            allow_build_query=allow_build_query,
            fmt=fmt,
            output=output,
            header_backend=header_backend,
            gcc_options=gcc_options,
            compiler_path=compiler_path,
            compiler_prefix=compiler_prefix,
            compiler_option_tokens=compiler_option_tokens,
            sysroot=sysroot,
            nostdinc=nostdinc,
            frontend_context=frontend_context,
        )
        return
    # The mutual-exclusion check above already guarantees exactly one of
    # ARTIFACT/--artifact-set is set, and the --artifact-set branch always
    # returns -- so `artifact` is non-None on every path reaching here.
    # Narrows for mypy, which can't see that across the early return.
    assert artifact is not None

    reject_dry_run_with_output(dry_run, output)
    # --against's help text documents "a single file -- not a directory or
    # package", but `dir_okay=False` on the option itself only rejects
    # directories -- a package archive (.deb/.rpm/.tar.gz/...) still passes
    # Click validation and previously reached resolve_input(), which cannot
    # extract packages, so it failed later with an opaque "cannot detect
    # input format" instead of a clear, immediate usage error (Codex
    # review). Checked before the --dry-run branch so dry-run and the real
    # run agree.
    if against is not None and is_package(against):
        raise click.UsageError(
            f"--against does not accept a package archive ({against}); "
            "packages are not supported here -- use `abicheck compare "
            "OLD_PACKAGE NEW_PACKAGE` for package-to-package comparisons."
        )
    start = time.monotonic()

    # Side-aware --header/--include (ADR-040): a bare value applies to both the
    # current ARTIFACT and the --against side; old=/new= scope to one side.
    header_both, header_old, header_new = split_sided_paths(header_pairs)
    include_both, include_old, include_new = split_sided_paths(include_pairs)
    headers = tuple(header_both) + tuple(header_new)
    includes = tuple(include_both) + tuple(include_new)
    baseline_header = tuple(header_both) + tuple(header_old)
    baseline_include = tuple(include_both) + tuple(include_old)

    # ADR-049 Phase 5 review (Codex, PR #657 P1): resolve the project config
    # PATH + object ONCE, upfront, so the same .abicheck.yml feeds both its
    # `compile:` block (via `resolve_compile_context`, below) and its scope/
    # suppression settings (further below) -- previously `resolve_compile_
    # context` had no cwd-upward discovery of its own, so a config found only
    # by walking up from cwd never fed `compile.defines`/include dirs/
    # frontend/std/sysroot anywhere even though its scope/suppression
    # settings were applied: a macro- or dialect-dependent header API could
    # then parse under the wrong context and produce a false COMPATIBLE
    # verdict. All error handling lives here (not duplicated in
    # `resolve_compile_context`/`resolve_compare_config`'s own callers) so
    # `cfg_path` passed to `resolve_compile_context` below is always either
    # `None` or a path already confirmed to parse.
    #
    # Precedence matches `merge_compile_config`'s own convention for the
    # `compile:` block (explicit --config > --sources tree root); the
    # cwd-upward fallback is scan-specific and, per the "reject comparison-
    # only flags without --against" guard above, only attempted for an
    # actual `--against` comparison -- a plain one-build audit never needs
    # any of this, so it must not even try the cwd-upward walk.
    cfg_path, project_cfg, _project_sha256 = _discover_scan_project_config(
        build_config, sources, against
    )

    # L2 header compile context (compare↔dump↔scan parity, ADR-037 D3): the one
    # shared resolver bundles the cross-toolchain + frontend flags and folds the
    # project's `.abicheck.yml` compile: block in (CLI > config; `cfg_path`
    # above is the exact same config the scope/suppression resolution below
    # reads from, and is only ever `None` or already known to parse cleanly).
    compile_context, includes_tuple = resolve_compile_context(
        click.get_current_context(),
        gcc_options=gcc_options,
        sysroot=sysroot,
        nostdinc=nostdinc,
        header_backend=header_backend,
        includes=tuple(includes),
        build_config=cfg_path,
        sources=sources,
        frontend_context=frontend_context,
        compiler_path=compiler_path,
        compiler_prefix=compiler_prefix,
        compiler_option_tokens=compiler_option_tokens,
    )
    includes = includes_tuple
    binary = artifact
    baseline = against

    # ADR-049 Phase 5 review (Codex, PR #657): every flag below only means
    # anything for a --against comparison -- `run_scan_core` calls
    # `_run_baseline_compare` only when a baseline is given, so without
    # --against these would otherwise be silently parsed, validated (a
    # malformed --env-matrix would still fail an unrelated one-build audit),
    # and then discarded -- hiding e.g. a --policy-file require_evidence
    # setting the user actually needed. Reject them explicitly rather than
    # accepting no-op configuration.
    if against is None:
        _reject_comparison_only_flags(no_baseline_reason="pass --against")

    # ADR-049 Phase 5 review (Codex, PR #657): resolve scope/suppression
    # settings through the project's `.abicheck.yml` the same way `compare`
    # does (CLI > config > default, ADR-037 D4) -- reusing `compare`'s own
    # `resolve_compare_config`, and the exact same already-loaded
    # `project_cfg` the compile-context resolution above used, rather than
    # reading raw CLI values only or re-discovering independently. Without
    # this, a project config's `suppression.strict`/`scope.public`/
    # `scope.public_symbols`/`scope.collapse_versioned_symbols`/
    # `suppression.require_justification` applied to `compare` but silently
    # had no effect on `scan --against`. Severity + exit-code-scheme config
    # keys are resolved here too and now DO feed `scan --against`'s own exit
    # code the same way `compare`'s does (see `sev_config`/`resolved_exit_
    # scheme` below) -- previously they were required positional args of the
    # shared function but deliberately discarded, which meant
    # `.abicheck.yml`'s `severity:`/`exit_code_scheme` block silently had no
    # effect on `scan --against`, unlike `compare`.
    #
    # Gated on `against is not None`: every field this resolves only means
    # anything for a baseline comparison (mirrors the "reject comparison-only
    # flags without --against" guard above) -- skipping it for a plain
    # one-build audit means the (already-loaded, possibly None) config never
    # affects an audit-only run.
    collapse_versioned_symbols = False
    require_justification = False
    # Config-only now that the hidden --strict-suppressions/--public-symbol/
    # --public-symbols-list trio is gone, and resolved only for a baseline
    # comparison (the block below) -- an audit-only run has no gate for them
    # to configure, so they keep their built-in defaults.
    strict_suppressions = False
    public_symbols: tuple[str, ...] = ()
    sev_config = None
    resolved_exit_scheme = "legacy"
    # What `--dry-run` previews as the exit-code contract. Defaults match an
    # audit-only run, which has no baseline comparison and therefore no gate.
    scheme_label = "legacy (0/2/4)"
    sev_config_for_preview = None
    # None for an audit-only run (no --against): there is no
    # `resolve_compare_config` result to fold a gate pack into, matching
    # `_resolve_scan_evaluation_config`'s own early return for that case.
    resolved_cfg = None
    if against is not None:
        from .cli_helpers_compare import resolve_compare_config

        resolved_cfg = resolve_compare_config(
            project_cfg,
            cli_severity_preset=severity_preset,
            cli_scope_public=_cli_flag("scope_public_headers", scope_public_headers),
            cli_exit_code_scheme=exit_code_scheme,
        )
        scope_public_headers = resolved_cfg.scope_public
        strict_suppressions = resolved_cfg.strict_suppressions
        public_symbols = tuple(resolved_cfg.public_symbols)
        # Every one of these is config-only now -- `scan` never had a
        # --collapse-versioned-symbols/--require-justification flag, and the
        # --strict-suppressions/--public-symbol/--public-symbols-list trio it
        # did carry were hidden duplicates of the same config keys and have
        # been removed, so this is `resolved_cfg`'s config > default
        # resolution with no CLI override to consider.
        collapse_versioned_symbols = resolved_cfg.collapse_versioned_symbols
        require_justification = resolved_cfg.require_justification
        # Mirrors `compare`'s own `sev_config = resolved_cfg.severity` (a gate
        # pack may have moved a severity level; `resolved_cfg` already carries
        # that) -- fed to `run_scan_core` below so the baseline comparison's
        # exit code honors the same severity/exit-code-scheme contract as
        # `compare`, closing the asymmetry documented in AGENTS.md's "Known
        # gaps" (scan previously computed its exit code from the verdict
        # alone, never consulting severity).
        sev_config = resolved_cfg.severity
        resolved_exit_scheme = resolved_cfg.exit_code_scheme

    # ADR-049 Phase 5: --against reuses `compare`'s own suppression/policy
    # loader (`_load_suppression_and_policy`) so a scan baseline comparison
    # can be scoped/suppressed/policy-classified the same way a direct
    # `compare` run is, instead of the previously-hardcoded
    # policy="strict_abi"/suppression=None. A no-op (returns (None, None))
    # when neither --suppress nor --policy-file is given, so a plain `scan
    # --against` invocation with none of these flags behaves exactly as
    # before.
    suppression, policy_file = _load_suppression_and_policy(
        suppress,
        policy,
        policy_file_path,
        strict_suppressions=strict_suppressions,
        require_justification=require_justification,
    )

    # ADR-049 Phase 5 §6.4: --against also reuses `compare`'s own force-public
    # overlay (--public-symbol/--public-symbols-list) and env-matrix
    # (--env-matrix) config surface, both of which `compare_snapshots` already
    # accepts as plain kwargs (`force_public_symbols`/`env_matrix`) -- no new
    # engine capability, just CLI/service plumbing parity.
    from .cli_helpers_compare import resolve_force_public_scope

    force_public_symbols, _symbols_list = resolve_force_public_scope(
        public_symbols, None
    )
    _warn_force_public_ignored(force_public_symbols, scope_public_headers)

    # --contract is the only way to ask for the ADR-049 evaluator on the CLI
    # (abicheck.cli_options.resolve_contract_evaluation), mirroring
    # `compare`'s own resolution in `cli_compare_helpers.run_compare` --
    # resolved here, before contract_evaluation is used for anything else
    # in this function. The Tier-2 entry's (`service._validate_contract_mode`)
    # explicit-only contract stays untouched for direct Python API callers.
    contract_evaluation = resolve_contract_evaluation(contract_mode)
    contract_mode = resolve_contract_domain(
        contract_mode, click.get_current_context()
    )

    from .errors import AbicheckError
    from .service import load_env_matrix

    try:
        env_matrix = load_env_matrix(env_matrix_path)
    except AbicheckError as exc:
        raise click.UsageError(str(exc)) from exc

    # ADR-049 Phase 5's "same typed config", for the third and last front
    # end. `checker.compare` can only claim `API_REQUEST` for arguments it
    # was handed, so without this the scan's persisted `evaluation_context`
    # carried the core verb's reconstruction rather than real D7 provenance
    # -- the same defect already fixed for `compare` and the MCP tool.
    # Resolved here because this is where the Click context is: which flags
    # the user actually typed is a question only the front end can answer.
    resolved_config, policy_file, resolved_cfg = _resolve_scan_evaluation_config(
        against=against, contract_evaluation=contract_evaluation,
        pack_paths=pack_paths, policy=policy, policy_file=policy_file,
        project_cfg=project_cfg, cfg_path=cfg_path,
        project_sha256=_project_sha256,
        suppression=suppression, suppress=suppress, symbols_list=_symbols_list,
        resolved_cfg=resolved_cfg,
    )
    # A selected gate pack may have just moved `resolved_cfg`'s severity/
    # exit-code-scheme (CLI cleanup phase two, "PR B") -- re-derive the
    # values `run_scan_core` below actually gates on from the (possibly
    # pack-folded) config, the same "read the resolved value, never
    # re-derive one" rule `pack_application.apply_to_compare_config`'s own
    # docstring states. A no-op when `resolved_cfg` is `None` (audit-only)
    # or no pack touched it.
    if resolved_cfg is not None:
        sev_config = resolved_cfg.severity
        resolved_exit_scheme = resolved_cfg.exit_code_scheme
        # `scheme_label`/`sev_config_for_preview` (what `--dry-run` prints)
        # are derived from THIS, possibly pack-folded, `resolved_cfg`
        # (Codex review: computing them earlier, before this fold, left
        # `scan --dry-run` describing the pre-pack scheme). `pack_paths` is
        # deliberately NOT passed here (unlike `compare`'s own call site,
        # where the pack genuinely isn't resolved yet) -- by this point the
        # pack is already folded, so the "a selected --pack may adjust it"
        # caveat would self-contradict the label it's attached to.
        from .cli_compare_receipt import dry_run_scheme_label

        scheme_label = dry_run_scheme_label(resolved_cfg, ())
        # Only a severity-scheme run has a gate to describe; under `legacy`
        # the severity values still resolve but never score anything, so
        # previewing them would imply a gate the run will not run.
        if resolved_exit_scheme == "severity":
            sev_config_for_preview = sev_config

    budget_s = _parse_budget(budget)
    enabled_checks, severities = _parse_crosschecks(crosschecks)

    changed, changed_src, seeded = _resolve_changed_seed(
        changed_paths_opt, since, sources
    )

    risk_rules = _load_risk_rules(risk_rules_path)
    risk = score_changed_paths(changed, risk_rules)

    # Absence of --against is already the one-build audit; presence of --against
    # is already the compare-too mode. Neither is a separate mode flag (ADR-043).
    scan_mode = ScanMode.AUDIT if against is None else ScanMode.PR
    # --abi3: the target Py_LIMITED_API floor for the stable-ABI audit; None off.
    abi3_floor = _parse_abi3_floor(abi3)
    # S2 (preprocessor macro/include capture) is collected by the conditional S2
    # tier (`preprocessor_scan.run_preprocessor_scan`) over the L3 build evidence;
    # it maps to the L3 `build` collect mode and the always-on tier runs the
    # preprocessor pass when a compile DB + `clang -E` are available (else the
    # coverage row reports it skipped — ADR-035 D2 coverage honesty).
    dp = EvidenceDepth(depth) if depth else None
    # The unset dial means 'auto' (ADR-037 D5): opt into the risk-driven S-method
    # so a seeded scan escalates by risk and an unseeded one falls back to the
    # preset. Only when --depth was omitted entirely -- a pinned rung stays
    # deterministic.
    sm, is_auto, auto_method = _resolve_auto_source_method(
        None, dp, False, seeded, risk
    )
    resolved, eff_depth_enum = resolve_level(
        mode=scan_mode,
        source_method=sm,
        depth=dp,
        auto_method=auto_method,
    )
    # collect_mode and reported depth come from the resolved (method, depth)
    # level. The S5 (source) replay scope is command-aware (ADR-043 D3): a valid
    # change seed (--since/--changed-path) scopes to CHANGED, otherwise TARGET --
    # the current library target, never a zero-TU no-op, whether --depth source
    # was pinned explicitly or reached via the auto/PR preset.
    collect_mode = level_to_collect_mode(
        resolved,
        eff_depth_enum,
        source_scope=SourceScope.CHANGED if seeded else SourceScope.TARGET,
    )
    headers, baseline_header, sources, build_info = _normalize_depth_inputs(
        eff_depth_enum, headers, baseline_header, sources, build_info,
    )
    effective_build_info = build_info

    if dry_run:
        from .dry_run import emit_dry_run
        from .frontends.cli.scan_dry_run import render_scan_dry_run
        from .service_scan import Budget, ScanRequest, estimate_scan

        # Computed here, not inside render_scan_dry_run: that module is a
        # canonical frontends/cli/ file, which must not import service_scan
        # directly -- doing so once already grew the large, already-accepted
        # CLI-registration import cycle (AI-readiness import-cycle-growth,
        # fresh evidence), the same reason artifact_set_dry_run.py takes its
        # own totals/notes as already-computed data instead of calling
        # estimate_artifact_set itself.
        try:
            estimate_req = ScanRequest(
                binaries=[artifact],
                headers=list(headers),
                includes=list(includes),
                sources=sources,
                build_info=effective_build_info,
                mode="pr",
                source_method=resolved.value,
                depth=eff_depth_enum.value,
                changed_paths=list(changed),
                seeded=seeded,
                budget=Budget(total_timeout=budget_s),
                lang=lang,
                build_targets=build_targets,
                build_config=build_config,
            )
            estimates = estimate_scan(
                estimate_req, resolved_level=(resolved, eff_depth_enum)
            )
            estimate_error = None
        except Exception as exc:  # pragma: no cover - best-effort probe
            estimates = None
            estimate_error = str(exc)

        emit_dry_run(
            render_scan_dry_run(
                artifact=artifact,
                against=against,
                sources=sources,
                effective_build_info=effective_build_info,
                changed=changed,
                changed_src=changed_src,
                seeded=seeded,
                depth=depth,
                eff_depth_enum=eff_depth_enum,
                resolved=resolved,
                collect_mode=collect_mode,
                header_backend=header_backend,
                fmt=fmt,
                build_targets=build_targets,
                scheme_label=scheme_label,
                sev_config=sev_config_for_preview,
                abi3_floor=abi3_floor,
                estimates=estimates,
                estimate_error=estimate_error,
            )
        )

    # --- run the engine core (the shared orchestration; ADR-035 D10) ----------
    # The classify→tier→level→compare body lives in ``run_scan_core`` so the CLI,
    # ``service.run_scan``, and the MCP tool drive one engine. The CLI only parses
    # argv, renders, and maps the budget-overflow signal onto an exit code.
    # An explicit --depth both consents to auto-running build.query
    # (level-implies-query) and pins the auto-strict evidence contract; with no
    # --mode/--source-method left on the public CLI, the two notions collapse to
    # one boolean.
    _level_explicit, _pinned_explicit = _scan_explicit_flags(None, depth)
    prov_headers, prov_dirs = _public_provenance_set(
        list(headers), list(public_header_dirs)
    )
    # Cleanup thunks for any out-of-tree inferred cmake build dir, owned here so the
    # dir outlives every scan phase that re-uses a compile unit's `directory` as a
    # cwd — the S2 preprocessor scan runs `clang -E` there. collect_inline_pack
    # would otherwise delete it as soon as L4 finished, before that scan ran (and
    # before any post-snapshot raise). Run in the finally below, on every exit path.
    build_dir_cleanups: list[Callable[[], None]] = []
    try:
        core = run_scan_core(
            start=start,
            binary=binary,
            headers=list(headers),
            includes=list(includes),
            public_headers=prov_headers,
            public_header_dirs=prov_dirs,
            sources=sources,
            effective_build_info=effective_build_info,
            build_config=build_config,
            baseline=baseline,
            baseline_headers=list(baseline_header),
            baseline_includes=list(baseline_include),
            lang=lang,
            allow_build_query=allow_build_query,
            scan_mode=scan_mode,
            resolved=resolved,
            eff_depth_enum=eff_depth_enum,
            collect_mode=collect_mode,
            changed=changed,
            changed_src=changed_src,
            seeded=seeded,
            risk=risk,
            is_auto=is_auto,
            enabled_checks=enabled_checks,
            severities=severities,
            budget=budget,
            budget_s=budget_s,
            # A concrete explicit level is what consents to level-implies-query
            # auto-running build.query: a non-auto --source-method, or --depth ONLY
            # when no --source-method is given (resolve_level gives --source-method
            # precedence and ignores --depth otherwise, so `auto`+`--depth` resolves
            # via auto/the preset, not the depth — it must not count as consent;
            # Codex review). An explicit --mode is deliberately NOT consent here.
            level_explicit=_level_explicit,
            # The pinned-depth contract (auto-strict) gates on the *deliberate* new
            # surface only — an explicit --depth (even alongside --source-method
            # auto) or a non-auto --source-method. An explicit --mode is NOT a pin:
            # it is a deprecated *preset* alias (pr/pr-deep/baseline/audit, all deep
            # by collect-mode) that the GitHub Action passes by default (`--mode pr`)
            # and that `--mode audit` uses for a binary-only lint — treating it as a
            # pin would break those best-effort paths (Codex review).
            pinned_explicit=_pinned_explicit,
            suppression=suppression,
            policy=policy,
            policy_file=policy_file,
            scope_to_public_surface=scope_public_headers,
            force_public_symbols=force_public_symbols,
            pattern_verdicts=pattern_verdicts,
            env_matrix=env_matrix,
            collapse_versioned_symbols=collapse_versioned_symbols,
            contract_evaluation=contract_evaluation,
            contract_mode=contract_mode,
            resolved_config=resolved_config,
            sev_config=sev_config,
            exit_code_scheme=resolved_exit_scheme,
            compile_context=None if compile_context.is_default else compile_context,
            defer_cleanup=build_dir_cleanups,
            abi3_floor=abi3_floor,
            max_findings=max_findings,
            require_complete_analysis=require_complete_analysis,
            build_targets=build_targets,
        )
    except _BudgetOverflow as bo:
        click.echo(bo.message, err=True)
        _emit_scan_abort_report(
            "budget_overflow",
            fmt,
            output,
            prior_decision=bo.prior_decision,
            secondary_fmt=secondary_fmt,
            secondary_output=secondary_output,
        )
        sys.exit(_EXIT_BUDGET_OVERFLOW)
    except _EvidenceContractError as ce:
        # A pinned depth that can't collect its evidence is a usage contract
        # violation → a clean CLI error (exit 1), distinct from the verdict codes
        # (2/4) and the budget code (5).
        _emit_scan_abort_report(
            "evidence_contract_error",
            fmt,
            output,
            secondary_fmt=secondary_fmt,
            secondary_output=secondary_output,
        )
        raise click.ClickException(ce.message) from ce
    finally:
        # Remove the inferred cmake build dir(s) now that every build-dir-dependent
        # phase has run (or the scan aborted). Best-effort (each thunk is suppressed)
        # so a removal/unlock error never aborts the rest nor masks the real outcome.
        from .workflows.extraction import drain_build_dir_cleanups

        drain_build_dir_cleanups(build_dir_cleanups)

    _emit_scan_report(
        core.outcome,
        fmt,
        output,
        show_suppressed=show_suppressed,
        secondary_fmt=secondary_fmt,
        secondary_output=secondary_output,
    )
