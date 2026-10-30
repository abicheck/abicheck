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

"""The ``.abicheck.yml`` ``compile:`` fold (ADR-037 D3), Click-free.

One resolver shared by ``compare``, ``dump`` and ``scan``: precedence is CLI >
config, ADR-074's ``-D`` macros are folded by name, and ``compile.compiler``
is honored only from a config the operator named. It used to live in the
root ``cli_options`` module, which kept it reachable only from front ends;
the ``compare`` session (``workflows/compare/session.py``) and the inline
evidence collection it runs need it too, so it lives here and the CLI's
``cli_options.resolve_compile_context`` is now just the Click adapter that
reads explicitness off the parameter source and translates
:class:`CompileConfigError` into a usage error (exit 64).

Warnings go to the caller's *notify* sink (stderr by default), the same
channel ``workflows.compare.resolve`` uses for its own progress notes.
"""

from __future__ import annotations

import dataclasses
import os
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from ..errors import ValidationError
from ..model.macro_definition import (
    MacroDefinition,
    macro_definition_tokens,
    merge_macro_definitions,
    parse_macro_definitions,
)

if TYPE_CHECKING:
    from ..dry_run_estimate import CompileContext
    from .extraction import BuildConfig

__all__ = [
    "CompileConfigError",
    "apply_cli_defines",
    "compile_config_argv_tokens",
    "merge_compile_config",
    "merge_compile_std_fields",
    "resolve_compile_context",
    "scope_compile_config_env_toggles",
    "set_env_flag_until",
]


class CompileConfigError(ValidationError):
    """An explicitly named build config that cannot be parsed (exit 64)."""


def _stderr(message: str) -> None:
    sys.stderr.write(f"{message}\n")


def set_env_flag_until(
    env_var: str, on_close: Callable[[Callable[[], None]], object]
) -> None:
    """Set *env_var* to ``"1"`` and register its restoration with *on_close*.

    The "explicit, invocation-scoped opt-in past a hard-fail gate" shape
    ``ABICHECK_ALLOW_AST_FALLBACK``/``ABICHECK_ALLOW_UNSUPPORTED_CASTXML``
    share: the previous value (or its absence) comes back when the run that
    asked for it closes. The Click callbacks pass ``ctx.call_on_close``.
    """
    previous = os.environ.get(env_var)
    os.environ[env_var] = "1"

    def _restore() -> None:
        if previous is None:
            os.environ.pop(env_var, None)
        else:
            os.environ[env_var] = previous

    on_close(_restore)


def scope_compile_config_env_toggles(
    bc: BuildConfig | None, on_close: Callable[[Callable[[], None]], object]
) -> None:
    """Apply ``compile.ast_frontend_fallback``/``compile.allow_unsupported_castxml``.

    Phase 7 (one-comparison-product.md §4.1/§4.2): the two flags these keys
    replace were pure per-invocation env-var togglers, so a ``compile:``
    block setting either to ``true`` sets the same variable for the same
    lifetime (:func:`set_env_flag_until`).
    """
    if bc is None:
        return
    if bc.compile_ast_frontend_fallback:
        set_env_flag_until("ABICHECK_ALLOW_AST_FALLBACK", on_close)
    if bc.compile_allow_unsupported_castxml:
        set_env_flag_until("ABICHECK_ALLOW_UNSUPPORTED_CASTXML", on_close)


def compile_config_argv_tokens(
    std: str | None, defines: Iterable[str], options: Iterable[str]
) -> list[str]:
    """Synthesize one ``compile.std``/``compile.defines``/``compile.options``
    document's worth of compiler-argv tokens, in the exact order
    :func:`merge_compile_config` itself folds them: ``[-std=<std>] +
    [-D<define> ...] + <options...>``. A single source of truth for this
    order matters because a same-flag conflict (e.g. two ``-std=`` tokens
    from different documents) is decided by *relative position* in the
    combined sequence, not by which field produced which token -- any
    second, independent reimplementation of this order (as
    ``action_config_overlay._merge_compile_block`` once had) risks
    silently disagreeing with the real one (P1, Codex review, PR #1222
    ninth round)."""
    tokens: list[str] = [f"-std={std}"] if std else []
    tokens += [f"-D{d}" for d in defines]
    tokens += list(options)
    return tokens


