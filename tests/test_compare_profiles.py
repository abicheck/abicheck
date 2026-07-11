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
    """Stand-in for a Click context's parameter-source get/set.

    A profile is a value-only default layer (it must NOT stamp a command-line
    source — see the module docstring / ADR-040), so this double only needs
    ``get_parameter_source`` to distinguish an explicitly-typed flag from a
    default.
    """

    def __init__(self, explicit: set[str]) -> None:
        self._explicit = set(explicit)

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

    def test_set_input_skips_single_pair_only_keys(self, tmp_path) -> None:
        """Regression (Codex P2): a release/fan-out compare must not exit 64.

        On directory/package operands the fan-out rejects the single-pair-only
        flags (``--depth`` by source, ``--exit-code-scheme`` by value). A profile
        default must be *skipped* for those keys — not injected — so it never
        becomes one of those rejections. Format/recommend still apply.
        """
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        kwargs: dict[str, object] = {
            "profile": "ci-gate", "old_input": old_dir, "new_input": new_dir,
            "depth": None, "fmt": "markdown", "exit_code_scheme": None,
        }
        apply_compare_profile(_FakeCtx(explicit=set()), kwargs)
        # single-pair-only keys are NOT injected on a set input
        assert kwargs["depth"] is None
        assert kwargs["exit_code_scheme"] is None
        # but the fan-out-compatible key still takes the profile default
        assert kwargs["fmt"] == "review"

    def test_release_profile_on_directories_does_not_error(self, tmp_path) -> None:
        """End-to-end: `compare dir dir --profile release` reaches the fan-out.

        Before the fix, the profile stamped --depth as command-line and the
        release path rejected it with exit 64 (usage error). Now it must get past
        option handling into the fan-out (which then reports no libraries).
        """
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        result = CliRunner().invoke(
            main, ["compare", str(old_dir), str(new_dir), "--profile", "release"]
        )
        # must NOT be the usage error (64) the depth/exit rejection raised
        assert result.exit_code != 64, result.output
        assert "not supported for directory/package" not in result.output

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
