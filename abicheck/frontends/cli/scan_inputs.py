# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""``scan``'s own CLI input parsing and flag resolution.

ADR-061 D5/definition-of-done items 3-4: ``cli_scan.py`` sat exactly at the
2000-line hard cap, so no change to the repository's most-edited module was
possible without first moving responsibility out of it. This is the first of
three extractions (see also ``cli_scan_emit.py`` and
``cli_scan_artifact_set.py``).

Everything here turns raw CLI text into a typed value or rejects it -- a
budget string into seconds, a crosscheck spec into a level mapping, a
``--changed`` seed into concrete paths, an ABI3 floor into a version tuple --
and decides nothing about how a scan runs or how its result is reported.
``cli_scan.py`` re-exports every name unchanged, so existing call sites and
the tests/comments that reference them by their original spelling resolve
exactly as before.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

from ...buildsource.scan_levels import EvidenceDepth, SourceMethod
from ...workflows.scan_config import RiskScore

#: Suffixes ``time``-style duration strings accept (``15m``, ``900s``, ``1h``).
_DURATION_UNITS: dict[str, int] = {"s": 1, "m": 60, "h": 3600}

if TYPE_CHECKING:
    # Annotation-only: a frontend renders an already-decided outcome, it
    # does not need the engine's result type at runtime. Importing it under
    # TYPE_CHECKING keeps this `frontends` module off an engine module that
    # has no ADR-061 owner yet (`scan_engine.py`), which `check_architecture`
    # correctly rejects for a migrated package.
    pass


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
    from ...stable_abi import parse_abi3_version

    floor = parse_abi3_version(abi3)
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


