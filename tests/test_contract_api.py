"""Contract and API tests verifying stable public interfaces.

Tests the CLI exit codes, JSON/SARIF/Markdown output schema stability,
snapshot serialization contract, DiffResult API, and ChangeKind enum
completeness.
"""
from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from abicheck.checker import Change, ChangeKind, DiffResult, Verdict, compare
from abicheck.checker_policy import (
    POLICY_REGISTRY,
    impact_for,
    policy_for,
)
from abicheck.cli import main
from abicheck.model import (
    AbiSnapshot,
    EnumMember,
    EnumType,
    Function,
    RecordType,
    TypeField,
    Variable,
    Visibility,
)
from abicheck.reporter import to_json, to_markdown
from abicheck.sarif import to_sarif_str
from abicheck.serialization import (
    load_snapshot,
    snapshot_to_json,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _snap(ver: str, funcs=None, variables=None, types=None, enums=None) -> AbiSnapshot:
    s = AbiSnapshot(library="libtest.so", version=ver)
    s.functions = funcs or []
    s.variables = variables or []
    s.types = types or []
    s.enums = enums or []
    return s


def _fn(name: str, mangled: str, ret: str = "int") -> Function:
    return Function(name=name, mangled=mangled, return_type=ret,
                    visibility=Visibility.PUBLIC)


def _write_snap(path: Path, snap: AbiSnapshot) -> None:
    path.write_text(snapshot_to_json(snap), encoding="utf-8")


def _make_diff(
    changes: list[Change] | None = None,
    verdict: Verdict = Verdict.BREAKING,
) -> DiffResult:
    return DiffResult(
        old_version="1.0",
        new_version="2.0",
        library="libtest.so",
        changes=changes or [],
        verdict=verdict,
    )


# ===========================================================================
# 1. CLI exit codes match documentation
# ===========================================================================


class TestCliExitCodes:
    """Exit code contract:
    0 -- NO_CHANGE or COMPATIBLE
    2 -- API_BREAK
    4 -- BREAKING
    """

    def test_no_change_exit_0(self, tmp_path: Path) -> None:
        """Identical snapshots -> exit 0."""
        runner = CliRunner()
        snap = _snap("1.0", funcs=[_fn("foo", "_Z3foov")])
        old_p = tmp_path / "old.json"
        new_p = tmp_path / "new.json"
        _write_snap(old_p, snap)
        _write_snap(new_p, snap)

        result = runner.invoke(main, ["compare", str(old_p), str(new_p)])
        assert result.exit_code == 0, (
            f"NO_CHANGE should exit 0, got {result.exit_code}\n{result.output}"
        )

    def test_compatible_exit_0(self, tmp_path: Path) -> None:
        """New function added -> COMPATIBLE -> exit 0."""
        runner = CliRunner()
        old_p = tmp_path / "old.json"
        new_p = tmp_path / "new.json"
        _write_snap(old_p, _snap("1.0", funcs=[_fn("foo", "_Z3foov")]))
        _write_snap(new_p, _snap("2.0", funcs=[
            _fn("foo", "_Z3foov"),
            _fn("bar", "_Z3barv"),
        ]))

        result = runner.invoke(main, ["compare", str(old_p), str(new_p)])
        assert result.exit_code == 0, (
            f"COMPATIBLE should exit 0, got {result.exit_code}\n{result.output}"
        )

    def test_api_break_exit_2(self, tmp_path: Path) -> None:
        """Enum member renamed -> API_BREAK -> exit 2."""
        runner = CliRunner()
        old_p = tmp_path / "old.json"
        new_p = tmp_path / "new.json"
        _write_snap(old_p, _snap("1.0", enums=[EnumType(
            name="Status",
            members=[EnumMember("OK", 0), EnumMember("FAIL", 1)],
        )]))
        _write_snap(new_p, _snap("2.0", enums=[EnumType(
            name="Status",
            members=[EnumMember("OK", 0), EnumMember("ERROR", 1)],
        )]))

        result = runner.invoke(main, ["compare", str(old_p), str(new_p)])
        assert result.exit_code == 2, (
            f"API_BREAK should exit 2, got {result.exit_code}\n{result.output}"
        )

    def test_breaking_exit_4(self, tmp_path: Path) -> None:
        """Function removed -> BREAKING -> exit 4."""
        runner = CliRunner()
        old_p = tmp_path / "old.json"
        new_p = tmp_path / "new.json"
        _write_snap(old_p, _snap("1.0", funcs=[
            _fn("foo", "_Z3foov"),
            _fn("bar", "_Z3barv"),
        ]))
        _write_snap(new_p, _snap("2.0", funcs=[_fn("foo", "_Z3foov")]))

        result = runner.invoke(main, ["compare", str(old_p), str(new_p)])
        assert result.exit_code == 4, (
            f"BREAKING should exit 4, got {result.exit_code}\n{result.output}"
        )


# ===========================================================================
# 2. JSON output schema stability
# ===========================================================================


class TestJsonOutputSchema:
    """Verify JSON reporter output has all required top-level and per-change keys."""

    def _get_json(self, diff: DiffResult) -> dict:
        return json.loads(to_json(diff))

    def test_top_level_keys_present(self) -> None:
        c = Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "Function foo removed")
        diff = _make_diff(changes=[c], verdict=Verdict.BREAKING)
        d = self._get_json(diff)
        for key in ("verdict", "changes"):
            assert key in d, f"Missing required top-level key: {key}"

    def test_summary_keys_present(self) -> None:
        c = Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "Function foo removed")
        diff = _make_diff(changes=[c], verdict=Verdict.BREAKING)
        d = self._get_json(diff)
        summary = d.get("summary", {})
        assert "breaking" in summary
        assert "total_changes" in summary

    def test_change_has_required_keys(self) -> None:
        c = Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "Function foo removed")
        diff = _make_diff(changes=[c], verdict=Verdict.BREAKING)
        d = self._get_json(diff)
        assert len(d["changes"]) > 0
        change = d["changes"][0]
        for key in ("kind", "symbol", "description"):
            assert key in change, f"Missing required change key: {key}"

    def test_no_change_verdict_in_json(self) -> None:
        diff = _make_diff(changes=[], verdict=Verdict.NO_CHANGE)
        d = self._get_json(diff)
        assert d["verdict"] == "NO_CHANGE"

    def test_compatible_verdict_in_json(self) -> None:
        c = Change(ChangeKind.FUNC_ADDED, "_Z3barv", "New function bar")
        diff = _make_diff(changes=[c], verdict=Verdict.COMPATIBLE)
        d = self._get_json(diff)
        assert d["verdict"] == "COMPATIBLE"


