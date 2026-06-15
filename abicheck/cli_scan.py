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
3. run the **pinned** evidence level (the ``--mode`` preset or an explicit
   ``--source-method``/``--depth``, resolved by ``scan_levels.py``), POI-scoped to
   the changed paths, by collecting L3/L4/L5 inline at the matching ADR-033 D2
   evidence mode;
4. if a ``--baseline`` is given, ``compare`` against it and fold the cross-source
   findings in as ``extra_changes``;
5. emit **one** report stating, per layer/method, what ran vs. skipped (never a
   bare "source scan failed").

Determinism (ADR-035 D3): the level is fixed by the pinned ``--mode``/``--source-
method``/``--depth``; the risk score escalates the level **only** under
``--source-method auto`` (opt-in). ``--budget`` is a failure guard on the chosen
level — it never silently shrinks scope.

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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import click

from .buildsource.crosscheck import ALL_CHECKS, CrosscheckConfig, run_crosschecks
from .buildsource.pattern_scan import scan_files
from .buildsource.risk import RiskRules, RiskScore, score_changed_paths
from .buildsource.scan_levels import (
    EvidenceDepth,
    ScanMode,
    SourceMethod,
    level_to_collect_mode,
    resolve_level,
)
from .checker_policy import API_BREAK_KINDS, BREAKING_KINDS
from .cli import _safe_write_output, _setup_verbosity, main

#: Exit code for a ``--budget`` overflow (ADR-035 D3: a budget always fails,
#: never silently shrinks scope). Distinct from the verdict codes (0/2/4) and the
#: generic error code (1) so CI can tell a budget overflow from a real break.
_EXIT_BUDGET_OVERFLOW = 5

#: Suffixes ``time``-style duration strings accept (``15m``, ``900s``, ``1h``).
_DURATION_UNITS: dict[str, int] = {"s": 1, "m": 60, "h": 3600}

#: Valid per-check severity levels for ``--crosscheck KEY=LEVEL``. ``off`` removes
#: the check; the others keep it enabled (the label rides into the report).
_CROSSCHECK_LEVELS = frozenset({"off", "info", "warning", "error"})


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
        if key not in ALL_CHECKS:
            raise click.BadParameter(
                f"unknown cross-check {key!r}; choose from {', '.join(ALL_CHECKS)}"
            )
        if level not in _CROSSCHECK_LEVELS:
            raise click.BadParameter(
                f"invalid level {level!r} for {key!r}; "
                f"choose from {', '.join(sorted(_CROSSCHECK_LEVELS))}"
            )
        if level == "off":
            enabled.discard(key)
        else:
            severities[key] = level
    return frozenset(enabled), severities


@dataclass
class ScanOutcome:
    """The composed result of a ``scan`` run, rendered to text or JSON.

    Holds enough to print one coverage- and confidence-annotated report: the
    resolved level, the risk score, the always-on tier results, the optional
    baseline diff, and the combined verdict/exit code.
    """

    mode: str
    resolved_method: str
    depth: str | None
    collect_mode: str
    risk: RiskScore
    auto: bool
    changed_path_count: int
    changed_path_source: str
    coverage: list[dict[str, Any]] = field(default_factory=list)
    pattern: dict[str, Any] = field(default_factory=dict)
    crosscheck: dict[str, Any] = field(default_factory=dict)
    crosscheck_severities: dict[str, str] = field(default_factory=dict)
    diff_summary: dict[str, Any] | None = None
    verdict: str = "COMPATIBLE"
    exit_code: int = 0
    elapsed_s: float = 0.0
    budget_s: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "level": {
                "source_method": self.resolved_method,
                "depth": self.depth,
                "collect_mode": self.collect_mode,
                "auto": self.auto,
            },
            "risk": self.risk.to_dict(),
            "changed_paths": {
                "count": self.changed_path_count,
                "source": self.changed_path_source,
            },
            "coverage": list(self.coverage),
            "pattern_scan": self.pattern,
            "crosscheck": self.crosscheck,
            "crosscheck_severities": dict(self.crosscheck_severities),
            "diff": self.diff_summary,
            "verdict": self.verdict,
            "exit_code": self.exit_code,
            "elapsed_s": round(self.elapsed_s, 3),
            "budget_s": self.budget_s,
        }


