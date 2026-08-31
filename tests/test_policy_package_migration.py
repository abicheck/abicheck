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

"""ADR-061: `policy`'s physically-migrated `legacy_paths` modules stay
100% backward compatible at their old flat import path.

`severity.py`, `exit_decision.py`, and `contract_coverage_exit.py` moved
from `abicheck/<name>.py` to `abicheck/policy/<name>.py`; the old flat
path is now a thin, static back-compat shim. This module pins the shape
of that shim directly: every publicly re-exported name resolves to the
*identical object* through both the old and new import path (not merely
an equal-valued copy), and both `from abicheck.<name> import X` and
`from abicheck import <name>` keep working.
"""

from __future__ import annotations

import importlib

import pytest

_MODULES = ("severity", "exit_decision", "contract_coverage_exit")

#: A shimmed module's public surface normally lives entirely in the one
#: `abicheck.policy.<name>` module ADR-061 moved it to. `exit_decision` is
#: the one exception: ADR-064 added `resolve_scan_exit_decision`/
#: `resolve_release_exit_decision` directly to a sibling leaf module,
#: `exit_decision_precedence`, purely to keep `exit_decision.py` itself
#: under this package's 800-line production cap (Codex review: the combined
#: module reached 824 lines) -- so this flat shim's own `__all__` entries
#: are split across both real modules instead of the usual single one.
_ADDITIONAL_REAL_MODULES: dict[str, tuple[str, ...]] = {
    "exit_decision": ("exit_decision_precedence",),
}


class TestOldAndNewImportPathsAgree:
    """Every re-exported name is the same object via either import path."""

    @pytest.mark.parametrize("name", _MODULES)
    def test_shim_module_and_real_module_share_every_public_name(
        self, name: str
    ) -> None:
        old = importlib.import_module(f"abicheck.{name}")
        real_modules = [importlib.import_module(f"abicheck.policy.{name}")]
        real_modules.extend(
            importlib.import_module(f"abicheck.policy.{extra}")
            for extra in _ADDITIONAL_REAL_MODULES.get(name, ())
        )

        assert hasattr(old, "__all__"), f"abicheck.{name} must declare __all__"
        assert old.__all__, f"abicheck.{name}.__all__ must not be empty"

        for attr in old.__all__:
            owner = next((m for m in real_modules if hasattr(m, attr)), None)
            assert owner is not None, (
                f"none of {[m.__name__ for m in real_modules]} defines "
                f"{attr!r}, which the flat shim abicheck.{name} re-exports"
            )
            assert getattr(old, attr) is getattr(owner, attr), (
                f"abicheck.{name}.{attr} and {owner.__name__}.{attr} must "
                "be the identical object, not merely an equal one"
            )

    @pytest.mark.parametrize("name", _MODULES)
    def test_every_all_entry_is_public(self, name: str) -> None:
        old = importlib.import_module(f"abicheck.{name}")
        for attr in old.__all__:
            assert not attr.startswith("_"), (
                f"abicheck.{name}.__all__ names a private attribute {attr!r}"
            )


class TestBothImportSpellingsWork:
    """`from abicheck.<name> import X` and `from abicheck import <name>`
    both keep working through the old flat path, for a representative
    name from each moved module."""

    def test_from_abicheck_severity_import_x(self) -> None:
        from abicheck.severity import SeverityConfig, compute_exit_code

        assert compute_exit_code is not None
        assert SeverityConfig is not None

    def test_from_abicheck_import_severity(self) -> None:
        from abicheck import severity

        assert severity.compute_exit_code is not None

    def test_from_abicheck_exit_decision_import_x(self) -> None:
        from abicheck.exit_decision import ExitDecision, resolve_exit_decision

        assert ExitDecision is not None
        assert resolve_exit_decision is not None

    def test_from_abicheck_exit_decision_import_adr_064_resolvers(self) -> None:
        """ADR-064's two additive resolvers (`resolve_scan_exit_decision`/
        `resolve_release_exit_decision`) must reach the flat shim too, per
        this file's own contract that a moved module's *full* public
        surface stays reachable at the old path (Codex review: an earlier
        revision added both only to `abicheck.policy.exit_decision`,
        leaving this exact import raising `ImportError`).
        """
        from abicheck.exit_decision import (
            resolve_release_exit_decision,
            resolve_scan_exit_decision,
        )

        assert resolve_scan_exit_decision is not None
        assert resolve_release_exit_decision is not None

    def test_from_abicheck_import_exit_decision(self) -> None:
        from abicheck import exit_decision

        assert exit_decision.ExitDecision is not None

    def test_from_abicheck_contract_coverage_exit_import_x(self) -> None:
        from abicheck.contract_coverage_exit import coverage_exit_floor

        assert coverage_exit_floor is not None

    def test_from_abicheck_import_contract_coverage_exit(self) -> None:
        from abicheck import contract_coverage_exit

        assert contract_coverage_exit.coverage_exit_floor is not None


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

        assert (
            importlib.util.find_spec("abicheck.policy.analysis_assurance")
            is None
        )
