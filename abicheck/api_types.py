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


def _path_tuple(paths: Iterable[Path | str] | None) -> tuple[Path, ...]:
    """Normalise an optional iterable of path-likes into a tuple of ``Path``."""
    if not paths:
        return ()
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
    policy: str = "strict_abi"
    policy_file_path: Path | None = None
    suppress: Path | None = None
    scope_public: bool = True
    force_public_symbols: frozenset[str] | None = None
    pattern_verdicts: bool = False
    enable_debuginfod: bool = False

    def validation_errors(self) -> list[str]:
        """Return a list of human-readable validation problems (empty == valid).

        Lives here (Tier 2) so the CLI and MCP front-ends surface *identical*
        error text for the same bad request (ADR-037 D9 / goal AC 8). Phase 6
        extends this with mutual-exclusion and depth-feasibility rules.
        """
        errors: list[str] = []
        if self.lang.lower() not in SUPPORTED_LANGS:
            allowed = ", ".join(sorted(SUPPORTED_LANGS))
            errors.append(f"unsupported language {self.lang!r}: choose from {allowed}")
        if not self.policy:
            errors.append("policy profile name must not be empty")
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
    "SUPPORTED_LANGS",
    "CompareRequest",
    "InputSpec",
    "OutputSpec",
]
