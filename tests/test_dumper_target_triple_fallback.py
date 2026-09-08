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
triple rather than losing it to a bare ``None``.

Split out of ``test_dumper_clang.py`` (already at its ADR-061 file-size debt
cap, see ``architecture/debt.yaml``) rather than added there.
``_compiler_options.explicit_target_triple`` itself (the pure recovery
helper) has its own dedicated coverage in ``test_compiler_options.py``; this
module covers only the one thing that lives in ``dumper.py`` -- that the
``_run_clang()`` call site actually wires the fallback in, end to end.
"""

from __future__ import annotations

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
    "an explicit, unprobeable cross-target" -- extract.headers.clang.
    context.is_darwin_target's own sys.platform fallback for a bare None
    is only safe for the former, so the explicit request must survive a
    probe failure intact."""
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


def test_probe_failure_with_no_explicit_target_stays_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The genuinely-ambiguous case (no explicit --target=, probe failed)
    must still reach the parser as bare None -- that's exactly the case
    extract.headers.clang.context.is_darwin_target's own sys.platform
    fallback exists to resolve, and this call site must not pre-empt it
    with a wrong guess of its own."""
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
    assert parser._target_triple is None


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


def test_probe_failure_under_a_cl_style_driver_does_not_recover_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A CL-style driver (clang-cl/dpcpp-cl) parses MSVC-shaped flags, so a
    GNU-shaped ``-target=``/``--target=`` in forwarded options may be one it
    silently ignored rather than one it honored -- recovering it as if it
    were real risks a WRONG platform guess. Stay with bare None (letting
    is_darwin_target's own sys.platform fallback decide) instead."""
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
