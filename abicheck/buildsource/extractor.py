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

"""Evidence-extractor plugin interface and security model (ADR-032).

This module is the *formal contract* every evidence extractor sits behind —
the build adapters (ADR-029), source-ABI extractors (ADR-030), graph backends
(ADR-031), and external CLI extractors (ADR-032 D3). It owns four things the
rest of the evidence package builds on:

* **The interface (D2).** :class:`DataExtractor` — a ``Protocol`` with the
  four lifecycle phases ``discover``/``collect``/``normalize``/``validate`` —
  plus :class:`CollectionContext` and the per-phase result dataclasses.
* **The capability model (D4).** :class:`ExtractorCapabilities` — what an
  extractor *can* produce, used to drive evidence coverage and CI policy.
* **The action-permission model (D5).** :class:`CollectionAction` plus
  :func:`resolve_allowed_actions` / :func:`require_action` — a manifest's
  declared actions are a *ceiling*, intersected at run time with the actions
  the operator enabled for the run. ``inspect`` is the only default-allowed
  action; everything that queries, compiles, builds, wraps, or hits the network
  is explicit opt-in.
* **The failure modes (D9).** :class:`CollectionMode` — ``permissive`` (default;
  a failed extractor degrades coverage), ``strict`` (a failed extractor fails
  the command), ``audit`` (preserve raw artifacts + full diagnostics).

The one rule (D1): an extractor *collects and normalizes facts*; it never
decides an ABI/API verdict. Verdict policy stays in the core compare engine.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from .redaction import DEFAULT_REDACTION, RedactionPolicy


class CollectionAction(str, Enum):
    """A side-effecting capability an extractor may request at run time (D5).

    Ordered shallow→deep by how much they touch the host: ``INSPECT`` only reads
    existing files; the rest run external processes, build code, intercept the
    build, or reach the network. Only ``INSPECT`` is allowed by default.
    """

    INSPECT = "inspect"                      # read existing files, parse a compile DB / CMake reply
    QUERY_BUILD_SYSTEM = "query_build_system"  # `ninja -t`, `bazel cquery`/`aquery`, regenerate a File API query
    RUN_COMPILER = "run_compiler"            # syntax-only source extraction (clang/castxml replay)
    RUN_BUILD = "run_build"                  # `cmake --build`, `bazel build`, `make`
    WRAP_BUILD = "wrap_build"                # Bear / intercept-build / compiler wrapper
    NETWORK = "network"                      # download tools or dependencies


#: The only action allowed unless the operator opts in (ADR-032 D5). Everything
#: heavier than reading files on disk must be explicitly enabled for the run.
DEFAULT_ALLOWED_ACTIONS: frozenset[CollectionAction] = frozenset({CollectionAction.INSPECT})

#: Actions that can *never* be enabled through ordinary opt-in flags — only a
#: future, explicit mode may grant them (ADR-032 D5: network is always denied).
ALWAYS_DENIED_ACTIONS: frozenset[CollectionAction] = frozenset({CollectionAction.NETWORK})


class CollectionMode(str, Enum):
    """How extractor failures affect the command (ADR-032 D9).

    These modes affect *collection* only; compare exit codes keep their ADR-009
    contract regardless.
    """

    PERMISSIVE = "permissive"  # missing/failed extractors → reduced coverage; collection continues (default)
    STRICT = "strict"          # requested evidence must be collected and valid, or the command exits non-zero
    AUDIT = "audit"            # preserve raw artifacts + full diagnostics for debugging extractor behaviour


class ExtractorError(RuntimeError):
    """An extractor could not complete a lifecycle phase.

    In ``permissive`` mode the driver records this as reduced coverage and keeps
    going; in ``strict`` mode it propagates and fails the command (ADR-032 D9).
    """


class ActionNotPermittedError(ExtractorError):
    """An extractor requested a :class:`CollectionAction` the run did not allow.

    Raised when a manifest's declared action survives the ceiling intersection
    but is not in the run-permitted set (ADR-032 D5) — collection fails with a
    clear diagnostic rather than silently escalating.
    """


def parse_action(raw: Any) -> CollectionAction:
    """Map a string (or :class:`CollectionAction`) to the enum, raising on junk.

    Unknown actions are rejected loudly so a typo in a manifest's
    ``allowed_actions`` cannot silently widen — or quietly drop — the surface.
    """
    if isinstance(raw, CollectionAction):
        return raw
    try:
        return CollectionAction(str(raw))
    except ValueError as exc:
        allowed = ", ".join(a.value for a in CollectionAction)
        raise ValueError(f"unknown collection action {raw!r}; expected one of: {allowed}") from exc


def parse_actions(raw: Any) -> set[CollectionAction]:
    """Parse an iterable of action tokens into a normalized set (D5)."""
    if not raw:
        return set()
    return {parse_action(item) for item in raw}


def resolve_allowed_actions(
    declared: set[CollectionAction] | frozenset[CollectionAction],
    run_permitted: set[CollectionAction] | frozenset[CollectionAction],
) -> set[CollectionAction]:
    """Intersect an extractor's declared ceiling with the run-permitted set (D5).

    A manifest's ``allowed_actions`` are a *ceiling, not a grant*: at run time
    they are intersected with the actions the operator enabled for this run, so a
    manifest can never escalate beyond what the operator turned on. ``network``
    is filtered out unconditionally (:data:`ALWAYS_DENIED_ACTIONS`).
    """
    return (set(declared) & set(run_permitted)) - ALWAYS_DENIED_ACTIONS


def require_action(
    action: CollectionAction,
    allowed: set[CollectionAction] | frozenset[CollectionAction],
    *,
    extractor: str = "",
) -> None:
    """Assert *action* is in *allowed*, else raise :class:`ActionNotPermittedError`.

    The error names the offending action and the opt-in needed so the operator
    can either enable it deliberately or pick a lighter extractor.
    """
    if action in allowed:
        return
    who = f" {extractor!r}" if extractor else ""
    raise ActionNotPermittedError(
        f"extractor{who} requested action {action.value!r}, which is not permitted "
        f"for this run (allowed: {', '.join(sorted(a.value for a in allowed)) or 'inspect only'}). "
        f"Enable it explicitly (e.g. --allow-build-query for {CollectionAction.QUERY_BUILD_SYSTEM.value})."
    )


@dataclass
class ExtractorCapabilities:
    """What an extractor *can* produce (ADR-032 D4).

    Capability reporting drives evidence coverage (ADR-028 D7) and CI policy
    (ADR-033) — it is declarative metadata, not a promise that every field will
    be populated on a given run. Unknown keys parsed from a manifest are kept in
    ``extra`` so a newer extractor never fails to load against an older abicheck.
    """

    compile_db: bool = False
    target_graph: bool = False
    toolchain: bool = False
    link_actions: bool = False
    source_abi: bool = False
    source_graph_summary: bool = False
    call_graph: bool = False
    requires_build_execution: bool = False
    requires_compiler_execution: bool = False
    requires_network: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    #: The declarative capability flags (everything but ``extra``), in schema order.
    _FLAGS = (
        "compile_db", "target_graph", "toolchain", "link_actions",
        "source_abi", "source_graph_summary", "call_graph",
        "requires_build_execution", "requires_compiler_execution", "requires_network",
    )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {name: bool(getattr(self, name)) for name in self._FLAGS}
        out.update(self.extra)
        return out

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> ExtractorCapabilities:
        d = d or {}
        known = set(cls._FLAGS)
        return cls(
            **{name: bool(d.get(name, False)) for name in cls._FLAGS},
            extra={k: v for k, v in d.items() if k not in known},
        )

    def implied_actions(self) -> set[CollectionAction]:
        """Actions the declared capabilities imply the extractor needs (D4↔D5).

        Used to cross-check a manifest: an extractor that declares
        ``requires_build_execution`` but does not list ``run_build`` among its
        ``allowed_actions`` is inconsistent, and the loader can flag it.
        """
        actions: set[CollectionAction] = set()
        if self.requires_build_execution:
            actions.add(CollectionAction.RUN_BUILD)
        if self.requires_compiler_execution:
            actions.add(CollectionAction.RUN_COMPILER)
        if self.requires_network:
            actions.add(CollectionAction.NETWORK)
        return actions


@dataclass
class CollectionContext:
    """Everything an extractor is told about the run (ADR-032 D2).

    Immutable, abicheck-owned inputs: the extractor reads them but never decides
    a verdict from them (D1). ``allowed_actions`` is the already-resolved set for
    *this run* (the manifest ceiling ∩ run-permitted, D5) — an extractor must
    call :func:`require_action` before doing anything heavier than ``inspect``.
    """

    binary_paths: list[Path] = field(default_factory=list)
    header_roots: list[Path] = field(default_factory=list)
    source_root: Path | None = None
    build_root: Path | None = None
    compile_db: Path | None = None
    target_selectors: list[str] = field(default_factory=list)
    changed_files: list[Path] = field(default_factory=list)
    mode: Literal["baseline", "pr", "nightly", "manual"] = "manual"
    allowed_actions: set[CollectionAction] = field(
        default_factory=lambda: set(DEFAULT_ALLOWED_ACTIONS)
    )
    collection_mode: CollectionMode = CollectionMode.PERMISSIVE
    redaction_policy: RedactionPolicy = field(default_factory=lambda: DEFAULT_REDACTION)
    cache_dir: Path | None = None

    def permits(self, action: CollectionAction) -> bool:
        """True if *action* is allowed for this run (D5)."""
        return action in self.allowed_actions

    def require(self, action: CollectionAction, *, extractor: str = "") -> None:
        """Raise :class:`ActionNotPermittedError` unless *action* is permitted."""
        require_action(action, self.allowed_actions, extractor=extractor)


@dataclass
class RawArtifact:
    """One raw tool output captured under ``raw/`` for provenance only (D6).

    Raw artifacts never feed the pack content hash (ADR-028 D4) — they exist for
    debugging/reproducibility. ``content_hash`` covers the command, working
    directory, relevant environment, input hashes, and tool/schema versions.
    """

    kind: str
    path: Path
    content_hash: str = ""
    command: str = ""
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "path": str(self.path),
            "content_hash": self.content_hash,
            "command": self.command,
            "detail": self.detail,
        }


@dataclass
class DiscoveryResult:
    """What an extractor reports it *can* collect for a given context (D2)."""

    can_run: bool = False
    capabilities: ExtractorCapabilities = field(default_factory=ExtractorCapabilities)
    requested_actions: set[CollectionAction] = field(default_factory=set)
    reason: str = ""  # human-readable note when ``can_run`` is False
    diagnostics: list[str] = field(default_factory=list)


@dataclass
class CollectionResult:
    """Raw artifacts produced by ``collect`` (D2). Never normalized verdicts."""

    raw_artifacts: list[RawArtifact] = field(default_factory=list)
    status: str = "ok"  # ok | partial | failed | skipped
    diagnostics: list[str] = field(default_factory=list)


@dataclass
class NormalizationResult:
    """Normalized, abicheck-owned artifact paths produced by ``normalize`` (D2)."""

    normalized_paths: list[Path] = field(default_factory=list)
    #: Maps the declared output ``kind`` (e.g. ``build_evidence``) → its path, so
    #: the driver knows how to fold each normalized file into the pack.
    by_kind: dict[str, Path] = field(default_factory=dict)
    status: str = "ok"
    diagnostics: list[str] = field(default_factory=list)


@dataclass
class ValidationResult:
    """Outcome of schema + consistency checks over normalized output (D8)."""

    ok: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.ok


@runtime_checkable
class DataExtractor(Protocol):
    """The four-phase evidence-extractor contract (ADR-032 D2).

    An extractor collects and normalizes facts; it never decides a verdict (D1).
    The phases are deliberately separable so collection (which may touch the
    host) is isolated from normalization (pure transformation into abicheck's
    schema) and validation (schema + consistency checks).
    """

    name: str
    version: str
    schema_version: int

    def discover(self, context: CollectionContext) -> DiscoveryResult:
        """Report whether this extractor can run and what it can collect."""
        ...

    def collect(self, context: CollectionContext, output_dir: Path) -> CollectionResult:
        """Collect raw artifacts. Must not normalize verdicts."""
        ...

    def normalize(self, raw_artifacts: list[RawArtifact], output_dir: Path) -> NormalizationResult:
        """Convert raw artifacts into abicheck-owned schema."""
        ...

    def validate(self, normalized_artifacts: list[Path]) -> ValidationResult:
        """Schema and consistency checks."""
        ...
