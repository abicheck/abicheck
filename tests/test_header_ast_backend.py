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

"""The header-AST backend protocol and the clang backend's injected runner
(lane B, stage B2a).

The dump reaches clang through ``dumper.HEADER_AST_BACKENDS["clang"]``, a
:class:`~abicheck.extract.headers.clang.backend.ClangBackend` whose process
runner is a constructor argument. These tests pin that seam: the registry,
the protocol, that the injected runner is the one that runs, and that the
default runner still checks the scan deadline before spawning anything.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest
from _clang_ast_cache_isolation import _isolate_ast_cache, _reset_ast_memo

from abicheck import deadline, dumper
from abicheck.dumper_clang_errors import run_clang_ast, run_clang_to_ast_file
from abicheck.errors import SnapshotError
from abicheck.extract.header_ast_fields import parse_header_ast_fields
from abicheck.extract.headers.backend import HeaderAstBackend, HeaderParseRequest
from abicheck.extract.headers.castxml.backend import CastxmlBackend, CastxmlRunError
from abicheck.extract.headers.castxml.probe import check_scan_deadline, run_castxml
from abicheck.extract.headers.clang.backend import ClangBackend

_HAVE_CLANG = shutil.which("clang") is not None or shutil.which("clang++") is not None
_HAVE_CASTXML = shutil.which("castxml") is not None


class _RecordingRunner:
    """Delegates to the real runner and records every command it ran."""

    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    def __call__(
        self, cmd: list[str], **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        self.commands.append(list(cmd))
        return run_clang_to_ast_file(cmd, **kwargs)


def test_registry_holds_a_default_clang_backend() -> None:
    backend = dumper.HEADER_AST_BACKENDS["clang"]
    assert isinstance(backend, ClangBackend)
    assert isinstance(backend, HeaderAstBackend)
    assert backend.name == "clang"
    assert backend.runner is run_clang_ast


def test_default_runner_checks_the_deadline_before_spawning() -> None:
    """An expired scan deadline raises before any process starts.

    Oracle: the command names a binary that does not exist, so a runner
    that spawned first would raise ``FileNotFoundError`` instead.
    """
    with deadline.with_deadline_ts(time.monotonic() - 1):
        with pytest.raises(deadline.DeadlineExceeded):
            run_clang_ast(
                ["abicheck-no-such-clang-binary"],
                timeout=5,
                on_created=lambda _p: None,
            )


def test_request_defaults_describe_an_empty_compile_context() -> None:
    req = HeaderParseRequest(headers=[Path("a.h")], extra_includes=[])
    assert (req.compiler, req.lang, req.frontend_context) == ("c++", None, "host")
    assert req.exported_dynamic == set() and req.public_header_paths == []


@pytest.mark.skipif(not _HAVE_CLANG, reason="clang not installed")
def test_injected_runner_runs_the_parse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_ast_cache(monkeypatch, tmp_path)
    _reset_ast_memo()
    header = tmp_path / "api.h"
    header.write_text("int abicheck_b2a_probe(int x);\nstruct S { int a; };\n")
    runner = _RecordingRunner()
    parser = ClangBackend(runner=runner).parse(
        HeaderParseRequest(
            headers=[header],
            extra_includes=[],
            public_header_paths=[str(header)],
        )
    )
    fields = parse_header_ast_fields(parser, producer="clang")
    assert "abicheck_b2a_probe" in {f.name for f in fields.functions}
    # Exactly one clang run, through the injected runner. (The command names
    # an aggregate include file, not the header itself.)
    assert len(runner.commands) == 1
    assert "clang" in Path(runner.commands[0][0]).name


@pytest.mark.skipif(
    not _HAVE_CLANG or shutil.which("cc") is None, reason="clang or cc not installed"
)
def test_dump_dispatches_through_the_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``dumper.dump(header_backend="clang")`` runs the registered backend."""
    _isolate_ast_cache(monkeypatch, tmp_path)
    _reset_ast_memo()
    header = tmp_path / "api.h"
    header.write_text("int abicheck_b2a_dump(int x);\n")
    src = tmp_path / "api.c"
    src.write_text('#include "api.h"\nint abicheck_b2a_dump(int x) { return x; }\n')
    so = tmp_path / "libapi.so"
    subprocess.run(
        ["cc", "-shared", "-fPIC", "-g", str(src), "-o", str(so)], check=True
    )
    runner = _RecordingRunner()
    monkeypatch.setitem(
        dumper.HEADER_AST_BACKENDS, "clang", ClangBackend(runner=runner)
    )
    snap = dumper.dump(so, [header], header_backend="clang", lang="c")
    assert "abicheck_b2a_dump" in {f.name for f in snap.declarations.functions}
    assert runner.commands, "the registered backend's runner never ran"


# ── castxml ────────────────────────────────────────────────────────────────


def test_registry_holds_a_default_castxml_backend() -> None:
    backend = dumper.HEADER_AST_BACKENDS["castxml"]
    assert isinstance(backend, CastxmlBackend)
    assert isinstance(backend, HeaderAstBackend)
    assert backend.name == "castxml"
    assert backend.runner is run_castxml
    assert backend.check_deadline is check_scan_deadline


def test_default_castxml_runner_checks_the_deadline_before_spawning() -> None:
    with deadline.with_deadline_ts(time.monotonic() - 1):
        with pytest.raises(deadline.DeadlineExceeded):
            run_castxml(["abicheck-no-such-castxml-binary"], timeout=5)


def test_castxml_run_error_keeps_the_original() -> None:
    original = SnapshotError("castxml exploded")
    wrapped = CastxmlRunError(original)
    assert isinstance(wrapped, SnapshotError)
    assert wrapped.original is original
    assert str(wrapped) == "castxml exploded"


@pytest.mark.skipif(not _HAVE_CASTXML, reason="castxml not installed")
def test_injected_castxml_runner_runs_the_parse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_ast_cache(monkeypatch, tmp_path)
    _reset_ast_memo()
    header = tmp_path / "api.h"
    header.write_text("int abicheck_b2b_probe(int x);\n")
    commands: list[list[str]] = []

    def recording(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        commands.append(list(cmd))
        return run_castxml(cmd, **kwargs)

    parser = CastxmlBackend(runner=recording).parse(
        HeaderParseRequest(
            headers=[header], extra_includes=[], public_header_paths=[str(header)]
        )
    )
    fields = parse_header_ast_fields(parser, producer="castxml")
    assert "abicheck_b2b_probe" in {f.name for f in fields.functions}
    # One parse, plus the macro-table preprocess run (``macro_table``).
    parses = [c for c in commands if "--castxml-output=1" in c]
    assert len(parses) == 1
    assert "castxml" in Path(parses[0][0]).name
    assert [c for c in commands if c not in parses] == [
        c for c in commands if "-E" in c and "-dM" in c
    ]


@pytest.mark.skipif(not _HAVE_CASTXML, reason="castxml not installed")
def test_a_failing_castxml_run_surfaces_as_castxml_run_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fallback policy in ``dumper`` keys on this type, so a failed run
    must arrive as ``CastxmlRunError`` carrying the run's own error."""
    _isolate_ast_cache(monkeypatch, tmp_path)
    _reset_ast_memo()
    header = tmp_path / "api.h"
    header.write_text("int f(void);\n")

    def failing(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 1, "", "error: injected failure")

    with pytest.raises(CastxmlRunError) as info:
        CastxmlBackend(runner=failing).parse(
            HeaderParseRequest(headers=[header], extra_includes=[])
        )
    assert isinstance(info.value.original, SnapshotError)
    assert not isinstance(info.value.original, CastxmlRunError)
