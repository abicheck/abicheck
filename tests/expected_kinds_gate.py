# SPDX-License-Identifier: Apache-2.0
"""Per-frontend, per-mode strict ``expected_kinds`` gate of the catalog lanes.

A catalog case's top-level ``expected`` verdict is coarse: a case can reach the
right severity through an unrelated detector while the ChangeKinds it exists
to calibrate never fire. Until this module, ``validate_examples.py`` reported a
missing ``expected_kinds`` entry (or a present ``expected_absent_kinds`` entry)
as a side signal and still counted the cell as ``PASS``; the clang header lane
passed case17/43/74-77/80 for weeks without the layout kinds those cases are
about.

In the *native* lanes (:data:`ENFORCED_MODES`) on the measured target
(:data:`ENFORCED_TARGETS`, Linux x86_64) such a violation now FAILs the cell,
unless ``ground_truth.json`` declares it for exactly that header frontend and
mode under ``expected_kinds_gaps``::

    "expected_kinds_gaps": {
      "castxml": [
        {"kinds": ["template_param_default_changed"],
         "modes": ["debug-headers"],            # optional; default: both
         "reason": "castxml 0.7 emits no uninstantiated template ...",
         "known_gap": "A header-only default template-argument change"}
      ]
    }

(a single gap object is accepted in place of the one-element list.) A declared
kind that the lane *does* report again (or no longer reports, for an absent
kind) is a **stale** declaration and also FAILs the cell, so a declaration
cannot outlive its gap. ``known_gap`` must start a heading of
``docs/contribute/known-gaps.md``: every declared gap is an open, indexed gap.

The verdict-level ``known_gap``/``XFAIL`` mechanism is separate and unchanged:
an XFAIL cell (a declared wrong verdict) is not kinds-checked.

Stdlib-only apart from the lazily imported header-backend resolver, so the
schema half is usable before ``pip install -e .``.
"""

from __future__ import annotations

import platform
import re
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, NamedTuple

#: The header frontends a gap can be declared for (``ABICHECK_AST_FRONTEND``).
FRONTENDS = ("castxml", "clang")
#: The artifact variants whose cells the kinds gate FAILs. The reduced-evidence
#: variants (release/stripped headers) lose DWARF by design; their verdicts are
#: reported-only and guarded by ``check_stripped_fp.py``, so their kinds signal
#: stays reported-only too.
ENFORCED_MODES = ("debug-headers", "build-source")
#: The (platform, architecture) targets whose cells the gate FAILs: the ones
#: every declaration in ground_truth.json was measured on. The catalog's kinds
#: are calibrated on Linux ELF; elsewhere a kind such as
#: ``func_removed_elf_only`` cannot fire at all, and no lane there has been
#: measured to declare its gaps, so the signal stays reported-only.
ENFORCED_TARGETS = (("linux", "x86_64"),)
GAPS_FIELD = "expected_kinds_gaps"
_GAP_KEYS = frozenset({"kinds", "modes", "reason", "known_gap"})
_REQUIRED_GAP_KEYS = frozenset({"kinds", "reason", "known_gap"})
#: Retired by this gate: one unscoped kind per case could not say which lane
#: has the gap, and could not go stale.
RETIRED_FIELDS = ("known_kind_gap", "known_kind_gap_note")
KNOWN_GAPS_PATH = Path(__file__).resolve().parent.parent / (
    "docs/contribute/known-gaps.md"
)


class KindsGap(NamedTuple):
    """One declared ``(frontend, modes) -> kinds`` gap of a case."""

    frontend: str
    kinds: tuple[str, ...]
    modes: tuple[str, ...]
    reason: str
    known_gap: str


class KindsVerdict(NamedTuple):
    """The kinds gate's outcome for one lane cell."""

    signal: str  # ok | gap-observed | declared-gap | stale-gap | mismatch | n/a
    detail: str
    fails: bool  # whether the cell must FAIL on it


NOT_CHECKED = KindsVerdict("n/a", "", False)


def known_gap_headings(path: Path = KNOWN_GAPS_PATH) -> tuple[str, ...]:
    """Every Markdown heading text of the known-gaps index."""
    text = path.read_text(encoding="utf-8")
    return tuple(
        m.group(1).strip() for m in re.finditer(r"^#{2,6}\s+(.+)$", text, re.M)
    )


