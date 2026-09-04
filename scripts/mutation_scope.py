#!/usr/bin/env python3
"""Narrow PR mutmut runs to detector modules implicated by the diff.

The scheduled and dispatch lanes deliberately retain the complete configured
scope.  On a pull request, `--diff-scoped` can only gate changed detector
modules, so generating mutants for unrelated modules only spends the job's
wall-clock budget and can prevent any measurement from completing.
"""

from __future__ import annotations

import argparse
import fnmatch
import subprocess
from pathlib import Path

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib

REPO_ROOT = Path(__file__).resolve().parent.parent
_CONFIG = REPO_ROOT / "pyproject.toml"

# These define how the lane selects, runs, or interprets mutation coverage.
# Their changes can affect every module, so a PR must retain the full scope.
MUTATION_INFRASTRUCTURE_PATHS = frozenset(
    {
        ".github/workflows/mutation.yml",
        "pyproject.toml",
        "scripts/check_mutation_score.py",
        "scripts/mutation_results.py",
        "scripts/mutation_scope.py",
        "tests/test_mutation_results.py",
        "tests/test_mutation_scope.py",
        "tests/test_mutation_workflow_contract.py",
    }
)


def _module_for_test_path(test_path: str, only_mutate: list[str]) -> str | None:
    """Return the single ``only_mutate`` module *test_path* pairs with, or
    ``None`` if it pairs with none.

    P2 review, fresh evidence: matching every module's ``tests/test_<stem>*.py``
    glob independently lets a shorter stem's pattern absorb a longer, unrelated
    module's own test file -- ``tests/test_selectors_namespace_glob.py``
    matches both ``tests/test_selectors*.py`` (module ``selectors.py``) and its
    own intended ``tests/test_selectors_namespace_glob*.py`` pattern (module
    ``selectors_namespace_glob.py``). Resolved by picking the LONGEST matching
    stem -- the most specific module -- as the exclusive pairing, so a test
    file is never attributed to more than one module at once.
    """
    candidates = [
        module
        for module in only_mutate
        if fnmatch.fnmatch(test_path, f"tests/test_{Path(module).stem}*.py")
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda m: len(Path(m).stem))


def selected_modules(
    changed_paths: set[str], only_mutate: list[str]
) -> list[str] | None:
    """Return a safe PR mutation subset, or ``None`` for the full scope.

    A changed detector module selects itself.  A conventional detector-test
    path selects its paired module.  Infrastructure-only and unclassified-test
    changes keep the full scope; when a detector module is also changed, its
    diff-scoped measurement remains the only bounded PR gate.
    """
    selected = {path for path in changed_paths if path in only_mutate}
    known_test_paths = set()
    for path in changed_paths:
        module = _module_for_test_path(path, only_mutate)
        if module is not None:
            known_test_paths.add(path)
            selected.add(module)

    changed_tests = {
        path
        for path in changed_paths
        if path.startswith("tests/") and path.endswith(".py")
    }
    # When the PR changes a detector module, `--diff-scoped` can only gate
    # that module's changed functions.  An unrelated test edit cannot enlarge
    # that function set, so retain the source-derived subset.  With no source
    # module selected, an unclassified test could weaken any module and needs
    # the full drift measurement.
    if changed_tests - known_test_paths and not selected:
        return None
    # A full configured run exceeds the CI job budget for mixed
    # infrastructure+detector changes.  The detector subset still measures
    # every changed production function; reserve complete coverage for
    # infrastructure-only changes and scheduled/label-forced runs.
    if changed_paths & MUTATION_INFRASTRUCTURE_PATHS and not selected:
        return None
    return sorted(selected) or None


