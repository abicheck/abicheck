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
from typing import Any

import click

from .buildsource.crosscheck import (  # noqa: F401 - CrosscheckConfig/run_crosschecks re-exported for tests
    ALL_CHECKS,
    CrosscheckConfig,
    run_crosschecks,
)
from .buildsource.pattern_scan import scan_files  # noqa: F401 - re-export for tests
from .buildsource.poi import (  # noqa: F401 - re-export for tests
    build_points_of_interest,
    resolve_symbol_tus,
)
from .buildsource.preprocessor_scan import (
    run_preprocessor_scan,  # noqa: F401 - re-export for tests
)
from .buildsource.risk import RiskScore, score_changed_paths
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
from .cli_options import (
    compile_context_options,
    lang_option,
    merge_compile_config,
    resolve_compile_context,
    split_sided_paths,
    verbose_option,
)
from .cli_params import DEPTH_PARAM, SIDED_PATH_PARAM
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
    compile_db: Path | None,
) -> tuple[tuple[Path, ...], tuple[Path, ...], Path | None, Path | None, Path | None]:
    """Prune inputs that would collect evidence above the effective scan depth."""
    if depth is not EvidenceDepth.BINARY:
        return headers, baseline_header, sources, build_info, compile_db
    return (), (), None, None, None


def _render_text(out: ScanOutcome) -> str:
    """Render the human-facing scan report by composing its section blocks."""
    lines: list[str] = []
    lines += render_summary_lines(out)
    lines += render_coverage_lines(out)
    lines += render_crosscheck_lines(out)
    lines += render_pattern_lines(out)
    lines += render_preprocessor_lines(out)
    lines += render_baseline_lines(out)
    lines += render_verdict_lines(out)
    return "\n".join(lines)


