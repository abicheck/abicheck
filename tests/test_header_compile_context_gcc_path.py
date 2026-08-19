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
2. ``buildsource.l2_seed._merge_l3_compile_context``'s "derived leads,
   explicit wins" precedence for ``gcc_path``/``gcc_prefix`` (moved there
   from ``service_input_resolution`` by the ``main``-side PR C dedup this
   branch merged against -- see that function's own docstring).
3. An end-to-end regression through ``service_input_resolution.
   resolve_side_snapshot`` proving the derived ``gcc_path`` actually reaches
   the ``CompileContext`` passed to ``service.resolve_input``.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
from abicheck.buildsource.header_compile_context import (
    resolve_header_compile_context,
)
from abicheck.compile_context import CompileContext
from abicheck.errors import HeaderCompileContextAmbiguousError


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


def test_resolve_derives_gcc_path_behind_launcher_wrapper_generic_driver_name(
    tmp_path: Path,
) -> None:
    """Round 23, Finding 5 (Codex, ``header_compile_context.py:1492``): a
    GENERIC (non-CL-named) driver behind a launcher, MSVC-dialect detected
    only via ``--driver-mode=cl`` (not a ``cl``/``clang-cl``-shaped
    basename). ``msvc_driver_token`` correctly returns ``None`` here (the
    basename ``clang`` isn't CL-style), but the fallback was ``cu.argv[0]``
    (the launcher itself, ``sccache``) instead of unwrapping the launcher
    to find the real driver -- the exact same failure class the CL-named
    case above was already fixed for, just reached through the
    ``driver is None`` fallback branch instead."""
    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src = tmp_path / "widget.cpp"
    src.write_text('#include "widget.h"\n', encoding="utf-8")
    cu = _cu(
        source=str(src),
        directory=str(tmp_path),
        abi_relevant_flags=["/std:c++20"],
        argv=[
            "sccache",
            "/opt/llvm/bin/clang",
            "--driver-mode=cl",
            "/c",
            str(src),
        ],
    )
    ev = BuildEvidence(compile_units=[cu])
    result = resolve_header_compile_context(ev, [header])
    assert result.context is not None
    assert result.context.gcc_path == "/opt/llvm/bin/clang"


def test_resolve_derives_gcc_path_negative_control_bare_slash_c_no_driver_at_all(
    tmp_path: Path,
) -> None:
    """Negative control for Finding 5: when there is truly no driver token
    anywhere (a bare ``/c`` marker with nothing else in argv), the
    ``strip_launchers``-based fallback also finds nothing (an empty or
    flag-shaped stripped result), and ``_derived_gcc_path`` falls all the
    way back to raw ``cu.argv[0]`` -- unchanged from the pre-fix
    behavior for this degenerate case."""
    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src = tmp_path / "widget.cpp"
    src.write_text('#include "widget.h"\n', encoding="utf-8")
    cu = _cu(
        source=str(src),
        directory=str(tmp_path),
        abi_relevant_flags=["/std:c++20"],
        argv=["/c", str(src)],
    )
    ev = BuildEvidence(compile_units=[cu])
    result = resolve_header_compile_context(ev, [header])
    assert result.context is not None
    assert result.context.gcc_path == "/c"


def test_resolve_derives_gcc_path_resolves_relative_driver_against_cu_directory(
    tmp_path: Path,
) -> None:
    """P2 review finding (path-bearing driver token follow-up): a relative
    driver path like ``../llvm/bin/clang-cl`` is only meaningful relative to
    the compile unit's own ``directory`` -- resolving it against abicheck's
    own CWD (the pre-fix behavior) reports a genuinely executable compiler
    as missing.

    The joined path is also normalized (a later P2 review finding,
    "Normalize resolved driver paths before grouping") -- ``os.path.
    normpath`` collapses the ``build/..`` segment rather than leaving it in
    the returned string verbatim, so this asserts against ``tmp_path /
    "llvm/bin/clang-cl"`` (the canonical form), not the raw, unnormalized
    join. See ``test_resolve_derives_gcc_path_normalizes_dotdot_segments_for_
    ambiguity_grouping`` below for the ambiguity-grouping regression this
    normalization exists to fix."""
    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src = tmp_path / "widget.cpp"
    src.write_text('#include "widget.h"\n', encoding="utf-8")
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    cu = _cu(
        source=str(src),
        directory=str(build_dir),
        abi_relevant_flags=["/std:c++20"],
        argv=["../llvm/bin/clang-cl", "/std:c++20", "-c", str(src)],
    )
    ev = BuildEvidence(compile_units=[cu])
    result = resolve_header_compile_context(ev, [header])
    assert result.context is not None
    assert result.context.gcc_path == str(tmp_path / "llvm" / "bin" / "clang-cl")


