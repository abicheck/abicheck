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

"""``dump``'s Phase 7c debug-resolution config (one-comparison-product.md
§4.2, ADR-021a) -- split out of ``cli_dump_helpers.py`` purely to keep that
file under its architecture ``no_growth`` baseline (AGENTS.md: "move
responsibility instead of raising the baseline").

``--dwarf-only``/``--debug-format``/``--debuginfod``/``--debuginfod-url``/
``--pdb-path`` are removed from ``dump``'s CLI entirely (CONFIG class, no
surviving override, matching §4.1's identical treatment of the same four
knobs on ``compare``): ``.abicheck.yml``'s ``debug:`` block is each field's
only source now.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click


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

    Same discovery precedence :func:`abicheck.cli_options.merge_compile_config`
    uses for the ``compile:`` block: an explicit ``--config``, else the
    ``.abicheck.yml`` auto-discovered at the ``--sources`` tree root. A
    missing/unparseable config resolves to every field's built-in default --
    best-effort, matching ``merge_compile_config``'s own auto-discovered-
    config leniency, since a malformed file the caller never explicitly
    bound to must not fail a ``dump`` it didn't ask to validate.
    """
    from ...workflows.extraction import discover_build_config, load_build_config

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
    (:func:`reject_debug_package_operands`) -- this is ``dump``'s one place
    where its debug inputs are settled, so the transport check belongs beside
    the rest of them rather than as a separate step a future caller of this
    resolver could forget. Checked on both branches, including the inline
    embed's: ``compare``'s embed forwards a config, never an operand set, so
    a non-empty *debug_roots* there is still this command's own to validate.
    """
    reject_debug_package_operands(debug_roots)
    if resolved_debug is not None:
        return resolved_debug
    return resolve_dump_debug_config(build_config, sources)


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
    from ...workflows.extraction import discover_build_config, load_build_config

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
    ctx: click.Context,
    *,
    build_config: Path | None,
    sources: Path | None,
    lang: str | None,
    lang_default: str,
    apply_env_toggles: Callable[[click.Context, Any], None],
) -> tuple[str, bool]:
    """Resolve ``dump``'s own project config once for ``compile.lang`` and
    apply the ``compile.ast_frontend_fallback``/
    ``compile.allow_unsupported_castxml`` env-var toggles (Phase 7: all
    three had a CLI flag on ``dump`` before this phase).

    *lang_default*/*apply_env_toggles* are injected (``cli_options.
    LANG_DEFAULT``/``apply_compile_config_env_toggles``) rather than
    imported here: this module sits under ``frontends.cli`` and importing
    ``cli_options`` from here would fold this leaf into the pre-existing
    CLI-registration import cycle (AGENTS.md "What NOT to do" -- a
    function-local import does not avoid this check, which walks the whole
    AST regardless of nesting).

    Returns ``(lang, lang_explicit)``. *lang* is *lang* unchanged when it is
    already set (``compare``'s inline embed forwards its own resolved
    value); otherwise ``compile.lang`` or *lang_default*. *lang_explicit* is
    ``True`` exactly when a real request drove that value (the caller's own
    already-non-None *lang*, or a config `compile.lang`) -- never for the
    bare *lang_default* fallback. `--lang` has no CLI spelling left on
    `dump` at all (Phase 7), so `ctx.get_parameter_source("lang")` can never
    report a genuine `COMMANDLINE` source any more; `compile.lang` is the
    only remaining way to make an explicit request (G31 Phase C follow-up:
    the whole reason `lang_explicit` exists is to distinguish that from the
    harmless default, which auto-detection must not be forced past).
    """
    from ...workflows.extraction import discover_build_config, load_build_config

    cfg_path = (
        build_config if build_config is not None else discover_build_config(sources)
    )
    project_cfg = None
    if cfg_path is not None:
        try:
            project_cfg = load_build_config(cfg_path)
        except ValueError:
            project_cfg = None
    apply_env_toggles(ctx, project_cfg)
    if lang is not None:
        return lang, True
    compile_lang = project_cfg.compile_lang if project_cfg else None
    return compile_lang or lang_default, compile_lang is not None


def resolve_stored_bundle_lang(
    kwargs: dict[str, Any],
    *,
    config_explicit: bool,
    new_is_stored: bool,
    lang_default: str,
) -> tuple[str, bool]:
    """``compare``'s stored-bundle-facts dispatch (``old_is_stored``) own
    ``lang``/``lang_explicit`` resolution -- split out of ``compare.py``
    purely to keep that file under its architecture ``no_growth`` baseline
    (AGENTS.md: "move responsibility instead of raising the baseline"),
    the same reason this module exists at all.

    Phase 7 (one-comparison-product.md §4.1): `--lang` has no CLI spelling
    left on `compare` at all -- `ctx.get_parameter_source("lang")` can never
    report COMMANDLINE any more, and `kwargs["lang"]` is never populated by
    Click either, since it is not a declared parameter. `compile.lang` from
    the already-resolved project config (``kwargs["config"]``, set by
    ``compare_bundle_facts.resolve_dispatch_compile_context`` before this is
    called) is this dispatch path's only remaining source, mirroring
    ``run_compare``'s own ``resolved_cfg.compile_lang`` resolution.

    *new_is_stored* changes what "explicit" means, matching
    ``reject_explicit_compile_config_for_stored_pair``'s own explicit-vs-
    ambient distinction: stored/stored has no header-frontend extraction
    channel on either side, so (like every other ``compile:`` field in that
    situation) only a genuinely *explicit* ``--config``'s ``compile.lang``
    is a real, rejectable request -- an ambient project default stays
    harmlessly unused. Stored/live's NEW side genuinely extracts, so
    ambient and explicit ``compile.lang`` both apply normally, the same way
    ``compile.frontend``/``compile.include_dirs`` already do for that side.
    """
    from ...workflows.extraction import load_build_config

    project_cfg = None
    if kwargs.get("config") is not None:
        try:
            project_cfg = load_build_config(kwargs["config"])
        except ValueError:
            project_cfg = None
    compile_lang = project_cfg.compile_lang if project_cfg is not None else None
    lang = kwargs.get("lang") or compile_lang or lang_default
    if new_is_stored:
        lang_explicit = compile_lang is not None and config_explicit
    else:
        lang_explicit = compile_lang is not None
    return lang, lang_explicit


def reject_debug_package_operands(debug_roots: tuple[Path, ...]) -> None:
    """Reject a debug *package* named to ``dump --debug-info`` (plan 7n).

    ``--debug-info`` carries one evidence role over three transports, and
    ``dump`` resolves two of them: a directory to search and a detached
    debug file both go straight to ``debug_resolver``'s chain. The third,
    a debug package, is a release-comparison transport -- unpacking it is
    ``prepare_release_inputs``' job, and ``dump``'s operand is a single
    binary with no fan-out to unpack one for. So it is a usage error that
    names where the capability lives, rather than a value the resolver
    would search for a ``.build-id`` tree inside and silently find nothing
    in (ADR-068 D4's "no silent no-op" rule, same as this module's
    siblings).
    """
    from ...frontends.cli.options.evidence_roles import unsided_debug_packages

    packages = unsided_debug_packages(debug_roots)
    if not packages:
        return
    named = ", ".join(str(p) for p in packages)
    raise click.UsageError(
        f"--debug-info {named}: that is a debug package, and `dump` takes a "
        "single binary operand with no package-extraction stage to unpack it. "
        "Extract it yourself and pass the directory (or the detached debug "
        "file) instead, or use `compare --debug-info` on the release "
        "directories/packages, which does unpack it."
    )
