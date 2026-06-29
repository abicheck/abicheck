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
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

from .buildsource.crosscheck import ALL_CHECKS, CrosscheckConfig, run_crosschecks
from .buildsource.pattern_scan import scan_files
from .buildsource.poi import build_points_of_interest, resolve_symbol_tus
from .buildsource.preprocessor_scan import run_preprocessor_scan
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
from .cli_options import (
    compile_context_options,
    lang_option,
    merge_compile_config,
    resolve_compile_context,
    verbose_option,
)
from .cli_params import DEPTH_PARAM

if TYPE_CHECKING:
    from .service_scan import CompileContext

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
    preprocessor: dict[str, Any] = field(default_factory=dict)
    crosscheck: dict[str, Any] = field(default_factory=dict)
    crosscheck_severities: dict[str, str] = field(default_factory=dict)
    poi: dict[str, Any] = field(default_factory=dict)
    advisories: list[str] = field(default_factory=list)
    audit: bool = False
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
            "preprocessor_scan": self.preprocessor,
            "crosscheck": self.crosscheck,
            "crosscheck_severities": dict(self.crosscheck_severities),
            "poi": self.poi,
            "advisories": list(self.advisories),
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
    dwarf = getattr(snap, "dwarf", None)
    has_debug = bool(getattr(dwarf, "has_dwarf", False)) if dwarf is not None else False
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
            else "no public-header AST (pass --headers; needs castxml or clang)",
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


def _l3_collected(snap: Any) -> bool:
    """True when the snapshot carries a non-empty L3 build-evidence layer.

    Used to decide whether a deep ``--source-method`` actually reached L3: a
    ``not_collected`` (or absent pack) L3 means the requested L3/L4/L5 layers were
    skipped for want of a compile database, which warrants a pointed advisory.
    ``partial`` counts as collected — it ran and produced something.
    """
    pack = getattr(snap, "build_source", None)
    if pack is None:
        return False
    for cov in pack.manifest.coverage:
        row = cov.to_dict() if hasattr(cov, "to_dict") else cov
        if row.get("layer") == "L3_build":
            return row.get("status") != "not_collected"
    return False


def _uses_fast_binary_surface(depth: EvidenceDepth) -> bool:
    """True when the scan depth needs only ELF exports plus cheap debug presence.

    The deeper DWARF DIE walk is source/type evidence. ``headers`` gets its type
    evidence from L2 AST, and ``build`` adds L3 compile context; neither needs the
    expensive DWARF expansion on the binary side.
    """
    return depth in {
        EvidenceDepth.BINARY,
        EvidenceDepth.HEADERS,
        EvidenceDepth.BUILD,
    }


def _uses_debug_presence_only(depth: EvidenceDepth) -> bool:
    """True when L2/L3 evidence is collected elsewhere, so DWARF stays cheap."""
    return depth in {EvidenceDepth.HEADERS, EvidenceDepth.BUILD}


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
    for note in out.advisories:
        lines.append(f"  note: {note}")

    poi_counts = out.poi.get("counts_by_reason") or {}
    if poi_counts:
        focus = ", ".join(f"{k}×{v}" for k, v in sorted(poi_counts.items()))
        lines.append(
            f"  focus (POI): {out.poi.get('total', 0)} point(s) "
            f"[{focus}] → {len(out.poi.get('changed_paths') or [])} path(s), "
            f"{len(out.poi.get('symbols') or [])} symbol(s)"
        )

    lines.append("")
    lines.append("Coverage")
    for row in out.coverage:
        lines.append(
            f"  {row['layer']:<18} {row['status']:<13} {row.get('detail', '')}"
        )

    if out.crosscheck.get("counts_by_check"):
        lines.append("")
        lines.append(
            "ABI-hygiene catalog (intra-version, advisory)"
            if out.audit
            else "Cross-source findings (advisory)"
        )
        for kind, n in sorted(out.crosscheck["counts_by_check"].items()):
            sev = out.crosscheck_severities.get(kind, "warning")
            lines.append(f"  [{sev}] {kind}: {n}")

    pat_counts = out.pattern.get("counts_by_kind") or {}
    if pat_counts:
        lines.append("")
        lines.append("Pattern pre-scan facts (advisory)")
        for kind, n in sorted(pat_counts.items()):
            lines.append(f"  {kind}: {n}")

    pp_div = out.preprocessor.get("divergences") or []
    pp_leaks = out.preprocessor.get("leaks") or []
    if pp_div or pp_leaks:
        lines.append("")
        lines.append("Preprocessor pre-scan facts (S2, advisory)")
        for d in pp_div:
            lines.append(
                f"  macro divergence: {d['macro']} ({d['n_values']} values across TUs)"
            )
        for leak in pp_leaks:
            lines.append(
                f"  {leak['leak_class']}-header leak: "
                f"{leak['public_header']} → {leak['leaked_header']}"
            )

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
    public_headers: list[Path] | None = None,
    public_header_dirs: list[Path] | None = None,
    compile_context: CompileContext | None = None,
    defer_cleanup: list[Callable[[], None]] | None = None,
    symbols_only: bool = False,
    debug_presence_only: bool = False,
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
        snap = resolve_input(
            binary,
            headers,
            includes,
            version="",
            lang=lang,
            public_headers=public_headers,
            public_header_dirs=public_header_dirs,
            compile=compile_context,
            symbols_only=symbols_only,
            debug_presence_only=debug_presence_only,
        )
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
            defer_cleanup=defer_cleanup,
        )
    return snap


