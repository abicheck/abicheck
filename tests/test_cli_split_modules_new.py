"""Coverage for code moved out of cli.py / dumper.py / compat/cli.py into
sibling sub-modules in PR #251. Exercises the moved helpers and command bodies
directly so that the patch-level coverage of the new files reflects what was
already covered when the code lived in the parent modules.
"""
from __future__ import annotations

import errno
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.compat._errors import _classify_compat_error_exit_code, _compat_fail
from abicheck.diff_platform_templates import (
    _extract_template_args,
    _split_top_level_args,
    _template_outer,
)

# ── suppression.suggest_suppressions: library-level coverage ───────────────
# The `suggest-suppressions` CLI command (cli_suggest.py) was deleted in the
# pre-1.0 CLI reset; the JSON-shape validation it used to perform (root must
# be an object, must have a "changes" array of objects) was pure CLI-wrapper
# behavior around reading a diff-report file and is gone with the command.
# `suppression.suggest_suppressions()` itself takes already-parsed
# `list[dict]` and is unchanged — exercise it directly.


class TestSuggestSuppressions:
    """Direct unit tests for suppression.suggest_suppressions()."""

    def test_happy_path_empty_changes(self) -> None:
        from abicheck.suppression import suggest_suppressions

        text = suggest_suppressions([])
        assert "version: 1" in text
        assert "suppressions:" in text


# ── compat/_errors: error classification ────────────────────────────────────


class TestCompatErrors:
    """Exercise the error-classification helpers extracted to compat/_errors."""

    def test_keyboard_interrupt_is_eleven(self) -> None:
        assert _classify_compat_error_exit_code(KeyboardInterrupt()) == 11

    def test_tool_missing_message_is_three(self) -> None:
        assert _classify_compat_error_exit_code(
            RuntimeError("castxml not found in PATH"), context="parsing",
        ) == 3

    def test_compile_failure_is_five(self) -> None:
        assert _classify_compat_error_exit_code(
            RuntimeError("castxml failed: cannot compile"),
        ) == 5

    def test_descriptor_context_is_six(self) -> None:
        assert _classify_compat_error_exit_code(
            ValueError("bad XML"), context="parsing descriptor",
        ) == 6

    def test_report_context_is_seven(self) -> None:
        assert _classify_compat_error_exit_code(
            RuntimeError("oops"), context="writing report",
        ) == 7

    def test_dump_context_is_eight(self) -> None:
        assert _classify_compat_error_exit_code(
            RuntimeError("snapshot failed"), context="running dump pipeline",
        ) == 8

    def test_fallback_is_ten(self) -> None:
        assert _classify_compat_error_exit_code(RuntimeError("unknown")) == 10

    def test_file_not_found_is_four(self, tmp_path: Path) -> None:
        exc = FileNotFoundError(2, "No such file or directory", str(tmp_path / "x"))
        assert _classify_compat_error_exit_code(exc, context="reading input") == 4

    def test_permission_error_is_four(self) -> None:
        assert _classify_compat_error_exit_code(PermissionError("denied")) == 4

    def test_os_error_eacces_is_four(self) -> None:
        exc = OSError(errno.EACCES, "access denied")
        assert _classify_compat_error_exit_code(exc) == 4

    def test_os_error_in_report_context_is_seven(self) -> None:
        exc = OSError(errno.ENOSPC, "no space")
        assert _classify_compat_error_exit_code(exc, context="writing report") == 7

    def test_unrelated_os_error_falls_through(self) -> None:
        # ENOSPC isn't classified by _classify_fs_error and the message has no
        # known token, so we land on the catch-all (10).
        assert _classify_compat_error_exit_code(OSError(errno.ENOSPC, "no space")) == 10

    def test_compat_fail_exits_with_code(self) -> None:
        with pytest.raises(SystemExit) as exc_info:
            _compat_fail("loading descriptor", FileNotFoundError("missing"))
        # FileNotFoundError → 4 (cannot access input files) unless tool-missing
        assert exc_info.value.code == 4


# ── cli_stack: command help / argument validation ───────────────────────────


class TestCliStackBasics:
    """Cover the surface of the deps / stack-check commands beyond their core
    pipeline (which requires real ELF binaries and is already covered by the
    existing integration-marker tests)."""

    def test_deps_help(self) -> None:
        result = CliRunner().invoke(main, ["deps", "--help"])
        assert result.exit_code == 0
        assert "dependency tree" in result.output.lower()

    def test_stack_check_help(self) -> None:
        result = CliRunner().invoke(main, ["deps", "compare", "--help"])
        assert result.exit_code == 0
        assert "stack" in result.output.lower()

    def test_stack_check_same_baseline_candidate_rejected(self, tmp_path: Path) -> None:
        result = CliRunner().invoke(main, [
            "deps", "compare", "usr/bin/myapp",
            "--old-root", str(tmp_path),
            "--new-root", str(tmp_path),
        ])
        assert result.exit_code != 0
        assert "same sysroot" in result.output

    def test_deps_rejects_non_elf(self, tmp_path: Path) -> None:
        f = tmp_path / "fake.txt"
        f.write_text("hello", encoding="utf-8")
        result = CliRunner().invoke(main, ["deps", "tree", str(f)])
        assert result.exit_code != 0
        assert "requires an ELF binary" in result.output

    def test_stack_check_rejects_non_elf(self, tmp_path: Path) -> None:
        baseline = tmp_path / "baseline"
        candidate = tmp_path / "candidate"
        baseline.mkdir()
        candidate.mkdir()
        (baseline / "fake").write_text("hello", encoding="utf-8")
        (candidate / "fake").write_text("hello", encoding="utf-8")
        result = CliRunner().invoke(main, [
            "deps", "compare", "fake",
            "--old-root", str(baseline),
            "--new-root", str(candidate),
        ])
        assert result.exit_code != 0
        assert "requires an ELF binary" in result.output


