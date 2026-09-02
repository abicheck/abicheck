# Copyright abicheck contributors
# SPDX-License-Identifier: Apache-2.0
"""Tests for `scripts/pipeline_status_ledger.py`'s ledger validator.

No test file existed for this module before this one -- every prior fix here
(the mixed-key `sorted()` crash, the `bool`-as-int `schema_version` bypass,
the unchecked top-level key set, and this file's own newline-anchor bypass)
was caught by a PR reviewer reading the diff, not by a test in this suite.
This file closes that gap: one test per structural rule the validator
states, plus a parametrized test for the specific bug class a real review
finding on PR #1019 identified -- `$`-anchored regexes accept a value with a
trailing newline (as produced by a YAML block scalar, `key: |`), because in
Python `re`, `$` matches immediately before a trailing `\n`, not only at the
true end of string. That is a *general* anchor-choice bug, not a fact about
`as_of_commit` alone, so it is checked against both regex-validated fields
(`as_of_commit`, `as_of_date`) rather than only the one field the review
comment happened to name.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from pipeline_status_ledger import (  # noqa: E402
    PIPELINE_STATUS_FILE,
    check_pipeline_status_ledger,
    load_pipeline_status,
)


class FakeFindings:
    """Minimal `Findings`-protocol recorder for standalone assertions."""

    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def err(self, check: str, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, check: str, msg: str) -> None:
        self.warnings.append(msg)


def _valid_ledger() -> dict[str, object]:
    """A minimal, fully-valid ledger matching the schema-1 field layout --
    independent of the real committed file, so these tests keep working even
    if the real file's content (not shape) changes."""
    concept = {
        "primitive": "complete",
        "producers": "complete",
        "consumers": "partial",
        "authority": "mixed",
        "removal_gate": "Some future phase closes this.",
    }
    concepts = {
        name: dict(concept)
        for name in (
            "facts",
            "identity",
            "semantic_ir",
            "public_surface",
            "analysis_plan",
            "run_outcome",
            "sectioned_storage",
            "report_document",
            "l5_source_graph_identity",
        )
    }
    concepts["facts"]["persistence"] = "complete"
    return {
        "schema_version": 1,
        "as_of_commit": "aa78c37",
        "as_of_date": "2026-09-02",
        "concepts": concepts,
    }


def test_valid_ledger_produces_no_findings() -> None:
    f = FakeFindings()
    check_pipeline_status_ledger(f, _valid_ledger())
    assert f.errors == []


def test_real_committed_ledger_is_valid() -> None:
    """The actual `docs/_meta/one-semantic-pipeline-status.yaml` file must
    itself satisfy this validator -- this is what `check_docs_contract.py`
    runs in CI, so a broken real file should fail here too, not only in a
    full `verify.py` run."""
    f = FakeFindings()
    data = load_pipeline_status(f)
    assert f.errors == []
    assert data is not None
    check_pipeline_status_ledger(f, data)
    assert f.errors == []


def test_missing_ledger_file_is_an_error(tmp_path, monkeypatch) -> None:
    import pipeline_status_ledger as mod

    monkeypatch.setattr(mod, "PIPELINE_STATUS_FILE", tmp_path / "does-not-exist.yaml")
    f = FakeFindings()
    assert load_pipeline_status(f) is None
    assert any("file not found" in e for e in f.errors)


@pytest.mark.parametrize("field", ["as_of_commit", "as_of_date"])
class TestNewlineAnchorBypass:
    """The bug class a real review finding on PR #1019 identified: a
    `$`-anchored regex accepts a value ending in `\\n` (what a YAML block
    scalar `key: |` produces), because `$` matches before a trailing
    newline too. `\\A...\\Z` must reject it outright."""

    def test_trailing_newline_is_rejected(self, field: str) -> None:
        data = _valid_ledger()
        data[field] = f"{data[field]}\n"
        f = FakeFindings()
        check_pipeline_status_ledger(f, data)
        assert any(field in e for e in f.errors), (
            f"a newline-terminated {field!r} must be rejected, got: {f.errors}"
        )

    def test_embedded_newline_is_rejected(self, field: str) -> None:
        data = _valid_ledger()
        data[field] = f"{data[field]}\nextra-line"
        f = FakeFindings()
        check_pipeline_status_ledger(f, data)
        assert any(field in e for e in f.errors)

    def test_plain_valid_value_still_accepted(self, field: str) -> None:
        """Guard against an overcorrection (e.g. rejecting every value)."""
        data = _valid_ledger()
        f = FakeFindings()
        check_pipeline_status_ledger(f, data)
        assert not any(field in e for e in f.errors)


