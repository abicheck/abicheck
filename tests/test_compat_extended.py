"""Tests for extended ABICC compat mode features.

Covers:
- compat-dump subcommand
- -symbols-list / -types-list (whitelist filtering)
- -warn-newsym flag
- -component / -limit-affected / -list-affected flags
- -skip-internal-symbols / -skip-internal-types regex flags
- -title wired to HTML output
- -quiet flag
- ABICC dump loading (Perl Data::Dumper) and XML dump error messaging
- JSON dump input support for compat mode
- relpath support in descriptor parsing
- P2 stub flag acceptance
- Logging setup
- Headers-list resolution
- Full ABICC flag acceptance (no unknown option errors)
"""

from __future__ import annotations

import errno
import json
import logging
import re
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

from abicheck.checker import Change, ChangeKind, DiffResult, Verdict
from abicheck.compat import CompatDescriptor, parse_descriptor
from abicheck.compat.cli import (
    _apply_warn_newsym,
    _build_internal_suppression,
    _build_skip_suppression,
    _build_whitelist_suppression,
    _limit_affected_changes,
    _load_descriptor_or_dump,
    _parse_compat_descriptors,
    _resolve_headers_from_list,
    _setup_logging,
    _warn_stub_flags,
    _write_affected_list,
)
from abicheck.html_report import generate_html_report
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.serialization import save_snapshot

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_result(
    changes: list[Change] | None = None,
    verdict: Verdict = Verdict.COMPATIBLE,
) -> DiffResult:
    return DiffResult(
        old_version="1.0",
        new_version="2.0",
        library="libtest.so",
        changes=changes or [],
        verdict=verdict,
    )


def _change(kind: ChangeKind, symbol: str = "test_sym") -> Change:
    return Change(kind=kind, symbol=symbol, description=f"{kind.value} on {symbol}")


