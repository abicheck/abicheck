from __future__ import annotations

from types import SimpleNamespace

from click.testing import CliRunner

from abicheck.checker import Change, ChangeKind, DiffResult, Verdict
from abicheck.cli import main
from abicheck.model import AbiSnapshot


def _snap(version: str = "1.0") -> AbiSnapshot:
    return AbiSnapshot(library="libfoo.so", version=version)


def test_dump_cmd_writes_output_file(tmp_path, monkeypatch):
    so_path = tmp_path / "libfoo.so"
    so_path.write_bytes(b"\x7fELF")
    header = tmp_path / "foo.h"
    header.write_text("int foo();\n", encoding="utf-8")
    out = tmp_path / "snap.json"

    monkeypatch.setattr("abicheck.cli_dump_helpers.dump", lambda **_: _snap("2.0"))

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "dump",
            str(so_path),
            "-H",
            str(header),
            "--version",
            "2.0",
            "-o",
            str(out),
        ],
    )

    assert result.exit_code == 0
    assert "Snapshot written to" in result.output
    assert out.exists()
    assert '"version": "2.0"' in out.read_text(encoding="utf-8")


# test_compare_cmd_warns_when_all_changes_suppressed: moved to test_cli_unit.py
# test_compare_cmd_breaking_exits_with_code_4: moved to test_cli_unit.py


def test_compat_check_cmd_descriptor_parse_error_exits_6(tmp_path, monkeypatch):
    old_desc = tmp_path / "old.xml"
    new_desc = tmp_path / "new.xml"
    old_desc.write_text("<xml/>", encoding="utf-8")
    new_desc.write_text("<xml/>", encoding="utf-8")

    monkeypatch.setattr("abicheck.compat.cli.parse_descriptor", lambda *_, **__: (_ for _ in ()).throw(ValueError("bad")))

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["compat", "check", "-lib", "foo", "-old", str(old_desc), "-new", str(new_desc)],
    )

    assert result.exit_code == 6
    assert "Error parsing descriptor" in result.output


def test_compat_check_cmd_breaking_exits_1_and_writes_report(tmp_path, monkeypatch):
    old_desc = tmp_path / "old.xml"
    new_desc = tmp_path / "new.xml"
    old_desc.write_text("<xml/>", encoding="utf-8")
    new_desc.write_text("<xml/>", encoding="utf-8")

    old_so = tmp_path / "old.so"
    new_so = tmp_path / "new.so"
    old_so.write_bytes(b"\x7fELF")
    new_so.write_bytes(b"\x7fELF")

    old_d = SimpleNamespace(libs=[old_so], headers=[], version="1.0")
    new_d = SimpleNamespace(libs=[new_so], headers=[], version="2.0")

    monkeypatch.setattr("abicheck.compat.cli.parse_descriptor", lambda p, **_kw: old_d if p == old_desc else new_d)
    monkeypatch.setattr("abicheck.compat.cli.dump", lambda *_args, **_kwargs: _snap())
    monkeypatch.setattr(
        "abicheck.compat.cli.compare",
        lambda *_args, **_kwargs: DiffResult(
            old_version="1.0",
            new_version="2.0",
            library="libfoo.so",
            verdict=Verdict.BREAKING,
            changes=[Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "removed")],
        ),
    )

    report = tmp_path / "compat" / "report.json"
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "compat",
            "check",
            "-lib",
            "foo",
            "-old",
            str(old_desc),
            "-new",
            str(new_desc),
            "-report-format",
            "json",
            "-report-path",
            str(report),
        ],
    )

    assert result.exit_code == 1
    assert report.exists()
    assert "BREAKING" in report.read_text(encoding="utf-8")
    assert "Verdict: BREAKING" in result.output


# test_dump_cmd_non_elf_input_clean_error: moved to test_cli_new_features.py::TestDumpClickException


def test_dump_cmd_missing_file_clean_error(tmp_path):
    """abicheck dump on missing path must print clean error and exit non-zero."""
    runner = CliRunner()
    result = runner.invoke(main, ["dump", str(tmp_path / "no_such_file.so")])
    # Click itself validates path existence and should give a clean error
    assert result.exit_code != 0
    assert "Traceback" not in result.output