def _gap_objects(raw: object) -> list[object]:
    return list(raw) if isinstance(raw, list) else [raw]


def validate_entry(
    case: str, entry: Mapping[str, Any], headings: Iterable[str]
) -> list[str]:
    """Schema errors of *entry*'s ``expected_kinds_gaps`` (empty when valid)."""
    errors = [
        f"{case}: {field} is retired; declare the gap per frontend under {GAPS_FIELD}"
        for field in RETIRED_FIELDS
        if field in entry
    ]
    raw = entry.get(GAPS_FIELD)
    if raw is None:
        return errors
    if not isinstance(raw, dict) or not raw:
        return [*errors, f"{case}: {GAPS_FIELD} must be a non-empty object"]
    heading_list = tuple(headings)
    checkable = set(entry.get("expected_kinds") or []) | set(
        entry.get("expected_absent_kinds") or []
    )
    for frontend, value in raw.items():
        where = f"{case}: {GAPS_FIELD}.{frontend}"
        if frontend not in FRONTENDS:
            errors.append(f"{where}: unknown frontend (allowed: {FRONTENDS})")
            continue
        seen: set[tuple[str, str]] = set()
        objects = _gap_objects(value)
        if not objects:
            errors.append(f"{where}: empty gap list")
        for gap in objects:
            if not isinstance(gap, dict):
                errors.append(f"{where}: a gap must be an object")
                continue
            unknown = sorted(set(gap) - _GAP_KEYS)
            missing = sorted(_REQUIRED_GAP_KEYS - set(gap))
            if unknown:
                errors.append(f"{where}: unknown keys {unknown}")
            if missing:
                errors.append(f"{where}: missing keys {missing}")
                continue
            kinds = gap["kinds"]
            if (
                not isinstance(kinds, list)
                or not kinds
                or not all(isinstance(k, str) and k for k in kinds)
                or len(set(kinds)) != len(kinds)
            ):
                errors.append(
                    f"{where}: kinds must be a non-empty list of unique names"
                )
                continue
            for kind in kinds:
                if kind not in checkable:
                    errors.append(
                        f"{where}: {kind!r} is in neither expected_kinds nor "
                        "expected_absent_kinds"
                    )
            modes = gap.get("modes", list(ENFORCED_MODES))
            if (
                not isinstance(modes, list)
                or not modes
                or any(m not in ENFORCED_MODES for m in modes)
            ):
                errors.append(
                    f"{where}: modes must be a non-empty subset of {ENFORCED_MODES}"
                )
                continue
            for field in ("reason", "known_gap"):
                if not isinstance(gap[field], str) or not gap[field].strip():
                    errors.append(f"{where}: {field} must be a non-empty string")
            known = gap["known_gap"]
            if (
                isinstance(known, str)
                and known.strip()
                and not any(h.startswith(known.strip()) for h in heading_list)
            ):
                errors.append(
                    f"{where}: known_gap {known!r} starts no heading of "
                    "docs/contribute/known-gaps.md"
                )
            for mode in modes:
                for kind in kinds:
                    if (kind, mode) in seen:
                        errors.append(f"{where}: {kind!r} declared twice for {mode}")
                    seen.add((kind, mode))
    return errors


def validate_ground_truth(
    verdicts: Mapping[str, Mapping[str, Any]], headings: Iterable[str] | None = None
) -> list[str]:
    """Schema errors of every case's ``expected_kinds_gaps``."""
    heading_list = tuple(headings) if headings is not None else known_gap_headings()
    errors: list[str] = []
    for case, entry in verdicts.items():
        errors.extend(validate_entry(case, entry, heading_list))
    return errors


def declared_gaps(entry: Mapping[str, Any]) -> list[KindsGap]:
    """The parsed gaps of a (valid) entry."""
    out: list[KindsGap] = []
    for frontend, value in (entry.get(GAPS_FIELD) or {}).items():
        for gap in _gap_objects(value):
            out.append(
                KindsGap(
                    frontend,
                    tuple(gap["kinds"]),
                    tuple(gap.get("modes", ENFORCED_MODES)),
                    gap["reason"],
                    gap["known_gap"],
                )
            )
    return out


