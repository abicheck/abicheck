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

"""Data model for bundle-aware multi-library ABI analysis (ADR-023).

Holds the dataclasses that describe a release viewed as a bundle of
libraries: the resolution graph, the per-release snapshot, and the
finding/result types produced by :func:`abicheck.bundle.compare_bundle`.

This is a leaf module: it imports nothing from :mod:`abicheck.bundle`. The
types here are re-exported from :mod:`abicheck.bundle` so the historical
``from abicheck.bundle import BundleSnapshot`` import paths keep working.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .checker_policy import ChangeKind, Verdict, compute_verdict
from .checker_types import Change, DiffResult
from .elf_metadata import ElfMetadata

# Symbols imported by virtually every C/C++ shared library that are
# provided by the system loader, not by the bundle. Resolution against the
# bundle is meaningless for these; ignore unresolved imports against this
# set when emitting :class:`ChangeKind.BUNDLE_INTRA_DEP_REMOVED`.
DEFAULT_SYSTEM_PROVIDERS: frozenset[str] = frozenset(
    {
        "libc.so.6",
        "libc.so.7",
        "libm.so.6",
        "libdl.so.2",
        "libpthread.so.0",
        "librt.so.1",
        "libstdc++.so.6",
        "libc++.so.1",
        "libc++abi.so.1",
        "libgcc_s.so.1",
        "libgomp.so.1",
        "libtbb.so.12",
        "libtbb.so.2",
        "libsycl.so",
        "libsycl.so.7",
        "libsycl.so.8",
        "libOpenCL.so.1",
        "libz.so.1",
        "ld-linux-x86-64.so.2",
        "ld-linux-aarch64.so.1",
    }
)


@dataclass(frozen=True)
class ProviderEntry:
    """One library in the bundle that exports ``symbol``."""

    library: str  # e.g. "libcore.so"
    version: str  # gnu.version_d tag, "" if unversioned


@dataclass(frozen=True)
class ConsumerEntry:
    """One library in the bundle that imports ``symbol``."""

    library: str  # e.g. "libalgo.so"
    version: str  # gnu.version_r required version, "" if unversioned
    weak: bool  # True when the import is weak (unresolved is OK)
    # Verneed provider soname for this symbol's required version ("" if unknown
    # or unversioned). Disambiguates colliding version labels across providers.
    version_soname: str = ""


@dataclass
class ResolutionGraph:
    """Bundle-level symbol resolution graph.

    The bundle layer answers questions like "which library in this release
    provides core_add?" and "which siblings import a symbol that no sibling
    exports?" by indexing the metadata of every library found in the
    release directory.
    """

    # symbol -> [providers]; one entry per defining library
    provides: dict[str, list[ProviderEntry]] = field(default_factory=dict)
    # symbol -> [consumers]; one entry per importing library
    consumers: dict[str, list[ConsumerEntry]] = field(default_factory=dict)
    # Per-library DT_NEEDED edges as bundle-relative library names.
    # library -> list of NEEDED sonames (only those that resolve inside the bundle).
    intra_needed: dict[str, list[str]] = field(default_factory=dict)
    # library -> DT_NEEDED sonames that did NOT resolve inside the bundle
    # (likely system libs — see DEFAULT_SYSTEM_PROVIDERS).
    extra_needed: dict[str, list[str]] = field(default_factory=dict)

    def providers_for(self, symbol: str) -> list[ProviderEntry]:
        return list(self.provides.get(symbol, ()))

    def consumers_of(self, symbol: str) -> list[ConsumerEntry]:
        return list(self.consumers.get(symbol, ()))


@dataclass
class BundleSnapshot:
    """A release directory captured as a bundle.

    Holds per-library ELF metadata and the precomputed resolution graph.
    """

    root: Path  # the release directory
    libraries: dict[str, Path]  # library_name -> filesystem path
    metadata: dict[str, ElfMetadata]  # library_name -> parsed ELF metadata
    resolution: ResolutionGraph

    @property
    def library_names(self) -> list[str]:
        return sorted(self.libraries.keys())

    def is_intra_bundle_provider(self, soname: str) -> bool:
        """Return True if ``soname`` matches a library inside this bundle.

        Matches on either the raw filename (``libfoo.so``) or the soname
        encoded by the library (``libfoo.so.1``).
        """
        if soname in self.libraries:
            return True
        for name, meta in self.metadata.items():
            if meta.soname == soname:
                return True
            # Allow filename-stem fallback (libfoo.so matches libfoo.so.1)
            if soname.startswith(name + "."):
                return True
            if name.startswith(soname + "."):
                return True
        return False


@dataclass
class BundleFinding:
    """A single bundle-level finding.

    Mirrors :class:`Change` so the same reporter / suppression / severity
    machinery can consume bundle findings without special-casing. The
    ``consumer_library`` / ``provider_library`` fields distinguish bundle
    findings from per-library changes.
    """

    kind: ChangeKind
    symbol: str  # mangled symbol name or type name
    description: str
    consumer_library: str | None = None  # affected library (for intra-dep findings)
    provider_library: str | None = None  # source-of-change library
    old_value: str | None = None
    new_value: str | None = None
    affected_libraries: list[str] = field(default_factory=list)
    # ADR-027 A3/D3.2 — per-finding reachability modulation, mirroring the A4
    # Change override so a demotion reaches the bundle verdict (which lowers
    # findings to Change and classifies via effective_category). Default None =
    # classify by kind, i.e. today's behaviour.
    effective_verdict: Verdict | None = None
    modulation_reason: str | None = None
    modulation_rule: str | None = None

    def to_change(self) -> Change:
        """Lower a :class:`BundleFinding` into the :class:`Change` representation.

        Used by the JSON/Markdown reporters that already know how to render
        ``Change`` objects. The bundle attribution fields are flattened into
        ``description`` so they survive the lowering. A reachability modulation
        (D3.2) is propagated onto the lowered ``Change`` so the bundle verdict
        and the compare-release exit code honour it.
        """
        prefix = ""
        if self.consumer_library and self.provider_library:
            prefix = f"[{self.consumer_library} ← {self.provider_library}] "
        elif self.provider_library:
            prefix = f"[{self.provider_library}] "
        elif self.consumer_library:
            prefix = f"[{self.consumer_library}] "
        return Change(
            kind=self.kind,
            symbol=self.symbol,
            description=prefix + self.description,
            old_value=self.old_value,
            new_value=self.new_value,
            affected_symbols=list(self.affected_libraries) or None,
            effective_verdict=self.effective_verdict,
            modulation_reason=self.modulation_reason,
            modulation_rule=self.modulation_rule,
        )


@dataclass
class BundleDiffResult:
    """Output of :func:`abicheck.bundle.compare_bundle`.

    Bundle findings are kept distinct from per-library diff results so a
    consumer (reporter, JSON output) can render them under their own
    section. The aggregate ``verdict`` is the worst of (worst per-library
    verdict, ``bundle_verdict``).
    """

    old_root: Path
    new_root: Path
    per_library: list[DiffResult] = field(default_factory=list)
    bundle_findings: list[BundleFinding] = field(default_factory=list)

    @property
    def bundle_verdict(self) -> Verdict:
        changes = [f.to_change() for f in self.bundle_findings]
        return compute_verdict(changes)

    @property
    def per_library_verdict(self) -> Verdict:
        order = [
            Verdict.NO_CHANGE,
            Verdict.COMPATIBLE,
            Verdict.COMPATIBLE_WITH_RISK,
            Verdict.API_BREAK,
            Verdict.BREAKING,
        ]
        worst = Verdict.NO_CHANGE
        for r in self.per_library:
            if order.index(r.verdict) > order.index(worst):
                worst = r.verdict
        return worst

    @property
    def verdict(self) -> Verdict:
        order = [
            Verdict.NO_CHANGE,
            Verdict.COMPATIBLE,
            Verdict.COMPATIBLE_WITH_RISK,
            Verdict.API_BREAK,
            Verdict.BREAKING,
        ]
        return max(self.per_library_verdict, self.bundle_verdict, key=order.index)
