# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""ADR-074 CLI contract for ``-D/--define`` on ``dump`` and ``compare``.

Mock-free at the resolver level: every precedence assertion runs the real
``cli_options.resolve_compile_context`` inside a real Click context against a
real ``.abicheck.yml``, so what is asserted is the token tail an actual
frontend invocation receives -- not a reimplementation of the fold.
"""

from __future__ import annotations

from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.cli_options import define_option, resolve_compile_context
from abicheck.model.macro_definition import define_spellings_from_tokens


def _write_config(tmp_path: Path, body: str) -> Path:
    cfg = tmp_path / ".abicheck.yml"
    cfg.write_text(body)
    return cfg


def _resolve(tmp_path: Path, *, config: Path | None, defines: tuple[str, ...]):
    """The real resolver, under a real Click context."""

    @click.command()
    @define_option
    def _cmd(defines: tuple[str, ...]) -> None:  # pragma: no cover - never run
        pass

    with _cmd.make_context("_cmd", []) as ctx:
        cc, _ = resolve_compile_context(
            ctx,
            sysroot=None,
            nostdinc=False,
            header_backend="auto",
            includes=(),
            build_config=config,
            defines=defines,
        )
    return cc


class TestGrammarThroughClick:
    """Which spellings Click actually accepts -- asserted, never assumed."""

    @pytest.mark.parametrize(
        "argv,expected",
        [
            (["-DFOO"], ("FOO",)),
            (["-D", "FOO"], ("FOO",)),
            (["--define", "FOO"], ("FOO",)),
            (["--define=FOO"], ("FOO",)),
            (["-DFOO=1"], ("FOO=1",)),
            (["-D", "FOO=1"], ("FOO=1",)),
            (["--define=FOO=1"], ("FOO=1",)),
            (["--define=NAME=A=B"], ("NAME=A=B",)),
            (["-DA", "-DB=2", "--define", "C"], ("A", "B=2", "C")),
            (["-DA=1", "-DA=2"], ("A=1", "A=2")),  # parse preserves; merge resolves
            ([], ()),
        ],
    )
    def test_accepted_spellings(
        self, argv: list[str], expected: tuple[str, ...]
    ) -> None:
        seen: dict[str, tuple[str, ...]] = {}

        @click.command()
        @define_option
        def _cmd(defines: tuple[str, ...]) -> None:
            seen["defines"] = defines

        result = CliRunner().invoke(_cmd, argv)
        assert result.exit_code == 0, result.output
        assert seen["defines"] == expected

    @pytest.mark.parametrize(
        "argv,expected",
        [
            (["--define=-DFOO"], "write -DFOO, not -D-DFOO"),
            (["--define=-UFOO"], "no --undefine counterpart"),
            (["--define=F(x)=1"], "function-like"),
            (["--define=A B"], "whitespace"),
            (["--define=1BAD"], "not a C identifier"),
            (["--define="], "empty value"),
            (["--define=@resp.txt"], "general compiler flags belong"),
        ],
    )
    def test_rejected_spellings_are_usage_errors(
        self, argv: list[str], expected: str
    ) -> None:
        @click.command()
        @define_option
        def _cmd(defines: tuple[str, ...]) -> None:  # pragma: no cover
            pass

        result = CliRunner().invoke(_cmd, argv)
        assert result.exit_code == 2  # Click's own UsageError code standalone
        assert expected in result.output

    def test_missing_value_after_D_is_an_error(self) -> None:
        @click.command()
        @define_option
        def _cmd(defines: tuple[str, ...]) -> None:  # pragma: no cover
            pass

        result = CliRunner().invoke(_cmd, ["-D"])
        assert result.exit_code != 0
        assert "requires an argument" in result.output or "Error" in result.output


class TestPrecedenceFold:
    def test_cli_only_without_any_config(self, tmp_path: Path) -> None:
        cc = _resolve(tmp_path, config=None, defines=("A", "B=2"))
        assert define_spellings_from_tokens(cc.gcc_option_tokens) == ("A", "B=2")

    def test_config_only(self, tmp_path: Path) -> None:
        cfg = _write_config(tmp_path, "compile:\n  defines:\n    - A\n    - B=1\n")
        cc = _resolve(tmp_path, config=cfg, defines=())
        assert define_spellings_from_tokens(cc.gcc_option_tokens) == ("A", "B=1")

    def test_no_cli_define_leaves_the_config_token_tail_byte_identical(
        self, tmp_path: Path
    ) -> None:
        """ADR-074 D3's compatibility claim, stated executably."""
        cfg = _write_config(
            tmp_path,
            "compile:\n  std: gnu++17\n  defines:\n    - A\n    - B=1\n"
            "  options:\n    - -fPIC\n",
        )
        cc = _resolve(tmp_path, config=cfg, defines=())
        assert cc.gcc_option_tokens == ("-std=gnu++17", "-DA", "-DB=1", "-fPIC")

    def test_cli_replaces_only_the_macro_it_names(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path, "compile:\n  defines:\n    - A\n    - B=1\n    - C=3\n"
        )
        cc = _resolve(tmp_path, config=cfg, defines=("B=2",))
        # A and C keep their position and value; B is re-emitted last, once.
        assert define_spellings_from_tokens(cc.gcc_option_tokens) == ("A", "C=3", "B=2")

    def test_cli_adds_a_macro_the_config_never_named(self, tmp_path: Path) -> None:
        cfg = _write_config(tmp_path, "compile:\n  defines:\n    - A\n")
        cc = _resolve(tmp_path, config=cfg, defines=("D=4",))
        assert define_spellings_from_tokens(cc.gcc_option_tokens) == ("A", "D=4")

    def test_cli_beats_a_raw_define_smuggled_through_compile_options(
        self, tmp_path: Path
    ) -> None:
        """The reason an overridden macro is re-emitted LAST rather than
        substituted in place: `compile.options` renders after `defines`, so an
        in-place substitution would lose the compiler's last-flag-wins race."""
        cfg = _write_config(
            tmp_path, "compile:\n  defines:\n    - A=1\n  options:\n    - -DA=9\n"
        )
        cc = _resolve(tmp_path, config=cfg, defines=("A=2",))
        tokens = list(cc.gcc_option_tokens)
        assert tokens.index("-DA=2") > tokens.index("-DA=9")
        assert "-DA=1" not in tokens

    def test_exactly_one_token_per_macro_name(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path, "compile:\n  defines:\n    - A=1\n    - A=2\n    - B\n"
        )
        cc = _resolve(tmp_path, config=cfg, defines=("A=3", "A=4"))
        names = [
            s.partition("=")[0]
            for s in define_spellings_from_tokens(cc.gcc_option_tokens)
        ]
        assert names == ["B", "A"]

    def test_std_and_options_survive_a_cli_define(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            "compile:\n  std: gnu++17\n  defines:\n    - A\n  options:\n    - -fPIC\n",
        )
        cc = _resolve(tmp_path, config=cfg, defines=("B",))
        assert cc.gcc_option_tokens == ("-std=gnu++17", "-DA", "-fPIC", "-DB")

    def test_defines_are_recorded_on_the_context_as_given(self, tmp_path: Path) -> None:
        cc = _resolve(tmp_path, config=None, defines=("A", "B=2"))
        assert cc.defines == ("A", "B=2")