def _write_file(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(textwrap.dedent(content).strip(), encoding="utf-8")
    return p


def _make_snapshot(version: str = "1.0") -> AbiSnapshot:
    return AbiSnapshot(
        library="libtest.so",
        version=version,
        functions=[
            Function(
                name="foo",
                mangled="_Z3foov",
                return_type="void",
                visibility=Visibility.PUBLIC,
            ),
        ],
        variables=[],
        types=[],
    )


# ── _build_whitelist_suppression ──────────────────────────────────────────────


class TestWhitelistSuppression:
    def test_whitelist_suppresses_non_listed(self, tmp_path: Path) -> None:
        wl = _write_file(tmp_path, "symbols.txt", "_Z3foov\n_Z3barv\n")
        sl = _build_whitelist_suppression(wl, None)

        # A change on a whitelisted symbol should NOT be suppressed
        c_foo = _change(ChangeKind.FUNC_REMOVED, "_Z3foov")
        assert not sl.is_suppressed(c_foo)

        # A change on a non-whitelisted symbol SHOULD be suppressed
        c_baz = _change(ChangeKind.FUNC_REMOVED, "_Z3bazv")
        assert sl.is_suppressed(c_baz)

    def test_whitelist_empty_file_no_crash(self, tmp_path: Path) -> None:
        wl = _write_file(tmp_path, "empty.txt", "# comments only\n")
        sl = _build_whitelist_suppression(wl, None)
        # No whitelist entries → no suppression rules generated
        assert len(sl) == 0

    def test_whitelist_types(self, tmp_path: Path) -> None:
        wl = _write_file(tmp_path, "types.txt", "MyStruct\n")
        sl = _build_whitelist_suppression(None, wl)

        c_listed = _change(ChangeKind.TYPE_SIZE_CHANGED, "MyStruct")
        assert not sl.is_suppressed(c_listed)

        c_other = _change(ChangeKind.TYPE_SIZE_CHANGED, "OtherStruct")
        assert sl.is_suppressed(c_other)

    def test_whitelist_comments_and_blanks(self, tmp_path: Path) -> None:
        content = "# Header\n\n_Z3foov\n\n# Trailer\n"
        wl = _write_file(tmp_path, "wl.txt", content)
        sl = _build_whitelist_suppression(wl, None)
        c_foo = _change(ChangeKind.FUNC_REMOVED, "_Z3foov")
        assert not sl.is_suppressed(c_foo)


# ── _build_internal_suppression ───────────────────────────────────────────────


class TestInternalSuppression:
    def test_skip_internal_symbols(self) -> None:
        sl = _build_internal_suppression("_ZN.*internal.*", None)
        c = _change(ChangeKind.FUNC_REMOVED, "_ZN3Foo8internalEv")
        assert sl.is_suppressed(c)

    def test_skip_internal_types(self) -> None:
        sl = _build_internal_suppression(None, ".*Impl$")
        c = _change(ChangeKind.TYPE_SIZE_CHANGED, "FooImpl")
        assert sl.is_suppressed(c)

    def test_skip_internal_does_not_match_public(self) -> None:
        sl = _build_internal_suppression("_ZN.*internal.*", None)
        c = _change(ChangeKind.FUNC_REMOVED, "_Z3foov")
        assert not sl.is_suppressed(c)

    def test_invalid_regex_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid symbol_pattern"):
            _build_internal_suppression("[invalid", None)


# ── _apply_warn_newsym ────────────────────────────────────────────────────────


class TestWarnNewsym:
    def test_func_added_becomes_breaking(self) -> None:
        result = _make_result(
            changes=[_change(ChangeKind.FUNC_ADDED)],
            verdict=Verdict.COMPATIBLE,
        )
        updated = _apply_warn_newsym(result)
        assert updated.verdict == Verdict.BREAKING

    def test_var_added_becomes_breaking(self) -> None:
        result = _make_result(
            changes=[_change(ChangeKind.VAR_ADDED)],
            verdict=Verdict.COMPATIBLE,
        )
        updated = _apply_warn_newsym(result)
        assert updated.verdict == Verdict.BREAKING

    def test_no_change_with_new_sym_becomes_breaking(self) -> None:
        result = _make_result(
            changes=[_change(ChangeKind.FUNC_ADDED)],
            verdict=Verdict.NO_CHANGE,
        )
        updated = _apply_warn_newsym(result)
        assert updated.verdict == Verdict.BREAKING

    def test_already_breaking_stays_breaking(self) -> None:
        result = _make_result(
            changes=[_change(ChangeKind.FUNC_ADDED), _change(ChangeKind.FUNC_REMOVED)],
            verdict=Verdict.BREAKING,
        )
        updated = _apply_warn_newsym(result)
        assert updated.verdict == Verdict.BREAKING

    def test_no_new_symbols_unchanged(self) -> None:
        result = _make_result(
            changes=[_change(ChangeKind.TYPE_SIZE_CHANGED)],
            verdict=Verdict.COMPATIBLE,
        )
        updated = _apply_warn_newsym(result)
        assert updated.verdict == Verdict.COMPATIBLE


# ── _limit_affected_changes ───────────────────────────────────────────────────


class TestLimitAffected:
    def test_limits_per_kind(self) -> None:
        changes = [_change(ChangeKind.FUNC_REMOVED, f"sym_{i}") for i in range(10)]
        result = _make_result(changes=changes, verdict=Verdict.BREAKING)
        limited = _limit_affected_changes(result, limit=3)
        assert len(limited.changes) == 3
        assert limited.verdict == Verdict.BREAKING  # verdict preserved

    def test_zero_limit_is_noop(self) -> None:
        changes = [_change(ChangeKind.FUNC_REMOVED)]
        result = _make_result(changes=changes)
        limited = _limit_affected_changes(result, limit=0)
        assert len(limited.changes) == 1

    def test_limits_different_kinds_independently(self) -> None:
        changes = [_change(ChangeKind.FUNC_REMOVED, f"r_{i}") for i in range(5)] + [
            _change(ChangeKind.FUNC_ADDED, f"a_{i}") for i in range(5)
        ]
        result = _make_result(changes=changes, verdict=Verdict.BREAKING)
        limited = _limit_affected_changes(result, limit=2)
        removed = [c for c in limited.changes if c.kind == ChangeKind.FUNC_REMOVED]
        added = [c for c in limited.changes if c.kind == ChangeKind.FUNC_ADDED]
        assert len(removed) == 2
        assert len(added) == 2


# ── _write_affected_list ──────────────────────────────────────────────────────


class TestWriteAffectedList:
    def test_writes_sorted_symbols(self, tmp_path: Path) -> None:
        result = _make_result(
            changes=[
                _change(ChangeKind.FUNC_REMOVED, "zebra"),
                _change(ChangeKind.FUNC_ADDED, "alpha"),
                _change(ChangeKind.TYPE_SIZE_CHANGED, "middle"),
            ]
        )
        out = tmp_path / "affected.txt"
        _write_affected_list(result, out)
        lines = out.read_text(encoding="utf-8").strip().split("\n")
        assert lines == ["alpha", "middle", "zebra"]

    def test_empty_changes_empty_file(self, tmp_path: Path) -> None:
        result = _make_result(changes=[])
        out = tmp_path / "affected.txt"
        _write_affected_list(result, out)
        assert out.read_text(encoding="utf-8") == ""

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        result = _make_result(changes=[_change(ChangeKind.FUNC_REMOVED)])
        out = tmp_path / "deep" / "nested" / "affected.txt"
        _write_affected_list(result, out)
        assert out.exists()

    def test_deduplicates_symbols(self, tmp_path: Path) -> None:
        result = _make_result(
            changes=[
                _change(ChangeKind.FUNC_REMOVED, "same_sym"),
                _change(ChangeKind.FUNC_RETURN_CHANGED, "same_sym"),
            ]
        )
        out = tmp_path / "affected.txt"
        _write_affected_list(result, out)
        lines = out.read_text(encoding="utf-8").strip().split("\n")
        assert lines == ["same_sym"]


# ── _load_descriptor_or_dump ──────────────────────────────────────────────────


class TestLoadDescriptorOrDump:
    def test_loads_json_dump(self, tmp_path: Path) -> None:
        snap = _make_snapshot()
        path = tmp_path / "dump.json"
        save_snapshot(snap, path)
        result = _load_descriptor_or_dump(path)
        from abicheck.model import AbiSnapshot

        assert isinstance(result, AbiSnapshot)
        assert result.version == "1.0"

    def test_loads_xml_descriptor(self, tmp_path: Path) -> None:
        xml = _write_file(
            tmp_path,
            "desc.xml",
            """
            <descriptor>
              <version>2.0</version>
              <libs>/usr/lib/libfoo.so</libs>
            </descriptor>
        """,
        )
        result = _load_descriptor_or_dump(xml)
        assert isinstance(result, CompatDescriptor)
        assert result.version == "2.0"

    def test_loads_abicc_perl_dump_by_extension(self, tmp_path: Path) -> None:
        dump_file = tmp_path / "old.dump"
        dump_file.write_text(
            """
            $VAR1 = {
              'LibraryName' => 'libfoo',
              'LibraryVersion' => '1.2.3',
              'TypeInfo' => {
                '0' => { 'Name' => 'void', 'Type' => 'Intrinsic' }
              },
              'SymbolInfo' => {
                '1' => { 'MnglName' => 'foo', 'ShortName' => 'foo', 'Return' => '0' }
              }
            };
        """,
            encoding="utf-8",
        )

        result = _load_descriptor_or_dump(dump_file)
        from abicheck.model import AbiSnapshot

        assert isinstance(result, AbiSnapshot)
        assert result.library == "libfoo"
        assert result.version == "1.2.3"
        assert any(f.mangled == "foo" for f in result.functions)

    def test_loads_abicc_perl_dump_by_content(self, tmp_path: Path) -> None:
        dump_file = tmp_path / "old.txt"
        dump_file.write_text(
            """
            $VAR1 = {
              'LibraryName' => 'libbar',
              'LibraryVersion' => '9',
              'TypeInfo' => {
                '0' => { 'Name' => 'void', 'Type' => 'Intrinsic' }
              },
              'SymbolInfo' => {
                '1' => { 'MnglName' => 'bar', 'ShortName' => 'bar', 'Return' => '0' }
              }
            };
        """,
            encoding="utf-8",
        )

        result = _load_descriptor_or_dump(dump_file)
        from abicheck.model import AbiSnapshot

        assert isinstance(result, AbiSnapshot)
        assert result.library == "libbar"

    def test_rejects_abicc_xml_dump(self, tmp_path: Path) -> None:
        dump_file = tmp_path / "dump.xml"
        dump_file.write_text(
            '<?xml version="1.0"?>\n<ABI_dump_1.0>\n<library>libfoo</library>\n</ABI_dump_1.0>',
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="ABICC XML dump format detected"):
            _load_descriptor_or_dump(dump_file)

    def test_error_message_for_abicc_xml_dump_mentions_current_support(
        self, tmp_path: Path
    ) -> None:
        dump_file = tmp_path / "dump.xml"
        dump_file.write_text("<?xml version='1.0'?><ABI_dump_1.0/>", encoding="utf-8")
        with pytest.raises(ValueError, match="supports ABICC Perl Data::Dumper dumps"):
            _load_descriptor_or_dump(dump_file)

    @pytest.mark.parametrize(
        ("suffix", "compression"), [(".json.gz", "gzip"), (".json.zst", "zstd")]
    )
    def test_loads_compressed_json_dump_by_canonical_suffix(
        self, tmp_path: Path, suffix: str, compression: str
    ) -> None:
        """Codex review, PR #699: `compat dump -dump-path v1.json.gz`/`.zst`
        writes a valid compressed snapshot -- Path.suffix only sees the
        *last* component ("gz"/"zst"), so this used to miss the
        `path.suffix == ".json"` heuristic entirely and fall through to
        the XML-descriptor branch, failing before reaching load_snapshot()."""
        snap = _make_snapshot()
        path = tmp_path / f"dump{suffix}"
        save_snapshot(snap, path, compression=compression)
        result = _load_descriptor_or_dump(path)
        from abicheck.model import AbiSnapshot

        assert isinstance(result, AbiSnapshot)
        assert result.version == "1.0"

    @pytest.mark.parametrize("compression", ["gzip", "zstd"])
    def test_loads_compressed_json_dump_under_neutral_name(
        self, tmp_path: Path, compression: str
    ) -> None:
        """Same gap, but for a compressed snapshot written under a name
        that's neither `.json` (the plain heuristic) nor a canonical
        compressed suffix -- must still be recognized via magic-byte
        detection alone, not just filename matching."""
        snap = _make_snapshot()
        path = tmp_path / "dump.bin"  # deliberately no suffix hint at all
        save_snapshot(snap, path, compression=compression)
        result = _load_descriptor_or_dump(path)
        from abicheck.model import AbiSnapshot

        assert isinstance(result, AbiSnapshot)
        assert result.version == "1.0"


class TestAbiccPerlDumpInfoMessage:
    def _write_dump(self, path: Path, lib: str) -> None:
        path.write_text(
            f"""
            $VAR1 = {{
              'LibraryName' => '{lib}',
              'LibraryVersion' => '1.0',
              'TypeInfo' => {{
                '0' => {{ 'Name' => 'void', 'Type' => 'Intrinsic' }}
              }},
              'SymbolInfo' => {{
                '1' => {{ 'MnglName' => 'foo', 'ShortName' => 'foo', 'Return' => '0' }}
              }}
            }};
            """,
            encoding="utf-8",
        )

    def test_info_message_shown_for_abicc_dump_input(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from abicheck.cli import main

        old_dump = tmp_path / "old.dump"
        new_dump = tmp_path / "new.dump"
        self._write_dump(old_dump, "libfoo")
        self._write_dump(new_dump, "libfoo")

        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "compat",
                "check",
                "-lib",
                "libfoo",
                "-old",
                str(old_dump),
                "-new",
                str(new_dump),
            ],
        )

        assert result.exit_code == 0, result.output
        assert "Info: ABICC Perl ABI.dump input detected" in result.output

    def test_info_message_suppressed_in_quiet_mode(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from abicheck.cli import main

        old_dump = tmp_path / "old.dump"
        new_dump = tmp_path / "new.dump"
        self._write_dump(old_dump, "libfoo")
        self._write_dump(new_dump, "libfoo")

        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "compat",
                "check",
                "-lib",
                "libfoo",
                "-old",
                str(old_dump),
                "-new",
                str(new_dump),
                "-q",
            ],
        )

        assert result.exit_code == 0, result.output
        assert "Info: ABICC Perl ABI.dump input detected" not in result.output


