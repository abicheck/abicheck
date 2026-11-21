"""The ``cxx_dialect`` operators: a rebuild under a newer ``-std=`` from identical source.

Their entity claims must pass the attribution a correct checker gives (seen
in the integration cells) and fail every wrong one: the break blamed on the
control function, on the declaration the *other* standard changes, or on
nothing.
"""

from __future__ import annotations

import pytest

from abiforge.model.config import STANDARD_BY_NAME
from abiforge.model.finding import Finding
from abiforge.model.seeds import SEED_BY_NAME
from abiforge.operators.dialect import DIALECT_OPERATORS

_OPS = {op.name: op for op in DIALECT_OPERATORS}
_NOEXCEPT = _OPS["dialect_cxx17_noexcept_in_function_type"]
_CHAR8 = _OPS["dialect_cxx20_char8_t_parameter"]


def _problems(op_name: str, findings: tuple[Finding, ...]) -> list[str]:
    op = _OPS[op_name]
    return [p for c in op.claims if (p := c.violation(findings, {})) is not None]


def _f(kind: str, symbol: str, name: str) -> Finding:
    return Finding(kind=kind, symbol=symbol, qualified_name=name)


# The attribution each evidence lane gave in the integration cells.
_RIGHT = {
    _NOEXCEPT.name: [
        (_f("FUNC_PARAMS_CHANGED", "_Z11dl_registerPFvvE", "dl_register"),),
        (_f("FUNC_VISIBILITY_CHANGED", "_Z11dl_registerPFvvE", "dl_register"),),
    ],
    _CHAR8.name: [
        (
            _f("CHAR8T_MIGRATION", "dl_take", "dl_take"),
            _f("FUNC_PARAMS_CHANGED", "_Z7dl_takeRKc", "dl_take"),
        ),
        (_f("FUNC_VISIBILITY_CHANGED", "_Z7dl_takeRKc", "dl_take"),),
    ],
}


@pytest.mark.parametrize(
    ("op_name", "findings"),
    [(n, f) for n, fs in _RIGHT.items() for f in fs],
)
def test_the_right_attribution_passes(
    op_name: str, findings: tuple[Finding, ...]
) -> None:
    assert _problems(op_name, findings) == []


@pytest.mark.parametrize(
    ("op_name", "findings", "needle"),
    [
        # Blamed on the control: the required claim is unmet and the control
        # carries a false break.
        (
            _NOEXCEPT.name,
            (_f("FUNC_REMOVED", "_Z7dl_keepi", "dl_keep"),),
            "false claim FUNC_REMOVED on dl_keep",
        ),
        # Blamed on the declaration only the other revision changes.
        (
            _NOEXCEPT.name,
            (
                _f("FUNC_PARAMS_CHANGED", "_Z11dl_registerPFvvE", "dl_register"),
                _f("FUNC_PARAMS_CHANGED", "_Z7dl_takeRKc", "dl_take"),
            ),
            "false claim FUNC_PARAMS_CHANGED on dl_take",
        ),
        (
            _CHAR8.name,
            (
                _f("FUNC_PARAMS_CHANGED", "_Z7dl_takeRKc", "dl_take"),
                _f("FUNC_REMOVED", "_Z11dl_registerPDoFvvE", "dl_register"),
            ),
            "false claim FUNC_REMOVED on dl_register",
        ),
        # The new name reported, not the old one an old consumer references.
        (
            _CHAR8.name,
            (_f("FUNC_ADDED", "_Z7dl_takeRKDu", "dl_take"),),
            "missing claim on _Z7dl_takeRKc",
        ),
        # Nothing reported at all.
        (_NOEXCEPT.name, (), "missing claim on _Z11dl_registerPFvvE"),
    ],
)
def test_a_wrong_attribution_fails(
    op_name: str, findings: tuple[Finding, ...], needle: str
) -> None:
    assert any(needle in p for p in _problems(op_name, findings))


def test_each_operator_only_changes_the_standard() -> None:
    """Identical source: the edit is the -std= flag, nothing else."""
    seed = SEED_BY_NAME["cxx_dialect"]
    for op, flag in ((_NOEXCEPT, "-std=c++17"), (_CHAR8, "-std=c++20")):
        changed = op.edit(dict(seed.roles))
        assert changed == {"build.cflags": (flag, "")}


@pytest.mark.parametrize(
    ("op", "allowed", "refused"),
    [
        (_NOEXCEPT, ("c++11", "c++14"), ("c++17", "c++20")),
        (_CHAR8, ("c++17",), ("c++14", "c++20", "c++23")),
    ],
)
def test_baseline_standard_precedes_the_revision(
    op: object, allowed: tuple[str, ...], refused: tuple[str, ...]
) -> None:
    """The baseline must be older than the revision the edit moves to."""
    (rng,) = op.standards  # type: ignore[attr-defined]
    years = {s: STANDARD_BY_NAME[s].year for s in (*allowed, *refused)}
    lo, hi = STANDARD_BY_NAME[rng.first].year, STANDARD_BY_NAME[rng.last].year
    assert all(lo <= years[s] <= hi for s in allowed)
    assert not any(lo <= years[s] <= hi for s in refused)