class TestHelpSurface:
    @pytest.mark.parametrize("command", ["dump", "compare"])
    def test_define_is_documented_on_plain_help(self, command: str) -> None:
        result = CliRunner().invoke(main, [command, "--help"])
        assert result.exit_code == 0
        assert "--define" in result.output
        assert "-D" in result.output

    @pytest.mark.parametrize("command", ["dump", "compare"])
    def test_define_is_also_in_help_all(self, command: str) -> None:
        result = CliRunner().invoke(main, [command, "--help-all"])
        assert result.exit_code == 0
        assert "--define" in result.output

    @pytest.mark.parametrize("command", ["dump", "compare"])
    def test_general_compiler_option_flags_stay_unavailable(self, command: str) -> None:
        """ADR-074's non-goal, kept executable: -D must not have dragged the
        retired generic family back with it."""
        result = CliRunner().invoke(main, [command, "--help-all"])
        for gone in ("--gcc-options", "--compiler-option", "--gcc-option"):
            assert gone not in result.output

    @pytest.mark.parametrize(
        "command,flag",
        [
            (c, f)
            for c in ("dump", "compare")
            for f in ("--gcc-options", "--compiler-option")
        ],
    )
    def test_retired_flags_are_still_rejected(self, command: str, flag: str) -> None:
        result = CliRunner().invoke(main, [command, "x", flag, "-DFOO"])
        assert result.exit_code != 0
        assert "No such option" in result.output

    @pytest.mark.parametrize("sided", ["old=FOO", "new=FOO", "old:LABEL=FOO"])
    def test_no_per_side_define_form(self, sided: str) -> None:
        """D1: unlike -H/-I/--version, -D has no `old=`/`new=` spelling. The
        operand is taken verbatim as a macro definition, so a sided attempt is
        a loud usage error rather than a silent per-side macro context (which
        would compare two different public surfaces).

        Asserted against the option itself rather than through `compare`,
        whose positional operands are validated before any option callback
        runs -- that ordering would mask the very rejection under test."""

        seen: dict[str, tuple[str, ...]] = {}

        @click.command()
        @define_option
        def _cmd(defines: tuple[str, ...]) -> None:
            seen["defines"] = defines

        result = CliRunner().invoke(_cmd, [f"-D{sided}"])
        if "=" in sided and sided.partition("=")[0].isidentifier():
            # `-Dold=FOO` is the macro literally named `old` -- the honest
            # reading, and the point: the `old=` prefix is never consumed as a
            # side selector, so no invocation can produce an asymmetric macro
            # context. The spelling round-trips verbatim.
            assert result.exit_code == 0, result.output
            assert seen["defines"] == (sided,)
        else:
            assert result.exit_code != 0
            assert "not a C identifier" in result.output


