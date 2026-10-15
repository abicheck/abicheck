"""ScopedGateFields.stamp: the one writer of the scoped-gate attributes.

Readers look these attributes up by name, so presence matters as much as
value: a field that a scope always produces (even as None) must be written,
and a conditional field a scope did not produce must not be written at all.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from abicheck.workflows.consumer_scope_gate import ScopedGateFields


def _fields(**overrides: Any) -> ScopedGateFields:
    base: dict[str, Any] = {
        "gate_scope": "used_by",
        "scoped_verdict": None,
        "scoped_exit_code": 0,
        "scoped_exit_code_scheme": "legacy",
        "scoped_relevant_finding_ids": frozenset(),
        "scoped_missing_labels": (),
        "scoped_only_changes": (),
    }
    base.update(overrides)
    return ScopedGateFields(**base)


def test_always_present_field_is_written_even_when_none() -> None:
    result = SimpleNamespace()

    _fields().stamp(result)

    # scoped_verdict is None when no consumer was supplied; readers still
    # expect the attribute to exist.
    assert result.scoped_verdict is None
    assert result.gate_scope == "used_by"


def test_conditional_fields_are_not_written_when_not_produced() -> None:
    result = SimpleNamespace()

    _fields().stamp(result)

    for name in (
        "used_by",
        "consumer_impact_summary",
        "required_symbols",
        "scoped_blocking_categories",
        "scoped_severity_counts",
    ):
        assert not hasattr(result, name), name


def test_produced_conditional_fields_are_written() -> None:
    result = SimpleNamespace()

    _fields(
        used_by=[{"app": "a"}],
        scoped_blocking_categories=("abi_breaking",),
        scoped_severity_counts={"abi_breaking": 1},
    ).stamp(result)

    assert result.used_by == [{"app": "a"}]
    assert result.scoped_blocking_categories == ("abi_breaking",)
    assert result.scoped_severity_counts == {"abi_breaking": 1}
    assert not hasattr(result, "required_symbols")
