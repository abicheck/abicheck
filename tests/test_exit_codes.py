"""Contract tests for abicheck compare CLI exit codes.

Exit code contract:
  0  — NO_CHANGE or COMPATIBLE (no binary break)
  1  — COMPATIBLE (reserved; currently compare exits 0 for compatible)
  2  — API_BREAK (source-level break, binary compatible)
  4  — BREAKING (binary ABI break)

Error / usage:
  Click raises SystemExit(2) for missing arguments / bad options.
  Other tool errors (e.g. bad JSON) raise SystemExit(1) via Click.

These tests use the CLI runner (not subprocess) for speed and isolation.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.model import (
    AbiSnapshot,
    EnumMember,
    EnumType,
    Function,
    RecordType,
    TypeField,
    Visibility,
)
from abicheck.serialization import snapshot_to_json

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _snap(ver: str, funcs=None, types=None, enums=None) -> AbiSnapshot:
    s = AbiSnapshot(library="libtest.so", version=ver)
    s.functions = funcs or []
    s.types = types or []
    s.enums = enums or []
    return s


def _fn(name: str, mangled: str) -> Function:
    return Function(
        name=name, mangled=mangled, return_type="int", visibility=Visibility.PUBLIC
    )


def _write_snap(path: Path, snap: AbiSnapshot) -> None:
    path.write_text(snapshot_to_json(snap), encoding="utf-8")


# ---------------------------------------------------------------------------
# Exit code 0 — NO_CHANGE
# ---------------------------------------------------------------------------


def test_exit_0_no_change(tmp_path: Path) -> None:
    """Identical snapshots → exit 0 (NO_CHANGE)."""
    runner = CliRunner()
    old_path = tmp_path / "old.json"
    new_path = tmp_path / "new.json"
    snap = _snap("1.0", funcs=[_fn("compute", "_Z7computei")])
    _write_snap(old_path, snap)
    _write_snap(new_path, snap)

    result = runner.invoke(main, ["compare", str(old_path), str(new_path)])
    assert result.exit_code == 0, f"Expected 0, got {result.exit_code}\n{result.output}"


def test_exit_0_compatible(tmp_path: Path) -> None:
    """Compatible addition (new function) → exit 0 (COMPATIBLE)."""
    runner = CliRunner()
    old_path = tmp_path / "old.json"
    new_path = tmp_path / "new.json"
    _write_snap(old_path, _snap("1.0", funcs=[_fn("compute", "_Z7computei")]))
    _write_snap(
        new_path,
        _snap(
            "2.0",
            funcs=[
                _fn("compute", "_Z7computei"),
                _fn("helper", "_Z6helperi"),
            ],
        ),
    )

    result = runner.invoke(main, ["compare", str(old_path), str(new_path)])
    assert result.exit_code == 0, f"Expected 0, got {result.exit_code}\n{result.output}"
    assert "COMPATIBLE" in result.output


def test_exit_0_compatible_with_risk(tmp_path: Path) -> None:
    """SYMBOL_VERSION_REQUIRED_ADDED → COMPATIBLE_WITH_RISK → exit 0.

    New GLIBC version requirement is binary-compatible for existing consumers
    (already linked at build time). Deployment risk only — exits 0.
    Previously (before PR #121) this would have exited 4 (BREAKING).
    """
    from abicheck.elf_metadata import ElfMetadata

    runner = CliRunner()
    old_path = tmp_path / "old.json"
    new_path = tmp_path / "new.json"

    old = _snap("1.0")
    old.elf = ElfMetadata(versions_required={"libc.so.6": ["GLIBC_2.5"]})
    new = _snap("2.0")
    new.elf = ElfMetadata(versions_required={"libc.so.6": ["GLIBC_2.5", "GLIBC_2.34"]})
    _write_snap(old_path, old)
    _write_snap(new_path, new)

    result = runner.invoke(main, ["compare", str(old_path), str(new_path)])
    assert result.exit_code == 0, (
        f"Expected exit 0 (COMPATIBLE_WITH_RISK), got {result.exit_code}\n{result.output}"
    )
    assert "COMPATIBLE_WITH_RISK" in result.output


# ---------------------------------------------------------------------------
# Exit code 2 — API_BREAK
# ---------------------------------------------------------------------------


def test_exit_2_api_break_enum_renamed(tmp_path: Path) -> None:
    """Enum member renamed → API_BREAK → exit 2."""
    from abicheck.checker import ChangeKind
    from abicheck.checker_policy import API_BREAK_KINDS

    # Verify enum_member_renamed is an API_BREAK in policy
    assert ChangeKind.ENUM_MEMBER_RENAMED in API_BREAK_KINDS

    runner = CliRunner()
    old_path = tmp_path / "old.json"
    new_path = tmp_path / "new.json"

    old = _snap(
        "1.0",
        enums=[
            EnumType(
                name="Status",
                members=[EnumMember("OK", 0), EnumMember("FAIL", 1)],
            )
        ],
    )
    new = _snap(
        "2.0",
        enums=[
            EnumType(
                name="Status",
                members=[
                    EnumMember("OK", 0),
                    EnumMember("ERROR", 1),
                ],  # renamed FAIL→ERROR
            )
        ],
    )
    _write_snap(old_path, old)
    _write_snap(new_path, new)

    result = runner.invoke(main, ["compare", str(old_path), str(new_path)])
    assert result.exit_code == 2, (
        f"Expected exit 2 (API_BREAK), got {result.exit_code}\n{result.output}"
    )


# ---------------------------------------------------------------------------
# Exit code 4 — BREAKING
# ---------------------------------------------------------------------------


def test_exit_4_func_removed(tmp_path: Path) -> None:
    """Public function removed → BREAKING → exit 4."""
    runner = CliRunner()
    old_path = tmp_path / "old.json"
    new_path = tmp_path / "new.json"
    _write_snap(
        old_path,
        _snap(
            "1.0", funcs=[_fn("compute", "_Z7computei"), _fn("helper", "_Z6helperi")]
        ),
    )
    _write_snap(new_path, _snap("2.0", funcs=[_fn("compute", "_Z7computei")]))

    result = runner.invoke(main, ["compare", str(old_path), str(new_path)])
    assert result.exit_code == 4, (
        f"Expected exit 4 (BREAKING), got {result.exit_code}\n{result.output}"
    )
    assert "BREAKING" in result.output


def test_exit_4_struct_size_changed(tmp_path: Path) -> None:
    """Struct size change → BREAKING → exit 4."""
    runner = CliRunner()
    old_path = tmp_path / "old.json"
    new_path = tmp_path / "new.json"

    old = _snap(
        "1.0",
        types=[
            RecordType(
                name="Buf",
                kind="struct",
                size_bits=64,
                fields=[TypeField("x", "int", 0), TypeField("y", "int", 32)],
            )
        ],
    )
    new = _snap(
        "2.0",
        types=[
            RecordType(
                name="Buf",
                kind="struct",
                size_bits=96,
                fields=[
                    TypeField("x", "int", 0),
                    TypeField("y", "int", 32),
                    TypeField("z", "int", 64),
                ],
            )
        ],
    )
    _write_snap(old_path, old)
    _write_snap(new_path, new)

    result = runner.invoke(main, ["compare", str(old_path), str(new_path)])
    assert result.exit_code == 4, (
        f"Expected exit 4 (BREAKING), got {result.exit_code}\n{result.output}"
    )


# ---------------------------------------------------------------------------
# Exit code on error / bad usage
# ---------------------------------------------------------------------------


def test_exit_on_missing_argument() -> None:
    """Missing argument → Click usage error → non-zero exit."""
    runner = CliRunner()
    result = runner.invoke(main, ["compare"])
    assert result.exit_code != 0, "Expected non-zero exit for missing arguments"


def test_exit_on_invalid_json(tmp_path: Path) -> None:
    """Invalid JSON snapshot → tool error → non-zero exit."""
    runner = CliRunner()
    bad_path = tmp_path / "bad.json"
    bad_path.write_text("this is not json", encoding="utf-8")
    good_path = tmp_path / "good.json"
    _write_snap(good_path, _snap("1.0"))

    result = runner.invoke(main, ["compare", str(bad_path), str(good_path)])
    assert result.exit_code != 0, (
        f"Expected non-zero exit for invalid JSON, got {result.exit_code}\n{result.output}"
    )


def test_exit_on_nonexistent_file(tmp_path: Path) -> None:
    """Non-existent snapshot file → Click reports error."""
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "compare",
            str(tmp_path / "missing.json"),
            str(tmp_path / "also_missing.json"),
        ],
    )
    assert result.exit_code != 0, (
        f"Expected non-zero exit for missing file, got {result.exit_code}"
    )


# ---------------------------------------------------------------------------
# Verdict appears in output (smoke check for JSON format)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fmt", ["json", "markdown"])
def test_verdict_in_output_formats(tmp_path: Path, fmt: str) -> None:
    """Verdict string present in both json and markdown output formats."""
    runner = CliRunner()
    old_path = tmp_path / "old.json"
    new_path = tmp_path / "new.json"
    _write_snap(
        old_path,
        _snap(
            "1.0", funcs=[_fn("compute", "_Z7computei"), _fn("helper", "_Z6helperi")]
        ),
    )
    _write_snap(new_path, _snap("2.0", funcs=[_fn("compute", "_Z7computei")]))

    result = runner.invoke(
        main, ["compare", str(old_path), str(new_path), "-o", f"{fmt}=-"]
    )
    assert result.exit_code == 4
    assert "BREAKING" in result.output
