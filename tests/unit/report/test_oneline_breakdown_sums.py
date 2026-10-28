"""The one-line summary's breakdown adds up to its total, and states what the JSON states.

Bug class ``report.headline_breakdown_drift``:
``compare -o oneline=...`` printed ``5 breaking, 2 compatible (8 total)`` for a
run whose JSON carried ``breaking 5, compatible_additions 2, informational 1``.
The one informational finding was counted in the total and named in no
clause, so the parts did not add up to the number they were announced beside.

The invariant is stated over every ``ChangeKind`` and every combination of
the three axes a headline can see -- verdict, compatibility category, and
whether the compatibility policy scored the finding at all -- plus the
standing-inventory axis (cross-source evolution). Its oracle is the
``ChangeKind`` registry's own facts (``default_verdict``, the addition and
informational sets, the evaluation status enum) and the JSON report. It never
calls the one-line renderer to decide what the one-line renderer should say.

The total is the JSON ``summary.total_changes`` population, hygiene
disposition findings included (ADR-067: a finding is recorded before any
disposition acts on it). A stamped hygiene finding -- the cross-source checks
such as ``exported_not_public`` -- is stated in its own
``header/binary consistency`` clause, so the parts still add up to the total.
"""

from __future__ import annotations

import json
import re
from types import SimpleNamespace

import pytest
from hypothesis import given, settings, strategies as st

from abicheck.checker import DiffResult
from abicheck.checker_policy import ChangeKind, CrossSourceEvolution, Verdict
from abicheck.contract_relevance_types import CompatibilityEvaluationStatus
from abicheck.model.change import Change
from abicheck.model.change_catalog.assembled import REGISTRY
from abicheck.policy.reclassify import ReclassifyRule
from abicheck.policy_file import PolicyFile
from abicheck.report.release_change_inventory import release_inventory_counters
from abicheck.report.release_member_entry import stamp_member_counts
from abicheck.report.release_member_summary import add_member_review_summary
from abicheck.report.release_oneline import (
    format_release_oneline,
    release_global_counts,
)
from abicheck.reporter import to_json
from abicheck.workflows.cross_source_hygiene import CROSS_SOURCE_EVOLUTION_CHECKS
from tests._report_test_helpers import stat_line

#: The summary-block keys the one-line breakdown names, in the JSON's own
#: spelling. The oracle below is keyed the same way so a mismatch reads as a
#: bucket name, not as an index.
_BUCKET_KEYS = (
    "breaking",
    "source_breaks",
    "risk_changes",
    "compatible_additions",
    "quality_issues",
    "informational",
)
#: ``hygiene`` is the standing cross-source population the one-line summary
#: states as its own clause (the JSON has no per-bucket split of it).
_KEYS = (*_BUCKET_KEYS, "not_evaluated", "hygiene")

#: The clause noun each key is stated with on the line. Literal, not imported
#: from the renderer: a noun the renderer renames must fail here.
_NOUN_TO_KEY = {
    "breaking": "breaking",
    "source-level breaks": "source_breaks",
    "risk": "risk_changes",
    "compatible": "compatible_additions",
    "quality": "quality_issues",
    "informational": "informational",
    "not evaluated": "not_evaluated",
    "header/binary consistency": "hygiene",
}
_CLAUSE = re.compile(
    r"(\d+) (source-level breaks|breaking|risk|compatible|quality|informational"
    r"|not evaluated|header/binary consistency)"
)
_HEADLINE = re.compile(r"^[^:]+: (?P<detail>.*) \((?P<total>\d+) total\)")

_KIND_VALUES = tuple(sorted(REGISTRY.entries))
#: The disposition kinds a standing-inventory run stamps: the cross-source
#: hygiene checks. Named explicitly so the generated change sets always
#: contain them, not only by chance of the full-registry draw.
_HYGIENE_KINDS = tuple(sorted(CROSS_SOURCE_EVOLUTION_CHECKS))
_STAMPED = tuple(CrossSourceEvolution)
_STAMPS = (None, *_STAMPED)


