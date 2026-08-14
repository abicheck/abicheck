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


def test_resolve_expands_directory_header_input_before_matching(
    tmp_path: Path,
) -> None:
    # A `-H`/InputSpec.headers entry may name a whole directory rather than a
    # single file (the normal L2 path expands it via
    # `service_scan.expand_header_inputs` before parsing). Passing the raw,
    # unexpanded directory here must not silently no-op: it should be
    # expanded to its real header files first, so a compile unit that
    # `#include`s one of those files is still matched and the derived context
    # still applies -- reproducing the reported gap (before the fix, matching
    # against the directory path itself finds no `#include "headers"`-shaped
    # text in any TU, so nothing matches and `parsed_with_build_context`
    # never gets stamped).
    header_dir = tmp_path / "headers"
    header_dir.mkdir()
    header = header_dir / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src = tmp_path / "widget.cpp"
    src.write_text('#include "widget.h"\nint f() { return 0; }\n', encoding="utf-8")
    cu = _cu(source=str(src), directory=str(tmp_path), standard="c++20")
    ev = BuildEvidence(compile_units=[cu])
    result = resolve_header_compile_context(ev, [header_dir])
    assert result.matched is True
    assert result.matched_unit_count == 1
    assert result.context is not None
    assert "-std=c++20" in result.context.gcc_option_tokens


