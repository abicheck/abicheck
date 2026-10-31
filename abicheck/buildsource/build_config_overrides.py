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

"""``config_overrides``: one config key set from outside ``.abicheck.yml``.

ADR-091 D4's ``--set PATH=VALUE`` (CLI) and ``config_overrides`` (typed API)
land here. An override is a mapping of dotted ``.abicheck.yml`` path to a
YAML-typed value. It is validated against the same :data:`KEY_TABLE` the file
is, merged over the raw file mapping, and then the merged mapping goes through
the one :meth:`BuildConfig.from_dict` strict load. There is no second parser:
an override that the file could not express is rejected by the file's own
schema.

Precedence is the caller's to apply: an explicit CLI flag or typed argument
still beats an override, because it is applied after the config is built
(``resolve_compare_config``). An override beats the file, which beats the
built-in default.

Core options are excluded: a concept with a visible flag (ADR-091 D4's core
tier) keeps exactly one spelling, so :data:`CORE_CONFIG_FLAGS` names the flag
that must be used instead.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .build_config_schema import KeyTable

__all__ = [
    "CORE_CONFIG_FLAGS",
    "ConfigOverrideError",
    "apply_config_overrides",
    "validated_config_overrides",
]

#: The config paths whose concept has a visible CLI flag (ADR-091 D4 core
#: tier), mapped to that flag. ``--set`` and ``config_overrides`` refuse these
#: so each core concept keeps one spelling. ``tests/test_config_overrides.py``
#: checks the keys against ``architecture/option-vocabulary.yaml``.
CORE_CONFIG_FLAGS: dict[str, str] = {
    "compile.include_dirs": "--include",
    "compile.defines": "--define",
    "debug.pdb_path": "--debug-info",
    "build.compile_db": "--build-info",
    "scope.exclude_headers": "--exclude-header",
    "contract": "--contract",
    "acknowledgment": "--acknowledgments",
    "severity.preset": "--severity-preset",
}


class ConfigOverrideError(ValueError):
    """An override names no config key, a core option, or a wrong-typed value."""


def validated_config_overrides(
    overrides: Mapping[str, object], table: KeyTable
) -> dict[str, Any]:
    """*overrides* as a nested ``.abicheck.yml`` mapping, or :class:`ConfigOverrideError`.

    Every path must be a :data:`KEY_TABLE` row and not a core concept; every
    value must satisfy that row's type, reported through the table's own
    findings so the message names the key the same way a file error does.
    """
    nested: dict[str, Any] = {}
    for path, value in overrides.items():
        if not isinstance(path, str) or not table.has_path(path):
            raise ConfigOverrideError(
                f"unknown config key {path!r} in --set/config_overrides"
            )
        flag = CORE_CONFIG_FLAGS.get(path)
        if flag is not None:
            raise ConfigOverrideError(
                f"{path!r} is a core option with its own flag; use {flag} instead of a config override"
            )
        block, _, sub = path.partition(".")
        if sub:
            nested.setdefault(block, {})[sub] = value
        else:
            nested[block] = value
    findings = table.findings(nested)
    if findings:
        raise ConfigOverrideError("; ".join(findings))
    return nested


def apply_config_overrides(
    raw: dict[str, Any] | None, overrides: Mapping[str, object] | None, table: KeyTable
) -> dict[str, Any]:
    """The raw ``.abicheck.yml`` mapping *raw* with *overrides* laid over it.

    *raw* is ``None`` or ``{}`` when there is no file. Overrides win leaf by
    leaf inside a block; a list-valued override replaces the file's list
    rather than extending it, so the run's value is exactly what was stated.
    """
    merged: dict[str, Any] = dict(raw or {})
    if not overrides:
        return merged
    for key, value in validated_config_overrides(overrides, table).items():
        current = merged.get(key)
        if isinstance(value, dict) and isinstance(current, dict):
            merged[key] = {**current, **value}
        else:
            merged[key] = value
    return merged