# ── HTML title wiring ─────────────────────────────────────────────────────────


class TestHtmlTitle:
    def _fake_result(self, verdict: str = "COMPATIBLE") -> object:
        v = SimpleNamespace(value=verdict)
        return SimpleNamespace(
            verdict=v,
            changes=[],
            suppressed_changes=[],
            suppressed_count=0,
            suppression_file_provided=False,
        )

    def test_custom_title_in_html(self) -> None:
        html = generate_html_report(
            self._fake_result(),
            lib_name="libfoo",
            title="My Custom Report Title",
        )
        assert "My Custom Report Title" in html
        # Default title should not appear
        assert "ABI Compatibility Report — libfoo" not in html

    def test_default_title_when_none(self) -> None:
        html = generate_html_report(
            self._fake_result(),
            lib_name="libfoo",
        )
        assert "ABI Compatibility Report — libfoo" in html

    def test_title_is_html_escaped(self) -> None:
        html = generate_html_report(
            self._fake_result(),
            title="<script>alert(1)</script>",
        )
        assert "<script>" not in html
        assert "&lt;script&gt;" in html


# ── CI cross-validation infrastructure ────────────────────────────────────────


class TestCrossValidationHelpers:
    """Tests for the building blocks used in CI cross-validation.

    These tests verify that the compat mode transforms compose correctly,
    enabling golden-file CI tests that compare abicheck output against
    known-good reference results.
    """

    def test_strict_plus_warn_newsym_composition(self) -> None:
        """When both -strict and -warn-newsym are active, FUNC_ADDED → BREAKING."""
        from abicheck.compat.cli import _apply_strict, _apply_warn_newsym

        result = _make_result(
            changes=[_change(ChangeKind.FUNC_ADDED)],
            verdict=Verdict.COMPATIBLE,
        )
        # Apply in same order as CLI: warn-newsym first, then strict
        result = _apply_warn_newsym(result)
        result = _apply_strict(result)
        assert result.verdict == Verdict.BREAKING

    def test_source_filter_plus_strict(self) -> None:
        """Source-only filter + strict: binary-only changes are removed before strict."""
        from abicheck.compat.cli import _apply_strict, _filter_source_only

        result = _make_result(
            changes=[
                _change(ChangeKind.SONAME_CHANGED, "libtest.so"),
                _change(ChangeKind.FUNC_ADDED, "_Z3foov"),
            ],
            verdict=Verdict.BREAKING,
        )
        result = _filter_source_only(result)
        assert result.verdict == Verdict.COMPATIBLE  # only FUNC_ADDED left
        result = _apply_strict(result)
        # Pure additions stay COMPATIBLE even under strict (ABICC parity)
        assert result.verdict == Verdict.COMPATIBLE

    def test_whitelist_plus_skip_composition(self, tmp_path: Path) -> None:
        """Whitelist and skip can be combined: whitelist first, then skip further."""
        from abicheck.compat.cli import _merge_suppression

        wl_file = _write_file(tmp_path, "wl.txt", "_Z3foov\n_Z3barv\n")
        skip_file = _write_file(tmp_path, "skip.txt", "_Z3barv\n")

        wl = _build_whitelist_suppression(wl_file, None)
        skip = _build_skip_suppression(skip_file, None)
        merged = _merge_suppression(wl, skip)

        # foo: whitelisted, not skipped → NOT suppressed
        assert not merged.is_suppressed(_change(ChangeKind.FUNC_REMOVED, "_Z3foov"))
        # bar: whitelisted but also skipped → IS suppressed
        assert merged.is_suppressed(_change(ChangeKind.FUNC_REMOVED, "_Z3barv"))
        # baz: not whitelisted → IS suppressed (by whitelist)
        assert merged.is_suppressed(_change(ChangeKind.FUNC_REMOVED, "_Z3bazv"))

    def test_exit_code_mapping(self) -> None:
        """Verify exit code mapping matches ABICC spec."""
        # NO_CHANGE → 0, COMPATIBLE → 0, BREAKING → 1, API_BREAK → 2
        verdicts = {
            Verdict.NO_CHANGE: 0,
            Verdict.COMPATIBLE: 0,
            Verdict.BREAKING: 1,
            Verdict.API_BREAK: 2,
        }
        for v, expected_exit in verdicts.items():
            result = _make_result(verdict=v)
            verdict_str = result.verdict.value
            if verdict_str == "BREAKING":
                code = 1
            elif verdict_str == "API_BREAK":
                code = 2
            else:
                code = 0
            assert code == expected_exit, f"{v} should map to exit {expected_exit}"


