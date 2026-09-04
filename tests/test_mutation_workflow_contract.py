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

"""The mutation lane's trigger must match what it actually mutates.

`.github/workflows/mutation.yml` filters `pull_request` on a path list, and
`[tool.mutmut].source_paths` decides what gets mutated. Two hand-maintained
copies of the same list is precisely the drift shape AGENTS.md warns about — a
module added to `source_paths` but not to the workflow is mutated only on the
weekly run, so a PR can weaken its tests and see a green board.

Same enforcement pattern as `tests/test_verify_profiles.py`, which pins that
pixi/pre-commit/CI all route through `scripts/verify.py`'s step catalog.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path, PurePosixPath

import pytest
import tomllib

yaml = pytest.importorskip("yaml")

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "mutation.yml"


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _on_block(wf: dict) -> dict:
    # PyYAML parses a bare `on:` key as the boolean True (YAML 1.1).
    return wf.get("on", wf.get(True))


def _mutmut_config() -> dict:
    with open(REPO_ROOT / "pyproject.toml", "rb") as fh:
        return tomllib.load(fh)["tool"]["mutmut"]


def _source_paths() -> list[str]:
    """What mutmut **copies** into `mutants/` — the whole importable package.

    Not the mutation scope; see `_only_mutate()`. The two were the same list
    once, and that is exactly what broke the lane: mutmut runs the suite from
    inside `mutants/`, so a copy holding 14 of 272 modules and no
    `__init__.py` shadowed the real package as a namespace package and every
    run died at "failed to collect stats".
    """
    return _mutmut_config()["source_paths"]


def _only_mutate() -> list[str]:
    """The modules actually mutated — the detector core."""
    return _mutmut_config()["only_mutate"]


def _filters() -> dict[str, list[str]]:
    """Every dorny/paths-filter list in the `resolve` job."""
    steps = _workflow()["jobs"]["resolve"]["steps"]
    step = next(s for s in steps if "paths-filter" in str(s.get("uses", "")))
    return yaml.safe_load(step["with"]["filters"])


def _paths_filter() -> list[str]:
    return _filters()["mutated"]


def _test_filter() -> list[str]:
    return _filters()["mutated_tests"]


#: The lane's own infrastructure. A change to any of these must start the
#: lane, because the real-mutmut test is `slow` and runs in no other selector.
_INFRASTRUCTURE_PATHS = {
    "scripts/mutation_results.py",
    "scripts/check_mutation_score.py",
    "scripts/mutation_scope.py",
    "tests/test_mutation_results.py",
    ".github/workflows/mutation.yml",
    "pyproject.toml",
}


def _test_globs() -> set[str]:
    """One `tests/test_<stem>*.py` glob per mutated module.

    A PR that only weakens a detector test changes no production file, so the
    source-path entries alone never start the lane and the resulting survivors
    wait for the weekly run (Codex review). Derived from `source_paths` rather
    than listed, so a module added there without its tests fails the contract
    below instead of silently losing its trigger.
    """
    return {f"tests/test_{PurePosixPath(p).stem}*.py" for p in _only_mutate()}


def test_paths_filter_covers_every_mutated_modules_tests() -> None:
    missing = _test_globs() - set(_test_filter())
    assert not missing, (
        f"mutated module(s) whose tests have no trigger: {sorted(missing)} — a "
        "PR weakening only those tests would not start this lane."
    )


def test_paths_filter_covers_every_mutated_source_path() -> None:
    missing = set(_only_mutate()) - set(_paths_filter())
    assert not missing, (
        f"mutated module(s) {sorted(missing)} have no trigger — they would be "
        "checked only on the weekly run."
    )


def test_paths_filter_covers_the_lanes_own_infrastructure() -> None:
    """Otherwise a change to the parser, its config or this workflow does not
    start the lane, and the real-mutmut test runs nowhere at all."""
    missing = _INFRASTRUCTURE_PATHS - set(_paths_filter())
    assert not missing, f"untriggered mutation-lane infrastructure: {sorted(missing)}"


def test_the_filter_is_exactly_sources_plus_infrastructure() -> None:
    """No stray entries — the filter is a contract, not a wishlist."""
    assert set(_paths_filter()) == set(_only_mutate()) | _INFRASTRUCTURE_PATHS
    assert set(_test_filter()) == _test_globs()


def test_no_step_expands_a_github_expression_inside_its_script() -> None:
    """`${{ }}` in a `run:` body is substituted before the shell sees it, so a
    branch name — attacker-chosen on a fork PR — would land as code. Every
    value this workflow needs goes through `env:` instead (CodeRabbit review,
    zizmor template-injection)."""
    offenders = []
    for job in _workflow()["jobs"].values():
        for step in job.get("steps", []):
            script = step.get("run")
            if script and "${{" in script:
                offenders.append((step.get("name", "?"), script))
    assert not offenders, f"expressions expanded inside run: {offenders}"


def test_hypothesis_deadlines_are_relaxed_only_under_mutmut(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A wall-clock deadline is not the property under test.

    The trampoline makes every mutated call slower, so a 200ms per-example
    deadline turns property tests into flaky ones for the whole lane — one
    aborted mutmut's clean-test phase while passing standalone. The relaxation
    is keyed on the variable's *presence*, because mutmut sets it to the empty
    string for exactly that phase.
    """
    conftest = REPO_ROOT / "tests" / "conftest.py"
    source = conftest.read_text(encoding="utf-8")
    assert '"MUTANT_UNDER_TEST" not in os.environ' in source, (
        "a truthiness check would miss the clean-test phase, where mutmut sets "
        "the variable to an empty string"
    )
    assert "deadline=None" in source
    # The one that actually aborted the lane: mutmut runs the suite several
    # times in one process, so a `@given` method is re-invoked with a fresh
    # pytest instance and Hypothesis reports "multiple different executors".
    assert "differing_executors" in source

    spec = importlib.util.spec_from_file_location("_conftest_probe", conftest)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.delenv("MUTANT_UNDER_TEST", raising=False)
    from hypothesis import settings

    before = settings.default.deadline
    spec.loader.exec_module(module)
    assert settings.default.deadline == before, (
        "an ordinary pytest run must keep its deadlines"
    )


