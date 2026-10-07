#!/usr/bin/env python3
# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""ADR reach and the unreached-function ratchet over a use-case recording.

Two analyses ``usecase_paths.py`` exposes as subcommands (``adr-reach`` and
``ratchet``), kept here so that file stays a recorder/ranker.

**ADR reach.** An ADR that names ``abicheck/...py`` files is a claim that
this code implements a decision. Joined with a recording (which functions
each scenario/flow run executed) and the use-case registry (which use cases
cite which ADR), every ADR lands in exactly one status:

``reached``
    some run *of a use case that cites the ADR* executes a function in a
    file the ADR names -- the decision is exercised on its own path;
``reached-elsewhere``
    the named code runs, but only under use cases that do not cite the ADR
    (or the ADR is cited by none) -- exercised, but not traceably;
``not-reached``
    the named files exist and no recorded run executes any function in
    them -- built but unexercised by any use case;
``untraced``
    every file the ADR names is gone from the tree (moved or deleted) --
    the ADR's code references are stale and cannot be traced;
``no-code-refs``
    the ADR names no ``abicheck/`` file at all (a process or product
    decision, or one whose prose never points at code).

A use case cites an ADR when its registry entry mentions the ADR's file
name or its ``ADR-NNN`` id anywhere (``docs:``, ``note``, ``evidence``...).
Two ADR files sharing a number (``020-...``) are both cited by ``ADR-020``.

**Ratchet.** The set of functions no recorded use case reaches, compared
with a committed baseline of the same recording sources: a function
unreached now but not in the baseline is growth (a new or newly-orphaned
function); one in the baseline but reached (or deleted) now is an
improvement to lock in by re-recording.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ADR_DIR = Path("docs/contribute/adr")
REGISTRY = Path("docs/contribute/usecase-registry.yaml")
UNREACHED_BASELINE = ROOT / "scripts" / "usecase_unreached_baseline.json"

STATUS_REACHED = "reached"
STATUS_ELSEWHERE = "reached-elsewhere"
STATUS_NOT_REACHED = "not-reached"
STATUS_UNTRACED = "untraced"
STATUS_NO_REFS = "no-code-refs"
STATUSES = (
    STATUS_REACHED,
    STATUS_ELSEWHERE,
    STATUS_NOT_REACHED,
    STATUS_UNTRACED,
    STATUS_NO_REFS,
)

_CODE_PATH_RE = re.compile(r"\babicheck/[A-Za-z0-9_/]+\.py\b")
_ADR_FILE_RE = re.compile(r"^(\d{3})[a-z]?-.+\.md$")


@dataclass
class AdrReach:
    adr: str  # file name, e.g. "037-cli-interface-contract.md"
    status: str
    named_files: list[str] = field(default_factory=list)
    missing_files: list[str] = field(default_factory=list)
    use_cases: list[str] = field(default_factory=list)
    reached_by: list[str] = field(default_factory=list)
    functions_total: int = 0
    functions_reached: int = 0


def adr_code_refs(text: str) -> list[str]:
    """Every ``abicheck/...py`` path an ADR names, sorted and de-duplicated."""
    return sorted(set(_CODE_PATH_RE.findall(text)))


def adr_files(root: Path = ROOT) -> dict[str, str]:
    """``{adr file name: text}`` for every numbered ADR."""
    return {
        p.name: p.read_text(encoding="utf-8")
        for p in sorted((root / ADR_DIR).glob("*.md"))
        if _ADR_FILE_RE.match(p.name)
    }


def adr_citations(use_cases: list[dict], adr_names: list[str]) -> dict[str, set[str]]:
    """``{adr file name: use-case ids citing it}``."""
    out: dict[str, set[str]] = defaultdict(set)
    for uc in use_cases:
        blob = json.dumps(uc, default=str)
        for name in adr_names:
            match = _ADR_FILE_RE.match(name)
            number = match.group(1) if match else ""
            if name in blob or re.search(rf"\bADR-{number}(?![0-9])", blob):
                out[name].add(str(uc.get("id")))
    return out


def load_registry_use_cases(root: Path = ROOT) -> list[dict]:
    import yaml

    doc = yaml.safe_load((root / REGISTRY).read_text(encoding="utf-8")) or {}
    return list(doc.get("use_cases") or [])


