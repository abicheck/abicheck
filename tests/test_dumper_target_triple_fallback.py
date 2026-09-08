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

"""``dumper._header_ast_parser``'s clang-backend call site (Codex review,
fresh evidence): when a live ``clang -print-target-triple`` probe fails, the
resulting parser must still carry an explicitly-requested ``--target=``
triple, or (failing that) a real ``sys.platform``-based fallback string,
rather than losing it to a bare ``None``.

Split out of ``test_dumper_clang.py`` (already at its ADR-061 file-size debt
cap, see ``architecture/debt.yaml``) rather than added there.
``_compiler_options.explicit_target_triple`` itself (the pure recovery
helper) has its own dedicated coverage in ``test_compiler_options.py``; this
module covers only the one thing that lives in ``dumper.py`` -- that the
``_run_clang()`` call site actually wires the fallback in, end to end.
"""

from __future__ import annotations

import shutil
import sys

import pytest

from abicheck import dumper
from abicheck.dumper import _header_ast_parser
from abicheck.dumper_clang import (
    _ClangAstParser,
    _default_clang_bin_name,
    _is_default_clang_bin,
)


def _tu(*inner: dict) -> dict:
    return {"kind": "TranslationUnitDecl", "inner": list(inner)}


class TestDefaultClangBinName:
    """The plain, unconfigured host binary name a resolved ``clang_bin``
    is compared against to tell "genuinely explicit/cross compiler" from
    "the plain host default was used regardless of what was requested"
    (Codex review, ninth round, fresh evidence)."""

    def test_cpp_style_compiler_selects_clangxx(self) -> None:
        for compiler in ("c++", "g++", "clang++"):
            assert _default_clang_bin_name(compiler) == "clang++"

    def test_other_compiler_selects_clang(self) -> None:
        for compiler in ("cc", "gcc", "clang", "icpx"):
            assert _default_clang_bin_name(compiler) == "clang"


@pytest.mark.skipif(shutil.which("clang") is None, reason="needs a real clang on PATH")
class TestIsDefaultClangBin:
    """Real executable identity, not raw spelling (Codex review, tenth
    round, fresh evidence): an absolute path to the exact same binary the
    plain default name resolves to on ``PATH`` (e.g. ``--compiler
    /usr/bin/clang`` when ``clang`` on `PATH` IS `/usr/bin/clang`) is
    still the plain host default, just spelled differently -- a prior
    revision of this check compared `clang_bin` against
    `_default_clang_bin_name` with plain string equality, which an
    absolute-path spelling of the identical binary always failed."""

    def test_bare_default_name_matches(self) -> None:
        assert _is_default_clang_bin("clang", "cc") is True

    def test_absolute_path_to_the_same_binary_matches(self) -> None:
        resolved = shutil.which("clang")
        assert resolved is not None
        assert _is_default_clang_bin(resolved, "cc") is True

    def test_a_genuinely_different_binary_does_not_match(self) -> None:
        assert _is_default_clang_bin("aarch64-apple-darwin-clang", "cc") is False

    def test_unresolvable_clang_bin_does_not_match(self) -> None:
        # Falls back to string comparison when clang_bin can't be resolved
        # on disk -- an unresolvable identity is not evidence of sameness.
        assert _is_default_clang_bin("/definitely/not/a/real/path/clang", "cc") is False


