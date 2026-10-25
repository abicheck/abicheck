"""``impact_assessment.impact_group_id`` follows the ``RootCauseCorrelator``
groups (G29 Phase 6 follow-up, report schema 6.5).

Before this change ``impact_group_id`` was always an alias of ``root_cause_id``.
Now a finding that is a member of a multi-piece correlator group carries that
group's id, which can differ from ``root_cause_id`` by *presence*: the
correlator groups a bare-symbol pair with no ``caused_by_type`` (a
``--used-by`` consumer's ``CONSUMER_REQUIRED_SYMBOL_REMOVED`` beside the
shared ``FUNC_REMOVED``) that the report-level grouping leaves ungrouped.
``root_cause_id`` itself must not move.

Expected values are computed here from the grouping rule written out
independently (``sha256(key)[:16]``, ``caused_by_type`` else ``symbol``,
groups of two or more correlator kinds), not by calling the implementation's
own helpers.
"""

from __future__ import annotations

import hashlib
import json
import random

from abicheck import reporter
from abicheck.checker_policy import ChangeKind, Verdict
from abicheck.checker_types import DiffResult
from abicheck.impact import ImpactAssessment
from abicheck.impact.engine import assess_change
from abicheck.model.change import Change
from abicheck.report.view import finding_id
from abicheck.root_cause_evidence import (
    impact_assessment_lookups,
    impact_group_lookup_for_changes,
    scoped_only_impact_group_lookup,
)
from abicheck.sarif import to_sarif
from abicheck.schemas.documents import load_compare_report_schema
from tests.schema_validation import validate_instance

_CORRELATED_KINDS = frozenset(
    {
        ChangeKind.FUNC_REMOVED,
        ChangeKind.INTERNAL_SYMBOL_REQUIRED_BY_PUBLIC_API,
        ChangeKind.CONSUMER_REQUIRED_SYMBOL_REMOVED,
    }
)


