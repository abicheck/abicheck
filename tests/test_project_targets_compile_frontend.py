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

"""Tests for ``profiles.<id>.compile.frontend``/``consumer_compile.frontend``
(G34 Phase B), split into its own sibling file alongside
``test_project_targets_consumer_compile.py``.

Covers the schema side only: shape validation (must be one of
``auto``/``castxml``/``clang``/``hybrid``) and to_dict/from_dict
round-tripping. The projection into ``run-plan.json``'s
``compile_ast_frontend``/``consumer_compile_ast_frontend`` is covered by
``test_run_plan.py``'s ``TestCompileFrontendOverlayProjection``.
"""

from __future__ import annotations

import pytest

from abicheck.buildsource.project_targets import ProfileSpec, ProjectTargetsConfig


@pytest.mark.parametrize("value", ("auto", "castxml", "clang", "hybrid"))
def test_compile_frontend_accepts_each_documented_value(value: str) -> None:
    config = ProjectTargetsConfig.from_dict(
        {"profiles": {"linux": {"compile": {"frontend": value}}}}
    )
    assert config.profiles["linux"].compile.frontend == value


def test_compile_frontend_rejects_unknown_value() -> None:
    with pytest.raises(ValueError, match="frontend must be one of"):
        ProjectTargetsConfig.from_dict(
            {"profiles": {"linux": {"compile": {"frontend": "gcc"}}}}
        )


def test_compile_frontend_wrong_type_raises() -> None:
    with pytest.raises(ValueError, match="frontend must be a string"):
        ProjectTargetsConfig.from_dict(
            {"profiles": {"linux": {"compile": {"frontend": 1}}}}
        )


def test_profile_without_frontend_leaves_it_empty() -> None:
    config = ProjectTargetsConfig.from_dict(
        {"profiles": {"linux": {"compile": {"binding": "gcc14"}}}}
    )
    assert config.profiles["linux"].compile.frontend == ""
    assert "frontend" not in config.profiles["linux"].compile.to_dict()


def test_consumer_compile_frontend_is_independent_of_producer_compile() -> None:
    """G34 Phase B: consumer_compile.frontend accepts the same values,
    independently of compile.frontend on the same profile."""
    config = ProjectTargetsConfig.from_dict(
        {
            "profiles": {
                "linux-gcc14-build-clang20-client": {
                    "compile": {"frontend": "castxml"},
                    "consumer_compile": {"frontend": "clang"},
                }
            }
        }
    )
    profile = config.profiles["linux-gcc14-build-clang20-client"]
    assert profile.compile.frontend == "castxml"
    assert profile.consumer_compile.frontend == "clang"
    # Round-trips through to_dict/from_dict.
    round_tripped = ProfileSpec.from_dict(
        "linux-gcc14-build-clang20-client", profile.to_dict()
    )
    assert round_tripped.to_dict() == profile.to_dict()
