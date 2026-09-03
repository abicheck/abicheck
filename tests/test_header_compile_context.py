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


def test_resolve_omits_conflicting_c_standard_when_cxx_explicitly_forced(
    tmp_path: Path,
) -> None:
    """Codex review, discussion_r3787398644: the matched compile unit is C
    with standard="c17", but the caller explicitly requested C++
    (lang="c++", lang_explicit=True). Forwarding the matched unit's derived
    -std=c17 into a forced-C++ header invocation makes Clang abort with
    "invalid argument '-std=c17' not allowed with 'C++'" -- so the
    conflicting derived standard must be omitted, not forwarded.

    Without the fix, this asserts False (the pre-fix code renders
    "-std=c17" here unconditionally) -- i.e. this test fails against the
    pre-fix code and passes after it.
    """
    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src = tmp_path / "widget.c"
    src.write_text('#include "widget.h"\nint f(void) { return 0; }\n', encoding="utf-8")
    cu = _cu(source=str(src), directory=str(tmp_path), language="C", standard="c17")
    ev = BuildEvidence(compile_units=[cu])
    result = resolve_header_compile_context(
        ev, [header], lang="c++", lang_explicit=True
    )
    assert result.matched is True
    assert result.context is not None
    tokens = result.context.gcc_option_tokens
    assert not any(t.startswith("-std=") for t in tokens), tokens


def test_resolve_keeps_matching_cxx_standard_when_cxx_explicitly_forced(
    tmp_path: Path,
) -> None:
    """The non-conflicting counterpart: a matched C++ unit's own -std= is
    still forwarded when the explicitly forced language agrees with it --
    forced_language must only ever suppress a genuine conflict, never a
    standard that already agrees."""
    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src = tmp_path / "widget.cpp"
    src.write_text('#include "widget.h"\nint f() { return 0; }\n', encoding="utf-8")
    cu = _cu(source=str(src), directory=str(tmp_path), language="CXX", standard="c++20")
    ev = BuildEvidence(compile_units=[cu])
    result = resolve_header_compile_context(
        ev, [header], lang="c++", lang_explicit=True
    )
    assert result.matched is True
    assert result.context is not None
    assert "-std=c++20" in result.context.gcc_option_tokens


def test_resolve_keeps_c_standard_when_language_not_explicitly_forced(
    tmp_path: Path,
) -> None:
    """lang_explicit=False (the default -- includes an unspecified/auto-detect
    request) must be a complete no-op: the matched unit's own -std=c17 is
    still forwarded exactly as before, even if a non-explicit ``lang="c++"``
    (e.g. Click's own default) happens to be passed alongside it."""
    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src = tmp_path / "widget.c"
    src.write_text('#include "widget.h"\nint f(void) { return 0; }\n', encoding="utf-8")
    cu = _cu(source=str(src), directory=str(tmp_path), language="C", standard="c17")
    ev = BuildEvidence(compile_units=[cu])
    result = resolve_header_compile_context(
        ev, [header], lang="c++", lang_explicit=False
    )
    assert result.matched is True
    assert result.context is not None
    assert "-std=c17" in result.context.gcc_option_tokens


def test_resolve_omits_conflicting_cxx_standard_when_c_explicitly_forced(
    tmp_path: Path,
) -> None:
    """The reverse conflict direction: a matched C++ unit's -std=c++20 must
    be omitted when the caller explicitly forces lang="c" -- symmetric with
    the C-forced-into-C++ case the review finding names."""
    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src = tmp_path / "widget.cpp"
    src.write_text('#include "widget.h"\nint f() { return 0; }\n', encoding="utf-8")
    cu = _cu(source=str(src), directory=str(tmp_path), language="CXX", standard="c++20")
    ev = BuildEvidence(compile_units=[cu])
    result = resolve_header_compile_context(ev, [header], lang="c", lang_explicit=True)
    assert result.matched is True
    assert result.context is not None
    tokens = result.context.gcc_option_tokens
    assert not any(t.startswith("-std=") for t in tokens), tokens


# ---------------------------------------------------------------------------
# 1b. Pure-function unit tests for the language-family helpers
#     (discussion_r3787398644), mirroring _is_structured_field_flag's own
#     small-helper-with-direct-tests treatment above.
# ---------------------------------------------------------------------------