def _change(
    kind_value: str, stamp: CrossSourceEvolution | None, *, scored: bool
) -> Change:
    change = Change(ChangeKind(kind_value), "sym", "sym")
    change.cross_source_evolution = stamp
    if not scored:
        change.compatibility_evaluation_status = (
            CompatibilityEvaluationStatus.NOT_EVALUATED
        )
    return change


def _expected(changes: list[Change], *, observed_only: bool) -> dict[str, int]:
    """Expected counts, from ``ChangeKind`` registry facts alone.

    *observed_only* selects the population the one-line headline states when
    standing inventory is present: stamped findings go to the ``hygiene``
    clause, unstamped findings to the compatibility buckets, with the observed
    COMPATIBLE verdict collapsed into the single ``compatible`` clause (the
    inventory block cannot split it, see ``render_text.format_stat_line``).
    The total over every key is the JSON ``total_changes`` population in both
    modes.
    """
    hygiene = observed_only and any(
        c.cross_source_evolution is not None for c in changes
    )
    out = dict.fromkeys(_KEYS, 0)
    for change in changes:
        if hygiene and change.cross_source_evolution is not None:
            out["hygiene"] += 1
            continue
        if (
            change.compatibility_evaluation_status
            is CompatibilityEvaluationStatus.NOT_EVALUATED
        ):
            out["not_evaluated"] += 1
            continue
        meta = REGISTRY.get(change.kind.value)
        assert meta is not None
        verdict = meta.default_verdict
        if verdict is Verdict.BREAKING:
            out["breaking"] += 1
        elif verdict is Verdict.API_BREAK:
            out["source_breaks"] += 1
        elif verdict is Verdict.COMPATIBLE_WITH_RISK:
            out["risk_changes"] += 1
        elif hygiene:
            out["compatible_additions"] += 1
        elif change.kind.value in REGISTRY.informational_kinds():
            out["informational"] += 1
        elif change.kind.value in REGISTRY.addition_kinds():
            out["compatible_additions"] += 1
        else:
            out["quality_issues"] += 1
    return out


def _parse_headline(line: str) -> tuple[dict[str, int], int]:
    match = _HEADLINE.match(line)
    assert match is not None, f"no breakdown and total in: {line!r}"
    parts = {_NOUN_TO_KEY[noun]: int(n) for n, noun in _CLAUSE.findall(match["detail"])}
    return parts, int(match["total"])


def _result(changes: list[Change]) -> DiffResult:
    return DiffResult(
        old_version="1.0", new_version="2.0", library="libdemo.so", changes=changes
    )


def _assert_headline_matches_report(changes: list[Change]) -> None:
    result = _result(changes)
    line = stat_line(result)
    parts, total = _parse_headline(line)

    # The invariant the bug report named: the parts add up to the total.
    assert sum(parts.values()) == total, line

    # The JSON report is the oracle for the total: the same population,
    # hygiene findings included, not a filtered subset of it.
    report = json.loads(to_json(result))
    summary = report["summary"]
    assert summary["total_changes"] == len(changes)
    assert total == summary["total_changes"], line

    observed = _expected(changes, observed_only=True)
    observed_total = sum(observed.values())
    assert total == observed_total, line
    assert parts == {k: v for k, v in observed.items() if v}, line

    # The JSON report, read as a document: its bucket counts are the same
    # facts the headline states, and its own parts add up to its total.
    full = _expected(changes, observed_only=False)
    for key in _BUCKET_KEYS:
        assert summary[key] == full[key], key
    # The JSON names no not-evaluated count (its per-change status is only
    # emitted under a stamped contract relevance), so the count it implies is
    # what its total holds beyond its own buckets.
    json_not_evaluated = summary["total_changes"] - sum(
        summary[key] for key in _BUCKET_KEYS
    )
    assert full["not_evaluated"] == json_not_evaluated

    inventory = summary["change_inventory"]
    stamped = [c for c in changes if c.cross_source_evolution is not None]
    # The JSON inventory block partitions the same total: its compatibility
    # counters are the observed half, its hygiene counters the standing half.
    assert inventory["compatibility_changes"] + sum(
        inventory[k] for k in inventory if k.startswith("hygiene_")
    ) == len(changes)
    assert sum(inventory[k] for k in inventory if k.startswith("hygiene_")) == len(
        stamped
    )
    if stamped:
        assert observed["hygiene"] == len(stamped), line
        assert (
            inventory["compatibility_changes"] == observed_total - observed["hygiene"]
        )
        assert inventory["compatibility_breaking"] == observed["breaking"]
        assert inventory["compatibility_source_breaks"] == observed["source_breaks"]
        assert inventory["compatibility_risk"] == observed["risk_changes"]
        assert inventory["compatibility_compatible"] == observed["compatible_additions"]
        assert "; hygiene:" in line


