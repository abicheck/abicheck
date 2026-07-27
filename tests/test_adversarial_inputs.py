"""Tests for robustness against malformed and adversarial inputs.

Each test must either succeed or raise an expected exception
(AbicheckError, ValueError, KeyError, json.JSONDecodeError, etc.)
-- never an unhandled crash.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from abicheck.checker import Change, ChangeKind, DiffResult, Verdict, compare
from abicheck.compat.abicc_dump_import import (
    import_abicc_perl_dump,
    looks_like_perl_dump,
)
from abicheck.model import AbiSnapshot, Function, Param, Variable
from abicheck.policy_file import PolicyFile
from abicheck.reporter import to_json, to_markdown
from abicheck.serialization import load_snapshot, snapshot_from_dict
from abicheck.suppression import SuppressionList

# ---------------------------------------------------------------------------
# 1. Malformed ELF files (abicheck/elf_metadata.py)
# ---------------------------------------------------------------------------

class TestMalformedElf:
    """parse_elf_metadata returns an empty ElfMetadata on any parse error."""

    def test_truncated_elf_magic_only(self, tmp_path: Path) -> None:
        """ELF with only the 4-byte magic, no headers."""
        from abicheck.elf_metadata import ElfMetadata, parse_elf_metadata

        p = tmp_path / "truncated.so"
        p.write_bytes(b"\x7fELF")
        result = parse_elf_metadata(p)
        assert isinstance(result, ElfMetadata)
        assert result.symbols == []

    def test_elf_magic_plus_random_bytes(self, tmp_path: Path) -> None:
        """ELF magic followed by garbage -- should not crash."""
        from abicheck.elf_metadata import ElfMetadata, parse_elf_metadata

        p = tmp_path / "garbage.so"
        p.write_bytes(b"\x7fELF" + b"\xde\xad\xbe\xef" * 64)
        result = parse_elf_metadata(p)
        assert isinstance(result, ElfMetadata)

    def test_zero_length_file(self, tmp_path: Path) -> None:
        """Empty file -- not a valid ELF."""
        from abicheck.elf_metadata import ElfMetadata, parse_elf_metadata

        p = tmp_path / "empty.so"
        p.write_bytes(b"")
        result = parse_elf_metadata(p)
        assert isinstance(result, ElfMetadata)
        assert result.symbols == []

    def test_invalid_section_headers(self, tmp_path: Path) -> None:
        """Craft a minimal ELF header (64-bit LE) with bogus section header data."""
        from abicheck.elf_metadata import ElfMetadata, parse_elf_metadata

        # Minimal 64-byte ELF header for 64-bit little-endian
        header = bytearray(64)
        header[0:4] = b"\x7fELF"
        header[4] = 2      # ELFCLASS64
        header[5] = 1      # ELFDATA2LSB
        header[6] = 1      # EV_CURRENT
        header[16:18] = (2).to_bytes(2, "little")  # ET_EXEC
        header[18:20] = (62).to_bytes(2, "little")  # EM_X86_64
        header[20:24] = (1).to_bytes(4, "little")  # EV_CURRENT
        header[40:48] = (64).to_bytes(8, "little")  # e_shoff = 64 (right after header)
        header[58:60] = (64).to_bytes(2, "little")  # e_shentsize = 64
        header[60:62] = (100).to_bytes(2, "little")  # e_shnum = 100 (bogus)
        # No actual section data -- pyelftools should fail gracefully
        p = tmp_path / "bad_sections.so"
        p.write_bytes(bytes(header) + b"\x00" * 128)
        result = parse_elf_metadata(p)
        assert isinstance(result, ElfMetadata)

    def test_nonexistent_file(self, tmp_path: Path) -> None:
        """File that does not exist -- should return empty metadata, not raise."""
        from abicheck.elf_metadata import ElfMetadata, parse_elf_metadata

        p = tmp_path / "does_not_exist.so"
        result = parse_elf_metadata(p)
        assert isinstance(result, ElfMetadata)


# ---------------------------------------------------------------------------
# 2. Malformed JSON snapshots (abicheck/serialization.py)
# ---------------------------------------------------------------------------

class TestMalformedJsonSnapshots:

    def test_empty_json_object(self, tmp_path: Path) -> None:
        """Empty JSON {} -- missing 'library' and 'version' keys."""
        p = tmp_path / "empty.json"
        p.write_text("{}", encoding="utf-8")
        with pytest.raises((KeyError, ValueError)):
            load_snapshot(p)

    def test_missing_required_fields(self, tmp_path: Path) -> None:
        """JSON with only library, missing version."""
        p = tmp_path / "partial.json"
        p.write_text('{"library": "libfoo.so"}', encoding="utf-8")
        with pytest.raises((KeyError, ValueError)):
            load_snapshot(p)

    def test_extra_unexpected_fields_tolerated(self, tmp_path: Path) -> None:
        """JSON with extra unknown top-level fields should be tolerated."""
        data = {
            "library": "libfoo.so",
            "version": "1.0",
            "functions": [],
            "variables": [],
            "types": [],
            "extra_field": "should be ignored",
            "another_unknown": 42,
        }
        p = tmp_path / "extra.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        snap = load_snapshot(p)
        assert snap.library == "libfoo.so"
        assert snap.version == "1.0"

    def test_wrong_types_functions_as_string(self, tmp_path: Path) -> None:
        """'functions' as a string instead of a list -- should raise or handle."""
        data = {
            "library": "libfoo.so",
            "version": "1.0",
            "functions": "not a list",
        }
        p = tmp_path / "wrong_type.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises((TypeError, ValueError)):
            load_snapshot(p)

    def test_truncated_json(self, tmp_path: Path) -> None:
        """Truncated JSON -- incomplete content."""
        p = tmp_path / "truncated.json"
        p.write_text('{"library": "libfoo.so", "vers', encoding="utf-8")
        with pytest.raises((json.JSONDecodeError, ValueError)):
            load_snapshot(p)

    def test_deeply_nested_json(self, tmp_path: Path) -> None:
        """Very deeply nested JSON -- should either parse or raise, not crash."""
        # Build a genuinely deeply nested structure inside an extra field
        nested: dict = {"library": "libfoo.so", "version": "1.0",
                        "functions": [], "variables": [], "types": []}
        inner: dict = {}
        nested["extra"] = inner
        for _ in range(200):
            child: dict = {}
            inner["nested"] = child
            inner = child
        inner["leaf"] = True
        p = tmp_path / "deep.json"
        p.write_text(json.dumps(nested), encoding="utf-8")
        # Should parse without crashing; deep nesting is in an extra field
        snap = load_snapshot(p)
        assert snap.library == "libfoo.so"

    def test_snapshot_from_dict_with_none_functions(self) -> None:
        """snapshot_from_dict when functions is None (not present)."""
        data = {"library": "lib.so", "version": "1.0"}
        snap = snapshot_from_dict(data)
        assert snap.functions == []
        assert snap.variables == []
        assert snap.types == []


# ---------------------------------------------------------------------------
# 3. Malformed suppression files (abicheck/suppression.py)
# ---------------------------------------------------------------------------

class TestMalformedSuppressionFiles:

    def test_empty_yaml(self, tmp_path: Path) -> None:
        """Empty YAML file -- should raise ValueError (not a dict)."""
        p = tmp_path / "empty.yaml"
        p.write_text("", encoding="utf-8")
        with pytest.raises(ValueError, match="must be a YAML mapping"):
            SuppressionList.load(p)

    def test_yaml_list_instead_of_dict(self, tmp_path: Path) -> None:
        """YAML that is a list instead of a dict."""
        p = tmp_path / "list.yaml"
        p.write_text("- item1\n- item2\n", encoding="utf-8")
        with pytest.raises(ValueError, match="must be a YAML mapping"):
            SuppressionList.load(p)

    def test_yaml_invalid_regex(self, tmp_path: Path) -> None:
        """YAML with an invalid regex pattern in symbol_pattern."""
        content = (
            "version: 1\n"
            "suppressions:\n"
            "  - symbol_pattern: '[invalid'\n"
            "    reason: test\n"
        )
        p = tmp_path / "bad_regex.yaml"
        p.write_text(content, encoding="utf-8")
        with pytest.raises(ValueError, match="Invalid symbol_pattern"):
            SuppressionList.load(p)

    def test_yaml_missing_version(self, tmp_path: Path) -> None:
        """YAML dict without 'version: 1'."""
        content = "suppressions:\n  - symbol: foo\n"
        p = tmp_path / "no_version.yaml"
        p.write_text(content, encoding="utf-8")
        with pytest.raises(ValueError, match="Unsupported suppression file version"):
            SuppressionList.load(p)

    def test_yaml_unknown_change_kind(self, tmp_path: Path) -> None:
        """Suppression entry with an unknown change_kind value."""
        content = (
            "version: 1\n"
            "suppressions:\n"
            "  - symbol: foo\n"
            "    change_kind: totally_bogus_kind\n"
        )
        p = tmp_path / "bad_kind.yaml"
        p.write_text(content, encoding="utf-8")
        with pytest.raises(ValueError, match="Unknown change_kind"):
            SuppressionList.load(p)

    def test_yaml_no_suppressions_key(self, tmp_path: Path) -> None:
        """Valid dict with version but no suppressions key -- returns empty list."""
        content = "version: 1\n"
        p = tmp_path / "no_sup.yaml"
        p.write_text(content, encoding="utf-8")
        sl = SuppressionList.load(p)
        assert len(sl) == 0

    def test_yaml_suppressions_not_a_list(self, tmp_path: Path) -> None:
        """suppressions is a string instead of a list."""
        content = "version: 1\nsuppressions: not_a_list\n"
        p = tmp_path / "bad_sup.yaml"
        p.write_text(content, encoding="utf-8")
        with pytest.raises(ValueError, match="must be a list"):
            SuppressionList.load(p)


# ---------------------------------------------------------------------------
# 4. Malformed policy files (abicheck/policy_file.py)
# ---------------------------------------------------------------------------

class TestMalformedPolicyFiles:

    def test_empty_yaml(self, tmp_path: Path) -> None:
        """Empty YAML file -- returns default policy."""
        p = tmp_path / "empty.yaml"
        p.write_text("", encoding="utf-8")
        pf = PolicyFile.load(p)
        assert pf.base_policy == "strict_abi"
        assert pf.overrides == {}

    def test_yaml_not_a_dict(self, tmp_path: Path) -> None:
        """YAML that parses as a list instead of a dict."""
        p = tmp_path / "list.yaml"
        p.write_text("- item1\n- item2\n", encoding="utf-8")
        with pytest.raises(ValueError, match="must be a YAML mapping"):
            PolicyFile.load(p)

    def test_invalid_overrides_not_dict(self, tmp_path: Path) -> None:
        """overrides is a list instead of a dict."""
        content = "base_policy: strict_abi\noverrides:\n  - bad\n"
        p = tmp_path / "bad_overrides.yaml"
        p.write_text(content, encoding="utf-8")
        with pytest.raises(ValueError, match="must be a YAML mapping"):
            PolicyFile.load(p)

    def test_unknown_change_kind_in_overrides(self, tmp_path: Path) -> None:
        """Unknown change kind name in overrides -- a hard load error.

        ADR-049 D8: a renamed/misspelled ChangeKind slug must not silently
        drop its override -- warning-and-skip was replaced with a raise.
        """
        from abicheck.errors import PolicyError

        content = (
            "base_policy: strict_abi\n"
            "overrides:\n"
            "  totally_made_up_kind: ignore\n"
        )
        p = tmp_path / "unknown_kind.yaml"
        p.write_text(content, encoding="utf-8")
        with pytest.raises(PolicyError, match="totally_made_up_kind"):
            PolicyFile.load(p)

    def test_invalid_severity_in_overrides(self, tmp_path: Path) -> None:
        """Valid change kind but invalid severity -- should raise ValueError."""
        content = (
            "base_policy: strict_abi\n"
            "overrides:\n"
            "  func_removed: explode\n"
        )
        p = tmp_path / "bad_severity.yaml"
        p.write_text(content, encoding="utf-8")
        with pytest.raises(ValueError, match="Invalid severity"):
            PolicyFile.load(p)

    def test_unknown_base_policy(self, tmp_path: Path) -> None:
        """base_policy with an unknown policy name."""
        content = "base_policy: nonexistent_policy\n"
        p = tmp_path / "bad_base.yaml"
        p.write_text(content, encoding="utf-8")
        with pytest.raises(ValueError, match="Unknown base_policy"):
            PolicyFile.load(p)

    def test_base_policy_not_string(self, tmp_path: Path) -> None:
        """base_policy as an integer instead of string."""
        content = "base_policy: 42\n"
        p = tmp_path / "int_base.yaml"
        p.write_text(content, encoding="utf-8")
        with pytest.raises(ValueError, match="must be a string"):
            PolicyFile.load(p)


# ---------------------------------------------------------------------------
# 5. Edge case model objects
# ---------------------------------------------------------------------------

class TestEdgeCaseModelObjects:

    def _empty_snapshot(self, name: str = "libfoo.so", version: str = "1.0") -> AbiSnapshot:
        return AbiSnapshot(library=name, version=version)

    def test_compare_two_empty_snapshots(self) -> None:
        """Comparing two empty snapshots should yield NO_CHANGE."""
        old = self._empty_snapshot(version="1.0")
        new = self._empty_snapshot(version="2.0")
        result = compare(old, new)
        assert result.verdict == Verdict.NO_CHANGE
        assert result.changes == []

    def test_snapshot_with_duplicate_mangled_names(self) -> None:
        """Duplicate mangled names -- first-wins policy, no crash."""
        snap = AbiSnapshot(
            library="lib.so",
            version="1.0",
            functions=[
                Function(name="foo", mangled="_Z3foov", return_type="void"),
                Function(name="foo_dup", mangled="_Z3foov", return_type="int"),
            ],
        )
        snap.index()
        # First-wins: the first function should be in the map
        f = snap.func_by_mangled("_Z3foov")
        assert f is not None
        assert f.name == "foo"

    def test_snapshot_with_empty_string_names(self) -> None:
        """Functions and variables with empty-string names."""
        snap = AbiSnapshot(
            library="lib.so",
            version="1.0",
            functions=[
                Function(name="", mangled="", return_type=""),
            ],
            variables=[
                Variable(name="", mangled="", type=""),
            ],
        )
        snap.index()
        # Should not crash
        assert snap.func_by_mangled("") is not None

    def test_snapshot_with_very_long_names(self) -> None:
        """Snapshot with names exceeding 1000 characters."""
        long_name = "A" * 2000
        long_mangled = "_Z" + "a" * 2000
        snap = AbiSnapshot(
            library="lib.so",
            version="1.0",
            functions=[
                Function(name=long_name, mangled=long_mangled, return_type="void"),
            ],
        )
        snap.index()
        f = snap.func_by_mangled(long_mangled)
        assert f is not None
        assert f.name == long_name

    def test_snapshot_with_unicode_names(self) -> None:
        """Snapshot with Unicode function/variable names."""
        snap = AbiSnapshot(
            library="lib.so",
            version="1.0",
            functions=[
                Function(
                    name="\u00e4\u00f6\u00fc\u00df_\u4e16\u754c",
                    mangled="_Z_unicode_\u00e4\u00f6\u00fc",
                    return_type="void",
                ),
            ],
            variables=[
                Variable(
                    name="\U0001f600_emoji_var",
                    mangled="_emoji_var",
                    type="int",
                ),
            ],
        )
        snap.index()
        assert snap.func_by_mangled("_Z_unicode_\u00e4\u00f6\u00fc") is not None
        assert snap.var_by_mangled("_emoji_var") is not None

    def test_compare_snapshot_against_itself(self) -> None:
        """Comparing a snapshot against itself should yield NO_CHANGE."""
        snap = AbiSnapshot(
            library="lib.so",
            version="1.0",
            functions=[
                Function(name="foo", mangled="_Z3foov", return_type="void"),
                Function(
                    name="bar",
                    mangled="_Z3bari",
                    return_type="int",
                    params=[Param(name="x", type="int")],
                ),
            ],
            variables=[
                Variable(name="g_val", mangled="g_val", type="int"),
            ],
        )
        result = compare(snap, snap)
        assert result.verdict == Verdict.NO_CHANGE

    def test_compare_added_function(self) -> None:
        """Adding a function should yield COMPATIBLE, not crash."""
        old = self._empty_snapshot(version="1.0")
        new = AbiSnapshot(
            library="libfoo.so",
            version="2.0",
            functions=[
                Function(name="new_func", mangled="_Z8new_funcv", return_type="void"),
            ],
        )
        result = compare(old, new)
        assert result.verdict == Verdict.COMPATIBLE

    def test_compare_removed_function(self) -> None:
        """Removing a function should yield BREAKING."""
        old = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[
                Function(name="old_func", mangled="_Z8old_funcv", return_type="void"),
            ],
        )
        new = self._empty_snapshot(version="2.0")
        result = compare(old, new)
        assert result.verdict == Verdict.BREAKING


# ---------------------------------------------------------------------------
# 6. compat/abicc_dump_import.py edge cases
# ---------------------------------------------------------------------------

class TestAbiccDumpImportEdgeCases:

    def test_looks_like_perl_dump_false_positive(self) -> None:
        """Text that starts with $VAR1 but has malformed content."""
        assert looks_like_perl_dump("$VAR1 = garbage that is not perl;")

    def test_malformed_perl_dump_file(self, tmp_path: Path) -> None:
        """File starting with $VAR1 but containing unparseable Perl."""
        p = tmp_path / "bad.dump"
        p.write_text("$VAR1 = { 'unclosed_brace ;", encoding="utf-8")
        with pytest.raises(ValueError):
            import_abicc_perl_dump(p)

    def test_empty_perl_dump(self, tmp_path: Path) -> None:
        """File with $VAR1 = {};  -- valid empty hash."""
        p = tmp_path / "empty.dump"
        p.write_text("$VAR1 = {};", encoding="utf-8")
        snap = import_abicc_perl_dump(p)
        assert isinstance(snap, AbiSnapshot)
        assert snap.functions == []
        assert snap.variables == []

    def test_perl_dump_minimal_sections(self, tmp_path: Path) -> None:
        """Perl dump with minimal sections: TypeInfo and SymbolInfo empty."""
        content = (
            "$VAR1 = {\n"
            "  'TypeInfo' => {},\n"
            "  'SymbolInfo' => {},\n"
            "  'LibraryName' => 'libtest.so',\n"
            "  'LibraryVersion' => '0.1',\n"
            "};\n"
        )
        p = tmp_path / "minimal.dump"
        p.write_text(content, encoding="utf-8")
        snap = import_abicc_perl_dump(p)
        assert snap.library == "libtest.so"
        assert snap.version == "0.1"
        assert snap.functions == []

    def test_perl_dump_not_starting_with_var1(self, tmp_path: Path) -> None:
        """File that does not start with $VAR1 -- should raise."""
        p = tmp_path / "notperl.dump"
        p.write_text("{'key': 'value'}", encoding="utf-8")
        with pytest.raises(ValueError, match="Invalid ABICC Perl dump"):
            import_abicc_perl_dump(p)

    def test_looks_like_perl_dump_empty_string(self) -> None:
        """Empty string should not look like a Perl dump."""
        assert not looks_like_perl_dump("")

    def test_looks_like_perl_dump_with_leading_whitespace(self) -> None:
        """Leading whitespace before $VAR1 should still be detected."""
        assert looks_like_perl_dump("   \n  $VAR1 = {};")


# ---------------------------------------------------------------------------
# 7. Reporter edge cases
# ---------------------------------------------------------------------------

class TestReporterEdgeCases:

    def _make_diff_result(
        self,
        changes: list[Change] | None = None,
        verdict: Verdict = Verdict.NO_CHANGE,
    ) -> DiffResult:
        return DiffResult(
            old_version="1.0",
            new_version="2.0",
            library="libtest.so",
            changes=changes or [],
            verdict=verdict,
        )

    def test_empty_changes_json(self) -> None:
        """DiffResult with empty changes list -- JSON output."""
        result = self._make_diff_result()
        output = to_json(result)
        parsed = json.loads(output)
        assert parsed["verdict"] == "NO_CHANGE"
        assert parsed["changes"] == []

    def test_empty_changes_markdown(self) -> None:
        """DiffResult with empty changes list -- Markdown output."""
        result = self._make_diff_result()
        output = to_markdown(result)
        assert "libtest.so" in output
        assert "NO_CHANGE" in output

    def test_changes_with_none_values(self) -> None:
        """Change objects with None old_value/new_value."""
        changes = [
            Change(
                kind=ChangeKind.FUNC_REMOVED,
                symbol="_Z3foov",
                description="function removed",
                old_value=None,
                new_value=None,
            ),
        ]
        result = self._make_diff_result(changes=changes, verdict=Verdict.BREAKING)
        json_out = to_json(result)
        parsed = json.loads(json_out)
        assert len(parsed["changes"]) == 1
        assert parsed["changes"][0]["old_value"] is None

        md_out = to_markdown(result)
        assert "function removed" in md_out

    def test_change_with_both_old_and_new_values(self) -> None:
        """Change with both old_value and new_value set."""
        changes = [
            Change(
                kind=ChangeKind.FUNC_RETURN_CHANGED,
                symbol="_Z3barv",
                description="return type changed",
                old_value="int",
                new_value="void",
            ),
        ]
        result = self._make_diff_result(changes=changes, verdict=Verdict.BREAKING)
        json_out = to_json(result)
        parsed = json.loads(json_out)
        assert parsed["changes"][0]["old_value"] == "int"
        assert parsed["changes"][0]["new_value"] == "void"

        md_out = to_markdown(result)
        assert "int" in md_out
        assert "void" in md_out

    def test_multiple_verdict_levels_markdown(self) -> None:
        """DiffResult with BREAKING verdict renders without error."""
        result = self._make_diff_result(verdict=Verdict.BREAKING)
        md = to_markdown(result)
        assert "BREAKING" in md

    def test_stat_mode_json(self) -> None:
        """Stat mode JSON output for empty result."""
        result = self._make_diff_result()
        output = to_json(result, stat=True)
        parsed = json.loads(output)
        assert "verdict" in parsed
        assert "summary" in parsed

    def test_stat_mode_markdown(self) -> None:
        """Stat mode Markdown output for empty result."""
        result = self._make_diff_result()
        output = to_markdown(result, stat=True)
        assert "NO_CHANGE" in output
