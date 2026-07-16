"""Coverage for code moved out of cli.py / dumper.py / compat/cli.py into
sibling sub-modules in PR #251. Exercises the moved helpers and command bodies
directly so that the patch-level coverage of the new files reflects what was
already covered when the code lived in the parent modules.
"""
from __future__ import annotations

import errno
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click
import pytest
from click.testing import CliRunner

from abicheck import cli_appcompat as _cli_appcompat
from abicheck.cli import main
from abicheck.cli_appcompat import (
    _handle_list_required_symbols,
    _validate_appcompat_args,
)
from abicheck.compat._errors import _classify_compat_error_exit_code, _compat_fail
from abicheck.diff_platform_templates import (
    _extract_template_args,
    _split_top_level_args,
    _template_outer,
)

# ── cli_suggest: suggest-suppressions ───────────────────────────────────────


class TestSuggestSuppressionsCmd:
    """Cover error paths and happy path of the suggest-suppressions command."""

    def _runner(self) -> CliRunner:
        return CliRunner()

    def test_help(self) -> None:
        result = self._runner().invoke(main, ["suggest-suppressions", "--help"])
        assert result.exit_code == 0
        assert "suggest-suppressions" in result.output.lower() or "DIFF_JSON" in result.output

    def test_invalid_json(self, tmp_path: Path) -> None:
        bad = tmp_path / "broken.json"
        bad.write_text("not-valid-json", encoding="utf-8")
        result = self._runner().invoke(main, ["suggest-suppressions", str(bad)])
        assert result.exit_code != 0
        assert "Cannot read JSON diff" in result.output

    def test_non_object_root(self, tmp_path: Path) -> None:
        f = tmp_path / "diff.json"
        f.write_text(json.dumps(["not-an-object"]), encoding="utf-8")
        result = self._runner().invoke(main, ["suggest-suppressions", str(f)])
        assert result.exit_code != 0
        assert "must be an object" in result.output

    def test_missing_changes_key(self, tmp_path: Path) -> None:
        f = tmp_path / "diff.json"
        f.write_text(json.dumps({"verdict": "compatible"}), encoding="utf-8")
        result = self._runner().invoke(main, ["suggest-suppressions", str(f)])
        assert result.exit_code != 0
        assert "missing required 'changes' key" in result.output

    def test_changes_not_array(self, tmp_path: Path) -> None:
        f = tmp_path / "diff.json"
        f.write_text(json.dumps({"changes": "oops"}), encoding="utf-8")
        result = self._runner().invoke(main, ["suggest-suppressions", str(f)])
        assert result.exit_code != 0
        assert "must be an array" in result.output

    def test_change_entry_wrong_type(self, tmp_path: Path) -> None:
        f = tmp_path / "diff.json"
        f.write_text(json.dumps({"changes": ["scalar-entry"]}), encoding="utf-8")
        result = self._runner().invoke(main, ["suggest-suppressions", str(f)])
        assert result.exit_code != 0
        assert "changes[0] must be an object" in result.output

    def test_happy_path_empty_changes(self, tmp_path: Path) -> None:
        f = tmp_path / "diff.json"
        f.write_text(json.dumps({"changes": []}), encoding="utf-8")
        result = self._runner().invoke(main, ["suggest-suppressions", str(f)])
        assert result.exit_code == 0


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


# ── cli_appcompat: validation helpers ───────────────────────────────────────


