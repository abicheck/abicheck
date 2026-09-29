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


# --- Header inputs a CompileContext makes reachable ------------------------
#
# Bug class: a compile context is folded into the key by *spelling*, but the
# header content it points the parser at (sysroot, -I/-isystem/-include/...)
# was not hashed, so an edit there served a snapshot parsed from stale
# headers. Invariant, checked over every tracked option and every spelling
# form (separate operand, joined, ``=``), through the public cached_run_dump
# entry point: editing a header under that input forces a re-dump, and an
# unchanged tree keeps the warm hit. Oracle: run_dump's own call count.

from abicheck.service_dump_cache import compile_context_header_inputs  # noqa: E402

_DIR_OPTS = (
    "-I",
    "-isystem",
    "-iquote",
    "-idirafter",
    "--sysroot",
    "-isysroot",
    "--include-directory",
    "-cxx-isystem",
    "-iframework",
    "-F",
)
_FILE_OPTS = ("-include", "-imacros", "--include")


def _spellings(opt: str, value: str) -> list[tuple[str, ...]]:
    forms: list[tuple[str, ...]] = [(opt, value)]
    if opt.startswith("--"):
        forms.append((f"{opt}={value}",))
    else:
        forms.append((f"{opt}{value}",))
    return forms


def _contexts_for(opt: str, value: str) -> list[CompileContext]:
    out = []
    for toks in _spellings(opt, value):
        out.append(CompileContext(gcc_option_tokens=toks))
        out.append(CompileContext(gcc_options=" ".join(toks)))
    if opt == "--sysroot":
        out.append(CompileContext(sysroot=Path(value)))
    return out


def _edit_busts_and_idle_hits(tmp_path, cc: CompileContext, target: Path) -> None:
    binary = tmp_path / "lib.so"
    binary.write_bytes(b"ELF fake content")
    calls: list[object] = []
    run = _fake(calls)

    def go() -> None:
        cached_run_dump(run, binary, "elf", [], [], "1.0", "c++", compile=cc)

    go()
    go()
    assert len(calls) == 1, cc  # unchanged inputs: warm hit
    target.write_text(target.read_text() + "int added;\n")
    go()
    assert len(calls) == 2, cc  # edited header: key changed
    go()
    assert len(calls) == 2, cc


def test_every_dir_option_spelling_tracks_header_edits(tmp_path, monkeypatch):
    from abicheck import snapshot_cache

    n = 0
    for opt in _DIR_OPTS:
        for cc_template in _contexts_for(opt, "PLACEHOLDER"):
            n += 1
            case = tmp_path / f"c{n}"
            inc = case / "inc" / "sub"
            inc.mkdir(parents=True)
            hdr = inc / "dep.h"
            hdr.write_text("int x;\n")
            root = case / "inc"
            cc = _replace_placeholder(cc_template, root)
            monkeypatch.setattr(snapshot_cache, "_CACHE_DIR", case / "cache")
            assert _cacheable(cc), cc
            _edit_busts_and_idle_hits(case, cc, hdr)
    assert n >= len(_DIR_OPTS) * 4


def test_every_file_option_spelling_tracks_edits(tmp_path, monkeypatch):
    from abicheck import snapshot_cache

    n = 0
    for opt in _FILE_OPTS:
        for cc_template in _contexts_for(opt, "PLACEHOLDER"):
            n += 1
            case = tmp_path / f"f{n}"
            case.mkdir()
            forced = case / "forced.h"
            forced.write_text("int y;\n")
            cc = _replace_placeholder(cc_template, forced)
            monkeypatch.setattr(snapshot_cache, "_CACHE_DIR", case / "cache")
            assert _cacheable(cc), cc
            _edit_busts_and_idle_hits(case, cc, forced)
    assert n >= len(_FILE_OPTS) * 4


def _replace_placeholder(cc: CompileContext, path: Path) -> CompileContext:
    import dataclasses

    p = path.as_posix()
    return dataclasses.replace(
        cc,
        gcc_options=cc.gcc_options.replace("PLACEHOLDER", p)
        if cc.gcc_options
        else None,
        gcc_option_tokens=tuple(
            t.replace("PLACEHOLDER", p) for t in cc.gcc_option_tokens
        ),
        sysroot=path if cc.sysroot is not None else None,
    )


def test_prefix_ambiguity_classifies_longest_option():
    # `--include` is a prefix of `--include-directory`; `-include` of
    # `-include-pch` (untracked). Each must be read as the longer option.
    files, dirs = compile_context_header_inputs(
        CompileContext(gcc_option_tokens=("--include-directory=/d", "--include=/f.h"))
    ) or ([], [])
    assert files == [Path("/f.h")] and dirs == [Path("/d")]


def test_untrackable_header_inputs_are_uncacheable():
    for toks in (
        ("@args.rsp",),
        ("-include-pch", "x.pch"),
        ("-iprefix", "/p"),
        ("-iwithprefix", "inc"),
        ("-ivfsoverlay", "o.yaml"),
        ("-fmodule-map-file=m.modulemap",),
        ("-I",),  # dangling operand
    ):
        cc = CompileContext(gcc_option_tokens=toks)
        assert compile_context_header_inputs(cc) is None, toks
        assert not _cacheable(cc), toks


def test_non_path_options_add_no_header_inputs():
    for toks in (("-DX",), ("-std=c++17",), ("-O2", "-Wall"), ("-x", "c++")):
        assert compile_context_header_inputs(
            CompileContext(gcc_option_tokens=toks)
        ) == ([], [])
