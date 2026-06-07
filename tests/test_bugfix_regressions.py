# Copyright 2026 Nikolay Petrov
#
# Regression tests for the 7 bug fixes in the abicheck PR.
# Each class covers one bug to ensure the fix is never reverted.

from __future__ import annotations

import hashlib
import json
import tempfile
import textwrap
from pathlib import Path

import click
import pytest

from abicheck.checker import Change, ChangeKind, DiffResult, Verdict, compare
from abicheck.model import (
    AbiSnapshot,
    EnumMember,
    EnumType,
    Function,
    Param,
    ParamKind,
    RecordType,
    TypeField,
    Variable,
    Visibility,
    is_non_abi_surface_type,
)
from abicheck.report_summary import build_summary, compatibility_metrics

# ── Shared helpers ────────────────────────────────────────────────────────────

def _snap(ver: str = "1.0", funcs=None, variables=None, types=None, enums=None) -> AbiSnapshot:
    s = AbiSnapshot(library="libtest.so", version=ver)
    if funcs is not None:
        s.functions = funcs
    if variables is not None:
        s.variables = variables
    if types is not None:
        s.types = types
    if enums is not None:
        s.enums = enums
    return s


def _pub_func(name: str, mangled: str, ret: str = "void", params=None) -> Function:
    return Function(
        name=name, mangled=mangled, return_type=ret,
        params=params or [], visibility=Visibility.PUBLIC,
    )


def _pub_var(name: str) -> Variable:
    return Variable(name=name, mangled=name, type="int", visibility=Visibility.PUBLIC)


class TestGoRuntimeDwarfNoise:
    """Go runtime/internal DWARF types are not the exported C ABI surface."""

    def test_go_runtime_type_churn_does_not_break_c_shared_library(self):
        old = _snap(
            funcs=[_pub_func("AdbcDriverInit", "AdbcDriverInit")],
            types=[
                RecordType(
                    name="runtime.g",
                    kind="struct",
                    size_bits=3520,
                    fields=[TypeField(name="goid", type="uint64", offset_bits=1280)],
                ),
                RecordType(
                    name="internal/abi.SwissMapType",
                    kind="struct",
                    size_bits=64,
                ),
                RecordType(
                    name="google.golang.org/grpc/internal/channelz.Identifier",
                    kind="struct",
                    size_bits=128,
                ),
                RecordType(
                    name="map<net/http.http2FrameType,string>",
                    kind="struct",
                    size_bits=256,
                ),
            ],
        )
        new = _snap(
            "2.0",
            funcs=[_pub_func("AdbcDriverInit", "AdbcDriverInit")],
            types=[
                RecordType(
                    name="runtime.g",
                    kind="struct",
                    size_bits=3648,
                    fields=[TypeField(name="goid", type="uint64", offset_bits=1216)],
                ),
                RecordType(
                    name="google.golang.org/grpc/internal/channelz.Identifier",
                    kind="struct",
                    size_bits=192,
                ),
                RecordType(
                    name="map<net/http.http2FrameType,string>",
                    kind="struct",
                    size_bits=320,
                ),
            ],
        )

        result = compare(old, new)

        assert result.verdict is Verdict.NO_CHANGE
        assert not any(c.kind == ChangeKind.TYPE_SIZE_CHANGED for c in result.changes)

    def test_global_cpp_map_template_type_is_kept(self):
        old = _snap(types=[RecordType(name="map<int,int>", kind="struct", size_bits=64)])
        new = _snap("2.0", types=[RecordType(name="map<int,int>", kind="struct", size_bits=128)])

        result = compare(old, new)

        assert is_non_abi_surface_type("map<int,int>") is False
        assert result.verdict is Verdict.BREAKING
        assert any(c.kind == ChangeKind.TYPE_SIZE_CHANGED for c in result.changes)


# ── Bug 1: Enum symbols use member-qualified format ──────────────────────────