# ── diff_platform_templates: pure helpers ───────────────────────────────────


class TestTemplateHelpers:
    """Cover the standalone string helpers in diff_platform_templates."""

    @pytest.mark.parametrize("type_str,expected", [
        ("std::vector<int>", ["int"]),
        ("std::map<int, double>", ["int", "double"]),
        ("Foo<Bar<int>, double>", ["Bar<int>", "double"]),
        ("std::vector<>", []),
        ("std::function<void(int, double)>", ["void(int, double)"]),
        ("int", None),
        ("std::vector<int", None),  # unbalanced
        # Regression: angle depth must unwind even when '>' appears while
        # parentheses are open (function-pointer templates).
        (
            "Foo<void (*)(std::vector<int>), double>",
            ["void (*)(std::vector<int>)", "double"],
        ),
    ])
    def test_extract_template_args(
        self, type_str: str, expected: list[str] | None,
    ) -> None:
        assert _extract_template_args(type_str) == expected

    @pytest.mark.parametrize("type_str,expected", [
        ("std::vector<int>", "std::vector"),
        ("std::map<int, double>", "std::map"),
        ("Foo<Bar<int>>", "Foo"),
        ("int", "int"),
    ])
    def test_template_outer(self, type_str: str, expected: str) -> None:
        assert _template_outer(type_str) == expected

    def test_split_top_level_args_respects_nesting(self) -> None:
        assert _split_top_level_args("int, Foo<int, double>, char") == [
            "int", "Foo<int, double>", "char",
        ]

    def test_split_top_level_args_respects_parens(self) -> None:
        assert _split_top_level_args("void(int, double), char") == [
            "void(int, double)", "char",
        ]

    def test_split_top_level_args_unwinds_angle_through_open_paren(self) -> None:
        """Regression: '>' inside an open '(' must still pop angle depth."""
        # Pre-fix this would not split at the outer comma because the angle
        # depth got stuck while parentheses were open.
        assert _split_top_level_args(
            "void (*)(std::vector<int>), double",
        ) == ["void (*)(std::vector<int>)", "double"]


# ── dumper_castxml: enum + vtable helpers ───────────────────────────────────


class TestCastxmlEnumHexInit:
    """Regression test for hex/octal enum initializers."""

    def test_hex_enum_value_parsed(self) -> None:
        from xml.etree.ElementTree import Element

        from abicheck.dumper_castxml import _CastxmlParser

        root = Element("CastXML")
        enum = Element("Enumeration", id="e1", name="Flags")
        enum.append(Element("EnumValue", name="A", init="0x10"))
        enum.append(Element("EnumValue", name="B", init="0x20"))
        enum.append(Element("EnumValue", name="C", init="-1"))
        root.append(enum)
        parser = _CastxmlParser(root, set(), set())
        [e] = parser.parse_enums()
        assert {m.name: m.value for m in e.members} == {"A": 16, "B": 32, "C": -1}


class TestCastxmlVtableUnindexed:
    """Regression test: multiple virtuals without vtable_index must not collapse."""

    def test_unindexed_virtuals_kept_separately(self) -> None:
        from xml.etree.ElementTree import Element

        from abicheck.dumper_castxml import _CastxmlParser

        root = Element("CastXML")
        cls = Element("Class", id="c1", name="C", members="")
        root.append(cls)
        # Two virtual methods, neither with a vtable_index attribute.
        root.append(Element(
            "Method", id="m1", name="foo", mangled="_ZN1C3fooEv",
            virtual="1", context="c1",
        ))
        root.append(Element(
            "Method", id="m2", name="bar", mangled="_ZN1C3barEv",
            virtual="1", context="c1",
        ))
        parser = _CastxmlParser(root, set(), set())
        slots = parser._collect_virtual_methods("c1")
        # Pre-fix, both methods would land on the same `None` key in a dict and
        # one would silently overwrite the other. Neither overrides the other
        # (no `overrides` attribute), so both must survive as distinct slots
        # keyed by their own method id.
        names = [name for _idx, name in slots.values()]
        assert sorted(names) == ["_ZN1C3barEv", "_ZN1C3fooEv"]
