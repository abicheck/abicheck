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

"""Uniform per-level provider protocol for the scan engine (ADR-035 D10).

The scan ladder is driven through one small interface so every level — the D2
pattern/preprocessor pre-scan, L3 build, L4 replay, L5 graph, and the D4
cross-checks — is *independently runnable* (ADR-033 D1) and a build-emitted /
external provider (ADR-035 D5, ADR-032 manifest) can drop in the same way.

The protocol is modelled on the ADR-032 ``DataExtractor`` but specialised for the
scan ladder: a provider declares **which S-method it implements and which L-layer
it populates** (the two orthogonal axes of ADR-035 D1), can ``estimate`` its cost
for *this* project, and ``run``s against a shared read-only :class:`ScanContext`
plus the D7 :class:`~abicheck.buildsource.poi.PointsOfInterest` work-list,
returning **normalized facts only** (never raw AST as a primary output).

This module is the typed *contract*: the existing collectors
(``inline.collect_inline_pack``, ``crosscheck.run_crosschecks``, …) are the
implementations the orchestrator drives. Keeping the contract here — free of any
heavy/compiler import — lets the CLI, ``service.run_scan``, and external providers
share one shape without pulling castxml/clang into a pure import path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ..model import AbiSnapshot
    from .poi import PointsOfInterest


@dataclass(frozen=True)
class ProviderCostEstimate:
    """Projected cost of one provider for *this* project (ADR-035 D10 dry-run).

    Mirrors ``service.CostEstimate`` but lives here so the provider contract has no
    dependency on the service layer (which imports buildsource, not the reverse).
    """

    method: str | None  # S-axis (s0..s6); None for an intrinsic L0-L2 provider
    layer: str  # L-axis it populates (L0_binary..L5_source_graph)
    tus: int = 0
    est_seconds: float = 0.0
    cache_hit_rate: float = 0.0
    note: str = ""


@dataclass(frozen=True)
class ProviderCapabilities:
    """What a provider needs and offers (ADR-032 ``ExtractorCapabilities`` shape)."""

    method: str | None  # S-method it implements (s0..s6) or None for intrinsic
    layer: str  # L-layer it populates
    needs_compiler: bool = False  # clang/castxml required (else reported skipped)
    needs_compile_db: bool = False  # a compile_commands.json required
    description: str = ""


@dataclass(frozen=True)
class ScanContext:
    """Shared, read-only inputs handed to every provider (ADR-035 D10).

    The L0-L2 surface is already parsed onto ``snapshot``; a provider only adds its
    own L3/L4/L5 (or cross-check) facts. ``budget`` is the failure-guard config
    (never a scope-shrinker), ``cache_dir`` the optional per-TU/content cache root.
    """

    snapshot: AbiSnapshot
    compile_db: Path | None = None
    sources: Path | None = None
    changed_paths: tuple[str, ...] = ()
    budget_seconds: float | None = None
    max_tus: int | None = None
    cache_dir: Path | None = None


@dataclass
class LayerFacts:
    """Normalized output of one provider run (never raw AST as primary output).

    ``facts`` is the count of normalized facts the provider produced; ``coverage``
    is the serialized coverage row (status/detail) for the mandatory report;
    ``payload`` carries the provider-specific normalized object (e.g. a
    ``BuildSourcePack``/``CrosscheckResult``) for the orchestrator to fold.
    """

    method: str | None
    layer: str
    status: str  # "present" | "partial" | "skipped" | "not_collected"
    facts: int = 0
    elapsed_s: float = 0.0
    skipped_reason: str | None = None
    detail: str = ""
    payload: Any = None
    coverage: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class LayerProvider(Protocol):
    """One level of the scan ladder (ADR-035 D10).

    Implementations declare the S-method/L-layer pair they cover, can estimate
    their cost for a project, and run against a shared :class:`ScanContext` + the
    D7 points-of-interest work-list, returning normalized :class:`LayerFacts`.
    """

    def capabilities(self) -> ProviderCapabilities: ...

    def estimate(self, ctx: ScanContext) -> ProviderCostEstimate: ...

    def run(self, ctx: ScanContext, poi: PointsOfInterest) -> LayerFacts: ...
