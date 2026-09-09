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
    clang_bin_is_explicitly_configured,
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


class TestIsDefaultClangBin:
    """Invocation BASENAME, not real executable identity or raw path
    spelling (Codex review, eleventh round, fresh evidence correcting the
    tenth): real Clang derives its own default target from ``argv[0]``,
    so a target-prefixed symlink to the exact same binary as plain
    ``clang`` (e.g. ``aarch64-apple-darwin-clang``, a real, documented
    cross-toolchain wrapper shape) is genuinely NOT the plain default --
    a prior revision of this check resolved both sides through `PATH`/
    symlinks and compared real executable IDENTITY, which wrongly
    equated that symlink with plain `clang` merely because they point at
    the same file. An absolute path to the plain binary
    (``/usr/bin/clang``) is still correctly recognized, since only the
    basename -- not the directory portion -- affects Clang's own
    behavior."""

    def test_bare_default_name_matches(self) -> None:
        assert _is_default_clang_bin("clang", "cc") is True

    def test_absolute_path_to_the_plain_binary_matches(self) -> None:
        assert _is_default_clang_bin("/usr/bin/clang", "cc") is True
        assert _is_default_clang_bin("/opt/llvm/bin/clang++", "c++") is True

    def test_a_target_prefixed_symlink_does_not_match(self) -> None:
        # Real evidence: a Clang 20 symlink named this way resolves to
        # the identical binary as plain `clang` but reports a DIFFERENT
        # -print-target-triple, driven entirely by this basename.
        assert _is_default_clang_bin("aarch64-apple-darwin-clang", "cc") is False

    def test_a_target_prefixed_symlink_by_absolute_path_does_not_match(self) -> None:
        assert (
            _is_default_clang_bin("/usr/bin/aarch64-apple-darwin-clang", "cc") is False
        )

    def test_a_native_versioned_driver_still_matches(self) -> None:
        # A packaged, version-suffixed Clang (e.g. Debian's clang-18) IS
        # still the plain host default -- the version suffix carries no
        # target information (Codex review, twelfth round, fresh
        # evidence).
        assert _is_default_clang_bin("clang-18", "cc") is True
        assert _is_default_clang_bin("/usr/bin/clang-18", "cc") is True
        assert _is_default_clang_bin("clang++-18", "c++") is True

    def test_a_versioned_target_prefixed_symlink_still_does_not_match(self) -> None:
        # Stripping the version suffix must not eat into the target
        # prefix itself -- only a trailing numeric suffix is version
        # information.
        assert _is_default_clang_bin("aarch64-apple-darwin-clang-18", "cc") is False


class TestClangBinIsExplicitlyConfigured:
    """Explicit ``--compiler``/``--compiler-prefix`` PROVENANCE, not the
    resolved binary's basename -- a wrapper explicitly configured this way
    can coincidentally share the plain default's basename while still
    genuinely not being it (Codex review, fresh evidence: such a wrapper
    can produce a real AST while not implementing ``-print-target-triple``
    at all, so `_is_default_clang_bin`'s basename check alone would wrongly
    let the last-resort `sys.platform` guess apply to it)."""

    def test_neither_given(self) -> None:
        assert clang_bin_is_explicitly_configured(None, None) is False

    def test_a_clang_family_compiler_path_is_explicit(self) -> None:
        assert clang_bin_is_explicitly_configured("/opt/wrapper/clang", None) is True

    def test_a_non_clang_family_compiler_path_is_not_explicit(self) -> None:
        # Mirrors `_resolve_clang_bin`'s own adoption condition: a
        # `gcc_path` naming a non-clang-family binary is silently ignored,
        # so its mere presence isn't "explicitly configured" for clang.
        assert clang_bin_is_explicitly_configured("/usr/bin/gcc", None) is False

    def test_a_compiler_prefix_is_explicit(self) -> None:
        assert clang_bin_is_explicitly_configured(None, "aarch64-apple-darwin-") is True


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