def _intrinsic_coverage(snap: Any) -> list[dict[str, Any]]:
    """Compute the intrinsic L0/L1/L2 coverage rows from a snapshot."""
    rows: list[dict[str, Any]] = []
    has_binary = bool(snap.elf or snap.pe or snap.macho)
    rows.append(
        {
            "layer": "L0_binary",
            "status": "present" if has_binary else "not_collected",
            "detail": f"{len(snap.functions)} function(s), "
            f"{len(snap.variables)} variable(s)"
            if has_binary
            else "no binary export table (snapshot-only input)",
        }
    )
    has_debug = snap.dwarf is not None
    rows.append(
        {
            "layer": "L1_debug",
            "status": "present" if has_debug else "not_collected",
            "detail": "DWARF/PDB debug info present" if has_debug else "no debug info",
        }
    )
    rows.append(
        {
            "layer": "L2_header",
            "status": "present" if snap.from_headers else "skipped",
            "detail": f"{len(snap.types)} type(s) from public headers"
            if snap.from_headers
            else "no public-header AST (pass --headers; needs castxml)",
        }
    )
    return rows


def _pack_coverage(snap: Any) -> list[dict[str, Any]]:
    """Read the L3/L4/L5 coverage rows from a snapshot's embedded pack, if any."""
    pack = getattr(snap, "build_source", None)
    if pack is None:
        return [
            {
                "layer": layer,
                "status": "not_collected",
                "detail": "no build/source evidence collected "
                "(pass --sources, or a deeper --source-method)",
            }
            for layer in ("L3_build", "L4_source_abi", "L5_source_graph")
        ]
    return [c.to_dict() for c in pack.manifest.coverage]


def _render_text(out: ScanOutcome) -> str:
    """Render the human-facing scan report."""
    lines: list[str] = []
    lines.append(f"abicheck scan — {out.mode} mode")
    lvl = f"  source-method={out.resolved_method}"
    if out.depth:
        lvl += f"  depth={out.depth}"
    lvl += f"  collect-mode={out.collect_mode}"
    if out.auto:
        lvl += "  (auto)"
    lines.append(lvl)
    matched = ", ".join(f"{k}×{v}" for k, v in sorted(out.risk.matched.items()))
    lines.append(
        f"  risk score={out.risk.total} "
        f"(auto→{out.risk.recommended_method})" + (f" [{matched}]" if matched else "")
    )
    lines.append(
        f"  changed paths: {out.changed_path_count} ({out.changed_path_source})"
    )

    lines.append("")
    lines.append("Coverage")
    for row in out.coverage:
        lines.append(
            f"  {row['layer']:<18} {row['status']:<13} {row.get('detail', '')}"
        )

    if out.crosscheck.get("counts_by_check"):
        lines.append("")
        lines.append("Cross-source findings (advisory)")
        for kind, n in sorted(out.crosscheck["counts_by_check"].items()):
            sev = out.crosscheck_severities.get(kind, "warning")
            lines.append(f"  [{sev}] {kind}: {n}")

    pat_counts = out.pattern.get("counts_by_kind") or {}
    if pat_counts:
        lines.append("")
        lines.append("Pattern pre-scan facts (advisory)")
        for kind, n in sorted(pat_counts.items()):
            lines.append(f"  {kind}: {n}")

    if out.diff_summary is not None:
        lines.append("")
        lines.append("Baseline comparison")
        lines.append(
            f"  breaking={out.diff_summary['breaking']} "
            f"api_break={out.diff_summary['api_break']} "
            f"risk={out.diff_summary['risk']} "
            f"compatible={out.diff_summary['compatible']}"
        )

    lines.append("")
    lines.append(f"Verdict: {out.verdict}")
    if out.budget_s is not None:
        lines.append(f"Elapsed: {out.elapsed_s:.2f}s / budget {out.budget_s:.0f}s")
    return "\n".join(lines)