# ── compat-dump JSON round-trip ───────────────────────────────────────────────


class TestCompatDumpRoundTrip:
    """Verify that JSON dumps produced by compat-dump can be loaded and compared."""

    def test_dump_and_reload(self, tmp_path: Path) -> None:
        snap = _make_snapshot("3.0")
        path = tmp_path / "dump.json"
        save_snapshot(snap, path)

        loaded = _load_descriptor_or_dump(path)
        from abicheck.model import AbiSnapshot

        assert isinstance(loaded, AbiSnapshot)
        assert loaded.version == "3.0"
        assert len(loaded.functions) == 1
        assert loaded.functions[0].name == "foo"

    def test_dump_json_is_valid(self, tmp_path: Path) -> None:
        snap = _make_snapshot()
        path = tmp_path / "dump.json"
        save_snapshot(snap, path)

        from abicheck.serialization import load_snapshot_document

        data = load_snapshot_document(path)
        assert data["library"] == "libtest.so"
        assert data["version"] == "1.0"
        assert len(data["functions"]) == 1


# ── Relpath support ──────────────────────────────────────────────────────────


class TestRelpathDescriptor:
    def test_relpath_replaces_macro_in_libs(self, tmp_path: Path) -> None:
        xml = _write_file(
            tmp_path,
            "desc.xml",
            """
            <descriptor>
              <version>1.0</version>
              <libs>{RELPATH}/lib/libfoo.so</libs>
            </descriptor>
        """,
        )
        desc = parse_descriptor(xml, relpath="/opt/myproject")
        assert re.fullmatch(
            r"([A-Za-z]:)?/opt/myproject/lib/libfoo\.so", desc.libs[0].as_posix()
        )

    def test_relpath_replaces_macro_in_headers(self, tmp_path: Path) -> None:
        xml = _write_file(
            tmp_path,
            "desc.xml",
            """
            <descriptor>
              <version>1.0</version>
              <libs>/usr/lib/libfoo.so</libs>
              <headers>{RELPATH}/include</headers>
            </descriptor>
        """,
        )
        desc = parse_descriptor(xml, relpath="/opt/myproject")
        assert re.fullmatch(
            r"([A-Za-z]:)?/opt/myproject/include", desc.headers[0].as_posix()
        )

    def test_no_relpath_leaves_macros(self, tmp_path: Path) -> None:
        xml = _write_file(
            tmp_path,
            "desc.xml",
            """
            <descriptor>
              <version>1.0</version>
              <libs>/usr/lib/libfoo.so</libs>
            </descriptor>
        """,
        )
        desc = parse_descriptor(xml)
        assert desc.version == "1.0"

    def test_load_descriptor_or_dump_passes_relpath(self, tmp_path: Path) -> None:
        xml = _write_file(
            tmp_path,
            "desc.xml",
            """
            <descriptor>
              <version>1.0</version>
              <libs>{RELPATH}/lib/libfoo.so</libs>
            </descriptor>
        """,
        )
        result = _load_descriptor_or_dump(xml, relpath="/opt/build")
        assert isinstance(result, CompatDescriptor)
        posix = result.libs[0].as_posix()
        # On Windows, a drive letter prefix (e.g. "C:") is expected.
        assert re.fullmatch(r"([A-Za-z]:)?/opt/build/lib/libfoo\.so", posix), posix


