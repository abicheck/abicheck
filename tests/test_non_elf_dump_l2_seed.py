# Copyright 2026 Nikolay Petrov
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""The PE/Mach-O dump path must seed L2 include dirs from the build too, in parity
with the ELF path — otherwise `dump foo.dll -H include --sources .` with no -I can't
resolve dependency headers and falls back to export-table mode (Codex review)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from abicheck.cli_dump_helpers import perform_elf_dump
from abicheck.cli_dump_non_elf import handle_non_elf_dump
from abicheck.compile_context import CompileContext
from abicheck.errors import HeaderCompileContextAmbiguousError
from abicheck.model import AbiSnapshot


def test_non_elf_dump_seeds_includes_and_runs_cleanup(monkeypatch, tmp_path):
    captured: dict = {}
    events: list[str] = []
    seeded_dir = tmp_path / "buildinc"

    def fake_seed_and_fold(**kwargs):
        events.append("seed")
        captured["seed_kwargs"] = kwargs
        # Return a build-derived include dir + a pending temp-dir cleanup.
        kwargs["pending_cleanups"].append(lambda: events.append("cleanup"))
        explicit_ctx = CompileContext(
            gcc_path=kwargs["gcc_path"],
            gcc_prefix=kwargs["gcc_prefix"],
            gcc_options=kwargs["gcc_options"],
            gcc_option_tokens=kwargs["gcc_option_tokens"],
            sysroot=kwargs["sysroot"],
            nostdinc=kwargs["nostdinc"],
            frontend=kwargs["frontend"],
            frontend_context=kwargs["frontend_context"],
        )
        return [seeded_dir], False, explicit_ctx, ()

    def fake_dump_native(so_path, binary_fmt, headers, includes, version, lang, **kw):
        events.append("dump")
        captured["includes"] = includes
        return AbiSnapshot(library="l", version=version)

    # ADR-061 Phase 4: the CLI dump path reaches this through
    # `workflows.extraction` (a frontend may not import the `extract` ring
    # directly), and a re-export binds the name there at import time -- so
    # that module is where this call resolves. See its own docstring.
    monkeypatch.setattr(
        "abicheck.workflows.extraction.seed_includes_and_fold_compile_context",
        fake_seed_and_fold,
    )

    handle_non_elf_dump(
        so_path=tmp_path / "foo.dll",
        binary_fmt="pe",
        headers=(tmp_path / "h.h",),
        includes=(),
        version="1",
        lang="c++",
        pdb_path=None,
        follow_deps=False,
        git_tag=None,
        build_id=None,
        no_git=True,
        output=None,
        dump_native_binary=fake_dump_native,
        stamp_provenance=lambda *a, **k: None,
        write_snapshot_output=lambda *a, **k: None,
        sources=tmp_path,
        collect_mode="build",
    )

    # The build-derived include dir reached the native dumper.
    assert seeded_dir in captured["includes"]
    # And the temp-build-dir cleanup ran after the dump consumed the dirs.
    assert events == ["seed", "dump", "cleanup"]


