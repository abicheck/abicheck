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
``.abicheck.yml`` or set them for one run with ``--set``. This module is the one
place that turns the resolved config into the values the flags carry, so the
CLI (``compare``, ``dump``) and the typed requests cannot read them differently.

Precedence is the one ADR-091 D2 rule: an explicit flag or typed argument, then
``--set``/``config_overrides``, then the file, then the built-in default. The
flag replaces the configured value; the two are never merged, because the value
names the run's evidence and a merged set would differ from what the command
line says. A relative path in the config resolves against the directory of the
config file that names it (the ``consumers.manifests`` rule); with no file (a
bare ``--set``, or the typed API) it is used as given.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..buildsource.build_config_schema import parse_debug_info_entry

__all__ = [
    "CONFIG_RUN_INPUT_PATHS",
    "DepsSettings",
    "config_debug_info",
    "config_relative_path",
    "configured_run_input_keys",
    "resolve_debug_info",
    "dump_debug_roots",
    "fold_compare_run_inputs",
    "fold_dump_run_inputs",
    "resolve_deps_settings",
    "sided_debug_roots",
    "typed_debug_info",
]

#: The config paths this module resolves. Each is applied by ``compare --set``,
#: ``dump --set`` and the typed ``config_overrides`` of both requests.
CONFIG_RUN_INPUT_PATHS: frozenset[str] = frozenset(
    {"debug.info", "deps.follow", "deps.search_paths", "deps.ld_library_path"}
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

    follow: bool = False
    search_paths: tuple[Path, ...] = ()
    ld_library_path: str = ""


def resolve_deps_settings(
    cfg: Any,
    cfg_path: Path | None,
    *,
    follow: bool,
    search_paths: Iterable[Path],
    ld_library_path: str,
) -> DepsSettings:
    """The dependency walk's inputs: each stated value, else its config key.

    ``--follow-deps`` is an on-only flag, so ``deps.follow: true`` is turned off
    for one run with ``--set deps.follow=false``, not with a flag.
    """
    stated_paths = tuple(Path(p) for p in search_paths)
    if cfg is None:
        return DepsSettings(bool(follow), stated_paths, ld_library_path or "")
    cfg_follow = getattr(cfg, "deps_follow", None)
    cfg_paths = tuple(
        config_relative_path(str(item), cfg_path)
        for item in (getattr(cfg, "deps_search_paths", None) or ())
    )
    cfg_ld = getattr(cfg, "deps_ld_library_path", None) or ""
    return DepsSettings(
        follow=bool(follow) or bool(cfg_follow),
        search_paths=stated_paths or cfg_paths,
        ld_library_path=ld_library_path or cfg_ld,
    )


def configured_run_input_keys(cfg: Any) -> tuple[str, ...]:
    """The :data:`CONFIG_RUN_INPUT_PATHS` keys *cfg* sets, in a stable order.

    A route that cannot honour them (``--no-baseline``, a stored BundleFacts
    operand) refuses a config that sets them, by name, as it refuses the flags.
    """
    if cfg is None:
        return ()
    keys: list[str] = []
    if getattr(cfg, "debug_info", None):
        keys.append("debug.info")
    if getattr(cfg, "deps_follow", None):
        keys.append("deps.follow")
    if getattr(cfg, "deps_search_paths", None):
        keys.append("deps.search_paths")
    if getattr(cfg, "deps_ld_library_path", None):
        keys.append("deps.ld_library_path")
    return tuple(keys)


def sided_debug_roots(
    values: Iterable[tuple[str, Path]],
) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    """``(old, new)`` debug roots: a side's own entries replace the shared ones.

    The rule ``compare --debug-info`` applies to a directory or detached file
    (``cli_compare_options._resolve_debug_roots``).
    """
    pairs = tuple(values)
    both = tuple(p for side, p in pairs if side == "both")
    old = tuple(p for side, p in pairs if side == "old")
    new = tuple(p for side, p in pairs if side == "new")
    return (old or both), (new or both)


def typed_debug_info(cfg: Any) -> tuple[tuple[str, Path], ...]:
    """``debug.info`` for a typed request, which names no config file.

    A typed request's ``InputSpec.debug_roots`` carries a directory or a detached
    debug file; a debug package is the CLI's directory/package transport, so it
    is refused here rather than searched as a directory and silently not found.
    """
    from ..buildsource.build_config_overrides import ConfigOverrideError
    from .evidence_transport import DebugTransport, classify_debug_transport

    values = config_debug_info(cfg, None)
    packages = [
        str(p)
        for _side, p in values
        if p.exists() and classify_debug_transport(p) is DebugTransport.PACKAGE
    ]
    if packages:
        raise ConfigOverrideError(
            f"debug.info: {', '.join(packages)} is a debug package, which a typed "
            "request has no field for; extract it and name the directory, or "
            "use `compare --debug-info` on the release directories/packages"
        )
    return values


def fold_compare_run_inputs(request: Any, cfg: Any) -> dict[str, Any]:
    """The ``CompareRequest`` updates ``debug.info``/``deps.*`` overrides make.

    A field the caller set away from its default is kept: the typed argument
    beats an override, as the flag beats ``--set``.
    """
    import dataclasses

    keys = set(request.config_overrides or ())
    updates: dict[str, Any] = {}
    deps = resolve_deps_settings(
        cfg
        if keys & {"deps.follow", "deps.search_paths", "deps.ld_library_path"}
        else None,
        None,
        follow=request.follow_deps,
        search_paths=request.dependency_search_paths,
        ld_library_path=request.ld_library_path,
    )
    if deps.follow != request.follow_deps:
        updates["follow_deps"] = deps.follow
    if deps.search_paths != tuple(request.dependency_search_paths):
        updates["dependency_search_paths"] = deps.search_paths
    if deps.ld_library_path != request.ld_library_path:
        updates["ld_library_path"] = deps.ld_library_path
    if "debug.info" in keys:
        old_roots, new_roots = sided_debug_roots(typed_debug_info(cfg))
        if old_roots and not request.old.debug_roots:
            updates["old"] = dataclasses.replace(request.old, debug_roots=old_roots)
        if new_roots and not request.new.debug_roots:
            updates["new"] = dataclasses.replace(request.new, debug_roots=new_roots)
    return updates


def fold_dump_run_inputs(
    request: Any, cfg: Any
) -> tuple[dict[str, Any], dict[str, Any]]:
    """``(request updates, input updates)`` for a ``DumpRequest``'s overrides.

    ``dump`` has one operand, so a ``debug.info`` entry scoped to ``old=`` or
    ``new=`` is refused rather than applied to the wrong side or dropped.
    """
    keys = set(request.config_overrides or ())
    request_updates: dict[str, Any] = {}
    input_updates: dict[str, Any] = {}
    deps = resolve_deps_settings(
        cfg
        if keys & {"deps.follow", "deps.search_paths", "deps.ld_library_path"}
        else None,
        None,
        follow=request.follow_deps,
        search_paths=request.dependency_search_paths,
        ld_library_path=request.ld_library_path,
    )
    if deps.follow != request.follow_deps:
        request_updates["follow_deps"] = deps.follow
    if deps.search_paths != tuple(request.dependency_search_paths):
        request_updates["dependency_search_paths"] = deps.search_paths
    if deps.ld_library_path != request.ld_library_path:
        request_updates["ld_library_path"] = deps.ld_library_path
    if "debug.info" in keys and not request.input.debug_roots:
        from ..buildsource.build_config_overrides import ConfigOverrideError

        try:
            roots = dump_debug_roots(typed_debug_info(cfg))
        except ConfigOverrideError:
            raise
        except ValueError as exc:
            raise ConfigOverrideError(str(exc)) from exc
        if roots:
            input_updates["debug_roots"] = roots
    return request_updates, input_updates


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
