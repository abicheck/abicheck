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

"""P0.3: L3 CompileUnit-derived L2 CompileContext (ADR-020a's automatic sibling).

Covers, in order:

1. ``buildsource.header_compile_context.resolve_header_compile_context`` --
   the header<->CompileUnit matching heuristic, single-context flag
   derivation, and the fail-closed ambiguous-context error.
2. ``buildsource.l2_seed.derive_l2_compile_context`` -- the real-filesystem,
   ``collect_inline_pack``-backed sibling of ``derive_l2_include_dirs``.
3. ``service_input_resolution``'s wiring: the derived context folds ahead of
   an explicit one, and ``AbiSnapshot.parsed_with_build_context`` is stamped
   only when a real context was applied.
4. An end-to-end regression (real clang, gated on tool availability) proving
   the "before" state was genuinely wrong (a build-only macro silently
   dropped a field) and that applying L3 evidence fixes it, while the
   existing ``header_parse_context_drift``/``header_build_context_mismatch``
   advisory findings correctly stop firing once context is genuinely applied
   and still fire when it isn't.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
from abicheck.buildsource.header_compile_context import (
    HeaderCompileContextResolution,
    resolve_header_compile_context,
)
from abicheck.compile_context import CompileContext
from abicheck.errors import HeaderCompileContextAmbiguousError

# ---------------------------------------------------------------------------
# 1. resolve_header_compile_context
# ---------------------------------------------------------------------------


def _cu(**kwargs: object) -> CompileUnit:
    defaults: dict[str, object] = dict(
        id=f"cu://{kwargs.get('source', 'x')}",
        source="",
        directory="",
        target_id="",
        compiler="",
        language="CXX",
        standard="",
        defines={},
        undefines=[],
        include_paths=[],
        system_include_paths=[],
        sysroot=None,
        target_triple="",
        abi_relevant_flags=[],
    )
    defaults.update(kwargs)
    return CompileUnit(**defaults)  # type: ignore[arg-type]


def test_resolve_returns_empty_when_no_build_evidence() -> None:
    assert resolve_header_compile_context(None, [Path("x.h")]) == (
        HeaderCompileContextResolution()
    )


def test_resolve_returns_empty_when_no_compile_units() -> None:
    ev = BuildEvidence()
    assert resolve_header_compile_context(ev, [Path("x.h")]).matched is False


def test_resolve_returns_empty_when_no_headers_given() -> None:
    ev = BuildEvidence(compile_units=[_cu()])
    assert resolve_header_compile_context(ev, []).matched is False


def test_resolve_returns_empty_when_no_unit_references_the_header(
    tmp_path: Path,
) -> None:
    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src = tmp_path / "unrelated.cpp"
    src.write_text("int f() { return 0; }\n", encoding="utf-8")
    cu = _cu(source=str(src), directory=str(tmp_path))
    ev = BuildEvidence(compile_units=[cu])
    result = resolve_header_compile_context(ev, [header])
    assert result.matched is False
    assert result.context is None


def test_resolve_derives_context_from_single_matching_unit(tmp_path: Path) -> None:
    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src = tmp_path / "widget.cpp"
    src.write_text('#include "widget.h"\nint f() { return 0; }\n', encoding="utf-8")
    cu = _cu(
        source=str(src),
        directory=str(tmp_path),
        standard="c++20",
        target_triple="x86_64-linux-gnu",
        defines={"WIDGET_EXTRA": "1", "FLAG": ""},
        undefines=["NDEBUG"],
        include_paths=["/opt/inc"],
        system_include_paths=["/opt/sysinc"],
        sysroot="/opt/sysroot",
        abi_relevant_flags=["-fPIC", "-fno-omit-frame-pointer"],
    )
    ev = BuildEvidence(compile_units=[cu])
    result = resolve_header_compile_context(ev, [header])
    assert result.matched is True
    assert result.matched_unit_count == 1
    assert result.context is not None
    tokens = result.context.gcc_option_tokens
    assert "-std=c++20" in tokens
    assert "--target=x86_64-linux-gnu" in tokens
    assert "--sysroot=/opt/sysroot" in tokens
    assert "-DWIDGET_EXTRA=1" in tokens
    assert "-DFLAG" in tokens
    assert "-UNDEBUG" in tokens
    assert "-I" in tokens and "/opt/inc" in tokens
    assert "-isystem" in tokens and "/opt/sysinc" in tokens
    assert "-fPIC" in tokens
    assert "-fno-omit-frame-pointer" in tokens


def test_resolve_matches_by_bare_filename_include(tmp_path: Path) -> None:
    # The header path passed in need not lexically match the #include spelling
    # (e.g. a vendored copy elsewhere on disk) -- filename-suffix matching,
    # mirroring build_context._header_included_by_tu.
    header = tmp_path / "pkg" / "widget.h"
    header.parent.mkdir()
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src = tmp_path / "user.cpp"
    src.write_text('#include "widget.h"\nint f();\n', encoding="utf-8")
    cu = _cu(source=str(src), directory=str(tmp_path), standard="c++17")
    ev = BuildEvidence(compile_units=[cu])
    result = resolve_header_compile_context(ev, [header])
    assert result.matched is True
    assert "-std=c++17" in (result.context.gcc_option_tokens if result.context else ())


def test_resolve_ignores_unreadable_source(tmp_path: Path) -> None:
    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    cu = _cu(source=str(tmp_path / "missing.cpp"), directory=str(tmp_path))
    ev = BuildEvidence(compile_units=[cu])
    assert resolve_header_compile_context(ev, [header]).matched is False


def test_resolve_agreeing_units_apply_one_context(tmp_path: Path) -> None:
    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    srcs = []
    for name in ("a.cpp", "b.cpp"):
        src = tmp_path / name
        src.write_text('#include "widget.h"\n', encoding="utf-8")
        srcs.append(src)
    units = [
        _cu(
            source=str(s), directory=str(tmp_path), standard="c++20", defines={"X": "1"}
        )
        for s in srcs
    ]
    ev = BuildEvidence(compile_units=units)
    result = resolve_header_compile_context(ev, [header])
    assert result.matched is True
    assert result.matched_unit_count == 2
    assert result.context is not None


def test_resolve_disagreeing_units_fail_closed(tmp_path: Path) -> None:
    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src_a = tmp_path / "a.cpp"
    src_a.write_text('#include "widget.h"\n', encoding="utf-8")
    src_b = tmp_path / "b.cpp"
    src_b.write_text('#include "widget.h"\n', encoding="utf-8")
    unit_a = _cu(source=str(src_a), directory=str(tmp_path), standard="c++17")
    unit_b = _cu(source=str(src_b), directory=str(tmp_path), standard="c++20")
    ev = BuildEvidence(compile_units=[unit_a, unit_b])
    with pytest.raises(HeaderCompileContextAmbiguousError) as excinfo:
        resolve_header_compile_context(ev, [header])
    msg = str(excinfo.value)
    assert "widget.h" in msg
    assert "2 materially different" in msg


def test_resolve_disagreeing_abi_flags_fail_closed(tmp_path: Path) -> None:
    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src_a = tmp_path / "a.cpp"
    src_a.write_text('#include "widget.h"\n', encoding="utf-8")
    src_b = tmp_path / "b.cpp"
    src_b.write_text('#include "widget.h"\n', encoding="utf-8")
    unit_a = _cu(
        source=str(src_a), directory=str(tmp_path), abi_relevant_flags=["-fPIC"]
    )
    unit_b = _cu(
        source=str(src_b), directory=str(tmp_path), abi_relevant_flags=["-fno-pic"]
    )
    ev = BuildEvidence(compile_units=[unit_a, unit_b])
    with pytest.raises(HeaderCompileContextAmbiguousError):
        resolve_header_compile_context(ev, [header])


def test_resolve_multiple_headers_union_of_matches(tmp_path: Path) -> None:
    h1 = tmp_path / "a.h"
    h1.write_text("struct A {};\n", encoding="utf-8")
    h2 = tmp_path / "b.h"
    h2.write_text("struct B {};\n", encoding="utf-8")
    src1 = tmp_path / "a.cpp"
    src1.write_text('#include "a.h"\n', encoding="utf-8")
    src2 = tmp_path / "b.cpp"
    src2.write_text('#include "b.h"\n', encoding="utf-8")
    unit1 = _cu(source=str(src1), directory=str(tmp_path), standard="c++20")
    unit2 = _cu(source=str(src2), directory=str(tmp_path), standard="c++20")
    ev = BuildEvidence(compile_units=[unit1, unit2])
    result = resolve_header_compile_context(ev, [h1, h2])
    assert result.matched_unit_count == 2


def test_resolve_expands_redacted_home_relative_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # CompileUnit.source/directory are redacted (home -> "~") for persistence
    # (ADR-032 D7); resolution must expand them back before reading.
    monkeypatch.setenv("HOME", str(tmp_path))
    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src = tmp_path / "widget.cpp"
    src.write_text('#include "widget.h"\n', encoding="utf-8")
    cu = _cu(source="~/widget.cpp", directory="~", standard="c++14")
    ev = BuildEvidence(compile_units=[cu])
    result = resolve_header_compile_context(ev, [header])
    assert result.matched is True


# ---------------------------------------------------------------------------
# 2. buildsource.l2_seed.derive_l2_compile_context
# ---------------------------------------------------------------------------


def _write_compile_db(tmp_path: Path, src: Path, extra_args: list[str]) -> None:
    (tmp_path / "compile_commands.json").write_text(
        json.dumps(
            [
                {
                    "directory": str(tmp_path),
                    "file": str(src),
                    "arguments": ["c++", "-c", str(src), "-o", "out.o", *extra_args],
                }
            ]
        ),
        encoding="utf-8",
    )


def test_derive_l2_compile_context_from_compile_db(tmp_path: Path) -> None:
    from abicheck.buildsource.l2_seed import derive_l2_compile_context

    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src = tmp_path / "widget.cpp"
    src.write_text('#include "widget.h"\n', encoding="utf-8")
    _write_compile_db(
        tmp_path, src, ["-std=c++20", "-DFOO=1", "-fPIC", "-fno-omit-frame-pointer"]
    )

    ctx, cleanups = derive_l2_compile_context([header], None, tmp_path)
    try:
        assert ctx is not None
        assert "-std=c++20" in ctx.gcc_option_tokens
        assert "-DFOO=1" in ctx.gcc_option_tokens
        assert "-fPIC" in ctx.gcc_option_tokens
        assert "-fno-omit-frame-pointer" in ctx.gcc_option_tokens
    finally:
        from abicheck.buildsource.inline import _run_cleanups

        _run_cleanups(cleanups)


def test_derive_l2_compile_context_no_inputs_is_none() -> None:
    from abicheck.buildsource.l2_seed import derive_l2_compile_context

    assert derive_l2_compile_context([Path("x.h")], None, None) == (None, [])


def test_derive_l2_compile_context_no_headers_is_none(tmp_path: Path) -> None:
    from abicheck.buildsource.l2_seed import derive_l2_compile_context

    assert derive_l2_compile_context([], None, tmp_path) == (None, [])


def test_derive_l2_compile_context_no_match_returns_none(tmp_path: Path) -> None:
    from abicheck.buildsource.l2_seed import derive_l2_compile_context

    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src = tmp_path / "unrelated.cpp"
    src.write_text("int f() { return 0; }\n", encoding="utf-8")
    _write_compile_db(tmp_path, src, ["-std=c++20"])

    ctx, cleanups = derive_l2_compile_context([header], None, tmp_path)
    assert ctx is None
    assert cleanups == []


def test_derive_l2_compile_context_ambiguous_raises_and_drains_cleanups(
    tmp_path: Path,
) -> None:
    from abicheck.buildsource.l2_seed import derive_l2_compile_context

    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src_a = tmp_path / "a.cpp"
    src_a.write_text('#include "widget.h"\n', encoding="utf-8")
    src_b = tmp_path / "b.cpp"
    src_b.write_text('#include "widget.h"\n', encoding="utf-8")
    (tmp_path / "compile_commands.json").write_text(
        json.dumps(
            [
                {
                    "directory": str(tmp_path),
                    "file": str(src_a),
                    "arguments": ["c++", "-c", str(src_a), "-std=c++17"],
                },
                {
                    "directory": str(tmp_path),
                    "file": str(src_b),
                    "arguments": ["c++", "-c", str(src_b), "-std=c++20"],
                },
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(HeaderCompileContextAmbiguousError):
        derive_l2_compile_context([header], None, tmp_path)


def test_derive_l2_compile_context_swallows_non_ambiguous_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Any *other* collection failure stays best-effort (mirrors
    # derive_l2_include_dirs's own `except Exception -> ([], [])` contract).
    from abicheck.buildsource import l2_seed

    def _boom(*a: object, **k: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(l2_seed, "collect_inline_pack", _boom)
    ctx, cleanups = l2_seed.derive_l2_compile_context([Path("x.h")], None, tmp_path)
    assert (ctx, cleanups) == (None, [])


# ---------------------------------------------------------------------------
# 3. service_input_resolution wiring
# ---------------------------------------------------------------------------


def test_merge_l3_compile_context_derived_leads_explicit_wins() -> None:
    from abicheck.service_input_resolution import _merge_l3_compile_context

    derived = CompileContext(gcc_option_tokens=("-DFOO=1", "-fPIC"))
    explicit = CompileContext(gcc_option_tokens=("-DFOO=2",), sysroot=Path("/x"))
    merged = _merge_l3_compile_context(explicit, derived)
    assert merged is not None
    assert merged.gcc_option_tokens == ("-DFOO=1", "-fPIC", "-DFOO=2")
    assert merged.sysroot == Path("/x")  # explicit-only fields preserved


def test_merge_l3_compile_context_none_derived_is_noop() -> None:
    from abicheck.service_input_resolution import _merge_l3_compile_context

    explicit = CompileContext(gcc_options="-DX=1")
    assert _merge_l3_compile_context(explicit, None) is explicit


def test_merge_l3_compile_context_none_explicit_uses_derived() -> None:
    from abicheck.service_input_resolution import _merge_l3_compile_context

    derived = CompileContext(gcc_option_tokens=("-std=c++20",))
    assert _merge_l3_compile_context(None, derived) is derived


def test_seeded_compile_context_noop_without_sources(tmp_path: Path) -> None:
    from abicheck.api_types import InputSpec
    from abicheck.service_compare_evidence import SideEvidence
    from abicheck.service_input_resolution import _seeded_compile_context

    side = InputSpec(path=tmp_path / "lib.so", headers=(tmp_path / "h.h",))
    evidence = SideEvidence(
        headers=[tmp_path / "h.h"], compile=None, collect_mode="off", dump_manifest=None
    )
    ctx, applied, cleanups = _seeded_compile_context(side, evidence)
    assert (ctx, applied, cleanups) == (None, False, [])


def test_resolve_side_snapshot_stamps_parsed_with_build_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Wiring test: derived L3 context reaches ``service.resolve_input`` and
    ``AbiSnapshot.parsed_with_build_context`` is stamped when it does."""
    from abicheck import service_input_resolution as sir
    from abicheck.api_types import InputSpec
    from abicheck.model import AbiSnapshot
    from abicheck.service_compare_evidence import SideEvidence

    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src = tmp_path / "widget.cpp"
    src.write_text('#include "widget.h"\n', encoding="utf-8")
    _write_compile_db(tmp_path, src, ["-std=c++20", "-fPIC"])

    so = tmp_path / "lib.so"
    so.write_bytes(b"\x7fELF" + b"\x00" * 100)

    captured: dict[str, object] = {}

    def _fake_resolve_input(*args: object, **kwargs: object) -> AbiSnapshot:
        captured.update(kwargs)
        return AbiSnapshot(library="lib", version="1.0", from_headers=True)

    import abicheck.service as service_mod

    monkeypatch.setattr(service_mod, "resolve_input", _fake_resolve_input)

    side = InputSpec(path=so, headers=(header,), version="1.0", sources=tmp_path)
    evidence = SideEvidence(
        headers=[header], compile=None, collect_mode="off", dump_manifest=None
    )
    snap = sir.resolve_side_snapshot(
        side,
        evidence,
        lang="c++",
        header_backend="clang",
        fmt="elf",
        public_headers=[],
        public_header_dirs=[],
    )
    compile_ctx = captured["compile"]
    assert isinstance(compile_ctx, CompileContext)
    assert "-std=c++20" in compile_ctx.gcc_option_tokens
    assert "-fPIC" in compile_ctx.gcc_option_tokens
    assert snap.parsed_with_build_context is True


