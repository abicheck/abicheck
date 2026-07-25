#!/usr/bin/env python3
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

"""platform_capabilities.py — the host-OS x binary-format capability matrix.

ADR-051 Stage 2 tracked a 6th source-of-truth generator (a platform/capability
matrix) as deliberately not attempted, since — unlike CLI options, MCP tools,
the Python API, or config keys — there was no existing machine-checkable
schema to wire a generator into: "what symbol/type diff works on which host
for which binary format" is a fact about the tool's actual behavior, not
something derivable from a registry that already exists for another reason
(docs/contribute/usecase-registry.yaml is a per-use-case coverage registry,
not this matrix; CI workflow matrices record what's *validated*, not what's
*capable*).

This module is that missing source of truth: a small, hand-curated, pure-
Python data structure (the same "pure stdlib, importable" pattern as
evidence_tiers.py) recording, per binary format, which host OS can produce a
symbol-level diff and which can produce a full type/param diff, and what
tooling each combination requires. `scripts/gen_platform_matrix.py` renders it
into docs/reference/platforms.md's "Quick Reference: What Works Where"
section; nothing else should hand-edit that section.
"""

from __future__ import annotations

from dataclasses import dataclass

_KNOWN_HOSTS = ("linux", "macos", "windows")
_HOST_LABEL = {"linux": "Linux", "macos": "macOS", "windows": "Windows"}


@dataclass(frozen=True)
class HostCapability:
    """One host OS's capability when analyzing a given binary format."""

    host: str  # one of _KNOWN_HOSTS
    native: bool  # is this the binary format's own platform?
    symbol_diff: bool  # can abicheck produce a symbol-level diff here?
    type_param_diff: bool  # can abicheck produce a full type/param diff here?
    requires: str  # tooling needed, rendered verbatim as a table cell

    def __post_init__(self) -> None:
        if self.host not in _KNOWN_HOSTS:
            raise ValueError(f"unknown host {self.host!r}")
        if self.type_param_diff and not self.symbol_diff:
            raise ValueError(
                f"{self.host}: type/param diff implies symbol diff must hold too"
            )


@dataclass(frozen=True)
class FormatMatrix:
    """The full host-by-host capability row set for one binary format."""

    format_key: str  # short slug, e.g. "elf"
    section_heading: str  # doc H3 heading, e.g. "Scanning Linux ELF binaries"
    hosts: tuple[HostCapability, ...]
    best_results_note: str | None = None  # optional "**Best results:** ..." line

    def __post_init__(self) -> None:
        seen = {h.host for h in self.hosts}
        if seen != set(_KNOWN_HOSTS):
            raise ValueError(
                f"{self.format_key}: must cover exactly {_KNOWN_HOSTS}, got {sorted(seen)}"
            )
        natives = [h for h in self.hosts if h.native]
        if len(natives) != 1:
            raise ValueError(
                f"{self.format_key}: must have exactly one native host, got {natives}"
            )


#: The three binary formats abicheck understands, each with its own native
#: host and cross-platform fallback behavior. Ordering matches the existing
#: "Quick Reference: What Works Where" narrative in docs/reference/platforms.md.
PLATFORM_CAPABILITY_MATRIX: tuple[FormatMatrix, ...] = (
    FormatMatrix(
        format_key="elf",
        section_heading="Scanning Linux ELF binaries",
        hosts=(
            HostCapability("linux", True, True, True, "`castxml`, `g++`/`gcc`"),
            HostCapability("macos", False, True, False, "—"),
            HostCapability("windows", False, True, False, "—"),
        ),
        best_results_note="run on Linux with headers provided.",
    ),
    FormatMatrix(
        format_key="pe",
        section_heading="Scanning Windows PE (DLL) binaries",
        hosts=(
            HostCapability("linux", False, True, False, "`pefile`"),
            HostCapability("macos", False, True, False, "`pefile`"),
            HostCapability("windows", True, True, True, "`castxml` + `cl.exe`"),
        ),
    ),
    FormatMatrix(
        format_key="macho",
        section_heading="Scanning macOS Mach-O (dylib) binaries",
        hosts=(
            HostCapability("linux", False, True, False, "`macholib`"),
            HostCapability("macos", True, True, True, "`castxml` (Xcode clang)"),
            HostCapability("windows", False, True, False, "`macholib`"),
        ),
    ),
)


def host_label(host: str) -> str:
    """Render a host key as its doc-facing display label."""
    return _HOST_LABEL[host]
