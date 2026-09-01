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

"""Per-profile ``RunPlanCheck`` field resolution -- split out of
``run_plan.py`` (a debt.yaml ``no_growth``-tracked module) purely to keep
that file under its recorded baseline; a mechanical extraction, not a
redesign (unchanged function bodies). ``run_plan.py`` imports every name
here back into its own namespace, so every existing
``from abicheck.buildsource.run_plan import _compose_gcc_options, ...``
call site (including this package's own tests) is unaffected.

Covers the P1 toolchain-profile audit's ``compile``/``consumer_compile``
overlay projection (``_compose_gcc_options``, ``_resolved_compile_fields``,
``_compile_fields_for_profile``, ``_consumer_compile_fields_for_profile``),
G34 Phase B's per-profile AST-frontend override (``_compile_ast_frontend_
for_profile``, ``_consumer_compile_ast_frontend_for_profile``), G34 Phase
0's consumer-overlay-active marker (``_consumer_compile_active_for_
profile``), and G34 Phase C's per-profile runner/dependency-source
resolution (``_scheduling_fields_for_profile``) -- one cohesive "resolve
this profile's own RunPlanCheck fields" cluster.
"""

from __future__ import annotations

from collections.abc import Mapping

from .project_targets import (
    DEFAULT_PROFILE_RUNNER_LABEL,
    ProfileCompileSpec,
    ProjectTargetsConfig,
    runner_label_for_os,
    unroutable_os_message,
)


def _compose_gcc_options(compile_spec: ProfileCompileSpec) -> str:
    """Compose ``compile_spec``'s standard/stdlib/target/abi_macros/args axes
    into one space-joined extra-flags string, forwarded verbatim as
    ``check-target``'s ``gcc-options`` input (P1 toolchain-profile audit).

    Every atom was already whitespace-validated by
    ``ProfileCompileSpec.from_dict`` (``_safe_profile_atom`` -- no argv
    smuggling), so plain space-joining is safe here. A consequence of that
    validation: no atom -- including an ``abi_macros`` value -- may itself
    contain a space, since this function has no further escaping step to
    fall back on. ``abi_macros`` are emitted sorted by name for
    deterministic output; ``args`` are appended verbatim, in declared
    order, last -- the operator's own explicit escape hatch wins over the
    structured axes this function derives flags from.

    **Deliberately not family-aware.** A P0 audit round had this function
    drop ``-stdlib=``/``--target=`` for ``compiler_family: gcc``; a later
    round found the real consumer here is always Clang, never a literal
    GCC binary, and dropping ``--target=`` broke direct-clang cross-
    compilation correctness. Reverted; both flags are emitted
    unconditionally regardless of ``compiler_family``. See AGENTS.md's
    "Toolchain-profile compiler-family rendering" entry for the full
    account -- not a per-flag heuristic to re-derive here.
    """
    parts: list[str] = []
    if compile_spec.standard:
        parts.append(f"-std={compile_spec.standard}")
    if compile_spec.stdlib:
        parts.append(f"-stdlib={compile_spec.stdlib}")
    if compile_spec.target:
        parts.append(f"--target={compile_spec.target}")
    for name in sorted(compile_spec.abi_macros):
        value = compile_spec.abi_macros[name]
        parts.append(f"-D{name}={value}" if value else f"-D{name}")
    parts.extend(compile_spec.args)
    return " ".join(parts)


def _resolved_compile_fields(
    compile_spec: ProfileCompileSpec | None,
    resolved_bindings: Mapping[str, str] | None,
) -> tuple[str, str]:
    """Returns ``(gcc_path, gcc_options)`` for one already-resolved
    :class:`ProfileCompileSpec` (either a profile's ``compile:`` or its
    ``consumer_compile:`` overlay, G34 Phase 0) -- ``("", "")`` when
    *compile_spec* is ``None``."""
    if compile_spec is None:
        return "", ""
    gcc_path = ""
    if compile_spec.binding and resolved_bindings is not None:
        gcc_path = resolved_bindings.get(compile_spec.binding, "")
    return gcc_path, _compose_gcc_options(compile_spec)


