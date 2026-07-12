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

"""``scan`` engine core (ADR-035 D10) — classify → always-on tier → pinned level
→ optional baseline compare.

:func:`run_scan_core` is "the one body the CLI, ``service.run_scan``, and the
MCP scan tool share" (ADR-035 D10) — this module is where that body actually
lives. It is deliberately free of ``@click.option`` decorators and argv
parsing so it can be called directly from ``service_scan.run_scan`` without a
front-end dependency: ``cli_scan.py`` (the Click command) and
``service_scan.py`` (the typed request/result API) both import from here,
never from each other.

Historically this lived inside ``cli_scan.py`` alongside the ``scan`` Click
command, which meant ``service_scan.run_scan`` had to reach into a
front-end module (via a function-local import) to call it — the reverse of
the intended frontend → service → engine dependency direction (ADR-037 D1).
Splitting it out here removes that inversion; ``cli_scan.py`` now imports
:func:`run_scan_core` from this module the same way ``service_scan.py`` does.

One pre-existing exception to "no CLI concerns": :func:`_build_new_snapshot`
raises ``click.ClickException`` on a resolve failure, and :func:`run_scan_core`
prints a ``click.echo`` note when ``--baseline`` is combined with ``--audit``.
Both predate this split and are left as-is (unrelated to the dependency-
direction fix) — ``click.ClickException`` is a plain exception subclass safe
to raise outside a running CLI context, and a future cleanup can route that
note through the advisories list like every other cross-cutting message.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

from .buildsource.crosscheck import CrosscheckConfig, run_crosschecks
from .buildsource.pattern_scan import scan_files
from .buildsource.poi import build_points_of_interest, resolve_symbol_tus
from .buildsource.preprocessor_scan import run_preprocessor_scan
from .buildsource.risk import RiskScore
from .buildsource.scan_levels import EvidenceDepth, ScanMode, SourceMethod
from .checker_policy import API_BREAK_KINDS, BREAKING_KINDS
from .cli_scan_baseline import _expand_public_headers, _run_baseline_compare
from .cli_scan_helpers import (
    _intrinsic_coverage,
    _l3_collected,
    _pack_coverage,
    _source_abi_coverage,
    _uses_debug_presence_only,
    l4_coverage_advisories,
    resolve_effective_allow_query,
    scan_pattern_roots,
)

if TYPE_CHECKING:
    from .service_scan import CompileContext


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
    stage_timings: dict[str, float] = field(default_factory=dict)
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
            "stage_timings": {
                k: round(v, 3) for k, v in sorted(self.stage_timings.items())
            },
            "diff": self.diff_summary,
            "verdict": self.verdict,
            "exit_code": self.exit_code,
            "elapsed_s": round(self.elapsed_s, 3),
            "budget_s": self.budget_s,
        }


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
) -> tuple[Any, list[Path]]:
    """Dump the candidate's L0-L2 surface and embed L3-L5 inline at *collect_mode*.

    Returns ``(snapshot, effective_includes)`` — the effective includes carry any
    build-derived L2 seed so a ``--baseline`` compare can reuse the same context.

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
    from .buildsource.l2_seed import seed_l2_includes
    from .errors import AbicheckError
    from .service import resolve_input

    # L2 include fallback: when headers are given but the user passed no explicit
    # -I, seed the build's include dirs so the aggregate public-header parse can
    # resolve dependency headers (e.g. pvxs headers include EPICS Base's
    # <epicsTime.h>). Shared with the dump path via seed_l2_includes.
    #
    # Keep the seed's temp-build-dir cleanups LOCAL (defer_cleanup=None), not on the
    # outer scan list: the seed may run the inferred-CMake query, whose build dir is
    # held under an exclusive flock until its cleanup runs. embed_build_source()
    # below runs its *own* inferred query in the same function, so if we deferred the
    # release to the outer drain (which happens after embed) that second query would
    # block on our still-held lock until INFERRED_QUERY_TIMEOUT_S (600s) before
    # falling back to a fresh dir. The finally below drains _l2_local_cleanups right
    # after resolve_input() has consumed the seeded dirs, releasing the lock before
    # L3/L4 collection replays the query (Codex review).
    includes, _l2_local_cleanups = seed_l2_includes(
        headers=headers,
        includes=includes,
        sources=sources,
        build_info=build_info,
        build_config=build_config,
        defer_cleanup=None,
        # -I dirs the user gave through --gcc-options/--gcc-option (carried on the
        # CompileContext) are explicit too — pass them so the seed stays a no-op
        # and the user's include search precedence is preserved (Codex review).
        gcc_options=compile_context.gcc_options if compile_context else None,
        gcc_option_tokens=(
            compile_context.gcc_option_tokens if compile_context else ()
        ),
        # L2-only pins (--depth headers → collect_mode "off") requested no build/
        # source evidence, so the include-dir seed must not run a build system just
        # to hint headers. Passive DB discovery still applies; only the zero-config
        # inferred cmake/make/bazel query is gated (Codex review).
        allow_inferred_build_query=collect_mode != "off",
    )

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
    finally:
        # The L2 parse has now consumed the build-derived include dirs (whether it
        # succeeded or raised), so release any inferred-CMake temp build dir now —
        # before embed_build_source() below replays its own inferred query — so its
        # exclusive flock does not block that replay for 600s (see the seed call).
        if _l2_local_cleanups:
            from .buildsource.inline import _run_cleanups

            _run_cleanups(_l2_local_cleanups)
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
            public_headers=tuple(
                str(p) for p in _expand_public_headers(
                    [*list(public_headers or ()), *list(public_header_dirs or ())]
                )
            ),
            public_header_dirs=tuple(str(p) for p in (public_header_dirs or ())),
            defer_cleanup=defer_cleanup,
        )
    # Return the *effective* includes (the seed above may have added build-derived
    # dirs) so a --baseline compare header-parses the old native library with the
    # same include context — else the baseline side fails on dependency headers the
    # candidate resolved via the seed (Codex review).
    return snap, includes


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


