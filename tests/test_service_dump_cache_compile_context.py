"""Whole-snapshot cache keying on a ``CompileContext``.

``compare`` always threads a ``CompileContext`` (even an all-default one);
the cache used to refuse any non-``None`` context, so a warm ``compare``
never hit it (oneDNN validation: every warm run re-parsed castxml output).
The cache now keys on the context's normalized content.
"""

from __future__ import annotations

import itertools
from pathlib import Path

from abicheck.compile_context import CompileContext
from abicheck.model import AbiSnapshot
from abicheck.service_dump_cache import (
    _dump_cache_extra_key,
    _dump_is_cacheable,
    cached_run_dump,
    compile_context_cache_field,
    effective_header_backend,
)


def _variants() -> dict[str, CompileContext]:
    return {
        "gcc_path": CompileContext(gcc_path="/opt/gcc"),
        "gcc_prefix": CompileContext(gcc_prefix="aarch64-linux-gnu-"),
        "gcc_options": CompileContext(gcc_options="-DX"),
        "tokens": CompileContext(gcc_option_tokens=("-DX",)),
        "tokens_yx": CompileContext(gcc_option_tokens=("-DY", "-DX")),
        "tokens_xy": CompileContext(gcc_option_tokens=("-DX", "-DY")),
        "sysroot": CompileContext(sysroot=Path("/sys")),
        "nostdinc": CompileContext(nostdinc=True),
        "device": CompileContext(frontend_context="device"),
    }


def _cacheable(compile: object | None) -> bool:
    return _dump_is_cacheable(
        pdb_path=None,
        dwarf_only=False,
        debug_roots=None,
        enable_debuginfod=False,
        debug_format=None,
        symbols_only=False,
        debug_presence_only=False,
        compile=compile,
    )


def test_real_contexts_are_cacheable_unknown_objects_are_not():
    assert _cacheable(None)
    assert _cacheable(CompileContext())
    for cc in _variants().values():
        assert _cacheable(cc)
    assert not _cacheable(object())


def test_none_and_default_share_a_key():
    default = compile_context_cache_field(None)
    assert compile_context_cache_field(CompileContext()) == default
    assert compile_context_cache_field(CompileContext(frontend="AUTO")) == default


def test_every_output_affecting_field_splits_the_key():
    keys = {name: compile_context_cache_field(cc) for name, cc in _variants().items()}
    keys["default"] = compile_context_cache_field(None)
    assert len(set(keys.values())) == len(keys), keys


def test_combined_fields_never_collide():
    # Exhaustive small-domain enumeration: every combination of three
    # independent fields yields a distinct key.
    seen: dict[str, tuple[object, ...]] = {}
    for gp, sr, ns in itertools.product(
        (None, "/a", "/b"), (None, Path("/s")), (False, True)
    ):
        key = compile_context_cache_field(
            CompileContext(gcc_path=gp, sysroot=sr, nostdinc=ns)
        )
        assert key not in seen, (seen[key], (gp, sr, ns))
        seen[key] = (gp, sr, ns)


def test_explicit_frontend_overrides_backend():
    assert effective_header_backend("auto", None) == "auto"
    assert effective_header_backend("auto", CompileContext()) == "auto"
    assert effective_header_backend("auto", CompileContext(frontend="Clang")) == "clang"
    assert (
        effective_header_backend("castxml", CompileContext(frontend="auto"))
        == "castxml"
    )


def test_extra_key_folds_compile_context():
    k0 = _dump_cache_extra_key("elf", "auto", None, None, uses_ast=False)
    k1 = _dump_cache_extra_key(
        "elf", "auto", None, None, uses_ast=False, compile=CompileContext()
    )
    k2 = _dump_cache_extra_key(
        "elf", "auto", None, None, uses_ast=False, compile=CompileContext(nostdinc=True)
    )
    k3 = _dump_cache_extra_key(
        "elf",
        "auto",
        None,
        None,
        uses_ast=False,
        compile=CompileContext(frontend="clang"),
    )
    assert k0 == k1
    assert len({k0, k2, k3}) == 3


def _fake(calls: list[object]):
    def fake_run_dump(path, binary_fmt, headers, includes, version, lang, **kwargs):
        calls.append(kwargs.get("compile"))
        return AbiSnapshot(library="libfoo.so.1", version="1.0")

    return fake_run_dump


def test_warm_hit_with_default_compile_context(tmp_path):
    binary = tmp_path / "lib.so"
    binary.write_bytes(b"ELF fake content")
    calls: list[object] = []
    run = _fake(calls)
    cached_run_dump(run, binary, "elf", [], [], "1.0", "c++", compile=CompileContext())
    cached_run_dump(run, binary, "elf", [], [], "1.0", "c++", compile=CompileContext())
    cached_run_dump(run, binary, "elf", [], [], "1.0", "c++", compile=None)
    assert len(calls) == 1


def test_non_default_contexts_cache_separately_and_warm(tmp_path):
    binary = tmp_path / "lib.so"
    binary.write_bytes(b"ELF fake content")
    calls: list[object] = []
    run = _fake(calls)
    variants = list(_variants().values())
    for cc in variants:
        cached_run_dump(run, binary, "elf", [], [], "1.0", "c++", compile=cc)
    assert calls == variants  # every miss handed the real context to run_dump
    for cc in variants:
        cached_run_dump(run, binary, "elf", [], [], "1.0", "c++", compile=cc)
    assert len(calls) == len(variants)
