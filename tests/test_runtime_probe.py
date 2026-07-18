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

"""Unit tests for abicheck.runtime_probe (ADR-044 P2 item 2)."""
from __future__ import annotations

import subprocess

import pytest

from abicheck import runtime_probe as rp
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


def _make_lib(tmp_path, name="lib.so"):
    p = tmp_path / name
    p.write_bytes(b"\x7fELF" + b"\x00" * 100)
    return p


class TestRunRuntimeProbePlatformGuards:
    def test_non_linux_platform_skips(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rp.sys, "platform", "darwin")
        app = _make_exec(tmp_path)
        old_lib = _make_lib(tmp_path, "old.so")
        new_lib = _make_lib(tmp_path, "new.so")
        result = run_runtime_probe(app, old_lib, new_lib)
        assert result.attempted is False
        assert result.skipped_reason is not None
        assert "linux" in result.skipped_reason.lower()

    def test_non_executable_app_skips(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rp.sys, "platform", "linux")
        app = tmp_path / "app"
        app.write_text("not executable")
        old_lib = _make_lib(tmp_path, "old.so")
        new_lib = _make_lib(tmp_path, "new.so")
        result = run_runtime_probe(app, old_lib, new_lib)
        assert result.attempted is False
        assert "executable" in (result.skipped_reason or "")


