"""Scoping a cache-reloaded snapshot equals scoping the fresh extraction.

Bug class ``cache.roundtrip_must_preserve_what_downstream_scoping_reads``: a
cache that serves a snapshot in place of an extraction must preserve every
piece of state a downstream step reads -- including runtime-only carriers the
public codec deliberately drops (the flat maps' declaring-header attribution).
Differential oracle: ``scope(fresh) == scope(store -> reload)``, over several
generated attribution shapes and, under ``integration``, over real dumps.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from abicheck.model import AbiSnapshot
from abicheck.model.declaration_headers import HeaderAttributedMap
from abicheck.workflows.dump.cache import declaring_headers, store
from abicheck.workflows.dump.dependency_scope import (
    scope_snapshot_excluding_dependencies as scope,
)

_LIB_H = "/proj/include/mylib.h"
_SYS_H = "/usr/include/stdio.h"


def _flat(snap: AbiSnapshot) -> tuple[dict[str, str], ...]:
    d = snap.declarations
    return (
        dict(d.constants),
        dict(d.typedefs),
        dict(d.typedefs_qualified),
        dict(d.typedef_entity_ids),
    )


def _snap(shape: dict[str, str | None]) -> AbiSnapshot:
    """*shape*: typedef name -> declaring header (``None`` = unknown)."""
    values = {k: f"int /*{k}*/" for k in shape}
    headers = {k: h for k, h in shape.items() if h}
    consts = HeaderAttributedMap({"MY_C": "1", "SYS_C": "2"}, {"SYS_C": _SYS_H})
    return AbiSnapshot(
        library="libx.so",
        version="1",
        from_headers=True,
        typedefs=HeaderAttributedMap(values, headers),
        typedefs_qualified=HeaderAttributedMap(values, headers),
        constants=consts,
    )


SHAPES = {
    "mixed": {"my_t": _LIB_H, "__FILE": _SYS_H, "size_t": _SYS_H},
    "only system": {"__off_t": _SYS_H, "__pid_t": _SYS_H},
    "unknown origin kept": {"mystery_t": None, "__FILE": _SYS_H},
    "multi-header": {"dual_t": _SYS_H, "lib_t": _LIB_H},
}


@pytest.fixture
def cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(store, "_CACHE_DIR", tmp_path)
    return tmp_path


@pytest.mark.parametrize("name", sorted(SHAPES))
def test_rescoping_a_reloaded_entry_equals_scoping_the_fresh_one(
    cache_dir: Path, name: str
) -> None:
    fresh = _snap(SHAPES[name])
    store._write_entry(fresh, "k" * 64, Path("libx.so"))
    reloaded = store._read_entry("k" * 64, Path("libx.so"))
    assert reloaded is not None
    assert _flat(scope(reloaded, [_LIB_H])) == _flat(scope(fresh, [_LIB_H]))


def test_the_fixture_actually_drops_something(cache_dir: Path) -> None:
    """Non-vacuity: scoping the fresh snapshot must drop a system typedef."""
    fresh = _snap(SHAPES["mixed"])
    assert "__FILE" not in scope(fresh, [_LIB_H]).declarations.typedefs


def test_header_entry_without_its_sidecar_is_a_miss(cache_dir: Path) -> None:
    store._write_entry(_snap(SHAPES["mixed"]), "a" * 64, Path("libx.so"))
    declaring_headers.sidecar_path(cache_dir, "a" * 64).unlink()
    assert store._read_entry("a" * 64, Path("libx.so")) is None


def test_binary_only_entry_needs_no_sidecar(cache_dir: Path) -> None:
    snap = AbiSnapshot(library="libx.so", version="1")
    store._write_entry(snap, "b" * 64, Path("libx.so"))
    assert not declaring_headers.sidecar_path(cache_dir, "b" * 64).exists()
    assert store._read_entry("b" * 64, Path("libx.so")) is not None
    assert not declaring_headers.sidecar_path(cache_dir, "b" * 64).exists()


_LIBSSL = Path("/usr/lib/x86_64-linux-gnu/libssl.so.3")
_SSL_H = Path("/usr/include/openssl/ssl.h")

_FIXTURE_SRC = """\
#include <stdio.h>
#include <stdint.h>
typedef struct { FILE *f; uint32_t n; } my_stream;
int my_open(my_stream *s);
"""


def _gcc_fixture(tmp_path: Path) -> tuple[Path, Path]:
    import subprocess

    hdr = tmp_path / "mylib.h"
    hdr.write_text(_FIXTURE_SRC)
    src = tmp_path / "mylib.c"
    src.write_text('#include "mylib.h"\nint my_open(my_stream *s){return s->n;}\n')
    lib = tmp_path / "libmylib.so"
    subprocess.run(
        ["gcc", "-shared", "-fPIC", "-g", "-o", str(lib), str(src)], check=True
    )
    return lib, hdr


@pytest.mark.integration
@pytest.mark.skipif(not shutil.which("castxml"), reason="needs castxml")
@pytest.mark.parametrize("case", ["gcc-fixture", "libssl"])
def test_real_dump_roundtrip_rescopes_identically(
    cache_dir: Path, tmp_path: Path, case: str
) -> None:
    from abicheck.workflows.dump.native import _run_dump_uncached

    if case == "libssl":
        if not (_LIBSSL.exists() and _SSL_H.exists()):
            pytest.skip("libssl/openssl headers not installed")
        lib, hdr = _LIBSSL, _SSL_H
    else:
        lib, hdr = _gcc_fixture(tmp_path)
    fresh = _run_dump_uncached(lib, "elf", [hdr], [], "1", "c")
    store._write_entry(fresh, "c" * 64, lib)
    reloaded = store._read_entry("c" * 64, lib)
    assert reloaded is not None
    a, b = scope(fresh, [hdr]), scope(reloaded, [hdr])
    assert _flat(b) == _flat(a)
    assert [t.name for t in b.declarations.types] == [
        t.name for t in a.declarations.types
    ]