def declared_kinds(entry: Mapping[str, Any], frontend: str, mode: str) -> set[str]:
    """Kinds whose violation *entry* declares for this ``(frontend, mode)`` cell."""
    return {
        kind
        for gap in declared_gaps(entry)
        if gap.frontend == frontend and mode in gap.modes
        for kind in gap.kinds
    }


def evaluate(
    entry: Mapping[str, Any],
    got_kinds: Iterable[str],
    *,
    frontend: str,
    mode: str,
    target: tuple[str, str] = ENFORCED_TARGETS[0],
) -> KindsVerdict:
    """Judge one lane cell's emitted kinds against the case's ground truth."""
    expected = list(entry.get("expected_kinds") or [])
    absent = list(entry.get("expected_absent_kinds") or [])
    declared = declared_kinds(entry, frontend, mode)
    if not expected and not absent:
        return NOT_CHECKED
    got = set(got_kinds)
    missing = [k for k in expected if k not in got]
    present = [k for k in absent if k in got]
    violations = set(missing) | set(present)
    undeclared_missing = [k for k in missing if k not in declared]
    undeclared_present = [k for k in present if k not in declared]
    stale = sorted(declared - violations)
    enforced = mode in ENFORCED_MODES and target in ENFORCED_TARGETS
    parts = []
    if undeclared_missing:
        parts.append(f"expected_kinds missing: {undeclared_missing}")
    if undeclared_present:
        parts.append(f"expected_absent_kinds present: {undeclared_present}")
    if stale:
        parts.append(
            f"stale {GAPS_FIELD}.{frontend} for {mode}: {stale} no longer "
            "reproduce; remove the declaration"
        )
    if undeclared_missing or undeclared_present:
        return KindsVerdict("mismatch", "; ".join(parts), enforced)
    if stale:
        return KindsVerdict("stale-gap", "; ".join(parts), enforced)
    if violations:
        return KindsVerdict(
            "declared-gap",
            f"declared {GAPS_FIELD}.{frontend} for {mode}: {sorted(violations)}",
            False,
        )
    if entry.get("detectability") == "none":
        return KindsVerdict(
            "gap-observed",
            "expected_kinds here is the tool's current insufficient observation "
            "under a declared detector gap, not calibration proof of the "
            "canonical verdict",
            False,
        )
    return KindsVerdict("ok", "", False)


def current_frontend() -> str:
    """This run's L2 header frontend, by the product's own resolution rule."""
    from abicheck.extract.header_ast_backend import _resolve_header_backend

    return str(_resolve_header_backend(None))


def current_target() -> tuple[str, str]:
    """This run's ``(platform, architecture)``, normalized like
    ``validate_examples.py``'s ``CURRENT_PLATFORM``/``CURRENT_ARCHITECTURE``."""
    plat = {"darwin": "macos", "win32": "windows"}.get(sys.platform, sys.platform)
    plat = "linux" if plat.startswith("linux") else plat
    machine = platform.machine().lower()
    arch = {"amd64": "x86_64", "arm64": "aarch64"}.get(machine, machine)
    return plat, arch


def judge_cell(
    entry: Mapping[str, Any],
    status: str,
    message: str,
    got_kinds: Iterable[str],
    *,
    mode: str,
    frontend: str | None = None,
    target: tuple[str, str] | None = None,
) -> tuple[str, str, KindsVerdict]:
    """``(status, message, verdict)`` of a lane cell after the kinds gate.

    Only a cell that ran compare and was not XFAILed by its verdict-level
    ``known_gap`` is checked; a gate failure turns ``PASS`` into ``FAIL`` and
    appends its detail to the message.
    """
    if status not in ("PASS", "FAIL"):
        return status, message, NOT_CHECKED
    verdict = evaluate(
        entry,
        got_kinds,
        frontend=frontend or current_frontend(),
        mode=mode,
        target=target or current_target(),
    )
    if not verdict.fails:
        return status, message, verdict
    detail = f"kinds gate: {verdict.detail}"
    return "FAIL", detail if not message else f"{message} | {detail}", verdict
