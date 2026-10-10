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

"""ADR-062 D9 / storage-format-v2 A1.6: capture every declared
``bundle_variants:`` variant into **one** ``ProjectSnapshot`` package.

Three phases, in an order that is the whole point of the design:

1. :func:`plan_variant_capture` -- *before any extraction*: every input names
   a declared variant, every ``required`` variant has an input that exists
   and holds at least one supported ABI input. A required variant that is
   unreachable is a :class:`VariantCaptureError` here, not a silently
   incomplete package discovered after a long capture run (the
   ``AnalysisPlanner`` "reject before extraction" discipline). An optional
   variant that is unreachable is recorded as *skipped*, with its reason.
2. :func:`capture_variants` -- dumps every library of every planned variant
   into memory. A required variant whose capture fails raises; nothing has
   been written yet. An optional one is skipped, with its reason.
3. The write -- only once every variant is in hand, into a staging
   directory beside the destination, renamed into place last. So a failure
   at any point leaves no package at all, never a partial one.

Each variant becomes one ``VariantRef`` whose ``declared`` map is the
config's (``BundleVariantSpec.declared()``) and whose ``captured`` map is
what the snapshots themselves recorded (:func:`captured_coordinates`) --
two maps, never merged, so a disagreement between them survives to the
package. A skipped optional variant has **no** ``VariantRef`` at all.

Artifact ids are namespaced by variant (``<variant>.<library-id>``, opaque
when that is not ref-id safe): ``import_bundle_facts`` derives an artifact
id from the library name alone, so two variants of one package sharing a
library name would otherwise collide (the limitation A1.7 recorded). Every
reader recovers the library name from ``ArtifactRef.native_identity``, never
from the id.
"""

from __future__ import annotations

import dataclasses
import os
import shutil
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..errors import AbicheckError
from ..model import AbiSnapshot
from ..model.bundle_variants import (
    BUNDLE_VARIANTS_KEY,
    BundleVariantsConfig,
    BundleVariantSpec,
    parse_bundle_variants,
)

__all__ = [
    "BUNDLE_VARIANT_SPEC_SECTION_KIND",
    "BUNDLE_VARIANT_SPEC_SCHEMA_VERSION",
    "PlannedVariant",
    "SkippedVariant",
    "VariantCaptureError",
    "VariantCaptureInput",
    "VariantCapturePlan",
    "VariantCaptureResult",
    "capture_variants",
    "captured_coordinates",
    "load_bundle_variants_config",
    "plan_variant_capture",
]

#: The variant-level section recording what ``declared`` alone cannot: the
#: ``required`` flag and which ``declared`` keys are feature toggles.
BUNDLE_VARIANT_SPEC_SECTION_KIND = "bundle_variant_spec"
BUNDLE_VARIANT_SPEC_SCHEMA_VERSION = 1


class VariantCaptureError(AbicheckError, ValueError):
    """A required variant is unreachable or failed, or the invocation names
    something the config does not declare. Raised before anything is
    written."""


@dataclass(frozen=True)
class VariantCaptureInput:
    """One variant's inputs: a file or directory of binaries/snapshots (the
    same operand shape ``compare``'s release fan-out takes), plus optional
    public headers and include directories for its dumps."""

    name: str
    path: Path
    headers: tuple[Path, ...] = ()
    includes: tuple[Path, ...] = ()


@dataclass(frozen=True)
class PlannedVariant:
    spec: BundleVariantSpec
    input: VariantCaptureInput
    #: canonical release-matching key -> input path (``build_match_map``).
    libraries: Mapping[str, Path]


@dataclass(frozen=True)
class SkippedVariant:
    """An optional variant the package will carry no ``VariantRef`` for."""

    name: str
    reason: str


@dataclass(frozen=True)
class VariantCapturePlan:
    planned: tuple[PlannedVariant, ...]
    skipped: tuple[SkippedVariant, ...] = ()


