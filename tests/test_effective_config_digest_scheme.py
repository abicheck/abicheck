"""The effective-config digest's gate is built in one place
(``effective_config_fields_from_raw``), and its scheme is always the one
``EffectiveGate`` derives from the severity setting -- there is no scheme
parameter left to state it separately (dead-code plan, Stage D; single
``EffectiveGate`` cutover)."""

from __future__ import annotations

import inspect

import pytest

from abicheck.checker import DiffResult, Verdict
from abicheck.effective_config_digest import effective_config_fields_from_raw
from abicheck.reporter_contract_blocks import add_effective_config_digest
from abicheck.severity import resolve_severity_config


def _result() -> DiffResult:
    return DiffResult(
        old_version="1.0",
        new_version="2.0",
        library="libtest.so.1",
        verdict=Verdict.NO_CHANGE,
    )


@pytest.mark.parametrize(
    "fn", [effective_config_fields_from_raw, add_effective_config_digest]
)
def test_no_scheme_parameter(fn) -> None:
    assert not any("scheme" in name for name in inspect.signature(fn).parameters)


@pytest.mark.parametrize("preset", [None, "default", "strict", "info-only"])
def test_scheme_follows_the_severity_setting(preset) -> None:
    severity = None if preset is None else resolve_severity_config(preset)
    fields = effective_config_fields_from_raw(_result(), severity_config=severity)
    assert fields["gate.exit_code_scheme"] == (
        "legacy" if severity is None else "severity"
    )


@pytest.mark.parametrize("preset", [None, "strict"])
def test_report_block_matches_the_raw_entry_point(preset) -> None:
    severity = None if preset is None else resolve_severity_config(preset)
    d: dict = {}
    add_effective_config_digest(d, _result(), severity_config=severity)
    assert d["effective_config_fields"] == effective_config_fields_from_raw(
        _result(), severity_config=severity
    )