def test_resolve_derives_gcc_path_resolves_relative_driver_via_env_chdir(
    tmp_path: Path,
) -> None:
    """P2 review round 19 Finding 1 (``_argv.py:453``): ``env -C DIR``
    changes the EFFECTIVE working directory the driver runs from, so a
    relative driver token following it is relative to
    ``<cu.directory>/DIR``, not bare ``cu.directory``. Before the fix,
    ``strip_launchers`` recognized and discarded ``-C build`` as a cosmetic
    prefix, and ``_derived_gcc_path`` resolved ``../llvm/bin/clang-cl``
    straight against ``cu.directory`` (``tmp_path``), landing one directory
    too high (``tmp_path.parent / "llvm/bin/clang-cl"``) instead of the
    correct ``tmp_path / "llvm/bin/clang-cl"``."""
    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src = tmp_path / "widget.cpp"
    src.write_text('#include "widget.h"\n', encoding="utf-8")
    cu = _cu(
        source=str(src),
        directory=str(tmp_path),
        abi_relevant_flags=["/std:c++20"],
        argv=[
            "env",
            "-C",
            "build",
            "../llvm/bin/clang-cl",
            "/std:c++20",
            "-c",
            str(src),
        ],
    )
    ev = BuildEvidence(compile_units=[cu])
    result = resolve_header_compile_context(ev, [header])
    assert result.context is not None
    assert result.context.gcc_path == str(tmp_path / "llvm" / "bin" / "clang-cl")


def test_resolve_derives_gcc_path_resolves_bare_driver_via_env_path(
    tmp_path: Path,
) -> None:
    """P2 review round 19 Finding 2 (``_argv.py:459``): ``env PATH=...``
    scopes a PATH override to the launched command, so a bare driver name
    that only exists on that overridden PATH (not abicheck's own inherited
    PATH) must still resolve. Before the fix, ``strip_launchers`` discarded
    the ``PATH=...`` assignment along with every other skipped token,
    leaving the bare ``clang-cl`` name unresolved -- ``_resolve_clang_bin``
    would report it missing when it isn't on the *inherited* PATH."""
    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src = tmp_path / "widget.cpp"
    src.write_text('#include "widget.h"\n', encoding="utf-8")
    bin_dir = tmp_path / "opt_llvm_bin"
    bin_dir.mkdir()
    driver = bin_dir / "clang-cl"
    # `shutil.which` consults PATHEXT on Windows rather than an exact
    # bare-name match, so an extensionless fixture is never found there
    # (CodeRabbit review, fresh evidence) -- create `clang-cl.exe` on
    # Windows and assert against *that* path, while the compiler token in
    # `argv` below stays the bare, extensionless "clang-cl" (Windows'
    # PATHEXT resolution finds the `.exe` file when searching for it).
    if sys.platform == "win32":
        driver = driver.with_suffix(".exe")
    driver.write_text("", encoding="utf-8")
    driver.chmod(0o755)
    cu = _cu(
        source=str(src),
        directory=str(tmp_path),
        abi_relevant_flags=["/std:c++20"],
        argv=[
            "env",
            f"PATH={bin_dir}",
            "clang-cl",
            "/std:c++20",
            "-c",
            str(src),
        ],
    )
    ev = BuildEvidence(compile_units=[cu])
    result = resolve_header_compile_context(ev, [header])
    assert result.context is not None
    # `shutil.which` on Windows returns the resolved path with PATHEXT's own
    # extension casing (commonly uppercase `.EXE`), not necessarily matching
    # the fixture file's own on-disk casing -- both name the same file on a
    # case-insensitive filesystem (live Windows CI signal, fresh evidence).
    # `os.path.normcase` is a no-op on POSIX, so this stays exact there.
    assert os.path.normcase(result.context.gcc_path) == os.path.normcase(str(driver))


