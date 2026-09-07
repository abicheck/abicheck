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

"""ADR-066 D4/S2: ``policy_file.py``'s ``versioning:`` YAML block parser.

A sibling of ``policy_file.py`` rather than an addition to it: that module
carries a ``no_growth`` architecture-debt baseline (`architecture/debt.yaml`),
and per this repo's own convention ("the way to shrink an entry is to move
responsibility out to a properly-owned module, never to trim the file to
fit" — root `AGENTS.md`), a genuinely new parsing responsibility gets its
own leaf module instead of growing the capped one. `policy_file.py` calls
:func:`parse_versioning_policy` from its own `load()`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .errors import PolicyError
from .policy.versioning_policy import (
    CompatibilityPromise,
    DeprecationWindow,
    SupportWindow,
    VersioningEnforcement,
    VersioningPolicy,
    VersioningScheme,
    built_in_default_versioning_policy,
)


def _parse_support_window(raw: Any, path: Path) -> SupportWindow:
    """Validate and parse the ``versioning.support_window`` sub-block."""
    if raw is None:
        return SupportWindow()
    if not isinstance(raw, dict):
        raise PolicyError(
            f"'versioning.support_window' must be a YAML mapping in {path}, "
            f"got {type(raw).__name__}"
        )
    versions = raw.get("versions", ())
    try:
        return SupportWindow(
            kind=raw.get("kind", "none"),
            last_n=raw.get("last_n"),
            line=raw.get("line"),
            versions=tuple(versions) if versions else (),
        )
    except (TypeError, ValueError) as exc:
        raise PolicyError(f"versioning.support_window in {path}: {exc}") from exc


def _parse_deprecation_window(raw: Any, path: Path) -> DeprecationWindow:
    """Validate and parse the ``versioning.deprecation_window`` sub-block."""
    if raw is None:
        return DeprecationWindow()
    if not isinstance(raw, dict):
        raise PolicyError(
            f"'versioning.deprecation_window' must be a YAML mapping in "
            f"{path}, got {type(raw).__name__}"
        )
    try:
        return DeprecationWindow(min_releases=raw.get("min_releases", 0))
    except (TypeError, ValueError) as exc:
        raise PolicyError(f"versioning.deprecation_window in {path}: {exc}") from exc


def parse_versioning_policy(raw: Any, path: Path) -> VersioningPolicy:
    """Validate and parse the ``versioning:`` namespace (ADR-066 D4/S2).

    Every field is optional; an absent key falls back to the built-in
    default for that control -- matching
    :func:`~abicheck.policy.versioning_policy.built_in_default_versioning_policy`
    so a document that carries no ``versioning:`` key at all resolves
    identically to one that spells out every default explicitly.
    """
    if not isinstance(raw, dict):
        raise PolicyError(
            f"'versioning' must be a YAML mapping in {path}, got " + type(raw).__name__
        )
    default = built_in_default_versioning_policy()
    scheme_raw = raw.get("scheme")
    promise_raw = raw.get("promise")
    enforcement_raw = raw.get("enforcement")
    try:
        scheme = (
            VersioningScheme(scheme_raw) if scheme_raw is not None else default.scheme
        )
        promise = (
            CompatibilityPromise(promise_raw)
            if promise_raw is not None
            else default.promise
        )
        enforcement = (
            VersioningEnforcement(enforcement_raw)
            if enforcement_raw is not None
            else default.enforcement
        )
    except ValueError as exc:
        raise PolicyError(f"versioning in {path}: {exc}") from exc
    return VersioningPolicy(
        scheme=scheme,
        promise=promise,
        support_window=_parse_support_window(raw.get("support_window"), path),
        deprecation_window=_parse_deprecation_window(
            raw.get("deprecation_window"), path
        ),
        enforcement=enforcement,
    )
