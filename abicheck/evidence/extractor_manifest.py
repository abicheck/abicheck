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

"""External CLI evidence extractors, driven by a manifest (ADR-032 D3).

A third-party build system can be integrated *without importing untrusted Python
into the abicheck process*: the operator registers a YAML manifest that declares
the extractor's name, capabilities, allowed actions, the commands to run for each
lifecycle phase, and the normalized outputs it produces. abicheck then talks to
it over a subprocess boundary with declared inputs and outputs.

Security model (D3 + D5):

* **Trusted-by-operator, never auto-discovered.** :func:`load_extractor_manifest`
  loads exactly the path it is given. Nothing here scans ``PATH``, the working
  tree, or any plugin directory — an external extractor runs only when the
  operator registers it explicitly (``--extractor-manifest <path>``).
* **Declared actions are a ceiling, not a grant.** The manifest's
  ``allowed_actions`` are intersected at run time with the actions the operator
  enabled (:func:`~abicheck.evidence.extractor.resolve_allowed_actions`); a
  manifest can never escalate beyond the run-permitted set. Before any phase
  runs, every action the manifest declares it needs must be permitted, or
  collection fails with :class:`~abicheck.evidence.extractor.ActionNotPermittedError`.
* **No shell, sanitized environment.** Commands run as an argv list with
  ``shell=False`` and a minimal environment, so a manifest cannot smuggle a
  shell pipeline or leak the operator's full environment to the tool.

The pure halves — manifest parsing and command-template rendering — are
unit-testable without any external binary; only :meth:`ExternalCliExtractor._run`
shells out.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import subprocess  # noqa: S404 - argv-only, shell=False; the action model gates invocation
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .extractor import (
    CollectionAction,
    CollectionContext,
    CollectionMode,
    CollectionResult,
    DiscoveryResult,
    ExtractorCapabilities,
    ExtractorError,
    NormalizationResult,
    RawArtifact,
    ValidationResult,
    parse_actions,
)
from .model import ExtractorRecord
from .redaction import DEFAULT_REDACTION, RedactionPolicy

#: Default per-command timeout (seconds). An external extractor that runs longer
#: is killed and recorded as failed rather than hanging the whole collection.
DEFAULT_COMMAND_TIMEOUT = 600

#: Environment variables passed through to an external extractor. Deliberately
#: minimal (D7): the tool needs to find its own binary and locale, but the
#: operator's full environment — which may hold tokens — is never forwarded.
_ENV_PASSTHROUGH = ("PATH", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT")

#: The lifecycle phases a manifest may define commands for, in run order.
_PHASES = ("discover", "collect", "normalize", "validate")

#: Placeholders a command template may reference. Anything else is rejected so a
#: typo (``{normalised_dir}``) fails loudly instead of being passed through raw.
_KNOWN_PLACEHOLDERS = frozenset({
    "raw_dir", "normalized_dir", "build_dir", "source_root",
    "compile_db", "cache_dir", "binary",
})

_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


class ManifestError(ExtractorError):
    """A manifest is malformed, missing required fields, or internally inconsistent."""


@dataclass
class ManifestOutput:
    """One normalized artifact the extractor promises to produce (D3 ``outputs``)."""

    kind: str        # e.g. "build_evidence"
    path: str        # relative to the pack root, e.g. "build/build_evidence.json"

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "path": self.path}


@dataclass
class ExtractorManifest:
    """A registered external CLI extractor (ADR-032 D3).

    ``allowed_actions`` is the *ceiling* the operator vouches for by registering
    the manifest; it is intersected with the run-permitted actions before any
    command runs. ``capabilities`` drives coverage/CI policy (D4). ``commands``
    maps a lifecycle phase → an argv template (a list of tokens, each of which
    may contain ``{placeholder}`` tokens).
    """

    name: str
    version: str = ""
    version_command: list[str] = field(default_factory=list)
    capabilities: ExtractorCapabilities = field(default_factory=ExtractorCapabilities)
    input_requirements: list[str] = field(default_factory=list)
    allowed_actions: set[CollectionAction] = field(default_factory=set)
    commands: dict[str, list[str]] = field(default_factory=dict)
    outputs: list[ManifestOutput] = field(default_factory=list)
    schema_version: int = 1

    def required_actions(self) -> set[CollectionAction]:
        """The actions abicheck must have permission for before invoking this.

        ``inspect`` is implied (the tool always at least reads files); anything
        else must be both declared here and permitted for the run.
        """
        return set(self.allowed_actions) | {CollectionAction.INSPECT}


def load_extractor_manifest(path: Path | str) -> ExtractorManifest:
    """Parse a YAML extractor manifest from *path* (trusted-by-operator; D3).

    Loads exactly the given file — never scans a directory or ``PATH``. Raises
    :class:`ManifestError` on a missing/invalid file, a missing required field,
    an unknown action, or a capability/action inconsistency (e.g. a manifest
    that declares ``requires_build_execution`` but omits ``run_build`` from
    ``allowed_actions``).
    """
    p = Path(path)
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ManifestError(f"cannot read extractor manifest {p}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ManifestError(f"invalid YAML in extractor manifest {p}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ManifestError(f"extractor manifest {p} must be a YAML mapping")

    name = str(raw.get("name", "")).strip()
    if not name:
        raise ManifestError(f"extractor manifest {p} is missing a 'name'")

    commands_raw = raw.get("commands") or {}
    if not isinstance(commands_raw, dict):
        raise ManifestError(f"extractor manifest {p}: 'commands' must be a mapping")
    commands: dict[str, list[str]] = {}
    for phase, template in commands_raw.items():
        if phase not in _PHASES:
            raise ManifestError(
                f"extractor manifest {p}: unknown command phase {phase!r}; "
                f"expected one of: {', '.join(_PHASES)}"
            )
        if not isinstance(template, list) or not all(isinstance(t, str) for t in template):
            raise ManifestError(
                f"extractor manifest {p}: command '{phase}' must be a list of string tokens "
                "(an argv list — never a single shell string)"
            )
        # Reject unknown placeholders up front so a render-time KeyError can't surprise us.
        for token in template:
            for ph in _PLACEHOLDER_RE.findall(token):
                if ph not in _KNOWN_PLACEHOLDERS:
                    raise ManifestError(
                        f"extractor manifest {p}: command '{phase}' references unknown "
                        f"placeholder {{{ph}}}; known: {', '.join(sorted(_KNOWN_PLACEHOLDERS))}"
                    )
        commands[phase] = list(template)
    if "collect" not in commands and "normalize" not in commands:
        raise ManifestError(
            f"extractor manifest {p}: must define at least a 'collect' or 'normalize' command"
        )

    try:
        actions = parse_actions(raw.get("allowed_actions") or [])
    except ValueError as exc:
        raise ManifestError(f"extractor manifest {p}: {exc}") from exc

    capabilities = ExtractorCapabilities.from_dict(raw.get("capabilities"))
    implied = capabilities.implied_actions()
    missing = implied - actions - {CollectionAction.NETWORK}
    if missing:
        raise ManifestError(
            f"extractor manifest {p}: capabilities require action(s) "
            f"{', '.join(sorted(a.value for a in missing))} that are absent from 'allowed_actions'"
        )

    version_command = raw.get("version_command") or []
    if version_command and (
        not isinstance(version_command, list)
        or not all(isinstance(t, str) for t in version_command)
    ):
        raise ManifestError(f"extractor manifest {p}: 'version_command' must be a list of strings")

    outputs_raw = raw.get("outputs") or {}
    outputs = _parse_outputs(outputs_raw, p)

    input_requirements = [str(x) for x in (raw.get("input_requirements") or [])]

    return ExtractorManifest(
        name=name,
        version=str(raw.get("version", "")),
        version_command=list(version_command),
        capabilities=capabilities,
        input_requirements=input_requirements,
        allowed_actions=actions,
        commands=commands,
        outputs=outputs,
        schema_version=int(raw.get("schema_version", 1) or 1),
    )


def _parse_outputs(outputs_raw: Any, p: Path) -> list[ManifestOutput]:
    """Parse the ``outputs`` block, which may be a list or a ``{group: [..]}`` map."""
    rows: list[Any]
    if isinstance(outputs_raw, dict):
        # Manifest groups outputs by category (e.g. ``normalized:``); flatten.
        rows = []
        for group in outputs_raw.values():
            if isinstance(group, list):
                rows.extend(group)
    elif isinstance(outputs_raw, list):
        rows = list(outputs_raw)
    else:
        raise ManifestError(f"extractor manifest {p}: 'outputs' must be a list or mapping")
    out: list[ManifestOutput] = []
    for row in rows:
        if not isinstance(row, dict) or "kind" not in row or "path" not in row:
            raise ManifestError(
                f"extractor manifest {p}: each output needs a 'kind' and 'path'"
            )
        out.append(ManifestOutput(kind=str(row["kind"]), path=str(row["path"])))
    return out


def render_command(template: list[str], substitutions: dict[str, str]) -> list[str]:
    """Render a command template into an argv list (pure; D3).

    Each ``{placeholder}`` in a token is replaced from *substitutions*. A
    placeholder with no provided value renders to an empty string only if it is
    explicitly present in *substitutions*; an entirely missing key raises
    :class:`ManifestError` (the manifest asked for an input the run did not
    supply). Returns a fresh list — never mutates *template*.
    """
    out: list[str] = []
    for token in template:
        def _sub(match: re.Match[str]) -> str:
            key = match.group(1)
            if key not in substitutions:
                raise ManifestError(
                    f"command references {{{key}}} but no value was supplied for this run"
                )
            return substitutions[key]
        out.append(_PLACEHOLDER_RE.sub(_sub, token))
    return out


@dataclass
class ExternalCliExtractor:
    """Adapt a registered :class:`ExtractorManifest` to the extractor interface.

    Implements the ADR-032 D2 contract over the subprocess boundary. The action
    ceiling is enforced before each phase; outputs are validated (D8); and a full
    reproducibility ledger row (:class:`ExtractorRecord`, D10) is produced.
    """

    manifest: ExtractorManifest
    redaction: RedactionPolicy = field(default_factory=lambda: DEFAULT_REDACTION)
    timeout: int = DEFAULT_COMMAND_TIMEOUT

    @property
    def name(self) -> str:
        return self.manifest.name

    @property
    def version(self) -> str:
        return self.manifest.version

    @property
    def schema_version(self) -> int:
        return self.manifest.schema_version

    # -- helpers ------------------------------------------------------------

    def _substitutions(self, context: CollectionContext, pack_root: Path) -> dict[str, str]:
        """Build the placeholder map from the context + pack layout (D6)."""
        sub: dict[str, str] = {
            "raw_dir": str(pack_root / "raw" / self.manifest.name),
            "normalized_dir": str(pack_root / "normalized" / self.manifest.name),
        }
        if context.build_root is not None:
            sub["build_dir"] = str(context.build_root)
        if context.source_root is not None:
            sub["source_root"] = str(context.source_root)
        if context.compile_db is not None:
            sub["compile_db"] = str(context.compile_db)
        if context.cache_dir is not None:
            sub["cache_dir"] = str(context.cache_dir)
        if context.binary_paths:
            sub["binary"] = str(context.binary_paths[0])
        return sub

    def _enforce_actions(self, context: CollectionContext) -> None:
        """Refuse to invoke unless every declared action is permitted (D5)."""
        for action in self.manifest.required_actions():
            context.require(action, extractor=self.manifest.name)

    def _run(self, argv: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        """Run *argv* with no shell and a sanitized environment.

        Centralizes the single place this module touches the host. ``shell`` is
        always False (an argv list, never a string); the environment is reduced
        to :data:`_ENV_PASSTHROUGH` so secrets in the operator's environment are
        never forwarded to a third-party tool.
        """
        env = {k: os.environ[k] for k in _ENV_PASSTHROUGH if k in os.environ}
        return subprocess.run(  # noqa: S603 - argv list, shell=False, sanitized env
            argv,
            cwd=str(cwd) if cwd else None,
            env=env,
            capture_output=True,
            text=True,
            timeout=self.timeout,
            check=False,
        )

    # -- lifecycle ----------------------------------------------------------

    def discover(self, context: CollectionContext) -> DiscoveryResult:
        """Report capability + required actions without running the heavy phases.

        Pure with respect to the host unless the manifest defines a ``discover``
        command *and* the run permits the manifest's actions; otherwise it just
        reports the declared capabilities, so a coverage/CI planner can decide
        whether to run this extractor at all.
        """
        requested = self.manifest.required_actions()
        unpermitted = requested - set(context.allowed_actions)
        if unpermitted:
            return DiscoveryResult(
                can_run=False,
                capabilities=self.manifest.capabilities,
                requested_actions=requested,
                reason=(
                    "requires action(s) not permitted for this run: "
                    + ", ".join(sorted(a.value for a in unpermitted))
                ),
            )
        return DiscoveryResult(
            can_run=True,
            capabilities=self.manifest.capabilities,
            requested_actions=requested,
        )

    def collect(self, context: CollectionContext, output_dir: Path) -> CollectionResult:
        """Run the manifest ``collect`` command, capturing raw artifacts (D2/D6)."""
        self._enforce_actions(context)
        template = self.manifest.commands.get("collect")
        if template is None:
            return CollectionResult(status="skipped", diagnostics=["no 'collect' command"])
        raw_dir = output_dir / "raw" / self.manifest.name
        raw_dir.mkdir(parents=True, exist_ok=True)
        argv = render_command(template, self._substitutions(context, output_dir))
        proc = self._run(argv, cwd=context.source_root or context.build_root)
        diagnostics: list[str] = []
        if proc.returncode != 0:
            diagnostics.append(
                f"collect exited {proc.returncode}: {(proc.stderr or '').strip()[:500]}"
            )
            return CollectionResult(status="failed", diagnostics=diagnostics)
        artifacts = [
            RawArtifact(kind="raw", path=p, content_hash="sha256:" + _file_sha256(p))
            for p in sorted(raw_dir.rglob("*")) if p.is_file()
        ]
        return CollectionResult(raw_artifacts=artifacts, status="ok", diagnostics=diagnostics)

    def normalize(self, raw_artifacts: list[RawArtifact], output_dir: Path) -> NormalizationResult:
        """Run the manifest ``normalize`` command (if any) and map outputs (D2/D8)."""
        template = self.manifest.commands.get("normalize")
        diagnostics: list[str] = []
        if template is not None:
            norm_dir = output_dir / "normalized" / self.manifest.name
            norm_dir.mkdir(parents=True, exist_ok=True)
            # Re-derive substitutions from the pack root; normalize is a pure
            # transform that only needs the raw/normalized dirs.
            sub = {
                "raw_dir": str(output_dir / "raw" / self.manifest.name),
                "normalized_dir": str(norm_dir),
            }
            argv = render_command(template, _with_optional_only(template, sub))
            proc = self._run(argv)
            if proc.returncode != 0:
                diagnostics.append(
                    f"normalize exited {proc.returncode}: {(proc.stderr or '').strip()[:500]}"
                )
                return NormalizationResult(status="failed", diagnostics=diagnostics)
        by_kind: dict[str, Path] = {}
        paths: list[Path] = []
        for out in self.manifest.outputs:
            path = output_dir / out.path
            by_kind[out.kind] = path
            paths.append(path)
        return NormalizationResult(
            normalized_paths=paths, by_kind=by_kind, status="ok", diagnostics=diagnostics
        )

    def validate(self, normalized_artifacts: list[Path]) -> ValidationResult:
        """Check each declared normalized output exists and is valid JSON (D8)."""
        errors: list[str] = []
        for path in normalized_artifacts:
            if not path.is_file():
                errors.append(f"declared output missing: {path}")
                continue
            try:
                json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                errors.append(f"output is not valid JSON ({path}): {exc}")
        return ValidationResult(ok=not errors, errors=errors)

    # -- ledger -------------------------------------------------------------

    def command_hash(self, context: CollectionContext, pack_root: Path) -> str:
        """Stable ``sha256:`` over the rendered commands + version + inputs (D10)."""
        h = hashlib.sha256()
        h.update((self.manifest.name + "\0" + self.manifest.version).encode("utf-8"))
        sub = self._substitutions(context, pack_root)
        for phase in _PHASES:
            template = self.manifest.commands.get(phase)
            if template is None:
                continue
            try:
                rendered = render_command(template, _with_optional_only(template, sub))
            except ManifestError:
                rendered = template
            h.update(("\0" + phase + "\0" + "\0".join(rendered)).encode("utf-8"))
        return "sha256:" + h.hexdigest()


def run_external_extractor(
    manifest: ExtractorManifest,
    context: CollectionContext,
    pack_root: Path,
    *,
    redaction: RedactionPolicy = DEFAULT_REDACTION,
) -> tuple[NormalizationResult, ExtractorRecord]:
    """Drive one external extractor end-to-end and produce its ledger row.

    Runs ``collect`` → ``normalize`` → ``validate`` under the action ceiling,
    recording timing, the redacted command, its hash, capabilities, and
    diagnostics into an :class:`ExtractorRecord` (D10). Never raises for a tool
    failure — the failure is captured in the returned record's ``status`` so the
    caller can apply the collection-mode policy (D9). Only an
    :class:`~abicheck.evidence.extractor.ActionNotPermittedError` propagates,
    because a permission violation is an operator error, not a tool failure.
    """
    extractor = ExternalCliExtractor(manifest, redaction=redaction)
    started = _dt.datetime.now(_dt.timezone.utc)
    record = ExtractorRecord(
        name=manifest.name,
        version=manifest.version,
        capabilities=[k for k, v in manifest.capabilities.to_dict().items() if v is True],
        command_hash=extractor.command_hash(context, pack_root),
        command=_redacted_command(manifest, context, pack_root, redaction),
        started_at=started.isoformat(),
        inputs=[redaction.path(x) for x in manifest.input_requirements],
    )

    # Action enforcement first: a violation is the operator's to fix, so it
    # propagates rather than being swallowed as a tool failure.
    extractor._enforce_actions(context)

    diagnostics: list[str] = []
    # A ManifestError from command rendering (e.g. a template needs {build_dir}
    # but the run supplied none) is a tool/config failure, not a permission
    # violation: capture it as a failed record so the D9 policy applies.
    try:
        collected = extractor.collect(context, pack_root)
        diagnostics.extend(collected.diagnostics)
        if collected.status == "failed":
            return _finish(NormalizationResult(status="failed", diagnostics=diagnostics),
                           record, started, "failed", diagnostics)

        normalized = extractor.normalize(collected.raw_artifacts, pack_root)
        diagnostics.extend(normalized.diagnostics)
    except ManifestError as exc:
        diagnostics.append(str(exc))
        return _finish(NormalizationResult(status="failed", diagnostics=diagnostics),
                       record, started, "failed", diagnostics)
    if normalized.status == "failed":
        return _finish(normalized, record, started, "failed", diagnostics)

    validation = extractor.validate(normalized.normalized_paths)
    if not validation.ok:
        diagnostics.extend(validation.errors)
        return _finish(normalized, record, started, "failed", diagnostics)

    record.artifacts = [str(p.relative_to(pack_root)) for p in normalized.normalized_paths]
    return _finish(normalized, record, started, "ok", diagnostics)


def _finish(
    normalized: NormalizationResult,
    record: ExtractorRecord,
    started: _dt.datetime,
    status: str,
    diagnostics: list[str],
) -> tuple[NormalizationResult, ExtractorRecord]:
    record.status = status
    record.finished_at = _dt.datetime.now(_dt.timezone.utc).isoformat()
    record.diagnostics = list(diagnostics)
    if status != "ok" and diagnostics:
        record.detail = diagnostics[0][:200]
    return normalized, record


def _redacted_command(
    manifest: ExtractorManifest,
    context: CollectionContext,
    pack_root: Path,
    redaction: RedactionPolicy,
) -> str:
    """A single redacted command string for the ledger (collect phase, D10)."""
    template = manifest.commands.get("collect") or manifest.commands.get("normalize") or []
    extractor = ExternalCliExtractor(manifest, redaction=redaction)
    sub = extractor._substitutions(context, pack_root)
    try:
        rendered = render_command(template, _with_optional_only(template, sub))
    except ManifestError:
        rendered = template
    return " ".join(redaction.arg(tok) for tok in rendered)


def _with_optional_only(template: list[str], sub: dict[str, str]) -> dict[str, str]:
    """Provide empty strings for known-but-unsupplied placeholders the template uses.

    ``raw_dir``/``normalized_dir`` are always supplied; other known placeholders
    that the run did not populate (no ``--build-dir``, etc.) render to empty
    rather than raising, so a partially-specified run still produces a stable,
    hashable command for the ledger.
    """
    out = dict(sub)
    for token in template:
        for ph in _PLACEHOLDER_RE.findall(token):
            out.setdefault(ph, "")
    return out


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


__all__ = [
    "DEFAULT_COMMAND_TIMEOUT",
    "ExternalCliExtractor",
    "ManifestError",
    "ManifestOutput",
    "ExtractorManifest",
    "CollectionMode",
    "load_extractor_manifest",
    "render_command",
    "run_external_extractor",
]
