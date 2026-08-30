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
"""ADR-050 D3/D5 (G32 Phase B/D) — the `--frontend-context host|device` flag.

Shared by `dump`/`compare`/`scan` via `cli_options.compile_context_options`
and resolved through the single `cli_options.resolve_compile_context` choke
point. Phase B shipped with "device" syntactically valid (click.Choice) but
rejected at resolution time -- Phase D's `sycl_context` selector is what
makes a "device" request meaningful, so it lifts that blanket rejection
here. A "device" request the underlying compiler/invocation can't actually
satisfy now fails from the real extraction pipeline
(`AstContextMissingError`/`AstContextAmbiguousError`, see
`test_sycl_context.py` and `test_dumper_clang.py`'s DPC++ wiring tests),
not from a blanket CLI-level reject.
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
def test_frontend_context_device_no_longer_blanket_rejected(tmp_path, runner, cmd):
    """ADR-050 D5 (G32 Phase D): Phase B's blanket "not supported yet"
    rejection is lifted now that `sycl_context.py`'s selector exists. These
    ELF stubs have no headers, so the header AST frontend never runs and
    "device" has nothing to select from; whatever failure results (e.g. an
    invalid ELF file) must not be Phase B's old resolution-time reject --
    proving the restriction is actually gone, not just given a new message.
    """
    so1 = _elf_stub(tmp_path / "a.so")
    args = [cmd, str(so1)]
    if cmd == "compare":
        args.append(str(_elf_stub(tmp_path / "b.so")))
    args += ["--frontend-context", "device"]
    result = runner.invoke(main, args)
    assert "not supported yet" not in result.output
    assert "--frontend-context" not in result.output


def test_frontend_context_invalid_value_rejected_by_click(tmp_path, runner):
    so = _elf_stub(tmp_path / "a.so")
    result = runner.invoke(main, ["dump", str(so), "--frontend-context", "bogus"])
    assert result.exit_code != 0
    assert "Invalid value" in result.output or "invalid choice" in result.output.lower()


def test_compare_frontend_context_device_threaded_for_directory_inputs(
    monkeypatch, tmp_path, runner
):
    """`--frontend-context` is a both-sides L2 compile-context knob (like
    `--ast-frontend`/`--compiler`), threaded to the release fan-out's
    resolved `CompileContext` rather than rejected (fix: whole-product-
    bundle known-gap entry, AGENTS.md)."""
    # ADR-061 Phase 4: patch the implementation owner -- `abicheck.cli` resolves
    # these lazily now, so a `setattr` there rebinds nothing the caller reads.
    import abicheck.frontends.cli.commands.compare as cli_mod

    old_dir = tmp_path / "old"
    old_dir.mkdir()
    new_dir = tmp_path / "new"
    new_dir.mkdir()

    dispatched: dict[str, object] = {}
    monkeypatch.setattr(
        cli_mod, "_dispatch_release_compare", lambda ctx, **kw: dispatched.update(kw)
    )
    result = runner.invoke(
        main,
        ["compare", str(old_dir), str(new_dir), "--frontend-context", "device"],
    )
    assert result.exit_code == 0, result.output
    assert dispatched["compile_context"].frontend_context == "device"


def test_compare_frontend_context_host_threaded_for_directory_inputs(
    monkeypatch, tmp_path, runner
):
    """Same threading, with the (default-looking) value ``host``, so the
    only thing that could make this fail is cli_resolve.py's set-input
    guard itself still rejecting an explicit ``--frontend-context``."""
    # ADR-061 Phase 4: patch the implementation owner -- `abicheck.cli` resolves
    # these lazily now, so a `setattr` there rebinds nothing the caller reads.
    import abicheck.frontends.cli.commands.compare as cli_mod

    old_dir = tmp_path / "old"
    old_dir.mkdir()
    new_dir = tmp_path / "new"
    new_dir.mkdir()

    dispatched: dict[str, object] = {}
    monkeypatch.setattr(
        cli_mod, "_dispatch_release_compare", lambda ctx, **kw: dispatched.update(kw)
    )
    result = runner.invoke(
        main,
        ["compare", str(old_dir), str(new_dir), "--frontend-context", "host"],
    )
    assert result.exit_code == 0, result.output
    assert dispatched["compile_context"].frontend_context == "host"


def test_resolve_compile_context_defaults_to_host():
    import click

    ctx = click.Context(click.Command("x"))
    cc, _ = resolve_compile_context(
        ctx,
        gcc_options=None, sysroot=None, nostdinc=False,
        header_backend="auto", includes=(), build_config=None,
    )
    assert cc.frontend_context == "host"


def test_resolve_compile_context_accepts_device():
    """ADR-050 D5 (G32 Phase D): "device" is a normally-accepted value now
    that `sycl_context.py`'s selector exists -- the request itself may
    still fail later, from the real extraction pipeline, if the underlying
    compiler/invocation can't actually produce a device context."""
    import click

    ctx = click.Context(click.Command("x"))
    cc, _ = resolve_compile_context(
        ctx,
        gcc_options=None, sysroot=None, nostdinc=False,
        header_backend="auto", includes=(), build_config=None,
        frontend_context="device",
    )
    assert cc.frontend_context == "device"


