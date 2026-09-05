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

"""Self-registering detector registry.

Detectors register themselves via the ``@registry.detector`` decorator,
eliminating the manual detector list in ``compare()``.

Architecture review: Problem B — decouples detector definition from orchestration.

Usage in detector modules::

    from .detector_registry import registry

    @registry.detector("functions")
    def _diff_functions(old, new):
        ...

    @registry.detector("pe", requires_support=lambda o, n: (
        o.pe is not None and n.pe is not None,
        "missing PE metadata",
    ))
    def _diff_pe(old, new):
        ...

Usage in checker::

    from .detector_registry import registry

    def compare(old, new, ...):
        changes, detector_results = registry.run_all(old, new)
        # ... post-processing
"""
from __future__ import annotations

import importlib
import pkgutil
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING

from .detectors import DetectorResult

if TYPE_CHECKING:
    from .checker_types import Change
    from .model import AbiSnapshot

    DetectorFn = Callable[[AbiSnapshot, AbiSnapshot], list[Change]]
    SupportFn = Callable[[AbiSnapshot, AbiSnapshot], tuple[bool, str | None]]


class _DetectorEntry:
    """Internal representation of a registered detector."""

    __slots__ = ("name", "fn", "support_fn", "support_is_trigger", "order")

    def __init__(
        self,
        name: str,
        fn: DetectorFn,
        support_fn: SupportFn | None,
        order: int,
        support_is_trigger: bool = False,
    ) -> None:
        self.name = name
        self.fn = fn
        self.support_fn = support_fn
        #: ADR-067 D3: what a ``False`` from ``support_fn`` *means*.
        #:
        #: The default is an **evidence gate** -- "the input this detector
        #: needs is absent" (no PE metadata, no SYCL section, no DWARF on the
        #: old side), which is a genuine coverage limitation and is recorded
        #: ``not_evaluated``.
        #:
        #: A **trigger** predicate is the opposite: the evidence is present
        #: and conclusive, and it says there is nothing here to report. The
        #: layout-coherence detector's "neither snapshot records a
        #: DWARF-vs-header mismatch" is one -- both snapshots state their
        #: coherence, and *matched* is an answer, not a gap. Recording that as
        #: ``not_evaluated`` claims a coverage limitation for a detector that
        #: effectively ran and correctly found zero, which is exactly the
        #: distinction `not_evaluated` exists to make (Codex review).
        #:
        #: Declared at registration rather than inferred, because the boolean
        #: is identical either way -- only the predicate's *meaning* differs,
        #: and only its author knows it.
        self.support_is_trigger = support_is_trigger
        self.order = order