def test_derived_standard_language_family() -> None:
    from abicheck.buildsource.header_compile_context import (
        _derived_standard_language_family,
    )

    assert _derived_standard_language_family("c17") == "c"
    assert _derived_standard_language_family("c11") == "c"
    assert _derived_standard_language_family("gnu11") == "c"
    assert _derived_standard_language_family("c++20") == "c++"
    assert _derived_standard_language_family("gnu++17") == "c++"
    assert _derived_standard_language_family("") is None


def test_forced_language_family() -> None:
    from abicheck.buildsource.header_compile_context import _forced_language_family

    assert _forced_language_family("c++", lang_explicit=True) == "c++"
    assert _forced_language_family("cpp", lang_explicit=True) == "c++"
    assert _forced_language_family("c", lang_explicit=True) == "c"
    # Not explicit: always a no-op regardless of the lang value.
    assert _forced_language_family("c++", lang_explicit=False) is None
    assert _forced_language_family("c", lang_explicit=False) is None
    # No lang at all: a no-op even if lang_explicit were somehow True.
    assert _forced_language_family(None, lang_explicit=True) is None
    assert _forced_language_family("", lang_explicit=True) is None


def test_standard_conflicts_with_forced_language() -> None:
    from abicheck.buildsource.header_compile_context import (
        _standard_conflicts_with_forced_language,
    )

    assert _standard_conflicts_with_forced_language("c17", "c++") is True
    assert _standard_conflicts_with_forced_language("c++20", "c") is True
    assert _standard_conflicts_with_forced_language("c17", "c") is False
    assert _standard_conflicts_with_forced_language("c++20", "c++") is False
    # No forced language: never a conflict, regardless of standard.
    assert _standard_conflicts_with_forced_language("c17", None) is False
    # Empty/unrecognized standard: nothing to conflict with.
    assert _standard_conflicts_with_forced_language("", "c++") is False


def test_cu_language_family() -> None:
    from abicheck.buildsource.header_compile_context import _cu_language_family

    assert _cu_language_family("C") == "c"
    assert _cu_language_family("CXX") == "c++"
    assert _cu_language_family("OBJC") is None
    assert _cu_language_family("") is None


def test_resolve_forced_language_resolves_language_ambiguity_before_grouping(
    tmp_path: Path,
) -> None:
    """P2 review finding (``discussion_r3787672845``): two otherwise-
    identical compile units differing ONLY in ``cu.language`` (one C, one
    C++, neither carrying an explicit ``-std=``, so
    ``_standard_conflicts_with_forced_language`` has nothing to compare)
    used to raise ``HeaderCompileContextAmbiguousError`` even when the
    caller passed an explicit ``lang="c++"``/``lang_explicit=True`` --
    because ``_EffectiveContextSignature`` grouped on ``cu.language`` before
    ``forced_language`` was ever computed. With the fix, the forced
    language is resolved *first* and narrows the matched-unit set to the
    C++ unit before signature grouping runs, so no ambiguity error is
    raised and the resolved context reflects the forced C++ unit."""
    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src_c = tmp_path / "a.c"
    src_c.write_text('#include "widget.h"\n', encoding="utf-8")
    src_cxx = tmp_path / "b.cpp"
    src_cxx.write_text('#include "widget.h"\n', encoding="utf-8")
    unit_c = _cu(
        source=str(src_c),
        directory=str(tmp_path),
        language="C",
        standard="",
        defines={"SHARED": "1"},
    )
    unit_cxx = _cu(
        source=str(src_cxx),
        directory=str(tmp_path),
        language="CXX",
        standard="",
        defines={"SHARED": "1"},
    )
    ev = BuildEvidence(compile_units=[unit_c, unit_cxx])
    result = resolve_header_compile_context(
        ev, [header], lang="c++", lang_explicit=True
    )
    assert result.matched is True
    assert result.matched_unit_count == 1
    assert result.context is not None
    assert "-DSHARED=1" in result.context.gcc_option_tokens


