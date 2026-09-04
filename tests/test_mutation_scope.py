from __future__ import annotations

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "mutation_scope", Path(__file__).parent.parent / "scripts" / "mutation_scope.py"
)
assert _SPEC and _SPEC.loader
scope = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(scope)

MODULES = ["abicheck/diff_types.py", "abicheck/serialization.py"]


def test_source_changes_select_only_changed_detector_modules() -> None:
    assert scope.selected_modules(
        {"abicheck/diff_types.py", "docs/guide.md"}, MODULES
    ) == ["abicheck/diff_types.py"]


def test_conventional_detector_test_selects_its_module() -> None:
    assert scope.selected_modules(
        {"tests/test_serialization_roundtrip.py"}, MODULES
    ) == ["abicheck/serialization.py"]


def test_unclassified_test_keeps_full_scope_without_detector_source() -> None:
    assert (
        scope.selected_modules({"tests/test_integration_helpers.py"}, MODULES) is None
    )


def test_mutation_infrastructure_with_detector_change_keeps_detector_subset() -> None:
    assert scope.selected_modules(
        {"abicheck/diff_types.py", "scripts/mutation_scope.py"}, MODULES
    ) == ["abicheck/diff_types.py"]


def test_mutation_infrastructure_only_keeps_full_scope() -> None:
    assert scope.selected_modules({"scripts/mutation_scope.py"}, MODULES) is None


def test_source_subset_survives_an_unclassified_test_change() -> None:
    assert scope.selected_modules(
        {"abicheck/diff_types.py", "tests/test_integration_helpers.py"}, MODULES
    ) == ["abicheck/diff_types.py"]


def test_malicious_changed_filename_is_data_not_a_selected_module() -> None:
    malicious = "tests/test_serialization.py;touch /tmp/should-not-run"
    assert scope.selected_modules({malicious}, MODULES) is None


def test_no_detector_change_keeps_full_scope() -> None:
    assert scope.selected_modules({"docs/guide.md"}, MODULES) is None


# ---------------------------------------------------------------------------
# require_baseline_for_pr — per-module correlation, not aggregate booleans
# ---------------------------------------------------------------------------

INFRA = {".github/workflows/mutation.yml", "pyproject.toml"}


def test_matching_module_and_its_own_test_still_needs_baseline() -> None:
    """P2 review, fresh evidence (finding 4): a source change plus its OWN
    paired regression test is NOT fully covered by --diff-scoped -- that
    gate only checks mutants in the specific function(s) the diff changed,
    not the whole module, so a test edit for a different, unchanged
    function in the same file would escape ungated under the previous
    exemption. Baseline drift is required whenever the module's own test
    file is touched at all, regardless of whether the module itself also
    changed."""
    changed = {"abicheck/diff_types.py", "tests/test_diff_types.py"}
    assert scope.require_baseline_for_pr(changed, MODULES, labelled=False) is True


def test_test_only_change_needs_baseline() -> None:
    changed = {"tests/test_diff_types.py"}
    assert scope.require_baseline_for_pr(changed, MODULES, labelled=False) is True


def test_infra_plus_test_only_change_still_needs_baseline() -> None:
    """P1 review finding 1: a PR touching lane infrastructure (which used to
    make the aggregate MATCHED boolean true) plus a conventional detector
    test must still require baseline drift -- infrastructure changing says
    nothing about whether the test's OWN paired production module changed."""
    changed = {"tests/test_diff_types.py"} | INFRA
    assert scope.require_baseline_for_pr(changed, MODULES, labelled=False) is True


def test_different_module_changed_still_needs_baseline_for_the_untouched_one() -> None:
    """P1 review finding 2: a PR touching one mutated module (diff_types.py)
    plus a conventional test for a DIFFERENT mutated module
    (serialization.py) must require baseline drift for the untouched
    module's own test -- the aggregate MATCHED boolean going true for
    diff_types.py must not paper over serialization.py's own gap."""
    changed = {"abicheck/diff_types.py", "tests/test_serialization_roundtrip.py"}
    assert scope.require_baseline_for_pr(changed, MODULES, labelled=False) is True


def test_infra_only_change_needs_no_baseline() -> None:
    """No detector test touched at all -- --diff-scoped has nothing to gate,
    but there's also no weakened test to miss."""
    assert scope.require_baseline_for_pr(INFRA, MODULES, labelled=False) is False


def test_label_always_requires_baseline_even_with_a_fully_paired_diff() -> None:
    changed = {"abicheck/diff_types.py", "tests/test_diff_types.py"}
    assert scope.require_baseline_for_pr(changed, MODULES, labelled=True) is True


