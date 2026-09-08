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

import sys

import pytest

from abicheck import dumper
from abicheck.dumper import _header_ast_parser
from abicheck.dumper_clang import _ClangAstParser


def _tu(*inner: dict) -> dict:
    return {"kind": "TranslationUnitDecl", "inner": list(inner)}


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


def test_probe_failure_under_a_cl_style_driver_recovers_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A CL-style driver (clang-cl/dpcpp-cl) parses MSVC-shaped flags, so a
    GNU-shaped ``-target=``/``--target=`` in forwarded options may be one it
    silently ignored rather than one it honored -- recovering it as if it
    were real risks a WRONG platform guess. It also gets no sys.platform
    guess either (Codex review, fresh evidence): a real
    ``clang-cl -print-target-triple`` reports a Windows triple regardless
    of the HOST OS running it (cross-compiled from macOS included), so
    guessing "darwin" from a macOS host here would misclassify a Windows
    AST as Darwin. Stays bare None -- the same conservative default
    ``is_darwin_target(None)`` already answers False for."""
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


def test_probe_failure_with_option_selected_cl_mode_recovers_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CL mode is also selected via an explicit ``--driver-mode=cl`` on an
    otherwise generically-named ``clang`` binary (a replayed compile
    unit's own form -- see buildsource.header_compile_context's "Preserve
    an explicit --driver-mode=cl" docstring), not only via a
    ``clang-cl``-shaped binary name. This must be gated identically to the
    name-based case: no explicit-target recovery, no sys.platform guess
    (Codex review, fresh evidence)."""
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
    assert parser._target_triple is None
