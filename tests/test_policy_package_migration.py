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

"""ADR-061: `policy`'s physically-migrated modules live only at their owner.

`severity.py`, `exit_decision.py`, and `contract_coverage_exit.py` moved
from `abicheck/<name>.py` to `abicheck/policy/<name>.py`. Their flat-path
re-export shims were retired under ADR-061's 2026-09-13 amendment (a
facade is a permanent second name for a thing that already has an owner),
so the old path must not quietly come back.
"""

from __future__ import annotations

import importlib
import importlib.util

import pytest

_MODULES = ("severity", "exit_decision", "contract_coverage_exit")


class TestRetiredFlatPathsStayRetired:
    """The flat shims are gone; only the `abicheck.policy` owner remains."""

    @pytest.mark.parametrize("name", (*_MODULES, "service_input_resolution"))
    def test_flat_path_does_not_resolve(self, name: str) -> None:
        assert importlib.util.find_spec(f"abicheck.{name}") is None


class TestNewCanonicalPathIsUsable:
    """The new `abicheck.policy.<name>` path is a real, independently
    importable module -- not merely reachable as a side effect of
    importing the old flat path."""

    @pytest.mark.parametrize("name", _MODULES)
    def test_new_module_imports_directly(self, name: str) -> None:
        module = importlib.import_module(f"abicheck.policy.{name}")
        assert module.__name__ == f"abicheck.policy.{name}"

    def test_policy_package_itself_imports(self) -> None:
        import abicheck.policy

        assert abicheck.policy.__name__ == "abicheck.policy"


class TestAnalysisAssuranceStaysFlat:
    """`analysis_assurance.py` is the one `policy`-classified file this
    migration deliberately left at its flat path (see the changelog
    fragment for why) -- pin that it still resolves normally rather than
    silently regressing to "missing"."""

    def test_analysis_assurance_still_resolves_at_the_flat_path(self) -> None:
        import abicheck.analysis_assurance as module

        assert hasattr(module, "analysis_assurance_exit_contribution")

    def test_analysis_assurance_has_not_moved_under_policy(self) -> None:
        import importlib.util

        assert importlib.util.find_spec("abicheck.policy.analysis_assurance") is None
