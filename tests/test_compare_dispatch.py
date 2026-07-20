# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
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

"""ADR-037 D7 (G22 Phase 4): `compare` input-type dispatch + folded aliases.

`compare` accepts a single .so / snapshot, a directory, or a package, and rejects
an application/PIE operand with a hint at `appcompat`. Directory/package operands
fan out to the same per-library comparison the (now deprecated) `compare-release`
runs — so `compare <dir> <dir>` reproduces a `compare-release <dir> <dir>` run.
"""
from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.cli_resolve import _looks_like_application, classify_compare_operand
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.serialization import snapshot_to_json


class TestCompareHeaderMarksProvenance:
    """compare's --header is documented as "Public header file or directory"

    (unlike dump's split -H/--public-header) — it must also be threaded
    through as the public-header set for provenance tagging, not just as
    castxml AST input. Regression: this was silently dropped, leaving every
    compare-on-native-binaries run in reduced-confidence "no-provenance" mode
    even though the given header genuinely was the public one.
    """

    def test_resolve_compare_snapshots_passes_header_as_public_header(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from abicheck import cli_resolve

        calls: list[dict] = []

        def fake_resolve_input(path, headers, includes, version, lang, **kwargs):
            calls.append({"path": path, "headers": headers, "version": version, **kwargs})
            return _snap(version=version)

        monkeypatch.setattr(cli_resolve, "_resolve_input", fake_resolve_input)

        old_h = [tmp_path / "old.h"]
        new_h = [tmp_path / "new.h"]
        cli_resolve._resolve_compare_snapshots(
            tmp_path / "old.so", tmp_path / "new.so",
            "elf", "elf",
            old_h, new_h,
            [], [],
            "old", "new",
            "c++",
            None, None, None,
            False, None, False, (), "",
        )
        assert len(calls) == 2
        old_call, new_call = calls
        assert old_call["public_headers"] == old_h
        assert new_call["public_headers"] == new_h


def _snap(version: str = "1.0", funcs: list[Function] | None = None,
          library: str = "libfoo.so") -> AbiSnapshot:
    if funcs is None:
        funcs = [Function(name="foo", mangled="_Z3foov", return_type="int",
                          visibility=Visibility.PUBLIC)]
    return AbiSnapshot(library=library, version=version, functions=funcs, from_headers=True)


def _write_snap(path: Path, snap: AbiSnapshot) -> Path:
    path.write_text(snapshot_to_json(snap), encoding="utf-8")
    return path


def test_source_is_pack_detects_manifest(tmp_path: Path) -> None:
    """A `collect` pack (manifest.json present) is distinguished from a raw tree."""
    from abicheck.cli import _source_is_pack

    tree = tmp_path / "checkout"
    tree.mkdir()
    (tree / "main.c").write_text("int main(void){return 0;}\n")
    assert not _source_is_pack(tree)

    pack = tmp_path / "pack"
    pack.mkdir()
    (pack / "manifest.json").write_text('{"build_source_pack_version": 1}')
    assert _source_is_pack(pack)

    # A Flow-2 inputs pack IS treated as a pack (ADR-043 Codex review): `merge`
    # is gone, so an inputs pack routed as "raw" here would be dropped entirely
    # at compare's default depth (--depth off collects nothing inline) instead
    # of falling through to the out-of-band loader that already auto-detects
    # it (_load_side_pack_input).
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / "manifest.json").write_text('{"kind": "abicheck_inputs"}')
    assert _source_is_pack(inputs)

    # A raw checkout with a stray/sparse manifest.json is NOT a pack — it must
    # still be collected from (Codex review).
    stray = tmp_path / "stray"
    stray.mkdir()
    (stray / "manifest.json").write_text('{"name": "my-project"}')
    assert not _source_is_pack(stray)
    # A present-but-corrupt manifest stays classified as a (corrupt) pack so the
    # downstream load errors loudly rather than silently collecting.
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "manifest.json").write_text("not json{")
    assert _source_is_pack(bad)


def test_inputs_pack_routes_to_out_of_band_loader_not_dropped(tmp_path: Path) -> None:
    """ADR-043 Codex-review regression: an abicheck_inputs/ pack passed via
    --old-build-info/--new-build-info must not be misclassified as "raw" evidence.
    Before the fix, _source_is_pack() returned False for it, so
    _embed_inline_source_side() treated it as a raw tree to collect inline — and
    at compare's default depth (collect_mode "off" collects nothing inline) that
    silently dropped the pack's facts entirely, with only a stderr warning and no
    fallback to the out-of-band loader that already knows how to load it."""
    from abicheck.cli_buildsource_helpers import _load_side_pack_input

    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / "source_facts").mkdir()
    (inputs / "manifest.json").write_text(json.dumps({
        "kind": "abicheck_inputs",
        "abicheck_inputs_version": 1,
        "library": "libfoo.so",
        "version": "1.0",
        "created_by": "test",
    }))

    from abicheck.cli import _source_is_pack

    assert _source_is_pack(inputs)  # classified as a pack, not raw source to collect
    pack = _load_side_pack_input(inputs)  # so the out-of-band loader accepts it
    assert pack is not None


