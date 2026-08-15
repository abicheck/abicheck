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

"""Sibling split of ``test_header_compile_context.py`` (which sits at its
2000-line hard cap) for Finding 3 (P1 review, ``discussion_r3787772668``):
deriving and threading ``CompileContext.gcc_path`` for an MSVC/clang-cl
compile unit, so a retained ``/std:`` survivor is replayed through a
compiler that actually understands MSVC syntax instead of defaulting to
plain ``clang++``.

Covers, in order:

1. ``header_compile_context._derived_gcc_path`` -- the matched compile
   unit's own compiler binary, derived only for a genuinely MSVC/clang-cl
   compile unit, a no-op otherwise.
2. ``service_input_resolution._merge_l3_compile_context``'s "derived leads,
   explicit wins" precedence for ``gcc_path``/``gcc_prefix``.
3. An end-to-end regression through ``service_input_resolution.
   resolve_side_snapshot`` proving the derived ``gcc_path`` actually reaches
   the ``CompileContext`` passed to ``service.resolve_input``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
from abicheck.buildsource.header_compile_context import (
    resolve_header_compile_context,
)
from abicheck.compile_context import CompileContext


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


def test_resolve_derives_gcc_path_for_clang_cl_unit(tmp_path: Path) -> None:
    """P1 review finding (``discussion_r3787772668``): a matched compile
    unit's own retained ``/std:c++20`` survivor is only replayable by a
    compiler that understands MSVC syntax -- the resolved
    :class:`CompileContext` must carry the exact compiler this unit was
    itself invoked with, not leave the caller to default to plain clang++."""
    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src = tmp_path / "widget.cpp"
    src.write_text('#include "widget.h"\n', encoding="utf-8")
    cu = _cu(
        source=str(src),
        directory=str(tmp_path),
        abi_relevant_flags=["/std:c++20"],
        argv=["clang-cl", "/std:c++20", "-c", str(src)],
    )
    ev = BuildEvidence(compile_units=[cu])
    result = resolve_header_compile_context(ev, [header])
    assert result.context is not None
    assert result.context.gcc_path == "clang-cl"


def test_resolve_derives_gcc_path_behind_launcher_wrapper(tmp_path: Path) -> None:
    """P2 review finding (``discussion_r3788073756``): a compiler-cache/
    launcher wrapper (``sccache``, ``ccache``, ``distcc``, ...) commonly
    precedes the real driver in argv. Before the fix, ``_derived_gcc_path``
    blindly returned ``cu.argv[0]`` (the launcher, ``sccache``) -- not a
    clang-family binary, so ``_resolve_clang_bin`` rejected it and fell back
    to plain ``clang++``, which cannot consume the retained ``/std:`` flags."""
    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src = tmp_path / "widget.cpp"
    src.write_text('#include "widget.h"\n', encoding="utf-8")
    cu = _cu(
        source=str(src),
        directory=str(tmp_path),
        abi_relevant_flags=["/std:c++20"],
        argv=["sccache", "clang-cl", "/std:c++20", "/c", str(src)],
    )
    ev = BuildEvidence(compile_units=[cu])
    result = resolve_header_compile_context(ev, [header])
    assert result.context is not None
    assert result.context.gcc_path == "clang-cl"


def test_resolve_does_not_derive_gcc_path_for_non_msvc_unit(tmp_path: Path) -> None:
    """A no-op for the common, non-MSVC case: a plain gcc/clang compile unit
    must not have any gcc_path fabricated for it."""
    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src = tmp_path / "widget.cpp"
    src.write_text('#include "widget.h"\n', encoding="utf-8")
    cu = _cu(
        source=str(src),
        directory=str(tmp_path),
        standard="c++20",
        argv=["g++", "-std=c++20", "-c", str(src)],
    )
    ev = BuildEvidence(compile_units=[cu])
    result = resolve_header_compile_context(ev, [header])
    assert result.context is not None
    assert result.context.gcc_path is None


def test_merge_l3_compile_context_derived_gcc_path_used_when_explicit_unset() -> None:
    """service_input_resolution's merge step actually folds the derived
    gcc_path in when the caller did not already pin one -- without this,
    resolve_header_compile_context's own gcc_path was silently discarded."""
    from abicheck.service_input_resolution import _merge_l3_compile_context

    derived = CompileContext(gcc_option_tokens=("/std:c++20",), gcc_path="clang-cl")
    explicit = CompileContext(gcc_option_tokens=("-DFOO=1",))
    merged = _merge_l3_compile_context(explicit, derived)
    assert merged is not None
    assert merged.gcc_path == "clang-cl"