#: The ``compile:`` keys the L2 compile context reads that have no visible flag
#: of their own (ADR-091 D4 config tier). ``--set`` applies them on the CLI
#: ``compare``, ``dump`` and the directory/package fan-out, which all resolve
#: the context through :func:`merge_compile_config`. The typed API has no
#: ``compile:`` fold, so it refuses them rather than dropping them.
#: ``compile.include_dirs`` and ``compile.defines`` are core (``-I``/``--define``)
#: and are not listed.
COMPILE_CONTEXT_OVERRIDE_PATHS: frozenset[str] = frozenset(
    {
        "compile.compiler",
        "compile.frontend",
        "compile.frontend_context",
        "compile.lang",
        "compile.nostdinc",
        "compile.options",
        "compile.std",
        "compile.sysroot",
    }
)


def _as_compile_field_list(value: object) -> list[str]:
    if value is None:
        return []
    return [str(v) for v in value] if isinstance(value, (list, tuple)) else [str(value)]


def merge_compile_std_fields(
    checkout_blk: dict[str, object], sources_blk: dict[str, object]
) -> dict[str, object] | None:
    """The cross-key half of ``action_config_overlay._merge_compile_block``:
    ``compile.std``/``compile.defines``/``compile.options`` are never three
    independent fields once EITHER document sets any of them -- the real
    ``merge_compile_config`` always synthesizes one document's worth of
    ``[-std=...] + [-D... ] + <options>`` argv tokens (see
    :func:`compile_config_argv_tokens`) atomically for a single ``compile:``
    document, then concatenates the sources-root document's own tokens
    ahead of the checkout document's (checkout's own token ends up LAST and
    wins a same-flag conflict, matching a compiler's "last flag wins"
    behavior) -- exactly ``merge_compile_config``'s real two-stage
    precedence.

    A same-flag conflict is decided by a token's position in that combined
    sequence, not by which ``compile.*`` field produced it, so folding
    ``defines``/``options`` independently per key (each key's own
    sources-then-checkout concatenation, done separately for ``defines``
    and separately for ``options``) is only an accident-free shortcut when
    both documents' contributions land in the SAME field, or when only ONE
    of the two documents contributes anything among ``std``/``defines``/
    ``options`` at all (then there is no second document's token to
    mis-order against). It silently picks the wrong winner whenever the two
    documents disagree via DIFFERENT fields -- checkout ``defines: [A]`` +
    sources ``options: [-DA=2]``, with neither setting ``std``, resolves
    via independent per-key folding to ``options: [-DA=2]`` `then`
    ``defines: [A]`` (rendered as ``-DA=2`` before checkout's own ``-DA``,
    so sources's redefinition wins the compiler's last-flag-wins rule) --
    the reverse of the documented precedence. The original fix here only
    widened the trigger to "a ``compile.std`` value co-occurs with a
    ``defines``/``options`` value **on either document**", which happened
    to also cover every ``std``-involving cross-field combination (``std``
    vs ``defines``, ``std`` vs ``options``) but still missed the
    defines-vs-options cross-key case entirely, since neither document sets
    ``std`` there (fresh Codex review evidence, PR #1222 tenth round). The
    general invariant this function now enforces: whenever BOTH documents
    each contribute at least one of ``std``/``defines``/``options`` (in any
    combination of which field each uses), all three are folded together as
    one atomic per-document token sequence -- there is no narrower,
    field-combination-specific condition that independent per-key folding
    can get right in general once both sides are contributing. Verified
    directly: checkout ``std: c++17`` + sources ``options: [-std=c++23]``
    resolves to ``('-std=c++23', '-std=c++17')`` (checkout wins); checkout
    ``defines: [A]`` + sources ``options: [-DA=2]`` resolves to
    ``('-DA=2', '-DA')`` (checkout wins); checkout ``std: c++17`` + sources
    ``defines: [A]`` (no ``options`` anywhere) resolves to
    ``('-DA', '-std=c++17')`` (checkout wins) -- a combination the
    ``std``-co-occurrence trigger already covered, kept here as a
    regression case for the general condition.

    Returns ``None`` when at most ONE document sets any of ``std``/
    ``defines``/``options`` -- either both are empty (a pure no-op, the
    joint fold would compute an empty token list regardless), or only one
    side contributes anything, in which case that side's own value is
    already correctly resolved by its own independent per-key rule and
    folding it into ``options`` would needlessly lose its legible
    structured-key representation (e.g. a lone checkout ``compile.std``
    would otherwise be rewritten into an ``options: ["-std=..."]`` entry
    for no ordering benefit). Otherwise returns the replacement ``options``
    field (possibly empty, when both sides set only an equal, redundant
    ``std`` whose tokens fully overlap -- callers still treat a present but
    empty ``options`` list as "nothing to apply")."""

    def _tokens(blk: dict[str, object]) -> list[str]:
        std = blk.get("std")
        return compile_config_argv_tokens(
            str(std) if std else None,
            _as_compile_field_list(blk.get("defines")),
            _as_compile_field_list(blk.get("options")),
        )

    def _any_compile_field(blk: dict[str, object]) -> bool:
        return (
            bool(blk.get("std"))
            or bool(_as_compile_field_list(blk.get("defines")))
            or bool(_as_compile_field_list(blk.get("options")))
        )

    if not (_any_compile_field(checkout_blk) and _any_compile_field(sources_blk)):
        return None
    return {"options": _tokens(sources_blk) + _tokens(checkout_blk)}


