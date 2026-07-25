# Copyright 2026 Nikolay Petrov
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
"""ADR-050 D3/D5 (G32 Phase B) — the `--frontend-context host|device` flag.

Shared by `dump`/`compare`/`scan` via `cli_options.compile_context_options`
and resolved through the single `cli_options.resolve_compile_context` choke
point. Only "host" is honored this phase -- "device" is a syntactically
valid click.Choice value (so a manifest-idiom-consistent flag exists ahead
of Phase D) but is rejected at resolution time, not silently treated as host.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.cli_options import resolve_compile_context
from abicheck.service_scan import CompileContext


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _elf_stub(path: Path) -> Path:
    path.write_bytes(b"\x7fELF")
    return path


@pytest.mark.parametrize("cmd", ["dump", "compare", "scan"])
def test_frontend_context_device_rejected(tmp_path, runner, cmd):
    so1 = _elf_stub(tmp_path / "a.so")
    args = [cmd, str(so1)]
    if cmd == "compare":
        args.append(str(_elf_stub(tmp_path / "b.so")))
    args += ["--frontend-context", "device"]
    result = runner.invoke(main, args)
    assert result.exit_code != 0
    assert "not supported yet" in result.output


def test_frontend_context_invalid_value_rejected_by_click(tmp_path, runner):
    so = _elf_stub(tmp_path / "a.so")
    result = runner.invoke(main, ["dump", str(so), "--frontend-context", "bogus"])
    assert result.exit_code != 0
    assert "Invalid value" in result.output or "invalid choice" in result.output.lower()


def test_compare_frontend_context_device_rejected_for_directory_inputs(
    tmp_path, runner
):
    old_dir = tmp_path / "old"
    old_dir.mkdir()
    new_dir = tmp_path / "new"
    new_dir.mkdir()
    result = runner.invoke(
        main,
        ["compare", str(old_dir), str(new_dir), "--frontend-context", "device"],
    )
    assert result.exit_code != 0
    assert "--frontend-context" in result.output
    assert "not supported for directory/package" in result.output


def test_resolve_compile_context_defaults_to_host():
    import click

    ctx = click.Context(click.Command("x"))
    cc, _ = resolve_compile_context(
        ctx,
        gcc_path=None, gcc_prefix=None, gcc_options=None,
        gcc_option_tokens=(), sysroot=None, nostdinc=False,
        header_backend="auto", includes=(), build_config=None,
    )
    assert cc.frontend_context == "host"


def test_resolve_compile_context_rejects_device():
    import click

    ctx = click.Context(click.Command("x"))
    with pytest.raises(click.UsageError, match="not supported yet"):
        resolve_compile_context(
            ctx,
            gcc_path=None, gcc_prefix=None, gcc_options=None,
            gcc_option_tokens=(), sysroot=None, nostdinc=False,
            header_backend="auto", includes=(), build_config=None,
            frontend_context="device",
        )


def test_compile_context_frontend_context_field_defaults_to_host():
    assert CompileContext().frontend_context == "host"
    assert CompileContext(frontend_context="device").frontend_context == "device"
