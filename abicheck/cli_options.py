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

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar, overload

import click

from .frontends.cli.options import export_options as _export_options
from .frontends.cli.options.evidence_roles import (
    split_build_evidence,
    split_debug_evidence,
    split_header_evidence,
)
from .frontends.cli.options.label import compare_label_option
from .frontends.cli.options.params import (
    BUILTIN_POLICY_PROFILES,
    DEFAULT_POLICY_PROFILE,
    POLICY_FILE_PARAM,
    SIDED_DUMP_MANIFEST_PARAM,
    SIDED_INCLUDE_PATH_PARAM,
    SIDED_PATH_PARAM,
    normalize_suppress_callback,
)
from .workflows.compile_config import (
    CompileConfigError,
    scope_compile_config_env_toggles,
    set_env_flag_until,
)

if TYPE_CHECKING:
    from .buildsource.build_config import BuildConfig
    from .dry_run_estimate import CompileContext

F = TypeVar("F", bound=Callable[..., object])


# ── ADR-040 Lever 1: side-aware option collapse ──────────────────────────────
#
# ``--old-X`` / ``--new-X`` / ``--X`` triples collapse to one repeatable ``--X``
# whose value carries an optional ``old=`` / ``new=`` prefix (:class:`SidedPathParam`
# returns ``(side, Path)`` pairs). The command bodies stay on their existing
# internal kwargs (``headers`` / ``old_headers_only`` / …) — the two helpers below
# translate the sided tuples back into those kwargs at the boundary, so the engine,
# and the Tier-2 service are untouched.


def split_sided_include_paths(
    triples: Sequence[tuple[str, Path, str | None]],
) -> tuple[tuple[Path, ...], tuple[Path, ...], tuple[Path, ...], dict[Path, str]]:
    """Like :func:`split_sided_paths` (removed), but for ``--include``'s own
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
    ``debug_info``/``pdb``/``version``/``ast-frontend`` dests into the
    per-side kwargs the command bodies consume, in place (ADR-040 L1).

    Absent keys are left untouched, so this is safe to call on any command that
    composes only a subset of the sided families.

    Three of these inputs carry more than one evidence *transport* since
    one-comparison-product plan Phase 7n -- ``header`` also accepts a
    development package, ``debug_info`` a debug package or a detached debug
    file, ``build_info`` a probe-matrix snapshot -- so their split is
    content-routed and lives in
    :mod:`abicheck.frontends.cli.options.evidence_roles`. Every destination
    this function produces is unchanged: the merge is in the flags, not in
    what the command bodies read.
    """
    if "header" in kwargs:
        kwargs.update(split_header_evidence(kwargs.pop("header")))  # type: ignore[arg-type]
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
    if "debug_info" in kwargs:
        kwargs.update(split_debug_evidence(kwargs.pop("debug_info")))  # type: ignore[arg-type]
    if "sources" in kwargs:
        old_s, new_s = _split_sided_single(kwargs.pop("sources"))  # type: ignore[arg-type]
        kwargs["old_sources"] = old_s
        kwargs["new_sources"] = new_s
    if "build_info" in kwargs:
        kwargs.update(split_build_evidence(kwargs.pop("build_info")))  # type: ignore[arg-type]
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
        "-H/--header list. Side-scoped: repeat the flag with an "
        "'old='/'new=' prefix per side (e.g. --dump-manifest old=v1/abi.yml "
        "--dump-manifest new=v2/abi.yml); a bare value applies to both. "
        "Mutually exclusive with -H/--header for that side (declare the "
        "public surface in the manifest's own base profile instead). ELF "
        "only so far.",
    )(func)
    func = compare_label_option(func)
    func = click.option(
        "-I",
        "--include",
        "include",
        multiple=True,
        type=SIDED_INCLUDE_PATH_PARAM,
        help="Extra include directory for castxml. Applies to both sides; scope "
        "to one side with an 'old='/'new=' prefix, repeating the flag per side "
        "(e.g. --include old=inc1 --include new=inc2). Repeatable. "
        "A labeled 'old:LABEL=PATH'/'new:LABEL=PATH' form (e.g. --include "
        "old:support=old/src --include new:support=new/src) names a "
        "side-specific support root under one shared logical identity, so a "
        "genuine two-checkout compare doesn't spuriously PROFILE_MISMATCH on "
        "it.",
    )(func)
    func = click.option(
        "-H",
        "--header",
        "header",
        multiple=True,
        type=SIDED_PATH_PARAM,
        help="Public header file or directory -- or a development package "
        "carrying them (RPM/Deb/tar; directory/package inputs only), which is "
        "recognised from the operand's content rather than its name. Applies "
        "to both sides; scope to "
        "one side with an 'old='/'new=' prefix, repeating the flag per side "
        "(e.g. --header old=v1/foo.h --header new=v2/foo.h). Repeatable. "
        "Recommended for full ABI analysis; without headers, abicheck uses whatever "
        "artifact evidence is available instead (ELF may add DWARF/BTF/CTF, PE may add PDB, "
        "Mach-O stays limited to binary metadata: exports plus load-command facts "
        "like install name, dependencies, and rpaths) but has no header AST or "
        "public-surface scoping. "
        "Scopes the ABI surface to declarations in these headers for ELF; on PE/Mach-O scoping is "
        "best-effort and falls back to the export table when castxml is unavailable or names don't match "
        "(e.g. MSVC C++ mangling). Validated for native binaries; ignored for snapshots.",
    )(func)
    func = exclude_header_option(func)
    return func