def _config_define_name(entry: str) -> str:
    """The macro name a ``.abicheck.yml`` ``compile.defines`` entry defines.

    Config entries are validated as single argv atoms but are *not* run
    through :func:`~abicheck.macro_definition.parse_macro_definition` -- a
    pre-existing project config may legitimately hold a spelling the
    stricter CLI grammar rejects, and ADR-074 does not retroactively
    invalidate one. Only the name is needed here (to decide whether a CLI
    ``-D`` overrides this entry), so it is taken the same way: everything
    before the first ``=``.
    """
    return entry.partition("=")[0]


def _parse_context_defines(cli_ctx: CompileContext) -> tuple[MacroDefinition, ...]:
    """The already-validated ``CompileContext.defines`` spellings, re-parsed
    into value objects. Re-parsing is cheap and keeps :mod:`macro_definition`
    the single grammar: the Click callback validated them at parse time, and
    a non-CLI caller that set the field by hand gets the identical check.

    Within-tier duplicates are collapsed here (last-wins, ADR-074 D3) so the
    "exactly one -D per macro name" invariant holds for ``-DA=1 -DA=2`` on one
    command line too, not only across the config/CLI tiers."""
    return merge_macro_definitions((), parse_macro_definitions(cli_ctx.defines))


def apply_cli_defines(
    cli_ctx: CompileContext, config_defines: Sequence[str]
) -> CompileContext:
    """Append the CLI ``-D/--define`` tokens to *cli_ctx*'s pass-through
    tokens, for the paths that have no ``compile:`` block to fold against
    (no config found, or an auto-discovered config that failed to parse).

    *config_defines* exists so the signature is the same shape as the real
    fold; it is empty on every current call site, and a non-empty value
    would mean the caller had a config after all.
    """
    definitions = merge_macro_definitions(
        parse_macro_definitions(config_defines), _parse_context_defines(cli_ctx)
    )
    if not definitions:
        return cli_ctx
    return dataclasses.replace(
        cli_ctx,
        gcc_option_tokens=cli_ctx.gcc_option_tokens
        + tuple(macro_definition_tokens(definitions)),
    )