def test_the_lane_does_not_cache_mutmut_results() -> None:
    """Removed deliberately, and pinned so it cannot return unnoticed.

    mutmut skips a mutant whose *source function hash* is unchanged, so reuse
    is sound only if nothing outside those functions changed. Three review
    rounds found ways that breaks — a test-only commit, a module-level
    constant, a deletion-only hunk — and the conservative rule covering the
    last one (any hunk containing a removal) matches essentially every edit,
    since a modification *is* a removal plus an addition. At that point the
    cache never hits and only carries risk, so the lane does a full run.
    Reinstating it needs a design that consults the removed side of the diff,
    not another new-side heuristic.
    """
    steps = _workflow()["jobs"]["mutmut"]["steps"]
    caches = [s for s in steps if str(s.get("uses", "")).startswith("actions/cache")]
    assert not caches, (
        "a mutmut cache was reintroduced — see this test's docstring for why "
        "new-side heuristics cannot make it safe"
    )


def test_the_trigger_has_no_workflow_level_paths_filter() -> None:
    """A workflow-level `paths:` is ANDed with `types:`, so labelling a PR that
    touches none of those files would not start the workflow — the documented
    `mutation` label override could never fire. The decision is made at job
    level instead, so path-match and label can be ORed."""
    pull_request = _on_block(_workflow())["pull_request"]
    assert "paths" not in pull_request
    assert "labeled" in pull_request["types"]


def test_the_run_decision_is_path_match_or_label() -> None:
    steps = _workflow()["jobs"]["resolve"]["steps"]
    decide = next(s for s in steps if s.get("id") == "decide")
    script = decide["run"]
    assert (
        '"$MATCHED" = "true" ] || [ "$MATCHED_TESTS" = "true" ] '
        '|| [ "$LABELLED" = "true"'
    ) in script, (
        "the run decision must OR the production path match, the detector-test "
        "match and the label"
    )
    # A non-PR event (schedule / dispatch) must always run.
    assert 'if [ "$GITHUB_EVENT_NAME" != "pull_request" ]' in script


def test_a_detector_test_change_makes_the_pr_lane_fail_closed() -> None:
    """A test-only diff has no changed production function to scope to, so
    --diff-scoped alone reports "gated nothing" and exits 0 — green for a run
    that checked nothing (Codex review)."""
    steps = _workflow()["jobs"]["mutmut"]["steps"]
    pr_step = next(
        s for s in steps if "--diff-scoped" in str(s.get("run", "")) and "run" in s
    )
    assert "--require-baseline" in pr_step["run"]
    assert "require_baseline" in pr_step["env"]["REQUIRE_BASELINE"]