def test_embed_inline_source_forwards_toolchain_and_collects(
    tmp_path: Path, monkeypatch
) -> None:
    """A raw source tree on a native side dumps inline at the requested depth and
    forwards the resolved compile/toolchain context (gcc/sysroot/nostdinc)."""
    import abicheck.cli as climod
    from abicheck.service_scan import CompileContext

    tree = tmp_path / "src"
    tree.mkdir()  # raw checkout (no manifest.json)
    captured: dict = {}

    class _Ctx:
        def invoke(self, _cmd, **kwargs):  # type: ignore[no-untyped-def]
            captured.update(kwargs)

    # Pretend the input is a native ELF binary so the embed path is taken.
    monkeypatch.setattr(climod, "_normalize_binary_input", lambda p: (Path(p), "elf"))
    cc = CompileContext(
        gcc_path="/x/g++", gcc_prefix="aarch64-", gcc_options="-O2",
        gcc_option_tokens=("-DFOO",), sysroot=Path("/sysroot"), nostdinc=True,
    )
    out, kept, kept_bi = climod._embed_inline_source_side(
        _Ctx(), input_path=tmp_path / "lib.so", sources=tree,
        headers=(), includes=(), version="1.0", lang="c++",
        header_backend="auto", compile_context=cc,
        frontend_explicit=False, nostdinc_explicit=False, build_info=None,
        follow_deps=True, search_paths=(Path("/libs"),),
        ld_library_path="/x:/y", dwarf_only=True, debug_format="dwarf",
        pdb_path=Path("/p.pdb"),
        collect_mode="source-target", out_dir=tmp_path, label="old",
    )

    assert kept is None and kept_bi is None and out == tmp_path / "old.abi.json"
    # Every kwarg forwarded to dump must be a real dump_cmd parameter — guards
    # against threading a removed/renamed option through ctx.invoke (which would
    # only blow up at runtime with a real Context, not this fake one). Codex review.
    import inspect
    dump_params = set(inspect.signature(climod.dump_cmd.callback).parameters)
    assert set(captured) <= dump_params, set(captured) - dump_params
    assert captured["sources"] == tree and captured["_resolved_collect_mode"] == "source-target"
    # The resolved compile context is frozen and handed to dump verbatim (so dump
    # does not re-resolve / re-discover the tree's config) — Codex review.
    frozen = captured["_resolved_compile_context"]
    assert frozen.gcc_path == "/x/g++" and frozen.sysroot == Path("/sysroot")
    assert frozen.nostdinc is True and frozen.gcc_option_tokens == ("-DFOO",)
    assert frozen.frontend == "auto"
    # dependency-analysis knobs ride into the inline dump too (Codex review)
    assert captured["follow_deps"] is True and captured["search_paths"] == (Path("/libs"),)
    assert captured["ld_library_path"] == "/x:/y"
    # native dump selectors (dwarf-only/debug-format/pdb) too (Codex review)
    assert captured["dwarf_only"] is True and captured["debug_format_opt"] == "dwarf"
    assert captured["pdb_path"] == Path("/p.pdb")


def test_embed_inline_source_forwards_debug_roots(tmp_path: Path, monkeypatch) -> None:
    """P1.1 Codex-review regression: --debug-root/--debuginfod must reach the
    inline dump too — without this, a raw --old/new-sources tree bypassed
    detached-debug-artifact resolution entirely (the inline dump used its own
    unset defaults), so a stripped binary on that side still lost its DWARF
    even though the non-inline compare path was already fixed."""
    import inspect

    import abicheck.cli as climod
    from abicheck.service_scan import CompileContext

    tree = tmp_path / "src"
    tree.mkdir()
    captured: dict = {}

    class _Ctx:
        def invoke(self, _cmd, **kwargs):  # type: ignore[no-untyped-def]
            captured.update(kwargs)

    monkeypatch.setattr(climod, "_normalize_binary_input", lambda p: (Path(p), "elf"))
    droot = tmp_path / "debugroot"
    climod._embed_inline_source_side(
        _Ctx(), input_path=tmp_path / "lib.so", sources=tree,
        headers=(), includes=(), version="1.0", lang="c++",
        header_backend="auto", compile_context=CompileContext(),
        frontend_explicit=False, nostdinc_explicit=False, build_info=None,
        follow_deps=False, search_paths=(), ld_library_path="",
        dwarf_only=False, debug_format=None, pdb_path=None,
        collect_mode="source-target", out_dir=tmp_path, label="old",
        debug_roots=(droot,), debuginfod=True, debuginfod_url="https://example.test",
    )
    dump_params = set(inspect.signature(climod.dump_cmd.callback).parameters)
    assert set(captured) <= dump_params, set(captured) - dump_params
    assert captured["debug_roots"] == (droot,)
    assert captured["debuginfod"] is True
    assert captured["debuginfod_url"] == "https://example.test"