def test_resolve_derives_context_from_single_matching_unit(tmp_path: Path) -> None:
    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src = tmp_path / "widget.cpp"
    src.write_text('#include "widget.h"\nint f() { return 0; }\n', encoding="utf-8")
    # Real, platform-native absolute paths under tmp_path -- a hand-typed
    # POSIX literal like "/opt/sysroot" isn't even a valid absolute path on
    # Windows (no drive letter), so it would silently exercise
    # _resolve_cu_relative_path's "join with directory" branch instead of
    # the "already absolute" branch a real CompileUnit's sysroot/include
    # paths take on every platform.
    sysroot_dir = tmp_path / "sysroot"
    inc_dir = tmp_path / "inc"
    sysinc_dir = tmp_path / "sysinc"
    cu = _cu(
        source=str(src),
        directory=str(tmp_path),
        standard="c++20",
        target_triple="x86_64-linux-gnu",
        defines={"WIDGET_EXTRA": "1", "FLAG": ""},
        undefines=["NDEBUG"],
        include_paths=[str(inc_dir)],
        system_include_paths=[str(sysinc_dir)],
        sysroot=str(sysroot_dir),
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
    # Compare against the same forward-slash-normalized rendering the
    # production code emits (.as_posix()), not a raw, platform-dependent
    # string -- this is the actual cross-platform contract, not an
    # incidental one that only happens to hold on POSIX.
    assert f"--sysroot={sysroot_dir.as_posix()}" in tokens
    assert "-DWIDGET_EXTRA=1" in tokens
    assert "-DFLAG" in tokens
    assert "-UNDEBUG" in tokens
    assert "-I" in tokens and inc_dir.as_posix() in tokens
    assert "-isystem" in tokens and sysinc_dir.as_posix() in tokens
    assert "-fPIC" in tokens
    assert "-fno-omit-frame-pointer" in tokens


def test_resolve_derives_c_standard_not_only_cxx(tmp_path: Path) -> None:
    # Regression: _context_flags previously only emitted -std= when
    # "++" in cu.standard, silently omitting a plain C standard
    # (-std=c17/-std=gnu11/...) even though the module claims to apply the
    # standard generally, C and C++ alike.
    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src = tmp_path / "widget.c"
    src.write_text('#include "widget.h"\nint f(void) { return 0; }\n', encoding="utf-8")
    cu = _cu(source=str(src), directory=str(tmp_path), language="C", standard="c17")
    ev = BuildEvidence(compile_units=[cu])
    result = resolve_header_compile_context(ev, [header])
    assert result.matched is True
    assert result.context is not None
    assert "-std=c17" in result.context.gcc_option_tokens


def test_resolve_strips_dangling_target_operand_flag(tmp_path: Path) -> None:
    """Finding 1: a compile DB spelling ``-target aarch64-linux-gnu`` as two
    separate argv tokens has only the bare ``-target`` switch captured into
    ``abi_relevant_flags`` (the adapter's naive prefix match has no
    lookahead) -- forwarding it verbatim alongside the structured
    ``--target=`` rendering would emit a dangling, operand-less switch.
    """
    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src = tmp_path / "widget.cpp"
    src.write_text('#include "widget.h"\n', encoding="utf-8")
    cu = _cu(
        source=str(src),
        directory=str(tmp_path),
        target_triple="aarch64-linux-gnu",
        # Mirrors extract_abi_relevant_flags's real output for a two-token
        # "-target aarch64-linux-gnu": only the bare switch is captured.
        abi_relevant_flags=["-target", "-fPIC"],
    )
    ev = BuildEvidence(compile_units=[cu])
    result = resolve_header_compile_context(ev, [header])
    assert result.context is not None
    tokens = result.context.gcc_option_tokens
    assert tokens.count("--target=aarch64-linux-gnu") == 1
    assert "-target" not in tokens  # the dangling bare switch is dropped
    assert "-fPIC" in tokens  # unrelated flags still forwarded
    # No malformed/dangling trailing switch anywhere in the rendered tokens.
    assert tokens[-1] != "-target"


def test_resolve_strips_dangling_sysroot_operand_flags(tmp_path: Path) -> None:
    """Finding 1: same shape for --sysroot and -isysroot's separate-token forms."""
    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src = tmp_path / "widget.cpp"
    src.write_text('#include "widget.h"\n', encoding="utf-8")
    # A real, platform-native absolute path (not a hand-typed POSIX literal
    # like "/sdk", which isn't absolute on Windows -- see the analogous fix
    # in test_resolve_derives_context_from_single_matching_unit above).
    sdk_dir = tmp_path / "sdk"
    cu = _cu(
        source=str(src),
        directory=str(tmp_path),
        sysroot=str(sdk_dir),
        abi_relevant_flags=["--sysroot", "-isysroot"],
    )
    ev = BuildEvidence(compile_units=[cu])
    result = resolve_header_compile_context(ev, [header])
    assert result.context is not None
    tokens = result.context.gcc_option_tokens
    expected = f"--sysroot={sdk_dir.as_posix()}"
    assert tokens.count(expected) == 1
    assert "--sysroot" not in tokens
    assert "-isysroot" not in tokens


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


# ---------------------------------------------------------------------------
# 1b. Finding 3: an explicit override resolves a same-field-only ambiguity
# ---------------------------------------------------------------------------


def test_resolve_explicit_std_resolves_std_only_disagreement(tmp_path: Path) -> None:
    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src_a = tmp_path / "a.cpp"
    src_a.write_text('#include "widget.h"\n', encoding="utf-8")
    src_b = tmp_path / "b.cpp"
    src_b.write_text('#include "widget.h"\n', encoding="utf-8")
    unit_a = _cu(source=str(src_a), directory=str(tmp_path), standard="c++17")
    unit_b = _cu(source=str(src_b), directory=str(tmp_path), standard="c++20")
    ev = BuildEvidence(compile_units=[unit_a, unit_b])
    explicit = CompileContext(gcc_option_tokens=("-std=c++20",))
    # No error: the std-only disagreement is excused by the explicit pin.
    result = resolve_header_compile_context(ev, [header], explicit=explicit)
    assert result.matched is True
    assert result.matched_unit_count == 2


def test_resolve_genuine_disagreement_with_no_explicit_override_still_fails(
    tmp_path: Path,
) -> None:
    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src_a = tmp_path / "a.cpp"
    src_a.write_text('#include "widget.h"\n', encoding="utf-8")
    src_b = tmp_path / "b.cpp"
    src_b.write_text('#include "widget.h"\n', encoding="utf-8")
    unit_a = _cu(source=str(src_a), directory=str(tmp_path), standard="c++17")
    unit_b = _cu(source=str(src_b), directory=str(tmp_path), standard="c++20")
    ev = BuildEvidence(compile_units=[unit_a, unit_b])
    # An explicit override for an *unrelated* dimension (a macro) must not
    # excuse the genuine, unpinned std disagreement.
    explicit = CompileContext(gcc_option_tokens=("-DUNRELATED=1",))
    with pytest.raises(HeaderCompileContextAmbiguousError):
        resolve_header_compile_context(ev, [header], explicit=explicit)
    # Same with no explicit context at all.
    with pytest.raises(HeaderCompileContextAmbiguousError):
        resolve_header_compile_context(ev, [header])


def test_resolve_partial_explicit_override_still_fails_for_remaining_field(
    tmp_path: Path,
) -> None:
    """Disagreement on two fields (std, target); only std is pinned
    explicitly -- must still fail closed on the unpinned target disagreement."""
    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src_a = tmp_path / "a.cpp"
    src_a.write_text('#include "widget.h"\n', encoding="utf-8")
    src_b = tmp_path / "b.cpp"
    src_b.write_text('#include "widget.h"\n', encoding="utf-8")
    unit_a = _cu(
        source=str(src_a),
        directory=str(tmp_path),
        standard="c++17",
        target_triple="x86_64-linux-gnu",
    )
    unit_b = _cu(
        source=str(src_b),
        directory=str(tmp_path),
        standard="c++20",
        target_triple="aarch64-linux-gnu",
    )
    ev = BuildEvidence(compile_units=[unit_a, unit_b])
    explicit = CompileContext(gcc_option_tokens=("-std=c++20",))
    with pytest.raises(HeaderCompileContextAmbiguousError):
        resolve_header_compile_context(ev, [header], explicit=explicit)


def test_resolve_explicit_define_resolves_macro_only_disagreement(
    tmp_path: Path,
) -> None:
    """Per-macro, not whole-field: an explicit -DFOO=2 excuses disagreement
    on FOO specifically, without needing to pin every macro."""
    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src_a = tmp_path / "a.cpp"
    src_a.write_text('#include "widget.h"\n', encoding="utf-8")
    src_b = tmp_path / "b.cpp"
    src_b.write_text('#include "widget.h"\n', encoding="utf-8")
    unit_a = _cu(
        source=str(src_a),
        directory=str(tmp_path),
        defines={"FOO": "1", "SHARED": "1"},
    )
    unit_b = _cu(
        source=str(src_b),
        directory=str(tmp_path),
        defines={"FOO": "2", "SHARED": "1"},
    )
    ev = BuildEvidence(compile_units=[unit_a, unit_b])
    explicit = CompileContext(gcc_option_tokens=("-DFOO=2",))
    result = resolve_header_compile_context(ev, [header], explicit=explicit)
    assert result.matched is True


def test_resolve_agreeing_structured_fields_with_different_raw_flag_spellings_not_ambiguous(
    tmp_path: Path,
) -> None:
    # Two compile units that resolve to the IDENTICAL structured
    # target_triple/sysroot, but whose adapter-captured raw
    # `abi_relevant_flags` spell the flag that produced it differently
    # (a complete single-token `--target=X` vs. a split two-token `-target`
    # survivor, likewise for sysroot) must NOT be reported as ambiguous --
    # the raw spelling carries no information the structured field doesn't
    # already carry, and this holds with no explicit CompileContext at all
    # (unlike the Finding-3 tests above, which need an explicit pin to
    # excuse a genuine structured-field disagreement, this is a spelling-
    # only non-disagreement the signature must never have raised on in the
    # first place).
    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src_a = tmp_path / "a.cpp"
    src_a.write_text('#include "widget.h"\n', encoding="utf-8")
    src_b = tmp_path / "b.cpp"
    src_b.write_text('#include "widget.h"\n', encoding="utf-8")
    unit_a = _cu(
        source=str(src_a),
        directory=str(tmp_path),
        standard="c++20",
        target_triple="aarch64-linux-gnu",
        sysroot="/opt/sysroot",
        abi_relevant_flags=["--target=aarch64-linux-gnu", "--sysroot=/opt/sysroot"],
    )
    unit_b = _cu(
        source=str(src_b),
        directory=str(tmp_path),
        standard="c++20",
        target_triple="aarch64-linux-gnu",
        sysroot="/opt/sysroot",
        abi_relevant_flags=["-target", "-isysroot"],
    )
    ev = BuildEvidence(compile_units=[unit_a, unit_b])
    result = resolve_header_compile_context(ev, [header])
    assert result.matched is True
    assert result.matched_unit_count == 2
    assert result.context is not None


def test_resolve_target_sdk_version_disagreement_still_raises(tmp_path: Path) -> None:
    """A bare prefix match on ``-target`` would have masked
    ``-target-sdk-version=<value>`` too -- a real ``clang -cc1`` flag that is
    NOT represented by ``target_triple`` at all. Two units disagreeing only
    on it must still raise, not silently collapse to one signature."""
    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src_a = tmp_path / "a.cpp"
    src_a.write_text('#include "widget.h"\n', encoding="utf-8")
    src_b = tmp_path / "b.cpp"
    src_b.write_text('#include "widget.h"\n', encoding="utf-8")
    unit_a = _cu(
        source=str(src_a),
        directory=str(tmp_path),
        abi_relevant_flags=["-target-sdk-version=13.0"],
    )
    unit_b = _cu(
        source=str(src_b),
        directory=str(tmp_path),
        abi_relevant_flags=["-target-sdk-version=14.0"],
    )
    ev = BuildEvidence(compile_units=[unit_a, unit_b])
    with pytest.raises(HeaderCompileContextAmbiguousError):
        resolve_header_compile_context(ev, [header])


def test_resolve_exact_target_and_sysroot_spellings_still_masked(
    tmp_path: Path,
) -> None:
    """The precision fix must not regress the exact structured spellings
    (``-target``/``--target``/``--target=...``/``--sysroot``/
    ``--sysroot=...``/``-isysroot``) themselves -- those still mask cleanly
    when the structured field already agrees."""
    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src_a = tmp_path / "a.cpp"
    src_a.write_text('#include "widget.h"\n', encoding="utf-8")
    src_b = tmp_path / "b.cpp"
    src_b.write_text('#include "widget.h"\n', encoding="utf-8")
    unit_a = _cu(
        source=str(src_a),
        directory=str(tmp_path),
        target_triple="aarch64-linux-gnu",
        sysroot="/opt/sysroot",
        abi_relevant_flags=["--target=aarch64-linux-gnu", "--sysroot=/opt/sysroot"],
    )
    unit_b = _cu(
        source=str(src_b),
        directory=str(tmp_path),
        target_triple="aarch64-linux-gnu",
        sysroot="/opt/sysroot",
        abi_relevant_flags=["-target", "--sysroot", "-isysroot"],
    )
    ev = BuildEvidence(compile_units=[unit_a, unit_b])
    result = resolve_header_compile_context(ev, [header])
    assert result.matched is True
    assert result.matched_unit_count == 2


def test_resolve_msvc_std_colon_disagreement_with_unpopulated_standard_field_still_raises(
    tmp_path: Path,
) -> None:
    """Review finding: MSVC ``/std:`` is never parsed into
    ``CompileUnit.standard`` (unlike GCC/Clang's ``-std=``), so when
    ``standard`` is empty on both units, the raw ``/std:c++17``/
    ``/std:c++20`` survivor in ``abi_relevant_flags`` is the ONLY signal
    recording the language standard at all. Unconditionally masking it (the
    way ``-std=``/``--target=``/``--sysroot=`` are unconditionally masked)
    would silently collapse these two genuinely-disagreeing MSVC units into
    one signature and apply the first unit's standard -- this must instead
    raise ``HeaderCompileContextAmbiguousError``."""
    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src_a = tmp_path / "a.cpp"
    src_a.write_text('#include "widget.h"\n', encoding="utf-8")
    src_b = tmp_path / "b.cpp"
    src_b.write_text('#include "widget.h"\n', encoding="utf-8")
    unit_a = _cu(
        source=str(src_a),
        directory=str(tmp_path),
        standard="",
        abi_relevant_flags=["/std:c++17"],
    )
    unit_b = _cu(
        source=str(src_b),
        directory=str(tmp_path),
        standard="",
        abi_relevant_flags=["/std:c++20"],
    )
    ev = BuildEvidence(compile_units=[unit_a, unit_b])
    with pytest.raises(HeaderCompileContextAmbiguousError):
        resolve_header_compile_context(ev, [header])


def test_resolve_msvc_std_colon_stays_masked_when_standard_field_populated_and_agrees(
    tmp_path: Path,
) -> None:
    """Companion to the test above: once ``CompileUnit.standard`` genuinely
    captured the language standard for BOTH units (and they agree), a
    ``/std:`` survivor in ``abi_relevant_flags`` really is redundant and
    must stay masked -- including when it carries a differing raw spelling
    from what produced the now-agreeing structured value, mirroring the
    already-established ``--target=``/``-target`` spelling-divergence
    tolerance."""
    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src_a = tmp_path / "a.cpp"
    src_a.write_text('#include "widget.h"\n', encoding="utf-8")
    src_b = tmp_path / "b.cpp"
    src_b.write_text('#include "widget.h"\n', encoding="utf-8")
    unit_a = _cu(
        source=str(src_a),
        directory=str(tmp_path),
        standard="c++20",
        abi_relevant_flags=["/std:c++20"],
    )
    unit_b = _cu(
        source=str(src_b),
        directory=str(tmp_path),
        standard="c++20",
        abi_relevant_flags=["/std:c++20"],
    )
    ev = BuildEvidence(compile_units=[unit_a, unit_b])
    result = resolve_header_compile_context(ev, [header])
    assert result.matched is True
    assert result.matched_unit_count == 2
    assert result.context is not None


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
    # (ADR-032 D7); resolution must expand them back before reading via
    # Path.expanduser(), which is genuinely platform-native: POSIX reads
    # HOME, Windows reads USERPROFILE (falling back to HOMEDRIVE+HOMEPATH)
    # and does not consult HOME at all. Both (not just HOME) -- os.path.
    # expanduser("~") reads a different env var depending on platform, the
    # same cross-platform pattern test_include_graph.py/test_archive_graph.py
    # already use for the identical situation -- must be set for this test
    # to exercise real expansion on every CI platform rather than only POSIX.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
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


def test_derive_l2_compile_context_explicit_std_resolves_ambiguity(
    tmp_path: Path,
) -> None:
    """Finding 3, threaded through the real ``derive_l2_compile_context``
    entry point (not just the lower-level ``resolve_header_compile_context``
    call above)."""
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
    explicit = CompileContext(gcc_option_tokens=("-std=c++20",))
    ctx, cleanups = derive_l2_compile_context(
        [header], None, tmp_path, explicit=explicit
    )
    try:
        assert ctx is not None
    finally:
        from abicheck.buildsource.inline import _run_cleanups

        _run_cleanups(cleanups)


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


def _write_corrupt_pack(pack_dir: Path) -> None:
    """A directory ``is_pack_dir()`` recognizes as a (corrupt) pack: a
    ``manifest.json`` present but unparseable, matching ``is_pack_dir``'s own
    documented "present but unparseable: keep treating it as a (corrupt) pack"
    contract -- ``BuildSourcePack.load()`` then raises decoding it."""
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / "manifest.json").write_text("{not valid json", encoding="utf-8")