def adr_reach(
    recording: dict,
    adrs: dict[str, str],
    use_cases: list[dict],
) -> list[AdrReach]:
    """Classify every ADR (see the module docstring)."""
    inventory: dict[str, list[str]] = recording["inventory"]
    # file -> {run label -> functions of that file it executed}
    executed_in_file: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: defaultdict(set)
    )
    for run_id, run in recording["runs"].items():
        label = run.get("use_case") or run_id
        for fn in run["functions"]:
            path, _, _qual = fn.partition("::")
            executed_in_file[path][label].add(fn)
    citations = adr_citations(use_cases, sorted(adrs))

    out: list[AdrReach] = []
    for name, text in sorted(adrs.items()):
        named = adr_code_refs(text)
        cited = sorted(citations.get(name, set()))
        if not named:
            out.append(AdrReach(name, STATUS_NO_REFS, use_cases=cited))
            continue
        present = [p for p in named if p in inventory]
        missing = [p for p in named if p not in inventory]
        total = sum(len(inventory[p]) for p in present)
        if not present:
            out.append(AdrReach(name, STATUS_UNTRACED, named, missing, cited))
            continue
        by_label: dict[str, set[str]] = defaultdict(set)
        for path in present:
            for label, fns in executed_in_file.get(path, {}).items():
                by_label[label] |= fns
        reached_fns = set().union(*by_label.values()) if by_label else set()
        if not by_label:
            status = STATUS_NOT_REACHED
        elif set(by_label) & set(cited):
            status = STATUS_REACHED
        else:
            status = STATUS_ELSEWHERE
        out.append(
            AdrReach(
                name,
                status,
                named,
                missing,
                cited,
                sorted(by_label),
                total,
                len(reached_fns),
            )
        )
    return out


def render_adr_reach_markdown(rows: list[AdrReach], limit: int | None = None) -> str:
    counts = {s: sum(1 for r in rows if r.status == s) for s in STATUSES}
    lines = [
        "## ADR reach",
        "",
        f"{len(rows)} ADRs: " + ", ".join(f"{counts[s]} {s}" for s in STATUSES) + ".",
        "",
    ]
    for status in (
        STATUS_NOT_REACHED,
        STATUS_ELSEWHERE,
        STATUS_UNTRACED,
        STATUS_REACHED,
    ):
        group = [r for r in rows if r.status == status]
        if not group:
            continue
        lines += [f"### {status} ({len(group)})", ""]
        lines += [
            "| ADR | named files | functions reached | citing use cases | reached by |",
            "|---|---|---|---|---|",
        ]
        for r in group[:limit]:
            named = len(r.named_files) - len(r.missing_files)
            stale = f" (+{len(r.missing_files)} gone)" if r.missing_files else ""
            lines.append(
                f"| {r.adr} | {named}{stale} | {r.functions_reached}/{r.functions_total} "
                f"| {', '.join(r.use_cases) or '-'} | {', '.join(r.reached_by[:5]) or '-'} |"
            )
        lines.append("")
    return "\n".join(lines)


# -- unreached ratchet -------------------------------------------------------


def load_unreached_baseline(path: Path = UNREACHED_BASELINE) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def unreached_baseline_payload(
    unreached: set[str], sources: list[str], revision: str | None
) -> dict:
    return {
        "_comment": (
            "Generated by `scripts/usecase_paths.py ratchet RECORDING --write`. "
            "Functions no recorded use case reaches; checked weekly by "
            "usecase-paths.yml. Shrinking it is the point; growing it needs a reason."
        ),
        "sources": sorted(sources),
        "revision": revision,
        "count": len(unreached),
        "unreached": sorted(unreached),
    }


@dataclass(frozen=True)
class RatchetResult:
    grown: list[str]
    shrunk: list[str]

    @property
    def ok(self) -> bool:
        return not self.grown


def ratchet_unreached(
    unreached: set[str], baseline: dict, sources: list[str]
) -> RatchetResult:
    recorded_sources = sorted(baseline.get("sources") or [])
    if recorded_sources != sorted(sources):
        raise ValueError(
            f"baseline was recorded from sources {recorded_sources}, this recording "
            f"from {sorted(sources)}: the unreached sets are not comparable"
        )
    before = set(baseline.get("unreached") or [])
    return RatchetResult(sorted(unreached - before), sorted(before - unreached))


def render_ratchet_markdown(result: RatchetResult, limit: int = 50) -> str:
    lines = [
        "## Unreached-function ratchet",
        "",
        f"{len(result.grown)} newly unreached, {len(result.shrunk)} no longer unreached "
        "(reached now, or deleted).",
        "",
    ]
    if result.grown:
        lines += ["### Newly unreached (growth)", ""]
        lines += [f"- `{fn}`" for fn in result.grown[:limit]]
        if len(result.grown) > limit:
            lines.append(f"- ... and {len(result.grown) - limit} more")
        lines.append("")
    if result.shrunk:
        lines += [
            "### No longer unreached -- re-record the baseline to lock it in",
            "",
        ]
        lines += [f"- `{fn}`" for fn in result.shrunk[:limit]]
        if len(result.shrunk) > limit:
            lines.append(f"- ... and {len(result.shrunk) - limit} more")
        lines.append("")
    return "\n".join(lines)
