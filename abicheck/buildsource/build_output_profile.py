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

"""``build-output.json``'s ``profile.build_system`` (integration-lab P0.7's
build-system axis, WS-A): the ``profile`` value types and the
``build_system`` validator.

Split out of :mod:`.build_output` to keep that module under the production
file-size ceiling; :mod:`.build_output` re-exports nothing from here beyond
what it uses.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .build_evidence import BuildEvidence


def _opt_str(value: Any) -> str:
    return str(value) if isinstance(value, str) and value else ""


@dataclass
class BuildOutputBuildSystem:
    """Which build system (and generator, where it has one) produced this
    build (integration-lab P0.7 build-system axis).

    ``name`` is the build system (``cmake``, ``bazel``, ``make``,
    ``msbuild``, ...), ``generator`` the backend where one exists (CMake's
    ``Ninja``/``Unix Makefiles``), empty otherwise. Mirrors
    ``BuildEvidence.generators``' ``kind``/``generator`` so a validator can
    check the two agree.
    """

    name: str = ""
    generator: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "generator": self.generator}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> BuildOutputBuildSystem:
        return cls(name=_opt_str(d.get("name")), generator=_opt_str(d.get("generator")))


@dataclass
class BuildOutputProfile:
    """One build's OS/arch/compiler/config identity (ADR-047 §2).

    Singular by design: a single build produces binaries for exactly one
    profile, never a list — see ADR-047 §2's "one build-output.json = one
    build profile, always" note. A project matrixing over profiles publishes
    one uniquely-named ``abicheck-build-<profile.id>/`` artifact per profile
    (S17), not one artifact holding several.
    """

    id: str = ""
    os: str = ""
    arch: str = ""
    compiler: dict[str, str] = field(default_factory=dict)
    cxx_abi: str = ""
    stdlib: str = ""
    config: str = ""
    #: ``None`` when the producer did not declare one (unrecorded, never
    #: "no build system"); omitted from :meth:`to_dict` then.
    build_system: BuildOutputBuildSystem | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "os": self.os,
            "arch": self.arch,
            "compiler": dict(self.compiler),
            "cxx_abi": self.cxx_abi,
            "stdlib": self.stdlib,
            "config": self.config,
        }
        if self.build_system is not None:
            d["build_system"] = self.build_system.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> BuildOutputProfile:
        compiler_raw = d.get("compiler")
        compiler = (
            {str(k): str(v) for k, v in compiler_raw.items()}
            if isinstance(compiler_raw, dict)
            else {}
        )
        return cls(
            id=_opt_str(d.get("id")),
            os=_opt_str(d.get("os")),
            arch=_opt_str(d.get("arch")),
            compiler=compiler,
            cxx_abi=_opt_str(d.get("cxx_abi")),
            stdlib=_opt_str(d.get("stdlib")),
            config=_opt_str(d.get("config")),
            build_system=(
                BuildOutputBuildSystem.from_dict(d["build_system"])
                if isinstance(d.get("build_system"), dict)
                else None
            ),
        )


def build_system_issues(
    root: Path,
    manifest_path: Path,
    build_output: Any,  # BuildOutput; typed loosely to avoid an import cycle
    *,
    resolve_under_root: Callable[[Path, str], Path | None],
) -> list[str]:
    """``profile.build_system``'s shape, and its agreement with every
    ``attribution_path`` BuildEvidence that names a generator.

    Absent is valid (unrecorded). Present-but-malformed is an error rather
    than a silent drop, since a consumer reading it as absent would lose the
    identity the producer meant to assert.
    """
    with manifest_path.open(encoding="utf-8") as fh:
        raw_profile = json.load(fh).get("profile")
    if not isinstance(raw_profile, dict) or "build_system" not in raw_profile:
        return []  # absent: unrecorded. An explicit null is malformed.
    raw = raw_profile["build_system"]
    if not isinstance(raw, dict):
        return ["profile.build_system must be an object {name, generator}."]
    declared = build_output.profile.build_system
    if declared is None or not declared.name:
        return ["profile.build_system.name must be a non-empty string."]
    for key in ("name", "generator"):
        if key in raw and not isinstance(raw[key], str):
            return [f"profile.build_system.{key} must be a string."]
    issues: list[str] = []
    for t in build_output.targets:
        if t.evidence is None or not t.evidence.attribution_path:
            continue
        path = resolve_under_root(root, t.evidence.attribution_path)
        if path is None:
            continue  # reported by the attribution check itself
        try:
            with path.open(encoding="utf-8") as fh:
                evidence = BuildEvidence.from_dict(json.load(fh))
        except (AttributeError, OSError, ValueError, TypeError):
            continue  # reported by the attribution check itself
        kinds = {g.kind.lower() for g in evidence.generators} - {"", "generic"}
        if kinds and declared.name.lower() not in kinds:
            issues.append(
                f"target {t.id!r}: profile.build_system.name "
                f"{declared.name!r} disagrees with the build evidence at "
                f"{t.evidence.attribution_path!r}, which names "
                f"{', '.join(sorted(kinds))}."
            )
            continue
        declared_backend = declared.generator.strip()
        backends = {
            g.generator.strip()
            for g in evidence.generators
            if g.kind.lower() == declared.name.lower() and g.generator.strip()
        }
        if declared_backend and backends and declared_backend not in backends:
            issues.append(
                f"target {t.id!r}: profile.build_system.generator "
                f"{declared.generator!r} disagrees with the build evidence at "
                f"{t.evidence.attribution_path!r}, which names "
                f"{', '.join(sorted(backends))}."
            )
    return issues
