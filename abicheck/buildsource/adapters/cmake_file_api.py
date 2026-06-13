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

"""CMake File API adapter (ADR-029 D4).

Reads the CMake File API *reply* directory
(``<build>/.cmake/api/v1/reply/``) — never parses ``CMakeLists.txt``. The
codemodel gives the target graph; ``fileSets`` give public/private header
intent; ``toolchains`` give compiler provenance. This is pure on-disk JSON
reading: no build, no execution (ADR-028 D6).

The reply directory is produced when a *query* file exists before configure
(e.g. ``<build>/.cmake/api/v1/query/codemodel-v2``). Adapters do not write
queries or run CMake by default; if the reply is absent, ``collect`` returns
empty evidence with a diagnostic.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..build_evidence import (
    BuildEvidence,
    Confidence,
    Generator,
    Target,
    TargetKind,
    Toolchain,
)
from ..redaction import DEFAULT_REDACTION, RedactionPolicy

# CMake target type → normalized TargetKind.
_KIND_BY_TYPE: dict[str, TargetKind] = {
    "EXECUTABLE": TargetKind.EXECUTABLE,
    "STATIC_LIBRARY": TargetKind.STATIC_LIBRARY,
    "SHARED_LIBRARY": TargetKind.SHARED_LIBRARY,
    "MODULE_LIBRARY": TargetKind.SHARED_LIBRARY,
    "OBJECT_LIBRARY": TargetKind.OBJECT_LIBRARY,
    "INTERFACE_LIBRARY": TargetKind.INTERFACE,
}

_REPLY_REL = Path(".cmake/api/v1/reply")


class CMakeFileApiAdapter:
    """Normalize a CMake File API reply into :class:`BuildEvidence`."""

    name = "cmake_file_api"

    def __init__(
        self,
        build_dir: Path | str,
        *,
        redaction: RedactionPolicy | None = None,
    ) -> None:
        self.build_dir = Path(build_dir)
        self.reply_dir = self.build_dir / _REPLY_REL
        self.redaction = redaction or DEFAULT_REDACTION

    def collect(self) -> BuildEvidence:
        ev = BuildEvidence()
        if not self.reply_dir.is_dir():
            ev.diagnostics.append(
                f"cmake_file_api: no reply directory at {self.reply_dir} "
                "(query not registered before configure); skipping"
            )
            return ev

        index = self._load_index()
        if index is None:
            ev.diagnostics.append("cmake_file_api: no index-*.json in reply directory")
            return ev

        objects = {obj.get("kind"): obj for obj in index.get("objects", [])}
        self._collect_generator(index, ev)
        self._collect_toolchains(objects.get("toolchains"), ev)
        self._collect_codemodel(objects.get("codemodel"), ev)
        return ev

    # -- index --------------------------------------------------------------

    def _load_index(self) -> dict[str, Any] | None:
        candidates = sorted(self.reply_dir.glob("index-*.json"))
        if not candidates:
            return None
        return _read(candidates[-1])  # latest by lexical (timestamped) name

    def _ref(self, json_file: str) -> dict[str, Any] | None:
        if not json_file:
            return None
        path = self.reply_dir / json_file
        return _read(path) if path.is_file() else None

    # -- generator ----------------------------------------------------------

    def _collect_generator(self, index: dict[str, Any], ev: BuildEvidence) -> None:
        cmake = index.get("cmake", {})
        gen = cmake.get("generator", {})
        ev.generators.append(
            Generator(
                kind="cmake",
                version=str(cmake.get("version", {}).get("string", "")),
                generator=str(gen.get("name", "")),
            )
        )

    # -- toolchains ---------------------------------------------------------

    def _collect_toolchains(self, ref: dict[str, Any] | None, ev: BuildEvidence) -> None:
        obj = self._ref(ref.get("jsonFile", "")) if ref else None
        if obj is None:
            return
        for tc in obj.get("toolchains", []):
            compiler = tc.get("compiler", {})
            lang = str(tc.get("language", ""))
            ev.toolchains.append(
                Toolchain(
                    id=f"toolchain://{compiler.get('id', 'unknown')}-{lang}".lower(),
                    path=self.redaction.path(str(compiler.get("path", ""))),
                    compiler_id=str(compiler.get("id", "")),
                    version=str(compiler.get("version", "")),
                    language=lang,
                    implicit_include_dirs=[
                        self.redaction.path(str(d.get("path", "")))
                        for d in compiler.get("implicit", {}).get("includeDirectories", [])
                    ],
                    target_triple=str(compiler.get("target", "")),
                )
            )

    # -- codemodel ----------------------------------------------------------

    def _collect_codemodel(self, ref: dict[str, Any] | None, ev: BuildEvidence) -> None:
        obj = self._ref(ref.get("jsonFile", "")) if ref else None
        if obj is None:
            return
        seen: set[str] = set()
        for config in obj.get("configurations", []):
            for target_ref in config.get("targets", []):
                detail = self._ref(target_ref.get("jsonFile", ""))
                if detail is None:
                    continue
                target = self._target_from_detail(detail)
                if target.id not in seen:
                    ev.targets.append(target)
                    seen.add(target.id)

    def _target_from_detail(self, detail: dict[str, Any]) -> Target:
        name = str(detail.get("name", ""))
        kind = _KIND_BY_TYPE.get(str(detail.get("type", "")), TargetKind.UNKNOWN)
        outputs = [
            self.redaction.path(str(a.get("path", "")))
            for a in detail.get("artifacts", [])
        ]
        deps = [
            f"target://{str(d.get('id', '')).split('::')[0]}"
            for d in detail.get("dependencies", [])
        ]
        public_headers, private_headers, sources = self._partition_sources(detail)
        return Target(
            id=f"target://{name}",
            name=name,
            kind=kind,
            build_system="cmake",
            source_files=sources,
            public_headers=public_headers,
            private_headers=private_headers,
            outputs=outputs,
            dependencies=deps,
            visibility=_visibility_for(public_headers, private_headers),
            confidence=Confidence.HIGH,
        )

    def _partition_sources(
        self, detail: dict[str, Any]
    ) -> tuple[list[str], list[str], list[str]]:
        """Split a target's sources into public headers, private headers, sources.

        Uses ``fileSets`` (CMake >= 3.23) for header visibility when present;
        falls back to extension heuristics otherwise.
        """
        file_sets = detail.get("fileSets", [])
        public: list[str] = []
        private: list[str] = []
        sources: list[str] = []
        for src in detail.get("sources", []):
            path = self.redaction.path(str(src.get("path", "")))
            if not path:
                continue
            fs_index = src.get("fileSetIndex")
            if isinstance(fs_index, int) and 0 <= fs_index < len(file_sets):
                fs = file_sets[fs_index]
                if str(fs.get("type", "")).upper() == "HEADERS":
                    vis = str(fs.get("visibility", "")).upper()
                    (public if vis in ("PUBLIC", "INTERFACE") else private).append(path)
                    continue
            if path.lower().endswith((".h", ".hh", ".hpp", ".hxx", ".h++")):
                private.append(path)
            else:
                sources.append(path)
        return public, private, sources


def _visibility_for(public_headers: list[str], private_headers: list[str]) -> str:
    if public_headers:
        return "public"
    if private_headers:
        return "private"
    return "unknown"


def _read(path: Path) -> dict[str, Any] | None:
    try:
        with path.open(encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None
