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

"""Config homes for run inputs that keep their flags (ADR-091 D4, 2026-10-31 amendment).

``debug.info`` (``--debug-info``), ``deps.follow`` (``--follow-deps``),
``deps.search_paths`` (``--search-path``) and ``deps.ld_library_path``
(``--ld-library-path``) stay flags, because they are per-side or per-run. A
project whose values do not change between runs can also state them in
``.abicheck.yml``. This module is the one place that turns the resolved config
into the values the flags carry, so ``compare``, ``dump`` and the typed
requests cannot read them differently.

``debug.info`` and ``deps.follow`` belong to core concepts, so ``--set`` and
the typed ``config_overrides`` refuse them like every other core key
(``CORE_CONFIG_FLAGS``): the flag is their per-run spelling. The two non-core
keys, :data:`CONFIG_RUN_INPUT_SET_PATHS`, are also reached by ``--set`` and
``config_overrides``.

Precedence is the one ADR-091 D2 rule: an explicit flag or typed argument, then
``--set``/``config_overrides`` (non-core keys), then the file, then the
built-in default. ``--follow-deps`` is a boolean pair: ``--no-follow-deps``
turns a configured ``deps.follow: true`` off for one run, and neither half
defers to the key. The flag replaces the configured value; the two are never
merged, because the value names the run's evidence and a merged set would
differ from what the command line says. A relative path written in the config
file resolves against that file's directory; one given by ``--set`` or the
typed ``config_overrides`` is used as given, relative to the current directory.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..buildsource.build_config_schema import parse_debug_info_entry

__all__ = [
    "CONFIG_RUN_INPUT_SET_PATHS",
    "DepsSettings",
    "config_debug_info",
    "config_relative_path",
    "configured_run_input_keys",
    "dump_debug_roots",
    "fold_deps_overrides",
    "resolve_debug_info",
    "resolve_deps_settings",
]

#: The non-core keys this module resolves: ``compare --set``, ``dump --set`` and
#: the typed ``config_overrides`` of both requests apply them.
CONFIG_RUN_INPUT_SET_PATHS: frozenset[str] = frozenset(
    {"deps.search_paths", "deps.ld_library_path"}
)


def config_relative_path(item: str, cfg_path: Path | None) -> Path:
    """*item* as a path, relative to the config file that names it."""
    path = Path(item)
    if path.is_absolute() or cfg_path is None:
        return path
    return cfg_path.parent / path


def config_debug_info(cfg: Any, cfg_path: Path | None) -> tuple[tuple[str, Path], ...]:
    """The configured ``debug.info`` entries as ``--debug-info`` values."""
    if cfg is None:
        return ()
    out: list[tuple[str, Path]] = []
    for entry in getattr(cfg, "debug_info", None) or ():
        side, raw = parse_debug_info_entry(str(entry))
        out.append((side, config_relative_path(raw, cfg_path)))
    return tuple(out)


def resolve_debug_info(
    explicit: Iterable[tuple[str, Path]], cfg: Any, cfg_path: Path | None
) -> tuple[tuple[str, Path], ...]:
    """The run's ``--debug-info`` values: the flag's when stated, else the config's."""
    stated = tuple(explicit)
    if stated:
        return stated
    return config_debug_info(cfg, cfg_path)


@dataclass(frozen=True)
class DepsSettings:
    """The dependency walk's three inputs after precedence."""

    #: ``None`` only on a *stated* settings value: ``--follow-deps`` and
    #: ``--no-follow-deps`` were both omitted, so ``deps.follow`` decides.
    #: :func:`resolve_deps_settings` always returns a ``bool``.
    follow: bool | None = False
    search_paths: tuple[Path, ...] = ()
    ld_library_path: str = ""


