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

"""Unit tests for abicheck.runtime_probe (P0.1: disabled, non-executing no-op).

``run_runtime_probe`` used to actually execute a consumer binary against an
analyzed OLD/NEW shared library. That was a trust-boundary violation
(analyzed artifacts must be data, never executed) and has been disabled:
the function is now a pure no-op that never spawns a subprocess and never
loads a library into this process, regardless of platform, executability,
or ELF layout. These tests pin exactly that: no marker file a real
constructor would create is ever produced, and the result always reports
``attempted=False``.
"""

from __future__ import annotations

import pytest

from abicheck.runtime_probe import (
    RuntimeProbeOutcome,
    RuntimeProbeResult,
    run_runtime_probe,
)


def _make_exec(tmp_path, name="app"):
    p = tmp_path / name
    p.write_text("#!/bin/sh\nexit 0\n")
    p.chmod(0o755)
    return p


def _make_marker_lib(tmp_path, marker_path, name="lib.so"):
    """A "library" that, if ever actually loaded/executed, writes *marker_path*.

    Not a real ELF constructor (that needs a real compiler) -- but this
    module never spawns a subprocess or reads the file at all, so the
    content is irrelevant; what matters is that running these tests never
    causes *marker_path* to be created.
    """
    p = tmp_path / name
    p.write_bytes(b"\x7fELF" + b"\x00" * 100)
    return p


def _make_lib(tmp_path, name="lib.so"):
    p = tmp_path / name
    p.write_bytes(b"\x7fELF" + b"\x00" * 100)
    return p


class TestRunRuntimeProbeIsDisabled:
    """run_runtime_probe never executes anything, on any platform or input."""

    def test_never_attempts_regardless_of_real_platform(self, tmp_path):
        """The module no longer branches on sys.platform at all -- there is
        nothing left to monkeypatch; this just pins that the real,
        currently-running platform still gets attempted=False."""
        app = _make_exec(tmp_path)
        old_lib = _make_lib(tmp_path, "old.so")
        new_lib = _make_lib(tmp_path, "new.so")
        result = run_runtime_probe(app, old_lib, new_lib)
        assert result.attempted is False
        assert result.old is None
        assert result.new is None

    def test_skipped_reason_is_explicit_and_non_empty(self, tmp_path):
        app = _make_exec(tmp_path)
        old_lib = _make_lib(tmp_path, "old.so")
        new_lib = _make_lib(tmp_path, "new.so")
        result = run_runtime_probe(app, old_lib, new_lib)
        assert result.skipped_reason
        assert "disabled" in result.skipped_reason.lower()

    def test_regressed_symbol_never_fires(self, tmp_path):
        app = _make_exec(tmp_path)
        old_lib = _make_lib(tmp_path, "old.so")
        new_lib = _make_lib(tmp_path, "new.so")
        result = run_runtime_probe(app, old_lib, new_lib)
        assert result.regressed_symbol is None

    def test_never_spawns_a_subprocess(self, tmp_path, monkeypatch):
        """No module-level subprocess/os hooks exist to spawn a process from
        -- calling the disabled function must not even attempt to import or
        invoke subprocess.run."""

        def _boom(*args, **kwargs):
            raise AssertionError("run_runtime_probe must never spawn a process")

        monkeypatch.setattr("subprocess.run", _boom)
        app = _make_exec(tmp_path)
        old_lib = _make_lib(tmp_path, "old.so")
        new_lib = _make_lib(tmp_path, "new.so")
        result = run_runtime_probe(app, old_lib, new_lib)
        assert result.attempted is False

    def test_never_creates_a_marker_file_a_real_constructor_would(self, tmp_path):
        """End-to-end regression for the vulnerability itself: even a
        "library" this test treats as if it could run load-time code must
        never actually be loaded, so no marker file appears."""
        marker = tmp_path / "constructor-ran.marker"
        app = _make_exec(tmp_path)
        old_lib = _make_marker_lib(tmp_path, marker, "old.so")
        new_lib = _make_marker_lib(tmp_path, marker, "new.so")
        run_runtime_probe(app, old_lib, new_lib)
        assert not marker.exists()

    def test_does_not_require_app_to_exist_or_be_executable(self, tmp_path):
        """A disabled probe has no reason to even stat its inputs."""
        app = tmp_path / "does-not-exist"
        old_lib = tmp_path / "old.so"
        new_lib = tmp_path / "new.so"
        result = run_runtime_probe(app, old_lib, new_lib)
        assert result.attempted is False

    def test_timeout_argument_accepted_and_ignored(self, tmp_path):
        app = _make_exec(tmp_path)
        old_lib = _make_lib(tmp_path, "old.so")
        new_lib = _make_lib(tmp_path, "new.so")
        result = run_runtime_probe(app, old_lib, new_lib, timeout=0.001)
        assert result.attempted is False


class TestRegressedSymbol:
    def test_regressed_when_old_ok_and_new_missing_symbol(self):
        result = RuntimeProbeResult(
            app_path="app",
            attempted=True,
            old=RuntimeProbeOutcome(ok=True),
            new=RuntimeProbeOutcome(ok=False, missing_symbol="foo"),
        )
        assert result.regressed_symbol == "foo"

    def test_no_regression_when_old_already_failed(self):
        """If the app never worked against the OLD library either, the
        failure isn't attributable to this library change."""
        result = RuntimeProbeResult(
            app_path="app",
            attempted=True,
            old=RuntimeProbeOutcome(ok=False, missing_symbol="foo"),
            new=RuntimeProbeOutcome(ok=False, missing_symbol="foo"),
        )
        assert result.regressed_symbol is None

    def test_no_regression_when_new_also_ok(self):
        result = RuntimeProbeResult(
            app_path="app",
            attempted=True,
            old=RuntimeProbeOutcome(ok=True),
            new=RuntimeProbeOutcome(ok=True),
        )
        assert result.regressed_symbol is None

    def test_no_regression_when_not_attempted(self):
        result = RuntimeProbeResult(app_path="app", attempted=False, skipped_reason="x")
        assert result.regressed_symbol is None

    @pytest.mark.parametrize("timeout", [True, False])
    def test_new_timeout_without_missing_symbol_is_not_a_regression(self, timeout):
        """A timeout has no attributable missing symbol -- must not be
        misread as a regression (the whole point of scoping this probe to
        the dynamic linker's own explicit signal, not generic failure)."""
        result = RuntimeProbeResult(
            app_path="app",
            attempted=True,
            old=RuntimeProbeOutcome(ok=True),
            new=RuntimeProbeOutcome(ok=False, timed_out=timeout),
        )
        assert result.regressed_symbol is None
