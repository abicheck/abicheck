"""ADR-061 gap F / definition-of-done item 16: the disposition mechanism.

Split out of ``test_architecture_check.py`` once that file crossed the
1,200-line test-file cap (a mechanical extraction, not a redesign) -- these
tests cover a distinct contract axis from the rest of that file: every
unclassified root module and every ``debt.yaml`` no-growth entry must carry
one of ADR-061's recorded dispositions (``migrate``/``retain``/``accept`` for
modules; ``migrate``/``accept`` for debt entries), never "unclassified for
now".
"""

from __future__ import annotations

import json
from pathlib import Path

from scripts.check_architecture import check_repository
from tests.test_architecture_check import _add_package, _rules, _tree, _write


def test_debt_entry_missing_disposition_fails_schema(tmp_path: Path) -> None:
    """ADR-061 gap F: every no-growth debt entry must state whether it is
    still tracked migration work or a reviewed, permanent exception."""
    root = _tree(tmp_path)
    _write(root / "abicheck/legacy.py", "VALUE = 1\n" * 10)
    debt = {
        "schema_version": 1,
        "files": [
            {
                "path": "abicheck/legacy.py",
                "baseline_lines": 9,
                "target": "model",
                "rule": "no_growth",
                "category": "legacy_monolith",
                "owner": "maintainers",
                "rationale": "Move it as a tested vertical slice.",
                "review_by": "2026-11-30",
            }
        ],
    }
    _write(root / "architecture/debt.yaml", json.dumps(debt))

    assert "schema" in _rules(root)


