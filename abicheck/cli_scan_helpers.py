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

"""Pure helpers for :mod:`abicheck.cli_scan`.

Split out of the (large, near-cap) ``cli_scan`` module: these are click-free,
side-effect-free functions that ``_render_text`` and ``run_scan_core`` compose.
Keeping them here holds ``cli_scan.py`` under the 2000-line hard cap while
decomposing the two long methods into legible pieces.

No import cycle: this module imports only from :mod:`abicheck.buildsource`
and, function-locally, the dependency-free
:mod:`abicheck.frontends.cli.options.secondary_output` leaf (never
:mod:`abicheck.cli_options` itself -- see
:func:`reject_incoherent_scan_secondary_output`'s own docstring for why that
distinction matters). The render helpers take the ``ScanOutcome`` dataclass
as ``Any`` rather than importing it from :mod:`abicheck.cli_scan` (even under
``TYPE_CHECKING``), which would form a cli_scan ↔ cli_scan_helpers cycle the
import-cycles gate flags.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

from .buildsource.scan_levels import EvidenceDepth

if TYPE_CHECKING:
    from .buildsource.scan_levels import SourceMethod


# --- operand/flag validation (click-free, no ScanOutcome dependency) --------


def reject_incoherent_scan_operands(
    *,
    artifact: Path | None,
    artifact_set: tuple[str, ...],
    against: Path | None,
    manifest_path: Path | None = None,
) -> None:
    """Reject operand/flag combinations ``scan`` cannot serve.

    ``--artifact-set`` is a repeatable option (CLI cleanup phase two, PR 5):
    ``artifact_set`` is the tuple Click collects, empty when the flag was
    never given at all -- so "supplied" is exactly ``bool(artifact_set)``,
    with no truthiness/``is not None`` mismatch left to reintroduce the
    CodeRabbit-caught bug the comma-separated single-string form once had
    (an empty ``--artifact-set ""`` still yields a non-empty one-element
    tuple, so it is correctly treated as *supplied* here and rejected by the
    empty-member check below, never silently read as "not set"). Any empty
    or blank member is rejected explicitly rather than left to collapse to
    ``Path("") == Path(".")`` and audit the whole CWD (CodeRabbit review,
    preserved from the comma-separated form's own fix). ``--artifact-set``
    is audit-only -- there is no old side for a set -- so ``--against`` is
    rejected with it. ``--dry-run`` *is* supported (CLI cleanup phase two,
    PR 5's set-mode-semantics slice) -- see
    :func:`abicheck.frontends.cli.artifact_set_dry_run.render_artifact_set_dry_run`
    -- so it is no longer rejected here.

    ``--bundle-system-providers`` was the mirror case (it only meant
    something *for* a set) until CLI cleanup phase two, PR J removed the
    flag entirely -- the system-provider allow-list extension is sourced
    only from ``.abicheck.yml``'s ``bundle:`` block now, which has no
    per-run "supplied without --artifact-set" state to reject.
    ``--manifest`` (PR H, ADR-056 D2) is the one remaining mirror case: an
    expected-provider ownership assertion only means something checked
    against a declared set.
    """
    if any(not member.strip() for member in artifact_set):
        raise click.UsageError("--artifact-set must not be empty.")
    supplied = bool(artifact_set)
    if (artifact is not None) == supplied:
        raise click.UsageError(
            "scan requires exactly one of ARTIFACT or --artifact-set."
        )
    if supplied:
        if against is not None:
            raise click.UsageError(
                "--against is not supported with --artifact-set "
                "(audit-only -- no old side for a set)."
            )
    else:
        if manifest_path is not None:
            raise click.UsageError("--manifest requires --artifact-set.")


def load_artifact_set_manifest(manifest_path: Path | None) -> Any:
    """Load ``scan --artifact-set --manifest``'s optional ownership manifest.

    Split out of ``cli_scan._run_artifact_set`` purely to keep that module
    under the AI-readiness 2000-line hard cap -- the load itself mirrors
    ``compare --instantiation-manifest``'s own
    ``cli_compare_release_helpers._analyze_release_bundle`` exactly: a
    malformed ``--manifest`` is an explicit user input error, not an
    environmental quirk, so it fails loudly (``click.ClickException``)
    rather than degrading. Returns ``None`` when *manifest_path* is
    ``None`` (the common case -- no ``--manifest`` given).
    """
    if manifest_path is None:
        return None
    from .bundle import load_manifest

    try:
        return load_manifest(manifest_path)
    except Exception as exc:
        raise click.ClickException(
            f"Failed to load manifest {manifest_path}: {exc}",
        ) from exc


def resolve_artifact_set_paths(spec: tuple[str, ...]) -> tuple[list[Path], bool]:
    """``--artifact-set`` values → ``(paths, explicit)`` (ADR-056).

    ``spec`` is the tuple Click's repeatable ``--artifact-set`` collects (CLI
    cleanup phase two, PR 5 -- the comma-separated single-string form this
    replaced is gone, no alias). A single value naming a directory expands
    to every discoverable shared library in it (``explicit=False`` -- an
    unsupported file found this way is silently skipped, mirroring
    ``build_bundle_snapshot``'s directory-scan behavior); anything else is
    an explicit path list, one member per occurrence, every member of which
    must resolve (``explicit=True``, per :func:`bundle.discover_artifact_set`).

    Moved here from ``cli_scan.py`` (PR H, CLI cleanup phase two) purely to
    keep that module under its 2000-line hard cap -- unchanged otherwise.
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


def reject_incoherent_scan_secondary_output(
    *,
    dry_run: bool,
    output: Path | None,
    secondary_fmt: str | None,
    secondary_output: Path | None,
    artifact_set: tuple[str, ...],
) -> None:
    """Reject a ``--secondary-*`` combination that cannot mean anything.

    The four checks common to any command carrying the shared
    ``secondary_output.secondary_output_options`` pair (dry-run,
    half-given pair either direction, same-file collision) now live once in
    ``secondary_output.reject_incoherent_secondary_output`` -- previously
    duplicated byte-for-byte from ``compare``'s own
    ``_reject_incoherent_compare_flags`` (Codex review). Imported from the
    dependency-free ``frontends.cli.options.secondary_output`` leaf module
    rather than from ``cli_options`` itself: this module sits on an existing
    import path back into ``cli_options`` (``cli_options -> cli_resolve ->
    service_scan -> scan_engine -> cli_scan_helpers``), so a
    ``cli_scan_helpers -> cli_options`` edge would close a real cycle the
    AI-readiness ``import-cycle-growth`` gate rejects -- see that leaf
    module's own docstring. This wrapper adds only the one check specific to
    ``scan``: ``--artifact-set`` has no single-artifact report to render a
    second time at all.
    """
    from .frontends.cli.options import (
        reject_incoherent_secondary_output as _reject_shared,
    )

    if artifact_set and (
        secondary_fmt is not None or secondary_output is not None
    ):
        raise click.UsageError(
            "--write is not supported with "
            "--artifact-set -- there is no single-artifact report to render "
            "a second time."
        )
    _reject_shared(
        dry_run=dry_run,
        output=output,
        secondary_fmt=secondary_fmt,
        secondary_output=secondary_output,
    )


# --- coverage-row helpers (snapshot → report rows) ---------------------------


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


def _source_abi_coverage(snap: Any) -> dict[str, Any]:
    """Return the embedded L4 coverage dict, if present."""
    pack = getattr(snap, "build_source", None)
    surface = getattr(pack, "source_abi", None) if pack is not None else None
    cov = getattr(surface, "coverage", None) if surface is not None else None
    return dict(cov or {})


def _pack_coverage(snap: Any) -> list[dict[str, Any]]:
    """Read the L3/L4/L5 coverage rows from a snapshot's embedded pack, if any."""
    pack = getattr(snap, "build_source", None)
    if pack is None:
        return [
            {
                "layer": layer,
                "status": "not_collected",
                "detail": "no build/source evidence collected "
                "(pass --sources, or a deeper --depth)",
            }
            for layer in ("L3_build", "L4_source_abi", "L5_source_graph")
        ]
    return [
        c.to_dict() if hasattr(c, "to_dict") else c
        for c in pack.manifest.coverage
    ]


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
            return bool(row.get("status") != "not_collected")
    return False


# --- run_scan_core helpers ---------------------------------------------------


def _uses_debug_presence_only(depth: EvidenceDepth) -> bool:
    """True when L2/L3 evidence is collected elsewhere, so DWARF stays cheap."""
    return depth in {EvidenceDepth.HEADERS, EvidenceDepth.BUILD}


def scan_pattern_roots(
    headers: list[Path],
    sources: Path | None,
    eff_depth_enum: EvidenceDepth,
) -> list[Path]:
    """Roots the compiler-free pattern pre-scan (S3) walks for the given depth.

    The header roots are always scanned; the ``--sources`` tree is added only
    when the depth actually reaches source evidence (not BINARY/HEADERS).
    """
    pattern_roots: list[Path] = [*headers]
    if sources is not None and eff_depth_enum not in {
        EvidenceDepth.BINARY,
        EvidenceDepth.HEADERS,
    }:
        pattern_roots.append(sources)
    return pattern_roots


def l4_coverage_advisories(l4_cov: dict[str, Any]) -> list[str]:
    """Advisory notes derived from the L4 source-ABI coverage dict."""
    advisories: list[str] = []
    if l4_cov.get("scope_widened_to_full"):
        advisories.append(
            "headers-only source replay widened to all compile units because no "
            "include graph/public-header target ownership could narrow it. Provide "
            "depfile/include graph evidence or seed with --since/--changed-path to "
            "avoid full fanout."
        )
    uncovered = int(l4_cov.get("public_headers_uncovered", 0) or 0)
    if uncovered:
        advisories.append(
            f"headers-only source replay used the include graph and skipped full "
            f"fanout, but {uncovered} public header(s) were not reached by any "
            "selected TU; source-only coverage is partial for those headers."
        )
    exported = int(l4_cov.get("exported_symbols", 0) or 0)
    matched = int(l4_cov.get("matched_symbols", 0) or 0)
    parsed = int(l4_cov.get("compile_units_parsed", 0) or 0)
    if exported and parsed and matched == 0:
        advisories.append(
            f"L4 source replay parsed {parsed} TU(s) but matched 0/{exported} "
            "exported symbol(s); source-link evidence is degraded. Check mangled "
            "symbol matching/public-header roots before relying on source-only "
            "findings."
        )
    return advisories


def resolve_effective_allow_query(
    allow_build_query: bool,
    build_config: Path | None,
    collect_mode: str,
    level_explicit: bool,
    resolved: SourceMethod,
) -> tuple[bool, str | None]:
    """Resolve whether a trusted --config build.query is auto-enabled (ADR-037 D4).

    Returns ``(effective_allow_query, advisory_or_None)``.

    level-implies-query (ADR-037 D4): an explicit, *trusted* --config that
    defines a build.query, together with an *explicitly pinned* deep level
    (--source-method/--depth, level_explicit), is itself consent to run that
    query -- no separate opt-in needed for a level the user explicitly asked
    for. ``allow_build_query`` stays a real parameter here (always ``False``
    now that scan's own --allow-build-query CLI flag is gone, CLI audit
    PR 5/5 -- confirmed via this function's own callers that nothing besides
    the flag itself ever set it True, so the guard below is dead in the
    ``True`` direction but harmless to leave for defensive clarity), guarding
    a real effect: it auto-enables the query and returns an advisory
    explaining why, rather than requiring an already-explicit level pin to
    also carry a separate flag. Trusted = an explicit --config path
    (build_config is not None here; an auto-discovered source-tree config is
    resolved later in embed_build_source and never reaches this gate), so
    this never runs an attacker-controlled command. Crucially it does NOT fire
    for the default mode preset (a plain `scan`/`--audit` with `--sources` whose
    collect_mode is already non-off) — only an explicit deep level counts, so a
    --config passed purely for project settings never silently runs a subprocess
    (Codex review). No-op when the config defines no query.
    """
    if not (
        not allow_build_query
        and build_config is not None
        and collect_mode != "off"
        and level_explicit
    ):
        return allow_build_query, None

    from .workflows.extraction import load_build_config

    try:
        _cfg = load_build_config(build_config)
    except Exception:  # malformed config surfaces later in the real load
        _cfg = None
    if _cfg is not None and _cfg.query:
        advisory = (
            f"level {resolved.value} with a trusted --config defining "
            "build.query: auto-enabled the query to collect L3+ evidence."
        )
        return True, advisory
    return allow_build_query, None


# --- _render_text section helpers --------------------------------------------


def render_summary_lines(out: Any) -> list[str]:
    """The report header block: mode/level, risk, changed paths, advisories, POI."""
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
    if out.stage_timings:
        timing = ", ".join(
            f"{name}={seconds:.2f}s"
            for name, seconds in sorted(out.stage_timings.items())
        )
        lines.append(f"  timings: {timing}")

    poi_counts = out.poi.get("counts_by_reason") or {}
    if poi_counts:
        focus = ", ".join(f"{k}×{v}" for k, v in sorted(poi_counts.items()))
        lines.append(
            f"  focus (POI): {out.poi.get('total', 0)} point(s) "
            f"[{focus}] → {len(out.poi.get('changed_paths') or [])} path(s), "
            f"{len(out.poi.get('symbols') or [])} symbol(s)"
        )
    return lines


def render_coverage_lines(out: Any) -> list[str]:
    """The always-present per-layer coverage table."""
    lines: list[str] = ["", "Coverage"]
    for row in out.coverage:
        lines.append(
            f"  {row['layer']:<18} {row['status']:<13} {row.get('detail', '')}"
        )
    return lines


def render_crosscheck_lines(out: Any) -> list[str]:
    """The cross-source / ABI-hygiene findings block (empty when none)."""
    if not out.crosscheck.get("counts_by_check"):
        return []
    lines: list[str] = [""]
    lines.append(
        "ABI-hygiene catalog (intra-version, advisory)"
        if out.audit
        else "Cross-source findings (advisory)"
    )
    for kind, n in sorted(out.crosscheck["counts_by_check"].items()):
        sev = out.crosscheck_severities.get(kind, "warning")
        lines.append(f"  [{sev}] {kind}: {n}")
    return lines


def render_pattern_lines(out: Any) -> list[str]:
    """The compiler-free pattern pre-scan facts block (empty when none)."""
    pat_counts = out.pattern.get("counts_by_kind") or {}
    if not pat_counts:
        return []
    lines: list[str] = ["", "Pattern pre-scan facts (advisory)"]
    for kind, n in sorted(pat_counts.items()):
        lines.append(f"  {kind}: {n}")
    return lines


def render_preprocessor_lines(out: Any) -> list[str]:
    """The S2 preprocessor pre-scan facts block (empty when none)."""
    pp_div = out.preprocessor.get("divergences") or []
    pp_leaks = out.preprocessor.get("leaks") or []
    if not (pp_div or pp_leaks):
        return []
    lines: list[str] = ["", "Preprocessor pre-scan facts (S2, advisory)"]
    for d in pp_div:
        lines.append(
            f"  macro divergence: {d['macro']} ({d['n_values']} values across TUs)"
        )
    for leak in pp_leaks:
        lines.append(
            f"  {leak['leak_class']}-header leak: "
            f"{leak['public_header']} → {leak['leaked_header']}"
        )
    return lines


def render_baseline_lines(out: Any, *, show_suppressed: bool = False) -> list[str]:
    """The baseline comparison summary block (empty without a baseline diff).

    Beyond the counts, lists the actual findings (kind/symbol/location) the
    baseline compare produced — a bare "breaking=1" count is not actionable
    without naming what broke (see ``_run_baseline_compare``'s ``findings``).

    Suppression must survive suppression: the counts line always includes
    ``suppressed=N`` when ``--suppress`` removed anything (a suppressed
    finding is a report-suppression decision, not silence -- a reviewer who
    never passes ``--show-suppressed`` should still see *that* something was
    withheld, even without the itemized list). ``--show-suppressed`` (*
    *show_suppressed*) additionally itemizes each one the same way the
    gating findings above are itemized, tagged with its
    ``pre_suppression_bucket`` (what it would have counted as) and the
    ``--suppress`` rule that matched it -- ``--format json``'s ``diff.
    suppressed[]`` already carries this unconditionally; this only changes
    what the text renderer prints.
    """
    if out.diff_summary is None:
        return []
    if "reason" in out.diff_summary:
        # ADR-050 D2: the baseline compare hard-failed on a profile/scope
        # mismatch before producing any counts -- diff_summary is just
        # {"reason": ...} here, not the normal breaking/api_break/risk/
        # compatible shape below.
        return ["", "Baseline comparison", f"  not comparable: {out.diff_summary['reason']}"]
    counts_line = (
        f"  breaking={out.diff_summary['breaking']} "
        f"api_break={out.diff_summary['api_break']} "
        f"risk={out.diff_summary['risk']} "
        f"compatible={out.diff_summary['compatible']}"
    )
    suppressed_count = out.diff_summary.get("suppressed_count", 0)
    if suppressed_count:
        counts_line += f" suppressed={suppressed_count}"
    lines = ["", "Baseline comparison", counts_line]
    # Codex review: the JSON summary has carried this since
    # `_baseline_summary` first surfaced it, but the text renderer (the
    # *default* format) never printed it — a byte-identical-binaries warning
    # was invisible in an ordinary `scan --against` run's console output.
    for warning in out.diff_summary.get("coverage_warnings", []):
        lines.append(f"  Warning: {warning}")
    for f in out.diff_summary.get("findings", []):
        loc = f" ({f['source_location']})" if f.get("source_location") else ""
        symbol = f.get("symbol") or "?"
        lines.append(f"    [{f['bucket']}] {f['kind']}: {symbol}{loc}")
    if out.diff_summary.get("findings_truncated"):
        lines.append(
            "    … additional findings omitted; rerun `compare` for the full list"
        )
    if suppressed_count and show_suppressed:
        lines.append("  Suppressed findings:")
        for f in out.diff_summary.get("suppressed", []):
            loc = f" ({f['source_location']})" if f.get("source_location") else ""
            symbol = f.get("symbol") or "?"
            bucket = f.get("pre_suppression_bucket") or "?"
            rule = f.get("suppression_rule") or "?"
            lines.append(
                f"    [{bucket} → suppressed] {f['kind']}: {symbol}{loc} "
                f"(rule: {rule})"
            )
        if out.diff_summary.get("suppressed_truncated"):
            lines.append(
                "    … additional suppressed findings omitted; rerun with "
                "--format json for the full list"
            )
    elif suppressed_count:
        lines.append("  (pass --show-suppressed to itemize)")
    lines.extend(_severity_gate_lines(out.diff_summary))
    return lines


def _severity_gate_lines(diff_summary: dict[str, Any]) -> list[str]:
    """The severity-gate explanation, when this scan resolved that scheme.

    Text is the *default* format, so the JSON ``severity`` block alone left
    the common case unexplained: an additions-only scan under
    a ``severity.addition: error`` config printed ``Verdict: COMPATIBLE`` and exited
    1 with nothing naming the cause -- indistinguishable in a CI log from
    ADR-049 §7's orthogonal contract-coverage 1 (Codex review). Empty for a
    legacy-scheme scan, which runs no severity gate and whose text output is
    therefore unchanged.
    """
    gate = diff_summary.get("severity")
    if not isinstance(gate, dict):
        return []
    exit_code = gate.get("exit_code")
    blocking_categories = gate.get("blocking_categories") or []
    if not gate.get("blocking"):
        # Stated rather than omitted: "the gate ran and cleared it" is a
        # different fact from "no gate ran", and only the former explains an
        # exit 0 on a diff whose verdict alone would have been 2 or 4.
        return ["  severity gate: pass (no error-level findings)"]
    blamed = ", ".join(str(c) for c in blocking_categories) or "unspecified"
    return [f"  severity gate: exit {exit_code} — blocking: {blamed}"]


def render_verdict_lines(out: Any) -> list[str]:
    """The always-present verdict / elapsed footer."""
    lines: list[str] = ["", f"Verdict: {out.verdict}"]
    if out.budget_s is not None:
        lines.append(f"Elapsed: {out.elapsed_s:.2f}s / budget {out.budget_s:.0f}s")
    return lines
