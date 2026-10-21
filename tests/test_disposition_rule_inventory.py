"""ADR-067 P1: the configured-rule inventory, stated as invariants.

Every configured rule -- suppression, reclassify, acknowledgment -- has exactly
one audit row, zero-count rules included (I1). Each row's counts agree with an
independent first-match oracle (I2 suppression, I4 reclassify), its status is
the one the four-state rule dictates (I3), adding an unmatched rule moves
nothing but its own row (I5), folds conserve across members (I6), and the
``unused_rules`` gate fires on exactly the unused rows and never lowers an
exit code (I7).

The oracle is written here, not taken from the implementation: a rule matches
a finding by exact symbol, ``re.fullmatch`` on the symbol, or the change kind,
and its first match in file order wins. Nothing calls ``Suppression.matches``,
``ReclassifyRule.matches`` or ``SuppressionList.audit``.
"""

from __future__ import annotations

import re
import tempfile
from collections.abc import Sequence
from datetime import date
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind, Verdict
from abicheck.detector_registry import registry
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.policy.acknowledgment import AcknowledgmentList
from abicheck.policy.disposition_close import ledger_for
from abicheck.policy.disposition_rules import configured_rule_rows
from abicheck.policy.reclassify import ReclassifyRule
from abicheck.policy.rule_inventory import (
    STATUS_ACTIVE,
    STATUS_EXPIRED,
    STATUS_NOT_EVALUATED,
    STATUS_SHADOWED_ONLY,
    STATUS_UNUSED,
    RuleAuditRow,
    RuleRef,
)
from abicheck.policy.suppression.rules import Suppression, SuppressionList
from abicheck.policy_file import PolicyFile
from abicheck.report.disposition_audit import (
    compute_disposition_audit,
    fold_disposition_audits,
    fold_rule_rows,
    fold_rule_status,
)

PAST = date(2000, 1, 1)
FUTURE = date(2099, 1, 1)
NAMES = ("gone0", "gone1", "gone2")
#: A symbol no scenario removes; a rule naming it must read unused.
_NOPE = "_ZN2ns4nopeEv"


def _symbol(name: str) -> str:
    return f"_ZN2ns{len(name)}{name}Ev"


def _snapshots(removed: Sequence[str]) -> tuple[AbiSnapshot, AbiSnapshot]:
    """An old side with every ``NAMES`` function; new side drops *removed*."""
    old = AbiSnapshot(library="libinv", version="1.0")
    new = AbiSnapshot(library="libinv", version="2.0")
    for name in NAMES:
        fn = Function(
            name=_symbol(name),
            mangled=f"_ZN2ns{len(name)}{name}Ev",
            return_type="void",
            visibility=Visibility.PUBLIC,
        )
        old.declarations.functions.append(fn)
        if name not in removed:
            new.declarations.functions.append(fn)
    return old, new


def _removed_symbols(removed: Sequence[str]) -> list[str]:
    return [_symbol(name) for name in NAMES if name in removed]


# -- the oracle --------------------------------------------------------------


def _suppression_matches(rule: Suppression, symbol: str, today: date) -> bool:
    """Every finding in these scenarios is a ``func_removed`` on *symbol*."""
    if rule.expires is not None and today > rule.expires:
        return False
    if rule.symbol is not None and symbol != rule.symbol:
        return False
    if (
        rule.symbol_pattern is not None
        and re.fullmatch(rule.symbol_pattern, symbol) is None
    ):
        return False
    return rule.change_kind in (None, ChangeKind.FUNC_REMOVED.value)


def _oracle_suppression(
    rules: Sequence[Suppression], symbols: Sequence[str], today: date
) -> tuple[list[int], list[int]]:
    """Per-rule (disposed, shadowed) under first-match-wins in file order."""
    disposed = [0] * len(rules)
    shadowed = [0] * len(rules)
    for symbol in symbols:
        hits = [
            i for i, r in enumerate(rules) if _suppression_matches(r, symbol, today)
        ]
        if not hits:
            continue
        disposed[hits[0]] += 1
        for i in hits[1:]:
            shadowed[i] += 1
    return disposed, shadowed


#: Every registered detector name, read from the live registry -- the producer
#: set of a rule that names no change kind (it could match any kind).
_ALL_DETECTORS: frozenset[str] = frozenset(registry.detector_names)
#: The detectors that can emit ``func_removed`` on the scenarios' ELF-style
#: symbols: the ``functions`` detector (every run has it, no evidence gate) and
#: the PE and Mach-O symbol paths. Written here by hand from the source, not
#: read from the generated map.
_FUNC_REMOVED_PRODUCERS: frozenset[str] = frozenset({"functions", "pe", "macho"})


