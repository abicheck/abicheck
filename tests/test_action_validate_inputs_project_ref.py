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

"""baseline-project-ref preflight values, checked by validate-inputs.sh.

Split out of ``test_action_validate_inputs.py``, which is held at its
no-growth baseline. ``_run_validate`` is shared from there.
"""

from __future__ import annotations

import pytest
from test_action_validate_inputs import _run_validate


class TestBaselineProjectRefValues:
    """baseline-project-ref accepts a closed set; anything else fails preflight.

    action.yml defaults the input to 'tag', so the unset case (no env var, as
    a direct invocation sees it) and the explicit '' case (check off) must
    both pass validation, and only a typo or an unsupported spelling fails.
    """

    @pytest.mark.parametrize(
        "value",
        ["tag", "commit", "", "a" * 40, "b" * 64],
        ids=["tag", "commit", "empty-disables", "sha1", "sha256"],
    )
    def test_accepted_values_pass(self, value: str) -> None:
        result = _run_validate(
            {
                "INPUT_MODE": "compare",
                "INPUT_OLD_LIBRARY": "old.so",
                "INPUT_NEW_LIBRARY": "new.so",
                "INPUT_BASELINE_PROJECT_REF": value,
            }
        )
        assert result.returncode == 0, result.stdout + result.stderr

    def test_unset_passes_as_the_tag_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A CI job exports the input; the unset case must not inherit it.
        monkeypatch.delenv("INPUT_BASELINE_PROJECT_REF", raising=False)
        result = _run_validate(
            {
                "INPUT_MODE": "compare",
                "INPUT_OLD_LIBRARY": "old.so",
                "INPUT_NEW_LIBRARY": "new.so",
            }
        )
        assert result.returncode == 0, result.stdout + result.stderr

    @pytest.mark.parametrize("value", ["v2.3.0", "Tag", "abc", "g" * 40])
    def test_unrecognised_value_fails_preflight(self, value: str) -> None:
        result = _run_validate(
            {
                "INPUT_MODE": "compare",
                "INPUT_OLD_LIBRARY": "old.so",
                "INPUT_NEW_LIBRARY": "new.so",
                "INPUT_BASELINE_PROJECT_REF": value,
            }
        )
        assert result.returncode == 1
        assert "baseline-project-ref" in (result.stdout + result.stderr)
