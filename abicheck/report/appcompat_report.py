# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Application-compatibility report projection (``appcompat``).

ADR-061 D5: ``reporter.py`` sat two lines under the 2000-line hard cap. This
is the one whole concern inside it with no other caller -- the JSON and
Markdown projections of an ``AppCompatResult`` and the five section builders
only they use. Everything here is about one application against one library
pair, which is a different question from the library-vs-library report the
rest of ``reporter.py`` answers, and it has its own module family already
(``appcompat.py``, ``appcompat_html.py``).

``reporter.py`` keeps ``appcompat_to_json``/``appcompat_to_markdown``
reachable under their original names through a lazy module-level
``__getattr__``, not a static re-export: this module imports the shared
per-change helpers (``_change_to_dict`` and friends) from ``reporter``, so a
static re-export would close a real import cycle between the two. That is the
same shim pattern ``cli_buildsource.py`` already uses for its own relocated
helpers (root ``AGENTS.md``, "Moving helpers out of a module that re-exports
them?").

**Known gap, inherited rather than introduced (Codex review, PR #994).**
``appcompat_to_json`` passes ``policy``/``kind_sets``/``policy_file`` into
``reporter._change_to_dict``, so each finding's severity and category are
resolved *while* the document is being built rather than read off already-
computed facts -- which is not what this package's "a renderer decides
nothing" contract wants. The call is byte-identical to the one that stood in
``reporter.py`` before this module existed, and both sit in the same
``report`` layer, so the move neither created nor widened it. Closing it
means changing ``_change_to_dict``'s contract for its four other call sites
(the full, leaf and root-cause JSON paths), which is ADR-061 Phase 2 item 4's
remaining per-finding-verdict work -- the same consolidation
``report/finding.py``'s ``ReportFinding`` did for JUnit/HTML/Markdown -- and
not something to attempt inside a file-size slice. Recorded here so it is
found from the code rather than only from a review thread.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ..checker_policy import EvidenceStatus
from ..reporter import (
    _VERDICT_EMOJI,
    _change_to_dict,
    _finding_id,
    _fmt_size,
    _metadata_dict,
)
from ..reporter_markdown import (
    root_cause_evidence_lookup_for_changes,
    root_cause_lookup_for_changes,
)

if TYPE_CHECKING:
    from ..checker import Change


def _appcompat_header_lines(
    app_path: str,
    old_lib: str,
    new_lib: str,
    v_emoji: str,
    v_label: str,
) -> list[str]:
    """Build the report header lines for appcompat markdown."""
    header = [
        f"**Application:** `{app_path}`",
        f"**Verdict:** {v_emoji} `{v_label}`",
        "",
    ]
    if old_lib:
        header.insert(1, f"**Library:** `{old_lib}` → `{new_lib}`")
        return header
    header.insert(1, f"**Library:** `{new_lib}`")
    return header


def _appcompat_coverage_lines(
    required_count: int,
    coverage: float,
    missing: list[object],
) -> list[str]:
    """Build symbol coverage section lines."""
    lines = [
        "## Symbol Coverage",
        "",
        f"App requires **{required_count}** library symbols.",
    ]
    if missing:
        lines.append(
            f"**{len(missing)}** required symbol(s) missing from new version "
            f"({coverage:.0f}% coverage).",
        )
    elif required_count > 0:
        lines.append(
            f"All {required_count} required symbols present in new version "
            f"({coverage:.0f}% coverage).",
        )
    lines.append("")
    return lines


def _appcompat_missing_lines(
    missing: list[object],
    missing_ver: list[object],
) -> list[str]:
    """Build missing symbol/version sections."""
    lines: list[str] = []
    if missing:
        lines += ["## Missing Symbols", ""]
        lines.append(
            "These symbols are required by the application but absent from the new library:"
        )
        lines.append("")
        for sym in missing:
            lines.append(f"- `{sym}`")
        lines.append("")
    if missing_ver:
        lines += ["## Missing Symbol Versions", ""]
        for ver in missing_ver:
            lines.append(f"- `{ver}`")
        lines.append("")
    return lines


def _appcompat_relevant_lines(breaking: list[Change], total_changes: int) -> list[str]:
    """Build relevant changes section lines."""
    if breaking:
        lines: list[str] = [
            f"## Relevant Changes ({len(breaking)} of {total_changes} total)",
            "",
            "These library changes affect symbols your application uses:",
            "",
            "| Kind | Symbol | Description |",
            "|------|--------|-------------|",
        ]
        for change in breaking:
            kind_val = change.kind.value if change.kind else ""
            lines.append(f"| `{kind_val}` | `{change.symbol}` | {change.description} |")
        lines.append("")
        return lines
    if total_changes > 0:
        return [
            f"## Relevant Changes (0 of {total_changes} total)",
            "",
            "None of the library's ABI changes affect your application.",
            "",
        ]
    return []


def _appcompat_irrelevant_lines(
    irrelevant: list[Change], show_irrelevant: bool
) -> list[str]:
    """Build irrelevant changes section/note lines."""
    if irrelevant and not show_irrelevant:
        return [
            f"_{len(irrelevant)} library ABI change(s) do NOT affect your application. "
            "Use `--show-irrelevant` to see them._",
            "",
        ]
    if irrelevant and show_irrelevant:
        lines = [
            f"## Irrelevant Changes ({len(irrelevant)})",
            "",
            "These library changes do NOT affect your application:",
            "",
        ]
        for change in irrelevant:
            kind_val = change.kind.value if change.kind else ""
            lines.append(f"- **{kind_val}**: {change.description}")
        lines.append("")
        return lines
    return []


def appcompat_to_json(result: object, indent: int = 2) -> str:
    """Render an AppCompatResult as JSON."""
    verdict = getattr(result, "verdict", None)
    full_diff = getattr(result, "full_diff", None)

    d: dict[str, object] = {
        "application": getattr(result, "app_path", ""),
        "old_library": getattr(result, "old_lib_path", ""),
        "new_library": getattr(result, "new_lib_path", ""),
        "verdict": verdict.value if verdict else "UNKNOWN",
        "symbol_coverage_pct": round(getattr(result, "symbol_coverage", 0.0), 1),
        "required_symbol_count": getattr(result, "required_symbol_count", 0),
    }

    missing = getattr(result, "missing_symbols", [])
    d["missing_symbols"] = list(missing)

    missing_ver = getattr(result, "missing_versions", [])
    d["missing_versions"] = list(missing_ver)

    breaking = getattr(result, "breaking_for_app", [])
    appcompat_policy = (
        getattr(getattr(result, "full_diff", None), "policy", "strict_abi")
        or "strict_abi"
    )
    # Thread the full_diff's PolicyFile/effective kind_sets through, mirroring
    # to_json's _change_to_dict calls (reporter.py _add_changes_block) —
    # without them, a per-finding severity here falls back to raw-kind
    # classification and can contradict full_library_verdict below, which
    # already honours the PolicyFile via full_diff.verdict.
    _kind_sets_fn = getattr(full_diff, "_effective_kind_sets", None)
    appcompat_kind_sets = _kind_sets_fn() if callable(_kind_sets_fn) else None
    appcompat_policy_file = getattr(full_diff, "policy_file", None)
    _rc_lookup = root_cause_lookup_for_changes(breaking)
    _rc_evidence = root_cause_evidence_lookup_for_changes(breaking)
    d["relevant_changes"] = [
        _change_to_dict(
            c,
            policy=appcompat_policy,
            kind_sets=appcompat_kind_sets,
            policy_file=appcompat_policy_file,
            evidence_status_override=EvidenceStatus.CONSUMER_PROVEN,
            root_cause=_rc_lookup.get(_finding_id(c)),
            root_cause_evidence=_rc_evidence.get(_finding_id(c)),
        )
        for c in breaking
    ]
    d["relevant_change_count"] = len(breaking)

    irrelevant = getattr(result, "irrelevant_for_app", [])
    d["irrelevant_change_count"] = len(irrelevant)

    total = len(breaking) + len(irrelevant)
    d["total_library_changes"] = total

    if full_diff:
        d["full_library_verdict"] = full_diff.verdict.value
        # Traceability: file metadata from the underlying library diff
        d["old_file"] = _metadata_dict(getattr(full_diff, "old_metadata", None))
        d["new_file"] = _metadata_dict(getattr(full_diff, "new_metadata", None))
        # Confidence & evidence
        conf = getattr(full_diff, "confidence", None)
        if conf is not None:
            d["confidence"] = conf.value if hasattr(conf, "value") else str(conf)
            etier = getattr(full_diff, "evidence_tier", None)
            if etier is not None:
                d["evidence_tier"] = (
                    etier.value if hasattr(etier, "value") else str(etier)
                )
            d["evidence_tiers"] = list(getattr(full_diff, "evidence_tiers", []) or [])
            cov_warns = getattr(full_diff, "coverage_warnings", []) or []
            if cov_warns:
                d["coverage_warnings"] = list(cov_warns)

    return json.dumps(d, indent=indent)


def appcompat_to_markdown(result: object, *, show_irrelevant: bool = False) -> str:
    """Render an AppCompatResult as Markdown."""
    verdict = getattr(result, "verdict", None)
    v_label = verdict.value if verdict else "UNKNOWN"
    v_emoji = _VERDICT_EMOJI.get(verdict, "?") if verdict else "?"

    app_path = getattr(result, "app_path", "")
    old_lib = getattr(result, "old_lib_path", "")
    new_lib = getattr(result, "new_lib_path", "")
    required_count = getattr(result, "required_symbol_count", 0)
    coverage = getattr(result, "symbol_coverage", 0.0)
    missing = getattr(result, "missing_symbols", [])
    missing_ver = getattr(result, "missing_versions", [])
    breaking = getattr(result, "breaking_for_app", [])
    irrelevant = getattr(result, "irrelevant_for_app", [])

    total_changes = len(breaking) + len(irrelevant)

    lines: list[str] = [
        "# Application Compatibility Report",
        "",
    ]

    lines += _appcompat_header_lines(app_path, old_lib, new_lib, v_emoji, v_label)

    # File metadata (traceability)
    full_diff = getattr(result, "full_diff", None)
    old_meta = getattr(full_diff, "old_metadata", None) if full_diff else None
    new_meta = getattr(full_diff, "new_metadata", None) if full_diff else None
    if old_meta or new_meta:
        lines += ["## Library Files", "", "| | Old | New |", "|---|---|---|"]
        old_path = getattr(old_meta, "path", "—") if old_meta else "—"
        new_path = getattr(new_meta, "path", "—") if new_meta else "—"
        old_sha = getattr(old_meta, "sha256", "—")[:12] if old_meta else "—"
        new_sha = getattr(new_meta, "sha256", "—")[:12] if new_meta else "—"
        old_size = _fmt_size(old_meta.size_bytes) if old_meta else "—"
        new_size = _fmt_size(new_meta.size_bytes) if new_meta else "—"
        lines += [
            f"| **Path** | `{old_path}` | `{new_path}` |",
            f"| **SHA-256** | `{old_sha}…` | `{new_sha}…` |",
            f"| **Size** | {old_size} | {new_size} |",
            "",
        ]

    # Confidence info
    conf = getattr(full_diff, "confidence", None) if full_diff else None
    if conf is not None:
        conf_val = conf.value if hasattr(conf, "value") else str(conf)
        tiers = getattr(full_diff, "evidence_tiers", []) or []
        tier_str = ", ".join(f"`{t}`" for t in tiers) if tiers else "_none_"
        policy_val = getattr(full_diff, "policy", None) or "strict_abi"
        lines += [
            f"> **Confidence**: {conf_val.upper()} | **Evidence**: {tier_str} | **Policy**: `{policy_val}`",
            "",
        ]
    else:
        # Still show policy when confidence is absent
        policy_val = getattr(full_diff, "policy", None) if full_diff else None
        if policy_val:
            lines += [f"> **Policy**: `{policy_val}`", ""]

    lines += _appcompat_coverage_lines(required_count, coverage, missing)
    lines += _appcompat_missing_lines(missing, missing_ver)
    lines += _appcompat_relevant_lines(breaking, total_changes)
    lines += _appcompat_irrelevant_lines(irrelevant, show_irrelevant)

    lines += [
        "---",
        "_Generated by [abicheck](https://github.com/abicheck/abicheck)_",
    ]
    return "\n".join(lines)