def test_non_elf_dump_forwards_only_explicit_includes_as_public_include_search_dirs(
    monkeypatch, tmp_path
):
    """The build-derived seed dir must reach the compile path (`includes`)
    but NOT the provenance-widening path (`public_include_search_dirs`) --
    same regression class the ELF `perform_elf_dump` path already avoids
    (Codex review, PR #839 round 9): an auto-derived umbrella-header
    directory can hold a genuinely private sibling header, and folding it
    into provenance would silently promote that sibling to PUBLIC_HEADER."""
    captured: dict = {}
    seeded_dir = tmp_path / "buildinc"
    explicit_dir = tmp_path / "explicit"

    def fake_seed_and_fold(**kwargs):
        kwargs["pending_cleanups"].append(lambda: None)
        explicit_ctx = CompileContext(
            gcc_path=kwargs["gcc_path"],
            gcc_prefix=kwargs["gcc_prefix"],
            gcc_options=kwargs["gcc_options"],
            gcc_option_tokens=kwargs["gcc_option_tokens"],
            sysroot=kwargs["sysroot"],
            nostdinc=kwargs["nostdinc"],
            frontend=kwargs["frontend"],
            frontend_context=kwargs["frontend_context"],
        )
        # Widen with a build-derived dir, mirroring the real seed's own
        # auto-add behavior.
        return [*kwargs["includes"], seeded_dir], False, explicit_ctx, ()

    def fake_dump_native(so_path, binary_fmt, headers, includes, version, lang, **kw):
        captured["includes"] = includes
        captured["public_include_search_dirs"] = kw.get("public_include_search_dirs")
        return AbiSnapshot(library="l", version=version)

    monkeypatch.setattr(
        "abicheck.workflows.extraction.seed_includes_and_fold_compile_context",
        fake_seed_and_fold,
    )

    handle_non_elf_dump(
        so_path=tmp_path / "foo.dll",
        binary_fmt="pe",
        headers=(tmp_path / "h.h",),
        includes=(explicit_dir,),
        version="1",
        lang="c++",
        pdb_path=None,
        follow_deps=False,
        git_tag=None,
        build_id=None,
        no_git=True,
        output=None,
        dump_native_binary=fake_dump_native,
        stamp_provenance=lambda *a, **k: None,
        write_snapshot_output=lambda *a, **k: None,
        sources=tmp_path,
        collect_mode="build",
    )

    # The compile path sees both the explicit dir and the build-derived one.
    assert set(captured["includes"]) == {explicit_dir, seeded_dir}
    # Provenance widening sees ONLY the caller's own explicit dir.
    assert captured["public_include_search_dirs"] == [explicit_dir]


def test_non_elf_dump_header_roots_includes_public_headers_and_dirs(
    monkeypatch, tmp_path
):
    """abicheck-internal-bugs finding 1: the PE/Mach-O ``dump`` path's
    ``write_snapshot_output`` call must fold ``public_headers``/
    ``public_header_dirs`` into ``header_roots`` alongside ``headers`` --
    matching ``dumper_scoping.apply_dependency_scope_to_run_dump_result``,
    the choke point ``compare``'s own live-binary dumping uses for the
    identical dependency-scoping decision. Before the fix this path passed
    only ``headers``, silently diverging from `compare`'s scoping whenever a
    library declared its public surface via ``public_headers``/
    ``public_header_dirs``."""
    hdr = tmp_path / "h.h"
    pub_header = tmp_path / "extra.h"
    pub_dir = tmp_path / "pubdir"

    captured: dict = {}

    def _write(*_a, header_roots=(), **_k):  # noqa: ANN002, ANN003
        captured["header_roots"] = header_roots

    handle_non_elf_dump(
        so_path=tmp_path / "foo.dll",
        binary_fmt="pe",
        headers=(hdr,),
        includes=(),
        version="1",
        lang="c++",
        pdb_path=None,
        follow_deps=False,
        git_tag=None,
        build_id=None,
        no_git=True,
        output=None,
        dump_native_binary=lambda *a, **k: AbiSnapshot(
            library="l", version="1", from_headers=True
        ),
        stamp_provenance=lambda *a, **k: None,
        write_snapshot_output=_write,
        public_headers=(pub_header,),
        public_header_dirs=(pub_dir,),
    )

    assert set(captured["header_roots"]) == {hdr, pub_header, pub_dir}


def test_non_elf_dump_gates_inferred_query_for_l2_only(monkeypatch, tmp_path):
    # --depth headers (collect_mode "off") must disable the inferred build query in
    # the PE/Mach-O seed too — the flag is threaded from collect_mode.
    captured: dict = {}

    def fake_seed_and_fold(**kwargs):
        captured["allow"] = kwargs["collect_mode"] != "off"
        explicit_ctx = CompileContext(
            gcc_path=kwargs["gcc_path"],
            gcc_prefix=kwargs["gcc_prefix"],
            gcc_options=kwargs["gcc_options"],
            gcc_option_tokens=kwargs["gcc_option_tokens"],
            sysroot=kwargs["sysroot"],
            nostdinc=kwargs["nostdinc"],
            frontend=kwargs["frontend"],
            frontend_context=kwargs["frontend_context"],
        )
        return list(kwargs["includes"]), False, explicit_ctx, ()

    monkeypatch.setattr(
        "abicheck.workflows.extraction.seed_includes_and_fold_compile_context",
        fake_seed_and_fold,
    )

    handle_non_elf_dump(
        so_path=tmp_path / "foo.dylib",
        binary_fmt="macho",
        headers=(tmp_path / "h.h",),
        includes=(),
        version="1",
        lang="c++",
        pdb_path=None,
        follow_deps=False,
        git_tag=None,
        build_id=None,
        no_git=True,
        output=None,
        dump_native_binary=lambda *a, **k: AbiSnapshot(library="l", version="1"),
        stamp_provenance=lambda *a, **k: None,
        write_snapshot_output=lambda *a, **k: None,
        sources=tmp_path,
        collect_mode="off",  # --depth headers → no inferred build
    )
    assert captured["allow"] is False


