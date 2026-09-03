"""Tests for :mod:`abicheck.frontends.cli.help`'s cross-platform help rendering.

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

from abicheck.cli import main
from abicheck.frontends.cli import help as cli_help
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


# ── deduplicate: False (CI incident: panels corrupted by repeated renders) ──
#
# rich-click's own command/option-group resolver defaults `deduplicate` to
# True and, when it thinks it sees the same name in two panels, calls
# `list.remove()` directly on the panel dict's own `commands`/`options` list
# -- not a copy. Every panel here is a module-level literal built once and
# reused for every `--help` render `configure_rich_help()` (which itself
# only runs once, at `abicheck.cli` import time) registers it under. Given
# enough `--help` renders in one process, rich-click eventually perceives a
# spurious cross-call duplicate and permanently deletes panel entries for
# the rest of that process -- this reproduced as a CI-only failure of
# `test_cli_root_surface.py::test_help_groups_commands_by_role` under the
# full pytest-xdist suite (never in isolation), where "Workflow
# composition"/"Project integration" silently vanished from `--help` output
# after enough prior renders in the same worker (ADR-054 PR review).


def test_all_command_panels_disable_deduplicate() -> None:
    for panels in cli_help.COMMAND_GROUPS.values():
        for panel in panels:
            assert panel.get("deduplicate") is False, panel


def test_all_option_panels_disable_deduplicate() -> None:
    for panels in cli_help.OPTION_GROUPS.values():
        for panel in panels:
            assert panel.get("deduplicate") is False, panel


def test_root_help_panels_survive_many_repeated_renders() -> None:
    """Regression test for the CI incident above: many repeated `--help`
    renders in one process must never erode the registered panels -- every
    render must show the identical, complete set of named panels."""
    runner = CliRunner()
    first = runner.invoke(main, ["--help"]).output
    assert "Core analysis" in first
    assert "Workflow composition" in first
    assert "Project integration" in first
    assert "Legacy compatibility" in first
    for _ in range(50):
        later = runner.invoke(main, ["--help"]).output
        assert later == first


# ── `compare --help-all` second-level disclosure (G21.8 / collapse M2) ───────


class TestCompareHelpAllDisclosure:
    def test_common_option_names_are_real_params(self) -> None:
        """Every dest name in the curated set must be a real ``compare`` option.

        Guards against typos/drift: a stale name here would silently mean that
        option is no longer protected from being hidden in the curated view
        (it would just vanish from ``--help`` instead of erroring loudly).
        """
        real_names = {
            p.name
            for p in main.commands["compare"].params
            if isinstance(p, click.Option)
        }
        assert cli_help.COMPARE_COMMON_OPTION_NAMES <= real_names

    def test_curated_help_hides_advanced_options(self) -> None:
        out = CliRunner().invoke(main, ["compare", "--help"]).output
        # A representative long-tail option from each folded panel.
        for advanced_flag in (
            "--compiler",
            "--ast-frontend",
            "--jobs",
            "--write",
            "--report-mode",
            "--pdb-path",
        ):
            assert advanced_flag not in out, (
                f"{advanced_flag} leaked into curated --help"
            )

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

    def test_hidden_count_never_overcounts_permanently_invisible_options(self) -> None:
        """The footer promises '--help-all shows every option' -- only true for
        an option --help-all can actually render. A few options are Click-
        ``hidden`` AND not listed in any OPTION_GROUPS panel (deprecated no-op
        shims like --header-graph): those
        never render even in --help-all, so counting them would overstate what
        running it actually recovers (Codex review, PR #757). Verified by
        computing the same 'recoverable' set independently of
        cli_help._make_help_callback's own implementation, so this fails if the
        production logic regresses to double-counting again."""
        import re

        for command, common_names in (
            ("compare", cli_help.COMPARE_COMMON_OPTION_NAMES),
            ("dump", cli_help.DUMP_COMMON_OPTION_NAMES),
            ("scan", cli_help.SCAN_COMMON_OPTION_NAMES),
        ):
            cmd = main.commands[command]
            panel_flags: set[str] = set()
            for panel in cli_help.OPTION_GROUPS.get(f"* {command}", []):
                panel_flags.update(panel["options"])  # type: ignore[arg-type]
            common_params = [
                p
                for p in cmd.params
                if isinstance(p, click.Argument) or p.name in common_names
            ]
            expected_recoverable = sum(
                1
                for p in cmd.params
                if p not in common_params
                and (not getattr(p, "hidden", False) or set(p.opts) & panel_flags)
            )
            output = CliRunner().invoke(main, [command, "--help"]).output
            m = re.search(r"(\d+) advanced option\(s\) hidden", output)
            assert m, f"{command}: footer missing entirely"
            assert int(m.group(1)) == expected_recoverable, command

    def test_help_all_shows_everything_curated_hides(self) -> None:
        out = CliRunner().invoke(main, ["compare", "--help-all"]).output
        for advanced_flag in (
            "--sysroot",
            "--ast-frontend",
            "--jobs",
            "--write",
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
            main, ["compare", str(old), str(new), "--compiler", "/usr/bin/gcc"]
        )
        assert result.exit_code == 0, result.output


# ── `dump --help-all` / `scan --help-all` (G21.8 M2 follow-on) ───────────────
#
# Same disclosure as `compare` above, generalized via `cli_help.curated_help_options`.
# One parametrized class covers the presentational assertions shared by both
# commands; each command additionally gets its own advanced-option-still-works
# check, since the functional flag/args differ per command.

_HELP_ALL_COMMANDS: list[
    tuple[str, frozenset[str], tuple[str, ...], tuple[str, ...]]
] = [
    (
        "dump",
        cli_help.DUMP_COMMON_OPTION_NAMES,
        ("--sysroot", "--ast-frontend", "--follow-deps", "--debug-root", "--pdb-path"),
        (
            "--header",
            "--include",
            "--depth",
            "--sources",
            "--build-info",
            "--output",
            "--verbose",
        ),
    ),
    (
        "scan",
        cli_help.SCAN_COMMON_OPTION_NAMES,
        # the strict-suppressions/public-symbol family is gone (CLI audit
        # PR 4/5) but genuinely render in --help-all via their new "Policy &
        # severity"/"Public-surface scoping" panel membership (cli_help.py),
        # so they stay valid examples here.
        (
            "--sysroot",
            "--ast-frontend",
            "--write",
            "--pattern-verdicts",
            "--risk-rules",
        ),
        (
            "--header",
            "--against",
            "--depth",
            "--sources",
            "--policy",
            "--contract",
            "--format",
            "--output",
        ),
    ),
]


class TestDumpAndScanHelpAllDisclosure:
    @pytest.mark.parametrize(
        "command,common_names,_advanced,_common", _HELP_ALL_COMMANDS
    )
    def test_common_option_names_are_real_params(
        self,
        command: str,
        common_names: frozenset[str],
        _advanced: tuple[str, ...],
        _common: tuple[str, ...],
    ) -> None:
        real_names = {
            p.name for p in main.commands[command].params if isinstance(p, click.Option)
        }
        assert common_names <= real_names

    @pytest.mark.parametrize("command,_names,advanced,_common", _HELP_ALL_COMMANDS)
    def test_curated_help_hides_advanced_options(
        self,
        command: str,
        _names: frozenset[str],
        advanced: tuple[str, ...],
        _common: tuple[str, ...],
    ) -> None:
        out = CliRunner().invoke(main, [command, "--help"]).output
        for advanced_flag in advanced:
            assert advanced_flag not in out, (
                f"{advanced_flag} leaked into curated {command} --help"
            )

    @pytest.mark.parametrize("command,_names,_advanced,common", _HELP_ALL_COMMANDS)
    def test_curated_help_keeps_common_options(
        self,
        command: str,
        _names: frozenset[str],
        _advanced: tuple[str, ...],
        common: tuple[str, ...],
    ) -> None:
        out = CliRunner().invoke(main, [command, "--help"]).output
        for common_flag in common:
            assert common_flag in out, (
                f"{common_flag} missing from curated {command} --help"
            )

    @pytest.mark.parametrize("command,_names,_advanced,_common", _HELP_ALL_COMMANDS)
    def test_curated_help_reports_hidden_count_and_points_to_help_all(
        self,
        command: str,
        _names: frozenset[str],
        _advanced: tuple[str, ...],
        _common: tuple[str, ...],
    ) -> None:
        result = CliRunner().invoke(main, [command, "--help"])
        assert result.exit_code == 0
        assert "advanced option(s) hidden" in result.output
        assert f"{command} --help-all" in result.output

    @pytest.mark.parametrize("command,_names,advanced,_common", _HELP_ALL_COMMANDS)
    def test_help_all_shows_everything_curated_hides(
        self,
        command: str,
        _names: frozenset[str],
        advanced: tuple[str, ...],
        _common: tuple[str, ...],
    ) -> None:
        out = CliRunner().invoke(main, [command, "--help-all"]).output
        for advanced_flag in advanced:
            assert advanced_flag in out

    @pytest.mark.parametrize("command,_names,_advanced,_common", _HELP_ALL_COMMANDS)
    def test_help_all_has_no_hidden_count_footer(
        self,
        command: str,
        _names: frozenset[str],
        _advanced: tuple[str, ...],
        _common: tuple[str, ...],
    ) -> None:
        result = CliRunner().invoke(main, [command, "--help-all"])
        assert result.exit_code == 0
        assert "advanced option(s) hidden" not in result.output

    @pytest.mark.parametrize("command,_names,_advanced,_common", _HELP_ALL_COMMANDS)
    def test_params_restored_after_curated_help(
        self,
        command: str,
        _names: frozenset[str],
        _advanced: tuple[str, ...],
        _common: tuple[str, ...],
    ) -> None:
        cmd = main.commands[command]
        before = list(cmd.params)
        CliRunner().invoke(main, [command, "--help"])
        assert cmd.params == before

    def test_dump_advanced_option_still_functional_after_curated_help_render(
        self, tmp_path
    ) -> None:
        """--compiler is hidden from curated `dump --help` but must still work."""
        CliRunner().invoke(main, ["dump", "--help"])
        so_path = tmp_path / "lib.so"
        so_path.write_bytes(b"")
        result = CliRunner().invoke(
            main, ["dump", str(so_path), "--compiler", "/usr/bin/gcc"]
        )
        # Not a valid ELF, so this is expected to fail downstream -- the point
        # is that Click accepts the (curated-hidden) --compiler flag at all,
        # rather than rejecting it as "no such option".
        assert "no such option" not in result.output.lower()

    def test_scan_advanced_option_still_functional_after_curated_help_render(
        self, tmp_path
    ) -> None:
        """--severity-preset is hidden from curated `scan --help` but must still work."""
        CliRunner().invoke(main, ["scan", "--help"])
        so_path = tmp_path / "lib.so"
        so_path.write_bytes(b"")
        result = CliRunner().invoke(
            main, ["scan", str(so_path), "--severity-preset", "strict"]
        )
        assert "no such option" not in result.output.lower()
