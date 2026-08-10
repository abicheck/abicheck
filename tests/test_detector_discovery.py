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
import subprocess
import sys
import threading

import abicheck
from abicheck.detector_registry import DetectorRegistry, registry


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


def test_ensure_loaded_includes_checker_local_detectors() -> None:
    # Regression (Codex review on #395): the 'advanced_dwarf' detector is
    # registered in checker.py, not a diff_* module. Standalone discovery must
    # still pick it up so registry.ensure_loaded() + run_all() matches a real
    # compare() run rather than silently dropping advanced-DWARF coverage.
    registry.ensure_loaded()
    assert "abicheck.checker" in sys.modules
    assert "advanced_dwarf" in registry.detector_names


def test_standalone_discovery_registers_advanced_dwarf() -> None:
    # The true regression scenario: a fresh process that calls
    # registry.ensure_loaded() WITHOUT importing abicheck.checker first. Run it in
    # a subprocess so the in-test imports (which already pulled checker) cannot
    # mask the bug. Discovery must still register the checker-local
    # 'advanced_dwarf' detector.
    code = (
        "from abicheck.detector_registry import registry\n"
        "registry.ensure_loaded()\n"
        "names = registry.detector_names\n"
        "assert 'advanced_dwarf' in names, names\n"
        "print(len(names))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stderr
    assert int(proc.stdout.strip()) >= 49


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


def test_ensure_loaded_concurrent_calls_are_safe() -> None:
    # A fresh registry instance whose discovery has not run yet. Hammer
    # ensure_loaded() from several threads at once; the lock must serialize the
    # one-time discovery without error or double-work corruption.
    fresh = DetectorRegistry()
    barrier = threading.Barrier(8)
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            barrier.wait()
            fresh.ensure_loaded()
        except BaseException as exc:  # noqa: BLE001 — record any thread failure
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert fresh._discovered is True


def test_param_qualifier_detectors_keep_their_registration_position() -> None:
    """G31 Phase C: moving a detector's BODY must not move its registration.

    ``registry.detector()`` stamps an incrementing counter and ``run_all()``
    executes in that order, so registration order fixes the order findings
    appear in every JSON/text report. When ``param_restrict``/``param_va_list``
    had their loop bodies split out to ``diff_param_qualifiers`` (``diff_symbols``
    is at the 2000-line hard cap), registering them from the new module moved
    them from indices 16/20 to 5/6 — a silent report-ordering change that no
    golden fixture covered, since none carries a restrict or va_list finding
    (Codex review).

    The registrations therefore stayed in ``diff_symbols`` at their original
    source positions. This pins the relative order that encodes, rather than
    the literal indices, which any unrelated new detector would shift.
    """
    registry.ensure_loaded()
    names = registry.detector_names
    order = {name: i for i, name in enumerate(names)}

    for required in ("functions", "fingerprint_renames", "param_restrict",
                     "param_va_list", "constants"):
        assert required in order, f"{required} is not registered"

    # Both run after the core function/rename detectors...
    assert order["param_restrict"] > order["functions"]
    assert order["param_restrict"] > order["fingerprint_renames"]
    # ...in their original relative order...
    assert order["param_va_list"] > order["param_restrict"]
    # ...and both still precede the header-tier constants detector that
    # followed them in diff_symbols' source.
    assert order["constants"] > order["param_va_list"]
