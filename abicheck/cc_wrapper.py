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

"""``abicheck-cc`` — Flow-2 compiler wrapper (ADR-035 D5, G19.4).

Prefix a normal compile with ``abicheck-cc`` to have abicheck capture the TU's
source ABI **during the real build**, with that TU's exact flags/macros::

    abicheck-cc c++ -std=c++17 -Iinclude -c src/foo.cpp -o foo.o

It runs the real compile (pass-through, preserving the compiler's exit code),
then **best-effort** extracts a normalized :class:`SourceAbiTu` for the TU and
appends it to an ``abicheck_inputs/`` pack. A later ``abicheck dump libfoo.so
--build-info ./abicheck_inputs/`` ingests those exact-build-context facts with
no second frontend (Flow 2). Fact extraction never fails the build (authority
rule, ADR-028 D3): a missing front-end or a parse error degrades to a warning.

This is the **supported portable producer** — it reuses the castxml/clang source
extractors. The Clang plugin (``contrib/abicheck-clang-plugin/``) is an optional
optimization that removes the second frontend pass; it emits the same
``source_facts`` schema, so both ride the identical ingest.

Configuration is by environment so the wrapper stays argv-transparent:

==========================  ===================================================
``ABICHECK_INPUTS_DIR``     pack output dir (default ``abicheck_inputs``)
``ABICHECK_CC_EXTRACTOR``   ``auto`` | ``clang`` | ``castxml`` (default auto)
``ABICHECK_CC_HEADERS``     ``os.pathsep``-joined public-header roots (ADR-015)
``ABICHECK_CC_LIBRARY``     library name stamped into the manifest / target id
``ABICHECK_CC_VERSION``     version stamped into the manifest
``ABICHECK_CC_DISABLE``     set (non-empty) → pure pass-through, no extraction
==========================  ===================================================
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

import click

from .buildsource.adapters.base import (
    compile_unit_id,
    effective_language,
    extract_abi_relevant_flags,
    sources_from_argv,
)
from .buildsource.build_evidence import CompileUnit
from .buildsource.inputs_emit import (
    append_source_facts,
    facts_filename,
    init_inputs_pack,
)
from .buildsource.source_abi import SourceAbiTu

#: Tokens that mark an invocation as preprocess-/dependency-only — it produces no
#: object that ships in the artifact, so capturing facts for it would pollute the
#: Flow-2 baseline with TUs that are not part of the build (Codex review). GNU/
#: clang: ``-E`` (preprocess), ``-M``/``-MM`` (standalone dependency scan — note
#: ``-MD``/``-MMD``/``-MF`` are *different* tokens used alongside a real ``-c``,
#: so exact-token matching leaves those compiles untouched). MSVC: ``/E /P /EP``.
_PREPROCESS_ONLY_FLAGS = frozenset({"-E", "-M", "-MM", "/E", "/P", "/EP"})


def _is_preprocess_only(command: Sequence[str]) -> bool:
    return any(arg in _PREPROCESS_ONLY_FLAGS for arg in command)


def compile_units_from_command(command: Sequence[str], directory: str | Path) -> list[CompileUnit]:
    """Build a :class:`CompileUnit` for **every** source operand in *command*.

    A single invocation may compile several TUs (``gcc -c a.c b.c``); each gets
    its own unit (shared flags, per-source id/language) so none is silently
    dropped from the pack. Returns ``[]`` for a link-only step or a
    preprocess-/dependency-only invocation (``-E``/``-M``…).
    """
    command = list(command)
    if len(command) < 2 or _is_preprocess_only(command):
        return []
    # accept_forced_language: capture an extensionless/generated source under a
    # forced `-x <lang>` (clang++ -x c++ -c generated), which extension-only
    # discovery would miss.
    sources = sources_from_argv(command, accept_forced_language=True)
    if not sources:
        return []
    # Lazy import keeps the lightweight wrapper's import graph thin. Flags are
    # shared across the command's TUs, so derive them once.
    from .build_context import _extract_flags

    ctx = _extract_flags(command, Path(directory))
    abi_flags = list(extract_abi_relevant_flags(command))
    units: list[CompileUnit] = []
    for source in sources:
        units.append(
            CompileUnit(
                id=compile_unit_id(source, command),
                source=source,
                directory=str(directory),
                argv=list(command),
                language=effective_language(command, source),
                standard=ctx.language_standard or "",
                defines={k: (v or "") for k, v in ctx.defines.items()},
                undefines=sorted(ctx.undefines),
                include_paths=[str(p) for p in ctx.include_paths],
                system_include_paths=[str(p) for p in ctx.system_includes],
                sysroot=str(ctx.sysroot) if ctx.sysroot else None,
                target_triple=ctx.target_triple or "",
                abi_relevant_flags=list(abi_flags),
            )
        )
    return units


def compile_unit_from_command(command: Sequence[str], directory: str | Path) -> CompileUnit | None:
    """The first TU of *command* (convenience over :func:`compile_units_from_command`)."""
    units = compile_units_from_command(command, directory)
    return units[0] if units else None


def emit_facts_for_command(
    command: Sequence[str],
    directory: str | Path,
    *,
    inputs_dir: str | Path,
    extractor: str = "auto",
    public_header_roots: Sequence[str] = (),
    library: str = "",
    version: str = "",
) -> SourceAbiTu | None:
    """Extract the TU's source ABI and append it to the pack; return the dump.

    Extracts **every** source TU in the command (``gcc -c a.c b.c`` → both), so
    a multi-source compile contributes all its objects' facts. A backend that
    raises on one TU is logged and skipped, leaving the other TUs' facts intact.
    Returns the first extracted dump (or ``None`` when there is no source TU or no
    usable backend); the return value is informational — the caller only cares
    that the pack grew.
    """
    units = compile_units_from_command(command, directory)
    if not units:
        return None
    from .buildsource.source_extractors.resolver import select_source_backend

    _choice, impl = select_source_backend(extractor)
    if impl is None:
        return None
    target_id = f"target://{library}" if library else ""
    init_inputs_pack(inputs_dir, library=library, version=version, created_by="abicheck-cc")
    first: SourceAbiTu | None = None
    for cu in units:
        # Per-TU isolation: one source the backend cannot parse must not drop the
        # other objects of the same multi-source compile (Codex review).
        try:
            tu = impl.extract(cu, public_header_roots=list(public_header_roots), target_id=target_id)
        except Exception as exc:
            click.echo(f"abicheck-cc: skipped facts for {cu.source}: {exc}", err=True)
            continue
        append_source_facts(
            inputs_dir, [tu], filename=facts_filename(cu.source, library=library)
        )
        if first is None:
            first = tu
    return first


def _split_paths(value: str) -> list[str]:
    return [p for p in value.split(os.pathsep) if p] if value else []


def run_cc_wrapper(
    command: Sequence[str],
    *,
    runner: Callable[[list[str]], subprocess.CompletedProcess[bytes]] | None = None,
    env: dict[str, str] | None = None,
    emit: Callable[..., SourceAbiTu | None] = emit_facts_for_command,
) -> int:
    """Run the real compile, then best-effort emit source facts; return its exit code.

    *runner* and *emit* are injectable so the pass-through + best-effort
    semantics are unit-testable without a real compiler. The compiler's exit code
    is always preserved — extraction is skipped on a failed compile and any
    extraction error is downgraded to a warning, never propagated to the caller.
    """
    command = list(command)
    if not command:
        click.echo("abicheck-cc: no compiler command given", err=True)
        return 2
    environ = env if env is not None else dict(os.environ)
    run = runner if runner is not None else _default_runner
    rc = run(command).returncode

    if rc != 0 or environ.get("ABICHECK_CC_DISABLE"):
        return rc
    try:
        emit(
            command,
            Path.cwd(),
            inputs_dir=environ.get("ABICHECK_INPUTS_DIR", "abicheck_inputs"),
            extractor=environ.get("ABICHECK_CC_EXTRACTOR", "auto"),
            public_header_roots=_split_paths(environ.get("ABICHECK_CC_HEADERS", "")),
            library=environ.get("ABICHECK_CC_LIBRARY", ""),
            version=environ.get("ABICHECK_CC_VERSION", ""),
        )
    except Exception as exc:  # never fail the build for a fact-extraction problem
        click.echo(f"abicheck-cc: source-fact extraction skipped: {exc}", err=True)
    return rc


def _default_runner(command: list[str]) -> subprocess.CompletedProcess[bytes]:
    # No shell: the command is an argv list straight from our own argv.
    return subprocess.run(command)


def main(argv: Sequence[str] | None = None) -> int:
    """Console-script entry: ``abicheck-cc <compiler> [args…]``."""
    args = list(sys.argv[1:] if argv is None else argv)
    return run_cc_wrapper(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
