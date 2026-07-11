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

"""Baseline-compare and dry-run-estimate helpers for :mod:`abicheck.cli_scan`.

Split out of the (near-cap) ``cli_scan`` module: these are the two ``scan``
sub-flows that stand apart from the always-on core pipeline —

* ``scan --baseline`` (:func:`_run_baseline_compare` + its native-library
  sniff :func:`_baseline_is_native_library`), and
* ``scan --estimate`` (:func:`_emit_estimate`), plus the small header-provenance
  helpers they share with the core (:func:`_public_provenance_set`,
  :func:`_expand_public_headers`) and the ``--risk-rules`` loader
  (:func:`_load_risk_rules`).

``cli_scan`` re-imports every name below so the historical import paths
(``abicheck.cli_scan._run_baseline_compare`` etc., relied on by the scan tests
and ``service_scan``) keep resolving unchanged. The heavy engine dependencies
(``service``, ``cli_buildsource``, ``errors``, ``binary_utils``, ``yaml``) stay
function-local exactly as they were in ``cli_scan`` so import time is unaffected.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

from .buildsource.risk import RiskRules
from .buildsource.scan_levels import EvidenceDepth, SourceMethod
from .cli import _safe_write_output

if TYPE_CHECKING:
    from .service_scan import CompileContext


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
    binary: Path,
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
    """Compare *new_snap* against *baseline*, preserving scan authority.

    Single-version cross-source findings are reported in the scan's dedicated
    ``crosscheck`` block and stay advisory for baseline comparisons unless the
    maintainer explicitly promotes one with ``--crosscheck KEY=error``. They are
    not folded into ``extra_changes`` by default: doing so lets a candidate-side
    evidence hygiene finding such as ``header_build_context_mismatch`` turn a
    clean old/new artifact diff into an ``API_BREAK`` false positive. Real
    old/new embedded build/source drift is still diffed below via
    ``prepare_embedded_build_source``.

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

    # Preserve hard L0 removals even when the richer header/source view cannot
    # prove public-header ownership for the removed entity.  A source/full scan
    # may parse both sides' headers through different consumer macro contexts;
    # in fixtures such as case97 the old library exported a function that the
    # old header exposes only under a consumer macro, so the final public-surface
    # comparison can otherwise filter the old-only ELF fact away.  Re-reading the
    # already-loaded snapshots without public-surface scoping and carrying only
    # the hard ELF-only removal kind keeps the L0 authority while avoiding the
    # older false-positive class where advisory cross-check findings were folded
    # into the verdict wholesale.
    l0_hard_removals: list[Any] = []
    if not symbols_only:
        l0_old_snap = resolve_input(
            baseline,
            [],
            [],
            version="",
            lang=lang,
            symbols_only=True,
        )
        l0_new_snap = resolve_input(
            binary,
            [],
            [],
            version="",
            lang=lang,
            symbols_only=True,
        )
        l0_diff = compare_snapshots(
            l0_old_snap,
            l0_new_snap,
            extra_changes=[],
            scope_to_public_surface=False,
        )
        l0_hard_removals = [
            change
            for change in getattr(l0_diff, "breaking", ())
            if getattr(getattr(change, "kind", None), "value", None)
            == "func_removed_elf_only"
        ]
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
        [*extra_changes, *l0_hard_removals],
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