def _require_baseline_step() -> dict:
    steps = _workflow()["jobs"]["resolve"]["steps"]
    return next(s for s in steps if s.get("id") == "require_baseline")


def test_a_label_forced_run_also_fails_closed() -> None:
    """The `mutation` label is documented as the complete check, so a
    label-forced run on a test-only diff outside the globs must not go green
    on "gated nothing" — the one run someone explicitly asked to be thorough
    (Codex review).

    P1 review: the earlier version of this step computed require_baseline
    from the AGGREGATE MATCHED/MATCHED_TESTS booleans in bash, which could
    not distinguish "the changed test's own paired production module also
    changed" from "some OTHER module changed, or only lane infrastructure
    did" — see scripts/mutation_scope.py's require_baseline_for_pr()
    docstring for both concrete counterexamples this step's own decide
    branch used to miss. Fixed by delegating to that per-module-correlated
    Python function instead of restating the label/matched-test condition
    in bash here at all — this test now pins that delegation (and that the
    label threads through as --labelled) rather than the retired bash
    condition text.
    """
    step = _require_baseline_step()
    assert "require_baseline_for_pr" not in step["run"]  # invoked via the CLI
    assert "scripts/mutation_scope.py" in step["run"]
    assert "--print-require-baseline" in step["run"]
    assert "--labelled" in step["run"]
    assert "LABELLED" in step["env"]
    assert "mutation" in str(step["env"]["LABELLED"])


def test_labelled_prs_keep_the_full_mutation_scope() -> None:
    """The `mutation` label requests a complete baseline-backed run.

    Scope narrowing rewrites `only_mutate`; doing that for a labelled run would
    omit unmodified configured modules and turn their baseline comparison into
    a false zero-mutant success.
    """
    steps = _workflow()["jobs"]["mutmut"]["steps"]
    scope = next(
        s
        for s in steps
        if s.get("name") == "Narrow mutation scope to PR-implicated detector modules"
    )
    condition = str(scope.get("if", ""))
    assert "github.event_name == 'pull_request'" in condition
    assert "!contains(github.event.pull_request.labels.*.name, 'mutation')" in condition


def test_the_require_baseline_signal_is_published_by_resolve() -> None:
    """Otherwise the fail-closed branch above reads an always-empty variable
    and silently never fires. The PR-path value comes from the
    require_baseline step (real per-module diff correlation); the
    non-PR-path value ("false", weekly/dispatch use a different mechanism
    entirely) still comes from the decide step -- the job output falls back
    to it only because the require_baseline step doesn't run outside a PR."""
    outputs = _workflow()["jobs"]["resolve"]["outputs"]
    assert "require_baseline" in outputs
    assert (
        "steps.require_baseline.outputs.require_baseline" in outputs["require_baseline"]
    )
    assert "steps.decide.outputs.require_baseline" in outputs["require_baseline"]

    steps = _workflow()["jobs"]["resolve"]["steps"]
    decide = next(s for s in steps if s.get("id") == "decide")
    assert 'echo "require_baseline=false"' in decide["run"]

    step = _require_baseline_step()
    assert step["if"] == "github.event_name == 'pull_request'"
    assert '>> "$GITHUB_OUTPUT"' in step["run"]


def test_the_mutmut_job_is_gated_on_that_decision() -> None:
    job = _workflow()["jobs"]["mutmut"]
    assert job["needs"] == "resolve"
    assert "needs.resolve.outputs.run == 'true'" in job["if"]


def test_the_scheduled_lane_requires_a_baseline() -> None:
    """Otherwise a completed weekly run returns 0 however many mutants survive."""
    steps = _workflow()["jobs"]["mutmut"]["steps"]
    scheduled = next(
        s for s in steps if "schedule" in str(s.get("if", "")) and "run" in s
    )
    assert "--require-baseline" in scheduled["run"]


def test_every_mutated_module_exists() -> None:
    missing = [p for p in _only_mutate() if not (REPO_ROOT / p).is_file()]
    assert not missing, f"[tool.mutmut].only_mutate names missing files: {missing}"


