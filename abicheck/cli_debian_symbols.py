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

"""CLI — debian-symbols command group.

Split out of :mod:`abicheck.cli` to keep that module under the
AI-readiness file-size limit. Imported for side-effect at the bottom
of :mod:`abicheck.cli` so ``main.add_command(debian_symbols_group)``
runs.
"""
from __future__ import annotations

import sys
from pathlib import Path

import click

from .cli import main

# ── debian-symbols command group ──────────────────────────────────────────────

@click.group("debian-symbols")
def debian_symbols_group() -> None:
    """Generate, validate, and diff Debian symbols files.

    Integrates abicheck with Debian/Ubuntu packaging workflows where
    dpkg-gensymbols and dpkg-shlibdeps use symbols files for fine-grained
    dependency tracking.
    """


@debian_symbols_group.command("generate")
@click.argument("so_path", type=click.Path(exists=True, path_type=Path))
@click.option("-o", "--output", "output_path", type=click.Path(path_type=Path), default=None,
              help="Output file path. Prints to stdout if not specified.")
@click.option("--package", default="", help="Debian package name (derived from SONAME if empty).")
@click.option("--version", "version", default="#MINVER#", show_default=True,
              help="Minimum version string for symbols.")
@click.option("--no-cpp", "no_cpp", is_flag=True, default=False,
              help="Do not emit C++ demangled (c++) form; use mangled names only.")
def debian_symbols_generate(
    so_path: Path,
    output_path: Path | None,
    package: str,
    version: str,
    no_cpp: bool,
) -> None:
    """Generate a Debian symbols file from a shared library.

    \b
    Example:
      abicheck debian-symbols generate libfoo.so -o debian/libfoo1.symbols
    """
    from .debian_symbols import generate_from_binary

    symbols_file = generate_from_binary(
        so_path,
        package=package,
        version=version,
        use_cpp=not no_cpp,
    )

    text = symbols_file.format()
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(text, encoding="utf-8")
        click.echo(f"Symbols file written to {output_path}")
    else:
        click.echo(text, nl=False)


@debian_symbols_group.command("validate")
@click.argument("so_path", type=click.Path(exists=True, path_type=Path))
@click.argument("symbols_path", type=click.Path(exists=True, path_type=Path))
def debian_symbols_validate(so_path: Path, symbols_path: Path) -> None:
    """Validate a Debian symbols file against a shared library binary.

    \b
    Exit codes:
      0  symbols file matches the binary
      2  mismatch (missing symbols)

    \b
    Example:
      abicheck debian-symbols validate libfoo.so debian/libfoo1.symbols
    """
    from .debian_symbols import format_validation_report, validate_from_binary

    result = validate_from_binary(so_path, symbols_path)
    click.echo(format_validation_report(result), nl=False)

    if not result.passed:
        sys.exit(2)


@debian_symbols_group.command("diff")
@click.argument("old_symbols", type=click.Path(exists=True, path_type=Path))
@click.argument("new_symbols", type=click.Path(exists=True, path_type=Path))
def debian_symbols_diff(old_symbols: Path, new_symbols: Path) -> None:
    """Diff two Debian symbols files.

    \b
    Example:
      abicheck debian-symbols diff old/libfoo1.symbols new/libfoo1.symbols
    """
    from .debian_symbols import (
        diff_symbols_files,
        format_diff_report,
        load_symbols_file,
    )

    old = load_symbols_file(old_symbols)
    new = load_symbols_file(new_symbols)
    diff = diff_symbols_files(old, new)

    click.echo(format_diff_report(diff, str(old_symbols), str(new_symbols)), nl=False)


main.add_command(debian_symbols_group)