def test_unknown_top_level_field_is_an_error() -> None:
    data = _valid_ledger()
    data["as_of_commmit"] = "typo"  # the misspelling is the point
    f = FakeFindings()
    check_pipeline_status_ledger(f, data)
    assert any("unknown top-level field" in e for e in f.errors)


@pytest.mark.parametrize("bad_version", [True, 2, "1", 1.0])
def test_non_canonical_schema_version_is_rejected(bad_version: object) -> None:
    data = _valid_ledger()
    data["schema_version"] = bad_version
    f = FakeFindings()
    check_pipeline_status_ledger(f, data)
    assert any("schema_version" in e for e in f.errors)


def test_calendar_invalid_date_is_rejected() -> None:
    data = _valid_ledger()
    data["as_of_date"] = "2026-02-30"
    f = FakeFindings()
    check_pipeline_status_ledger(f, data)
    assert any("calendar date" in e for e in f.errors)


def test_missing_required_concept_is_an_error() -> None:
    data = _valid_ledger()
    del data["concepts"]["facts"]
    f = FakeFindings()
    check_pipeline_status_ledger(f, data)
    assert any("missing required entries" in e for e in f.errors)


def test_unknown_concept_key_is_an_error_with_mixed_type_keys() -> None:
    """A YAML mapping can carry non-string keys (a bare `1:`); the
    unknown-concept report must not crash sorting a mix of int and str."""
    data = _valid_ledger()
    data["concepts"][1] = {}  # type: ignore[index]
    f = FakeFindings()
    check_pipeline_status_ledger(f, data)  # must not raise
    assert any("not in the tracked inventory" in e for e in f.errors)


def test_non_hashable_status_value_does_not_crash() -> None:
    data = _valid_ledger()
    data["concepts"]["facts"]["primitive"] = ["complete"]
    f = FakeFindings()
    check_pipeline_status_ledger(f, data)  # must not raise
    assert any("primitive" in e for e in f.errors)


def test_invalid_authority_value_is_an_error() -> None:
    data = _valid_ledger()
    data["concepts"]["facts"]["authority"] = "bogus"
    f = FakeFindings()
    check_pipeline_status_ledger(f, data)
    assert any("authority" in e for e in f.errors)


def test_empty_removal_gate_is_an_error() -> None:
    data = _valid_ledger()
    data["concepts"]["facts"]["removal_gate"] = "   "
    f = FakeFindings()
    check_pipeline_status_ledger(f, data)
    assert any("removal_gate" in e for e in f.errors)


def test_persistence_required_only_for_facts() -> None:
    """`persistence` is `facts`-only: requiring it (missing -> error) on
    `facts` and rejecting it as unknown on every other concept are both
    covered by one round trip through a full ledger."""
    data = _valid_ledger()
    del data["concepts"]["facts"]["persistence"]
    data["concepts"]["identity"]["persistence"] = "complete"
    f = FakeFindings()
    check_pipeline_status_ledger(f, data)
    assert any(
        "concepts.facts: missing required field 'persistence'" in e for e in f.errors
    )
    assert any(
        "concepts.identity: unknown field(s)" in e and "persistence" in e
        for e in f.errors
    )


def test_unknown_concept_field_is_an_error() -> None:
    data = _valid_ledger()
    data["concepts"]["facts"]["extra_bogus_field"] = "x"
    f = FakeFindings()
    check_pipeline_status_ledger(f, data)
    assert any("unknown field(s)" in e for e in f.errors)


def test_ledger_file_exists_on_disk() -> None:
    assert PIPELINE_STATUS_FILE.is_file()