def test_resolve_side_snapshot_does_not_stamp_when_unmatched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from abicheck import service_input_resolution as sir
    from abicheck.api_types import InputSpec
    from abicheck.model import AbiSnapshot
    from abicheck.service_compare_evidence import SideEvidence

    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src = tmp_path / "unrelated.cpp"
    src.write_text("int f() { return 0; }\n", encoding="utf-8")
    _write_compile_db(tmp_path, src, ["-std=c++20"])

    so = tmp_path / "lib.so"
    so.write_bytes(b"\x7fELF" + b"\x00" * 100)

    def _fake_resolve_input(*args: object, **kwargs: object) -> AbiSnapshot:
        return AbiSnapshot(library="lib", version="1.0", from_headers=True)

    import abicheck.service as service_mod

    monkeypatch.setattr(service_mod, "resolve_input", _fake_resolve_input)

    side = InputSpec(path=so, headers=(header,), version="1.0", sources=tmp_path)
    evidence = SideEvidence(
        headers=[header], compile=None, collect_mode="off", dump_manifest=None
    )
    snap = sir.resolve_side_snapshot(
        side,
        evidence,
        lang="c++",
        header_backend="clang",
        fmt="elf",
        public_headers=[],
        public_header_dirs=[],
    )
    assert snap.parsed_with_build_context is False


