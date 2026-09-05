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
import yaml

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
    """A minimal, fully-valid ledger matching the schema-2 field layout --
    independent of the real committed file, so these tests keep working even
    if the real file's content (not shape) changes."""
    concept = {
        "primitive": "complete",
        "producers": "complete",
        "consumers": "partial",
        "authority": "mixed",
        "lifecycle": "wired",
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
        "schema_version": 2,
        "as_of_commit": "aa78c37",
        "as_of_date": "2026-09-02",
        "concepts": concepts,
    }


def _dump_ledger_yaml(data: dict[str, object]) -> str:
    return yaml.safe_dump(data, sort_keys=False)


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


@pytest.mark.parametrize(
    "yaml_text",
    [
        # A repeated top-level key.
        "schema_version: 1\nschema_version: 2\nas_of_commit: aa78c37\n"
        'as_of_date: "2026-09-02"\nconcepts: {}\n',
        # A repeated key nested inside a mapping value.
        "schema_version: 1\nas_of_commit: aa78c37\n"
        'as_of_date: "2026-09-02"\n'
        "concepts:\n  facts:\n    primitive: complete\n    primitive: partial\n",
    ],
    ids=["top-level", "nested"],
)
def test_duplicate_mapping_key_is_rejected(
    tmp_path, monkeypatch, yaml_text: str
) -> None:
    """Plain `yaml.safe_load` silently keeps only the last value for a
    repeated key -- a merge or manual edit that duplicates `schema_version`
    or a `concepts.<name>.<field>` entry must not validate only the
    surviving copy and silently ignore a conflicting duplicate (a real
    review finding on PR #1019)."""
    import pipeline_status_ledger as mod

    ledger = tmp_path / "ledger.yaml"
    ledger.write_text(yaml_text, encoding="utf-8")
    monkeypatch.setattr(mod, "PIPELINE_STATUS_FILE", ledger)
    f = FakeFindings()
    assert load_pipeline_status(f) is None  # must not raise
    assert any("duplicate key" in e for e in f.errors)


def test_unhashable_mapping_key_is_rejected_not_crashed(tmp_path, monkeypatch) -> None:
    """A YAML mapping key can itself be a sequence or mapping (`? [a, b]`),
    which Python cannot hash -- `key in seen`/`mapping[key] = ...` would
    otherwise raise a raw `TypeError` that neither `_DuplicateKeyError` nor
    `yaml.YAMLError` catches, crashing the whole docs-contract job instead
    of producing the promised finding (a real review finding on PR #1019)."""
    import pipeline_status_ledger as mod

    yaml_text = (
        "schema_version: 1\nas_of_commit: aa78c37\n"
        'as_of_date: "2026-09-02"\nconcepts:\n  ? [a, b]\n  : {}\n'
    )
    ledger = tmp_path / "ledger.yaml"
    ledger.write_text(yaml_text, encoding="utf-8")
    monkeypatch.setattr(mod, "PIPELINE_STATUS_FILE", ledger)
    f = FakeFindings()
    assert load_pipeline_status(f) is None  # must not raise
    assert any("not a scalar" in e for e in f.errors)


def test_non_duplicate_yaml_is_unaffected_by_the_strict_loader(
    tmp_path, monkeypatch
) -> None:
    """Guard against an overcorrection: the strict loader must parse
    ordinary, non-duplicated YAML identically to `yaml.safe_load`."""
    import pipeline_status_ledger as mod

    ledger = tmp_path / "ledger.yaml"
    ledger.write_text(_dump_ledger_yaml(_valid_ledger()), encoding="utf-8")
    monkeypatch.setattr(mod, "PIPELINE_STATUS_FILE", ledger)
    f = FakeFindings()
    data = load_pipeline_status(f)
    assert f.errors == []
    assert data == _valid_ledger()


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


@pytest.mark.parametrize("bad_version", [True, 1, 3, "2", 2.0])
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


# --------------------------------------------------------------------------
# Schema 2: the `introduced -> wired -> authoritative -> retired` ladder and
# the separate `investigated_declined` disposition (track T2).
# --------------------------------------------------------------------------


def _declined_entry(**overrides: object) -> dict[str, object]:
    """One structurally valid `investigated_declined` entry."""
    entry: dict[str, object] = {
        "item": "Some behavioral change, investigated and declined.",
        # At or before `_valid_ledger()`'s own `as_of_date` — a decision
        # dated after the ledger's snapshot is itself a finding, see
        # `TestDecidedAgainstAsOfDate`.
        "decided": "2026-09-01",
        "leaves_open": "The consolidation this decline does not close.",
        "tracked_as": "some-plan.md's own section",
    }
    entry.update(overrides)
    return entry


