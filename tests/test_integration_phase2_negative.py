"""Phase 2 integration hardening tests.

Focuses on negative end-to-end scenarios requested in the coverage plan:
- bad ELF input
- missing exported symbols across versions
- broken header parsing via castxml
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(sys.platform != "linux", reason="ELF/castxml tests require Linux"),
]


def _run_abicheck(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "abicheck.cli", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _require_tool(name: str) -> None:
    if shutil.which(name) is None:
        pytest.skip(f"{name} not found in PATH")


def _compile_so(tmp: Path, name: str, source: str) -> Path:
    so = tmp / name
    res = subprocess.run(
        ["gcc", "-shared", "-fPIC", "-x", "c", "-", "-o", str(so)],
        input=source,
        text=True,
        capture_output=True,
        check=False,
    )
    if res.returncode != 0:
        pytest.skip(f"gcc failed: {res.stderr[:200]}")
    return so


def test_dump_fails_on_non_elf_input(tmp_path: Path) -> None:
    bad = tmp_path / "not_elf.so"
    bad.write_text("not an elf\n", encoding="utf-8")

    out = _run_abicheck(["dump", str(bad)])

    assert out.returncode != 0
    combined = out.stderr + out.stdout
    # Error message varies: old ELF-only builds say "Failed to parse ELF file",
    # cross-platform builds say "Unrecognised binary format" or similar, and
    # `dump`'s real ELF run (since CLI cleanup phase two, PR C) now shares
    # `service.resolve_input`'s canonical "Cannot detect format" wording with
    # `compare`/`scan` instead of `dumper.dump`'s own bespoke ELF-only message.
    assert any(
        msg in combined
        for msg in (
            "Failed to parse ELF file",
            "Unrecognised binary format",
            "unknown format",
            "not a valid",
            "Cannot detect format",
        )
    ), f"Unexpected error output: {combined!r}"


def test_compare_detects_missing_exported_symbol_end_to_end(tmp_path: Path) -> None:
    _require_tool("gcc")
    _require_tool("castxml")

    header = tmp_path / "api.h"
    header.write_text("int api_fn(void);\n", encoding="utf-8")

    old_so = _compile_so(tmp_path, "libold.so", "int api_fn(void) { return 1; }\n")
    new_so = _compile_so(tmp_path, "libnew.so", "int other_fn(void) { return 2; }\n")

    old_snap = tmp_path / "old.json"
    new_snap = tmp_path / "new.json"

    d1 = _run_abicheck(["dump", str(old_so), "-H", str(header), "-o", str(old_snap)])
    assert d1.returncode == 0, d1.stderr

    d2 = _run_abicheck(["dump", str(new_so), "-H", str(header), "-o", str(new_snap)])
    assert d2.returncode == 0, d2.stderr

    cmp_res = _run_abicheck(["compare", str(old_snap), str(new_snap), "--format", "markdown"])

    assert cmp_res.returncode == 4
    # api_fn present in header but absent from new .dynsym → FUNC_VISIBILITY_CHANGED
    assert "BREAKING" in cmp_res.stdout
    assert "api_fn" in cmp_res.stdout


def test_dump_fails_on_broken_header(tmp_path: Path) -> None:
    _require_tool("gcc")
    _require_tool("castxml")

    so = _compile_so(tmp_path, "libok.so", "int api_fn(void) { return 0; }\n")
    broken_header = tmp_path / "broken.h"
    broken_header.write_text("int api_fn(\n", encoding="utf-8")

    out = _run_abicheck(["dump", str(so), "-H", str(broken_header)])

    assert out.returncode != 0
    assert "castxml failed" in (out.stderr + out.stdout).lower()
