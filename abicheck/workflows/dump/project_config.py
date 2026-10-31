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

import dataclasses
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ...buildsource.build_config_overrides import ConfigOverrideError
from ...compile_context import CompileContext
from ...errors import ValidationError
from ..compile_config import (
    COMPILE_CONTEXT_OVERRIDE_PATHS,
    fold_typed_compile_overrides,
    scope_compile_config_env_toggles,
)
from ..evidence_transport import (
    unsided_debug_packages,
    unsupported_detached_debug_message,
)

if TYPE_CHECKING:
    from ..extraction import BuildConfig
    from ..service_requests import DumpRequest

__all__ = [
    "DUMP_APPLIED_PATHS",
    "DUMP_TYPED_OVERRIDE_CHANNELS",
    "DebugOperandError",
    "DumpConfigError",
    "DumpDebugConfig",
    "DumpProjectConfig",
    "check_debug_package_operands",
    "resolve_dump_debug_config",
    "resolve_dump_project_config",
    "resolve_dump_scope_exclude_headers",
    "with_dump_config_overrides",
]


class DebugOperandError(ValidationError):
    """A ``--debug-info`` operand ``dump`` has no path to consume (exit 64)."""


class DumpConfigError(ValidationError):
    """A ``--set`` a run cannot apply, or a named ``.abicheck.yml`` it cannot read.

    Raised instead of falling back to the file-less defaults: a run that
    stated an override must not silently drop it (exit 64 on the CLI).
    """


#: The ``--set`` keys ``dump`` applies (ADR-091 D4), each with the typed
#: ``DumpRequest`` field it fills: ``(config path, owner, field, BuildConfig
#: attribute)``. ``owner`` is ``"request"`` for a ``DumpRequest`` field and
#: ``"input"`` for one of its ``InputSpec``. Every row is read by a resolver
#: below through :func:`_dump_build_config`, and the CLI resolves through the
#: same resolvers, so the two spellings cannot disagree.
#: ``scope.exclude_headers`` and ``debug.pdb_path`` are core options (their own
#: flags, ``CORE_CONFIG_FLAGS``), so ``--set`` refuses them before any channel
#: is consulted; they are not listed here.
DUMP_TYPED_OVERRIDE_CHANNELS: tuple[tuple[str, str, str, str], ...] = (
    ("build.compile_db_filter", "input", "compile_db_filter", "compile_db_filter"),
    ("compile.lang", "request", "lang", "compile_lang"),
    ("debug.format", "request", "debug_format", "debug_format"),
    ("debug.dwarf_only", "request", "dwarf_only", "debug_dwarf_only"),
    ("debug.debuginfod", "request", "debuginfod", "debug_debuginfod"),
    ("debug.debuginfod_url", "request", "debuginfod_url", "debug_debuginfod_url"),
)

#: The keys only the CLI applies. Each one is a process-wide environment
#: toggle (``scope_compile_config_env_toggles``) with no typed field to fill,
#: so a typed caller is refused for them rather than given a silent no-op.
_DUMP_CLI_ONLY_PATHS: frozenset[str] = frozenset(
    {"compile.ast_frontend_fallback", "compile.allow_unsupported_castxml"}
)

#: Every key ``dump --set`` applies.
DUMP_APPLIED_PATHS: frozenset[str] = (
    frozenset({path for path, _owner, _field, _attr in DUMP_TYPED_OVERRIDE_CHANNELS})
    | _DUMP_CLI_ONLY_PATHS
    | COMPILE_CONTEXT_OVERRIDE_PATHS
)


def _config_path(build_config: Path | None, sources: Path | None) -> Path | None:
    """The config a ``dump`` run reads: *build_config* when named, else discovered."""
    from ..extraction import discover_build_config

    return build_config if build_config is not None else discover_build_config(sources)


def _dump_build_config(
    cfg_path: Path | None, config_overrides: Mapping[str, object] | None
) -> BuildConfig | None:
    """The one loader every ``dump`` config resolver reads through (ADR-091 D4).

    Without overrides this is the file as ``dump`` has always read it: ``None``
    when there is no file, and ``None`` for a malformed one, since a discovered
    file is best-effort. With overrides the file is read strictly, because a
    run that stated a value must not lose it to a fallback; a missing file
    then means the built-in defaults with the overrides laid over them. An
    override that the schema refuses raises :class:`ConfigOverrideError`.
    """
    from ..extraction import load_build_config, load_build_config_with_digest

    if not config_overrides:
        if cfg_path is None:
            return None
        try:
            return load_build_config(cfg_path)
        except ValueError:
            return None
    try:
        return load_build_config_with_digest(
            cfg_path, config_overrides=config_overrides
        )[0]
    except ConfigOverrideError:
        raise
    except ValueError as exc:
        raise DumpConfigError(str(exc)) from exc


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
    config_overrides: Mapping[str, object] | None = None,
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
    bc = _dump_build_config(_config_path(build_config, sources), config_overrides)
    if bc is None:
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
    config_overrides: Mapping[str, object] | None = None,
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
    return resolve_dump_debug_config(build_config, sources, config_overrides)


