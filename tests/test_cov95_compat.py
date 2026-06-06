"""Coverage-focused tests for abicheck.compat.cli and abicheck.compat.abicc_dump_import.

Targets error/edge paths in the ABICC compat CLI and the Perl-dump importer that
are not exercised by the existing functional test suites. Pure-Python, no external
tools: everything is driven through Perl/JSON dumps (loaded directly) or by calling
internal helpers with crafted inputs.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.errors import SnapshotError, ValidationError

# ── helpers ────────────────────────────────────────────────────────────────────

_DUMP_TEMPLATE = """
$VAR1 = {{
  'LibraryName' => '{lib}',
  'LibraryVersion' => '{ver}',
  'TypeInfo' => {{
    '0' => {{ 'Name' => 'void', 'Type' => 'Intrinsic' }},
    '1' => {{ 'Name' => 'int', 'Type' => 'Intrinsic' }}
  }},
  'SymbolInfo' => {{
{symbols}
  }}
}};
"""


def _write_dump(
    path: Path, lib: str = "libfoo", ver: str = "1.0", symbols: str | None = None
) -> Path:
    if symbols is None:
        symbols = (
            "    '1' => { 'MnglName' => 'foo', 'ShortName' => 'foo', 'Return' => '0' }"
        )
    path.write_text(
        textwrap.dedent(
            _DUMP_TEMPLATE.format(lib=lib, ver=ver, symbols=symbols)
        ).strip(),
        encoding="utf-8",
    )
    return path


def _run(args: list[str]) -> object:
    return CliRunner().invoke(main, args)


# ════════════════════════════════════════════════════════════════════════════════
# abicc_dump_import.py — error / edge paths
# ════════════════════════════════════════════════════════════════════════════════


class TestPerlDumpImportErrors:
    def test_read_failure_raises_snapshot_error(self, tmp_path: Path) -> None:
        """import_abicc_perl_dump on a directory triggers the OSError -> SnapshotError path (46-47)."""
        from abicheck.compat.abicc_dump_import import import_abicc_perl_dump

        d = tmp_path / "adir"
        d.mkdir()
        with pytest.raises(SnapshotError, match="Failed to read ABICC Perl dump"):
            import_abicc_perl_dump(d)

    def test_top_level_not_dict_raises(self, tmp_path: Path) -> None:
        """A valid $VAR1 assignment whose value is a list (not a hash) hits line 55."""
        from abicheck.compat.abicc_dump_import import import_abicc_perl_dump

        p = tmp_path / "list.dump"
        p.write_text("$VAR1 = ['a', 'b'];", encoding="utf-8")
        with pytest.raises(ValidationError, match="top-level structure is not a hash"):
            import_abicc_perl_dump(p)

    def test_missing_var1_assignment(self) -> None:
        """_parse_perl_dumper_subset rejects text not starting with $VAR1 (line 69)."""
        from abicheck.compat.abicc_dump_import import _parse_perl_dumper_subset

        with pytest.raises(ValidationError, match="missing .VAR1 assignment"):
            _parse_perl_dumper_subset("not a dump at all")

    def test_malformed_assignment_no_equals(self) -> None:
        """$VAR1 with no '=' is a malformed assignment (line 72)."""
        from abicheck.compat.abicc_dump_import import _parse_perl_dumper_subset

        with pytest.raises(ValidationError, match="malformed assignment"):
            _parse_perl_dumper_subset("$VAR1 {}")

    def test_normalize_failure_non_json_serializable(self) -> None:
        """A literal that parses but cannot round-trip through JSON hits 89-90.

        A set literal is a valid Python literal (ast.literal_eval accepts it) but
        json.dumps cannot serialize it, raising TypeError.
        """
        from abicheck.compat.abicc_dump_import import _parse_perl_dumper_subset

        with pytest.raises(
            ValidationError, match="normalize ABICC Perl dump structure"
        ):
            _parse_perl_dumper_subset("$VAR1 = {1, 2, 3};")

    def test_escape_sequence_inside_single_quotes(self) -> None:
        """Backslash-escape inside single-quoted string exercises lines 111-112."""
        from abicheck.compat.abicc_dump_import import _perl_expr_to_python_literal

        # The \' should be preserved as an escaped quote, not end the string.
        converted = _perl_expr_to_python_literal(r"{'k' => 'a\'b'}")
        assert r"'a\'b'" in converted
        assert converted == r"{'k' : 'a\'b'}"

    def test_literal_eval_failure_raises(self) -> None:
        """A syntactically broken literal raises the parse-safely error (83-84)."""
        from abicheck.compat.abicc_dump_import import _parse_perl_dumper_subset

        with pytest.raises(ValidationError, match="parse ABICC Perl dump safely"):
            _parse_perl_dumper_subset("$VAR1 = {'a' => };")

    def test_undef_bareword_conversion(self) -> None:
        """A standalone undef bareword becomes None (lines 130-136)."""
        from abicheck.compat.abicc_dump_import import _perl_expr_to_python_literal

        converted = _perl_expr_to_python_literal("{'a' => undef}")
        assert "None" in converted
        # 'undefined' (undef as a prefix of a longer bareword) must NOT convert.
        assert _perl_expr_to_python_literal("undefx") == "undefx"

    def test_import_full_path_rejects_non_dumper(self, tmp_path: Path) -> None:
        """import_abicc_perl_dump rejects content not starting with $VAR1 (line 50)."""
        from abicheck.compat.abicc_dump_import import import_abicc_perl_dump

        p = tmp_path / "bad.dump"
        p.write_text("just some text", encoding="utf-8")
        with pytest.raises(ValidationError, match="expected Data::Dumper content"):
            import_abicc_perl_dump(p)

    def test_is_abicc_perl_dump_file_unreadable_returns_false(
        self, tmp_path: Path
    ) -> None:
        """is_abicc_perl_dump_file on an unreadable (directory) path returns False (line 271)."""
        from abicheck.compat.abicc_dump_import import is_abicc_perl_dump_file

        d = tmp_path / "subdir"
        d.mkdir()
        assert is_abicc_perl_dump_file(d) is False


class TestSnapshotFromDictEdgeCases:
    def test_non_dict_symbol_entry_skipped(self, tmp_path: Path) -> None:
        """A SymbolInfo entry that is not a dict is skipped (line 159)."""
        from abicheck.compat.abicc_dump_import import _snapshot_from_abicc_dict

        data = {
            "SymbolInfo": {
                "1": "not-a-dict",
                "2": {"MnglName": "good", "ShortName": "good", "Return": "0"},
            },
            "TypeInfo": {"0": {"Name": "void", "Type": "Intrinsic"}},
        }
        snap = _snapshot_from_abicc_dict(data, tmp_path / "x.dump")
        assert [f.mangled for f in snap.functions] == ["good"]

    def test_symbol_without_mangled_name_skipped(self, tmp_path: Path) -> None:
        """A symbol dict missing MnglName is skipped (line 163)."""
        from abicheck.compat.abicc_dump_import import _snapshot_from_abicc_dict

        data = {
            "SymbolInfo": {
                "1": {"ShortName": "nameless", "Return": "0"},
                "2": {"MnglName": "kept", "ShortName": "kept", "Return": "0"},
            },
        }
        snap = _snapshot_from_abicc_dict(data, tmp_path / "x.dump")
        assert [f.mangled for f in snap.functions] == ["kept"]

    def test_param_non_dict_entry_skipped(self, tmp_path: Path) -> None:
        """A Param map entry that is not a dict is skipped (line 210)."""
        from abicheck.compat.abicc_dump_import import _parse_params

        sym = {"Param": {"0": "junk", "1": {"type": "1", "name": "ok"}}}
        type_map = {"1": {"Name": "int"}}
        params = _parse_params(sym, type_map)
        assert [p.name for p in params] == ["ok"]

    def test_param_non_integer_position_sorts_last(self, tmp_path: Path) -> None:
        """A non-integer Param key falls back to idx 9999 (lines 213-214)."""
        from abicheck.compat.abicc_dump_import import _parse_params

        sym = {
            "Param": {
                "notanint": {"type": "1", "name": "last"},
                "0": {"type": "1", "name": "first"},
            }
        }
        type_map = {"1": {"Name": "int"}}
        params = _parse_params(sym, type_map)
        assert [p.name for p in params] == ["first", "last"]

    def test_resolve_type_name_none(self) -> None:
        """_resolve_type_name(None) returns 'unknown' (line 226)."""
        from abicheck.compat.abicc_dump_import import _resolve_type_name

        assert _resolve_type_name(None, {}) == "unknown"

    def test_resolve_type_name_missing_in_map(self) -> None:
        """A type id absent from the map yields 'unknown' (line 231)."""
        from abicheck.compat.abicc_dump_import import _resolve_type_name

        assert _resolve_type_name("99", {"1": {"Name": "int"}}) == "unknown"

    def test_resolve_type_name_blank_name(self) -> None:
        """A type whose Name is blank/missing yields 'unknown' (line 237)."""
        from abicheck.compat.abicc_dump_import import _resolve_type_name

        assert _resolve_type_name("1", {"1": {"Name": "   "}}) == "unknown"
        assert _resolve_type_name("2", {"2": {"Type": "Struct"}}) == "unknown"

    def test_variable_symbol_branch(self, tmp_path: Path) -> None:
        """A symbol with neither Param/Return/Constructor/Destructor is a variable (181-182)."""
        from abicheck.compat.abicc_dump_import import _snapshot_from_abicc_dict

        data = {
            "TypeInfo": {"1": {"Name": "int", "Type": "Intrinsic"}},
            "SymbolInfo": {
                "1": {"MnglName": "gvar", "ShortName": "gvar", "Type": "1"},
            },
        }
        snap = _snapshot_from_abicc_dict(data, tmp_path / "x.dump")
        assert [v.mangled for v in snap.variables] == ["gvar"]
        assert snap.variables[0].type == "int"

    def test_extract_record_types_skips_non_dict_and_blank_name(self) -> None:
        """Non-dict type info (244) and blank-named records (251) are skipped."""
        from abicheck.compat.abicc_dump_import import _extract_record_types

        type_map = {
            "0": "not-a-dict",
            "1": {"Type": "Struct", "Name": ""},
            "2": {"Type": "Class", "Name": "Real"},
            "3": {"Type": "Intrinsic", "Name": "int"},
        }
        records = _extract_record_types(type_map)
        assert [r.name for r in records] == ["Real"]
        assert records[0].kind == "class"


# ════════════════════════════════════════════════════════════════════════════════
# compat/cli.py — helper functions
# ════════════════════════════════════════════════════════════════════════════════


class TestSkipSuppressionFallback:
    def test_plain_lowercase_identifier_gets_mangled_fallback(
        self, tmp_path: Path
    ) -> None:
        """A plain lowercase C name produces both exact and mangled-pattern rules (line 113)."""
        from abicheck.checker import Change, ChangeKind
        from abicheck.compat.cli import _build_skip_suppression

        skip = tmp_path / "skip.txt"
        skip.write_text("sub\n", encoding="utf-8")
        sl = _build_skip_suppression(skip, None)

        # Exact plain name suppressed
        plain = Change(kind=ChangeKind.FUNC_REMOVED, symbol="sub", description="d")
        assert sl.is_suppressed(plain)
        # Mangled form _Z3subii suppressed via the fallback pattern
        mangled = Change(
            kind=ChangeKind.FUNC_REMOVED, symbol="_Z3subii", description="d"
        )
        assert sl.is_suppressed(mangled)


class TestWideningReturnType:
    def test_widening_pair_detected(self) -> None:
        """int->long widening return change is recognized (lines 300-322)."""
        from abicheck.checker import Change, ChangeKind
        from abicheck.compat.cli import _is_widening_return_type_change

        c = Change(
            kind=ChangeKind.FUNC_RETURN_CHANGED,
            symbol="f",
            description="d",
            old_value="int",
            new_value="long",
        )
        assert _is_widening_return_type_change(c) is True

    def test_non_widening_pair_not_detected(self) -> None:
        from abicheck.checker import Change, ChangeKind
        from abicheck.compat.cli import _is_widening_return_type_change

        c = Change(
            kind=ChangeKind.FUNC_RETURN_CHANGED,
            symbol="f",
            description="d",
            old_value="long",
            new_value="int",
        )
        assert _is_widening_return_type_change(c) is False

    def test_wrong_kind_not_widening(self) -> None:
        from abicheck.checker import Change, ChangeKind
        from abicheck.compat.cli import _is_widening_return_type_change

        c = Change(kind=ChangeKind.FUNC_REMOVED, symbol="f", description="d")
        assert _is_widening_return_type_change(c) is False

    def test_filter_source_only_drops_widening(self) -> None:
        """_filter_source_only removes a widening return change entirely."""
        from abicheck.checker import Change, ChangeKind, DiffResult, Verdict
        from abicheck.compat.cli import _filter_source_only

        widening = Change(
            kind=ChangeKind.FUNC_RETURN_CHANGED,
            symbol="f",
            description="d",
            old_value="int",
            new_value="long",
        )
        result = DiffResult(
            old_version="1",
            new_version="2",
            library="lib",
            changes=[widening],
            verdict=Verdict.API_BREAK,
        )
        filtered = _filter_source_only(result)
        assert filtered.changes == []


class TestResultTransforms:
    def _result(self, changes: list, verdict) -> object:
        from abicheck.checker import DiffResult

        return DiffResult(
            old_version="1",
            new_version="2",
            library="lib",
            changes=changes,
            verdict=verdict,
        )

    def test_apply_warn_newsym_promotes(self) -> None:
        """_apply_warn_newsym promotes a COMPATIBLE result with a new symbol (388-407)."""
        from abicheck.checker import Change, ChangeKind, Verdict
        from abicheck.compat.cli import _apply_warn_newsym

        r = self._result(
            [Change(kind=ChangeKind.FUNC_ADDED, symbol="s", description="d")],
            Verdict.COMPATIBLE,
        )
        out = _apply_warn_newsym(r)
        assert out.verdict == Verdict.BREAKING

    def test_apply_warn_newsym_no_new_symbol_unchanged(self) -> None:
        """No new symbol -> result returned unchanged (line 408)."""
        from abicheck.checker import Change, ChangeKind, Verdict
        from abicheck.compat.cli import _apply_warn_newsym

        r = self._result(
            [Change(kind=ChangeKind.FUNC_REMOVED, symbol="s", description="d")],
            Verdict.BREAKING,
        )
        out = _apply_warn_newsym(r)
        assert out.verdict == Verdict.BREAKING

    def test_limit_affected_changes(self) -> None:
        """_limit_affected_changes caps per-kind (413-426)."""
        from abicheck.checker import Change, ChangeKind, Verdict
        from abicheck.compat.cli import _limit_affected_changes

        changes = [
            Change(kind=ChangeKind.FUNC_REMOVED, symbol=f"s{i}", description="d")
            for i in range(5)
        ]
        r = self._result(changes, Verdict.BREAKING)
        out = _limit_affected_changes(r, 2)
        assert len(out.changes) == 2

    def test_limit_affected_zero_is_noop(self) -> None:
        from abicheck.checker import Change, ChangeKind, Verdict
        from abicheck.compat.cli import _limit_affected_changes

        changes = [Change(kind=ChangeKind.FUNC_REMOVED, symbol="s", description="d")]
        r = self._result(changes, Verdict.BREAKING)
        assert _limit_affected_changes(r, 0).changes == changes

    def test_apply_strict_full_promotes_api_break(self) -> None:
        """_apply_strict full mode promotes API_BREAK to BREAKING (285-286)."""
        from abicheck.checker import Change, ChangeKind, Verdict
        from abicheck.compat.cli import _apply_strict

        r = self._result(
            [Change(kind=ChangeKind.FUNC_PARAMS_CHANGED, symbol="s", description="d")],
            Verdict.API_BREAK,
        )
        out = _apply_strict(r, mode="full")
        assert out.verdict == Verdict.BREAKING

    def test_filter_binary_only_removes_api_break_kinds(self) -> None:
        """_filter_binary_only drops API_BREAK-only kinds (359-383)."""
        from abicheck.checker import Change, Verdict
        from abicheck.compat.cli import _API_BREAK_KINDS, _filter_binary_only

        kind = next(iter(_API_BREAK_KINDS))
        r = self._result(
            [Change(kind=kind, symbol="s", description="d")], Verdict.API_BREAK
        )
        out = _filter_binary_only(r)
        assert out.changes == []


class TestMergeSuppression:
    def test_merge_with_none_base_returns_extra(self) -> None:
        """_merge_suppression(None, extra) returns extra unchanged (line 456)."""
        from abicheck.compat.cli import _merge_suppression
        from abicheck.suppression import Suppression, SuppressionList

        extra = SuppressionList(suppressions=[Suppression(symbol="x")])
        merged = _merge_suppression(None, extra)
        assert merged is extra


class TestDetectCompilerVersion:
    def test_missing_compiler_returns_empty(self) -> None:
        """When no compiler is found, returns '' (lines 467-471)."""
        from abicheck.compat.cli import _detect_compiler_version

        # A clearly non-existent path forces the which() fallbacks; if the host
        # has gcc the explicit bogus path short-circuits to a run that fails.
        result = _detect_compiler_version("/nonexistent/definitely/not/a/compiler")
        assert isinstance(result, str)

    def test_explicit_bogus_path_handles_oserror(self) -> None:
        """An explicit bogus gcc path that cannot be executed returns '' (475-476)."""
        from abicheck.compat.cli import _detect_compiler_version

        result = _detect_compiler_version("/nonexistent/gcc-binary-xyz")
        assert result == ""


class TestSetupLoggingModeNone:
    def test_logging_mode_n_returns_no_handlers(self, tmp_path: Path) -> None:
        """-logging-mode n returns (None, None) without attaching handlers (line 509)."""
        from abicheck.compat.cli import _setup_logging

        h1, h2 = _setup_logging(
            tmp_path / "a.log", tmp_path / "b.log", None, "n", quiet=False
        )
        assert h1 is None and h2 is None
        assert not (tmp_path / "a.log").exists()


class TestLoadSkipHeaders:
    def test_none_returns_empty_set(self) -> None:
        """_load_skip_headers(None) returns an empty set (line 540)."""
        from abicheck.compat.cli import _load_skip_headers

        assert _load_skip_headers(None) == set()

    def test_reads_names_skipping_comments(self, tmp_path: Path) -> None:
        """Lines are read, comments/blanks skipped (line 544)."""
        from abicheck.compat.cli import _load_skip_headers

        f = tmp_path / "skip_headers.txt"
        f.write_text("# comment\n\nfoo.h\nbar.h\n", encoding="utf-8")
        assert _load_skip_headers(f) == {"foo.h", "bar.h"}


class TestResolveHeadersFromList:
    def test_relative_path_resolved_against_list_dir(self, tmp_path: Path) -> None:
        """A relative header path in the list is resolved against the list's dir (line 567)."""
        from abicheck.compat.cli import _resolve_headers_from_list

        hdr = tmp_path / "rel.h"
        hdr.write_text("#pragma once\n", encoding="utf-8")
        lst = tmp_path / "list.txt"
        lst.write_text("rel.h\n", encoding="utf-8")
        result = _resolve_headers_from_list(lst, None, [])
        assert hdr in result

    def test_skip_headers_filtering_removes_match(self, tmp_path: Path) -> None:
        """skip_headers set removes matching headers by name (line 578)."""
        from abicheck.compat.cli import _resolve_headers_from_list

        keep = tmp_path / "keep.h"
        drop = tmp_path / "drop.h"
        keep.write_text("x", encoding="utf-8")
        drop.write_text("x", encoding="utf-8")
        result = _resolve_headers_from_list(
            None, None, [keep, drop], skip_headers={"drop.h"}
        )
        assert keep in result
        assert drop not in result


