#!/usr/bin/env python3
# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Which code each use case runs, and what changed about it.

Four subcommands share one recorded artifact:

``record``
    Runs the use-case sources under coverage, each run tagged with its id,
    and writes ``usecase-paths.json``: for every run, the set of
    ``abicheck`` functions it executed, plus the inventory of every function
    defined in the tree. Two sources:

    * ``scenarios`` -- the automated end-to-end scenarios of
      ``tests/scenarios/*.yaml`` (``SC-*``, each tagged with the use case it
      ``validates:``). Fast and compiler-free: snapshots, not binaries.
    * ``flows`` -- real CLI command lines from ``scripts/usecase_flows.yaml``
      run against real compiled catalog cases. This is the source that
      reaches the binary readers and header frontends; it needs gcc, cmake
      and castxml (``--catalog-build`` points at a configured build, or
      ``--build-catalog`` builds the listed cases).

``rank``
    Importance. Each function is placed in a tier by how many distinct use
    cases reach it: ``shared`` (three or more), ``use-case`` (one or two),
    ``unreached`` (none). With ``--unit-coverage`` (a coverage data file
    from the unit suite) it also lists use-case code whose own unit
    coverage is below a threshold -- the code that matters most and is
    verified least.

``diff``
    Two recordings (base and head) compared:

    * **path changes** -- per run, functions that entered or left its
      path. A refactor that routes a use case through different code shows
      up here even when every test passes.
    * **relevance drops** -- functions some use case reached on base and
      none reaches on head. Not a deletion list: a signal to look at why
      the code stopped being used (dead now, or a use case lost a path).
    * newly reached functions, and runs present on one side only.

``dead``
    Unreached functions classified by production reference
    (``production_references.py``): dead (every reference lies inside other
    dead code, to a fixpoint), documented API, ADR/plan-named, still
    referenced, or not checkable by name. A review list for
    ``docs/contribute/plans/dead-code-and-single-owner.md``.

Being unreached is never a verdict that code is dead: platform readers for
PE/Mach-O are unreached on Linux because no such toolchain runs there, and
documented API may have no CLI scenario at all.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = "abicheck"
SCHEMA_VERSION = 1
SCENARIO_TESTS = "tests/test_scenarios.py"
DEFAULT_FLOWS = ROOT / "scripts" / "usecase_flows.yaml"
MODULE_LEVEL = "<module>"
SCENARIO_TIMEOUT = 1800
FLOW_STEP_TIMEOUT = 600


# ── function inventory ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class FunctionSpan:
    qualname: str
    start: int  # first body line (see _body_start)
    end: int
    def_line: int = 0  # first decorator or the def line itself


def _body_start(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> int | None:
    """First line of *fn* that runs only when *fn* is called.

    The ``def`` line, its decorators and its default arguments execute when
    the enclosing module or class body runs, so counting them would mark
    every function of an imported module as reached. A leading docstring is
    not a statement coverage records.

    ``None`` -- no span, never reported as reached -- for a body that is
    only a docstring, and for a one-line ``def f(): body``: its body shares
    the ``def`` line, so line coverage cannot tell defining it from calling
    it. Those are almost all protocol stubs (33 of ~9,300 functions).
    """
    body = fn.body
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    if not body or body[0].lineno == fn.lineno:
        return None
    return body[0].lineno


def function_spans(source: str) -> list[FunctionSpan]:
    """Every ``def`` in *source* with its qualified name and the line span
    of its body (see :func:`_body_start`): a line in the span executes only
    when the function is called.
    """
    spans: list[FunctionSpan] = []

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                qual = f"{prefix}{child.name}"
                start = _body_start(child)
                if start is not None:
                    first = min(
                        [child.lineno] + [d.lineno for d in child.decorator_list]
                    )
                    spans.append(
                        FunctionSpan(qual, start, child.end_lineno or start, first)
                    )
                visit(child, f"{qual}.<locals>.")
            elif isinstance(child, ast.ClassDef):
                visit(child, f"{prefix}{child.name}.")
            else:
                visit(child, prefix)

    visit(ast.parse(source), "")
    return spans


def line_owner_map(spans: list[FunctionSpan]) -> dict[int, str]:
    """Line -> innermost enclosing function (inner spans win)."""
    owner: dict[int, str] = {}
    # Outer spans first, inner later, so the innermost one is written last.
    for span in sorted(spans, key=lambda s: (s.start, -s.end)):
        for line in range(span.start, span.end + 1):
            owner[line] = span.qualname
    return owner


def function_id(rel_path: str, qualname: str) -> str:
    return f"{rel_path}::{qualname}"


def inventory(root: Path = ROOT) -> dict[str, list[str]]:
    """Every function defined under the package: ``{rel_path: [qualname]}``."""
    out: dict[str, list[str]] = {}
    for path in sorted((root / PACKAGE).rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        try:
            spans = function_spans(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        out[rel] = sorted({s.qualname for s in spans})
    return out


# ── coverage data -> per-context function sets ──────────────────────────────


def functions_by_context(data_file: Path, root: Path = ROOT) -> dict[str, set[str]]:
    """Read a (combined) coverage data file recorded with contexts and map
    each context to the set of function ids it executed. Module-level lines
    (imports, constants) are not a path and are left out."""
    import coverage

    data = coverage.CoverageData(basename=str(data_file))
    data.read()
    result: dict[str, set[str]] = defaultdict(set)
    owners: dict[str, dict[int, str]] = {}
    for measured in data.measured_files():
        path = Path(measured)
        try:
            rel = path.resolve().relative_to(root).as_posix()
        except ValueError:
            continue
        if not rel.startswith(f"{PACKAGE}/"):
            continue
        if rel not in owners:
            try:
                owners[rel] = line_owner_map(
                    function_spans(path.read_text(encoding="utf-8"))
                )
            except (OSError, SyntaxError, UnicodeDecodeError):
                owners[rel] = {}
        owner = owners[rel]
        for line, contexts in (data.contexts_by_lineno(measured) or {}).items():
            qual = owner.get(line)
            if qual is None:
                continue
            for ctx in contexts:
                if ctx:
                    result[ctx].add(function_id(rel, qual))
    return dict(result)


# ── source: scenario catalog ────────────────────────────────────────────────


def load_scenarios(root: Path = ROOT) -> list[dict]:
    import yaml

    scenarios: list[dict] = []
    for path in sorted((root / "tests" / "scenarios").glob("*.yaml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        scenarios.extend(doc.get("scenarios") or [])
    return [s for s in scenarios if s.get("automated") and s.get("test")]


def scenario_context_key(context: str) -> str | None:
    """``tests/test_scenarios.py::test_sc_x[p]|run`` -> ``test_sc_x``.

    Only the ``run`` phase counts: setup and teardown build fixtures, which
    is not what the scenario is about.
    """
    nodeid, _, phase = context.partition("|")
    if phase != "run" or "::" not in nodeid:
        return None
    name = nodeid.rsplit("::", 1)[1]
    return name.split("[", 1)[0]


def record_scenarios(
    workdir: Path, root: Path = ROOT
) -> tuple[dict[str, dict], list[str]]:
    scenarios = load_scenarios(root)
    by_test: dict[str, list[dict]] = defaultdict(list)
    for sc in scenarios:
        by_test[sc["test"]].append(sc)
    data_file = workdir / ".coverage.scenarios"
    env = _env_for(root, COVERAGE_FILE=str(data_file))
    cmd = [
        sys.executable, "-m", "pytest", SCENARIO_TESTS, "-q",
        "-p", "no:cacheprovider", "-p", "no:randomly", "-o", "addopts=",
        "-k", " or ".join(sorted(by_test)),
        f"--cov={PACKAGE}", "--cov-context=test", "--cov-report=",
    ]  # fmt: skip
    failures: list[str] = []
    try:
        proc = subprocess.run(
            cmd,
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=SCENARIO_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return {}, [f"scenario tests timed out after {SCENARIO_TIMEOUT}s"]
    if proc.returncode != 0:
        tail = "\n".join(proc.stdout.splitlines()[-15:])
        failures.append(f"scenario tests exited {proc.returncode}:\n{tail}")
    runs: dict[str, dict] = {}
    if not data_file.exists():
        failures.append("scenario run produced no coverage data")
        return runs, failures
    per_ctx = functions_by_context(data_file, root)
    per_test: dict[str, set[str]] = defaultdict(set)
    for ctx, funcs in per_ctx.items():
        key = scenario_context_key(ctx)
        if key:
            per_test[key] |= funcs
    for test, scs in by_test.items():
        for sc in scs:
            funcs = per_test.get(test, set())
            if not funcs:
                failures.append(f"{sc['id']}: {test} executed no {PACKAGE} code")
            runs[sc["id"]] = {
                "source": "scenario",
                "use_case": sc.get("validates"),
                "functions": sorted(funcs),
            }
    return runs, failures


# ── source: real CLI flows over the catalog ─────────────────────────────────


def load_flows(path: Path) -> dict:
    import yaml

    return yaml.safe_load(path.read_text(encoding="utf-8"))


def build_catalog(build_dir: Path, cases: list[str], root: Path = ROOT) -> None:
    build_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["cmake", "-S", str(root / "catalog"), "-B", str(build_dir)],
        check=True, capture_output=True,
    )  # fmt: skip
    targets: list[str] = []
    for case in cases:
        targets += [f"{case}_v1", f"{case}_v2"]
    proc = subprocess.run(
        ["cmake", "--build", str(build_dir), "-j", str(os.cpu_count() or 2),
         "--target", *targets],
        capture_output=True, text=True,
    )  # fmt: skip
    if proc.returncode != 0:
        # Per-case fallback so one case that does not build on this host
        # costs only itself.
        for case in cases:
            subprocess.run(
                ["cmake", "--build", str(build_dir), "--target",
                 f"{case}_v1", f"{case}_v2"],
                capture_output=True,
            )  # fmt: skip
    for case in cases:
        subprocess.run(
            ["cmake", "--build", str(build_dir), "--target", f"{case}_app"],
            capture_output=True,
        )  # fmt: skip


def _first_existing(directory: Path, names: list[str]) -> Path | None:
    for name in names:
        hit = sorted(directory.glob(name))
        if hit:
            return hit[0]
    return None


def case_inputs(
    case: str, build_dir: Path, out: Path, root: Path = ROOT
) -> dict[str, str]:
    src = root / "catalog" / "cases" / case
    built = build_dir / case
    inputs: dict[str, str] = {"out": str(out)}
    v1 = _first_existing(built, ["libv1.so*", "libv1*.so", "libv1.dylib"])
    v2 = _first_existing(built, ["libv2.so*", "libv2*.so", "libv2.dylib"])
    if v1:
        inputs["v1"] = str(v1)
    if v2:
        inputs["v2"] = str(v2)
    for key, name, side in (("h1", "v1.h", "old"), ("h2", "v2.h", "new")):
        header = (
            src / name
            if (src / name).exists()
            else _first_existing(src / side, ["*.h", "*.hpp"])
        )
        if header:
            inputs[key] = str(header)
    app = _first_existing(built, ["app_v1", "app"])
    if app:
        inputs["app"] = str(app)
    if {"v1", "v2", "h1", "h2"} <= inputs.keys():
        for key, lib, hdr, ver in (("d1", "v1", "h1", "1"), ("d2", "v2", "h2", "2")):
            desc = out / f"{key}.xml"
            desc.write_text(
                f"<descriptor><version>{ver}</version>"
                f"<headers>{inputs[hdr]}</headers><libs>{inputs[lib]}</libs>"
                "</descriptor>\n",
                encoding="utf-8",
            )
            inputs[key] = str(desc)
    return inputs


def _expand(argv: list[str], inputs: dict[str, str]) -> list[str]:
    return [str(a).format(**inputs) for a in argv]


def _env_for(root: Path, **extra: str) -> dict[str, str]:
    """Child environment importing ``abicheck`` from *root*, ahead of any
    installed copy, so a base checkout is measured with its own code."""
    env = dict(os.environ, **extra)
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in (str(root), env.get("PYTHONPATH", "")) if p
    )
    return env


def record_flows(
    flows_path: Path, build_dir: Path, workdir: Path, root: Path = ROOT
) -> tuple[dict[str, dict], list[str], list[str]]:
    spec = load_flows(flows_path)
    allowed = set(spec.get("exit_codes", [0]))
    runs: dict[str, dict] = {}
    failures: list[str] = []
    skipped: list[str] = []
    data_dir = workdir / "flowcov"
    data_dir.mkdir(parents=True, exist_ok=True)
    rcfile = workdir / "flows.coveragerc"
    rcfile.write_text(
        f"[run]\nsource = {PACKAGE}\nparallel = true\n"
        f"data_file = {data_dir / '.coverage'}\n",
        encoding="utf-8",
    )
    meta: dict[str, dict] = {}
    for case in spec["cases"]:
        for flow in spec["flows"]:
            run_id = f"{flow['id']}/{case}"
            out = workdir / "out" / flow["id"] / case
            out.mkdir(parents=True, exist_ok=True)
            inputs = case_inputs(case, build_dir, out, root)
            missing = [
                n for n in ["v1", "v2", *flow.get("needs", [])] if n not in inputs
            ]
            if missing:
                skipped.append(f"{run_id}: missing {', '.join(missing)}")
                continue
            steps = flow.get("steps") or [flow["argv"]]
            env = _env_for(
                root, **{k: str(v) for k, v in (flow.get("env") or {}).items()}
            )
            for step in steps:
                cmd = [
                    sys.executable, "-m", "coverage", "run",
                    f"--rcfile={rcfile}", f"--context={run_id}",
                    "-m", PACKAGE, *_expand(step, inputs),
                ]  # fmt: skip
                try:
                    proc = subprocess.run(
                        cmd, cwd=out, env=env, capture_output=True, text=True,
                        timeout=FLOW_STEP_TIMEOUT,
                    )  # fmt: skip
                except subprocess.TimeoutExpired:
                    # Later steps of this flow depend on this one; the other
                    # flows' coverage is kept.
                    failures.append(f"{run_id}: timed out after {FLOW_STEP_TIMEOUT}s")
                    break
                if proc.returncode not in allowed:
                    err = (proc.stderr.strip().splitlines() or [""])[-1]
                    failures.append(f"{run_id}: exit {proc.returncode}: {err}")
            meta[run_id] = {"source": "flow", "use_case": flow["use_case"]}
    combined = data_dir / ".coverage"
    subprocess.run(
        [sys.executable, "-m", "coverage", "combine", f"--rcfile={rcfile}",
         "--keep", "-q", str(data_dir)],
        cwd=root, capture_output=True,
    )  # fmt: skip
    per_ctx = functions_by_context(combined, root) if combined.exists() else {}
    for run_id, info in meta.items():
        funcs = per_ctx.get(run_id, set())
        if not funcs:
            failures.append(f"{run_id}: executed no {PACKAGE} code")
        runs[run_id] = {**info, "functions": sorted(funcs)}
    return runs, failures, skipped


# ── record ──────────────────────────────────────────────────────────────────


def _git_head(root: Path) -> str | None:
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True
    )
    return proc.stdout.strip() or None


def cmd_record(args: argparse.Namespace) -> int:
    sources = set(args.source)
    root = Path(args.root).resolve()
    runs: dict[str, dict] = {}
    failures: list[str] = []
    skipped: list[str] = []
    with tempfile.TemporaryDirectory(prefix="usecase-paths-") as tmp:
        work = Path(tmp)
        if "scenarios" in sources:
            r, f = record_scenarios(work, root)
            runs.update(r)
            failures += f
        if "flows" in sources:
            if not (args.catalog_build or args.build_catalog):
                print(
                    "flows source needs --catalog-build or --build-catalog",
                    file=sys.stderr,
                )
                return 1
            build_dir = Path(args.catalog_build or args.build_catalog).resolve()
            if args.build_catalog:
                if not shutil.which("cmake"):
                    print("--build-catalog needs cmake on PATH", file=sys.stderr)
                    return 1
                build_catalog(build_dir, load_flows(args.flows)["cases"], root)
            r, f, s = record_flows(args.flows, build_dir, work, root)
            runs.update(r)
            failures += f
            skipped += s
    doc = {
        "schema_version": SCHEMA_VERSION,
        "revision": _git_head(root),
        "sources": sorted(sources),
        "runs": dict(sorted(runs.items())),
        "inventory": inventory(root),
        "failures": failures,
        "skipped": skipped,
    }
    Path(args.out).write_text(
        json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"recorded {len(runs)} runs -> {args.out}")
    for line in failures:
        print(f"FAILED  {line}", file=sys.stderr)
    for line in skipped:
        print(f"skipped {line}")
    return 1 if failures and args.strict else 0


# ── rank ────────────────────────────────────────────────────────────────────

TIER_SHARED = "shared"
TIER_USECASE = "use-case"
TIER_UNREACHED = "unreached"


def load_recording(path: Path) -> dict:
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    if doc.get("schema_version") != SCHEMA_VERSION:
        raise SystemExit(
            f"{path}: unsupported schema_version {doc.get('schema_version')}"
        )
    return doc


def all_functions(doc: dict) -> set[str]:
    return {
        function_id(rel, qual)
        for rel, quals in doc["inventory"].items()
        for qual in quals
    }


def reach(doc: dict) -> dict[str, set[str]]:
    """Function id -> the use cases (or run ids, when untagged) reaching it."""
    out: dict[str, set[str]] = defaultdict(set)
    for run_id, run in doc["runs"].items():
        label = run.get("use_case") or run_id
        for fn in run["functions"]:
            out[fn].add(label)
    return out


def tier_of(n_use_cases: int, shared_at: int) -> str:
    if n_use_cases >= shared_at:
        return TIER_SHARED
    if n_use_cases >= 1:
        return TIER_USECASE
    return TIER_UNREACHED


def rank(doc: dict, shared_at: int = 3) -> dict[str, dict]:
    reached = reach(doc)
    out: dict[str, dict] = {}
    for fn in all_functions(doc) | set(reached):
        ucs = reached.get(fn, set())
        out[fn] = {"tier": tier_of(len(ucs), shared_at), "use_cases": sorted(ucs)}
    return out


def unit_coverage_by_function(data_file: Path, root: Path = ROOT) -> dict[str, float]:
    """Statement coverage of each function in a unit-suite coverage file."""
    import coverage

    cov = coverage.Coverage(data_file=str(data_file))
    cov.load()
    out: dict[str, float] = {}
    for measured in cov.get_data().measured_files():
        path = Path(measured)
        try:
            rel = path.resolve().relative_to(root).as_posix()
        except ValueError:
            continue
        if not rel.startswith(f"{PACKAGE}/"):
            continue
        try:
            _, statements, _, missing, _ = cov.analysis2(measured)
            spans = function_spans(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - a stale file is skipped, not fatal
            continue
        owner = line_owner_map(spans)
        total: dict[str, int] = defaultdict(int)
        hit: dict[str, int] = defaultdict(int)
        missing_set = set(missing)
        for line in statements:
            qual = owner.get(line)
            if qual is None:
                continue
            total[qual] += 1
            if line not in missing_set:
                hit[qual] += 1
        for qual, n in total.items():
            out[function_id(rel, qual)] = hit[qual] / n
    return out


def cmd_rank(args: argparse.Namespace) -> int:
    doc = load_recording(args.recording)
    ranked = rank(doc, args.shared_at)
    counts: dict[str, int] = defaultdict(int)
    for info in ranked.values():
        counts[info["tier"]] += 1
    weak: list[tuple[str, float, int]] = []
    if args.unit_coverage:
        unit = unit_coverage_by_function(Path(args.unit_coverage))
        for fn, info in ranked.items():
            if info["tier"] == TIER_UNREACHED:
                continue
            pct = unit.get(fn)
            if pct is not None and pct < args.weak_below:
                weak.append((fn, pct, len(info["use_cases"])))
        weak.sort(key=lambda t: (-t[2], t[1], t[0]))
    if args.json:
        Path(args.json).write_text(
            json.dumps({"tiers": ranked, "weak": weak}, indent=1, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
    by_module: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for fn, info in ranked.items():
        by_module[fn.split("::", 1)[0]][info["tier"]] += 1
    print(f"# Use-case importance ({len(doc['runs'])} runs)\n")
    print("| tier | functions |\n|---|---|")
    for tier in (TIER_SHARED, TIER_USECASE, TIER_UNREACHED):
        print(f"| {tier} | {counts[tier]} |")
    print(f"\n## Most shared functions (top {args.top})\n")
    shared = sorted(
        (fn for fn, i in ranked.items() if i["tier"] == TIER_SHARED),
        key=lambda f: (-len(ranked[f]["use_cases"]), f),
    )
    for fn in shared[: args.top]:
        print(f"- `{fn}` -- {len(ranked[fn]['use_cases'])} use cases")
    print("\n## Modules no use case reaches\n")
    for mod, tiers in sorted(by_module.items()):
        if tiers[TIER_SHARED] == 0 and tiers[TIER_USECASE] == 0:
            print(f"- `{mod}` ({tiers[TIER_UNREACHED]} functions)")
    if args.unit_coverage:
        print(
            f"\n## Use-case code with unit coverage below {args.weak_below:.0%} "
            f"(top {args.top})\n"
        )
        for fn, pct, n in weak[: args.top]:
            print(f"- `{fn}` -- {pct:.0%} unit coverage, {n} use cases")
    return 0


# ── diff ────────────────────────────────────────────────────────────────────


def diff_recordings(base: dict, head: dict) -> dict:
    base_runs, head_runs = base["runs"], head["runs"]
    path_changes: dict[str, dict[str, list[str]]] = {}
    for run_id in sorted(set(base_runs) & set(head_runs)):
        b = set(base_runs[run_id]["functions"])
        h = set(head_runs[run_id]["functions"])
        if b != h:
            path_changes[run_id] = {
                "entered": sorted(h - b),
                "left": sorted(b - h),
            }
    base_reach, head_reach = reach(base), reach(head)
    head_defined = all_functions(head)
    dropped = sorted(fn for fn in base_reach if fn not in head_reach)
    return {
        "path_changes": path_changes,
        "relevance_dropped": [
            {
                "function": fn,
                "use_cases": sorted(base_reach[fn]),
                "still_defined": fn in head_defined,
            }
            for fn in dropped
        ],  # fmt: skip
        "newly_reached": sorted(fn for fn in head_reach if fn not in base_reach),
        "runs_only_in_base": sorted(set(base_runs) - set(head_runs)),
        "runs_only_in_head": sorted(set(head_runs) - set(base_runs)),
        "head_failures": head.get("failures", []),
    }


def render_diff_markdown(d: dict, limit: int = 40) -> str:
    lines = ["## Use-case path changes", ""]
    if not any(
        d[k] for k in ("path_changes", "relevance_dropped", "newly_reached",
                       "runs_only_in_base", "runs_only_in_head")
    ):  # fmt: skip
        lines.append("No use case changed which code it runs.")
    still = [r for r in d["relevance_dropped"] if r["still_defined"]]
    gone = [r for r in d["relevance_dropped"] if not r["still_defined"]]
    if still:
        lines += [
            "",
            f"### No longer reached by any use case ({len(still)}) -- look here",
            "",
        ]
        lines += [
            f"- `{r['function']}` (was: {', '.join(r['use_cases'])})"
            for r in still[:limit]
        ]
    if gone:
        lines += ["", f"### Removed or renamed, previously reached ({len(gone)})", ""]
        lines += [
            f"- `{r['function']}` (was: {', '.join(r['use_cases'])})"
            for r in gone[:limit]
        ]
    if d["path_changes"]:
        # One shared change (a new helper every compare now calls) would
        # otherwise repeat once per run; group runs by the change they share.
        groups: dict[tuple[tuple[str, ...], tuple[str, ...]], list[str]] = defaultdict(
            list
        )
        for run_id, ch in d["path_changes"].items():
            groups[(tuple(ch["entered"]), tuple(ch["left"]))].append(run_id)
        lines += [
            "",
            f"### Runs whose path changed ({len(d['path_changes'])} runs, {len(groups)} distinct changes)",
            "",
        ]
        for (entered, left), run_ids in sorted(
            groups.items(), key=lambda g: (-len(g[1]), g[1][0])
        )[:limit]:
            shown = ", ".join(run_ids[:6]) + (
                f" and {len(run_ids) - 6} more" if len(run_ids) > 6 else ""
            )
            lines.append(f"- **{shown}**: +{len(entered)} / -{len(left)}")
            lines += [f"  - entered `{fn}`" for fn in entered[:10]]
            lines += [f"  - left `{fn}`" for fn in left[:10]]
    if d["newly_reached"]:
        lines += ["", f"### Newly reached ({len(d['newly_reached'])})", ""]
        lines += [f"- `{fn}`" for fn in d["newly_reached"][:limit]]
    for key, title in (("runs_only_in_base", "Runs missing on head"),
                       ("runs_only_in_head", "Runs new on head"),
                       ("head_failures", "Runs that failed on head")):  # fmt: skip
        if d[key]:
            lines += ["", f"### {title} ({len(d[key])})", ""]
            lines += [f"- {x}" for x in d[key][:limit]]
    return "\n".join(lines) + "\n"


_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def changed_functions(git_base: str, root: Path = ROOT) -> set[str]:
    """Functions whose source (signature, decorators or body) the working
    tree at *root* changed relative to *git_base*."""
    proc = subprocess.run(
        ["git", "diff", "-U0", "--no-color", "--no-renames", git_base, "--", f"{PACKAGE}/"],
        cwd=root, capture_output=True, text=True, check=True,
    )  # fmt: skip
    changed_lines: dict[str, set[int]] = defaultdict(set)
    current: str | None = None
    for line in proc.stdout.splitlines():
        if line.startswith("+++ "):
            current = line[6:] if line.startswith("+++ b/") else None
        elif current and (m := _HUNK_RE.match(line)):
            start, count = int(m.group(1)), int(m.group(2) or "1")
            # A pure deletion (count 0) changes the function around line `start`.
            changed_lines[current].update(range(start, start + max(count, 1)))
    out: set[str] = set()
    for rel, lines in changed_lines.items():
        path = root / rel
        if not path.exists():
            continue
        try:
            spans = function_spans(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for span in spans:
            if any(span.def_line <= ln <= span.end for ln in lines):
                out.add(function_id(rel, span.qualname))
    return out


def render_changed_by_importance(
    changed: set[str], ranked: dict[str, dict], limit: int = 40
) -> str:
    """The functions a PR edits, most-relied-on first: where review effort
    and test scrutiny should go."""
    rows = sorted(
        changed,
        key=lambda fn: (-len(ranked.get(fn, {}).get("use_cases", [])), fn),
    )
    lines = ["## Changed functions by use-case importance", ""]
    if not rows:
        return (
            "\n".join(lines + ["This change edits no function in the package."]) + "\n"
        )
    counts: dict[str, int] = defaultdict(int)
    for fn in rows:
        counts[ranked.get(fn, {}).get("tier", TIER_UNREACHED)] += 1
    lines.append(
        f"{len(rows)} functions edited: {counts[TIER_SHARED]} shared, "
        f"{counts[TIER_USECASE]} use-case, {counts[TIER_UNREACHED]} unreached by any recorded use case."
    )
    lines.append("")
    for fn in rows[:limit]:
        info = ranked.get(fn, {"tier": TIER_UNREACHED, "use_cases": []})
        n = len(info["use_cases"])
        lines.append(
            f"- **{info['tier']}** `{fn}`" + (f" ({n} use cases)" if n else "")
        )
    return "\n".join(lines) + "\n"


def cmd_diff(args: argparse.Namespace) -> int:
    d = diff_recordings(load_recording(args.base), load_recording(args.head))
    if args.json:
        Path(args.json).write_text(json.dumps(d, indent=1) + "\n", encoding="utf-8")
    text = render_diff_markdown(d)
    if args.git_base:
        head = load_recording(args.head)
        changed = changed_functions(args.git_base, Path(args.root).resolve())
        text = (
            render_changed_by_importance(changed, rank(head, args.shared_at))
            + "\n"
            + text
        )
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if args.step_summary and summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(text)
    fail = False
    if "dropped" in args.fail_on and any(
        r["still_defined"] for r in d["relevance_dropped"]
    ):
        fail = True
    if "changed" in args.fail_on and d["path_changes"]:
        fail = True
    if "failed" in args.fail_on and d["head_failures"]:
        fail = True
    return 1 if fail else 0


# ── dead ────────────────────────────────────────────────────────────────────


def unreached_functions(doc: dict) -> set[str]:
    reached = set(reach(doc))
    return {fn for fn in all_functions(doc) if fn not in reached}


def cmd_dead(args: argparse.Namespace) -> int:
    from production_references import (
        dead_parameters,
        dead_report,
        render_markdown,
        render_parameters_markdown,
    )

    doc = load_recording(args.recording)
    root = Path(args.root).resolve()
    report = dead_report(root, unreached_functions(doc))
    params = dead_parameters(root, dead_functions=set(report.dead))
    if args.json:
        Path(args.json).write_text(
            json.dumps(
                {
                    "revision": doc.get("revision"),
                    "sources": doc.get("sources"),
                    "dead": report.dead,
                    "documented": report.documented,
                    "decided": report.decided,
                    "tests": report.tests,
                    "live": {
                        fid: f"{s.path}:{s.line}"
                        for fid, s in sorted(report.live.items())
                    },
                    "unverifiable": dict(sorted(report.unverifiable.items())),
                    "dead_parameters": params.dead,
                    "documented_parameters": params.documented,
                },
                indent=1,
            )
            + "\n",
            encoding="utf-8",
        )
    print(render_markdown(report, limit=args.top))
    print(render_parameters_markdown(params, limit=args.top))
    return 0


# ── CLI ─────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    rec = sub.add_parser("record", help="run use cases under coverage")
    rec.add_argument("--out", default="usecase-paths.json")
    rec.add_argument("--source", action="append", choices=["scenarios", "flows"],
                     help="repeatable; default: scenarios")  # fmt: skip
    rec.add_argument("--flows", type=Path, default=DEFAULT_FLOWS)
    rec.add_argument("--catalog-build", help="an already-built catalog directory")
    rec.add_argument(
        "--build-catalog", help="build the flows' cases into this directory"
    )
    rec.add_argument(
        "--root",
        default=str(ROOT),
        help="checkout to record (default: this one); its own abicheck is imported",
    )
    rec.add_argument("--strict", action="store_true", help="exit 1 when a run failed")
    rec.set_defaults(func=cmd_record)

    rk = sub.add_parser("rank", help="importance tiers from one recording")
    rk.add_argument("recording", type=Path)
    rk.add_argument("--shared-at", type=int, default=3,
                    help="use cases needed for the shared tier (default 3)")  # fmt: skip
    rk.add_argument("--unit-coverage", help="coverage data file from the unit suite")
    rk.add_argument("--weak-below", type=float, default=0.8)
    rk.add_argument("--top", type=int, default=30)
    rk.add_argument("--json", help="write the full ranking here")
    rk.set_defaults(func=cmd_rank)

    df = sub.add_parser("diff", help="compare a base and a head recording")
    df.add_argument("base", type=Path)
    df.add_argument("head", type=Path)
    df.add_argument("--json", help="write the full diff here")
    df.add_argument("--step-summary", action="store_true",
                    help="also append the report to $GITHUB_STEP_SUMMARY")  # fmt: skip
    df.add_argument("--fail-on", action="append", default=[],
                    choices=["dropped", "changed", "failed"],
                    help="exit 1 on this kind of change (default: report only)")  # fmt: skip
    df.add_argument(
        "--git-base",
        help="also list the functions changed since this revision, by importance",
    )
    df.add_argument(
        "--root",
        default=str(ROOT),
        help="checkout --git-base diffs (default: this one)",
    )
    df.add_argument("--shared-at", type=int, default=3)
    df.set_defaults(func=cmd_diff)

    dd = sub.add_parser(
        "dead",
        help="unreached functions with no production reference (a review list)",
    )
    dd.add_argument("recording", type=Path)
    dd.add_argument(
        "--root",
        default=str(ROOT),
        help="checkout the recording was made from (default: this one)",
    )
    dd.add_argument("--json", help="write the full classification here")
    dd.add_argument("--top", type=int, default=None,
                    help="list at most this many per section (default: all)")  # fmt: skip
    dd.set_defaults(func=cmd_dead)

    args = parser.parse_args(argv)
    if args.cmd == "record" and not args.source:
        args.source = ["scenarios"]
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
