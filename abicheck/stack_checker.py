# Copyright 2026 Nikolay Petrov
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

"""Full-stack ABI compatibility checker.

Compares two resolved runtime environments (baseline vs candidate) for a given
root binary and produces a stack-level verdict covering:
  - Loadability: will the binary load in the candidate environment?
  - ABI risk: are there harmful ABI changes in any dependency?
  - Impact: which imports are affected by changed dependencies?
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from .binder import BindingStatus, SymbolBinding, compute_bindings
from .checker import DiffResult, compare
from .checker_policy import BREAKING_KINDS
from .checker_types import Change
from .resolver import DependencyGraph, resolve_dependencies
from .stack_binding_diff import diff_runtime_bindings

log = logging.getLogger(__name__)


class StackVerdict(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


@dataclass
class StackChange:
    """A changed DSO between baseline and candidate stacks."""
    library: str               # SONAME or path
    change_type: str           # "added", "removed", "content_changed"
    abi_diff: DiffResult | None = None  # Per-library ABI diff
    impacted_imports: list[SymbolBinding] = field(default_factory=list)


@dataclass
class StackCheckResult:
    """Result of a full-stack ABI compatibility check."""
    root_binary: str
    baseline_env: str
    candidate_env: str
    loadability: StackVerdict       # Will it load?
    abi_risk: StackVerdict          # Are there harmful ABI changes?
    baseline_graph: DependencyGraph
    candidate_graph: DependencyGraph
    bindings_baseline: list[SymbolBinding] = field(default_factory=list)
    bindings_candidate: list[SymbolBinding] = field(default_factory=list)
    missing_symbols: list[SymbolBinding] = field(default_factory=list)
    stack_changes: list[StackChange] = field(default_factory=list)
    # Symbols whose effective runtime provider or weak-resolution status
    # changed between the baseline and candidate environments even though no
    # single DSO's own export table changed (dependency reordering, a
    # sibling library gaining/losing the export, interposition drift).
    binding_changes: list[Change] = field(default_factory=list)
    risk_score: str = "low"                    # "high", "medium", "low"


def _compute_loadability(
    graph: DependencyGraph,
    missing: list[SymbolBinding],
    version_mismatches: list[SymbolBinding],
) -> StackVerdict:
    """Determine the loadability verdict for a candidate environment."""
    if not graph.nodes:
        return StackVerdict.FAIL
    if graph.unresolved or missing:
        return StackVerdict.FAIL
    if version_mismatches:
        return StackVerdict.WARN
    return StackVerdict.PASS


#: Sentinel ``Change.symbol`` values diff_platform*.py uses for a whole-binary
#: (not per-export) finding — e.g. a machine/class/endianness/ABI-flags
#: mismatch. No real import is ever named this, so an *equality* match against
#: it would never fire; these findings mean the provider DSO cannot satisfy
#: *any* dynamic symbol resolution at load time, so any root importing
#: anything from it is unconditionally touched (Codex review, PR #551).
_DSO_WIDE_BREAK_SYMBOLS = frozenset({"ELF_HEADER", "PE_HEADER", "MACHO_HEADER"})


def _breaking_change_touches_imports(change: StackChange) -> bool:
    """True if a symbol this root actually imports from *change*'s provider is
    among the DSO's own BREAKING findings (not just any breaking finding
    anywhere in the DSO).

    ``change.abi_diff.verdict`` is a whole-DSO verdict: a provider can be
    BREAKING because of one removed symbol while a given root only imports an
    unrelated, unaffected symbol from the same DSO. Without this intersection
    every root importing *anything* from a broken provider was marked FAIL,
    even when the specific break could never have affected it (P1.4).

    A BREAKING finding for a type/layout change (e.g. a resized struct) names
    the *type* in ``Change.symbol`` (e.g. "Point"), not the functions that use
    it — those exported functions are separately recorded in
    ``Change.affected_symbols``. A root importing ``foo(Point)`` must still
    count as touched even though "foo" never equals "Point" (Codex review).

    A DSO-wide break (wrong machine/class/endianness/ABI-flags) is recorded
    against a sentinel symbol, not a real export, so it can never appear in
    ``imported_symbols`` — but such a DSO cannot satisfy *any* import at
    runtime, so it counts as touching every symbol this root imports from it
    (Codex review).
    """
    assert change.abi_diff is not None
    imported_symbols = {b.symbol for b in change.impacted_imports}
    breaking_symbols: set[str] = set()
    for c in change.abi_diff.breaking:
        if c.symbol in _DSO_WIDE_BREAK_SYMBOLS:
            return True
        breaking_symbols.add(c.symbol)
        breaking_symbols.update(c.affected_symbols or ())
    return bool(imported_symbols & breaking_symbols)


def _compute_abi_risk(
    stack_changes: list[StackChange],
    binding_changes: list[Change] | None = None,
) -> StackVerdict:
    """Determine ABI risk verdict from stack changes and binding changes."""
    has_breaking = False
    has_risk = False

    for change in stack_changes:
        if change.change_type == "removed":
            has_breaking = True
        elif change.abi_diff is None and change.change_type == "content_changed":
            # ABI diff failed (unreadable file or diff error) — treat as risk
            # since we can't confirm compatibility.
            has_risk = True
        elif change.abi_diff is not None and change.impacted_imports:
            if change.abi_diff.verdict.value == "BREAKING":
                if _breaking_change_touches_imports(change):
                    has_breaking = True
                else:
                    # The DSO is broken, but not by anything this root
                    # imports — real "environment contains a broken DSO"
                    # signal, but not a confirmed impact on this root.
                    has_risk = True
            elif change.abi_diff.verdict.value in ("API_BREAK", "COMPATIBLE_WITH_RISK"):
                has_risk = True
        elif change.abi_diff is not None and not change.impacted_imports:
            if change.abi_diff.verdict.value == "BREAKING":
                has_risk = True

    for bchange in binding_changes or []:
        if bchange.kind in BREAKING_KINDS:
            has_breaking = True
        else:
            has_risk = True

    if has_breaking:
        return StackVerdict.FAIL
    if has_risk:
        return StackVerdict.WARN
    return StackVerdict.PASS


def _compute_risk_score(loadability: StackVerdict, abi_risk: StackVerdict) -> str:
    """Compute risk score from loadability and ABI risk verdicts."""
    if loadability == StackVerdict.FAIL or abi_risk == StackVerdict.FAIL:
        return "high"
    if abi_risk == StackVerdict.WARN:
        return "medium"
    return "low"


def under_sysroot(root: Path, binary: Path) -> Path:
    """Join *binary* under *root*, treating an absolute BINARY as sysroot
    -relative (chroot semantics).

    ``Path.__truediv__`` discards its left operand entirely when the right
    operand is absolute (``root / "/usr/bin/x"`` == ``Path("/usr/bin/x")``),
    so a plain ``root / binary`` join silently escapes the sysroot for a
    user-supplied absolute BINARY -- the ELF check and stack walk would then
    target the host filesystem instead of the confined environment this
    command is scoped to. Mirrors ``resolver._seed_root``'s sysroot
    -prefixing for the single-env ``deps stack`` command, so the dual
    -sysroot ``deps compare`` command applies the same normalization.
    """
    if binary.is_absolute():
        return root / str(binary).lstrip("/")
    return root / binary


def check_stack(
    binary: Path,
    baseline_root: Path,
    candidate_root: Path,
    ld_library_path: str = "",
    search_paths: list[Path] | None = None,
) -> StackCheckResult:
    """Compare a binary's full dependency stack across two environments.

    Args:
        binary: Path to the root ELF binary (relative to both roots).
        baseline_root: Sysroot for the baseline environment.
        candidate_root: Sysroot for the candidate environment.
        ld_library_path: Simulated LD_LIBRARY_PATH (applied to both).
        search_paths: Additional search directories.

    Returns:
        A StackCheckResult with loadability/ABI verdicts and per-library changes.
    """
    baseline_binary = under_sysroot(baseline_root, binary)
    candidate_binary = under_sysroot(candidate_root, binary)

    # Resolve dependency graphs in both environments.
    baseline_graph = resolve_dependencies(
        baseline_binary,
        search_paths=search_paths,
        sysroot=baseline_root,
        ld_library_path=ld_library_path,
    )
    candidate_graph = resolve_dependencies(
        candidate_binary,
        search_paths=search_paths,
        sysroot=candidate_root,
        ld_library_path=ld_library_path,
    )

    # Compute symbol bindings in both environments.
    baseline_bindings = compute_bindings(baseline_graph)
    candidate_bindings = compute_bindings(candidate_graph)

    missing = [b for b in candidate_bindings if b.status == BindingStatus.MISSING]
    version_mismatches = [b for b in candidate_bindings if b.status == BindingStatus.VERSION_MISMATCH]

    loadability = _compute_loadability(candidate_graph, missing, version_mismatches)
    stack_changes = _diff_stacks(baseline_graph, candidate_graph, candidate_bindings)
    binding_changes = diff_runtime_bindings(
        baseline_graph, candidate_graph, baseline_bindings, candidate_bindings
    )
    abi_risk = _compute_abi_risk(stack_changes, binding_changes)
    risk_score = _compute_risk_score(loadability, abi_risk)

    return StackCheckResult(
        root_binary=str(binary),
        baseline_env=str(baseline_root),
        candidate_env=str(candidate_root),
        loadability=loadability,
        abi_risk=abi_risk,
        baseline_graph=baseline_graph,
        candidate_graph=candidate_graph,
        bindings_baseline=baseline_bindings,
        bindings_candidate=candidate_bindings,
        missing_symbols=missing,
        stack_changes=stack_changes,
        binding_changes=binding_changes,
        risk_score=risk_score,
    )


def check_single_env(
    binary: Path,
    search_paths: list[Path] | None = None,
    sysroot: Path | None = None,
    ld_library_path: str = "",
) -> StackCheckResult:
    """Check a binary's dependency stack in a single environment.

    Useful for "will this binary load?" analysis without a baseline comparison.
    """
    graph = resolve_dependencies(
        binary,
        search_paths=search_paths,
        sysroot=sysroot,
        ld_library_path=ld_library_path,
    )
    bindings = compute_bindings(graph)

    missing = [b for b in bindings if b.status == BindingStatus.MISSING]
    version_mismatches = [b for b in bindings if b.status == BindingStatus.VERSION_MISMATCH]

    loadability = _compute_loadability(graph, missing, version_mismatches)

    risk_score = "high" if loadability == StackVerdict.FAIL else (
        "medium" if loadability == StackVerdict.WARN else "low"
    )

    return StackCheckResult(
        root_binary=str(binary),
        baseline_env=str(sysroot or ""),
        candidate_env=str(sysroot or ""),
        loadability=loadability,
        abi_risk=StackVerdict.PASS,
        baseline_graph=graph,
        candidate_graph=graph,
        bindings_baseline=bindings,
        bindings_candidate=bindings,
        missing_symbols=missing,
        stack_changes=[],
        risk_score=risk_score,
    )


def _diff_stacks(
    baseline: DependencyGraph,
    candidate: DependencyGraph,
    candidate_bindings: list[SymbolBinding] | None = None,
) -> list[StackChange]:
    """Identify changed DSOs between baseline and candidate stacks."""
    changes: list[StackChange] = []

    # Build soname → node mapping for each stack.
    baseline_by_soname = {node.soname: (key, node) for key, node in baseline.nodes.items()}
    candidate_by_soname = {node.soname: (key, node) for key, node in candidate.nodes.items()}

    # Index candidate bindings by provider path for impacted_imports lookup.
    bindings_by_provider: dict[str, list[SymbolBinding]] = {}
    for b in (candidate_bindings or []):
        if b.provider:
            bindings_by_provider.setdefault(b.provider, []).append(b)

    all_sonames = set(baseline_by_soname.keys()) | set(candidate_by_soname.keys())

    for soname in sorted(all_sonames):
        base_entry = baseline_by_soname.get(soname)
        cand_entry = candidate_by_soname.get(soname)

        if base_entry is None and cand_entry is not None:
            changes.append(StackChange(library=soname, change_type="added"))
            continue

        if base_entry is not None and cand_entry is None:
            changes.append(StackChange(library=soname, change_type="removed"))
            continue

        assert base_entry is not None and cand_entry is not None
        base_key, base_node = base_entry
        cand_key, cand_node = cand_entry

        # Check if the file content changed (by file hash).
        base_hash = _file_hash(base_node.path)
        cand_hash = _file_hash(cand_node.path)

        if base_hash is None or cand_hash is None:
            # Unreadable file — treat as content changed (error).
            abi_diff = None
            impacted = bindings_by_provider.get(cand_key, [])
            changes.append(StackChange(
                library=soname,
                change_type="content_changed",
                abi_diff=abi_diff,
                impacted_imports=impacted,
            ))
        elif base_hash != cand_hash:
            # Run per-library ABI diff using the existing checker.
            abi_diff = _run_abi_diff(base_node.path, cand_node.path, soname)
            # Populate impacted_imports: bindings that reference the changed DSO.
            impacted = bindings_by_provider.get(cand_key, [])
            changes.append(StackChange(
                library=soname,
                change_type="content_changed",
                abi_diff=abi_diff,
                impacted_imports=impacted,
            ))

    return changes


def _file_hash(path: Path) -> str | None:
    """Compute SHA-256 hash of a file. Returns None on read error."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _run_abi_diff(old_path: Path, new_path: Path, library_name: str) -> DiffResult | None:
    """Run the existing abicheck compare on two library files."""
    from .dumper import dump

    try:
        old_snap = dump(
            so_path=old_path, headers=[], extra_includes=[],
            version="baseline", compiler="c++",
        )
        new_snap = dump(
            so_path=new_path, headers=[], extra_includes=[],
            version="candidate", compiler="c++",
        )
        return compare(old_snap, new_snap)
    except Exception as exc:  # noqa: BLE001
        log.warning("_run_abi_diff: failed for %s: %s", library_name, exc)
        return None
