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
@verbose_option
def deps_tree_cmd(
    binary: Path, search_paths: tuple[Path, ...],
    sysroot: Path | None, ld_library_path: str,
    fmt: str, output: Path | None, verbose: bool,
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
    _setup_verbosity(verbose)

    fmt_detected = _detect_binary_format(binary)
    if fmt_detected != "elf":
        raise click.ClickException(
            f"deps tree requires an ELF binary; got "
            f"{fmt_detected or 'unknown format'}: {binary}"
        )

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
@click.option("--baseline", type=click.Path(exists=True, path_type=Path),
              default=Path("/"), show_default=True,
              help="Sysroot for the baseline environment.")
@click.option("--candidate", type=click.Path(exists=True, path_type=Path),
              default=Path("/"), show_default=True,
              help="Sysroot for the candidate environment.")
@click.option("--search-path", "search_paths", multiple=True,
              type=click.Path(exists=True, path_type=Path),
              help="Additional directory to search for shared libraries.")
@click.option("--ld-library-path", "ld_library_path", default="",
              help="Simulated LD_LIBRARY_PATH (colon-separated).")
@click.option("--format", "fmt", type=click.Choice(["json", "markdown", "html"]),
              default="markdown", show_default=True, help="Output format.")
@click.option("-o", "--output", type=click.Path(path_type=Path), default=None,
              help="Write output to this path (default: stdout).")
@verbose_option
def deps_compare_cmd(
    binary: Path, baseline: Path, candidate: Path,
    search_paths: tuple[Path, ...], ld_library_path: str,
    fmt: str, output: Path | None, verbose: bool,
) -> None:
    """Compare a binary's full dependency stack across two environments.

    Resolves all transitive dependencies in both BASELINE and CANDIDATE sysroots,
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
      abicheck deps compare usr/bin/myapp --baseline /old-root --candidate /new-root
      abicheck deps compare usr/lib/libfoo.so.1 \\
        --baseline ./image-v1 --candidate ./image-v2 --format json
    """
    _setup_verbosity(verbose)

    # Guard against accidental no-op comparisons.
    if baseline.resolve() == candidate.resolve():
        raise click.UsageError(
            "--baseline and --candidate resolve to the same sysroot; "
            "provide two different roots for stack comparison."
        )

    # Validate that every existing binary is ELF in both sysroots
    for label, root in [("baseline", baseline), ("candidate", candidate)]:
        resolved = root / binary
        if resolved.exists():
            fmt_detected = _detect_binary_format(resolved)
            if fmt_detected != "elf":
                raise click.ClickException(
                    f"deps compare requires an ELF binary; got "
                    f"{fmt_detected or 'unknown format'}: {resolved}"
                )

    from .stack_checker import check_stack
    from .stack_report import stack_to_json, stack_to_markdown

    result = check_stack(
        binary,
        baseline_root=baseline,
        candidate_root=candidate,
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
