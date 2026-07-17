"""Tests for :mod:`abicheck.cli_help`'s cross-platform help rendering.

``_ensure_utf8_streams`` fixes a real crash: ``python -m abicheck.cli --help``
raised ``UnicodeEncodeError`` and exited non-zero on Windows CI, because help
text carries non-ASCII characters (em dashes, arrows) and a redirected/piped
Windows stdout defaults to the legacy ANSI code page, not UTF-8.
"""

from __future__ import annotations

import io

import click
import pytest
from click.testing import CliRunner

from abicheck import cli_help
from abicheck.cli import main
from abicheck.model import AbiSnapshot
from abicheck.serialization import snapshot_to_json


def _cp1252_stream() -> io.TextIOWrapper:
    """A text stream backed by a real buffer, encoded like a legacy Windows console."""
    return io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")


def test_ensure_utf8_streams_noop_on_non_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli_help.sys, "platform", "linux")
    stream = _cp1252_stream()
    monkeypatch.setattr(cli_help.sys, "stdout", stream)
    monkeypatch.setattr(cli_help.sys, "stderr", stream)
    cli_help._ensure_utf8_streams()
    assert stream.encoding == "cp1252"


def test_ensure_utf8_streams_reconfigures_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli_help.sys, "platform", "win32")
    out_stream = _cp1252_stream()
    err_stream = _cp1252_stream()
    monkeypatch.setattr(cli_help.sys, "stdout", out_stream)
    monkeypatch.setattr(cli_help.sys, "stderr", err_stream)

    # Before the fix: a cp1252 stream can't encode an arrow or an em dash.
    with pytest.raises(UnicodeEncodeError):
        out_stream.write("→")
        out_stream.flush()

    cli_help._ensure_utf8_streams()

    assert out_stream.encoding.lower() == "utf-8"
    assert err_stream.encoding.lower() == "utf-8"
    # After the fix: the same characters that crashed above now write cleanly.
    out_stream.write("right arrow → and em dash —")
    out_stream.flush()


def test_ensure_utf8_streams_handles_stream_without_reconfigure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stream lacking ``reconfigure`` (e.g. replaced by a test harness) is skipped, not crashed on."""

    class _NoReconfigure:
        pass

    monkeypatch.setattr(cli_help.sys, "platform", "win32")
    monkeypatch.setattr(cli_help.sys, "stdout", _NoReconfigure())
    monkeypatch.setattr(cli_help.sys, "stderr", _NoReconfigure())
    cli_help._ensure_utf8_streams()  # must not raise


def test_configure_rich_help_calls_ensure_utf8_streams(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []
    monkeypatch.setattr(cli_help, "_ensure_utf8_streams", lambda: calls.append(True))
    cli_help.configure_rich_help()
    assert calls == [True]


# ── `compare --help-all` second-level disclosure (G21.8 / collapse M2) ───────


class TestCompareHelpAllDisclosure:
    def test_common_option_names_are_real_params(self) -> None:
        """Every dest name in the curated set must be a real ``compare`` option.

        Guards against typos/drift: a stale name here would silently mean that
        option is no longer protected from being hidden in the curated view
        (it would just vanish from ``--help`` instead of erroring loudly).
        """
        real_names = {
            p.name for p in main.commands["compare"].params if isinstance(p, click.Option)
        }
        assert cli_help.COMPARE_COMMON_OPTION_NAMES <= real_names

    def test_curated_help_hides_advanced_options(self) -> None:
        out = CliRunner().invoke(main, ["compare", "--help"]).output
        # A representative long-tail option from each folded panel.
        for advanced_flag in (
            "--gcc-path",
            "--ast-frontend",
            "--jobs",
            "--severity-abi-breaking",
            "--report-mode",
            "--pdb-path",
        ):
            assert advanced_flag not in out, f"{advanced_flag} leaked into curated --help"

    def test_curated_help_keeps_common_options(self) -> None:
        out = CliRunner().invoke(main, ["compare", "--help"]).output
        for common_flag in (
            "--header",
            "--include",
            "--lang",
            "--output",
            "--format",
            "--show-only",
            "--config",
            "--severity-preset",
            "--used-by",
            "--required-symbol",
            "--depth",
            "--sources",
            "--build-info",
            "--scope-public-headers",
            "--verbose",
            "--dry-run",
        ):
            assert common_flag in out, f"{common_flag} missing from curated --help"

    def test_curated_help_reports_hidden_count_and_points_to_help_all(self) -> None:
        result = CliRunner().invoke(main, ["compare", "--help"])
        assert result.exit_code == 0
        assert "advanced option(s) hidden" in result.output
        assert "compare --help-all" in result.output

    def test_help_all_shows_everything_curated_hides(self) -> None:
        out = CliRunner().invoke(main, ["compare", "--help-all"]).output
        for advanced_flag in (
            "--gcc-path",
            "--ast-frontend",
            "--jobs",
            "--severity-abi-breaking",
            "--report-mode",
            "--pdb-path",
        ):
            assert advanced_flag in out

    def test_help_all_has_no_hidden_count_footer(self) -> None:
        result = CliRunner().invoke(main, ["compare", "--help-all"])
        assert result.exit_code == 0
        assert "advanced option(s) hidden" not in result.output

    def test_params_restored_after_curated_help(self) -> None:
        """Rendering curated help must not permanently shrink ``compare``'s params."""
        cmd = main.commands["compare"]
        before = list(cmd.params)
        CliRunner().invoke(main, ["compare", "--help"])
        assert cmd.params == before

    def test_advanced_option_still_functional_after_curated_help_render(
        self, tmp_path
    ) -> None:
        """An option hidden from curated --help must still work when actually passed.

        Renders curated help once first (which temporarily shrinks
        ``compare``'s params list) to prove the restore in the ``finally``
        block leaves the command fully functional for a real invocation.
        """
        CliRunner().invoke(main, ["compare", "--help"])
        old = tmp_path / "old.json"
        new = tmp_path / "new.json"
        snap_json = snapshot_to_json(AbiSnapshot(library="x", version="1"))
        old.write_text(snap_json, encoding="utf-8")
        new.write_text(snap_json, encoding="utf-8")
        result = CliRunner().invoke(
            main, ["compare", str(old), str(new), "--gcc-path", "/usr/bin/gcc"]
        )
        assert result.exit_code == 0, result.output
