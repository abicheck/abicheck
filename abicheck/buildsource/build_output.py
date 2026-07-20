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

"""``build-output.json`` schema + validator (ADR-047 §2/§11.1, G30 P1.1).

A standardized, producer-agnostic artifact directory a project's *existing*
build (or an `install` step) populates once — "build once, scan many" (S3) —
without abicheck ever owning the build:

.. code-block:: text

    abicheck-build/
      build-output.json          # this module's schema
      artifacts/                 # binaries as published by the real build
      headers/                   # public header roots, as-installed layout
      generated-headers/         # codegen/configure output, kept separate
      evidence/
        compile_commands.json    # if produced
        abicheck_inputs/         # source-facts pack (inputs_pack.py protocol)
      provenance/                # toolchain version dumps, build logs digest

This module only defines the contract and validates a hand-authored (or
build-emitted) ``build-output.json`` -- there is no producer tooling here yet
(that's G30 P1.2's ``resolve-baseline``/``check-target``, which *consume*
this artifact) and no ``abicheck build-output emit`` helper either. Pure:
reads files, never runs a tool.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .inputs_pack import is_inputs_pack, load_inputs_manifest, read_source_facts

#: Schema discriminator stamped into every ``build-output.json`` (ADR-047 §2).
BUILD_OUTPUT_SCHEMA = "abicheck.build-output/v1"

#: The manifest filename inside an ``abicheck-build/`` directory.
BUILD_OUTPUT_MANIFEST_NAME = "build-output.json"

#: The only ``evidence.projection`` value P1's validator accepts. ``"inferred"``
#: is schema-reserved for P2's TU->link-unit->DSO attribution (ADR-047 §2) --
#: accepting it here today would let an unattributed, build-wide pack validate
#: as legitimate per-target evidence before the safety mechanism that would
#: justify trusting it exists.
DECLARED_PROJECTION = "declared"

#: Every enum value the schema recognizes for ``evidence.projection``, so the
#: validator can tell "not declared" (fails, but is a known future value) from
#: "not a real projection value at all" (also fails, but is a different kind
#: of mistake) -- both produce a validation error today; kept as a named
#: constant so P2 has one place to extend when ``"inferred"`` is implemented.
KNOWN_PROJECTIONS = frozenset({"declared", "inferred"})


def _opt_str(value: Any, default: str = "") -> str:
    return str(value) if isinstance(value, str) and value else default


def _str_list(d: dict[str, Any], key: str) -> list[str]:
    raw = d.get(key)
    return [str(x) for x in raw if x] if isinstance(raw, list) else []


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

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "os": self.os,
            "arch": self.arch,
            "compiler": dict(self.compiler),
            "cxx_abi": self.cxx_abi,
            "stdlib": self.stdlib,
            "config": self.config,
        }

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
        )


@dataclass
class BuildOutputEvidence:
    """A target's L3/L4/L5 evidence pointer (ADR-047 §2).

    ``projection`` is the field the P1.1 validator gates on: ``"declared"``
    means the build itself asserted this evidence pack belongs to exactly
    this target (e.g. per-target compile-DB filtering, or a wrapper invoked
    once per link step); ``"inferred"`` would mean abicheck derived the
    association from a build-wide pack, which needs P2's attribution work
    and is rejected by this validator until then.
    """

    kind: str = ""  # e.g. "source-facts"
    path: str = ""  # relative to the build-output root
    projection: str = ""  # "declared" | "inferred" (only "declared" validates)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "path": self.path, "projection": self.projection}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> BuildOutputEvidence:
        return cls(
            kind=_opt_str(d.get("kind")),
            path=_opt_str(d.get("path")),
            projection=_opt_str(d.get("projection")),
        )


@dataclass
class BuildOutputTarget:
    """One library/binary this build produced (ADR-047 §2)."""

    id: str = ""
    binary: str = ""
    public_header_roots: list[str] = field(default_factory=list)
    #: Public header roots populated by codegen/configure, kept separate from
    #: public_header_roots (ADR-047 §2's S10 guard) so an empty codegen step
    #: can't silently claim an as-installed header root that was never
    #: actually generated.
    generated_header_roots: list[str] = field(default_factory=list)
    compile_context: dict[str, Any] = field(default_factory=dict)
    bundle: str = ""
    evidence: BuildOutputEvidence | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "binary": self.binary,
            "public_header_roots": list(self.public_header_roots),
            "generated_header_roots": list(self.generated_header_roots),
            "compile_context": dict(self.compile_context),
            "bundle": self.bundle,
        }
        if self.evidence is not None:
            d["evidence"] = self.evidence.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> BuildOutputTarget:
        evidence_raw = d.get("evidence")
        evidence = (
            BuildOutputEvidence.from_dict(evidence_raw)
            if isinstance(evidence_raw, dict)
            else None
        )
        compile_context_raw = d.get("compile_context")
        compile_context = (
            dict(compile_context_raw) if isinstance(compile_context_raw, dict) else {}
        )
        return cls(
            id=_opt_str(d.get("id")),
            binary=_opt_str(d.get("binary")),
            public_header_roots=_str_list(d, "public_header_roots"),
            generated_header_roots=_str_list(d, "generated_header_roots"),
            compile_context=compile_context,
            bundle=_opt_str(d.get("bundle")),
            evidence=evidence,
        )


@dataclass
class BuildOutputBundle:
    """A named group of targets built/released together (ADR-047 §2)."""

    id: str = ""
    targets: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "targets": list(self.targets)}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> BuildOutputBundle:
        return cls(id=_opt_str(d.get("id")), targets=_str_list(d, "targets"))


@dataclass
class BuildOutputEvidenceProducer:
    """Which tool produced the build's L3/L4/L5 evidence (ADR-047 §2)."""

    kind: str = ""  # "wrapper" | "clang-plugin" | "replay" | ...
    tool: str = ""
    version: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "tool": self.tool, "version": self.version}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> BuildOutputEvidenceProducer:
        return cls(
            kind=_opt_str(d.get("kind")),
            tool=_opt_str(d.get("tool")),
            version=_opt_str(d.get("version")),
        )