def test_source_paths_is_the_importable_package_not_a_file_list() -> None:
    """The regression that made this lane fail on its first real run.

    mutmut copies `source_paths` into `mutants/` and runs the suite from
    there. With individual files listed, `mutants/abicheck/` held 14 of 272
    modules and no `__init__.py`, so `import abicheck` resolved to that
    partial copy as a namespace package — `abicheck.__version__` and
    `abicheck.compat` were absent and mutmut aborted at "failed to collect
    stats" before generating a single result. The editable install does not
    save it: its finder is appended to `sys.meta_path`, so the path finder
    reaches `mutants/` first. Reproduced locally and in CI.
    """
    for path in _source_paths():
        assert (REPO_ROOT / path).is_dir(), (
            f"[tool.mutmut].source_paths entry {path!r} is not a directory — "
            "mutmut would copy an incomplete package into mutants/"
        )
        assert (REPO_ROOT / path / "__init__.py").is_file(), (
            f"{path} has no __init__.py, so the copy under mutants/ would "
            "shadow the real package as a namespace package"
        )


def test_the_repository_files_tests_read_are_copied_into_mutants() -> None:
    """mutmut runs the suite from inside `mutants/`, so a test that reads a
    repository file resolves it relative to that copy.

    Without these, collection died with 37 `FileNotFoundError`s — and any one
    of them aborts the lane before a single mutant is measured. Pinned by
    directory rather than by the individual files, since the failure mode of a
    missing entry is a dead lane, not a wrong number.
    """
    also_copy = set(_mutmut_config().get("also_copy", []))
    required = {
        ".github",
        "agent-evals",
        "docs",
        "examples",
        # Not a directory: `test_ai_readiness.py` and `test_docs_hooks.py`
        # read this file directly, and it is the one repository *input* the
        # directory entries do not carry (Codex review).
        "repo_facts.json",
        # The test-selection configuration has to follow the suite into
        # `mutants/`, otherwise its slow nested mutmut fixture runs there.
        "pyproject.toml",
        "scripts",
        "skills-src",
        # `test_gen_agent_skills.py` compares the committed publication trees
        # against `skills-src/`, so copying only the source reports every
        # generated file as missing (Codex review).
        ".agents",
        ".claude",
        ".gemini",
    }
    missing = required - also_copy
    assert not missing, (
        f"tests read these repository paths but mutmut would not copy them: "
        f"{sorted(missing)}"
    )


def test_every_copied_path_that_exists_is_a_real_repository_path() -> None:
    """A typo in `also_copy` is silent — mutmut skips a path that does not
    exist — so the entry would simply never arrive under `mutants/`."""
    unknown = [
        p for p in _mutmut_config().get("also_copy", []) if not (REPO_ROOT / p).exists()
    ]
    assert not unknown, f"[tool.mutmut].also_copy names nonexistent paths: {unknown}"


def test_every_mutated_module_lives_under_a_copied_source_path() -> None:
    """`only_mutate` narrows what is mutated; it cannot reach outside what is
    copied, so a module named there but not under `source_paths` is mutated
    nowhere."""
    roots = tuple(f"{p.rstrip('/')}/" for p in _source_paths())
    outside = [p for p in _only_mutate() if not p.startswith(roots)]
    assert not outside, f"only_mutate names paths outside source_paths: {outside}"


def test_mutmut_config_uses_v3_key_names() -> None:
    """The v2 spellings do not merely warn — they break the run.

    `tests_dir` as a string makes mutmut 3.x abort with a TypeError before
    generating a mutant, and `runner` is not a recognised 3.x key at all, so
    the marker exclusion would be silently dropped and every mutant would
    re-run the integration and slow lanes. Both confirmed against 3.7.0.
    """
    with open(REPO_ROOT / "pyproject.toml", "rb") as fh:
        cfg = tomllib.load(fh)["tool"]["mutmut"]
    assert "source_paths" in cfg
    for dead_key in ("paths_to_mutate", "tests_dir", "runner"):
        assert dead_key not in cfg, (
            f"[tool.mutmut].{dead_key} is a mutmut 2.x key; 3.x either ignores "
            "it or aborts on it"
        )
    assert isinstance(cfg.get("pytest_add_cli_args_test_selection"), list)