def test_no_changes_at_all_needs_no_baseline() -> None:
    assert scope.require_baseline_for_pr(set(), MODULES, labelled=False) is False


def test_every_module_paired_with_its_own_test_still_needs_baseline() -> None:
    """Property: for EVERY module in only_mutate, changing it together with
    its own test still requires baseline drift (P2 review, fresh evidence,
    finding 4 -- see test_matching_module_and_its_own_test_still_needs_
    baseline above for why the module-also-changed exemption was unsound
    and removed)."""
    for module in MODULES:
        stem = Path(module).stem
        changed = {module, f"tests/test_{stem}_extra.py"}
        assert (
            scope.require_baseline_for_pr(changed, MODULES, labelled=False) is True
        ), module


def test_every_module_test_alone_needs_baseline() -> None:
    """Property: for EVERY module in only_mutate, touching only its test
    (module itself unchanged) always requires baseline drift."""
    for module in MODULES:
        stem = Path(module).stem
        changed = {f"tests/test_{stem}.py"}
        assert (
            scope.require_baseline_for_pr(changed, MODULES, labelled=False) is True
        ), module


# ---------------------------------------------------------------------------
# _module_for_test_path / non-overlapping stem matching (P2 review)
# ---------------------------------------------------------------------------

OVERLAPPING_MODULES = [
    "abicheck/policy/selectors.py",
    "abicheck/policy/selectors_namespace_glob.py",
]


def test_longer_stem_test_pairs_with_its_own_longer_stem_module() -> None:
    """The real overlap the review found: tests/test_selectors_namespace_
    glob.py matches BOTH tests/test_selectors*.py (selectors.py) and its own
    intended tests/test_selectors_namespace_glob*.py pattern. The longer,
    more specific stem must win."""
    assert (
        scope._module_for_test_path(
            "tests/test_selectors_namespace_glob.py", OVERLAPPING_MODULES
        )
        == "abicheck/policy/selectors_namespace_glob.py"
    )


def test_shorter_stem_test_still_pairs_with_its_own_module() -> None:
    assert (
        scope._module_for_test_path("tests/test_selectors.py", OVERLAPPING_MODULES)
        == "abicheck/policy/selectors.py"
    )


def test_unrelated_test_pairs_with_neither_overlapping_module() -> None:
    assert (
        scope._module_for_test_path("tests/test_unrelated.py", OVERLAPPING_MODULES)
        is None
    )


def test_overlap_does_not_select_the_wrong_module_for_pr_scoping() -> None:
    """selected_modules must attribute the changed test to ONLY its own
    longer-stemmed module, never also to the shorter-stemmed one."""
    changed = {"tests/test_selectors_namespace_glob.py"}
    assert scope.selected_modules(changed, OVERLAPPING_MODULES) == [
        "abicheck/policy/selectors_namespace_glob.py"
    ]


def test_overlap_pairing_does_not_also_flag_the_untouched_shorter_module() -> None:
    """P2 review, fresh evidence: a PR changing selectors_namespace_glob.py
    together with its own test must not ALSO be attributed to
    selectors.py's own (unrelated) test glob -- the overlap resolution
    (longest-stem-wins) still applies before this check ever runs.
    Baseline drift IS still required here (finding 4: the module-also-
    changed exemption was unsound and removed), but for the right reason
    -- the touched test's own paired module, not a false positive from the
    shorter-stemmed sibling."""
    changed = {
        "abicheck/policy/selectors_namespace_glob.py",
        "tests/test_selectors_namespace_glob.py",
    }
    assert (
        scope.require_baseline_for_pr(changed, OVERLAPPING_MODULES, labelled=False)
        is True
    )


def test_overlap_still_requires_baseline_when_the_longer_module_is_untouched() -> None:
    changed = {"tests/test_selectors_namespace_glob.py"}
    assert (
        scope.require_baseline_for_pr(changed, OVERLAPPING_MODULES, labelled=False)
        is True
    )


def test_rewrite_only_mutate_preserves_following_config(tmp_path: Path) -> None:
    config = tmp_path / "pyproject.toml"
    config.write_text('before = 1\nonly_mutate = [\n    "a.py",\n]\nafter = 2\n')
    scope.rewrite_only_mutate(config, ["b.py"])
    assert (
        config.read_text() == 'before = 1\nonly_mutate = [\n    "b.py",\n]\nafter = 2\n'
    )