def _run_abi3_audit(
    new_snap: Any,
    abi3_floor: tuple[int, int],
    binary: Path,
    cc: Any,
) -> None:
    """Run the opt-in stable-ABI (abi3) audit, folding its findings into ``cc``.

    A single-artifact audit of the candidate's CPython imports against a target
    Py_LIMITED_API floor. Its findings ride the cross-check stream: they are
    RISK ``python_stable_abi_violation`` rows, advisory by default (like every
    single-artifact check) and gated only via ``--crosscheck
    python_stable_abi_violation=error``. Requires the --binary to be a CPython
    extension module; --abi3 on a plain library is a usage error.
    """
    py_ext = new_snap.python_ext
    if py_ext is None or not py_ext.is_extension:
        raise _EvidenceContractError(
            f"--abi3 {abi3_floor[0]}.{abi3_floor[1]} was given but "
            f"'{binary.name}' is not a recognisable CPython extension module "
            "(no PyInit_* export and no CPython C-API imports). The stable-ABI "
            "audit applies only to extension modules (Cython/pybind11/"
            "nanobind/C)."
        )
    from .diff_python import audit_stable_abi_imports

    abi3_findings = audit_stable_abi_imports(py_ext, abi3_floor)
    cc.findings.extend(abi3_findings)
    # Name the offending symbols in the coverage row (rendered verbatim in
    # text and carried in JSON) so a CI artifact tells the user WHICH import
    # to fix — the cross-check summary only reports a per-kind count, which
    # would otherwise hide the symbol in Change.detail/new_value (Codex
    # review). Capped so a pathological module cannot flood the report.
    offending: list[str] = []
    for f in abi3_findings:
        offending.extend(f.new_value if isinstance(f.new_value, list) else [])
    detail = (
        f"{len(py_ext.cpython_imports)} CPython import(s) audited against "
        f"Py_LIMITED_API {abi3_floor[0]}.{abi3_floor[1]}; "
        f"{len(abi3_findings)} violation finding(s)"
    )
    if offending:
        shown = ", ".join(offending[:20])
        more = f" (+{len(offending) - 20} more)" if len(offending) > 20 else ""
        detail += f" — outside the stable ABI: {shown}{more}"
    cc.coverage.append({"layer": "abi3_audit", "status": "ran", "detail": detail})


def _build_scan_poi(
    baseline: Path | None,
    seeded: bool,
    collect_mode: str,
    binary: Path,
    lang: str,
    changed: list[str],
    risk: RiskScore,
    pattern: Any,
) -> tuple[Any, Any]:
    """Build the D7 points-of-interest work-list + the baseline export view used.

    Returns ``(poi, poi_baseline)``. The export-delta walk needs both sides'
    export tables, so a cheap header-free L0 view of the candidate and baseline is
    loaded only when there is a baseline to diff against (else a wasted parse).
    """
    needs_export_delta_poi = (
        baseline is not None
        and seeded
        and collect_mode in {"source-changed", "graph-full"}
    )
    poi_baseline = (
        _load_exports_for_poi(baseline, lang) if needs_export_delta_poi else None
    )
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
    return poi, poi_baseline


