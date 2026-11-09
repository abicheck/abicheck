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

"""The one owner of *unobserved dimensions* in the assurance rollup.

An unobserved dimension is evidence a comparison needed but one side did not
observe -- C++20 constraints a header backend does not model, headers a dump
discarded, sections a load could not use, an export table not completely
read. None of them is a verdict; each only narrows what the run can claim, so
any status-lowering finding keeps the rollup from reading ``complete``.

Each dimension lives in its own private module and exposes a
``*_findings(old, new)`` function returning :class:`UnobservedFinding`
records. ``UNOBSERVED_DIMENSIONS`` is the declared registry
(gap modules build findings from the leaf ``_unobserved_finding``):
``_compute.compute_analysis_assurance`` iterates it for both the notes and the
status rollup, and ``workflows/analysis_assurance_attach`` iterates its
``coverage_warning=True`` providers for the reports' coverage-gap rows.
Adding a dimension means adding one :class:`UnobservedDimension` entry here --
never an ``or <gap>`` term in ``_compute.py``. A gap module that existed but
was never wired used to read as ``complete`` (the ``scope_fallback``
incident); ``tests/test_analysis_assurance_unobserved_registry.py`` fails for
any module under this package that is neither registered nor explicitly
classified there as a non-dimension axis.

Registry order is the order the notes are emitted in. Keep it stable: the
notes are a byte-compared report surface.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ._concepts import CONCEPT_GAP_DIMENSION, concept_constraint_findings
from ._depth_projection import (
    DEPTH_PROJECTION_DIMENSION,
    depth_projection_findings,
)
from ._export_read import EXPORT_READ_DIMENSION, export_read_findings
from ._noexcept import NOEXCEPT_GAP_DIMENSION, noexcept_findings
from ._scope_fallback import SCOPE_FALLBACK_DIMENSION, scope_fallback_findings
from ._template_defaults import (
    TEMPLATE_DEFAULTS_DIMENSION,
    template_default_findings,
)
from ._unobserved_finding import UnobservedFinding
from ._unreadable_sections import (
    UNREADABLE_SECTION_DIMENSION,
    unreadable_section_findings,
)

if TYPE_CHECKING:
    from ...model import AbiSnapshot

__all__ = [
    "UNOBSERVED_DIMENSIONS",
    "COVERAGE_WARNING_DIMENSIONS",
    "UnobservedDimension",
    "UnobservedFinding",
    "collect_unobserved",
]

FindingsFn = Callable[["AbiSnapshot | None", "AbiSnapshot"], list[UnobservedFinding]]


@dataclass(frozen=True, slots=True)
class UnobservedDimension:
    """A registered provider: dimension id, owning module, findings function.

    *coverage_warning* also routes its findings into
    ``DiffResult.coverage_warnings`` (ADR-085 D3, ADR-079).
    """

    dimension: str
    module: str
    findings: FindingsFn
    coverage_warning: bool = False


_PKG = __name__.rsplit(".", 1)[0]
#: The registry, in note-emission order (a constant: nothing to memoize).
UNOBSERVED_DIMENSIONS: tuple[UnobservedDimension, ...] = (
    UnobservedDimension(
        TEMPLATE_DEFAULTS_DIMENSION,
        f"{_PKG}._template_defaults",
        template_default_findings,
        coverage_warning=True,
    ),
    UnobservedDimension(
        CONCEPT_GAP_DIMENSION,
        f"{_PKG}._concepts",
        concept_constraint_findings,
        coverage_warning=True,
    ),
    UnobservedDimension(
        NOEXCEPT_GAP_DIMENSION,
        f"{_PKG}._noexcept",
        noexcept_findings,
        coverage_warning=True,
    ),
    UnobservedDimension(
        SCOPE_FALLBACK_DIMENSION, f"{_PKG}._scope_fallback", scope_fallback_findings
    ),
    UnobservedDimension(
        DEPTH_PROJECTION_DIMENSION,
        f"{_PKG}._depth_projection",
        depth_projection_findings,
    ),
    UnobservedDimension(
        UNREADABLE_SECTION_DIMENSION,
        f"{_PKG}._unreadable_sections",
        unreadable_section_findings,
    ),
    UnobservedDimension(
        EXPORT_READ_DIMENSION,
        f"{_PKG}._export_read",
        export_read_findings,
        coverage_warning=True,
    ),
)


#: Dimensions whose findings also become ``DiffResult.coverage_warnings``.
COVERAGE_WARNING_DIMENSIONS: frozenset[str] = frozenset(
    d.dimension for d in UNOBSERVED_DIMENSIONS if d.coverage_warning
)


def collect_unobserved(
    old: AbiSnapshot | None,
    new: AbiSnapshot,
    *,
    coverage_only: bool = False,
) -> list[UnobservedFinding]:
    """Every registered dimension's findings, in registry order."""
    return [
        finding
        for provider in UNOBSERVED_DIMENSIONS
        if not coverage_only or provider.coverage_warning
        for finding in provider.findings(old, new)
    ]
