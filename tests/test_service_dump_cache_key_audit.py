"""Every ``cached_run_dump`` parameter is accounted for by the cache.

Now that ``CompileContext``-bearing runs are cacheable, a parameter that can
change the produced snapshot but is neither folded into the whole-snapshot
key nor excluded by ``_dump_is_cacheable`` would let a warm run serve a
snapshot extracted under different inputs. This enumerates the *real*
signature, so a newly threaded parameter fails here until it is classified.
It also proves a hit never aliases an object an earlier caller mutated.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

import pytest

from abicheck import snapshot_cache
from abicheck.compile_context import CompileContext
from abicheck.model import AbiSnapshot
from abicheck.service_dump_cache import cached_run_dump

# Folded into the key: varying the value must change it.
KEYED: dict[str, Any] = {
    "binary_fmt": "pe",
    "headers": "<alt-header>",
    "includes": "<alt-dir>",
    "version": "2.0",
    "lang": "c",
    "lang_explicit": True,
    "public_headers": "<alt-header>",
    "public_header_dirs": "<alt-dir>",
    "header_backend": "clang",
    "compile": CompileContext(gcc_options="-DX"),
    "include_dependencies": False,
    "public_include_search_dirs": "<alt-dir>",
}
# Excludes the call from the cache entirely: varying the value must bypass it.
GATED: dict[str, Any] = {
    "pdb_path": Path("/x.pdb"),
    "dwarf_only": True,
    "debug_roots": [Path("/dbg")],
    "enable_debuginfod": True,
    "debug_format": "dwarf",
    "symbols_only": True,
    "debug_presence_only": True,
    "include_labels": "<label>",
}
# Cannot change the produced snapshot on a cacheable call.
INERT = {
    "run_dump",
    "path",  # its content is the key's base material
    "notify",  # progress callback only
    "debuginfod_url",  # only read when enable_debuginfod (GATED)
    "dump_manifest",  # keyed structurally; covered by the manifest cache tests
}


def test_classification_is_exhaustive():
    params = set(inspect.signature(cached_run_dump).parameters)
    classified = set(KEYED) | set(GATED) | INERT
    assert params == classified, (params - classified, classified - params)


@pytest.fixture
def env(tmp_path: Path, monkeypatch):
    binary = tmp_path / "lib.so"
    binary.write_bytes(b"\x7fELF-fake")
    inc = tmp_path / "inc"
    inc.mkdir()
    hdr = inc / "a.h"
    hdr.write_text("int a;\n")
    alt_dir = tmp_path / "alt"
    alt_dir.mkdir()
    alt_hdr = alt_dir / "b.h"
    alt_hdr.write_text("int b;\n")
    keys: list[str] = []
    real_lookup = snapshot_cache.lookup_key

    def spy(key: str, path: Path):
        keys.append(key)
        return real_lookup(key, path)

    monkeypatch.setattr(snapshot_cache, "lookup_key", spy)
    subst = {
        "<alt-header>": [alt_hdr],
        "<alt-dir>": [alt_dir],
        "<label>": {inc: "vendor"},
    }
    base = {"headers": [hdr], "includes": [inc], "header_backend": "castxml"}
    return binary, base, subst, keys


def _call(binary: Path, kwargs: dict[str, Any], calls: list[int]) -> AbiSnapshot:
    def fake(*_a: Any, **_k: Any) -> AbiSnapshot:
        calls.append(1)
        return AbiSnapshot(library="lib.so", version="1.0", from_headers=True)

    return cached_run_dump(fake, binary, kwargs.pop("binary_fmt", "elf"), **kwargs)


@pytest.mark.parametrize("name", sorted(KEYED))
def test_keyed_parameter_changes_the_key(env, name):
    binary, base, subst, keys = env
    _call(binary, dict(base), [])
    value = KEYED[name]
    varied = {
        **base,
        name: subst.get(value, value) if isinstance(value, str) else value,
    }
    _call(binary, varied, [])
    assert len(keys) == 2 and keys[0] != keys[1], (name, keys)


@pytest.mark.parametrize("name", sorted(GATED))
def test_gated_parameter_bypasses_the_cache(env, name):
    binary, base, subst, keys = env
    value = GATED[name]
    varied = {
        **base,
        name: subst.get(value, value) if isinstance(value, str) else value,
    }
    calls: list[int] = []
    _call(binary, dict(varied), calls)
    _call(binary, dict(varied), calls)
    assert keys == [] and len(calls) == 2, name


def test_mutating_a_returned_snapshot_never_reaches_a_later_hit(env):
    binary, base, _subst, _keys = env
    calls: list[int] = []
    first = _call(binary, dict(base), calls)
    first.parsed_with_build_context = True
    first.version = "mutated"
    second = _call(binary, dict(base), calls)
    third = _call(binary, dict(base), calls)
    assert len(calls) == 1  # the later two were real hits
    for hit in (second, third):
        assert hit is not first
        assert hit.version == "1.0"
        assert not hit.parsed_with_build_context
    second.version = "again"
    assert third.version == "1.0"
