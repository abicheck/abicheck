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


def test_matching_module_and_its_own_test_needs_no_baseline() -> None:
    """A source change plus its OWN paired regression test is fully covered
    by --diff-scoped; no drift reference is needed."""
    changed = {"abicheck/diff_types.py", "tests/test_diff_types.py"}
    assert scope.require_baseline_for_pr(changed, MODULES, labelled=False) is False


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


def test_every_module_paired_with_its_own_test_needs_no_baseline() -> None:
    """Property: for EVERY module in only_mutate, changing it together with
    its own test never requires baseline drift."""
    for module in MODULES:
        stem = Path(module).stem
        changed = {module, f"tests/test_{stem}_extra.py"}
        assert (
            scope.require_baseline_for_pr(changed, MODULES, labelled=False) is False
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


def test_rewrite_only_mutate_preserves_following_config(tmp_path: Path) -> None:
    config = tmp_path / "pyproject.toml"
    config.write_text('before = 1\nonly_mutate = [\n    "a.py",\n]\nafter = 2\n')
    scope.rewrite_only_mutate(config, ["b.py"])
    assert (
        config.read_text() == 'before = 1\nonly_mutate = [\n    "b.py",\n]\nafter = 2\n'
    )