def _producers(rule_kind: str | None) -> frozenset[str]:
    """The oracle's candidate detectors for a rule that names *rule_kind*."""
    return _ALL_DETECTORS if rule_kind is None else _FUNC_REMOVED_PRODUCERS


def _oracle_status(
    disposed: int,
    shadowed: int,
    expires: date | None,
    *,
    producers: frozenset[str],
    ran: frozenset[str],
    today: date,
) -> str:
    """ADR-067 P1 addendum: a zero-match rule is ``unused`` iff one of the
    detectors that can produce its kinds ran; otherwise ``not_evaluated``."""
    if disposed > 0:
        return STATUS_ACTIVE
    if shadowed > 0:
        return STATUS_SHADOWED_ONLY
    if expires is not None and today > expires:
        return STATUS_EXPIRED
    return STATUS_UNUSED if producers & ran else STATUS_NOT_EVALUATED


def _ran(result: object) -> frozenset[str]:
    """The detectors that ran, read straight off the result's own records."""
    return frozenset(
        d.name
        for d in result.detector_results  # type: ignore[attr-defined]
        if d.enabled and not d.not_evaluated
    )


# -- scenario builders -------------------------------------------------------


def _rule(
    selector: tuple[str, str],
    *,
    expires: date | None = None,
    label: str | None = None,
    reason: str = "reviewed",
) -> Suppression:
    """A rule from one ``(selector kind, value)`` pair.

    ``change_kind`` is only a narrowing of another selector (the grammar rejects
    it alone), so a kind rule pairs it with a match-everything pattern.
    """
    kind, value = selector
    if kind == "symbol":
        return Suppression(symbol=value, reason=reason, label=label, expires=expires)
    if kind == "pattern":
        return Suppression(
            symbol_pattern=value, reason=reason, label=label, expires=expires
        )
    return Suppression(
        symbol_pattern=".*",
        change_kind=value,
        reason=reason,
        label=label,
        expires=expires,
    )


_SELECTORS: tuple[tuple[str, str], ...] = (
    ("symbol", _symbol("gone0")),
    ("symbol", _symbol("gone1")),
    ("symbol", _NOPE),
    ("pattern", r"_ZN2ns5gone[01]Ev"),
    ("pattern", r"_ZN2ns5gone.*"),
    ("pattern", r"_ZN2ns4nope.*"),
    ("kind", ChangeKind.FUNC_REMOVED.value),
)


def _scenario(
    removed: Sequence[str],
    rules: Sequence[Suppression],
) -> tuple[object, list[str]]:
    old, new = _snapshots(removed)
    result = compare(old, new, SuppressionList(list(rules)))
    return result, _removed_symbols(removed)


# -- I1 + I2 + I3 on hand-picked scenarios ------------------------------------


_HAND_PICKED: tuple[tuple[str, tuple[Suppression, ...]], ...] = (
    # Zero-match only: a rule that names nothing that was removed.
    ("zero_match", (_rule(("symbol", _NOPE)),)),
    # Overlap: two rules both match gone0; the first disposes, the second shadows.
    (
        "overlap",
        (_rule(("symbol", _symbol("gone0"))), _rule(("pattern", r"_ZN2ns5gone.*"))),
    ),
    # Duplicates: the same entry twice is two rules, not one.
    (
        "duplicates",
        (_rule(("pattern", r"_ZN2ns5gone.*")), _rule(("pattern", r"_ZN2ns5gone.*"))),
    ),
    # Expired: never matches, reads expired rather than unused.
    ("expired", (_rule(("kind", ChangeKind.FUNC_REMOVED.value), expires=PAST),)),
    # Mixed: a broad rule first, a narrow zero-match, a future-dated live one.
    (
        "mixed",
        (
            _rule(("kind", ChangeKind.FUNC_REMOVED.value), label="broad"),
            _rule(("symbol", _NOPE)),
            _rule(("symbol", _symbol("gone1")), expires=FUTURE),
        ),
    ),
)