def require_baseline_for_pr(
    changed_paths: set[str], only_mutate: list[str], *, labelled: bool
) -> bool:
    """Whether this PR run needs baseline drift, not just ``--diff-scoped``.

    ``--diff-scoped`` can only gate mutants in functions the diff actually
    changed in a MUTATED production module — so a change that weakens a
    detector test without touching that test's own paired production module
    passes ``--diff-scoped`` having gated nothing at all for that module. Two
    P2 review findings on the earlier bash-only ``mutation.yml`` heuristic
    (aggregate ``mutated``/``mutated_tests`` booleans over the WHOLE
    ``only_mutate`` + infrastructure path list) share one root cause: an
    aggregate boolean cannot answer a per-module question.

    1. A PR touching only a conventional detector test plus lane
       infrastructure (``pyproject.toml``, this script, the workflow file)
       matched BOTH the aggregate ``mutated`` filter (via the infrastructure
       path) and ``mutated_tests`` -- so the old ``MATCHED_TESTS && !MATCHED``
       condition read false and skipped the baseline requirement even though
       no production module the test pairs with had actually changed.
    2. A PR touching one mutated module (e.g. ``diff_types.py``) AND a
       conventional test for a DIFFERENT module (e.g.
       ``test_serialization_roundtrip.py``) matched the aggregate
       ``mutated`` filter via the FIRST module -- so the same condition read
       false even though ``serialization.py`` itself never changed and
       ``--diff-scoped`` could not gate a weakened assertion there.

    Fixed by resolving the question per module, directly from the real
    changed-path set, rather than from any aggregate path-filter boolean:
    for every ``only_mutate`` module whose OWN test glob
    (``tests/test_<stem>*.py``) is touched, baseline drift is required
    unless that SAME module is also in *changed_paths*. Infrastructure
    paths are irrelevant to this check entirely (they say nothing about
    which module's tests were touched), which is what makes finding 1
    impossible by construction; checking each module independently rather
    than folding into one aggregate boolean is what makes finding 2
    impossible.

    P2 review, fresh evidence (finding 3): the per-module glob check above
    still let a shorter module stem's pattern also match an unrelated
    longer-stemmed module's own test file (see ``_module_for_test_path``'s
    docstring for the exact overlap) -- a changed ``tests/test_selectors_
    namespace_glob.py`` alone was treated as touching ``selectors.py``'s
    test glob too, requiring baseline drift for a module the PR never
    actually risked weakening coverage for. Fixed by routing through the
    same longest-stem-wins ``_module_for_test_path`` pairing
    ``selected_modules`` uses, so a test path is attributed to at most one
    module.

    P2 review, fresh evidence (finding 4): the "unless that SAME module is
    also in changed_paths" exemption above was itself unsound and has been
    REMOVED. ``--diff-scoped`` gates mutants only in the specific
    *functions* the diff touched in a mutated module, not the whole
    module -- so a PR that changes ``diff_types.py``'s ``foo()`` and, in
    the same commit, weakens ``tests/test_diff_types.py``'s existing
    assertion for an unrelated, UNCHANGED ``bar()`` previously read as
    "the module also changed, diff-scoped has this covered" and skipped
    baseline drift entirely -- while diff-scoped's own function-level scope
    never touches ``bar()`` at all, so nothing gated that weakening.
    Correlating a test edit to the exact production function(s) it
    exercises (so the exemption could apply only when they're proven to be
    among the diff's own changed functions) would need a real test-to-
    production call-graph mapping this repository has no existing
    machinery for and that a heuristic could get wrong in either direction
    -- rather than ship an unverified approximation, this drops the
    exemption outright: baseline drift is required whenever an
    ``only_mutate`` module's own test file is touched at all, full stop.
    Diff-scoped still runs and still gates the real, function-level
    survivors; this only removes the (unsound) shortcut around the
    complementary drift check.

    A ``mutation`` label always requires baseline drift regardless of the
    diff -- it is documented as the complete check, and a label-forced run
    on a diff this function would otherwise clear must not silently report
    "gated nothing" and exit 0.
    """
    if labelled:
        return True
    touched_modules = {
        _module_for_test_path(p, only_mutate)
        for p in changed_paths
        if p.startswith("tests/") and p.endswith(".py")
    }
    touched_modules.discard(None)
    return bool(touched_modules)


def rewrite_only_mutate(config_path: Path, modules: list[str]) -> None:
    """Replace only the TOML array, preserving the repository's comments."""
    text = config_path.read_text(encoding="utf-8")
    start = text.index("only_mutate = [")
    end = text.index("]\n", start) + 2
    rendered = (
        "only_mutate = [\n"
        + "".join(f'    "{module}",\n' for module in modules)
        + "]\n"
    )
    config_path.write_text(text[:start] + rendered + text[end:], encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-ref",
        required=True,
        help=(
            "Git revision this PR's mutation scope is diffed against, "
            "resolved as 'git diff --name-only <BASE_REF>...HEAD' (three-dot: "
            "changes on HEAD since the merge-base with <BASE_REF>, not a "
            "literal two-sided diff). Any revision 'git diff' accepts: a "
            "branch name (e.g. 'origin/main'), a tag, or a full/short commit "
            "SHA. Must be resolvable in the local checkout this script runs "
            "in (fetch it first if it isn't already present)."
        ),
    )
    parser.add_argument(
        "--print-require-baseline",
        action="store_true",
        help=(
            "Instead of rewriting only_mutate, print "
            "'require_baseline=true'/'require_baseline=false' (GITHUB_OUTPUT "
            "format) answering require_baseline_for_pr() for this diff, and "
            "exit without touching pyproject.toml. Pairs with --labelled."
        ),
    )
    parser.add_argument(
        "--labelled",
        action="store_true",
        help=(
            "Only meaningful with --print-require-baseline: the PR carries "
            "the 'mutation' label, which always requires baseline drift "
            "regardless of the diff."
        ),
    )
    args = parser.parse_args()

    config = tomllib.loads(_CONFIG.read_text(encoding="utf-8"))["tool"]["mutmut"]
    only_mutate = config["only_mutate"]
    result = subprocess.run(
        ["git", "diff", "--name-only", f"{args.base_ref}...HEAD"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    changed_paths = set(result.stdout.splitlines())

    if args.print_require_baseline:
        answer = require_baseline_for_pr(
            changed_paths, only_mutate, labelled=args.labelled
        )
        print(f"require_baseline={'true' if answer else 'false'}")
        return 0

    modules = selected_modules(changed_paths, only_mutate)
    if modules is None:
        print("mutation scope: full configured scope")
    else:
        rewrite_only_mutate(_CONFIG, modules)
        print("mutation scope: " + ", ".join(modules))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
