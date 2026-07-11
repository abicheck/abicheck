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

"""Reusable Click option groups.

Stacked-decorator helpers that bundle related ``compare`` options so the large
``cli.py`` stays under the AI-readiness file-size cap. Imported at the top of
``cli.py`` and applied to ``compare_cmd``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar, overload

import click

from .cli_params import (
    DEPTH_PARAM,
    POLICY_FILE_PARAM,
    SIDED_BUILD_INFO_PARAM,
    SIDED_PATH_PARAM,
    SIDED_SOURCES_PARAM,
)

if TYPE_CHECKING:
    from .service_scan import CompileContext

F = TypeVar("F", bound=Callable[..., object])


# ── ADR-040 Lever 1: side-aware option collapse ──────────────────────────────
#
# ``--old-X`` / ``--new-X`` / ``--X`` triples collapse to one repeatable ``--X``
# whose value carries an optional ``old=`` / ``new=`` prefix (:class:`SidedPathParam`
# returns ``(side, Path)`` pairs). The command bodies stay on their existing
# internal kwargs (``headers`` / ``old_headers_only`` / …) — the two helpers below
# translate the sided tuples back into those kwargs at the boundary, so the engine,
# the Tier-2 service, and the ABICC compat layer are untouched.


def split_sided_paths(
    pairs: Sequence[tuple[str, Path]],
) -> tuple[tuple[Path, ...], tuple[Path, ...], tuple[Path, ...]]:
    """Split ``(side, path)`` pairs into ``(both, old_only, new_only)`` tuples.

    Used by ``header`` / ``include`` (the "both-sides + per-side extra" model,
    where the both bucket is applied to each side and ``old=``/``new=`` add
    per-side overrides).
    """
    both: list[Path] = []
    old_only: list[Path] = []
    new_only: list[Path] = []
    for side, path in pairs:
        {"both": both, "old": old_only, "new": new_only}[side].append(path)
    return tuple(both), tuple(old_only), tuple(new_only)


def _split_sided_single(
    pairs: Sequence[tuple[str, Path]],
) -> tuple[Path | None, Path | None]:
    """Resolve ``(side, path)`` pairs to a single ``(old, new)`` per-side value.

    Used by ``sources`` / ``build-info`` (one pack per side): a bare/``both=``
    value applies to *both* sides, while ``old=``/``new=`` override that side.
    Last value wins if a side is given twice.
    """
    old: Path | None = None
    new: Path | None = None
    for side, path in pairs:
        if side in ("both", "old"):
            old = path
        if side in ("both", "new"):
            new = path
    return old, new


def _split_sided_base(
    pairs: Sequence[tuple[str, Path]],
) -> tuple[Path | None, Path | None, Path | None]:
    """Resolve ``(side, path)`` pairs to ``(both, old, new)`` single values.

    The "base + per-side" single-valued model (e.g. ``--pdb-path``): a bare/
    ``both=`` value is the shared base (applied to both sides unless overridden),
    while ``old=``/``new=`` set a per-side override. Last value wins per bucket.
    Unlike :func:`_split_sided_single`, ``both`` is kept as its own base value
    rather than fanned out — the downstream resolver applies the base per side.
    """
    both: Path | None = None
    old: Path | None = None
    new: Path | None = None
    for side, path in pairs:
        if side == "both":
            both = path
        elif side == "old":
            old = path
        else:
            new = path
    return both, old, new


def normalize_sided_options(kwargs: dict[str, object]) -> None:
    """Translate the sided ``header``/``include``/``sources``/``build_info``/
    ``debug_root``/``pdb``/``probe_matrix`` dests into the per-side kwargs the
    command bodies consume, in place (ADR-040 L1).

    Absent keys are left untouched, so this is safe to call on any command that
    composes only a subset of the sided families.
    """
    if "header" in kwargs:
        both, old, new = split_sided_paths(kwargs.pop("header"))  # type: ignore[arg-type]
        kwargs["headers"] = both
        kwargs["old_headers_only"] = old
        kwargs["new_headers_only"] = new
    if "include" in kwargs:
        both, old, new = split_sided_paths(kwargs.pop("include"))  # type: ignore[arg-type]
        kwargs["includes"] = both
        kwargs["old_includes_only"] = old
        kwargs["new_includes_only"] = new
    if "debug_root" in kwargs:
        both, old, new = split_sided_paths(kwargs.pop("debug_root"))  # type: ignore[arg-type]
        kwargs["debug_roots"] = both
        kwargs["debug_roots_old"] = old
        kwargs["debug_roots_new"] = new
    if "sources" in kwargs:
        old_s, new_s = _split_sided_single(kwargs.pop("sources"))  # type: ignore[arg-type]
        kwargs["old_sources"] = old_s
        kwargs["new_sources"] = new_s
    if "build_info" in kwargs:
        old_b, new_b = _split_sided_single(kwargs.pop("build_info"))  # type: ignore[arg-type]
        kwargs["old_build_info"] = old_b
        kwargs["new_build_info"] = new_b
    if "probe_matrix" in kwargs:
        old_p, new_p = _split_sided_single(kwargs.pop("probe_matrix"))  # type: ignore[arg-type]
        kwargs["probe_matrix_old"] = old_p
        kwargs["probe_matrix_new"] = new_p
    if "pdb" in kwargs:
        base_p, old_pp, new_pp = _split_sided_base(kwargs.pop("pdb"))  # type: ignore[arg-type]
        kwargs["pdb_path"] = base_p
        kwargs["old_pdb_path"] = old_pp
        kwargs["new_pdb_path"] = new_pp


# ── ADR-037 D3: shared option families ───────────────────────────────────────
#
# Every option family that more than one verdict-emitting command needs is
# declared **once** here as a decorator; commands compose the decorators instead
# of re-declaring the family inline. The ``cli-contract`` AI-readiness gate
# (ADR-037 D10.2/D10.4) and ``tests/test_cli_contract.py`` key on the tables at
# the bottom of this module (``FAMILY_FLAGS`` / ``VERDICT_EMITTING_COMMANDS`` /
# ``INTENTIONAL_SUBSET``), so keep those in sync when a family changes.
#
# Decorators apply bottom-up (Click reverses ``__click_params__``), so each
# helper lists its options in reverse of their displayed order — matching the
# existing ``build_source_*`` helpers below.


def two_sided_input_options(func: F) -> F:
    """Headers / includes / version labels, shared (`-H/-I` + per-side + version).

    Identical across ``compare`` / ``compare-release`` / ``appcompat`` /
    ``compare-release`` / ``appcompat``: a both-sides input plus an old-only / new-only override and
    a per-side version label. (``--lang`` and the ``--ast-frontend`` family stay
    inline.)
    """
    func = click.option(
        "--new-version",
        "new_version",
        default="new",
        show_default=True,
        help="Version label for new side (used when input is a .so file).",
    )(func)
    func = click.option(
        "--old-version",
        "old_version",
        default="old",
        show_default=True,
        help="Version label for old side (used when input is a .so file).",
    )(func)
    func = click.option(
        "-I",
        "--include",
        "include",
        multiple=True,
        type=SIDED_PATH_PARAM,
        help="Extra include directory for castxml. Applies to both sides; scope "
        "to one side with an 'old='/'new=' prefix, repeating the flag per side "
        "(e.g. --include old=inc1 --include new=inc2). Repeatable (ADR-040).",
    )(func)
    func = click.option(
        "-H",
        "--header",
        "header",
        multiple=True,
        type=SIDED_PATH_PARAM,
        help="Public header file or directory. Applies to both sides; scope to "
        "one side with an 'old='/'new=' prefix, repeating the flag per side "
        "(e.g. --header old=v1/foo.h --header new=v2/foo.h). Repeatable (ADR-040). "
        "Recommended for full ABI analysis; without headers, native binaries fall back to symbols-only mode. "
        "Scopes the ABI surface to declarations in these headers for ELF; on PE/Mach-O scoping is "
        "best-effort and falls back to the export table when castxml is unavailable or names don't match "
        "(e.g. MSVC C++ mangling). Validated for native binaries; ignored for snapshots.",
    )(func)
    return func


def release_input_options(func: F) -> F:
    """Per-side header/include/version for the *internal* release engine.

    ``compare_release_cmd`` is unregistered (ADR-037 D7): it is never parsed from
    the CLI, only ``ctx.invoke``-d from ``compare``'s directory/package dispatch
    with the already-normalised per-side kwargs (``headers`` / ``old_headers_only``
    / …). So it keeps the pre-ADR-040 per-side param surface — the side-aware
    ``--header``/``--include`` collapse (Lever 1) applies to the *user-facing*
    ``compare`` / ``appcompat`` commands, which normalise before dispatching here.
    These option spellings are inert (the command is not registered) and do not
    count against any flag budget or option-set snapshot.
    """
    func = click.option(
        "--new-version", "new_version", default="new", show_default=True,
        help="Version label for new side (used when input is a .so file).",
    )(func)
    func = click.option(
        "--old-version", "old_version", default="old", show_default=True,
        help="Version label for old side (used when input is a .so file).",
    )(func)
    func = click.option(
        "--new-include", "new_includes_only", multiple=True,
        type=click.Path(path_type=Path),
        help="Include dir for new side only.",
    )(func)
    func = click.option(
        "--old-include", "old_includes_only", multiple=True,
        type=click.Path(path_type=Path),
        help="Include dir for old side only.",
    )(func)
    func = click.option(
        "--new-header", "new_headers_only", multiple=True,
        type=click.Path(path_type=Path),
        help="Public header for new side only.",
    )(func)
    func = click.option(
        "--old-header", "old_headers_only", multiple=True,
        type=click.Path(path_type=Path),
        help="Public header for old side only.",
    )(func)
    func = click.option(
        "-I", "--include", "includes", multiple=True,
        type=click.Path(path_type=Path),
        help="Extra include directory (both sides).",
    )(func)
    func = click.option(
        "-H", "--header", "headers", multiple=True,
        type=click.Path(path_type=Path),
        help="Public header file or directory (both sides).",
    )(func)
    return func


def policy_options(func: F) -> F:
    """Verdict-classification policy + suppression file (`--policy`/`--policy-file`/`--suppress`).

    Shared verbatim by every verdict-emitting command. (``--policy`` accepting a
    *path* directly, folding ``--policy-file`` in, is a later-phase D4 change.)
    """
    func = click.option(
        "--suppress",
        type=click.Path(exists=True, path_type=Path),
        default=None,
        help="Suppression file (YAML) to filter known/intentional changes.",
    )(func)
    func = click.option(
        "--policy-file",
        "policy_file_path",
        type=POLICY_FILE_PARAM,
        default=None,
        help="YAML policy file with per-kind verdict overrides, or a built-in name "
        "(e.g. 'security'). Overrides --policy.",
    )(func)
    func = click.option(
        "--policy",
        "policy",
        type=click.Choice(
            ["strict_abi", "sdk_vendor", "plugin_abi"], case_sensitive=True
        ),
        default="strict_abi",
        show_default=True,
        help="Built-in policy profile for verdict classification. Ignored when "
        "--policy-file is given.",
    )(func)
    return func


def severity_options(func: F) -> F:
    """The severity preset + the four per-category overrides.

    ADR-037 D4 demotes the per-category flags into ``.abicheck.yml``'s
    ``severity:`` block (G22 Phase 5): they stay on the CLI as **hidden**
    overrides (a CLI value still beats config for a one-off run), but the visible
    surface keeps only ``--severity-preset``. The whole family remains a genuine
    shared decorator across ``compare`` / ``compare-release`` / ``appcompat`` so
    the contract gate (D10.2) still sees it composed once, not copy-pasted.
    """
    func = click.option(
        "--severity-addition",
        "severity_addition",
        type=click.Choice(["error", "warning", "info"], case_sensitive=True),
        default=None,
        hidden=True,
        help="Override severity for new public API additions (config: "
        "severity.addition). Beats the preset and config for this run.",
    )(func)
    func = click.option(
        "--severity-quality-issues",
        "severity_quality_issues",
        type=click.Choice(["error", "warning", "info"], case_sensitive=True),
        default=None,
        hidden=True,
        help="Override severity for quality issues like std symbol leaks (config: "
        "severity.quality_issues).",
    )(func)
    func = click.option(
        "--severity-potential-breaking",
        "severity_potential_breaking",
        type=click.Choice(["error", "warning", "info"], case_sensitive=True),
        default=None,
        hidden=True,
        help="Override severity for potential incompatibilities needing review "
        "(config: severity.potential_breaking).",
    )(func)
    func = click.option(
        "--severity-abi-breaking",
        "severity_abi_breaking",
        type=click.Choice(["error", "warning", "info"], case_sensitive=True),
        default=None,
        hidden=True,
        help="Override severity for clear ABI/API incompatibilities (config: "
        "severity.abi_breaking).",
    )(func)
    func = click.option(
        "--severity-preset",
        "severity_preset",
        type=click.Choice(["default", "strict", "info-only"], case_sensitive=True),
        default=None,
        help="Severity preset: 'default', 'strict', or 'info-only'. "
        "Controls exit codes and report labels. Per-category "
        "--severity-* options override the chosen preset.",
    )(func)
    return func


def scope_options(func: F) -> F:
    """Public-surface scoping (`--scope-public-headers/--no-`).

    The universally-shared toggle. ``--show-filtered`` (a ``compare``-only audit
    view) stays inline on ``compare`` rather than being forced onto commands that
    have no filtered-findings report to dump.
    """
    func = click.option(
        "--scope-public-headers/--no-scope-public-headers",
        "scope_public_headers",
        default=True,
        show_default=True,
        help="Restrict findings to the public-header ABI surface (ADR-024): "
        "changes to symbols/types not reachable from public-header-declared "
        "exported API are recorded as filtered, not reported. Internal-type "
        "leaks are never hidden. On by default; use --no-scope-public-headers "
        "to report every finding regardless of surface.",
    )(func)
    return func


#: Canonical ``--lang`` choice set + default. Declared once so the choice
#: *order* (shown in ``--help`` and error text) and case-insensitivity cannot
#: drift between commands — historically ``scan`` listed ``["c", "c++"]`` and
#: omitted ``case_sensitive=False`` while every other command used
#: ``["c++", "c"]`` with it (ADR-037 D3 parity).
LANG_CHOICES: tuple[str, ...] = ("c++", "c")
LANG_DEFAULT: str = "c++"


@overload
def lang_option(func: F) -> F: ...
@overload
def lang_option(*, help: str = ...) -> Callable[[F], F]: ...
def lang_option(
    func: F | None = None,
    *,
    help: str = "Language mode for the header backend.",
) -> F | Callable[[F], F]:
    """The shared ``--lang`` option (factory; usable bare or with ``help=``).

    A factory rather than a bare decorator only so each command can keep its own
    one-line ``help`` (``compare``/``dump`` say "header backend", ``appcompat``
    said "castxml", ``plugin-check`` notes it only applies when dumping binaries),
    while the *choice set*, *order*, *default*, and case-insensitivity live here
    once and therefore cannot drift (ADR-037 D3). Usable directly
    (``@lang_option``) or called (``@lang_option(help="…")``).
    """

    def deco(f: F) -> F:
        f = click.option(
            "--lang",
            "lang",
            default=LANG_DEFAULT,
            show_default=True,
            type=click.Choice(list(LANG_CHOICES), case_sensitive=False),
            help=help,
        )(f)
        return f

    return deco if func is None else deco(func)


def compile_context_options(func: F) -> F:
    """L2 header-AST compile context — the cross-toolchain + frontend family.

    The single source of truth for the flags that tell the header frontend how to
    parse the public headers: ``--ast-frontend`` (which frontend), the cross
    compiler (``--gcc-path``/``--gcc-prefix``), pass-through compiler flags
    (``--gcc-options``/``--gcc-option``), an alternate ``--sysroot``, and
    ``--nostdinc``. Shared verbatim by ``dump`` **and** ``scan`` so the two never
    drift (ADR-037 D3 parity; ADR-035 amendment — ``scan`` must be able to reach a
    real L2). Decorators apply bottom-up, so the options are listed in reverse of
    their displayed order. Dest names match the ``dumper.dump`` /
    :class:`~abicheck.service_scan.CompileContext` kwargs exactly.
    """
    func = click.option(
        "--nostdinc/--no-nostdinc",
        "nostdinc",
        default=False,
        help="Do not search the standard system include paths (suppresses the "
        "castxml/clang system-include auto-detection too). Paired form so an "
        "explicit --no-nostdinc on `scan` can override a config `compile.nostdinc: "
        "true` for a one-off run (CLI > config).",
    )(func)
    func = click.option(
        "--sysroot",
        "sysroot",
        type=click.Path(path_type=Path),
        default=None,
        help="Alternative system root directory for header resolution.",
    )(func)
    func = click.option(
        "--gcc-option",
        "gcc_option_tokens",
        multiple=True,
        help="A single extra compiler flag passed to the header frontend verbatim "
        "(repeatable; not whitespace-split). Use two for a flag + spaced value, "
        "e.g. --gcc-option=-include --gcc-option='some header.h'.",
    )(func)
    func = click.option(
        "--gcc-options",
        "gcc_options",
        default=None,
        help="Extra compiler flags passed through to the header frontend (split on "
        "whitespace). For a flag whose value contains spaces use --gcc-option.",
    )(func)
    func = click.option(
        "--gcc-prefix",
        "gcc_prefix",
        default=None,
        help="Cross-toolchain prefix (e.g. aarch64-linux-gnu-).",
    )(func)
    func = click.option(
        "--gcc-path",
        "gcc_path",
        default=None,
        help="Path to a GCC/G++ (or clang) cross-compiler binary.",
    )(func)
    func = click.option(
        "--ast-frontend",
        "header_backend",
        default="auto",
        show_default=True,
        type=click.Choice(["auto", "castxml", "clang"], case_sensitive=False),
        help="C/C++ AST frontend (ADR-037 D8): castxml (default schema reference) "
        "or clang (-ast-dump=json; for hosts where castxml is absent or its "
        "bundled frontend chokes). auto = castxml if present, else clang, with an "
        "automatic clang fallback on a castxml toolchain-version error. Env: "
        "ABICHECK_AST_FRONTEND.",
    )(func)
    return func


def merge_compile_config(
    cli_ctx: CompileContext,
    cli_includes: tuple[Path, ...],
    build_config: Path | None,
    sources: Path | None = None,
    *,
    frontend_explicit: bool = False,
    nostdinc_explicit: bool = False,
) -> tuple[CompileContext, tuple[Path, ...]]:
    """Fold a ``.abicheck.yml`` ``compile:`` block into the CLI compile context.

    The single resolver shared by ``compare`` / ``dump`` / ``scan`` (ADR-037 D3):
    precedence is CLI > config (ADR-035 D6.1 / ADR-037 D4) — a per-field CLI value
    overrides config, an unset CLI field inherits it. The config's ``std`` +
    ``defines`` synthesize literal ``-std=…``/``-D…`` argv entries only when the
    user did not pass ``--gcc-options``; ``include_dirs`` (resolved against the
    config's directory)
    are appended *after* the CLI ``-I`` so explicit roots keep search precedence.
    Returns the merged ``(CompileContext, includes)``.

    The config is the explicit ``--config`` when given, else the ``.abicheck.yml``
    auto-discovered at the ``--sources`` tree root — so a source-tree scan honors
    the project's ``compile:`` block for L2 the same way ``embed_build_source``
    honors its other non-executable settings for L3-L5 (Codex review). Only the
    non-executable ``compile:`` block is read here; ``build.query`` still requires
    an explicit trusted ``--config`` + ``--allow-build-query`` (ADR-032 D5).

    A parse error is fail-loud for an **explicit** ``--config`` (``ClickException``)
    — otherwise an L2-only dump/scan with no ``--sources`` would silently drop the
    intended ``compile:`` settings and still exit 0 — but best-effort (warn +
    CLI-only fallback) for an **auto-discovered** config the user didn't bind to.
    """
    from .buildsource.inline import discover_build_config, load_build_config
    from .service_scan import CompileContext

    explicit_config = build_config is not None
    cfg = build_config if explicit_config else discover_build_config(sources)
    if cfg is None:
        return cli_ctx, cli_includes

    try:
        bc = load_build_config(cfg)
    except ValueError as exc:
        if explicit_config:
            # An *explicit* --config the user pointed at must fail loudly: for an
            # L2-only dump/scan (no --sources/--build-info) nothing reloads it
            # downstream, so a warn-and-fallback would silently drop the intended
            # compile.std/defines/sysroot/frontend and still exit 0 (Codex review).
            raise click.ClickException(
                f"cannot parse build config {cfg}: {exc}"
            ) from exc
        # An *auto-discovered* config stays best-effort: a malformed file found by
        # walking up from cwd / the --sources root shouldn't fail a run the user
        # didn't ask to bind to it. Warn so it isn't silently ignored; the real
        # downstream load (embed_build_source, when --sources is given) still
        # surfaces it as a clean ClickException.
        click.echo(
            f"warning: could not parse auto-discovered {cfg}; using CLI compile "
            f"context only ({exc}).",
            err=True,
        )
        return cli_ctx, cli_includes
    base = cfg.parent

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
    else:
        # Config fields are structured metadata, not a shell-like option string.
        # Keep each synthesized flag as one literal argv entry so whitespace inside
        # a define/std value cannot be shlex-split into additional compiler
        # options (for example plugin-loading flags).
        config_tokens: list[str] = []
        if bc.compile_std:
            config_tokens.append(f"-std={bc.compile_std}")
        config_tokens += [f"-D{d}" for d in bc.compile_defines]
        gcc_options = None
        gcc_option_tokens = gcc_option_tokens + tuple(config_tokens)
    sysroot = (
        cli_ctx.sysroot
        if cli_ctx.sysroot is not None
        else (Path(bc.compile_sysroot) if bc.compile_sysroot else None)
    )
    # CLI > config: an explicit --nostdinc/--no-nostdinc wins in *either*
    # direction; an unset flag inherits the config value (Codex review).
    nostdinc = cli_ctx.nostdinc if nostdinc_explicit else bool(bc.compile_nostdinc)
    merged = CompileContext(
        gcc_path=cli_ctx.gcc_path,
        gcc_prefix=cli_ctx.gcc_prefix,
        gcc_options=gcc_options,
        gcc_option_tokens=gcc_option_tokens,
        sysroot=sysroot,
        nostdinc=nostdinc,
        frontend=frontend,
    )
    includes = tuple(cli_includes) + tuple(
        (base / p) if not Path(p).is_absolute() else Path(p)
        for p in bc.compile_include_dirs
    )
    return merged, includes


def resolve_compile_context(
    ctx: click.Context,
    *,
    gcc_path: str | None,
    gcc_prefix: str | None,
    gcc_options: str | None,
    gcc_option_tokens: tuple[str, ...],
    sysroot: Path | None,
    nostdinc: bool,
    header_backend: str,
    includes: tuple[Path, ...],
    build_config: Path | None,
    sources: Path | None = None,
) -> tuple[CompileContext, tuple[Path, ...]]:
    """Build the CLI :class:`CompileContext` and fold the config ``compile:`` block in.

    The single entry point the ``@compile_context_options`` family resolves to
    (ADR-037 D3): construct a :class:`~abicheck.service_scan.CompileContext` from
    the decorator's flags, then delegate to :func:`merge_compile_config` with the
    ``--ast-frontend`` / ``--nostdinc`` explicitness read from the Click parameter
    source (so an explicitly-typed value — even a default-looking ``auto`` — beats
    a pinned config one). ``compare`` / ``dump`` / ``scan`` all call this so their
    L2 compile context cannot drift.
    """
    from .service_scan import CompileContext

    cli_ctx = CompileContext(
        gcc_path=gcc_path,
        gcc_prefix=gcc_prefix,
        gcc_options=gcc_options,
        gcc_option_tokens=tuple(gcc_option_tokens),
        sysroot=sysroot,
        nostdinc=nostdinc,
        frontend=header_backend,
    )

    def _explicit(param: str) -> bool:
        return bool(
            ctx.get_parameter_source(param) == click.core.ParameterSource.COMMANDLINE
        )

    return merge_compile_config(
        cli_ctx,
        tuple(includes),
        build_config,
        sources=sources,
        frontend_explicit=_explicit("header_backend"),
        nostdinc_explicit=_explicit("nostdinc"),
    )


def output_options(
    formats: Sequence[str],
    *,
    default: str = "markdown",
    format_help: str = "Output format.",
    output_help: str | None = "Write output to this path (default: stdout).",
) -> Callable[[F], F]:
    """Factory for the ``--format`` / ``-o/--output`` pair.

    A factory rather than a bare decorator because the *set* of producible
    formats legitimately differs per command (``appcompat`` cannot emit
    sarif/junit, ``compare-release`` cannot emit html/review) — but the option
    *structure*, the ``-o/--output`` flag, and the contract live here once.
    """

    # ``help=None`` renders no help line in Click, so a single call covers both
    # the with-help and without-help cases without a ``**dict[str, object]``
    # unpack (which mypy can't reconcile with ``click.option``'s overloads).
    def deco(func: F) -> F:
        func = click.option(
            "-o",
            "--output",
            "output",
            type=click.Path(path_type=Path),
            default=None,
            help=output_help,
        )(func)
        func = click.option(
            "--format",
            "fmt",
            type=click.Choice(list(formats)),
            default=default,
            show_default=True,
            help=format_help,
        )(func)
        return func

    return deco


def verbose_option(func: F) -> F:
    """The universal ``-v/--verbose`` flag, defined once (ADR-037 D3).

    ``output_options`` already owns ``-o/--output``; verbose is the other flag
    nearly every command carries, and it had drifted to blank/inconsistent help
    across ~14 inline copies. One decorator keeps the spelling and help uniform.
    """
    func = click.option(
        "-v",
        "--verbose",
        is_flag=True,
        default=False,
        help="Enable verbose/debug output.",
    )(func)
    return func


def env_matrix_option(func: F) -> F:
    """The ``--env-matrix`` option: declared deployment constraints (ADR-020b).

    Defined here so ``cli.py`` stays under its size cap and any future
    front-end shares one spelling/help. The value stays a path; loading and
    validation happen in the Tier-2 service
    (:func:`abicheck.service.load_env_matrix`) so CLI and request-API callers
    surface identical errors.
    """
    func = click.option(
        "--env-matrix",
        "env_matrix_path",
        type=click.Path(exists=True, dir_okay=False, path_type=Path),
        default=None,
        help="Environment-matrix YAML declaring deployment constraints "
        "(ADR-020b). With runtime_floors (e.g. 'runtime_floors: {GLIBC: "
        '"2.28"}\'), a new symbol-version requirement is judged against '
        "the declared floor: at/below it -> compatible, above it -> "
        "breaking, instead of the default deployment-risk verdict.",
    )(func)
    return func


def set_input_options(func: F) -> F:
    """Set-input fan-out knobs: ``-j/--jobs`` / ``--dso-only`` / ``--output-dir``.

    ADR-037 D7 folds ``compare-release`` into ``compare`` via input-type
    dispatch: when ``compare``'s operands are directories or packages it fans out
    to a per-library comparison, and these three flags tune that fan-out (parallel
    jobs, executable filtering, per-library report directory). On single-file
    inputs they are a no-op and ``compare`` warns. Declared once here so the
    dispatch and the deprecated ``compare-release`` alias share one surface.
    Applied bottom-up, so listed in reverse of displayed order.
    """
    func = click.option(
        "--output-dir",
        "output_dir",
        type=click.Path(path_type=Path),
        default=None,
        help="Directory to write per-library reports (directory/package inputs only).",
    )(func)
    func = click.option(
        "--dso-only",
        "dso_only",
        is_flag=True,
        default=False,
        help="Only compare shared objects, skip executables (directory/package inputs only).",
    )(func)
    func = click.option(
        "-j",
        "--jobs",
        "jobs",
        type=int,
        default=0,
        show_default=True,
        help="Parallel library comparisons for directory/package inputs "
        "(0 = auto-detect CPU count, the default).",
    )(func)
    return func


def release_options(func: F) -> F:
    """Directory/package (release) comparison knobs, folded onto ``compare``.

    The release-only options the removed ``compare-release`` command exposed:
    package extraction (``--debug-info*``/``--devel-pkg*``), DSO selection
    (``--include-private-dso``/``--keep-extracted``), the removed-library gate, and
    the ADR-023 bundle/manifest analysis. They bite only when ``compare``'s
    operands are directories or packages (the per-library fan-out); on single-file
    inputs they are inert. Declared once here so ``compare`` and the internal
    release engine share one surface (ADR-037 D7). Applied bottom-up, so listed in
    reverse of displayed order.
    """
    func = click.option(
        "--no-bundle-analysis",
        "no_bundle_analysis",
        is_flag=True,
        default=False,
        help="Skip bundle-level cross-library analysis (debug/parity escape hatch). "
        "Bundle findings catch intra-bundle symbol removals, signature drift "
        "across DSO boundaries, type drift across siblings, provider migration, "
        "and manifest mismatches. (directory/package inputs only)",
    )(func)
    func = click.option(
        "--bundle-cohort",
        "bundle_cohorts",
        multiple=True,
        metavar="PREFIX",
        help="Declare a co-versioned library cohort by name prefix (e.g. "
        "'libfoo_'). Repeatable. Enables the BUNDLE_SONAME_SKEW check. "
        "(directory/package inputs only)",
    )(func)
    func = click.option(
        "--bundle-system-providers",
        "bundle_system_providers",
        default="",
        help="Comma-separated extra sonames to treat as system-provided "
        "(extends the built-in libc/libstdc++/libgcc/libtbb allow-list). "
        "(directory/package inputs only)",
    )(func)
    func = click.option(
        "--manifest",
        "manifest_path",
        type=click.Path(exists=True, path_type=Path),
        default=None,
        help="ABI instantiation manifest (YAML/JSON) listing symbols the release "
        "publicly promises (ADR-023). (directory/package inputs only)",
    )(func)
    func = click.option(
        "--keep-extracted",
        "keep_extracted",
        is_flag=True,
        default=False,
        help="Keep extracted temporary files for debugging. "
        "(directory/package inputs only)",
    )(func)
    func = click.option(
        "--include-private-dso",
        "include_private_dso",
        is_flag=True,
        default=False,
        help="Include private (non-public) shared objects from non-standard "
        "paths. (directory/package inputs only)",
    )(func)
    func = click.option(
        "--devel-pkg2",
        "devel_pkg2",
        type=click.Path(exists=True, path_type=Path),
        default=None,
        help="Development package with headers for the new side. "
        "(directory/package inputs only)",
    )(func)
    func = click.option(
        "--devel-pkg1",
        "devel_pkg1",
        type=click.Path(exists=True, path_type=Path),
        default=None,
        help="Development package with headers for the old side. "
        "(directory/package inputs only)",
    )(func)
    func = click.option(
        "--debug-info2",
        "debug_info2",
        type=click.Path(exists=True, path_type=Path),
        default=None,
        help="Debug info package for the new side (RPM/Deb/tar). "
        "(directory/package inputs only)",
    )(func)
    func = click.option(
        "--debug-info1",
        "debug_info1",
        type=click.Path(exists=True, path_type=Path),
        default=None,
        help="Debug info package for the old side (RPM/Deb/tar). "
        "(directory/package inputs only)",
    )(func)
    func = click.option(
        "--fail-on-removed-library/--no-fail-on-removed-library",
        "fail_on_removed",
        default=False,
        help="Exit 8 when a library present in old_dir is absent in new_dir. "
        "(directory/package inputs only)",
    )(func)
    return func


def debug_resolution_options(func: F) -> F:
    """Separate-debug-file resolution (ADR-021a): roots + debuginfod + format.

    Currently a ``compare``-only family — it resolves *local* ELF debug
    artifacts, which the package-oriented (``compare-release``) and
    snapshot-oriented (``appcompat``) commands do not take. It
    lives here so the moment a second command needs it there is one definition to
    compose, not a copy to drift (ADR-037 D3).
    """
    func = click.option(
        "--dwarf",
        "debug_format",
        flag_value="dwarf",
        hidden=True,
        help="Force DWARF debug format for both sides (ELF only).",
    )(func)
    func = click.option(
        "--ctf",
        "debug_format",
        flag_value="ctf",
        hidden=True,
        help="Force CTF debug format for both sides (ELF only).",
    )(func)
    func = click.option(
        "--btf",
        "debug_format",
        flag_value="btf",
        default=None,
        hidden=True,
        help="Force BTF debug format for both sides (ELF only).",
    )(func)
    func = click.option(
        "--debug-format",
        "debug_format_opt",
        type=click.Choice(["auto", "dwarf", "btf", "ctf"], case_sensitive=False),
        default=None,
        help="Force the ELF debug format for both sides (auto=pick best available). "
        "Supersedes the individual --btf/--ctf/--dwarf flags.",
    )(func)
    func = click.option(
        "--debuginfod-url",
        "debuginfod_url",
        default=None,
        help="debuginfod server URL (overrides DEBUGINFOD_URLS env var).",
    )(func)
    func = click.option(
        "--debuginfod",
        is_flag=True,
        default=False,
        help="Enable debuginfod network resolution for debug info (opt-in).",
    )(func)
    func = click.option(
        "--debug-root",
        "debug_root",
        multiple=True,
        type=SIDED_PATH_PARAM,
        help="Directory containing separate debug files (build-id trees, "
        "path-mirror, dSYM bundles). Applies to both sides; scope to one with an "
        "'old='/'new=' prefix, repeating the flag per side "
        "(e.g. --debug-root old=dbg1 --debug-root new=dbg2). Repeatable (ADR-040).",
    )(func)
    func = click.option(
        "--dwarf-only",
        is_flag=True,
        default=False,
        help="Force DWARF-only mode for both sides: use DWARF debug info "
        "as primary data source even when headers are available.",
    )(func)
    return func


def adr027_compare_options(func: F) -> F:
    """Add the ADR-027 API-surface-intelligence options to ``compare``.

    ``--pattern-verdicts`` / ``--explain-patterns`` (A4 modulation) and
    ``--surface-metrics`` (A1/D1.2 metric drift). Decorators apply bottom-up, so
    they are listed here in reverse of their displayed order.
    """
    func = click.option(
        "--surface-metrics",
        "surface_metrics",
        is_flag=True,
        default=False,
        help="Emit aggregate public-surface metric drift (ADR-027): "
        "public_surface_grew/shrank, undocumented_export_ratio_increased. "
        "Informational (COMPATIBLE).",
    )(func)
    func = click.option(
        "--explain-patterns",
        "explain_patterns",
        is_flag=True,
        default=False,
        help="Print idiom evidence behind each modulation (implies "
        "--pattern-verdicts).",
    )(func)
    func = click.option(
        "--pattern-verdicts/--no-pattern-verdicts",
        "pattern_verdicts",
        default=False,
        help="Modulate verdicts with idiom/anti-pattern evidence (ADR-027): "
        "demote opaque-pointer/PIMPL-hidden layout changes (header-aware only) "
        "and raise breaks when an opacity/handle guarantee is lost. Disclosed in "
        "the pattern_modulations ledger; reversible.",
    )(func)
    return func


def build_source_dump_options(func: F) -> F:
    """Add the ``--build-info`` / ``--sources`` embed options to ``dump``.

    Source-tree-centric inputs (ADR-028..033 amendment): ``--sources`` is a
    source checkout — L4 source ABI replay and the L5 graph are run inline and
    embedded; ``--build-info`` is an optional build dir / ``compile_commands.json``
    / pre-captured pack supplying L3 (auto-discovered inside the source tree when
    omitted). A path that is itself a pack directory from ``abicheck collect``
    is loaded as that pack instead. Embedding makes the ``.abi.json``
    self-contained, so a later ``compare old.json new.json`` carries the facts
    with no out-of-band directories. Applied bottom-up, so listed in reverse of
    display.
    """
    from pathlib import Path

    func = click.option(
        "--depth",
        "depth",
        type=DEPTH_PARAM,
        default=None,
        help="Unified evidence-depth dial (ADR-037 D5; same vocabulary as "
        "`compare`/`scan --depth`): symbols=L0/L1 only, headers=+L2 AST (default), "
        "build=+L3 build context, source=+L4 replay & the L5 graph, full=deepest. "
        "--max == --depth full.",
    )(func)
    func = click.option(
        "--max",
        "max_depth",
        is_flag=True,
        default=False,
        help="Shorthand for --depth full (collect the deepest evidence available).",
    )(func)
    func = click.option(
        "--allow-build-query",
        "allow_build_query",
        is_flag=True,
        default=False,
        hidden=True,  # deprecated no-op (ADR-032 amended): build query is now automatic
        help="Deprecated and ignored. Build-system queries now run automatically "
        "when --sources is given (abicheck infers and runs cmake/make/bazel "
        "itself); no flag is needed. Kept as a no-op for backward compatibility.",
    )(func)
    func = click.option(
        "--config",
        "build_config",
        type=click.Path(exists=True, dir_okay=False, path_type=Path),
        default=None,
        help="Path to the project `.abicheck.yml` (ADR-037 D4): build system, "
        "query command, compile-DB location, plus the stable severity/scope/"
        "suppression/source settings. Defaults to `.abicheck.yml` at the "
        "--sources tree root for non-executing settings; build.query runs only "
        "from an explicit --config.",
    )(func)
    func = click.option(
        "--build-compile-db",
        "build_compile_db",
        default=None,
        metavar="GLOB",
        help="Where a build/query lands its compile_commands.json, relative to "
        "--sources (e.g. 'build/compile_commands.json'). CLI equivalent of "
        "`.abicheck.yml` build.compile_db; overrides it when both are given.",
    )(func)
    func = click.option(
        "--build-query",
        "build_query",
        default=None,
        metavar="CMD",
        help="Override the inferred build-system query command that emits a "
        "compile DB without a full build (e.g. 'cmake -S . -B build "
        "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON'). CLI equivalent of `.abicheck.yml` "
        "build.query — runs automatically as trusted operator input. Usually "
        "unnecessary: with just --sources, abicheck infers and runs the query "
        "itself.",
    )(func)
    func = click.option(
        "--sources",
        "sources",
        type=click.Path(exists=True, path_type=Path),
        default=None,
        help="Source checkout to run L4 source ABI replay + the L5 graph over "
        "and embed inline. (A pack directory from `abicheck collect` is loaded "
        "as that pack instead.)",
    )(func)
    func = click.option(
        "--build-info",
        "build_info",
        type=click.Path(exists=True, path_type=Path),
        default=None,
        help="Optional L3 build context: a build dir, a compile_commands.json, "
        "or a pre-captured pack. Auto-discovered inside the --sources tree when "
        "omitted.",
    )(func)
    func = click.option(
        "--inputs",
        "inputs_pack",
        type=click.Path(exists=True, file_okay=False, path_type=Path),
        default=None,
        help="A Flow-2 `abicheck_inputs/` pack emitted by the build (abicheck-cc "
        "wrapper or the Clang facts plugin). Its L3/L4/L5 facts are folded into "
        "this dump inline and the source surface is linked against the binary's "
        "exports — the same result as a follow-up `abicheck merge`, in one "
        "command. No compiler frontend is re-run.",
    )(func)
    return func


def evidence_options(func: F) -> F:
    """The shared two-sided evidence family (ADR-037 D3's ``@evidence_options``).

    The single source of truth for the depth/source/build-info surface a
    *two-sided* verdict command exposes: ``--depth`` / ``--max`` plus the per-side
    ``--old/new-sources`` and ``--old/new-build-info`` packs. ``dump`` is
    single-sided (one artifact, plus the build-query knobs) so it composes the
    sibling :func:`build_source_dump_options` instead — they are deliberately not
    one decorator because their surfaces differ (per-side vs build-query), which
    is why ``evidence`` is a registered-but-not-required family (only commands
    that take source depth compose it).

    By default ``compare old.json new.json`` reads build-info + source facts
    **embedded** in each snapshot (single-artifact UX). The optional side-aware
    ``--build-info`` and ``--sources`` (ADR-040) point at out-of-band pack
    directories to supply or override those facts — for both sides, or per side
    with an ``old=``/``new=`` prefix; ``--depth`` selects how deep the inline
    collection runs (ADR-037 D5). All folded into the verdict as ordinary
    findings, never overriding artifact-backed ABI verdicts (ADR-028 D3).
    Applied bottom-up, so listed in reverse of displayed order.
    """
    func = click.option(
        "--max",
        "max_depth",
        is_flag=True,
        default=False,
        help="Shorthand for --depth full (collect the deepest evidence available).",
    )(func)
    func = click.option(
        "--depth",
        "depth",
        type=DEPTH_PARAM,
        default=None,
        help="Unified evidence-depth dial (ADR-037 D5): symbols=L0/L1 only, "
        "headers=+L2 AST (default), build=+L3, source=+L4 replay & the L5 graph, "
        "full=deepest. --max == --depth full. Deeper-than-headers needs "
        "--sources or --build-info.",
    )(func)
    func = click.option(
        "--sources",
        "sources",
        multiple=True,
        type=SIDED_SOURCES_PARAM,
        help="L4/L5 source: a raw source checkout (collected inline at --depth, "
        "embedding build/source/graph facts) or a pre-built `collect` pack, "
        "overriding embedded. Applies to both sides; scope to one with an "
        "'old='/'new=' prefix, repeating the flag per side "
        "(e.g. --sources old=src_v1 --sources new=src_v2) (ADR-040).",
    )(func)
    func = click.option(
        "--build-info",
        "build_info",
        multiple=True,
        type=SIDED_BUILD_INFO_PARAM,
        help="Out-of-band L3 build-info: a build dir, a compile_commands.json, or "
        "a pack, overriding embedded. Applies to both sides; scope to one with an "
        "'old='/'new=' prefix, repeating the flag per side "
        "(e.g. --build-info old=b1 --build-info new=b2) (ADR-040).",
    )(func)
    return func


#: Back-compat alias for the pre-ADR-037-D3 name. ``evidence_options`` is the
#: canonical spelling (the D3 table); this keeps existing imports working.
build_source_compare_options = evidence_options


# ── ADR-037 D10: contract metadata (single source of truth for the gate) ──────
#
# The ``cli-contract`` AI-readiness gate (D10.2 decorator coverage, D10.4
# one-default-per-flag) and its test mirror key on these tables. Keeping them
# beside the decorators means adding/renaming a family is a one-place edit.

#: Family name → the long ``--flag`` names that family contributes. The gate
#: checks a verdict-emitting command carries the *whole* family (composed via the
#: matching decorator) or is allowlisted in ``INTENTIONAL_SUBSET``.
FAMILY_FLAGS: dict[str, frozenset[str]] = {
    "two_sided_input": frozenset(
        {
            "--header",
            "--include",
            "--old-version",
            "--new-version",
        }
    ),
    "policy": frozenset({"--policy", "--policy-file", "--suppress"}),
    "severity": frozenset(
        {
            "--severity-preset",
            "--severity-abi-breaking",
            "--severity-potential-breaking",
            "--severity-quality-issues",
            "--severity-addition",
        }
    ),
    "scope": frozenset({"--scope-public-headers"}),
    "output": frozenset({"--format", "--output"}),
    # Two-sided evidence family (ADR-037 D3 ``@evidence_options``): registered
    # but *not* required — only commands that take source depth (``compare``)
    # compose it.
    "evidence": frozenset(
        {
            "--depth",
            "--max",
            "--sources",
            "--build-info",
        }
    ),
    # Local-ELF debug-resolution family: registered but *not* required either — it
    # resolves local ELF debug artifacts the package/snapshot-oriented commands
    # do not take.
    "debug_resolution": frozenset(
        {
            "--dwarf-only",
            "--debug-root",
            "--debuginfod",
            "--debuginfod-url",
            "--debug-format",
            "--btf",
            "--ctf",
            "--dwarf",
        }
    ),
}

#: Family name → the decorator callable that supplies it (used by the gate's
#: AST coverage check, which keys on the decorator applied to a command).
FAMILY_DECORATOR: dict[str, str] = {
    "two_sided_input": "two_sided_input_options",
    "policy": "policy_options",
    "severity": "severity_options",
    "scope": "scope_options",
    "output": "output_options",
    "evidence": "evidence_options",
}

#: Families every verdict-emitting command must compose (unless allowlisted).
#: ``debug_resolution`` is deliberately *not* required — it resolves local ELF
#: debug artifacts that the package/snapshot-oriented commands do not take.
#: ``evidence`` is likewise registered-but-not-required — only commands that take
#: source depth (``compare``) compose ``@evidence_options`` (ADR-037 D3).
REQUIRED_FAMILIES: frozenset[str] = frozenset(
    {
        "two_sided_input",
        "policy",
        "severity",
        "scope",
        "output",
    }
)

#: command name → module basename, for the gate to locate each command's source.
VERDICT_EMITTING_COMMANDS: dict[str, str] = {
    "compare": "cli.py",
    "appcompat": "cli_appcompat.py",
}

#: (command, family) → reason. A deliberate, reviewed omission of a shared
#: family from a verdict-emitting command (ADR-037 D3: opt out *explicitly*).
#: Empty today — every verdict-emitting command carries the full required set.
INTENTIONAL_SUBSET: dict[tuple[str, str], str] = {}

#: ADR-037 D10.5 — soft per-command flag-count budget for ``compare`` (a WARN
#: nudge, enforced by ``tests/test_config_rebalance.py::TestFlagBudget``).
#: Counts only the *visible* options: the families demoted to ``.abicheck.yml``
#: in Phase 5 (per-category severity, scope FP-tuning, suppression hygiene) are
#: hidden and config-bound (D4), so they don't count against the budget. The
#: ADR's end-state target is ~20; this interim ceiling keeps new visible flags
#: from creeping back in while the deprecation window runs.
#:
#: The budget is **derived** from the ledger below, not a hand-set number:
#: ``BASE`` is the visible count that settled after the ADR-037 D7
#: ``compare-release`` fold-in, and every visible flag added since must appear in
#: ``COMPARE_FLAG_BUDGET_RAISES`` with a one-line rationale (why it is a per-run
#: analysis input, not a project setting demotable to config). Because the budget
#: equals ``BASE + len(RAISES)`` and the test asserts ``visible <= budget``, a new
#: visible flag *cannot* be slipped in by silently consuming slack — the only way
#: to raise the ceiling is to add a documented ledger entry (a regression that
#: previously let ``--post-manifest`` land undocumented; see the ledger test).
#:
#: History that folded into ``BASE`` (no per-flag ledger — these predate the
#: ledger and moved the count in bulk): 60→66 when ``@compile_context_options``
#: (--gcc-*/--sysroot/--nostdinc, ADR-037 D3) unified onto ``compare`` for
#: dump/scan L2 parity; 66→76 visible when ``compare-release`` was removed and its
#: release-only knobs (package extraction, DSO selection, removed-library gate,
#: ADR-023 bundle/manifest) folded onto ``compare``'s directory/package path
#: (ADR-037 D7) — genuine release surface, inert on single files.
#: Lowered 76→70 by ADR-040 Lever 1 Phase B: the per-side ``--old/new-header``,
#: ``--old/new-include``, ``--old/new-sources`` and ``--old/new-build-info``
#: triples collapsed into the four side-aware flags ``--header`` / ``--include``
#: / ``--sources`` / ``--build-info`` (``old=``/``new=`` value prefix), a net −6.
#: Lowered 70→65 by ADR-040 Lever 1 Phase C (slice 1): ``--pdb-path`` and
#: ``--debug-root`` collapsed their per-side triples (−2 each) and
#: ``--probe-matrix-old/new`` folded into one side-aware ``--probe-matrix`` (−1).
COMPARE_FLAG_BUDGET_BASE = 65

#: Per-flag ledger of every visible ``compare`` flag added since the D7 fold-in.
#: flag spelling → rationale (why it is a per-run analysis input, not a stable
#: project setting demotable to ``.abicheck.yml``). Keep in sync with reality:
#: ``tests/test_config_rebalance.py`` asserts each key is a currently-visible
#: ``compare`` option, so demoting one to hidden/config means removing its entry
#: (and lowering ``BASE`` if it belonged to the base surface).
COMPARE_FLAG_BUDGET_RAISES: dict[str, str] = {
    "--post-manifest": (
        "G23 / #492: scopes the comparison to a POST Python export manifest's "
        "committed ABI surface. A per-run scoping input (which manifest to hold "
        "the release to), not a stable project setting — like --manifest."
    ),
    "--reconcile-build-context": (
        "ADR-039: clears context-free header-parse false positives using the "
        "build's active preprocessor defines. An invocation-time analysis toggle "
        "like --pattern-verdicts, not a project setting demotable to .abicheck.yml."
    ),
    "--env-matrix": (
        "ADR-020b runtime_floors: declared deployment constraints that turn "
        "version-requirement RISK findings into decidable COMPATIBLE/BREAKING "
        "verdicts. The matrix varies per deployment target checked, so it is a "
        "per-run input, not a stable project setting."
    ),
    "--profile": (
        "ADR-040 Lever 3: a single per-run bundle of workflow defaults "
        "(ci-gate/release/quick) that explicit flags always override. One visible "
        "flag replaces the habit of typing 4-6; the reductions in ADR-040 Levers "
        "1-2 lower BASE to bring the net well below today."
    ),
}

#: Derived ceiling — never hand-edit; add a ``COMPARE_FLAG_BUDGET_RAISES`` entry.
COMPARE_FLAG_BUDGET = COMPARE_FLAG_BUDGET_BASE + len(COMPARE_FLAG_BUDGET_RAISES)


def count_visible_options(cmd: object) -> int:
    """Count a Click command's user-visible (non-hidden) options (ADR-037 D10.5)."""
    n = 0
    for p in getattr(cmd, "params", []):
        if getattr(p, "param_type_name", None) == "option" and not getattr(
            p, "hidden", False
        ):
            n += 1
    return n


#: ADR-040 Lever 3 — named run profiles for ``compare``. Each maps a profile
#: name to a bundle of ``{option-dest: value}`` defaults for the documented
#: workflows. A profile is a *default layer*: an explicitly-passed flag always
#: wins (see :func:`apply_compare_profile`), mirroring the config < CLI rule and
#: the way ``--severity-preset`` collapses four severity flags into one token.
#: Values are in each option's *resolved* form (``depth`` uses the canonical
#: ``USER_DEPTHS`` rungs, ``fmt``/``exit_code_scheme`` the ``Choice`` strings,
#: booleans as ``bool``) so they can be injected without re-running conversion.
COMPARE_PROFILES: dict[str, dict[str, object]] = {
    # CI gate: fast header-depth check, compact review digest, severity-aware
    # exit codes — the "block the PR" workflow. (Public-surface scoping is the
    # default, so the profile does not restate it — a project's .abicheck.yml
    # scope choice stays authoritative.)
    "ci-gate": {
        "depth": "headers",
        "fmt": "review",
        "exit_code_scheme": "severity",
    },
    # Release cut: deepest evidence, full Markdown report with a semver/SONAME
    # recommendation appended — the "should I bump?" flow.
    "release": {
        "depth": "full",
        "fmt": "markdown",
        "recommend": True,
    },
    # Quick look: symbols-only, one-line summary — the "just tell me" flow.
    "quick": {
        "depth": "binary",
        "stat": True,
    },
}

def _profile_targets_set_input(kwargs: dict[str, object]) -> bool:
    """True when the ``compare`` operands are a directory/package (set) input.

    Mirrors the ADR-037 D7 dispatch (:func:`cli_resolve.classify_compare_operand`)
    so profile handling matches how ``run_compare`` will actually route the
    comparison, without duplicating the classification rules.
    """
    from pathlib import Path

    from .cli_resolve import classify_compare_operand

    kinds: set[str] = set()
    for key in ("old_input", "new_input"):
        operand = kwargs.get(key)
        if operand is None:
            continue
        try:
            kinds.add(classify_compare_operand(Path(str(operand))))
        except Exception:  # noqa: BLE001 - classification is best-effort here
            continue
    return bool(kinds & {"directory", "package"})


def profile_option(func: F) -> F:
    """The ``--profile`` option (ADR-040 Lever 3): one token for a workflow.

    Kept here so ``cli.py`` stays under its size cap and any future front-end
    shares one spelling/help. The value is validated against
    :data:`COMPARE_PROFILES`; application (default-layering under explicit flags)
    happens in :func:`apply_compare_profile`.
    """
    func = click.option(
        "--profile",
        "profile",
        type=click.Choice(list(COMPARE_PROFILES), case_sensitive=True),
        default=None,
        help="Run-profile preset bundling workflow defaults (ADR-040): "
        "'ci-gate' (headers depth, review digest, severity exit codes), "
        "'release' (full depth, recommendation, Markdown), 'quick' "
        "(symbols-only, one-line summary). Explicit flags override the profile; "
        "single-pair compares only (configure release defaults in .abicheck.yml).",
    )(func)
    return func


def apply_compare_profile(ctx: object, kwargs: dict[str, object]) -> None:
    """Fold the selected ``--profile`` defaults into *kwargs*, in place.

    Pops ``profile`` from *kwargs* (it is a CLI-layer concept the downstream
    ``run_compare`` signature does not take) and fills each setting the profile
    declares **only** when the user left that option at its default.

    **Profiles are single-pair-only.** A profile bundles single-pair-only knobs
    (``--depth``, ``--exit-code-scheme``) and single-pair report formats
    (``review``) that the directory/package *release fan-out* deliberately does
    not accept — the fan-out sources those from ``.abicheck.yml`` instead. Rather
    than silently drop half a profile (the codebase rejects such flags loudly on
    set inputs, e.g. :func:`cli_resolve._reject_evidence_flags_for_set_inputs`),
    a ``--profile`` on directory/package operands is rejected with a message that
    points at the config home for release defaults. This keeps the feature
    consistent with the existing set-input contract and free of the per-key /
    per-value special cases the fan-out would otherwise force.

    **Precedence (single-pair): explicit flag > profile > project config >
    default.** A ``--profile`` is a per-run choice the user typed on the command
    line, so — like any typed flag — it overrides project ``.abicheck.yml``
    defaults, while a genuinely typed flag still overrides the profile. Injection
    is value-only and gated on ``ctx.get_parameter_source`` so an explicit flag
    is never clobbered; the profile is **not** stamped as a command-line source
    (nothing downstream needs the source, and not stamping keeps the mechanism
    simple).
    """
    name = kwargs.pop("profile", None)
    if not name:
        return
    import click
    from click.core import ParameterSource

    if _profile_targets_set_input(kwargs):
        raise click.UsageError(
            f"--profile {name} is not supported for directory/package (release) "
            "comparisons: profiles bundle single-pair-only knobs (--depth, "
            "--exit-code-scheme, the 'review' format). Configure release defaults "
            "in .abicheck.yml (the fan-out reads format/severity/scheme from it), "
            "or compare the libraries individually to use a profile."
        )

    profile = COMPARE_PROFILES[str(name)]
    get_source = getattr(ctx, "get_parameter_source", None)
    explicit = {
        ParameterSource.COMMANDLINE,
        ParameterSource.ENVIRONMENT,
    }
    for dest, value in profile.items():
        # ``--depth`` and ``--max`` are the same dial on two dests: an explicit
        # ``--max`` (dest ``max_depth``) is the user's depth choice, so the
        # profile's ``depth`` must yield to it — otherwise resolve_dump_depth
        # sees "--max plus a different --depth" and exits 64 (Codex review).
        if (
            dest == "depth"
            and get_source is not None
            and get_source("max_depth") in explicit
        ):
            continue
        src = get_source(dest) if get_source is not None else None
        # Only fill a value the user did not set explicitly (DEFAULT / DEFAULT_MAP
        # / unknown). An explicit --flag or a mapped env var stays untouched.
        if src not in explicit:
            kwargs[dest] = value


#: ADR-037 D10.3 — the single MCP-param ⇄ CLI-flag name map. The ``abi_compare``
#: MCP tool and the native ``compare`` command answer the same question through
#: the same Tier-2 chokepoint, but their surface vocabularies differ (JSON snake
#: keys vs ``--kebab`` flags, e.g. ``output_format`` vs ``--format``). This table
#: is the *source of truth* that reconciles them; the ``cli-contract`` gate
#: (D10.3) fails if an ``abi_compare`` parameter or a mapped ``compare`` flag is
#: absent, so the two front-ends cannot silently drift. Value ``None`` marks an
#: MCP parameter with **no** ``compare`` flag equivalent (a deliberate, reviewed
#: omission — e.g. report-shaping knobs the MCP tool exposes that the CLI spells
#: differently), keeping the omission explicit rather than an accident.
MCP_CLI_NAME_MAP: dict[str, str | None] = {
    # input operands
    "old_input": "--old (positional OLD)",
    "new_input": "--new (positional NEW)",
    # ADR-040 L1: the per-side header params now map to the single side-aware
    # ``--header`` flag (scoped via an ``old=``/``new=`` value prefix).
    "old_headers": "--header",
    "new_headers": "--header",
    "headers": "--header",
    "include_dirs": "--include",
    "language": "--lang",
    # policy / suppression
    "policy": "--policy",
    "policy_file": "--policy-file",
    "suppression_file": "--suppress",
    # output / report shaping
    "output_format": "--format",
    "show_only": "--show-only",
    "report_mode": "--report-mode",
    "show_impact": "--show-impact",
    "stat": "--stat",
}