@pytest.mark.parametrize("removed", [(), ("gone0",), ("gone0", "gone2"), NAMES])
@pytest.mark.parametrize(
    ("label", "rules"), _HAND_PICKED, ids=[name for name, _ in _HAND_PICKED]
)
def test_every_configured_rule_has_one_row_matching_the_oracle(
    label: str, rules: tuple[Suppression, ...], removed: tuple[str, ...]
) -> None:
    result, symbols = _scenario(removed, rules)
    today = date.today()
    audit = compute_disposition_audit(result)
    # The oracle's view of the detected population is the run's own.
    detected = sorted(r.symbol for r in ledger_for(result).records)
    assert detected == sorted(symbols)

    disposed, shadowed = _oracle_suppression(rules, symbols, today)
    ran = _ran(result)
    rows = audit.rules
    # I1: one row per configured rule, in file order, zero-count rows included.
    assert len(rows) == len(rules), label
    for index, row in enumerate(rows):
        assert row.entry_index == index
        # I2: counts equal the oracle's first-match credit and shadowing.
        assert row.matched_count == disposed[index], (label, index)
        assert row.shadowed_count == shadowed[index], (label, index)
        # I3: the status the per-rule four-state rule dictates.
        assert row.status == _oracle_status(
            disposed[index],
            shadowed[index],
            rules[index].expires,
            producers=_producers(rules[index].change_kind),
            ran=ran,
            today=today,
        ), (label, index)


def test_conservation_survives_zero_and_shadowed_rows() -> None:
    """Rows are reporting, never a second count of the same findings."""
    rules = _HAND_PICKED[1][1]  # overlap
    result, symbols = _scenario(NAMES, rules)
    audit = compute_disposition_audit(result)
    assert audit.detected_total == len(symbols)
    assert (
        sum(row.matched_count for row in audit.rules)
        == dict(audit.counts)["suppressed"]
    )


# -- I3: not_evaluated vs unused -----------------------------------------------


def test_an_unmatched_rule_is_unused_when_every_detector_ran() -> None:
    result, _ = _scenario((), (_rule(("symbol", _NOPE)),))
    _every_detector_ran(result)
    (row,) = compute_disposition_audit(result).rules
    assert row.status == STATUS_UNUSED


def _every_detector_ran(result: object) -> None:
    """Model a run in which no detector's evidence gate refused: every
    registered detector has an enabled, evaluated record. An empty list would
    model "nothing was recorded", which reads ``not_evaluated``, not this."""
    from abicheck.detectors import DetectorResult

    result.detector_results = [  # type: ignore[attr-defined]
        DetectorResult(name=name, changes_count=0) for name in registry.detector_names
    ]


def _every_detector_refused(result: object) -> None:
    """Model a run in which every detector's evidence gate refused."""
    from abicheck.detectors import DetectorResult

    result.detector_results = [  # type: ignore[attr-defined]
        DetectorResult(
            name=name,
            changes_count=0,
            enabled=False,
            coverage_gap="test: evidence absent",
            not_evaluated=True,
        )
        for name in registry.detector_names
    ]


def _apply_ran_state(result: object, state: str) -> None:
    """``as_compared`` keeps the comparison's own records; the other two force
    every detector to ran or to refused."""
    if state == "all_ran":
        _every_detector_ran(result)
    elif state == "all_refused":
        _every_detector_refused(result)


def test_an_unmatched_rule_is_not_evaluated_when_a_detector_did_not_run() -> None:
    """ADR-067 D3: 'matched nothing' is not proof when the check did not run."""
    from abicheck.detectors import DetectorResult

    result, _ = _scenario((), (_rule(("symbol", _NOPE)),))
    result.detector_results = [
        DetectorResult(
            name="pe",
            changes_count=0,
            enabled=False,
            coverage_gap="missing PE metadata",
            not_evaluated=True,
        )
    ]
    audit = compute_disposition_audit(result)
    (row,) = audit.rules
    assert row.status == STATUS_NOT_EVALUATED
    # And the gate must not treat it as unused.
    from abicheck.policy.unused_rules_gate import unused_rules_review

    review = unused_rules_review(audit.rules, mode="block")
    assert review.gate_contribution == 0
    assert review.unused == ()


# -- I4: reclassify matched vs changed ----------------------------------------


def _reclass(
    *,
    to: Verdict,
    symbol: str | None = None,
    pattern: str | None = None,
    kind: str | None = None,
    expires: date | None = None,
) -> ReclassifyRule:
    return ReclassifyRule(
        to_verdict=to,
        symbol=symbol,
        symbol_pattern=pattern if pattern is not None or symbol is not None else ".*",
        change_kind=kind,
        reason="policy",
        expires=expires,
    )