class DetectorRegistry:
    """Registry for self-registering ABI change detectors.

    Detectors are stored in registration order and executed sequentially
    by ``run_all()``.
    """

    def __init__(self) -> None:
        self._detectors: list[_DetectorEntry] = []
        self._names: set[str] = set()
        self._counter: int = 0
        self._discovered: bool = False
        self._discovery_lock = threading.Lock()

    # Modules that host detectors but are NOT named ``diff_*`` and so are not
    # found by prefix discovery. ``checker`` registers ``_diff_advanced_dwarf``
    # locally (kept there so tests can monkeypatch ``checker.diff_advanced_dwarf``)
    # — importing it standalone yields one fewer detector than a real
    # ``compare()`` run. Keep this list in sync with any such out-of-band
    # registration.
    _EXTRA_DETECTOR_MODULES = ("abicheck.checker",)

    def ensure_loaded(self) -> None:
        """Import every detector-hosting module so its detectors register.

        Safety net against the historical footgun where a new ``diff_*`` module
        had to be added by hand to ``checker``'s side-effect import block — a
        module that was forgotten contributed zero detectors with no error.

        Covers both the ``abicheck.diff_*`` modules (by prefix discovery) and the
        out-of-band detector hosts in :data:`_EXTRA_DETECTOR_MODULES` (currently
        ``checker``, which registers a monkeypatch-pinned detector locally). This
        guarantees ``registry.ensure_loaded(); registry.run_all(...)`` registers
        the *same* set as a real ``compare()`` run, even in a fresh process that
        never imported ``checker`` first.

        When called from inside ``compare()`` the modules are already in
        ``sys.modules`` (checker's explicit imports fixed the canonical
        registration order), so it is a no-op there; re-import does not
        re-register. A *new* ``diff_*`` module is discovered automatically,
        appended after the existing detectors in deterministic (sorted-by-name)
        order — no ``checker`` edit required. Idempotent and cheap after the
        first call.

        Thread-safe: concurrent callers (e.g. the MCP server handling parallel
        compare requests) take a lock so discovery runs exactly once. The fast
        path (already discovered) is lock-free.
        """
        if self._discovered:
            return
        with self._discovery_lock:
            # Re-check under the lock: another thread may have finished while we
            # were blocked.
            if self._discovered:
                return
            import abicheck

            module_names = sorted(
                f"abicheck.{info.name}"
                for info in pkgutil.iter_modules(abicheck.__path__)
                if info.name.startswith("diff_")
            )
            module_names.extend(self._EXTRA_DETECTOR_MODULES)
            for name in module_names:
                importlib.import_module(name)
            # Set only after a full successful pass, so a mid-loop import error
            # does not leave discovery permanently half-done on a retry.
            self._discovered = True

    def detector(
        self,
        name: str,
        *,
        requires_support: SupportFn | None = None,
        support_is_trigger: bool = False,
    ) -> Callable[[DetectorFn], DetectorFn]:
        """Decorator to register a detector function.

        Args:
            name: Unique detector name (used in DetectorResult and reporting).
            requires_support: Optional callable ``(old, new) -> (bool, reason)``
                that gates whether this detector runs.
            support_is_trigger: What a ``False`` from *requires_support*
                means. ``False`` (the default) -- the evidence this detector
                needs is absent, a real coverage gap, recorded
                ``not_evaluated``. ``True`` -- the evidence is present and
                conclusively says there is nothing to report, so the detector
                is recorded as an ordinary evaluated zero. See
                :attr:`_DetectorEntry.support_is_trigger`.

        Returns:
            The original function, unmodified.
        """
        def decorator(fn: DetectorFn) -> DetectorFn:
            if name in self._names:
                raise ValueError(f"Duplicate detector name: {name!r}")
            self._names.add(name)
            entry = _DetectorEntry(
                name,
                fn,
                requires_support,
                self._counter,
                support_is_trigger=support_is_trigger,
            )
            self._counter += 1
            self._detectors.append(entry)
            return fn
        return decorator

    def run_all(
        self,
        old: AbiSnapshot,
        new: AbiSnapshot,
    ) -> tuple[list[Change], list[DetectorResult]]:
        """Execute all registered detectors in registration order.

        Returns:
            (changes, detector_results) — aggregated changes and per-detector metadata.
        """
        changes: list[Change] = []
        detector_results: list[DetectorResult] = []

        for entry in sorted(self._detectors, key=lambda e: e.order):
            # Check support gate
            if entry.support_fn is not None:
                enabled, reason = entry.support_fn(old, new)
                if not enabled and entry.support_is_trigger:
                    # A conclusive trigger, not an evidence gate: the detector
                    # effectively ran and the answer is zero. Recorded as an
                    # ordinary evaluated zero, with no coverage gap -- and
                    # deliberately `enabled=True`, since `confidence.py` reads
                    # that flag and a "nothing to report" answer is not a
                    # reason to lower a run's analysis confidence.
                    detector_results.append(
                        DetectorResult(
                            name=entry.name,
                            changes_count=0,
                            enabled=True,
                        )
                    )
                    continue
                if not enabled:
                    detector_results.append(
                        DetectorResult(
                            name=entry.name,
                            changes_count=0,
                            enabled=False,
                            coverage_gap=reason,
                            # ADR-067 D3: the support gate refused this
                            # detector, so it produced no evidence at all --
                            # recorded as "not evaluated", never as a real
                            # zero. `reason` is *why*; this is the state.
                            not_evaluated=True,
                        )
                    )
                    continue

            # Run detector
            detected = entry.fn(old, new)
            changes.extend(detected)
            detector_results.append(
                DetectorResult(
                    name=entry.name,
                    changes_count=len(detected),
                    enabled=True,
                )
            )

        return changes, detector_results

    @property
    def detector_names(self) -> list[str]:
        """Registered detector names in registration order."""
        return [e.name for e in sorted(self._detectors, key=lambda e: e.order)]

    def __len__(self) -> int:
        return len(self._detectors)


# Module-level singleton — all detector modules import and register on this.
registry = DetectorRegistry()
