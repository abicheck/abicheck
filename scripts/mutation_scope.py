#!/usr/bin/env python3
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

"""Run-cost primitives for the mutation lane: what a run needs to execute.

`check_mutation_score.py` owns *gating*; this leaf module owns the two ways
the expensive `mutmut run` test-execution phase is made cheaper without
weakening any gate the run actually applies:

**Function scoping** (`function_run_scope`). The diff-scoped gate
(`check_diff_scoped`) reads only mutants that live in a function the branch
changed. Every other mutant's outcome is consumed by nothing but the drift
gates (per-module / global total), and those need a recorded baseline. So
when no baseline exists, executing only the changed functions' mutants
drops no measurement any gate reads — the gate's answer is identical to a
full run's, at a fraction of the cost. When a baseline *does* exist, the
caller keeps the conservative module-level scoping instead, because then the
out-of-scope population is a real input to the drift gate.

Each scoped mutant is still tested against the branch's *whole* tree
(mutmut copies every source and ``also_copy`` path), which is why touching
paths outside ``only_mutate`` does not matter here the way it does for
module-level scoping: nothing this run reports is a claim about an
untouched function.

**Sharding** (`shard_assignment`, `merge_baseline_parts`). A run that must
measure the whole population is split into N disjoint sets of *functions*
(`mutation_units`), balanced by size, one CI job each. Each shard gates its
own functions against the baseline's per-function counts
(`check_shard_drift`) and checks that every mutant mutmut produced belongs to
some shard (`unassigned_records`); a baseline is recorded per shard and
merged, refusing any merge whose parts do not partition the units exactly.
"""

from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
import tomllib
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path, PurePosixPath

REPO_ROOT = Path(__file__).resolve().parent.parent

#: mutmut's separator in a method's mangled name: ``xǁClassǁmethod``.
_CLASS_SEP = "ǁ"

#: What mutmut 3.x prints when every ``MUTANT_NAMES`` pattern matched nothing
#: (an ``assert`` in ``collect_source_file_mutation_data``, verified against
#: 3.8.0). For a function-scoped run that is a real answer — the changed
#: functions carry no mutable code — not an aborted measurement.
NOTHING_MATCHES_MARKER = "Filtered for specific mutants, but nothing matches"

#: pyproject sections whose change alters what a mutation run measures or how
#: its suite runs. Any other pyproject edit (a dependency pin, project
#: metadata, another tool's config) does not, and must not trigger the lane.
MUTATION_RELEVANT_PYPROJECT_KEYS: tuple[tuple[str, ...], ...] = (
    ("tool", "mutmut"),
    ("tool", "pytest"),
)


def function_scope_pattern(module_path: str, qualname: str) -> str:
    """``("abicheck/x.py", "C.m")`` -> ``"abicheck.x.xǁCǁm__mutmut_*"``.

    The inverse of `mutation_results.demangle_function`: a top-level function
    is ``x_<name>``, anything qualified is ``xǁ`` + its components joined by
    ``ǁ``. The ``__mutmut_`` anchor keeps ``x_foo`` from also matching
    ``x_foobar``.
    """
    dotted = module_path[: -len(".py")] if module_path.endswith(".py") else module_path
    dotted = dotted.replace("/", ".")
    parts = qualname.split(".")
    if len(parts) == 1:
        mangled = f"x_{parts[0]}"
    else:
        mangled = "x" + _CLASS_SEP + _CLASS_SEP.join(parts)
    return f"{dotted}.{mangled}__mutmut_*"


def module_scope_pattern(module_path: str) -> str:
    """``"abicheck/x.py"`` -> ``"abicheck.x.*"`` (every mutant in the module)."""
    dotted = module_path[: -len(".py")] if module_path.endswith(".py") else module_path
    return dotted.replace("/", ".") + ".*"