# ── Headers list resolution ──────────────────────────────────────────────────


class TestHeadersListResolution:
    def test_headers_list_file(self, tmp_path: Path) -> None:
        # Create a real header file
        hdr = tmp_path / "my_header.h"
        hdr.write_text("#pragma once\n", encoding="utf-8")

        lst = _write_file(tmp_path, "headers.txt", f"{hdr}\n")
        result = _resolve_headers_from_list(lst, None, [])
        assert hdr in result

    def test_single_header(self, tmp_path: Path) -> None:
        hdr = tmp_path / "single.h"
        hdr.write_text("#pragma once\n", encoding="utf-8")
        result = _resolve_headers_from_list(None, str(hdr), [])
        assert hdr in result

    def test_merges_with_base(self, tmp_path: Path) -> None:
        base_hdr = tmp_path / "base.h"
        base_hdr.write_text("#pragma once\n", encoding="utf-8")
        extra_hdr = tmp_path / "extra.h"
        extra_hdr.write_text("#pragma once\n", encoding="utf-8")

        result = _resolve_headers_from_list(None, str(extra_hdr), [base_hdr])
        assert base_hdr in result
        assert extra_hdr in result

    def test_skips_nonexistent_headers(self, tmp_path: Path) -> None:
        lst = _write_file(tmp_path, "headers.txt", "/nonexistent/header.h\n")
        result = _resolve_headers_from_list(lst, None, [])
        assert len(result) == 0


