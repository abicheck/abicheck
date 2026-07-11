# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""ADR-040 Lever 3 — ``compare --profile`` run-profile presets.

Covers the two contract points: a profile fills workflow defaults, and an
explicit flag always overrides the profile (default-layer semantics).
"""
from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from abicheck.cli import main
from abicheck.cli_options import COMPARE_PROFILES, apply_compare_profile
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.serialization import snapshot_to_json


def _write_snapshots(tmp_path: Path) -> tuple[Path, Path]:
    old = AbiSnapshot(
        library="libtest.so", version="1.0",
        functions=[Function(name="foo", mangled="_Z3foov", return_type="int",
                            visibility=Visibility.PUBLIC)],
    )
    new = AbiSnapshot(
        library="libtest.so", version="2.0",
        functions=[Function(name="foo", mangled="_Z3foov", return_type="int",
                            visibility=Visibility.PUBLIC)],
    )
    old_p = tmp_path / "old.json"
    new_p = tmp_path / "new.json"
    old_p.write_text(snapshot_to_json(old), encoding="utf-8")
    new_p.write_text(snapshot_to_json(new), encoding="utf-8")
    return old_p, new_p


class _FakeCtx:
    """Minimal stand-in for a Click context's parameter-source lookup."""

    def __init__(self, explicit: set[str]) -> None:
        self._explicit = explicit

    def get_parameter_source(self, name: str):  # noqa: ANN201 - test double
        from click.core import ParameterSource

        return (
            ParameterSource.COMMANDLINE
            if name in self._explicit
            else ParameterSource.DEFAULT
        )


class TestApplyProfileUnit:
    def test_profile_fills_defaults(self) -> None:
        kwargs: dict[str, object] = {"profile": "ci-gate", "depth": None, "fmt": "markdown"}
        apply_compare_profile(_FakeCtx(explicit=set()), kwargs)
        # profile is consumed (never forwarded to run_compare)
        assert "profile" not in kwargs
        # ci-gate defaults land where the user didn't choose
        assert kwargs["depth"] == "headers"
        assert kwargs["fmt"] == "review"
        assert kwargs["exit_code_scheme"] == "severity"

    def test_explicit_flag_beats_profile(self) -> None:
        kwargs: dict[str, object] = {"profile": "ci-gate", "depth": None, "fmt": "json"}
        # user typed --format json explicitly → profile must not clobber it
        apply_compare_profile(_FakeCtx(explicit={"fmt"}), kwargs)
        assert kwargs["fmt"] == "json"
        # but an unset field still takes the profile default
        assert kwargs["depth"] == "headers"

    def test_no_profile_is_a_noop(self) -> None:
        kwargs: dict[str, object] = {"profile": None, "depth": None}
        apply_compare_profile(_FakeCtx(explicit=set()), kwargs)
        assert "profile" not in kwargs
        assert kwargs["depth"] is None

    def test_every_profile_targets_real_compare_dests(self) -> None:
        """Guard against a typo'd dest in COMPARE_PROFILES silently no-op'ing."""
        dests = {
            p.name
            for p in main.commands["compare"].params
            if getattr(p, "param_type_name", None) == "option"
        }
        for name, settings in COMPARE_PROFILES.items():
            for dest in settings:
                assert dest in dests, f"profile {name!r} targets unknown dest {dest!r}"


class TestProfileEndToEnd:
    def test_quick_profile_emits_stat_summary(self, tmp_path) -> None:
        old_p, new_p = _write_snapshots(tmp_path)
        result = CliRunner().invoke(
            main, ["compare", str(old_p), str(new_p), "--profile", "quick"]
        )
        assert result.exit_code == 0, result.output
        # --profile quick sets --stat: output is the compact one-line summary
        # (e.g. "NO_CHANGE: no changes (0 total)"), not the full report.
        assert "total)" in result.output
        assert result.output.strip().count("\n") == 0

    def test_explicit_format_overrides_profile_e2e(self, tmp_path) -> None:
        old_p, new_p = _write_snapshots(tmp_path)
        result = CliRunner().invoke(
            main,
            ["compare", str(old_p), str(new_p), "--profile", "ci-gate", "--format", "json"],
        )
        assert result.exit_code == 0, result.output
        # ci-gate would pick 'review'; explicit --format json wins → JSON object
        assert result.output.lstrip().startswith("{")

    def test_unknown_profile_is_a_usage_error(self, tmp_path) -> None:
        old_p, new_p = _write_snapshots(tmp_path)
        result = CliRunner().invoke(
            main, ["compare", str(old_p), str(new_p), "--profile", "bogus"]
        )
        assert result.exit_code != 0
        assert "bogus" in result.output or "Invalid value" in result.output
