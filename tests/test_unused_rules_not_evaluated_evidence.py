"""ADR-067 P1 addendum 2: which rules read ``unused`` vs ``not_evaluated`` per
evidence level, and the ``not_evaluated`` warning under ``warn``/``block``.

The ran-detector sets below are the ones real gcc-built comparisons produced
(``run_compare`` on a C library: ELF-only, ELF+headers, ELF+DWARF+headers),
recorded as the complement of what did *not* run. The expected statuses are a
hand-written table -- the oracle -- not a call into ``candidate_detectors``.
"""

from __future__ import annotations

import itertools
from datetime import date

import pytest

from abicheck.policy import rule_inventory as ri
from abicheck.policy.detector_kind_map import DETECTOR_KINDS
from abicheck.policy.rule_inventory import (
    ConfiguredRule,
    RuleCounts,
    RuleRef,
    build_rule_rows,
)
from abicheck.policy.unused_rules_gate import (
    render_not_evaluated_rules,
    unused_rules_review,
)

TODAY = date(2026, 10, 21)

#: Detectors that did NOT run in each recorded real comparison.
_NOT_RUN = {
    "elf-only": {
        "advanced_dwarf",
        "dwarf",
        "kabi",
        "macho",
        "pe",
        "python_api",
        "python_ext",
        "sycl",
        "vtable_layout",
    },
    "elf+headers": {
        "advanced_dwarf",
        "dwarf",
        "fingerprint_renames",
        "kabi",
        "macho",
        "pe",
        "python_api",
        "python_ext",
        "sycl",
    },
    "elf+dwarf+headers": {
        "fingerprint_renames",
        "kabi",
        "macho",
        "pe",
        "python_api",
        "python_ext",
        "sycl",
    },
}
_RAN = {
    level: frozenset(DETECTOR_KINDS) - frozenset(nr) for level, nr in _NOT_RUN.items()
}

U, N = "unused", "not_evaluated"
#: kind -> expected status per level with nothing observed. Hand-derived:
#: struct_size_changed / struct_field_type_changed come only from the DWARF
#: detector; public_* kinds come from pipeline steps, never a detector.
_EXPECTED = {
    "func_removed": (U, U, U),
    "func_removed_elf_only": (U, U, U),
    "symbol_size_changed": (U, U, U),
    "type_size_changed": (U, U, U),
    "enum_member_added": (U, U, U),
    "needed_added": (U, U, U),
    "struct_size_changed": (N, N, U),
    "struct_field_type_changed": (N, N, U),
    "calling_convention_changed": (U, U, U),  # functions detector also emits it
    "vtable_symbol_identity_changed": (U, U, U),
    "secondary_vtable_group_changed": (N, U, U),
    "kabi_crc_changed": (N, N, N),
    "public_surface_shrank": (N, N, N),
    "public_macro_removed": (N, N, N),
}
_LEVELS = ("elf-only", "elf+headers", "elf+dwarf+headers")


def _rule(kind: str | None, index: int = 0) -> ConfiguredRule:
    return ConfiguredRule(
        ref=RuleRef(ri.KIND_RECLASSIFY, "policy.yml", index),
        rule_id=f"rule-{kind}",
        change_kinds=None if kind is None else frozenset({kind}),
    )


def _status(
    kind: str | None, ran: frozenset[str], observed: frozenset[str] = frozenset()
) -> str:
    return ri.rule_status(
        _rule(kind),
        RuleCounts(),
        today=TODAY,
        ran_detectors=ran,
        observed_kinds=observed,
    )


@pytest.mark.parametrize(("kind", "level"), list(itertools.product(_EXPECTED, _LEVELS)))
def test_status_table_over_evidence_levels(kind: str, level: str) -> None:
    expected = _EXPECTED[kind][_LEVELS.index(level)]
    assert _status(kind, _RAN[level]) == expected


@pytest.mark.parametrize("level", _LEVELS)
@pytest.mark.parametrize(
    "kind",
    [
        "public_surface_shrank",
        "public_macro_removed",
        "kabi_crc_changed",
        "struct_size_changed",
    ],
)
def test_an_observed_kind_proves_its_producer_ran(kind: str, level: str) -> None:
    # Positive control: the same rule reads unused once the run recorded a
    # change of its kind (whatever disposed of it). Negative control: an
    # unrelated observed kind changes nothing.
    assert _status(kind, _RAN[level], frozenset({kind})) == U
    if _EXPECTED[kind][_LEVELS.index(level)] == N:
        assert _status(kind, _RAN[level], frozenset({"func_added"})) == N