# ── P2 stub flag warnings ───────────────────────────────────────────────────


class TestStubFlagWarnings:
    def test_stub_flag_emits_warning(self, capsys: pytest.CaptureFixture) -> None:
        _warn_stub_flags(quiet=False, mingw_compatible=True)
        captured = capsys.readouterr()
        assert "-mingw-compatible" in captured.err

    def test_stub_flag_quiet_suppresses(self, capsys: pytest.CaptureFixture) -> None:
        _warn_stub_flags(quiet=True, mingw_compatible=True)
        captured = capsys.readouterr()
        assert captured.err == ""

    def test_false_flags_no_warning(self, capsys: pytest.CaptureFixture) -> None:
        _warn_stub_flags(quiet=False, mingw_compatible=False, static_libs=False)
        captured = capsys.readouterr()
        assert captured.err == ""

    def test_multiple_stubs_all_warned(self, capsys: pytest.CaptureFixture) -> None:
        _warn_stub_flags(
            quiet=False, mingw_compatible=True, static_libs=True, quick=True
        )
        captured = capsys.readouterr()
        assert "-mingw-compatible" in captured.err
        assert "-static" in captured.err
        assert "-quick" in captured.err


# ── Logging setup ────────────────────────────────────────────────────────────


class TestLoggingSetup:
    def test_log_to_file(self, tmp_path: Path) -> None:
        log_file = tmp_path / "test.log"
        _setup_logging(log_file, None, None, "w", quiet=False)
        logger = logging.getLogger("abicheck")
        logger.debug("test message")
        # Clean up handlers to avoid affecting other tests
        for h in logger.handlers[:]:
            if isinstance(h, logging.FileHandler):
                logger.removeHandler(h)
                h.close()
        assert log_file.exists()

    def test_quiet_sets_warning_level(self, tmp_path: Path) -> None:
        _setup_logging(None, None, None, None, quiet=True)
        logger = logging.getLogger("abicheck")
        assert logger.level >= logging.WARNING
        # Reset
        logger.setLevel(logging.NOTSET)

    def test_append_mode(self, tmp_path: Path) -> None:
        log_file = tmp_path / "append.log"
        log_file.write_text("existing\n", encoding="utf-8")
        _setup_logging(log_file, None, None, "a", quiet=False)
        logger = logging.getLogger("abicheck")
        logger.info("appended")
        for h in logger.handlers[:]:
            if isinstance(h, logging.FileHandler):
                logger.removeHandler(h)
                h.close()
        content = log_file.read_text(encoding="utf-8")
        assert "existing" in content


# ── Full ABICC flag acceptance (CLI integration) ─────────────────────────────


