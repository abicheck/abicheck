"""Entity claims: the validator tells "f removed" from "f and g removed", and
catches a false attribution inside a correct BREAKING verdict.

Every answer here is a controlled, hand-written checker response (no build),
judged through the same ``reconcile`` the ABIForge runner uses. The oracle for
each case is the edit's meaning, written out in the test, never the
validator's own helper.
"""

from __future__ import annotations

import itertools
import re

import pytest

from abiforge.model.config import Toolchain, default_config
from abiforge.model.expect import (
    MISMATCH_CLASSES,
    PASSING_CLASSES,
    ExecTruth,
    Expectation,
    issue_keys,
    reconcile,
)
from abiforge.model.finding import EntityClaim, EntityRef, Finding
from abiforge.model.seeds import SEED_BY_NAME, SEEDS
from abiforge.operators import OP_BY_NAME, OPERATORS
from abiforge.run.witness import _builtin_only, candidates, changed_roles

_GCC = Toolchain("gcc", 0, "gcc", "g++")
_BROKE = ExecTruth(available=True, binary_break=True, signals=("run call",))
_FINE = ExecTruth(available=True)
_SONAME = Finding("SONAME_BUMP_RECOMMENDED", "DT_SONAME", impact="COMPATIBLE")


def _cfg(lang: str = "c", evidence: str = "headers"):
    return default_config(lang, _GCC).with_(evidence=evidence)


def _with_witness(exp: Expectation, *names: str) -> Expectation:
    """The expectation ``run_cell`` builds once nm confirms the witnesses."""
    from dataclasses import replace

    return replace(
        exp,
        claims=exp.claims
        + tuple(EntityClaim(EntityRef(n), required=False) for n in names),
    )


def _judge(exp, truth, verdict, findings, *, structured=True):
    kinds = frozenset(f.kind for f in findings)
    return reconcile(
        truth, exp, verdict, kinds, findings=findings if structured else None
    )


# --- the four controlled answers -------------------------------------------

_F = Finding("FUNC_REMOVED", "get_count", impact="BREAKING")
_G = Finding("FUNC_REMOVED", "add_ints", impact="BREAKING")
_OPQ_F = Finding("FUNC_REMOVED", "opq_f", impact="BREAKING")
_S_BREAK = Finding("TYPE_SIZE_CHANGED", "S", impact="BREAKING")
_S_NOTE = Finding("TYPE_FIELD_ADDED_COMPATIBLE", "S", impact="COMPATIBLE")


def _remove_f():
    return _with_witness(OP_BY_NAME["func_remove"].expect(_cfg()), "add_ints")


def _mixed():
    return _with_witness(
        OP_BY_NAME["func_removed_and_private_record_grew"].expect(_cfg()), "opq_g"
    )


#: (name, expectation factory, verdict, findings, class after, problem keys after)
ANSWERS = (
    ("a_correct", _remove_f, "BREAKING", (_F, _SONAME), "agree", frozenset()),
    (
        "b_unchanged_g_removed",
        _remove_f,
        "BREAKING",
        (_F, _G, _SONAME),
        "false-attribution",
        frozenset({"false-claim:add_ints:FUNC_REMOVED"}),
    ),
    (
        "c_breaking_blames_private_S",
        _mixed,
        "BREAKING",
        (_OPQ_F, _S_BREAK, _SONAME),
        "false-attribution",
        frozenset({"false-claim:S:TYPE_SIZE_CHANGED"}),
    ),
    (
        "d_permitted_internal_note",
        _mixed,
        "BREAKING",
        (_OPQ_F, _S_NOTE, _SONAME),
        "agree",
        frozenset(),
    ),
)


@pytest.mark.parametrize(
    ("name", "make", "verdict", "findings", "cls", "keys"),
    ANSWERS,
    ids=[a[0] for a in ANSWERS],
)
def test_controlled_answer_after(name, make, verdict, findings, cls, keys) -> None:
    got = _judge(make(), _BROKE, verdict, findings)
    assert got.cls == cls, got
    assert issue_keys(got.problems) == keys


@pytest.mark.parametrize(
    ("name", "make", "verdict", "findings", "cls", "keys"),
    ANSWERS,
    ids=[a[0] for a in ANSWERS],
)
def test_controlled_answer_before(name, make, verdict, findings, cls, keys) -> None:
    """The kind-set judgement (no structured findings) passes all four: (b) and
    (c) are the errors it cannot see."""
    got = _judge(make(), _BROKE, verdict, findings, structured=False)
    assert got.cls == "agree", got


def test_missing_required_break_still_detected() -> None:
    """Sensitivity: dropping the real break is still caught, with or without
    findings, and an attribution of the removal to the wrong function is caught
    even though the kind set is unchanged."""
    exp = _remove_f()
    assert _judge(exp, _BROKE, "NO_CHANGE", ()).cls == "false-clean"
    exp_mixed = _mixed()
    got = _judge(exp_mixed, _BROKE, "BREAKING", (_S_BREAK, _SONAME))
    assert got.cls in MISMATCH_CLASSES
    assert "missing:FUNC_REMOVED" in issue_keys(got.problems)
    # FUNC_REMOVED present in the kind set, but attributed to g, not f.
    wrong_entity = _judge(exp, _BROKE, "BREAKING", (_G, _SONAME))
    assert wrong_entity.cls == "false-attribution"
    assert issue_keys(wrong_entity.problems) == {
        "missing-claim:get_count",
        "false-claim:add_ints:FUNC_REMOVED",
    }