def _reclass_matches(rule: ReclassifyRule, symbol: str, today: date) -> bool:
    if rule.expires is not None and today > rule.expires:
        return False
    if rule.symbol is not None and symbol != rule.symbol:
        return False
    if (
        rule.symbol_pattern is not None
        and re.fullmatch(rule.symbol_pattern, symbol) is None
    ):
        return False
    return rule.change_kind in (None, ChangeKind.FUNC_REMOVED.value)


def test_reclassify_rows_separate_selector_hits_from_verdict_changes() -> None:
    """I4. ``FUNC_REMOVED`` is BREAKING under strict_abi: a rule to ``break`` matches
    but changes nothing (a no-op); a rule to ``ignore`` changes the verdict."""
    rules = [
        _reclass(to=Verdict.BREAKING, symbol=_symbol("gone0")),  # no-op on gone0
        _reclass(to=Verdict.COMPATIBLE, symbol=_symbol("gone1")),  # changes gone1
        _reclass(
            to=Verdict.COMPATIBLE, kind=ChangeKind.FUNC_REMOVED.value
        ),  # gone2, shadowed on gone0/gone1
    ]
    pf = PolicyFile(reclassify=rules)
    old, new = _snapshots(NAMES)
    result = compare(old, new, policy_file=pf)
    rows = compute_disposition_audit(result).reclassifications
    assert len(rows) == 3
    today = date.today()
    symbols = _removed_symbols(NAMES)
    hits = [0, 0, 0]
    changed = [0, 0, 0]
    for symbol in symbols:
        matched = [j for j, r in enumerate(rules) if _reclass_matches(r, symbol, today)]
        hits = [h + (j in matched) for j, h in enumerate(hits)]
        if matched and rules[matched[0]].to_verdict != Verdict.BREAKING:
            changed[matched[0]] += 1
    json_rows = compute_disposition_audit(result).to_dict()["reclassifications"]
    for j, row in enumerate(rows):
        assert row.selector_matched_count == hits[j], j
        assert row.matched_count == changed[j], j
        # `changed_count` is the JSON name for the same verdict-changing number.
        assert json_rows[j]["changed_count"] == changed[j], j
        assert row.shadowed_count == hits[j] - changed[j], j
    assert rows[0].status == STATUS_SHADOWED_ONLY  # matched gone0, changed nothing
    assert rows[1].status == STATUS_ACTIVE
    assert rows[2].status == STATUS_ACTIVE
    # The sum of changed decisions is the overlay total (no double count).
    assert (
        sum(row.matched_count for row in rows)
        == compute_disposition_audit(result).reclassified_total
    )


# -- I5: observational inertness ----------------------------------------------


def test_an_unmatched_rule_moves_only_its_own_row() -> None:
    base = [_rule(("symbol", _symbol("gone0"))), _rule(("pattern", r"_ZN2ns5gone.*"))]
    extra = _rule(("symbol", "_ZN2ns5otherEv"), label="inert")
    old, new = _snapshots(NAMES)
    before = compare(old, new, SuppressionList(list(base)))
    after = compare(old, new, SuppressionList([*base, extra]))
    a = compute_disposition_audit(before)
    b = compute_disposition_audit(after)
    assert b.detected_total == a.detected_total
    assert b.effective_total == a.effective_total
    assert b.counts == a.counts
    assert after.verdict == before.verdict
    assert [row.matched_count for row in b.rules[:2]] == [
        row.matched_count for row in a.rules
    ]
    (added,) = b.rules[2:]
    assert added.matched_count == 0
    assert added.label == "inert"


# -- I6: fold conservation across members --------------------------------------


def test_fold_keeps_one_row_per_rule_and_conserves_counts() -> None:
    rules = [
        _rule(("symbol", _symbol("gone0"))),
        _rule(("symbol", _symbol("gone0"))),  # identical twin
        _rule(("symbol", _NOPE)),  # zero-match
    ]
    first = compute_disposition_audit(
        compare(*_snapshots(NAMES), SuppressionList(rules))
    )
    second = compute_disposition_audit(
        compare(*_snapshots(("gone0",)), SuppressionList(rules))
    )
    folded = fold_disposition_audits([first, second])
    assert len(folded.rules) == 3, "no zero row lost, and the twins stay two rows"
    assert [row.entry_index for row in folded.rules] == [0, 1, 2]
    for index in range(3):
        assert folded.rules[index].matched_count == (
            first.rules[index].matched_count + second.rules[index].matched_count
        )
    assert sum(dict(folded.counts).values()) == folded.detected_total