def test_resolve_side_snapshot_propagates_ambiguous_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from abicheck import service_input_resolution as sir
    from abicheck.api_types import InputSpec
    from abicheck.model import AbiSnapshot
    from abicheck.service_compare_evidence import SideEvidence

    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src_a = tmp_path / "a.cpp"
    src_a.write_text('#include "widget.h"\n', encoding="utf-8")
    src_b = tmp_path / "b.cpp"
    src_b.write_text('#include "widget.h"\n', encoding="utf-8")
    (tmp_path / "compile_commands.json").write_text(
        json.dumps(
            [
                {
                    "directory": str(tmp_path),
                    "file": str(src_a),
                    "arguments": ["c++", "-c", str(src_a), "-std=c++17"],
                },
                {
                    "directory": str(tmp_path),
                    "file": str(src_b),
                    "arguments": ["c++", "-c", str(src_b), "-std=c++20"],
                },
            ]
        ),
        encoding="utf-8",
    )
    so = tmp_path / "lib.so"
    so.write_bytes(b"\x7fELF" + b"\x00" * 100)

    def _fake_resolve_input(*args: object, **kwargs: object) -> AbiSnapshot:
        raise AssertionError("must not be reached: ambiguity fails closed first")

    import abicheck.service as service_mod

    monkeypatch.setattr(service_mod, "resolve_input", _fake_resolve_input)

    side = InputSpec(path=so, headers=(header,), version="1.0", sources=tmp_path)
    evidence = SideEvidence(
        headers=[header], compile=None, collect_mode="off", dump_manifest=None
    )
    with pytest.raises(HeaderCompileContextAmbiguousError):
        sir.resolve_side_snapshot(
            side,
            evidence,
            lang="c++",
            header_backend="clang",
            fmt="elf",
            public_headers=[],
            public_header_dirs=[],
        )