def _compile_fields_for_profile(
    config: ProjectTargetsConfig,
    profile_id: str,
    resolved_bindings: Mapping[str, str] | None,
) -> tuple[str, str]:
    """Returns ``(compile_gcc_path, compile_gcc_options)`` for *profile_id*
    (P1 toolchain-profile audit) -- ``("", "")`` when the profile has no
    ``compile:`` overlay, is unknown, or declares no ``binding``/no
    resolvable-flags fields."""
    profile = config.profiles.get(profile_id)
    compile_spec = profile.compile if profile is not None else None
    return _resolved_compile_fields(compile_spec, resolved_bindings)


def _consumer_compile_fields_for_profile(
    config: ProjectTargetsConfig,
    profile_id: str,
    resolved_bindings: Mapping[str, str] | None,
) -> tuple[str, str]:
    """Returns ``(consumer_compile_gcc_path, consumer_compile_gcc_options)``
    for *profile_id* (G34 Phase 0) -- ``("", "")`` when the profile has no
    ``consumer_compile:`` overlay, is unknown, or declares no ``binding``/no
    resolvable-flags fields. Mirrors :func:`_compile_fields_for_profile`
    exactly, resolved from the profile's separate consumer-toolchain overlay
    instead of its producer ``compile:`` block."""
    profile = config.profiles.get(profile_id)
    consumer_compile_spec = profile.consumer_compile if profile is not None else None
    return _resolved_compile_fields(consumer_compile_spec, resolved_bindings)


def _compile_ast_frontend_for_profile(
    config: ProjectTargetsConfig, profile_id: str
) -> str:
    """Returns *profile_id*'s ``compile.frontend`` (G34 Phase B) -- ``""``
    when the profile has no ``compile:`` overlay, is unknown, or sets no
    ``frontend``."""
    profile = config.profiles.get(profile_id)
    compile_spec = profile.compile if profile is not None else None
    return compile_spec.frontend if compile_spec is not None else ""


def _consumer_compile_active_for_profile(
    config: ProjectTargetsConfig, profile_id: str
) -> bool:
    """True iff *profile_id* declares a non-empty ``consumer_compile:`` overlay."""
    profile = config.profiles.get(profile_id)
    if profile is None or profile.consumer_compile is None:
        return False
    return not profile.consumer_compile.is_empty


def _consumer_compile_ast_frontend_for_profile(
    config: ProjectTargetsConfig, profile_id: str
) -> str:
    """Returns *profile_id*'s ``consumer_compile.frontend`` (G34 Phase B),
    resolved the same way :func:`_compile_ast_frontend_for_profile` is, from
    the profile's separate consumer-toolchain overlay (G34 Phase 0)."""
    profile = config.profiles.get(profile_id)
    consumer_compile_spec = profile.consumer_compile if profile is not None else None
    return consumer_compile_spec.frontend if consumer_compile_spec is not None else ""


def _scheduling_fields_for_profile(
    config: ProjectTargetsConfig, profile_id: str
) -> tuple[str, str]:
    """Returns ``(runs_on, dependency_source)`` for *profile_id* (G34 Phase C).

    An unknown profile resolves to the same defaults an ``os:``-less one
    does, matching how every other ``*_for_profile`` helper here treats a
    profile it cannot find — the cell is generated either way, and a missing
    profile is a separate, already-reported error.

    An ``os:`` that names nothing schedulable raises instead of defaulting:
    quietly sending it to a Linux runner would produce a green cell that
    checked the wrong platform. :func:`~abicheck.buildsource.project_targets.
    _profile_issues` reports the same condition at ``project validate`` time,
    so reaching this raise means validation was skipped, not that the message
    is new.
    """
    profile = config.profiles.get(profile_id)
    if profile is None:
        return DEFAULT_PROFILE_RUNNER_LABEL, ""
    label = runner_label_for_os(profile.os)
    if label is None:
        raise ValueError(unroutable_os_message("profiles", profile_id, profile.os))
    return label, profile.dependency_source