@pytest.mark.parametrize(
    ("member_statuses", "released"),
    [
        # A member that could not evaluate the rule is ignored, so the release
        # is unused when every member that could evaluate it left it unused.
        ((STATUS_UNUSED, STATUS_NOT_EVALUATED), STATUS_UNUSED),
        ((STATUS_NOT_EVALUATED, STATUS_NOT_EVALUATED), STATUS_NOT_EVALUATED),
        ((STATUS_ACTIVE, STATUS_NOT_EVALUATED), STATUS_ACTIVE),
        # Every evaluating member must agree before the release reads unused.
        ((STATUS_UNUSED, STATUS_ACTIVE), STATUS_ACTIVE),
        ((STATUS_UNUSED, STATUS_UNUSED), STATUS_UNUSED),
        ((STATUS_UNUSED, STATUS_SHADOWED_ONLY), STATUS_SHADOWED_ONLY),
        ((STATUS_UNUSED, STATUS_EXPIRED), STATUS_EXPIRED),
        ((STATUS_EXPIRED, STATUS_NOT_EVALUATED), STATUS_EXPIRED),
        ((STATUS_SHADOWED_ONLY, STATUS_NOT_EVALUATED), STATUS_SHADOWED_ONLY),
    ],
)
def test_release_fold_ignores_members_that_could_not_evaluate(
    member_statuses: tuple[str, ...], released: str
) -> None:
    assert fold_rule_status(member_statuses) == released


_FOLD_TABLE: tuple[tuple[tuple[str, ...], str], ...] = (
    ((STATUS_UNUSED, STATUS_NOT_EVALUATED), STATUS_UNUSED),
    ((STATUS_NOT_EVALUATED, STATUS_NOT_EVALUATED), STATUS_NOT_EVALUATED),
    ((STATUS_ACTIVE, STATUS_NOT_EVALUATED), STATUS_ACTIVE),
    ((STATUS_UNUSED, STATUS_ACTIVE), STATUS_ACTIVE),
    ((STATUS_UNUSED, STATUS_UNUSED), STATUS_UNUSED),
    ((STATUS_UNUSED, STATUS_SHADOWED_ONLY), STATUS_SHADOWED_ONLY),
    ((STATUS_UNUSED, STATUS_EXPIRED), STATUS_EXPIRED),
    ((STATUS_EXPIRED, STATUS_NOT_EVALUATED), STATUS_EXPIRED),
    ((STATUS_SHADOWED_ONLY, STATUS_NOT_EVALUATED), STATUS_SHADOWED_ONLY),
)


@pytest.mark.parametrize(("member_statuses", "released"), _FOLD_TABLE)
def test_release_gate_and_release_row_agree_on_every_member_mix(
    member_statuses: tuple[str, ...], released: str
) -> None:
    """The exit contribution and the folded audit row read the same members.

    Each member is stamped the way ``member_compare`` stamps it: a configured
    rule, its evaluable key unless ``not_evaluated``, its unused key when
    ``unused``. The gate contributes ``1`` exactly when the row folds to
    ``unused``.
    """
    from abicheck.policy.release_exit_decision import release_unused_rules_contribution

    key = "suppression|shared.yml|0"
    members = []
    for status in member_statuses:
        entry: dict[str, object] = {
            "unused_rules_mode": "block",
            "configured_rule_keys": [key],
            "evaluable_rule_keys": [] if status == STATUS_NOT_EVALUATED else [key],
            "unused_rule_keys": [key] if status == STATUS_UNUSED else [],
        }
        members.append(entry)
    contribution = release_unused_rules_contribution(members)
    assert contribution == (1 if released == STATUS_UNUSED else 0)
    assert fold_rule_status(member_statuses) == released
    assert contribution == (
        1 if fold_rule_status(member_statuses) == STATUS_UNUSED else 0
    )


def test_release_fold_rows_read_an_unevaluated_member_as_no_evidence() -> None:
    """One library could evaluate the rule and left it unused; the other
    refused the detector that produces it. The release row is unused, and the
    counts still sum across members."""
    ref = RuleRef("suppression", "shared.yml", 0)

    def row(status: str) -> RuleAuditRow:
        return RuleAuditRow(
            ref=ref,
            rule_id="shared",
            label=None,
            reason=None,
            expires=None,
            status=status,
            matched_count=0,
            shadowed_count=0,
            selector_matched_count=0,
        )

    (folded,) = fold_rule_rows([[row(STATUS_UNUSED)], [row(STATUS_NOT_EVALUATED)]])
    assert folded.status == STATUS_UNUSED
    assert folded.ref == ref