def test_resolve_derives_gcc_path_bare_driver_unresolvable_env_path_left_bare(
    tmp_path: Path,
) -> None:
    """Negative case: when the env-supplied PATH doesn't actually contain
    the named driver, the bare name is left unchanged (the pre-fix
    behavior) rather than raising or fabricating a path."""
    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src = tmp_path / "widget.cpp"
    src.write_text('#include "widget.h"\n', encoding="utf-8")
    cu = _cu(
        source=str(src),
        directory=str(tmp_path),
        abi_relevant_flags=["/std:c++20"],
        argv=[
            "env",
            "PATH=/definitely/does/not/exist",
            "clang-cl",
            "/std:c++20",
            "-c",
            str(src),
        ],
    )
    ev = BuildEvidence(compile_units=[cu])
    result = resolve_header_compile_context(ev, [header])
    assert result.context is not None
    assert result.context.gcc_path == "clang-cl"


def test_resolve_derives_gcc_path_expands_home_redacted_driver_token(
    tmp_path: Path,
) -> None:
    """P2 review finding: an evidence adapter's own home-redaction (ADR-032
    D7, ``~/llvm/bin/clang-cl``) must be expanded, not returned verbatim --
    ``shutil.which("~/llvm/bin/clang-cl")`` never resolves a literal tilde."""
    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src = tmp_path / "widget.cpp"
    src.write_text('#include "widget.h"\n', encoding="utf-8")
    cu = _cu(
        source=str(src),
        directory=str(tmp_path),
        abi_relevant_flags=["/std:c++20"],
        argv=["~/llvm/bin/clang-cl", "/std:c++20", "-c", str(src)],
    )
    ev = BuildEvidence(compile_units=[cu])
    result = resolve_header_compile_context(ev, [header])
    assert result.context is not None
    assert result.context.gcc_path is not None
    assert "~" not in result.context.gcc_path
    # Host-independent suffix check: `~` expansion substitutes the real,
    # host-native home directory, so on Windows the normalized result is
    # backslash-separated (e.g. "C:\\Users\\me\\llvm\\bin\\clang-cl")
    # rather than the POSIX-style "llvm/bin/clang-cl" this assertion
    # originally hardcoded (Windows CI, fresh evidence).
    assert result.context.gcc_path.replace("\\", "/").endswith("llvm/bin/clang-cl")


def test_resolve_derives_gcc_path_leaves_bare_path_name_unchanged(
    tmp_path: Path,
) -> None:
    """A bare PATH name (no separator at all) must be left unchanged -- it is
    looked up on PATH, not resolved against the compile unit's directory."""
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


def test_resolve_derives_gcc_path_absolute_driver_left_absolute(
    tmp_path: Path,
) -> None:
    """An already-absolute driver path must not be joined onto ``directory``
    a second time."""
    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src = tmp_path / "widget.cpp"
    src.write_text('#include "widget.h"\n', encoding="utf-8")
    cu = _cu(
        source=str(src),
        directory=str(tmp_path),
        abi_relevant_flags=["/std:c++20"],
        argv=["/opt/llvm/bin/clang-cl", "/std:c++20", "-c", str(src)],
    )
    ev = BuildEvidence(compile_units=[cu])
    result = resolve_header_compile_context(ev, [header])
    assert result.context is not None
    assert result.context.gcc_path == "/opt/llvm/bin/clang-cl"


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
    from abicheck.buildsource.l2_seed import _merge_l3_compile_context

    derived = CompileContext(gcc_option_tokens=("/std:c++20",), gcc_path="clang-cl")
    explicit = CompileContext(gcc_option_tokens=("-DFOO=1",))
    merged = _merge_l3_compile_context(explicit, derived)
    assert merged is not None
    assert merged.gcc_path == "clang-cl"


def test_merge_l3_compile_context_explicit_gcc_path_wins_over_derived() -> None:
    """A caller's own explicit --gcc-path is never overridden by a derived
    one, mirroring every other _ExplicitPin-covered dimension."""
    from abicheck.buildsource.l2_seed import _merge_l3_compile_context

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
    from abicheck.buildsource.l2_seed import _merge_l3_compile_context

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
    from abicheck.buildsource.l2_seed import _merge_l3_compile_context

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
    from abicheck.buildsource.l2_seed import _merge_l3_compile_context

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