# ---------------------------------------------------------------------------
# 4. End-to-end (real clang): macro-gated field, before/after, drift finding
# ---------------------------------------------------------------------------

_HEADER = """
#pragma once
struct Widget {
    int x;
#ifdef WIDGET_EXTRA
    int y;
#endif
};
int touch(Widget* w);
"""

_SOURCE = """
#include "widget.h"
int touch(Widget* w) { return w->x; }
"""


def _have(tool: str) -> bool:
    return shutil.which(tool) is not None


@pytest.fixture
def widget_lib(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Build a real .so (compiled *with* WIDGET_EXTRA, so its real ABI has the
    extra field) plus its public header and a compile_commands.json recording
    that same real -DWIDGET_EXTRA=1 (+ two ABI-relevant flags)."""
    if not (_have("clang") and _have("g++")):
        pytest.skip("clang and g++ are required for this P0.3 integration test")
    header = tmp_path / "widget.h"
    header.write_text(_HEADER, encoding="utf-8")
    src = tmp_path / "widget.cpp"
    src.write_text(_SOURCE, encoding="utf-8")
    so = tmp_path / "libwidget.so"
    subprocess.run(
        [
            "g++",
            "-shared",
            "-fPIC",
            "-fno-omit-frame-pointer",
            "-DWIDGET_EXTRA=1",
            "-o",
            str(so),
            str(src),
            f"-I{tmp_path}",
        ],
        check=True,
        capture_output=True,
    )
    (tmp_path / "compile_commands.json").write_text(
        json.dumps(
            [
                {
                    "directory": str(tmp_path),
                    "file": str(src),
                    "arguments": [
                        "g++",
                        "-c",
                        str(src),
                        "-o",
                        "widget.o",
                        "-DWIDGET_EXTRA=1",
                        "-fPIC",
                        "-fno-omit-frame-pointer",
                        "-std=c++17",
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )
    return so, header, tmp_path


pytestmark_e2e = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="P0.3 end-to-end test is ELF/Linux-scoped (mirrors "
    "test_clang_header_backend_integration.py)",
)


def _widget_fields(snap: object) -> list[str]:
    widget = next(t for t in snap.types if t.name == "Widget")  # type: ignore[attr-defined]
    return [f.name for f in widget.fields]


@pytestmark_e2e
def test_e2e_without_context_regresses_to_pre_p03_behavior(
    widget_lib: tuple[Path, Path, Path],
) -> None:
    """Regression-shaped: proves the "before" state is genuinely wrong -- a
    header parsed with no build context silently drops the build-only field,
    diverging from the library's real (compiled-in) ABI."""
    from abicheck import service
    from abicheck.api_types import DumpRequest, InputSpec

    so, header, _src_dir = widget_lib
    req = DumpRequest(
        input=InputSpec(path=so, headers=(header,), version="1.0"),
        frontend="clang",
        lang_explicit=True,
    )
    snap = service.run_dump_request(req)
    assert snap.from_headers is True
    assert snap.parsed_with_build_context is False
    assert _widget_fields(snap) == ["x"]  # "y" silently missing: the bug
    # Fixed by supplying `sources=` (this pass's actual wiring) -- see
    # test_e2e_with_l3_evidence_context_is_genuinely_applied below.


@pytestmark_e2e
def test_e2e_with_l3_evidence_context_is_genuinely_applied(
    widget_lib: tuple[Path, Path, Path],
) -> None:
    """The real fix: with L3 evidence available, the field the build actually
    compiles in is now present, and parsed_with_build_context is stamped."""
    from abicheck import service
    from abicheck.api_types import DumpRequest, InputSpec

    so, header, src_dir = widget_lib
    req = DumpRequest(
        input=InputSpec(path=so, headers=(header,), version="1.0", sources=src_dir),
        frontend="clang",
        lang_explicit=True,
    )
    snap = service.run_dump_request(req)
    assert snap.from_headers is True
    assert snap.parsed_with_build_context is True
    assert _widget_fields(snap) == ["x", "y"]  # fixed: matches the real ABI


@pytestmark_e2e
def test_e2e_header_parse_context_drift_stops_firing_once_applied(
    widget_lib: tuple[Path, Path, Path],
) -> None:
    from abicheck import service
    from abicheck.api_types import DumpRequest, InputSpec
    from abicheck.buildsource.build_diff import check_header_parse_drift

    so, header, src_dir = widget_lib

    without_ctx = service.run_dump_request(
        DumpRequest(
            input=InputSpec(path=so, headers=(header,), version="1.0"),
            frontend="clang",
            lang_explicit=True,
        )
    )
    with_ctx = service.run_dump_request(
        DumpRequest(
            input=InputSpec(path=so, headers=(header,), version="1.0", sources=src_dir),
            frontend="clang",
            lang_explicit=True,
        )
    )
    assert with_ctx.build_source is not None
    build = with_ctx.build_source.build_evidence
    assert build is not None
    abi_flags = {opt.key for opt in build.build_options if opt.abi_relevant}
    assert abi_flags  # sanity: the fixture's -fPIC/-fno-omit-frame-pointer register

    # Without context: still fires (unchanged pre-P0.3 behavior).
    assert check_header_parse_drift(
        build, headers_parsed_with_context=without_ctx.parsed_with_build_context
    )
    # With context genuinely applied: stops firing.
    assert (
        check_header_parse_drift(
            build, headers_parsed_with_context=with_ctx.parsed_with_build_context
        )
        == []
    )


@pytestmark_e2e
def test_e2e_crosscheck_header_build_context_mismatch_stops_firing(
    widget_lib: tuple[Path, Path, Path],
) -> None:
    """The sibling crosscheck finding (``header_build_context_mismatch``) keys
    off the identical ``parsed_with_build_context`` flag -- confirm P0.3's fix
    closes that advisory too, not just ``header_parse_context_drift``.

    Unlike ``header_parse_context_drift`` (a *compare*-time, cross-snapshot
    finding), this crosscheck runs over ONE artifact and needs that artifact's
    *own* embedded ``build_source`` to know a build exists at all -- a
    snapshot dumped with no ``sources=`` carries no build evidence whatsoever
    and correctly *skips* (not "fires"), since it has no way to know what it's
    missing. So the real regression check here isolates the one variable P0.3
    actually controls (``parsed_with_build_context``) on an otherwise-identical
    snapshot that *does* carry the build evidence — rather than comparing two
    snapshots that also differ in whether build evidence exists at all.
    """
    import copy

    from abicheck import service
    from abicheck.api_types import DumpRequest, InputSpec
    from abicheck.buildsource.crosscheck import run_crosschecks
    from abicheck.checker_policy import ChangeKind

    so, header, src_dir = widget_lib

    def _fires(snap: object) -> bool:
        result = run_crosschecks(snap)  # type: ignore[arg-type]
        return any(
            c.kind == ChangeKind.HEADER_BUILD_CONTEXT_MISMATCH for c in result.findings
        )

    with_ctx = service.run_dump_request(
        DumpRequest(
            input=InputSpec(path=so, headers=(header,), version="1.0", sources=src_dir),
            frontend="clang",
            lang_explicit=True,
        )
    )
    assert with_ctx.parsed_with_build_context is True
    assert _fires(with_ctx) is False

    stale = copy.deepcopy(with_ctx)
    stale.parsed_with_build_context = False
    assert _fires(stale) is True
