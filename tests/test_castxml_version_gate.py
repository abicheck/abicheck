# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Regressions for the proactive CastXML version gate wired into
``dumper._castxml_dump`` (``abicheck/castxml_policy.py``).

Split out of ``tests/test_dumper_unit.py`` (at the file-size hard cap) rather
than grown in place — see ``AGENTS.md`` "Files that are large".
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from abicheck.dumper import _castxml_dump
from abicheck.errors import UnsupportedCastxmlVersionError


def _mock_identity(version_output: str):
    def _fake(_executable: str) -> dict[str, str]:
        return {
            "selected": "/mock/castxml",
            "realpath": "/mock/castxml",
            "mtime_ns": "0",
            "size": "0",
            "sha256": "deadbeef",
            "version": version_output,
        }

    return _fake


class TestCastxmlVersionGate:
    def test_below_minimum_version_raises_before_scan(self, monkeypatch):
        monkeypatch.setattr(
            "abicheck.dumper._resolve_selected_tool", lambda _: "/mock/castxml"
        )
        monkeypatch.setattr(
            "abicheck.dumper._tool_identity_metadata",
            _mock_identity("castxml version 0.4.5\nclang version 8.0.0"),
        )
        with pytest.raises(UnsupportedCastxmlVersionError, match="0.4.5"):
            _castxml_dump([Path("h.h")], [])

    def test_at_or_above_max_version_raises(self, monkeypatch):
        monkeypatch.setattr(
            "abicheck.dumper._resolve_selected_tool", lambda _: "/mock/castxml"
        )
        monkeypatch.setattr(
            "abicheck.dumper._tool_identity_metadata",
            _mock_identity("castxml version 0.8.0\nclang version 18.1.8"),
        )
        with pytest.raises(UnsupportedCastxmlVersionError):
            _castxml_dump([Path("h.h")], [])

    def test_supported_version_does_not_raise_from_gate(self, monkeypatch, tmp_path):
        # Supported version passes the gate; let the (mocked) subsequent
        # castxml invocation fail with a distinct, unrelated error so this
        # test only proves the *gate* didn't fire.
        monkeypatch.setattr(
            "abicheck.dumper._resolve_selected_tool", lambda _: "/mock/castxml"
        )
        monkeypatch.setattr(
            "abicheck.dumper._tool_identity_metadata",
            _mock_identity("castxml version 0.7.0\nclang version 18.1.8"),
        )
        monkeypatch.setattr("abicheck.dumper._cache_key", lambda *a, **kw: "k")
        monkeypatch.setattr(
            "abicheck.dumper._cache_path", lambda k: tmp_path / "does-not-exist.xml"
        )

        def fake_run(*_args, **_kwargs):
            return subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="unrelated marker: xyz123"
            )

        monkeypatch.setattr("abicheck.dumper.deadline.run_bounded", fake_run)
        with pytest.raises(RuntimeError, match="xyz123"):
            _castxml_dump([Path("h.h")], [])

    def test_override_env_var_allows_unsupported_version(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ABICHECK_ALLOW_UNSUPPORTED_CASTXML", "1")
        monkeypatch.setattr(
            "abicheck.dumper._resolve_selected_tool", lambda _: "/mock/castxml"
        )
        monkeypatch.setattr(
            "abicheck.dumper._tool_identity_metadata",
            _mock_identity("castxml version 0.4.5\nclang version 8.0.0"),
        )
        monkeypatch.setattr("abicheck.dumper._cache_key", lambda *a, **kw: "k")
        monkeypatch.setattr(
            "abicheck.dumper._cache_path", lambda k: tmp_path / "does-not-exist.xml"
        )

        def fake_run(*_args, **_kwargs):
            return subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="unrelated marker: xyz123"
            )

        monkeypatch.setattr("abicheck.dumper.deadline.run_bounded", fake_run)
        # The gate no longer raises UnsupportedCastxmlVersionError; the run
        # proceeds to the (mocked) real castxml invocation and fails there
        # instead, proving the override let it past the gate.
        with pytest.raises(RuntimeError, match="xyz123"):
            _castxml_dump([Path("h.h")], [])

    def test_unresolvable_executable_skips_gate(self, monkeypatch, tmp_path):
        """A path that can't even be stat'd (missing binary) is a different,
        pre-existing failure mode — the gate defers to the real invocation's
        own error rather than raising its own unrelated version complaint."""
        monkeypatch.setattr(
            "abicheck.dumper._resolve_selected_tool", lambda _: "/mock/castxml"
        )
        monkeypatch.setattr("abicheck.dumper._cache_key", lambda *a, **kw: "k")
        monkeypatch.setattr(
            "abicheck.dumper._cache_path", lambda k: tmp_path / "does-not-exist.xml"
        )

        def fake_run(*_args, **_kwargs):
            return subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="unrelated marker: xyz123"
            )

        monkeypatch.setattr("abicheck.dumper.deadline.run_bounded", fake_run)
        with pytest.raises(RuntimeError, match="xyz123"):
            _castxml_dump([Path("h.h")], [])