def _build_new_snapshot(
    binary: Path,
    headers: list[Path],
    includes: list[Path],
    sources: Path | None,
    collect_mode: str,
    lang: str,
    allow_build_query: bool,
    changed_paths: tuple[str, ...] = (),
    build_info: Path | None = None,
    build_config: Path | None = None,
) -> Any:
    """Dump the candidate's L0-L2 surface and embed L3-L5 inline at *collect_mode*.

    The resolved ``changed_paths`` (from ``--changed-path``/``--since``) are
    threaded into the inline source replay so a ``source-changed`` collection
    actually narrows to the affected TUs — the ADR-035 D7 POI-focused cost model —
    instead of falling back to a full ``target`` replay.

    ``build_info`` (an out-of-tree compile DB / build dir / pack) and
    ``build_config`` (a trusted ``.abicheck.yml`` enabling ``build.query``) are
    threaded through so a pinned s5/s6 scan can collect L3/L4 even when the build
    context lives outside ``--sources`` — otherwise it silently degrades to
    partial coverage (Codex review).
    """
    from .errors import AbicheckError
    from .service import resolve_input

    try:
        snap = resolve_input(binary, headers, includes, version="", lang=lang)
    except AbicheckError as exc:
        raise click.ClickException(f"Failed to load --binary {binary}: {exc}") from exc
    # Collect evidence when there is something to collect from — a source tree OR
    # an out-of-tree build-info input — at a non-"off" level.
    if (sources is not None or build_info is not None) and collect_mode != "off":
        from .cli_buildsource import embed_build_source

        embed_build_source(
            snap,
            build_info=build_info,
            sources=sources,
            build_config=build_config,
            allow_build_query=allow_build_query,
            collect_mode=collect_mode,
            changed_paths=changed_paths,
        )
    return snap


def _crosscheck_severity_exit(findings: list[Any], severities: dict[str, str]) -> int:
    """Exit-code floor from cross-checks the maintainer promoted to ``error``.

    A cross-check stays advisory (exit 0) until the maintainer opts it into
    gating with ``--crosscheck KEY=error`` (ADR-035 UX step 7 / D6). Once opted
    in, a finding for that check raises the exit to the source-break tier (2) —
    even for a RISK-class check — so the documented promotion path actually
    gates CI. ``info``/``warning`` never gate.
    """
    gating = {k for k, level in severities.items() if level == "error"}
    if gating and any(f.kind.value in gating for f in findings):
        return 2
    return 0


def _audit_exit_code(
    findings: list[Any], severities: dict[str, str]
) -> tuple[str, int]:
    """Verdict/exit for the no-baseline path from cross-source finding tiers.

    Cross-source findings are never ``BREAKING`` on their own (authority rule), so
    an audit can reach at most ``API_BREAK`` (exit 2); ``RISK`` stays advisory
    (exit 0) unless the maintainer promoted that check to ``error`` (D6).
    Adoption never starts by blocking merges (ADR-035 UX step 7).
    """
    # Defensive: a mis-partitioned kind would be caught by the import-time
    # assertion, but never let a cross-source finding gate a BREAKING verdict.
    assert not any(f.kind in BREAKING_KINDS for f in findings), (
        "cross-source findings must never be BREAKING (ADR-035 D1 authority rule)"
    )
    has_api_break = any(f.kind in API_BREAK_KINDS for f in findings)
    exit_code = max(
        2 if has_api_break else 0,
        _crosscheck_severity_exit(findings, severities),
    )
    return ("API_BREAK" if exit_code >= 2 else "COMPATIBLE"), exit_code


