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

A function that only moved (a module split into a package) is not growth:
a newly unreached function pairs with a vanished baseline entry that has the
same bare qualified name under another path, and -- when both sides carry a
normalized-body digest (``function_body_hashes``) -- the same digest. A
name-only pair (a digest missing on one side) is accepted only when it is the
sole candidate on each side. Pairs are reported as ``moved``, never as growth
or improvement; ``ratchet --write-moves`` rewrites just those entries.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import subprocess
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
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


BODY_HASH_LEN = 16


def _is_docstring(node: ast.stmt) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def function_body_hashes(source: str) -> dict[str, str]:
    """``{qualname: digest}`` of the normalized body of every function in *source*.

    The body is hashed as its position-free ``ast.dump``: moving the code,
    reformatting it or editing comments leaves the digest alone, while any
    change to its statements changes it. A leading docstring is not part of
    the body. Qualified names follow ``usecase_paths.function_spans``.
    """
    out: dict[str, str] = {}

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                qual = f"{prefix}{child.name}"
                body = list(child.body)
                if body and _is_docstring(body[0]):
                    body = body[1:]
                dump = ast.dump(ast.Module(body=body, type_ignores=[]))
                digest = hashlib.sha256(dump.encode("utf-8")).hexdigest()
                out[qual] = digest[:BODY_HASH_LEN]
                visit(child, f"{qual}.<locals>.")
            elif isinstance(child, ast.ClassDef):
                visit(child, f"{prefix}{child.name}.")
            else:
                visit(child, prefix)

    visit(ast.parse(source), "")
    return out


def _hashes_for(
    function_ids: Iterable[str], read_source: Callable[[str], str | None]
) -> dict[str, str]:
    """``{function id: digest}``, reading each file once through *read_source*."""
    quals_by_path: dict[str, set[str]] = defaultdict(set)
    for fid in function_ids:
        path, _, qual = fid.partition("::")
        quals_by_path[path].add(qual)
    out: dict[str, str] = {}
    for path, quals in sorted(quals_by_path.items()):
        text = read_source(path)
        if text is None:
            continue
        try:
            hashes = function_body_hashes(text)
        except (SyntaxError, ValueError):
            continue
        for qual in quals:
            if qual in hashes:
                out[f"{path}::{qual}"] = hashes[qual]
    return out