def test_debt_entry_invalid_disposition_fails_schema(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    _write(root / "abicheck/legacy.py", "VALUE = 1\n" * 10)
    debt = {
        "schema_version": 1,
        "files": [
            {
                "path": "abicheck/legacy.py",
                "baseline_lines": 9,
                "target": "model",
                "rule": "no_growth",
                "disposition": "unclassified",
                "category": "legacy_monolith",
                "owner": "maintainers",
                "rationale": "Move it as a tested vertical slice.",
                "review_by": "2026-11-30",
            }
        ],
    }
    _write(root / "architecture/debt.yaml", json.dumps(debt))

    assert "schema" in _rules(root)


def test_debt_entry_accept_disposition_is_valid(tmp_path: Path) -> None:
    """ "accept" is the disposition a genuinely reviewed, permanent
    architectural exception carries -- distinct from "migrate"'s ongoing
    tracked-work default."""
    root = _tree(tmp_path)
    _write(root / "abicheck/legacy.py", "VALUE = 1\n" * 10)
    debt = {
        "schema_version": 1,
        "files": [
            {
                "path": "abicheck/legacy.py",
                "baseline_lines": 9,
                "target": "n/a -- accepted exception",
                "rule": "no_growth",
                "disposition": "accept",
                "category": "generated_catalog",
                "owner": "maintainers",
                "rationale": "A reviewed, permanent parser/catalog exception per D5.",
                "review_by": "2026-11-30",
            }
        ],
    }
    _write(root / "architecture/debt.yaml", json.dumps(debt))

    assert "schema" not in _rules(root)


def test_unclassified_root_module_without_disposition_fails(tmp_path: Path) -> None:
    """ADR-061 gap F / definition-of-done item 16: a flat root module with no
    owning layer must carry one of the three recorded dispositions in
    ``architecture/dispositions.yaml`` -- "unclassified for now" is not
    itself a valid disposition, so leaving one out must fail the gate."""
    root = _tree(tmp_path)
    _write(root / "abicheck/orphan.py", "VALUE = 1\n")

    assert "unclassified-module-disposition" in _rules(root)


def test_migrate_disposition_records_an_unclassified_module(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    _write(root / "abicheck/orphan.py", "VALUE = 1\n")
    dispositions = {
        "schema_version": 1,
        "modules": [
            {
                "path": "abicheck/orphan.py",
                "disposition": "migrate",
                "target_layer": "model",
                "slice": "model/orphan.py",
                "rationale": "exercise the migrate disposition",
                "owner": "test",
                "review_by": "2099-01-01",
            }
        ],
    }
    _write(root / "architecture/dispositions.yaml", json.dumps(dispositions))

    assert "unclassified-module-disposition" not in _rules(root)


def test_retain_disposition_records_an_unclassified_module(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    _write(root / "abicheck/orphan.py", "VALUE = 1\n")
    dispositions = {
        "schema_version": 1,
        "modules": [
            {
                "path": "abicheck/orphan.py",
                "disposition": "retain",
                "supported_reason": "exercise the retain disposition",
                "owner": "test",
                "review_by": "2099-01-01",
            }
        ],
    }
    _write(root / "architecture/dispositions.yaml", json.dumps(dispositions))

    assert "unclassified-module-disposition" not in _rules(root)


def test_accept_disposition_records_an_unclassified_module(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    _write(root / "abicheck/orphan.py", "VALUE = 1\n")
    dispositions = {
        "schema_version": 1,
        "modules": [
            {
                "path": "abicheck/orphan.py",
                "disposition": "accept",
                "reason": "exercise the accept disposition",
                "owner": "test",
                "review_by": "2099-01-01",
            }
        ],
    }
    _write(root / "architecture/dispositions.yaml", json.dumps(dispositions))

    assert "unclassified-module-disposition" not in _rules(root)


def test_disposition_with_invalid_value_fails_schema_and_still_reports(
    tmp_path: Path,
) -> None:
    """A bad ``disposition`` value (e.g. "unclassified") must fail its own
    schema check rather than silently satisfying the completion gate."""
    root = _tree(tmp_path)
    _write(root / "abicheck/orphan.py", "VALUE = 1\n")
    dispositions = {
        "schema_version": 1,
        "modules": [
            {
                "path": "abicheck/orphan.py",
                "disposition": "unclassified",
                "owner": "test",
                "review_by": "2099-01-01",
            }
        ],
    }
    _write(root / "architecture/dispositions.yaml", json.dumps(dispositions))

    findings = _rules(root)
    assert "schema" in findings
    assert "unclassified-module-disposition" in findings


def test_disposition_missing_required_field_fails_schema_and_still_reports(
    tmp_path: Path,
) -> None:
    root = _tree(tmp_path)
    _write(root / "abicheck/orphan.py", "VALUE = 1\n")
    dispositions = {
        "schema_version": 1,
        "modules": [
            {
                "path": "abicheck/orphan.py",
                "disposition": "migrate",
                "target_layer": "model",
                # "slice" and "rationale" deliberately omitted.
                "owner": "test",
                "review_by": "2099-01-01",
            }
        ],
    }
    _write(root / "architecture/dispositions.yaml", json.dumps(dispositions))

    findings = _rules(root)
    assert "schema" in findings
    assert "unclassified-module-disposition" in findings


def test_disposition_unknown_target_layer_fails_schema(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    _write(root / "abicheck/orphan.py", "VALUE = 1\n")
    dispositions = {
        "schema_version": 1,
        "modules": [
            {
                "path": "abicheck/orphan.py",
                "disposition": "migrate",
                "target_layer": "nonexistent",
                "slice": "model/orphan.py",
                "rationale": "exercise an unknown target layer",
                "owner": "test",
                "review_by": "2099-01-01",
            }
        ],
    }
    _write(root / "architecture/dispositions.yaml", json.dumps(dispositions))

    assert "schema" in _rules(root)


def test_disposition_nested_path_fails_schema(tmp_path: Path) -> None:
    """CodeRabbit/Codex review (PR #1189): a disposition path must name a
    direct ``abicheck/*.py`` root module, not a nested package path -- the
    coverage loop only ever scans root files, so a nested path would pass
    schema validation while covering nothing real."""
    root = _tree(tmp_path)
    _add_package(root, "model")
    _write(root / "abicheck/model/value.py", "VALUE = 1\n")
    dispositions = {
        "schema_version": 1,
        "modules": [
            {
                "path": "abicheck/model/value.py",
                "disposition": "accept",
                "reason": "exercise a nested path",
                "owner": "test",
                "review_by": "2099-01-01",
            }
        ],
    }
    _write(root / "architecture/dispositions.yaml", json.dumps(dispositions))

    assert "schema" in _rules(root)


def test_disposition_nonexistent_path_fails_schema(tmp_path: Path) -> None:
    """CodeRabbit/Codex review (PR #1189): a disposition entry for a module
    that doesn't exist must fail schema validation, not silently satisfy the
    completion check -- a deleted/renamed module's stale entry must be
    caught, not read as still-valid coverage."""
    root = _tree(tmp_path)
    dispositions = {
        "schema_version": 1,
        "modules": [
            {
                "path": "abicheck/typo.py",
                "disposition": "accept",
                "reason": "exercise a nonexistent path",
                "owner": "test",
                "review_by": "2099-01-01",
            }
        ],
    }
    _write(root / "architecture/dispositions.yaml", json.dumps(dispositions))

    assert "schema" in _rules(root)


def test_disposition_non_string_target_layer_fails_schema_without_crashing(
    tmp_path: Path,
) -> None:
    """Codex review (PR #1189): a non-string ``target_layer`` (e.g. a JSON
    list) must not crash the checker with an unhashable-type error -- it
    must report a schema finding like any other malformed value."""
    root = _tree(tmp_path)
    _write(root / "abicheck/orphan.py", "VALUE = 1\n")
    dispositions = {
        "schema_version": 1,
        "modules": [
            {
                "path": "abicheck/orphan.py",
                "disposition": "migrate",
                "target_layer": ["model"],
                "slice": "model/orphan.py",
                "rationale": "exercise a non-string target layer",
                "owner": "test",
                "review_by": "2099-01-01",
            }
        ],
    }
    _write(root / "architecture/dispositions.yaml", json.dumps(dispositions))

    assert "schema" in _rules(root)


def test_duplicate_disposition_path_fails_schema(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    _write(root / "abicheck/orphan.py", "VALUE = 1\n")
    entry = {
        "path": "abicheck/orphan.py",
        "disposition": "accept",
        "reason": "exercise a duplicate path",
        "owner": "test",
        "review_by": "2099-01-01",
    }
    dispositions = {"schema_version": 1, "modules": [entry, dict(entry)]}
    _write(root / "architecture/dispositions.yaml", json.dumps(dispositions))

    # The duplicate entry itself fails schema validation; the module is
    # still covered by its first, valid occurrence.
    assert "schema" in _rules(root)


def test_missing_dispositions_file_is_a_config_error(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    (root / "architecture/dispositions.yaml").unlink()

    assert "config" in _rules(root)


def test_classified_module_needs_no_disposition(tmp_path: Path) -> None:
    """A module already owned by a layer (via legacy_paths or a physical
    package) is not this gate's concern -- only genuinely unclassified root
    modules are."""
    root = _tree(tmp_path)
    config = json.loads((root / "architecture/modules.yaml").read_text())
    config["layers"]["model"]["legacy_paths"] = ["abicheck/owned.py"]
    _write(root / "architecture/modules.yaml", json.dumps(config))
    _write(root / "abicheck/owned.py", "VALUE = 1\n")

    assert "unclassified-module-disposition" not in _rules(root)


def test_real_repository_dispositions_file_is_internally_consistent() -> None:
    """The real ``architecture/dispositions.yaml`` this PR ships must itself
    pass schema validation and cover every unclassified root module -- run
    against the actual repository tree, not a miniature fixture."""
    root = Path(__file__).resolve().parent.parent
    findings = [
        f
        for f in check_repository(root)
        if f.rule == "unclassified-module-disposition"
        or "dispositions.yaml" in f.message
    ]
    assert findings == []