@main.command("scan")
@click.option(
    "--binary",
    "binaries",
    multiple=True,
    required=True,
    type=click.Path(exists=True, path_type=Path),
    help="Library/artifact (or .abi.json snapshot) to scan.",
)
@click.option(
    "-H",
    "--header",
    "--headers",
    "headers",
    multiple=True,
    type=click.Path(exists=True, path_type=Path),
    help="Public header file or directory (repeatable). Alias: -H/--header.",
)
@click.option(
    "-I",
    "--include",
    "includes",
    multiple=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Additional include directory for header parsing (repeatable).",
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
    help="Out-of-tree build dir / compile_commands.json / pack supplying L3.",
)
@click.option(
    "--compile-db",
    "compile_db",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Explicit compile_commands.json (use when not under --sources).",
)
@click.option(
    "--build-config",
    "build_config",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Trusted .abicheck.yml (enables build.query with --allow-build-query).",
)
@click.option(
    "--baseline",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Previous build's dump/library to compare against.",
)
@click.option(
    "--mode",
    "mode",
    type=click.Choice([m.value for m in ScanMode]),
    default=ScanMode.PR.value,
    show_default=True,
    help="Fixed (L,S) preset selecting how deep the scan runs.",
)
@click.option(
    "--source-method",
    "source_method",
    type=click.Choice([m.value for m in SourceMethod]),
    default=None,
    help="Precise S-axis level to reach; deterministic. 'auto' = risk-driven (opt-in).",
)
@click.option(
    "--depth",
    "depth",
    type=click.Choice([d.value for d in EvidenceDepth]),
    default=None,
    help="Coarse L-axis selector (lossy; --source-method wins if both).",
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
    "--audit",
    "audit",
    is_flag=True,
    default=False,
    help="Single-build hygiene lint, no baseline (intra-version).",
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
@click.option(
    "--lang", type=click.Choice(["c", "c++"]), default="c++", show_default=True
)
@click.option(
    "--allow-build-query",
    is_flag=True,
    default=False,
    help="Permit a trusted build.query subprocess to emit a compile DB.",
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["text", "json"]),
    default="text",
    show_default=True,
)
@click.option("-o", "--output", type=click.Path(path_type=Path), default=None)
@click.option("-v", "--verbose", is_flag=True, default=False)
def scan_cmd(
    binaries: tuple[Path, ...],
    headers: tuple[Path, ...],
    includes: tuple[Path, ...],
    sources: Path | None,
    build_info: Path | None,
    compile_db: Path | None,
    build_config: Path | None,
    baseline: Path | None,
    mode: str,
    source_method: str | None,
    depth: str | None,
    since: str | None,
    changed_paths_opt: tuple[str, ...],
    budget: str | None,
    audit: bool,
    crosschecks: tuple[str, ...],
    risk_rules_path: Path | None,
    lang: str,
    allow_build_query: bool,
    fmt: str,
    output: Path | None,
    verbose: bool,
) -> None:
    """Deterministic source-intelligence scan (classify → always-on tier → level).

    One orchestrator over `dump`/`compare`: classifies the PR's changed paths,
    runs the always-on compiler-free pattern pre-scan and the intra-version
    cross-source checks, then runs the pinned evidence level (the `--mode` preset
    or an explicit `--source-method`/`--depth`) and — when `--baseline` is given —
    compares against it. Emits one coverage-annotated report.

    \b
    Exit codes:
      0  compatible (or advisory-only findings)
      2  source-level / API break (incl. API_BREAK cross-source findings)
      4  ABI break (from the baseline comparison)
      5  --budget overflow

    \b
    Examples:
      abicheck scan --binary new/libfoo.so --headers new/include \\
                    --sources . --baseline old/libfoo.abi.json
      abicheck scan --binary libfoo.so --headers include/ --audit
      abicheck scan --binary new.so -H include/ --source-method auto --since origin/main
    """
    _setup_verbosity(verbose)
    start = time.monotonic()

    if len(binaries) != 1:
        raise click.UsageError(
            "scan currently accepts a single --binary "
            "(bundle scanning is planned for a later phase)."
        )
    binary = binaries[0]

    budget_s = _parse_budget(budget)
    enabled_checks, severities = _parse_crosschecks(crosschecks)

    # Changed-path seed: --changed-path wins; else --since via git; else none.
    # ``seeded`` tracks whether a *valid* seed was produced — a successful empty
    # diff (seeded, no paths) is distinct from a missing/failed seed (not seeded):
    # the former lets auto pick s0 (no-op PR), the latter falls back to the broad
    # mode preset (ADR-035 D7 / Codex review).
    seeded = False
    if changed_paths_opt:
        changed = list(changed_paths_opt)
        changed_src = "--changed-path"
        seeded = True
    elif since:
        git_changed = _git_changed_paths(since, sources)
        if git_changed is None:
            changed = []
            changed_src = f"--since {since} (seed failed; broad scope)"
        else:
            changed = git_changed
            changed_src = f"--since {since}"
            seeded = True
    else:
        changed = []
        changed_src = "none (no diff seed; broad scope)"

    risk_rules = _load_risk_rules(risk_rules_path)
    risk = score_changed_paths(changed, risk_rules)

    scan_mode = ScanMode.AUDIT if audit else ScanMode(mode)
    sm = SourceMethod(source_method) if source_method else None
    # S2 (preprocessor macro/include capture) has no collection backend yet
    # (ADR-035 G19 Phase 3b). Reject it rather than silently running the L3-only
    # `build` mode while reporting `s2`, which would overstate coverage (Codex).
    if sm is SourceMethod.S2:
        raise click.UsageError(
            "--source-method s2 (preprocessor macro/include capture) is not yet "
            "implemented; use s1 for build context or s5 for source-ABI replay."
        )
    dp = EvidenceDepth(depth) if depth else None
    is_auto = sm is SourceMethod.AUTO
    # auto uses the risk score ONLY when a valid diff seed was produced. A seeded
    # empty diff (no-op PR) correctly yields s0 (skip the scan); a missing/failed
    # seed instead falls back to the mode preset, so a bad-ref / non-repo CI run
    # does not silently drop all L3-L5 source evidence (Codex review).
    auto_method = risk.recommended_method if (is_auto and seeded) else None
    resolved, eff_depth_enum = resolve_level(
        mode=scan_mode,
        source_method=sm,
        depth=dp,
        auto_method=auto_method,
    )
    # collect_mode and reported depth come from the resolved (method, depth) level,
    # so a deeper preset (pr-deep = graph) is distinct from pr, and an explicit
    # --source-method reports its own depth, not the mode preset (Codex review).
    collect_mode = level_to_collect_mode(resolved, eff_depth_enum)
    eff_depth = eff_depth_enum.value

    # --- build the candidate snapshot (L0-L2 + inline L3-L5 at the level) ------
    # An explicit --compile-db (a file) wins over --build-info (dir/pack) as the
    # L3 source; both feed embed_build_source's build_info input.
    effective_build_info = compile_db or build_info
    new_snap = _build_new_snapshot(
        binary,
        list(headers),
        list(includes),
        sources,
        collect_mode,
        lang,
        allow_build_query,
        changed_paths=tuple(changed),
        build_info=effective_build_info,
        build_config=build_config,
    )

    # --- always-on tier: compiler-free pattern pre-scan (S3) ------------------
    pattern_roots: list[Path] = [*headers]
    if sources is not None:
        pattern_roots.append(sources)
    pattern = scan_files(pattern_roots, changed or None)

    # --- always-on tier: intra-version cross-source checks (D4) ---------------
    cc = run_crosschecks(new_snap, CrosscheckConfig(enabled=frozenset(enabled_checks)))

    # --- pinned-level baseline comparison (if any) ----------------------------
    diff_summary: dict[str, Any] | None = None
    if baseline is not None and scan_mode is not ScanMode.AUDIT:
        verdict, exit_code, diff_summary = _run_baseline_compare(
            baseline,
            new_snap,
            cc.findings,
            lang,
            collect_mode,
            list(headers),
            list(includes),
        )
        # A cross-check the maintainer promoted to `error` (D6) gates the exit
        # even when the baseline diff itself is clean.
        sev_exit = _crosscheck_severity_exit(cc.findings, severities)
        if sev_exit > exit_code:
            exit_code = sev_exit
            # Keep the reported verdict in sync with the promoted exit code so a
            # consumer keying off the verdict string isn't misled (Codex review).
            # Only a non-breaking verdict is promoted — never downgrade a real
            # BREAKING/API_BREAK from the artifact diff.
            if verdict in ("NO_CHANGE", "COMPATIBLE", "COMPATIBLE_WITH_RISK"):
                verdict = "API_BREAK"
    else:
        if baseline is not None:
            click.echo(
                "note: --audit ignores --baseline (intra-version scan).", err=True
            )
        verdict, exit_code = _audit_exit_code(cc.findings, severities)

    elapsed = time.monotonic() - start

    # --- budget guard: overflow FAILS, never shrinks scope (ADR-035 D3) -------
    if budget_s is not None and elapsed > budget_s:
        click.echo(
            f"error: --budget {budget} exceeded "
            f"({elapsed:.1f}s > {budget_s:.0f}s). "
            "Pin a shallower level or raise the budget; a budget never silently "
            "shrinks the pinned scope.",
            err=True,
        )
        sys.exit(_EXIT_BUDGET_OVERFLOW)

    outcome = ScanOutcome(
        mode=scan_mode.value,
        resolved_method=resolved.value,
        depth=eff_depth,
        collect_mode=collect_mode,
        risk=risk,
        auto=is_auto,
        changed_path_count=len(changed),
        changed_path_source=changed_src,
        coverage=[
            *_intrinsic_coverage(new_snap),
            pattern.coverage().to_dict(),
            *_pack_coverage(new_snap),
            *cc.coverage,
        ],
        pattern=pattern.to_dict(),
        crosscheck=cc.to_dict(),
        crosscheck_severities=severities,
        diff_summary=diff_summary,
        verdict=verdict,
        exit_code=exit_code,
        elapsed_s=elapsed,
        budget_s=budget_s,
    )

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

    if exit_code != 0:
        sys.exit(exit_code)


