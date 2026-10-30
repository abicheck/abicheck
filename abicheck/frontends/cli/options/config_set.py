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

"""``--set PATH=VALUE``: the CLI spelling of ``config_overrides`` (ADR-091 D4).

The option only turns text into the typed mapping ``{dotted path: value}``.
The mapping is then validated and applied by
:mod:`abicheck.buildsource.build_config_overrides`, the same code the typed
API runs, so the two cannot accept different things. ``VALUE`` is read as a
YAML scalar or flow value, so ``true``, ``3`` and ``[a, b]`` have the types a
file would give them.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import click
import yaml

from ....workflows.compare_configuration import (
    CLI_ONLY_OVERRIDE_PATHS,
    TYPED_OVERRIDE_CHANNELS,
)
from ....workflows.extraction import ConfigOverrideError, validate_config_overrides

#: The keys `compare --set` applies: the typed channels plus the CLI-only ones.
#: Every other key (compile.*, build.*, sources.*, ...) is refused with a usage
#: error, because its loader does not yet receive the override.
_COMPARE_APPLIED_PATHS: frozenset[str] = frozenset(
    {path for path, _field, _attr in TYPED_OVERRIDE_CHANNELS} | CLI_ONLY_OVERRIDE_PATHS
)

#: The keys a directory/package (release) compare forwards to its members. The
#: release request carries no public-symbol, suppression, assurance or debug
#: field, so every other ``--set`` key is refused on that route rather than
#: accepted and not applied.
_RELEASE_APPLIED_PATHS: frozenset[str] = frozenset(
    {
        "scope.public",
        "scope.collapse_versioned_symbols",
        "severity.abi_breaking",
        "severity.potential_breaking",
        "severity.quality_issues",
        "severity.addition",
        "deployment",
    }
)

__all__ = [
    "config_set_option",
    "parse_set_assignments",
    "reject_unforwarded_release_overrides",
]


def reject_unforwarded_release_overrides(overrides: dict[str, object]) -> None:
    """Refuse a ``--set`` key a directory/package comparison does not forward.

    Called only for a release route (an operand that is a directory or package).
    """
    unforwarded = sorted(set(overrides) - _RELEASE_APPLIED_PATHS)
    if unforwarded:
        raise click.UsageError(
            f"--set: a directory/package comparison does not apply "
            f"{', '.join(unforwarded)} yet; set it in .abicheck.yml or compare "
            "the libraries one at a time"
        )


def parse_set_assignments(values: tuple[str, ...]) -> dict[str, object]:
    """``("PATH=VALUE", ...)`` as ``{PATH: value}``; a later repeat wins.

    Raises :class:`click.UsageError` (exit 64) for a malformed assignment, a
    value that is not YAML, an unknown path, a core option, or a wrong type.
    """
    overrides: dict[str, object] = {}
    for item in values:
        path, sep, raw = item.partition("=")
        path = path.strip()
        if not sep or not path:
            raise click.UsageError(f"--set expects PATH=VALUE, got {item!r}")
        try:
            overrides[path] = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            raise click.UsageError(
                f"--set {path}: value {raw!r} is not valid YAML ({exc})"
            ) from None
    if overrides:
        try:
            validate_config_overrides(overrides)
        except ConfigOverrideError as exc:
            raise click.UsageError(f"--set: {exc}") from None
        unapplied = sorted(set(overrides) - _COMPARE_APPLIED_PATHS)
        if unapplied:
            raise click.UsageError(
                f"--set: compare does not apply {', '.join(unapplied)} yet "
                "(a key outside its resolved config); set it in .abicheck.yml"
            )
    return overrides


def _parse_set_callback(
    _ctx: click.Context, _param: click.Parameter, value: tuple[str, ...]
) -> dict[str, Any]:
    return parse_set_assignments(value)


config_set_option: Callable[[Any], Any] = click.option(
    "--set",
    "config_overrides",
    multiple=True,
    callback=_parse_set_callback,
    metavar="PATH=VALUE",
    help="Set one .abicheck.yml key for this run, e.g. "
    "--set compile.std=c++17 or --set scope.show_redundant=true. "
    "Repeatable. PATH is the config path; VALUE is read as YAML, so "
    "--set compile.include_dirs=[include] is a list. Overrides the "
    ".abicheck.yml file; an explicit flag still wins over both. A core "
    "option with its own flag is refused and names that flag.",
)
