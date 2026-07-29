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

"""Trusted toolchain-bindings file (P1 toolchain-profile audit).

Resolves a `.abicheck.yml` `profiles.<id>.compile.binding` logical
identifier (e.g. ``"gcc14"``, ``"castxml07"``) to an exact executable path,
via a *separately trusted* mapping file — never via the auto-discovered,
untrusted `.abicheck.yml` itself.

**Trust boundary** (CLAUDE.md "M1", mirrors
``buildsource.extractor_manifest``'s security model): a `.abicheck.yml`
found by auto-discovery may *declare* a logical ``binding`` id
(``ProfileCompileSpec.binding``, validated whitespace-free by
``project_targets._safe_profile_atom`` so it can never smuggle argv), but it
must never carry a raw executable path or shell-interpretable string —
that would let a repository an operator merely checked out (not
configured) point abicheck at an arbitrary local binary. A bindings file is
the opposite: an operator- or CI-managed mapping, loaded only from an
explicit path (``--toolchain-bindings`` / a trusted ``--config`` source),
never auto-discovered from the working tree. :func:`load_bindings_file`
loads exactly the path it is given — nothing here scans ``PATH``, the
working tree, or any plugin directory.

This module resolves *identity* only (a binding id to a path string); it
never itself invokes the resolved executable — the same "parsing/resolving
is not executing" split ``extractor_manifest.py`` documents for its own
command templates.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import yaml

#: The one supported schema discriminator. Mirrors
#: ``build_output.BUILD_OUTPUT_SCHEMA``'s naming convention.
BINDINGS_SCHEMA = "abicheck.toolchain-bindings/v1"


class BindingsFileError(ValueError):
    """A trusted bindings file is malformed, or a binding id doesn't resolve."""


@dataclass(frozen=True)
class BindingsFile:
    """A loaded, validated toolchain-bindings mapping.

    ``bindings`` maps a logical binding id (e.g. ``"gcc14"``) to the exact
    executable path an operator/CI environment resolved it to. Values are
    stored verbatim, never re-validated as shell-safe here — the same
    executable-identity probing every other tool path in this project goes
    through (``dumper_toolchain._resolved_tool``/``_tool_identity_metadata``)
    already rejects a non-regular-file/missing path at actual resolution
    time, so this loader does not duplicate that filesystem check against a
    path that may not even exist on *this* host (a bindings file can be
    authored once and shared across CI runners with different layouts).
    """

    schema: str
    bindings: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"schema": self.schema, "bindings": dict(self.bindings)}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> BindingsFile:
        if not isinstance(d, dict):
            raise BindingsFileError(
                f"bindings file must be a mapping, got {type(d).__name__}: {d!r}"
            )
        schema = d.get("schema")
        if schema != BINDINGS_SCHEMA:
            raise BindingsFileError(
                f"bindings file schema must be {BINDINGS_SCHEMA!r}, got {schema!r}"
            )
        unknown = set(d) - {"schema", "bindings"}
        if unknown:
            raise BindingsFileError(f"bindings file: unknown key(s) {sorted(unknown)}")
        raw_bindings = d.get("bindings", {})
        if not isinstance(raw_bindings, dict):
            raise BindingsFileError("bindings file: 'bindings' must be a mapping")
        bindings: dict[str, str] = {}
        for binding_id, raw_path in raw_bindings.items():
            if not isinstance(binding_id, str) or not binding_id.strip():
                raise BindingsFileError(
                    f"bindings file: binding id must be a non-empty string, "
                    f"got {binding_id!r}"
                )
            if not isinstance(raw_path, str) or not raw_path.strip():
                raise BindingsFileError(
                    f"bindings file: bindings.{binding_id} must be a non-empty "
                    f"string path, got {raw_path!r}"
                )
            bindings[binding_id] = raw_path
        return cls(schema=schema, bindings=bindings)


def load_bindings_file(path: Path | str) -> BindingsFile:
    """Load and validate *path* as a trusted toolchain-bindings file.

    Loads exactly the given path — the caller (an explicit ``--config``/
    ``--toolchain-bindings`` CLI flag or a managed CI environment) is the
    trust decision; nothing here searches for or auto-discovers a bindings
    file. Raises :class:`BindingsFileError` for a missing/unreadable file,
    malformed YAML, or a structurally invalid document (wrong schema,
    non-string binding id/path).
    """
    p = Path(path)
    try:
        raw_text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise BindingsFileError(f"cannot read bindings file {p}: {exc}") from exc
    try:
        raw = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        raise BindingsFileError(f"cannot parse bindings file {p}: {exc}") from exc
    if raw is None:
        raw = {}
    try:
        return BindingsFile.from_dict(raw)
    except BindingsFileError as exc:
        raise BindingsFileError(f"{p}: {exc}") from exc


def resolve_binding(bindings_file: BindingsFile, binding_id: str) -> str:
    """Resolve *binding_id* (a ``ProfileCompileSpec.binding`` value) to its
    exact executable path via *bindings_file*.

    Raises :class:`BindingsFileError` when *binding_id* has no entry —
    fail closed, matching the CastXML-version-gate/comparability-gate
    convention elsewhere in this project (an unresolvable profile axis is
    an explicit, actionable error, never a silent fallback to whatever
    happens to be on ``PATH``).
    """
    try:
        return bindings_file.bindings[binding_id]
    except KeyError:
        available = ", ".join(sorted(bindings_file.bindings)) or "(none declared)"
        raise BindingsFileError(
            f"toolchain binding {binding_id!r} is not declared in the bindings "
            f"file (available: {available})"
        ) from None


class _HasCompileBinding(Protocol):
    """Structural subset of ``project_targets.ProfileSpec`` this module
    needs — avoids importing that module (and its own YAML-config-parsing
    surface) just to type-check a duck-typed iterable of profiles."""

    id: str
    compile: Any
    consumer_compile: Any


def check_profile_bindings_resolve(
    profiles: Mapping[str, _HasCompileBinding], bindings_file: BindingsFile
) -> list[str]:
    """Return one human-readable error string per declared
    ``profiles.<id>.compile.binding``/``profiles.<id>.consumer_compile.binding``
    (G34 Phase 0) that doesn't resolve against *bindings_file* — empty when
    every declared binding resolves (including when no profile declares one
    at all).

    Never raises; a profile with no ``compile:``/``consumer_compile:``
    overlay or no ``binding`` field on either is silently skipped (nothing
    declared to resolve). Checked identically and independently for both
    overlays — an unresolved ``consumer_compile.binding`` is exactly as much
    a caller error as an unresolved ``compile.binding``: silently converting
    it into an empty path would let ``project plan`` succeed and emit
    consumer options without the explicitly requested compiler executable,
    indistinguishable from an intentionally unspecified consumer compiler
    (Codex review, fresh evidence).
    """
    errors: list[str] = []
    for profile in profiles.values():
        for key in ("compile", "consumer_compile"):
            compile_spec = getattr(profile, key, None)
            binding_id = getattr(compile_spec, "binding", "") if compile_spec else ""
            if not binding_id:
                continue
            if binding_id not in bindings_file.bindings:
                available = (
                    ", ".join(sorted(bindings_file.bindings)) or "(none declared)"
                )
                errors.append(
                    f"profiles.{profile.id}.{key}.binding {binding_id!r} is not "
                    f"declared in the bindings file (available: {available})"
                )
    return errors