def exclude_header_option(func: F) -> F:
    """``--exclude-header PATTERN`` -- drop matching headers from the parse.

    A separate decorator so ``dump`` and ``compare`` share one spelling and
    one help text (``tests/test_cli_contract.py`` enforces that a shared
    concept has one canonical primary spelling).
    """
    return click.option(
        "--exclude-header",
        "exclude_headers",
        multiple=True,
        metavar="PATTERN",
        help="Exclude headers matching PATTERN (fnmatch-style) from the "
        "parsed surface. Matched against the header's bare name "
        "(--exclude-header fftw3.h), its full path, or a glob "
        "(--exclude-header '**/detail/*'). Repeatable; applies to both "
        "sides. Use it when a header *directory* contains headers that "
        "cannot be parsed together -- two vendored copies of a third-party "
        "API declaring conflicting typedefs, for example -- which otherwise "
        "makes the whole directory unusable as a -H operand. A matching "
        "header reached through another header's #include is scoped out "
        "like a toolchain header: what only it declares is not observed "
        "(reported as reduced evidence, not a removal), while types the "
        "library's own API uses are still checked. Requires dependency "
        "scoping (off under --include-system-declarations).",
    )(func)


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
    """Policy + suppression files for every verdict-emitting command (ADR-037 D4).

    ``--policy`` takes both operands (:func:`_resolve_policy_operand`).
    """
    func = click.option(
        "--suppress",
        type=click.Path(exists=True, path_type=Path),
        multiple=True,
        callback=normalize_suppress_callback,
        help="Suppression file (YAML) to filter known/intentional changes. Repeatable: the rules of all files are merged in order, each keeping its file.",
    )(func)
    return policy_option(func)


def policy_option(func: F) -> F:
    """``--policy NAME|PATH`` alone, for a command that classifies verdicts
    but takes no suppression file (``project history``). The command also
    receives ``policy_file_path`` (see :func:`_resolve_policy_operand`)."""
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


def _define_callback(  # noqa: ARG001 -- click option callback signature (ctx, param, value)
    ctx: click.Context, param: click.Parameter, value: tuple[str, ...]
) -> tuple[str, ...]:
    """Validate every ``-D/--define`` operand eagerly, at parse time, and
    return the canonical ``NAME``/``NAME=VALUE`` spellings.

    Validation lives in :mod:`abicheck.macro_definition` (the one parser),
    not here: this callback only translates its
    :class:`~abicheck.macro_definition.MacroDefinitionError` into the usage
    error Click reports (exit 64, per AGENTS.md's exit-code table), so a
    malformed definition fails before any compiler is invoked and reports
    the same text whichever command was run.
    """
    from .model.macro_definition import MacroDefinitionError, parse_macro_definition

    spellings: list[str] = []
    for raw in value or ():
        try:
            spellings.append(parse_macro_definition(raw).spelling)
        except MacroDefinitionError as exc:
            raise click.UsageError(str(exc)) from exc
    return tuple(spellings)


