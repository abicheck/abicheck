"""Tests for scripts/test_redundancy.py: which tests add nothing to a run.

The classification is only as strong as its two guarantees, and each is
pinned here against coverage data built by hand (contexts set explicitly, so
no pytest run is involved):

* a test is called redundant only when every line it covers is covered by
  another test, and *mutant*-redundant only when mutation data says so;
  without a kill map no test is ever ``fully-redundant``;
* two identical tests are both reported, each naming the other, so neither
  survives the report unexamined.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
from coverage import CoverageData

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

_SRC = "/src/abicheck/mod.py"
_OTHER_SRC = "/src/abicheck/other.py"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    # Registered first: @dataclass looks its module up in sys.modules.
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


tr = _load("test_redundancy")


def _coverage(tmp_path: Path, tests: dict[str, dict[str, list[int]]]) -> Path:
    """Write a coverage file whose context per test is exactly the lines given.

    *tests* maps a test id to ``{source file: [line numbers]}``, which is the
    shape ``pytest --cov-context=test`` would have produced for that test.
    """
    path = tmp_path / ".coverage"
    data = CoverageData(basename=str(path))
    for test_id, files in tests.items():
        data.set_context(f"{test_id}|run")
        data.add_lines({src: lines for src, lines in files.items()})
    data.write()
    return path


def _by_id(results: list) -> dict[str, object]:
    return {r.unit: r for r in results}


def _run(path: Path, **kwargs) -> dict:
    lines, universe = tr.load_coverage(path)
    return tr.build_report(
        path, kwargs.get("scope", []), lines, universe, kwargs.get("kills")
    )


def _units(report: dict, level: str = "case") -> dict[str, dict]:
    return {u["id"]: u for u in report["levels"][level]["units"]}


A = "tests/test_mod.py::test_a"
B = "tests/test_mod.py::test_b"
C = "tests/test_mod.py::test_c"


def test_test_with_a_line_no_one_else_covers_is_unique(tmp_path: Path) -> None:
    path = _coverage(tmp_path, {A: {_SRC: [1, 2, 3]}, B: {_SRC: [1, 2]}})
    units = _units(_run(path))

    assert units[A]["classification"] == "unique"
    assert units[A]["unique_lines"] == 1
    assert units[B]["classification"] == "coverage-redundant"
    assert units[B]["unique_lines"] == 0


def test_subset_tests_are_redundant_and_name_the_test_that_covers_them(
    tmp_path: Path,
) -> None:
    # A covers everything B and C cover, plus line 4 that only A covers.
    path = _coverage(
        tmp_path,
        {A: {_SRC: [1, 2, 3, 4]}, B: {_SRC: [1, 2]}, C: {_SRC: [2, 3]}},
    )
    units = _units(_run(path))

    assert units[A]["classification"] == "unique"
    assert units[B]["subsumed_by"] == [A]
    assert units[C]["subsumed_by"] == [A]
    assert units[B]["redundant_only_by_union"] is False


def test_identical_tests_both_reported_each_naming_the_other(tmp_path: Path) -> None:
    path = _coverage(tmp_path, {A: {_SRC: [1, 2]}, B: {_SRC: [1, 2]}})
    units = _units(_run(path))

    assert units[A]["classification"] == "coverage-redundant"
    assert units[B]["classification"] == "coverage-redundant"
    assert units[A]["subsumed_by"] == [B]
    assert units[B]["subsumed_by"] == [A]


def test_redundancy_by_union_of_several_tests_is_named_as_such(tmp_path: Path) -> None:
    # C's lines {1, 2} are covered by A ({1}) and B ({2}) together, but by
    # neither alone, so no single test subsumes it.
    path = _coverage(
        tmp_path,
        {A: {_SRC: [1]}, B: {_SRC: [2]}, C: {_SRC: [1, 2]}},
    )
    units = _units(_run(path))

    assert units[C]["classification"] == "coverage-redundant"
    assert units[C]["subsumed_by"] == []
    assert units[C]["redundant_only_by_union"] is True


def test_without_mutation_data_no_test_is_ever_fully_redundant(tmp_path: Path) -> None:
    path = _coverage(tmp_path, {A: {_SRC: [1, 2]}, B: {_SRC: [1, 2]}})
    report = _run(path)

    classes = {u["classification"] for u in report["levels"]["case"]["units"]}
    assert "fully-redundant" not in classes
    assert classes == {"coverage-redundant"}
    assert all(u["kills"] is None for u in report["levels"]["case"]["units"])
    assert report["mutation"]["available"] is False
    assert "unknown" in report["mutation"]["limitation"]


def test_kill_map_separates_a_test_with_a_unique_kill_from_a_fully_redundant_one(
    tmp_path: Path,
) -> None:
    path = _coverage(tmp_path, {A: {_SRC: [1, 2]}, B: {_SRC: [1, 2]}})
    kills = {
        "abicheck.mod.x_f__mutmut_1": {A, B},
        "abicheck.mod.x_f__mutmut_2": {A},
    }
    units = _units(_run(path, kills=kills))

    # A is coverage-redundant but kills mutant 2 that no one else kills.
    assert units[A]["classification"] == "unique"
    assert units[A]["unique_kills"] == 1
    # B shares every kill with A and covers nothing A does not.
    assert units[B]["classification"] == "fully-redundant"
    assert units[B]["kills"] == 1
    assert units[B]["unique_kills"] == 0


def test_parametrized_cases_are_judged_both_per_case_and_per_function(
    tmp_path: Path,
) -> None:
    # Two cases of one function that cover exactly the same lines.
    case_1 = "tests/test_mod.py::test_p[1]"
    case_2 = "tests/test_mod.py::test_p[2]"
    path = _coverage(tmp_path, {case_1: {_SRC: [1, 2]}, case_2: {_SRC: [1, 2]}})
    report = _run(path)

    cases = _units(report, "case")
    assert cases[case_1]["classification"] == "coverage-redundant"
    assert cases[case_1]["subsumed_by"] == [case_2]
    assert cases[case_2]["subsumed_by"] == [case_1]

    # As one function, the pair is the only thing covering those lines.
    functions = _units(report, "function")
    assert list(functions) == ["tests/test_mod.py::test_p"]
    assert functions["tests/test_mod.py::test_p"]["classification"] == "unique"


def test_scope_limits_the_report_but_not_the_comparison(tmp_path: Path) -> None:
    kept = "tests/test_keep.py::test_x"
    other = "tests/test_other.py::test_y"
    path = _coverage(tmp_path, {kept: {_SRC: [1, 2]}, other: {_SRC: [1, 2]}})
    units = _units(_run(path, scope=["tests/test_keep.py"]))

    assert list(units) == [kept]
    # The test it is compared against lives outside the scope, and still counts.
    assert units[kept]["subsumed_by"] == [other]


def test_a_unit_with_no_covered_line_is_unknown_not_redundant() -> None:
    lines = {(_SRC, 1): {A}}
    results = tr.analyse(lines, {A, "tests/test_mod.py::test_empty"}, lambda t: t, None)
    by_id = _by_id(results)

    assert by_id["tests/test_mod.py::test_empty"].classification == "unknown"
    assert by_id[A].classification == "unique"


@pytest.mark.parametrize(
    ("context", "expected"),
    [
        ("tests/test_mod.py::test_a|run", "tests/test_mod.py::test_a"),
        ("tests/test_mod.py::test_a|setup", "tests/test_mod.py::test_a"),
        ("tests/test_mod.py::test_a[1-2]|teardown", "tests/test_mod.py::test_a[1-2]"),
        ("", None),
        ("|run", None),
        ("tests/test_mod.py::test_a|other", None),
        ("hand-set-context", None),
    ],
)
def test_context_names_map_to_the_test_that_ran_them(
    context: str, expected: str | None
) -> None:
    assert tr.context_test_id(context) == expected


def test_function_aggregation_strips_parameters() -> None:
    assert (
        tr.function_of("tests/test_mod.py::TestC::test_a[x|y]")
        == "tests/test_mod.py::TestC::test_a"
    )
    assert tr.file_of("tests/test_mod.py::TestC::test_a") == "tests/test_mod.py"


def test_kill_map_with_an_unattributable_key_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "kills.json"
    path.write_text(json.dumps({"not_a_mutant": [A]}), encoding="utf-8")

    with pytest.raises(ValueError, match="do not attribute to a module"):
        tr.load_kills(path)


def test_kill_map_is_loaded_when_every_key_attributes(tmp_path: Path) -> None:
    path = tmp_path / "kills.json"
    path.write_text(
        json.dumps({"abicheck.mod.x_f__mutmut_1": [A, B]}), encoding="utf-8"
    )

    assert tr.load_kills(path) == {"abicheck.mod.x_f__mutmut_1": {A, B}}


def test_missing_coverage_data_is_an_error_not_an_empty_report(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="--cov-context=test"):
        tr.load_coverage(tmp_path / "absent")


def test_main_writes_json_and_markdown_and_exits_zero(tmp_path: Path) -> None:
    path = _coverage(tmp_path, {A: {_SRC: [1, 2, 3]}, B: {_SRC: [1, 2]}})
    out_json = tmp_path / "report.json"
    out_md = tmp_path / "report.md"

    code = tr.main(
        [
            "--coverage-file",
            str(path),
            "--json",
            str(out_json),
            "--markdown",
            str(out_md),
        ]
    )

    assert code == 0
    report = json.loads(out_json.read_text(encoding="utf-8"))
    assert report["schema"] == tr.SCHEMA_VERSION
    assert report["levels"]["case"]["summary"]["coverage-redundant"] == 1
    markdown = out_md.read_text(encoding="utf-8")
    assert "Test redundancy report" in markdown
    assert f"`{B}`" in markdown


def test_main_reports_a_missing_coverage_file_as_failure(tmp_path: Path) -> None:
    assert tr.main(["--coverage-file", str(tmp_path / "absent")]) == 1


def test_a_coverage_run_without_test_contexts_is_an_error_not_an_empty_report(
    tmp_path: Path,
) -> None:
    # A plain `pytest --cov` run records lines under the empty context only.
    path = tmp_path / ".coverage"
    data = CoverageData(basename=str(path))
    data.set_context("")
    data.add_lines({_SRC: [1, 2]})
    data.write()

    with pytest.raises(ValueError, match="no per-test contexts"):
        tr.load_coverage(path)


def test_a_pipe_in_a_test_id_cannot_split_the_markdown_row() -> None:
    unit = {
        "id": "tests/test_mod.py::test_a[x|y]",
        "classification": "coverage-redundant",
        "covered_lines": 2,
        "unique_lines": 0,
        "kills": None,
        "unique_kills": None,
        "subsumed_by": ["tests/test_mod.py::test_b[x|y]"],
        "redundant_only_by_union": False,
    }
    row = tr._row(unit)

    assert "`tests/test_mod.py::test_a[x\\|y]`" in row
    assert "`tests/test_mod.py::test_b[x\\|y]`" in row
    # Six cells have five separators; the pipes inside the ids add none.
    assert row.count(" | ") == 5