def _append_replay_scope_advisory(
    advisories: list[str], seeded: bool, collect_mode: str, sources: Path | None,
) -> None:
    """Advise (ADR-035 P3) when an unseeded run falls back to headers-only replay.

    Only when L4 replay can actually run (a --sources tree is present) does the
    headers-only fallback apply; firing otherwise would report a replay that never
    happened (CodeRabbit review).
    """
    if not seeded and collect_mode == "source-changed" and sources is not None:
        advisories.append(
            "no --since/--changed-path seed; the L4 replay and the L5 call-graph "
            "pass both cover the public-API surface (headers-only) instead of a "
            "focused diff — cost grows with the project, not the change. Pass "
            "--since <ref> or --changed-path to scope both to the changed TUs."
        )


def _check_scan_evidence_contract(
    advisories: list[str],
    new_snap: Any,
    collect_mode: str,
    pinned_explicit: bool,
    sources: Path | None,
    effective_build_info: Path | None,
    eff_depth_enum: EvidenceDepth,
    resolved: SourceMethod,
) -> None:
    """Fail-loud on a pinned depth with no evidence; else advise on missing L3.

    ADR-037 D5 (#2 auto-strict): a depth the user *explicitly pinned* with no
    source evidence at all (no --sources/--build-info, and the trusted --config
    build.query flow produced no L3) is a usage-contract violation → raise. When a
    source input *was* supplied but L3 still came back empty, that stays a pointed
    advisory naming the remedy. The implicit 'auto' default never errors here.
    """
    if collect_mode == "off":
        return
    gave_source_input = sources is not None or effective_build_info is not None
    l3 = _l3_collected(new_snap)
    if pinned_explicit and not gave_source_input and not l3:
        raise _EvidenceContractError(
            f"pinned depth '{eff_depth_enum.value}' (source-method {resolved.value}) "
            "needs source evidence, but no --sources/--build-info was given — there "
            "is nothing to collect L3/L4/L5 from. Pass --sources <tree> or "
            "--build-info <dir|compile_commands.json> (or a trusted --config plus "
            "--allow-build-query), or drop the pin / use the default 'auto' for a "
            "best-effort binary scan. (Pinned depths are a contract.)"
        )
    if gave_source_input and not l3:
        advisories.append(
            f"requested depth '{eff_depth_enum.value}' (source-method "
            f"{resolved.value}) needs an L3 compile database, but none was found — "
            "L3/L4/L5 were skipped. Provide one with --build-info/--compile-db (a "
            "compile_commands.json or build dir), or a trusted --config plus "
            "--allow-build-query to generate it."
        )


