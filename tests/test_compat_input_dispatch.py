# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""``compat``'s ``-old``/``-new`` operand is dispatched on content, never on
the file name alone.

Bug class: a format decision keyed on a suffix that more than one producer
writes. ABICC names its Perl dumps ``ABI.dump``; ``compat dump -dump-path
X.dump`` writes an abicheck JSON snapshot under the same suffix, and the
``.dump`` shortcut sent that snapshot to the Perl importer, so a dump
written by ``compat dump`` could not be read back by ``compat``. The oracle
here is the producer itself: every encoding abicheck writes, under every
name, must load as the snapshot that was written, and an ABICC Perl dump
must reach the Perl importer whatever it is called.
"""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest

from abicheck.compat.abicc_dump_import import is_abicc_perl_dump_file
from abicheck.compat.run_inputs import _load_descriptor_or_dump
from abicheck.model import AbiSnapshot, Function
from abicheck.serialization import write_snapshot

_PERL_DUMP = """$VAR1 = {
  'LibraryName' => 'libperl',
  'LibraryVersion' => '7',
  'TypeInfo' => {'0' => {'Name' => 'void', 'Type' => 'Intrinsic'}},
  'SymbolInfo' => {'1' => {'MnglName' => 'pf', 'ShortName' => 'pf', 'Return' => '0'}}
};
"""


def _available_compressions() -> list[str]:
    out = ["none", "gzip"]
    try:
        import zstandard  # noqa: F401
    except ImportError:
        pass
    else:
        out.append("zstd")
    return out


# Names chosen to cover the suffix that collided (``.dump``), the one the
# loader trusts by name (``.json``), a name that looks like an ABICC
# descriptor (``.xml``), an unrelated suffix and no suffix at all.
_NAMES = ("v1.dump", "ABI.dump", "v1.json", "v1.xml", "v1.bin", "v1")


@pytest.mark.parametrize(
    ("compression", "name"),
    list(itertools.product(_available_compressions(), _NAMES)),
)
def test_every_snapshot_abicheck_writes_loads_under_any_name(
    tmp_path: Path, compression: str, name: str
) -> None:
    snap = AbiSnapshot(library="libfoo.so", version="1.0")
    snap.declarations.functions.append(
        Function(name="foo", mangled="_Z3foov", return_type="int")
    )
    path = tmp_path / name
    write_snapshot(snap, path, compression=compression)

    assert not is_abicc_perl_dump_file(path)
    loaded = _load_descriptor_or_dump(path)
    assert isinstance(loaded, AbiSnapshot)
    assert loaded.library == "libfoo.so"
    assert [f.mangled for f in loaded.declarations.functions] == ["_Z3foov"]


@pytest.mark.parametrize("name", ["ABI.dump", "v1.txt", "v1.xml", "v1"])
def test_a_perl_dump_reaches_the_perl_importer_under_any_name(
    tmp_path: Path, name: str
) -> None:
    path = tmp_path / name
    path.write_text(_PERL_DUMP, encoding="utf-8")

    assert is_abicc_perl_dump_file(path)
    loaded = _load_descriptor_or_dump(path)
    assert isinstance(loaded, AbiSnapshot)
    assert loaded.library == "libperl"
    assert any(f.mangled == "pf" for f in loaded.declarations.functions)


def test_a_descriptor_named_dump_is_still_a_descriptor(tmp_path: Path) -> None:
    lib = tmp_path / "libfoo.so"
    lib.write_bytes(b"\x7fELF")
    path = tmp_path / "v1.dump"
    path.write_text(
        f"<descriptor><version>1.0</version><libs>{lib}</libs></descriptor>",
        encoding="utf-8",
    )

    assert not is_abicc_perl_dump_file(path)
    loaded = _load_descriptor_or_dump(path)
    assert not isinstance(loaded, AbiSnapshot)
    assert loaded.version == "1.0"