def test_resolve_mixed_language_units_without_forced_language_still_ambiguous(
    tmp_path: Path,
) -> None:
    """Companion to the test above: WITHOUT an explicit forced language, the
    identical two-unit (one C, one C++) setup must still correctly raise
    ``HeaderCompileContextAmbiguousError`` -- the forced-language narrowing
    must never kick in, and never mask, a genuine language disagreement the
    caller hasn't resolved."""
    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src_c = tmp_path / "a.c"
    src_c.write_text('#include "widget.h"\n', encoding="utf-8")
    src_cxx = tmp_path / "b.cpp"
    src_cxx.write_text('#include "widget.h"\n', encoding="utf-8")
    unit_c = _cu(source=str(src_c), directory=str(tmp_path), language="C", standard="")
    unit_cxx = _cu(
        source=str(src_cxx), directory=str(tmp_path), language="CXX", standard=""
    )
    ev = BuildEvidence(compile_units=[unit_c, unit_cxx])
    with pytest.raises(HeaderCompileContextAmbiguousError):
        resolve_header_compile_context(ev, [header])


def test_resolve_forced_language_falls_back_to_unfiltered_set_when_no_unit_matches(
    tmp_path: Path,
) -> None:
    """When an explicit forced language names something no matched compile
    unit actually is (here: forcing C++ while every matched unit is C), the
    full, unfiltered matched set is used instead of narrowing to an empty
    one -- narrowing to nothing would silently discard real L3 evidence
    this function has no way to resolve a language mismatch for. A single
    agreeing C unit still resolves to one context (not an ambiguity, and
    not "no evidence")."""
    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src = tmp_path / "a.c"
    src.write_text('#include "widget.h"\n', encoding="utf-8")
    unit = _cu(source=str(src), directory=str(tmp_path), language="C", standard="c17")
    ev = BuildEvidence(compile_units=[unit])
    result = resolve_header_compile_context(
        ev, [header], lang="c++", lang_explicit=True
    )
    assert result.matched is True
    assert result.matched_unit_count == 1
    assert result.context is not None
    # The lone C unit's own -std=c17 conflicts with the forced C++ language
    # (via _standard_conflicts_with_forced_language), so it's still omitted
    # -- this is the pre-existing round-7 behavior, unaffected by the
    # fallback-to-unfiltered path itself.
    assert not any(t.startswith("-std=") for t in result.context.gcc_option_tokens)


def test_explicit_pin_of_covers_bare_operand_and_malformed_options_branches() -> None:
    """Direct unit coverage for ``_ExplicitPin.of``'s less-common branches
    (bare-operand ``-target``/``-isysroot``/``-D``/``-U`` spellings, and a
    malformed ``gcc_options`` string) that no existing end-to-end
    ``resolve_header_compile_context(..., explicit=...)`` case happens to
    exercise -- these are pre-existing branches from earlier P0.3 review
    rounds, added here per Codex's coverage note on this PR rather than a
    fresh finding of this pass."""
    from abicheck.buildsource.header_compile_context import _ExplicitPin

    # Malformed gcc_options (unbalanced quote) must degrade to "no tokens
    # from it" rather than raising.
    pin = _ExplicitPin.of(CompileContext(gcc_options="'unterminated"))
    assert pin == _ExplicitPin()

    # Bare (space-separated-operand) spellings of -target/-isysroot/-D/-U.
    pin = _ExplicitPin.of(
        CompileContext(
            gcc_option_tokens=(
                "-target",
                "aarch64-linux-gnu",
                "-isysroot",
                "/sdk",
                "-D",
                "FOO=1",
                "-U",
                "BAR",
            )
        )
    )
    assert pin.target_triple is True
    assert pin.sysroot is True
    assert "FOO" in pin.defines
    assert "BAR" in pin.undefines


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