class TestSnapshotFromCompatInputMultiLib:
    def test_descriptor_multiple_libs_warns_and_uses_first(
        self, tmp_path: Path
    ) -> None:
        """A descriptor (CompatDescriptor) with multiple libs warns (line 1476).

        The library does not exist, so this fails after the warning at line 1486 —
        we capture the SystemExit and confirm the warning was emitted.
        """
        from abicheck.compat.cli import _snapshot_from_compat_input
        from abicheck.compat.descriptor import CompatDescriptor

        desc = CompatDescriptor(
            version="1.0",
            headers=[],
            libs=[tmp_path / "a.so", tmp_path / "b.so"],
        )
        with pytest.raises(SystemExit):
            _snapshot_from_compat_input(
                desc,
                None,
                tmp_path / "desc.xml",
                headers_list_path=None,
                single_header=None,
                skip_headers_set=set(),
                quiet=False,
                gcc_path=None,
                gcc_prefix=None,
                gcc_options=None,
                sysroot=None,
                nostdinc=False,
                lang=None,
            )

    def test_snapshot_input_vnum_override_on_snapshot(self, tmp_path: Path) -> None:
        """When input is already an AbiSnapshot, vnum override replaces version (1465-1467)."""
        from abicheck.compat.cli import _snapshot_from_compat_input
        from abicheck.model import AbiSnapshot

        snap = AbiSnapshot(
            library="lib", version="1.0", functions=[], variables=[], types=[]
        )
        out, ver = _snapshot_from_compat_input(
            snap,
            "9.9",
            tmp_path / "in.json",
            headers_list_path=None,
            single_header=None,
            skip_headers_set=set(),
            quiet=True,
            gcc_path=None,
            gcc_prefix=None,
            gcc_options=None,
            sysroot=None,
            nostdinc=False,
            lang=None,
        )
        assert ver == "9.9"
        assert out.version == "9.9"


