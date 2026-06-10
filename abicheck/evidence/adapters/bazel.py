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

"""Bazel adapter (ADR-029 D6).

Consumes Bazel's official query outputs rather than parsing ``BUILD`` files:

- ``bazel cquery ... --output=jsonproto`` → the *configured* target graph
  (target kinds, ``deps`` after ``select()``, sources, public headers);
- ``bazel aquery ... --output=jsonproto`` → the action graph (exact compile and
  link argv, inputs, outputs, mnemonics).

Both are *analysis* queries of an existing workspace — they do not build the
project (ADR-028 D6 / ADR-029 D10). Like the Ninja adapter, ``collect`` also
accepts **pre-captured** query output (inline JSON text or a file path) so the
fast lane and hermetic CI never need a live ``bazel``.

Confidence rules (ADR-029 D6): ``aquery`` actions are high-confidence for
commands/inputs/outputs and ``cquery`` is high-confidence for the configured
target graph; public/private header *intent* is reduced confidence because it
depends on rule-specific conventions rather than an explicit visibility model.

Only the textual ``jsonproto`` form is parsed; a binary proto blob is recorded
as a diagnostic rather than mis-read (pass ``--output=jsonproto``).
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from ...build_context import _extract_flags
from ..build_evidence import (
    BuildEvidence,
    CompileUnit,
    Confidence,
    Generator,
    LinkUnit,
    Target,
    TargetKind,
)
from ..redaction import DEFAULT_REDACTION, RedactionPolicy
from .base import (
    compile_unit_id,
    derive_build_options,
    detect_language,
    extract_abi_relevant_flags,
)

# Bazel rule class → normalized TargetKind. cc_library defaults to an archive;
# its optional shared output is modelled separately by the linkstatic action.
_KIND_BY_RULE: dict[str, TargetKind] = {
    "cc_binary": TargetKind.EXECUTABLE,
    "cc_test": TargetKind.EXECUTABLE,
    "cc_library": TargetKind.STATIC_LIBRARY,
    "cc_shared_library": TargetKind.SHARED_LIBRARY,
    "objc_library": TargetKind.STATIC_LIBRARY,
}

#: aquery mnemonics that denote a compile action (one translation unit).
_COMPILE_MNEMONICS = frozenset(
    {"CppCompile", "CCompile", "CcCompile", "ObjcCompile", "ObjcppCompile", "CppModuleCompile"}
)
#: aquery mnemonics that denote a link/archive action (one library/executable).
_LINK_MNEMONICS = frozenset({"CppLink", "CppArchive", "CcLink"})


class BazelAdapter:
    """Normalize Bazel ``cquery``/``aquery`` jsonproto into :class:`BuildEvidence`."""

    name = "bazel"

    def __init__(
        self,
        workspace: Path | str | None = None,
        *,
        target: str | None = None,
        cquery: str | Path | None = None,
        aquery: str | Path | None = None,
        allow_query: bool = True,
        redaction: RedactionPolicy | None = None,
    ) -> None:
        self.workspace = Path(workspace) if workspace is not None else None
        self.target = target
        self._cquery = cquery
        self._aquery = aquery
        self.allow_query = allow_query
        self.redaction = redaction or DEFAULT_REDACTION

    def collect(self) -> BuildEvidence:
        ev = BuildEvidence()
        ev.generators.append(Generator(kind="bazel"))

        cq_text = self._resolve("cquery", self._cquery, ev)
        if cq_text is not None:
            self._collect_cquery(cq_text, ev)

        aq_text = self._resolve("aquery", self._aquery, ev)
        if aq_text is not None:
            self._collect_aquery(aq_text, ev)

        # Project per-unit ABI flags into diffable build options, same as every
        # other adapter, so a Bazel-only pack still reports flag drift (D9).
        ev.build_options = derive_build_options(ev.compile_units)
        return ev

    # -- input resolution ---------------------------------------------------

    def _resolve(self, kind: str, value: str | Path | None, ev: BuildEvidence) -> str | None:
        text = _as_text(value)
        if text is not None:
            return text
        if self.workspace is not None and self.target is not None and self.allow_query:
            return self._run_bazel(kind, ev)
        return None

    def _run_bazel(self, kind: str, ev: BuildEvidence) -> str | None:
        bazel = shutil.which("bazel") or shutil.which("bazelisk")
        if bazel is None or self.workspace is None or self.target is None:
            ev.diagnostics.append(f"bazel: executable not found on PATH; cannot run {kind}")
            return None
        cmd = [bazel, kind, f"deps({self.target})", "--output=jsonproto"]
        try:
            # An analysis query of an existing workspace (ADR-028 D6 / D10) —
            # never a build action.
            proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
                cmd, cwd=str(self.workspace), capture_output=True, text=True,
                timeout=300, check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            ev.diagnostics.append(f"bazel: {kind} failed: {exc}")
            return None
        if proc.returncode != 0:
            ev.diagnostics.append(
                f"bazel: {kind} exited {proc.returncode}: {proc.stderr.strip()[:200]}"
            )
            return None
        return proc.stdout

    # -- cquery: configured target graph ------------------------------------

    def _collect_cquery(self, text: str, ev: BuildEvidence) -> None:
        data = _load_jsonproto(text, "cquery", ev)
        if data is None:
            return
        seen: set[str] = set()
        for ct in _dicts(data.get("results")):
            target_obj = ct.get("target")
            rule = target_obj.get("rule") if isinstance(target_obj, dict) else None
            if not isinstance(rule, dict):
                continue
            target = self._target_from_rule(rule)
            if target is not None and target.id not in seen:
                ev.targets.append(target)
                seen.add(target.id)

    def _target_from_rule(self, rule: dict[str, object]) -> Target | None:
        name = str(rule.get("name", ""))
        if not name:
            return None
        attrs = _attr_map(rule.get("attribute", []))
        srcs = [self.redaction.path(s) for s in attrs.get("srcs", [])]
        hdrs = [self.redaction.path(h) for h in attrs.get("hdrs", [])]
        deps = [f"target://{d}" for d in attrs.get("deps", [])]
        return Target(
            id=f"target://{name}",
            name=name.rsplit(":", 1)[-1],
            kind=_KIND_BY_RULE.get(str(rule.get("ruleClass", "")), TargetKind.UNKNOWN),
            build_system="bazel",
            source_files=srcs,
            public_headers=hdrs,
            outputs=[self.redaction.path(o) for o in _str_list(rule.get("ruleOutput"))],
            dependencies=deps,
            visibility="public" if hdrs else "unknown",
            # Graph facts (kind/deps/outputs) are high-confidence; header intent
            # is heuristic, but the dominant target facts justify HIGH here.
            confidence=Confidence.HIGH,
        )

    # -- aquery: action graph -----------------------------------------------

    def _collect_aquery(self, text: str, ev: BuildEvidence) -> None:
        data = _load_jsonproto(text, "aquery", ev)
        if data is None:
            return
        graph = _AqueryGraph(data)
        for action in _dicts(data.get("actions")):
            mnemonic = str(action.get("mnemonic", ""))
            if mnemonic in _COMPILE_MNEMONICS:
                cu = self._compile_unit(action, graph)
                if cu is not None:
                    ev.compile_units.append(cu)
            elif mnemonic in _LINK_MNEMONICS:
                lu = self._link_unit(action, graph)
                if lu is not None:
                    ev.link_units.append(lu)

    def _compile_unit(self, action: dict[str, object], graph: _AqueryGraph) -> CompileUnit | None:
        argv = _str_list(action.get("arguments"))
        source = _source_from_argv(argv)
        if not source:
            return None
        ctx = _extract_flags(argv, Path("."))
        output = graph.path(action.get("primaryOutputId"))
        red_argv = self.redaction.argv(argv)
        red_source = self.redaction.path(source)
        return CompileUnit(
            id=compile_unit_id(red_source, red_argv, output),
            source=red_source,
            output=self.redaction.path(output),
            target_id=graph.target_id(action.get("targetId")),
            argv=red_argv,
            language=detect_language(source),
            standard=ctx.language_standard or "",
            defines={k: self.redaction.define_value(k, v or "") for k, v in ctx.defines.items()},
            undefines=sorted(ctx.undefines),
            include_paths=[self.redaction.path(str(p)) for p in ctx.include_paths],
            system_include_paths=[self.redaction.path(str(p)) for p in ctx.system_includes],
            sysroot=self.redaction.path(str(ctx.sysroot)) if ctx.sysroot else None,
            target_triple=ctx.target_triple or "",
            abi_relevant_flags=[self.redaction.arg(f) for f in extract_abi_relevant_flags(argv)],
        )

    def _link_unit(self, action: dict[str, object], graph: _AqueryGraph) -> LinkUnit | None:
        output = graph.path(action.get("primaryOutputId"))
        if not output:
            return None
        inputs = [p for p in graph.input_paths(action) if p.endswith((".o", ".obj", ".a"))]
        red_output = self.redaction.path(output)
        return LinkUnit(
            id=f"link://{red_output}",
            target_id=graph.target_id(action.get("targetId")),
            output=red_output,
            kind=_link_kind(output),
            inputs=[self.redaction.path(p) for p in inputs],
            linker_argv=self.redaction.argv(_str_list(action.get("arguments"))),
        )


class _AqueryGraph:
    """Resolves aquery artifact ids to exec paths and action targets to labels.

    aquery jsonproto stores paths as a deduplicated fragment tree: each artifact
    points at a ``pathFragment`` whose ``parentId`` chain spells the exec path.
    Inputs are referenced indirectly through ``depSetOfFiles`` nesting.
    """

    def __init__(self, data: dict[str, object]) -> None:
        self._frag = {
            str(f.get("id")): (str(f.get("label", "")), f.get("parentId"))
            for f in _dicts(data.get("pathFragments"))
        }
        self._artifact_frag = {
            str(a.get("id")): str(a.get("pathFragmentId"))
            for a in _dicts(data.get("artifacts"))
        }
        self._labels = {
            str(t.get("id")): str(t.get("label", ""))
            for t in _dicts(data.get("targets"))
        }
        self._depsets = {
            str(d.get("id")): d
            for d in _dicts(data.get("depSetOfFiles"))
        }

    def path(self, artifact_id: object) -> str:
        if artifact_id is None:
            return ""
        frag_id: object = self._artifact_frag.get(str(artifact_id))
        parts: list[str] = []
        seen: set[str] = set()
        while frag_id is not None and str(frag_id) in self._frag and str(frag_id) not in seen:
            seen.add(str(frag_id))
            label, parent = self._frag[str(frag_id)]
            if label:
                parts.append(label)
            frag_id = parent
        return "/".join(reversed(parts))

    def target_id(self, target_id: object) -> str:
        label = self._labels.get(str(target_id), "") if target_id is not None else ""
        return f"target://{label}" if label else ""

    def input_paths(self, action: dict[str, object]) -> list[str]:
        out: list[str] = []
        for art_id in self._flatten_depsets(_str_list(action.get("inputDepSetIds"))):
            p = self.path(art_id)
            if p:
                out.append(p)
        return out

    def _flatten_depsets(self, depset_ids: list[str]) -> list[str]:
        artifacts: list[str] = []
        seen: set[str] = set()
        stack = list(depset_ids)
        while stack:
            ds_id = stack.pop()
            if ds_id in seen:
                continue
            seen.add(ds_id)
            ds = self._depsets.get(ds_id)
            if ds is None:
                continue
            artifacts.extend(_str_list(ds.get("directArtifactIds")))
            stack.extend(_str_list(ds.get("transitiveDepSetIds")))
        return artifacts


def _link_kind(output: str) -> str:
    low = output.lower()
    if low.endswith((".so", ".dylib")) or ".so." in low:
        return "shared_library"
    if low.endswith((".a", ".lib")):
        return "static_library"
    return "executable"


def _source_from_argv(argv: list[str]) -> str:
    """Return the first argv token that names a compilable source file."""
    for arg in argv[1:]:
        if not arg.startswith("-") and detect_language(arg):
            return arg
    return ""


def _attr_map(attributes: object) -> dict[str, list[str]]:
    """Collapse a rule's ``attribute`` list into {name: [string-list values]}."""
    out: dict[str, list[str]] = {}
    for attr in _dicts(attributes):
        name = str(attr.get("name", ""))
        if not name:
            continue
        values = _str_list(attr.get("stringListValue"))
        if not values and attr.get("stringValue"):
            values = [str(attr.get("stringValue"))]
        if values:
            out[name] = values
    return out


def _load_jsonproto(text: str, kind: str, ev: BuildEvidence) -> dict[str, object] | None:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        ev.diagnostics.append(
            f"bazel: could not parse {kind} output as jsonproto ({exc}); "
            "pass --output=jsonproto (binary proto is not supported)"
        )
        return None
    if not isinstance(data, dict):
        ev.diagnostics.append(f"bazel: {kind} jsonproto was not a JSON object")
        return None
    return data


def _dicts(value: object) -> list[dict[str, object]]:
    """Return only the dict members of a list (defensive jsonproto parsing)."""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _as_text(value: str | Path | None) -> str | None:
    """Return text content for a value that may be inline text or a file path."""
    if value is None:
        return None
    if isinstance(value, Path):
        return value.read_text(encoding="utf-8") if value.is_file() else None
    candidate = Path(value)
    try:
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8")
    except OSError:
        pass
    return value