def test_context_flags_excludes_relative_sysroot_duplicate_after_structured_field(
    tmp_path: Path,
) -> None:
    """Reviewer finding: a raw combined-form sysroot survivor in
    ``abi_relevant_flags`` (e.g. ``--sysroot=sdk``, still relative to the
    compile unit's own ``directory``) must not be appended after the
    already-rendered, absolute structured ``--sysroot=<abs>`` token --
    last-flag-wins compiler semantics would otherwise let the later,
    uncorrected relative flag silently override the correct one, so the
    header gets parsed against a sysroot relative to abicheck's own current
    directory instead of the compile unit's.

    Reproduces the exact ``CompileDbAdapter`` shape from the finding: the
    structured field resolves to an absolute path while a *differently
    spelled*, still-relative raw duplicate
    (``('--sysroot=/.../sdk', '--sysroot=sdk')``) survives independently in
    ``abi_relevant_flags``, since the adapter's raw-flag extraction and its
    structured-field derivation are two independent passes over the same
    argv.
    """
    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src = tmp_path / "widget.cpp"
    src.write_text('#include "widget.h"\n', encoding="utf-8")
    sdk_dir = tmp_path / "sdk"
    cu = _cu(
        source=str(src),
        directory=str(tmp_path),
        sysroot=str(sdk_dir),
        # The raw, uncorrected combined-form survivor a real adapter can
        # independently capture alongside the resolved, absolute structured
        # `sysroot` field -- relative to `cu.directory`, not abicheck's cwd.
        abi_relevant_flags=["--sysroot=sdk", "-fPIC"],
    )
    ev = BuildEvidence(compile_units=[cu])
    result = resolve_header_compile_context(ev, [header])
    assert result.context is not None
    tokens = result.context.gcc_option_tokens
    expected = f"--sysroot={sdk_dir.as_posix()}"
    # Only the correct, absolute structured rendering appears -- the raw
    # relative duplicate is excluded entirely, not merely deduplicated
    # against a differently-spelled copy of itself.
    assert tokens.count(expected) == 1
    assert "--sysroot=sdk" not in tokens
    assert "-fPIC" in tokens
    # And, precisely: the absolute sysroot flag is not immediately
    # shadowed by anything else naming --sysroot/-isysroot afterward.
    sysroot_idx = tokens.index(expected)
    assert not any(
        t.startswith(("--sysroot", "-isysroot")) for t in tokens[sysroot_idx + 1 :]
    )


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


def test_resolve_explicit_define_pin_also_excuses_raw_abi_flag_survivor_disagreement(
    tmp_path: Path,
) -> None:
    """P2 review finding (``discussion_r3787772663``): an ABI-relevant
    macro like ``_GLIBCXX_USE_CXX11_ABI`` is captured TWICE by a real
    adapter -- once into the structured ``cu.defines`` dict (already
    filtered by the pin, per the sibling test above) and once as a raw
    ``-D_GLIBCXX_USE_CXX11_ABI=<value>`` survivor in
    ``cu.abi_relevant_flags`` (``adapters.base.extract_abi_relevant_flags``'s
    ``_ABI_RELEVANT_DEFINES`` handling). Before the fix, only the structured
    copy was pin-masked -- the raw survivor still differed across the two
    units, so an explicit ``-D_GLIBCXX_USE_CXX11_ABI=1`` pin failed to
    excuse the disagreement and ``HeaderCompileContextAmbiguousError`` was
    still raised despite the documented override."""
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
        abi_relevant_flags=["-D_GLIBCXX_USE_CXX11_ABI=0"],
    )
    unit_b = _cu(
        source=str(src_b),
        directory=str(tmp_path),
        defines={"_GLIBCXX_USE_CXX11_ABI": "1"},
        abi_relevant_flags=["-D_GLIBCXX_USE_CXX11_ABI=1"],
    )
    ev = BuildEvidence(compile_units=[unit_a, unit_b])
    explicit = CompileContext(gcc_option_tokens=("-D_GLIBCXX_USE_CXX11_ABI=1",))
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