def _ignored_test_files() -> list[str]:
    """Test files mutmut is told to skip.

    Both spellings, since they are the same decision at different
    granularities: `--ignore=<file>` drops a whole module, `--deselect=<file>
    ::<test>` drops one test from a module that otherwise runs. A check that
    only knew about `--ignore` would let a deselect escape every guarantee
    below.
    """
    files = []
    for arg in _mutmut_config()["pytest_add_cli_args"]:
        if arg.startswith("--ignore="):
            files.append(arg.split("=", 1)[1])
        elif arg.startswith("--deselect="):
            files.append(arg.split("=", 1)[1].split("::", 1)[0])
    return files


def _package_of(path: Path) -> str:
    """Dotted package a repository file lives in, e.g. `abicheck.buildsource`."""
    rel = path.resolve().relative_to(REPO_ROOT)
    return ".".join(rel.parts[:-1])


def _first_party_imports(path: Path) -> set[str]:
    """`abicheck.*` modules imported by *path*, relative imports resolved.

    Resolving them is the whole point: inside the package almost every import
    is relative (`from .diff_types import ...`), so a walk that only followed
    absolute ones reported that `abicheck/checker.py` reaches nothing — and
    the reachability check built on it would have been vacuous.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return set()
    package = _package_of(path)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            if not node.level:
                if node.module:
                    names.add(node.module)
                continue
            # `from . import x` / `from ..y import z`: climb `level - 1`
            # packages from this file's own package.
            parts = package.split(".") if package else []
            base = parts[: len(parts) - (node.level - 1)]
            if not base:
                continue
            prefix = ".".join(base)
            names.add(f"{prefix}.{node.module}" if node.module else prefix)
            if node.module is None:
                names |= {f"{prefix}.{a.name}" for a in node.names}
    return {n for n in names if n == "abicheck" or n.startswith("abicheck.")}


def _module_file(module: str) -> Path | None:
    direct = REPO_ROOT / (module.replace(".", "/") + ".py")
    if direct.is_file():
        return direct
    package = REPO_ROOT / module.replace(".", "/") / "__init__.py"
    return package if package.is_file() else None


def _reached_mutated_modules(path: Path, mutated: set[str]) -> frozenset[str]:
    """Every mutated module *path* can reach, directly or through its imports.

    All of them, not the first one found: the walk visits a set, so "the first
    mutated module reached" varied between runs and made the recorded cost
    below flap between two equally true answers.
    """
    seen: set[str] = set()
    found: set[str] = set()
    stack = list(_first_party_imports(path))
    while stack:
        module = stack.pop()
        if module in seen:
            continue
        seen.add(module)
        if module in mutated:
            found.add(module)
        target = _module_file(module)
        if target is not None:
            stack.extend(_first_party_imports(target))
    return frozenset(found)


#: Exclusions that are *not* free: the file cannot run under mutmut, and it
#: can also reach a mutated module, so skipping it loses whatever kills it
#: would have contributed and inflates that module's survivor count.
#:
#: Recorded rather than hidden, with the module each one costs. The inflation
#: is absorbed by the baseline (recorded under this same configuration), so
#: drift gating stays valid; what it can distort is the absolute --diff-scoped
#: gate, which may report a survivor that one of these tests would in fact
#: have killed.
_ACCEPTED_KILL_LOSS = {
    # Drives `actions/check-target/run.sh`, which re-enters the mutated tree
    # from a subprocess that has no mutmut config, so the import of any
    # mutated module raises there. Reaches checker_policy via
    # abicheck.aggregate.
    # Drives `actions/collect-facts`'s scripts the same way, and hits the same
    # subprocess re-entry.
    "tests/test_action_collect_facts.py": frozenset(
        {
            "abicheck.checker_policy",
            "abicheck.name_classification",
            "abicheck.policy.selectors",
            "abicheck.policy.selectors_namespace_glob",
        }
    ),
    "tests/test_action_check_target.py": frozenset(
        {
            "abicheck.checker_policy",
            "abicheck.finding_identity",
            "abicheck.name_classification",
            "abicheck.policy.selectors",
            "abicheck.policy.selectors_namespace_glob",
        }
    ),
    # Drives `action/run.sh`, which execs the real `abicheck` console script
    # from a scratch `cwd` -- the same subprocess re-entry as the two entries
    # above, just discovered later (G38 bundle-facts PR, the first to trigger
    # this lane against a serialization.py change with this test present).
    "tests/test_action_run_sh_annotate_renderer.py": frozenset(
        {
            "abicheck.checker_policy",
            "abicheck.diff_symbols",
            "abicheck.finding_identity",
            "abicheck.name_classification",
            "abicheck.policy.selectors",
            "abicheck.policy.selectors_namespace_glob",
            "abicheck.serialization",
            "abicheck.snapshot_io",
        }
    ),
    # Same subprocess re-entry as the entry immediately above, tripped by
    # this same G38 bundle-facts PR once it touched a second mutation-scoped
    # module (bundle_signature_evidence.py):
    # TestRealAbicheckWritesPersistedAnnotationsForADirectoryOperand shells
    # out to a real `abicheck compare` via `action/run.sh` from a scratch
    # `cwd` the identical way.
    "tests/test_action_run_sh_compare_pr_json_write.py": frozenset(
        {
            "abicheck.checker_policy",
            "abicheck.diff_symbols",
            "abicheck.finding_identity",
            "abicheck.name_classification",
            "abicheck.policy.selectors",
            "abicheck.policy.selectors_namespace_glob",
            "abicheck.serialization",
            "abicheck.snapshot_io",
        }
    ),
    # The same subprocess re-entry once more, and the first instance reached
    # through a *workflow* step rather than through `action/`: this file runs
    # check-project.yml's "Resolve candidate binary/binaries" body from a
    # scratch `cwd`, and that step's own child imports `abicheck`, resolved to
    # the mutant tree through the inherited PYTHONPATH. Reproduce without
    # mutmut in one line:
    #   PYTHONPATH=<mutants dir> python -m pytest \\
    #     tests/test_reusable_workflows_project_evidence.py -k resolver_selects
    "tests/test_reusable_workflows_project_evidence.py": frozenset(
        {
            "abicheck.checker_policy",
            "abicheck.diff_symbols",
            "abicheck.finding_identity",
            "abicheck.name_classification",
            "abicheck.policy.selectors",
            "abicheck.policy.selectors_namespace_glob",
            "abicheck.serialization",
            "abicheck.snapshot_io",
            "abicheck.suppression",
        }
    ),
}


def test_no_ignored_test_file_can_kill_a_detector_mutant() -> None:
    """An exclusion is free only if the file reaches no mutated module.

    These files are skipped because they cannot run from `mutants/` at all —
    they read the real repository's git history, generated skill trees and
    vendor adapters, or drive a subprocess that re-enters the mutated tree
    without mutmut's own config. With `-x`, any one of them ends the lane
    before a mutant is measured, so skipping them is not optional.

    Reachability is transitive, not direct: a test importing
    `abicheck.aggregate` can kill a `checker_policy` mutant it never names,
    and a direct-import check called exactly that exclusion free.
    """
    mutated_modules = {p.replace("/", ".").removesuffix(".py") for p in _only_mutate()}
    offenders = {}
    for rel in _ignored_test_files():
        path = REPO_ROOT / rel
        if not path.is_file():
            continue
        reached = _reached_mutated_modules(path, mutated_modules)
        if reached and _ACCEPTED_KILL_LOSS.get(rel) != reached:
            offenders[rel] = sorted(reached)
    assert not offenders, (
        f"ignored test file(s) reach mutated modules, so skipping them loses "
        f"real kills: {offenders}. Fix the exclusion, or record the cost in "
        "_ACCEPTED_KILL_LOSS with the reason it cannot run."
    )


def test_no_accepted_kill_loss_entry_has_gone_stale() -> None:
    """The acknowledgement list is only honest while every entry is still
    needed: a file that stopped being ignored, or stopped reaching the module
    it was recorded against, would otherwise sit there implying a cost that no
    longer exists."""
    mutated_modules = {p.replace("/", ".").removesuffix(".py") for p in _only_mutate()}
    ignored = set(_ignored_test_files())
    stale = {}
    for rel, module in _ACCEPTED_KILL_LOSS.items():
        if rel not in ignored:
            stale[rel] = "no longer ignored"
        elif _reached_mutated_modules(REPO_ROOT / rel, mutated_modules) != module:
            stale[rel] = f"no longer reaches exactly {sorted(module)}"
    assert not stale, f"stale _ACCEPTED_KILL_LOSS entries: {stale}"


def test_every_ignored_test_file_exists() -> None:
    """A stale `--ignore=`/`--deselect=` is silent: pytest accepts a path that
    is gone, so the entry would sit there implying a coverage hole that no
    longer exists — and the real file it was meant to skip would abort the
    lane."""
    missing = [rel for rel in _ignored_test_files() if not (REPO_ROOT / rel).is_file()]
    assert not missing, f"[tool.mutmut] skip list names missing files: {missing}"


def test_every_deselected_test_still_exists() -> None:
    """A deselect names a specific test, so it rots in a second way the file
    check cannot see: the file survives a rename of the test inside it, and
    pytest does not complain about a `--deselect` that matches nothing."""
    missing = []
    for arg in _mutmut_config()["pytest_add_cli_args"]:
        if not arg.startswith("--deselect="):
            continue
        rel, _, test_name = arg.split("=", 1)[1].partition("::")
        path = REPO_ROOT / rel
        if not path.is_file():
            missing.append(arg)
            continue
        source = path.read_text(encoding="utf-8")
        if f"def {test_name.split('::')[-1]}(" not in source:
            missing.append(arg)
    assert not missing, f"[tool.mutmut] --deselect names missing tests: {missing}"


def test_marker_exclusion_still_reaches_pytest() -> None:
    """The slow/integration lanes must stay out of every mutant's test run."""
    with open(REPO_ROOT / "pyproject.toml", "rb") as fh:
        args = tomllib.load(fh)["tool"]["mutmut"]["pytest_add_cli_args"]
    assert "-m" in args
    expr = args[args.index("-m") + 1]
    for marker in ("integration", "libabigail", "abicc", "slow"):
        assert f"not {marker}" in expr, f"marker {marker} no longer excluded"


