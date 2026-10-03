"""The effective-config digest's gate is built in one place
(``effective_config_fields_from_raw``), and an unstated scheme keeps the
derived one (dead-code plan, Stage D)."""

from __future__ import annotations

import pytest

from abicheck.checker import DiffResult, Verdict
from abicheck.effective_config_digest import effective_config_fields_from_raw
from abicheck.severity import resolve_severity_config


def _result() -> DiffResult:
    return DiffResult(
        old_version="1.0",
        new_version="2.0",
        library="libtest.so.1",
        verdict=Verdict.NO_CHANGE,
    )


class TestUnstatedSchemeFallsBack:
    """``add_effective_config_digest`` and the raw-input entry point are one
    path now; an unstated scheme (``None`` or ``""``) must keep the scheme the
    gate derives, never record an empty one."""

    @pytest.mark.parametrize("unstated", [None, ""])
    @pytest.mark.parametrize("preset", [None, "strict"])
    def test_unstated_scheme_keeps_the_derived_one(self, unstated, preset):
        severity = None if preset is None else resolve_severity_config(preset)
        fields = effective_config_fields_from_raw(
            _result(), severity_config=severity, exit_code_scheme=unstated
        )
        assert fields["gate.exit_code_scheme"] == (
            "legacy" if severity is None else "severity"
        )

    @pytest.mark.parametrize("unstated", [None, ""])
    def test_report_block_matches_the_raw_entry_point(self, unstated):
        from abicheck.reporter_contract_blocks import add_effective_config_digest

        d: dict = {}
        add_effective_config_digest(
            d, _result(), severity_config=None, exit_code_scheme=unstated
        )
        assert d["effective_config_fields"] == effective_config_fields_from_raw(
            _result(), severity_config=None, exit_code_scheme=unstated
        )