def merge_compile_config(
    cli_ctx: CompileContext,
    cli_includes: tuple[Path, ...],
    build_config: Path | None,
    sources: Path | None = None,
    *,
    frontend_explicit: bool = False,
    nostdinc_explicit: bool = False,
    frontend_context_explicit: bool = False,
    config_explicit: bool | None = None,
    config_overrides: Mapping[str, object] | None = None,
    notify: Callable[[str], None] | None = None,
) -> tuple[CompileContext, tuple[Path, ...]]:
    """Fold a ``.abicheck.yml`` ``compile:`` block into the CLI compile context.

    The single resolver shared by ``compare`` / ``dump`` / ``scan`` (ADR-037 D3):
    precedence is CLI > config (ADR-035 D6.1 / ADR-037 D4) — a per-field CLI value
    overrides config, an unset CLI field inherits it. The config's ``std`` +
    ``defines`` synthesize literal ``-std=…``/``-D…`` argv entries only when
    ``cli_ctx.gcc_options`` is unset (the removed ``--gcc-options`` CLI flag
    is gone, CLI audit PR 5/5, so this is now always the case from the CLI —
    the field stays as an internal-composition-only escape hatch); those
    synthesized tokens are prepended *before* any CLI ``--compiler-option``/
    ``--compiler-option`` tokens, not appended after, so an explicit CLI ``-std=``/
    ``-D`` still wins the way a compiler resolves a repeated flag (Codex
    review: appending after silently let config override an explicit CLI
    token once ``--gcc-options`` -- the flag that used to suppress this
    synthesis entirely -- was removed). ``include_dirs`` (resolved against
    the *project root* -- ``config_paths.project_root_for_config()``, not
    necessarily the config file's own directory: a config discovered under
    ``.github/`` or ``.github/abicheck/`` is still anchored to the project
    root those directories live in, not to ``.github`` itself) are appended
    *after* the CLI ``-I`` so explicit roots keep search precedence.
    Returns the merged ``(CompileContext, includes)``.

    The config is the explicit ``--config`` when given, else the ``.abicheck.yml``
    auto-discovered at the ``--sources`` tree root — so a source-tree scan honors
    the project's ``compile:`` block for L2 the same way ``embed_build_source``
    honors its other non-executable settings for L3-L5 (Codex review). Only the
    non-executable ``compile:`` block is read here; ``build.query`` still requires
    an explicit trusted ``--config`` (ADR-032 D5). ``compile.compiler`` gets the
    identical trust gate for the identical reason: it names an executable to
    invoke, so an auto-discovered config never selects one (CodeRabbit review,
    PR #1146; see the ``compile.compiler`` handling below).

    A parse error is fail-loud for an **explicit** ``--config`` (:class:`CompileConfigError`)
    — otherwise an L2-only dump/scan with no ``--sources`` would silently drop the
    intended ``compile:`` settings and still exit 0 — but best-effort (warn +
    CLI-only fallback) for an **auto-discovered** config the user didn't bind to.
    Loudness follows ``config_explicit`` when the caller states it (a
    front end that already selected the config through
    ``config_paths.resolve_project_config`` knows whether it was named or
    discovered), else ``build_config is not None``.

    ``config_explicit`` narrowly overrides only the ``compile.compiler``
    trust gate documented below, for a caller that has *already* resolved
    ``build_config`` through its own auto-discovery (e.g. a cwd-upward
    ``discover_project_config()``/``discover_build_config()`` call made
    ahead of this one, for severity/scope/suppression resolution). Passing
    the resulting non-``None`` discovered path straight through as
    ``build_config`` without this parameter would make the ``build_config
    is not None`` inference the trust gate used to rely on directly read as
    "explicitly trusted", defeating that gate for exactly the untrusted,
    auto-discovered config it exists to distrust (Codex review, fresh
    evidence — real finding on PR #1154, confirmed exploitable end to end
    against ``compare``'s own such caller). Leave unset (``None``) when
    ``build_config`` is still the raw, unresolved CLI value (the common
    case, and what every pre-existing caller passes) — the historical
    ``build_config is not None`` inference is exactly right there.

    *config_overrides* (ADR-091 D4, ``--set``) are laid over the file before
    the ``compile:`` block is read, through the same
    :func:`~abicheck.buildsource.build_config_overrides.apply_config_overrides`
    loader every other ``.abicheck.yml`` consumer uses, so ``--set
    compile.K=V`` and ``compile: {K: V}`` in the file resolve identically. With
    no file, the overrides alone supply the block. A ``compile.compiler`` set
    by an override is an explicit operator statement, so it passes the trust
    gate even when the file was auto-discovered.
    """
    from ..config_paths import project_root_for_config
    from ..dry_run_estimate import CompileContext
    from .extraction import discover_build_config, load_build_config

    warn = notify or _stderr

    explicit_config = build_config is not None
    cfg = build_config if explicit_config else discover_build_config(sources)
    if cfg is None and not config_overrides:
        # ADR-074: the CLI's own -D/--define definitions still have to reach the
        # frontend when there is no config at all -- fold against an empty
        # config-defines list rather than returning the raw context, which would
        # silently drop every -D on a project with no .abicheck.yml.
        return apply_cli_defines(cli_ctx, ()), cli_includes
    # An operator-named config: trusted to select `compile.compiler`, and
    # fails loud when malformed. `config_explicit` states it when known.
    trust_compiler_selection = (
        explicit_config if config_explicit is None else config_explicit
    )
    # ADR-091 D4: a `--set compile.compiler=...` is the operator's own
    # statement, trusted whatever file (if any) it is laid over.
    compiler_stated = bool(config_overrides and "compile.compiler" in config_overrides)

    try:
        bc = load_build_config(cfg, config_overrides=config_overrides)
    except ValueError as exc:
        if trust_compiler_selection or cfg is None:
            # An *explicit* --config the user pointed at must fail loudly: for an
            # L2-only dump/scan (no --sources/--build-info) nothing reloads it
            # downstream, so a warn-and-fallback would silently drop the intended
            # compile.std/defines/sysroot/frontend and still exit 0 (Codex review).
            # A usage error (CompileConfigError), not an operational one, so it
            # exits 64, not 1 — a bad .abicheck.yml is a usage error (ADR-043).
            raise CompileConfigError(f"cannot parse build config {cfg}: {exc}") from exc
        # An *auto-discovered* config stays best-effort: a malformed file found by
        # walking up from cwd / the --sources root shouldn't fail a run the user
        # didn't ask to bind to it. Warn so it isn't silently ignored; the real
        # downstream load (embed_build_source, when --sources is given) still
        # surfaces it as a clean ClickException.
        warn(
            f"warning: could not parse auto-discovered {cfg}; using CLI compile "
            f"context only ({exc})."
        )
        return apply_cli_defines(cli_ctx, ()), cli_includes
    # The project root a relative `compile.include_dirs` entry resolves
    # against — cfg.parent for a root-level .abicheck.yml (unchanged), but
    # the directory containing .github/ for a config discovered there
    # (config_paths.py's own docstring has the full reasoning; Codex review,
    # fresh evidence from the .github/ discovery feature landing this base
    # was wrong for).
    base = project_root_for_config(cfg) if cfg is not None else Path.cwd()

    # CLI > config: an explicit --ast-frontend wins even when it is "auto" (the
    # documented escape hatch to bypass a pinned config frontend); only a *default*
    # "auto" inherits the config's frontend (Codex review).
    frontend = (
        cli_ctx.frontend
        if (frontend_explicit or cli_ctx.frontend != "auto")
        else (bc.compile_frontend or "auto")
    )
    gcc_options: str | None
    gcc_option_tokens = cli_ctx.gcc_option_tokens
    if cli_ctx.gcc_options is not None:
        gcc_options = cli_ctx.gcc_options
        # The internal-composition escape hatch still has to carry the CLI's
        # own -D (ADR-074): there is no config-defines synthesis on this
        # branch to fold them into, so they are appended directly.
        gcc_option_tokens = gcc_option_tokens + tuple(
            macro_definition_tokens(_parse_context_defines(cli_ctx))
        )
    else:
        # Config fields are structured metadata, not a shell-like option string.
        # Keep each synthesized flag as one literal argv entry so whitespace inside
        # a define/std value cannot be shlex-split into additional compiler
        # options (for example plugin-loading flags). Phase 7 (compile.options
        # — the demoted --compiler-option): raw pass-through tokens, appended
        # after std/defines synthesis and before any surviving CLI
        # --compiler-option tokens (scan only, since compare/dump no longer
        # have the flag), same "config first, CLI wins a repeated flag"
        # precedence as std/defines above.
        # ADR-074 D3: a macro the CLI defined is dropped from the config's own
        # position and re-emitted last (see `apply_cli_defines`), so exactly one
        # -D per macro reaches the frontend and the CLI value wins even against
        # a raw -DNAME smuggled through `compile.options`. Config defines for
        # every *other* macro keep their exact position and behavior, so a run
        # with no -D is byte-identical to before.
        cli_definitions = _parse_context_defines(cli_ctx)
        cli_named = {d.name for d in cli_definitions}
        config_tokens = compile_config_argv_tokens(
            bc.compile_std,
            [d for d in bc.compile_defines if _config_define_name(d) not in cli_named],
            bc.compile_options,
        )
        config_tokens += macro_definition_tokens(cli_definitions)
        gcc_options = None
        # CLI > config (same precedence every other field in this function
        # follows): config-synthesized tokens go *first* so an explicit CLI
        # --compiler-option token appended after it is the one a
        # compiler actually honors for a repeated flag like -std= (Codex
        # review: appending config tokens *after* CLI ones silently let
        # `compile.std`/`compile.defines` override an explicit CLI
        # --compiler-option=-std=... once the old --gcc-options scalar --
        # which used to suppress this synthesis entirely -- was removed).
        gcc_option_tokens = tuple(config_tokens) + gcc_option_tokens
    sysroot = (
        cli_ctx.sysroot
        if cli_ctx.sysroot is not None
        else (Path(bc.compile_sysroot) if bc.compile_sysroot else None)
    )
    # CLI > config: an explicit --nostdinc/--no-nostdinc wins in *either*
    # direction; an unset flag inherits the config value (Codex review).
    nostdinc = cli_ctx.nostdinc if nostdinc_explicit else bool(bc.compile_nostdinc)
    # Phase 7 (compile.compiler — the demoted --compiler/--compiler-prefix
    # pair, merged into one config spelling): only consulted when the CLI
    # gave neither (still possible on `scan`, which keeps both flags). A
    # value ending in "-" is a cross-toolchain prefix; anything else is a
    # full compiler path.
    #
    # Security (CodeRabbit review, PR #1146): this selects the executable
    # invoked for header extraction, the same "arbitrary command from a
    # config the operator didn't choose" shape ADR-032 D5 already gates for
    # `build.query` — an auto-discovered `.abicheck.yml` (found by directory
    # search, e.g. inside a fork PR's own branch content in CI) is never
    # trusted to name it; only a config the operator explicitly passed via
    # `--config` may. Ignored-not-silently-dropped: a clear diagnostic
    # explains the skip, mirroring `build.query`'s own
    # "ignored from auto-discovered .abicheck.yml" message.
    gcc_path = cli_ctx.gcc_path
    gcc_prefix = cli_ctx.gcc_prefix
    if gcc_path is None and gcc_prefix is None and bc.compile_compiler:
        if not (trust_compiler_selection or compiler_stated):
            warn(
                "warning: compile.compiler ignored from auto-discovered "
                f"{cfg}; pass a trusted config with --config to permit "
                "selecting a compiler executable."
            )
        elif bc.compile_compiler.endswith("-"):
            gcc_prefix = bc.compile_compiler
        else:
            gcc_path = bc.compile_compiler
    # Phase 7 (compile.frontend_context — the demoted --frontend-context):
    # CLI > config, same explicitness-gated precedence as --nostdinc/
    # --ast-frontend above. `scan` still carries a real Click default of
    # "host" for this flag with no explicitness tracking of its own, so it
    # always passes `frontend_context_explicit=False` here too -- a purely
    # additive fallback with no prior config key to have collided with.
    frontend_context = (
        cli_ctx.frontend_context
        if frontend_context_explicit
        else (bc.compile_frontend_context or cli_ctx.frontend_context)
    )
    merged = CompileContext(
        gcc_path=gcc_path,
        gcc_prefix=gcc_prefix,
        gcc_options=gcc_options,
        gcc_option_tokens=gcc_option_tokens,
        # Carried through, not dropped (CodeRabbit review): the rendered `-D`
        # tokens above are what the frontend consumes, but the field is what
        # the resolved request and provenance report as *asked for*. Emptying
        # it here left a context whose tokens said `-DA` and whose `defines`
        # said nothing -- and, since `release_public_surface._compile_options`
        # now keys on it, a context that had been folded and one that had not
        # would have hashed alike.
        defines=cli_ctx.defines,
        sysroot=sysroot,
        nostdinc=nostdinc,
        frontend=frontend,
        frontend_context=frontend_context,
    )
    includes = tuple(cli_includes) + tuple(
        (base / p) if not Path(p).is_absolute() else Path(p)
        for p in bc.compile_include_dirs
    )
    return merged, includes