@dataclass(frozen=True)
class VariantCaptureResult:
    """What was written: one entry per captured variant, plus the optional
    variants skipped at plan time or during capture."""

    output: Path
    captured: Mapping[str, Mapping[str, str]]
    declared: Mapping[str, Mapping[str, str]]
    libraries: Mapping[str, tuple[str, ...]]
    skipped: tuple[SkippedVariant, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "output": str(self.output),
            "variants": {
                name: {
                    "declared": dict(self.declared[name]),
                    "captured": dict(self.captured[name]),
                    "libraries": list(self.libraries[name]),
                }
                for name in sorted(self.captured)
            },
            "skipped": [{"variant": s.name, "reason": s.reason} for s in self.skipped],
        }


def load_bundle_variants_config(path: Path) -> BundleVariantsConfig:
    """The ``bundle_variants:`` block of the ``.abicheck.yml`` at *path*.

    The whole file goes through ``BuildConfig``'s own strict ingestion first,
    so an unrelated malformed key is reported the same way ``compare``
    reports it. Raises :class:`VariantCaptureError` when the file has no
    ``bundle_variants:`` block.
    """
    import yaml

    from .extraction import load_build_config

    try:
        load_build_config(path)  # the same strict gate every ingestion applies
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise VariantCaptureError(f"{path}: {exc}") from exc
    if not isinstance(raw, dict) or BUNDLE_VARIANTS_KEY not in raw:
        raise VariantCaptureError(f"{path} declares no '{BUNDLE_VARIANTS_KEY}:' block")
    try:
        return parse_bundle_variants(raw[BUNDLE_VARIANTS_KEY])
    except ValueError as exc:
        raise VariantCaptureError(f"{path}: {exc}") from exc


def _discover(path: Path) -> dict[str, Path]:
    from .extraction import build_match_map
    from .release_inputs import collect_release_inputs

    files = collect_release_inputs(path)
    mapping, _warnings = build_match_map(files)
    return mapping


def plan_variant_capture(
    config: BundleVariantsConfig, inputs: Sequence[VariantCaptureInput]
) -> VariantCapturePlan:
    """Resolve *inputs* against *config* without extracting anything.

    Every problem with a *required* variant (and every input naming an
    undeclared variant, or naming one twice) is collected and raised as one
    :class:`VariantCaptureError`; an optional variant with no usable input
    becomes a :class:`SkippedVariant`.
    """
    errors: list[str] = []
    by_name: dict[str, VariantCaptureInput] = {}
    for item in inputs:
        if config.get(item.name) is None:
            errors.append(
                f"--variant {item.name}: not declared in bundle_variants "
                f"(declared: {', '.join(config.names)})"
            )
        elif item.name in by_name:
            errors.append(f"--variant {item.name}: given more than once")
        else:
            by_name[item.name] = item
    planned: list[PlannedVariant] = []
    skipped: list[SkippedVariant] = []
    for spec in config.variants:
        given = by_name.get(spec.name)
        reason: str | None = None
        libraries: dict[str, Path] = {}
        if given is None:
            reason = "no --variant input was given for it"
        elif not given.path.exists():
            reason = f"input path {given.path} does not exist"
        else:
            try:
                libraries = _discover(given.path)
            except (AbicheckError, ValueError, OSError) as exc:
                reason = f"no capturable input under {given.path}: {exc}"
        if reason is None and given is not None:
            planned.append(PlannedVariant(spec, given, libraries))
        elif spec.required:
            errors.append(f"required variant {spec.name!r}: {reason}")
        else:
            skipped.append(SkippedVariant(spec.name, str(reason)))
    if errors:
        raise VariantCaptureError("; ".join(errors))
    if not planned:
        raise VariantCaptureError(
            "no variant can be captured: "
            + "; ".join(f"{s.name}: {s.reason}" for s in skipped)
        )
    return VariantCapturePlan(tuple(planned), tuple(skipped))


def _joined(values: Sequence[str]) -> str | None:
    """One value when every library agrees; the sorted distinct values
    comma-joined when they do not (a mixed variant is itself the fact)."""
    distinct = sorted({v for v in values if v})
    return ",".join(distinct) if distinct else None