def test_resolve_msvc_std_colon_retained_when_disagreeing_with_populated_standard_field(
    tmp_path: Path,
) -> None:
    """P2 review finding (``discussion_r3787584574``): ``clang-cl`` accepts
    BOTH GCC/Clang's ``-std=`` and MSVC's ``/std:`` on one command line, and
    per real ``clang-cl`` semantics the LATER, MSVC-style ``/std:`` wins --
    confirmed empirically (``clang-cl -std=c++17 /std:c++20`` compiles under
    C++20, ``-std=`` ignored). ``cu.standard`` here is populated to
    ``"c++17"`` by the co-present ``-std=c++17`` token (mirroring
    ``build_context.py``'s unconditional ``-std=`` capture), NOT by the
    disagreeing ``/std:c++20`` survivor also present in
    ``abi_relevant_flags`` -- so ``bool(cu.standard)`` alone (the previous,
    now-wrong gate) would have wrongly treated ``/std:c++20`` as redundant
    with the structured field and masked it away. The fix instead compares
    the ``/std:`` token's own value against ``cu.standard`` and, finding
    them disagree, retains ``/std:c++20`` in the rendered context -- which
    is what a real ``clang-cl`` invocation actually honors."""
    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src = tmp_path / "a.cpp"
    src.write_text('#include "widget.h"\n', encoding="utf-8")
    unit = _cu(
        source=str(src),
        directory=str(tmp_path),
        standard="c++17",
        abi_relevant_flags=["-std=c++17", "/std:c++20"],
    )
    ev = BuildEvidence(compile_units=[unit])
    result = resolve_header_compile_context(ev, [header])
    assert result.matched is True
    assert result.context is not None
    tokens = list(result.context.gcc_option_tokens)
    assert "/std:c++20" in tokens
    # -std=c++17 (rendered from the structured field) must come *before*
    # /std:c++20 in the final token list, so real compiler last-flag-wins
    # semantics let /std:c++20 -- what clang-cl actually honors -- win.
    assert tokens.index("-std=c++17") < tokens.index("/std:c++20")


def test_resolve_msvc_std_colon_disagreement_raises_even_with_standard_field_populated(
    tmp_path: Path,
) -> None:
    """Companion to the above: since ``/std:`` now stays in the per-unit
    ambiguity signature whenever it disagrees with ``cu.standard`` (rather
    than being unconditionally masked once ``cu.standard`` is merely
    non-empty), two compile units both carrying a populated ``cu.standard``
    from a co-present ``-std=`` -- but disagreeing ``/std:`` survivors --
    must still raise ``HeaderCompileContextAmbiguousError``, not silently
    collapse into one signature."""
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
        abi_relevant_flags=["-std=c++17", "/std:c++20"],
    )
    unit_b = _cu(
        source=str(src_b),
        directory=str(tmp_path),
        standard="c++17",
        abi_relevant_flags=["-std=c++17", "/std:c++23"],
    )
    ev = BuildEvidence(compile_units=[unit_a, unit_b])
    with pytest.raises(HeaderCompileContextAmbiguousError):
        resolve_header_compile_context(ev, [header])


def test_resolve_msvc_std_colon_retained_when_values_agree_on_clang_cl(
    tmp_path: Path,
) -> None:
    """P2 review finding (``discussion_r3787672845``): even when the
    ``/std:<value>`` token AGREES with the structured ``cu.standard`` field,
    the two spellings are still NOT interchangeable on a clang-cl-dialect
    compile unit. ``clang-cl /?`` documents ``/std:<value>`` as "Set
    language version," while a bare ``-std=c++20`` with no ``/std:`` at all
    produces "warning: unknown argument ignored" and compiles at clang-cl's
    *default* dialect, not C++20 -- so dropping ``/std:c++20`` here (the
    pre-fix behavior, since the two values genuinely agree) would silently
    change the dialect L2 actually replays under. A unit detected as
    MSVC/clang-cl-dialect (via its own ``argv``) must retain ``/std:c++20``
    regardless of value agreement."""
    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src = tmp_path / "a.cpp"
    src.write_text('#include "widget.h"\n', encoding="utf-8")
    unit = _cu(
        source=str(src),
        directory=str(tmp_path),
        standard="c++20",
        abi_relevant_flags=["-std=c++20", "/std:c++20"],
        argv=["clang-cl", "-std=c++20", "/std:c++20", "-c", str(src)],
    )
    ev = BuildEvidence(compile_units=[unit])
    result = resolve_header_compile_context(ev, [header])
    assert result.matched is True
    assert result.context is not None
    tokens = list(result.context.gcc_option_tokens)
    assert "/std:c++20" in tokens
    # The structurally-rendered -std=c++20 comes first; /std:c++20 -- what
    # clang-cl actually honors -- must come after it (last-flag-wins).
    assert tokens.index("-std=c++20") < tokens.index("/std:c++20")


