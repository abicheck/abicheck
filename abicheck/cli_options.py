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

import logging
import os
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar, overload

import click

from .cli_params import (
    DEPTH_PARAM,
    POLICY_FILE_PARAM,
    SIDED_BUILD_INFO_PARAM,
    SIDED_DUMP_MANIFEST_PARAM,
    SIDED_EXISTING_PATH_PARAM,
    SIDED_INCLUDE_PATH_PARAM,
    SIDED_PATH_PARAM,
    SIDED_SOURCES_PARAM,
    SIDED_STR_PARAM,
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


def split_sided_include_paths(
    triples: Sequence[tuple[str, Path, str | None]],
) -> tuple[tuple[Path, ...], tuple[Path, ...], tuple[Path, ...], dict[Path, str]]:
    """Like :func:`split_sided_paths`, but for ``--include``'s own
    :class:`~abicheck.cli_params.SidedIncludePathParam` triples (ADR-050 D1):
    also collects each entry's optional label into one ``path -> label`` map
    spanning all three buckets.

    A single combined map (not per-side) is correct because a label is a
    *logical identity* the caller pairs across sides deliberately — the same
    ``support`` label on both ``--include old:support=old/src --include
    new:support=new/src`` — while the dict *keys* (each side's own resolved
    ``Path``) stay naturally distinct per side. Downstream, the same map is
    consulted when building either side's ``IncludeDir`` list.
    """
    both: list[Path] = []
    old_only: list[Path] = []
    new_only: list[Path] = []
    labels: dict[Path, str] = {}
    for side, path, label in triples:
        {"both": both, "old": old_only, "new": new_only}[side].append(path)
        if label is not None:
            labels[path] = label
    return tuple(both), tuple(old_only), tuple(new_only), labels


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


def _split_sided_version(
    pairs: Sequence[tuple[str, str]],
) -> tuple[str, str]:
    """Resolve ``(side, label)`` pairs to ``(old_version, new_version)`` labels.

    Same fan-out as :func:`_split_sided_single` (a bare/``both=`` value applies
    to both sides, ``old=``/``new=`` override that side, last wins) but with the
    historical per-side *defaults* ``"old"`` / ``"new"`` when a side is unset —
    version labels are always populated, unlike the optional path families.
    """
    old = "old"
    new = "new"
    for side, label in pairs:
        if side in ("both", "old"):
            old = label
        if side in ("both", "new"):
            new = label
    return old, new


def normalize_sided_options(kwargs: dict[str, object]) -> None:
    """Translate the sided ``header``/``include``/``sources``/``build_info``/
    ``debug_root``/``pdb``/``probe_matrix``/``version`` dests into the per-side
    kwargs the command bodies consume, in place (ADR-040 L1).

    Absent keys are left untouched, so this is safe to call on any command that
    composes only a subset of the sided families.
    """
    if "header" in kwargs:
        both, old, new = split_sided_paths(kwargs.pop("header"))  # type: ignore[arg-type]
        kwargs["headers"] = both
        kwargs["old_headers_only"] = old
        kwargs["new_headers_only"] = new
    if "dump_manifest" in kwargs:
        old_dm, new_dm = _split_sided_single(kwargs.pop("dump_manifest"))  # type: ignore[arg-type]
        kwargs["old_dump_manifest"] = old_dm
        kwargs["new_dump_manifest"] = new_dm
    if "include" in kwargs:
        both, old, new, labels = split_sided_include_paths(
            kwargs.pop("include")  # type: ignore[arg-type]
        )
        kwargs["includes"] = both
        kwargs["old_includes_only"] = old
        kwargs["new_includes_only"] = new
        kwargs["include_labels"] = labels
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
    if "debug_info" in kwargs:
        old_di, new_di = _split_sided_single(kwargs.pop("debug_info"))  # type: ignore[arg-type]
        kwargs["debug_info1"] = old_di
        kwargs["debug_info2"] = new_di
    if "devel_pkg" in kwargs:
        old_dp, new_dp = _split_sided_single(kwargs.pop("devel_pkg"))  # type: ignore[arg-type]
        kwargs["devel_pkg1"] = old_dp
        kwargs["devel_pkg2"] = new_dp
    if "pdb" in kwargs:
        base_p, old_pp, new_pp = _split_sided_base(kwargs.pop("pdb"))  # type: ignore[arg-type]
        kwargs["pdb_path"] = base_p
        kwargs["old_pdb_path"] = old_pp
        kwargs["new_pdb_path"] = new_pp
    if "version" in kwargs:
        old_v, new_v = _split_sided_version(kwargs.pop("version"))  # type: ignore[arg-type]
        kwargs["old_version"] = old_v
        kwargs["new_version"] = new_v


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
        "--dump-manifest",
        "dump_manifest",
        multiple=True,
        type=SIDED_DUMP_MANIFEST_PARAM,
        help="A strict YAML document describing multiple translation units to "
        "compile and merge into one side's snapshot, instead of a single "
        "-H/--header list (ADR-050 D3). Side-scoped: repeat the flag with an "
        "'old='/'new=' prefix per side (e.g. --dump-manifest old=v1/abi.yml "
        "--dump-manifest new=v2/abi.yml); a bare value applies to both. "
        "Mutually exclusive with -H/--header for that side (declare the "
        "public surface in the manifest's own base profile instead). ELF "
        "only so far.",
    )(func)
    func = click.option(
        "--version",
        "version",
        multiple=True,
        type=SIDED_STR_PARAM,
        help="Version label used when an input is a bare .so file. Scope to one "
        "side with an 'old='/'new=' prefix, repeating the flag per side (e.g. "
        "--version old=1.0 --version new=2.0); a bare value applies to both. "
        "Defaults: old side 'old', new side 'new' (ADR-040).",
    )(func)
    func = click.option(
        "-I",
        "--include",
        "include",
        multiple=True,
        type=SIDED_INCLUDE_PATH_PARAM,
        help="Extra include directory for castxml. Applies to both sides; scope "
        "to one side with an 'old='/'new=' prefix, repeating the flag per side "
        "(e.g. --include old=inc1 --include new=inc2). Repeatable (ADR-040). "
        "A labeled 'old:LABEL=PATH'/'new:LABEL=PATH' form (e.g. --include "
        "old:support=old/src --include new:support=new/src) names a "
        "side-specific support root under one shared logical identity, so a "
        "genuine two-checkout compare doesn't spuriously PROFILE_MISMATCH on "
        "it (ADR-050 D1).",
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
        "Recommended for full ABI analysis; without headers, abicheck uses whatever "
        "artifact evidence is available instead (ELF may add DWARF, PE may add PDB, "
        "Mach-O stays limited to binary metadata: exports plus load-command facts "
        "like install name, dependencies, and rpaths) but has no header AST or "
        "public-surface scoping. "
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
        help="Include dir for new side only.",
    )(func)
    func = click.option(
        "--old-include",
        "old_includes_only",
        multiple=True,
        type=click.Path(path_type=Path),
        help="Include dir for old side only.",
    )(func)
    func = click.option(
        "--new-header",
        "new_headers_only",
        multiple=True,
        type=click.Path(path_type=Path),
        help="Public header for new side only.",
    )(func)
    func = click.option(
        "--old-header",
        "old_headers_only",
        multiple=True,
        type=click.Path(path_type=Path),
        help="Public header for old side only.",
    )(func)
    func = click.option(
        "-I",
        "--include",
        "includes",
        multiple=True,
        type=click.Path(path_type=Path),
        help="Extra include directory (both sides).",
    )(func)
    func = click.option(
        "-H",
        "--header",
        "headers",
        multiple=True,
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


def snapshot_compression_option(func: F) -> F:
    """``--compression``, currently used by ``dump`` (ADR-059).

    ``auto`` (default) infers the storage envelope from ``-o/--output``'s
    canonical suffix (``.json.gz``/``.json.zst`` -> gzip/zstd, anything
    else -> plain); an explicit value is honored, and hard-errors if it
    contradicts a canonical output suffix rather than silently renaming or
    silently overriding. See ``abicheck/snapshot_io.py``."""
    func = click.option(
        "--compression",
        "snapshot_compression",
        type=click.Choice(["auto", "none", "gzip", "zstd"], case_sensitive=False),
        default="auto",
        show_default=True,
        help="Snapshot storage envelope: 'auto' infers gzip/zstd/plain from "
        "-o/--output's suffix (.json.gz/.json.zst/plain .json); an "
        "explicit value is used as-is and errors if it contradicts the "
        "output suffix. Compression is a storage detail only -- it never "
        "changes the decoded snapshot content.",
    )(func)
    return func


def include_dependencies_option(func: F) -> F:
    """``--include-dependencies``, shared by ``dump`` and ``compare``
    (dumper_scoping.py): by default, toolchain/system-header declarations
    (std::/SYCL/etc. pulled in transitively by #include) are excluded from
    a header-AST dump -- a header-origin filter, not a public-API-surface
    one (the library's own private/internal declarations are always kept,
    exactly like its public ones). Pass this flag to get the old, unfiltered
    full dump instead. A no-op when there are no header-derived declarations
    at all (a binary-only/DWARF-only dump). Filters the flat
    function/variable/type/enum lists (typedefs are always kept) and the
    DWARF/DWARF-advanced collections keyed off them; an embedded header-only
    semantic graph (always attached by default) is not filtered. A filtered
    and an unfiltered snapshot are not comparable -- mixing them raises
    ScopeMismatchError."""
    func = click.option(
        "--include-dependencies",
        "include_dependencies",
        is_flag=True,
        default=False,
        help="Include toolchain/system-header declarations (std::/SYCL/etc. "
        "pulled in transitively by #include). By default these are "
        "excluded -- pass this flag to get the old, unfiltered full "
        "surface instead. A no-op on a binary-only/DWARF-only dump. "
        "Mixing a filtered and an unfiltered snapshot across a "
        "comparison raises ScopeMismatchError (dumper_scoping.py).",
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


def _scoped_env_flag_callback(
    env_var: str,
) -> Callable[[click.Context, click.Parameter, bool], None]:
    """Build a Click callback that sets *env_var* to ``"1"`` for one command
    invocation, restoring (or unsetting) it via ``ctx.call_on_close``.

    Shared by ``--allow-ast-frontend-fallback``/``ABICHECK_ALLOW_AST_FALLBACK``
    and ``--allow-unsupported-castxml``/``ABICHECK_ALLOW_UNSUPPORTED_CASTXML``
    — both are "explicit, invocation-scoped opt-in past a hard-fail gate" env
    vars with an identical set/restore shape, so a real CLI flag for either
    reduces to just naming the env var here.
    """

    def _callback(ctx: click.Context, _param: click.Parameter, value: bool) -> None:
        if not value:
            return None
        previous = os.environ.get(env_var)
        os.environ[env_var] = "1"

        def _restore() -> None:
            if previous is None:
                os.environ.pop(env_var, None)
            else:
                os.environ[env_var] = previous

        ctx.call_on_close(_restore)
        return None

    return _callback


_enable_ast_fallback_for_command = _scoped_env_flag_callback(
    "ABICHECK_ALLOW_AST_FALLBACK"
)
_enable_unsupported_castxml_for_command = _scoped_env_flag_callback(
    "ABICHECK_ALLOW_UNSUPPORTED_CASTXML"
)


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
        "--frontend-context",
        "frontend_context",
        default="host",
        show_default=True,
        type=click.Choice(["host", "device"], case_sensitive=False),
        help="Which AST context the L2 header frontend should target (ADR-050 "
        "D3/D5). 'device' selects the SYCL/DPC++ offload-device AST from a "
        "DPC++-capable compiler (icx/icpx/dpcpp) invoked with -fsycl; it fails "
        "loudly if the configured frontend cannot produce a device context. "
        "Matches a manifest's own frontend_context field for the legacy, "
        "non-manifest path.",
    )(func)
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
        "--allow-unsupported-castxml",
        is_flag=True,
        expose_value=False,
        envvar="ABICHECK_ALLOW_UNSUPPORTED_CASTXML",
        callback=_enable_unsupported_castxml_for_command,
        help="Proceed with a CastXML build outside the supported version range "
        "(castxml_policy.MIN_CASTXML/MAX_CASTXML/MIN_CASTXML_CLANG_MAJOR) instead "
        "of aborting the scan before headers are parsed. Exploratory-mode-only: "
        "the resulting snapshot's ast_toolchain_supported is recorded as false "
        "with ast_toolchain_unsupported_reasons, so it is never mistaken for a "
        "normal supported scan and cannot become a new strict baseline without a "
        "further explicit acknowledgment.",
    )(func)
    func = click.option(
        "--allow-ast-frontend-fallback",
        is_flag=True,
        expose_value=False,
        envvar="ABICHECK_ALLOW_AST_FALLBACK",
        callback=_enable_ast_fallback_for_command,
        help="Allow auto-selected CastXML to fall back to Clang for a recognized "
        "toolchain mismatch, an unsupported CastXML release, or a direct-include "
        "guard. Disabled by default because the frontends can produce materially "
        "different findings. A non-host --frontend-context (SYCL/DPC++) under an "
        "unpinned auto (not pinned to castxml via ABICHECK_AST_FRONTEND=castxml) "
        "routes to Clang without this flag, since CastXML has no host/device "
        "concept to fall back from; a castxml-pinned auto still rejects it.",
    )(func)
    func = click.option(
        "--ast-frontend",
        "header_backend",
        default="auto",
        show_default=True,
        type=click.Choice(["auto", "castxml", "clang", "hybrid"], case_sensitive=False),
        help="C/C++ AST frontend (ADR-037 D8): castxml (default schema reference) "
        "or clang (-ast-dump=json; for hosts where castxml is absent or its "
        "bundled frontend chokes). hybrid (G28 Phase 3) runs BOTH and merges "
        "them (dumper_hybrid.merge_snapshots) — needs both tools installed and "
        "costs roughly 2x a single-backend dump; never selected by auto. auto "
        "resolves to castxml (or the ABICHECK_AST_FRONTEND pin) and never "
        "changes producer unless --allow-ast-frontend-fallback (or "
        "ABICHECK_ALLOW_AST_FALLBACK=1) is explicitly set — except a non-host "
        "--frontend-context (SYCL/DPC++), which an unpinned auto routes to "
        "clang since castxml can't satisfy it at all (a castxml-pinned auto, "
        "via ABICHECK_AST_FRONTEND=castxml, still rejects it). "
        "Env: ABICHECK_AST_FRONTEND.",
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
            # UsageError (not plain ClickException) so it exits 64, not 1 — a bad
            # .abicheck.yml is a usage error (ADR-043 CLI reset).
            raise click.UsageError(f"cannot parse build config {cfg}: {exc}") from exc
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
        # No config-file equivalent of --frontend-context exists (ADR-050
        # D5) -- config merging only ever narrows CLI-unset fields, so the
        # CLI-resolved value must simply survive the merge instead of
        # silently reverting to CompileContext's "host" default.
        frontend_context=cli_ctx.frontend_context,
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
    frontend_context: str = "host",
) -> tuple[CompileContext, tuple[Path, ...]]:
    """Build the CLI :class:`CompileContext` and fold the config ``compile:`` block in.

    The single entry point the ``@compile_context_options`` family resolves to
    (ADR-037 D3): construct a :class:`~abicheck.service_scan.CompileContext` from
    the decorator's flags, then delegate to :func:`merge_compile_config` with the
    ``--ast-frontend`` / ``--nostdinc`` explicitness read from the Click parameter
    source (so an explicitly-typed value — even a default-looking ``auto`` — beats
    a pinned config one). ``compare`` / ``dump`` / ``scan`` all call this so their
    L2 compile context cannot drift.

    ``frontend_context`` (ADR-050 D3/D5) passes through unchanged here — Click's
    own ``type=click.Choice(["host", "device"])`` on ``--frontend-context``
    already rejects anything else at parse time. A ``"device"`` request that
    the underlying compiler/invocation can't actually satisfy fails loudly
    from the real extraction pipeline (``AstContextMissingError``/
    ``AstContextAmbiguousError`` in ``sycl_context``), not from a blanket
    reject here.
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
        frontend_context=frontend_context,
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


#: ADR-049 D8 pack selection, shared by `compare` and `scan --against`.
#: One decorator rather than a copy per command: `tests/test_cli_contract.py`
#: pins that a shared concept uses one canonical spelling, and the resolver
#: already records `--pack` as the selecting option
#: (`resolve_selected_packs`'s own default), so a second spelling would make
#: the receipt name an option that does not exist.
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


def artifact_set_options(func: F) -> F:
    """``scan --artifact-set`` knobs (ADR-056).

    A small, dedicated pair rather than reuse of `release_options` wholesale
    — `scan` doesn't need `--no-bundle-analysis`/`--bundle-cohort`/
    `--manifest`, only the set operand and the system-provider allow-list.
    `--bundle-system-providers`' option text matches `release_options`'
    below verbatim (same flag, same meaning, just declared for a different
    command) rather than being redefined with different wording.
    """
    func = click.option(
        "--artifact-set",
        "artifact_set",
        default=None,
        metavar="DIR|PATH,PATH,...",
        help="Audit a *set* of libraries with no old side, as one artifact "
        "(ADR-056): a directory (every discoverable shared library in it) "
        "or an explicit comma-separated path list. Mutually exclusive with "
        "the positional ARTIFACT and with --against (audit-only — no "
        "old-side comparison for a set).",
    )(func)
    func = click.option(
        "--bundle-system-providers",
        "bundle_system_providers",
        default="",
        help="Comma-separated extra sonames to treat as system-provided "
        "(extends the built-in libc/libstdc++/libgcc/libtbb allow-list). "
        "Only meaningful with --artifact-set.",
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
        "--devel-pkg",
        "devel_pkg",
        multiple=True,
        type=SIDED_EXISTING_PATH_PARAM,
        help="Development package with headers, scoped per side with an "
        "'old='/'new=' prefix (e.g. --devel-pkg old=a-dev.rpm --devel-pkg "
        "new=b-dev.rpm). Directory/package inputs only (ADR-040).",
    )(func)
    func = click.option(
        "--debug-info",
        "debug_info",
        multiple=True,
        type=SIDED_EXISTING_PATH_PARAM,
        help="Debug info package (RPM/Deb/tar), scoped per side with an "
        "'old='/'new=' prefix (e.g. --debug-info old=a-dbg.rpm --debug-info "
        "new=b-dbg.rpm). Directory/package inputs only (ADR-040).",
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
        hidden=True,
        help="Force the ELF debug format for both sides (auto=pick best available). "
        "Supersedes the individual --btf/--ctf/--dwarf flags. Demoted to the "
        "debug.format config key (ADR-040 L2); this flag still overrides it.",
    )(func)
    func = click.option(
        "--debuginfod-url",
        "debuginfod_url",
        default=None,
        hidden=True,
        help="debuginfod server URL (overrides DEBUGINFOD_URLS env var). Demoted to "
        "the debug.debuginfod_url config key (ADR-040 L2); this flag still overrides it.",
    )(func)
    func = click.option(
        "--debuginfod/--no-debuginfod",
        "debuginfod",
        default=False,
        hidden=True,
        help="Enable debuginfod network resolution for debug info (opt-in). Demoted "
        "to the debug.debuginfod config key (ADR-040 L2); --debuginfod/--no-debuginfod "
        "still overrides it either way.",
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
        "--dwarf-only/--no-dwarf-only",
        "dwarf_only",
        default=False,
        hidden=True,
        help="Force DWARF-only mode for both sides: use DWARF debug info "
        "as primary data source even when headers are available. Demoted to the "
        "debug.dwarf_only config key (ADR-040 L2); --dwarf-only/--no-dwarf-only "
        "still overrides it either way (e.g. --no-dwarf-only restores header parsing "
        "for a one-off run).",
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


def app_usage_scope_options(func: F) -> F:
    """Add the ADR-043 app-usage/required-symbol scoping options to ``compare``.

    ``--used-by``/``--verify-runtime`` and ``--required-symbol``/
    ``--required-symbols`` are mutually exclusive scoping mechanisms folding
    the former standalone ``appcompat``/``plugin-check`` commands into
    ``compare``. Decorators apply bottom-up, so they are listed here in
    reverse of their displayed order.
    """
    func = click.option(
        "--required-symbols",
        "required_symbols_file",
        type=click.Path(exists=True, dir_okay=False, path_type=Path),
        default=None,
        help="File of required symbols, one per line (blank lines and '#' "
        "comments ignored). Combined with any --required-symbol values.",
    )(func)
    func = click.option(
        "--required-symbol",
        "required_symbols_opt",
        multiple=True,
        help="An exported linker symbol a plugin host resolves via dlopen/dlsym "
        "and requires (repeatable; folds `plugin-check`). Scopes the "
        "comparison to this explicit entrypoint contract instead of the "
        "full diff. Mutually exclusive with --used-by.",
    )(func)
    func = click.option(
        "--verify-runtime",
        "verify_runtime",
        is_flag=True,
        default=False,
        help="With --used-by: actually run each consumer binary once against "
        "the OLD library and once against the NEW one (LD_BIND_NOW=1), "
        "recording a consumer_runtime_load_failed RISK finding when the "
        "dynamic linker itself reports an undefined symbol against the "
        "new library after loading cleanly against the old one (ADR-044 "
        "P2 item 2). A dynamic corroborating signal alongside the static "
        "scanner, never a replacement for it. Requires OLD/NEW to be real "
        "library binaries (not JSON snapshots) and is Linux-only; a "
        "no-op elsewhere. Ignored without --used-by.",
    )(func)
    func = click.option(
        "--used-by",
        "used_by_apps",
        multiple=True,
        type=click.Path(exists=True, dir_okay=False, path_type=Path),
        help="Application binary whose actual imports/required symbol versions "
        "scope the comparison (repeatable; folds `appcompat`). The full "
        "library comparison still runs once; the worst app-scoped result "
        "becomes the primary verdict/exit code, with the full verdict and "
        "unrelated changes kept as informational context. OLD/NEW may be "
        "real library binaries or JSON snapshots carrying binary evidence "
        "(a `dump` of a real library, not headers-only). Mutually "
        "exclusive with --required-symbol/--required-symbols.",
    )(func)
    return func


def build_source_dump_options(func: F) -> F:
    """Add the ``--build-info`` / ``--sources`` embed options to ``dump``.

    Source-tree-centric inputs (ADR-028..033 amendment): ``--sources`` is a
    source checkout — L4 source ABI replay and the L5 graph are run inline and
    embedded; ``--build-info`` is an optional build dir / ``compile_commands.json``
    / pre-built pack supplying L3 (auto-discovered inside the source tree when
    omitted). Either flag also accepts, and auto-detects, a build-emitted
    ``abicheck_inputs/`` Flow-2 pack directory or a pre-built ``BuildSourcePack``
    directory (from an internal/producer-side collection step) — both are
    ingested and validated automatically, no separate ``inputs validate``/
    ``merge`` step needed (ADR-043 D1). Embedding makes the ``.abi.json``
    self-contained, so a later ``compare old.json new.json`` carries the facts
    with no out-of-band directories. Applied bottom-up, so listed in reverse of
    display.
    """
    func = click.option(
        "--depth",
        "depth",
        type=DEPTH_PARAM,
        default=None,
        help="Evidence-depth dial (same vocabulary as `compare`/`scan --depth`): "
        "binary=symbols only, headers=+header AST (default), build=+build "
        "context, source=+source replay & call graph.",
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
        help="Source checkout to run source-ABI replay and build the call "
        "graph over, embedding both inline. (An existing pack directory — e.g. "
        "from the abicheck-cc wrapper or Clang plugin — is auto-detected by "
        "its manifest.json and loaded as that pack instead.)",
    )(func)
    func = click.option(
        "--build-info",
        "build_info",
        type=click.Path(exists=True, path_type=Path),
        default=None,
        help="Optional build context: a build dir, a compile_commands.json, "
        "or a pre-captured pack. Auto-discovered inside the --sources tree when "
        "omitted.",
    )(func)
    return func


def header_graph_options(func: F) -> F:
    """The shared, deprecated ``--header-graph``/``--header-graph-includes`` pair.

    G29 Phase A: the L2 header-only semantic graph
    (:func:`~abicheck.buildsource.header_graph.build_header_only_graph`) — and
    its include-file extension — is now always built whenever headers are
    available (``--depth headers`` or deeper), for both ``compare`` and
    ``dump``. These two flags are no longer opt-in toggles; they are kept as
    *hidden*, inert no-op shims (``hidden=True`` — absent from ``--help`` and
    from ``tests/test_cli_contract.py``'s ``_OPTION_SET_SNAPSHOT``) purely so
    an existing script/CI invocation that still passes ``--header-graph``
    doesn't hard-fail with "no such option". Passing either flag prints a
    one-line deprecation note to stderr and otherwise changes nothing — the
    graph is built identically whether or not the flag is given. Planned
    removal: two minor releases after this change ships (track in
    CHANGELOG.md). Shared by ``compare`` and ``dump`` so the two flags' spelling
    can never drift between them. Applied bottom-up, so listed in reverse of
    display.
    """
    func = click.option(
        "--header-graph-includes",
        "header_graph_includes_deprecated",
        is_flag=True,
        default=False,
        hidden=True,
        help="Deprecated, no-op: the include-file graph pass is now always run "
        "alongside --header-graph's replacement (always-on L2 header graph). "
        "Planned removal: two minor releases out.",
    )(func)
    func = click.option(
        "--header-graph",
        "header_graph_deprecated",
        is_flag=True,
        default=False,
        hidden=True,
        help="Deprecated, no-op: the L2 header-only semantic graph (ADR-041 "
        "addendum) is now always built for --depth headers and above. Planned "
        "removal: two minor releases out.",
    )(func)
    return func


def warn_deprecated_header_graph_flags(
    header_graph_deprecated: bool, header_graph_includes_deprecated: bool
) -> None:
    """Emit a deprecation note for the inert ``--header-graph``/``-includes`` shim.

    Called from ``compare``/``dump_cmd`` bodies (not the Click callback
    itself, so it runs after Click has finished parsing) whenever either
    flag was passed on the command line. Behavior is identical either way —
    this is purely a stderr note, per the "hidden shim must not control
    behavior" policy (AGENTS.md deprecation convention).
    """
    if header_graph_deprecated or header_graph_includes_deprecated:
        click.echo(
            "Note: --header-graph/--header-graph-includes are deprecated "
            "no-ops — the L2 header-only semantic graph is now always built "
            "for --depth headers and above. Planned removal: two minor "
            "releases out.",
            err=True,
        )


def evidence_options(func: F) -> F:
    """The shared two-sided evidence family (ADR-037 D3's ``@evidence_options``).

    The single source of truth for the depth/source/build-info surface a
    *two-sided* verdict command exposes: ``--depth`` plus the per-side
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
        "--depth",
        "depth",
        type=DEPTH_PARAM,
        default=None,
        help="Evidence-depth dial: binary=symbols only, headers=+header AST "
        "(default), build=+build context, source=+source replay & call graph. "
        "Deeper-than-headers needs --sources or --build-info.",
    )(func)
    func = click.option(
        "--sources",
        "sources",
        multiple=True,
        type=SIDED_SOURCES_PARAM,
        help="Source checkout for --depth build/source (collected inline, "
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
        help="Out-of-band build context: a build dir, a compile_commands.json, "
        "or a pack, overriding embedded. Applies to both sides; scope to one "
        "with an 'old='/'new=' prefix, repeating the flag per side "
        "(e.g. --build-info old=b1 --build-info new=b2) (ADR-040).",
    )(func)
    return func


#: Back-compat alias for the pre-ADR-037-D3 name. ``evidence_options`` is the
#: canonical spelling (the D3 table); this keeps existing imports working.
build_source_compare_options = evidence_options


#: ADR-037 D10 CLI-contract metadata (family/flag tables, the compare
#: flag-count budget ledger, and :func:`count_visible_options`) moved to
#: ``cli_options_contract.py`` when this module reached the 2000-line hard
#: cap. Re-exported here so every existing caller — the ``cli-contract``
#: gate's own tests and ``tests/test_config_rebalance.py`` — keeps its
#: import path, the same pattern used for ``cli_profiles.py`` below.
from .cli_options_contract import (  # noqa: E402
    COMPARE_FLAG_BUDGET as COMPARE_FLAG_BUDGET,
    COMPARE_FLAG_BUDGET_BASE as COMPARE_FLAG_BUDGET_BASE,
    COMPARE_FLAG_BUDGET_RAISES as COMPARE_FLAG_BUDGET_RAISES,
    FAMILY_DECORATOR as FAMILY_DECORATOR,
    FAMILY_FLAGS as FAMILY_FLAGS,
    INTENTIONAL_SUBSET as INTENTIONAL_SUBSET,
    REQUIRED_FAMILIES as REQUIRED_FAMILIES,
    VERDICT_EMITTING_COMMANDS as VERDICT_EMITTING_COMMANDS,
    count_visible_options as count_visible_options,
)

#: ADR-040 Lever 3's run-profile *data* (the profile table, its ``--profile``
#: option, and the receipt key) moved to ``cli_profiles.py`` when this module
#: reached the 2000-line hard cap. Re-exported here so every existing caller
#: — ``cli.py``'s ``compare`` wrapper and the profile tests — keeps its
#: import path, the same pattern ``cli_helpers_compare`` already uses for its
#: own moved helpers.
#:
#: The two *functions* below stayed: ``_profile_targets_set_input`` needs
#: ``cli_resolve.classify_compare_operand``, so moving them would have made
#: ``cli_profiles`` a new member of the CLI-registration import cycle rather
#: than the leaf it is — a fresh SCC member for a size-cap split is exactly
#: what the ``import-cycle-growth`` gate exists to catch, and the split works
#: without one.
#
# Spelled ``X as X`` (an explicit re-export) rather than declared in an
# ``__all__``: this module has never had one, and adding a three-name list
# would quietly narrow what ``import *`` gives every other consumer.
from .cli_profiles import (  # noqa: E402
    COMPARE_PROFILES as COMPARE_PROFILES,
    RUN_PROFILE_META_KEY as RUN_PROFILE_META_KEY,
    profile_option as profile_option,
)


def _profile_targets_set_input(kwargs: dict[str, object]) -> bool:
    """True when the ``compare`` operands are a directory/package (set) input.

    Mirrors the ADR-037 D7 dispatch (:func:`cli_resolve.classify_compare_operand`)
    so profile handling matches how ``run_compare`` will actually route the
    comparison, without duplicating the classification rules.
    """
    from .cli_resolve import classify_compare_operand

    kinds: set[str] = set()
    for key in ("old_input", "new_input"):
        operand = kwargs.get(key)
        if operand is None:
            continue
        try:
            kinds.add(classify_compare_operand(Path(str(operand))))
        except Exception:  # noqa: BLE001 - classification is best-effort here
            # Logged rather than swallowed silently (bandit B112): an operand
            # this classifier cannot read contributes no kind, and the real
            # dispatch in ``run_compare`` reports it properly.
            logging.getLogger(__name__).debug(
                "unclassifiable operand %r", operand, exc_info=True
            )
    return bool(kinds & {"directory", "package"})


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
    injected: dict[str, object] = {}
    for dest, value in profile.items():
        src = get_source(dest) if get_source is not None else None
        # Only fill a value the user did not set explicitly (DEFAULT / DEFAULT_MAP
        # / unknown). An explicit --flag or a mapped env var stays untouched.
        if src not in explicit:
            kwargs[dest] = value
            injected[dest] = value
    # ADR-049 D7 gives a run profile its own precedence layer, so "nothing
    # downstream needs the source" (above) stopped being true: an injected
    # value is indistinguishable from a built-in default once it is in
    # *kwargs*, and a receipt resolved without this recorded a profile's
    # choice as a default nobody made (`cli_compare_receipt`). Recorded on
    # the context rather than stamped as a command-line source, which would
    # make a profile outrank the explicit flags it is documented to yield to.
    meta = getattr(ctx, "meta", None)
    if meta is not None:
        meta[RUN_PROFILE_META_KEY] = {"name": str(name), "injected": injected}


#: ADR-049's contract-evaluation option decorator moved to
#: ``cli_contract_options.py`` when this module reached its own 2000-line
#: hard limit -- the same split, for the same reason, as ``cli_profiles.py``
#: before it. Re-exported here (``X as X``, so the re-export is explicit to
#: mypy) because it is a shared decorator callers already reach through this
#: module, and a leaf holding option definitions never imports back.
from .cli_contract_options import (  # noqa: E402
    contract_options as contract_options,
    pack_option as pack_option,
)