#: One representative per outcome bucket (registry-derived, deterministic).
_REPRESENTATIVE = {
    "breaking": next(
        k for k in _KIND_VALUES if REGISTRY.get(k).default_verdict is Verdict.BREAKING
    ),
    "source_breaks": next(
        k for k in _KIND_VALUES if REGISTRY.get(k).default_verdict is Verdict.API_BREAK
    ),
    "risk_changes": next(
        k
        for k in _KIND_VALUES
        if REGISTRY.get(k).default_verdict is Verdict.COMPATIBLE_WITH_RISK
    ),
    "compatible_additions": sorted(REGISTRY.addition_kinds())[0],
    "quality_issues": next(
        k
        for k in _KIND_VALUES
        if REGISTRY.get(k).default_verdict is Verdict.COMPATIBLE
        and k not in REGISTRY.addition_kinds()
        and k not in REGISTRY.informational_kinds()
    ),
    "informational": sorted(REGISTRY.informational_kinds())[0],
}


@pytest.mark.parametrize("bucket", list(_REPRESENTATIVE))
@pytest.mark.parametrize(
    "stamp", _STAMPS, ids=lambda s: "unstamped" if s is None else s.value
)
@pytest.mark.parametrize("scored", [True, False], ids=["scored", "not-evaluated"])
def test_each_bucket_on_its_own_states_itself_and_adds_up(
    bucket: str, stamp: CrossSourceEvolution | None, scored: bool
) -> None:
    """Exhaustive over (bucket x standing-inventory state x evaluation state)."""
    _assert_headline_matches_report(
        [_change(_REPRESENTATIVE[bucket], stamp, scored=scored)]
    )


def test_every_bucket_together_adds_up() -> None:
    """All six buckets and an unscored finding in one run."""
    changes = [_change(kind, None, scored=True) for kind in _REPRESENTATIVE.values()]
    changes.append(_change(_REPRESENTATIVE["breaking"], None, scored=False))
    _assert_headline_matches_report(changes)


@pytest.mark.parametrize("kind", _HYGIENE_KINDS)
@pytest.mark.parametrize("stamp", _STAMPED, ids=lambda s: s.value)
@pytest.mark.parametrize("scored", [True, False], ids=["scored", "not-evaluated"])
def test_each_hygiene_kind_is_its_own_clause_and_counts_in_the_total(
    kind: str, stamp: CrossSourceEvolution, scored: bool
) -> None:
    """Every disposition kind, in every standing state, is counted in the total
    and named in the ``header/binary consistency`` clause, alone and beside an
    observed compatibility finding."""
    change = _change(kind, stamp, scored=scored)
    _assert_headline_matches_report([change])
    parts, total = _parse_headline(stat_line(_result([change])))
    assert total == 1
    assert parts == {"hygiene": 1}

    observed = _change(_REPRESENTATIVE["breaking"], None, scored=True)
    _assert_headline_matches_report([change, observed])
    parts, total = _parse_headline(stat_line(_result([change, observed])))
    assert total == 2
    assert parts == {"breaking": 1, "hygiene": 1}


