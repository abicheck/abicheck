"""A function an extractor generation retired is demoted as drift, never removed.

Generation 57 stopped recording castxml's compiler-declared ``__builtin_*``
functions. A stored castxml baseline from before 57 still holds one, so
against a fresh dump it reads as removed. The oracle is written out here, not
taken from the implementation: a finding is demoted exactly when the
function's name carries the reserved ``__builtin_`` prefix *and* generation 57
lies between the operands (either order); it stays in the report with the
extractor-drift rule, and every other finding keeps its verdict.
"""

from __future__ import annotations

import pytest

from abicheck.checker import compare
from abicheck.model import AbiSnapshot, Function, Param, Visibility
from abicheck.model.change import Change
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.model.change_catalog.registry import Verdict
from abicheck.policy.generation_drift_demotion import GENERATION_DRIFT_RULE
from abicheck.policy.retired_function_drift import demote_retired_function_drift
from abicheck.serialization import snapshot_from_dict, snapshot_to_dict
from abicheck.storage.generation_migrations import (
    FACT_GENERATIONS_KEY,
    retired_function_prefixes_between,
)

_RETIRING = 57
_NAMES = [
    ("__builtin_memcpy", True),
    ("__builtin_memset", True),
    ("memcpy", False),
    ("my__builtin_memcpy", False),
    ("_builtin_memcpy", False),
    ("__builtinmemcpy", False),
    ("_Z4keepi", False),
]
_GENERATION_PAIRS = [
    (20, 57, True),
    (56, 57, True),
    (57, 20, True),  # either order
    (1, 60, True),
    (57, 57, False),
    (57, 60, False),
    (20, 56, False),
    (56, 56, False),
]


def _expected(name_is_builtin: bool, old: int, new: int) -> bool:
    low, high = sorted((old, new))
    return name_is_builtin and low < _RETIRING <= high


@pytest.mark.parametrize(("old", "new", "straddles"), _GENERATION_PAIRS)
@pytest.mark.parametrize(("name", "is_builtin"), _NAMES)
def test_only_a_retired_name_between_straddling_operands_is_demoted(
    name: str, is_builtin: bool, old: int, new: int, straddles: bool
) -> None:
    change = Change(ChangeKind.FUNC_REMOVED, name, f"{name} removed")
    prefixes = retired_function_prefixes_between(old, new)
    assert bool(prefixes) == straddles
    (out,) = demote_retired_function_drift([change], prefixes, old, new)
    if _expected(is_builtin, old, new):
        assert out.effective_verdict is Verdict.COMPATIBLE_WITH_RISK
        assert out.modulation_rule == GENERATION_DRIFT_RULE
        assert "re-dump baseline" in (out.modulation_reason or "")
    else:
        assert out.effective_verdict is None
        assert out.modulation_rule is None


def test_a_finding_another_rule_already_modulated_is_left_alone() -> None:
    change = Change(ChangeKind.FUNC_REMOVED, "__builtin_memcpy", "removed")
    change.effective_verdict = Verdict.BREAKING
    change.modulation_rule = "some_prior_rule"
    demote_retired_function_drift([change], ("__builtin_",), 20, 57)
    assert change.effective_verdict is Verdict.BREAKING
    assert change.modulation_rule == "some_prior_rule"


def _function(name: str, mangled: str) -> Function:
    return Function(
        name=name,
        mangled=mangled,
        return_type="void *",
        params=[Param(name="d", type="void *")],
        visibility=Visibility.PUBLIC,
    )


def _stamped(functions: list[Function], extractor: int) -> AbiSnapshot:
    doc = snapshot_to_dict(
        AbiSnapshot(library="libx.so.1", version="1", functions=functions)
    )
    doc[FACT_GENERATIONS_KEY] = {"extractor": extractor, "resolver": 2, "source": 0}
    return snapshot_from_dict(doc)


def test_compare_keeps_the_builtin_removal_visible_but_not_gating() -> None:
    keep = _function("keep", "keep")
    builtin = _function("__builtin_memcpy", "__builtin_memcpy")
    old = _stamped([keep, builtin], _RETIRING - 1)
    new = _stamped([keep], _RETIRING)
    result = compare(old, new)
    builtin_changes = [c for c in result.changes if c.symbol == "__builtin_memcpy"]
    assert builtin_changes  # recorded, not disposed of
    assert all(c.modulation_rule == GENERATION_DRIFT_RULE for c in builtin_changes)
    assert result.verdict.name not in {"BREAKING", "API_BREAK"}


def test_compare_still_gates_a_real_removal_across_the_same_generations() -> None:
    keep = _function("keep", "keep")
    gone = _function("gone", "gone")
    builtin = _function("__builtin_memcpy", "__builtin_memcpy")
    old = _stamped([keep, gone, builtin], _RETIRING - 1)
    new = _stamped([keep], _RETIRING)
    result = compare(old, new)
    gone_changes = [c for c in result.changes if c.symbol == "gone"]
    assert gone_changes
    assert all(c.modulation_rule != GENERATION_DRIFT_RULE for c in gone_changes)
    assert result.verdict.name == "BREAKING"
