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


def test_rewrite_only_mutate_preserves_following_config(tmp_path: Path) -> None:
    config = tmp_path / "pyproject.toml"
    config.write_text('before = 1\nonly_mutate = [\n    "a.py",\n]\nafter = 2\n')
    scope.rewrite_only_mutate(config, ["b.py"])
    assert (
        config.read_text() == 'before = 1\nonly_mutate = [\n    "b.py",\n]\nafter = 2\n'
    )
