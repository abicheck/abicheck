"""One compare exit gate: the native CLI and the typed API agree.

The oracle is ``docs/reference/exit-codes.md`` -- the preset table, the
severity-aware exit table and the override-precedence rule -- not either
front end's code. Each case runs one snapshot pair twice: through the native
``compare`` CLI with the severity settings in ``.abicheck.yml``, and through
``run_compare_request`` with the same values on ``CompareRequest``. Both must
produce the documented exit code.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.policy.release_gate_options import resolve_release_gate_options
from abicheck.policy.severity import resolve_severity_config
from abicheck.service import CompareRequest, InputSpec, run_compare_request
from abicheck.workflows.gate import (
    effective_gate_for_compare,
    effective_gate_for_resolved_compare_config,
)
from tests.test_typed_api_gate_options import _breaking_pair, _write

#: exit-codes.md "Severity presets": each preset's ``abi_breaking`` level.
PRESET_ABI_BREAKING = {"default": "error", "strict": "error", "info-only": "info"}

#: exit-codes.md severity-aware table: an error-level ``abi_breaking`` finding
#: exits ``4``; an informational level has no error-level finding, so ``0``.
SEVERITY_EXIT = {"error": 4, "info": 0}

#: exit-codes.md legacy table: a ``BREAKING`` verdict with no severity setting.
LEGACY_BREAKING_EXIT = 4

PRESETS = [None, "default", "strict", "info-only"]
ABI_OVERRIDES = [None, "info"]


def _documented_exit(preset: str | None, abi_override: str | None) -> int:
    """The exit code the documentation gives for this severity input."""
    level = abi_override or (PRESET_ABI_BREAKING[preset] if preset else None)
    if level is None:
        return LEGACY_BREAKING_EXIT
    return SEVERITY_EXIT[level]


def _cli_exit(
    tmp_path: Path, old: Path, new: Path, preset: str | None, abi: str | None
) -> int:
    severity = {}
    if preset is not None:
        severity["preset"] = preset
    if abi is not None:
        severity["abi_breaking"] = abi
    lines = ["severity:"] + [f"  {k}: {v}" for k, v in severity.items()]
    config = tmp_path / ".abicheck.yml"
    config.write_text("\n".join(lines) + "\n", encoding="utf-8")
    result = CliRunner().invoke(
        main, ["compare", str(old), str(new), "--config", str(config)]
    )
    return result.exit_code


def _typed_exit(old: Path, new: Path, preset: str | None, abi: str | None) -> int:
    request = CompareRequest(
        old=InputSpec(path=old),
        new=InputSpec(path=new),
        severity_preset=preset,
        severity_abi_breaking=abi,
    )
    return run_compare_request(request).exit_decision.code


@pytest.mark.parametrize("abi", ABI_OVERRIDES)
@pytest.mark.parametrize("preset", PRESETS)
def test_cli_and_typed_api_match_the_documented_exit(
    tmp_path: Path, preset: str | None, abi: str | None
) -> None:
    old, new = _write(tmp_path, *_breaking_pair())
    expected = _documented_exit(preset, abi)
    assert _typed_exit(old, new, preset, abi) == expected
    assert _cli_exit(tmp_path, old, new, preset, abi) == expected


@pytest.mark.parametrize("preset", PRESETS)
def test_gate_derivation_is_one_function_for_both_front_ends(
    preset: str | None,
) -> None:
    """The CLI's projection and the typed path's derivation are the same gate."""
    severity = resolve_release_gate_options(
        None,
        severity_preset=preset,
        severity_abi_breaking=None,
        severity_potential_breaking=None,
        severity_quality_issues=None,
        severity_addition=None,
    ).severity
    cli_cfg = SimpleNamespace(
        severity=resolve_severity_config(preset) if preset else None,
        severity_active=preset is not None,
    )
    assert effective_gate_for_resolved_compare_config(
        cli_cfg, require_complete_analysis=True
    ) == effective_gate_for_compare(severity, require_complete_analysis=True)


@pytest.mark.parametrize("require_complete", [False, True])
def test_require_complete_reaches_the_gate(require_complete: bool) -> None:
    """exit-codes.md "Analysis assurance": the flag is part of the gate."""
    gate = effective_gate_for_compare(None, require_complete_analysis=require_complete)
    assert gate.require_complete_analysis is require_complete