@pytest.mark.parametrize(
    "bad_lifecycle",
    ["complete", "self", "", "RETIRED", ["wired"], None, 2],
    ids=["status-value", "authority-value", "empty", "case", "list", "none", "int"],
)
def test_lifecycle_value_outside_the_ladder_is_rejected(bad_lifecycle: object) -> None:
    """Covers the enum itself, and — via the `list` case — that an
    unhashable YAML value produces a finding rather than a `TypeError`
    crash, the same bug class the status-field check already guards."""
    data = _valid_ledger()
    data["concepts"]["facts"]["lifecycle"] = bad_lifecycle
    f = FakeFindings()
    check_pipeline_status_ledger(f, data)  # must not raise
    assert any("lifecycle" in e for e in f.errors)


def test_missing_lifecycle_is_a_missing_required_field() -> None:
    data = _valid_ledger()
    del data["concepts"]["facts"]["lifecycle"]
    f = FakeFindings()
    check_pipeline_status_ledger(f, data)
    assert any(
        "concepts.facts: missing required field 'lifecycle'" in e for e in f.errors
    )


@pytest.mark.parametrize("authority", sorted({"legacy", "mixed", "self"}))
@pytest.mark.parametrize(
    "lifecycle", ["introduced", "wired", "authoritative", "retired"]
)
def test_authority_lifecycle_agreement_over_the_whole_domain(
    authority: str, lifecycle: str
) -> None:
    """The consistency rule is exhaustively enumerated over its entire
    (3 x 4) input domain rather than checked on the one pair a reviewer
    happened to name, per AGENTS.md's bug-class regression-testing rule.

    The oracle is stated independently of the implementation's own
    `_PIPELINE_AUTHORITY_TO_LIFECYCLES` lookup table: here it is expressed
    as ordinal comparisons against the ladder's rungs (`self` sits at or
    above `authoritative`, `legacy` at or below `wired`, `mixed` exactly at
    `wired`), so a table edited in one direction cannot silently satisfy
    the test that is supposed to catch it.
    """
    ladder = ["introduced", "wired", "authoritative", "retired"]
    rung = ladder.index(lifecycle)
    expected_ok = {
        "self": rung >= ladder.index("authoritative"),
        "legacy": rung <= ladder.index("wired"),
        "mixed": rung == ladder.index("wired"),
    }[authority]

    data = _valid_ledger()
    entry = data["concepts"]["facts"]
    entry["authority"] = authority
    entry["lifecycle"] = lifecycle
    # Keep the other cross-field rules satisfied so this test isolates the
    # authority/lifecycle rule: `retired` separately requires every status
    # field to be `complete`, and `introduced` requires an unstarted
    # consumer while every rung above it requires a started one.
    entry["consumers"] = "not_started" if lifecycle == "introduced" else "complete"
    f = FakeFindings()
    check_pipeline_status_ledger(f, data)
    contradictions = [e for e in f.errors if "contradicts authority" in e]
    assert bool(contradictions) is not expected_ok, (
        f"authority={authority!r} lifecycle={lifecycle!r}: expected "
        f"{'no' if expected_ok else 'a'} contradiction finding, got {f.errors}"
    )


@pytest.mark.parametrize("lifecycle", ["wired", "authoritative", "retired"])
@pytest.mark.parametrize("field", ["producers", "consumers"])
def test_a_rung_above_introduced_requires_a_started_producer_and_consumer(
    lifecycle: str, field: str
) -> None:
    """Nothing downstream can read a concept no extraction site populates,
    so `producers: not_started` above `introduced` is as impossible as
    `consumers: not_started` there (Codex review, PR #1066). Exercised at
    every affected rung, for both fields."""
    data = _valid_ledger()
    entry = data["concepts"]["facts"]
    entry["authority"] = "self" if lifecycle != "wired" else "mixed"
    entry["lifecycle"] = lifecycle
    entry[field] = "not_started"
    f = FakeFindings()
    check_pipeline_status_ledger(f, data)
    assert any(f"lifecycle {lifecycle!r}" in e and field in e for e in f.errors)