# -- I7: the unused_rules gate ------------------------------------------------


def _gated(mode: str, rules: Sequence[Suppression], removed: Sequence[str] = ()):
    """A comparison under *mode* in which every detector ran (see
    :func:`_every_detector_ran`); the gate is defined over that state."""
    old, new = _snapshots(removed)
    pf = PolicyFile(unused_rules=mode)
    result = compare(old, new, SuppressionList(list(rules)), policy_file=pf)
    _every_detector_ran(result)
    return result


def test_block_contributes_one_when_a_rule_is_unused() -> None:
    from abicheck.policy.unused_rules_gate import unused_rules_exit_contribution

    result = _gated("block", [_rule(("symbol", _NOPE))])
    assert unused_rules_exit_contribution(result) == 1


@pytest.mark.parametrize("mode", ["allow", "warn"])
def test_warn_and_allow_list_the_row_but_never_move_the_exit(mode: str) -> None:
    from abicheck.policy.unused_rules_gate import unused_rules_exit_contribution

    result = _gated(mode, [_rule(("symbol", _NOPE))])
    assert unused_rules_exit_contribution(result) == 0
    review = compute_disposition_audit(result).unused_rules_review
    assert review is not None
    assert review["policy"] == mode
    assert len(review["unused"]) == 1  # listed, whatever the mode


def test_block_does_not_fire_when_every_rule_is_used() -> None:
    from abicheck.policy.unused_rules_gate import unused_rules_exit_contribution

    result = _gated("block", [_rule(("symbol", _symbol("gone0")))], removed=("gone0",))
    assert unused_rules_exit_contribution(result) == 0


def test_the_exit_fold_never_lowers_a_real_failure() -> None:
    from abicheck.policy.exit_decision import ExitReason, resolve_exit_decision

    decision = resolve_exit_decision(
        compatibility_contribution=4, unused_rules_contribution=1
    )
    assert decision.code == 4
    assert ExitReason.UNUSED_RULES not in decision.reasons
    clean = resolve_exit_decision(
        compatibility_contribution=0, unused_rules_contribution=1
    )
    assert clean.code == 1
    assert clean.reasons == (ExitReason.UNUSED_RULES,)


def test_the_policy_key_alone_sets_the_mode() -> None:
    """The gate is set only by ``unused_rules:`` in the policy document; the
    former ``--fail-on-unused-rules`` flag is gone, so no run escalates."""
    from abicheck.policy.unused_rules_gate import resolve_unused_rules_mode

    rules = [_rule(("symbol", _NOPE))]
    assert resolve_unused_rules_mode(_gated("allow", rules)) == "allow"
    assert resolve_unused_rules_mode(_gated("warn", rules)) == "warn"
    assert resolve_unused_rules_mode(_gated("block", rules)) == "block"
    # No policy document at all: nothing to escalate, so the mode is allow.
    no_policy, _ = _scenario((), rules)
    assert resolve_unused_rules_mode(no_policy) == "allow"


def test_the_release_fold_counts_a_rule_unused_only_when_every_member_left_it_unused() -> (
    None
):
    """Per-release union: a leg that exercises a rule keeps it from being unused."""
    from abicheck.policy.release_exit_decision import release_unused_rules_contribution

    used = {
        "configured_rule_keys": ["suppression||0"],
        "unused_rule_keys": [],
        "unused_rules_mode": "block",
    }
    unused = {
        "configured_rule_keys": ["suppression||0"],
        "unused_rule_keys": ["suppression||0"],
        "unused_rules_mode": "block",
    }
    assert release_unused_rules_contribution([used, unused]) == 0
    assert release_unused_rules_contribution([unused, unused]) == 1
    assert release_unused_rules_contribution([unused]) == 1
    # A member under `allow` stamps no rule keys, so it is not a carrier: the
    # block members decide, and the strictest mode is `block`.
    allow = {"unused_rules_mode": "allow"}
    assert release_unused_rules_contribution([unused, allow]) == 1
    # Under `allow` everywhere the gate is inert, whatever the members say.
    assert (
        release_unused_rules_contribution([{**unused, "unused_rules_mode": "allow"}])
        == 0
    )


# -- the hypothesis property (slow) --------------------------------------------