def captured_coordinates(snapshots: Sequence[AbiSnapshot]) -> dict[str, str]:
    """The ``VariantRef.captured`` map: only what the snapshots recorded.

    Read from real snapshot fields, never inferred: the DWARF
    ``DW_AT_producer`` toolchain (``dwarf_advanced.toolchain``), the DWARF
    target architecture, the container format and ELF machine/class, and
    the header parse's resolved standard/frontend. A key with no evidence in
    any snapshot is absent, not empty.
    """
    fields: dict[str, list[str]] = {}

    def _add(key: str, value: object) -> None:
        if value not in (None, ""):
            fields.setdefault(key, []).append(str(value))

    for snap in snapshots:
        advanced = snap.declarations.debug_advanced
        if advanced is not None:
            _add("compiler_family", advanced.toolchain.compiler.lower())
            _add("compiler_version", advanced.toolchain.version)
            _add("compiler_producer", advanced.toolchain.producer_string)
            _add("target_arch", advanced.target_arch)
        _add("binary_format", snap.platform)
        if snap.elf is not None:
            _add("elf_machine", snap.elf.machine)
            _add("elf_class", snap.elf.elf_class if snap.elf.machine else None)
        _add("language_standard", snap.ast_resolved_standard)
        _add("header_frontend", snap.ast_producer)
    joined = {key: _joined(values) for key, values in sorted(fields.items())}
    return {key: value for key, value in joined.items() if value is not None}


def _default_dump(path: Path, item: VariantCaptureInput) -> AbiSnapshot:
    from ..workflows.dump.pipeline import run_dump_request
    from .contracts import DumpRequest, InputSpec

    return run_dump_request(
        DumpRequest(
            input=InputSpec(path=path, headers=item.headers, includes=item.includes)
        )
    )


def _is_snapshot_file(path: Path) -> bool:
    return ".json" in path.suffixes


def _variant_artifact_ids(
    variant_id: str, artifact_ids: Sequence[str]
) -> dict[str, str]:
    from ..storage.ref_ids import resolve_ref_ids

    namespaced = {aid: f"{variant_id}.{aid}" for aid in artifact_ids}
    resolved = resolve_ref_ids(list(namespaced.values()), opaque_prefix="art")
    return {aid: resolved[name] for aid, name in namespaced.items()}