@pytest.mark.parametrize("producers", ["not_started", "partial", "complete"])
def test_introduced_accepts_any_producer_status(producers: str) -> None:
    """The deliberate asymmetry: `introduced` is defined by the absence of
    *readers*, not of writers, so a fully-populated concept nothing consumes
    yet is exactly what the bottom rung is for. Guards against
    over-generalizing the producer rule into a symmetric one."""
    data = _valid_ledger()
    entry = data["concepts"]["facts"]
    entry["authority"] = "legacy"
    entry["lifecycle"] = "introduced"
    entry["consumers"] = "not_started"
    entry["producers"] = producers
    f = FakeFindings()
    check_pipeline_status_ledger(f, data)
    assert f.errors == []


@pytest.mark.parametrize("lifecycle", ["wired", "authoritative", "retired"])
def test_a_rung_above_introduced_requires_a_started_consumer(
    lifecycle: str,
) -> None:
    """Every rung above `introduced` asserts something downstream actually
    reads the concept — checked at every such rung, not only the one
    combination that motivated the rule."""
    data = _valid_ledger()
    entry = data["concepts"]["facts"]
    entry["authority"] = "self" if lifecycle != "wired" else "mixed"
    entry["lifecycle"] = lifecycle
    entry["consumers"] = "not_started"
    f = FakeFindings()
    check_pipeline_status_ledger(f, data)
    assert any(f"lifecycle {lifecycle!r}" in e and "consumers" in e for e in f.errors)


@pytest.mark.parametrize("consumers", ["partial", "complete"])
def test_introduced_requires_an_unstarted_consumer(consumers: str) -> None:
    """The symmetric half (Codex review, PR #1066): `introduced` is defined
    as "the primitive exists, nothing reads it", so a *started* consumer
    there is `wired` by definition — exactly as an unstarted consumer at
    `wired` or above is `introduced` by definition. Enforcing only one
    direction let the bottom rung silently absorb any consumer status."""
    data = _valid_ledger()
    entry = data["concepts"]["facts"]
    entry["authority"] = "legacy"
    entry["lifecycle"] = "introduced"
    entry["consumers"] = consumers
    f = FakeFindings()
    check_pipeline_status_ledger(f, data)
    assert any("'introduced'" in e and "consumers" in e for e in f.errors)


def test_introduced_with_an_unstarted_consumer_is_accepted() -> None:
    """Guard against an overcorrection: the bottom rung stays reachable."""
    data = _valid_ledger()
    entry = data["concepts"]["facts"]
    entry["authority"] = "legacy"
    entry["lifecycle"] = "introduced"
    entry["consumers"] = "not_started"
    f = FakeFindings()
    check_pipeline_status_ledger(f, data)
    assert f.errors == []


@pytest.mark.parametrize(
    "lifecycle", ["introduced", "wired", "authoritative", "retired"]
)
def test_no_rung_admits_an_unstarted_primitive(lifecycle: str) -> None:
    """`introduced` already means the type/module is defined, so a
    `primitive: not_started` concept is on no rung at all — checked at every
    rung rather than only above `introduced`, which is where the check was
    originally (and wrongly) gated (Codex review, PR #1066)."""
    data = _valid_ledger()
    entry = data["concepts"]["facts"]
    entry["authority"] = (
        "self" if lifecycle in ("authoritative", "retired") else "legacy"
    )
    entry["lifecycle"] = lifecycle
    entry["consumers"] = "not_started" if lifecycle == "introduced" else "complete"
    entry["primitive"] = "not_started"
    f = FakeFindings()
    check_pipeline_status_ledger(f, data)
    assert any(f"lifecycle {lifecycle!r}" in e and "primitive" in e for e in f.errors)


@pytest.mark.parametrize(
    "field", ["primitive", "producers", "consumers", "persistence"]
)
def test_retired_requires_every_status_field_complete(field: str) -> None:
    """`retired` claims the replaced implementation is gone, so a concept
    still reporting partial work anywhere cannot sit there. Checked for
    each status field independently, `facts`-only `persistence` included."""
    data = _valid_ledger()
    entry = data["concepts"]["facts"]
    entry["authority"] = "self"
    entry["lifecycle"] = "retired"
    entry["consumers"] = "complete"
    entry[field] = "partial"
    f = FakeFindings()
    check_pipeline_status_ledger(f, data)
    assert any("'retired'" in e and field in e for e in f.errors)


def test_a_fully_complete_retired_concept_is_accepted() -> None:
    """Guard against an overcorrection: the top rung must be reachable."""
    data = _valid_ledger()
    entry = data["concepts"]["facts"]
    entry["authority"] = "self"
    entry["lifecycle"] = "retired"
    entry["consumers"] = "complete"
    f = FakeFindings()
    check_pipeline_status_ledger(f, data)
    assert f.errors == []