def test_derive_l2_include_dirs_corrupt_sources_pack_degrades_to_empty(
    tmp_path: Path,
) -> None:
    """P2 regression: a ``--sources`` pack recognized by ``is_pack_dir`` but
    failing to load (corrupt ``manifest.json``) must degrade this best-effort
    seeding to ``([], [])``, not raise -- ``BuildSourcePack.load()`` used to
    run inside this function's own protected section pre-refactor; the shared
    ``_resolve_l2_seed_pack_args`` extraction moved it ahead of the ``try``
    by mistake (Codex review)."""
    from abicheck.buildsource.l2_seed import derive_l2_include_dirs

    pack_dir = tmp_path / "pack"
    _write_corrupt_pack(pack_dir)
    assert derive_l2_include_dirs(None, pack_dir) == ([], [])


def test_derive_l2_include_dirs_corrupt_build_info_pack_degrades_to_empty(
    tmp_path: Path,
) -> None:
    """Same as above via ``--build-info`` naming the corrupt pack directly."""
    from abicheck.buildsource.l2_seed import derive_l2_include_dirs

    pack_dir = tmp_path / "pack"
    _write_corrupt_pack(pack_dir)
    assert derive_l2_include_dirs(pack_dir, None) == ([], [])


def test_derive_l2_compile_context_corrupt_sources_pack_degrades_to_empty(
    tmp_path: Path,
) -> None:
    """Same P2 regression as above, for ``derive_l2_compile_context``."""
    from abicheck.buildsource.l2_seed import derive_l2_compile_context

    pack_dir = tmp_path / "pack"
    _write_corrupt_pack(pack_dir)
    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    assert derive_l2_compile_context([header], None, pack_dir) == (None, [])


