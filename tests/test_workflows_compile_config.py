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

"""``workflows.compile_config`` is Click-free; its CLI adapter keeps exit 64.

The fold itself is exercised through ``tests/test_compile_context_parity.py``
and ``tests/test_compile_context_security.py``; this file pins the boundary:
the workflows owner raises :class:`CompileConfigError` (a
``ValidationError``) and writes warnings to its *notify* sink, and
``cli_options.resolve_compile_context`` translates the error to a
``click.UsageError`` with the message unchanged.
"""

from __future__ import annotations

import click
import pytest

from abicheck.dry_run_estimate import CompileContext
from abicheck.errors import ValidationError
from abicheck.workflows.compile_config import CompileConfigError, merge_compile_config


@pytest.mark.parametrize(
    "body",
    ["compile: [unterminated\n", "compile:\n  options: [-Xclang, -load, x.so]\n"],
)
def test_explicit_unusable_config_is_a_validation_error(tmp_path, body) -> None:
    bad = tmp_path / "named.yml"
    bad.write_text(body, encoding="utf-8")
    with pytest.raises(CompileConfigError) as info:
        merge_compile_config(CompileContext(), (), bad)
    assert isinstance(info.value, ValidationError)
    assert str(info.value).startswith(f"cannot parse build config {bad}: ")


def test_warnings_go_to_the_notify_sink(tmp_path, capsys) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / ".abicheck.yml").write_text("compile: [unterminated\n", encoding="utf-8")
    notes: list[str] = []
    merge_compile_config(CompileContext(), (), None, sources=src, notify=notes.append)
    assert len(notes) == 1 and "could not parse auto-discovered" in notes[0]
    assert capsys.readouterr().err == ""


def test_untrusted_compiler_warning_goes_to_the_notify_sink(tmp_path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / ".abicheck.yml").write_text(
        "compile:\n  compiler: /opt/evil/gcc\n", encoding="utf-8"
    )
    notes: list[str] = []
    merged, _ = merge_compile_config(
        CompileContext(), (), None, sources=src, notify=notes.append
    )
    assert merged.gcc_path is None
    assert any("compile.compiler ignored" in n for n in notes)


def test_resolve_compile_context_reports_malformed_explicit_config_as_usage_error(
    tmp_path,
) -> None:
    # The Click adapter over the workflows fold keeps the exit-64 contract:
    # the framework-free CompileConfigError becomes a click.UsageError.
    from abicheck.cli_options import resolve_compile_context

    bad = tmp_path / ".abicheck.yml"
    bad.write_text("compile: [unterminated\n", encoding="utf-8")
    ctx = click.Context(click.Command("dump"))
    with pytest.raises(click.UsageError, match="cannot parse build config"):
        resolve_compile_context(
            ctx,
            sysroot=None,
            nostdinc=False,
            header_backend="auto",
            includes=(),
            build_config=bad,
        )
