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

"""The clang backend's ``sys.platform`` last-resort target guess steers the
parser but is never stamped as the recorded effective target."""

from __future__ import annotations

import sys

import pytest

from abicheck.dumper import _header_ast_parser
from abicheck.dumper_clang import _ClangAstParser
from abicheck.extract.headers.clang import backend as clang_backend


def _tu(*inner: dict) -> dict:
    return {"kind": "TranslationUnitDecl", "inner": list(inner)}


def test_sys_platform_guess_is_never_stamped_as_the_effective_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ``sys.platform`` last resort steers the parser's own heuristics
    but is not compiler evidence: the provenance stamp must receive no
    triple (unrecorded), never the host guess."""
    from abicheck.extract import target_platform_probe

    ast = _tu({"kind": "TranslationUnitDecl", "inner": []})
    monkeypatch.setattr(
        clang_backend, "clang_header_dump", lambda *a, **k: (ast, None, False)
    )
    monkeypatch.setattr(
        clang_backend, "_configured_target_triple", lambda *a, **k: None
    )
    monkeypatch.setattr(
        target_platform_probe,
        "_probe_target_platform",
        lambda *a, **k: (None, None),
    )
    stamped: list[object] = []
    real_stamp = clang_backend._stamp_ast_parser

    def _spy(parser: object, **kw: object) -> object:
        stamped.append(kw.get("target_triple"))
        return real_stamp(parser, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(clang_backend, "_stamp_ast_parser", _spy)
    for host in ("darwin", "linux"):
        stamped.clear()
        monkeypatch.setattr(sys, "platform", host)
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
        assert parser._target_triple == host
        assert stamped == [None]
        meta = getattr(parser, "_abicheck_ast_toolchain", {})
        assert host not in meta.values()