def dump_header_input_options(func: F) -> F:
    """``dump``'s ``-H/--header`` and ``-I/--include`` operand pair.

    Declared here with every other shared CLI flag rather than inline in
    ``frontends/cli/commands/dump.py`` (ADR-061: move responsibility to its
    owner instead of growing a capped command module). Decorators apply
    bottom-up, so ``--include`` is listed first to display after ``--header``.
    """
    func = click.option(
        "-I",
        "--include",
        "includes",
        multiple=True,
        type=click.Path(path_type=Path),
        help="Extra include directory for castxml.",
    )(func)
    func = click.option(
        "-H",
        "--header",
        "headers",
        multiple=True,
        type=click.Path(exists=True, path_type=Path),
        help="Public header file or directory (repeat for multiple).",
    )(func)
    return func


def define_option(func: F) -> F:
    """``-D/--define NAME[=VALUE]`` -- ADR-074's narrow, per-invocation
    preprocessor definition for the L2 header parse. Shared verbatim by
    ``dump`` and ``compare`` so the two cannot drift (ADR-037 D3 parity).

    This is deliberately **not** a reopening of the retired
    ``--compiler-option``/``--gcc-options`` family (ADR-068 plan §4.2, "CLI
    cleanup phase two" 7b): the operand is a *logical macro definition*,
    parsed and revalidated into exactly one ``-D``-prefixed argv token, and
    can never become a second compiler option. General compiler flags remain
    config-only, under ``.abicheck.yml``'s ``compile.options``.

    Why it earns a CLI spelling at all when ``compile.compiler``/
    ``compile.std``/``compile.options`` did not: those name *toolchain
    identity*, which is stable per project and belongs in reviewed
    configuration. A feature macro that gates an opt-in public surface
    (PVXS's ``PVXS_ENABLE_EXPERT_API``, pcre2's ``PCRE2_CODE_UNIT_WIDTH``)
    selects *which surface is being analysed* -- the same question ``-H``
    and ``-I`` answer, and both of those kept their CLI spelling for exactly
    that reason.

    On ``compare`` the definitions apply to **both** sides identically:
    there is deliberately no ``old=``/``new=`` form, since two sides parsed
    under different macro contexts are two different public surfaces and
    every finding between them would be an artifact of the flags, not of the
    change (ADR-074 D1).
    """
    func = click.option(
        "-D",
        "--define",
        "defines",
        multiple=True,
        metavar="NAME[=VALUE]",
        callback=_define_callback,
        help="Define a preprocessor macro for the header parse (repeatable). "
        "A one-off override for headers whose public surface is gated behind "
        "a feature macro, e.g. -DPVXS_ENABLE_EXPERT_API or "
        "-DPCRE2_CODE_UNIT_WIDTH=8. Applies to both sides of a compare. For "
        "a stable project/CI contract prefer .abicheck.yml's "
        "compile.defines:, which this overrides per macro name. Takes a "
        "macro definition only -- general compiler flags stay in "
        "compile.options.",
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
        if value:
            set_env_flag_until(env_var, ctx.call_on_close)
        return None

    return _callback


_enable_ast_fallback_for_command = _scoped_env_flag_callback(
    "ABICHECK_ALLOW_AST_FALLBACK"
)
_enable_unsupported_castxml_for_command = _scoped_env_flag_callback(
    "ABICHECK_ALLOW_UNSUPPORTED_CASTXML"
)


def apply_compile_config_env_toggles(
    ctx: click.Context, bc: BuildConfig | None
) -> None:
    """Apply ``compile.ast_frontend_fallback``/``compile.allow_unsupported_castxml``.

    Phase 7 (one-comparison-product.md §4.1/§4.2): ``--allow-ast-frontend-
    fallback``/``--allow-unsupported-castxml`` are removed from ``compare``/
    ``dump`` (CONFIG class -- no CLI spelling survives, ADR-068 D5 guard #2).
    Both were already pure per-invocation env-var togglers
    (:func:`_scoped_env_flag_callback`), so a ``.abicheck.yml`` ``compile:``
    block setting either to ``true`` reuses those same callbacks directly
    (scoped to *ctx* the identical way, restored via ``ctx.call_on_close``)
    rather than re-implementing the set/restore dance a second time.
    ``scan`` is unaffected: it keeps both flags, which still win over a
    config ``true`` simply by also being applied (idempotent env-var set).
    """
    scope_compile_config_env_toggles(bc, ctx.call_on_close)


#: The AST frontends ``--ast-frontend`` accepts, in one place so the sided and
#: single-valued spellings of the option cannot drift apart.
AST_FRONTENDS: tuple[str, ...] = ("auto", "castxml", "clang", "hybrid")


# ADR-049 D7's `--contract` value resolvers moved to the leaf
# `frontends/cli/options/contract.py` (see that module's own note for why:
# the one-sided `--no-baseline` dispatch needs them without joining the CLI
# registration SCC). Re-exported here (`X as X`, so ruff keeps them) because
# this module is their documented import path -- `cli_compare_helpers`
# reaches them here (as the retired `cli_scan` did), and needn't change.
from .frontends.cli.options.contract import (  # noqa: E402
    resolve_contract_domain as resolve_contract_domain,
    resolve_contract_evaluation as resolve_contract_evaluation,
)


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
    # ADR-074: the canonical `NAME`/`NAME=VALUE` spellings from -D/--define,
    # already validated by the option's own callback. Folded by macro name
    # against `compile.defines` in `merge_compile_config`.
    defines: tuple[str, ...] = (),
    # --gcc-options removed as a CLI flag (CLI audit PR 5/5); kept as an
    # internal-only, defaulted-None parameter so callers that still compose
    # an effective_gcc_options string from other sources (build-context
    # flags, etc.) can pass it through unchanged.
    gcc_options: str | None = None,
    # See `merge_compile_config`'s own docstring: set this when `build_config`
    # is already a *resolved* path from the caller's own auto-discovery
    # (not necessarily the raw CLI value), so the `compile.compiler` trust
    # gate isn't fooled into treating a discovered config as explicitly
    # chosen (Codex review, fresh evidence — real finding on PR #1154).
    config_explicit: bool | None = None,
    config_overrides: Mapping[str, object] | None = None,
) -> tuple[CompileContext, tuple[Path, ...]]:
    """The Click adapter over ``workflows.compile_config.resolve_compile_context``.

    That function is the one L2 compile-context resolution (ADR-037 D3); this
    adapter reads the ``--nostdinc``/``--frontend-context`` explicitness off
    the Click parameter source (so an explicitly-typed value — even a
    default-looking one — beats a pinned config one) and renders an unusable
    named config as a usage error. ``compare``'s single-pair session calls
    the same function with explicitness from its stated options.

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
    from .workflows.compile_config import (
        resolve_compile_context as _resolve_compile_context,
    )

    def _explicit(param: str) -> bool:
        return bool(
            ctx.get_parameter_source(param) == click.core.ParameterSource.COMMANDLINE
        )

    try:
        return _resolve_compile_context(
            sysroot=sysroot,
            nostdinc=nostdinc,
            header_backend=header_backend,
            includes=includes,
            build_config=build_config,
            sources=sources,
            frontend_context=frontend_context,
            compiler_path=compiler_path,
            compiler_prefix=compiler_prefix,
            compiler_option_tokens=compiler_option_tokens,
            defines=defines,
            gcc_options=gcc_options,
            config_explicit=config_explicit,
            nostdinc_explicit=_explicit("nostdinc"),
            # `_explicit` answers False for a parameter a command no longer
            # declares (compare/dump, Phase 7), so the config value applies.
            frontend_context_explicit=_explicit("frontend_context"),
            config_overrides=config_overrides,
        )
    except CompileConfigError as exc:
        raise click.UsageError(str(exc)) from exc


#: The one ``-o FORMAT=DESTINATION`` export request (plan slice 7m) lives in
#: the dependency-free ``frontends.cli.options.export`` leaf module, not here
#: -- same cycle-avoidance reason the ``-o`` module it replaces
#: recorded, and the same reason ``export_options`` is only *re-exported*
#: here: every other cross-command Click option group is applied from this
#: module, so the decorator has to be reachable through it, while modules
#: sitting on an import path back into this one need the parser half without
#: acquiring that edge.
#:
#: It replaces both ``output_options`` (the ``--format`` / path-only
#: ``-o/--output`` pair) and ``secondary_output_options``
#: (``--write FORMAT=PATH``): four mechanisms for "which artifacts does this
#: analysis produce" are now one repeatable operand, with ``-`` as the stdout
#: destination -- see that module's docstring for why the merge Phase 7k
#: declined is the right call once ``-`` exists.
export_options = _export_options


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


def changed_path_options(func: F) -> F:
    """Changed-path localization: ``--since`` / ``--changed-path`` (ADR-068,
    plan §3 #12).

    A per-run input by construction -- a PR's own diff -- so it stays a CLI
    flag rather than a ``.abicheck.yml`` property (ADR-068 D5). Scoping only:
    neither flag produces a finding of its own; together they seed the
    points-of-interest set a ``--depth source`` run replays, per ADR-043 D7
    (changed-path scope when a seed exists, else the current library target --
    never a zero-TU no-op). The seed itself is resolved by the one shared
    owner, :func:`abicheck.workflows.changed_paths.resolve_changed_seed`, so
    ``compare`` and ``scan`` cannot drift on what a seed is.
    """
    func = click.option(
        "--changed-path",
        "changed_paths_opt",
        multiple=True,
        help="Changed path to focus this run's source-evidence scope on "
        "(repeatable; alternative to --since). Narrows the L4/L5 "
        "points of interest a --depth source run examines; produces no "
        "finding of its own and never changes a verdict.",
    )(func)
    func = click.option(
        "--since",
        "since",
        default=None,
        help="Focus this run's source-evidence scope on the files changed "
        "vs a git ref (e.g. origin/main), resolved with `git diff "
        "--name-only <ref>...HEAD`. Same scoping-only effect as "
        "--changed-path, which wins when both are given; a ref that "
        "cannot be resolved warns and leaves the scope broad.",
    )(func)
    return func


def abi3_option(func: F) -> F:
    """The ``--abi3 VERSION`` stable-ABI audit floor (ADR-068 D3, plan §3 #15).

    On ``compare`` this selects the candidate-side (NEW) enrichment stage: the
    audit is meaningful only on the candidate, so its findings ride the same
    result document, marked, and are never evaluated on the baseline side. The
    floor is also a stable project property, so ``.abicheck.yml``'s
    ``python.abi3_floor`` supplies a default this flag overrides per run
    (ADR-068 D5).
    """
    func = click.option(
        "--abi3-floor",
        "abi3",
        default=None,
        metavar="VERSION",
        help="Audit the candidate (NEW) against a Py_LIMITED_API floor, e.g. "
        "`3.9`. Classifies the module's imported CPython C-API against the "
        "stable ABI and flags private/unstable imports and stable symbols "
        "newer than the floor as `python_stable_abi_violation` (advisory; "
        "gate it through --policy/.abicheck.yml policy.overrides). Requires "
        "the candidate to be a CPython extension module -- anything else is "
        "an evidence-contract error (exit 7). Overrides .abicheck.yml's "
        "python.abi3_floor for this run.",
    )(func)
    return func


#: ``env_matrix_option``/``--env-matrix`` was removed (ADR-068 D5, plan
#: "compare --env-matrix" ruling): declared deployment constraints are a
#: stable project property, now ``.abicheck.yml``'s ``deployment:`` config
#: key (``buildsource.build_config.BuildConfig.deployment``, parsed via
#: ``EnvironmentMatrix.from_dict``), resolved through
#: ``workflows.compare_configuration.resolve_compare_config``'s ``deployment`` field
#: with no surviving CLI override (guard #2 -- no escape hatch). See
#: ``scripts/retired_surfaces.py`` for the retired spelling.


def set_input_options(func: F) -> F:
    """Set-input fan-out knobs: ``--output-dir`` / ``--select``/``--select-required``.

    ADR-037 D7 folds ``compare-release`` into ``compare`` via input-type
    dispatch: when ``compare``'s operands are directories or packages it fans out
    to a per-library comparison, and these flags tune that fan-out (per-library
    report directory, and -- ADR-065 S1 -- an explicit member selection). On
    single-file inputs they are a no-op and ``compare`` warns. Declared once
    here so the dispatch and the deprecated ``compare-release`` alias share
    one surface. Applied bottom-up, so listed in reverse of displayed order.

    ``-j/--jobs`` used to be part of this family (ADR-068 D5 / plan Phase
    7h): removed outright -- the fan-out already auto-detects the CPU count
    and clamps it to available memory, so a manual override was a tuning
    detail, not a per-run decision.

    ``--dso-only`` (Phase 7d, one-comparison-product.md §4.1) is gone too --
    ``release.dso_only`` in ``.abicheck.yml`` is its only source now (a
    stable release-topology property, no surviving CLI override), resolved
    onto :class:`abicheck.workflows.compare_configuration.ResolvedCompareConfig`.
    """
    func = click.option(
        "--select-required",
        "select_required",
        multiple=True,
        metavar="KEY",
        help="Declare a required expected release member by its canonical "
        "release-matching key (e.g. 'libfoo.so', never a raw filename stem; "
        "directory/package inputs only). Repeatable. With "
        "any --select/--select-required given, only declared members are "
        "compared. A missing declared-required member contributes to "
        "the .abicheck.yml scope.on_incomplete completeness gate; a plain "
        "--select member does not.",
    )(func)
    func = click.option(
        "--select",
        "select",
        multiple=True,
        metavar="KEY",
        help="Declare an optional expected release member. Repeatable -- see "
        "--select-required (directory/package inputs only).",
    )(func)
    # Plan slice 7m: ``--output-dir`` retired here. The per-component fan-out
    # it named is now a *destination shape* inside the one export request --
    # ``-o json=reports/`` writes exactly what ``--output-dir reports`` wrote
    # -- so it is covered by the same collision detection, stdout
    # exclusivity, and dry-run rejection every other export is, instead of
    # being a fourth parallel way to say where an artifact goes.
    return func


#: ``compare``'s release-fanout/build-source/header-graph/evidence option
#: groups moved to ``frontends/cli/options/release.py`` when this module
#: reached the 2000-line hard cap -- the same split, for the same reason, as
#: ``cli_options_contract.py`` before it. Re-exported
#: here (``X as X``, so the re-export is explicit to mypy) because every
#: existing caller -- and every existing test importing them from here --
#: reaches these decorators through this module.
#: ADR-037 D10 CLI-contract metadata (family/flag tables, the compare
#: flag-count budget ledger) moved to
#: ``cli_options_contract.py`` when this module reached the 2000-line hard
#: cap. Re-exported here so every existing caller — the ``cli-contract``
#: gate's own tests and ``tests/test_config_rebalance.py`` — keeps its
#: import path, the same pattern used for the modules re-exported below.
#: G38 Phase 17's ``--bundle-facts-library-manifest`` option, a small,
#: standalone leaf module (see its own docstring for why it isn't declared
#: inline on ``compare_cmd``). Re-exported here for the same reason as
#: ``contract_options``/``pack_option`` below.
from .frontends.cli.options.bundle_facts_library_manifest import (  # noqa: E402
    bundle_facts_manifest_options as bundle_facts_manifest_options,
    reject_bundle_facts_manifest_without_old_bundle_facts as reject_bundle_facts_manifest_without_old_bundle_facts,
)

#: ADR-049's contract-evaluation option decorator moved to
#: ``cli_contract_options.py`` when this module reached its own 2000-line
#: hard limit -- the same split, for the same reason, as the other
#: ``frontends/cli/options/`` siblings. Re-exported here (``X as X``, so
#: the re-export is explicit to mypy) because it is a shared decorator
#: callers already reach through this module, and a leaf holding option
#: definitions never imports back.
from .frontends.cli.options.contract import (  # noqa: E402
    contract_options as contract_options,
    pack_option as pack_option,
)
from .frontends.cli.options.inventory import (  # noqa: E402
    COMPARE_FLAG_BUDGET as COMPARE_FLAG_BUDGET,
    COMPARE_OPTION_RULINGS as COMPARE_OPTION_RULINGS,
    DUMP_FLAG_BUDGET as DUMP_FLAG_BUDGET,
    DUMP_OPTION_RULINGS as DUMP_OPTION_RULINGS,
    FAMILY_DECORATOR as FAMILY_DECORATOR,
    FAMILY_FLAGS as FAMILY_FLAGS,
    INTENTIONAL_SUBSET as INTENTIONAL_SUBSET,
    REQUIRED_FAMILIES as REQUIRED_FAMILIES,
    RULINGS_BY_COMMAND as RULINGS_BY_COMMAND,
    VERDICT_EMITTING_COMMANDS as VERDICT_EMITTING_COMMANDS,
)
from .frontends.cli.options.release import (  # noqa: E402
    app_usage_scope_options as app_usage_scope_options,
    build_source_compare_options as build_source_compare_options,
    build_source_dump_options as build_source_dump_options,
    debug_resolution_options as debug_resolution_options,
    evidence_options as evidence_options,
    release_options as release_options,
    variant_kwargs_from_context as variant_kwargs_from_context,
    variant_options as variant_options,
)