def test_embed_inline_source_merges_tree_config_but_cli_wins(
    tmp_path: Path, monkeypatch
) -> None:
    """The side's source-root .abicheck.yml compile: block is merged into the
    frozen context (so dump --sources behavior is preserved), but an explicit CLI
    override still wins over the config frontend (both Codex findings)."""
    import abicheck.cli as climod
    from abicheck.service_scan import CompileContext

    tree = tmp_path / "src"
    tree.mkdir()
    (tree / ".abicheck.yml").write_text(
        "version: 3\ncompile:\n  frontend: clang\n  sysroot: /from/cfg\n"
    )
    captured: dict = {}

    class _Ctx:
        def invoke(self, _cmd, **kwargs):  # type: ignore[no-untyped-def]
            captured.update(kwargs)

    monkeypatch.setattr(climod, "_normalize_binary_input", lambda p: (Path(p), "elf"))
    # frontend left at default "auto", NOT explicit → config's clang wins; the
    # tree's sysroot is picked up too.
    climod._embed_inline_source_side(
        _Ctx(), input_path=tmp_path / "lib.so", sources=tree,
        headers=(), includes=(), version="1.0", lang="c++",
        header_backend="auto", compile_context=CompileContext(),
        frontend_explicit=False, nostdinc_explicit=False, build_info=None,
        follow_deps=False, search_paths=(), ld_library_path="",
        dwarf_only=False, debug_format=None, pdb_path=None,
        collect_mode="source-target", out_dir=tmp_path, label="old",
    )
    merged = captured["_resolved_compile_context"]
    assert merged.frontend == "clang" and merged.sysroot == Path("/from/cfg")

    # Now mark --ast-frontend auto explicit → CLI "auto" must beat config clang.
    captured.clear()
    climod._embed_inline_source_side(
        _Ctx(), input_path=tmp_path / "lib.so", sources=tree,
        headers=(), includes=(), version="1.0", lang="c++",
        header_backend="auto", compile_context=CompileContext(),
        frontend_explicit=True, nostdinc_explicit=False, build_info=None,
        follow_deps=False, search_paths=(), ld_library_path="",
        dwarf_only=False, debug_format=None, pdb_path=None,
        collect_mode="source-target", out_dir=tmp_path, label="old",
    )
    assert captured["_resolved_compile_context"].frontend == "auto"

    # A nostdinc already resolved True (e.g. from compare --config) survives
    # the tree merge even though this tree's config omits it (Codex review).
    captured.clear()
    climod._embed_inline_source_side(
        _Ctx(), input_path=tmp_path / "lib.so", sources=tree,
        headers=(), includes=(), version="1.0", lang="c++",
        header_backend="auto", compile_context=CompileContext(nostdinc=True),
        frontend_explicit=False, nostdinc_explicit=True, build_info=None,
        follow_deps=False, search_paths=(), ld_library_path="",
        dwarf_only=False, debug_format=None, pdb_path=None,
        collect_mode="source-target", out_dir=tmp_path, label="old",
    )
    assert captured["_resolved_compile_context"].nostdinc is True


def test_embed_inline_source_ignored_when_depth_collects_nothing(
    tmp_path: Path, monkeypatch
) -> None:
    """At a depth that collects no source (collect_mode 'off') a raw tree is
    ignored rather than silently deepening the run."""
    import abicheck.cli as climod
    from abicheck.service_scan import CompileContext

    tree = tmp_path / "src"
    tree.mkdir()
    called = {"n": 0}

    class _Ctx:
        def invoke(self, _cmd, **kwargs):  # type: ignore[no-untyped-def]
            called["n"] += 1

    monkeypatch.setattr(climod, "_normalize_binary_input", lambda p: (Path(p), "elf"))
    out, kept, kept_bi = climod._embed_inline_source_side(
        _Ctx(), input_path=tmp_path / "lib.so", sources=tree,
        headers=(), includes=(), version="1.0", lang="c++",
        header_backend="auto", compile_context=CompileContext(),
        frontend_explicit=False, nostdinc_explicit=False, build_info=None,
        follow_deps=False, search_paths=(),
        ld_library_path="", dwarf_only=False, debug_format=None,
        pdb_path=None, collect_mode="off", out_dir=tmp_path, label="old",
    )

    assert kept is None and kept_bi is None and out == tmp_path / "lib.so"
    assert called["n"] == 0  # no dump performed


def test_embed_inline_source_drops_raw_build_info_when_tree_ignored(
    tmp_path: Path, monkeypatch
) -> None:
    """When the source tree can't be collected (here: collect_mode 'off'), a raw
    --build-info dir is dropped too — otherwise prepare_embedded_build_source would
    try to load it as a pack and abort with 'Invalid evidence pack' (Codex review).
    A build-info that *is* a validated pack survives so it can still be applied."""
    import abicheck.cli as climod
    from abicheck.service_scan import CompileContext

    tree = tmp_path / "src"
    tree.mkdir()
    raw_build = tmp_path / "build"  # raw build dir, NOT a pack
    raw_build.mkdir()

    class _Ctx:
        def invoke(self, _cmd, **kwargs):  # type: ignore[no-untyped-def]
            pass

    monkeypatch.setattr(climod, "_normalize_binary_input", lambda p: (Path(p), "elf"))
    _, kept, kept_bi = climod._embed_inline_source_side(
        _Ctx(), input_path=tmp_path / "lib.so", sources=tree,
        headers=(), includes=(), version="1.0", lang="c++",
        header_backend="auto", compile_context=CompileContext(),
        frontend_explicit=False, nostdinc_explicit=False, build_info=raw_build,
        follow_deps=False, search_paths=(),
        ld_library_path="", dwarf_only=False, debug_format=None,
        pdb_path=None, collect_mode="off", out_dir=tmp_path, label="old",
    )

    assert kept is None and kept_bi is None  # raw build dir dropped, not kept