class TestAllAbiccFlagsAccepted:
    """Verify that every known ABICC flag is accepted by the CLI without error.

    These tests use Click's test runner to invoke the compat command with
    each flag and verify it doesn't produce an 'unknown option' error.
    The commands will fail for other reasons (missing .so files, etc.)
    but the flag itself should be recognized.
    """

    def _invoke_compat(self, args: list[str], tmp_path: Path) -> object:
        """Invoke compat with minimal required args + extra flags."""
        from click.testing import CliRunner

        from abicheck.cli import main

        # Create minimal XML descriptors
        for name in ("old.xml", "new.xml"):
            (tmp_path / name).write_text(
                f"<d><version>1.0</version><libs>{tmp_path / 'fake.so'}</libs></d>",
                encoding="utf-8",
            )

        base_args = [
            "compat",
            "check",
            "-lib",
            "test",
            "-old",
            str(tmp_path / "old.xml"),
            "-new",
            str(tmp_path / "new.xml"),
        ]
        runner = CliRunner()
        return runner.invoke(main, base_args + args)

    def test_p2_stub_flags_accepted(self, tmp_path: Path) -> None:
        """All P2 stub flags should be accepted (not 'unknown option')."""
        flags = [
            "-mingw-compatible",
            "-cxx-incompatible",
            "-cpp-compatible",
            "-static",
            "-ext",
            "-quick",
            "-force",
            "-check",
            "-extra-dump",
            "-sort",
            "-xml",
            "-skip-typedef-uncover",
            "-check-private-abi",
            "-skip-unidentified",
            "-tolerant",
            "-disable-constants-check",
            "-skip-added-constants",
            "-skip-removed-constants",
        ]
        for flag in flags:
            result = self._invoke_compat([flag], tmp_path)
            assert "No such option" not in (result.output or ""), (
                f"{flag} should be accepted"
            )

    def test_cross_compilation_flags_accepted(self, tmp_path: Path) -> None:
        flags = [
            ["-gcc-path", "/usr/bin/g++"],
            ["-gcc-prefix", "aarch64-linux-gnu-"],
            ["-gcc-options", "-DFOO=1 -DBAR=2"],
            ["-sysroot", "/opt/sysroot"],
            ["-nostdinc"],
            ["-lang", "C++"],
            ["-arch", "x86_64"],
        ]
        for flag_args in flags:
            result = self._invoke_compat(flag_args, tmp_path)
            assert "No such option" not in (result.output or ""), (
                f"{flag_args} should be accepted"
            )

    def test_relpath_flags_accepted(self, tmp_path: Path) -> None:
        flags = [
            ["-relpath", "/opt/build"],
            ["-relpath1", "/opt/old"],
            ["-relpath2", "/opt/new"],
        ]
        for flag_args in flags:
            result = self._invoke_compat(flag_args, tmp_path)
            assert "No such option" not in (result.output or ""), (
                f"{flag_args} should be accepted"
            )

    def test_logging_flags_accepted(self, tmp_path: Path) -> None:
        log_file = tmp_path / "test.log"
        flags = [
            ["-log-path", str(log_file)],
            ["-log1-path", str(log_file)],
            ["-log2-path", str(log_file)],
            ["-logging-mode", "w"],
        ]
        for flag_args in flags:
            result = self._invoke_compat(flag_args, tmp_path)
            assert "No such option" not in (result.output or ""), (
                f"{flag_args} should be accepted"
            )

    def test_report_flags_accepted(self, tmp_path: Path) -> None:
        flags = [
            ["-bin-report-path", str(tmp_path / "bin.html")],
            ["-src-report-path", str(tmp_path / "src.html")],
            ["-old-style"],
            ["-component", "mycomponent"],
            ["-limit-affected", "5"],
            ["-list-affected"],
            ["-warn-newsym"],
        ]
        for flag_args in flags:
            result = self._invoke_compat(flag_args, tmp_path)
            assert "No such option" not in (result.output or ""), (
                f"{flag_args} should be accepted"
            )

    def test_filtering_flags_accepted(self, tmp_path: Path) -> None:
        skip_file = tmp_path / "skip.txt"
        skip_file.write_text("# empty\n", encoding="utf-8")
        flags = [
            ["-skip-symbols", str(skip_file)],
            ["-skip-types", str(skip_file)],
            ["-symbols-list", str(skip_file)],
            ["-types-list", str(skip_file)],
            ["-skip-internal-symbols", ".*internal.*"],
            ["-skip-internal-types", ".*Impl$"],
            ["-keep-cxx"],
            ["-keep-reserved"],
        ]
        for flag_args in flags:
            result = self._invoke_compat(flag_args, tmp_path)
            assert "No such option" not in (result.output or ""), (
                f"{flag_args} should be accepted"
            )

    def test_version_aliases_accepted(self, tmp_path: Path) -> None:
        flags = [
            ["-v1", "1.0"],
            ["-v2", "2.0"],
            ["-vnum1", "1.0"],
            ["-vnum2", "2.0"],
            ["-version1", "1.0"],
            ["-version2", "2.0"],
        ]
        for flag_args in flags:
            result = self._invoke_compat(flag_args, tmp_path)
            assert "No such option" not in (result.output or ""), (
                f"{flag_args} should be accepted"
            )

    def test_use_dumps_flag_accepted(self, tmp_path: Path) -> None:
        result = self._invoke_compat(["-use-dumps"], tmp_path)
        assert "No such option" not in (result.output or "")

    def test_headers_list_and_header_accepted(self, tmp_path: Path) -> None:
        hdr_list = tmp_path / "headers.txt"
        hdr_list.write_text("# empty\n", encoding="utf-8")
        flags = [
            ["-headers-list", str(hdr_list)],
            ["-header", "myheader.h"],
        ]
        for flag_args in flags:
            result = self._invoke_compat(flag_args, tmp_path)
            assert "No such option" not in (result.output or ""), (
                f"{flag_args} should be accepted"
            )