def test_resolve_msvc_std_colon_agreement_across_units_stays_unambiguous_and_retained(
    tmp_path: Path,
) -> None:
    """Companion to the test above at multi-unit scope: two clang-cl units
    that fully agree (including on ``/std:``) must still resolve to a
    single, non-ambiguous context, and the retained ``/std:c++20`` survives
    into the rendered command for that single context."""
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
        abi_relevant_flags=["-std=c++20", "/std:c++20"],
        argv=["clang-cl", "-std=c++20", "/std:c++20", "-c", str(src_a)],
    )
    unit_b = _cu(
        source=str(src_b),
        directory=str(tmp_path),
        standard="c++20",
        abi_relevant_flags=["-std=c++20", "/std:c++20"],
        argv=["clang-cl", "-std=c++20", "/std:c++20", "-c", str(src_b)],
    )
    ev = BuildEvidence(compile_units=[unit_a, unit_b])
    result = resolve_header_compile_context(ev, [header])
    assert result.matched is True
    assert result.matched_unit_count == 2
    assert result.context is not None
    assert "/std:c++20" in list(result.context.gcc_option_tokens)


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


def test_derive_l2_compile_context_omits_conflicting_c_standard_when_cxx_forced(
    tmp_path: Path,
) -> None:
    """discussion_r3787398644, threaded down to derive_l2_compile_context:
    a real compile_commands.json matching to a C compile unit
    (standard=c17, via the .c source extension), with the caller explicitly
    forcing lang="c++" -- the derived -std=c17 must not be forwarded."""
    from abicheck.buildsource.l2_seed import derive_l2_compile_context

    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src = tmp_path / "widget.c"
    src.write_text('#include "widget.h"\n', encoding="utf-8")
    _write_compile_db(tmp_path, src, ["-std=c17"])

    ctx, cleanups = derive_l2_compile_context(
        [header], None, tmp_path, lang="c++", lang_explicit=True
    )
    try:
        assert ctx is not None
        assert not any(t.startswith("-std=") for t in ctx.gcc_option_tokens), (
            ctx.gcc_option_tokens
        )
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
    contract -- ``pack_io.load()`` then raises decoding it."""
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / "manifest.json").write_text("{not valid json", encoding="utf-8")


def test_derive_l2_include_dirs_corrupt_sources_pack_degrades_to_empty(
    tmp_path: Path,
) -> None:
    """P2 regression: a ``--sources`` pack recognized by ``is_pack_dir`` but
    failing to load (corrupt ``manifest.json``) must degrade this best-effort
    seeding to ``([], [])``, not raise -- ``pack_io.load()`` used to
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


# seed_includes_and_fold_compile_context's own direct branch coverage lives
# in tests/test_non_elf_dump_l2_seed.py (room there; this file is near the
# 2000-line hard cap) -- see its "seed_includes_and_fold_compile_context
# branch coverage" section.


# section 3 (service_input_resolution / buildsource.l2_seed._merge_l3_compile_context
# wiring) moved to tests/test_header_compile_context_merge.py -- split out
# during the P0.3 follow-up round 2 merge against main to keep this file
# under the 2000-line hard cap.


def test_seeded_compile_context_noop_without_sources(tmp_path: Path) -> None:
    from abicheck.api_types import InputSpec
    from abicheck.service_compare_evidence import SideEvidence
    from abicheck.service_input_resolution import _seeded_includes_and_compile_context

    side = InputSpec(path=tmp_path / "lib.so", headers=(tmp_path / "h.h",))
    evidence = SideEvidence(
        headers=[tmp_path / "h.h"], compile=None, collect_mode="off", dump_manifest=None
    )
    includes, ctx, applied, cleanups = _seeded_includes_and_compile_context(
        side, evidence
    )
    assert (includes, ctx, applied, cleanups) == ([], None, False, [])