def test_checkout_is_unshallow_for_the_diff_scoped_lane() -> None:
    """`--diff-scoped` needs the merge-base; a shallow clone reports every
    function as changed and the gate becomes noise."""
    steps = _workflow()["jobs"]["mutmut"]["steps"]
    checkout = next(
        s for s in steps if str(s.get("uses", "")).startswith("actions/checkout")
    )
    assert checkout.get("with", {}).get("fetch-depth") == 0


def test_real_mutmut_parser_output_is_saved_for_failures() -> None:
    """A failed real-tool probe must identify its failing test in CI artifacts."""
    steps = _workflow()["jobs"]["mutmut"]["steps"]
    probe = next(
        s
        for s in steps
        if s.get("name") == "Verify the parser against a real mutmut run"
    )
    assert "tee mutmut-real-parser-pytest.txt" in probe["run"]
    upload = next(s for s in steps if s.get("name") == "Upload mutmut results")
    assert "mutmut-real-parser-pytest.txt" in upload["with"]["path"]


def test_the_real_mutmut_parser_test_runs_in_this_lane() -> None:
    """This is the only CI lane with mutmut installed, and every pytest
    selector in the repo excludes `slow` — so without an explicit step the one
    test that validates the parser against mutmut's *actual* output runs
    nowhere, and the parser falls back to being checked only against
    hand-written fixtures (Codex review)."""
    steps = _workflow()["jobs"]["mutmut"]["steps"]
    step = next(
        (s for s in steps if "test_mutation_results.py" in str(s.get("run", ""))), None
    )
    assert step is not None, (
        "no step runs tests/test_mutation_results.py — the real-tool contract "
        "would never execute in CI"
    )
    assert "-m slow" in step["run"], "the real-run test carries the slow marker"
    assert step.get("env", {}).get("ABICHECK_MIN_EXECUTED") == "1", (
        "without conftest.py's silent-skip guard, a skipif that starts "
        "matching turns this step green with zero tests run"
    )


def test_that_step_precedes_the_mutation_run() -> None:
    """Cheap check first: no point spending the mutation budget when the
    parser that reads its output is already known to be wrong."""
    names = [s.get("name", "") for s in _workflow()["jobs"]["mutmut"]["steps"]]
    verify_idx = next(i for i, n in enumerate(names) if "parser against a real" in n)
    run_idx = next(i for i, n in enumerate(names) if "Run mutation testing" in n)
    assert verify_idx < run_idx


def test_mutmut_is_version_pinned() -> None:
    """An unpinned install is how this lane silently changed behaviour before."""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "mutmut>=3.7,<4" in text
