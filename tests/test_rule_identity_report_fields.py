"""Report schema 6.6: one rule reads the same in every view.

An id-less rule's ``rule_id`` is an opaque ``rule-<hex>`` digest of its
canonical identity. Every row that carries that ``rule_id`` also carries the
readable ``rule_identity`` it digests, a user-``id:`` rule carries neither,
``suppression_audit.stale_rules`` joins to ``disposition_audit.rules`` through
the parallel ``stale_rule_ids``, and the release rationale names an id-less
rule by its label (or its identity) instead of only the digest.

The oracle for the identity is recomputed here from the rule's own selector
fields with ``json.dumps``/``hashlib``, not through ``rule_identity``'s helpers.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence

import pytest

from abicheck.checker import compare
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.policy.suppression.rules import Suppression, SuppressionList
from abicheck.reporter import to_json
from abicheck.semver import recommend_release
from abicheck.workflows.suppression_audit_attach import attach_suppression_audit

_NAMES = ("alpha", "beta", "gamma")


def _sym(name: str) -> str:
    return f"_ZN2ns{len(name)}{name}Ev"


def _snapshots() -> tuple[AbiSnapshot, AbiSnapshot]:
    """Old side exports every name; new side drops ``alpha`` only."""
    old = AbiSnapshot(library="libid", version="1.0")
    new = AbiSnapshot(library="libid", version="2.0")
    for name in _NAMES:
        fn = Function(
            name=_sym(name),
            mangled=_sym(name),
            return_type="void",
            visibility=Visibility.PUBLIC,
        )
        old.declarations.functions.append(fn)
        if name != "alpha":
            new.declarations.functions.append(fn)
    return old, new


def _oracle_identity(fields: dict[str, object]) -> str:
    return json.dumps(fields, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _oracle_id(fields: dict[str, object]) -> str:
    return "rule-" + hashlib.sha256(_oracle_identity(fields).encode()).hexdigest()[:12]


def _report(rules: Sequence[Suppression]) -> tuple[dict[str, object], object]:
    supp = SuppressionList(list(rules))
    result = compare(*_snapshots(), supp)
    attach_suppression_audit(result, supp)
    return json.loads(to_json(result)), result


#: (rule kwargs, selector fields the identity must hold)
_CASES = [
    ({"symbol": _sym("alpha")}, {"symbol": _sym("alpha")}),
    ({"symbol": _sym("nope")}, {"symbol": _sym("nope")}),
    (
        {"symbol_pattern": r"_ZN2ns4nope.*", "change_kind": "func_removed"},
        {"symbol_pattern": r"_ZN2ns4nope.*", "change_kind": "func_removed"},
    ),
    (
        {"symbol": _sym("nope2"), "label": "weak-func-hidden"},
        {"symbol": _sym("nope2"), "label": "weak-func-hidden"},
    ),
]


@pytest.mark.parametrize(("kwargs", "fields"), _CASES)
def test_id_less_row_carries_the_identity_its_id_digests(
    kwargs: dict[str, object], fields: dict[str, object]
) -> None:
    doc, _ = _report([Suppression(reason="r", **kwargs)])  # type: ignore[arg-type]
    rows = doc["disposition_audit"]["rules"]  # type: ignore[index]
    assert len(rows) == 1
    row = rows[0]
    assert row["rule_identity"] == _oracle_identity(fields)
    assert row["rule_id"] == _oracle_id(fields)


def test_user_id_rule_has_no_rule_identity() -> None:
    doc, _ = _report([Suppression(symbol=_sym("nope"), reason="r", id="keep-nope")])
    (row,) = doc["disposition_audit"]["rules"]  # type: ignore[index]
    assert row["rule_id"] == "keep-nope"
    assert "rule_identity" not in row
    sa = doc["suppression_audit"]
    assert sa["stale_rules"] == ["keep-nope"]  # type: ignore[index]
    assert sa["stale_rule_ids"] == ["keep-nope"]  # type: ignore[index]


def test_stale_entries_join_to_audit_rows_by_rule_id() -> None:
    rules = [
        Suppression(symbol=_sym("alpha"), reason="used"),
        Suppression(symbol=_sym("nope"), reason="stale a"),
        Suppression(symbol_pattern=r"_ZN2ns3zzz.*", reason="stale b"),
        Suppression(symbol=_sym("nope3"), reason="stale c", id="named-stale"),
    ]
    doc, _ = _report(rules)
    sa = doc["suppression_audit"]
    rows = {r["rule_id"]: r for r in doc["disposition_audit"]["rules"]}  # type: ignore[index]
    ids = sa["stale_rule_ids"]  # type: ignore[index]
    assert len(ids) == len(sa["stale_rules"]) == 3  # type: ignore[index]
    for rid, text in zip(ids, sa["stale_rules"], strict=True):  # type: ignore[index]
        row = rows[rid]
        assert row["status"] == "unused"
        assert text == row.get("rule_identity", rid)
    used = _oracle_id({"symbol": _sym("alpha")})
    assert used not in ids
    # Ledger provenance of the disposed finding carries the same identity.
    supp_rows = doc["suppression"]["suppressed_changes"]  # type: ignore[index]
    assert supp_rows[0]["rule"]["rule_id"] == used
    assert supp_rows[0]["rule"]["rule_identity"] == rows[used]["rule_identity"]


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        (
            {"symbol": _sym("alpha"), "label": "weak-func-hidden"},
            lambda f: f"{_oracle_id(f)} (label: weak-func-hidden)",
        ),
        (
            {"symbol": _sym("alpha")},
            lambda f: f"{_oracle_id(f)} (identity: {_oracle_identity(f)})",
        ),
        ({"symbol": _sym("alpha"), "id": "mine"}, lambda f: "mine"),
    ],
)
def test_release_rationale_names_id_less_rule_readably(
    kwargs: dict[str, object], expected: object
) -> None:
    _, result = _report([Suppression(reason="r", **kwargs)])  # type: ignore[arg-type]
    fields = {k: v for k, v in kwargs.items() if k != "id"}
    rationale = recommend_release(result).rationale  # type: ignore[arg-type]
    assert f"(rule(s): {expected(fields)})" in rationale  # type: ignore[operator]
