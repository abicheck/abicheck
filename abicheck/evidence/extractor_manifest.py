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

#: The lifecycle phases a manifest may define commands for, in run order. Only
#: ``collect`` and ``normalize`` are *executed* by :class:`ExternalCliExtractor`
#: today; ``discover`` and ``validate`` are accepted (so an ADR-032 D3 manifest
#: loads) but the adapter uses static capability discovery and built-in JSON
#: output validation rather than running those commands — they are reserved for
#: a future engine that shells them out.
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
    _reject_unsafe_name(name, p)

    commands = _parse_commands(raw.get("commands") or {}, p)
    actions, capabilities = _resolve_actions_and_capabilities(raw, p)

    version_command = raw.get("version_command") or []
    if version_command and (
        not isinstance(version_command, list)
        or not all(isinstance(t, str) for t in version_command)
    ):
        raise ManifestError(f"extractor manifest {p}: 'version_command' must be a list of strings")

    outputs_raw = raw.get("outputs") or {}
    outputs = _parse_outputs(outputs_raw, p)
    if not outputs:
        raise ManifestError(
            f"extractor manifest {p}: must declare at least one output — an extractor "
            "that produces no foldable evidence cannot be recorded as a successful "
            "collection (a missing/empty 'outputs' is a typo, not a no-op)"
        )

    input_requirements = [str(x) for x in (raw.get("input_requirements") or [])]

    try:
        schema_version = int(raw.get("schema_version", 1) or 1)
    except (TypeError, ValueError) as exc:
        raise ManifestError(
            f"extractor manifest {p}: 'schema_version' must be an integer"
        ) from exc

    return ExtractorManifest(
        name=name,
        version=str(raw.get("version", "")),
        version_command=list(version_command),
        capabilities=capabilities,
        input_requirements=input_requirements,
        allowed_actions=actions,
        commands=commands,
        outputs=outputs,
        schema_version=schema_version,
    )


def _parse_commands(commands_raw: Any, p: Path) -> dict[str, list[str]]:
    """Validate the ``commands`` block into a ``{phase: argv}`` mapping (D3).

    Each phase must be a known lifecycle phase, each command a non-empty list of
    string tokens (an argv list — never a shell string), and every placeholder
    must be known so a render-time ``KeyError`` cannot surprise us. At least one
    of ``collect``/``normalize`` must be present. Raises :class:`ManifestError`.
    """
    if not isinstance(commands_raw, dict):
        raise ManifestError(f"extractor manifest {p}: 'commands' must be a mapping")
    commands: dict[str, list[str]] = {}
    for phase, template in commands_raw.items():
        if phase not in _PHASES:
            raise ManifestError(
                f"extractor manifest {p}: unknown command phase {phase!r}; "
                f"expected one of: {', '.join(_PHASES)}"
            )
        if (
            not isinstance(template, list)
            or not template
            or not all(isinstance(t, str) for t in template)
        ):
            raise ManifestError(
                f"extractor manifest {p}: command '{phase}' must be a non-empty list of string "
                "tokens (an argv list — never a single shell string, never empty)"
            )
        _reject_unknown_placeholders(template, phase, p)
        commands[phase] = list(template)
    if "collect" not in commands and "normalize" not in commands:
        raise ManifestError(
            f"extractor manifest {p}: must define at least a 'collect' or 'normalize' command"
        )
    return commands


def _reject_unknown_placeholders(template: list[str], phase: str, p: Path) -> None:
    """Reject ``{placeholder}`` tokens that are not in :data:`_KNOWN_PLACEHOLDERS`."""
    for token in template:
        for ph in _PLACEHOLDER_RE.findall(token):
            if ph not in _KNOWN_PLACEHOLDERS:
                raise ManifestError(
                    f"extractor manifest {p}: command '{phase}' references unknown "
                    f"placeholder {{{ph}}}; known: {', '.join(sorted(_KNOWN_PLACEHOLDERS))}"
                )


