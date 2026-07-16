# Copyright 2026 Nikolay Petrov
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

"""CLI — full-stack dependency commands (the ``deps`` group).

``deps tree`` resolves a single binary's dependency closure and symbol bindings;
``deps compare`` diffs a binary's full dependency stack across two environments
(the capability the standalone ``stack-check`` command used to provide, folded in
here). Split out of :mod:`abicheck.cli` to keep that module under the
AI-readiness file-size limit. Imported for side-effect at the bottom of
:mod:`abicheck.cli` so the ``@main.group(...)`` / ``@deps_group.command(...)``
decorators run.
"""
from __future__ import annotations

import sys
from pathlib import Path

import click

from .cli import _detect_binary_format, _safe_write_output, _setup_verbosity, main
from .cli_options import verbose_option
from .stack_checker import under_sysroot


@main.group("deps")
def deps_group() -> None:
    """Inspect a binary's shared-library dependency stack.

    \b
    Subcommands:
      tree     Resolve one binary's dependency closure and symbol bindings.
      compare  Diff a binary's full dependency stack across two environments.
    """


@deps_group.command("tree")
@click.argument("binary", type=click.Path(exists=True, path_type=Path))
@click.option("--search-path", "search_paths", multiple=True,
              type=click.Path(exists=True, path_type=Path),
              help="Additional directory to search for shared libraries.")
@click.option("--sysroot", type=click.Path(exists=True, path_type=Path), default=None,
              help="Sysroot prefix for cross/container analysis.")
@click.option("--ld-library-path", "ld_library_path", default="",
              help="Simulated LD_LIBRARY_PATH (colon-separated).")
@click.option("--format", "fmt", type=click.Choice(["json", "markdown", "html"]),
              default="markdown", show_default=True, help="Output format.")
@click.option("-o", "--output", type=click.Path(path_type=Path), default=None,
              help="Write output to this path (default: stdout).")
@click.option("--dry-run", "dry_run", is_flag=True, default=False,
              help="Show the resolved binary, sysroot, search order, and loader "
                   "inputs without walking/checking the full stack. Writes "
                   "nothing; incompatible with -o/--output.")
@verbose_option
def deps_tree_cmd(
    binary: Path, search_paths: tuple[Path, ...],
    sysroot: Path | None, ld_library_path: str,
    fmt: str, output: Path | None, dry_run: bool, verbose: bool,
) -> None:
    """Show the resolved dependency tree and symbol binding status.

    Resolves the transitive closure of DT_NEEDED dependencies for BINARY
    using loader-accurate search order (RPATH/RUNPATH, LD_LIBRARY_PATH,
    default dirs) and reports symbol binding status.

    \b
    Exit codes:
      0  All dependencies resolved, all required symbols bound
      1  Missing dependencies or symbols (load would fail)

    \b
    Examples:
      abicheck deps tree ./build/libfoo.so
      abicheck deps tree /usr/bin/myapp --format json -o deps.json
      abicheck deps tree ./app --sysroot /path/to/container/rootfs
    """
    from .dry_run import (
        DryRunResult,
        emit_dry_run,
        reject_dry_run_with_output,
        tool_status,
    )

    reject_dry_run_with_output(dry_run, output)
    _setup_verbosity(verbose)

    # Validated ahead of the --dry-run emit below (not just before the real
    # walk) -- a stat/magic-byte check is itself cheap, read-only resolution,
    # so a dry run must agree with the real run instead of reporting "ok" for
    # a non-ELF binary the real run immediately rejects (Codex review).
    fmt_detected = _detect_binary_format(binary)
    if fmt_detected != "elf":
        raise click.ClickException(
            f"deps tree requires an ELF binary; got "
            f"{fmt_detected or 'unknown format'}: {binary}"
        )

    if dry_run:
        dry_result = DryRunResult(command="deps tree")
        dry_result.add(
            "Inputs",
            f"binary: {binary} (detected format: {fmt_detected})",
            f"sysroot: {sysroot}" if sysroot else "sysroot: (none)",
        )
        dry_result.add(
            "Build/source inputs",
            f"search path: {', '.join(str(p) for p in search_paths)}" if search_paths else None,
            f"LD_LIBRARY_PATH: {ld_library_path}" if ld_library_path else None,
        )
        dry_result.add("Tools and frontends", *tool_status("readelf", "ldd"))
        dry_result.add(
            "Output and exit-code behavior",
            f"format: {fmt}",
            "exit codes: 0 all resolved/bound, 1 missing dependency/symbol",
        )
        emit_dry_run(dry_result)

    from .stack_checker import check_single_env
    from .stack_report import stack_to_json, stack_to_markdown

    result = check_single_env(
        binary,
        search_paths=list(search_paths) or None,
        sysroot=sysroot,
        ld_library_path=ld_library_path,
    )

    if fmt == "json":
        text = stack_to_json(result)
    elif fmt == "html":
        from .stack_html import stack_to_html
        text = stack_to_html(result)
    else:
        text = stack_to_markdown(result)
    if output:
        _safe_write_output(output, text)
        click.echo(f"Report written to {output}", err=True)
    else:
        click.echo(text)

    if result.loadability.value == "fail":
        sys.exit(1)