def _write_package(
    staging: Path,
    captures: Sequence[
        tuple[BundleVariantSpec, dict[str, AbiSnapshot], dict[str, Path]]
    ],
) -> tuple[dict[str, dict[str, str]], dict[str, tuple[str, ...]]]:
    from ..project_snapshot_store import DirectoryObjectStore, write_project_manifest
    from ..storage.bundle_facts_package import write_bundle_facts_package
    from ..storage.package import ArtifactRef, ObjectRef, PackageManifest, VariantRef
    from .bundle_facts_capture import capture_bundle_facts

    store = DirectoryObjectStore(staging)
    variant_refs: list[VariantRef] = []
    artifact_refs: list[ArtifactRef] = []
    section_versions: dict[str, int] = {
        BUNDLE_VARIANT_SPEC_SECTION_KIND: BUNDLE_VARIANT_SPEC_SCHEMA_VERSION
    }
    versions = None
    captured: dict[str, dict[str, str]] = {}
    libraries: dict[str, tuple[str, ...]] = {}
    for spec, snapshots, paths in captures:
        facts = capture_bundle_facts(
            snapshots,
            variant_fingerprint=spec.name,
            library_paths={
                name: p for name, p in paths.items() if not _is_snapshot_file(p)
            },
        )
        manifest = write_bundle_facts_package(facts, store=store, variant_id=spec.name)
        (variant,) = manifest.variant_refs
        renamed = _variant_artifact_ids(spec.name, variant.artifact_ids)
        artifact_refs.extend(
            dataclasses.replace(a, artifact_id=renamed[a.artifact_id])
            for a in manifest.artifact_refs
        )
        spec_ref = ObjectRef(
            kind=BUNDLE_VARIANT_SPEC_SECTION_KIND,
            digest=store.put(
                {
                    "kind": BUNDLE_VARIANT_SPEC_SECTION_KIND,
                    "schema_version": BUNDLE_VARIANT_SPEC_SCHEMA_VERSION,
                    "required": spec.required,
                    "feature_toggle_keys": sorted(spec.feature_toggles),
                }
            ),
        )
        captured[spec.name] = captured_coordinates(list(snapshots.values()))
        libraries[spec.name] = tuple(sorted(snapshots))
        variant_refs.append(
            VariantRef(
                variant_id=spec.name,
                declared=spec.declared(),
                captured=captured[spec.name],
                artifact_ids=tuple(renamed.values()),
                sections={
                    **variant.sections,
                    BUNDLE_VARIANT_SPEC_SECTION_KIND: spec_ref,
                },
            )
        )
        section_versions.update(manifest.versions.section_schema_versions)
        if versions is None:
            versions = manifest.versions
        elif manifest.versions.source_schema_version != versions.source_schema_version:
            raise VariantCaptureError(
                f"variant {spec.name!r} was captured under snapshot schema "
                f"{manifest.versions.source_schema_version}, another variant under "
                f"{versions.source_schema_version}; one package states one"
            )
    assert versions is not None  # plan_variant_capture guarantees >= 1 variant
    write_project_manifest(
        staging,
        PackageManifest(
            versions=dataclasses.replace(
                versions, section_schema_versions=section_versions
            ),
            variant_refs=tuple(variant_refs),
            artifact_refs=tuple(artifact_refs),
        ),
    )
    return captured, libraries


def _check_output(output: Path) -> None:
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise VariantCaptureError(
            f"output {output} already exists and is not an empty directory; "
            "refusing to overwrite a package"
        )


def capture_variants(
    plan: VariantCapturePlan,
    output: Path,
    *,
    dump: Callable[[Path, VariantCaptureInput], AbiSnapshot] | None = None,
    on_skip: Callable[[SkippedVariant], None] | None = None,
) -> VariantCaptureResult:
    """Capture every planned variant, then write one package to *output*.

    *dump* (``path, input -> AbiSnapshot``) defaults to the shared
    ``run_dump_request`` pipeline every ``dump`` goes through. Nothing is
    written until every variant is captured; a required variant's failure
    raises :class:`VariantCaptureError` with *output* untouched.
    """
    _check_output(output)
    dumper = dump if dump is not None else _default_dump
    skipped = list(plan.skipped)
    captures: list[
        tuple[BundleVariantSpec, dict[str, AbiSnapshot], dict[str, Path]]
    ] = []
    for planned in plan.planned:
        snapshots: dict[str, AbiSnapshot] = {}
        failure: str | None = None
        for key, path in sorted(planned.libraries.items()):
            try:
                snapshots[key] = dumper(path, planned.input)
            except Exception as exc:  # any extractor failure fails the variant
                failure = f"capturing {path.name} failed: {exc}"
                break
        if failure is None:
            captures.append((planned.spec, snapshots, dict(planned.libraries)))
        elif planned.spec.required:
            raise VariantCaptureError(
                f"required variant {planned.spec.name!r}: {failure}; no package "
                "was written"
            )
        else:
            skipped.append(SkippedVariant(planned.spec.name, failure))
            if on_skip is not None:
                on_skip(skipped[-1])
    if not captures:
        raise VariantCaptureError(
            "every variant failed to capture; no package was written: "
            + "; ".join(f"{s.name}: {s.reason}" for s in skipped)
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    try:
        captured, libraries = _write_package(staging, captures)
        _check_output(output)
        if output.exists():
            output.rmdir()
        os.replace(staging, output)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return VariantCaptureResult(
        output=output,
        captured=captured,
        declared={spec.name: spec.declared() for spec, _s, _p in captures},
        libraries=libraries,
        skipped=tuple(skipped),
    )