def resolve_compile_context(
    *,
    sysroot: Path | None,
    nostdinc: bool,
    header_backend: str,
    includes: tuple[Path, ...],
    build_config: Path | None,
    sources: Path | None = None,
    frontend_context: str = "host",
    compiler_path: str | None = None,
    compiler_prefix: str | None = None,
    compiler_option_tokens: tuple[str, ...] = (),
    defines: tuple[str, ...] = (),
    gcc_options: str | None = None,
    config_explicit: bool | None = None,
    nostdinc_explicit: bool = False,
    frontend_context_explicit: bool = False,
    config_overrides: Mapping[str, object] | None = None,
    notify: Callable[[str], None] | None = None,
) -> tuple[CompileContext, tuple[Path, ...]]:
    """The run's L2 compile context from its compile knobs, ``compile:`` folded in.

    The one resolution the compile-context family reaches (ADR-037 D3):
    ``compare``'s single-pair session, its ``--dry-run`` receipt, ``dump``
    and the release fan-out all call this, so their L2 context cannot drift.
    ``compiler_path``/``compiler_prefix``/``compiler_option_tokens`` map onto
    ``CompileContext``'s ``gcc_path``/``gcc_prefix``/``gcc_option_tokens``.
    The two ``*_explicit`` flags say the value was stated by the caller
    rather than defaulted (the CLI reads them off the Click parameter
    source); ``frontend_explicit`` is never set here, since no caller has a
    per-run ``--ast-frontend`` left to state.
    """
    from ..dry_run_estimate import CompileContext as _CompileContext

    return merge_compile_config(
        _CompileContext(
            gcc_path=compiler_path,
            gcc_prefix=compiler_prefix,
            gcc_options=gcc_options,
            gcc_option_tokens=tuple(compiler_option_tokens),
            defines=tuple(defines),
            sysroot=sysroot,
            nostdinc=nostdinc,
            frontend=header_backend,
            frontend_context=frontend_context,
        ),
        tuple(includes),
        build_config,
        sources=sources,
        frontend_explicit=False,
        nostdinc_explicit=nostdinc_explicit,
        frontend_context_explicit=frontend_context_explicit,
        config_explicit=config_explicit,
        config_overrides=config_overrides,
        notify=notify,
    )