def _resolve_actions_and_capabilities(
    raw: dict[str, Any], p: Path
) -> tuple[set[CollectionAction], ExtractorCapabilities]:
    """Parse ``allowed_actions`` + ``capabilities`` and check they are consistent (D4/D5).

    Network is always denied (ADR-032 D5) and no run mode grants it, so a
    manifest that needs it — via the ``network`` action *or* the
    ``requires_network`` capability — can never run and is rejected at
    registration rather than silently accepted under the inspect-only default.
    Capabilities' implied actions must all be present in ``allowed_actions``.
    Raises :class:`ManifestError`.
    """
    try:
        actions = parse_actions(raw.get("allowed_actions") or [])
    except ValueError as exc:
        raise ManifestError(f"extractor manifest {p}: {exc}") from exc

    if CollectionAction.NETWORK in actions:
        raise ManifestError(
            f"extractor manifest {p}: the 'network' action is always denied "
            "(ADR-032 D5) and cannot be registered."
        )
    capabilities = _coerce_capabilities(raw.get("capabilities"), p)
    if capabilities.requires_network:
        raise ManifestError(
            f"extractor manifest {p}: 'requires_network' is not supported — network "
            "access is always denied (ADR-032 D5). Use a non-networked extractor or "
            "pre-capture the data and feed it as a file input."
        )
    missing = capabilities.implied_actions() - actions
    if missing:
        raise ManifestError(
            f"extractor manifest {p}: capabilities require action(s) "
            f"{', '.join(sorted(a.value for a in missing))} that are absent from 'allowed_actions'"
        )
    return actions, capabilities


def _coerce_capabilities(raw_caps: Any, p: Path) -> ExtractorCapabilities:
    """Parse the ``capabilities`` block, accepting both ADR-032 forms (D3/D4).

    D4 specifies a mapping of capability → bool; the D3 manifest example uses a
    YAML *list* of capability names (each implicitly enabled). Both are accepted.
    Any other shape (a bare string, a number, a list with non-string items) is a
    :class:`ManifestError` so the loader's caller records a failed extractor
    rather than aborting on an ``AttributeError`` from ``.get`` on a non-mapping.
    """
    if raw_caps is None:
        return ExtractorCapabilities()
    if isinstance(raw_caps, dict):
        return ExtractorCapabilities.from_dict(raw_caps)
    if isinstance(raw_caps, list):
        if not all(isinstance(x, str) for x in raw_caps):
            raise ManifestError(
                f"extractor manifest {p}: 'capabilities' list items must be capability names (strings)"
            )
        return ExtractorCapabilities.from_dict({name: True for name in raw_caps})
    raise ManifestError(
        f"extractor manifest {p}: 'capabilities' must be a mapping or a list of capability names"
    )


def _parse_outputs(outputs_raw: Any, p: Path) -> list[ManifestOutput]:
    """Parse the ``outputs`` block, which may be a list or a ``{group: [..]}`` map."""
    rows: list[Any]
    if isinstance(outputs_raw, dict):
        # Manifest groups outputs by category (e.g. ``normalized:``); flatten.
        # A non-list group is a manifest error, not something to drop silently:
        # silently dropping it would leave the extractor with zero declared
        # outputs, so it would run, validate nothing, and be recorded ``ok``
        # (even under strict mode) without producing any evidence (Codex P2).
        rows = []
        for key, group in outputs_raw.items():
            if not isinstance(group, list):
                raise ManifestError(
                    f"extractor manifest {p}: outputs group {key!r} must be a list of "
                    "{kind, path} entries"
                )
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
        path = str(row["path"])
        _reject_unsafe_output_path(path, p)
        out.append(ManifestOutput(kind=str(row["kind"]), path=path))
    return out


def _reject_unsafe_name(name: str, p: Path) -> None:
    """Reject extractor names that are not a single safe path component (D3).

    The name is later used to build per-extractor pack directories
    (``raw/<name>``, ``normalized/<name>``). An absolute path or one containing
    ``..`` or a path separator would let ``pack_root / name`` discard the pack
    prefix or traverse out of it, so abicheck could create, scan, or purge
    directories outside the pack root. Checked under both POSIX and Windows path
    rules so a manifest stays portable. Raises :class:`ManifestError`.
    """
    from pathlib import PurePosixPath, PureWindowsPath

    posix, win = PurePosixPath(name), PureWindowsPath(name)
    if (
        name in (".", "..")
        or "/" in name
        or "\\" in name
        or posix.is_absolute()
        or win.is_absolute()
        or win.drive
    ):
        raise ManifestError(
            f"extractor manifest {p}: 'name' must be a single safe path component, got {name!r}"
        )