def test_resolve_raises_ambiguous_when_otherwise_identical_units_differ_only_in_driver(
    tmp_path: Path,
) -> None:
    """P2 review finding (``discussion_r3788...`` ambiguity-grouping
    follow-up): two matched compile units that are otherwise identical but
    resolve to *different* clang-cl drivers must raise
    ``HeaderCompileContextAmbiguousError``, not silently pick the first
    unit's driver -- the compiler itself supplies ABI-relevant built-in
    macros/default include paths/target defaults, so switching drivers
    (e.g. because compile-database iteration order changed) can silently
    change the generated L2 snapshot."""
    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src_a = tmp_path / "a.cpp"
    src_a.write_text('#include "widget.h"\n', encoding="utf-8")
    src_b = tmp_path / "b.cpp"
    src_b.write_text('#include "widget.h"\n', encoding="utf-8")
    cu_a = _cu(
        source=str(src_a),
        directory=str(tmp_path),
        abi_relevant_flags=["/std:c++20"],
        argv=["clang-cl", "/std:c++20", "-c", str(src_a)],
    )
    cu_b = _cu(
        source=str(src_b),
        directory=str(tmp_path),
        abi_relevant_flags=["/std:c++20"],
        argv=["cl.exe", "/std:c++20", "-c", str(src_b)],
    )
    ev = BuildEvidence(compile_units=[cu_a, cu_b])
    with pytest.raises(HeaderCompileContextAmbiguousError):
        resolve_header_compile_context(ev, [header])


def test_resolve_same_driver_units_are_not_ambiguous(tmp_path: Path) -> None:
    """Companion positive case: two otherwise-identical units that resolve
    to the *same* driver must not raise -- the new gcc_path field in the
    ambiguity signature must not introduce a false ambiguity for the common
    case."""
    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src_a = tmp_path / "a.cpp"
    src_a.write_text('#include "widget.h"\n', encoding="utf-8")
    src_b = tmp_path / "b.cpp"
    src_b.write_text('#include "widget.h"\n', encoding="utf-8")
    cu_a = _cu(
        source=str(src_a),
        directory=str(tmp_path),
        abi_relevant_flags=["/std:c++20"],
        argv=["clang-cl", "/std:c++20", "-c", str(src_a)],
    )
    cu_b = _cu(
        source=str(src_b),
        directory=str(tmp_path),
        abi_relevant_flags=["/std:c++20"],
        argv=["clang-cl", "/std:c++20", "-c", str(src_b)],
    )
    ev = BuildEvidence(compile_units=[cu_a, cu_b])
    result = resolve_header_compile_context(ev, [header])
    assert result.context is not None
    assert result.context.gcc_path == "clang-cl"


def test_resolve_preserves_explicit_driver_mode_cl_on_generic_binary_name(
    tmp_path: Path,
) -> None:
    """P2 review finding ("Preserve explicit CL driver mode during replay"):
    a compile unit can select MSVC/CL dialect via ``--driver-mode=cl`` on a
    generically-named driver (``clang``, not ``clang-cl``) -- there is no
    CL-style basename for :func:`_derived_gcc_path` to record, so the
    rendered context must instead carry ``--driver-mode=cl`` itself among
    its ``gcc_option_tokens``, or the reconstructed command invokes
    GNU-mode clang against a retained ``/std:`` survivor it cannot parse."""
    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src = tmp_path / "t.cpp"
    src.write_text('#include "widget.h"\n', encoding="utf-8")
    cu = _cu(
        source=str(src),
        directory=str(tmp_path),
        abi_relevant_flags=["/std:c++20"],
        argv=["clang", "--driver-mode=cl", "/std:c++20", "/c", str(src)],
    )
    ev = BuildEvidence(compile_units=[cu])
    result = resolve_header_compile_context(ev, [header])
    assert result.context is not None
    assert "--driver-mode=cl" in result.context.gcc_option_tokens
    assert "/std:c++20" in result.context.gcc_option_tokens
    # No CL-style basename to record here (argv[0] is the generic "clang"),
    # so gcc_path is the bare fallback -- --driver-mode=cl is what actually
    # carries the CL-dialect selection forward.
    assert result.context.gcc_path == "clang"


