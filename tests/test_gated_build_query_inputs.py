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

"""``_gated_build_query_inputs`` is the one place a caller's "may this input
resolve an executable ``build.query``" decision is enforced (Codex review,
two rounds): ``build_config`` and ``build_query`` both pass only when
``allow_build_query`` is exactly ``True``, since a config file may itself
carry a ``build.query``. The retired ``scan`` had a second mode that let a
config's passive half through without that consent; it went with ``scan``'s
last caller (dead-code plan, Stage E)."""

from __future__ import annotations

from pathlib import Path

from abicheck.workflows.artifact.resolve import _gated_build_query_inputs


class TestGatedBuildQueryInputs:
    """``dump``/``compare``'s typed-API contract."""

    def test_both_nulled_when_query_not_allowed(self):
        config = Path("/tmp/.abicheck.yml")
        got_config, got_query = _gated_build_query_inputs(
            config, "cmake --build .", allow_build_query=False
        )
        assert got_config is None
        assert got_query is None

    def test_both_pass_through_when_query_allowed(self):
        config = Path("/tmp/.abicheck.yml")
        got_config, got_query = _gated_build_query_inputs(
            config, "cmake --build .", allow_build_query=True
        )
        assert got_config == config
        assert got_query == "cmake --build ."

    def test_none_inputs_stay_none_either_way(self):
        assert _gated_build_query_inputs(None, None, allow_build_query=False) == (
            None,
            None,
        )
        assert _gated_build_query_inputs(None, None, allow_build_query=True) == (
            None,
            None,
        )
