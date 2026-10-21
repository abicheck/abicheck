"""The stored-bundle-facts ``compare`` orchestration, driven on the workflow
interface with stubbed collaborators and no Click context.

``test_bundle_facts_stored_request.py`` pins the request rules. These pin what
:func:`execute_bundle_facts_compare` does once a request is accepted: the
no-match and no-pair outcomes, policy-merge errors and warnings, the
stored/live manifest path, extraction failures, and the ``--output-dir``
preflight.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import abicheck.bundle_side_input as side_input
import abicheck.workflows.bundle_stored_pair_compare as stored_pair
from abicheck.errors import PolicyError, SnapshotError
from abicheck.workflows import bundle_facts_stored_compare as mod
from abicheck.workflows.bundle_facts_library_overrides import (
    BundleFactsLibraryOverridesError,
)
from abicheck.workflows.bundle_facts_stored_request import (
    BundleFactsCompareError,
    BundleFactsCompareRequest,
)


def _result(per_library: list[Any] | None = None, scope_record: Any = None) -> Any:
    return SimpleNamespace(
        per_library=per_library or [],
        scope_record=scope_record,
    )


@pytest.fixture(autouse=True)
def _stub_decisions(monkeypatch: pytest.MonkeyPatch) -> None:
    """Scope and assurance are policy's; stub them so these tests stay on the
    orchestration. The real policy answers are pinned in their own suites."""
    monkeypatch.setattr(
        mod, "resolve_scope_decision", lambda record, policy: ("scope", record)
    )
    monkeypatch.setattr(
        mod,
        "release_assurance_from_results",
        lambda results, *, require_complete: ("assurance", require_complete),
    )


def _request(tmp_path: Path, **overrides: Any) -> BundleFactsCompareRequest:
    new_input = tmp_path / "new"
    new_input.mkdir(exist_ok=True)
    fields: dict[str, Any] = {
        "old_facts_path": tmp_path / "old.bundlefacts.json",
        "new_input": new_input,
    }
    fields.update(overrides)
    return BundleFactsCompareRequest(**fields)


def _stored_pair(monkeypatch: pytest.MonkeyPatch, result: Any) -> None:
    monkeypatch.setattr(
        stored_pair, "compare_stored_bundle_facts_pair", lambda *a, **k: result
    )


# -- stored/stored: outcomes ------------------------------------------------


def test_a_compared_pair_returns_the_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _result(scope_record="record")
    _stored_pair(monkeypatch, result)
    outcome = mod.execute_bundle_facts_compare(_request(tmp_path, new_is_stored=True))
    assert outcome.result is result
    assert outcome.scope_decision == ("scope", "record")
    assert outcome.assurance_decision == ("assurance", False)
    assert outcome.require_complete_analysis is False


def test_no_match_and_no_record_is_an_operational_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stored_pair(monkeypatch, _result())
    with pytest.raises(
        BundleFactsCompareError, match="No library in .* matched any library"
    ) as exc:
        mod.execute_bundle_facts_compare(_request(tmp_path, new_is_stored=True))
    assert exc.value.usage is False
    assert "stored per_library_snapshots" in str(exc.value)


def test_no_match_on_a_live_new_input_names_the_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stored/live: the no-match message names NEW_INPUT itself, not a stored
    record, since NEW_INPUT was never a stored document."""
    monkeypatch.setattr(
        side_input,
        "compare_release_against_bundle_facts",
        lambda *a, **k: _result(),
    )
    monkeypatch.setattr(mod, "extract_if_package", _extract_plain)
    request = _request(tmp_path)
    with pytest.raises(BundleFactsCompareError, match="No library in ") as exc:
        mod.execute_bundle_facts_compare(request)
    assert f"No library in {request.new_input} matched" in str(exc.value)