def test_resolve_driver_mode_cl_present_even_when_gcc_path_already_cl_style(
    tmp_path: Path,
) -> None:
    """Companion case: for an already-CL-style-named driver (``clang-cl``),
    emitting ``--driver-mode=cl`` too is harmless (a real ``clang-cl``
    already self-selects CL mode from its own basename) -- this asserts the
    unconditional emission (mirroring L4 replay's own ``if msvc:
    cmd.append("--driver-mode=cl")`` precedent) doesn't regress the more
    common clang-cl-named case."""
    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src = tmp_path / "t.cpp"
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
    assert "--driver-mode=cl" in result.context.gcc_option_tokens
    assert result.context.gcc_path == "clang-cl"


def test_resolve_normalizes_dotdot_driver_paths_so_equivalent_units_are_not_ambiguous(
    tmp_path: Path,
) -> None:
    """P2 review finding ("Normalize resolved driver paths before
    grouping"): two matched units in different build subdirectories can
    spell the SAME executable through a relative path containing ``..`` --
    e.g. ``a/../../tool/clang-cl`` and ``b/../../tool/clang-cl`` both name
    ``<root>/tool/clang-cl`` once ``..`` segments collapse, but joining each
    token onto its own unit ``directory`` alone (without normalizing) left
    the two joined strings textually different, so
    ``_EffectiveContextSignature``'s plain string comparison on ``gcc_path``
    raised a spurious ``HeaderCompileContextAmbiguousError`` for units that
    in fact agree on every ABI-relevant dimension."""
    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    (tmp_path / "tool").mkdir()
    src_a = tmp_path / "a.cpp"
    src_a.write_text('#include "widget.h"\n', encoding="utf-8")
    src_b = tmp_path / "b.cpp"
    src_b.write_text('#include "widget.h"\n', encoding="utf-8")
    build_a = tmp_path / "build" / "a"
    build_a.mkdir(parents=True)
    build_b = tmp_path / "build" / "b"
    build_b.mkdir(parents=True)
    cu_a = _cu(
        source=str(src_a),
        directory=str(build_a),
        abi_relevant_flags=["/std:c++20"],
        argv=["../../tool/clang-cl", "/std:c++20", "-c", str(src_a)],
    )
    cu_b = _cu(
        source=str(src_b),
        directory=str(build_b),
        abi_relevant_flags=["/std:c++20"],
        argv=["../../tool/clang-cl", "/std:c++20", "-c", str(src_b)],
    )
    ev = BuildEvidence(compile_units=[cu_a, cu_b])
    result = resolve_header_compile_context(ev, [header])
    assert result.context is not None
    assert result.context.gcc_path == str(tmp_path / "tool" / "clang-cl")


def test_resolve_still_ambiguous_when_normalized_driver_paths_genuinely_differ(
    tmp_path: Path,
) -> None:
    """Companion negative case: normalization must only collapse
    equivalent spellings, never mask a genuine driver disagreement -- two
    units resolving (after normalization) to two different real
    executables must still raise."""
    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    (tmp_path / "tool_a").mkdir()
    (tmp_path / "tool_b").mkdir()
    src_a = tmp_path / "a.cpp"
    src_a.write_text('#include "widget.h"\n', encoding="utf-8")
    src_b = tmp_path / "b.cpp"
    src_b.write_text('#include "widget.h"\n', encoding="utf-8")
    build_a = tmp_path / "build" / "a"
    build_a.mkdir(parents=True)
    build_b = tmp_path / "build" / "b"
    build_b.mkdir(parents=True)
    cu_a = _cu(
        source=str(src_a),
        directory=str(build_a),
        abi_relevant_flags=["/std:c++20"],
        argv=["../../tool_a/clang-cl", "/std:c++20", "-c", str(src_a)],
    )
    cu_b = _cu(
        source=str(src_b),
        directory=str(build_b),
        abi_relevant_flags=["/std:c++20"],
        argv=["../../tool_b/clang-cl", "/std:c++20", "-c", str(src_b)],
    )
    ev = BuildEvidence(compile_units=[cu_a, cu_b])
    with pytest.raises(HeaderCompileContextAmbiguousError):
        resolve_header_compile_context(ev, [header])


