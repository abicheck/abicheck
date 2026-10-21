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

"""``action/validate-inputs.sh``'s checks on the baseline source: a file path
beside a baseline-set selection, and the ``expected-archive-digest`` pin.

Each refusal here has a twin in ``action/run.sh``'s pairing check, which the
Action runs again for anyone invoking run.sh directly. Both must refuse the same
inputs, so the run.sh side is covered through the fetch region in
``test_action_baseline_archive_decision.py``.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from _workflow_exec import bash_executable, require_bash

VALIDATE_SH = Path(__file__).resolve().parents[1] / "action" / "validate-inputs.sh"
DIGEST = "ab" * 32


def _validate(env_extra: dict[str, str]) -> subprocess.CompletedProcess[str]:
    require_bash()
    # Strip every INPUT_* the test process may have inherited, so a case that
    # omits an input cannot pick up an ambient value.
    env = {k: v for k, v in os.environ.items() if not k.startswith("INPUT_")}
    env.update(env_extra)
    return subprocess.run(
        [bash_executable(), str(VALIDATE_SH)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def _output(result: subprocess.CompletedProcess[str]) -> str:
    return result.stdout + result.stderr


@pytest.mark.skipif(
    not VALIDATE_SH.is_file(), reason="action/validate-inputs.sh not found"
)
class TestFilePathBesideBaselineSelection:
    def test_file_path_with_profile_and_target_is_a_usage_error(
        self, tmp_path: Path
    ) -> None:
        baseline = tmp_path / "abi-baseline.json"
        baseline.write_text("{}", encoding="utf-8")
        result = _validate(
            {
                "INPUT_MODE": "compare",
                "INPUT_NEW_LIBRARY": "new.so",
                "INPUT_ABI_BASELINE": str(baseline),
                "INPUT_BASELINE_PROFILE": "release",
                "INPUT_BASELINE_TARGET": "libfoo",
            }
        )
        # Refused, not silently resolved to the file: the selection would
        # otherwise be discarded without a word.
        assert result.returncode == 1, _output(result)
        assert "abi-baseline is a file path" in _output(result)
        assert "silently discarded" in _output(result)

    def test_file_path_alone_passes(self, tmp_path: Path) -> None:
        baseline = tmp_path / "abi-baseline.json"
        baseline.write_text("{}", encoding="utf-8")
        result = _validate(
            {
                "INPUT_MODE": "compare",
                "INPUT_NEW_LIBRARY": "new.so",
                "INPUT_ABI_BASELINE": str(baseline),
            }
        )
        assert result.returncode == 0, _output(result)
        assert "abi-baseline is a file path" not in _output(result)

    def test_release_tag_with_profile_and_target_still_passes(self) -> None:
        # The refusal is about a *file*: a tag or latest-release with the
        # selection is the supported shape and must keep passing.
        result = _validate(
            {
                "INPUT_MODE": "compare",
                "INPUT_NEW_LIBRARY": "new.so",
                "INPUT_ABI_BASELINE": "v1.0.0",
                "INPUT_BASELINE_PROFILE": "release",
                "INPUT_BASELINE_TARGET": "libfoo",
            }
        )
        assert result.returncode == 0, _output(result)


@pytest.mark.skipif(
    not VALIDATE_SH.is_file(), reason="action/validate-inputs.sh not found"
)
class TestExpectedArchiveDigestInput:
    def test_pin_with_a_selected_archive_passes(self) -> None:
        result = _validate(
            {
                "INPUT_MODE": "compare",
                "INPUT_NEW_LIBRARY": "new.so",
                "INPUT_ABI_BASELINE": "latest-release",
                "INPUT_BASELINE_PROFILE": "release",
                "INPUT_BASELINE_TARGET": "libfoo",
                "INPUT_EXPECTED_ARCHIVE_DIGEST": f"sha256:{DIGEST}",
            }
        )
        assert result.returncode == 0, _output(result)

    @pytest.mark.parametrize("pin", [DIGEST, f"SHA256:{DIGEST.upper()}"])
    def test_bare_and_prefixed_forms_are_both_accepted(self, pin: str) -> None:
        result = _validate(
            {
                "INPUT_MODE": "compare",
                "INPUT_NEW_LIBRARY": "new.so",
                "INPUT_ABI_BASELINE": "latest-release",
                "INPUT_BASELINE_PROFILE": "release",
                "INPUT_BASELINE_TARGET": "libfoo",
                "INPUT_EXPECTED_ARCHIVE_DIGEST": pin,
            }
        )
        assert result.returncode == 0, _output(result)

    def test_pin_without_a_selected_archive_is_refused(self) -> None:
        # Nothing else would fetch the archive it pins, so the pin would be
        # silently unused.
        result = _validate(
            {
                "INPUT_MODE": "compare",
                "INPUT_NEW_LIBRARY": "new.so",
                "INPUT_ABI_BASELINE": "latest-release",
                "INPUT_EXPECTED_ARCHIVE_DIGEST": f"sha256:{DIGEST}",
            }
        )
        assert result.returncode == 1, _output(result)
        assert "pins a release-contract baseline-set archive" in _output(result)

    @pytest.mark.parametrize(
        "pin",
        ["sha256:" + "a" * 63, "md5:" + "a" * 32, "sha256:" + "z" * 64],
    )
    def test_malformed_pin_is_refused_even_with_a_selection(self, pin: str) -> None:
        result = _validate(
            {
                "INPUT_MODE": "compare",
                "INPUT_NEW_LIBRARY": "new.so",
                "INPUT_ABI_BASELINE": "latest-release",
                "INPUT_BASELINE_PROFILE": "release",
                "INPUT_BASELINE_TARGET": "libfoo",
                "INPUT_EXPECTED_ARCHIVE_DIGEST": pin,
            }
        )
        assert result.returncode == 1, _output(result)
        assert "is not a SHA-256 digest" in _output(result)
