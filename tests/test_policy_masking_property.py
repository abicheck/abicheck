# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Masking a finding never raises the exit code, and the audit names exactly
what was masked (ADR-067, "record before disposing").

Two policy mechanisms take a detected finding out of the gate: a suppression
rule (the finding moves to ``suppressed_changes``) and an acknowledgment under
``acknowledgments.breaks: accept`` (the break stays a break in the report but
stops gating). For **every** subset of the fixture's findings, under each
mechanism and each exit scheme, this module asserts:

* the exit code -- both the process owner's
  (``resolve_compare_exit_decision_with_abort_axes``) and the ``ReportDocument``
  ``exit`` block -- equals an oracle computed from the findings left in the
  gate, using the literal tables of ``docs/reference/exit-codes.md``; hence it
  is never above the unmasked run's, and masking more never raises it;
* the ``ReportDocument`` disposition audit and suppression ledger name exactly
  the masked findings -- no more, no fewer -- the detected total is unchanged,
  and every configured rule is listed with the count it actually matched.

``test_disposition_audit.py`` proves the ledger's counting identity over rule
shapes; this module proves the *exit* side of the same invariant and the
exact identity of what the audit reports, over the whole subset lattice.
"""

from __future__ import annotations

import itertools
import re

import pytest

from abicheck.checker import compare
from abicheck.model import AbiSnapshot, Function, Variable, Visibility
from abicheck.policy.acknowledgment import Acknowledgment, AcknowledgmentList
from abicheck.policy.effective_gate import EffectiveGate
from abicheck.policy.exit_decision_precedence import (
    resolve_compare_exit_decision_with_abort_axes,
)
from abicheck.policy.severity import SeverityConfig, SeverityLevel
from abicheck.policy.suppression.rules import Suppression, SuppressionList
from abicheck.report.document_builder import build_report_document

ERR, WARN, INFO = SeverityLevel.ERROR, SeverityLevel.WARNING, SeverityLevel.INFO

#: The fixture's findings: symbol -> (legacy exit when it gates, severity
#: category). Legacy: BREAKING -> 4, a compatible addition -> 0
#: (docs/reference/exit-codes.md "Legacy exit codes").
_FINDINGS = {
    "gone": (4, "abi_breaking"),
    "retyped": (4, "abi_breaking"),
    "vgone": (4, "abi_breaking"),
    "added": (0, "addition"),
}
#: The ``ChangeKind`` value each fixture finding is detected as.
_KIND = {
    "gone": "func_removed",
    "retyped": "func_return_changed",
    "vgone": "var_removed",
    "added": "func_added",
}
_BREAKS = tuple(s for s, (legacy, _) in _FINDINGS.items() if legacy == 4)
_CATEGORY_EXIT = {
    "abi_breaking": 4,
    "potential_breaking": 2,
    "quality_issues": 1,
    "addition": 1,
}

_SCHEMES: dict[str, SeverityConfig | None] = {
    "legacy": None,
    "default": SeverityConfig(),
    "strict": SeverityConfig(
        abi_breaking=ERR, potential_breaking=ERR, quality_issues=ERR, addition=ERR
    ),
    "additions_only": SeverityConfig(
        abi_breaking=INFO, potential_breaking=WARN, quality_issues=WARN, addition=ERR
    ),
}


def _fn(name: str, ret: str = "void") -> Function:
    return Function(
        name=name, mangled=name, return_type=ret, visibility=Visibility.PUBLIC
    )


def _pair() -> tuple[AbiSnapshot, AbiSnapshot]:
    old = AbiSnapshot(
        library="libmask.so",
        version="1.0",
        functions=[_fn("gone"), _fn("keep"), _fn("retyped")],
        variables=[
            Variable(
                name="vgone", mangled="vgone", type="int", visibility=Visibility.PUBLIC
            )
        ],
    )
    new = AbiSnapshot(
        library="libmask.so",
        version="2.0",
        functions=[_fn("keep"), _fn("retyped", "int"), _fn("added")],
    )
    return old, new


def _oracle_exit(scheme: SeverityConfig | None, gating: set[str]) -> int:
    if scheme is None:
        return max((_FINDINGS[s][0] for s in gating), default=0)
    return max(
        (
            _CATEGORY_EXIT[_FINDINGS[s][1]]
            for s in gating
            if getattr(scheme, _FINDINGS[s][1]) is SeverityLevel.ERROR
        ),
        default=0,
    )


def _subsets(items: tuple[str, ...]) -> list[frozenset[str]]:
    return [
        frozenset(c)
        for n in range(len(items) + 1)
        for c in itertools.combinations(items, n)
    ]


def _codes(result, scheme: SeverityConfig | None) -> tuple[int, dict[str, object]]:
    process = resolve_compare_exit_decision_with_abort_axes(
        result, EffectiveGate.from_severity(scheme)
    ).code
    doc = build_report_document(result, severity_config=scheme).to_mapping()
    exit_block = doc["exit"]
    assert isinstance(exit_block, dict)
    assert exit_block["code"] == process
    return process, doc


def test_fixture_detects_exactly_the_tabled_findings() -> None:
    result = compare(*_pair())
    assert {(c.symbol, c.kind.value) for c in result.changes} == set(_KIND.items())


# ---------------------------------------------------------------------------
# Suppression
# ---------------------------------------------------------------------------


def _per_symbol_rules(masked: frozenset[str]) -> SuppressionList:
    rules = [
        Suppression(
            symbol=s, reason=f"mask {s}", label=f"mask-{s}", allow_public_break=True
        )
        for s in sorted(masked)
    ]
    # A rule matching nothing: the audit must still list it, at zero.
    rules.append(Suppression(symbol="never_present", reason="unused", label="unused"))
    return SuppressionList(rules)


def _pattern_rule(masked: frozenset[str]) -> SuppressionList | None:
    if not masked:
        return None
    alternation = "|".join(re.escape(s) for s in sorted(masked))
    return SuppressionList(
        [
            Suppression(
                symbol_pattern=f"^({alternation})$",
                reason="mask by pattern",
                label="pattern",
                allow_public_break=True,
            )
        ]
    )


@pytest.fixture(scope="module")
def pair() -> tuple[AbiSnapshot, AbiSnapshot]:
    return _pair()


@pytest.mark.parametrize("scheme_name", list(_SCHEMES))
@pytest.mark.parametrize("mechanism", ["per_symbol", "pattern"])
def test_suppression_masking_never_raises_exit_and_is_audited_exactly(
    pair: tuple[AbiSnapshot, AbiSnapshot], scheme_name: str, mechanism: str
) -> None:
    scheme = _SCHEMES[scheme_name]
    old, new = pair
    by_mask: dict[frozenset[str], int] = {}
    for masked in _subsets(tuple(_FINDINGS)):
        rules = (
            _per_symbol_rules(masked)
            if mechanism == "per_symbol"
            else _pattern_rule(masked)
        )
        result = compare(old, new, rules)
        code, doc = _codes(result, scheme)

        assert code == _oracle_exit(scheme, set(_FINDINGS) - masked), masked
        by_mask[masked] = code

        # The masked findings, exactly, are in the suppression ledger ...
        suppression = doc["suppression"]
        assert isinstance(suppression, dict)
        assert {e["symbol"] for e in suppression["suppressed_changes"]} == masked
        assert suppression["suppressed_count"] == len(masked)
        # ... and nowhere in the visible changes.
        assert {c["symbol"] for c in doc["changes"]} == set(_FINDINGS) - masked

        audit = doc["disposition_audit"]
        assert isinstance(audit, dict)
        assert audit["detected_total"] == len(_FINDINGS)
        assert audit["counts"]["suppressed"] == len(masked)
        assert sum(audit["counts"].values()) == len(_FINDINGS)
        matched = {row["label"]: row["matched_count"] for row in audit["rules"]}
        if mechanism == "per_symbol":
            assert matched == {**{f"mask-{s}": 1 for s in masked}, "unused": 0}
        else:
            assert matched == ({"pattern": len(masked)} if masked else {})

    unmasked = by_mask[frozenset()]
    for masked, code in by_mask.items():
        assert code <= unmasked
        for extra in set(_FINDINGS) - masked:
            assert by_mask[masked | {extra}] <= code, (masked, extra)


# ---------------------------------------------------------------------------
# Acknowledgment under ``acknowledgments.breaks: accept``
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("scheme_name", list(_SCHEMES))
def test_accepted_acknowledgment_never_raises_exit_and_is_audited_exactly(
    pair: tuple[AbiSnapshot, AbiSnapshot], scheme_name: str
) -> None:
    scheme = _SCHEMES[scheme_name]
    old, new = pair
    by_ack: dict[frozenset[str], int] = {}
    for acked in _subsets(tuple(_FINDINGS)):
        acks = AcknowledgmentList(
            [
                Acknowledgment(symbol=s, change_kind=_KIND[s], reason=f"planned {s}")
                for s in sorted(acked)
            ]
        )
        result = compare(old, new, acknowledgments=acks, acknowledgment_breaks="accept")
        code, doc = _codes(result, scheme)
        accepted = acked & set(_BREAKS)

        # `accept` takes acknowledged *breaks* out of the gate; an
        # acknowledged addition still gates.
        assert code == _oracle_exit(scheme, set(_FINDINGS) - accepted), acked
        by_ack[acked] = code

        audit = doc["disposition_audit"]
        assert isinstance(audit, dict)
        assert {row["symbol"] for row in audit["accepted_breaks"]} == accepted
        assert audit["acknowledged_total"] == len(acked)
        assert audit["detected_total"] == len(_FINDINGS)
        matched = {
            row["reason"]: row["matched_count"] for row in audit["acknowledgments"]
        }
        assert matched == {f"planned {s}": 1 for s in acked}
        # An accepted break is still a break in the report: nothing vanishes.
        assert {c["symbol"] for c in doc["changes"]} == set(_FINDINGS)

    unacked = by_ack[frozenset()]
    for acked, code in by_ack.items():
        assert code <= unacked
        for extra in set(_FINDINGS) - acked:
            assert by_ack[acked | {extra}] <= code, (acked, extra)
