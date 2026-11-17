"""VAR_VALUE_CHANGED from an exported const object's section bytes (G7).

A binary-only snapshot has no header, so ``Variable.value`` is never set and a
const object whose initializer moved reported nothing. The extractor now
records the object's own section bytes (``Variable.initializer_hex``); the
detector compares them when both sides carry them.

The expected bytes come from ``test_var_value_bytes_oracle``, which reads the
ELF with pyelftools and never calls abicheck, so each assertion checks the
detector against bytes the test read itself.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from test_var_value_bytes_oracle import build_shared, exported_object_bytes

from abicheck.checker import compare
from abicheck.dumper import dump
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.model.declarations import Variable

# The section-byte reader is the ELF path. A PE or Mach-O build of the same
# source exports no ``g_k`` from a bare ``-shared`` link, so these tests are
# ELF-only by construction and skip elsewhere.
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not sys.platform.startswith("linux"),
        reason="ELF section-byte reader: these tests build ELF shared objects",
    ),
]


def _snap(tmp_path: Path, name: str, src: str):
    so = build_shared(src, tmp_path / f"{name}.so")
    return dump(so, headers=[])


def _value_kinds(old, new) -> list:
    return [
        c for c in compare(old, new).changes if c.kind is ChangeKind.VAR_VALUE_CHANGED
    ]


def test_changed_const_initializer_reports_value_changed(tmp_path: Path) -> None:
    old = _snap(tmp_path, "old", "const int g_k = 3;\n")
    new = _snap(tmp_path, "new", "const int g_k = 4;\n")
    (change,) = _value_kinds(old, new)
    assert change.symbol == "g_k"
    # The oracle's bytes, read by the test, are exactly what the detector reports.
    oracle_old = exported_object_bytes(tmp_path / "old.so", "g_k")
    oracle_new = exported_object_bytes(tmp_path / "new.so", "g_k")
    assert oracle_old == b"\x03\x00\x00\x00"
    assert oracle_new == b"\x04\x00\x00\x00"
    assert change.old_value == oracle_old.hex()
    assert change.new_value == oracle_new.hex()


def test_same_initializer_reports_nothing(tmp_path: Path) -> None:
    old = _snap(tmp_path, "old", "const int g_k = 3;\n")
    new = _snap(tmp_path, "new", "const int g_k = 3;\n")
    assert _value_kinds(old, new) == []


def test_a_wider_array_is_size_change_not_value_change(tmp_path: Path) -> None:
    # Different lengths: SYMBOL_SIZE_CHANGED owns the finding; the bytes are not
    # compared against a differently-sized object.
    old = _snap(tmp_path, "old", "const int g_t[2] = {1, 2};\n")
    new = _snap(tmp_path, "new", "const int g_t[4] = {1, 2, 3, 4};\n")
    assert _value_kinds(old, new) == []


def test_a_writable_relocated_pointer_is_not_read(tmp_path: Path) -> None:
    # A pointer initializer lives in a writable section with a relocation, so
    # its file bytes are a placeholder. Moving its target in the layout must not
    # read as a value change.
    old = _snap(
        tmp_path,
        "old",
        'const int pad[1] = {0};\nconst char *const g_p = "a";\n'
        "const char *const g_q = g_p;\n",
    )
    new = _snap(
        tmp_path,
        "new",
        'const int pad[3] = {0, 0, 0};\nconst char *const g_p = "a";\n'
        "const char *const g_q = g_p;\n",
    )
    assert _value_kinds(old, new) == []


def test_a_static_const_object_is_not_an_export(tmp_path: Path) -> None:
    old = _snap(
        tmp_path, "old", "static const int s_k = 3;\nint f(void){return s_k;}\n"
    )
    new = _snap(
        tmp_path, "new", "static const int s_k = 4;\nint f(void){return s_k;}\n"
    )
    assert _value_kinds(old, new) == []


def test_absent_bytes_on_one_side_is_unknown_not_changed() -> None:
    old = Variable(name="g_k", mangled="g_k", type="?", initializer_hex="03000000")
    new = Variable(name="g_k", mangled="g_k", type="?", initializer_hex=None)
    from abicheck.diff_types_abicc_parity import _initializer_bytes_changed

    assert _initializer_bytes_changed(old, new) is False
    assert _initializer_bytes_changed(new, old) is False


def test_length_mismatch_is_not_compared() -> None:
    from abicheck.diff_types_abicc_parity import _initializer_bytes_changed

    old = Variable(name="g_t", mangled="g_t", type="?", initializer_hex="01000000")
    new = Variable(
        name="g_t", mangled="g_t", type="?", initializer_hex="0100000002000000"
    )
    assert _initializer_bytes_changed(old, new) is False


def test_versioned_alias_pair_has_no_bytes(tmp_path: Path) -> None:
    # g_k@V1 and g_k@@V2 share the bare name, so the bare name cannot pick one
    # value. The variable keeps no bytes rather than the table-order first.
    src = tmp_path / "alias.c"
    src.write_text(
        "const int g_k_v1 = 3;\n"
        '__asm__(".symver g_k_v1, g_k@V1");\n'
        "const int g_k_v2 = 4;\n"
        '__asm__(".symver g_k_v2, g_k@@V2");\n',
        encoding="utf-8",
    )
    script = tmp_path / "alias.map"
    script.write_text("V1 {};\nV2 {};\n", encoding="utf-8")
    so = tmp_path / "alias.so"
    import subprocess

    subprocess.run(
        [
            "gcc",
            "-O2",
            "-shared",
            "-fPIC",
            f"-Wl,--version-script={script}",
            "-o",
            str(so),
            str(src),
        ],
        check=True,
        capture_output=True,
    )
    snap = dump(so, headers=[])
    (bare,) = [v for v in snap.declarations.variables if v.name == "g_k"]
    assert bare.initializer_hex is None
    # The two distinct aliases are not ambiguous under their own names.
    by_name = {v.name: v.initializer_hex for v in snap.declarations.variables}
    assert by_name["g_k_v1"] == "03000000"
    assert by_name["g_k_v2"] == "04000000"


def test_relocation_coverage_is_by_overlap_not_by_start() -> None:
    from abicheck.extract.elf_data_initializer import _covered_by_relocation

    relocated = [10, 20]
    assert _covered_by_relocation(relocated, 10, 1)  # a relocation at the start
    assert not _covered_by_relocation(relocated, 11, 9)  # ends just before 20
    assert _covered_by_relocation(relocated, 11, 10)  # reaches 20
    assert not _covered_by_relocation([], 0, 8)
