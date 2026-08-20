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

"""abicheck-internal-bugs finding 2, Codex review follow-up: "Preserve
existing unpinned snapshot baselines".

A baseline persisted before ``dumper_toolchain._probe_default_language_
standard`` existed recorded an empty ``language_standard`` for any unpinned
(no explicit ``-std=``, no forced-C++20-heuristic) dump. A freshly re-dumped
snapshot of the identical input under the identical toolchain now records a
real ``"probed:..."`` value there instead, purely because the tool was
upgraded. That must not, by itself, make the pair ``NOT_COMPARABLE`` --
``comparability._language_standard_probe_upgrade_corroborated`` is the
carve-out closing that gap, tested here directly against the gate (split out
of ``test_comparability_gate.py``, which sits at the AI-readiness file-size
hard cap, rather than appended there).
"""

from __future__ import annotations

import pytest

from abicheck.comparability import (
    check_contracts_comparable,
    compute_extraction_contract,
)
from abicheck.errors import ProfileMismatchError
from abicheck.model import AbiSnapshot


def _snap(contract) -> AbiSnapshot:  # noqa: ANN001
    return AbiSnapshot(library="libfoo.so", version="1.0", contract=contract)


def test_gate_empty_vs_probed_language_standard_waived_when_compiler_unchanged():
    """The exact scenario the reviewer flagged: an old (pre-probe) baseline
    and a freshly re-dumped snapshot of the identical input under the
    identical resolved compiler must stay comparable."""
    old = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="11.4.0",
            language_standard="",  # pre-probe baseline: always empty when unpinned
        )
    )
    new = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="11.4.0",
            language_standard="probed:__cplusplus=201703L",
        )
    )
    check_contracts_comparable(old, new)  # must not raise


def test_gate_empty_vs_probed_language_standard_still_raises_when_compiler_family_differs():
    """The carve-out must not blindly waive every empty-vs-probed pair --
    only when the resolved compiler itself is independently confirmed
    unchanged. A genuinely different compiler_family alongside the
    transition is still a real profile difference."""
    old = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="11.4.0",
            language_standard="",
        )
    )
    new = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="clang",
            compiler_version="18.1.3",
            language_standard="probed:__cplusplus=202002L",
        )
    )
    with pytest.raises(ProfileMismatchError):
        check_contracts_comparable(old, new)


def test_gate_empty_vs_probed_language_standard_still_raises_when_compiler_version_differs():
    """Same family, upgraded version: a real toolchain change between the
    baseline and the comparison must still be caught, not waived just
    because the family string happens to match."""
    old = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="9.4.0",
            language_standard="",
        )
    )
    new = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="13.2.0",
            language_standard="probed:__cplusplus=201703L",
        )
    )
    with pytest.raises(ProfileMismatchError):
        check_contracts_comparable(old, new)


def test_gate_empty_vs_probed_language_standard_not_waived_without_compiler_evidence():
    """When neither side records a compiler_family/compiler_version at all,
    there is nothing to corroborate the transition against -- the carve-out
    must decline, not treat missing evidence as a pass."""
    old = _snap(compute_extraction_contract(l2_frontend_ran=True, language_standard=""))
    new = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True, language_standard="probed:__cplusplus=201703L"
        )
    )
    with pytest.raises(ProfileMismatchError):
        check_contracts_comparable(old, new)