def _sha16(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _oracle_group_ids(changes: list[Change]) -> dict[int, str]:
    """Independent statement of the correlator rule: ``id(change)`` ->
    group id, for every correlator-kind change sharing a key with at least
    one other correlator-kind change."""
    by_key: dict[str, list[Change]] = {}
    for c in changes:
        if c.kind in _CORRELATED_KINDS and (c.caused_by_type or c.symbol):
            by_key.setdefault(c.caused_by_type or c.symbol, []).append(c)
    out: dict[int, str] = {}
    for key, members in by_key.items():
        if len(members) >= 2:
            for c in members:
                out[id(c)] = _sha16(key)
    return out


def _result(changes: list[Change], scoped: tuple[Change, ...] = ()) -> DiffResult:
    r = DiffResult(
        old_version="1.0",
        new_version="2.0",
        library="libtest.so.1",
        changes=changes,
        verdict=Verdict.BREAKING,
    )
    if scoped:
        # Same attribute cli_compare_helpers sets for --used-by/--required-symbol.
        r.scoped_only_changes = scoped
    return r


def _impact_entries(
    changes: list[Change], scoped: tuple[Change, ...] = ()
) -> list[dict]:
    d = json.loads(reporter.to_json(_result(changes, scoped)))
    return d["changes"]


class TestBareSymbolPairDiverges:
    def test_pair_shares_impact_group_while_root_cause_stays_absent(self) -> None:
        func = Change(ChangeKind.FUNC_REMOVED, "pub_helper", "removed")
        consumer = Change(
            ChangeKind.CONSUMER_REQUIRED_SYMBOL_REMOVED, "pub_helper", "consumer needs"
        )
        entries = _impact_entries([func, consumer])
        assert len(entries) == 2
        expected = _sha16("pub_helper")
        for entry in entries:
            ia = entry["impact_assessment"]
            # Report-level grouping: neither finding references the symbol via
            # caused_by_type, so both stay ungrouped and root_cause_id is absent.
            assert "root_cause_id" not in ia
            assert "root_cause_display" not in ia
            # Correlator grouping: both members share one group id.
            assert ia["impact_group_id"] == expected
            assert ia["root_cause_evidence"]["strongest_evidence_level"] == (
                "consumer_proven"
            )

    def test_unrelated_finding_stays_outside_the_group(self) -> None:
        func = Change(ChangeKind.FUNC_REMOVED, "pub_helper", "removed")
        consumer = Change(
            ChangeKind.CONSUMER_REQUIRED_SYMBOL_REMOVED, "pub_helper", "consumer needs"
        )
        other = Change(ChangeKind.FUNC_REMOVED, "unrelated", "removed")
        entries = _impact_entries([func, consumer, other])
        by_symbol = {e["symbol"]: e for e in entries}
        assert by_symbol["pub_helper"]["impact_assessment"]["impact_group_id"] == (
            _sha16("pub_helper")
        )
        # A lone correlator-kind finding is no group: no impact_group_id at all.
        assert "impact_assessment" not in by_symbol["unrelated"]

    def test_scoped_only_sibling_joins_the_group(self) -> None:
        # The --used-by shape: the consumer overlay exists only as a
        # scoped-only change, so only the lookup over changes + scoped sees the pair.
        func = Change(ChangeKind.FUNC_REMOVED, "pub_helper", "removed")
        consumer = Change(
            ChangeKind.CONSUMER_REQUIRED_SYMBOL_REMOVED, "pub_helper", "consumer needs"
        )
        result = _result([func], scoped=(consumer,))
        lookup = scoped_only_impact_group_lookup(result, [func], None)
        expected = _sha16("pub_helper")
        assert lookup == {finding_id(func): expected, finding_id(consumer): expected}
        # Without the scoped sibling there is nothing to correlate with.
        assert impact_group_lookup_for_changes([func]) == {}
        entries = _impact_entries([func], scoped=(consumer,))
        assert entries[0]["impact_assessment"]["impact_group_id"] == expected
        assert "root_cause_id" not in entries[0]["impact_assessment"]


class TestCorrelationLookups:
    def test_combined_lookup_returns_all_three_views_for_a_bare_pair(self) -> None:
        func = Change(ChangeKind.FUNC_REMOVED, "pub_helper", "removed")
        consumer = Change(
            ChangeKind.CONSUMER_REQUIRED_SYMBOL_REMOVED, "pub_helper", "consumer needs"
        )
        root, evidence, group = impact_assessment_lookups([func, consumer])
        # No caused_by_type on either side: no report-level root cause ...
        assert root == {}
        # ... but both are correlator members, so both carry evidence and the group id.
        expected = _sha16("pub_helper")
        assert group == {finding_id(func): expected, finding_id(consumer): expected}
        assert set(evidence) == {finding_id(func), finding_id(consumer)}

    def test_show_only_filter_drops_scoped_siblings_from_the_group(self) -> None:
        func = Change(ChangeKind.FUNC_REMOVED, "pub_helper", "removed")
        consumer = Change(
            ChangeKind.CONSUMER_REQUIRED_SYMBOL_REMOVED, "pub_helper", "consumer needs"
        )
        result = _result([func], scoped=(consumer,))
        # Unfiltered: the scoped-only sibling joins the group.
        assert len(scoped_only_impact_group_lookup(result, [func], None)) == 2
        # A --show-only that keeps no severity drops the scoped sibling, so the
        # primary finding loses its partner and is no longer a group member.
        assert scoped_only_impact_group_lookup(result, [func], "compatible") == {}


class TestAgreementWhenBothPresent:
    def test_caused_by_type_pair_has_equal_ids_and_unchanged_root_cause(self) -> None:
        root = Change(ChangeKind.FUNC_REMOVED, "ns::helper", "removed")
        overlay = Change(
            ChangeKind.INTERNAL_SYMBOL_REQUIRED_BY_PUBLIC_API,
            "pub_entry",
            "required",
            caused_by_type="ns::helper",
        )
        entries = _impact_entries([root, overlay])
        expected = _sha16("ns::helper")
        for entry in entries:
            ia = entry["impact_assessment"]
            # root_cause_id keeps the report grouping's own hash, unchanged.
            assert ia["root_cause_id"] == expected
            assert ia["root_cause_display"] == "ns::helper"
            assert ia["impact_group_id"] == ia["root_cause_id"]


class TestNothingCorrelates:
    def test_report_group_without_correlator_group_falls_back_to_root_cause(
        self,
    ) -> None:
        # Two non-correlator kinds share caused_by_type "T": a report-level root
        # cause exists, but the load-failure correlator has no group, so
        # impact_group_id must equal root_cause_id.
        a = Change(ChangeKind.FUNC_PARAMS_CHANGED, "f1", "params", caused_by_type="T")
        b = Change(ChangeKind.FUNC_RETURN_CHANGED, "f2", "return", caused_by_type="T")
        assert impact_group_lookup_for_changes([a, b]) == {}
        entries = _impact_entries([a, b])
        for entry in entries:
            ia = entry["impact_assessment"]
            assert ia["root_cause_id"] == _sha16("T")
            assert ia["impact_group_id"] == ia["root_cause_id"]
            assert "root_cause_evidence" not in ia

    def test_lone_finding_carries_no_impact_group(self) -> None:
        lone = Change(ChangeKind.FUNC_REMOVED, "solo", "removed")
        entries = _impact_entries([lone])
        assert "impact_assessment" not in entries[0]


class TestAssessChangeContract:
    def test_explicit_group_id_wins_over_root_cause(self) -> None:
        change = Change(ChangeKind.FUNC_REMOVED, "s", "removed")
        ia = assess_change(change, root_cause=("rid", "disp"), impact_group_id="gid")
        assert ia.root_cause_id == "rid"
        assert ia.impact_group_id == "gid"

    def test_absent_group_id_falls_back_to_root_cause(self) -> None:
        change = Change(ChangeKind.FUNC_REMOVED, "s", "removed")
        ia = assess_change(change, root_cause=("rid", "disp"))
        assert ia.impact_group_id == "rid"

    def test_group_id_without_root_cause_serializes_on_its_own(self) -> None:
        change = Change(ChangeKind.FUNC_REMOVED, "s", "removed")
        ia = assess_change(change, impact_group_id="gid")
        assert ia.has_signal()
        d = ia.to_dict()
        assert d["impact_group_id"] == "gid"
        assert "root_cause_id" not in d
        assert "root_cause_display" not in d

    def test_default_assessment_is_still_sparse(self) -> None:
        assert not ImpactAssessment().has_signal()
        assert "impact_group_id" not in ImpactAssessment().to_dict()


class TestSarifSurface:
    def test_sarif_impact_assessment_carries_group_without_root_cause(self) -> None:
        func = Change(ChangeKind.FUNC_REMOVED, "pub_helper", "removed")
        consumer = Change(
            ChangeKind.CONSUMER_REQUIRED_SYMBOL_REMOVED, "pub_helper", "consumer needs"
        )
        doc = to_sarif(_result([func, consumer]))
        results = doc["runs"][0]["results"]
        assert len(results) == 2
        for res in results:
            ia = res["properties"]["impactAssessment"]
            assert "rootCauseId" not in res["properties"]
            assert "root_cause_id" not in ia
            assert ia["impact_group_id"] == _sha16("pub_helper")


class TestSchemaAcceptsGroupWithoutRootCause:
    def test_bare_pair_changes_validate_against_compare_schema(self) -> None:
        func = Change(ChangeKind.FUNC_REMOVED, "pub_helper", "removed")
        consumer = Change(
            ChangeKind.CONSUMER_REQUIRED_SYMBOL_REMOVED, "pub_helper", "consumer needs"
        )
        doc = json.loads(reporter.to_json(_result([func, consumer])))
        assert doc["report_schema_version"] == "6.7"
        # A bare DiffResult lacks the pipeline-level required keys, so validate
        # each `changes` entry against the schema's own change definition.
        schema = load_compare_report_schema()
        change_schema = {"$defs": schema["$defs"], "$ref": "#/$defs/change"}
        assert len(doc["changes"]) == 2
        for entry in doc["changes"]:
            assert "impact_group_id" in entry["impact_assessment"]
            validate_instance(entry, change_schema)


class TestPropertyAgainstOracle:
    def test_random_changes_match_independent_correlator_rule(self) -> None:
        correlated = sorted(_CORRELATED_KINDS, key=lambda k: k.value)
        other = [ChangeKind.FUNC_PARAMS_CHANGED, ChangeKind.FUNC_RETURN_CHANGED]
        symbols = ["a", "b", "c"]
        rng = random.Random(20260921)
        for _trial in range(200):
            changes: list[Change] = []
            for i in range(rng.randint(1, 5)):
                kind = rng.choice(correlated + other)
                symbol = rng.choice(symbols + [None]) or f"s{i}"
                caused_by = rng.choice([None, None, *symbols])
                changes.append(Change(kind, symbol, f"d{i}", caused_by_type=caused_by))
            oracle = _oracle_group_ids(changes)
            entries = _impact_entries(changes)
            # Walk in input order; guard the alignment before comparing by index.
            assert [e["symbol"] for e in entries] == [c.symbol for c in changes]
            for change, entry in zip(changes, entries, strict=True):
                ia = entry.get("impact_assessment", {})
                root = ia.get("root_cause_id")
                group = ia.get("impact_group_id")
                if root is not None and group is not None:
                    assert root == group
                expected = oracle.get(id(change))
                if expected is None:
                    assert group == root
                else:
                    assert group == expected


class TestScopedFoldInCarriesImpactGroup:
    """A ``--used-by`` run folds scoped-only findings into ``changes`` after the
    document is built (``apply_scoped_gate``). That fold must stamp the same
    group id the correlator gives the pair, not only the lookup helpers."""

    def test_folded_scoped_only_entry_carries_its_group_id(self) -> None:
        func = Change(ChangeKind.FUNC_REMOVED, "pub_helper", "removed")
        consumer = Change(
            ChangeKind.CONSUMER_REQUIRED_SYMBOL_REMOVED, "pub_helper", "consumer needs"
        )
        result = _result([func], scoped=(consumer,))
        # Stamping used_by is what makes apply_scoped_gate run the fold-in.
        result.used_by = ()
        entries = {
            e["symbol"]: e for e in json.loads(reporter.to_json(result))["changes"]
        }
        expected = _sha16("pub_helper")
        assert entries["pub_helper"]["impact_assessment"]["impact_group_id"] == expected
        folded = [
            e
            for e in json.loads(reporter.to_json(result))["changes"]
            if e["kind"] == ChangeKind.CONSUMER_REQUIRED_SYMBOL_REMOVED.value
        ]
        assert len(folded) == 1
        assert folded[0]["impact_assessment"]["impact_group_id"] == expected
        assert "root_cause_id" not in folded[0]["impact_assessment"]