def test_probe_failure_with_a_response_file_does_not_guess_sys_platform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A forwarded ``@response-file`` token may hide its own
    ``-target``/``--driver-mode=`` this module cannot see without
    expanding it -- a real compiler process honors one, so a probe
    failure must not fall back to a `sys.platform` guess that could
    easily be wrong (Codex review, eleventh round, fresh evidence)."""
    ast = _tu({"kind": "TranslationUnitDecl", "inner": []})
    monkeypatch.setattr(
        dumper, "_clang_header_dump", lambda *a, **k: (ast, None, False)
    )
    monkeypatch.setattr(dumper, "_configured_target_triple", lambda *a, **k: None)
    monkeypatch.setattr(dumper, "_resolve_clang_bin", lambda *a, **k: "clang++")
    monkeypatch.setattr(sys, "platform", "darwin")

    parser = _header_ast_parser(
        [],
        [],
        backend="clang",
        compiler="c++",
        gcc_path=None,
        gcc_prefix=None,
        gcc_options="@response.rsp",
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


def test_probe_failure_with_a_response_file_alongside_a_target_does_not_recover_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CodeRabbit review, fresh evidence, correcting this test's own prior
    claim: real Clang determines its CL-vs-GNU driver mode from an early
    scan of the *entire* argument list, response files included, before
    parsing proceeds -- so a forwarded ``@response.rsp`` could itself flip
    that mode (or, per the earlier Codex finding, carry a later
    ``-target``/``--target=`` of its own) regardless of where it sits
    relative to a visible ``--target=``. With no way to see inside the
    file, any forwarded response file voids recovery of an explicit target,
    even one that appears alongside it on the visible command line -- this
    case is no longer "still recovered", it is unknown like any other."""
    ast = _tu({"kind": "TranslationUnitDecl", "inner": []})
    monkeypatch.setattr(
        dumper, "_clang_header_dump", lambda *a, **k: (ast, None, False)
    )
    monkeypatch.setattr(dumper, "_configured_target_triple", lambda *a, **k: None)
    monkeypatch.setattr(dumper, "_resolve_clang_bin", lambda *a, **k: "clang++")

    parser = _header_ast_parser(
        [],
        [],
        backend="clang",
        compiler="c++",
        gcc_path=None,
        gcc_prefix=None,
        gcc_options="@response.rsp --target=x86_64-unknown-linux-gnu",
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


@pytest.mark.skipif(shutil.which("clang") is None, reason="needs a real clang on PATH")
def test_probe_failure_with_an_explicit_absolute_path_no_longer_guesses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Superseded contract (Codex review, fresh evidence, correcting the
    tenth round's own claim): this test used to assert the `sys.platform`
    guess still applies when ``--compiler`` names the native Clang by an
    absolute path, reasoning the AST genuinely came from the native
    compiler. That reasoning predates the bare re-probe
    (`_configured_target_triple(None, ..., clang_bin)`) this module now
    tries first -- for a REAL clang binary, that re-probe essentially
    never fails (`-print-target-triple` is always supported), so reaching
    this last-resort branch at all while `--compiler` was explicitly
    adopted is now itself evidence of an anomaly (e.g. a non-conforming
    wrapper sharing the plain default's basename), not confirmation of a
    genuine native clang. `clang_bin_is_explicitly_configured` now
    suppresses the guess for any adopted `--compiler`/`--compiler-prefix`,
    real absolute-path native clang included -- the safe answer once both
    probes have failed for an explicitly-configured binary is "unknown"."""
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
    assert parser._target_triple is None


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


def test_probe_failure_with_an_explicit_config_file_does_not_guess_sys_platform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A forwarded ``--config=<file>`` may hide its own ``-target``/
    ``--driver-mode=`` this module cannot see without reading it -- a real
    Clang invocation honors one (empirically verified against a real
    Clang 18 install), so a probe failure must not fall back to either the
    bare re-probe or a `sys.platform` guess (Codex review, fresh
    evidence)."""
    ast = _tu({"kind": "TranslationUnitDecl", "inner": []})
    monkeypatch.setattr(
        dumper, "_clang_header_dump", lambda *a, **k: (ast, None, False)
    )
    monkeypatch.setattr(dumper, "_configured_target_triple", lambda *a, **k: None)
    monkeypatch.setattr(dumper, "_resolve_clang_bin", lambda *a, **k: "clang++")
    monkeypatch.setattr(sys, "platform", "darwin")

    parser = _header_ast_parser(
        [],
        [],
        backend="clang",
        compiler="c++",
        gcc_path=None,
        gcc_prefix=None,
        gcc_options="--config=darwin.cfg",
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


def test_probe_failure_with_a_custom_renamed_compiler_recovers_via_bare_reprobe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex review, fresh evidence: `_is_default_clang_bin`'s exact-
    basename comparison alone wrongly treated ANY custom rename of the
    native compiler (e.g. a company-specific wrapper symlink such as
    ``company-clang``) as if it were a cross-compiler, silently
    suppressing the host-platform fallback for it even though real Clang
    reports its own native default for that name (empirically verified
    against a real Clang 18 install: only a recognized ``<triple>-clang``
    prefix like ``aarch64-apple-darwin-clang`` changes the reported
    default; an arbitrary rename does not). A second, option-free probe of
    the SAME resolved ``clang_bin`` recovers this as real evidence instead
    of guessing from the name."""
    ast = _tu({"kind": "TranslationUnitDecl", "inner": []})
    monkeypatch.setattr(
        dumper, "_clang_header_dump", lambda *a, **k: (ast, None, False)
    )

    def _fake_configured_target_triple(
        gcc_options: str | None, gcc_option_tokens: tuple[str, ...], clang_bin: str
    ) -> str | None:
        # The real (option-bearing) probe fails; a bare re-probe of the
        # identical binary succeeds and reveals its true native default.
        if gcc_options is None and gcc_option_tokens == ():
            return "x86_64-pc-linux-gnu"
        return None

    monkeypatch.setattr(
        dumper, "_configured_target_triple", _fake_configured_target_triple
    )
    monkeypatch.setattr(dumper, "_resolve_clang_bin", lambda *a, **k: "company-clang")

    parser = _header_ast_parser(
        [],
        [],
        backend="clang",
        compiler="c",
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
    assert parser._target_triple == "x86_64-pc-linux-gnu"


def test_probe_failure_with_a_custom_renamed_compiler_falls_back_when_bare_reprobe_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Negative control for the test above: when even the bare re-probe
    fails (the binary cannot be invoked at all), the caller falls back to
    `_is_default_clang_bin`'s exact-name comparison for the final,
    narrowest `sys.platform` guess -- which stays conservative (``None``)
    for a name it cannot positively identify as the plain default."""
    ast = _tu({"kind": "TranslationUnitDecl", "inner": []})
    monkeypatch.setattr(
        dumper, "_clang_header_dump", lambda *a, **k: (ast, None, False)
    )
    monkeypatch.setattr(dumper, "_configured_target_triple", lambda *a, **k: None)
    monkeypatch.setattr(dumper, "_resolve_clang_bin", lambda *a, **k: "company-clang")
    monkeypatch.setattr(sys, "platform", "linux")

    parser = _header_ast_parser(
        [],
        [],
        backend="clang",
        compiler="c",
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
    assert parser._target_triple is None


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


def test_probe_failure_under_a_cl_style_driver_recovers_via_bare_reprobe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex review, fresh evidence, empirically verified against a real
    Clang 18 install: a target-prefixed CL-style driver name (e.g.
    ``aarch64-apple-darwin-clang-cl``) reports its own prefixed target from
    a BARE ``-print-target-triple`` re-probe -- ``clang-cl`` documents that
    flag the same way plain clang does. When the option-bearing probe
    fails and no explicit ``--target=``/``-target``/``/clang:``-forwarded
    spelling is honored, the CL branch must still try the same bare
    re-probe the GNU branch already gets, rather than giving up at
    ``None`` and leaving a genuinely Darwin-decorated name unstripped."""
    ast = _tu({"kind": "TranslationUnitDecl", "inner": []})
    monkeypatch.setattr(
        dumper, "_clang_header_dump", lambda *a, **k: (ast, None, False)
    )

    def _fake_configured_target_triple(
        gcc_options: str | None, gcc_option_tokens: tuple[str, ...], clang_bin: str
    ) -> str | None:
        # The real (option-bearing) probe fails; a bare re-probe of the
        # identical binary succeeds and reveals its true prefixed default.
        if gcc_options is None and gcc_option_tokens == ():
            return "aarch64-apple-darwin"
        return None

    monkeypatch.setattr(
        dumper, "_configured_target_triple", _fake_configured_target_triple
    )
    monkeypatch.setattr(
        dumper, "_resolve_clang_bin", lambda *a, **k: "aarch64-apple-darwin-clang-cl"
    )

    parser = _header_ast_parser(
        [],
        [],
        backend="clang",
        compiler="aarch64-apple-darwin-clang-cl",
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
    assert parser._target_triple == "aarch64-apple-darwin"


def test_successful_probe_skips_the_bare_reprobe_entirely(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex review, fresh evidence: the bare re-probe is a second real
    compiler subprocess invocation (its own 10-second timeout) -- it must
    be deferred until the primary, option-bearing probe has actually
    failed, not evaluated unconditionally on every call. A successful
    primary probe must short-circuit it away entirely."""
    ast = _tu({"kind": "TranslationUnitDecl", "inner": []})
    monkeypatch.setattr(
        dumper, "_clang_header_dump", lambda *a, **k: (ast, None, False)
    )
    calls: list[tuple[str | None, tuple[str, ...]]] = []

    def _fake_configured_target_triple(
        gcc_options: str | None, gcc_option_tokens: tuple[str, ...], clang_bin: str
    ) -> str | None:
        calls.append((gcc_options, gcc_option_tokens))
        return "x86_64-pc-linux-gnu"

    monkeypatch.setattr(
        dumper, "_configured_target_triple", _fake_configured_target_triple
    )
    monkeypatch.setattr(dumper, "_resolve_clang_bin", lambda *a, **k: "clang")

    parser = _header_ast_parser(
        [],
        [],
        backend="clang",
        compiler="c",
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
    assert parser._target_triple == "x86_64-pc-linux-gnu"
    # Only the ONE primary probe call -- no bare (option-free) re-probe.
    assert calls == [("-O2", ())]


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


def test_probe_failure_bare_reprobe_preserves_the_driver_mode_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex review, fresh evidence, empirically verified against a real
    Clang 18 install: bare ``clang-cl -print-target-triple`` reports
    Windows, but ``clang-cl --driver-mode=g++ -print-target-triple``
    reports the host GNU target -- two different answers for the
    identical binary. The bare re-probe must therefore preserve an
    explicit ``--driver-mode=`` override, or it would silently revert to
    the binary's own name-based default."""
    ast = _tu({"kind": "TranslationUnitDecl", "inner": []})
    monkeypatch.setattr(
        dumper, "_clang_header_dump", lambda *a, **k: (ast, None, False)
    )

    def _fake_configured_target_triple(
        gcc_options: str | None, gcc_option_tokens: tuple[str, ...], clang_bin: str
    ) -> str | None:
        if gcc_options is None and gcc_option_tokens == ("--driver-mode=g++",):
            return "x86_64-pc-linux-gnu"
        return None

    monkeypatch.setattr(
        dumper, "_configured_target_triple", _fake_configured_target_triple
    )
    monkeypatch.setattr(dumper, "_resolve_clang_bin", lambda *a, **k: "clang-cl")

    parser = _header_ast_parser(
        [],
        [],
        backend="clang",
        compiler="clang-cl",
        gcc_path=None,
        gcc_prefix=None,
        gcc_options="--driver-mode=g++",
        sysroot=None,
        nostdinc=False,
        lang=None,
        exported_dynamic=set(),
        exported_static=set(),
        public_header_paths=[],
        public_dir_paths=[],
    )

    assert isinstance(parser, _ClangAstParser)
    assert parser._target_triple == "x86_64-pc-linux-gnu"


def test_probe_failure_with_a_config_user_dir_does_not_guess_sys_platform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A forwarded ``--config-user-dir=<dir>`` implicitly loads a
    ``clang.cfg`` from that directory with no explicit ``--config=`` at
    all (empirically verified against a real Clang 18 install), so it is
    just as opaque as a response file or an explicit config file."""
    ast = _tu({"kind": "TranslationUnitDecl", "inner": []})
    monkeypatch.setattr(
        dumper, "_clang_header_dump", lambda *a, **k: (ast, None, False)
    )
    monkeypatch.setattr(dumper, "_configured_target_triple", lambda *a, **k: None)
    monkeypatch.setattr(dumper, "_resolve_clang_bin", lambda *a, **k: "clang++")
    monkeypatch.setattr(sys, "platform", "darwin")

    parser = _header_ast_parser(
        [],
        [],
        backend="clang",
        compiler="c++",
        gcc_path=None,
        gcc_prefix=None,
        gcc_options="--config-user-dir=/etc/clang",
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


def test_probe_failure_with_an_explicitly_configured_wrapper_does_not_guess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex review, fresh evidence: an explicit ``--compiler`` wrapper can
    be installed at a path whose basename happens to be plain ``clang``
    (`_is_default_clang_bin` alone would call it the native default) while
    still not implementing ``-print-target-triple`` at all, failing both
    the option-bearing probe and the bare re-probe. Its explicit
    provenance (`--compiler` was actually adopted, per
    `clang_bin_is_explicitly_configured`) must suppress the last-resort
    `sys.platform` guess too, or a Darwin-targeting wrapper on a Linux
    host would be recorded as `linux`."""
    ast = _tu({"kind": "TranslationUnitDecl", "inner": []})
    monkeypatch.setattr(
        dumper, "_clang_header_dump", lambda *a, **k: (ast, None, False)
    )
    monkeypatch.setattr(dumper, "_configured_target_triple", lambda *a, **k: None)
    monkeypatch.setattr(dumper, "_resolve_clang_bin", lambda *a, **k: "/opt/wrapper/clang")
    monkeypatch.setattr(sys, "platform", "linux")

    parser = _header_ast_parser(
        [],
        [],
        backend="clang",
        compiler="c",
        gcc_path="/opt/wrapper/clang",
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
    assert parser._target_triple is None


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