@deps_group.command("compare")
@click.argument("binary", type=click.Path(path_type=Path))
@click.option("--old-root", type=click.Path(exists=True, path_type=Path),
              default=Path("/"), show_default=True,
              help="Sysroot for the old (baseline) environment.")
@click.option("--new-root", type=click.Path(exists=True, path_type=Path),
              default=Path("/"), show_default=True,
              help="Sysroot for the new (candidate) environment.")
@click.option("--search-path", "search_paths", multiple=True,
              type=click.Path(exists=True, path_type=Path),
              help="Additional directory to search for shared libraries.")
@click.option("--ld-library-path", "ld_library_path", default="",
              help="Simulated LD_LIBRARY_PATH (colon-separated).")
@click.option("--format", "fmt", type=click.Choice(["json", "markdown", "html"]),
              default="markdown", show_default=True, help="Output format.")
@click.option("-o", "--output", type=click.Path(path_type=Path), default=None,
              help="Write output to this path (default: stdout).")
@click.option("--dry-run", "dry_run", is_flag=True, default=False,
              help="Show old/new roots, resolved binary paths, and search order "
                   "without running per-library ABI diffs. Writes nothing; "
                   "incompatible with -o/--output.")
@verbose_option
def deps_compare_cmd(
    binary: Path, old_root: Path, new_root: Path,
    search_paths: tuple[Path, ...], ld_library_path: str,
    fmt: str, output: Path | None, dry_run: bool, verbose: bool,
) -> None:
    """Compare a binary's full dependency stack across two environments.

    Resolves all transitive dependencies in both OLD_ROOT and NEW_ROOT sysroots,
    computes symbol bindings, detects changed DSOs, runs per-library ABI diffs,
    and produces a stack-level compatibility verdict.

    BINARY is the path relative to the sysroot (e.g. usr/bin/myapp).

    \b
    Exit codes:
      0  PASS — binary loads and no harmful ABI changes
      1  WARN — loads but ABI risk detected
      4  FAIL — load failure or binary ABI break

    \b
    Examples:
      abicheck deps compare usr/bin/myapp --old-root /old-root --new-root /new-root
      abicheck deps compare usr/lib/libfoo.so.1 \\
        --old-root ./image-v1 --new-root ./image-v2 --format json
    """
    from .dry_run import DryRunResult, emit_dry_run, reject_dry_run_with_output

    reject_dry_run_with_output(dry_run, output)
    _setup_verbosity(verbose)

    # Guard against accidental no-op comparisons.
    if old_root.resolve() == new_root.resolve():
        raise click.UsageError(
            "--old-root and --new-root resolve to the same sysroot; "
            "provide two different roots for stack comparison."
        )

    # Validate that every existing binary is ELF in both sysroots. Checked
    # ahead of the --dry-run emit below (not just before the real stack walk)
    # -- a stat/magic-byte check is itself cheap, read-only resolution, so a
    # dry run must agree with the real run instead of reporting "ok" for a
    # non-ELF binary the real run immediately rejects (Codex review).
    for label, root in [("old", old_root), ("new", new_root)]:
        resolved = under_sysroot(root, binary)
        if resolved.exists():
            fmt_detected = _detect_binary_format(resolved)
            if fmt_detected != "elf":
                raise click.ClickException(
                    f"deps compare requires an ELF binary; got "
                    f"{fmt_detected or 'unknown format'}: {resolved}"
                )

    if dry_run:
        dry_result = DryRunResult(command="deps compare")
        dry_result.add(
            "Inputs",
            f"binary: {binary}",
            f"old-root: {old_root}",
            f"new-root: {new_root}",
        )
        dry_result.add(
            "Build/source inputs",
            f"old resolved path: {under_sysroot(old_root, binary)}",
            f"new resolved path: {under_sysroot(new_root, binary)}",
            f"search path: {', '.join(str(p) for p in search_paths)}" if search_paths else None,
        )
        dry_result.add(
            "Consumer/contract scoping",
            "intended: per-library ABI diff across the resolved dependency stacks",
        )
        dry_result.add(
            "Output and exit-code behavior",
            f"format: {fmt}",
            "exit codes: 0 pass, 1 warn (ABI risk), 4 fail (load/ABI break)",
        )
        emit_dry_run(dry_result)

    from .stack_checker import check_stack
    from .stack_report import stack_to_json, stack_to_markdown

    result = check_stack(
        binary,
        baseline_root=old_root,
        candidate_root=new_root,
        ld_library_path=ld_library_path,
        search_paths=list(search_paths) or None,
    )

    if fmt == "json":
        text = stack_to_json(result)
    elif fmt == "html":
        from .stack_html import stack_to_html
        text = stack_to_html(result)
    else:
        text = stack_to_markdown(result)
    if output:
        _safe_write_output(output, text)
        click.echo(f"Report written to {output}", err=True)
    else:
        click.echo(text)

    if result.loadability.value == "fail" or result.abi_risk.value == "fail":
        sys.exit(4)
    elif result.abi_risk.value == "warn" or result.loadability.value == "warn":
        sys.exit(1)