def _load_exports_for_poi(path: Path | None, lang: str) -> Any | None:
    """Best-effort cheap load for the D7 export-delta POI walk (ADR-035 D7).

    Loads *path* **header-free** — so no castxml/L2 and no L3-L5 collection, just
    the L0 export tables (and, for a JSON baseline, its embedded L5 graph). The
    export-delta walk in ``build_points_of_interest`` needs both sides' export
    tables *before* the expensive collection runs; this is how it gets the
    candidate's. Returns ``None`` on any failure (a registry-ref baseline, a load
    error, …) so POI focusing simply degrades to changed-paths/triggers/risk —
    it must never break the scan. This is an L0/L1-only read (well below the L4
    cost cliff); the one expensive collection still runs once, below.
    """
    if path is None:
        return None
    from .service import resolve_input

    try:
        return resolve_input(path, [], [], version="", lang=lang, symbols_only=True)
    except Exception:  # noqa: BLE001 - best-effort focusing input, never fatal
        return None


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
    "--config",
    "build_config",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Trusted project .abicheck.yml (enables build.query with "
    "--allow-build-query).",
)
@click.option(
    "--baseline",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Previous build's dump/library to compare against.",
)
@click.option(
    "--baseline-header",
    "--baseline-headers",
    "baseline_header",
    multiple=True,
    type=click.Path(exists=True, path_type=Path),
    help="Public header(s)/dir for the --baseline side when it is a native "
    "library whose headers differ from the new build's -H. Without this, a "
    "native baseline is parsed with the new -H (correct only when the headers "
    "did not change). Ignored for a JSON-snapshot baseline (headers already "
    "baked in).",
)
@click.option(
    "--baseline-include",
    "baseline_include",
    multiple=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Include root(s) for parsing --baseline-header (the old side's -I). "
    "Defaults to the new build's -I when unset.",
)
@click.option(
    "--mode",
    "mode",
    type=click.Choice([m.value for m in ScanMode]),
    default=ScanMode.PR.value,
    show_default=False,
    hidden=True,
    help="DEPRECATED (ADR-037 D5 G22 Phase 6): fixed (L,S) preset. Use --depth; "
    "kept as a warned alias for one release. (--audit is the standalone "
    "no-baseline lint switch.)",
)
@click.option(
    "--source-method",
    "source_method",
    type=click.Choice([m.value for m in SourceMethod]),
    default=None,
    hidden=True,
    help="DEPRECATED (ADR-037 D5 G22 Phase 6): precise S-axis technique. Use "
    "--depth; kept as a warned alias for one release.",
)
@click.option(
    "--depth",
    "depth",
    type=DEPTH_PARAM,
    default=None,
    help="Evidence depth to collect — the single dial, named by what you get: "
    "binary (L0/L1 symbols only), headers (+L2 AST), build (+L3 build context), "
    "source (+L4 replay & the L5 graph), full (deepest). Omit for 'auto' "
    "(risk-driven when a --since/--changed-path seed is present, else a sensible "
    "default). --audit is orthogonal (no-baseline lint).",
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
    "--estimate",
    "estimate",
    is_flag=True,
    default=False,
    help="Dry-run: print projected per-layer cost for this project; scan nothing.",
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
    binaries: tuple[Path, ...],
    headers: tuple[Path, ...],
    includes: tuple[Path, ...],
    public_header_dirs: tuple[Path, ...],
    sources: Path | None,
    build_info: Path | None,
    compile_db: Path | None,
    build_config: Path | None,
    baseline: Path | None,
    baseline_header: tuple[Path, ...],
    baseline_include: tuple[Path, ...],
    mode: str,
    source_method: str | None,
    depth: str | None,
    since: str | None,
    changed_paths_opt: tuple[str, ...],
    budget: str | None,
    audit: bool,
    estimate: bool,
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

    # ADR-037 D5 G22 Phase 6: --depth is the single visible dial; --mode and
    # --source-method are hidden, deprecated warned aliases for one release.
    _ctx = click.get_current_context()
    _mode_explicit = (
        _ctx.get_parameter_source("mode") == click.core.ParameterSource.COMMANDLINE
    )
    _sm_explicit = (
        _ctx.get_parameter_source("source_method")
        == click.core.ParameterSource.COMMANDLINE
    )
    if _mode_explicit or _sm_explicit:
        _dep = [
            f
            for f, e in (("--mode", _mode_explicit), ("--source-method", _sm_explicit))
            if e
        ]
        click.echo(
            f"warning: {', '.join(_dep)} {'is' if len(_dep) == 1 else 'are'} "
            "deprecated (ADR-037 D5); use --depth "
            "(binary|headers|build|source|full), or omit it for auto. --audit is "
            "the no-baseline lint switch.",
            err=True,
        )

    scan_mode = ScanMode.AUDIT if audit else ScanMode(mode)
    sm = SourceMethod(source_method) if source_method else None
    # S2 (preprocessor macro/include capture) is collected by the conditional S2
    # tier (`preprocessor_scan.run_preprocessor_scan`) over the L3 build evidence;
    # it maps to the L3 `build` collect mode and the always-on tier runs the
    # preprocessor pass when a compile DB + `clang -E` are available (else the
    # coverage row reports it skipped — ADR-035 D2 coverage honesty).
    dp = EvidenceDepth(depth) if depth else None
    # The unset dial means 'auto' (ADR-037 D5): opt into the risk-driven S-method
    # so a seeded PR scan escalates by risk and an unseeded one falls back to the
    # preset. Only when *nothing* was pinned (no --depth, no --source-method, no
    # explicit --mode) — a pinned rung stays deterministic.
    if sm is None and dp is None and not _mode_explicit:
        sm = SourceMethod.AUTO
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
    # Keyed on the *resolved* effective depth, not the raw --depth:
    # --source-method wins over --depth, so `--source-method s5 --depth binary`
    # still keeps the inputs needed for a source scan.
    headers, baseline_header, sources, build_info, compile_db = _normalize_depth_inputs(
        eff_depth_enum,
        headers,
        baseline_header,
        sources,
        build_info,
        compile_db,
    )
    effective_build_info = compile_db or build_info

    # --- --estimate: dry-run cost probe, scan nothing (ADR-035 D10) -----------
    if estimate:
        _emit_estimate(
            binary=binary,
            headers=list(headers),
            includes=list(includes),
            sources=sources,
            build_info=effective_build_info,
            mode=scan_mode.value,
            # Thread the *resolved* concrete level (not the raw flags) so the
            # estimate matches what the real scan would run — e.g. the auto
            # default resolving a seeded empty diff to s0/off, not the pr preset,
            # and pr-deep keeping its (s5, graph) depth rather than collapsing to
            # source under the source-method>depth precedence (Codex review).
            resolved_method=resolved,
            eff_depth=eff_depth_enum,
            changed=changed,
            seeded=seeded,
            budget_s=budget_s,
            lang=lang,
            fmt=fmt,
            output=output,
        )
        return

    # --- run the engine core (the shared orchestration; ADR-035 D10) ----------
    # The classify→tier→level→compare body lives in ``run_scan_core`` so the CLI,
    # ``service.run_scan``, and the MCP tool drive one engine. The CLI only parses
    # argv, renders, and maps the budget-overflow signal onto an exit code.
    # Two distinct notions of "explicit", deliberately not the same boolean:
    #  • _level_explicit — consent to auto-run build.query (level-implies-query):
    #    a non-auto --source-method, or --depth ONLY when no --source-method is
    #    given (--source-method auto wins in resolution and must NOT grant query
    #    consent). Conservative.
    #  • _pinned_explicit — the auto-strict evidence contract: an explicit --depth
    #    *always* pins (regardless of --source-method auto, which only picks the
    #    method, not whether the user demanded source depth), or a non-auto
    #    --source-method (CodeRabbit review). --mode is a deprecated preset, never
    #    a pin.
    _sm_pin = source_method is not None and source_method != SourceMethod.AUTO.value
    _level_explicit = _sm_pin or (source_method is None and depth is not None)
    _pinned_explicit = (depth is not None) or _sm_pin
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

    outcome = core.outcome
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


class _BudgetOverflow(Exception):
    """Raised by ``run_scan_core`` when the scan exceeds ``--budget`` (ADR-035 D3).

    A scan-engine signal (not a click concern): the budget is a *failure guard*
    that never shrinks scope, so the core raises and the CLI maps it onto exit 5.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class _EvidenceContractError(Exception):
    """Raised by ``run_scan_core`` when a *pinned* depth can't collect its evidence.

    ADR-037 D5 (#2 auto-strict): an explicitly-pinned ``--depth``/``--source-method``
    is a contract — if the requested source/build evidence is unavailable the scan
    fails loudly rather than silently degrading to a shallower one. Like
    :class:`_BudgetOverflow`, it is an engine signal the CLI maps onto an error
    exit (a clean ``ClickException``, exit 1) and ``service.run_scan`` maps onto a
    failed :class:`ScanResult`. The implicit ``auto`` default never raises it.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass
class ScanCoreResult:
    """The engine core's typed output — the rendered :class:`ScanOutcome` plus the
    raw cross-source findings and the candidate snapshot, so ``service.run_scan``
    can build a typed ``ScanResult`` without re-running anything."""

    outcome: ScanOutcome
    findings: list[Any]
    snapshot: Any


def run_scan_core(
    *,
    start: float,
    binary: Path,
    headers: list[Path],
    includes: list[Path],
    public_headers: list[Path],
    public_header_dirs: list[Path],
    sources: Path | None,
    effective_build_info: Path | None,
    build_config: Path | None,
    baseline: Path | None,
    lang: str,
    allow_build_query: bool,
    baseline_headers: list[Path] | None = None,
    baseline_includes: list[Path] | None = None,
    scan_mode: ScanMode,
    resolved: SourceMethod,
    eff_depth_enum: EvidenceDepth,
    collect_mode: str,
    changed: list[str],
    changed_src: str,
    seeded: bool,
    risk: RiskScore,
    is_auto: bool,
    enabled_checks: frozenset[str],
    severities: dict[str, str],
    budget: str | None,
    budget_s: float | None,
    level_explicit: bool = False,
    pinned_explicit: bool = False,
    compile_context: CompileContext | None = None,
    defer_cleanup: list[Callable[[], None]] | None = None,
) -> ScanCoreResult:
    """The shared scan orchestration (classify → always-on tier → level → compare).

    Pure of click/argv: it takes already-resolved inputs, runs the engine, and
    returns a :class:`ScanCoreResult`. Raises :class:`_BudgetOverflow` on budget
    overflow (the CLI maps it to exit 5). This is the one body the CLI,
    ``service.run_scan``, and the MCP scan tool share (ADR-035 D10).
    """
    # --- always-on tier: compiler-free pattern pre-scan (S3) ------------------
    # Runs *before* the snapshot build so its escalation triggers feed the D7
    # points-of-interest work-list that focuses the (expensive) source replay.
    # Scope: a *seeded* diff (even an empty one) confines the scan to the changed
    # set — an empty seed (no-op PR) scans nothing, preserving the empty-diff
    # scope; only a genuinely *unseeded* run (no --since/--changed-path) falls
    # back to the whole-tree scan (Codex review).
    pattern_roots: list[Path] = [*headers]
    if sources is not None and eff_depth_enum not in {
        EvidenceDepth.BINARY,
        EvidenceDepth.HEADERS,
    }:
        pattern_roots.append(sources)
    pattern = scan_files(pattern_roots, changed if seeded else None)

    # --- D7 points-of-interest: cheap facts steer the expensive scan ----------
    # Floor = the directly-changed paths (always included); the pattern triggers,
    # risk score, and the L0↔L2 export deltas only *add* candidates, never drop a
    # changed TU (ADR-035 D7). The export-delta walk needs both sides' export
    # tables up front, so read a cheap, header-free L0 view of the candidate and
    # baseline here (no castxml/L3-L5); the one expensive collection still runs
    # once, below, with the resulting focus seed. The candidate view is only
    # loaded when there is a baseline to diff it against — the delta walk consumes
    # the two together, so loading it baseline-less would be a wasted L0/L1 parse.
    needs_export_delta_poi = (
        baseline is not None
        and seeded
        and collect_mode in {"source-changed", "graph-full"}
    )
    poi_baseline = _load_exports_for_poi(baseline, lang) if needs_export_delta_poi else None
    poi_candidate = (
        _load_exports_for_poi(binary, lang) if poi_baseline is not None else None
    )
    poi = build_points_of_interest(
        changed_paths=changed,
        risk=risk,
        pattern_triggers=pattern.escalation_triggers,
        baseline=poi_baseline,
        candidate=poi_candidate,
    )

    # --- build the candidate snapshot (L0-L2 + inline L3-L5 at the level) ------
    # An explicit --compile-db (a file) wins over --build-info (dir/pack) as the
    # L3 source; both feed embed_build_source's build_info input. The POI path set
    # focuses the replay — but ONLY when a real diff seed was supplied
    # (``seeded``). Without --since/--changed-path the scan is broad by contract
    # (the report says so), so passing pattern-trigger POIs as the changed set
    # would wrongly narrow PR-mode replay to a single pattern-flagged TU and skip
    # source-only checks elsewhere (Codex review). When seeded, the focusing
    # work-list is the changed-path floor *plus* the TUs resolved from changed
    # exports via the baseline's L5 graph (resolve_symbol_tus) — so a changed
    # export with an unchanged header still points the replay at the one TU that
    # emits it (ADR-035 D7, the focusing half).
    symbol_tus = resolve_symbol_tus(poi, poi_baseline) if seeded else ()
    replay_seed = (
        tuple(dict.fromkeys((*poi.changed_paths(), *symbol_tus))) if seeded else ()
    )
    # ADR-035 P3: an unseeded s5/pr run cannot narrow 'source-changed' to a diff,
    # so the L4 replay falls back to the public-API 'headers-only' surface
    # (inline.collect_inline_pack). Record an advisory naming the cost + the knob
    # that focuses it, rather than silently paying a broad replay (validation P3
    # "no auto-warn"). Carried on the result (text + JSON) so it never pollutes a
    # structured-format stdout.
    advisories: list[str] = []
    # Only when L4 replay can actually run (a --sources tree is present —
    # `_run_inline_source_abi` returns early without one, and `--build-info`
    # alone yields L3 but no replay) does the headers-only fallback apply; firing
    # the advisory otherwise would report a replay that never happened
    # (CodeRabbit review).
    if not seeded and collect_mode == "source-changed" and sources is not None:
        advisories.append(
            "no --since/--changed-path seed; the source replay covers the "
            "public-API surface (headers-only) instead of a focused diff. Pass "
            "--since <ref> or --changed-path to scope it to the change."
        )
    # level-implies-query (ADR-037 D4): an explicit, *trusted* --config that
    # defines a build.query, together with an *explicitly pinned* deep level
    # (--source-method/--depth, level_explicit), is itself consent to run that
    # query — making the user pass --allow-build-query as well for a level they
    # explicitly asked for is needless friction. Trusted = an explicit --config
    # path (build_config is not None here; an auto-discovered source-tree config
    # is resolved later in embed_build_source and never reaches this gate), so
    # this never runs an attacker-controlled command. Crucially it does NOT fire
    # for the default mode preset (a plain `scan`/`--audit` with `--sources` whose
    # collect_mode is already non-off) — only an explicit deep level counts, so a
    # --config passed purely for project settings never silently runs a subprocess
    # (Codex review). No-op when the config defines no query.
    effective_allow_query = allow_build_query
    if (
        not allow_build_query
        and build_config is not None
        and collect_mode != "off"
        and level_explicit
    ):
        from .buildsource.inline import load_build_config

        try:
            _cfg = load_build_config(build_config)
        except Exception:  # malformed config surfaces later in the real load
            _cfg = None
        if _cfg is not None and _cfg.query:
            effective_allow_query = True
            advisories.append(
                f"level {resolved.value} with a trusted --config defining "
                "build.query: auto-enabled the query to collect L3+ evidence "
                "(equivalent to --allow-build-query). Pass --allow-build-query "
                "explicitly to silence this note."
            )

    new_snap = _build_new_snapshot(
        binary,
        list(headers),
        list(includes),
        sources,
        collect_mode,
        lang,
        effective_allow_query,
        changed_paths=replay_seed,
        build_info=effective_build_info,
        build_config=build_config,
        public_headers=list(public_headers),
        public_header_dirs=list(public_header_dirs),
        compile_context=compile_context,
        defer_cleanup=defer_cleanup,
        symbols_only=eff_depth_enum is EvidenceDepth.BINARY,
        debug_presence_only=_uses_debug_presence_only(eff_depth_enum),
    )

    # --- level-vs-evidence: fail-loud on missing input, advise otherwise ------
    # A deep depth (build/source/full → collect_mode != "off") needs an L3 compile
    # database; without one the L3/L4/L5 layers cannot be collected.
    #
    # ADR-037 D5 (#2 auto-strict): a depth the user *explicitly pinned* is a
    # contract. If it was pinned with **no source evidence at all** — no
    # --sources / --build-info, and the trusted --config build.query flow didn't
    # produce L3 either — there is nothing to collect from, so we ERROR with the
    # remedy rather than silently produce a shallow binary-only scan. When a
    # source input *was* supplied (or L3 was actually collected via the config
    # query) but L3 still came back empty, that stays a pointed *advisory* naming
    # the remedy, not a hard error. The implicit 'auto' default never errors here.
    gave_source_input = sources is not None or effective_build_info is not None
    needs_source = collect_mode != "off"
    if (
        needs_source
        and pinned_explicit
        and not gave_source_input
        and not _l3_collected(new_snap)
    ):
        raise _EvidenceContractError(
            f"pinned depth '{eff_depth_enum.value}' (source-method {resolved.value}) "
            "needs source evidence, but no --sources/--build-info was given — there "
            "is nothing to collect L3/L4/L5 from. Pass --sources <tree> or "
            "--build-info <dir|compile_commands.json> (or a trusted --config plus "
            "--allow-build-query), or drop the pin / use the default 'auto' for a "
            "best-effort binary scan. (Pinned depths are a contract.)"
        )
    if needs_source and gave_source_input and not _l3_collected(new_snap):
        advisories.append(
            f"requested depth '{eff_depth_enum.value}' (source-method "
            f"{resolved.value}) needs an L3 compile database, but none was found — "
            "L3/L4/L5 were skipped. Provide one with --build-info/--compile-db (a "
            "compile_commands.json or build dir), or a trusted --config plus "
            "--allow-build-query to generate it."
        )

    # --- conditional tier: S2 preprocessor pre-scan (D2) ----------------------
    # Runs only when L3 build evidence + a preprocessor (`clang -E`) are present;
    # otherwise the coverage row honestly reports it skipped (never clean). Emits
    # advisory macro-divergence + private/generated-header-leak facts. Headers are
    # expanded to the individual public header *files* (``-H include/`` accepts a
    # directory) so the per-header leak pass preprocesses each header, not the
    # directory as one bogus TU (Codex review).
    pp_build = (
        new_snap.build_source.build_evidence
        if new_snap.build_source is not None
        else None
    )
    preproc = run_preprocessor_scan(pp_build, _expand_public_headers(list(headers)))

    # --- always-on tier: intra-version cross-source checks (D4) ---------------
    # The resolved changed-path set is handed to the engine so
    # ``public_to_internal_dependency`` can elevate a finding whose internal
    # target was touched this revision (ADR-035 D4 "L5 reachability ↔ PR
    # changed files").
    # The changed-path set handed to the engine also carries the TUs the D7
    # export-delta walk resolved (symbol_tus), so ``public_to_internal_dependency``
    # elevates a finding whose internal target sits in a TU this revision touched
    # *via a changed export* — not only the literally git-changed files.
    cc = run_crosschecks(
        new_snap,
        CrosscheckConfig(
            enabled=frozenset(enabled_checks),
            changed_paths=frozenset(changed) | set(symbol_tus),
        ),
    )

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
            list(public_headers),
            list(public_header_dirs),
            compile_context=compile_context,
            baseline_headers=baseline_headers,
            baseline_includes=baseline_includes,
            symbols_only=eff_depth_enum is EvidenceDepth.BINARY,
            debug_presence_only=_uses_debug_presence_only(eff_depth_enum),
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
        raise _BudgetOverflow(
            f"error: --budget {budget} exceeded "
            f"({elapsed:.1f}s > {budget_s:.0f}s). "
            "Pin a shallower level or raise the budget; a budget never silently "
            "shrinks the pinned scope."
        )

    outcome = ScanOutcome(
        mode=scan_mode.value,
        resolved_method=resolved.value,
        depth=eff_depth_enum.value,
        collect_mode=collect_mode,
        risk=risk,
        auto=is_auto,
        changed_path_count=len(changed),
        changed_path_source=changed_src,
        coverage=[
            *_intrinsic_coverage(new_snap),
            pattern.coverage().to_dict(),
            preproc.coverage().to_dict(),
            *_pack_coverage(new_snap),
            *cc.coverage,
        ],
        pattern=pattern.to_dict(),
        preprocessor=preproc.to_dict(),
        crosscheck=cc.to_dict(),
        crosscheck_severities=severities,
        poi=poi.to_dict(),
        advisories=advisories,
        audit=scan_mode is ScanMode.AUDIT,
        diff_summary=diff_summary,
        verdict=verdict,
        exit_code=exit_code,
        elapsed_s=elapsed,
        budget_s=budget_s,
    )
    return ScanCoreResult(
        outcome=outcome, findings=list(cc.findings), snapshot=new_snap
    )


def _public_provenance_set(
    headers: list[Path], public_header_dirs: list[Path]
) -> tuple[list[Path], list[Path]]:
    """Build the ``(public_headers, public_header_dirs)`` provenance set for scan.

    A directory boundary is what lets ``apply_provenance`` classify origins as
    PUBLIC/INTERNAL (and so unlocks the leakage / RTTI / exported-vs-public
    cross-checks, ADR-024). Directories come from ``--public-header-dir`` and from
    any ``-H`` argument that is itself a directory; ``-H`` *file* arguments ride
    along as explicit public headers.

    A lone ``-H`` umbrella *file* with no directory does **not** activate
    provenance: a single header cannot establish a public directory boundary
    (the abicheck A1 finding), so we return empty sets and every origin stays
    ``UNKNOWN`` — preserving the prior default-scan behaviour.
    """
    dirs = list(public_header_dirs)
    files: list[Path] = []
    for h in headers:
        if h.is_dir():
            dirs.append(h)
        else:
            files.append(h)
    if not dirs:
        return [], []
    return files, dirs


def _expand_public_headers(headers: list[Path]) -> list[str]:
    """Expand ``-H`` inputs (files or directories) to individual header files.

    ``-H/--headers`` accepts a directory (the snapshot build expands it the same
    way); the S2 leak pass needs the individual header *files* so clang
    preprocesses each one, not a directory as a single bogus TU. Falls back to the
    raw paths if expansion fails (e.g. an empty dir) so the pass still runs.
    """
    from .service import expand_header_inputs

    try:
        return [str(p) for p in expand_header_inputs(headers)]
    except Exception:  # noqa: BLE001 - expansion is best-effort for the advisory tier
        return [str(h) for h in headers]


def _emit_estimate(
    *,
    binary: Path,
    headers: list[Path],
    includes: list[Path],
    sources: Path | None,
    build_info: Path | None,
    mode: str,
    resolved_method: SourceMethod,
    eff_depth: EvidenceDepth,
    changed: list[str],
    seeded: bool,
    budget_s: float | None,
    lang: str,
    fmt: str,
    output: Path | None,
) -> None:
    """Render the ADR-035 D10 dry-run cost estimate (``scan --estimate``).

    A thin front-end over :func:`service.estimate_scan`: builds a
    :class:`service.ScanRequest`, probes the project (TU count, header fan-out)
    and prints the projected per-layer cost — scanning nothing, running no
    compiler. Always exits 0 (it is a probe, not a gate).
    """
    from .service import Budget, ScanRequest, estimate_scan

    req = ScanRequest(
        binaries=[binary],
        headers=headers,
        includes=includes,
        sources=sources,
        build_info=build_info,
        mode=mode,
        source_method=resolved_method.value,
        depth=eff_depth.value,
        changed_paths=list(changed),
        seeded=seeded,
        budget=Budget(total_timeout=budget_s),
        lang=lang,
    )
    # Pass the *already-resolved* level so the estimate mirrors the real scan
    # exactly — re-resolving from the round-tripped flags would re-apply the
    # source-method > depth precedence and lose a mode preset's deeper depth
    # (pr-deep = (s5, graph)); Codex review.
    estimates = estimate_scan(req, resolved_level=(resolved_method, eff_depth))
    total = sum(e.est_seconds for e in estimates)

    if fmt == "json":
        text = json.dumps(
            {
                "mode": mode,
                "estimate": [e.to_dict() for e in estimates],
                "total_est_seconds": round(total, 3),
            },
            indent=2,
        )
    else:
        lines = [
            f"abicheck scan --estimate — {mode} mode (dry run; nothing scanned)",
            "",
        ]
        lines.append(f"  {'layer':<16} {'method':<8} {'TUs':>6}  {'est_s':>8}  note")
        for e in estimates:
            lines.append(
                f"  {e.layer:<16} {(e.method or '-'):<8} {e.tus:>6}  "
                f"{e.est_seconds:>8.2f}  {e.note}"
            )
        lines.append("")
        lines.append(f"  projected total: {total:.2f}s")
        text = "\n".join(lines)

    if output:
        _safe_write_output(output, text)
        click.echo(f"Estimate written to {output}", err=True)
    else:
        click.echo(text)


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


def _baseline_is_native_library(path: Path) -> bool:
    """True if *path* is a native binary, not a JSON / ABICC-dump snapshot.

    A snapshot baseline already has its headers baked in, so the candidate-`-H`
    reuse is harmless there; only a native binary is re-parsed (and thus at risk
    of being read through the wrong headers).

    Detection is content-first to match `resolve_input`'s own native dispatch:
    magic-byte sniffing (`detect_binary_format`) catches the cases a suffix scan
    misses — an extensionless ELF (`build/foo`), a Mach-O framework binary, a
    `.pyd`/`.node` shared object (Codex review). The filename heuristic is only a
    fallback for paths that cannot be sniffed (e.g. a not-yet-existing file in a
    unit test), and the snapshot suffixes short-circuit first so a real `.json`
    on disk is never mis-sniffed.
    """
    name = path.name.lower()
    if name.endswith((".json", ".dump", ".tar.gz", ".tgz", ".xml")):
        return False
    from .binary_utils import detect_binary_format

    if detect_binary_format(path) is not None:
        return True
    return ".so" in name or name.endswith((".dll", ".dylib"))


def _run_baseline_compare(
    baseline: Path,
    new_snap: Any,
    extra_changes: list[Any],
    lang: str,
    collect_mode: str,
    headers: list[Path],
    includes: list[Path],
    public_headers: list[Path],
    public_header_dirs: list[Path],
    compile_context: CompileContext | None = None,
    baseline_headers: list[Path] | None = None,
    baseline_includes: list[Path] | None = None,
    symbols_only: bool = False,
    debug_presence_only: bool = False,
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
    from .cli_buildsource import prepare_embedded_build_source
    from .errors import AbicheckError
    from .service import compare_snapshots, resolve_input

    # Each side is parsed with its *own* headers. `scan` has a single -H (built for
    # the candidate); for a native --baseline library whose public headers differ,
    # --baseline-header/-include select the old side's headers. Without them we
    # reuse the candidate -H/-I — correct only when the headers did not change — so
    # warn rather than silently read the old side through the new headers (Codex).
    if baseline_headers:
        bl_headers = list(baseline_headers)
        bl_includes = list(baseline_includes) if baseline_includes else includes
        bl_public_headers = bl_headers
        # The old-side public boundary comes ONLY from --baseline-header: dirs in
        # it are public-header dirs, files opt in just themselves. Do NOT fall back
        # to the new side's public dirs — a relative dir like `include/` would
        # (segment-based provenance) re-mark old private headers as PUBLIC and skew
        # the public-surface scoping (Codex review).
        bl_public_dirs = [p for p in bl_headers if p.is_dir()]
    else:
        bl_headers, bl_includes = headers, includes
        bl_public_headers, bl_public_dirs = public_headers, public_header_dirs
        if headers and _baseline_is_native_library(baseline):
            click.echo(
                f"warning: --baseline {baseline.name} is a native library parsed "
                f"with the new build's headers (-H); if its public headers differ "
                f"from the new version, pass --baseline-header (else the old side is "
                f"read through the new headers and the diff may be wrong/noisy).",
                err=True,
            )

    try:
        old_snap = resolve_input(
            baseline,
            bl_headers,
            bl_includes,
            version="",
            lang=lang,
            public_headers=bl_public_headers,
            public_header_dirs=bl_public_dirs,
            compile=compile_context,
            symbols_only=symbols_only,
            debug_presence_only=debug_presence_only,
        )
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
    diff = compare_snapshots(
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
