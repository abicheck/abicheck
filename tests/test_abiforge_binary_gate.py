"""ADR-084 at the expectation: a binary-gated API_BREAK is COMPATIBLE_WITH_RISK
at binary or DWARF depth, and keeps API_BREAK once header evidence is present.

The oracle is the hand table below, not the derivation's own helper: each row
states the verdict a reader of ADR-084 expects per evidence set. Rows are kinds
the policy gates (``BINARY_GATED``) and one header-gated kind, which must keep
its verdict at binary depth (the cap does not reach it).
"""

from __future__ import annotations

import pytest

from abiforge.model.config import Toolchain, default_config
from abiforge.model.expect import Expectation, binary_depth_cap
from abiforge.operators import OP_BY_NAME

GCC = Toolchain("gcc", 13, "gcc", "g++")

# kind -> verdict per configuration: binary, dwarf, headers (stripped and not).
ORACLE = {
    "FIELD_BECAME_CONST": (
        "COMPATIBLE_WITH_RISK",
        "COMPATIBLE_WITH_RISK",
        "API_BREAK",
        "API_BREAK",
    ),
    "FIELD_ACCESS_CHANGED": (
        "COMPATIBLE_WITH_RISK",
        "COMPATIBLE_WITH_RISK",
        "API_BREAK",
        "API_BREAK",
    ),
    "TYPE_REMOVED": (
        "COMPATIBLE_WITH_RISK",
        "COMPATIBLE_WITH_RISK",
        "API_BREAK",
        "API_BREAK",
    ),
    "METHOD_ACCESS_CHANGED": (
        "COMPATIBLE_WITH_RISK",
        "COMPATIBLE_WITH_RISK",
        "API_BREAK",
        "API_BREAK",
    ),
    # Header-gated: no binary-depth cap; the verdict holds at every depth.
    "BASE_CLASS_ACCESS_NARROWED": ("API_BREAK", "API_BREAK", "API_BREAK", "API_BREAK"),
}

_CONFIGS = [
    ("binary", False),
    ("dwarf", False),
    ("headers", False),
    ("headers", True),
]


def _cfg(evidence: str, stripped: bool):
    return default_config("cxx", GCC).with_(evidence=evidence, stripped=stripped)


@pytest.mark.parametrize("kind", sorted(ORACLE))
def test_binary_depth_cap_matches_the_hand_table(kind: str) -> None:
    base = Expectation(
        expected=frozenset({kind}), verdict_min="API_BREAK", observable=True
    )
    for (evidence, stripped), want in zip(_CONFIGS, ORACLE[kind], strict=True):
        got = binary_depth_cap(base, _cfg(evidence, stripped)).verdict_min
        assert got == want, f"{kind} at {evidence} stripped={stripped}"


def test_the_oracle_rows_are_the_policy_gated_set() -> None:
    from abiforge.checkers.abicheck import binary_gated_kind_names

    gated = {k for k in ORACLE if k in binary_gated_kind_names()}
    assert gated == {k for k in ORACLE if ORACLE[k][0] == "COMPATIBLE_WITH_RISK"}


@pytest.mark.parametrize("op_name", ["field_became_const", "field_access_narrowed"])
def test_the_field_cells_expect_the_capped_verdict_at_dwarf(op_name: str) -> None:
    exp = OP_BY_NAME[op_name].expect(_cfg("dwarf", False))
    # The floor is capped; the ceiling stays API_BREAK, so a real source break is
    # not contradicted by the expectation.
    assert exp.verdict_min == "COMPATIBLE_WITH_RISK"
    assert exp.verdict_max == "API_BREAK"


def test_a_header_stripped_cell_keeps_api_break() -> None:
    exp = OP_BY_NAME["field_became_const"].expect(_cfg("headers", True))
    assert exp.verdict_min == "API_BREAK"


# --- ADR-084 at reconcile: a source-only break at binary depth -----------------

from abiforge.model.expect import (  # noqa: E402
    ExecTruth,
    Expectation as _Exp,
    reconcile,
)

# (verdict abicheck reports, disclosed?) -> class, for a source-only break with
# no header or source evidence. The four outcomes of the rule, written by hand.
RECONCILE_ORACLE = {
    ("COMPATIBLE", False): "false-clean",
    ("NO_CHANGE", False): "false-clean",
    ("COMPATIBLE_WITH_RISK", False): "false-clean",
    ("COMPATIBLE_WITH_RISK", True): "evidence-limited",
    ("API_BREAK", False): "agree",
    ("API_BREAK", True): "agree",
}
GATED_KINDS = (
    "FIELD_BECAME_CONST",
    "FIELD_ACCESS_CHANGED",
    "TYPE_REMOVED",
    "METHOD_ACCESS_CHANGED",
)
SOURCE_ONLY = ExecTruth(available=True, binary_break=False, source_break=True)


@pytest.mark.parametrize("kind", GATED_KINDS)
@pytest.mark.parametrize(("verdict", "disclosed"), sorted(RECONCILE_ORACLE))
def test_source_only_break_outcomes_at_binary_depth(
    kind: str, verdict: str, disclosed: bool
) -> None:
    exp = _Exp(
        expected=frozenset({kind}),
        verdict_min="COMPATIBLE_WITH_RISK",
        verdict_max="API_BREAK",
        observable=True,
    )
    got = reconcile(
        SOURCE_ONLY,
        exp,
        verdict,
        frozenset({kind}),
        disclosed=frozenset({kind}) if disclosed else frozenset(),
        evidence=frozenset({"B", "D"}),
    ).cls
    assert got == RECONCILE_ORACLE[(verdict, disclosed)]


def test_header_evidence_keeps_the_existing_rule() -> None:
    """With H in the evidence the ADR-084 rule is off: the disclosed downgrade
    is still a clean verdict against a source break, so it is a false clean."""
    exp = _Exp(
        expected=frozenset({"FIELD_BECAME_CONST"}),
        verdict_min="COMPATIBLE_WITH_RISK",
        verdict_max="API_BREAK",
        observable=True,
    )
    got = reconcile(
        SOURCE_ONLY,
        exp,
        "COMPATIBLE_WITH_RISK",
        frozenset({"FIELD_BECAME_CONST"}),
        disclosed=frozenset({"FIELD_BECAME_CONST"}),
        evidence=frozenset({"B", "H"}),
    ).cls
    assert got == "false-clean"
