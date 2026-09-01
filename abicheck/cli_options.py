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
from typing import TYPE_CHECKING, Any, TypeVar, overload

import click

from .frontends.cli.options import secondary_output_options as _secondary_output_options
from .frontends.cli.options.params import (
    BUILTIN_POLICY_PROFILES,
    DEFAULT_POLICY_PROFILE,
    POLICY_FILE_PARAM,
    SIDED_DUMP_MANIFEST_PARAM,
    SIDED_INCLUDE_PATH_PARAM,
    SIDED_PATH_PARAM,
    SIDED_STR_PARAM,
    SidedChoiceParam,
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
    :class:`~abicheck.frontends.cli.options.params.SidedIncludePathParam` triples (ADR-050 D1):
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


def _split_sided_frontend(
    pairs: Sequence[tuple[str, str]],
) -> tuple[str, str | None, str | None]:
    """Resolve ``--ast-frontend``'s ``(side, frontend)`` pairs.

    The "base + per-side override" model :func:`_split_sided_base` implements
    for paths, for a string with a default: a bare/``both=`` value is the
    frontend both sides use, ``old=``/``new=`` override one side (``None`` =
    inherit the base), and an unset base is ``"auto"``. Last value wins per
    bucket.
    """
    base = "auto"
    old: str | None = None
    new: str | None = None
    for side, frontend in pairs:
        if side == "both":
            base = frontend
        elif side == "old":
            old = frontend
        else:
            new = frontend
    return base, old, new


def normalize_sided_options(kwargs: dict[str, object]) -> None:
    """Translate the sided ``header``/``include``/``sources``/``build_info``/
    ``debug_root``/``pdb``/``probe_matrix``/``version``/``ast-frontend`` dests
    into the per-side kwargs the command bodies consume, in place (ADR-040 L1).

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
    if isinstance(kwargs.get("header_backend"), tuple):
        base_f, old_f, new_f = _split_sided_frontend(
            kwargs["header_backend"]  # type: ignore[arg-type]
        )
        kwargs["header_backend"] = base_f
        kwargs["old_header_backend"] = old_f
        kwargs["new_header_backend"] = new_f


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
        "artifact evidence is available instead (ELF may add DWARF/BTF/CTF, PE may add PDB, "
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


def _resolve_policy_operand(
    ctx: click.Context, param: click.Parameter, value: str | None
) -> str:
    """Split ``--policy NAME|PATH`` into a profile and a policy document.

    ``--policy`` used to name only a built-in profile, with a separate
    ``--policy-file`` naming a document -- two flags for the one question
    "how are verdicts classified for this run?", and the second silently
    winning when both were given. One flag now answers it: a
    :data:`~abicheck.frontends.cli.options.params.BUILTIN_POLICY_PROFILES` name selects that
    profile, anything else is resolved as a document (a path, or a packaged
    built-in like ``security``) and runs under the default profile, exactly
    what ``--policy-file`` did.

    The document half is published on ``ctx.params["policy_file_path"]`` --
    the same key the old option bound -- so every consumer downstream still
    receives the pair it always did, and only the user-facing spelling
    changed.
    """
    resolved = value if value is not None else DEFAULT_POLICY_PROFILE
    if resolved in BUILTIN_POLICY_PROFILES:
        ctx.params["policy_file_path"] = None
        return resolved
    ctx.params["policy_file_path"] = POLICY_FILE_PARAM.convert(resolved, param, ctx)
    return DEFAULT_POLICY_PROFILE


def policy_options(func: F) -> F:
    """Verdict-classification policy + suppression file (`--policy`/`--suppress`).

    Shared verbatim by every verdict-emitting command. ADR-037 D4's fold: the
    separate ``--policy-file`` is gone and ``--policy`` takes both operands
    (see :func:`_resolve_policy_operand`).
    """
    func = click.option(
        "--suppress",
        type=click.Path(exists=True, path_type=Path),
        default=None,
        help="Suppression file (YAML) to filter known/intentional changes.",
    )(func)
    func = click.option(
        "--policy",
        "policy",
        metavar="NAME|PATH",
        default=DEFAULT_POLICY_PROFILE,
        show_default=True,
        callback=_resolve_policy_operand,
        help="How verdicts are classified: a built-in profile "
        "(strict_abi, sdk_vendor, plugin_abi), or a YAML policy document -- "
        "a path, or a packaged built-in name like 'security' -- carrying "
        "per-kind ('overrides:') or selector-scoped ('reclassify:') "
        "re-classification.",
    )(func)
    return func


def severity_options(func: F) -> F:
    """``--severity-preset``, the one visible severity control.

    ADR-037 D4 demoted the four per-category overrides
    (``--severity-abi-breaking``/``--severity-potential-breaking``/
    ``--severity-quality-issues``/``--severity-addition``) into
    ``.abicheck.yml``'s ``severity:`` block, and they have now been removed
    from the CLI outright: a hidden flag that duplicates a config key is a
    second way to say one thing, and the config block is the one that
    survives across invocations. ``severity:`` in the project config is the
    only per-category spelling; the preset stays on the CLI because it is a
    genuine one-off coarse override.

    Still a shared decorator across ``compare`` / ``compare-release`` /
    ``appcompat`` so the contract gate (D10.2) sees it composed once, not
    copy-pasted.
    """
    func = click.option(
        "--severity-preset",
        "severity_preset",
        type=click.Choice(["default", "strict", "info-only"], case_sensitive=True),
        default=None,
        help="Severity preset: 'default', 'strict', or 'info-only'. "
        "Controls exit codes and report labels. A project config's "
        "severity: block overrides individual categories of the preset.",
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
    """``--include-system-declarations``, shared by ``dump`` and ``compare``
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
    ScopeMismatchError.

    Named for what it does rather than for the internal
    ``include_dependencies`` dest it still binds (kept because
    ``AbiSnapshot.dependency_scope``, ``service.run_dump``'s parameter, and
    the snapshot cache key all spell it that way): "dependencies" reads as
    the DT_NEEDED library graph ``--follow-deps`` walks, which is a
    different thing entirely, while what this flag actually restores is the
    *declarations* a system/toolchain header contributed to the AST."""
    func = click.option(
        "--include-system-declarations",
        "include_dependencies",
        is_flag=True,
        default=False,
        help="Include declarations that came from toolchain/system headers "
        "(std::/SYCL/etc. pulled in transitively by #include). Unrelated to "
        "--follow-deps, which walks the DT_NEEDED library graph. By default "
        "these declarations are "
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


#: The AST frontends ``--ast-frontend`` accepts, in one place so the sided and
#: single-valued spellings of the option cannot drift apart.
AST_FRONTENDS: tuple[str, ...] = ("auto", "castxml", "clang", "hybrid")


def compile_context_options(*, sided_frontend: bool = False) -> Callable[[F], F]:
    """L2 header-AST compile context — the cross-toolchain + frontend family.

    A factory (``@compile_context_options()``) because ``--ast-frontend`` is
    side-aware on ``compare`` and single-valued on ``dump``/``scan``: with
    *sided_frontend*, it becomes a repeatable ``[old=|new=]FRONTEND`` option
    (ADR-040 Lever 1's convention, the same one ``--header``/``--include``/
    ``--version`` follow) and :func:`normalize_sided_options` splits it back
    into the ``header_backend`` / ``old_header_backend`` /
    ``new_header_backend`` triple the compare flow already threads. That
    replaces the separate ``--old-ast-frontend``/``--new-ast-frontend`` pair,
    which were a third and fourth spelling of one setting on the one command
    that has two sides.

    The single source of truth for the flags that tell the header frontend how to
    parse the public headers: ``--ast-frontend`` (which frontend), the cross
    compiler (``--compiler``/``--compiler-prefix``, plus the deprecated-but-still
    -functional ``--compiler``/``--compiler-prefix`` aliases), pass-through compiler
    flags (``--gcc-options``/``--compiler-option``, the latter superseding the
    deprecated ``--compiler-option``), an alternate ``--sysroot``, and ``--nostdinc``.
    Shared verbatim by ``dump``, ``scan``, **and** ``compare`` so the three never
    drift (ADR-037 D3 parity; ADR-035 amendment — ``scan`` must be able to reach a
    real L2). Decorators apply bottom-up, so the options are listed in reverse of
    their displayed order. Dest names match the ``dumper.dump`` /
    :class:`~abicheck.service_scan.CompileContext` kwargs exactly, except for the
    ``--compiler``/``--compiler-prefix``/``--compiler-option`` trio, which
    :func:`resolve_compile_context` maps onto the same ``gcc_*`` fields.
    """

    def _apply(func: F) -> F:
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
        # ── --compiler/--compiler-prefix/--compiler-option ──────────────────────
        # The one spelling for the cross-toolchain family. The former
        # --gcc-path/--gcc-prefix/--gcc-option names were always misleading (each
        # accepts a Clang cross-compiler binary just as well) and are removed
        # outright rather than kept as aliases -- carrying two spellings meant a
        # per-invocation conflict resolver whose only correct answer for the
        # repeatable --*-option pair was to reject mixing them anyway.
        # `gcc_path`/`gcc_prefix`/`gcc_option_tokens`/`gcc_options` stay as
        # internal `CompileContext` field names (also composed from build-context
        # flags and the castxml/clang command assembly, and serialized into the
        # run-plan JSON) -- only the user-facing CLI flags are gone.
        func = click.option(
            "--compiler-option",
            "compiler_option_tokens",
            multiple=True,
            help="A single extra compiler flag passed to the header frontend verbatim "
            "(repeatable; not whitespace-split). Use two for a flag + spaced value, "
            "e.g. --compiler-option=-include --compiler-option='some header.h'.",
        )(func)
        func = click.option(
            "--compiler-prefix",
            "compiler_prefix",
            default=None,
            help="Cross-toolchain prefix (e.g. aarch64-linux-gnu-).",
        )(func)
        func = click.option(
            "--compiler",
            "compiler_path",
            default=None,
            help="Path to a GCC/G++ or Clang cross-compiler binary.",
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
            "auto that resolves to plain castxml (no ABICHECK_AST_FRONTEND pin) "
            "routes to Clang without this flag, since CastXML has no host/device "
            "concept to fall back from; a castxml- or hybrid-pinned auto (hybrid "
            "has no device concept either) still rejects it.",
        )(func)
        frontend_kwargs: dict[str, Any] = (
            {"multiple": True, "type": SidedChoiceParam(AST_FRONTENDS)}
            if sided_frontend
            else {
                "default": "auto",
                "show_default": True,
                "type": click.Choice(AST_FRONTENDS, case_sensitive=False),
            }
        )
        func = click.option(
            "--ast-frontend",
            "header_backend",
            **frontend_kwargs,
            help=(
                "Scope to one side with an 'old='/'new=' prefix, repeating the "
                "flag per side (e.g. --ast-frontend old=castxml --ast-frontend "
                "new=clang) when the old release parses on one frontend and the new "
                "one needs the other; a bare value applies to both (default: auto). "
                if sided_frontend
                else ""
            )
            + "C/C++ AST frontend (ADR-037 D8): castxml (default schema reference) "
            "or clang (-ast-dump=json; for hosts where castxml is absent or its "
            "bundled frontend chokes). hybrid (G28 Phase 3) runs BOTH and merges "
            "them (dumper_hybrid.merge_snapshots) — needs both tools installed and "
            "costs roughly 2x a single-backend dump; never selected by auto. auto "
            "resolves to castxml (or the ABICHECK_AST_FRONTEND pin) and never "
            "changes producer unless --allow-ast-frontend-fallback (or "
            "ABICHECK_ALLOW_AST_FALLBACK=1) is explicitly set — except a non-host "
            "--frontend-context (SYCL/DPC++), which an auto resolving to plain "
            "castxml (no pin) routes to clang since castxml can't satisfy it at "
            "all (a castxml- or hybrid-pinned auto still rejects it, since "
            "hybrid has no device concept either; an explicit clang, or auto "
            "pinned to clang via ABICHECK_AST_FRONTEND=clang, satisfies it "
            "directly). "
            "Env: ABICHECK_AST_FRONTEND.",
        )(func)
        return func

    return _apply


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
    an explicit trusted ``--config`` + ``--allow-build-query`` (ADR-032 D5).

    A parse error is fail-loud for an **explicit** ``--config`` (``ClickException``)
    — otherwise an L2-only dump/scan with no ``--sources`` would silently drop the
    intended ``compile:`` settings and still exit 0 — but best-effort (warn +
    CLI-only fallback) for an **auto-discovered** config the user didn't bind to.
    """
    from .config_paths import project_root_for_config
    from .service_scan import CompileContext
    from .workflows.extraction import discover_build_config, load_build_config

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
    # The project root a relative `compile.include_dirs` entry resolves
    # against — cfg.parent for a root-level .abicheck.yml (unchanged), but
    # the directory containing .github/ for a config discovered there
    # (config_paths.py's own docstring has the full reasoning; Codex review,
    # fresh evidence from the .github/ discovery feature landing this base
    # was wrong for).
    base = project_root_for_config(cfg)

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


def resolve_contract_evaluation(contract_mode: str | None) -> bool:
    """``--contract VALUE`` is what enables the ADR-049 evaluator on the CLI.

    There used to be a separate ``--contract-evaluation`` switch, and
    ``--contract`` without it was a hard `UsageError` (exit 64). That was
    first loosened into an implication (naming a domain is enough to ask for
    a decision against it), which left two ways to request one thing; the
    standalone switch is now gone, so the flag *is* the request.

    Deliberately CLI-only. The typed Python API (`api_types.CompareRequest.
    validation_errors`) and the Tier-2 entry (`service._validate_contract_mode`)
    keep requiring an explicit `contract_evaluation=True` alongside a
    *contract_mode* -- both are documented public-API contracts (CLAUDE.md:
    changing them is a breaking Python API change, coordinated separately from
    a CLI ergonomics fix) and this resolver runs strictly before either is ever
    constructed, so the value it derives is indistinguishable from an
    explicitly-passed one to them.

    The former domain-less evaluation (``--contract-evaluation`` with no
    ``--contract``, whose domain fell through to the D7 chain below an
    explicit CLI value) is spelled ``--contract auto``:
    :func:`resolve_contract_domain` maps it back to ``None``, which is exactly
    the state that lets `compatibility_evaluation_wiring.
    resolve_legacy_contract_mode`'s ``--scope-public-headers`` reading, and
    then `.abicheck.yml`, decide the domain.
    """
    return contract_mode is not None


def resolve_contract_domain(
    contract_mode: str | None, ctx: click.Context | None = None
) -> str | None:
    """Map ``--contract auto`` back to "no explicit domain stated".

    ``auto`` exists only to separate the two questions the one flag now
    answers: *evaluate at all* (any value) and *which domain* (a named one).
    Downstream, "the caller stated no domain" has always been spelled ``None``,
    and every D7 tier below ``explicit_cli`` keys off that -- so ``auto`` must
    not reach the resolver as a literal, or it would read as an explicit CLI
    value outranking the very layers it exists to defer to (and
    ``contract_relevance_types.coerce_contract_mode`` would raise on it, since
    ``auto`` is not a real ``ContractMode``).

    Normalizing the local value alone is not enough: the two front ends read
    the raw parameters differently -- ``compare`` hands
    ``cli_compare_receipt.resolve_and_apply`` explicit values, but
    ``cli_scan._resolve_scan_evaluation_config`` rebuilds its inputs from
    ``ctx.params`` and its typed-parameter set from
    ``ctx.get_parameter_source``. Given *ctx*, the normalization is applied
    there too, and the parameter source is demoted from ``COMMANDLINE`` to
    ``DEFAULT`` -- ``auto`` is precisely the caller declining to state a
    domain, so recording it as an explicit CLI value would re-create the
    precedence bug this mapping exists to avoid (Codex review).
    """
    if contract_mode != "auto":
        return contract_mode
    if ctx is not None:
        if "contract_mode" in ctx.params:
            ctx.params["contract_mode"] = None
        ctx.set_parameter_source("contract_mode", click.core.ParameterSource.DEFAULT)
    return None


def _shared_frontend_explicit(ctx: click.Context) -> bool:
    """Did the command line state a *shared* ``--ast-frontend`` value?

    Click reports one parameter source for the whole ``--ast-frontend``
    parameter, so a side-aware command marks it ``COMMANDLINE`` as soon as
    *any* occurrence is given -- including a purely side-qualified
    ``new=castxml``, for which :func:`_split_sided_frontend` then synthesizes
    the shared value ``"auto"`` that nobody typed. Reading the parameter
    source alone would hand that synthesized default to
    :func:`merge_compile_config` as an explicit override and suppress a
    configured ``compile.frontend`` for the side the user never mentioned --
    so a one-sided override would silently discard the project's setting for
    the other side (Codex review). The raw pairs are still on ``ctx.params``
    here (``normalize_sided_options`` rewrites the command's own kwargs, not
    the context), so the shared value's own explicitness is recoverable:
    it was stated exactly when some pair carries the ``both`` side.

    A command composing the unsided ``@compile_context_options()`` has a
    plain string here and keeps the parameter-source answer unchanged.
    """
    if (
        ctx.get_parameter_source("header_backend")
        != click.core.ParameterSource.COMMANDLINE
    ):
        return False
    raw = ctx.params.get("header_backend")
    if isinstance(raw, (tuple, list)):
        return any(
            isinstance(pair, tuple) and len(pair) == 2 and pair[0] == "both"
            for pair in raw
        )
    return True


def sided_frontend_explicit(ctx: click.Context) -> bool:
    """Did the command line state a *sided* ``--ast-frontend old=/new=`` value?

    The inverse-shaped sibling of :func:`_shared_frontend_explicit`, for a
    caller that needs to know whether a per-side override was given (as
    opposed to a bare/``both=`` value) -- e.g. a directory/package compare,
    which threads the both-sides compile context to its release fan-out but
    has no per-library-pair-within-a-release meaning for "parse the old
    library's headers with a different frontend than the new one" (see
    ``cli_resolve._reject_compile_context_for_set_inputs``). Reads the same
    raw ``(side, frontend)`` pairs off ``ctx.params`` that
    :func:`_shared_frontend_explicit` does, for the same reason (normalize_
    sided_options rewrites the command's own kwargs dict, not the context).

    A command composing the unsided ``@compile_context_options()`` has a
    plain string on ``ctx.params["header_backend"]``, never a pair list, so
    this always answers ``False`` for it.
    """
    if (
        ctx.get_parameter_source("header_backend")
        != click.core.ParameterSource.COMMANDLINE
    ):
        return False
    raw = ctx.params.get("header_backend")
    if isinstance(raw, (tuple, list)):
        return any(
            isinstance(pair, tuple) and len(pair) == 2 and pair[0] != "both"
            for pair in raw
        )
    return False


def resolve_compile_context(
    ctx: click.Context,
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
    # --gcc-options removed as a CLI flag (CLI audit PR 5/5); kept as an
    # internal-only, defaulted-None parameter so callers that still compose
    # an effective_gcc_options string from other sources (build-context
    # flags, etc.) can pass it through unchanged.
    gcc_options: str | None = None,
) -> tuple[CompileContext, tuple[Path, ...]]:
    """Build the CLI :class:`CompileContext` and fold the config ``compile:`` block in.

    The single entry point the ``@compile_context_options`` family resolves to
    (ADR-037 D3): construct a :class:`~abicheck.service_scan.CompileContext` from
    the decorator's flags, then delegate to :func:`merge_compile_config` with the
    ``--ast-frontend`` / ``--nostdinc`` explicitness read from the Click parameter
    source (so an explicitly-typed value — even a default-looking ``auto`` — beats
    a pinned config one). ``compare`` / ``dump`` / ``scan`` all call this so their
    L2 compile context cannot drift.

    ``compiler_path``/``compiler_prefix``/``compiler_option_tokens`` are the
    ``--compiler``/``--compiler-prefix``/``--compiler-option`` values; they map
    straight onto ``CompileContext``'s long-standing ``gcc_path``/
    ``gcc_prefix``/``gcc_option_tokens`` fields, which keep their internal
    names (they are also composed from build-context flags and serialized into
    the run-plan JSON), so nothing downstream sees the CLI rename.

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
        gcc_path=compiler_path,
        gcc_prefix=compiler_prefix,
        gcc_options=gcc_options,
        gcc_option_tokens=tuple(compiler_option_tokens),
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
        frontend_explicit=_shared_frontend_explicit(ctx),
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


#: The ``--write FORMAT=PATH`` decorator factory
#: (Codex review: previously declared inline, separately, by ``compare``
#: and ``scan --against``, with drifted help text and duplicated
#: ``reject_incoherent_*`` validation logic) lives in the dependency-free
#: ``frontends.cli.options.secondary_output`` leaf module, not here --
#: ``cli_scan_helpers.py`` needs its validator half and sits on an existing import path back into
#: this module (``cli_options -> cli_resolve -> service_scan -> scan_engine
#: -> cli_scan_helpers``), so a ``cli_scan_helpers -> cli_options`` edge
#: would close a real import cycle. Re-exported here only for the two CLI
#: modules (``cli.py``/``cli_scan.py``) that apply it as a decorator
#: alongside every other option group defined in this file.
secondary_output_options = _secondary_output_options


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
        multiple=True,
        metavar="DIR|PATH",
        help="Audit a *set* of libraries with no old side, as one artifact "
        "(ADR-056): a directory (every discoverable shared library in it), "
        "or a repeatable explicit path, one --artifact-set per member. "
        "Mutually exclusive with the positional ARTIFACT and with --against "
        "(audit-only — no old-side comparison for a set).",
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


#: ``compare``'s release-fanout/build-source/header-graph/evidence option
#: groups moved to ``frontends/cli/options/release.py`` when this module
#: reached the 2000-line hard cap -- the same split, for the same reason, as
#: ``cli_profiles.py``/``cli_options_contract.py`` before it. Re-exported
#: here (``X as X``, so the re-export is explicit to mypy) because every
#: existing caller -- and every existing test importing them from here --
#: reaches these decorators through this module.
#: ADR-037 D10 CLI-contract metadata (family/flag tables, the compare
#: flag-count budget ledger, and :func:`count_visible_options`) moved to
#: ``cli_options_contract.py`` when this module reached the 2000-line hard
#: cap. Re-exported here so every existing caller — the ``cli-contract``
#: gate's own tests and ``tests/test_config_rebalance.py`` — keeps its
#: import path, the same pattern used for ``cli_profiles.py`` below.
from .frontends.cli.options.inventory import (  # noqa: E402
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
from .frontends.cli.options.profiles import (  # noqa: E402
    COMPARE_PROFILES as COMPARE_PROFILES,
    RUN_PROFILE_META_KEY as RUN_PROFILE_META_KEY,
    profile_option as profile_option,
)
from .frontends.cli.options.release import (  # noqa: E402
    adr027_compare_options as adr027_compare_options,
    app_usage_scope_options as app_usage_scope_options,
    build_source_compare_options as build_source_compare_options,
    build_source_dump_options as build_source_dump_options,
    debug_resolution_options as debug_resolution_options,
    evidence_options as evidence_options,
    header_graph_options as header_graph_options,
    release_options as release_options,
    warn_deprecated_header_graph_flags as warn_deprecated_header_graph_flags,
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
from .frontends.cli.options.contract import (  # noqa: E402
    contract_options as contract_options,
    pack_option as pack_option,
)