def test_a_valid_investigated_declined_list_is_accepted() -> None:
    data = _valid_ledger()
    data["concepts"]["facts"]["investigated_declined"] = [
        _declined_entry(),
        _declined_entry(item="A second one."),
    ]
    f = FakeFindings()
    check_pipeline_status_ledger(f, data)
    assert f.errors == []


def test_investigated_declined_is_optional_on_every_concept() -> None:
    """It must not become a de-facto required field: a concept with nothing
    declined omits it, and that is not a finding."""
    data = _valid_ledger()
    assert all(
        "investigated_declined" not in entry for entry in data["concepts"].values()
    )
    f = FakeFindings()
    check_pipeline_status_ledger(f, data)
    assert f.errors == []


def test_investigated_declined_does_not_close_a_consolidation() -> None:
    """The rule this field exists for (duplication-and-convergence-
    assessment.md, "The completion rule this plan was missing"): declining
    a *behavioral* change does not delete a second *implementation*, so a
    concept carrying a decline cannot claim the `retired` rung."""
    data = _valid_ledger()
    entry = data["concepts"]["facts"]
    entry["authority"] = "self"
    entry["lifecycle"] = "retired"
    entry["consumers"] = "complete"
    entry["investigated_declined"] = [_declined_entry()]
    f = FakeFindings()
    check_pipeline_status_ledger(f, data)
    assert any("not a synonym for 'retired'" in e for e in f.errors)


@pytest.mark.parametrize("lifecycle", ["introduced", "wired", "authoritative"])
def test_investigated_declined_is_allowed_below_retired(lifecycle: str) -> None:
    """Guard against an overcorrection: a decline is a normal, correct
    disposition — it blocks only the rung that asserts a deletion."""
    data = _valid_ledger()
    entry = data["concepts"]["facts"]
    entry["authority"] = "legacy" if lifecycle != "authoritative" else "self"
    entry["lifecycle"] = lifecycle
    entry["consumers"] = "not_started" if lifecycle == "introduced" else "partial"
    entry["investigated_declined"] = [_declined_entry()]
    f = FakeFindings()
    check_pipeline_status_ledger(f, data)
    assert f.errors == []


@pytest.mark.parametrize("field", ["item", "decided", "leaves_open", "tracked_as"])
def test_each_declined_entry_field_is_required(field: str) -> None:
    data = _valid_ledger()
    entry = _declined_entry()
    del entry[field]
    data["concepts"]["facts"]["investigated_declined"] = [entry]
    f = FakeFindings()
    check_pipeline_status_ledger(f, data)
    assert any(f"missing required field {field!r}" in e for e in f.errors)


@pytest.mark.parametrize("field", ["item", "leaves_open", "tracked_as"])
@pytest.mark.parametrize("blank", ["", "   ", "\n", 7, None, ["x"]])
def test_each_declined_entry_string_field_must_be_non_empty(
    field: str, blank: object
) -> None:
    """A blank `leaves_open` would be the loophole restated in structured
    form — an entry that records a decline while saying nothing about what
    stays open. Checked for every string field and every blank-ish shape,
    including non-string and unhashable values."""
    data = _valid_ledger()
    data["concepts"]["facts"]["investigated_declined"] = [
        _declined_entry(**{field: blank})
    ]
    f = FakeFindings()
    check_pipeline_status_ledger(f, data)  # must not raise
    assert any(f"[0].{field}" in e for e in f.errors)


@pytest.mark.parametrize(
    "bad_date",
    ["2026-02-30", "2026-9-4", "20260904", "2026-09-04\n", "yesterday", None],
    ids=["calendar", "unpadded", "no-dashes", "trailing-newline", "prose", "none"],
)
def test_declined_entry_date_is_validated_like_as_of_date(bad_date: object) -> None:
    """`decided` and `as_of_date` share one validation helper precisely so
    they cannot drift apart — including the `\\A...\\Z` anchoring that a
    YAML block scalar's trailing newline would otherwise slip past."""
    data = _valid_ledger()
    data["concepts"]["facts"]["investigated_declined"] = [
        _declined_entry(decided=bad_date)
    ]
    f = FakeFindings()
    check_pipeline_status_ledger(f, data)
    assert any("[0].decided" in e for e in f.errors)