def _reject_unsafe_output_path(path: str, p: Path) -> None:
    """Reject output paths that are absolute or escape the pack root (D3/D6).

    Outputs are declared *relative to the pack root*; an absolute or ``..`` path
    would let the tool write outside the pack and later crash
    :func:`run_external_extractor` at ``Path.relative_to(pack_root)``. Checked
    under both POSIX and Windows path rules so a manifest is portable and a
    traversal cannot slip through on one OS. Raises :class:`ManifestError`, which
    the loader's caller records as a failed extractor instead of aborting.
    """
    from pathlib import PurePosixPath, PureWindowsPath

    posix, win = PurePosixPath(path), PureWindowsPath(path)
    if (
        not path
        or posix.is_absolute()
        or win.is_absolute()
        or win.drive
        or path.startswith(("/", "\\"))
        or ".." in posix.parts
        or ".." in win.parts
    ):
        raise ManifestError(
            f"extractor manifest {p}: output path {path!r} must be relative to the "
            "pack root and must not be absolute or contain '..'"
        )


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
    #: The run context, so the ``normalize`` phase sees the same context-derived
    #: placeholders ({source_root}/{build_dir}/{compile_db}/{binary}/{cache_dir})
    #: that ``collect`` does. The driver sets it; ``None`` falls back to the
    #: raw/normalized dirs only (for a standalone protocol call).
    context: CollectionContext | None = None

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
        """Build the placeholder map from the context + pack layout (D6).

        Every path is rendered *absolute* (resolved against the invoking cwd at
        build time), because :meth:`collect` runs the subprocess with its cwd set
        to the source/build root. A relative ``--build-dir build`` rendered as
        ``build`` would otherwise resolve to ``build/build`` from inside that cwd;
        absolute paths keep argv values stable regardless of the subprocess cwd.
        """
        def ap(p: Path) -> str:
            return os.path.abspath(str(p))

        sub: dict[str, str] = {
            "raw_dir": ap(pack_root / "raw" / self.manifest.name),
            "normalized_dir": ap(pack_root / "normalized" / self.manifest.name),
        }
        if context.build_root is not None:
            sub["build_dir"] = ap(context.build_root)
        if context.source_root is not None:
            sub["source_root"] = ap(context.source_root)
        if context.compile_db is not None:
            sub["compile_db"] = ap(context.compile_db)
        if context.cache_dir is not None:
            sub["cache_dir"] = ap(context.cache_dir)
        if context.binary_paths:
            sub["binary"] = ap(context.binary_paths[0])
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
        diagnostics: list[str] = []
        try:
            proc = self._run(argv, cwd=context.source_root or context.build_root)
        except (OSError, subprocess.SubprocessError) as exc:
            # Missing binary (FileNotFoundError/OSError) or a hung tool
            # (TimeoutExpired) is a tool failure, not a crash: record it so the
            # collection-mode policy (D9) decides, rather than aborting the run.
            return CollectionResult(
                status="failed",
                diagnostics=[f"collect could not run {argv[0]!r}: {exc}"],
            )
        if proc.returncode != 0:
            diagnostics.append(
                f"collect exited {proc.returncode}: "
                f"{_stderr_excerpt(proc.stderr, context.collection_mode)}"
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
            # Use the same context-derived substitutions collect() does so a
            # normalize command can reference {source_root}/{build_dir}/etc. when
            # the run supplied them; a referenced-but-unsupplied placeholder fails
            # loudly (render_command raises ManifestError) rather than silently
            # rendering an empty path. With no context, only raw/normalized dirs
            # are available (standalone protocol call).
            if self.context is not None:
                sub = self._substitutions(self.context, output_dir)
            else:
                sub = {
                    "raw_dir": str(output_dir / "raw" / self.manifest.name),
                    "normalized_dir": str(norm_dir),
                }
            argv = render_command(template, sub)
            try:
                proc = self._run(argv)
            except (OSError, subprocess.SubprocessError) as exc:
                return NormalizationResult(
                    status="failed",
                    diagnostics=[f"normalize could not run {argv[0]!r}: {exc}"],
                )
            mode = self.context.collection_mode if self.context else CollectionMode.PERMISSIVE
            if proc.returncode != 0:
                diagnostics.append(
                    f"normalize exited {proc.returncode}: {_stderr_excerpt(proc.stderr, mode)}"
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

    def resolve_version(self, context: CollectionContext, pack_root: Path) -> str:
        """Run the manifest ``version_command`` to record the real tool version (D10).

        ADR-032 D10 wants the *actual* tool version in the ledger, not just what
        the manifest claims. When a ``version_command`` is declared and the run
        already permits invoking the tool (the caller gates this after
        :meth:`discover`), run it and use the first stdout line. Any failure
        (missing tool, non-zero exit, timeout, render error) falls back to the
        manifest's declared ``version`` so version probing never breaks a run.
        """
        template = self.manifest.version_command
        if not template:
            return self.manifest.version
        try:
            argv = render_command(template, _with_optional_only(template, {}))
            proc = self._run(argv)
        except (OSError, subprocess.SubprocessError, ManifestError):
            return self.manifest.version
        if proc.returncode != 0:
            return self.manifest.version
        lines = [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()]
        return lines[0] if lines else self.manifest.version

    def command_hash(
        self, context: CollectionContext, pack_root: Path, *, version: str | None = None
    ) -> str:
        """Stable ``sha256:`` over the rendered commands + version + inputs (D10).

        *version* defaults to the manifest's declared version; the driver passes
        the *resolved* tool version so the hash reflects the actual toolchain.
        """
        h = hashlib.sha256()
        ver = self.manifest.version if version is None else version
        h.update((self.manifest.name + "\0" + ver).encode("utf-8"))
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

    def ledger_command(self, context: CollectionContext, pack_root: Path) -> str:
        """Redacted argv of the *executed* phases for the ledger ``command`` (D10).

        Joins the rendered ``collect`` and ``normalize`` commands (the phases this
        adapter actually runs) with `` && `` so a two-phase extractor is fully
        represented, not just its first phase. Redaction is argv-aware
        (:meth:`RedactionPolicy.argv`) so a secret split across two tokens
        (``-D KEY=secret``) is stripped. A render error falls back to the raw
        template so the ledger still records intent.
        """
        sub = self._substitutions(context, pack_root)
        parts: list[str] = []
        for phase in ("collect", "normalize"):
            template = self.manifest.commands.get(phase)
            if not template:
                continue
            try:
                rendered = render_command(template, _with_optional_only(template, sub))
            except ManifestError:
                rendered = template
            parts.append(" ".join(self.redaction.argv(list(rendered))))
        return " && ".join(parts)


def run_external_extractor(
    manifest: ExtractorManifest,
    context: CollectionContext,
    pack_root: Path,
    *,
    redaction: RedactionPolicy = DEFAULT_REDACTION,
) -> tuple[NormalizationResult, ExtractorRecord]:
    """Drive one external extractor end-to-end and produce its ledger row.

    Gates on :meth:`ExternalCliExtractor.discover` (D4): an extractor whose
    declared actions are not permitted for the run is recorded ``skipped`` with
    the reason rather than run. Otherwise it runs ``collect`` → ``normalize`` →
    ``validate`` under the action ceiling, recording timing, the *resolved* tool
    version, the redacted command, its hash, capabilities, and diagnostics into
    an :class:`ExtractorRecord` (D10). Never raises for a tool failure — the
    failure is captured in the returned record's ``status`` so the caller can
    apply the collection-mode policy (D9).
    """
    extractor = ExternalCliExtractor(manifest, redaction=redaction, context=context)
    started = _dt.datetime.now(_dt.timezone.utc)
    capabilities = [k for k, v in manifest.capabilities.to_dict().items() if v is True]
    inputs = [redaction.path(x) for x in manifest.input_requirements]

    # D4: decide whether this extractor may run at all before touching the host.
    # A ceiling violation is a skipped row (requested evidence absent), which the
    # D9 collection-mode policy acts on — not an exception the caller must catch.
    discovery = extractor.discover(context)
    if not discovery.can_run:
        record = ExtractorRecord(
            name=manifest.name, version=manifest.version, status="skipped",
            capabilities=capabilities, inputs=inputs,
            command_hash=extractor.command_hash(context, pack_root),
            command=extractor.ledger_command(context, pack_root),
            started_at=started.isoformat(), detail=discovery.reason,
        )
        return _finish(
            NormalizationResult(status="skipped", diagnostics=[discovery.reason]),
            record, started, "skipped", [discovery.reason],
        )

    # The run permits this extractor's actions, so probing its version (D10) is
    # within the already-granted permission to invoke the tool.
    version = extractor.resolve_version(context, pack_root)
    record = ExtractorRecord(
        name=manifest.name,
        version=version,
        capabilities=capabilities,
        command_hash=extractor.command_hash(context, pack_root, version=version),
        command=extractor.ledger_command(context, pack_root),
        started_at=started.isoformat(),
        inputs=inputs,
    )

    # Defensive: discover() already verified the ceiling, but keep the per-phase
    # guard so collect() is safe to call directly too.
    extractor._enforce_actions(context)

    # Clear any stale declared outputs before running so success requires *this*
    # invocation to produce them. In a reused pack directory — or when two
    # manifests share a canonical output like build/build_evidence.json — a file
    # left by an earlier run must not be validated and folded as fresh evidence
    # when the current tool exits 0 without rewriting it (Codex P2).
    for output in manifest.outputs:
        stale = pack_root / output.path
        try:
            stale.unlink()
        except OSError:
            pass  # absent or undeletable; validate() will catch a missing output

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


def _stderr_excerpt(stderr: str | None, mode: CollectionMode) -> str:
    """Trim a tool's stderr for a diagnostic — but keep it whole in audit mode.

    ``audit`` mode promises full diagnostics (ADR-032 D9), so the 500-char cap
    that keeps ordinary permissive/strict diagnostics readable is lifted there;
    the failure details may be the whole point of the audit pack.
    """
    text = (stderr or "").strip()
    return text if mode == CollectionMode.AUDIT else text[:500]


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