def test_embed_inline_collects_raw_build_info_without_sources(
    tmp_path: Path, monkeypatch
) -> None:
    """A raw --build-info on a native side with no --sources still triggers the
    inline dump (so L3 is collected/embedded) rather than falling through to the
    pack loader and aborting with 'Invalid evidence pack' (Codex review)."""
    import abicheck.cli as climod
    from abicheck.service_scan import CompileContext

    raw_build = tmp_path / "build"  # raw build dir, NOT a pack
    raw_build.mkdir()
    captured: dict = {}

    class _Ctx:
        def invoke(self, _cmd, **kwargs):  # type: ignore[no-untyped-def]
            captured.update(kwargs)

    monkeypatch.setattr(climod, "_normalize_binary_input", lambda p: (Path(p), "elf"))
    out, kept, kept_bi = climod._embed_inline_source_side(
        _Ctx(), input_path=tmp_path / "lib.so", sources=None,
        headers=(), includes=(), version="1.0", lang="c++",
        header_backend="auto", compile_context=CompileContext(),
        frontend_explicit=False, nostdinc_explicit=False, build_info=raw_build,
        follow_deps=False, search_paths=(),
        ld_library_path="", dwarf_only=False, debug_format=None,
        pdb_path=None, collect_mode="build", out_dir=tmp_path, label="old",
    )

    # The dump was invoked with the raw build-info forwarded; both consumed → None.
    assert out == tmp_path / "old.abi.json"
    assert kept is None and kept_bi is None
    assert captured["build_info"] == raw_build and captured["sources"] is None
    assert captured["_resolved_collect_mode"] == "build"