def test_compatible_pair_blocked_is_false_positive() -> None:
    exp = OP_BY_NAME["private_record_grew"].expect(_cfg())
    got = _judge(exp, _FINE, "BREAKING", (_S_BREAK,))
    assert got.cls == "false-positive"
    assert "false-claim:S:TYPE_SIZE_CHANGED" in issue_keys(got.problems)
    ok = _judge(exp, _FINE, "COMPATIBLE", (_S_NOTE,))
    assert ok.cls == "agree"


def test_privacy_claim_undecided_without_headers() -> None:
    """From DWARF or symbols alone, an opaque S reached by pointer cannot be
    told from a public client-allocated record: the claim is undecided
    (``evidence-limited``), never a pass and never a mismatch."""
    for ev in ("binary", "dwarf"):
        exp = OP_BY_NAME["private_record_grew"].expect(_cfg(evidence=ev))
        assert exp.undecided and not exp.claims
        got = _judge(exp, _FINE, "BREAKING", (_S_BREAK,))
        assert got.cls == "evidence-limited", got
        assert got.cls in PASSING_CLASSES


@pytest.mark.parametrize(
    ("symbol", "qualified", "matches"),
    [
        ("_Z1fi", "f", True),
        ("_Z1fd", "f", False),  # the sibling overload f(double)
        ("f", None, False),
    ],
)
def test_overload_pinned_to_mangled_symbol(symbol, qualified, matches) -> None:
    ref = EntityRef("f", symbol="_Z1fi")
    assert ref.matches(Finding("FUNC_REMOVED", symbol, qualified)) is matches


def test_unpinned_name_matches_symbol_or_qualified_name() -> None:
    ref = EntityRef("get_count")
    assert ref.matches(Finding("FUNC_REMOVED", "get_count"))
    assert ref.matches(Finding("FUNC_REMOVED", "_Z9get_countv", "get_count"))
    assert not ref.matches(Finding("FUNC_REMOVED", "_Z9get_countv"))


def test_issue_keys_round_trip_claim_problems() -> None:
    assert issue_keys(
        [
            "false claim TYPE_SIZE_CHANGED on S",
            "missing claim on _Z1fi: none of ['FUNC_REMOVED'] attributed to it",
        ]
    ) == {"false-claim:S:TYPE_SIZE_CHANGED", "missing-claim:_Z1fi"}


# --- witnesses: reusable and conservative -----------------------------------

_TYPE_WORD = re.compile(
    r"\b(?:struct|union|enum|class)\s+(\w+)|\btypedef\b[^;]*\b(\w+)\s*;"
)


def _types_named(text: str) -> set[str]:
    return {a or b for a, b in _TYPE_WORD.findall(text)}


def test_func_remove_witness_is_an_unchanged_public_function() -> None:
    cands = candidates(OP_BY_NAME["func_remove"], SEED_BY_NAME["c_core"])
    assert cands and cands[0].name == "add_ints"
    assert "get_count" not in {c.name for c in cands}


def test_mixed_edit_witness_avoids_the_grown_record() -> None:
    names = {
        c.name
        for c in candidates(
            OP_BY_NAME["func_removed_and_private_record_grew"],
            SEED_BY_NAME["c_opaque"],
        )
    }
    # opq_f is removed; s_new/s_sum/s_free name S, which the edit changes.
    assert names == {"opq_g"}


@pytest.mark.parametrize(
    ("op", "seed"),
    [
        (op, seed)
        for op, seed in itertools.product(OPERATORS, SEEDS)
        if op.applies_to(seed)
    ],
    ids=lambda x: getattr(x, "name", str(x)),
)
def test_witness_never_reaches_an_edited_entity(op, seed) -> None:
    """Over every (operator, seed): a witness is never in an edited role, never
    shares a name with an edited function (overloads), never exists for a
    rebuild or link edit, and -- when the edit touches type text -- names no
    type at all (a nested type may change it transitively)."""
    cands = candidates(op, seed)
    changed = changed_roles(op, seed)
    if op.rebuild is not None or any(r.startswith("build.") for r in changed):
        assert cands == ()
        return
    new = op.edit(seed.roles)
    edited_text = " ".join(
        t for r in changed for t in (*seed.roles.get(r, ("", "")), *new[r])
    )
    edited_fns = set(re.findall(r"(\w+)\s*\(", edited_text))
    for c in cands:
        assert c.role not in changed
        assert c.name not in edited_fns
        if re.search(
            r"\b(struct|union|enum|class|typedef|using|template)\b", edited_text
        ):
            assert not _types_named(c.prototype)
            assert not (
                _types_named(edited_text) & set(re.findall(r"\w+", c.prototype))
            )


@pytest.mark.parametrize(
    ("proto", "ok"),
    [
        (("int ", "int a, int b"), True),
        (("const char *", "const char *s"), True),
        (("double ", "struct point p, double f"), False),
        (("S *", "int a"), False),
        (("int ", "const S *s"), False),
        (("handle_t ", "int x"), False),
        (("", "int x"), False),
    ],
)
def test_builtin_only(proto, ok) -> None:
    assert _builtin_only(*proto) is ok