def _resolve_changed_seed(
    changed_paths_opt: tuple[str, ...], since: str | None, sources: Path | None,
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
    source_method: str | None, depth: str | None,
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


def render_scan_dry_run(
    *,
    artifact: Path,
    against: Path | None,
    headers: list[Path],
    includes: list[Path],
    sources: Path | None,
    effective_build_info: Path | None,
    changed: list[str],
    changed_src: str,
    seeded: bool,
    depth: str | None,
    eff_depth_enum: EvidenceDepth,
    resolved: SourceMethod,
    collect_mode: str,
    budget_s: float | None,
    lang: str,
    header_backend: str,
    fmt: str,
) -> Any:
    """Build the ``scan --dry-run`` report (ADR-043 D4): resolve, never scan.

    Reuses :func:`service.estimate_scan`'s per-layer cost/TU-count probe (the
    same read-only projection ``--estimate`` used to provide) so the report
    also states how many translation units the resolved level would touch.
    """
    from .dry_run import DryRunResult, tool_status
    from .service import Budget, ScanRequest, estimate_scan

    result = DryRunResult(command="scan")
    result.add(
        "Inputs",
        f"artifact: {artifact}",
        f"against: {against}" if against else "against: (none -- one-build audit only)",
    )
    scope_label = "changed" if seeded else "target"
    result.add(
        "Resolved depth and source scope",
        f"requested depth: {depth or '(auto)'}",
        f"effective collect mode: {collect_mode}",
        f"source scope: {scope_label}" if resolved.value == "s5" else None,
        f"changed paths ({changed_src}): {len(changed)}",
    )
    result.add("Headers and compile context", f"ast-frontend: {header_backend}")
    result.add(
        "Build/source inputs",
        f"--sources: {sources}" if sources else None,
        f"--build-info: {effective_build_info}" if effective_build_info else None,
    )
    result.add("Tools and frontends", *tool_status("castxml", "clang", "gcc", "g++"))
    result.add(
        "Consumer/contract scoping",
        "audit checks: always run (pattern pre-scan + intra-version cross-source)",
        "compatibility comparison: will run against --against"
        if against is not None
        else "compatibility comparison: will NOT run (no --against)",
    )
    result.add(
        "Output and exit-code behavior",
        f"format: {fmt}",
        "dry-run exit codes: 0 valid, 1 requested depth not satisfiable, "
        "64 usage error (a real scan run's exit codes are 0 compatible, "
        "2 API break, 4 ABI break, 5 budget overflow)",
    )
    try:
        req = ScanRequest(
            binaries=[artifact], headers=headers, includes=includes,
            sources=sources, build_info=effective_build_info,
            mode="pr", source_method=resolved.value, depth=eff_depth_enum.value,
            changed_paths=list(changed), seeded=seeded,
            budget=Budget(total_timeout=budget_s), lang=lang,
        )
        estimates = estimate_scan(req, resolved_level=(resolved, eff_depth_enum))
        total = sum(e.est_seconds for e in estimates)
        result.add(
            "Resolved depth and source scope",
            *(
                f"{e.layer}: {e.tus} TU(s), ~{e.est_seconds:.2f}s -- {e.note}"
                for e in estimates
            ),
            f"projected total: {total:.2f}s",
        )
    except Exception as exc:  # pragma: no cover - best-effort probe
        result.warn(f"could not project per-layer cost: {exc}")
    return result


def _emit_scan_report(outcome: ScanOutcome, fmt: str, output: Path | None) -> None:
    """Render the scan outcome, write/echo it, and exit non-zero on a verdict."""
    text = (
        json.dumps(outcome.to_dict(), indent=2)
        if fmt == "json"
        else _render_text(outcome)
    )
    if output:
        _safe_write_output(output, text)
        click.echo(f"Report written to {output}", err=True)
    else:
        click.echo(text)

    if outcome.exit_code != 0:
        sys.exit(outcome.exit_code)


@main.command("scan")
@click.argument("artifact", type=click.Path(exists=True, path_type=Path))
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
    "--compile-db",
    "compile_db",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Explicit compile_commands.json (use when not under --sources).",
)
@click.option(
    "--config",
    "build_config",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Trusted project .abicheck.yml (enables build.query with "
    "--allow-build-query).",
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
@lang_option
@click.option(
    "--allow-build-query",
    is_flag=True,
    default=False,
    hidden=True,  # deprecated no-op: build query runs automatically with --sources
    help="Deprecated and ignored. With --sources, abicheck infers and runs the "
    "build-system query (cmake/make/bazel) itself; no flag is needed.",
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["text", "json"]),
    default="text",
    show_default=True,
    help="Output format.",
)
@click.option("-o", "--output", type=click.Path(path_type=Path), default=None,
              help="Write output to this path (default: stdout).")
@verbose_option
@compile_context_options  # dump↔scan L2 compile-context parity (ADR-037 D3)
def scan_cmd(
    artifact: Path,
    header_pairs: tuple[tuple[str, Path], ...],
    include_pairs: tuple[tuple[str, Path], ...],
    public_header_dirs: tuple[Path, ...],
    sources: Path | None,
    build_info: Path | None,
    compile_db: Path | None,
    build_config: Path | None,
    against: Path | None,
    depth: str | None,
    since: str | None,
    changed_paths_opt: tuple[str, ...],
    budget: str | None,
    abi3: str | None,
    dry_run: bool,
    crosschecks: tuple[str, ...],
    risk_rules_path: Path | None,
    lang: str,
    allow_build_query: bool,
    fmt: str,
    output: Path | None,
    verbose: bool,
    header_backend: str = "auto",
    gcc_path: str | None = None,
    gcc_prefix: str | None = None,
    gcc_options: str | None = None,
    gcc_option_tokens: tuple[str, ...] = (),
    sysroot: Path | None = None,
    nostdinc: bool = False,
) -> None:
    """Deterministic source-intelligence scan (classify → always-on tier → level).

    One orchestrator over `dump`/`compare`: classifies the PR's changed paths,
    runs the always-on compiler-free pattern pre-scan and the intra-version
    cross-source checks, then runs the pinned evidence level (the `--depth`
    dial, or `auto` when omitted) and — when `--against` is given — compares
    ARTIFACT against it. Emits one coverage-annotated report. Absence of
    `--against` already means a one-build audit; it is not a separate mode flag.

    \b
    Exit codes:
      0  compatible (or advisory-only findings)
      2  source-level / API break (incl. API_BREAK cross-source findings)
      4  ABI break (from the --against comparison)
      5  --budget overflow

    \b
    Examples:
      abicheck scan new/libfoo.so --header new/include \\
                    --sources . --against old/libfoo.abi.json
      abicheck scan libfoo.so --header include/
      abicheck scan new.so -H include/ --depth source --since origin/main
    """
    from .dry_run import reject_dry_run_with_output

    reject_dry_run_with_output(dry_run, output)
    _setup_verbosity(verbose)
    start = time.monotonic()

    # Side-aware --header/--include (ADR-040): a bare value applies to both the
    # current ARTIFACT and the --against side; old=/new= scope to one side.
    header_both, header_old, header_new = split_sided_paths(header_pairs)
    include_both, include_old, include_new = split_sided_paths(include_pairs)
    headers = tuple(header_both) + tuple(header_new)
    includes = tuple(include_both) + tuple(include_new)
    baseline_header = tuple(header_both) + tuple(header_old)
    baseline_include = tuple(include_both) + tuple(include_old)

    # L2 header compile context (compare↔dump↔scan parity, ADR-037 D3): the one
    # shared resolver bundles the cross-toolchain + frontend flags and folds the
    # project's `.abicheck.yml` compile: block in (CLI > config; the config is
    # --config or the one auto-discovered at the --sources root).
    compile_context, includes_tuple = resolve_compile_context(
        click.get_current_context(),
        gcc_path=gcc_path,
        gcc_prefix=gcc_prefix,
        gcc_options=gcc_options,
        gcc_option_tokens=tuple(gcc_option_tokens),
        sysroot=sysroot,
        nostdinc=nostdinc,
        header_backend=header_backend,
        includes=tuple(includes),
        build_config=build_config,
        sources=sources,
    )
    includes = includes_tuple
    binary = artifact
    baseline = against

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
    sm, is_auto, auto_method = _resolve_auto_source_method(None, dp, False, seeded, risk)
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
        resolved, eff_depth_enum,
        source_scope=SourceScope.CHANGED if seeded else SourceScope.TARGET,
    )
    headers, baseline_header, sources, build_info, compile_db = _normalize_depth_inputs(
        eff_depth_enum,
        headers,
        baseline_header,
        sources,
        build_info,
        compile_db,
    )
    effective_build_info = compile_db or build_info

    if dry_run:
        from .dry_run import emit_dry_run

        emit_dry_run(render_scan_dry_run(
            artifact=artifact, against=against,
            headers=list(headers), includes=list(includes),
            sources=sources, effective_build_info=effective_build_info,
            changed=changed, changed_src=changed_src, seeded=seeded,
            depth=depth, eff_depth_enum=eff_depth_enum, resolved=resolved,
            collect_mode=collect_mode, budget_s=budget_s, lang=lang,
            header_backend=header_backend, fmt=fmt,
        ))

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
            compile_context=None if compile_context.is_default else compile_context,
            defer_cleanup=build_dir_cleanups,
            abi3_floor=abi3_floor,
        )
    except _BudgetOverflow as bo:
        click.echo(bo.message, err=True)
        sys.exit(_EXIT_BUDGET_OVERFLOW)
    except _EvidenceContractError as ce:
        # A pinned depth that can't collect its evidence is a usage contract
        # violation → a clean CLI error (exit 1), distinct from the verdict codes
        # (2/4) and the budget code (5).
        raise click.ClickException(ce.message) from ce
    finally:
        # Remove the inferred cmake build dir(s) now that every build-dir-dependent
        # phase has run (or the scan aborted). Best-effort (each thunk is suppressed)
        # so a removal/unlock error never aborts the rest nor masks the real outcome.
        from .buildsource.build_query import drain_build_dir_cleanups

        drain_build_dir_cleanups(build_dir_cleanups)

    _emit_scan_report(core.outcome, fmt, output)