def test_probe_failure_recovers_explicit_target_triple(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A probe failure alone can't distinguish "no target requested" from
    "an explicit, unprobeable cross-target" -- this call site's own
    sys.platform-based guess (tested below) is only safe for the former,
    so the explicit request must survive a probe failure intact."""
    ast = _tu({"kind": "TranslationUnitDecl", "inner": []})
    monkeypatch.setattr(
        dumper, "_clang_header_dump", lambda *a, **k: (ast, None, False)
    )
    # Simulates a failed `-print-target-triple` probe (compiler resolution
    # mismatch, a sandboxed/restricted CI runner, ...) without needing a
    # real subprocess failure.
    monkeypatch.setattr(dumper, "_configured_target_triple", lambda *a, **k: None)

    parser = _header_ast_parser(
        [],
        [],
        backend="clang",
        compiler="c++",
        gcc_path=None,
        gcc_prefix=None,
        gcc_options="--target=x86_64-unknown-linux-gnu",
        sysroot=None,
        nostdinc=False,
        lang=None,
        exported_dynamic=set(),
        exported_static=set(),
        public_header_paths=[],
        public_dir_paths=[],
    )

    assert isinstance(parser, _ClangAstParser)
    assert parser._target_triple == "x86_64-unknown-linux-gnu"


def test_probe_failure_with_no_explicit_target_falls_back_to_sys_platform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The genuinely-ambiguous case (no explicit --target=, probe failed)
    must reach the parser as a real, sys.platform-based triple string --
    NOT bare None. extract.headers.clang.context.is_darwin_target itself
    never guesses from a bare None (that shape also serves direct,
    no-pipeline unit-test construction of the parser, which must stay
    conservative regardless of host OS); only this real pipeline call
    site, which knows a probe was genuinely attempted, earns the guess."""
    ast = _tu({"kind": "TranslationUnitDecl", "inner": []})
    monkeypatch.setattr(
        dumper, "_clang_header_dump", lambda *a, **k: (ast, None, False)
    )
    monkeypatch.setattr(dumper, "_configured_target_triple", lambda *a, **k: None)

    parser = _header_ast_parser(
        [],
        [],
        backend="clang",
        compiler="c++",
        gcc_path=None,
        gcc_prefix=None,
        gcc_options="-O2",
        sysroot=None,
        nostdinc=False,
        lang=None,
        exported_dynamic=set(),
        exported_static=set(),
        public_header_paths=[],
        public_dir_paths=[],
    )

    assert isinstance(parser, _ClangAstParser)
    assert parser._target_triple == sys.platform


@pytest.mark.skipif(shutil.which("clang") is None, reason="needs a real clang on PATH")
def test_probe_failure_with_an_absolute_path_to_the_native_clang_still_guesses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--compiler`` naming the native default Clang by an absolute path
    (e.g. ``/usr/bin/clang``, resolved here via a real ``shutil.which``)
    is still the plain host default, just spelled differently -- the AST
    genuinely came from the native compiler, so the `sys.platform` guess
    must still apply (Codex review, tenth round, fresh evidence)."""
    resolved_native_clang = shutil.which("clang")
    assert resolved_native_clang is not None
    ast = _tu({"kind": "TranslationUnitDecl", "inner": []})
    monkeypatch.setattr(
        dumper, "_clang_header_dump", lambda *a, **k: (ast, None, False)
    )
    monkeypatch.setattr(dumper, "_configured_target_triple", lambda *a, **k: None)
    monkeypatch.setattr(
        dumper, "_resolve_clang_bin", lambda *a, **k: resolved_native_clang
    )
    monkeypatch.setattr(sys, "platform", "darwin")

    parser = _header_ast_parser(
        [],
        [],
        backend="clang",
        compiler="cc",
        gcc_path=resolved_native_clang,
        gcc_prefix=None,
        gcc_options="-O2",
        sysroot=None,
        nostdinc=False,
        lang=None,
        exported_dynamic=set(),
        exported_static=set(),
        public_header_paths=[],
        public_dir_paths=[],
    )

    assert isinstance(parser, _ClangAstParser)
    assert parser._target_triple == "darwin"


def test_probe_failure_with_a_resolved_cross_compiler_does_not_guess_sys_platform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit ``--compiler``/``--compiler-prefix`` cross-toolchain that
    ``_resolve_clang_bin`` actually adopts (its own resolved ``clang_bin``
    differs from the plain host default) carries no relationship to the
    HOST OS at all -- an Apple-targeting compiler run on Linux, or vice
    versa. Guessing `sys.platform` there risks the same misclassification
    in either direction the CL-mode guess already guards against, just
    from a different evidence source. Stays bare None -- the same
    conservative default ``is_darwin_target(None)`` already answers False
    for."""
    ast = _tu({"kind": "TranslationUnitDecl", "inner": []})
    monkeypatch.setattr(
        dumper, "_clang_header_dump", lambda *a, **k: (ast, None, False)
    )
    monkeypatch.setattr(dumper, "_configured_target_triple", lambda *a, **k: None)
    monkeypatch.setattr(
        dumper, "_resolve_clang_bin", lambda *a, **k: "aarch64-apple-darwin-clang++"
    )
    monkeypatch.setattr(sys, "platform", "darwin")

    parser = _header_ast_parser(
        [],
        [],
        backend="clang",
        compiler="c++",
        gcc_path=None,
        gcc_prefix="aarch64-apple-darwin-",
        gcc_options="-O2",
        sysroot=None,
        nostdinc=False,
        lang=None,
        exported_dynamic=set(),
        exported_static=set(),
        public_header_paths=[],
        public_dir_paths=[],
    )

    assert isinstance(parser, _ClangAstParser)
    assert parser._target_triple is None


def test_probe_failure_with_a_resolved_cross_compiler_still_recovers_explicit_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cross-compiler gate only ever suppresses the `sys.platform`
    GUESS -- an explicit ``--target=`` is still real evidence and is
    recovered regardless of whether a cross-compiler is also configured."""
    ast = _tu({"kind": "TranslationUnitDecl", "inner": []})
    monkeypatch.setattr(
        dumper, "_clang_header_dump", lambda *a, **k: (ast, None, False)
    )
    monkeypatch.setattr(dumper, "_configured_target_triple", lambda *a, **k: None)
    monkeypatch.setattr(
        dumper, "_resolve_clang_bin", lambda *a, **k: "aarch64-apple-darwin-clang++"
    )

    parser = _header_ast_parser(
        [],
        [],
        backend="clang",
        compiler="c++",
        gcc_path=None,
        gcc_prefix="aarch64-apple-darwin-",
        gcc_options="--target=x86_64-unknown-linux-gnu",
        sysroot=None,
        nostdinc=False,
        lang=None,
        exported_dynamic=set(),
        exported_static=set(),
        public_header_paths=[],
        public_dir_paths=[],
    )

    assert isinstance(parser, _ClangAstParser)
    assert parser._target_triple == "x86_64-unknown-linux-gnu"


def test_probe_failure_with_a_gcc_path_resolve_ignores_still_guesses_sys_platform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``gcc_path`` naming a non-clang-family binary (e.g. a plain GCC
    path) is silently ignored by ``_resolve_clang_bin``, which falls back
    to the plain host default regardless (Codex review, fresh evidence,
    ninth round): the RESOLVED `clang_bin` is what determines whether the
    guess applies, not whether `gcc_path`/`gcc_prefix` was merely passed
    -- the binary that actually produced the AST here IS the plain host
    compiler, so its target genuinely is approximated by `sys.platform`."""
    ast = _tu({"kind": "TranslationUnitDecl", "inner": []})
    monkeypatch.setattr(
        dumper, "_clang_header_dump", lambda *a, **k: (ast, None, False)
    )
    monkeypatch.setattr(dumper, "_configured_target_triple", lambda *a, **k: None)
    # Simulates _resolve_clang_bin ignoring a non-clang-family gcc_path and
    # falling back to the plain default, exactly as the real function does.
    monkeypatch.setattr(dumper, "_resolve_clang_bin", lambda *a, **k: "clang++")
    monkeypatch.setattr(sys, "platform", "darwin")

    parser = _header_ast_parser(
        [],
        [],
        backend="clang",
        compiler="c++",
        gcc_path="/usr/bin/gcc",
        gcc_prefix=None,
        gcc_options="-O2",
        sysroot=None,
        nostdinc=False,
        lang=None,
        exported_dynamic=set(),
        exported_static=set(),
        public_header_paths=[],
        public_dir_paths=[],
    )

    assert isinstance(parser, _ClangAstParser)
    assert parser._target_triple == "darwin"


def test_successful_probe_is_never_overridden_by_explicit_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successfully-probed triple is authoritative -- the explicit
    ``--target=`` fallback only ever fills a bare None, never replaces a
    real probe result (e.g. one that differs because the compiler itself
    resolved an alias or a default sysroot-implied target)."""
    ast = _tu({"kind": "TranslationUnitDecl", "inner": []})
    monkeypatch.setattr(
        dumper, "_clang_header_dump", lambda *a, **k: (ast, None, False)
    )
    monkeypatch.setattr(
        dumper, "_configured_target_triple", lambda *a, **k: "aarch64-apple-macos11"
    )

    parser = _header_ast_parser(
        [],
        [],
        backend="clang",
        compiler="c++",
        gcc_path=None,
        gcc_prefix=None,
        gcc_options="--target=x86_64-unknown-linux-gnu",
        sysroot=None,
        nostdinc=False,
        lang=None,
        exported_dynamic=set(),
        exported_static=set(),
        public_header_paths=[],
        public_dir_paths=[],
    )

    assert isinstance(parser, _ClangAstParser)
    assert parser._target_triple == "aarch64-apple-macos11"


def test_probe_failure_under_a_cl_style_driver_recovers_the_honored_spelling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A CL-style driver (clang-cl/dpcpp-cl) documents and honors the one
    attached, double-dash ``--target=<value>`` spelling -- a real
    ``clang-cl --target=x86_64-apple-darwin`` genuinely selects that target
    and produces the corresponding decorated AST names (Codex review,
    second round, fresh evidence). So a probe failure still recovers that
    spelling, same as for a GNU-style driver; only the OTHER, silently-
    ignored spellings (below) and the sys.platform guess stay suppressed."""
    ast = _tu({"kind": "TranslationUnitDecl", "inner": []})
    monkeypatch.setattr(
        dumper, "_clang_header_dump", lambda *a, **k: (ast, None, False)
    )
    monkeypatch.setattr(dumper, "_configured_target_triple", lambda *a, **k: None)
    monkeypatch.setattr(dumper, "_resolve_clang_bin", lambda *a, **k: "clang-cl")
    monkeypatch.setattr(sys, "platform", "darwin")

    parser = _header_ast_parser(
        [],
        [],
        backend="clang",
        compiler="clang-cl",
        gcc_path=None,
        gcc_prefix=None,
        gcc_options="--target=x86_64-apple-macos11",
        sysroot=None,
        nostdinc=False,
        lang=None,
        exported_dynamic=set(),
        exported_static=set(),
        public_header_paths=[],
        public_dir_paths=[],
    )

    assert isinstance(parser, _ClangAstParser)
    assert parser._target_triple == "x86_64-apple-macos11"


def test_probe_failure_under_a_cl_style_driver_recovers_the_separate_short_spelling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The separate-argument, single-dash ``-target <value>`` spelling is
    ALSO genuinely honored under CL mode (Codex review, third round,
    fresh evidence correcting the second round's over-broad claim that
    every separate-argument spelling was ignored): a real
    ``clang-cl -target x86_64-apple-darwin -print-target-triple`` exits
    successfully and prints the value back. So a probe failure still
    recovers it, same as the attached double-dash spelling."""
    ast = _tu({"kind": "TranslationUnitDecl", "inner": []})
    monkeypatch.setattr(
        dumper, "_clang_header_dump", lambda *a, **k: (ast, None, False)
    )
    monkeypatch.setattr(dumper, "_configured_target_triple", lambda *a, **k: None)
    monkeypatch.setattr(dumper, "_resolve_clang_bin", lambda *a, **k: "clang-cl")
    monkeypatch.setattr(sys, "platform", "darwin")

    parser = _header_ast_parser(
        [],
        [],
        backend="clang",
        compiler="clang-cl",
        gcc_path=None,
        gcc_prefix=None,
        gcc_options="-target x86_64-apple-macos11",
        sysroot=None,
        nostdinc=False,
        lang=None,
        exported_dynamic=set(),
        exported_static=set(),
        public_header_paths=[],
        public_dir_paths=[],
    )

    assert isinstance(parser, _ClangAstParser)
    assert parser._target_triple == "x86_64-apple-macos11"


@pytest.mark.parametrize(
    "gcc_options",
    [
        "-target=x86_64-apple-macos11",
        "--target x86_64-apple-macos11",
    ],
)
def test_probe_failure_under_a_cl_style_driver_ignores_unhonored_spellings(
    monkeypatch: pytest.MonkeyPatch, gcc_options: str
) -> None:
    """The two spellings a real CL-style driver does NOT apply (single-
    dash attached, and separate-argument double-dash) complete with an
    "unknown argument ignored" warning rather than selecting the target --
    recovering one of them as if it were real risks a WRONG platform
    guess. It also gets no sys.platform guess either (Codex review, fresh
    evidence): a real ``clang-cl -print-target-triple`` reports a Windows
    triple regardless of the HOST OS running it (cross-compiled from
    macOS included), so guessing "darwin" from a macOS host here would
    misclassify a Windows AST as Darwin. Stays bare None -- the same
    conservative default ``is_darwin_target(None)`` already answers False
    for."""
    ast = _tu({"kind": "TranslationUnitDecl", "inner": []})
    monkeypatch.setattr(
        dumper, "_clang_header_dump", lambda *a, **k: (ast, None, False)
    )
    monkeypatch.setattr(dumper, "_configured_target_triple", lambda *a, **k: None)
    monkeypatch.setattr(dumper, "_resolve_clang_bin", lambda *a, **k: "clang-cl")
    monkeypatch.setattr(sys, "platform", "darwin")

    parser = _header_ast_parser(
        [],
        [],
        backend="clang",
        compiler="clang-cl",
        gcc_path=None,
        gcc_prefix=None,
        gcc_options=gcc_options,
        sysroot=None,
        nostdinc=False,
        lang=None,
        exported_dynamic=set(),
        exported_static=set(),
        public_header_paths=[],
        public_dir_paths=[],
    )

    assert isinstance(parser, _ClangAstParser)
    assert parser._target_triple is None


def test_successful_probe_still_honored_for_a_cl_style_driver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CL-style gate only ever suppresses the two FALLBACKS -- a
    successfully-probed triple (the real ``clang-cl -print-target-triple``
    output) is still trusted outright, same as for any other driver."""
    ast = _tu({"kind": "TranslationUnitDecl", "inner": []})
    monkeypatch.setattr(
        dumper, "_clang_header_dump", lambda *a, **k: (ast, None, False)
    )
    monkeypatch.setattr(
        dumper, "_configured_target_triple", lambda *a, **k: "x86_64-pc-windows-msvc"
    )
    monkeypatch.setattr(dumper, "_resolve_clang_bin", lambda *a, **k: "clang-cl")

    parser = _header_ast_parser(
        [],
        [],
        backend="clang",
        compiler="clang-cl",
        gcc_path=None,
        gcc_prefix=None,
        gcc_options=None,
        sysroot=None,
        nostdinc=False,
        lang=None,
        exported_dynamic=set(),
        exported_static=set(),
        public_header_paths=[],
        public_dir_paths=[],
    )

    assert isinstance(parser, _ClangAstParser)
    assert parser._target_triple == "x86_64-pc-windows-msvc"


def test_probe_failure_under_a_cl_style_driver_recovers_clang_forwarded_spelling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``clang-cl``'s documented ``/clang:<arg>`` mechanism forwards <arg>
    straight through to the underlying Clang driver -- a real
    ``clang-cl /clang:--target=x86_64-apple-darwin`` genuinely selects that
    target (Codex review, fourth round, fresh evidence). So a probe
    failure recovers it too, same as the two spellings above."""
    ast = _tu({"kind": "TranslationUnitDecl", "inner": []})
    monkeypatch.setattr(
        dumper, "_clang_header_dump", lambda *a, **k: (ast, None, False)
    )
    monkeypatch.setattr(dumper, "_configured_target_triple", lambda *a, **k: None)
    monkeypatch.setattr(dumper, "_resolve_clang_bin", lambda *a, **k: "clang-cl")
    monkeypatch.setattr(sys, "platform", "darwin")

    parser = _header_ast_parser(
        [],
        [],
        backend="clang",
        compiler="clang-cl",
        gcc_path=None,
        gcc_prefix=None,
        gcc_options="/clang:--target=x86_64-apple-macos11",
        sysroot=None,
        nostdinc=False,
        lang=None,
        exported_dynamic=set(),
        exported_static=set(),
        public_header_paths=[],
        public_dir_paths=[],
    )

    assert isinstance(parser, _ClangAstParser)
    assert parser._target_triple == "x86_64-apple-macos11"


def test_a_cl_style_name_explicitly_overridden_to_gnu_mode_is_not_cl_style(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit ``--driver-mode=g++`` genuinely switches a CL-named
    binary OUT of CL mode (a real ``clang-cl --driver-mode=g++
    -print-target-triple`` reports a GNU-shaped target -- Codex review,
    fifth round, fresh evidence). So on a probe failure it gets the
    GNU-style recovery (every spelling, plus the sys.platform guess), not
    the narrowed CL-style one -- the reverse of the name-only default."""
    ast = _tu({"kind": "TranslationUnitDecl", "inner": []})
    monkeypatch.setattr(
        dumper, "_clang_header_dump", lambda *a, **k: (ast, None, False)
    )
    monkeypatch.setattr(dumper, "_configured_target_triple", lambda *a, **k: None)
    monkeypatch.setattr(dumper, "_resolve_clang_bin", lambda *a, **k: "clang-cl")

    parser = _header_ast_parser(
        [],
        [],
        backend="clang",
        compiler="clang-cl",
        gcc_path=None,
        gcc_prefix=None,
        gcc_options="--driver-mode=g++ -target=x86_64-unknown-linux-gnu",
        sysroot=None,
        nostdinc=False,
        lang=None,
        exported_dynamic=set(),
        exported_static=set(),
        public_header_paths=[],
        public_dir_paths=[],
    )

    assert isinstance(parser, _ClangAstParser)
    assert parser._target_triple == "x86_64-unknown-linux-gnu"


def test_probe_failure_with_option_selected_cl_mode_recovers_the_honored_spelling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CL mode is also selected via an explicit ``--driver-mode=cl`` on an
    otherwise generically-named ``clang`` binary (a replayed compile
    unit's own form -- see buildsource.header_compile_context's "Preserve
    an explicit --driver-mode=cl" docstring), not only via a
    ``clang-cl``-shaped binary name. This must be gated identically to the
    name-based case: the one honored spelling (attached ``--target=``) is
    still recovered on a probe failure; only the sys.platform guess and
    the other, silently-ignored spellings stay suppressed (Codex review,
    fresh evidence)."""
    ast = _tu({"kind": "TranslationUnitDecl", "inner": []})
    monkeypatch.setattr(
        dumper, "_clang_header_dump", lambda *a, **k: (ast, None, False)
    )
    monkeypatch.setattr(dumper, "_configured_target_triple", lambda *a, **k: None)
    monkeypatch.setattr(dumper, "_resolve_clang_bin", lambda *a, **k: "clang")
    monkeypatch.setattr(sys, "platform", "darwin")

    parser = _header_ast_parser(
        [],
        [],
        backend="clang",
        compiler="clang",
        gcc_path=None,
        gcc_prefix=None,
        gcc_options="--driver-mode=cl --target=x86_64-apple-macos11",
        sysroot=None,
        nostdinc=False,
        lang=None,
        exported_dynamic=set(),
        exported_static=set(),
        public_header_paths=[],
        public_dir_paths=[],
    )

    assert isinstance(parser, _ClangAstParser)
    assert parser._target_triple == "x86_64-apple-macos11"
