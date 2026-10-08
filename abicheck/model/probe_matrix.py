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

"""Probe-matrix schema: the data a header-only probe run produces.

A probe spec (configurations x consumer TUs), one compiled pair's result, and
the version-stamped matrix of results. Pure data, so the compare-layer
build-configuration detectors (``diff_build_config``) and the workflows-layer
driver that fills it (``probe_harness.run_probe_matrix``) share one owner
without an inward-pointing import. Moved from ``probe_harness`` (lane B).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .snapshot import AbiSnapshot


@dataclass(frozen=True)
class ProbeConfiguration:
    """A (compiler, flags, defines) tuple."""

    id: str
    compiler: str
    flags: tuple[str, ...] = ()
    defines: dict[str, str] = field(default_factory=dict)
    include_dirs: tuple[str, ...] = ()
    cxx_std: int | None = None  # 17, 20, 23 — parsed from -std=c++NN

    def as_command_args(self) -> list[str]:
        """Return the compiler invocation prefix (binary + flags + defines)."""
        out: list[str] = [self.compiler, *self.flags]
        for k, v in self.defines.items():
            out.append(f"-D{k}={v}" if v else f"-D{k}")
        for d in self.include_dirs:
            out.append(f"-I{d}")
        return out


@dataclass(frozen=True)
class Probe:
    """One consumer TU snippet."""

    name: str
    headers: tuple[str, ...]
    body: str

    def render(self) -> str:
        """Generate the full .cpp source the harness will compile."""
        lines = []
        for h in self.headers:
            # Bare angle/quote characters are accepted as-is; the
            # YAML author writes ``<acme/lib/algorithm>`` or
            # ``"my_header.h"`` exactly as they would in C++.
            if h.startswith("<") or h.startswith('"'):
                lines.append(f"#include {h}")
            else:
                lines.append(f"#include <{h}>")
        lines.append("")
        lines.append(self.body.rstrip())
        return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class ProbeSpec:
    """A parsed probe-harness YAML manifest."""

    name: str
    configurations: tuple[ProbeConfiguration, ...]
    probes: tuple[Probe, ...]
    defaults: dict[str, str] = field(default_factory=dict)


@dataclass
class ProbeResult:
    """Outcome of compiling one (configuration × probe) pair."""

    configuration_id: str
    probe_id: str
    object_path: str | None = None
    snapshot: AbiSnapshot | None = None
    error: str | None = None


@dataclass
class MatrixSnapshot:
    """A version-stamped set of ProbeResults — the matrix-aware analogue
    of ``AbiSnapshot``."""

    library: str
    version: str
    spec_name: str
    cxx_stds: dict[str, int | None] = field(default_factory=dict)
    defaults: dict[str, str] = field(default_factory=dict)
    results: list[ProbeResult] = field(default_factory=list)

    def by_configuration(self) -> dict[str, list[ProbeResult]]:
        out: dict[str, list[ProbeResult]] = {}
        for r in self.results:
            out.setdefault(r.configuration_id, []).append(r)
        return out