def test_derive_l2_compile_context_corrupt_build_info_pack_degrades_to_empty(
    tmp_path: Path,
) -> None:
    from abicheck.buildsource.l2_seed import derive_l2_compile_context

    pack_dir = tmp_path / "pack"
    _write_corrupt_pack(pack_dir)
    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    assert derive_l2_compile_context([header], pack_dir, None) == (None, [])


# ---------------------------------------------------------------------------
# 3. service_input_resolution wiring
# ---------------------------------------------------------------------------


def test_merge_l3_compile_context_derived_leads_explicit_wins() -> None:
    from abicheck.service_input_resolution import _merge_l3_compile_context

    derived = CompileContext(gcc_option_tokens=("-DFOO=1", "-fPIC"))
    explicit = CompileContext(gcc_option_tokens=("-DFOO=2",), sysroot=Path("/x"))
    merged = _merge_l3_compile_context(explicit, derived)
    assert merged is not None
    # Finding 2: explicit's structured `sysroot` is folded into a trailing
    # token (and the structured field cleared) so it lands strictly after
    # every derived token in the actually-rendered command, not before it.
    assert merged.gcc_option_tokens == (
        "-DFOO=1",
        "-fPIC",
        "--sysroot=/x",
        "-DFOO=2",
    )
    assert merged.sysroot is None


