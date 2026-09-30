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
"""The extraction context a profile's baseline is dumped under (G41 Phase 1).

A candidate check cell dumps its operand under its profile's compile
context: the ``consumer_compile:`` overlay when the profile declares one
(check-target's consumer-context dump replaces the candidate snapshot), else
its producer ``compile:`` overlay. The baseline job used to dump every
profile under the bare ``build-config`` alone, so a profile with any compile
overlay produced an OLD snapshot the comparability gate then (correctly)
refused against the NEW one -- per-profile accepted baselines were not
operational for exactly the profiles that most need them.

:func:`resolve_baseline_extraction_context` derives the baseline side from
the *same* per-profile resolvers the run plan uses for the candidate
(``run_plan_profile_fields``), so the two cannot drift, and
:func:`write_baseline_build_config` folds it into the ``compile:`` block of
a copy of the project's build config -- the only channel ``dump`` accepts a
compile context through since per-run compiler flags were removed.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .._compiler_options import split_gcc_options
from ..action_config_overlay import rebase_relative_config_paths
from .project_targets import ProjectTargetsConfig
from .run_plan_profile_fields import (
    _compile_ast_frontend_for_profile,
    _compile_fields_for_profile,
    _consumer_compile_active_for_profile,
    _consumer_compile_ast_frontend_for_profile,
    _consumer_compile_fields_for_profile,
)

#: Which overlay the context came from.
CONTEXT_CONSUMER = "consumer"
CONTEXT_PRODUCER = "producer"
CONTEXT_DEFAULT = "default"  # the profile declares no compile overlay at all


@dataclass(frozen=True)
class BaselineExtractionContext:
    """One profile's resolved baseline compile context."""

    profile_id: str
    source: str
    gcc_path: str = ""
    gcc_options: str = ""
    ast_frontend: str = ""

    @property
    def is_default(self) -> bool:
        return not (self.gcc_path or self.gcc_options or self.ast_frontend)

    def to_dict(self) -> dict[str, str]:
        return {
            "profile": self.profile_id,
            "source": self.source,
            "gcc_path": self.gcc_path,
            "gcc_options": self.gcc_options,
            "ast_frontend": self.ast_frontend,
        }


def resolve_baseline_extraction_context(
    config: ProjectTargetsConfig,
    profile_id: str,
    resolved_bindings: Mapping[str, str] | None = None,
) -> BaselineExtractionContext:
    """The context a baseline for *profile_id* must be dumped under to be
    comparable with that profile's candidate cells.

    Consumer overlay when active (its fields only -- an unset consumer field
    does not fall back to the producer's, matching the candidate side),
    otherwise the producer ``compile:`` overlay.
    """
    if _consumer_compile_active_for_profile(config, profile_id):
        path, options = _consumer_compile_fields_for_profile(
            config, profile_id, resolved_bindings
        )
        frontend = _consumer_compile_ast_frontend_for_profile(config, profile_id)
        return BaselineExtractionContext(
            profile_id, CONTEXT_CONSUMER, path, options, frontend
        )
    path, options = _compile_fields_for_profile(config, profile_id, resolved_bindings)
    frontend = _compile_ast_frontend_for_profile(config, profile_id)
    ctx = BaselineExtractionContext(
        profile_id, CONTEXT_PRODUCER, path, options, frontend
    )
    if ctx.is_default:
        return BaselineExtractionContext(profile_id, CONTEXT_DEFAULT)
    return ctx


def compile_overlay(context: BaselineExtractionContext) -> dict[str, Any]:
    """The ``compile:`` keys *context* sets (empty for a default context).

    Same key mapping the root Action's compile-context synthesis applies to
    the candidate's ``gcc-path``/``gcc-options``/``ast-frontend`` inputs.
    """
    blk: dict[str, Any] = {}
    if context.ast_frontend and context.ast_frontend != "auto":
        blk["frontend"] = context.ast_frontend
    if context.gcc_path:
        blk["compiler"] = context.gcc_path
    if context.gcc_options:
        blk["options"] = split_gcc_options(context.gcc_options)
    return blk


