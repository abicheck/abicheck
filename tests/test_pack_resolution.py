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

"""Contracts of ``workflows.pack_resolution`` that a front end depends on."""

from __future__ import annotations


def test_release_default_base_policy_matches_the_cli_policy_default() -> None:
    """``workflows.pack_resolution`` cannot import the frontends constant
    (ADR-061 import direction), so it carries its own copy of the ``--policy``
    default; this pins the copy to the one Click actually uses."""
    from abicheck.frontends.cli.options.params import DEFAULT_POLICY_PROFILE
    from abicheck.workflows.pack_resolution import DEFAULT_BASE_POLICY

    assert DEFAULT_BASE_POLICY == DEFAULT_POLICY_PROFILE
