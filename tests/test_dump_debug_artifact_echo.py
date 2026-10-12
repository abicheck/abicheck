# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""`dump --debug-info` resolves its detached debug artifact through the
workflow owner (`workflows.extraction.resolve_debug_info`) directly -- no
CLI-runtime wrapper -- and echoes what it found."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from abicheck.cli import main

# ── dump: --debug-info resolves and echoes the debug artifact ────────────────


class TestDumpEchoesResolvedDebugArtifact:
    """`dump --debug-info DIR` resolves through `workflows.extraction.
    resolve_debug_info` itself (no CLI-runtime wrapper) and forwards the
    roots/debuginfod settings in the resolver's own keyword shape."""

    def test_dump_echoes_resolved_artifact(self, tmp_path, monkeypatch) -> None:
        from types import SimpleNamespace

        binary = tmp_path / "lib.so"
        binary.write_bytes(b"\x7fELF" + b"\x00" * 50)
        calls: list[tuple[Path, dict]] = []

        def _fake_resolve(path, **kw):
            calls.append((path, kw))
            return SimpleNamespace(source="x.debug")

        monkeypatch.setattr(
            "abicheck.workflows.extraction.resolve_debug_info", _fake_resolve
        )
        result = CliRunner().invoke(
            main, ["dump", str(binary), "--debug-info", str(tmp_path)]
        )
        assert "Debug info: x.debug" in result.stderr, result.output
        assert calls, result.output
        path, kw = calls[0]
        assert path == binary
        assert kw == {
            "debug_roots": [tmp_path],
            "enable_debuginfod": False,
            "debuginfod_urls": None,
        }