def test_resolve_driver_disagreement_excused_when_caller_pins_explicit_gcc_path(
    tmp_path: Path,
) -> None:
    """A driver-only disagreement the caller already resolved via an
    explicit ``--gcc-path`` must not raise -- mirrors the existing
    ``standard``/``target_triple``/``sysroot`` pin-masking behavior
    (Finding 3 of the original P1 review) applied to the new gcc_path
    dimension."""
    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src_a = tmp_path / "a.cpp"
    src_a.write_text('#include "widget.h"\n', encoding="utf-8")
    src_b = tmp_path / "b.cpp"
    src_b.write_text('#include "widget.h"\n', encoding="utf-8")
    cu_a = _cu(
        source=str(src_a),
        directory=str(tmp_path),
        abi_relevant_flags=["/std:c++20"],
        argv=["clang-cl", "/std:c++20", "-c", str(src_a)],
    )
    cu_b = _cu(
        source=str(src_b),
        directory=str(tmp_path),
        abi_relevant_flags=["/std:c++20"],
        argv=["cl.exe", "/std:c++20", "-c", str(src_b)],
    )
    ev = BuildEvidence(compile_units=[cu_a, cu_b])
    explicit = CompileContext(gcc_path="/opt/custom/clang-cl")
    result = resolve_header_compile_context(ev, [header], explicit=explicit)
    assert result.context is not None


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


def test_explicit_pin_of_recognizes_slash_d_define() -> None:
    """P2 review finding ("Recognize /D while collecting explicit define
    pins"): `_ExplicitPin.of()` only recognized `-D` (GCC/Clang spelling),
    leaving `pin.defines` empty for a clang-cl caller pinning an ABI macro
    via `/D_GLIBCXX_USE_CXX11_ABI=1` -- even though the raw-flag masking it
    feeds (`_pinned_define_macro`) already recognized `/D` too. `clang-cl
    /?` documents `/D <macro[=value]>` as the supported define form."""
    from abicheck.buildsource.header_compile_context import _ExplicitPin

    pin = _ExplicitPin.of(
        CompileContext(gcc_option_tokens=("/D_GLIBCXX_USE_CXX11_ABI=1",))
    )
    assert pin.defines == frozenset({"_GLIBCXX_USE_CXX11_ABI"})


def test_explicit_pin_of_recognizes_slash_u_undefine() -> None:
    """Sibling case for MSVC/clang-cl's `/U` (undefine)."""
    from abicheck.buildsource.header_compile_context import _ExplicitPin

    pin = _ExplicitPin.of(CompileContext(gcc_option_tokens=("/U", "FOO")))
    assert pin.undefines == frozenset({"FOO"})


def test_resolve_slash_d_pin_excuses_clang_cl_macro_disagreement(
    tmp_path: Path,
) -> None:
    """End-to-end regression for the same finding: a clang-cl caller pinning
    `_GLIBCXX_USE_CXX11_ABI` via `/D_GLIBCXX_USE_CXX11_ABI=1` must excuse a
    macro-only disagreement between two matched clang-cl compile units --
    mirroring the existing `-D`-spelled sibling test in
    test_header_compile_context.py
    (test_resolve_explicit_define_pin_also_excuses_raw_abi_flag_survivor_disagreement)
    but for the MSVC/clang-cl `/D` spelling end to end (both the structured
    `cu.defines` entry and its raw `/D...` survivor in
    `cu.abi_relevant_flags` must be excused, or the ambiguity error still
    fires)."""
    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src_a = tmp_path / "a.cpp"
    src_a.write_text('#include "widget.h"\n', encoding="utf-8")
    src_b = tmp_path / "b.cpp"
    src_b.write_text('#include "widget.h"\n', encoding="utf-8")
    unit_a = _cu(
        source=str(src_a),
        directory=str(tmp_path),
        defines={"_GLIBCXX_USE_CXX11_ABI": "0"},
        abi_relevant_flags=["/D_GLIBCXX_USE_CXX11_ABI=0"],
        argv=["clang-cl", "/c", str(src_a)],
    )
    unit_b = _cu(
        source=str(src_b),
        directory=str(tmp_path),
        defines={"_GLIBCXX_USE_CXX11_ABI": "1"},
        abi_relevant_flags=["/D_GLIBCXX_USE_CXX11_ABI=1"],
        argv=["clang-cl", "/c", str(src_b)],
    )
    ev = BuildEvidence(compile_units=[unit_a, unit_b])
    explicit = CompileContext(gcc_option_tokens=("/D_GLIBCXX_USE_CXX11_ABI=1",))
    result = resolve_header_compile_context(ev, [header], explicit=explicit)
    assert result.matched is True