def test_compile_context_frontend_context_field_defaults_to_host():
    assert CompileContext().frontend_context == "host"
    assert CompileContext(frontend_context="device").frontend_context == "device"


def test_scan_frontend_context_device_reaches_dumper_dump(tmp_path):
    """ADR-050 D5 (G32 Phase D) acceptance criterion: `scan --frontend-context
    device` threads the request all the way into the L2 header frontend via
    `compile_context_options`/`resolve_compile_context`, the same path `dump`/
    `compare` use -- closing the loop Phase B's blanket rejection opened.

    `scan_engine.run_scan_core` resolves its ELF input via
    `service.resolve_input(..., is_elf=True, compile=...)`, which reaches
    `abicheck.dumper.dump` through `service._dump_elf`; mocking `dump` itself
    (the same technique `test_service_unit.py::test_elf_forwards_provenance_
    to_dumper` uses for `public_headers`/`public_header_dirs`) lets this test
    assert the kwarg reaches that exact call, without needing a real DPC++
    toolchain in this environment.
    """
    from unittest.mock import patch

    from abicheck.model import AbiSnapshot
    from abicheck.service import resolve_input

    so = tmp_path / "lib.so"
    so.write_bytes(b"\x7fELF" + b"\x00" * 100)
    hdr = tmp_path / "api.h"
    hdr.write_text("void f();\n")
    snap = AbiSnapshot(library="lib", version="1.0")
    with patch("abicheck.dumper.dump", return_value=snap) as mock_dump:
        resolve_input(
            so,
            headers=[hdr],
            includes=[],
            is_elf=True,
            compile=CompileContext(frontend_context="device"),
        )
    assert mock_dump.call_args.kwargs["frontend_context"] == "device"


def test_dump_cli_elf_path_forwards_frontend_context_to_dumper_dump(tmp_path, runner):
    """Codex review, PR #636: the native `dump` CLI command's ELF path used
    to call `cli_dump_helpers.perform_elf_dump`, which bypassed
    `service.run_dump` and called `dumper.dump` directly -- omitting
    `frontend_context` entirely, so `dump --frontend-context device`
    silently produced a host-context snapshot instead of forwarding the
    request or failing.

    CLI cleanup phase two, PR C: the real ELF run now executes through
    `execute_dump_request` -> `service.resolve_input` -> `service._dump_elf`,
    the same path `scan`/`compare` already used, which is exactly what
    closes this gap for good rather than needing its own forwarding fix --
    see `test_scan_frontend_context_device_reaches_dumper_dump` above, whose
    identical patch target this test now shares. `dump` is looked up fresh
    from `abicheck.dumper` at call time inside `service_dump_native._dump_elf`
    (a function-local import), so patching `abicheck.dumper.dump` reaches it,
    same as it already does for `scan`/`compare`.
    """
    from unittest.mock import patch

    from abicheck.model import AbiSnapshot

    so = _elf_stub(tmp_path / "lib.so")
    hdr = tmp_path / "api.h"
    hdr.write_text("void f();\n")
    snap = AbiSnapshot(library="lib", version="1.0")
    with patch("abicheck.dumper.dump", return_value=snap) as mock_dump:
        result = runner.invoke(
            main,
            [
                "dump", str(so),
                "-H", str(hdr),
                "--frontend-context", "device",
                "--compiler", "icpx",
            ],
        )
    assert result.exit_code == 0, result.output
    assert mock_dump.call_args.kwargs["frontend_context"] == "device"