def test_merge_l3_compile_context_explicit_gcc_path_wins_over_derived() -> None:
    """A caller's own explicit --gcc-path is never overridden by a derived
    one, mirroring every other _ExplicitPin-covered dimension."""
    from abicheck.service_input_resolution import _merge_l3_compile_context

    derived = CompileContext(gcc_option_tokens=("/std:c++20",), gcc_path="clang-cl")
    explicit = CompileContext(gcc_path="/opt/custom/clang-cl")
    merged = _merge_l3_compile_context(explicit, derived)
    assert merged is not None
    assert merged.gcc_path == "/opt/custom/clang-cl"


def test_merge_l3_compile_context_explicit_prefix_only_not_overridden_by_derived_path() -> (
    None
):
    """P2 review finding (``discussion_r3788073754``): ``gcc_path``/
    ``gcc_prefix`` are one logical compiler selector, not two independent
    fields -- ``_resolve_clang_bin`` always checks ``gcc_path`` first. A
    caller who explicitly set ONLY ``gcc_prefix`` (meaning "use this prefix,
    no path override") must not have a *different* derived ``gcc_path``
    merged in for the unset explicit slot, since that derived path would then
    silently win over the caller's actual intent."""
    from abicheck.service_input_resolution import _merge_l3_compile_context

    derived = CompileContext(gcc_option_tokens=("/std:c++20",), gcc_path="clang-cl")
    explicit = CompileContext(gcc_prefix="/opt/llvm/bin/")
    merged = _merge_l3_compile_context(explicit, derived)
    assert merged is not None
    assert merged.gcc_prefix == "/opt/llvm/bin/"
    assert merged.gcc_path is None


def test_merge_l3_compile_context_explicit_path_only_not_paired_with_derived_prefix() -> (
    None
):
    """Companion, opposite direction: an explicit ``gcc_path`` alone must not
    pick up a derived ``gcc_prefix`` either -- the pair is adopted from
    ``derived`` together, or not at all."""
    from abicheck.service_input_resolution import _merge_l3_compile_context

    derived = CompileContext(
        gcc_option_tokens=("/std:c++20",), gcc_prefix="/opt/derived/bin/"
    )
    explicit = CompileContext(gcc_path="/opt/custom/clang-cl")
    merged = _merge_l3_compile_context(explicit, derived)
    assert merged is not None
    assert merged.gcc_path == "/opt/custom/clang-cl"
    assert merged.gcc_prefix is None


def test_merge_l3_compile_context_neither_explicit_adopts_derived_pair_together() -> (
    None
):
    """Companion positive case: when the caller set neither field, the
    derived ``(gcc_path, gcc_prefix)`` pair is adopted together, from the
    same source -- unchanged from the pre-fix behavior for this case."""
    from abicheck.service_input_resolution import _merge_l3_compile_context

    derived = CompileContext(
        gcc_option_tokens=("/std:c++20",),
        gcc_path="clang-cl",
        gcc_prefix="/opt/derived/bin/",
    )
    explicit = CompileContext(gcc_option_tokens=("-DFOO=1",))
    merged = _merge_l3_compile_context(explicit, derived)
    assert merged is not None
    assert merged.gcc_path == "clang-cl"
    assert merged.gcc_prefix == "/opt/derived/bin/"


def test_resolve_side_snapshot_seeds_clang_cl_gcc_path_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end-shaped, mirroring test_resolve_side_snapshot_stamps_parsed_
    with_build_context in test_header_compile_context.py: the derived
    clang-cl gcc_path reaches the actual CompileContext passed to
    service.resolve_input, not just the intermediate
    resolve_header_compile_context result."""
    from abicheck import service_input_resolution as sir
    from abicheck.api_types import InputSpec
    from abicheck.model import AbiSnapshot
    from abicheck.service_compare_evidence import SideEvidence

    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src = tmp_path / "widget.cpp"
    src.write_text('#include "widget.h"\n', encoding="utf-8")
    (tmp_path / "compile_commands.json").write_text(
        json.dumps(
            [
                {
                    "directory": str(tmp_path),
                    "file": str(src),
                    "arguments": [
                        "clang-cl",
                        "/c",
                        str(src),
                        "/std:c++20",
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )

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
    sir.resolve_side_snapshot(
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
    assert compile_ctx.gcc_path == "clang-cl"
    assert "/std:c++20" in compile_ctx.gcc_option_tokens