def test_resolve_slash_d_pin_still_fails_closed_without_pin(tmp_path: Path) -> None:
    """Companion negative case: without the explicit /D pin, the identical
    disagreement still raises -- proving the excuse above genuinely comes
    from the pin, not from some other masking."""
    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src_a = tmp_path / "a.cpp"
    src_a.write_text('#include "widget.h"\n', encoding="utf-8")
    src_b = tmp_path / "b.cpp"
    src_b.write_text('#include "widget.h"\n', encoding="utf-8")
    unit_a = _cu(
        source=str(src_a),
        directory=str(tmp_path),
        defines={"_GLIBCXX_USE_CXX11_ABI": "0"},
        abi_relevant_flags=["/D_GLIBCXX_USE_CXX11_ABI=0"],
        argv=["clang-cl", "/c", str(src_a)],
    )
    unit_b = _cu(
        source=str(src_b),
        directory=str(tmp_path),
        defines={"_GLIBCXX_USE_CXX11_ABI": "1"},
        abi_relevant_flags=["/D_GLIBCXX_USE_CXX11_ABI=1"],
        argv=["clang-cl", "/c", str(src_b)],
    )
    ev = BuildEvidence(compile_units=[unit_a, unit_b])
    with pytest.raises(HeaderCompileContextAmbiguousError):
        resolve_header_compile_context(ev, [header])


def test_explicit_pin_of_gcc_path_true_for_gcc_prefix_only() -> None:
    """P2 review finding ("Treat an explicit prefix as a compiler-selector
    pin"): `_ExplicitPin.gcc_path` must also be True when only
    `explicit.gcc_prefix` is set -- either explicit selector field alone
    already fully resolves the compiler-selection dimension for
    `_merge_l3_compile_context`'s purposes (per the earlier gcc_path/
    gcc_prefix mutual-exclusivity fix), so the ambiguity-signature masking
    must treat the two as one dimension too."""
    from abicheck.buildsource.header_compile_context import _ExplicitPin

    pin = _ExplicitPin.of(CompileContext(gcc_prefix="/opt/llvm/bin/"))
    assert pin.gcc_path is True


def test_resolve_gcc_prefix_only_excuses_driver_disagreement(
    tmp_path: Path,
) -> None:
    """End-to-end regression: a caller supplying only `gcc_prefix` (never
    `gcc_path`) must excuse a driver-only disagreement between matched
    units the same way an explicit `gcc_path` already does -- mirroring
    test_resolve_driver_disagreement_excused_when_caller_pins_explicit_gcc_path
    above but with `gcc_prefix` as the sole explicit selector. Before the
    fix, `_ExplicitPin.of()` checked only `explicit.gcc_path is not None`,
    so this raised `HeaderCompileContextAmbiguousError` even though
    `_merge_l3_compile_context` already treats the explicit prefix as
    authoritative and discards every derived path."""
    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src_a = tmp_path / "a.cpp"
    src_a.write_text('#include "widget.h"\n', encoding="utf-8")
    src_b = tmp_path / "b.cpp"
    src_b.write_text('#include "widget.h"\n', encoding="utf-8")
    cu_a = _cu(
        source=str(src_a),
        directory=str(tmp_path),
        abi_relevant_flags=["/std:c++20"],
        argv=["clang-cl", "/std:c++20", "-c", str(src_a)],
    )
    cu_b = _cu(
        source=str(src_b),
        directory=str(tmp_path),
        abi_relevant_flags=["/std:c++20"],
        argv=["cl.exe", "/std:c++20", "-c", str(src_b)],
    )
    ev = BuildEvidence(compile_units=[cu_a, cu_b])
    explicit = CompileContext(gcc_prefix="/opt/llvm/bin/")
    result = resolve_header_compile_context(ev, [header], explicit=explicit)
    assert result.matched is True