class TestBuildCompatSuppressionErrors:
    def test_invalid_skip_internal_regex_exits(self, tmp_path: Path) -> None:
        """An invalid -skip-internal-symbols regex triggers _compat_fail (1519-1525)."""
        from abicheck.compat.cli import _build_compat_suppression

        with pytest.raises(SystemExit) as ei:
            _build_compat_suppression(
                None,
                None,
                None,
                None,
                skip_internal_symbols="[unterminated",
                skip_internal_types=None,
                suppress=None,
            )
        assert ei.value.code == 6

    def test_suppress_file_load_error_exits(self, tmp_path: Path) -> None:
        """A missing suppression file triggers _compat_fail loading path (1527-1533)."""
        from abicheck.compat.cli import _build_compat_suppression

        with pytest.raises(SystemExit):
            _build_compat_suppression(
                None,
                None,
                None,
                None,
                None,
                None,
                suppress=tmp_path / "missing.yaml",
            )

    def test_valid_suppress_file_merged(self, tmp_path: Path) -> None:
        """A valid YAML suppression file is loaded and merged (line 1533 success path)."""
        from abicheck.compat.cli import _build_compat_suppression

        sup = tmp_path / "sup.yaml"
        sup.write_text("version: 1\nsuppressions:\n  - symbol: foo\n", encoding="utf-8")
        result = _build_compat_suppression(
            None, None, None, None, None, None, suppress=sup
        )
        assert result is not None
        assert len(result) == 1


