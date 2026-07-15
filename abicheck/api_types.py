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

"""Typed request/response structs for the Tier-2 service layer (ADR-037 D2).

"Options are data, not signatures": the service verbs take frozen request
dataclasses instead of an ever-growing list of keyword arguments. A new feature
becomes a new field with a default, never a signature break — and the same
struct is assembled identically from CLI flags, MCP JSON, and direct Python
callers, so a default can no longer silently diverge between front-ends (the
``scope_public`` True-vs-False drift ADR-037 §Context #1 documents).

This module is Phase 1 of the G22 plan: it introduces :class:`InputSpec`,
:class:`CompareRequest` (with :meth:`CompareRequest.validate`), and
:class:`OutputSpec`. Later phases extend ``CompareRequest`` with the depth
(D5), policy/severity (D4), and frontend (D8) fields the ADR sketches — each as
an additive field with a default.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .errors import ValidationError

#: Languages the C/C++ frontends accept (mirrors the CLI ``--lang`` choices).
SUPPORTED_LANGS = frozenset({"c", "c++"})

#: AST frontends the ``--ast-frontend`` flag accepts (ADR-037 D8). ``auto`` /
#: ``castxml`` / ``clang`` drive header-AST parsing *and* L4 source-ABI replay;
#: ``android`` is a source-ABI-only value (it reuses a pre-captured header-abi
#: dump and has no header-AST path), so selecting it without source inputs is a
#: validation error (D9).
SUPPORTED_FRONTENDS = frozenset({"auto", "castxml", "clang", "android"})

#: The subset of :data:`SUPPORTED_FRONTENDS` valid for header-AST parsing.
HEADER_AST_FRONTENDS = frozenset({"auto", "castxml", "clang"})


def _path_tuple(paths: Iterable[Path | str] | None) -> tuple[Path, ...]:
    """Normalise an optional iterable of path-likes into a tuple of ``Path``.

    A bare ``str``/``Path`` is treated as a *single* path, not an iterable of
    characters/parts — so ``headers="include/api.h"`` yields one path, not one
    per character.
    """
    if paths is None:
        return ()
    if isinstance(paths, (str, Path)):
        return (Path(paths),)
    return tuple(Path(p) for p in paths)


@dataclass(frozen=True)
class InputSpec:
    """One side of a comparison: a binary/snapshot path plus its build context.

    Frozen so a request can be hashed/shared without a caller mutating it after
    validation. Use :meth:`of` to build one from loose CLI/MCP values (it
    coerces ``str`` to ``Path`` and ``None`` lists to empty tuples).
    """

    path: Path
    headers: tuple[Path, ...] = ()
    includes: tuple[Path, ...] = ()
    version: str = ""
    pdb: Path | None = None
    debug_roots: tuple[Path, ...] = ()

    @classmethod
    def of(
        cls,
        path: Path | str,
        *,
        headers: Iterable[Path | str] | None = None,
        includes: Iterable[Path | str] | None = None,
        version: str = "",
        pdb: Path | str | None = None,
        debug_roots: Iterable[Path | str] | None = None,
    ) -> InputSpec:
        """Build an :class:`InputSpec`, coercing loose front-end values."""
        return cls(
            path=Path(path),
            headers=_path_tuple(headers),
            includes=_path_tuple(includes),
            version=version,
            pdb=Path(pdb) if pdb is not None else None,
            debug_roots=_path_tuple(debug_roots),
        )


@dataclass(frozen=True)
class OutputSpec:
    """Where/how a result is rendered — the invocation-level output choice.

    ``path is None`` means "write to stdout". Kept deliberately small for
    Phase 1; the rendering verbs still take an explicit format today, but the
    struct gives later phases a single place to grow output options.
    """

    fmt: str = "text"
    path: Path | None = None


@dataclass(frozen=True)
class CompareRequest:
    """A fully-specified comparison request — the single input to ``run_compare``.

    Every front-end (CLI, MCP, ``compare-release`` fan-out, ``appcompat``)
    assembles one of these and hands it to :func:`abicheck.service.run_compare`,
    so there is exactly one classification path and one set of defaults.
    """

    old: InputSpec
    new: InputSpec
    lang: str = "c++"
    frontend: str = "auto"
    has_sources: bool = False
    policy: str = "strict_abi"
    policy_file_path: Path | None = None
    suppress: Path | None = None
    scope_public: bool = True
    force_public_symbols: frozenset[str] | None = None
    # `compare --post-manifest`: the committed `pp_*`/ufunc-loop surface of a POST
    # manifest. When set, the comparison is scoped to this set — export findings
    # outside it (e.g. private `__pp_*` kernel churn) are demoted. None = not
    # manifest-scoped.
    public_surface_allowlist: frozenset[str] | None = None
    pattern_verdicts: bool = False
    enable_debuginfod: bool = False
    # Override debuginfod server URL (only meaningful with enable_debuginfod);
    # None uses the resolver's default server list / DEBUGINFOD_URLS env var.
    debuginfod_url: str | None = None
    # ADR-039: clear context-free header-parse false positives using the build's
    # active preprocessor defines (a conditional field's phantom add/remove/size
    # delta the build proves never changed). Opt-in; a no-op unless the snapshots
    # carry ``build_context_defines`` + per-field ``guard`` annotations.
    reconcile_build_context: bool = False
    # ADR-020b: declared deployment constraints (EnvironmentMatrix YAML). When
    # its ``runtime_floors`` are set, new symbol-version requirements classify
    # against the declared floors (≤ floor → COMPATIBLE, > floor → BREAKING)
    # instead of the default deployment-RISK verdict.
    env_matrix_path: Path | None = None

    def validation_errors(self) -> list[str]:
        """Return a list of human-readable validation problems (empty == valid).

        Lives here (Tier 2) so the CLI and MCP front-ends surface *identical*
        error text for the same bad request (ADR-037 D9 / goal AC 8): value
        validation (language / AST frontend enums) and the cross-flag
        feasibility rules (an ``android`` frontend has no header-AST path, so it
        needs source inputs).
        """
        errors: list[str] = []
        if self.lang.lower() not in SUPPORTED_LANGS:
            allowed = ", ".join(sorted(SUPPORTED_LANGS))
            errors.append(f"unsupported language {self.lang!r}: choose from {allowed}")
        frontend = self.frontend.lower()
        if frontend not in SUPPORTED_FRONTENDS:
            allowed = ", ".join(sorted(SUPPORTED_FRONTENDS))
            errors.append(
                f"unsupported AST frontend {self.frontend!r}: choose from {allowed}"
            )
        elif frontend == "android" and not self.has_sources:
            # D8/D9: 'android' reuses a pre-captured header-abi dump; it has no
            # header-AST path, so a header-only run can't use it.
            errors.append(
                "the 'android' AST frontend is source-ABI only (it has no "
                "header-AST path); supply source inputs (--sources) to use it"
            )
        if not self.policy:
            errors.append("policy profile name must not be empty")
        # D9 pre-flight: a --policy-file path that doesn't exist is a hard error
        # here (Tier 2), so CLI and MCP surface the same message before any work.
        if self.policy_file_path is not None and not Path(self.policy_file_path).exists():
            errors.append(f"policy file not found: {self.policy_file_path}")
        if self.env_matrix_path is not None and not Path(self.env_matrix_path).exists():
            errors.append(f"environment matrix file not found: {self.env_matrix_path}")
        return errors

    def validate(self) -> CompareRequest:
        """Validate fail-fast; raise :class:`ValidationError` on the first batch.

        Returns ``self`` so callers can write ``request.validate()`` inline.
        """
        errors = self.validation_errors()
        if errors:
            raise ValidationError("; ".join(errors))
        return self

    def replace(self, **changes: Any) -> CompareRequest:
        """Return a copy with *changes* applied (frozen-dataclass ``replace``)."""
        return replace(self, **changes)


__all__ = [
    "HEADER_AST_FRONTENDS",
    "SUPPORTED_FRONTENDS",
    "SUPPORTED_LANGS",
    "CompareRequest",
    "InputSpec",
    "OutputSpec",
]