_selector = st.sampled_from(_SELECTORS)
_expires = st.sampled_from([None, PAST, FUTURE])
_label = st.sampled_from([None, "a", "b"])


@st.composite
def _rule_sets(draw: st.DrawFn) -> list[Suppression]:
    count = draw(st.integers(min_value=0, max_value=4))
    rules: list[Suppression] = []
    for _ in range(count):
        rules.append(
            _rule(
                draw(_selector),
                expires=draw(_expires),
                label=draw(_label),
            )
        )
    return rules


#: ``as_compared``: the comparison's own detector records. ``all_ran`` and
#: ``all_refused`` force the two extremes, so both ``unused`` and
#: ``not_evaluated`` are reached on every rule shape.
_ran_state = st.sampled_from(["as_compared", "all_ran", "all_refused"])


@pytest.mark.slow
@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    rules=_rule_sets(),
    removed=st.sets(st.sampled_from(NAMES)).map(
        lambda s: tuple(n for n in NAMES if n in s)
    ),
    ran_state=_ran_state,
)
def test_generated_rule_sets_match_the_oracle(
    rules: list[Suppression], removed: tuple[str, ...], ran_state: str
) -> None:
    result, symbols = _scenario(removed, rules)
    _apply_ran_state(result, ran_state)
    today = date.today()
    audit = compute_disposition_audit(result)
    disposed, shadowed = _oracle_suppression(rules, symbols, today)
    ran = _ran(result)
    assert len(audit.rules) == len(rules)  # I1, zero rows and duplicates included
    for index, row in enumerate(audit.rules):
        assert row.matched_count == disposed[index]  # I2
        assert row.shadowed_count == shadowed[index]
        assert row.status == _oracle_status(  # I3, per rule
            disposed[index],
            shadowed[index],
            rules[index].expires,
            producers=_producers(rules[index].change_kind),
            ran=ran,
            today=today,
        )
    assert (
        sum(row.matched_count for row in audit.rules)
        == dict(audit.counts)["suppressed"]
    )


def test_configured_rule_rows_reads_the_same_rows_the_audit_reports() -> None:
    """The gate and the report read one function; they cannot disagree."""
    result, _ = _scenario(NAMES, (_rule(("symbol", _NOPE)),))
    rows = configured_rule_rows(result, ledger=ledger_for(result))
    assert rows == compute_disposition_audit(result).rules


# -- I4: reclassify selector hits and verdict changes ------------------------

#: Every finding is a ``func_removed``, BREAKING by default, so ``to: ignore``
#: moves it and ``to: break`` matches it without moving it (a no-op).
_RECLASSIFY_SELECTORS: tuple[tuple[str, str], ...] = (
    ("symbol", _symbol("gone0")),
    ("symbol", _NOPE),
    ("pattern", r"_ZN2ns5gone.*"),
    ("pattern", r"_ZN2ns5gon[e1].*"),
)


def _reclassify_yaml(
    rules: Sequence[tuple[tuple[str, str], str, date | None, str | None]],
) -> str:
    if not rules:
        return "reclassify: []\n"
    lines = ["reclassify:"]
    for (field, value), to, expires, label in rules:
        key = "symbol" if field == "symbol" else "symbol_pattern"
        lines.append(f"  - {key}: {_yaml_str(value)}")
        lines.append(f"    to: {to}")
        if expires is not None:
            lines.append(f"    expires: {expires.isoformat()}")
        if label is not None:
            lines.append(f"    label: {label}")
    return "\n".join(lines) + "\n"