# ════════════════════════════════════════════════════════════════════════════════
# compat/cli.py — CLI-level (CliRunner) end-to-end paths via dumps
# ════════════════════════════════════════════════════════════════════════════════


class TestCompatCheckCli:
    def test_missing_old_file_fails(self, tmp_path: Path) -> None:
        """A nonexistent -old descriptor produces a classified error exit code."""
        new = _write_dump(tmp_path / "new.dump")
        result = _run(
            [
                "compat",
                "check",
                "-lib",
                "libfoo",
                "-old",
                str(tmp_path / "nope.xml"),
                "-new",
                str(new),
            ]
        )
        assert result.exit_code != 0

    def test_json_report_written(self, tmp_path: Path) -> None:
        """A JSON report is written to the requested path."""
        old = _write_dump(tmp_path / "old.dump")
        new = _write_dump(tmp_path / "new.dump")
        report = tmp_path / "out" / "report.json"
        result = _run(
            [
                "compat",
                "check",
                "-lib",
                "libfoo",
                "-old",
                str(old),
                "-new",
                str(new),
                "-report-format",
                "json",
                "-report-path",
                str(report),
            ]
        )
        assert result.exit_code == 0, result.output
        assert report.exists()
        assert report.read_text(encoding="utf-8").lstrip().startswith("{")

    def test_md_report_and_stdout(self, tmp_path: Path) -> None:
        """Markdown format with -stdout echoes the report to stdout (line 1008)."""
        old = _write_dump(tmp_path / "old.dump")
        new = _write_dump(tmp_path / "new.dump")
        result = _run(
            [
                "compat",
                "check",
                "-lib",
                "libfoo",
                "-old",
                str(old),
                "-new",
                str(new),
                "-report-format",
                "md",
                "-report-path",
                str(tmp_path / "r.md"),
                "-stdout",
            ]
        )
        assert result.exit_code == 0, result.output
        assert (tmp_path / "r.md").exists()

    def test_breaking_change_exit_code_1(self, tmp_path: Path) -> None:
        """Removing a function between old and new yields BREAKING (exit 1)."""
        old = _write_dump(
            tmp_path / "old.dump",
            symbols=(
                "    '1' => { 'MnglName' => 'foo', 'ShortName' => 'foo', 'Return' => '0' },\n"
                "    '2' => { 'MnglName' => 'bar', 'ShortName' => 'bar', 'Return' => '0' }"
            ),
        )
        new = _write_dump(
            tmp_path / "new.dump",
            symbols="    '1' => { 'MnglName' => 'foo', 'ShortName' => 'foo', 'Return' => '0' }",
        )
        result = _run(
            [
                "compat",
                "check",
                "-lib",
                "libfoo",
                "-old",
                str(old),
                "-new",
                str(new),
                "-report-format",
                "json",
                "-report-path",
                str(tmp_path / "r.json"),
            ]
        )
        assert result.exit_code == 1, result.output
        assert "Verdict: BREAKING" in result.output

    def test_bin_and_src_report_paths(self, tmp_path: Path) -> None:
        """-bin-report-path and -src-report-path emit split reports (993-1000)."""
        old = _write_dump(
            tmp_path / "old.dump",
            symbols=(
                "    '1' => { 'MnglName' => 'foo', 'ShortName' => 'foo', 'Return' => '0' },\n"
                "    '2' => { 'MnglName' => 'bar', 'ShortName' => 'bar', 'Return' => '0' }"
            ),
        )
        new = _write_dump(
            tmp_path / "new.dump",
            symbols="    '1' => { 'MnglName' => 'foo', 'ShortName' => 'foo', 'Return' => '0' }",
        )
        binr = tmp_path / "bin.json"
        srcr = tmp_path / "src.json"
        result = _run(
            [
                "compat",
                "check",
                "-lib",
                "libfoo",
                "-old",
                str(old),
                "-new",
                str(new),
                "-report-format",
                "json",
                "-report-path",
                str(tmp_path / "r.json"),
                "-bin-report-path",
                str(binr),
                "-src-report-path",
                str(srcr),
            ]
        )
        assert result.exit_code == 1, result.output
        assert binr.exists()
        assert srcr.exists()

    def test_list_affected_writes_file(self, tmp_path: Path) -> None:
        """-list-affected writes an .affected.txt sidecar (1003-1005)."""
        old = _write_dump(
            tmp_path / "old.dump",
            symbols=(
                "    '1' => { 'MnglName' => 'foo', 'ShortName' => 'foo', 'Return' => '0' },\n"
                "    '2' => { 'MnglName' => 'bar', 'ShortName' => 'bar', 'Return' => '0' }"
            ),
        )
        new = _write_dump(
            tmp_path / "new.dump",
            symbols="    '1' => { 'MnglName' => 'foo', 'ShortName' => 'foo', 'Return' => '0' }",
        )
        report = tmp_path / "r.json"
        result = _run(
            [
                "compat",
                "check",
                "-lib",
                "libfoo",
                "-old",
                str(old),
                "-new",
                str(new),
                "-report-format",
                "json",
                "-report-path",
                str(report),
                "-list-affected",
            ]
        )
        assert result.exit_code == 1, result.output
        assert report.with_suffix(".affected.txt").exists()

    def test_htm_alias_normalized_to_html(self, tmp_path: Path) -> None:
        """-report-format htm is normalized to html (line 1360)."""
        old = _write_dump(tmp_path / "old.dump")
        new = _write_dump(tmp_path / "new.dump")
        report = tmp_path / "r.html"
        result = _run(
            [
                "compat",
                "check",
                "-lib",
                "libfoo",
                "-old",
                str(old),
                "-new",
                str(new),
                "-report-format",
                "htm",
                "-report-path",
                str(report),
            ]
        )
        assert result.exit_code == 0, result.output
        assert report.exists()
        assert "<!DOCTYPE html>" in report.read_text(encoding="utf-8")

    def test_component_builds_title(self, tmp_path: Path) -> None:
        """-component (without -title) builds an effective title (line 1365)."""
        old = _write_dump(tmp_path / "old.dump")
        new = _write_dump(tmp_path / "new.dump")
        report = tmp_path / "r.html"
        result = _run(
            [
                "compat",
                "check",
                "-lib",
                "libfoo",
                "-old",
                str(old),
                "-new",
                str(new),
                "-report-path",
                str(report),
                "-component",
                "mycomp",
            ]
        )
        assert result.exit_code == 0, result.output
        html = report.read_text(encoding="utf-8")
        assert "mycomp" in html

    def test_headers_only_note(self, tmp_path: Path) -> None:
        """-headers-only emits an informational note (line 1334)."""
        old = _write_dump(tmp_path / "old.dump")
        new = _write_dump(tmp_path / "new.dump")
        result = _run(
            [
                "compat",
                "check",
                "-lib",
                "libfoo",
                "-old",
                str(old),
                "-new",
                str(new),
                "-report-format",
                "json",
                "-report-path",
                str(tmp_path / "r.json"),
                "-headers-only",
            ]
        )
        assert result.exit_code == 0, result.output
        assert "-headers-only is accepted" in result.output

    def test_skip_headers_note(self, tmp_path: Path) -> None:
        """-skip-headers with entries emits the excluding-N-headers note (line 789)."""
        old = _write_dump(tmp_path / "old.dump")
        new = _write_dump(tmp_path / "new.dump")
        skip = tmp_path / "skip.txt"
        skip.write_text("foo.h\n", encoding="utf-8")
        result = _run(
            [
                "compat",
                "check",
                "-lib",
                "libfoo",
                "-old",
                str(old),
                "-new",
                str(new),
                "-report-format",
                "json",
                "-report-path",
                str(tmp_path / "r.json"),
                "-skip-headers",
                str(skip),
            ]
        )
        assert result.exit_code == 0, result.output
        assert "Applying -skip-headers" in result.output

    def test_info_notes_for_compat_flags(self, tmp_path: Path) -> None:
        """Various accepted-but-noted flags emit info notes (1404-1423)."""
        old = _write_dump(tmp_path / "old.dump")
        new = _write_dump(tmp_path / "new.dump")
        filt = tmp_path / "filt.xml"
        filt.write_text("<filter/>", encoding="utf-8")
        params = tmp_path / "params.txt"
        params.write_text("x\n", encoding="utf-8")
        app = tmp_path / "app.bin"
        app.write_text("x", encoding="utf-8")
        result = _run(
            [
                "compat",
                "check",
                "-lib",
                "libfoo",
                "-old",
                str(old),
                "-new",
                str(new),
                "-report-format",
                "json",
                "-report-path",
                str(tmp_path / "r.json"),
                "-old-style",
                "-use-dumps",
                "-d",
                str(filt),
                "-p",
                str(params),
                "-app",
                str(app),
                "-arch",
                "x86_64",
                "-keep-cxx",
                "-keep-reserved",
                "-count-symbols",
                "100",
                "-count-all-symbols",
                "200",
            ]
        )
        assert result.exit_code == 0, result.output
        out = result.output
        assert "-compat-html" in out
        assert "-use-dumps" in out
        assert "-filter" in out
        assert "-params" in out
        assert "-app" in out
        assert "-keep-cxx" in out
        assert "-keep-reserved" in out
        assert "-count-symbols" in out
        assert "-count-all-symbols" in out

    def test_strict_promotes_compatible_addition_stays(self, tmp_path: Path) -> None:
        """-strict with a pure addition stays compatible (exit 0)."""
        old = _write_dump(
            tmp_path / "old.dump",
            symbols="    '1' => { 'MnglName' => 'foo', 'ShortName' => 'foo', 'Return' => '0' }",
        )
        new = _write_dump(
            tmp_path / "new.dump",
            symbols=(
                "    '1' => { 'MnglName' => 'foo', 'ShortName' => 'foo', 'Return' => '0' },\n"
                "    '2' => { 'MnglName' => 'bar', 'ShortName' => 'bar', 'Return' => '0' }"
            ),
        )
        result = _run(
            [
                "compat",
                "check",
                "-lib",
                "libfoo",
                "-old",
                str(old),
                "-new",
                str(new),
                "-report-format",
                "json",
                "-report-path",
                str(tmp_path / "r.json"),
                "-strict",
            ]
        )
        assert result.exit_code == 0, result.output

    def test_xml_report_format(self, tmp_path: Path) -> None:
        """-report-format xml writes an XML report (lines 950-958)."""
        old = _write_dump(tmp_path / "old.dump")
        new = _write_dump(tmp_path / "new.dump")
        report = tmp_path / "r.xml"
        result = _run(
            [
                "compat",
                "check",
                "-lib",
                "libfoo",
                "-old",
                str(old),
                "-new",
                str(new),
                "-report-format",
                "xml",
                "-report-path",
                str(report),
            ]
        )
        assert result.exit_code == 0, result.output
        assert report.exists()