def resolve_dump_scope_exclude_headers(
    build_config: Path | None,
    sources: Path | None,
    exclude_headers: tuple[str, ...],
    config_overrides: Mapping[str, object] | None = None,
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
    bc = _dump_build_config(_config_path(build_config, sources), config_overrides)
    if bc is None:
        return ()
    return tuple(bc.exclude_headers or ())


def resolve_dump_build_compile_db_filter(
    build_config: Path | None,
    sources: Path | None,
    config_overrides: Mapping[str, object] | None = None,
) -> str | None:
    """Resolve ``build.compile_db_filter`` from ``.abicheck.yml``.

    one-comparison-product.md §4.2's CONFIG row: the former
    ``dump --compile-db-filter``. Same discovery precedence, and the same
    deliberate leniency on a malformed auto-discovered config, as
    :func:`resolve_dump_debug_config` right above -- one shape for every
    Phase 7 config demotion on this command, rather than a second rule.
    """
    bc = _dump_build_config(_config_path(build_config, sources), config_overrides)
    if bc is None:
        return None
    return bc.compile_db_filter or None


def resolve_dump_lang_and_env_toggles(
    on_close: Callable[[Callable[[], None]], object],
    *,
    build_config: Path | None,
    sources: Path | None,
    lang: str | None,
    lang_default: str,
    config_overrides: Mapping[str, object] | None = None,
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
    project_cfg = _dump_build_config(
        _config_path(build_config, sources), config_overrides
    )
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
    config_overrides: Mapping[str, object] | None = None,
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
    from ..extraction import validate_config_overrides

    if config_overrides:
        # Validated once, up front, so every resolver below reads a valid set.
        try:
            validate_config_overrides(config_overrides)
        except ConfigOverrideError as exc:
            raise DumpConfigError(f"--set: {exc}") from exc
    ref = resolve_project_config(build_config, sources=sources, search_from=Path.cwd())
    lang, lang_explicit = resolve_dump_lang_and_env_toggles(
        on_close,
        build_config=ref.path,
        sources=sources,
        lang=lang,
        lang_default=lang_default,
        config_overrides=config_overrides,
    )
    return DumpProjectConfig(
        path=ref.path,
        explicit=ref.explicit,
        lang=lang,
        lang_explicit=lang_explicit,
        # §4.2's CONFIG row: `build.compile_db_filter` replaces
        # `--compile-db-filter`.
        compile_db_filter=resolve_dump_build_compile_db_filter(
            ref.path, sources, config_overrides
        ),
        # `scope.exclude_headers` -- `--exclude-header`'s config spelling; see
        # the resolver's docstring for why `dump` must honor it too.
        exclude_headers=resolve_dump_scope_exclude_headers(
            ref.path, sources, exclude_headers, config_overrides
        ),
        debug=resolve_dump_debug_fields(
            resolved_debug,
            build_config=ref.path,
            sources=sources,
            debug_roots=debug_roots,
            config_overrides=config_overrides,
        ),
    )


#: The ``run_dump`` keyword parameter each override channel fills. A path not
#: listed here has no ``run_dump`` parameter (``build.compile_db_filter`` belongs
#: to ``InputSpec``), so ``run_dump`` refuses it and the typed ``DumpRequest``
#: path carries it instead.
RUN_DUMP_OVERRIDE_PARAMS: dict[str, str] = {
    "compile.lang": "lang",
    "debug.format": "debug_format",
    "debug.dwarf_only": "dwarf_only",
    "debug.debuginfod": "debuginfod",
    "debug.debuginfod_url": "debuginfod_url",
}


def run_dump_override_arguments(
    config_overrides: Mapping[str, object],
    arguments: Mapping[str, object],
    defaults: Mapping[str, object],
) -> dict[str, object]:
    """The ``run_dump`` keyword arguments *config_overrides* set (ADR-091 D4).

    *arguments* are the arguments the caller passed and *defaults* the
    parameters' own defaults. An argument the caller set away from its default
    is kept, since a typed argument beats an override, as on
    :func:`with_dump_config_overrides`. The override set is validated first, so
    a key the schema refuses raises :class:`ConfigOverrideError` before anything
    else runs.
    """
    cfg = _dump_build_config(None, config_overrides)
    if cfg is None:  # pragma: no cover -- overrides are non-empty here
        return {}
    unsupported = sorted(
        set(config_overrides)
        - set(RUN_DUMP_OVERRIDE_PARAMS)
        - COMPILE_CONTEXT_OVERRIDE_PATHS
    )
    if unsupported:
        raise ConfigOverrideError(
            f"{', '.join(unsupported)}: no run_dump parameter applies this key; "
            "use run_dump_request with a DumpRequest, or set it in .abicheck.yml"
        )
    attrs = {path: attr for path, _owner, _field, attr in DUMP_TYPED_OVERRIDE_CHANNELS}
    out: dict[str, object] = {}
    for path, name in RUN_DUMP_OVERRIDE_PARAMS.items():
        if path not in config_overrides:
            continue
        value = getattr(cfg, attrs[path])
        if value is None or arguments.get(name, defaults[name]) != defaults[name]:
            continue
        out[name] = value
        if name == "lang":
            out["lang_explicit"] = True
    out.update(_folded_compile_arguments(config_overrides, arguments, defaults))
    return out


def _folded_compile_arguments(
    config_overrides: Mapping[str, object],
    arguments: Mapping[str, object],
    defaults: Mapping[str, object],
) -> dict[str, object]:
    """``run_dump``'s ``frontend``/``compile`` with the ``compile.*`` overrides folded in.

    Through :func:`~abicheck.workflows.compile_config.fold_typed_compile_overrides`,
    the resolver ``dump --set compile.*`` reaches, so the two cannot disagree.
    Only the arguments the fold changes are returned.
    """
    frontend = arguments.get("frontend", defaults.get("frontend", "auto"))
    compile_ctx = arguments.get("compile", defaults.get("compile"))
    if compile_ctx is not None and not isinstance(compile_ctx, CompileContext):
        raise TypeError(
            f"run_dump(compile=) must be a CompileContext or None, got "
            f"{type(compile_ctx).__name__}"
        )
    folded_frontend, (folded_compile,) = fold_typed_compile_overrides(
        str(frontend), (compile_ctx,), config_overrides
    )
    out: dict[str, object] = {}
    if folded_frontend != frontend:
        out["frontend"] = folded_frontend
    if folded_compile != compile_ctx:
        out["compile"] = folded_compile
    return out


def with_dump_config_overrides(request: DumpRequest) -> DumpRequest:
    """*request* with its ``config_overrides`` resolved into the fields they set.

    The typed spelling of ``dump --set`` (ADR-091 D4). A typed request names no
    config file, so the overrides are validated and laid over the built-in
    defaults through the same loader the CLI uses, then each applied key fills
    the request field it maps to (:data:`DUMP_TYPED_OVERRIDE_CHANNELS`) only
    when the caller left that field at its default: an explicit typed argument
    beats an override, as an explicit flag beats ``--set``. A key with no typed
    channel is refused with :class:`ConfigOverrideError`, never dropped.
    """
    if not request.config_overrides:
        return request
    cfg = _dump_build_config(None, request.config_overrides)
    if cfg is None:  # pragma: no cover -- overrides are non-empty here
        return request
    channel_paths = {
        path for path, _owner, _field, _attr in DUMP_TYPED_OVERRIDE_CHANNELS
    } | COMPILE_CONTEXT_OVERRIDE_PATHS
    unsupported = sorted(set(request.config_overrides) - channel_paths)
    if unsupported:
        raise ConfigOverrideError(
            f"{', '.join(unsupported)}: no typed DumpRequest field applies "
            "this key; set it in .abicheck.yml or use the CLI"
        )
    request_defaults = {f.name: f.default for f in dataclasses.fields(request)}
    input_defaults = {f.name: f.default for f in dataclasses.fields(request.input)}
    request_updates: dict[str, Any] = {}
    input_updates: dict[str, Any] = {}
    for path, owner, field_name, attr in DUMP_TYPED_OVERRIDE_CHANNELS:
        if path not in request.config_overrides:
            continue
        value = getattr(cfg, attr)
        if owner == "input":
            if (
                getattr(request.input, field_name) == input_defaults[field_name]
                and value
            ):
                input_updates[field_name] = value
            continue
        if field_name == "lang":
            if request.lang == request_defaults["lang"] and value is not None:
                request_updates["lang"] = value
                request_updates["lang_explicit"] = True
            continue
        if (
            getattr(request, field_name) == request_defaults[field_name]
            and value is not None
        ):
            request_updates[field_name] = value
    frontend, (compile_ctx,) = fold_typed_compile_overrides(
        request.frontend, (request.input.compile,), request.config_overrides
    )
    if frontend != request.frontend:
        request_updates["frontend"] = frontend
    if compile_ctx != request.input.compile:
        input_updates["compile"] = compile_ctx
    if not request_updates and not input_updates:
        return request
    return dataclasses.replace(
        request,
        input=dataclasses.replace(request.input, **input_updates),
        **request_updates,
    )
