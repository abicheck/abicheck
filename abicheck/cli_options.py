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

from .cli_params import DEPTH_PARAM, POLICY_FILE_PARAM

if TYPE_CHECKING:
    from .service_scan import CompileContext

F = TypeVar("F", bound=Callable[..., object])


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
        "--new-include",
        "new_includes_only",
        multiple=True,
        type=click.Path(path_type=Path),
        help="Include dir for new side only (overrides -I for new).",
    )(func)
    func = click.option(
        "--old-include",
        "old_includes_only",
        multiple=True,
        type=click.Path(path_type=Path),
        help="Include dir for old side only (overrides -I for old).",
    )(func)
    func = click.option(
        "--new-header",
        "new_headers_only",
        multiple=True,
        type=click.Path(path_type=Path),
        help="Public header for new side only (overrides -H for new). "
        "Validated for native binaries; ignored for snapshots.",
    )(func)
    func = click.option(
        "--old-header",
        "old_headers_only",
        multiple=True,
        type=click.Path(path_type=Path),
        help="Public header for old side only (overrides -H for old). "
        "Validated for native binaries; ignored for snapshots.",
    )(func)
    func = click.option(
        "-I",
        "--include",
        "includes",
        multiple=True,
        type=click.Path(path_type=Path),
        help="Extra include directory for castxml (applied to both sides).",
    )(func)
    func = click.option(
        "-H",
        "--header",
        "headers",
        multiple=True,
        type=click.Path(path_type=Path),
        help="Public header file or directory applied to both sides (repeat for multiple). "
        "Recommended for full ABI analysis; without headers, native binaries fall back to symbols-only mode. "
        "Scopes the ABI surface to declarations in these headers for ELF; on PE/Mach-O scoping is "
        "best-effort and falls back to the export table when castxml is unavailable or names don't match "
        "(e.g. MSVC C++ mangling). Validated for native binaries; ignored for snapshots.",
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
    ``defines`` synthesize ``-std=…``/``-D…`` flags only when the user did not pass
    ``--gcc-options``; ``include_dirs`` (resolved against the config's directory)
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
            raise click.ClickException(f"cannot parse build config {cfg}: {exc}") from exc
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
    if cli_ctx.gcc_options is not None:
        gcc_options = cli_ctx.gcc_options
    else:
        parts: list[str] = []
        if bc.compile_std:
            parts.append(f"-std={bc.compile_std}")
        parts += [f"-D{d}" for d in bc.compile_defines]
        gcc_options = " ".join(parts) or None
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
        gcc_option_tokens=cli_ctx.gcc_option_tokens,
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
    output_help: str | None = None,
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
        "--debug-root2",
        "debug_roots_new",
        multiple=True,
        type=click.Path(path_type=Path),
        help="Debug root for new side only (overrides --debug-root for new).",
    )(func)
    func = click.option(
        "--debug-root1",
        "debug_roots_old",
        multiple=True,
        type=click.Path(path_type=Path),
        help="Debug root for old side only (overrides --debug-root for old).",
    )(func)
    func = click.option(
        "--debug-root",
        "debug_roots",
        multiple=True,
        type=click.Path(path_type=Path),
        help="Directory containing separate debug files (build-id trees, "
        "path-mirror, dSYM bundles). Applied to both sides. Can be repeated.",
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
        help="Permit running `build.query` from an explicit trusted "
        "--config to emit a compile DB / exports (ADR-032 D5 "
        "query_build_system). Off by default, and ignored for auto-discovered "
        "source-tree configs: only existing build outputs are inspected — "
        "a full project build is never run.",
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
        help="Build-system query command that emits a compile DB without a full "
        "build (e.g. 'cmake -S . -B build -DCMAKE_EXPORT_COMPILE_COMMANDS=ON'). "
        "CLI equivalent of `.abicheck.yml` build.query — no config file needed. "
        "Only runs with --allow-build-query.",
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
    **embedded** in each snapshot (single-artifact UX). The optional
    ``--old-build-info`` / ``--new-build-info`` and ``--old-sources`` /
    ``--new-sources`` point at out-of-band pack directories to supply or
    override those facts per side; ``--depth`` selects how deep the inline
    collection runs (ADR-037 D5). All folded into the verdict as ordinary
    findings, never overriding artifact-backed ABI verdicts (ADR-028 D3).
    Applied bottom-up, so listed in reverse of displayed order.
    """
    from pathlib import Path

    pack_dir = click.Path(exists=True, file_okay=False, path_type=Path)
    # --build-info also accepts a file (a raw compile_commands.json), not just a
    # build dir / pack dir — the per-side replacement for the removed
    # deep-compare, whose build-info option took dirs *or* a compile DB file
    # (Codex review). The later pack/raw validation still distinguishes them.
    build_info_path = click.Path(exists=True, path_type=Path)
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
        "--old/new-sources or --old/new-build-info.",
    )(func)
    func = click.option(
        "--new-sources",
        "new_sources",
        type=pack_dir,
        default=None,
        help="New-side L4/L5 source: a raw source checkout (collected inline at "
        "--depth, embedding build/source/graph facts) or a pre-built `collect` "
        "pack. Overrides embedded.",
    )(func)
    func = click.option(
        "--old-sources",
        "old_sources",
        type=pack_dir,
        default=None,
        help="Old-side L4/L5 source: a raw source checkout (collected inline at "
        "--depth, embedding build/source/graph facts) or a pre-built `collect` "
        "pack. Overrides embedded.",
    )(func)
    func = click.option(
        "--new-build-info",
        "new_build_info",
        type=build_info_path,
        default=None,
        help="Out-of-band L3 build-info for the new side: a build dir, a "
        "compile_commands.json, or a pack (overrides embedded).",
    )(func)
    func = click.option(
        "--old-build-info",
        "old_build_info",
        type=build_info_path,
        default=None,
        help="Out-of-band L3 build-info for the old side: a build dir, a "
        "compile_commands.json, or a pack (overrides embedded).",
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
            "--old-header",
            "--new-header",
            "--old-include",
            "--new-include",
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
            "--old-sources",
            "--new-sources",
            "--old-build-info",
            "--new-build-info",
        }
    ),
    # Local-ELF debug-resolution family: registered but *not* required either — it
    # resolves local ELF debug artifacts the package/snapshot-oriented commands
    # do not take.
    "debug_resolution": frozenset(
        {
            "--dwarf-only",
            "--debug-root",
            "--debug-root1",
            "--debug-root2",
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
#: nudge, enforced by ``tests/test_config_rebalance.py::test_flag_budget``).
#: Counts only the *visible* options: the families demoted to ``.abicheck.yml``
#: in Phase 5 (per-category severity, scope FP-tuning, suppression hygiene) are
#: hidden and config-bound (D4), so they don't count against the budget. The
#: ADR's end-state target is ~20; this interim ceiling keeps new visible flags
#: from creeping back in while the deprecation window runs. Raised from 60→66 when
#: the shared ``@compile_context_options`` L2 family (--ast-frontend was already
#: visible; +6 for --gcc-path/--gcc-prefix/--gcc-options/--gcc-option/--sysroot/
#: --nostdinc) was unified onto ``compare`` for dump/scan parity (ADR-037 D3): the
#: family is genuine L2 surface ``compare`` previously lacked, not config-demotable.
#: Raised 66→77 when the standalone ``compare-release`` command was removed and its
#: 11 release-only knobs (``@release_options``: package extraction, DSO selection,
#: removed-library gate, ADR-023 bundle/manifest analysis) folded onto ``compare``'s
#: directory/package path (ADR-037 D7) — genuine release surface, inert on single
#: files, grouped in its own ``--help`` panel.
COMPARE_FLAG_BUDGET = 77


def count_visible_options(cmd: object) -> int:
    """Count a Click command's user-visible (non-hidden) options (ADR-037 D10.5)."""
    n = 0
    for p in getattr(cmd, "params", []):
        if getattr(p, "param_type_name", None) == "option" and not getattr(
            p, "hidden", False
        ):
            n += 1
    return n


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
    "old_headers": "--old-header",
    "new_headers": "--new-header",
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