def test_the_case09_shape_states_its_hygiene_finding_in_the_total() -> None:
    """Regression shape: ``2 breaking, 2 compatible`` plus one persistent
    ``exported_not_public`` is 5 changes in the JSON, so the line says 5."""
    changes = [
        _change(_REPRESENTATIVE["breaking"], None, scored=True),
        _change(_REPRESENTATIVE["breaking"], None, scored=True),
        _change(_REPRESENTATIVE["compatible_additions"], None, scored=True),
        _change(_REPRESENTATIVE["compatible_additions"], None, scored=True),
        _change("exported_not_public", CrossSourceEvolution.PERSISTENT, scored=True),
    ]
    _assert_headline_matches_report(changes)
    parts, total = _parse_headline(stat_line(_result(changes)))
    assert total == 5
    assert parts == {"breaking": 2, "compatible_additions": 2, "hygiene": 1}


@settings(max_examples=200, deadline=None)
@given(
    specs=st.lists(
        st.one_of(
            st.tuples(
                st.sampled_from(_HYGIENE_KINDS),
                st.sampled_from(_STAMPED),
                st.booleans(),
            ),
            st.tuples(st.sampled_from(_KIND_VALUES), st.none(), st.booleans()),
        ),
        min_size=1,
        max_size=12,
    )
)
def test_hygiene_disposition_change_sets_add_up_to_the_json_total(
    specs: list[tuple[str, CrossSourceEvolution | None, bool]],
) -> None:
    """Change sets drawn from the cross-source hygiene kinds, mixed with observed
    kinds: the headline total is the JSON ``total_changes`` and the parts sum
    to it."""
    _assert_headline_matches_report(
        [_change(kind, stamp, scored=scored) for kind, stamp, scored in specs]
    )


@settings(max_examples=200, deadline=None)
@given(
    specs=st.lists(
        st.tuples(
            st.sampled_from(_KIND_VALUES),
            st.sampled_from(_STAMPS),
            st.booleans(),
        ),
        max_size=12,
    )
)
def test_any_change_set_headline_adds_up_and_matches_json(
    specs: list[tuple[str, CrossSourceEvolution | None, bool]],
) -> None:
    _assert_headline_matches_report(
        [_change(kind, stamp, scored=scored) for kind, stamp, scored in specs]
    )


_RELEASE_KEYS = (
    "breaking",
    "source_breaks",
    "risk_changes",
    "compatible_additions",
    "quality_issues",
    "informational",
)


@settings(max_examples=150, deadline=None)
@given(
    members=st.lists(
        st.fixed_dictionaries({k: st.integers(0, 6) for k in _RELEASE_KEYS}),
        max_size=5,
    ),
    global_counts=st.one_of(
        st.none(),
        st.fixed_dictionaries({k: st.integers(0, 6) for k in _RELEASE_KEYS}),
    ),
)
def test_release_headline_adds_up_to_the_sum_of_its_members(
    members: list[dict[str, int]], global_counts: dict[str, int] | None
) -> None:
    line = format_release_oneline("BREAKING", members, release_global=global_counts)
    parts, total = _parse_headline(line)
    expected = dict.fromkeys(_RELEASE_KEYS, 0)
    for entry in [*members, *([global_counts] if global_counts else [])]:
        for key in _RELEASE_KEYS:
            expected[key] += entry[key]
    assert sum(parts.values()) == total, line
    assert total == sum(expected.values()), line
    assert parts == {k: v for k, v in expected.items() if v}, line


@pytest.mark.parametrize("bucket", list(_REPRESENTATIVE))
def test_release_global_findings_split_like_member_findings(bucket: str) -> None:
    """A bundle/matrix finding is bucketed the way a member finding is."""
    change = _change(_REPRESENTATIVE[bucket], None, scored=True)
    matrix = SimpleNamespace(changes=[change], policy=None, policy_file=None)
    counts = release_global_counts(None, matrix)
    assert counts[bucket] == 1, counts
    assert sum(counts[k] for k in _BUCKET_KEYS) == 1
    assert counts["total"] == 1


# --- Release one-line total against real member entries (CodeRabbit, PR #1828)
#
# The release one-line is folded from the entries the fan-out really writes:
# each member is projected by ``stamp_member_counts`` +
# ``add_member_review_summary`` (the same two calls ``ReleaseMemberEntryProjection``
# makes), and the oracle for the total is each member's own JSON
# ``summary.total_changes`` -- never a count the renderer derives for itself.