# -- Finding 2/4 (Codex + CodeRabbit): foreign absolute driver paths --------


def test_resolve_derives_gcc_path_windows_absolute_driver_preserved_on_any_host(
    tmp_path: Path,
) -> None:
    """A Windows drive-letter-absolute driver path (``C:\\LLVM\\bin\\
    clang-cl.exe``) recorded by build evidence collected on Windows must be
    recognized as absolute -- and normalized with Windows (``ntpath``)
    grammar -- regardless of which host OS abicheck itself runs on. Before
    the fix, host-native ``Path.is_absolute()`` on POSIX returned ``False``
    for this token (no leading ``/``), so it was wrongly joined onto the
    compile unit's own ``directory`` -- corrupting ``gcc_path`` into a
    nonexistent path and letting otherwise-identical units acquire
    different ``gcc_path`` signatures purely from their differing
    ``directory``."""
    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src = tmp_path / "widget.cpp"
    src.write_text('#include "widget.h"\n', encoding="utf-8")
    cu = _cu(
        source=str(src),
        directory=str(tmp_path),
        abi_relevant_flags=["/std:c++20"],
        argv=[r"C:\LLVM\bin\clang-cl.exe", "/std:c++20", "-c", str(src)],
    )
    ev = BuildEvidence(compile_units=[cu])
    result = resolve_header_compile_context(ev, [header])
    assert result.context is not None
    assert result.context.gcc_path == r"C:\LLVM\bin\clang-cl.exe"


def test_resolve_derives_gcc_path_windows_unc_absolute_driver_preserved() -> None:
    """The symmetric UNC form (``\\\\server\\share\\clang-cl.exe``) is also
    recognized as absolute regardless of host."""
    from abicheck.buildsource.header_compile_context import _resolve_driver_token

    token = r"\\server\share\clang-cl.exe"
    assert _resolve_driver_token(token, "/some/directory") == token


def test_resolve_derives_gcc_path_two_units_with_windows_absolute_driver_not_ambiguous(
    tmp_path: Path,
) -> None:
    """Companion regression for the ambiguity-grouping consequence named in
    the finding: two otherwise-identical compile units in DIFFERENT
    directories, both naming the SAME Windows-absolute driver, must not be
    treated as disagreeing on ``gcc_path`` -- the pre-fix bug corrupted
    each unit's ``gcc_path`` by joining the (wrongly-judged-relative)
    Windows token onto each unit's own, different ``directory``, making two
    genuinely-identical drivers compare unequal."""
    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src_a = tmp_path / "a.cpp"
    src_a.write_text('#include "widget.h"\n', encoding="utf-8")
    src_b = tmp_path / "b.cpp"
    src_b.write_text('#include "widget.h"\n', encoding="utf-8")
    build_a = tmp_path / "build_a"
    build_a.mkdir()
    build_b = tmp_path / "build_b"
    build_b.mkdir()
    cu_a = _cu(
        source=str(src_a),
        directory=str(build_a),
        abi_relevant_flags=["/std:c++20"],
        argv=[r"C:\LLVM\bin\clang-cl.exe", "/std:c++20", "-c", str(src_a)],
    )
    cu_b = _cu(
        source=str(src_b),
        directory=str(build_b),
        abi_relevant_flags=["/std:c++20"],
        argv=[r"C:\LLVM\bin\clang-cl.exe", "/std:c++20", "-c", str(src_b)],
    )
    ev = BuildEvidence(compile_units=[cu_a, cu_b])
    result = resolve_header_compile_context(ev, [header])
    assert result.context is not None
    assert result.context.gcc_path == r"C:\LLVM\bin\clang-cl.exe"


def test_resolve_derives_gcc_path_posix_absolute_driver_not_windows_mangled() -> None:
    """The other direction (CodeRabbit's own research note): a genuine
    POSIX absolute path must never be routed through ``ntpath.normpath``
    (which would accept a bare ``/`` root as absolute too and corrupt its
    separators) -- it must resolve via the POSIX grammar branch instead."""
    from abicheck.buildsource.header_compile_context import _resolve_driver_token

    token = "/opt/llvm/bin/clang-cl"
    assert _resolve_driver_token(token, "/some/directory") == token