def test_non_elf_dump_folds_l3_compile_context_into_header_parse(tmp_path):
    """P0.3 L3->L2 fold (AGENTS.md's former "The native ELF `abicheck dump`
    path never applies L3 build context..." known gap -- PE/Mach-O shared
    the identical gap and is closed here alongside it): a --sources dump
    whose compile database resolves a real -std=/-D for these headers must
    reach the native dumper's own `compile=` context, not just the L2
    include-dir fallback. Also stamps `parsed_with_build_context`."""
    hdr = tmp_path / "widget.h"
    hdr.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src = tmp_path / "widget.cpp"
    src.write_text('#include "widget.h"\n', encoding="utf-8")
    (tmp_path / "compile_commands.json").write_text(
        json.dumps(
            [
                {
                    "directory": str(tmp_path),
                    "file": str(src),
                    "arguments": [
                        "c++",
                        "-c",
                        str(src),
                        "-o",
                        "out.o",
                        "-std=c++20",
                        "-DFOO=1",
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )

    captured: dict = {}

    def fake_dump_native(so_path, binary_fmt, headers, includes, version, lang, **kw):
        captured["compile"] = kw["compile"]
        return AbiSnapshot(library="l", version=version, from_headers=True)

    written: dict = {}

    def fake_write(snap, *a, **k):  # noqa: ANN002, ANN003
        written["snap"] = snap

    handle_non_elf_dump(
        so_path=tmp_path / "foo.dll",
        binary_fmt="pe",
        headers=(hdr,),
        includes=(),
        version="1",
        lang="c++",
        pdb_path=None,
        follow_deps=False,
        git_tag=None,
        build_id=None,
        no_git=True,
        output=None,
        dump_native_binary=fake_dump_native,
        stamp_provenance=lambda *a, **k: None,
        write_snapshot_output=fake_write,
        sources=tmp_path,
        collect_mode="source-target",
        compile_db_context_matched=False,
    )

    tokens = captured["compile"].gcc_option_tokens
    assert "-std=c++20" in tokens
    assert "-DFOO=1" in tokens
    assert written["snap"].parsed_with_build_context is True


def test_perform_elf_dump_keeps_l3_derived_flags_out_of_build_context_collector(
    tmp_path, monkeypatch
):
    """Codex review, PR #782 (P2): the ADR-039 collector's own comment already
    says the auto-derived, per-header build context must never be unioned
    snapshot-wide. The P0.3 fold's ``l3_context_applied`` reassignment folds
    that same derived context into ``gcc_option_tokens`` for the header
    parse -- ``_user_define_flags`` must still see only the user's own
    explicit tokens (captured before the fold), never the derived ones."""
    so = tmp_path / "lib.so"
    hdr = tmp_path / "widget.h"
    hdr.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src = tmp_path / "widget.cpp"
    src.write_text('#include "widget.h"\n', encoding="utf-8")
    compile_db = tmp_path / "compile_commands.json"
    compile_db.write_text(
        json.dumps([{
            "directory": str(tmp_path),
            "file": str(src),
            "arguments": [
                "c++", "-c", str(src), "-o", "out.o", "-std=c++20", "-DL3ONLY=1",
            ],
        }]),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "abicheck.cli_dump_helpers.dump",
        lambda **_kw: AbiSnapshot(library="lib.so", version="1.0", from_headers=True),
    )
    monkeypatch.setattr("abicheck.service._attach_header_graph", lambda snap, *_a, **_k: snap)

    captured: dict = {}

    def _fake_attach_build_context(snap, compile_db_arg, headers, extra_flags, **_kw):
        captured["extra_flags"] = extra_flags

    monkeypatch.setattr(
        "abicheck.header_conditionals.attach_build_context",
        _fake_attach_build_context,
    )

    perform_elf_dump(
        so, (hdr,), (), "1.0", "c++", None, None, None,
        (),  # gcc_path/prefix/options/option_tokens (no explicit user tokens)
        None, False,  # sysroot, nostdinc
        False, None,  # dwarf_only, effective_debug_format
        (), (),  # public_headers, public_header_dirs
        compile_db,  # effective_compile_db -- triggers the ADR-039 collector
        False, (), "", None, None, False, None,
        None,  # build_info
        tmp_path,  # sources=tmp_path (auto-discovers compile_commands.json) -> L3 fold
        None, False,
        "source-target",  # build_config, allow_build_query, collect_mode
        lambda inputs: list(inputs),
        lambda *a, **k: None,
        lambda *a, **k: None,
        lambda *a, **k: None,
    )

    assert "extra_flags" in captured, "the ADR-039 collector was never invoked"
    assert "-DL3ONLY=1" not in captured["extra_flags"], (
        "the L3-derived, per-header-matched flag leaked into the snapshot-wide "
        "build-context collector -- it must stay scoped to the header parse only"
    )


def test_perform_elf_dump_keeps_deferred_inferred_root_below_l3_derived_includes(
    tmp_path, monkeypatch
):
    """Codex review, PR #782 (P2): resolve_inferred_header_roots's own
    ``deferred`` tokens exist specifically to search BELOW any existing
    build context (that's the whole point of their -isystem/-idirafter
    bucket choice) -- folding them into the "explicit" side of the P0.3 L3
    fold let _merge_l3_compile_context rank them ahead of a real L3-derived
    include dir for a colliding header, the exact inversion "deferred" is
    meant to prevent."""
    from abicheck.compile_context import CompileContext
    from abicheck.model import AbiSnapshot

    so = tmp_path / "lib.so"
    root = tmp_path / "include"
    (root / "oneapi").mkdir(parents=True)
    umb = root / "oneapi" / "tbb.h"
    umb.write_text("// umbrella", encoding="utf-8")
    existing_build_inc = tmp_path / "existingbuild"
    existing_build_inc.mkdir()
    generated_inc = tmp_path / "generated"
    generated_inc.mkdir()

    captured: dict = {}

    def fake_dump(**kwargs):
        captured["gcc_option_tokens"] = kwargs["gcc_option_tokens"]
        return AbiSnapshot(library="lib", version="1.0", from_headers=True)

    def fake_seed_and_fold(**kwargs):
        # A minimal stand-in for a real L3 fold: derive an -isystem
        # <generated_inc> ahead of the caller's own explicit tokens, and
        # confirm those explicit tokens do NOT already carry the deferred
        # inferred-root token (this fix's whole point).
        explicit_tokens = kwargs["gcc_option_tokens"]
        assert str(root) not in explicit_tokens, (
            "the deferred inferred-root token must not be folded into the "
            "'explicit' side of the L3 merge -- it would then rank ahead "
            "of a real L3-derived include dir"
        )
        merged = CompileContext(
            gcc_path=kwargs["gcc_path"],
            gcc_prefix=kwargs["gcc_prefix"],
            gcc_options=None,
            gcc_option_tokens=(*explicit_tokens, "-isystem", str(generated_inc)),
            sysroot=kwargs["sysroot"],
            nostdinc=kwargs["nostdinc"],
            frontend=kwargs["frontend"],
            frontend_context=kwargs["frontend_context"],
        )
        return list(kwargs["includes"]), True, merged, (generated_inc,)

    monkeypatch.setattr(
        "abicheck.workflows.extraction.seed_includes_and_fold_compile_context",
        fake_seed_and_fold,
    )
    monkeypatch.setattr("abicheck.cli_dump_helpers.dump", fake_dump)
    monkeypatch.setattr(
        "abicheck.service._attach_header_graph", lambda snap, *_a, **_k: snap
    )

    perform_elf_dump(
        so, (umb,), (), "1.0", "c++", None, None, None,
        ("-isystem", str(existing_build_inc)),  # gcc_option_tokens: existing context
        None, False,
        False, None,
        (), (),
        None,  # effective_compile_db
        False, (), "", None, None, False, None,
        None,  # build_info
        tmp_path,  # sources -> attempts the (mocked) L3 fold
        None, False,
        "source-target",
        lambda inputs: list(inputs),
        lambda *a, **k: None,
        lambda *a, **k: None,
        lambda *a, **k: None,
    )

    toks = list(captured["gcc_option_tokens"])
    assert str(root) in toks, "the inferred header root must still be present"
    assert str(generated_inc) in toks
    # The L3-derived include dir must be searched BEFORE the deferred
    # inferred root -- an inferred root outranking a real build's own
    # generated header dir would shadow it for a colliding header.
    assert toks.index(str(generated_inc)) < toks.index(str(root))


# ── seed_includes_and_fold_compile_context branch coverage ──────────────────


def _seed_and_fold(**overrides: Any):
    from abicheck.buildsource.l2_seed import seed_includes_and_fold_compile_context

    kwargs = dict(
        headers=[], includes=[], sources=None, build_info=None, build_config=None,
        build_query=None, build_compile_db=None, collect_mode="source-target",
        gcc_path=None, gcc_prefix=None, gcc_options=None, gcc_option_tokens=(),
        sysroot=None, nostdinc=False, frontend="auto", frontend_context="host",
        lang="c++", lang_explicit=False, pending_cleanups=[],
    )
    kwargs.update(overrides)
    return seed_includes_and_fold_compile_context(**kwargs)


def _write_compile_db(tmp_path, src, extra_args):
    (tmp_path / "compile_commands.json").write_text(
        json.dumps([{
            "directory": str(tmp_path), "file": str(src),
            "arguments": ["c++", "-c", str(src), "-o", "out.o", *extra_args],
        }]),
        encoding="utf-8",
    )


def _write_corrupt_pack(pack_dir):
    """A directory ``is_pack_dir()`` recognizes as a (corrupt) pack -- a
    ``manifest.json`` present but unparseable, so ``pack_io.load()``
    raises decoding it."""
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / "manifest.json").write_text("{not valid json", encoding="utf-8")


def test_seed_and_fold_no_inputs_is_noop():
    from pathlib import Path

    incs, applied, ctx, dirs = _seed_and_fold(headers=[Path("x.h")])
    assert (incs, applied, dirs) == ([], False, ())
    assert ctx.gcc_path is None


def test_seed_and_fold_no_match_returns_none(tmp_path):
    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src = tmp_path / "unrelated.cpp"
    src.write_text("int f() { return 0; }\n", encoding="utf-8")
    _write_compile_db(tmp_path, src, ["-std=c++20"])

    pending: list[Any] = []
    incs, applied, ctx, dirs = _seed_and_fold(
        headers=[header], sources=tmp_path, pending_cleanups=pending
    )
    assert (applied, dirs, pending) == (False, (), [])


def test_seed_and_fold_ambiguous_raises_and_drains_pending_cleanups(tmp_path):
    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src_a = tmp_path / "a.cpp"
    src_a.write_text('#include "widget.h"\n', encoding="utf-8")
    src_b = tmp_path / "b.cpp"
    src_b.write_text('#include "widget.h"\n', encoding="utf-8")
    (tmp_path / "compile_commands.json").write_text(
        json.dumps([
            {"directory": str(tmp_path), "file": str(src_a),
             "arguments": ["c++", "-c", str(src_a), "-std=c++17"]},
            {"directory": str(tmp_path), "file": str(src_b),
             "arguments": ["c++", "-c", str(src_b), "-std=c++20"]},
        ]),
        encoding="utf-8",
    )
    pending: list[Any] = []
    with pytest.raises(HeaderCompileContextAmbiguousError):
        _seed_and_fold(headers=[header], sources=tmp_path, pending_cleanups=pending)
    # The fail-closed case still drains any temp-build-dir cleanups this
    # attempt created before propagating (P0.3's rule) -- nothing left pending.
    assert pending == []


def test_seed_and_fold_corrupt_pack_degrades_to_empty(tmp_path):
    pack_dir = tmp_path / "pack"
    _write_corrupt_pack(pack_dir)
    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")

    _incs, applied, _ctx, dirs = _seed_and_fold(headers=[header], sources=pack_dir)
    assert (applied, dirs) == (False, ())


# --- The legacy `-p`/`--compile-db` auto-match must not stack on the P0.3 fold ---
#
# CLI cleanup phase two, PR 3A. Both mechanisms are fed by the *same*
# `--build-info` compile database, so when the fold resolves a context for
# these headers, presenting the legacy match's own derived flags to it as
# though they were an explicit user choice records the same evidence twice --
# measured end to end as `macro_ops` == `[["D","FOO=1"],["D","FOO=1"]]` and
# `include_sequence` == `[]` where every other resolver records one entry and
# one slot, i.e. a `profile_fingerprint` a `scan --against` correctly refuses
# as NOT_COMPARABLE. These two pin the *decision* at the seam it is made,
# rather than only through a real toolchain (the end-to-end lens is
# `tests/test_dump_cli_typed_api_parity.py`, which is `integration`-marked and
# so cannot gate this in the default lane).


def _perform_elf_dump_capturing_fold_options(
    tmp_path, monkeypatch, *, legacy_flags: tuple[str, ...]
) -> dict:
    """Run ``perform_elf_dump`` with a stubbed fold and return its kwargs."""
    so = tmp_path / "lib.so"
    hdr = tmp_path / "widget.h"
    hdr.write_text("struct Widget { int x; };\n", encoding="utf-8")
    captured: dict = {}

    def fake_seed_and_fold(**kwargs):
        captured["kwargs"] = kwargs
        explicit = CompileContext(
            gcc_path=kwargs["gcc_path"],
            gcc_prefix=kwargs["gcc_prefix"],
            gcc_options=kwargs["gcc_options"],
            gcc_option_tokens=kwargs["gcc_option_tokens"],
            sysroot=kwargs["sysroot"],
            nostdinc=kwargs["nostdinc"],
            frontend=kwargs["frontend"],
            frontend_context=kwargs["frontend_context"],
        )
        return list(kwargs["includes"]), False, explicit, ()

    monkeypatch.setattr(
        "abicheck.workflows.extraction.seed_includes_and_fold_compile_context",
        fake_seed_and_fold,
    )
    monkeypatch.setattr(
        "abicheck.cli_dump_helpers.dump",
        lambda **_kw: AbiSnapshot(library="lib.so", version="1.0", from_headers=True),
    )
    monkeypatch.setattr(
        "abicheck.service._attach_header_graph", lambda snap, *_a, **_k: snap
    )
    monkeypatch.setattr(
        "abicheck.header_conditionals.attach_build_context",
        lambda *_a, **_k: None,
    )

    # `-DFOO=1` is the legacy match's own derived flag, already folded into
    # `effective_gcc_options` by `dump_cmd`'s `_merge_gcc_options`; `-DUSER=1`
    # is the caller's own `--gcc-options` string.
    effective = " ".join((*legacy_flags, "-DUSER=1")) if legacy_flags else "-DUSER=1"
    perform_elf_dump(
        so_path=so,
        headers=(hdr,),
        includes=(),
        version="1.0",
        lang="c++",
        gcc_path=None,
        gcc_prefix=None,
        effective_gcc_options=effective,
        gcc_option_tokens=(),
        sysroot=None,
        nostdinc=False,
        dwarf_only=False,
        effective_debug_format=None,
        public_headers=(),
        public_header_dirs=(),
        effective_compile_db=None,
        follow_deps=False,
        search_paths=(),
        ld_library_path="",
        git_tag=None,
        build_id=None,
        no_git=False,
        output=None,
        build_info=None,
        sources=tmp_path,
        build_config=None,
        allow_build_query=False,
        collect_mode="source-target",
        expand_header_inputs=lambda inputs: list(inputs),
        populate_dependency_info=lambda *a, **k: None,
        stamp_provenance=lambda *a, **k: None,
        write_snapshot_output=lambda *a, **k: None,
        user_gcc_options="-DUSER=1",
        legacy_build_context_flags=legacy_flags,
    )
    assert "kwargs" in captured, "the L2 seed/fold was never invoked"
    return captured["kwargs"]


def test_perform_elf_dump_keeps_legacy_compile_db_flags_out_of_the_l3_fold(
    tmp_path, monkeypatch
):
    """When the legacy `-p`/`--compile-db` auto-match derived flags from the
    same compile database the P0.3 fold reads, the fold must merge over the
    caller's OWN explicit options only -- otherwise the identical `-D` is
    recorded twice and a derived `-I` suppresses the L2 include seed that
    every other resolver relies on."""
    kwargs = _perform_elf_dump_capturing_fold_options(
        tmp_path, monkeypatch, legacy_flags=("-DFOO=1", "-I/dep/include")
    )
    assert kwargs["gcc_options"] == "-DUSER=1", (
        "the legacy auto-match's derived flags were presented to the P0.3 fold "
        "as explicit caller context"
    )


def test_perform_elf_dump_still_applies_legacy_flags_when_no_fold_ran(
    tmp_path, monkeypatch
):
    """The complement, so the fix cannot be read as "the legacy match is
    gone": with no legacy flags derived at all, the fold sees exactly the
    caller's merged options, bit-for-bit as before."""
    kwargs = _perform_elf_dump_capturing_fold_options(
        tmp_path, monkeypatch, legacy_flags=()
    )
    assert kwargs["gcc_options"] == "-DUSER=1"


def test_perform_elf_dump_forwards_public_roots_to_the_write_time_embed(
    tmp_path, monkeypatch
):
    """The caller half of the write-time L4 public-root fix.

    `_write_snapshot_output` forwards `public_headers`/`public_header_dirs` to
    `embed_build_source` (pinned in `tests/test_dump_embed_idempotence.py`);
    this pins that `perform_elf_dump` actually hands them over rather than
    letting them default to empty, which is what made a real `dump -H api.h
    --depth source` link nothing at L4.
    """
    so = tmp_path / "lib.so"
    hdr = tmp_path / "widget.h"
    hdr.write_text("struct Widget { int x; };\n", encoding="utf-8")
    incdir = tmp_path / "include"
    incdir.mkdir()
    captured: dict = {}

    monkeypatch.setattr(
        "abicheck.cli_dump_helpers.dump",
        lambda **_kw: AbiSnapshot(library="lib.so", version="1.0", from_headers=True),
    )
    monkeypatch.setattr(
        "abicheck.service._attach_header_graph", lambda snap, *_a, **_k: snap
    )
    monkeypatch.setattr(
        "abicheck.header_conditionals.attach_build_context", lambda *_a, **_k: None
    )

    def _capture_write(*_a, **kw):
        captured.update(kw)

    perform_elf_dump(
        so_path=so,
        headers=(hdr,),
        includes=(),
        version="1.0",
        lang="c++",
        gcc_path=None,
        gcc_prefix=None,
        effective_gcc_options=None,
        gcc_option_tokens=(),
        sysroot=None,
        nostdinc=False,
        dwarf_only=False,
        effective_debug_format=None,
        public_headers=(hdr,),
        public_header_dirs=(incdir,),
        effective_compile_db=None,
        follow_deps=False,
        search_paths=(),
        ld_library_path="",
        git_tag=None,
        build_id=None,
        no_git=False,
        output=None,
        build_info=None,
        sources=None,
        build_config=None,
        allow_build_query=False,
        collect_mode="source-target",
        expand_header_inputs=lambda inputs: list(inputs),
        populate_dependency_info=lambda *a, **k: None,
        stamp_provenance=lambda *a, **k: None,
        write_snapshot_output=_capture_write,
    )

    assert captured.get("public_headers") == (hdr,)
    assert captured.get("public_header_dirs") == (incdir,)