def write_baseline_build_config(
    context: BaselineExtractionContext, base_config: Path | None, out: Path
) -> Path | None:
    """Write *base_config* with *context*'s compile overlay applied to *out*.

    Returns the path ``dump --config`` should read: *base_config* itself
    (possibly ``None``) when the context sets nothing, *out* otherwise.
    Overlay keys win over the base document's own ``compile:`` keys, the
    same precedence the candidate side gives its Action inputs; relative
    config paths are rebased so the relocated copy parses the same surface.
    """
    overlay = compile_overlay(context)
    if not overlay:
        return base_config
    base: dict[str, Any] = {}
    if base_config is not None:
        loaded = yaml.safe_load(base_config.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise ValueError(f"{base_config}: project config must be a mapping")
        base = rebase_relative_config_paths(loaded, found_path=base_config)
    compile_blk = dict(base.get("compile") or {})
    compile_blk.update(overlay)
    base["compile"] = compile_blk
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(base, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return out


def prepare_baseline_build_config(
    *,
    project_config: Path,
    profile_id: str,
    bindings_path: Path | None,
    build_config: Path | None,
    out: Path,
) -> tuple[BaselineExtractionContext, Path | None]:
    """The publish/update-baseline workflows' one call: load the project's
    targets/profiles config and (optional) toolchain bindings, resolve
    *profile_id*'s context, and write the ``--config`` document ``dump``
    must read. With no explicit *build_config*, the project's own
    discoverable config is the base for a non-default context -- the same document the candidate
    side's Action merges its overlay into -- so the overlay never silently
    replaces the project's other settings.

    Raises ``ValueError`` (via the loaders) for an unreadable config or an
    unresolvable declared binding: a baseline dumped under a silently
    different compiler would only be refused later as not comparable.
    """
    from ..config_paths import discover_project_config
    from .project_targets import load_project_targets_config
    from .toolchain_bindings import check_profile_bindings_resolve, load_bindings_file

    config = load_project_targets_config(project_config)
    resolved: Mapping[str, str] | None = None
    if bindings_path is not None:
        bindings = load_bindings_file(bindings_path)
        errors = check_profile_bindings_resolve(config.profiles, bindings)
        if errors:
            raise ValueError("; ".join(errors))
        resolved = bindings.bindings
    context = resolve_baseline_extraction_context(config, profile_id, resolved)
    if context.is_default:
        # Nothing to fold in: hand back exactly what the caller passed, so a
        # profile without overlays dumps precisely as it did before (an
        # explicitly-passed discovered config would newly enable build.query).
        return context, build_config
    base = build_config if build_config is not None else discover_project_config()
    return context, write_baseline_build_config(context, base, out)


def main(environ: Mapping[str, str] | None = None) -> int:
    """``python -m abicheck.buildsource.baseline_extraction_context``: the
    one step publish-baseline.yml and update-main-baseline.yml both run.

    Reads ``PROFILE_ID``, ``PROJECT_CONFIG`` (default: ``BUILD_CONFIG``,
    else ``.abicheck.yml``), ``BUILD_CONFIG``, ``BINDINGS_PATH`` and
    ``RUNNER_TEMP`` from the environment and appends ``build-config=`` and
    ``extraction-context=`` to ``$GITHUB_OUTPUT``. Exit 1 with an
    ``::error::`` annotation when the context cannot be resolved.
    """
    import os
    import sys

    env = os.environ if environ is None else environ
    build_config = env.get("BUILD_CONFIG") or None
    project_config = env.get("PROJECT_CONFIG") or build_config or ".abicheck.yml"
    bindings = env.get("BINDINGS_PATH") or None
    try:
        ctx, config_path = prepare_baseline_build_config(
            project_config=Path(project_config).resolve(),
            profile_id=env["PROFILE_ID"],
            bindings_path=Path(bindings).resolve() if bindings else None,
            build_config=Path(build_config).resolve() if build_config else None,
            out=Path(env.get("RUNNER_TEMP") or ".") / "abicheck-baseline-config.json",
        )
    except ValueError as exc:
        sys.stderr.write(
            f"::error::cannot resolve the baseline extraction context: {exc}\n"
        )
        return 1
    rendered = json.dumps(ctx.to_dict(), sort_keys=True)
    sys.stdout.write(f"baseline extraction context: {rendered}\n")
    with open(env["GITHUB_OUTPUT"], "a", encoding="utf-8") as fh:
        fh.write(f"build-config={config_path or ''}\n")
        fh.write(f"extraction-context={rendered}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