class TestValidateAppcompatArgs:
    """Direct unit tests for the appcompat argument validator."""

    def _call(self, **kw: Any) -> None:
        defaults: dict[str, Any] = {
            "weak_mode": False,
            "old_lib": Path("old.so"),
            "new_lib": Path("new.so"),
            "list_symbols": False,
            "old_headers_only": (),
            "new_headers_only": (),
            "old_includes_only": (),
            "new_includes_only": (),
        }
        defaults.update(kw)
        _validate_appcompat_args(**defaults)

    def test_full_mode_with_both_libs_passes(self) -> None:
        self._call()  # should not raise

    def test_weak_mode_with_no_libs_passes(self) -> None:
        self._call(weak_mode=True, old_lib=None, new_lib=None)

    def test_weak_mode_with_positional_lib_fails(self) -> None:
        with pytest.raises(click.UsageError, match="cannot be used with positional"):
            self._call(weak_mode=True, new_lib=None)

    def test_full_mode_missing_old_fails(self) -> None:
        with pytest.raises(click.UsageError, match="Provide OLD_LIB"):
            self._call(old_lib=None)

    def test_full_mode_missing_new_fails(self) -> None:
        with pytest.raises(click.UsageError, match="Provide OLD_LIB"):
            self._call(new_lib=None)

    @pytest.mark.parametrize("kwarg,flag", [
        ("old_headers_only", "--header old="),
        ("new_headers_only", "--header new="),
        ("old_includes_only", "--include old="),
        ("new_includes_only", "--include new="),
    ])
    def test_weak_mode_rejects_per_side_flags(self, kwarg: str, flag: str) -> None:
        with pytest.raises(click.UsageError, match=flag):
            self._call(
                weak_mode=True, old_lib=None, new_lib=None,
                **{kwarg: (Path("h.h"),)},
            )

    def test_list_symbols_rejects_per_side_flags(self) -> None:
        with pytest.raises(click.UsageError, match="--list-required-symbols"):
            self._call(
                list_symbols=True,
                old_headers_only=(Path("h.h"),),
            )


@dataclass
class _FakeReqs:
    needed_libs: list[str]
    undefined_symbols: set[str]
    required_versions: dict[str, str]


class TestHandleListRequiredSymbols:
    """Direct unit tests for the list-required-symbols handler."""

    def _call(self, fmt: str, *, weak_mode: bool = True) -> str:
        captured: list[str] = []

        def _fake_echo(msg: str = "", **_kw: Any) -> None:
            captured.append(msg)

        reqs = _FakeReqs(
            needed_libs=["libfoo.so.1"],
            undefined_symbols={"foo", "bar"},
            required_versions={"GLIBC_2.17": "libc.so.6"},
        )

        def _fake_get_soname(p: Path) -> str:
            return "libfoo.so.1"

        def _fake_parse(_app: Path, _lib: str) -> _FakeReqs:
            return reqs

        orig = _cli_appcompat.click.echo
        _cli_appcompat.click.echo = _fake_echo  # type: ignore[assignment]
        try:
            _handle_list_required_symbols(
                Path("app"),
                Path("lib") if weak_mode else None,
                None if weak_mode else Path("old"),
                None if weak_mode else Path("new"),
                weak_mode=weak_mode, fmt=fmt,
                _get_lib_soname=_fake_get_soname,
                parse_app_requirements=_fake_parse,
            )
        finally:
            _cli_appcompat.click.echo = orig  # type: ignore[assignment]
        return "\n".join(captured)

    def test_text_format(self) -> None:
        out = self._call("markdown")
        assert "libfoo.so.1" in out
        assert "foo" in out and "bar" in out
        assert "GLIBC_2.17" in out

    def test_json_format(self) -> None:
        out = self._call("json")
        data = json.loads(out)
        assert data["library"] == "libfoo.so.1"
        assert sorted(data["required_symbols"]) == ["bar", "foo"]
        assert data["required_versions"] == {"GLIBC_2.17": "libc.so.6"}

    def test_missing_target_lib_raises(self) -> None:
        def _never(p: Path) -> str:
            raise AssertionError("should not be called")

        with pytest.raises(click.UsageError, match="requires a library path"):
            _handle_list_required_symbols(
                Path("app"),
                None, None, None,
                weak_mode=False, fmt="markdown",
                _get_lib_soname=_never,
                parse_app_requirements=_never,  # type: ignore[arg-type]
            )


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
        # one would silently overwrite the other. Both must survive.
        names = [name for _idx, name in slots]
        assert sorted(names) == ["_ZN1C3barEv", "_ZN1C3fooEv"]