def test_zero_pairs_with_a_record_warns_outside_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A zero-pair run that carries a scope record is not an error: the
    completeness axis reports it. Non-JSON formats get a warning."""
    _stored_pair(monkeypatch, _result(scope_record="record"))
    warnings: list[str] = []
    outcome = mod.execute_bundle_facts_compare(
        _request(tmp_path, new_is_stored=True, fmt="markdown", warn=warnings.append)
    )
    assert outcome.result.per_library == []
    assert warnings == ["no library pair was compared -- no comparison completed."]


def test_zero_pairs_in_json_does_not_warn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stored_pair(monkeypatch, _result(scope_record="record"))
    warnings: list[str] = []
    mod.execute_bundle_facts_compare(
        _request(tmp_path, new_is_stored=True, fmt="json", warn=warnings.append)
    )
    assert warnings == []


def test_stored_pair_failures_become_operational_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _raise(*_a: Any, **_k: Any) -> Any:
        raise SnapshotError("container node budget exceeded")

    monkeypatch.setattr(stored_pair, "compare_stored_bundle_facts_pair", _raise)
    with pytest.raises(BundleFactsCompareError, match="budget exceeded") as exc:
        mod.execute_bundle_facts_compare(_request(tmp_path, new_is_stored=True))
    assert exc.value.usage is False


# -- policy merge -----------------------------------------------------------


def test_a_policy_error_from_the_project_config_is_a_usage_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _raise(*_a: Any, **_k: Any) -> Any:
        raise PolicyError("policy override conflicts")

    monkeypatch.setattr(mod, "merge_project_config_policy_overrides", _raise)
    with pytest.raises(BundleFactsCompareError, match="conflicts") as exc:
        mod.execute_bundle_facts_compare(_request(tmp_path, new_is_stored=True))
    assert exc.value.usage is True
    assert exc.value.param_hint == "--config"


def test_policy_downgrade_warnings_reach_the_caller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        mod, "project_config_policy_downgrade_warnings", lambda *a, **k: ["w1", "w2"]
    )
    _stored_pair(monkeypatch, _result(scope_record="record"))
    warnings: list[str] = []
    mod.execute_bundle_facts_compare(
        _request(tmp_path, new_is_stored=True, warn=warnings.append)
    )
    assert warnings == ["w1", "w2"]


def test_the_loaded_suppression_and_policy_are_passed_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def _capture(*_a: Any, **kwargs: Any) -> Any:
        captured.update(kwargs)
        return _result(scope_record="record")

    monkeypatch.setattr(stored_pair, "compare_stored_bundle_facts_pair", _capture)
    suppression, policy_file = object(), object()
    # The policy merge and downgrade check need a real PolicyFile; this test
    # only pins the pass-through, so both are stubbed to identity.
    monkeypatch.setattr(
        mod, "merge_project_config_policy_overrides", lambda pf, **_k: pf
    )
    monkeypatch.setattr(
        mod, "project_config_policy_downgrade_warnings", lambda *a, **k: []
    )
    mod.execute_bundle_facts_compare(
        _request(
            tmp_path,
            new_is_stored=True,
            load_suppression_and_policy=lambda: (suppression, policy_file),
        )
    )
    assert captured["suppress"] is suppression
    assert captured["policy_file"] is policy_file


# -- stored/live: extraction, overrides, and the depth clear ---------------


def _extract_plain(
    input_path: Path,
    _debug: Any,
    _devel: Any,
    _make_temp_dir: Any,
    _is_package: Any,
    _detect: Any,
) -> tuple[Path, None, Path | None, None, bool]:
    return input_path, None, None, None, True


def _extract_with_headers(header_dir: Path) -> Any:
    def _extract(
        input_path: Path,
        _debug: Any,
        _devel: Any,
        _make_temp_dir: Any,
        _is_package: Any,
        _detect: Any,
    ) -> tuple[Path, None, Path, None, bool]:
        return input_path, None, header_dir, None, False

    return _extract


def test_a_live_new_input_is_compared_with_its_package_headers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    header_dir = tmp_path / "devel"
    header_dir.mkdir()
    captured: dict[str, Any] = {}

    def _capture(*_a: Any, **kwargs: Any) -> Any:
        captured.update(kwargs)
        return _result(scope_record="record")

    monkeypatch.setattr(mod, "extract_if_package", _extract_with_headers(header_dir))
    monkeypatch.setattr(side_input, "compare_release_against_bundle_facts", _capture)
    mod.execute_bundle_facts_compare(_request(tmp_path))
    # The package's own header dir becomes the header root when none was given.
    assert captured["headers"] == [header_dir]


def test_depth_binary_clears_headers_and_manifest_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    header_dir = tmp_path / "devel"
    header_dir.mkdir()
    captured: dict[str, Any] = {}

    def _capture(*_a: Any, **kwargs: Any) -> Any:
        captured.update(kwargs)
        return _result(scope_record="record")

    monkeypatch.setattr(mod, "extract_if_package", _extract_with_headers(header_dir))
    monkeypatch.setattr(
        mod, "known_libraries_for_new_side", lambda *a, **k: ["libx.so"]
    )
    monkeypatch.setattr(
        mod,
        "load_bundle_facts_library_overrides",
        lambda *a, **k: SimpleNamespace(
            headers={"libx.so": [header_dir]}, includes={}, compile={"x": 1}
        ),
    )
    monkeypatch.setattr(side_input, "compare_release_against_bundle_facts", _capture)
    manifest = tmp_path / "manifest.yml"
    mod.execute_bundle_facts_compare(
        _request(tmp_path, depth="binary", bundle_facts_library_manifest=manifest)
    )
    assert captured["headers"] is None
    assert captured["per_library_headers"] == {}
    assert captured["per_library_includes"] == {}
    assert captured["per_library_compile"] == {}


def test_a_manifest_naming_no_library_is_a_usage_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _raise(*_a: Any, **_k: Any) -> Any:
        raise BundleFactsLibraryOverridesError("unknown library 'libnope.so'")

    monkeypatch.setattr(mod, "extract_if_package", _extract_plain)
    monkeypatch.setattr(mod, "known_libraries_for_new_side", lambda *a, **k: [])
    monkeypatch.setattr(mod, "load_bundle_facts_library_overrides", _raise)
    with pytest.raises(BundleFactsCompareError, match="unknown library") as exc:
        mod.execute_bundle_facts_compare(
            _request(tmp_path, bundle_facts_library_manifest=tmp_path / "m.yml")
        )
    assert exc.value.usage is True


def test_an_unextractable_new_input_is_an_operational_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _raise(*_a: Any, **_k: Any) -> Any:
        raise SnapshotError("archive is corrupt")

    monkeypatch.setattr(mod, "extract_if_package", _raise)
    with pytest.raises(BundleFactsCompareError, match="archive is corrupt") as exc:
        mod.execute_bundle_facts_compare(_request(tmp_path))
    assert exc.value.usage is False


# -- --output-dir preflight -----------------------------------------------


def _pair_with_one_library(monkeypatch: pytest.MonkeyPatch, library: str) -> None:
    _stored_pair(
        monkeypatch,
        _result(
            per_library=[SimpleNamespace(library=library)],
            scope_record="record",
        ),
    )


def test_output_dir_is_created_before_the_outcome_returns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pair_with_one_library(monkeypatch, "libx.so")
    out_dir = tmp_path / "per-library"
    mod.execute_bundle_facts_compare(
        _request(tmp_path, new_is_stored=True, output_dir=out_dir)
    )
    assert out_dir.is_dir()


def test_output_dir_that_is_a_file_is_a_usage_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pair_with_one_library(monkeypatch, "libx.so")
    clash = tmp_path / "clash"
    clash.write_text("not a directory")
    with pytest.raises(BundleFactsCompareError, match="already exists and") as exc:
        mod.execute_bundle_facts_compare(
            _request(tmp_path, new_is_stored=True, output_dir=clash)
        )
    assert exc.value.usage is True


def test_output_dir_named_by_another_output_is_a_usage_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pair_with_one_library(monkeypatch, "libx.so")
    out_dir = tmp_path / "reports"
    with pytest.raises(BundleFactsCompareError, match="also named by") as exc:
        mod.execute_bundle_facts_compare(
            _request(tmp_path, new_is_stored=True, output_dir=out_dir, output=out_dir)
        )
    assert exc.value.usage is True


def test_a_per_library_report_colliding_with_another_output_is_a_usage_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pair_with_one_library(monkeypatch, "libx.so")
    out_dir = tmp_path / "reports"
    collision = out_dir / "libx.so.json"
    with pytest.raises(BundleFactsCompareError, match="collides with") as exc:
        mod.execute_bundle_facts_compare(
            _request(
                tmp_path,
                new_is_stored=True,
                output_dir=out_dir,
                output=collision,
            )
        )
    assert exc.value.usage is True


def test_an_uncreatable_output_dir_is_an_operational_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A parent path component that is a regular file cannot hold a directory."""
    _pair_with_one_library(monkeypatch, "libx.so")
    parent_file = tmp_path / "parent"
    parent_file.write_text("a file")
    with pytest.raises(BundleFactsCompareError, match="Cannot create") as exc:
        mod.execute_bundle_facts_compare(
            _request(
                tmp_path,
                new_is_stored=True,
                output_dir=parent_file / "child",
            )
        )
    assert exc.value.usage is False