@pytest.mark.parametrize(
    "value",
    [[], {}, "a string", None, 3],
    ids=["empty-list", "mapping", "string", "none", "int"],
)
def test_investigated_declined_must_be_a_non_empty_list(value: object) -> None:
    """An empty list is rejected on purpose: it is indistinguishable from a
    real list left un-updated, so the field is omitted instead."""
    data = _valid_ledger()
    data["concepts"]["facts"]["investigated_declined"] = value
    f = FakeFindings()
    check_pipeline_status_ledger(f, data)
    assert any("investigated_declined: must be a non-empty list" in e for e in f.errors)


def test_non_mapping_declined_entry_is_reported_not_crashed() -> None:
    data = _valid_ledger()
    data["concepts"]["facts"]["investigated_declined"] = ["just a string"]
    f = FakeFindings()
    check_pipeline_status_ledger(f, data)  # must not raise
    assert any("[0]: must be a mapping" in e for e in f.errors)


def test_unknown_declined_entry_field_is_an_error_with_mixed_type_keys() -> None:
    entry = _declined_entry()
    entry["tracked_at"] = "typo of tracked_as"  # the misspelling is the point
    entry[7] = "a non-string YAML key"  # type: ignore[index]
    data = _valid_ledger()
    data["concepts"]["facts"]["investigated_declined"] = [entry]
    f = FakeFindings()
    check_pipeline_status_ledger(f, data)  # must not raise sorting mixed keys
    assert any("[0]: unknown field(s)" in e for e in f.errors)


def test_the_real_ledger_records_no_retired_concept_carrying_a_decline() -> None:
    """The committed file's own audit invariant, asserted against the real
    file rather than a fixture: every concept that records an
    investigated-and-declined disposition sits below `retired`."""
    f = FakeFindings()
    data = load_pipeline_status(f)
    assert data is not None
    for name, entry in data["concepts"].items():
        if entry.get("investigated_declined"):
            assert entry["lifecycle"] != "retired", name


class TestDecidedAgainstAsOfDate:
    """A decision dated after the snapshot the ledger claims to describe is
    the file asserting that decision existed at an earlier assessment
    (Codex review, PR #1066). Checked across the ordering's whole
    three-way domain — before, equal, after — rather than only the one
    direction the finding named, so an overcorrection rejecting a
    same-day or earlier decision fails here too."""

    @pytest.mark.parametrize(
        ("as_of_date", "decided", "expect_error"),
        [
            ("2026-09-02", "2026-09-01", False),
            ("2026-09-02", "2026-09-02", False),
            ("2026-09-02", "2026-09-03", True),
            # Ordering must hold across a month and a year boundary too: a
            # naive lexical comparison happens to be correct for zero-padded
            # ISO dates, and these pin that it stays correct rather than
            # accidentally so for one month's worth of inputs.
            ("2026-09-30", "2026-10-01", True),
            ("2026-12-31", "2027-01-01", True),
            ("2027-01-01", "2026-12-31", False),
        ],
    )
    def test_ordering(self, as_of_date: str, decided: str, expect_error: bool) -> None:
        data = _valid_ledger()
        data["as_of_date"] = as_of_date
        data["concepts"]["facts"]["investigated_declined"] = [
            _declined_entry(decided=decided)
        ]
        f = FakeFindings()
        check_pipeline_status_ledger(f, data)
        later = [e for e in f.errors if "later than the ledger" in e]
        assert bool(later) is expect_error, (
            f"as_of_date={as_of_date} decided={decided}: expected "
            f"{'a' if expect_error else 'no'} finding, got {f.errors}"
        )

    def test_an_unparseable_as_of_date_adds_no_second_finding(self) -> None:
        """A malformed `as_of_date` already reports itself; comparing a
        `decided` against it would add a misleading second finding about
        the entry rather than the header."""
        data = _valid_ledger()
        data["as_of_date"] = "not-a-date"
        data["concepts"]["facts"]["investigated_declined"] = [_declined_entry()]
        f = FakeFindings()
        check_pipeline_status_ledger(f, data)
        assert any("'as_of_date'" in e for e in f.errors)
        assert not any("later than the ledger" in e for e in f.errors)


def test_the_real_ledger_dates_no_decision_after_its_own_snapshot() -> None:
    """The committed file's own instance of the invariant."""
    f = FakeFindings()
    data = load_pipeline_status(f)
    assert data is not None
    for name, entry in data["concepts"].items():
        for declined in entry.get("investigated_declined", []):
            assert declined["decided"] <= data["as_of_date"], name
