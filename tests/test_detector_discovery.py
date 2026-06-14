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

from __future__ import annotations

import pkgutil
import sys

import abicheck
from abicheck.detector_registry import registry


def _all_diff_module_names() -> set[str]:
    return {
        f"abicheck.{m.name}"
        for m in pkgutil.iter_modules(abicheck.__path__)
        if m.name.startswith("diff_")
    }


def test_ensure_loaded_imports_every_diff_module() -> None:
    # The footgun C4 closes: a diff_* module silently not imported (hence its
    # detectors never register). After ensure_loaded, every one must be present.
    registry.ensure_loaded()
    missing = _all_diff_module_names() - set(sys.modules)
    assert not missing, f"diff_* modules not imported by ensure_loaded: {sorted(missing)}"


def test_ensure_loaded_is_idempotent_and_order_stable() -> None:
    registry.ensure_loaded()
    names_first = registry.detector_names
    count_first = len(registry)
    # Second call must not re-register or reorder anything.
    registry.ensure_loaded()
    assert registry.detector_names == names_first
    assert len(registry) == count_first


def test_registry_has_detectors() -> None:
    registry.ensure_loaded()
    # Baseline sanity floor — guards against a refactor that wipes registration.
    # Current count is 49; keep a soft floor so legitimate growth/shrinkage of a
    # few detectors does not trip the gate, but a wholesale failure does.
    assert len(registry) >= 45


def test_detector_names_are_unique() -> None:
    registry.ensure_loaded()
    names = registry.detector_names
    assert len(names) == len(set(names))