def _json_total(changes: list[Change]) -> int:
    return json.loads(to_json(_result(changes)))["summary"]["total_changes"]


def _release_member_entry(name: str, changes: list[Change]) -> dict[str, object]:
    result = DiffResult(
        old_version="1.0", new_version="2.0", library=name, changes=changes
    )
    entry: dict[str, object] = {"library": name, "verdict": "BREAKING"}
    stamp_member_counts(entry, result)
    add_member_review_summary(entry, result, None)
    return entry


def _release_line(entries: list[dict[str, object]]) -> str:
    return format_release_oneline(
        "BREAKING",
        entries,
        change_inventory=release_inventory_counters(entries),
    )


@pytest.mark.parametrize(
    "stamp",
    [None, CrossSourceEvolution.NOT_EVALUATED, CrossSourceEvolution.PERSISTENT],
    ids=["unstamped", "stamped-not-evaluated", "stamped-persistent"],
)
def test_release_line_states_an_unscored_finding_in_its_total(
    stamp: CrossSourceEvolution | None,
) -> None:
    """A finding contract evaluation left NOT_EVALUATED is counted in the total
    and named as ``not evaluated`` -- with or without a standing stamp. The
    release line used to drop it (``0 total``) or state a negative count
    (``-1 not evaluated``) beside the JSON's ``total_changes: 1``."""
    changes = [_change("func_removed", stamp, scored=False)]
    entry = _release_member_entry("libdemo.so", changes)

    line = _release_line([entry])
    parts, total = _parse_headline(line)

    assert total == _json_total(changes) == 1, line
    assert sum(parts.values()) == total, line
    expected = {"hygiene": 1} if stamp is not None else {"not_evaluated": 1}
    assert parts == expected, line
    assert "-" not in line.split(": ", 1)[1], line


def test_release_line_keeps_a_member_entry_without_an_inventory_block() -> None:
    """A release-support-promise entry carries bucket counts but no
    ``change_inventory`` block. Beside a member that does carry one, the
    inventory replaces the breakdown, so the block-less entry's break must be
    folded in, not silently dropped from both parts and total."""
    member = _release_member_entry(
        "libdemo.so",
        [
            _change("func_removed", None, scored=True),
            _change(
                "exported_not_public", CrossSourceEvolution.PERSISTENT, scored=True
            ),
        ],
    )
    promise = {
        "library": "libpromised.so",
        "verdict": "BREAKING",
        "breaking": 1,
        "source_breaks": 0,
        "risk_changes": 0,
        "compatible_additions": 0,
        "quality_issues": 0,
        "informational": 0,
    }

    parts, total = _parse_headline(_release_line([member, promise]))

    assert parts == {"breaking": 2, "hygiene": 1}
    assert total == 3


@settings(max_examples=100, deadline=None)
@given(
    members=st.lists(
        st.lists(
            st.tuples(
                st.sampled_from(_KIND_VALUES),
                st.sampled_from(_STAMPS),
                st.booleans(),
            ),
            max_size=6,
        ),
        max_size=4,
    )
)
def test_release_line_from_real_member_entries_adds_up_to_json(
    members: list[list[tuple[str, CrossSourceEvolution | None, bool]]],
) -> None:
    """Release lines built from real member projections (stamped hygiene in
    every state, scored and unscored findings, any kind) state the JSON
    population: the total is the sum of members' JSON ``total_changes`` and the
    parts are the one-line oracle over every change, as in the single-pair test."""
    entries: list[dict[str, object]] = []
    all_changes: list[Change] = []
    json_total = 0
    for index, specs in enumerate(members):
        changes = [_change(kind, stamp, scored=scored) for kind, stamp, scored in specs]
        entries.append(_release_member_entry(f"lib{index}.so", changes))
        all_changes.extend(changes)
        json_total += _json_total(changes)

    line = _release_line(entries)
    parts, total = _parse_headline(line)

    assert total == json_total == len(all_changes), line
    assert sum(parts.values()) == total, line
    assert parts == {
        k: v for k, v in _expected(all_changes, observed_only=True).items() if v
    }, line


