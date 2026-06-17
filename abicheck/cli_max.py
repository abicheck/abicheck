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

"""``deep-compare`` — the one-shot deep-evidence compare orchestrator (G21.9).

The data pipeline is ``dump -> compare`` (L0->L5). To reach high-confidence
L3-L5 evidence today, a user has to ``dump`` each side with ``--sources`` at a
chosen depth (embedding the build/source/graph facts inline) and then
``compare`` the two embedded snapshots. ``deep-compare`` collapses that into one
command: it dumps each native-binary side with its source tree at the requested
``--depth`` and hands the two embedded snapshots to ``compare``.

It is deliberately **P09-compatible**: the user supplies the source trees
explicitly (``--old-sources``/``--new-sources``/``--sources``). Nothing is
auto-discovered or guessed — at ``--depth headers`` it degrades to a plain
``compare``. A snapshot/JSON input is passed straight through (it cannot be
re-dumped); evidence flags for such a side are ignored with a warning.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import click

# Import _normalize_binary_input from .cli (the registration parent) rather than
# .cli_resolve so cli_max adds no new import edge beyond the by-design
# cli<->cli_max sibling cycle (cli re-exports it in __all__). The shared option
# families come from the leaf cli_options (no cycle back to cli).
from .cli import _normalize_binary_input, compare_cmd, dump_cmd, main
from .cli_dump_helpers import resolve_dump_depth
from .cli_options import (
    output_options,
    policy_options,
    scope_options,
    two_sided_input_options,
)
from .cli_params import DEPTH_PARAM

_DIR = click.Path(exists=True, file_okay=False, path_type=Path)


def _prepare_side(
    ctx: click.Context,
    *,
    input_path: Path,
    headers: tuple[Path, ...],
    includes: tuple[Path, ...],
    sources: Path | None,
    build_info: Path | None,
    collect_mode: str,
    version: str,
    lang: str,
    header_backend: str,
    out_dir: Path,
    label: str,
) -> Path:
    """Dump one native-binary side to an embedded snapshot; pass snapshots through.

    Returns the path ``compare`` should read for this side. A native binary is
    dumped with its source tree at the requested depth (so L3-L5 ride inline in
    the snapshot); a JSON/Perl snapshot input can't be re-dumped, so it is
    returned unchanged and any evidence flags are reported as ignored.
    """
    norm_path, fmt = _normalize_binary_input(input_path)
    if fmt is None:
        if sources is not None or build_info is not None:
            click.echo(
                f"Warning: {label} input {input_path} is a snapshot, not a native "
                "binary; its --*-sources/--*-build-info are ignored (re-dump the "
                "binary to embed deeper evidence).",
                err=True,
            )
        return input_path

    # A native side at a deep (non-off) depth with no sources/build-info of its
    # own embeds only L0-L2 — and diff_embedded_build_source runs L4/L5 diffs
    # only when BOTH sides carry the surface, so the deep findings silently
    # vanish. Warn per side rather than guess inputs (P09) so the asymmetry is
    # visible (Codex review).
    if collect_mode != "off" and sources is None and build_info is None:
        click.echo(
            f"Warning: {label} side {input_path} is a native binary but no "
            f"--{label}-sources/--{label}-build-info was given, so it embeds no "
            f"L3-L5 evidence at this --depth. Source/graph findings need both sides; "
            f"this comparison is asymmetric. Pass evidence for the {label} side or "
            "use --depth headers.",
            err=True,
        )

    out = out_dir / f"{label}.abi.json"
    ctx.invoke(
        dump_cmd,
        so_path=norm_path,
        headers=headers,
        includes=includes,
        version=version,
        lang=lang,
        header_backend=header_backend,
        sources=sources,
        build_info=build_info,
        collect_mode=collect_mode,
        output=out,
    )
    return out


@main.command("deep-compare")
@click.argument("old_input", type=click.Path(exists=True, path_type=Path))
@click.argument("new_input", type=click.Path(exists=True, path_type=Path))
# ── Evidence inputs (explicit; P09) ──────────────────────────────────────────
@click.option("--old-sources", "old_sources", type=_DIR, default=None,
              help="Source tree for the OLD side (checkout, not a pack). Its L3-L5 "
                   "facts are collected inline at --depth and embedded in the dump.")
@click.option("--new-sources", "new_sources", type=_DIR, default=None,
              help="Source tree for the NEW side.")
@click.option("--sources", "both_sources", type=_DIR, default=None,
              help="Source tree applied to both sides (per-side --old/new-sources override it).")
@click.option("--old-build-info", "old_build_info", type=click.Path(exists=True, path_type=Path),
              default=None, help="Optional L3 build input (build dir / compile_commands.json / "
                   "pack) for the OLD side; auto-found inside --old-sources when omitted.")
@click.option("--new-build-info", "new_build_info", type=click.Path(exists=True, path_type=Path),
              default=None, help="Optional L3 build input for the NEW side.")
# ── Headers / includes / version labels (shared family, ADR-037 D3) ───────────
# Two-sided header/include/version family (the per-side version labels also come
# from this decorator; --lang and the --header-backend trio stay inline below).
@two_sided_input_options
# ── Depth dial (unified vocabulary with `compare`/`dump`/`scan`, ADR-037 D5) ──
@click.option("--depth", "depth", type=DEPTH_PARAM, default=None,
              help="Evidence depth for both sides (unified dial): symbols=L0/L1, "
                   "headers=+L2 AST (== plain compare), build=+L3, source=+L4 "
                   "replay & the L5 graph, full=deepest.")
@click.option("--max", "max_depth", is_flag=True, default=False,
              help="Shorthand for --depth full (the deepest evidence available).")
# ── Header backend + language (per-side labels come from the shared family) ────
@click.option("--lang", default="c++", show_default=True,
              type=click.Choice(["c++", "c"], case_sensitive=False),
              help="Language mode for the header backend (both sides).")
@click.option("--header-backend", "header_backend", default="auto", show_default=True,
              type=click.Choice(["auto", "castxml", "clang"], case_sensitive=False),
              help="L2 header-AST frontend (both sides). Env: ABICHECK_HEADER_BACKEND.")
@click.option("--old-header-backend", "old_header_backend", default=None,
              type=click.Choice(["auto", "castxml", "clang"], case_sensitive=False),
              help="L2 header-AST frontend for the old side only (overrides --header-backend).")
@click.option("--new-header-backend", "new_header_backend", default=None,
              type=click.Choice(["auto", "castxml", "clang"], case_sensitive=False),
              help="L2 header-AST frontend for the new side only (overrides --header-backend).")
# ── Compare pass-through (shared families, ADR-037 D3) ────────────────────────
@output_options(
    ["json", "markdown", "sarif", "html", "junit", "review"],
    output_help="Write the report here instead of stdout.",
)
@policy_options  # --policy / --policy-file / --suppress
# Only the coarse --severity-preset is surfaced here (ADR-037 D4: the
# per-category overrides are config-bound); see INTENTIONAL_SUBSET in cli_options.
@click.option("--severity-preset", "severity_preset",
              type=click.Choice(["default", "strict", "info-only"], case_sensitive=True),
              default=None, help="Severity preset controlling exit codes and labels.")
@click.option("--recommend", is_flag=True, default=False,
              help="Append a release recommendation (semver bump + SONAME action).")
@scope_options  # --scope-public-headers/--no- (ADR-037 D3)
# ── Orchestration knobs ──────────────────────────────────────────────────────
@click.option("--keep-snapshots", "keep_snapshots", type=click.Path(file_okay=False, path_type=Path),
              default=None,
              help="Write the intermediate per-side dumps here (for caching/debugging) "
                   "instead of a throwaway temp dir.")
@click.option("-v", "--verbose", is_flag=True, default=False, help="Enable verbose/debug output.")
@click.pass_context
def deep_compare_cmd(
    ctx: click.Context,
    old_input: Path, new_input: Path,
    old_sources: Path | None, new_sources: Path | None, both_sources: Path | None,
    old_build_info: Path | None, new_build_info: Path | None,
    headers: tuple[Path, ...],
    old_headers_only: tuple[Path, ...], new_headers_only: tuple[Path, ...],
    includes: tuple[Path, ...],
    old_includes_only: tuple[Path, ...], new_includes_only: tuple[Path, ...],
    depth: str | None, max_depth: bool,
    old_version: str, new_version: str,
    lang: str, header_backend: str,
    old_header_backend: str | None, new_header_backend: str | None,
    fmt: str, output: Path | None,
    policy: str, policy_file_path: Path | None,
    severity_preset: str | None,
    suppress: Path | None, recommend: bool, scope_public_headers: bool,
    keep_snapshots: Path | None, verbose: bool,
) -> None:
    """Deep-compare two libraries in one command: dump both sides with their
    source trees at --depth, then compare the embedded snapshots.

    This is the one-shot front door to high-confidence L3-L5 evidence. Supply a
    source checkout per side; abicheck collects the build/source/graph facts
    inline (no guessing — explicit sources only) and folds them into the verdict.

    \b
    Examples:
    \b
      # Deepest evidence, one command (sources per side)
      abicheck deep-compare libfoo.so.1 libfoo.so.2 \\
        --old-sources ./foo-1.x --new-sources ./foo-2.x \\
        -H include/foo.h --max --recommend
    \b
      # Same source tree for both, L3 build context only
      abicheck deep-compare old/libfoo.so new/libfoo.so \\
        --sources ./foo-src --depth build
    \b
    Exit codes match `compare` (the verdict comes from it unchanged).
    """
    # Suppressed for machine formats whose consumers may capture stderr with
    # stdout (mirrors `compare-release`'s deprecation-note discipline).
    if fmt not in {"json", "sarif", "junit"}:
        click.echo(
            "Note: 'deep-compare' is deprecated (ADR-037 D7); use "
            "'abicheck compare <old> <new> --max --old-sources ... --new-sources ...' "
            "for one-shot deep-evidence comparison.",
            err=True,
        )

    old_src = old_sources if old_sources is not None else both_sources
    new_src = new_sources if new_sources is not None else both_sources

    # The depth the embedded snapshots carry; forwarded to compare so its
    # coverage table reflects the evidence actually requested.
    compare_collect_mode = resolve_dump_depth(depth, max_depth, "off", False)
    # --depth symbols suppresses the L2 header AST on both sides (ADR-037 D5).
    if depth == "symbols":
        headers, old_headers_only, new_headers_only = (), (), ()

    # A depth that collects L3-L5 needs *some* evidence to collect from. A side
    # only fails this when it is a native binary that must be dumped yet has no
    # --sources/--build-info of its own — a snapshot/JSON input is exempt because
    # it may already embed an L3-L5 pack that compare consumes (like plain
    # `compare --collect-mode graph-full`). Error only when *neither* side can
    # contribute, so cached deep snapshots still work (Codex review). The
    # asymmetric one-native-side-missing case is handled per side in
    # _prepare_side (a warning, not a hard error).
    def _native_without_evidence(inp: Path, src: Path | None, bi: Path | None) -> bool:
        if src is not None or bi is not None:
            return False
        _, fmt = _normalize_binary_input(inp)
        return fmt is not None  # native binary that would dump with no sources

    if (
        compare_collect_mode != "off"
        and _native_without_evidence(old_input, old_src, old_build_info)
        and _native_without_evidence(new_input, new_src, new_build_info)
    ):
        raise click.UsageError(
            f"deep-compare --depth {depth or 'full'} collects L3-L5 evidence but "
            "neither native input has sources: pass --sources (or per-side "
            "--old-sources/--new-sources) and/or --old/new-build-info. For an "
            "L2-only run use --depth headers or plain `abicheck compare`."
        )

    old_h = old_headers_only or headers
    new_h = new_headers_only or headers
    old_inc = old_includes_only or includes
    new_inc = new_includes_only or includes

    import contextlib

    with contextlib.ExitStack() as stack:
        if keep_snapshots is not None:
            keep_snapshots.mkdir(parents=True, exist_ok=True)
            out_dir = keep_snapshots
        else:
            out_dir = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="abicheck-deep-")))

        old_ready = _prepare_side(
            ctx, input_path=old_input, headers=old_h, includes=old_inc, sources=old_src,
            build_info=old_build_info, collect_mode=compare_collect_mode,
            version=old_version, lang=lang,
            header_backend=old_header_backend or header_backend,
            out_dir=out_dir, label="old",
        )
        new_ready = _prepare_side(
            ctx, input_path=new_input, headers=new_h, includes=new_inc, sources=new_src,
            build_info=new_build_info, collect_mode=compare_collect_mode,
            version=new_version, lang=lang,
            header_backend=new_header_backend or header_backend,
            out_dir=out_dir, label="new",
        )

        # The source facts already ride inline in the dumped snapshots, so compare
        # reads them from the embedded payload — do NOT forward --*-sources (those
        # are pack directories in `compare`, not source trees). Forward the depth
        # via collect_mode only so the coverage rows are honest.
        ctx.invoke(
            compare_cmd,
            old_input=old_ready,
            new_input=new_ready,
            old_version=old_version,
            new_version=new_version,
            lang=lang,
            header_backend=header_backend,
            fmt=fmt,
            output=output,
            policy=policy,
            policy_file_path=policy_file_path,
            severity_preset=severity_preset,
            suppress=suppress,
            recommend=recommend,
            scope_public_headers=scope_public_headers,
            collect_mode=compare_collect_mode,
            verbose=verbose,
        )