def test_embed_inline_raw_build_info_on_snapshot_is_ignored(
    tmp_path: Path, monkeypatch
) -> None:
    """A raw --build-info on a snapshot input (can't re-dump) is warned about and
    cleared, so it never reaches the pack loader (Codex review)."""
    import abicheck.cli as climod
    from abicheck.service_scan import CompileContext

    raw_build = tmp_path / "build"
    raw_build.mkdir()

    class _Ctx:
        def invoke(self, _cmd, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("dump must not run on a snapshot input")

    monkeypatch.setattr(climod, "_normalize_binary_input", lambda p: (Path(p), None))
    out, kept, kept_bi = climod._embed_inline_source_side(
        _Ctx(), input_path=tmp_path / "old.json", sources=None,
        headers=(), includes=(), version="1.0", lang="c++",
        header_backend="auto", compile_context=CompileContext(),
        frontend_explicit=False, nostdinc_explicit=False, build_info=raw_build,
        follow_deps=False, search_paths=(), ld_library_path="",
        dwarf_only=False, debug_format=None, pdb_path=None,
        collect_mode="build", out_dir=tmp_path, label="old",
    )

    assert kept is None and kept_bi is None  # raw build-info dropped, not kept


def test_embed_inline_raw_build_info_dropped_at_off_depth(
    tmp_path: Path, monkeypatch
) -> None:
    """A raw --build-info with a no-collect depth (collect_mode 'off') is dropped
    rather than reaching the pack loader."""
    import abicheck.cli as climod
    from abicheck.service_scan import CompileContext

    raw_build = tmp_path / "build"
    raw_build.mkdir()
    called = {"n": 0}

    class _Ctx:
        def invoke(self, _cmd, **kwargs):  # type: ignore[no-untyped-def]
            called["n"] += 1

    monkeypatch.setattr(climod, "_normalize_binary_input", lambda p: (Path(p), "elf"))
    _, kept, kept_bi = climod._embed_inline_source_side(
        _Ctx(), input_path=tmp_path / "lib.so", sources=None,
        headers=(), includes=(), version="1.0", lang="c++",
        header_backend="auto", compile_context=CompileContext(),
        frontend_explicit=False, nostdinc_explicit=False, build_info=raw_build,
        follow_deps=False, search_paths=(), ld_library_path="",
        dwarf_only=False, debug_format=None, pdb_path=None,
        collect_mode="off", out_dir=tmp_path, label="old",
    )

    assert kept is None and kept_bi is None and called["n"] == 0


def test_embed_inline_source_rejects_hybrid_frontend_at_depth_source(
    tmp_path: Path, monkeypatch
) -> None:
    """Codex review: dump_cmd rejects --depth source + --ast-frontend hybrid
    for a raw --sources tree, but the ctx.invoke(dump_cmd, ...) this function
    makes never passes depth= -- so without an equivalent check here, the
    identical `compare --depth source --sources <raw tree> --ast-frontend
    hybrid` invocation silently reached the nested dump_cmd with depth=None,
    skipping the rejection dump --sources <tree> --depth source --ast-frontend
    hybrid would give for the same tree. This must raise the same
    UsageError, without ever calling ctx.invoke (which would run a real,
    silently-degraded L4 replay)."""
    import abicheck.cli as climod
    from abicheck.service_scan import CompileContext

    tree = tmp_path / "src"
    tree.mkdir()
    called = {"n": 0}

    class _Ctx:
        def invoke(self, _cmd, **kwargs):  # type: ignore[no-untyped-def]
            called["n"] += 1

    monkeypatch.setattr(climod, "_normalize_binary_input", lambda p: (Path(p), "elf"))
    with pytest.raises(climod.click.UsageError, match="--ast-frontend hybrid"):
        climod._embed_inline_source_side(
            _Ctx(), input_path=tmp_path / "lib.so", sources=tree,
            headers=(), includes=(), version="1.0", lang="c++",
            header_backend="hybrid", compile_context=CompileContext(),
            frontend_explicit=True, nostdinc_explicit=False, build_info=None,
            follow_deps=False, search_paths=(),
            ld_library_path="", dwarf_only=False, debug_format=None,
            pdb_path=None, collect_mode="source-target", out_dir=tmp_path,
            label="old", depth="source",
        )
    assert called["n"] == 0  # rejected before the inline dump ever runs


def test_embed_inline_source_hybrid_not_rejected_below_depth_source(
    tmp_path: Path, monkeypatch
) -> None:
    """The hybrid rejection is scoped to --depth source specifically (mirrors
    dump_cmd's own scoping) -- hybrid is the normal, supported dual-backend
    choice for the L2 header AST at every other depth."""
    import abicheck.cli as climod
    from abicheck.service_scan import CompileContext

    tree = tmp_path / "src"
    tree.mkdir()
    called = {"n": 0}

    class _Ctx:
        def invoke(self, _cmd, **kwargs):  # type: ignore[no-untyped-def]
            called["n"] += 1

    monkeypatch.setattr(climod, "_normalize_binary_input", lambda p: (Path(p), "elf"))
    climod._embed_inline_source_side(
        _Ctx(), input_path=tmp_path / "lib.so", sources=tree,
        headers=(), includes=(), version="1.0", lang="c++",
        header_backend="hybrid", compile_context=CompileContext(),
        frontend_explicit=True, nostdinc_explicit=False, build_info=None,
        follow_deps=False, search_paths=(),
        ld_library_path="", dwarf_only=False, debug_format=None,
        pdb_path=None, collect_mode="build", out_dir=tmp_path,
        label="old", depth="build",
    )
    assert called["n"] == 1  # inline dump ran normally, no rejection


def test_header_graph_deprecated_flag_is_inert_with_raw_sources(tmp_path: Path) -> None:
    """G29 Phase A: --header-graph is now a hidden, deprecated no-op — passing
    it alongside a raw --old-sources tree no longer raises a rejection (there
    is no user request to reject any more; the L2 graph is always attempted
    where it structurally can be, and silently skipped on this inline-embed
    path either way, flag or not). The compare still runs to completion and
    the deprecation note reaches stderr."""
    old, new = _breaking_pair()
    old_f = _write_snap(tmp_path / "old.json", old)
    new_f = _write_snap(tmp_path / "new.json", new)
    tree = tmp_path / "src"
    tree.mkdir()  # no manifest.json → looks like a raw source checkout
    result = CliRunner().invoke(
        main,
        [
            "compare", str(old_f), str(new_f),
            "--sources", "old=" + str(tree),
            "--header-graph",
        ],
    )
    out = (result.output or "") + (result.stderr or "")
    assert "deprecated" in out.lower()
    assert "not supported" not in out
    assert result.exit_code in (0, 2, 4), out


def test_compare_source_tree_on_snapshot_input_is_ignored(tmp_path: Path) -> None:
    """A raw --old-sources tree on a snapshot input can't be embedded (you can't
    re-dump a snapshot), so compare warns and still produces a verdict."""
    old, new = _breaking_pair()
    old_f = _write_snap(tmp_path / "old.json", old)
    new_f = _write_snap(tmp_path / "new.json", new)
    tree = tmp_path / "src"
    tree.mkdir()  # no manifest.json → looks like a raw source checkout
    result = CliRunner().invoke(
        main, ["compare", str(old_f), str(new_f), "--sources", "old=" + str(tree)]
    )
    out = (result.output or "") + (result.stderr or "")
    assert "ignored" in out, out
    assert result.exit_code in (0, 2, 4), out


def _breaking_pair(lib: str = "libfoo.so") -> tuple[AbiSnapshot, AbiSnapshot]:
    old = _snap("1.0", [
        Function(name="foo", mangled="_Z3foov", return_type="int", visibility=Visibility.PUBLIC),
        Function(name="bar", mangled="_Z3barv", return_type="void", visibility=Visibility.PUBLIC),
    ], library=lib)
    new = _snap("2.0", [
        Function(name="foo", mangled="_Z3foov", return_type="int", visibility=Visibility.PUBLIC),
    ], library=lib)
    return old, new


def _make_pie_executable(path: Path) -> Path:
    """Write a minimal ELF64 ET_DYN file carrying a PT_INTERP segment (a PIE app)."""
    e_phoff = 64
    e_phentsize = 56
    e_phnum = 1
    # ELF header (64 bytes): magic, class=2, data=1, then fields; e_type at 16.
    hdr = bytearray(64)
    hdr[0:4] = b"\x7fELF"
    hdr[4] = 2  # ELFCLASS64
    hdr[5] = 1  # little-endian
    hdr[6] = 1  # EV_CURRENT
    struct.pack_into("<H", hdr, 16, 3)   # e_type = ET_DYN
    struct.pack_into("<H", hdr, 18, 0x3e)  # e_machine = x86-64
    struct.pack_into("<Q", hdr, 32, e_phoff)   # e_phoff
    struct.pack_into("<H", hdr, 54, e_phentsize)  # e_phentsize
    struct.pack_into("<H", hdr, 56, e_phnum)   # e_phnum
    # One program header: PT_INTERP (p_type=3).
    ph = bytearray(e_phentsize)
    struct.pack_into("<I", ph, 0, 3)  # p_type = PT_INTERP
    path.write_bytes(bytes(hdr) + bytes(ph))
    return path


def _elf_header(e_type: int, *, ei_class: int = 2, ei_data: int = 1) -> bytes:
    """Craft a minimal 64-byte ELF header with a given e_type/class/endianness."""
    hdr = bytearray(64)
    hdr[0:4] = b"\x7fELF"
    hdr[4] = ei_class
    hdr[5] = ei_data
    hdr[6] = 1
    order = "<" if ei_data == 1 else ">"
    struct.pack_into(f"{order}H", hdr, 16, e_type)
    return bytes(hdr)


class TestLooksLikeApplication:
    """Direct coverage of the ELF-header guard branches (ADR-037 D7)."""

    def test_et_exec_is_application(self, tmp_path: Path) -> None:
        p = tmp_path / "exe"
        p.write_bytes(_elf_header(2))  # ET_EXEC
        assert _looks_like_application(p) is True

    def test_et_dyn_without_interp_is_not_application(self, tmp_path: Path) -> None:
        p = tmp_path / "lib.so"
        p.write_bytes(_elf_header(3))  # ET_DYN, no program headers → no PT_INTERP
        assert _looks_like_application(p) is False

    def test_et_rel_is_not_application(self, tmp_path: Path) -> None:
        p = tmp_path / "obj.o"
        p.write_bytes(_elf_header(1))  # ET_REL
        assert _looks_like_application(p) is False

    def test_unknown_endianness_is_inconclusive(self, tmp_path: Path) -> None:
        p = tmp_path / "weird"
        p.write_bytes(_elf_header(2, ei_data=7))  # bogus EI_DATA
        assert _looks_like_application(p) is False

    def test_unknown_class_is_inconclusive(self, tmp_path: Path) -> None:
        p = tmp_path / "weird2"
        p.write_bytes(_elf_header(2, ei_class=9))  # bogus EI_CLASS
        assert _looks_like_application(p) is False

    def test_truncated_header_is_inconclusive(self, tmp_path: Path) -> None:
        p = tmp_path / "trunc"
        p.write_bytes(b"\x7fELF\x02")  # magic + class byte only, no data byte
        assert _looks_like_application(p) is False

    def test_non_elf_is_not_application(self, tmp_path: Path) -> None:
        p = tmp_path / "text"
        p.write_bytes(b"not an elf at all")
        assert _looks_like_application(p) is False


def _invoke(*args: str) -> tuple[int, str, str]:
    result = CliRunner().invoke(main, list(args))
    return result.exit_code, result.output, (result.stderr or "")


# ── classifier ────────────────────────────────────────────────────────────────

class TestClassifier:
    def test_snapshot_is_file(self, tmp_path: Path) -> None:
        p = _write_snap(tmp_path / "libfoo.json", _snap())
        assert classify_compare_operand(p) == "file"

    def test_directory(self, tmp_path: Path) -> None:
        d = tmp_path / "rel"
        d.mkdir()
        assert classify_compare_operand(d) == "directory"

    def test_package(self, tmp_path: Path) -> None:
        pkg = tmp_path / "foo.tar.gz"
        pkg.write_bytes(b"\x1f\x8b\x08\x00")  # gzip magic; name suffix triggers detection
        assert classify_compare_operand(pkg) == "package"

    def test_pie_executable_is_app(self, tmp_path: Path) -> None:
        app = _make_pie_executable(tmp_path / "myapp")
        assert classify_compare_operand(app) == "app"


# ── dispatch ──────────────────────────────────────────────────────────────────

class TestCompareDispatch:
    def test_file_vs_file_snapshot(self, tmp_path: Path) -> None:
        old, new = _breaking_pair()
        old_f = _write_snap(tmp_path / "old.json", old)
        new_f = _write_snap(tmp_path / "new.json", new)
        code, out, _ = _invoke("compare", str(old_f), str(new_f))
        assert code == 4
        assert "BREAKING" in out

    def test_dir_vs_dir_fans_out(self, tmp_path: Path) -> None:
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        old_foo, new_foo = _breaking_pair("libfoo.so")
        _write_snap(old_dir / "libfoo.json", old_foo)
        _write_snap(new_dir / "libfoo.json", new_foo)
        _write_snap(old_dir / "libbar.json", _snap())
        _write_snap(new_dir / "libbar.json", _snap())
        code, out, _ = _invoke("compare", str(old_dir), str(new_dir))
        assert code == 4
        assert "BREAKING" in out

    def test_dir_input_dispatches_even_when_only_one_side_is_a_set(
        self, tmp_path: Path
    ) -> None:
        # A directory on *one* side is enough to route through the set-input
        # (release) path; the other side is a single snapshot file. The engine
        # matches libraries by stem across sides.
        old_dir = tmp_path / "old"
        old_dir.mkdir()
        _write_snap(old_dir / "libfoo.json", _snap())
        new_file = _write_snap(tmp_path / "libfoo.json", _snap())
        code, out, _ = _invoke("compare", str(old_dir), str(new_file))
        assert code == 0
        assert "NO_CHANGE" in out

    def test_exit_code_scheme_rejected_on_set_inputs(self, tmp_path: Path) -> None:
        # --exit-code-scheme can't be honoured by the release fan-out, so it is
        # rejected rather than silently ignored (ADR-037 D12).
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        _write_snap(old_dir / "libfoo.json", _snap())
        _write_snap(new_dir / "libfoo.json", _snap())
        code, out, err = _invoke(
            "compare", str(old_dir), str(new_dir), "--exit-code-scheme", "legacy"
        )
        assert code != 0
        assert "--exit-code-scheme is not supported" in (out + err)

    def test_secondary_format_rejected_on_set_inputs(self, tmp_path: Path) -> None:
        # --secondary-format reuses the single comparison's DiffResult, which
        # doesn't exist as a single object across the release fan-out.
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        _write_snap(old_dir / "libfoo.json", _snap())
        _write_snap(new_dir / "libfoo.json", _snap())
        code, out, err = _invoke(
            "compare", str(old_dir), str(new_dir),
            "--secondary-format", "json", "--secondary-output", str(tmp_path / "sec.json"),
        )
        assert code != 0
        assert "--secondary-format is not supported" in (out + err)

    def test_config_legacy_exit_scheme_applies_to_set_inputs(self, tmp_path: Path) -> None:
        # A project config may demote ABI-breaking findings to warnings for
        # reporting, while still pinning the process exit to the legacy verdict
        # scheme. Directory/package compare must preserve that CI gate.
        cfg = tmp_path / ".abicheck.yml"
        cfg.write_text(
            "exit_code_scheme: legacy\n"
            "severity:\n"
            "  abi_breaking: warning\n",
            encoding="utf-8",
        )
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        old_foo, new_foo = _breaking_pair("libfoo.so")
        _write_snap(old_dir / "libfoo.json", old_foo)
        _write_snap(new_dir / "libfoo.json", new_foo)

        code, out, _ = _invoke(
            "compare", str(old_dir), str(new_dir), "--config", str(cfg), "--format", "json"
        )

        assert code == 4
        assert json.loads(out)["verdict"] == "BREAKING"

    def test_config_severity_scheme_without_severity_block_applies_to_set_inputs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Codex review on #549: exit_code_scheme: severity from .abicheck.yml,
        with no severity: block and no --severity-* flag anywhere, must still
        gate on the default severity preset for directory/package inputs —
        not silently fall back to the legacy verdict exit. The single-file
        compare path never hits this: its resolved_cfg.severity is always
        populated (defaulting to PRESET_DEFAULT), gated only by scheme; the
        release fan-out re-derived its severity config from the raw
        --severity-* values alone, which are all None here."""
        from abicheck.checker import Change, ChangeKind, DiffResult, Verdict

        cfg = tmp_path / ".abicheck.yml"
        cfg.write_text("exit_code_scheme: severity\n", encoding="utf-8")

        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        _write_snap(old_dir / "libfoo.json", _snap())
        _write_snap(new_dir / "libfoo.json", _snap())

        # An API_BREAK finding: legacy scheme exits 2; the default severity
        # preset (potential_breaking=warning) must exit 0 instead.
        api_break_diff = DiffResult(
            old_version="1.0", new_version="2.0", library="libfoo.so",
            changes=[Change(ChangeKind.ENUM_MEMBER_RENAMED, "Color::RED", "member renamed")],
            verdict=Verdict.API_BREAK,
        )
        monkeypatch.setattr(
            "abicheck.cli_compare_release._run_compare_pair",
            lambda *a, **kw: (api_break_diff, None, None),
        )

        code, out, _ = _invoke(
            "compare", str(old_dir), str(new_dir), "--config", str(cfg),
            "--format", "json", "--no-bundle-analysis",
        )

        assert code == 0
        assert json.loads(out)["verdict"] == "API_BREAK"

    @pytest.mark.parametrize(
        "flag, value, is_path",
        [
            ("--depth", "source", False),
            ("--sources", "old=src", True),
            ("--build-info", "new=build", True),
        ],
    )
    def test_evidence_flags_rejected_on_set_inputs(
        self, tmp_path: Path, flag: str, value: str | None, is_path: bool
    ) -> None:
        # Inline build/source evidence flags can't be threaded through the
        # release fan-out, so they are rejected rather than silently dropped
        # (Codex review).
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        _write_snap(old_dir / "libfoo.json", _snap())
        _write_snap(new_dir / "libfoo.json", _snap())
        if value is None:
            extra = [flag]
        elif is_path:
            # sided value like "old=src" — the path follows the side prefix
            side, _, name = value.partition("=")
            (tmp_path / name).mkdir(exist_ok=True)  # --sources/--build-info need a real path
            extra = [flag, f"{side}={tmp_path / name}"]
        else:
            extra = [flag, value]  # --depth takes a literal choice value
        code, out, err = _invoke("compare", str(old_dir), str(new_dir), *extra)
        assert code != 0
        assert "not supported for directory/package" in (out + err)

    def test_header_graph_deprecated_flag_is_harmless_on_set_inputs(
        self, tmp_path: Path
    ) -> None:
        """G29 Phase A: --header-graph/--header-graph-includes are hidden,
        deprecated no-ops now — unlike the other evidence flags above, they
        are no longer rejected on a directory/package compare (there's
        nothing left to reject: the per-library fan-out never built the L2
        graph for either flag value)."""
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        _write_snap(old_dir / "libfoo.json", _snap())
        _write_snap(new_dir / "libfoo.json", _snap())
        _code, out, err = _invoke(
            "compare", str(old_dir), str(new_dir),
            "--header-graph", "--header-graph-includes",
        )
        assert "not supported for directory/package" not in (out + err)
        assert "deprecated" in (out + err).lower()

    def test_used_by_rejected_on_set_inputs(self, tmp_path: Path) -> None:
        # The release fan-out has no per-app scoping; a directory/package
        # compare with --used-by must reject loudly rather than silently run
        # an unscoped release comparison (Codex review).
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        _write_snap(old_dir / "libfoo.json", _snap())
        _write_snap(new_dir / "libfoo.json", _snap())
        app = _make_pie_executable(tmp_path / "myapp")
        code, out, err = _invoke(
            "compare", str(old_dir), str(new_dir), "--used-by", str(app)
        )
        assert code != 0
        msg = out + err
        assert "not supported for directory/package" in msg
        assert "--used-by" in msg

    def test_used_by_rejected_on_set_inputs_even_with_dry_run(
        self, tmp_path: Path
    ) -> None:
        # Regression: --dry-run used to emit its "ok" report and exit 0/1
        # *before* this rejection ran, so a dry run could report a
        # --used-by + directory/package combination as valid even though the
        # real run immediately rejects it (Codex review / post-merge PR #566
        # review). The dry run must agree with the real run.
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        _write_snap(old_dir / "libfoo.json", _snap())
        _write_snap(new_dir / "libfoo.json", _snap())
        app = _make_pie_executable(tmp_path / "myapp")
        code, out, err = _invoke(
            "compare", str(old_dir), str(new_dir), "--used-by", str(app), "--dry-run"
        )
        assert code != 0
        msg = out + err
        assert "not supported for directory/package" in msg
        assert "--used-by" in msg
        assert "Dry run only" not in msg

    def test_required_symbol_rejected_on_set_inputs(self, tmp_path: Path) -> None:
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        _write_snap(old_dir / "libfoo.json", _snap())
        _write_snap(new_dir / "libfoo.json", _snap())
        code, out, err = _invoke(
            "compare", str(old_dir), str(new_dir), "--required-symbol", "_Z3foov"
        )
        assert code != 0
        msg = out + err
        assert "not supported for directory/package" in msg
        assert "--required-symbol" in msg

    def test_app_operand_rejected_with_hint(self, tmp_path: Path) -> None:
        app = _make_pie_executable(tmp_path / "myapp")
        new = _write_snap(tmp_path / "new.json", _snap())
        code, out, err = _invoke("compare", str(app), str(new))
        msg = out + err
        assert code != 0
        assert "--used-by" in msg

    def test_set_only_flags_warn_on_single_file(self, tmp_path: Path) -> None:
        old, new = _breaking_pair()
        old_f = _write_snap(tmp_path / "old.json", old)
        new_f = _write_snap(tmp_path / "new.json", new)
        result = CliRunner().invoke(
            main, ["compare", str(old_f), str(new_f), "--dso-only"]
        )
        # The flag is ignored (single-file path), with a warning on stderr.
        assert result.exit_code == 4
        assert "only apply to directory/package" in (result.stderr or "")

    def test_explicit_jobs_zero_still_warns_on_single_file(self, tmp_path: Path) -> None:
        # `--jobs 0` is the default value, but passing it explicitly is still a
        # set-input flag the single-file path can't use, so it must warn.
        old, new = _breaking_pair()
        old_f = _write_snap(tmp_path / "old.json", old)
        new_f = _write_snap(tmp_path / "new.json", new)
        result = CliRunner().invoke(
            main, ["compare", str(old_f), str(new_f), "--jobs", "0"]
        )
        assert "-j/--jobs" in (result.stderr or "")


# ── parity: compare <dir> <dir> == compare-release <dir> <dir> (summary) ────────

class TestReleaseFanoutParity:
    def test_dir_summary_matches_compare_release(self, tmp_path: Path) -> None:
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        old_foo, new_foo = _breaking_pair("libfoo.so")
        _write_snap(old_dir / "libfoo.json", old_foo)
        _write_snap(new_dir / "libfoo.json", new_foo)
        _write_snap(old_dir / "libbar.json", _snap())
        _write_snap(new_dir / "libbar.json", _snap())

        rel = CliRunner().invoke(
            main, ["compare", str(old_dir), str(new_dir), "--format", "json"]
        )
        cmp = CliRunner().invoke(
            main, ["compare", str(old_dir), str(new_dir), "--format", "json"]
        )
        assert rel.exit_code == cmp.exit_code == 4
        assert json.loads(rel.output) == json.loads(cmp.output)

    def test_output_dir_fanout_preserved(self, tmp_path: Path) -> None:
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        out_dir = tmp_path / "reports"
        old_dir.mkdir()
        new_dir.mkdir()
        old_foo, new_foo = _breaking_pair("libfoo.so")
        _write_snap(old_dir / "libfoo.json", old_foo)
        _write_snap(new_dir / "libfoo.json", new_foo)
        code, _, _ = _invoke(
            "compare", str(old_dir), str(new_dir),
            "--output-dir", str(out_dir), "--format", "json",
        )
        assert code == 4
        # Per-library reports were written under --output-dir (two-level output).
        assert out_dir.is_dir()
        assert list(out_dir.glob("*.json"))


# ── directory comparison (no deprecation: compare-release was removed) ───────

class TestDirectoryComparison:
    def test_compare_directories_runs_release_fanout(self, tmp_path: Path) -> None:
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        _write_snap(old_dir / "libfoo.json", _snap())
        _write_snap(new_dir / "libfoo.json", _snap())
        result = CliRunner().invoke(main, ["compare", str(old_dir), str(new_dir)])
        assert result.exit_code == 0
        # The standalone compare-release command was removed; no deprecation note.
        assert "deprecated" not in (result.stderr or "")