def resolve_deps_settings(
    cfg: Any,
    cfg_path: Path | None,
    *,
    follow: bool | None,
    search_paths: Iterable[Path],
    ld_library_path: str,
    overridden: Iterable[str] = (),
) -> DepsSettings:
    """The dependency walk's inputs: each stated value, else its config key.

    A ``deps.search_paths`` value written in the file resolves against
    *cfg_path*'s directory; one set by ``--set``/``config_overrides`` (named in
    *overridden*) is used as given, relative to the current directory.

    *follow* is ``True``/``False`` when ``--follow-deps``/``--no-follow-deps``
    was given (either replaces ``deps.follow``) and ``None`` when neither was,
    which defers to ``deps.follow`` and then to the built-in ``False``.
    """
    stated_paths = tuple(Path(p) for p in search_paths)
    if cfg is None:
        return DepsSettings(bool(follow), stated_paths, ld_library_path or "")
    cfg_follow = getattr(cfg, "deps_follow", None)
    base = None if "deps.search_paths" in set(overridden) else cfg_path
    cfg_paths = tuple(
        config_relative_path(str(item), base)
        for item in (getattr(cfg, "deps_search_paths", None) or ())
    )
    cfg_ld = getattr(cfg, "deps_ld_library_path", None) or ""
    return DepsSettings(
        follow=bool(cfg_follow) if follow is None else bool(follow),
        search_paths=stated_paths or cfg_paths,
        ld_library_path=ld_library_path or cfg_ld,
    )


def configured_run_input_keys(
    cfg: Any, *, follow: bool | None = None
) -> tuple[str, ...]:
    """The ``debug.info``/``deps.*`` keys *cfg* sets, in a stable order.

    A route that cannot honour them (``--no-baseline``, a stored BundleFacts
    operand) refuses a config that sets them, by name, as it refuses the flags.
    No ``deps.*`` key is listed when *follow* is ``False``: ``--no-follow-deps``
    turned the configured walk off for this run, and the two search keys are
    inputs to that walk only, so the route drops nothing it was asked to do.
    """
    if cfg is None:
        return ()
    keys: list[str] = []
    if getattr(cfg, "debug_info", None):
        keys.append("debug.info")
    if follow is False:
        return tuple(keys)
    if getattr(cfg, "deps_follow", None):
        keys.append("deps.follow")
    if getattr(cfg, "deps_search_paths", None):
        keys.append("deps.search_paths")
    if getattr(cfg, "deps_ld_library_path", None):
        keys.append("deps.ld_library_path")
    return tuple(keys)


def fold_deps_overrides(request: Any, cfg: Any) -> dict[str, Any]:
    """The ``CompareRequest``/``DumpRequest`` updates its ``deps.*`` overrides make.

    A field the caller set away from its default is kept: the typed argument
    beats an override, as the flag beats ``--set``.
    """
    if not set(request.config_overrides or ()) & CONFIG_RUN_INPUT_SET_PATHS:
        return {}
    deps = resolve_deps_settings(
        cfg,
        None,
        follow=request.follow_deps,
        search_paths=request.dependency_search_paths,
        ld_library_path=request.ld_library_path,
    )
    updates: dict[str, Any] = {}
    if deps.search_paths != tuple(request.dependency_search_paths):
        updates["dependency_search_paths"] = deps.search_paths
    if deps.ld_library_path != request.ld_library_path:
        updates["ld_library_path"] = deps.ld_library_path
    return updates


def dump_debug_roots(values: Iterable[tuple[str, Path]]) -> tuple[Path, ...]:
    """``debug.info`` for ``dump``'s one operand: unscoped entries only.

    Raises :class:`ValueError` naming the entry when one is scoped to a side.
    """
    pairs = tuple(values)
    sided = [f"{side}={p}" for side, p in pairs if side in ("old", "new")]
    if sided:
        raise ValueError(
            f"debug.info: {', '.join(sided)} is scoped to one side of a "
            "comparison, and `dump` has one operand; pass --debug-info for "
            "this dump, or list the path without a side"
        )
    return tuple(p for _side, p in pairs)