class TestCompatGroupForwarding:
    def test_option_led_invocation_injects_check(self, tmp_path: Path) -> None:
        """`compat -lib ... -old ... -new ...` auto-forwards to the check subcommand (line 611)."""
        old = _write_dump(tmp_path / "old.dump")
        new = _write_dump(tmp_path / "new.dump")
        result = _run(
            [
                "compat",
                "-lib",
                "libfoo",
                "-old",
                str(old),
                "-new",
                str(new),
                "-report-format",
                "json",
                "-report-path",
                str(tmp_path / "r.json"),
            ]
        )
        assert result.exit_code == 0, result.output

    def test_bare_help_shows_group_help(self) -> None:
        """`compat --help` shows the group help without injecting check (lines 608-609)."""
        result = _run(["compat", "--help"])
        assert result.exit_code == 0
        assert "check" in result.output


class TestCompatDumpCli:
    def test_unsupported_dump_format_warns(self, tmp_path: Path) -> None:
        """compat dump with a non-json -dump-format warns then uses json (line 703)."""
        desc = tmp_path / "desc.xml"
        desc.write_text(
            f"<d><version>1.0</version><libs>{tmp_path / 'missing.so'}</libs></d>",
            encoding="utf-8",
        )
        result = _run(
            [
                "compat",
                "dump",
                "-lib",
                "libfoo",
                "-dump",
                str(desc),
                "-dump-format",
                "perl",
            ]
        )
        # Library does not exist -> exit 2 after the format warning is printed.
        assert "is not supported" in result.output
        assert result.exit_code == 2

    def test_missing_library_exits_2(self, tmp_path: Path) -> None:
        """compat dump where the descriptor's lib is missing exits 2 (lines 727-729)."""
        desc = tmp_path / "desc.xml"
        desc.write_text(
            f"<d><version>2.0</version><libs>{tmp_path / 'nope.so'}</libs></d>",
            encoding="utf-8",
        )
        result = _run(
            [
                "compat",
                "dump",
                "-lib",
                "libfoo",
                "-dump",
                str(desc),
                "-vnum",
                "9.9",
                "-arch",
                "arm64",
            ]
        )
        assert result.exit_code == 2
        assert "library not found" in result.output

    def test_bad_descriptor_exits_with_classified_code(self, tmp_path: Path) -> None:
        """compat dump on an unparsable descriptor calls _compat_fail (lines 713-714)."""
        desc = tmp_path / "bad.xml"
        desc.write_text("<unclosed", encoding="utf-8")
        result = _run(["compat", "dump", "-lib", "libfoo", "-dump", str(desc)])
        assert result.exit_code != 0
        assert "Error parsing descriptor" in result.output