class TestBug1EnumSymbolFormat:
    """Enum change symbols must be 'EnumName::MemberName', not just 'EnumName'."""

    def test_enum_member_removed_symbol_has_member(self):
        old = _snap(enums=[EnumType("Color", [EnumMember("RED", 0), EnumMember("GREEN", 1)])])
        new = _snap(enums=[EnumType("Color", [EnumMember("RED", 0)])])
        result = compare(old, new)
        removed = [c for c in result.changes if c.kind == ChangeKind.ENUM_MEMBER_REMOVED]
        assert len(removed) == 1
        assert removed[0].symbol == "Color::GREEN"

    def test_enum_member_added_symbol_has_member(self):
        old = _snap(enums=[EnumType("Color", [EnumMember("RED", 0)])])
        new = _snap(enums=[EnumType("Color", [EnumMember("RED", 0), EnumMember("BLUE", 2)])])
        result = compare(old, new)
        added = [c for c in result.changes if c.kind == ChangeKind.ENUM_MEMBER_ADDED]
        assert len(added) == 1
        assert added[0].symbol == "Color::BLUE"

    def test_enum_member_value_changed_symbol_has_member(self):
        old = _snap(enums=[EnumType("Err", [EnumMember("OK", 0), EnumMember("FAIL", 1)])])
        new = _snap(enums=[EnumType("Err", [EnumMember("OK", 0), EnumMember("FAIL", 99)])])
        result = compare(old, new)
        changed = [c for c in result.changes if c.kind == ChangeKind.ENUM_MEMBER_VALUE_CHANGED]
        assert len(changed) == 1
        assert changed[0].symbol == "Err::FAIL"

    def test_multiple_members_produce_distinct_symbols(self):
        old = _snap(enums=[EnumType("X", [EnumMember("A", 0), EnumMember("B", 1), EnumMember("C", 2)])])
        new = _snap(enums=[EnumType("X", [EnumMember("A", 0)])])
        result = compare(old, new)
        removed = [c for c in result.changes if c.kind == ChangeKind.ENUM_MEMBER_REMOVED]
        symbols = {c.symbol for c in removed}
        assert symbols == {"X::B", "X::C"}

    def test_sentinel_member_symbol_has_member(self):
        old = _snap(enums=[EnumType("E", [EnumMember("A", 0), EnumMember("E_MAX", 10)])])
        new = _snap(enums=[EnumType("E", [EnumMember("A", 0), EnumMember("E_MAX", 20)])])
        result = compare(old, new)
        changed = [c for c in result.changes if c.kind == ChangeKind.ENUM_LAST_MEMBER_VALUE_CHANGED]
        assert len(changed) == 1
        assert changed[0].symbol == "E::E_MAX"


# ── Bug 2: Human-readable parameter format ───────────────────────────────────