def test_seeded_includes_and_compile_context_preserves_none_on_no_op_fold(
    monkeypatch, tmp_path: Path
) -> None:
    """Codex review, fresh evidence: `seed_includes_and_fold_compile_context`
    always returns a real `CompileContext` for its own `l3_effective_context`
    (built fresh from individual kwargs, never literally the caller's
    `evidence.compile`) -- so when the fold finds nothing (`applied=False`)
    and the caller supplied no context of its own, the wrapper must convert
    that fabricated default back to `None` rather than silently promoting a
    `None` into a real object. `service_dump_cache._dump_is_cacheable` only
    permits caching when `compile is None`, so losing this distinction would
    silently disable caching for an otherwise-cacheable typed dump/compare
    operand whenever unrelated build evidence was supplied and matched
    nothing."""
    from abicheck.api_types import InputSpec
    from abicheck.compile_context import CompileContext
    from abicheck.service_compare_evidence import SideEvidence
    from abicheck.service_input_resolution import _seeded_includes_and_compile_context

    def _fake_seed(*, pending_cleanups, **kwargs):
        # Mirrors the real primitive's own no-op-fold return shape: a fresh,
        # default-valued CompileContext, not the caller's (here None) one.
        return [], False, CompileContext(), ()

    monkeypatch.setattr(
        "abicheck.buildsource.l2_seed.seed_includes_and_fold_compile_context",
        _fake_seed,
    )

    header = tmp_path / "h.h"
    header.write_text("void f();\n", encoding="utf-8")
    side = InputSpec(path=tmp_path / "lib.so", sources=tmp_path, headers=(header,))
    evidence = SideEvidence(
        headers=[header], compile=None, collect_mode="off", dump_manifest=None
    )
    includes, ctx, applied, cleanups = _seeded_includes_and_compile_context(
        side, evidence
    )
    assert applied is False
    assert ctx is None


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


def test_resolve_side_snapshot_forwards_symbols_only_and_debug_presence_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PR C (dump/scan resolver convergence): ``symbols_only``/
    ``debug_presence_only`` reach ``service.resolve_input`` unchanged.

    Before this, only ``scan_engine._build_new_snapshot`` (which calls
    ``service.resolve_input`` directly, bypassing this shared primitive) could
    express either flag — the shared ``resolve_side_snapshot``/
    ``_resolve_side_snapshot_impl`` primitive silently dropped them, always
    forwarding ``False``/``False`` regardless of what the caller passed.
    """
    from abicheck import service_input_resolution as sir
    from abicheck.api_types import InputSpec
    from abicheck.model import AbiSnapshot
    from abicheck.service_compare_evidence import SideEvidence

    so = tmp_path / "lib.so"
    so.write_bytes(b"\x7fELF" + b"\x00" * 100)

    captured: dict[str, object] = {}

    def _fake_resolve_input(*args: object, **kwargs: object) -> AbiSnapshot:
        captured.update(kwargs)
        return AbiSnapshot(library="lib", version="1.0", from_headers=False)

    import abicheck.service as service_mod

    monkeypatch.setattr(service_mod, "resolve_input", _fake_resolve_input)

    side = InputSpec(path=so, version="1.0")
    evidence = SideEvidence(
        headers=[], compile=None, collect_mode="off", dump_manifest=None
    )
    sir.resolve_side_snapshot(
        side,
        evidence,
        lang="c++",
        header_backend="auto",
        fmt="elf",
        public_headers=[],
        public_header_dirs=[],
        symbols_only=True,
        debug_presence_only=True,
    )
    assert captured["symbols_only"] is True
    assert captured["debug_presence_only"] is True

    # Default is unchanged False/False for every pre-existing caller.
    captured.clear()
    sir.resolve_side_snapshot(
        side,
        evidence,
        lang="c++",
        header_backend="auto",
        fmt="elf",
        public_headers=[],
        public_header_dirs=[],
    )
    assert captured["symbols_only"] is False
    assert captured["debug_presence_only"] is False


def test_resolve_side_snapshot_forwards_only_explicit_includes_as_public_include_search_dirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round 13 (Codex review): the shared ``resolve_side_snapshot``/
    ``_resolve_side_snapshot_impl`` primitive fed its own build-derived,
    auto-widened ``includes`` local straight into ``service.resolve_input``
    with no separate ``public_include_search_dirs`` -- so provenance
    widening picked up an auto-derived seed directory the same way the
    already-fixed ELF/PE/Mach-O CLI resolvers used to. This primitive is
    shared by `compare`'s implicit-dump operand and `dump`'s typed
    `DumpRequest`/`run_dump_request` API, so the gap reached both.
    """
    from abicheck import service_input_resolution as sir
    from abicheck.api_types import InputSpec
    from abicheck.model import AbiSnapshot
    from abicheck.service_compare_evidence import SideEvidence

    so = tmp_path / "lib.so"
    so.write_bytes(b"\x7fELF" + b"\x00" * 100)
    explicit_dir = tmp_path / "explicit"

    captured: dict[str, object] = {}

    def _fake_resolve_input(*args: object, **kwargs: object) -> AbiSnapshot:
        captured.update(kwargs)
        return AbiSnapshot(library="lib", version="1.0", from_headers=False)

    import abicheck.service as service_mod

    monkeypatch.setattr(service_mod, "resolve_input", _fake_resolve_input)

    side = InputSpec(path=so, version="1.0", includes=(explicit_dir,))
    evidence = SideEvidence(
        headers=[], compile=None, collect_mode="off", dump_manifest=None
    )
    sir.resolve_side_snapshot(
        side,
        evidence,
        lang="c++",
        header_backend="auto",
        fmt="elf",
        public_headers=[],
        public_header_dirs=[],
    )
    assert captured["public_include_search_dirs"] == [explicit_dir]