def _check_scan_budget(
    budget: str | None, budget_s: float | None, elapsed: float,
) -> None:
    """Budget overflow FAILS, never shrinks scope (ADR-035 D3)."""
    if budget_s is not None and elapsed > budget_s:
        raise _BudgetOverflow(
            f"error: --budget {budget} exceeded "
            f"({elapsed:.1f}s > {budget_s:.0f}s). "
            "Pin a shallower level or raise the budget; a budget never silently "
            "shrinks the pinned scope."
        )


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
    abi3_floor: tuple[int, int] | None = None,
) -> ScanCoreResult:
    """The shared scan orchestration (classify → always-on tier → level → compare).

    Pure of click/argv: it takes already-resolved inputs, runs the engine, and
    returns a :class:`ScanCoreResult`. Raises :class:`_BudgetOverflow` on budget
    overflow (the CLI maps it to exit 5). This is the one body the CLI,
    ``service.run_scan``, and the MCP scan tool share (ADR-035 D10).
    """
    stage_timings: dict[str, float] = {}

    def _record_stage(name: str, started: float) -> None:
        stage_timings[name] = time.monotonic() - started

    # --- always-on tier: compiler-free pattern pre-scan (S3) ------------------
    # Runs *before* the snapshot build so its escalation triggers feed the D7
    # points-of-interest work-list that focuses the (expensive) source replay.
    # Scope: a *seeded* diff (even an empty one) confines the scan to the changed
    # set — an empty seed (no-op PR) scans nothing, preserving the empty-diff
    # scope; only a genuinely *unseeded* run (no --since/--changed-path) falls
    # back to the whole-tree scan (Codex review).
    pattern_roots = scan_pattern_roots(list(headers), sources, eff_depth_enum)
    _stage = time.monotonic()
    pattern = scan_files(pattern_roots, changed if seeded else None)
    _record_stage("pattern_scan", _stage)

    # --- D7 points-of-interest: cheap facts steer the expensive scan ----------
    # Floor = the directly-changed paths (always included); the pattern triggers,
    # risk score, and the L0↔L2 export deltas only *add* candidates, never drop a
    # changed TU (ADR-035 D7). The export-delta walk needs both sides' export
    # tables up front, so read a cheap, header-free L0 view of the candidate and
    # baseline here (no castxml/L3-L5); the one expensive collection still runs
    # once, below, with the resulting focus seed. The candidate view is only
    # loaded when there is a baseline to diff it against — the delta walk consumes
    # the two together, so loading it baseline-less would be a wasted L0/L1 parse.
    _stage = time.monotonic()
    poi, poi_baseline = _build_scan_poi(
        baseline, seeded, collect_mode, binary, lang, changed, risk, pattern
    )
    _record_stage("poi", _stage)

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
    _append_replay_scope_advisory(advisories, seeded, collect_mode, sources)
    effective_allow_query, _query_advisory = resolve_effective_allow_query(
        allow_build_query, build_config, collect_mode, level_explicit, resolved
    )
    if _query_advisory is not None:
        advisories.append(_query_advisory)

    _stage = time.monotonic()
    new_snap, eff_includes = _build_new_snapshot(
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
    _record_stage("candidate_snapshot", _stage)
    l4_cov = _source_abi_coverage(new_snap)
    advisories.extend(l4_coverage_advisories(l4_cov))

    # --- level-vs-evidence: fail-loud on missing input, advise otherwise ------
    # A deep depth (build/source/full → collect_mode != "off") needs an L3 compile
    # database; without one the L3/L4/L5 layers cannot be collected.
    _check_scan_evidence_contract(
        advisories, new_snap, collect_mode, pinned_explicit,
        sources, effective_build_info, eff_depth_enum, resolved,
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
    _stage = time.monotonic()
    preproc = run_preprocessor_scan(pp_build, _expand_public_headers(list(headers)))
    _record_stage("preprocessor_scan", _stage)

    # --- always-on tier: intra-version cross-source checks (D4) ---------------
    # The resolved changed-path set is handed to the engine so
    # ``public_to_internal_dependency`` can elevate a finding whose internal
    # target was touched this revision (ADR-035 D4 "L5 reachability ↔ PR
    # changed files").
    # The changed-path set handed to the engine also carries the TUs the D7
    # export-delta walk resolved (symbol_tus), so ``public_to_internal_dependency``
    # elevates a finding whose internal target sits in a TU this revision touched
    # *via a changed export* — not only the literally git-changed files.
    _stage = time.monotonic()
    cc = run_crosschecks(
        new_snap,
        CrosscheckConfig(
            enabled=frozenset(enabled_checks),
            changed_paths=frozenset(changed) | set(symbol_tus),
        ),
    )
    _record_stage("crosschecks", _stage)

    # --- stable-ABI (abi3) audit (opt-in via --abi3) --------------------------
    if abi3_floor is not None:
        _run_abi3_audit(new_snap, abi3_floor, binary, cc)

    # --- pinned-level baseline comparison (if any) ----------------------------
    diff_summary: dict[str, Any] | None = None
    if baseline is not None and scan_mode is not ScanMode.AUDIT:
        _stage = time.monotonic()
        verdict, exit_code, diff_summary = _run_baseline_compare(
            baseline,
            binary,
            new_snap,
            [],
            lang,
            collect_mode,
            list(headers),
            # Effective (seeded) includes so the baseline native parse gets the same
            # build-derived dependency include dirs as the candidate (Codex review).
            list(eff_includes),
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
        _record_stage("baseline_compare", _stage)
    else:
        if baseline is not None:
            click.echo(
                "note: --audit ignores --baseline (intra-version scan).", err=True
            )
        verdict, exit_code = _audit_exit_code(cc.findings, severities)

    elapsed = time.monotonic() - start
    _check_scan_budget(budget, budget_s, elapsed)

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
        stage_timings=stage_timings,
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
