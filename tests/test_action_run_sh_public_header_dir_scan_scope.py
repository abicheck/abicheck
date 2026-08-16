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

"""Behavioral tests for ``action/run.sh``'s scan-mode ``public-header-dir``
forwarding, scoped to the candidate side for a scalar baseline scan
(Codex review, lab report follow-up).

``scan`` mode forwards ``public-header-dir`` a second time as a bare
(unsided) ``-H`` root, purely so its own header *extraction* candidate set
matches a fresh ``dump``'s (see the long comment at that call site in
``run.sh``). For an audit-only scan (no ``--against``) or a
``new-library-set`` audit, "bare" is correct: there is only one side. But
for a *scalar* ``scan --against`` comparison, a bare ``-H`` root is
side-*both* on the CLI (ADR-040 L1) -- ``_resolve_baseline_header_scope()``
then reads the candidate's ``public-header-dir`` tree into the *baseline*
side's header set too, even when an explicit ``old-header`` is also given
(the old header list is unioned with the both-bucket, not replacing it),
which can hide a declaration the candidate removed (still reachable via
the candidate's own tree) or fabricate a difference from a candidate-only
header the baseline never had. Forwarding it as ``-H new=...`` instead
scopes it to the candidate side only, matching the fix's own comment in
``run.sh``.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import pytest

RUN_SH = Path(__file__).resolve().parents[1] / "action" / "run.sh"
_END_MARKER = 'if [[ "${INPUT_VERBOSE:-false}" == "true" ]]; then'


def _mode_branches_region() -> str:
    text = RUN_SH.read_text(encoding="utf-8")
    return text[: text.index(_END_MARKER)]


def _bash_executable() -> str:
    if os.name != "nt":
        return "bash"
    for candidate in (
        os.environ.get("GIT_BASH_PATH"),
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
    ):
        if candidate and Path(candidate).is_file():
            return candidate
    return "bash"


def _run_cmd(env_extra: dict[str, str]) -> list[str]:
    script = (
        _mode_branches_region()
        + '\nprintf \'%s\\x1f\' ${CMD[@]+"${CMD[@]}"}\n'
    )
    with tempfile.NamedTemporaryFile(
        "w", suffix=".sh", delete=False, encoding="utf-8", newline="\n",
    ) as f:
        f.write(script)
        script_path = f.name
    env = dict(os.environ)
    env.update(env_extra)
    try:
        result = subprocess.run(
            [_bash_executable(), script_path],
            capture_output=True, text=True, encoding="utf-8", env=env,
        )
    finally:
        os.unlink(script_path)
    if result.returncode != 0:
        raise AssertionError(
            f"harness script failed (exit {result.returncode})\n"
            f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
        )
    return [item for item in result.stdout.split("\x1f") if item]


def _h_pairs(cmd: list[str]) -> list[str]:
    return [cmd[j + 1] for j, v in enumerate(cmd) if v == "-H"]


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestScanPublicHeaderDirBaselineScope:
    def test_sided_new_when_baseline_is_set(self) -> None:
        # Scalar scan --against: public-header-dir must NOT leak into the
        # baseline/old side as a bare -H root.
        cmd = _run_cmd(
            {
                "INPUT_MODE": "scan",
                "INPUT_NEW_LIBRARY": "lib.so",
                "INPUT_AGAINST": "baseline.json",
                "INPUT_PUBLIC_HEADER_DIR": "pub",
            }
        )
        pairs = _h_pairs(cmd)
        assert "new=pub" in pairs
        assert "pub" not in pairs

    def test_bare_when_audit_only(self) -> None:
        # No --against: there is only one side, bare -H is correct (matches
        # --header's own bare/unsided forwarding for this shape).
        cmd = _run_cmd(
            {
                "INPUT_MODE": "scan",
                "INPUT_NEW_LIBRARY": "lib.so",
                "INPUT_PUBLIC_HEADER_DIR": "pub",
            }
        )
        pairs = _h_pairs(cmd)
        assert "pub" in pairs
        assert "new=pub" not in pairs

    def test_bare_when_force_audit_only(self) -> None:
        # audit: true (deprecated back-compat alias) forces audit-only even
        # with against/abi-baseline resolved -- --against is never
        # forwarded, so public-header-dir must stay bare too.
        cmd = _run_cmd(
            {
                "INPUT_MODE": "scan",
                "INPUT_NEW_LIBRARY": "lib.so",
                "INPUT_AGAINST": "baseline.json",
                "INPUT_AUDIT": "true",
                "INPUT_PUBLIC_HEADER_DIR": "pub",
            }
        )
        pairs = _h_pairs(cmd)
        assert "pub" in pairs
        assert "new=pub" not in pairs

    def test_bare_for_artifact_set(self) -> None:
        # new-library-set is audit-only by construction (ADR-056) -- no old
        # side to contaminate, so bare -H is correct.
        cmd = _run_cmd(
            {
                "INPUT_MODE": "scan",
                "INPUT_NEW_LIBRARY_SET": "a.so,b.so",
                "INPUT_PUBLIC_HEADER_DIR": "pub",
            }
        )
        pairs = _h_pairs(cmd)
        assert "pub" in pairs
        assert "new=pub" not in pairs
