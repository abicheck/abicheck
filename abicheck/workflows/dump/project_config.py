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

"""``dump``'s project-config resolution (one-comparison-product.md §4.2), Click-free.

Every ``dump`` setting that comes from ``.abicheck.yml`` -- ``compile.lang``,
``build.compile_db_filter``, ``scope.exclude_headers`` and the ``debug:``
block (ADR-021a) -- is read here from one selected file
(:func:`resolve_dump_project_config`). ``compare`` runs the same resolution
for a raw ``--sources``/``--build-info`` side it collects inline
(``workflows/compare/inline_evidence.py``), which is why it lives in
``workflows`` and not beside the ``dump`` command: both callers need the one
answer, and the compare session may not import a front end.

The ``--debug-info`` operand rule :func:`check_debug_package_operands`
raises :class:`DebugOperandError` (a ``ValidationError``); the CLI
translates it to a usage error with the message unchanged.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...errors import ValidationError
from ..compile_config import scope_compile_config_env_toggles
from ..evidence_transport import (
    unsided_debug_packages,
    unsupported_detached_debug_message,
)

__all__ = [
    "DebugOperandError",
    "DumpDebugConfig",
    "DumpProjectConfig",
    "check_debug_package_operands",
    "resolve_dump_debug_config",
    "resolve_dump_project_config",
    "resolve_dump_scope_exclude_headers",
]


class DebugOperandError(ValidationError):
    """A ``--debug-info`` operand ``dump`` has no path to consume (exit 64)."""


def check_debug_package_operands(debug_roots: tuple[Path, ...]) -> None:
    """Reject a ``dump --debug-info`` operand no extraction path reads (7n).

    ``--debug-info`` carries one evidence role over three transports, and
    ``dump`` resolves two of them: a directory to search and a detached
    debug file both go straight to ``debug_resolver``'s chain. The third,
    a debug package, is a release-comparison transport -- unpacking it is
    ``prepare_release_inputs``' job, and ``dump``'s operand is a single
    binary with no fan-out to unpack one for. So it is a usage error that
    names where the capability lives, rather than a value the resolver
    would search for a ``.build-id`` tree inside and silently find nothing
    in (ADR-068 D4's "no silent no-op" rule). The same rule refuses a named
    PDB or DWARF-package file on either command
    (``evidence_transport.unsupported_detached_debug``).
    """
    message = unsupported_detached_debug_message(debug_roots)
    if message is not None:
        raise DebugOperandError(message)
    packages = unsided_debug_packages(debug_roots)
    if not packages:
        return
    named = ", ".join(str(p) for p in packages)
    raise DebugOperandError(
        f"--debug-info {named}: that is a debug package, and `dump` takes a "
        "single binary operand with no package-extraction stage to unpack it. "
        "Extract it yourself and pass the directory (or the detached debug "
        "file) instead, or use `compare --debug-info` on the release "
        "directories/packages, which does unpack it."
    )


@dataclass(frozen=True)
class DumpDebugConfig:
    """``dump``'s resolved separate-debug-file settings.

    ``format`` is the raw (not yet ``auto``-normalized) config string.
    """

    format: str | None = None
    dwarf_only: bool = False
    debuginfod: bool = False
    debuginfod_url: str | None = None
    pdb_path: Path | None = None


def resolve_dump_debug_config(
    build_config: Path | None,
    sources: Path | None,
) -> DumpDebugConfig:
    """Resolve ``dump``'s debug-resolution config from ``.abicheck.yml``.

    Same discovery precedence :func:`abicheck.workflows.compile_config.merge_compile_config`
    uses for the ``compile:`` block: an explicit ``--config``, else the
    ``.abicheck.yml`` auto-discovered at the ``--sources`` tree root. A
    missing/unparseable config resolves to every field's built-in default --
    best-effort, matching ``merge_compile_config``'s own auto-discovered-
    config leniency, since a malformed file the caller never explicitly
    bound to must not fail a ``dump`` it didn't ask to validate.
    """
    from ..extraction import discover_build_config, load_build_config

    cfg_path = (
        build_config if build_config is not None else discover_build_config(sources)
    )
    if cfg_path is None:
        return DumpDebugConfig()
    try:
        bc = load_build_config(cfg_path)
    except ValueError:
        return DumpDebugConfig()
    return DumpDebugConfig(
        format=bc.debug_format,
        dwarf_only=bool(bc.debug_dwarf_only),
        debuginfod=bool(bc.debug_debuginfod),
        debuginfod_url=bc.debug_debuginfod_url,
        pdb_path=Path(bc.debug_pdb_path) if bc.debug_pdb_path else None,
    )


def resolve_dump_debug_fields(
    resolved_debug: DumpDebugConfig | None,
    *,
    build_config: Path | None,
    sources: Path | None,
    debug_roots: tuple[Path, ...] = (),
) -> DumpDebugConfig:
    """*resolved_debug* verbatim when given (``compare``'s inline embed
    forwards its own already-resolved config); otherwise resolve this
    ``dump``'s own project config via :func:`resolve_dump_debug_config`.

    Also validates the ``--debug-info`` operands the resolved config governs
    (:func:`check_debug_package_operands`) -- this is ``dump``'s one place
    where its debug inputs are settled, so the transport check belongs beside
    the rest of them rather than as a separate step a future caller of this
    resolver could forget. Checked on both branches, including the inline
    embed's: ``compare``'s embed forwards a config, never an operand set, so
    a non-empty *debug_roots* there is still this command's own to validate.
    """
    check_debug_package_operands(debug_roots)
    if resolved_debug is not None:
        return resolved_debug
    return resolve_dump_debug_config(build_config, sources)


def resolve_dump_scope_exclude_headers(
    build_config: Path | None,
    sources: Path | None,
    exclude_headers: tuple[str, ...],
) -> tuple[str, ...]:
    """Resolve ``scope.exclude_headers`` from ``.abicheck.yml`` for ``dump``.

    The same key, the same precedence and the same "weaker than the flag,
    never unioned with it" rule ``compare`` applies -- shared as a rule
    rather than as code, since the two commands discover their config
    differently (``compare`` has already resolved a ``BuildConfig`` by the
    time it asks; ``dump`` discovers one here, like every other Phase 7
    config demotion on this command).

    ``dump`` has to honor it, not merely ``compare``, because the two
    produce operands for each other. A project stating the key in its
    config and running ``dump`` to publish a baseline would otherwise get a
    baseline built from the *whole* header tree -- including the header the
    config says cannot be parsed alongside its siblings, so on a tree like
    the reported one the dump simply fails -- while the identical config
    made ``compare`` succeed. Same front-end-default asymmetry
    ``include_dependencies`` was fixed for, in a new place.

    Note what does *not* currently catch that asymmetry, since it is the
    obvious guard to assume: the comparability gate compares each side's
    recorded ``excluded_header_patterns``
    (``extract.header_exclusions.exclusion_asymmetry_reason``), and the
    native ``dump`` CLI records nothing there -- for the flag either, not
    just this key (measured; see ``docs/contribute/known-gaps.md``). So a
    differently-narrowed ``dump`` baseline is not refused, it is silently
    compared. That makes honoring the key here the thing that keeps the two
    commands consistent, rather than a convenience on top of a gate that
    would have caught it.

    Same deliberate leniency on a malformed auto-discovered config as its
    siblings here.
    """
    if exclude_headers:
        return tuple(exclude_headers)
    from ..extraction import discover_build_config, load_build_config

    cfg_path = (
        build_config if build_config is not None else discover_build_config(sources)
    )
    if cfg_path is None:
        return ()
    try:
        bc = load_build_config(cfg_path)
    except ValueError:
        return ()
    return tuple(bc.exclude_headers or ())


def resolve_dump_build_compile_db_filter(
    build_config: Path | None,
    sources: Path | None,
) -> str | None:
    """Resolve ``build.compile_db_filter`` from ``.abicheck.yml``.

    one-comparison-product.md §4.2's CONFIG row: the former
    ``dump --compile-db-filter``. Same discovery precedence, and the same
    deliberate leniency on a malformed auto-discovered config, as
    :func:`resolve_dump_debug_config` right above -- one shape for every
    Phase 7 config demotion on this command, rather than a second rule.
    """
    from ..extraction import discover_build_config, load_build_config

    cfg_path = (
        build_config if build_config is not None else discover_build_config(sources)
    )
    if cfg_path is None:
        return None
    try:
        bc = load_build_config(cfg_path)
    except ValueError:
        return None
    return bc.compile_db_filter or None


def resolve_dump_lang_and_env_toggles(
    on_close: Callable[[Callable[[], None]], object],
    *,
    build_config: Path | None,
    sources: Path | None,
    lang: str | None,
    lang_default: str,
) -> tuple[str, bool]:
    """Resolve ``dump``'s own project config once for ``compile.lang`` and
    apply the ``compile.ast_frontend_fallback``/
    ``compile.allow_unsupported_castxml`` env-var toggles (Phase 7: all
    three had a CLI flag on ``dump`` before this phase).

    The toggles last until *on_close* runs the restoration it is handed
    (the CLI passes ``ctx.call_on_close``;
    :func:`~abicheck.workflows.compile_config.scope_compile_config_env_toggles`).
    *lang_default* is the front end's own default spelling.

    Returns ``(lang, lang_explicit)``. *lang* is *lang* unchanged when it is
    already set (``compare``'s inline embed forwards its own resolved
    value); otherwise ``compile.lang`` or *lang_default*. *lang_explicit* is
    ``True`` exactly when a real request drove that value (the caller's own
    already-non-None *lang*, or a config `compile.lang`) -- never for the
    bare *lang_default* fallback. `--lang` has no CLI spelling left on
    `dump` at all (Phase 7); `compile.lang` is the
    only remaining way to make an explicit request (G31 Phase C follow-up:
    the whole reason `lang_explicit` exists is to distinguish that from the
    harmless default, which auto-detection must not be forced past).
    """
    from ..extraction import discover_build_config, load_build_config

    cfg_path = (
        build_config if build_config is not None else discover_build_config(sources)
    )
    project_cfg = None
    if cfg_path is not None:
        try:
            project_cfg = load_build_config(cfg_path)
        except ValueError:
            project_cfg = None
    scope_compile_config_env_toggles(project_cfg, on_close)
    if lang is not None:
        return lang, True
    compile_lang = project_cfg.compile_lang if project_cfg else None
    return compile_lang or lang_default, compile_lang is not None


@dataclass(frozen=True)
class DumpProjectConfig:
    """Every ``dump`` setting that comes from the project config, resolved
    from one config selection (:func:`abicheck.config_paths.
    resolve_project_config`) so no field can read a different file."""

    path: Path | None
    explicit: bool
    lang: str
    lang_explicit: bool
    compile_db_filter: Any
    exclude_headers: tuple[str, ...]
    debug: DumpDebugConfig


def resolve_dump_project_config(
    on_close: Callable[[Callable[[], None]], object],
    *,
    build_config: Path | None,
    sources: Path | None,
    lang: str | None,
    lang_default: str,
    exclude_headers: tuple[str, ...],
    resolved_debug: DumpDebugConfig | None,
    debug_roots: tuple[Path, ...],
) -> DumpProjectConfig:
    """Select ``dump``'s project config once and resolve every config-sourced
    field from it.

    The selection is the one rule every command shares: ``--config``, else
    the ``--sources`` tree root, else the nearest config at or above the
    current directory -- so a project's ``.abicheck.yml`` applies to ``dump``
    run from its checkout exactly as it does to ``compare``. Only an explicit
    ``--config`` is *trusted* (``explicit``): a discovered one contributes
    passive settings but never runs ``build.query``.
    """
    from ...config_paths import resolve_project_config

    ref = resolve_project_config(build_config, sources=sources, search_from=Path.cwd())
    lang, lang_explicit = resolve_dump_lang_and_env_toggles(
        on_close,
        build_config=ref.path,
        sources=sources,
        lang=lang,
        lang_default=lang_default,
    )
    return DumpProjectConfig(
        path=ref.path,
        explicit=ref.explicit,
        lang=lang,
        lang_explicit=lang_explicit,
        # §4.2's CONFIG row: `build.compile_db_filter` replaces
        # `--compile-db-filter`.
        compile_db_filter=resolve_dump_build_compile_db_filter(ref.path, sources),
        # `scope.exclude_headers` -- `--exclude-header`'s config spelling; see
        # the resolver's docstring for why `dump` must honor it too.
        exclude_headers=resolve_dump_scope_exclude_headers(
            ref.path, sources, exclude_headers
        ),
        debug=resolve_dump_debug_fields(
            resolved_debug,
            build_config=ref.path,
            sources=sources,
            debug_roots=debug_roots,
        ),
    )
