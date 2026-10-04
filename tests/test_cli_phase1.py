from __future__ import annotations

from click.testing import CliRunner

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

    monkeypatch.setattr("abicheck.dumper.dump", lambda **_: _snap("2.0"))

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


# test_dump_cmd_non_elf_input_clean_error: moved to test_cli_new_features.py::TestDumpClickException


def test_dump_cmd_missing_file_clean_error(tmp_path):
    """abicheck dump on missing path must print clean error and exit non-zero."""
    runner = CliRunner()
    result = runner.invoke(main, ["dump", str(tmp_path / "no_such_file.so")])
    # Click itself validates path existence and should give a clean error
    assert result.exit_code != 0
    assert "Traceback" not in result.output
