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

**Sharding** (`shard_modules`, `merge_baseline_parts`). A run that must
measure the whole population is split into N disjoint module sets, one CI
job each. The per-module drift gate is per module by construction, so each
shard gates its own modules exactly as a full run would; a baseline is
recorded per shard and merged, refusing any merge whose parts do not
partition ``only_mutate`` exactly.
"""

from __future__ import annotations

import argparse
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


def shard_modules(
    only_mutate: Iterable[str], k: int, n: int, repo_root: Path = REPO_ROOT
) -> list[str]:
    """The modules shard *k* of *n* measures.

    Longest-processing-time greedy over each module's source size (a proxy
    for its mutant count), ties broken by path, so the partition is
    deterministic, disjoint, and covers ``only_mutate`` exactly across all
    *n* shards — every shard computes the same assignment independently.
    """

    def weight(path: str) -> int:
        try:
            return (repo_root / path).stat().st_size
        except OSError:
            return 0

    modules = sorted(set(only_mutate), key=lambda p: (-weight(p), p))
    loads = [0] * n
    assigned: list[list[str]] = [[] for _ in range(n)]
    for module in modules:
        target = min(range(n), key=lambda i: (loads[i], i))
        assigned[target].append(module)
        loads[target] += weight(module)
    return sorted(assigned[k - 1])


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

    A shard that would be assigned no module (``n > len(only_mutate)``) is
    not started at all.
    """
    if not splittable or not only_mutate:
        return [1]
    return [k for k in range(1, n + 1) if shard_modules(only_mutate, k, n, repo_root)]


def merge_baseline_parts(
    parts: list[dict[str, object]], only_mutate: Iterable[str]
) -> dict[str, object]:
    """Combine per-shard baseline documents into one full baseline.

    Each part must declare ``measured_modules``. The parts must be disjoint
    and their union must equal ``only_mutate`` — a missing shard would record
    its modules as zero survivors, which is the silent baseline loss the
    gate's own ``--write-baseline`` guards already refuse.
    """
    expected = set(only_mutate)
    seen: set[str] = set()
    modules: dict[str, object] = {}
    comment: object = None
    for part in parts:
        measured = part.get("measured_modules")
        if not isinstance(measured, list):
            raise ValueError("baseline part lacks a measured_modules list")
        overlap = seen & set(measured)
        if overlap:
            raise ValueError(f"baseline parts overlap on {sorted(overlap)}")
        seen |= set(measured)
        part_modules = part.get("modules")
        if not isinstance(part_modules, dict):
            raise ValueError("baseline part lacks a modules mapping")
        for module, entry in part_modules.items():
            if module not in measured:
                raise ValueError(
                    f"baseline part records {module}, which it did not measure"
                )
            modules[module] = entry
        comment = comment or part.get("_comment")
    if seen != expected:
        missing = sorted(expected - seen)
        extra = sorted(seen - expected)
        raise ValueError(
            f"baseline parts do not partition only_mutate (missing={missing}, "
            f"unexpected={extra})"
        )
    total = 0
    for entry in modules.values():
        if isinstance(entry, dict) and isinstance(entry.get("survivors"), int):
            total += entry["survivors"]
    return {
        "_comment": comment,
        "total_survivors": total,
        "modules": dict(sorted(modules.items())),
    }


def _section(doc: Mapping[str, object], keys: tuple[str, ...]) -> object:
    node: object = doc
    for key in keys:
        if not isinstance(node, Mapping):
            return None
        node = node.get(key)
    return node


def extend_selection(
    selection: list[str], changed: list[str], exists: Callable[[str], bool]
) -> list[str]:
    """The stats-pass selection for a run whose diff changed *changed* paths.

    Each added or changed ``tests/**/test_*.py`` that still exists joins the
    committed selection, so a PR's own new tests are never left out of its
    measurement. A changed shared test module (``conftest.py``, a helper)
    can change which files reach mutated code in ways no path rule can see,
    so it widens the run back to the whole suite. Never narrows.

    The result is written back over the committed selection, which the
    widened run itself checks (``test_the_committed_selection_is_well_formed``):
    so it stays sorted, and the whole suite is spelled exactly as
    ``gen_mutation_test_selection.FULL_SELECTION`` spells it.
    """
    out = list(selection)
    for path in changed:
        if (
            not path.startswith("tests/")
            or not path.endswith(".py")
            or not exists(path)
        ):
            continue
        if PurePosixPath(path).name.startswith("test_"):
            if path not in out:
                out.append(path)
        else:
            return ["tests/"]
    return sorted(set(out))


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
        new_sel = extend_selection(
            old_sel, changed, lambda p: (REPO_ROOT / p).is_file()
        )
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
        doc = merge_baseline_parts(parts, only_mutate)
    except ValueError as e:
        print(f"ERROR: {e}")
        return 1
    Path(args.out).write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    print(f"mutation-scope: merged {len(parts)} part(s) into {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
