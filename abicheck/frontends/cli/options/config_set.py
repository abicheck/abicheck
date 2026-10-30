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

from ....workflows.extraction import ConfigOverrideError, validate_config_overrides

__all__ = ["config_set_option", "parse_set_assignments"]


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