class TestBug2ParamFormat:
    """FUNC_PARAMS_CHANGED old_value/new_value must be human-readable, not repr()."""

    def test_simple_param_type_format(self):
        old_f = _pub_func("f", "_Zf", params=[Param(name="x", type="int")])
        new_f = _pub_func("f", "_Zf", params=[Param(name="x", type="long")])
        result = compare(_snap(funcs=[old_f]), _snap("2.0", funcs=[new_f]))
        change = next(c for c in result.changes if c.kind == ChangeKind.FUNC_PARAMS_CHANGED)
        assert change.old_value == "int"
        assert change.new_value == "long"

    def test_pointer_param_shows_asterisk(self):
        # Param.type already carries the sigil (as produced by the DWARF parser)
        old_f = _pub_func("f", "_Zf", params=[Param(name="p", type="int *", kind=ParamKind.POINTER)])
        new_f = _pub_func("f", "_Zf", params=[Param(name="p", type="void *", kind=ParamKind.POINTER)])
        result = compare(_snap(funcs=[old_f]), _snap("2.0", funcs=[new_f]))
        change = next(c for c in result.changes if c.kind == ChangeKind.FUNC_PARAMS_CHANGED)
        assert change.old_value == "int *"
        assert change.new_value == "void *"

    def test_reference_param_shows_ampersand(self):
        old_f = _pub_func("f", "_Zf", params=[Param(name="r", type="int &", kind=ParamKind.REFERENCE)])
        new_f = _pub_func("f", "_Zf", params=[Param(name="r", type="int &&", kind=ParamKind.RVALUE_REF)])
        result = compare(_snap(funcs=[old_f]), _snap("2.0", funcs=[new_f]))
        change = next(c for c in result.changes if c.kind == ChangeKind.FUNC_PARAMS_CHANGED)
        assert change.old_value == "int &"
        assert change.new_value == "int &&"

    def test_no_repr_artifacts(self):
        """Ensure no Python repr artifacts like ParamKind.VALUE or tuple syntax."""
        old_f = _pub_func("f", "_Zf", params=[Param(name="a", type="int"), Param(name="b", type="float")])
        new_f = _pub_func("f", "_Zf", params=[Param(name="a", type="int")])
        result = compare(_snap(funcs=[old_f]), _snap("2.0", funcs=[new_f]))
        change = next(c for c in result.changes if c.kind == ChangeKind.FUNC_PARAMS_CHANGED)
        assert "ParamKind" not in change.old_value
        assert "[(" not in change.old_value
        assert change.old_value == "int, float"
        assert change.new_value == "int"

    def test_empty_params_to_params(self):
        old_f = _pub_func("f", "_Zf", params=[])
        new_f = _pub_func("f", "_Zf", params=[Param(name="x", type="int")])
        result = compare(_snap(funcs=[old_f]), _snap("2.0", funcs=[new_f]))
        change = next(c for c in result.changes if c.kind == ChangeKind.FUNC_PARAMS_CHANGED)
        assert change.old_value == "(none)"
        assert change.new_value == "int"


# ── Bug 3: Snapshot metadata skipped for JSON/Perl inputs ────────────────────

class TestBug3MetadataCollection:
    """_collect_metadata must return None for JSON/Perl snapshots."""

    def test_binary_file_returns_metadata(self):
        from abicheck.cli import _collect_metadata
        with tempfile.NamedTemporaryFile(suffix=".so", delete=False) as f:
            content = b"fake binary content"
            f.write(content)
            f.flush()
            path = Path(f.name)
        try:
            meta = _collect_metadata(path)
            assert meta is not None
            assert meta.sha256 == hashlib.sha256(content).hexdigest()
            assert meta.size_bytes == len(content)
        finally:
            path.unlink()

    def test_json_snapshot_returns_none(self):
        from abicheck.cli import _collect_metadata
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            json.dump({"library": "lib.so", "version": "1.0"}, f)
            path = Path(f.name)
        try:
            meta = _collect_metadata(path)
            assert meta is None
        finally:
            path.unlink()

    def test_perl_dump_returns_none(self):
        from abicheck.cli import _collect_metadata
        with tempfile.NamedTemporaryFile(suffix=".dump", mode="w", delete=False) as f:
            f.write("$VAR1 = { 'library' => 'lib.so' };")
            f.flush()
            path = Path(f.name)
        try:
            meta = _collect_metadata(path)
            assert meta is None
        finally:
            path.unlink()


# ── Bug 4: Policy overrides affect DiffResult section properties ─────────────