# ===========================================================================
# 3. SARIF output validation
# ===========================================================================


class TestSarifOutputSchema:
    """Verify SARIF output has required structure per SARIF 2.1.0."""

    def _get_sarif(self, diff: DiffResult) -> dict:
        return json.loads(to_sarif_str(diff))

    def test_sarif_top_level_structure(self) -> None:
        c = Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "Function foo removed")
        diff = _make_diff(changes=[c], verdict=Verdict.BREAKING)
        doc = self._get_sarif(diff)
        assert doc["version"] == "2.1.0"
        assert "$schema" in doc
        assert "runs" in doc
        assert len(doc["runs"]) >= 1

    def test_sarif_tool_section(self) -> None:
        c = Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "Function foo removed")
        diff = _make_diff(changes=[c], verdict=Verdict.BREAKING)
        doc = self._get_sarif(diff)
        run = doc["runs"][0]
        assert "tool" in run
        assert "driver" in run["tool"]

    def test_sarif_results_present(self) -> None:
        c = Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "Function foo removed")
        diff = _make_diff(changes=[c], verdict=Verdict.BREAKING)
        doc = self._get_sarif(diff)
        results = doc["runs"][0]["results"]
        assert len(results) > 0

    def test_sarif_result_has_required_fields(self) -> None:
        c = Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "Function foo removed")
        diff = _make_diff(changes=[c], verdict=Verdict.BREAKING)
        doc = self._get_sarif(diff)
        result = doc["runs"][0]["results"][0]
        assert "ruleId" in result
        assert "message" in result
        assert "level" in result


# ===========================================================================
# 4. Markdown output structure
# ===========================================================================


class TestMarkdownOutputStructure:
    """Verify markdown output contains expected headers and format."""

    def test_verdict_present_in_markdown(self) -> None:
        diff = _make_diff(changes=[], verdict=Verdict.NO_CHANGE)
        md = to_markdown(diff)
        assert "NO_CHANGE" in md

    def test_breaking_section_present(self) -> None:
        c = Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "Function foo removed",
                   old_value="foo")
        diff = _make_diff(changes=[c], verdict=Verdict.BREAKING)
        md = to_markdown(diff)
        assert "BREAKING" in md
        # Should contain a breaking changes section
        assert "Breaking" in md

    def test_compatible_section_present(self) -> None:
        c = Change(ChangeKind.FUNC_ADDED, "_Z3barv", "New function bar",
                   new_value="bar")
        diff = _make_diff(changes=[c], verdict=Verdict.COMPATIBLE)
        md = to_markdown(diff)
        assert "COMPATIBLE" in md

    def test_legend_present(self) -> None:
        diff = _make_diff(changes=[], verdict=Verdict.NO_CHANGE)
        md = to_markdown(diff)
        assert "Legend" in md


