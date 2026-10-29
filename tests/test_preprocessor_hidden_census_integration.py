"""Exports hidden by *any* ``#ifdef`` are counted per guard macro, end to end
(field report H5, generalized beyond ``sycl::``).

Sources: ``field_shapes/ifdef_generic_guard`` (v1 exports ``foo_one`` and
``foo_two`` under ``#ifdef FOO`` and ``on_queue`` under
``#ifdef SYCL_LANGUAGE_VERSION``; v2 drops ``foo_two``).

Oracle: the fixture's own per-guard list of hidden functions, cross-checked
against ``nm -D`` -- never the scanner's helpers. The branch scan is persisted
(schema v67), so a compare of stored snapshots attributes by guard exactly as a
live compare does; a pre-v67 stored snapshot carries no scan and falls back to
the ``sycl::`` name rule.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _field_shapes import materialize, shape_dir
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.model.undeclared_export_census import (
    GUARD_WARNING_MARKER,
    UNDECLARED_EXPORT_WARNING_MARKER,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not (shutil.which("castxml") and shutil.which("g++") and shutil.which("nm")),
        reason="needs castxml, g++ and nm",
    ),
    # The census reads the ELF export table, and the oracle is ``nm -D``:
    # g++ emits Mach-O/PE elsewhere, where ``nm -D`` fails or lists nothing.
    pytest.mark.skipif(sys.platform != "linux", reason="ELF-only fixture (nm -D)"),
]

_SHAPE = "ifdef_generic_guard"

#: Per side, the exported functions each guard macro hides (the oracle).
_HIDDEN = {
    "v1": {
        "FOO": {"foo_one(int)", "foo_two(double)"},
        "SYCL_LANGUAGE_VERSION": {"on_queue(sycl::queue&)"},
    },
    "v2": {
        "FOO": {"foo_one(int)"},
        "SYCL_LANGUAGE_VERSION": {"on_queue(sycl::queue&)"},
    },
}
_VISIBLE = {"base(int)"}


def _build(tmp: Path, side: str) -> Path:
    src = materialize(_SHAPE, side, tmp / side)
    lib = src / "libapi.so"
    subprocess.run(
        ["g++", "-shared", "-fPIC", "-o", str(lib), str(src / "api.cpp")],
        check=True,
    )
    return lib


def _function_exports(lib: Path) -> set[str]:
    out = subprocess.run(
        ["nm", "-D", "-C", "--defined-only", str(lib)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return {line.split(" ", 2)[2] for line in out.splitlines() if " T " in line}


def _headers(side_label: str, side: str) -> list[str]:
    return ["-H", f"{side_label}={shape_dir(_SHAPE, side) / 'api.h'}"]


def _compare(old: Path, new: Path, tmp: Path, *extra: str) -> dict:
    report = tmp / "report.json"
    result = CliRunner().invoke(
        main,
        ["compare", str(old), str(new), *extra, "-o", f"json={report}"],
        catch_exceptions=False,
    )
    assert result.exit_code in (0, 4), result.output
    return json.loads(report.read_text(encoding="utf-8"))


def _guard_counts(report: dict) -> dict[tuple[str, str], int]:
    """``{(side, macro): count}`` read back from the coverage lines."""
    counts = {}
    for line in report["coverage_warnings"]:
        if GUARD_WARNING_MARKER not in line:
            continue
        side = line.split(" ", 1)[0]
        count = int(line.split(" side headers: ", 1)[1].split(" ", 1)[0])
        macro = line.split(GUARD_WARNING_MARKER + " ", 1)[1].split(" ", 1)[0]
        counts[(side, macro)] = count
    return counts


@pytest.fixture(scope="module")
def libs(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    tmp = tmp_path_factory.mktemp("ifdef_guard")
    built = {side: _build(tmp, side) for side in ("v1", "v2")}
    for side, lib in built.items():
        # Oracle cross-check: every hidden and visible function is exported.
        exports = _function_exports(lib)
        hidden = set().union(*_HIDDEN[side].values())
        assert hidden | _VISIBLE <= exports, (side, sorted(exports))
    return built


def _live_args() -> list[str]:
    return [*_headers("old", "v1"), *_headers("new", "v2")]


def test_live_compare_counts_hidden_exports_per_guard(
    libs: dict[str, Path], tmp_path: Path
) -> None:
    report = _compare(libs["v1"], libs["v2"], tmp_path, *_live_args())
    expected = {
        (label, macro): len(names)
        for label, side in (("old", "v1"), ("new", "v2"))
        for macro, names in _HIDDEN[side].items()
    }
    assert _guard_counts(report) == expected
    # Guard attribution replaces the sycl:: name rule on a live side.
    assert not any(
        UNDECLARED_EXPORT_WARNING_MARKER in w for w in report["coverage_warnings"]
    )
    block = report["undeclared_export_census"]
    for label, side in (("old", "v1"), ("new", "v2")):
        rows = {r["macro"]: r for r in block[label]["by_guard"]}
        assert {m: r["count"] for m, r in rows.items()} == {
            m: len(n) for m, n in _HIDDEN[side].items()
        }
        for macro, row in rows.items():
            assert set(row["examples"]) == _HIDDEN[side][macro]
        assert block[label]["attributed"] == block[label]["undeclared_total"]
    # Informational only: the census adds no finding of its own.
    kinds = {c["kind"] for c in report["changes"]}
    assert kinds <= {"exported_not_public", "func_removed_elf_only"}


def test_defining_the_guard_clears_its_count_and_reports_the_removal(
    libs: dict[str, Path], tmp_path: Path
) -> None:
    report = _compare(libs["v1"], libs["v2"], tmp_path, *_live_args(), "-D", "FOO")
    counts = _guard_counts(report)
    assert not any(macro == "FOO" for _, macro in counts)
    assert counts == {
        ("old", "SYCL_LANGUAGE_VERSION"): 1,
        ("new", "SYCL_LANGUAGE_VERSION"): 1,
    }
    removed = {c["symbol"] for c in report["changes"] if c["kind"] == "func_removed"}
    assert removed == {"_Z7foo_twod"}
    assert report["verdict"] == "BREAKING"


_SCAN_KEY = "live_preprocessor_hidden"


def _find_scan(node: object) -> list[object]:
    """Every value stored under the scan key, wherever the document's section
    layout places it (a sectioned document nests keys one level down)."""
    found: list[object] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == _SCAN_KEY:
                found.append(value)
            found.extend(_find_scan(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_find_scan(item))
    return found


def _strip_scan(node: object) -> None:
    if isinstance(node, dict):
        node.pop(_SCAN_KEY, None)
        for value in node.values():
            _strip_scan(value)
    elif isinstance(node, list):
        for item in node:
            _strip_scan(item)


def _dump_sides(libs: dict[str, Path], tmp_path: Path) -> dict[str, Path]:
    snaps: dict[str, Path] = {}
    for side in ("v1", "v2"):
        snaps[side] = tmp_path / f"{side}.json"
        result = CliRunner().invoke(
            main,
            [
                "dump",
                str(libs[side]),
                "-H",
                str(shape_dir(_SHAPE, side) / "api.h"),
                "-o",
                str(snaps[side]),
            ],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
    return snaps


def test_stored_snapshots_attribute_hidden_exports_like_live(
    libs: dict[str, Path], tmp_path: Path
) -> None:
    # Schema v67: the scan is stored with the snapshot, so the stored census is
    # the live census -- same guard attribution, same verdict, same findings.
    snaps = _dump_sides(libs, tmp_path)
    for label, side in snaps.items():
        stored_scan = _find_scan(json.loads(side.read_text("utf-8")))
        # The scan names functions by base name; _HIDDEN holds their signatures.
        expected = {
            macro: sorted(sig.split("(", 1)[0] for sig in sigs)
            for macro, sigs in _HIDDEN[label].items()
        }
        assert stored_scan == [expected]
    live = _compare(libs["v1"], libs["v2"], tmp_path / "live", *_live_args())
    stored = _compare(snaps["v1"], snaps["v2"], tmp_path / "stored")
    assert _guard_counts(stored) == _guard_counts(live) != {}
    assert stored["undeclared_export_census"] == live["undeclared_export_census"]
    assert stored["verdict"] == live["verdict"]
    assert sorted((c["kind"], c["symbol"]) for c in stored["changes"]) == sorted(
        (c["kind"], c["symbol"]) for c in live["changes"]
    )
    assert stored["confidence"] == live["confidence"]


def test_pre_v67_stored_snapshots_fall_back_to_the_sycl_name_rule(
    libs: dict[str, Path], tmp_path: Path
) -> None:
    # A pre-v67 document carries no scan (the key is absent), so the census
    # cannot attribute by guard and must fall back to the named cause.
    snaps = _dump_sides(libs, tmp_path)
    for side in snaps.values():
        doc = json.loads(side.read_text("utf-8"))
        _strip_scan(doc)
        assert not _find_scan(doc)
        side.write_text(json.dumps(doc), encoding="utf-8")
    report = _compare(snaps["v1"], snaps["v2"], tmp_path)
    assert _guard_counts(report) == {}
    fallback = [
        w for w in report["coverage_warnings"] if UNDECLARED_EXPORT_WARNING_MARKER in w
    ]
    assert [w.split(" ", 1)[0] for w in fallback] == ["old", "new"]
    assert all("(1 reference sycl::" in w for w in fallback)
    block = report["undeclared_export_census"]
    assert block["old"]["by_cause"] == [{"cause": "sycl", "count": 1}]
    assert block["old"]["by_guard"] == []
    # Informational only: the census adds no finding of its own.
    kinds = {c["kind"] for c in report["changes"]}
    assert kinds <= {"exported_not_public", "func_removed_elf_only"}
