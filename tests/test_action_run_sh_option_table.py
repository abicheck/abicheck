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

"""The bug class ``cli_surface.derived_table_lost_in_its_own_encoding``.

``action/run.sh`` derives ``$_CLI_VALUE_OPTIONS`` -- which spellings the
installed ``abicheck`` CLI takes a value for -- by introspecting Click at run
time (ADR-070 D3), then encodes the result as one delimiter-framed string it
does membership tests against. These tests own the *encoding* half of that:
whether every entry the derivation produced can still be found afterwards.

Its sibling ``tests/test_extra_args_is_value_option_completeness.py`` owns the
*content* half (``cli_surface.copied_option_table_went_stale``) -- that what
the surface enumerates matches what Click actually declares. Two different
failures: there the table says the wrong thing; here it says the right thing
and the lookup cannot see it.

Split out of ``tests/test_action_run_sh_helpers.py`` rather than appended to
it: that module is at ``architecture/debt.yaml``'s test-size ceiling, and
this is a distinct claim with its own harness (it runs the helper region
under a *deliberately different* interpreter), not another case of that
module's tokenizer tests.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from test_action_run_sh_helpers import (
    RUN_SH,
    _bash_executable,
    _cli_introspection_prelude,
    _helpers_region,
)


def _run_harness_with(py_bin: str, body: str) -> str:
    """`_run_harness`, but with the CLI-introspection interpreter chosen.

    Kept beside its one caller rather than folded into `_run_harness`: every
    other test in this module deliberately wants *this* interpreter, and
    making the binary a parameter there would invite a future test to pass one
    by accident.
    """
    script = _helpers_region() + _cli_introspection_prelude(py_bin) + f"\n{body}\n"
    with tempfile.NamedTemporaryFile(
        "w", suffix=".sh", delete=False, encoding="utf-8", newline="\n"
    ) as fh:
        fh.write(script)
        path = fh.name
    try:
        result = subprocess.run(
            [_bash_executable(), path], capture_output=True, text=True
        )
    finally:
        os.unlink(path)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestDerivedOptionTableIsLineEndingAgnostic:
    """`_cli_value_options_init` must derive the same table whatever line
    endings the interpreter's stdout uses.

    The bug class, confirmed on the real windows-latest unit lane: `$_PY_BIN`
    there is a native `python.exe`, and Python's text-mode stdout translates
    every `"\n"` it writes to `os.linesep` -- so the derivation's own
    `print(spelling)` emits `"--format\r\n"`. `$(...)` strips the trailing
    newline but not the embedded CRs, so `tr '\n' '|'` built a table holding
    `|--format\r|` while every lookup asks for `|--format|`.

    What makes it a *class* rather than one option's problem, and why this is
    tested at the table rather than at one helper: the table is `_extra_args_
    options`' only source of option/value awareness, so a miss makes **every**
    value-taking option read as non-value-taking at once. `_CLI_VALUE_OPTIONS_
    DERIVED` still said `true`, so `_require_cli_value_options_or_fail`'s hard
    failure never fired and the run silently took the wrong branch -- exactly
    the outcome ADR-070 D3 chose a loud failure to avoid. Downstream that is
    an *over*-detection: a literal `--write` or `--dry-run` sitting in
    `extra-args` as another option's value reads as a real flag, and this
    script then skips the `--write json=`/`-o` injection while a full
    comparison runs, publishing no report at all.

    The oracle is Click's own parameter table (`_value_taking_options`),
    derived in Python independently of anything the shell computes, and every
    spelling in it is checked -- not the one or two that happened to surface
    the defect. The CR-emitting interpreter is a real subprocess, so this
    reproduces the Windows byte stream on every platform: before the `tr -d
    '\r'` fix, this class fails on Linux too.
    """

    @staticmethod
    def _crlf_interpreter(tmp_path: Path) -> str:
        """A wrapper that behaves like this interpreter but writes CRLF.

        Emulates the *byte stream* `python.exe` produces rather than
        simulating the helper's parsing, so the thing under test is the real
        `_cli_value_options_init` reading real CR-terminated lines.
        """
        shim = tmp_path / "crlf_python"
        shim.write_text(
            f'#!/bin/sh\nexec {shlex.quote(sys.executable)} "$@" | sed "s/$/\\r/"\n',
            encoding="utf-8",
            newline="\n",
        )
        shim.chmod(0o755)
        return str(shim)

    def _recognized(self, py_bin: str, options: list[str]) -> dict[str, bool]:
        """Ask the real shell helper about each of *options*, in one run."""
        body = "".join(
            f'if _extra_args_is_value_option {opt!r}; then printf "%s YES\\n" {opt!r};'
            f' else printf "%s NO\\n" {opt!r}; fi\n'
            for opt in options
        )
        out = _run_harness_with(py_bin, body)
        return {
            line.rsplit(" ", 1)[0]: line.rsplit(" ", 1)[1] == "YES"
            for line in out.splitlines()
            if line
        }

    def test_every_value_taking_option_survives_crlf_output(
        self, tmp_path: Path
    ) -> None:
        from test_extra_args_is_value_option_completeness import (
            _value_taking_options,
        )

        options = sorted(_value_taking_options(("compare",)))
        assert options, "no value-taking options found via introspection"
        crlf = self._recognized(self._crlf_interpreter(tmp_path), options)
        missed = sorted(opt for opt in options if not crlf.get(opt))
        assert not missed, (
            "these value-taking options went unrecognized when the interpreter "
            f"wrote CRLF: {missed}"
        )

    def test_every_value_taking_option_is_recognized_at_all(self) -> None:
        """The line-ending-independent half, and the one that made the
        delimiter defect visible.

        The derived table is delimited `|a|b|c`, while the membership test
        asks for `|<opt>|` -- so whichever spelling `node.params` yields last
        carried no trailing delimiter and never matched, on every platform.
        It was `--variant`; the point is that it is positional, not that it
        was that option, which is why this asserts the whole table against
        Click rather than sampling.
        """
        from test_extra_args_is_value_option_completeness import (
            _value_taking_options,
        )

        options = sorted(_value_taking_options(("compare",)))
        assert options, "no value-taking options found via introspection"
        recognized = self._recognized(sys.executable, options)
        missed = sorted(opt for opt in options if not recognized.get(opt))
        assert not missed, f"value-taking options the table does not match: {missed}"

    def test_the_crlf_and_lf_tables_agree_option_for_option(
        self, tmp_path: Path
    ) -> None:
        """Equality against the LF run, so the check cannot pass by the CRLF
        run answering `True` for everything (which would be just as wrong,
        in the other direction)."""
        from test_extra_args_is_value_option_completeness import (
            _value_taking_options,
        )

        options = sorted(_value_taking_options(("compare",))) + [
            "--verbose",
            "--dry-run",
            "--write",
            "-v",
            "not-an-option",
        ]
        lf = self._recognized(sys.executable, options)
        crlf = self._recognized(self._crlf_interpreter(tmp_path), options)
        assert crlf == lf
        # And the oracle is not vacuous in either direction: the run really
        # distinguishes value-taking options from the rest.
        assert any(lf.values()) and not all(lf.values())

    def test_a_crlf_interpreter_still_expands_a_short_cluster(
        self, tmp_path: Path
    ) -> None:
        """The downstream consumer, not only the membership test: cluster
        expansion is gated on `_extra_args_is_value_option`, so it returned
        nothing at all for every cluster under the defect."""
        out = _run_harness_with(
            self._crlf_interpreter(tmp_path),
            "_extra_args_expand_short_clusters '-vH' || true\n",
        )
        assert out == "-v\n-H\n"