#: A reclassify rule that demotes a removal whose symbol matches ``demoted_``
#: to COMPATIBLE. ``to: ignore`` is the policy spelling of COMPATIBLE.
_DEMOTE_REMOVALS = PolicyFile(
    reclassify=[
        ReclassifyRule(
            to_verdict=Verdict.COMPATIBLE,
            to="ignore",
            change_kind="func_removed",
            symbol_pattern="demoted_.*",
        )
    ]
)

_SUMMARY_BUCKETS = (
    "breaking",
    "source_breaks",
    "risk_changes",
    "compatible_additions",
    "quality_issues",
    "informational",
    "total_changes",
)


def _scoped_summary(changes: list[Change]) -> dict[str, object]:
    """The summary of a run with a supplied consumer scope (``used_by``) attached.

    An empty ``used_by`` is enough: the scoped fold recomputes the summary
    buckets from the rendered ``changes`` entries whenever the attribute is
    present, which is the path the policy-demoted bug lives on.
    """
    result = DiffResult(
        old_version="1.0",
        new_version="2.0",
        library="libdemo.so",
        changes=changes,
        policy_file=_DEMOTE_REMOVALS,
    )
    result.used_by = []  # type: ignore[attr-defined]
    return json.loads(to_json(result))["summary"]


def _full_summary(changes: list[Change]) -> dict[str, object]:
    """The oracle: the same run with no consumer scope attached."""
    result = DiffResult(
        old_version="1.0",
        new_version="2.0",
        library="libdemo.so",
        changes=changes,
        policy_file=_DEMOTE_REMOVALS,
    )
    return json.loads(to_json(result))["summary"]


def test_scoped_summary_buckets_match_full_summary_for_a_policy_demoted_break() -> None:
    """A removal the policy demotes to COMPATIBLE is a quality issue in the full
    summary (``build_summary`` classifies by effective category). Under a
    consumer scope the raw-kind check reclassified it as an addition, so the
    scoped summary counted one more addition and one less quality issue."""
    changes = [
        Change(ChangeKind.FUNC_REMOVED, "demoted_api", "demoted_api"),
        Change(ChangeKind.FUNC_REMOVED, "real_api", "real_api"),
        Change(ChangeKind.FUNC_ADDED, "new_api", "new_api"),
    ]
    full = _full_summary(changes)
    scoped = _scoped_summary(changes)

    assert full["quality_issues"] == 1
    for key in _SUMMARY_BUCKETS:
        assert scoped[key] == full[key], key


@settings(max_examples=100, deadline=None)
@given(
    specs=st.lists(
        st.tuples(
            st.sampled_from(["demoted_a", "demoted_b", "kept_a"]),
            st.sampled_from(["func_removed", "func_added", "func_params_changed"]),
        ),
        min_size=1,
        max_size=8,
    )
)
def test_scoped_summary_matches_full_summary_for_any_demoted_mix(
    specs: list[tuple[str, str]],
) -> None:
    changes = [Change(ChangeKind(kind), symbol, symbol) for symbol, kind in specs]
    full = _full_summary(changes)
    scoped = _scoped_summary(changes)
    for key in _SUMMARY_BUCKETS:
        assert scoped[key] == full[key], (key, specs)


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        ("not-a-dict", None),
        ({"kind": "func_removed"}, None),
        ({"kind": "func_removed", "severity": 4}, None),
        ({"kind": "func_removed", "severity": "breaking"}, "breaking"),
        ({"kind": "public_surface_grew", "severity": "compatible"}, "informational"),
        (
            {
                "kind": "func_removed",
                "severity": "compatible",
                "recommended_action": "review_recommended",
            },
            "quality_issues",
        ),
        ({"kind": "func_added", "severity": "compatible"}, "compatible_additions"),
    ],
)
def test_scoped_summary_bucket_for_each_entry_shape(
    entry: object, expected: str | None
) -> None:
    from abicheck.report.scoped_gate import _summary_bucket_for_entry

    assert _summary_bucket_for_entry(entry) == expected