def function_run_scope(
    touched: Mapping[str, set[str]],
    only_mutate: Iterable[str],
    unattributable: Iterable[str] = (),
    module_scope: str = "<module>",
) -> tuple[list[str], set[str]]:
    """``(mutant_name_patterns, fully_measured_modules)`` for a function-scoped run.

    *touched* is `changed_functions`' ``path -> qualnames``; only
    ``only_mutate`` paths contribute. A module in *unattributable* (removed
    lines the base revision could not resolve to a function) is measured
    whole, since the function those lines belonged to is unknown. A
    module-scope edit (*module_scope* in the set) contributes no pattern of
    its own: mutmut has no module-scope mutant, and the only gate that scores
    such an edit is per-module drift, which this mode runs without.

    Returns ``([], set())`` when nothing in scope changed, which the caller
    must treat as "no mutant to execute", never as "run everything".
    """
    scope = set(only_mutate)
    whole = {p for p in unattributable if p in scope}
    patterns = {module_scope_pattern(p) for p in whole}
    for path, functions in touched.items():
        if path not in scope or path in whole:
            continue
        for qualname in functions:
            if qualname != module_scope:
                patterns.add(function_scope_pattern(path, qualname))
    return sorted(patterns), whole


def parse_shard(spec: str) -> tuple[int, int]:
    """``"2/4"`` -> ``(2, 4)``; 1-based, ``1 <= k <= n``."""
    k_text, sep, n_text = spec.partition("/")
    if not sep or not k_text.isdigit() or not n_text.isdigit():
        raise ValueError(f"shard must look like K/N, got {spec!r}")
    k, n = int(k_text), int(n_text)
    if not 1 <= k <= n:
        raise ValueError(f"shard index must satisfy 1 <= K <= N, got {spec!r}")
    return k, n