# ===========================================================================
# 5. Snapshot JSON schema
# ===========================================================================


class TestSnapshotJsonSchema:
    """Verify snapshot serialization round-trips correctly with required keys."""

    def test_snapshot_has_required_top_level_keys(self) -> None:
        from abicheck.storage.sectioned_document import from_sectioned_document

        snap = _snap("1.0",
                     funcs=[_fn("foo", "_Z3foov")],
                     enums=[EnumType(name="E", members=[EnumMember("A", 0)])])
        d = from_sectioned_document(json.loads(snapshot_to_json(snap)))
        for key in ("library", "version", "functions", "variables", "types", "enums"):
            assert key in d, f"Missing required snapshot key: {key}"

    def test_function_has_required_keys(self) -> None:
        from abicheck.storage.sectioned_document import from_sectioned_document

        snap = _snap("1.0", funcs=[_fn("foo", "_Z3foov")])
        d = from_sectioned_document(json.loads(snapshot_to_json(snap)))
        func = d["functions"][0]
        for key in ("name", "mangled", "return_type"):
            assert key in func, f"Missing required function key: {key}"

    def test_roundtrip_preserves_data(self, tmp_path: Path) -> None:
        snap = _snap("1.0",
                     funcs=[_fn("foo", "_Z3foov")],
                     variables=[Variable(name="g", mangled="_g", type="int")],
                     types=[RecordType(name="S", kind="struct", size_bits=32,
                                       fields=[TypeField("x", "int", 0)])])
        path = tmp_path / "snap.json"
        _write_snap(path, snap)
        loaded = load_snapshot(path)
        assert loaded.library == "libtest.so"
        assert loaded.version == "1.0"
        assert len(loaded.functions) == 1
        assert loaded.functions[0].name == "foo"
        assert len(loaded.variables) == 1
        assert len(loaded.types) == 1


# ===========================================================================
# 6. DiffResult API contract
# ===========================================================================


class TestDiffResultApiContract:
    """Verify DiffResult exposes expected attributes."""

    def test_diffresult_has_expected_attributes(self) -> None:
        diff = _make_diff()
        assert hasattr(diff, "verdict")
        assert hasattr(diff, "changes")
        assert hasattr(diff, "breaking")
        assert hasattr(diff, "source_breaks")
        assert hasattr(diff, "compatible")
        assert hasattr(diff, "risk")

    def test_breaking_property_returns_list(self) -> None:
        c = Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "removed")
        diff = _make_diff(changes=[c], verdict=Verdict.BREAKING)
        assert isinstance(diff.breaking, list)
        assert len(diff.breaking) > 0

    def test_compatible_property_returns_list(self) -> None:
        c = Change(ChangeKind.FUNC_ADDED, "_Z3barv", "added")
        diff = _make_diff(changes=[c], verdict=Verdict.COMPATIBLE)
        assert isinstance(diff.compatible, list)
        assert len(diff.compatible) > 0

    def test_changes_contain_expected_fields(self) -> None:
        c = Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "removed")
        diff = _make_diff(changes=[c])
        change = diff.changes[0]
        assert hasattr(change, "kind")
        assert hasattr(change, "symbol")
        assert hasattr(change, "description")
        assert change.kind == ChangeKind.FUNC_REMOVED
        assert change.symbol == "_Z3foov"

    def test_compare_returns_diffresult(self) -> None:
        old = _snap("1.0", funcs=[_fn("foo", "_Z3foov")])
        new = _snap("2.0", funcs=[_fn("foo", "_Z3foov")])
        result = compare(old, new)
        assert isinstance(result, DiffResult)
        assert result.verdict == Verdict.NO_CHANGE


# ===========================================================================
# 7. ChangeKind enum completeness
# ===========================================================================


class TestChangeKindCompleteness:
    """Verify all ChangeKind values have policy and impact entries."""

    def test_all_changekind_values_have_policy(self) -> None:
        """policy_for must not raise for any ChangeKind value."""
        for kind in ChangeKind:
            entry = policy_for(kind)
            assert entry is not None, f"policy_for({kind}) returned None"
            assert entry.default_verdict is not None

    def test_all_changekind_values_in_policy_registry(self) -> None:
        """Every ChangeKind should be explicitly categorized in the registry."""
        for kind in ChangeKind:
            # policy_for always returns a fallback, but we want explicit coverage
            # in the actual registry for every kind
            assert kind in POLICY_REGISTRY, (
                f"ChangeKind.{kind.name} is not in POLICY_REGISTRY"
            )

    def test_impact_for_does_not_raise(self) -> None:
        """impact_for must not raise for any ChangeKind value."""
        for kind in ChangeKind:
            # Should return a string (possibly empty), never raise
            result = impact_for(kind)
            assert isinstance(result, str)