class TestBug4PolicyOverrideProperties:
    """DiffResult.breaking/compatible/etc. must reflect PolicyFile overrides."""

    def test_override_moves_kind_from_breaking_to_compatible(self):
        from abicheck.policy_file import PolicyFile

        pf = PolicyFile(
            base_policy="strict_abi",
            overrides={ChangeKind.FUNC_REMOVED: Verdict.COMPATIBLE},
        )
        result = DiffResult(
            old_version="1.0", new_version="2.0", library="lib.so",
            changes=[Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "func removed")],
            verdict=Verdict.COMPATIBLE,
            policy="strict_abi",
            policy_file=pf,
        )
        # Without override, FUNC_REMOVED is BREAKING
        assert len(result.breaking) == 0
        assert len(result.compatible) == 1
        assert result.compatible[0].kind == ChangeKind.FUNC_REMOVED

    def test_override_moves_kind_to_risk(self):
        from abicheck.policy_file import PolicyFile

        pf = PolicyFile(
            base_policy="strict_abi",
            overrides={ChangeKind.ENUM_MEMBER_ADDED: Verdict.COMPATIBLE_WITH_RISK},
        )
        result = DiffResult(
            old_version="1.0", new_version="2.0", library="lib.so",
            changes=[Change(ChangeKind.ENUM_MEMBER_ADDED, "E::NEW", "added")],
            verdict=Verdict.COMPATIBLE_WITH_RISK,
            policy="strict_abi",
            policy_file=pf,
        )
        assert len(result.risk) == 1
        assert len(result.compatible) == 0

    def test_no_policy_file_uses_base_policy(self):
        """Without policy_file, properties use base policy sets directly."""
        result = DiffResult(
            old_version="1.0", new_version="2.0", library="lib.so",
            changes=[Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "removed")],
            verdict=Verdict.BREAKING,
            policy="strict_abi",
        )
        assert len(result.breaking) == 1
        assert len(result.compatible) == 0

    def test_override_from_yaml(self, tmp_path):
        from abicheck.policy_file import PolicyFile

        p = tmp_path / "policy.yaml"
        p.write_text(textwrap.dedent("""\
            base_policy: strict_abi
            overrides:
              func_removed: ignore
        """), encoding="utf-8")
        pf = PolicyFile.load(p)
        result = DiffResult(
            old_version="1.0", new_version="2.0", library="lib.so",
            changes=[Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "removed")],
            verdict=pf.compute_verdict([Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "removed")]),
            policy=pf.base_policy,
            policy_file=pf,
        )
        assert len(result.breaking) == 0
        assert len(result.compatible) == 1


# ── Bug 5: Safe output file writing with parent dir creation ─────────────────

class TestBug5SafeWriteOutput:
    """_safe_write_output creates parent directories and handles errors."""

    def test_creates_parent_dirs(self, tmp_path):
        from abicheck.cli import _safe_write_output
        out = tmp_path / "deeply" / "nested" / "report.txt"
        _safe_write_output(out, "hello")
        assert out.read_text() == "hello"

    def test_overwrites_existing_file(self, tmp_path):
        from abicheck.cli import _safe_write_output
        out = tmp_path / "report.txt"
        out.write_text("old")
        _safe_write_output(out, "new")
        assert out.read_text() == "new"

    def test_bad_path_raises_click_exception(self, tmp_path):
        from abicheck.cli import _safe_write_output
        # Create a regular file, then try to write inside it as if it were a
        # directory — fails on every OS because mkdir will raise OSError.
        blocker = tmp_path / "blocker"
        blocker.write_text("I am a file")
        bad_path = blocker / "subdir" / "report.txt"
        with pytest.raises(click.ClickException, match="Cannot write to"):
            _safe_write_output(bad_path, "data")


# ── Bug 7: ELF validation for deps/stack-check ──────────────────────────────

class TestBug7ElfValidation:
    """deps and stack-check commands must reject non-ELF inputs."""

    def test_deps_rejects_json_file(self, tmp_path):
        from click.testing import CliRunner

        from abicheck.cli import main

        fake = tmp_path / "fake.json"
        fake.write_text('{"library": "lib.so"}')
        runner = CliRunner()
        result = runner.invoke(main, ["deps", str(fake)])
        assert result.exit_code != 0
        assert "ELF" in result.output or "ELF" in (result.exception and str(result.exception) or "")

    def test_deps_rejects_text_file(self, tmp_path):
        from click.testing import CliRunner

        from abicheck.cli import main

        fake = tmp_path / "notes.txt"
        fake.write_text("not a binary")
        runner = CliRunner()
        result = runner.invoke(main, ["deps", str(fake)])
        assert result.exit_code != 0