def test_resolve_side_snapshot_omits_conflicting_c_standard_when_cxx_forced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """discussion_r3787398644, threaded all the way through resolve_side_
    snapshot (the exact caller ``service_dump_pipeline.run_dump_request``
    uses): a matched C compile unit (standard=c17) with the side's own
    ``lang="c++", lang_explicit=True`` must not carry -std=c17 into the
    compile context handed to ``service.resolve_input``.

    Without the fix, ``compile_ctx.gcc_option_tokens`` contains
    "-std=c17" here -- exactly the token a real forced-C++ Clang/CastXML
    invocation rejects (see the module-level docstring in
    ``header_compile_context._context_flags`` for the confirmed repro).
    """
    from abicheck import service_input_resolution as sir
    from abicheck.api_types import InputSpec
    from abicheck.model import AbiSnapshot
    from abicheck.service_compare_evidence import SideEvidence

    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src = tmp_path / "widget.c"
    src.write_text('#include "widget.h"\n', encoding="utf-8")
    _write_compile_db(tmp_path, src, ["-std=c17"])

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
        lang_explicit=True,
        header_backend="clang",
        fmt="elf",
        public_headers=[],
        public_header_dirs=[],
    )
    compile_ctx = captured["compile"]
    assert isinstance(compile_ctx, CompileContext)
    assert not any(t.startswith("-std=") for t in compile_ctx.gcc_option_tokens), (
        compile_ctx.gcc_option_tokens
    )
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
def test_e2e_forced_cxx_dump_succeeds_against_c_compile_unit_std(
    c_widget_lib: tuple[Path, Path, Path],
) -> None:
    """End-to-end reproduction of ``discussion_r3787398644``'s own repro:
    ``run_dump_request`` with a real ``gcc -std=c17`` compile database
    matched against the requested header(s), and the caller explicitly
    forcing a C++ parse (``DumpRequest``'s own default ``lang="c++"``, plus
    ``lang_explicit=True``).

    Before the fix, this raises: the matched C compile unit's own
    ``-std=c17`` is forwarded verbatim into the forced-C++ Clang header
    invocation, and Clang aborts with "invalid argument '-std=c17' not
    allowed with 'C++'" -- reproduced directly against this fixture without
    the fix applied. After the fix, the conflicting derived standard is
    omitted and the dump succeeds, with the real L3 context (target
    triple/defines/etc.) still genuinely applied.
    """
    from abicheck import service
    from abicheck.api_types import DumpRequest, InputSpec

    so, header, src_dir = c_widget_lib
    req = DumpRequest(
        input=InputSpec(path=so, headers=(header,), version="1.0", sources=src_dir),
        frontend="clang",
        lang_explicit=True,  # lang defaults to "c++" -- the finding's own repro
    )
    snap = service.run_dump_request(req)
    assert snap.from_headers is True
    assert snap.parsed_with_build_context is True
    widget = next(t for t in snap.types if t.name == "Widget")
    assert [f.name for f in widget.fields] == ["x", "y"]


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