def body_hashes_of(function_ids: Iterable[str], root: Path = ROOT) -> dict[str, str]:
    """Digests of *function_ids* as they are in the tree under *root* now."""

    def read(path: str) -> str | None:
        try:
            return (root / path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None

    return _hashes_for(function_ids, read)


def git_body_hashes(
    revision: str | None, function_ids: Iterable[str], root: Path = ROOT
) -> dict[str, str]:
    """Digests of *function_ids* as they were at *revision* (``git show``).

    Empty when there is no revision or git cannot show it (a shallow clone):
    the ratchet then pairs those entries by name only.
    """
    if not revision:
        return {}

    def read(path: str) -> str | None:
        proc = subprocess.run(
            ["git", "-C", str(root), "show", f"{revision}:{path}"],
            capture_output=True,
            text=True,
            check=False,
        )
        return proc.stdout if proc.returncode == 0 else None

    return _hashes_for(function_ids, read)


def unreached_baseline_payload(
    unreached: set[str],
    sources: list[str],
    revision: str | None,
    body_hashes: Mapping[str, str] | None = None,
) -> dict:
    payload: dict = {
        "_comment": (
            "Generated by `scripts/usecase_paths.py ratchet RECORDING --write` "
            "(full re-record) or `--write-moves` (moved entries only). Functions no "
            "recorded use case reaches; checked weekly by usecase-paths.yml. "
            "Shrinking it is the point; growing it needs a reason. `body_hashes` "
            "(optional) holds a normalized-body digest per entry, so a moved "
            "function is recognized by its body."
        ),
        "sources": sorted(sources),
        "revision": revision,
        "count": len(unreached),
        "unreached": sorted(unreached),
    }
    if body_hashes:
        payload["body_hashes"] = {
            fid: body_hashes[fid] for fid in sorted(unreached) if fid in body_hashes
        }
    return payload


@dataclass(frozen=True)
class Move:
    source: str  # the baseline entry the function was at
    target: str  # where it is now
    matched_by: str  # "body" (same normalized body) or "name" (unambiguous)


@dataclass(frozen=True)
class RatchetResult:
    grown: list[str]
    shrunk: list[str]
    moved: list[Move] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.grown


def match_moves(
    fresh: list[str],
    vanished: list[str],
    source_hashes: Mapping[str, str],
    target_hashes: Mapping[str, str],
) -> list[Move]:
    """Pair newly unreached (*fresh*) functions with vanished baseline entries.

    The bare qualified name must match. When both digests are known they must
    be equal. Equal digests pair first. Without a digest match, a pair is
    accepted only when it is the sole candidate on each side. Each baseline
    entry pairs at most once.
    """

    def qual(fid: str) -> str:
        return fid.partition("::")[2]

    sources_by_qual: dict[str, list[str]] = defaultdict(list)
    for src in vanished:
        sources_by_qual[qual(src)].append(src)
    targets_by_qual: dict[str, list[str]] = defaultdict(list)
    for tgt in fresh:
        targets_by_qual[qual(tgt)].append(tgt)

    moves: list[Move] = []
    used: set[str] = set()
    for tgt in fresh:
        cands = [s for s in sources_by_qual.get(qual(tgt), []) if s not in used]
        if not cands:
            continue
        digest = target_hashes.get(tgt)
        same_body = [
            s for s in cands if digest is not None and source_hashes.get(s) == digest
        ]
        if same_body:
            src, matched_by = same_body[0], "body"
        else:
            if len(cands) != 1 or len(targets_by_qual[qual(tgt)]) != 1:
                continue
            src = cands[0]
            if digest is not None and source_hashes.get(src) is not None:
                continue  # both bodies known and different: another function
            matched_by = "name"
        used.add(src)
        moves.append(Move(src, tgt, matched_by))
    return moves


def ratchet_unreached(
    unreached: set[str],
    baseline: dict,
    sources: list[str],
    target_hashes: Mapping[str, str] | None = None,
    source_hashes: Mapping[str, str] | None = None,
) -> RatchetResult:
    recorded_sources = sorted(baseline.get("sources") or [])
    if recorded_sources != sorted(sources):
        raise ValueError(
            f"baseline was recorded from sources {recorded_sources}, this recording "
            f"from {sorted(sources)}: the unreached sets are not comparable"
        )
    before = set(baseline.get("unreached") or [])
    if source_hashes is None:
        source_hashes = baseline.get("body_hashes") or {}
    fresh = sorted(unreached - before)
    vanished = sorted(before - unreached)
    moves = match_moves(fresh, vanished, source_hashes, target_hashes or {})
    moved_to = {m.target for m in moves}
    moved_from = {m.source for m in moves}
    return RatchetResult(
        grown=[fn for fn in fresh if fn not in moved_to],
        shrunk=[fn for fn in vanished if fn not in moved_from],
        moved=moves,
    )


def ratchet_against_baseline(
    unreached: set[str], baseline: dict, sources: list[str], root: Path = ROOT
) -> tuple[RatchetResult, dict[str, str]]:
    """The ratchet with move detection, plus the digests of the new functions.

    Old bodies are the digests stored in the baseline, else read from its
    revision when git still has it, so a same-name function with a changed
    body counts as new.
    """
    before = set(baseline.get("unreached") or [])
    target_hashes = body_hashes_of(unreached - before, root)
    source_hashes = {
        **git_body_hashes(baseline.get("revision"), before - unreached, root),
        **(baseline.get("body_hashes") or {}),
    }
    result = ratchet_unreached(
        unreached, baseline, sources, target_hashes, source_hashes
    )
    return result, target_hashes


def write_baseline(path: Path | str, payload: dict) -> None:
    Path(path).write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")


def write_unreached_baseline(
    path: Path | str, unreached: set[str], sources: list[str], revision: str | None
) -> None:
    write_baseline(path, unreached_baseline_payload(unreached, sources, revision))


def ratchet_or_move(
    unreached: set[str],
    baseline_path: Path | str,
    sources: list[str],
    *,
    write_moves: bool,
    root: Path = ROOT,
) -> RatchetResult:
    """Compare with the baseline file; with *write_moves*, rewrite its moved entries."""
    baseline = load_unreached_baseline(baseline_path)
    result, target_hashes = ratchet_against_baseline(unreached, baseline, sources, root)
    if write_moves:
        write_baseline(baseline_path, apply_moves(baseline, result, target_hashes))
    return result


def apply_moves(
    baseline: dict, result: RatchetResult, target_hashes: Mapping[str, str]
) -> dict:
    """*baseline* with only its moved entries rewritten to their new paths.

    Genuinely new functions are not added, and every other entry keeps its
    digest. The recorded revision is left as it was.
    """
    unreached = set(baseline.get("unreached") or [])
    hashes = dict(baseline.get("body_hashes") or {})
    for move in result.moved:
        unreached.discard(move.source)
        unreached.add(move.target)
        hashes.pop(move.source, None)
        if move.target in target_hashes:
            hashes[move.target] = target_hashes[move.target]
    out = dict(baseline)
    out["unreached"] = sorted(unreached)
    out["count"] = len(unreached)
    kept = {fid: hashes[fid] for fid in sorted(hashes) if fid in unreached}
    if kept:
        out["body_hashes"] = kept
    else:
        out.pop("body_hashes", None)
    return out


def render_ratchet_markdown(result: RatchetResult, limit: int = 50) -> str:
    lines = [
        "## Unreached-function ratchet",
        "",
        f"{len(result.grown)} newly unreached, {len(result.moved)} moved "
        "(same function under a new path; not growth), "
        f"{len(result.shrunk)} no longer unreached (reached now, or deleted).",
        "",
    ]
    if result.grown:
        lines += ["### Newly unreached (growth)", ""]
        lines += [f"- `{fn}`" for fn in result.grown[:limit]]
        if len(result.grown) > limit:
            lines.append(f"- ... and {len(result.grown) - limit} more")
        lines.append("")
    if result.moved:
        lines += ["### Moved (not growth)", ""]
        lines += [
            f"- `{m.source}` -> `{m.target}` (matched by {m.matched_by})"
            for m in result.moved[:limit]
        ]
        if len(result.moved) > limit:
            lines.append(f"- ... and {len(result.moved) - limit} more")
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