def _yaml_str(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _reclassify_matches(
    selector: tuple[str, str], expires: date | None, symbol: str, today: date
) -> bool:
    if expires is not None and today > expires:
        return False
    field, value = selector
    if field == "symbol":
        return symbol == value
    return re.fullmatch(value, symbol) is not None


def _oracle_reclassify(
    rules: Sequence[tuple[tuple[str, str], str, date | None, str | None]],
    symbols: Sequence[str],
    today: date,
) -> tuple[list[int], list[int]]:
    """Per-rule (selector hits, verdict changes) under first-match-wins.

    Only the first matching rule decides, and it changes the verdict only when
    its target is ``ignore``: the finding's base verdict is BREAKING.
    """
    hits = [0] * len(rules)
    changed = [0] * len(rules)
    for symbol in symbols:
        matched = [
            i
            for i, (selector, _to, expires, _label) in enumerate(rules)
            if _reclassify_matches(selector, expires, symbol, today)
        ]
        for i in matched:
            hits[i] += 1
        if matched and rules[matched[0]][1] == "ignore":
            changed[matched[0]] += 1
    return hits, changed


_rc_rule = st.tuples(
    st.sampled_from(_RECLASSIFY_SELECTORS),
    st.sampled_from(["ignore", "break"]),
    st.sampled_from([None, PAST, FUTURE]),
    st.sampled_from([None, "a", "b"]),
)


@pytest.mark.slow
@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    rules=st.lists(_rc_rule, min_size=0, max_size=4),
    removed=st.sets(st.sampled_from(NAMES)).map(
        lambda s: tuple(n for n in NAMES if n in s)
    ),
    ran_state=_ran_state,
)
def test_generated_reclassify_rule_sets_match_the_oracle(
    rules: list[tuple[tuple[str, str], str, date | None, str | None]],
    removed: tuple[str, ...],
    ran_state: str,
) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "policy.yml"
        path.write_text(_reclassify_yaml(rules), encoding="utf-8")
        policy = PolicyFile.load(path)
        old, new = _snapshots(removed)
        result = compare(old, new, policy_file=policy)
    _apply_ran_state(result, ran_state)
    today = date.today()
    audit = compute_disposition_audit(result)
    hits, changed = _oracle_reclassify(rules, _removed_symbols(removed), today)
    ran = _ran(result)
    assert len(audit.reclassifications) == len(rules)  # I1, zero rows included
    for index, row in enumerate(audit.reclassifications):
        assert row.selector_matched_count == hits[index]  # I4: selector hits
        assert row.matched_count == changed[index]  # I4: verdict changes
        # These reclassify selectors name no kind, so every detector is a candidate.
        assert row.status == _oracle_status(
            changed[index],
            hits[index] - changed[index],
            rules[index][2],
            producers=_ALL_DETECTORS,
            ran=ran,
            today=today,
        )
    assert audit.reclassified_total == sum(changed)


# -- acknowledgments: one row per configured record, zero-count included -----


def test_an_acknowledgment_that_covers_nothing_still_has_a_row(tmp_path: Path) -> None:
    acks = tmp_path / "acks.yml"
    acks.write_text(
        "version: 1\nacknowledgments:\n"
        "  - symbol: nope\n    change_kind: func_removed\n    reason: planned\n"
        f"  - symbol: {_symbol('gone0')}\n    change_kind: func_removed\n"
        "    reason: old waiver\n    expires: 2000-01-01\n",
        encoding="utf-8",
    )
    old, new = _snapshots(("gone0",))
    result = compare(old, new, acknowledgments=AcknowledgmentList.load(acks))
    unused, expired = compute_disposition_audit(result).acknowledgments
    # The record names func_removed, so its candidates are the func_removed
    # producers; the ``functions`` detector ran, so it is ``unused`` here. The
    # DWARF detector being refused does not matter: it is not a candidate.
    today = date.today()
    assert unused.matched_count == 0
    assert "nope" in unused.rule_id
    assert unused.status == _oracle_status(
        0,
        0,
        None,
        producers=_FUNC_REMOVED_PRODUCERS,
        ran=_ran(result),
        today=today,
    )
    assert unused.status == STATUS_UNUSED
    assert (expired.reason, expired.matched_count) == ("old waiver", 0)
    assert expired.status == STATUS_EXPIRED


def test_a_rule_that_expires_before_rendering_reads_expired_not_active(
    tmp_path: Path,
) -> None:
    """The compare-time stamp is refreshed at render time (ADR-067 P1).

    The rule matches at comparison time, so the ledger carries its stamp. It
    has expired by the time the audit is rendered, so it must read ``expired``
    with nothing disposed of, not ``active`` while the totals say zero.
    """
    from datetime import timedelta

    policy = tmp_path / "policy.yml"
    policy.write_text(
        "reclassify:\n"
        f"  - symbol: {_symbol('gone0')}\n"
        "    to: ignore\n"
        f"    expires: {date.today().isoformat()}\n",
        encoding="utf-8",
    )
    old, new = _snapshots(("gone0",))
    result = compare(old, new, policy_file=PolicyFile.load(policy))
    later = date.today() + timedelta(days=2)
    audit = compute_disposition_audit(result, today=later)
    (row,) = audit.reclassifications
    assert (row.status, row.matched_count) == (STATUS_EXPIRED, 0)
    assert audit.reclassified_total == 0
