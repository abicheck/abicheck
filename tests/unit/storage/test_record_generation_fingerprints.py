"""The fingerprint record and its re-record CLI (ADR-062 D2).

Bug class: the re-record script is the only sanctioned way to change the
recorded fingerprints, so a refusal that does not refuse (a bump that skips
the constant, a no-semantic-change that hides a generation change) lets a
semantic change pass the gate with a clean-looking record. The oracle is the
refusal the script's own contract states, written out here, plus a literal
record shape rather than the implementation's helpers.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

from abicheck.storage import generation_scope as scope, versioning


def _script() -> ModuleType:
    path = scope.REPO_ROOT / "scripts" / "record_generation_fingerprints.py"
    spec = importlib.util.spec_from_file_location(
        "record_generation_fingerprints", path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def record_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "generation_fingerprints.json"
    monkeypatch.setattr(scope, "RECORD_PATH", path)
    return path


def test_load_record_is_empty_before_the_first_recording(record_path: Path) -> None:
    assert not record_path.exists()
    assert scope.load_record() == {}


def test_load_record_ignores_a_record_that_is_not_an_object(record_path: Path) -> None:
    record_path.write_text("[1, 2, 3]", encoding="utf-8")
    assert scope.load_record() == {}


def test_write_record_round_trips_in_the_stable_form(record_path: Path) -> None:
    data = {"resolver": {"generation": 2, "files": {"a.py": "x"}}, "extractor": {}}
    scope.write_record(data)
    assert record_path.read_text(encoding="utf-8") == (
        json.dumps(
            {"extractor": {}, "resolver": {"files": {"a.py": "x"}, "generation": 2}},
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    assert scope.load_record() == data


def test_scope_files_refuses_an_unknown_axis() -> None:
    with pytest.raises(ValueError, match="unknown generation axis"):
        scope.scope_files("nonsense")


def test_file_digests_records_a_missing_file_rather_than_dropping_it() -> None:
    digests = scope.file_digests(["abicheck/does_not_exist_anywhere.py"])
    assert digests == {"abicheck/does_not_exist_anywhere.py": "MISSING"}


@pytest.mark.parametrize("axis", ["extractor", "resolver"])
def test_cli_refuses_a_bump_that_is_not_exactly_one_above_the_record(
    record_path: Path, monkeypatch: pytest.MonkeyPatch, axis: str
) -> None:
    script = _script()
    scope.write_record({axis: {"generation": 1, "files": {}, "fingerprint": "x"}})
    monkeypatch.setattr(versioning, f"{axis.upper()}_GENERATION", 3)
    assert script.main(["--axis", axis, "--bump"]) == 2
    # The record was not touched by the refused bump.
    assert scope.load_record()[axis]["generation"] == 1


def test_cli_refuses_no_semantic_change_without_a_reason(record_path: Path) -> None:
    assert _script().main(["--axis", "resolver", "--no-semantic-change"]) == 2
    assert not record_path.exists()


def test_cli_refuses_no_semantic_change_when_the_constant_moved(
    record_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scope.write_record({"resolver": {"generation": 1, "files": {}, "fingerprint": "x"}})
    monkeypatch.setattr(versioning, "RESOLVER_GENERATION", 2)
    code = _script().main(
        ["--axis", "resolver", "--no-semantic-change", "--reason", "refactor"]
    )
    assert code == 2
    assert scope.load_record()["resolver"]["generation"] == 1


def test_cli_accepts_a_bump_one_above_the_record(
    record_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scope.write_record({"resolver": {"generation": 1, "files": {}, "fingerprint": "x"}})
    monkeypatch.setattr(versioning, "RESOLVER_GENERATION", 2)
    assert _script().main(["--axis", "resolver", "--bump"]) == 0
    entry = scope.load_record()["resolver"]
    assert entry["generation"] == 2
    assert entry["fingerprint"] == scope.fingerprint(entry["files"])
    assert entry["reason"] == "bumped to 2 for a semantic change"


def test_cli_records_the_initial_recording_without_a_prior_record(
    record_path: Path,
) -> None:
    code = _script().main(
        ["--axis", "extractor", "--no-semantic-change", "--reason", "initial"]
    )
    assert code == 0
    entry = scope.load_record()["extractor"]
    assert entry["generation"] == versioning.EXTRACTOR_GENERATION
    assert entry["reason"] == "initial"
