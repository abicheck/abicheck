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

"""ADR-091 R14 for check-target's consumer inputs: a retired name fails the run.

``consumer-ast-frontend``, ``consumer-gcc-path`` and ``consumer-gcc-options``
were renamed to ``consumer-frontend``, ``consumer-compiler`` and
``consumer-compile-options``. Each old name stays a declared tombstone so a
caller that still sets it fails before analysis, instead of GitHub dropping an
undeclared input and the consumer compile being silently skipped.
"""

from __future__ import annotations

import pytest
from _check_target_exec import _BASE_IDENTITY, VALIDATE_SH, _run
from _workflow_exec import have_bash

pytestmark = pytest.mark.skipif(not have_bash(), reason="needs a real bash")

#: The identity every validate-inputs run needs (test_action_check_target.py's
#: own happy path) plus the one baseline path the accepted-main channel requires.
BASE = {**_BASE_IDENTITY, "INPUT_BASELINE_PATH": "./baseline"}

RENAMED = [
    ("consumer-ast-frontend", "INPUT_CONSUMER_AST_FRONTEND", "consumer-frontend", "clang"),
    ("consumer-gcc-path", "INPUT_CONSUMER_GCC_PATH", "consumer-compiler", "/usr/bin/g++"),
    (
        "consumer-gcc-options",
        "INPUT_CONSUMER_GCC_OPTIONS",
        "consumer-compile-options",
        "-fPIC",
    ),
]


def test_the_base_identity_validates(tmp_path) -> None:
    result = _run(VALIDATE_SH, dict(BASE), tmp_path)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(("old", "var", "new", "value"), RENAMED)
def test_setting_a_retired_consumer_input_fails_and_names_its_replacement(
    tmp_path, old: str, var: str, new: str, value: str
) -> None:
    result = _run(VALIDATE_SH, {**BASE, var: value}, tmp_path)
    message = result.stdout + result.stderr
    assert result.returncode != 0
    assert old in message and new in message, message