@dataclass
class BuildOutput:
    """Parsed ``build-output.json`` (ADR-047 §2).

    Every field is optional/defaulted, matching the ``buildsource``-wide
    convention (see ``abicheck/buildsource/CLAUDE.md`` "Conventions") so a
    hand-written or forward/backward-mismatched manifest never aborts a load
    — problems are reported by :func:`validate_build_output`, not raised.
    """

    schema: str = BUILD_OUTPUT_SCHEMA
    project: str = ""
    head_sha: str = ""
    source_tree_digest: str = ""
    profile: BuildOutputProfile = field(default_factory=BuildOutputProfile)
    targets: list[BuildOutputTarget] = field(default_factory=list)
    bundles: list[BuildOutputBundle] = field(default_factory=list)
    evidence_producer: BuildOutputEvidenceProducer = field(
        default_factory=BuildOutputEvidenceProducer
    )
    digests: dict[str, str] = field(default_factory=dict)
    diagnostics: dict[str, list[str]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "project": self.project,
            "head_sha": self.head_sha,
            "source_tree_digest": self.source_tree_digest,
            "profile": self.profile.to_dict(),
            "targets": [t.to_dict() for t in self.targets],
            "bundles": [b.to_dict() for b in self.bundles],
            "evidence_producer": self.evidence_producer.to_dict(),
            "digests": dict(self.digests),
            "diagnostics": {k: list(v) for k, v in self.diagnostics.items()},
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> BuildOutput:
        profile_raw = d.get("profile")
        profile = (
            BuildOutputProfile.from_dict(profile_raw)
            if isinstance(profile_raw, dict)
            else BuildOutputProfile()
        )
        targets_raw = d.get("targets")
        targets = (
            [BuildOutputTarget.from_dict(t) for t in targets_raw if isinstance(t, dict)]
            if isinstance(targets_raw, list)
            else []
        )
        bundles_raw = d.get("bundles")
        bundles = (
            [BuildOutputBundle.from_dict(b) for b in bundles_raw if isinstance(b, dict)]
            if isinstance(bundles_raw, list)
            else []
        )
        producer_raw = d.get("evidence_producer")
        producer = (
            BuildOutputEvidenceProducer.from_dict(producer_raw)
            if isinstance(producer_raw, dict)
            else BuildOutputEvidenceProducer()
        )
        digests_raw = d.get("digests")
        digests = (
            {str(k): str(v) for k, v in digests_raw.items()}
            if isinstance(digests_raw, dict)
            else {}
        )
        diagnostics_raw = d.get("diagnostics")
        diagnostics: dict[str, list[str]] = {}
        if isinstance(diagnostics_raw, dict):
            for key, value in diagnostics_raw.items():
                if isinstance(value, list):
                    diagnostics[str(key)] = [str(x) for x in value]
        return cls(
            schema=_opt_str(d.get("schema"), BUILD_OUTPUT_SCHEMA),
            project=_opt_str(d.get("project")),
            head_sha=_opt_str(d.get("head_sha")),
            source_tree_digest=_opt_str(d.get("source_tree_digest")),
            profile=profile,
            targets=targets,
            bundles=bundles,
            evidence_producer=producer,
            digests=digests,
            diagnostics=diagnostics,
        )


def is_build_output_dir(path: Path | str) -> bool:
    """Whether *path* is an ``abicheck-build/``-shaped directory.

    A directory whose ``build-output.json`` declares
    ``schema: abicheck.build-output/v1`` — the explicit discriminator mirrors
    :func:`~.inputs_pack.is_inputs_pack`'s pattern so a directory input can be
    routed to the right loader without guessing from its contents alone.
    """
    p = Path(path)
    manifest = p / BUILD_OUTPUT_MANIFEST_NAME
    if not (p.is_dir() and manifest.is_file()):
        return False
    try:
        with manifest.open(encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and data.get("schema") == BUILD_OUTPUT_SCHEMA


def load_build_output(root: Path | str) -> BuildOutput:
    """Load and parse ``<root>/build-output.json``.

    Raises ``FileNotFoundError`` if absent, ``ValueError`` if *root* carries a
    ``build-output.json`` that does not declare
    ``schema: abicheck.build-output/v1`` — matching
    :func:`~.inputs_pack.load_inputs_manifest`'s same two-exception contract.
    """
    root = Path(root)
    manifest_path = root / BUILD_OUTPUT_MANIFEST_NAME
    if not manifest_path.is_file():
        raise FileNotFoundError(
            f"No build-output manifest at {manifest_path}. Expected an "
            f"abicheck-build/ directory with a build-output.json declaring "
            f"schema: {BUILD_OUTPUT_SCHEMA}."
        )
    with manifest_path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"{manifest_path} must contain a JSON object.")
    if data.get("schema") != BUILD_OUTPUT_SCHEMA:
        raise ValueError(
            f"{manifest_path} does not declare schema: {BUILD_OUTPUT_SCHEMA} — "
            "not a recognized build-output.json."
        )
    return BuildOutput.from_dict(data)


@dataclass
class BuildOutputValidationReport:
    """Result of validating one ``build-output.json`` (ADR-047 §11.1)."""

    root: str = ""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "ok": self.ok,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _resolve_under_root(root: Path, rel: str) -> Path | None:
    """Resolve *rel* under *root*, refusing an absolute path or an escape.

    Mirrors :func:`~.inputs_pack._safe_pack_path`'s same guard — every path a
    ``build-output.json`` declares is relative-to-root, and this is the one
    place that needs to hold for a third-party/hand-authored manifest.
    """
    if Path(rel).is_absolute():
        return None
    candidate = (root / rel).resolve()
    root_resolved = root.resolve()
    if candidate != root_resolved and not candidate.is_relative_to(root_resolved):
        return None
    return root / rel


def _header_root_issues(
    root: Path, target_id: str, roots: list[str], *, label: str
) -> list[str]:
    """A declared header root must exist and contain at least one file.

    Applies to both ``public_header_roots`` and ``generated_header_roots`` —
    the latter is ADR-047 §2's S10 guard: an empty ``generated-headers/``
    root declared non-empty is a hard failure, never a warning, so a codegen
    step that silently didn't run can't pass validation.
    """
    issues: list[str] = []
    for rel in roots:
        resolved = _resolve_under_root(root, rel)
        if resolved is None:
            issues.append(
                f"target {target_id!r}: {label} root {rel!r} is absolute or "
                "escapes the build-output directory."
            )
            continue
        if not resolved.is_dir():
            issues.append(
                f"target {target_id!r}: {label} root {rel!r} does not exist "
                f"(expected a directory at {resolved})."
            )
            continue
        if not any(resolved.rglob("*")):
            issues.append(
                f"target {target_id!r}: {label} root {rel!r} exists but is "
                "empty — an empty declared header root is a hard failure, "
                "not a warning (ADR-047 §2's S10 guard: this usually means a "
                "codegen/configure step that was supposed to populate it "
                "never actually ran)."
            )
    return issues


def _binary_issues(
    root: Path, target: BuildOutputTarget, digests: dict[str, str]
) -> list[str]:
    if not target.binary:
        return [f"target {target.id!r}: no binary declared."]
    resolved = _resolve_under_root(root, target.binary)
    if resolved is None:
        return [
            f"target {target.id!r}: binary {target.binary!r} is absolute or "
            "escapes the build-output directory."
        ]
    if not resolved.is_file():
        return [
            f"target {target.id!r}: binary {target.binary!r} does not exist "
            f"(expected a file at {resolved})."
        ]
    expected_digest = digests.get(target.binary)
    if expected_digest is None:
        return [
            f"target {target.id!r}: no digests[{target.binary!r}] entry — "
            "every targets[].binary must have a matching digest so a "
            "consumer can detect a stale/tampered artifact (ADR-047 §11.1)."
        ]
    actual_digest = _file_sha256(resolved)
    expected_normalized = expected_digest.removeprefix("sha256:")
    if actual_digest != expected_normalized:
        return [
            f"target {target.id!r}: binary {target.binary!r} digest mismatch "
            f"(declared {expected_digest!r}, actual sha256:{actual_digest})."
        ]
    return []


def _evidence_projection_issues(target: BuildOutputTarget) -> list[str]:
    evidence = target.evidence
    if evidence is None:
        return []
    if evidence.projection != DECLARED_PROJECTION:
        if evidence.projection == "inferred":
            return [
                f"target {target.id!r}: evidence.projection is 'inferred', "
                "which P1's build-output.json validator does not accept — "
                "the TU->link-unit->DSO attribution needed to trust an "
                "inferred association is G30 P2, not built yet (ADR-047 §2/"
                "§9). A build-wide pack may only feed a build-wide source "
                "audit or a per-target header-depth check until then, never "
                "a per-target effective_depth: source claim."
            ]
        return [
            f"target {target.id!r}: evidence.projection is "
            f"{evidence.projection!r}; must be {DECLARED_PROJECTION!r}."
        ]
    return []


def _declared_evidence_sharing_issues(
    root: Path, targets: list[BuildOutputTarget]
) -> list[str]:
    """The shared-pack-across-targets + manifest/target-mismatch checks.

    ADR-047 §11.1's corrected scope: fail a ``"declared"`` claim only when
    (a) the evidence pack is referenced by more than one target (shared pack
    — whether or not its TUs carry ``target_id``), or (b) the pack's
    ``manifest.library`` (or a tagged TU's ``target_id``) disagrees with the
    specific target referencing it. A single-target, ``manifest.library``-
    matched pack with untagged TUs must still pass — this is deliberately
    narrower than rejecting every untagged-TU pack.
    """
    issues: list[str] = []
    declared_targets = [
        t
        for t in targets
        if t.evidence is not None and t.evidence.projection == DECLARED_PROJECTION
    ]

    # (a) shared-pack detection: group by the evidence path's resolved,
    # absolute location so two differently-spelled-but-identical relative
    # paths still collide.
    path_to_targets: dict[Path, list[str]] = {}
    for t in declared_targets:
        assert t.evidence is not None
        resolved = _resolve_under_root(root, t.evidence.path)
        if resolved is None:
            issues.append(
                f"target {t.id!r}: evidence.path {t.evidence.path!r} is "
                "absolute or escapes the build-output directory."
            )
            continue
        path_to_targets.setdefault(resolved.resolve(), []).append(t.id)
    for shared_path, target_ids in path_to_targets.items():
        if len(target_ids) > 1:
            issues.append(
                "evidence pack at "
                f"{shared_path} is referenced by more than one target "
                f"({', '.join(sorted(target_ids))}) with projection: "
                f"{DECLARED_PROJECTION!r} — a pack shared across targets is "
                "exactly the unprojected, build-wide evidence ADR-047 §9's "
                "safe model says must never satisfy a per-target 'declared' "
                "claim; each target needs its own pack, or the shared build-"
                "wide claim must be dropped to projection-less/no per-target "
                "source-depth claim."
            )

    # (b) manifest.library / TU target_id mismatch against the specific
    # target that references the pack -- only for packs not already flagged
    # as shared above (a shared pack's per-target identity is meaningless).
    for t in declared_targets:
        assert t.evidence is not None
        resolved = _resolve_under_root(root, t.evidence.path)
        if resolved is None or len(path_to_targets.get(resolved.resolve(), [])) > 1:
            continue
        if not is_inputs_pack(resolved):
            issues.append(
                f"target {t.id!r}: evidence.path {t.evidence.path!r} is not "
                "a readable abicheck_inputs pack (no manifest.json declaring "
                "kind: abicheck_inputs)."
            )
            continue
        manifest = load_inputs_manifest(resolved)
        if manifest.library and manifest.library != t.id:
            issues.append(
                f"target {t.id!r}: evidence pack's manifest.library is "
                f"{manifest.library!r}, not {t.id!r} — this pack's own "
                "declared identity does not match the target referencing it."
            )
            continue
        diagnostics: list[str] = []
        tus = read_source_facts(resolved, manifest, diagnostics=diagnostics)
        expected_tu_target = f"target://{t.id}"
        mismatched = sorted(
            {
                tu.target_id
                for tu in tus
                if tu.target_id and tu.target_id != expected_tu_target
            }
        )
        if mismatched:
            issues.append(
                f"target {t.id!r}: evidence pack's TU records name a "
                f"different target_id ({', '.join(mismatched)}) than "
                f"expected ({expected_tu_target!r}) — this pack's evidence "
                "does not agree on which target it describes."
            )
    return issues


def validate_build_output(root: Path | str) -> BuildOutputValidationReport:
    """Validate one ``build-output.json`` + its referenced artifacts (ADR-047 §11.1).

    Never raises for a structurally-readable ``build-output.json`` — problems
    are reported, not thrown. Raises ``FileNotFoundError``/``ValueError`` only
    when *root* is not a readable ``abicheck-build/`` directory at all,
    matching :func:`load_build_output`.
    """
    root = Path(root)
    report = BuildOutputValidationReport(root=str(root))
    build_output = load_build_output(root)

    if not build_output.targets:
        report.warnings.append("build-output.json declares no targets[].")

    for target in build_output.targets:
        if not target.id:
            report.errors.append("a targets[] entry has no id.")
            continue
        report.errors.extend(
            _header_root_issues(
                root, target.id, target.public_header_roots, label="public header"
            )
        )
        report.errors.extend(
            _header_root_issues(
                root,
                target.id,
                target.generated_header_roots,
                label="generated header",
            )
        )
        report.errors.extend(_binary_issues(root, target, build_output.digests))
        report.errors.extend(_evidence_projection_issues(target))

    report.errors.extend(_declared_evidence_sharing_issues(root, build_output.targets))

    return report