class TestDryRunReceiptsWithoutAToolchain:
    """The `--dry-run` receipts and the `--no-baseline` compile resolution,
    exercised as *unit* tests.

    These paths were reachable only from `test_define_option_integration.py`,
    which carries `pytest.mark.integration` and is therefore excluded from the
    coverage lane -- so a receipt regression would have been invisible to the
    gate and would have needed a real compiler to catch. None of them parses a
    header: `--dry-run` resolves and reports, and the no-baseline case is
    stopped at resolution, so a plain file stands in for the artifact.
    """

    @staticmethod
    def _artifact(tmp_path: Path) -> Path:
        """A path that exists. Dry-run classifies inputs; it never parses."""
        so = tmp_path / "libx.so"
        so.write_bytes(b"\x7fELF" + b"\0" * 64)
        return so

    @staticmethod
    def _headers(tmp_path: Path) -> Path:
        inc = tmp_path / "include"
        inc.mkdir(exist_ok=True)
        (inc / "x.h").write_text("int x(void);\n")
        return inc

    def test_dump_dry_run_reports_the_effective_defines(self, tmp_path: Path) -> None:
        result = CliRunner().invoke(
            main,
            [
                "dump",
                str(self._artifact(tmp_path)),
                "-H",
                str(self._headers(tmp_path)),
                "-DFEATURE_API",
                "-DMODE=2",
                "--dry-run",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "defines: FEATURE_API, MODE=2" in result.output

    def test_dump_dry_run_omits_the_line_when_nothing_is_defined(
        self, tmp_path: Path
    ) -> None:
        """No `defines:` line at all for a run without macros -- the receipt
        reads exactly as it did before ADR-074."""
        result = CliRunner().invoke(
            main,
            [
                "dump",
                str(self._artifact(tmp_path)),
                "-H",
                str(self._headers(tmp_path)),
                "--dry-run",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "defines:" not in result.output

    def test_dump_dry_run_folds_config_and_cli_by_macro_name(
        self, tmp_path: Path
    ) -> None:
        cfg = _write_config(
            tmp_path, "compile:\n  defines:\n    - FEATURE_API\n    - MODE=1\n"
        )
        result = CliRunner().invoke(
            main,
            [
                "dump",
                str(self._artifact(tmp_path)),
                "-H",
                str(self._headers(tmp_path)),
                "--config",
                str(cfg),
                "-DMODE=2",
                "--dry-run",
            ],
        )
        assert result.exit_code == 0, result.output
        # MODE overridden in place, FEATURE_API kept, exactly one entry each.
        assert "defines: FEATURE_API, MODE=2" in result.output

    def test_compare_dry_run_states_the_pair_wide_defines_once(
        self, tmp_path: Path
    ) -> None:
        """Stated once for the pair, never per side -- `-D` has no old=/new=
        form, so a per-side rendering would misrepresent the contract."""
        artifact = self._artifact(tmp_path)
        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(artifact),
                str(artifact),
                "-H",
                str(self._headers(tmp_path)),
                "-DFEATURE_API",
                "--dry-run",
            ],
        )
        assert result.exit_code == 0, result.output
        assert result.output.count("defines: FEATURE_API") == 1

    def test_a_source_only_dump_reports_the_defines(self, tmp_path: Path) -> None:
        """A source-only dump (no binary operand) still runs an L2 header
        parse, so the macros apply and the receipt states them."""
        src = tmp_path / "src"
        (src / "include").mkdir(parents=True)
        (src / "include" / "x.h").write_text("int x(void);\n")
        (src / "x.c").write_text("int x(void){return 0;}\n")
        result = CliRunner().invoke(
            main, ["dump", "--sources", str(src), "-DFEATURE_API", "--dry-run"]
        )
        assert result.exit_code == 0, result.output
        assert "defines: FEATURE_API" in result.output

    def test_a_dump_manifest_run_still_reports_the_defines(
        self, tmp_path: Path
    ) -> None:
        """ADR-074 D1: a manifest's own profiles keep owning their per-TU
        context, and `-D` folds into the same pass-through tail as
        `compile.defines` -- it is not rejected, and not silently ignored."""
        inc = self._headers(tmp_path)
        manifest = tmp_path / "m.yaml"
        manifest.write_text(
            f"roots: [{inc}]\ntranslation_units:\n  - name: tu1\n"
            f"    includes: [{inc}]\n"
        )
        result = CliRunner().invoke(
            main,
            [
                "dump",
                str(self._artifact(tmp_path)),
                "--dump-manifest",
                str(manifest),
                "-DFEATURE_API",
                "--dry-run",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "defines: FEATURE_API" in result.output
        assert "Multi-TU manifest" in result.output

    def test_no_baseline_dry_run_reports_the_cli_defines(self, tmp_path: Path) -> None:
        """The third dry-run receipt. It previews a run that really does parse
        headers, so omitting the macro set left the one receipt that could not
        tell you why a declaration would or would not appear."""
        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(self._artifact(tmp_path)),
                "--no-baseline",
                "-H",
                str(self._headers(tmp_path)),
                "-DFEATURE_API",
                "-DMODE=2",
                "--dry-run",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "defines: FEATURE_API, MODE=2" in result.output

    def test_no_baseline_dry_run_reports_config_defines_too(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Read off the resolved context, not the raw --define values, so a
        macro the project configured shows up even with no CLI flag at all."""
        headers = self._headers(tmp_path)
        artifact = self._artifact(tmp_path)
        (tmp_path / ".abicheck.yml").write_text(
            "compile:\n  defines:\n    - FROM_CONFIG\n"
        )
        monkeypatch.chdir(tmp_path)
        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(artifact),
                "--no-baseline",
                "-H",
                str(headers),
                "--dry-run",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "defines: FROM_CONFIG" in result.output

    def test_no_baseline_dry_run_omits_the_line_without_macros(
        self, tmp_path: Path
    ) -> None:
        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(self._artifact(tmp_path)),
                "--no-baseline",
                "-H",
                str(self._headers(tmp_path)),
                "--dry-run",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "defines:" not in result.output

    def test_no_baseline_resolves_a_compile_context_carrying_the_defines(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The `--no-baseline` wiring, without a compiler: stop at the
        candidate resolution and inspect the context it was handed."""
        from abicheck.frontends.cli.commands import compare_no_baseline as nb

        seen: dict[str, object] = {}

        def _spy(path: Path, **kwargs: object):
            seen.update(kwargs)
            raise RuntimeError("resolution reached; no parse in this test")

        monkeypatch.setattr(nb, "resolve_no_baseline_candidate", _spy)
        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(self._artifact(tmp_path)),
                "--no-baseline",
                "-H",
                str(self._headers(tmp_path)),
                "-DFEATURE_API",
                "-DMODE=2",
            ],
        )
        assert result.exit_code != 0  # the spy aborts the run by design
        context = seen.get("compile")
        assert context is not None, f"no compile context was passed: {sorted(seen)}"
        assert define_spellings_from_tokens(context.gcc_option_tokens) == (  # type: ignore[union-attr]
            "FEATURE_API",
            "MODE=2",
        )


class TestDefinesSurviveTheFoldsOtherBranches:
    """`merge_compile_config` has three exits, and a CLI `-D` has to come out
    of all of them. The two below are the ones no CLI invocation reaches by
    the ordinary route, so they are exercised against the resolver directly."""

    @staticmethod
    def _merged(cli_ctx, *, build_config=None, **kwargs):
        from abicheck.cli_options import merge_compile_config

        context, _includes = merge_compile_config(cli_ctx, (), build_config, **kwargs)
        return define_spellings_from_tokens(context.gcc_option_tokens)

    def test_a_malformed_auto_discovered_config_still_honours_the_cli_defines(
        self, tmp_path: Path
    ) -> None:
        """An auto-discovered `.abicheck.yml` that fails to parse is
        warn-and-continue, deliberately: the user did not ask to bind to it.
        But "continue" must not mean dropping what they *did* type."""
        from abicheck.compile_context import CompileContext

        (tmp_path / ".abicheck.yml").write_text("compile: [this is not a mapping\n")
        assert self._merged(
            CompileContext(defines=("FEATURE_API", "MODE=2")), sources=tmp_path
        ) == ("FEATURE_API", "MODE=2")

    def test_the_internal_composition_hatch_still_carries_the_cli_defines(
        self, tmp_path: Path
    ) -> None:
        """`gcc_options` is an internal-composition-only escape hatch with no
        CLI spelling. It suppresses the config-defines synthesis, so ADR-074's
        own definitions have to be appended on that branch too rather than
        riding along with a synthesis that never happens."""
        from abicheck.compile_context import CompileContext

        cfg = _write_config(tmp_path, "compile:\n  defines:\n    - FROM_CONFIG\n")
        spellings = self._merged(
            CompileContext(gcc_options="-O2", defines=("FEATURE_API",)),
            build_config=cfg,
        )
        assert "FEATURE_API" in spellings