class TestRunOnce:
    def test_symbol_lookup_error_detected(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rp.sys, "platform", "linux")

        def _fake_run(argv, env=None, capture_output=None, text=None, errors=None, timeout=None, check=None):
            assert env["LD_BIND_NOW"] == "1"
            return subprocess.CompletedProcess(
                argv, returncode=127, stdout="",
                stderr="./app: symbol lookup error: ./app: undefined symbol: foo_bar\n",
            )

        monkeypatch.setattr(rp.subprocess, "run", _fake_run)
        app = _make_exec(tmp_path)
        old_lib = _make_lib(tmp_path, "old.so")
        new_lib = _make_lib(tmp_path, "new.so")
        result = run_runtime_probe(app, old_lib, new_lib)
        assert result.attempted is True
        assert result.old is not None and result.old.ok is False
        assert result.old.missing_symbol == "foo_bar"
        assert result.new is not None and result.new.ok is False
        assert result.new.missing_symbol == "foo_bar"

    def test_versioned_symbol_lookup_error_strips_version_suffix(
        self, tmp_path, monkeypatch,
    ):
        """Codex review: glibc appends ", version X" after the bare symbol
        name for a versioned undefined-symbol failure (e.g. "undefined
        symbol: foo, version FOO_1.0") -- the old \\S+ capture included the
        trailing comma ("foo,"), so the synthesized finding's symbol never
        matched the real import/export name and an exact suppression for
        "foo" could never apply."""
        monkeypatch.setattr(rp.sys, "platform", "linux")

        def _fake_run(argv, env=None, capture_output=None, text=None, errors=None, timeout=None, check=None):
            return subprocess.CompletedProcess(
                argv, returncode=127, stdout="",
                stderr=(
                    "./app: symbol lookup error: ./app: undefined symbol: "
                    "foo_bar, version FOO_1.0\n"
                ),
            )

        monkeypatch.setattr(rp.subprocess, "run", _fake_run)
        app = _make_exec(tmp_path)
        old_lib = _make_lib(tmp_path, "old.so")
        new_lib = _make_lib(tmp_path, "new.so")
        result = run_runtime_probe(app, old_lib, new_lib)
        assert result.old is not None
        assert result.old.missing_symbol == "foo_bar"

    def test_clean_run_is_ok(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rp.sys, "platform", "linux")

        def _fake_run(argv, env=None, capture_output=None, text=None, errors=None, timeout=None, check=None):
            return subprocess.CompletedProcess(argv, returncode=0, stdout="", stderr="")

        monkeypatch.setattr(rp.subprocess, "run", _fake_run)
        app = _make_exec(tmp_path)
        old_lib = _make_lib(tmp_path, "old.so")
        new_lib = _make_lib(tmp_path, "new.so")
        result = run_runtime_probe(app, old_lib, new_lib)
        assert result.old is not None and result.old.ok is True
        assert result.old.missing_symbol is None
        assert result.new is not None and result.new.ok is True

    def test_ld_library_path_prepended_not_overwritten(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rp.sys, "platform", "linux")
        monkeypatch.setenv("LD_LIBRARY_PATH", "/existing/path")
        captured_envs = []

        def _fake_run(argv, env=None, capture_output=None, text=None, errors=None, timeout=None, check=None):
            captured_envs.append(env["LD_LIBRARY_PATH"])
            return subprocess.CompletedProcess(argv, returncode=0, stdout="", stderr="")

        monkeypatch.setattr(rp.subprocess, "run", _fake_run)
        app = _make_exec(tmp_path)
        old_lib = _make_lib(tmp_path, "old.so")
        new_lib = _make_lib(tmp_path, "new.so")
        run_runtime_probe(app, old_lib, new_lib)
        assert all(env.endswith("/existing/path") for env in captured_envs)
        assert all(str(old_lib.parent) in env or str(new_lib.parent) in env for env in captured_envs)

    def test_timeout_marks_timed_out(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rp.sys, "platform", "linux")

        def _fake_run(argv, env=None, capture_output=None, text=None, errors=None, timeout=None, check=None):
            raise subprocess.TimeoutExpired(cmd=argv, timeout=timeout)

        monkeypatch.setattr(rp.subprocess, "run", _fake_run)
        app = _make_exec(tmp_path)
        old_lib = _make_lib(tmp_path, "old.so")
        new_lib = _make_lib(tmp_path, "new.so")
        result = run_runtime_probe(app, old_lib, new_lib, timeout=0.01)
        assert result.old is not None
        assert result.old.ok is False
        assert result.old.timed_out is True

    def test_oserror_captured_not_raised(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rp.sys, "platform", "linux")

        def _fake_run(argv, env=None, capture_output=None, text=None, errors=None, timeout=None, check=None):
            raise OSError("Exec format error")

        monkeypatch.setattr(rp.subprocess, "run", _fake_run)
        app = _make_exec(tmp_path)
        old_lib = _make_lib(tmp_path, "old.so")
        new_lib = _make_lib(tmp_path, "new.so")
        result = run_runtime_probe(app, old_lib, new_lib)
        assert result.old is not None and result.old.ok is False
        assert "Exec format error" in result.old.stderr_tail

    def test_non_utf8_stderr_does_not_raise(self, tmp_path, monkeypatch):
        """Codex review, fresh evidence: a real executable's stderr is
        arbitrary bytes, not guaranteed valid UTF-8 -- subprocess.run(...,
        text=True) with no errors= handling raises UnicodeDecodeError *after*
        the child exits, which would escape this best-effort helper and
        abort the whole compare instead of degrading to a RuntimeProbeOutcome.
        Uses a real subprocess (not a mocked subprocess.run) since the
        decoding itself is exactly what's under test."""
        monkeypatch.setattr(rp.sys, "platform", "linux")
        app = tmp_path / "app"
        # A lone 0xFF byte is invalid in UTF-8 in any position -- write it
        # via printf's octal escape (portable across /bin/sh implementations)
        # sandwiched in valid ASCII so the symbol-lookup regex can still be
        # checked not to false-positive on it.
        app.write_text(
            "#!/bin/sh\n"
            "printf 'before \\377 after\\n' >&2\n"
            "exit 127\n"
        )
        app.chmod(0o755)
        old_lib = _make_lib(tmp_path, "old.so")
        new_lib = _make_lib(tmp_path, "new.so")
        # The point under test is that this call returns at all (no
        # UnicodeDecodeError escaping _run_once) -- no "symbol lookup
        # error: ... undefined symbol: X" text is present, so per this
        # probe's own deliberately-narrow design (only that exact glibc
        # message is interpreted as a regression) ok stays True regardless
        # of the script's exit code.
        result = run_runtime_probe(app, old_lib, new_lib)
        assert result.old is not None
        assert result.old.ok is True
        assert result.old.missing_symbol is None
        assert "before" in result.old.stderr_tail
        assert "after" in result.old.stderr_tail

    def test_relative_bare_name_resolved_before_exec(self, tmp_path, monkeypatch):
        """A bare relative app name (no '/') must not be searched for on PATH
        (Codex review): subprocess treats an argv[0] with no directory
        component as a PATH lookup, not cwd-relative, so an unresolved
        ``Path("app")`` would silently miss the app in its own directory
        whenever '.' is not on PATH."""
        monkeypatch.setattr(rp.sys, "platform", "linux")
        monkeypatch.chdir(tmp_path)
        captured_argv = []

        def _fake_run(argv, env=None, capture_output=None, text=None, errors=None, timeout=None, check=None):
            captured_argv.append(argv)
            return subprocess.CompletedProcess(argv, returncode=0, stdout="", stderr="")

        monkeypatch.setattr(rp.subprocess, "run", _fake_run)
        app = _make_exec(tmp_path)
        relative_app = rp.Path(app.name)
        old_lib = _make_lib(tmp_path, "old.so")
        new_lib = _make_lib(tmp_path, "new.so")
        run_runtime_probe(relative_app, old_lib, new_lib)
        assert all(argv[0] == str(app.resolve()) for argv in captured_argv)


class TestRegressedSymbol:
    def test_regressed_when_old_ok_and_new_missing_symbol(self):
        result = RuntimeProbeResult(
            app_path="app", attempted=True,
            old=RuntimeProbeOutcome(ok=True),
            new=RuntimeProbeOutcome(ok=False, missing_symbol="foo"),
        )
        assert result.regressed_symbol == "foo"

    def test_no_regression_when_old_already_failed(self):
        """If the app never worked against the OLD library either, the
        failure isn't attributable to this library change."""
        result = RuntimeProbeResult(
            app_path="app", attempted=True,
            old=RuntimeProbeOutcome(ok=False, missing_symbol="foo"),
            new=RuntimeProbeOutcome(ok=False, missing_symbol="foo"),
        )
        assert result.regressed_symbol is None

    def test_no_regression_when_new_also_ok(self):
        result = RuntimeProbeResult(
            app_path="app", attempted=True,
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
            app_path="app", attempted=True,
            old=RuntimeProbeOutcome(ok=True),
            new=RuntimeProbeOutcome(ok=False, timed_out=timeout),
        )
        assert result.regressed_symbol is None
