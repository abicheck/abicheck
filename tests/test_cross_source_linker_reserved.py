"""Linker-reserved ELF symbols never yield a cross-source finding.

Bug class: an export-table-walking cross-source check reads the raw ELF
export table without the shared linker-artifact predicate, so
``__bss_start``/``_edata``/``_end`` (exported by gold and Bazel's default
link, not by bfd ``ld`` 2.42) surfaced as ``exported_not_public`` RISK findings
on every such build.

General invariant: for every linker-reserved name and every check in
``ALL_CHECKS``, over several independently shaped snapshots, adding that name
to the export table changes *nothing* about the cross-check findings. The
oracle is differential (same snapshot with vs. without the symbol), not the
filter the implementation uses, and the reserved-name list is restated from
the ELF toolchain conventions rather than imported.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from abicheck.buildsource.cross_source_checks import (
    ALL_CHECKS,
    CrosscheckConfig,
    run_crosschecks,
)
from abicheck.buildsource.cross_source_checks_base import (
    _exported_symbol_names,
    _linked_export_symbols,
)
from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.elf_symbol_filter import (
    ELF_LINKER_RESERVED_SYMBOLS,
    is_linker_reserved_symbol,
)
from abicheck.model import AbiSnapshot, Function, RecordType, ScopeOrigin

#: Restated independently: the ELF entry/exit stubs and the section-boundary
#: markers GNU linkers define (``ld --verbose`` / gold's defaults).
_EXPECTED_RESERVED = {"_init", "_fini", "__bss_start", "_edata", "_end"}


def test_reserved_set_covers_the_toolchain_names():
    assert _EXPECTED_RESERVED <= ELF_LINKER_RESERVED_SYMBOLS
    for name in _EXPECTED_RESERVED:
        assert is_linker_reserved_symbol(name)
    # Near-misses are ordinary exports, not reserved.
    for name in ("end", "_end_", "edata", "__bss_start2", "_initialize", "fini"):
        assert not is_linker_reserved_symbol(name)


def _public_fn(name: str, mangled: str) -> Function:
    return Function(
        name=name, mangled=mangled, return_type="int", origin=ScopeOrigin.PUBLIC_HEADER
    )


def _shape_plain(extra: tuple[str, ...]) -> AbiSnapshot:
    snap = AbiSnapshot(
        library="libm.so",
        version="1",
        from_headers=True,
        elf=ElfMetadata(symbols=[ElfSymbol(name=n) for n in ("add", "sub", *extra)]),
    )
    snap.declarations.functions = [_public_fn("add", "add")]  # `sub` stays undocumented
    return snap


def _shape_versioned(extra: tuple[str, ...]) -> AbiSnapshot:
    syms = [ElfSymbol(name="_Z3apiv", version="M_1", visibility="default")]
    syms += [ElfSymbol(name=n, version="", visibility="default") for n in extra]
    snap = AbiSnapshot(
        library="libm.so",
        version="1",
        from_headers=True,
        elf=ElfMetadata(symbols=syms, versions_defined=["M_1"]),
    )
    snap.declarations.functions = [_public_fn("api", "_Z3apiv")]
    return snap


def _shape_private_type(extra: tuple[str, ...]) -> AbiSnapshot:
    names = ("_Z3apiv", "_ZTI8Internal", "_ZTV8Internal", *extra)
    snap = AbiSnapshot(
        library="libm.so",
        version="1",
        from_headers=True,
        elf=ElfMetadata(symbols=[ElfSymbol(name=n) for n in names]),
    )
    snap.declarations.functions = [_public_fn("api", "_Z3apiv")]
    snap.declarations.types = [
        RecordType(name="Internal", kind="class", origin=ScopeOrigin.PRIVATE_HEADER)
    ]
    return snap


_SHAPES = [_shape_plain, _shape_versioned, _shape_private_type]


def _finding_keys(snap: AbiSnapshot, check: str) -> list[tuple[str, str]]:
    res = run_crosschecks(snap, CrosscheckConfig(enabled=frozenset({check})))
    return sorted((c.kind.value, c.symbol) for c in res.findings)


@pytest.mark.parametrize("shape", _SHAPES, ids=lambda f: f.__name__)
@pytest.mark.parametrize("check", ALL_CHECKS)
def test_reserved_symbols_change_no_check_output(shape, check):
    baseline = _finding_keys(shape(()), check)
    disagreements = []
    for name in sorted(_EXPECTED_RESERVED):
        got = _finding_keys(shape((name,)), check)
        if got != baseline:
            disagreements.append((name, got))
    # All at once too: the class, not one member.
    everything = _finding_keys(shape(tuple(sorted(_EXPECTED_RESERVED))), check)
    if everything != baseline:
        disagreements.append(("<all>", everything))
    assert disagreements == [], (check, baseline, disagreements)


def test_differential_oracle_is_not_vacuous():
    # The plain shape's undocumented `sub` must be reported, so an
    # exported_not_public that reported nothing would not pass the matrix above
    # by accident.
    assert ("exported_not_public", "sub") in _finding_keys(
        _shape_plain(()), "exported_not_public"
    )


def test_export_readers_drop_reserved_names_on_elf_only():
    snap = _shape_plain(tuple(sorted(_EXPECTED_RESERVED)))
    for reader in (_exported_symbol_names, _linked_export_symbols):
        names = reader(snap)
        assert names is not None
        assert {"add", "sub"} <= names
        assert not names & _EXPECTED_RESERVED


def _gold_available() -> bool:
    return all(shutil.which(t) for t in ("gcc", "ld.gold", "clang"))


@pytest.mark.integration
@pytest.mark.skipif(not _gold_available(), reason="needs gcc + ld.gold + clang")
def test_gold_linked_additive_change_reports_no_linker_symbols(
    tmp_path: Path, monkeypatch
):
    """End-to-end repro: a gold-linked lib exports __bss_start/_edata/_end."""
    from click.testing import CliRunner

    from abicheck.cli import main

    old_inc, new_inc = tmp_path / "old_inc", tmp_path / "new_inc"
    old_inc.mkdir()
    new_inc.mkdir()
    (old_inc / "m.h").write_text("int add(int a, int b);\n")
    (new_inc / "m.h").write_text("int add(int a, int b);\nint sub(int a, int b);\n")
    (tmp_path / "old.c").write_text(
        '#include "m.h"\nint add(int a,int b){return a+b;}\n'
    )
    (tmp_path / "new.c").write_text(
        '#include "m.h"\nint add(int a,int b){return a+b;}\nint sub(int a,int b){return a-b;}\n'
    )
    for side, inc in (("old", old_inc), ("new", new_inc)):
        subprocess.run(
            [
                "gcc",
                "-shared",
                "-fPIC",
                "-fuse-ld=gold",
                f"-I{inc}",
                str(tmp_path / f"{side}.c"),
                "-o",
                str(tmp_path / f"{side}.so"),
            ],
            check=True,
        )
    nm = subprocess.run(
        ["nm", "-D", "--defined-only", str(tmp_path / "new.so")],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    if "__bss_start" not in nm:
        pytest.skip("this gold build does not export the section markers")

    monkeypatch.setenv("ABICHECK_AST_FRONTEND", "clang")
    out = tmp_path / "r.json"
    result = CliRunner().invoke(
        main,
        [
            "compare",
            str(tmp_path / "old.so"),
            str(tmp_path / "new.so"),
            "--header",
            f"old={old_inc}",
            "--header",
            f"new={new_inc}",
            "-o",
            f"json={out}",
        ],
    )
    assert result.exit_code == 0, result.output
    import json

    doc = json.loads(out.read_text())
    symbols = {c.get("symbol") for c in doc.get("changes", [])}
    assert "sub" in symbols  # the real change is still seen
    assert not symbols & _EXPECTED_RESERVED
    assert doc["verdict"] == "COMPATIBLE"