# ── Bug 8: affected_pct computed from old_symbol_count ───────────────────────

class TestBug8AffectedPct:
    """affected_pct must be non-zero when old_symbol_count is known."""

    def test_affected_pct_with_old_symbol_count(self):
        metrics = compatibility_metrics(
            [Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "removed")],
            old_symbol_count=10,
        )
        assert metrics.affected_pct == pytest.approx(10.0)

    def test_binary_compatibility_pct_with_old_symbol_count(self):
        metrics = compatibility_metrics(
            [Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "removed")],
            old_symbol_count=10,
        )
        assert metrics.binary_compatibility_pct == pytest.approx(90.0)

    def test_old_symbol_count_stored_on_diff_result(self):
        old = _snap(funcs=[_pub_func("a", "_Za"), _pub_func("b", "_Zb")],
                    variables=[_pub_var("g")])
        new = _snap("2.0", funcs=[_pub_func("a", "_Za")])
        result = compare(old, new)
        assert result.old_symbol_count == 3  # 2 functions + 1 variable

    def test_build_summary_uses_old_symbol_count(self):
        result = DiffResult(
            old_version="1.0", new_version="2.0", library="lib.so",
            changes=[Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "removed")],
            verdict=Verdict.BREAKING,
            old_symbol_count=20,
        )
        summary = build_summary(result)
        assert summary.affected_pct == pytest.approx(5.0)
        assert summary.binary_compatibility_pct == pytest.approx(95.0)

    def test_zero_symbol_count_gives_none(self):
        """A library with 0 public symbols should have old_symbol_count=None."""
        old = _snap(funcs=[], variables=[])
        new = _snap("2.0", funcs=[])
        result = compare(old, new)
        assert result.old_symbol_count is None


# ── Bug 1 + Suppression interaction: type_pattern still matches ──────────────

class TestBug1SuppressionInteraction:
    """type_pattern suppression must work with member-qualified enum symbols."""

    def test_type_pattern_matches_enum_member_qualified_symbol(self, tmp_path):
        from abicheck.suppression import SuppressionList

        p = tmp_path / "sup.yaml"
        p.write_text(textwrap.dedent("""\
            version: 1
            suppressions:
              - type_pattern: "Status"
                reason: "intentional enum change"
        """), encoding="utf-8")
        sl = SuppressionList.load(p)

        old = _snap(enums=[EnumType("Status", [EnumMember("OK", 0), EnumMember("FAIL", 1)])])
        new = _snap("2.0", enums=[EnumType("Status", [EnumMember("OK", 0)])])
        result = compare(old, new, suppression=sl)
        assert result.suppressed_count >= 1

    def test_type_pattern_regex_still_works(self, tmp_path):
        from abicheck.suppression import SuppressionList

        p = tmp_path / "sup.yaml"
        p.write_text(textwrap.dedent("""\
            version: 1
            suppressions:
              - type_pattern: "Status|Error"
                reason: "suppress both"
        """), encoding="utf-8")
        sl = SuppressionList.load(p)

        old = _snap(enums=[
            EnumType("Status", [EnumMember("OK", 0), EnumMember("FAIL", 1)]),
            EnumType("Error", [EnumMember("NONE", 0), EnumMember("TIMEOUT", 1)]),
        ])
        new = _snap("2.0", enums=[
            EnumType("Status", [EnumMember("OK", 0)]),
            EnumType("Error", [EnumMember("NONE", 0)]),
        ])
        result = compare(old, new, suppression=sl)
        assert result.suppressed_count >= 2