def test_any_kind_rule_is_evaluated_whenever_any_detector_ran() -> None:
    assert _status(None, _RAN["elf-only"]) == U
    assert _status(None, frozenset()) == N
    # An observed kind is no proof for a kind-less rule: the observed-kind
    # rule needs a kind to compare against.
    assert _status(None, frozenset(), frozenset({"func_added"})) == N


def test_exhaustive_small_domain_against_set_oracle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every (ran subset, observed subset, rule kind) over a 3-detector map,
    against an oracle written directly from the rule's prose."""
    toy = {"a": frozenset({"k1", "k2"}), "b": frozenset({"k2"}), "c": frozenset({"k3"})}
    monkeypatch.setattr(ri, "DETECTOR_KINDS", toy)
    kinds = ["k1", "k2", "k3", "k4"]  # k4: no detector emits it
    dets = sorted(toy)
    for r in range(len(dets) + 1):
        for ran in itertools.combinations(dets, r):
            for o in range(3):
                for observed in itertools.combinations(kinds, o):
                    for kind in kinds:
                        producers = {d for d in dets if kind in toy[d]}
                        oracle = U if (producers & set(ran)) or kind in observed else N
                        got = _status(kind, frozenset(ran), frozenset(observed))
                        assert got == oracle, (kind, ran, observed)


class TestMissingEvidence:
    def test_dwarf_only_rule_names_the_dwarf_detector_and_its_gate_reason(self) -> None:
        (row,) = build_rule_rows(
            [_rule("struct_size_changed")],
            {},
            today=TODAY,
            ran_detectors=_RAN["elf-only"],
            detector_gaps={"dwarf": "no DWARF debug info on either side"},
        )
        assert row.status == N
        assert row.missing_evidence is not None
        assert "dwarf (no DWARF debug info on either side)" in row.missing_evidence

    def test_pipeline_kind_names_the_kind(self) -> None:
        (row,) = build_rule_rows(
            [_rule("public_macro_removed")],
            {},
            today=TODAY,
            ran_detectors=_RAN["elf+headers"],
        )
        assert row.status == N
        assert "no registered detector produces public_macro_removed" in (
            row.missing_evidence or ""
        )

    def test_only_not_evaluated_rows_carry_it(self) -> None:
        (row,) = build_rule_rows(
            [_rule("func_removed")], {}, today=TODAY, ran_detectors=_RAN["elf-only"]
        )
        assert row.status == U
        assert row.missing_evidence is None


class TestReviewWarning:
    def _rows(self):
        return build_rule_rows(
            [_rule("func_removed", 0), _rule("struct_size_changed", 1)],
            {},
            today=TODAY,
            ran_detectors=_RAN["elf-only"],
        )

    @pytest.mark.parametrize("mode", ["warn", "block"])
    def test_warn_and_block_list_not_evaluated_without_gating_on_it(
        self, mode: str
    ) -> None:
        review = unused_rules_review(self._rows(), mode=mode)
        assert [e["entry_index"] for e in review.not_evaluated] == [1]
        assert "dwarf" in str(review.not_evaluated[0]["missing_evidence"])
        # Gate reacts to the unused rule only.
        assert review.gate_contribution == (1 if mode == "block" else 0)
        lines = render_not_evaluated_rules(review.to_dict(), 10)
        assert lines and "rules not evaluated" in lines[0]
        assert "rule-struct_size_changed" in "\n".join(lines)

    def test_block_with_only_not_evaluated_rules_does_not_gate(self) -> None:
        rows = build_rule_rows(
            [_rule("struct_size_changed")],
            {},
            today=TODAY,
            ran_detectors=_RAN["elf-only"],
        )
        review = unused_rules_review(rows, mode="block")
        assert review.gate_contribution == 0
        assert len(review.not_evaluated) == 1

    def test_allow_carries_no_not_evaluated_list(self) -> None:
        review = unused_rules_review(self._rows(), mode="allow")
        assert review.not_evaluated == ()
        assert "not_evaluated" not in review.to_dict()
        assert render_not_evaluated_rules(review.to_dict(), 10) == []

    def test_round_trip(self) -> None:
        from abicheck.policy.unused_rules_gate import UnusedRulesReview

        review = unused_rules_review(self._rows(), mode="warn")
        assert UnusedRulesReview.from_dict(review.to_dict()) == review