def _load_risk_rules(path: Path | None) -> RiskRules:
    """Load a ``risk_rules:`` profile from a YAML file, or the shipped default."""
    if path is None:
        return RiskRules.default()
    import yaml  # hard dep (pyyaml); import out of the try so the except can name it

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, yaml.YAMLError) as exc:
        # yaml.YAMLError (e.g. ParserError) is not a ValueError, so catch it
        # explicitly — else malformed --risk-rules YAML escapes as a traceback
        # through the installed console script (Codex review).
        raise click.ClickException(f"cannot read --risk-rules {path}: {exc}") from exc
    block = raw.get("risk_rules") if isinstance(raw, dict) else None
    return RiskRules.from_dict(block if isinstance(block, dict) else raw)


def _run_baseline_compare(
    baseline: Path,
    new_snap: Any,
    extra_changes: list[Any],
    lang: str,
    collect_mode: str,
    headers: list[Path],
    includes: list[Path],
) -> tuple[str, int, dict[str, Any]]:
    """Compare *new_snap* against *baseline*, folding cross-source findings in.

    The cross-source findings ride in as ``extra_changes`` so they appear in the
    diff and the verdict reflects them — but, being partitioned into
    ``RISK``/``API_BREAK`` only, they can never push the verdict to ``BREAKING``
    (ADR-035 D1 authority rule).

    *headers*/*includes* are the same scan header inputs used to build the
    candidate, threaded into the baseline parse so a native ``--baseline``
    library is header-scoped symmetrically — else the old side stays
    symbol/DWARF-only and the compare drops old type evidence or invents spurious
    API diffs (Codex review). They are inert for a JSON-snapshot baseline.

    The embedded L3/L4/L5 build/source packs on either snapshot are diffed via
    :func:`prepare_embedded_build_source` — the same path ``abicheck compare``
    uses — so source-only / graph findings the collected evidence reveals are
    folded into the verdict too (``checker.compare`` itself does not read
    ``build_source``).
    """
    from .checker import compare
    from .cli_buildsource import prepare_embedded_build_source
    from .errors import AbicheckError
    from .service import resolve_input

    try:
        old_snap = resolve_input(baseline, headers, includes, version="", lang=lang)
    except AbicheckError as exc:
        raise click.ClickException(
            f"Failed to load --baseline {baseline}: {exc}"
        ) from exc
    # Fold embedded build-info/source (L3/L4/L5) diff findings into extra_changes
    # before comparing — mirrors the compare command (Codex review). Only engage
    # when a snapshot actually carries an embedded pack; otherwise pass
    # ``collect_mode="off"`` so the pipeline stays inert (no spurious collection
    # attempt / output noise on a plain artifact-only baseline compare).
    has_embedded = (
        old_snap.build_source is not None or new_snap.build_source is not None
    )
    merged_extra, _coverage_rows, _metrics, _ev = prepare_embedded_build_source(
        old_snap,
        new_snap,
        collect_mode if has_embedded else "off",
        list(extra_changes),
        None,
        None,
        None,
        None,
    )
    diff = compare(
        old_snap,
        new_snap,
        extra_changes=merged_extra,
        scope_to_public_surface=True,
    )
    summary = {
        "breaking": len(diff.breaking),
        "api_break": len(diff.source_breaks),
        "risk": len(diff.risk),
        "compatible": len(diff.compatible),
    }
    verdict = diff.verdict.value
    if verdict == "BREAKING":
        exit_code = 4
    elif verdict == "API_BREAK":
        exit_code = 2
    else:
        exit_code = 0
    return verdict, exit_code, summary