def test_merge_l3_compile_context_explicit_gcc_options_string_folded_after_derived() -> (
    None
):
    """Finding 2: the free-form ``gcc_options`` string channel, not just
    ``sysroot``, must also land after every derived token."""
    from abicheck.service_input_resolution import _merge_l3_compile_context

    derived = CompileContext(gcc_option_tokens=("-DFOO=1",))
    explicit = CompileContext(gcc_options="-DFOO=2 -DBAR=3")
    merged = _merge_l3_compile_context(explicit, derived)
    assert merged is not None
    assert merged.gcc_option_tokens == ("-DFOO=1", "-DFOO=2", "-DBAR=3")
    assert merged.gcc_options is None


def test_merge_l3_compile_context_conflicting_sysroot_explicit_wins_in_rendered_command(
    tmp_path: Path,
) -> None:
    """End-to-end-shaped: build the actual castxml command from a merged
    context carrying a derived AND an explicit, conflicting sysroot, and
    assert the *last* --sysroot= token (the one that wins under real
    compiler last-flag-wins semantics) is the explicit one."""
    from abicheck.dumper_ast_config import _build_castxml_command
    from abicheck.service_input_resolution import _merge_l3_compile_context

    derived = CompileContext(gcc_option_tokens=("--sysroot=/derived",))
    explicit = CompileContext(sysroot=Path("/explicit"))
    merged = _merge_l3_compile_context(explicit, derived)
    assert merged is not None
    cmd = _build_castxml_command(
        "g++",
        "gnu",
        [],
        tmp_path / "out.xml",
        tmp_path / "agg.h",
        sysroot=merged.sysroot,
        gcc_options=merged.gcc_options,
        gcc_option_tokens=merged.gcc_option_tokens,
        force_cpp=True,
    )
    sysroot_tokens = [tok for tok in cmd if tok.startswith("--sysroot=")]
    assert sysroot_tokens == ["--sysroot=/derived", "--sysroot=/explicit"]
    assert sysroot_tokens[-1] == "--sysroot=/explicit"  # last-flag-wins: explicit


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

# ``widget_lib`` (the real g++-compiled shared library + compile_commands.json
# fixture the suite below depends on) lives in ``tests/conftest.py`` -- shared,
# real-compiler fixtures belong there rather than inline in a test file (see
# AGENTS.md's test-quality conventions), and pytest auto-discovers it as a
# regular fixture with no import needed here.


def pytestmark_e2e(func: object) -> object:
    """Composed marker for the real-clang/g++ end-to-end suite below: needs a
    real toolchain (``@pytest.mark.integration`` -- excluded from the
    default fast lane, which runs ``-m "not integration and ..."``, per
    AGENTS.md) and is ELF/Linux-scoped.
    """
    skip = pytest.mark.skipif(
        not sys.platform.startswith("linux"),
        reason="P0.3 end-to-end test is ELF/Linux-scoped (mirrors "
        "test_clang_header_backend_integration.py)",
    )
    return pytest.mark.integration(skip(func))


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