def mutable_functions(source: str) -> dict[str, int]:
    """``qualname -> source lines`` for every function mutmut mutates in *source*.

    Mirrors mutmut 3.x's trampoline: a top-level ``def`` (``x_<name>``) and a
    method of a top-level class (``xǁClassǁmethod``) get mutants; a nested
    function's mutants are attributed to its enclosing top-level function, and
    a nested class's methods get none. A name defined twice (``@overload``, a
    property setter) is one unit, its weights summed. Unparseable source
    yields ``{}``; any mutant this misses is caught at run time by
    `unassigned_records`, never silently dropped.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return {}
    units: dict[str, int] = {}

    def add(qualname: str, node: ast.AST) -> None:
        end = getattr(node, "end_lineno", None) or node.lineno  # type: ignore[attr-defined]
        units[qualname] = units.get(qualname, 0) + end - node.lineno + 1  # type: ignore[attr-defined]

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            add(node.name, node)
        elif isinstance(node, ast.ClassDef):
            for member in node.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    add(f"{node.name}.{member.name}", member)
    return units


def mutation_units(
    only_mutate: Iterable[str], repo_root: Path = REPO_ROOT
) -> dict[tuple[str, str], int]:
    """``(module_path, qualname) -> weight`` over every ``only_mutate`` module."""
    units: dict[tuple[str, str], int] = {}
    for module in sorted(set(only_mutate)):
        try:
            source = (repo_root / module).read_text(encoding="utf-8")
        except OSError:
            continue
        for qualname, weight in mutable_functions(source).items():
            units[(module, qualname)] = weight
    return units


def shard_assignment(
    only_mutate: Iterable[str], k: int, n: int, repo_root: Path = REPO_ROOT
) -> list[tuple[str, str]]:
    """The ``(module_path, qualname)`` units shard *k* of *n* measures.

    Longest-processing-time greedy over each function's line count (a proxy
    for its mutant count), ties broken by identity, so the partition is
    deterministic, disjoint, and covers every unit exactly across all *n*
    shards — every shard computes the same assignment independently.

    The unit is a function, not a module: the largest modules
    (``diff_platform.py``, ``diff_types.py``) each held close to a whole
    job's 5h45m mutmut budget on their own, so module-level shards could not
    be balanced below that floor however many there were.
    """
    units = mutation_units(only_mutate, repo_root)
    order = sorted(units, key=lambda u: (-units[u], u))
    loads = [0] * n
    assigned: list[list[tuple[str, str]]] = [[] for _ in range(n)]
    for unit in order:
        target = min(range(n), key=lambda i: (loads[i], i))
        assigned[target].append(unit)
        loads[target] += units[unit]
    return sorted(assigned[k - 1])


def unit_key(unit: tuple[str, str]) -> str:
    """``("abicheck/x.py", "C.m")`` -> ``"abicheck/x.py::C.m"`` (JSON-safe)."""
    return f"{unit[0]}::{unit[1]}"


def unassigned_records(
    records: Iterable[tuple[str, str]], units: Iterable[tuple[str, str]]
) -> list[tuple[str, str]]:
    """``(module_path, function)`` pairs mutmut produced that no unit covers.

    Every shard sees mutmut's full listing (out-of-shard mutants read ``not
    checked``), so each can check the whole partition. A non-empty answer
    means `mutable_functions` no longer matches mutmut's own enumeration and
    those mutants would be measured by no shard at all.
    """
    known = set(units)
    return sorted({r for r in records if r not in known})


def report_unassigned(
    records: Iterable[tuple[str, str]], units: Iterable[tuple[str, str]]
) -> bool:
    """Print and return False when some mutant belongs to no shard."""
    stray = unassigned_records(records, units)
    if stray:
        print(
            f"ERROR: {len(stray)} function(s) carry mutants that no shard is "
            "assigned (mutation_scope.mutable_functions no longer matches "
            "mutmut's enumeration): " + ", ".join(f"{m}::{f}" for m, f in stray[:20])
        )
    return not stray


def shard_drift_gate(
    records: Iterable[tuple[str, str, bool]],
    baseline_modules: Mapping[str, int],
    function_baseline: Mapping[tuple[str, str], int],
    units: list[tuple[str, str]],
    baseline_file: str,
) -> int:
    """The drift gate for a function shard: prints its verdict, returns 0/1.

    A shard holds part of a module, so the module totals in the baseline
    cannot be compared; its per-function counts can. A baseline without them
    fails closed rather than scoring nothing.
    """
    if baseline_modules and not function_baseline:
        print(
            f"ERROR: {baseline_file} carries no per-function counts, which a "
            "function-sharded run needs to score drift. Re-record it "
            "(workflow_dispatch, write_baseline: true)."
        )
        return 1
    failures = check_shard_drift(records, function_baseline, units)
    if failures:
        print(
            "ERROR: survivor count rose above baseline for this shard's "
            "functions — a test was weakened or new under-verified code landed:"
        )
        print("\n".join(failures))
        return 1
    print(f"mutation-score: baseline OK for this shard's {len(units)} function(s)")
    return 0


def check_shard_drift(
    records: Iterable[tuple[str, str, bool]],
    function_baseline: Mapping[tuple[str, str], int],
    units: Iterable[tuple[str, str]],
) -> list[str]:
    """Per-module drift over only the functions this shard measured.

    *records* is ``(module_path, function, is_survivor)``. A shard holds part
    of a module, so its module total is compared against the sum of the
    baseline's per-function counts for exactly those functions.
    """
    mine = set(units)
    now: dict[str, int] = {}
    for module, function, survived in records:
        if survived and (module, function) in mine:
            now[module] = now.get(module, 0) + 1
    was: dict[str, int] = {}
    for module, function in mine:
        was[module] = was.get(module, 0) + function_baseline.get((module, function), 0)
    failures = []
    for module in sorted(set(now) | set(was)):
        current, recorded = now.get(module, 0), was.get(module, 0)
        if current > recorded:
            failures.append(
                f"  {module}: {recorded} -> {current} (+{current - recorded})"
            )
    return failures


def is_splittable(
    scope_patterns: list[str] | None,
    only_mutate: list[str] | None,
    global_total_only: bool,
) -> bool:
    """Whether a run measures the whole population and can be split.

    A diff-scoped run (``scope_patterns`` set) is already small, and a
    global-total-only baseline cannot be scored per shard: both run whole in
    shard 1. ``--shard`` and ``--plan-shards`` both answer through here, so
    the plan the workflow starts runners from cannot disagree with what each
    shard then does.
    """
    return scope_patterns is None and bool(only_mutate) and not global_total_only


def planned_shards(
    n: int,
    only_mutate: list[str] | None,
    splittable: bool,
    repo_root: Path = REPO_ROOT,
) -> list[int]:
    """The 1-based shard indices of *n* that have work (``--plan-shards``).

    A shard that would be assigned no unit (more shards than functions) is
    not started at all.
    """
    if not splittable or not only_mutate:
        return [1]
    return [
        k for k in range(1, n + 1) if shard_assignment(only_mutate, k, n, repo_root)
    ]


def merge_baseline_parts(
    parts: list[dict[str, object]], units: Iterable[tuple[str, str]]
) -> dict[str, object]:
    """Combine per-shard baseline documents into one full baseline.

    Each part declares ``measured_units`` (`unit_key` spellings). The parts
    must be disjoint and their union must equal *units* — a missing shard
    would record its functions as zero survivors, the silent baseline loss
    the gate's own ``--write-baseline`` guards already refuse. A module split
    across shards has its survivors, keys and per-function counts summed.
    """
    expected = {unit_key(u) for u in units}
    seen: set[str] = set()
    modules: dict[str, dict[str, object]] = {}
    comment: object = None
    for part in parts:
        measured = part.get("measured_units")
        if not isinstance(measured, list):
            raise ValueError("baseline part lacks a measured_units list")
        overlap = seen & set(measured)
        if overlap:
            raise ValueError(f"baseline parts overlap on {sorted(overlap)}")
        seen |= set(measured)
        part_modules = part.get("modules")
        if not isinstance(part_modules, dict):
            raise ValueError("baseline part lacks a modules mapping")
        for module, entry in part_modules.items():
            if not isinstance(entry, dict):
                raise ValueError(f"baseline part entry for {module} is malformed")
            functions = entry.get("functions") or {}
            for function in functions:
                if unit_key((module, function)) not in measured:
                    raise ValueError(
                        f"baseline part records {module}::{function}, which it "
                        "did not measure"
                    )
            merged = modules.setdefault(
                module, {"survivors": 0, "keys": [], "functions": {}}
            )
            merged["survivors"] += int(entry.get("survivors", 0))  # type: ignore[operator]
            merged["keys"] = sorted([*merged["keys"], *(entry.get("keys") or [])])  # type: ignore[misc]
            merged["functions"].update(functions)  # type: ignore[attr-defined]
        comment = comment or part.get("_comment")
    if seen != expected:
        missing = sorted(expected - seen)
        extra = sorted(seen - expected)
        raise ValueError(
            f"baseline parts do not partition the mutation units "
            f"(missing={missing[:20]}, unexpected={extra[:20]})"
        )
    return {
        "_comment": comment,
        "total_survivors": sum(int(m["survivors"]) for m in modules.values()),  # type: ignore[call-overload]
        "modules": dict(sorted(modules.items())),
    }


def _section(doc: Mapping[str, object], keys: tuple[str, ...]) -> object:
    node: object = doc
    for key in keys:
        if not isinstance(node, Mapping):
            return None
        node = node.get(key)
    return node


def _module_names(path: str) -> set[str]:
    """Spellings an import of the ``tests/`` module at *path* can use.

    ``tests/regressions/manifest.py`` answers ``manifest`` and
    ``regressions.manifest`` (and ``tests.regressions.manifest``); a
    package's ``__init__.py`` answers its directory's names.
    """
    parts = list(PurePosixPath(path).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return {".".join(parts[start:]) for start in range(len(parts))}


def _imported_modules(text: str) -> set[str] | None:
    """Every dotted module an import statement in *text* can name.

    ``from pkg import name`` yields both ``pkg`` and ``pkg.name``: *name*
    may be a submodule. Relative dots are dropped. ``None`` when *text* does
    not parse, which callers treat as "may import anything".
    """
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return None
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if base:
                mods.add(base)
            for alias in node.names:
                mods.add(f"{base}.{alias.name}" if base else alias.name)
    return mods


def _imports_any(text: str, names: set[str], packages: set[str]) -> bool:
    """Does *text* import a module spelled as one of *names*?

    A spelling matches exactly or as a dotted suffix (``tests._util`` for
    ``_util``). A *packages* spelling (a changed ``__init__.py``) also
    matches any submodule import, since importing ``pkg.sub`` runs
    ``pkg/__init__.py``. Generous by design: a false positive only adds a
    test file to the stats pass.
    """
    mods = _imported_modules(text)
    if mods is None:
        return True
    for mod in mods:
        dotted = "." + mod + "."
        if any(dotted.endswith("." + n + ".") for n in names):
            return True
        if any("." + n + "." in dotted for n in packages):
            return True
    return False


def helper_importers(changed: Iterable[str], sources: Mapping[str, str]) -> set[str]:
    """Test files that import a changed ``tests/`` helper, directly or not.

    *sources* maps every ``tests/**/*.py`` path to its text. The closure is
    a fixpoint over helpers importing helpers, so a test reaching a changed
    helper through another one is found too. Only ``test_*.py`` files are
    returned: those are what the stats-pass selection lists.
    """
    reached = set(changed)
    frontier = set(changed)
    while frontier:
        names = set().union(*(_module_names(p) for p in frontier))
        packages = set().union(
            *(
                _module_names(p)
                for p in frontier
                if PurePosixPath(p).name == "__init__.py"
            )
        )
        frontier = {
            path
            for path, text in sources.items()
            if path not in reached and _imports_any(text, names, packages)
        }
        reached |= frontier
    return {p for p in reached if PurePosixPath(p).name.startswith("test_")}


def extend_selection(
    selection: list[str],
    changed: list[str],
    exists: Callable[[str], bool],
    sources: Mapping[str, str] | None = None,
) -> list[str]:
    """The stats-pass selection for a run whose diff changed *changed* paths.

    Each added or changed ``tests/**/test_*.py`` that still exists joins the
    committed selection, so a PR's own new tests are never left out of its
    measurement. A changed shared helper module adds the test files that
    import it (`helper_importers`, over *sources*). A changed ``conftest.py``
    adds nothing: it supplies fixtures to files the selection already runs,
    and the trace-based completeness check (``gen_mutation_test_selection
    --check``, on every full run) is what catches a file that newly reaches
    mutated code. Never narrows.

    This used to widen to the whole suite for any such change. Most PRs
    touch a helper or a conftest, so most diff-scoped runs then executed
    ~50k tests per mutant and hit the 5h45m mutmut timeout -- a run that
    measured nothing because it could never finish.

    The result is sorted and unique, like the committed file: the widened
    file is written over it before mutmut copies ``tests/``, and the stats
    pass runs the suite's own well-formedness check against it -- an
    appended entry out of order failed that check and aborted every shard.
    """
    out = set(selection)
    helpers = []
    for path in changed:
        if (
            not path.startswith("tests/")
            or not path.endswith(".py")
            or not exists(path)
        ):
            continue
        name = PurePosixPath(path).name
        if name.startswith("test_"):
            out.add(path)
        elif name != "conftest.py":
            helpers.append(path)
    if helpers and sources is not None:
        out |= {p for p in helper_importers(helpers, sources) if exists(p)}
    return sorted(out)


def pyproject_mutation_config_changed(old_text: str | None, new_text: str) -> bool:
    """Did any `MUTATION_RELEVANT_PYPROJECT_KEYS` section change?

    Fails toward "changed": an unparseable side, or a pyproject absent at the
    base, cannot be shown irrelevant.
    """
    if old_text is None:
        return True
    try:
        old = tomllib.loads(old_text)
        new = tomllib.loads(new_text)
    except tomllib.TOMLDecodeError:
        return True
    return any(
        _section(old, keys) != _section(new, keys)
        for keys in MUTATION_RELEVANT_PYPROJECT_KEYS
    )


def _git_changed_paths(base: str) -> list[str]:
    proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
        ["git", "diff", "--name-only", "--diff-filter=AMR", base, "HEAD"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return [line for line in proc.stdout.splitlines() if line]


def _git_show(ref: str, path: str) -> str | None:
    proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
        ["git", "show", f"{ref}:{path}"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return proc.stdout if proc.returncode == 0 else None


def _merge_base(base_ref: str) -> str | None:
    proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
        ["git", "merge-base", base_ref, "HEAD"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return proc.stdout.strip() if proc.returncode == 0 else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    cfg = sub.add_parser(
        "pyproject-changed",
        help="Print true/false: did the mutation-relevant pyproject config change?",
    )
    cfg.add_argument("--base-ref", required=True)
    ext = sub.add_parser(
        "extend-selection",
        help="Add this branch's changed test files to the stats-pass selection file.",
    )
    ext.add_argument("--base-ref", required=True)
    ext.add_argument("--selection", required=True)
    merge = sub.add_parser("merge-baselines", help="Merge per-shard baseline parts.")
    merge.add_argument("parts", nargs="+")
    merge.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    if args.cmd == "pyproject-changed":
        base = _merge_base(args.base_ref)
        old = _git_show(base, "pyproject.toml") if base else None
        new = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        print("true" if pyproject_mutation_config_changed(old, new) else "false")
        return 0

    if args.cmd == "extend-selection":
        base = _merge_base(args.base_ref)
        if base is None:
            print(f"ERROR: no merge base with {args.base_ref}")
            return 1
        changed = _git_changed_paths(base)
        path = Path(args.selection)
        old_sel = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln]
        sources = {
            p.relative_to(REPO_ROOT).as_posix(): p.read_text(
                encoding="utf-8", errors="replace"
            )
            for p in (REPO_ROOT / "tests").rglob("*.py")
        }
        new_sel = extend_selection(
            old_sel, changed, lambda p: (REPO_ROOT / p).is_file(), sources
        )
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from gen_mutation_test_selection import selection_problems  # noqa: PLC0415

        problems = selection_problems(new_sel, lambda p: (REPO_ROOT / p).is_file())
        if problems:
            # Fail here, at the step that produced it, not 20 minutes later
            # inside mutmut's stats pass where the suite checks the same rule.
            print(f"ERROR: widened selection is malformed: {problems}")
            return 1
        path.write_text("\n".join(new_sel) + "\n", encoding="utf-8")
        added = [p for p in new_sel if p not in old_sel]
        print(
            f"mutation-scope: stats selection {len(old_sel)} -> {len(new_sel)} entries: {added}"
        )
        return 0

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from check_mutation_score import load_only_mutate_globs  # noqa: E402,PLC0415

    only_mutate = load_only_mutate_globs()
    if not only_mutate:
        print("ERROR: cannot read [tool.mutmut].only_mutate")
        return 1
    parts = [json.loads(Path(p).read_text(encoding="utf-8")) for p in args.parts]
    try:
        doc = merge_baseline_parts(parts, mutation_units(only_mutate))
    except ValueError as e:
        print(f"ERROR: {e}")
        return 1
    Path(args.out).write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    print(f"mutation-scope: merged {len(parts)} part(s) into {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