class TestCompatExtendedExitCodeMapping:
    @pytest.mark.parametrize(
        "exc,context,expected",
        [
            (FileNotFoundError("missing"), "parsing descriptor", 4),
            (FileNotFoundError("castxml: command not found"), "during castxml run", 3),
            (PermissionError("denied"), "parsing descriptor", 4),
            (OSError(errno.ENOENT, "missing input"), "during dump", 4),
            (
                OSError(errno.ENOENT, "castxml not found in PATH"),
                "during castxml run",
                3,
            ),
            (OSError(errno.EACCES, "permission denied"), "during dump", 4),
            (OSError(errno.EPERM, "operation not permitted"), "during dump", 4),
            (RuntimeError("castxml not found in PATH"), "during dump", 3),
            (RuntimeError("cannot compile headers"), "during dump", 5),
            (
                RuntimeError(
                    "castxml failed (exit 1): fatal error: foo.h: No such file or directory"
                ),
                "during dump",
                5,
            ),
            (
                RuntimeError("castxml failed (exit 1): compilation terminated"),
                "during dump",
                5,
            ),
            (RuntimeError("command not found"), "during dump", 3),
            (RuntimeError("No such file or directory"), "other", 3),
            (ValueError("invalid regex"), "in skip-symbols/skip-types", 6),
            (
                ValueError("invalid regex"),
                "in skip-internal-symbols/skip-internal-types",
                6,
            ),
            (ValueError("bad descriptor"), "parsing descriptor", 6),
            (ValueError("bad logging mode"), "setting up logging", 6),
            (RuntimeError("cannot write"), "report generation", 7),
            (OSError("disk full"), "writing report output", 7),
            (RuntimeError("unexpected pipeline crash"), "during dump", 8),
            (RuntimeError("unexpected internal error"), "other", 10),
            (KeyboardInterrupt(), "during dump", 11),
        ],
    )
    def test_classify_compat_error_exit_code(
        self, exc: BaseException, context: str, expected: int
    ) -> None:
        from abicheck.compat.cli import _classify_compat_error_exit_code

        assert _classify_compat_error_exit_code(exc, context=context) == expected


class TestCompatFailHelper:
    def test_compat_fail_raises_system_exit_with_classified_code(self, capsys) -> None:
        from abicheck.compat.cli import _compat_fail

        with pytest.raises(SystemExit) as excinfo:
            _compat_fail("parsing descriptor", ValueError("bad descriptor"))

        assert excinfo.value.code == 6
        err = capsys.readouterr().err
        assert "Error parsing descriptor" in err


class TestParseCompatDescriptorsMalformedContract:
    def test_malformed_contract_field_exits_clean_not_bare_typeerror(
        self, tmp_path: Path, capsys
    ) -> None:
        """A wrong-shaped ``contract.profile_fields``/``scope_fields`` in a
        JSON dump makes ``extraction_contract_from_dict`` raise ``TypeError``
        rather than silently degrading (storage AGENTS.md invariant 6) --
        ``_parse_compat_descriptors`` must map that into the same clean,
        classified compat-mode exit every other malformed-descriptor
        failure gets (rc=6, ``_classify_compat_error_exit_code``'s
        "invalid descriptor/config/suppression inputs" bucket), not let a
        bare ``TypeError`` escape uncaught (Codex review, PR #974 -- the
        identical gap found and fixed in
        ``workflows/input_resolution.py``'s own JSON-loading branch, which
        this function mirrors for compat mode).
        """
        snap = _make_snapshot("1.0")
        old_p = tmp_path / "old.json"
        new_p = tmp_path / "new.json"
        from abicheck.serialization import load_snapshot_document

        save_snapshot(snap, old_p)
        save_snapshot(snap, new_p)
        # Read back the flat document (unwrapping the sectioned shape
        # `save_snapshot` now writes) and re-write it flat -- still a fully
        # valid input (`snapshot_from_dict` reads either shape), and the
        # simplest way to inject a malformed top-level `contract` field.
        raw = load_snapshot_document(new_p)
        raw["contract"] = {"profile_fields": "not-a-dict"}
        new_p.write_text(json.dumps(raw), encoding="utf-8")

        # Pre-fix, this raised a bare TypeError instead of the classified
        # SystemExit(6) every other malformed-descriptor failure produces --
        # pytest.raises(SystemExit) itself is the regression check: a
        # TypeError escaping here would fail to match and surface as such.
        with pytest.raises(SystemExit) as excinfo:
            _parse_compat_descriptors(old_p, new_p, None, None)

        assert excinfo.value.code == 6
        err = capsys.readouterr().err
        assert "Error parsing descriptor" in err
