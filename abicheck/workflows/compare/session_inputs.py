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

"""Steps 2-4 of the single-pair ``compare`` session: compile context,
operands and resolution (``workflows/compare/session.py`` runs them in
order). Each refusal is a
:class:`~abicheck.workflows.run_input_errors.RunInputError` worded as
``compare`` has always worded it; each note goes to the session's *notify*.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ...errors import PlanningError, SnapshotError, ValidationError
from ..run_input_errors import RunInputError

if TYPE_CHECKING:
    from ...dry_run_estimate import CompileContext
    from ..compare_classify import ResolvedComparePair
    from ..service_requests import CompareRequest
    from .session_request import PairSessionRequest

__all__ = [
    "normalize_operand",
    "note_debug_resolution",
    "operand_rule_refusals",
    "pair_request",
    "resolve_compile_context",
    "resolve_pair",
    "warn_ignored_development_packages",
    "warn_ignored_header_inputs",
]

Notify = Callable[[str], None]


def resolve_compile_context(
    request: PairSessionRequest, notify: Notify
) -> tuple[CompileContext, tuple[Path, ...]]:
    """The run's both-sides L2 compile context, ``compile:`` folded in (ADR-037 D3).

    The same fold ``dump`` and the ``--dry-run`` receipt run, with
    explicitness read from the options the invocation stated (ADR-074's
    defines fold by macro name). ``cfg.path`` is the selected config --
    possibly auto-discovered -- so ``config_explicit`` keeps an
    auto-discovered ``compile.compiler`` behind the trust gate (PR #1154).
    """
    from ...dry_run_estimate import CompileContext
    from ..compile_config import CompileConfigError, merge_compile_config

    c = request.compile
    try:
        return merge_compile_config(
            CompileContext(
                gcc_path=c.compiler_path,
                gcc_prefix=c.compiler_prefix,
                gcc_options=c.gcc_options,
                gcc_option_tokens=tuple(c.compiler_option_tokens),
                defines=tuple(c.defines),
                sysroot=c.sysroot,
                nostdinc=c.nostdinc,
                frontend=c.header_backend,
                frontend_context=c.frontend_context,
            ),
            tuple(request.headers.includes),
            request.project.path,
            frontend_explicit=False,
            nostdinc_explicit="nostdinc" in request.stated_options,
            frontend_context_explicit="frontend_context" in request.stated_options,
            config_explicit=request.project.config_explicit,
            notify=notify,
        )
    except CompileConfigError as exc:
        raise RunInputError.usage(str(exc)) from exc


@contextmanager
def operand_rule_refusals() -> Iterator[None]:
    """Word a refused operand combination in ``compare``'s own flag language.

    The rules are ``workflows.operand_rules``'; ``debug.format`` names the
    ``.abicheck.yml`` key the value came from.
    """
    from ..operand_rules import DebugFormatNotElfError, ManifestOperandError

    try:
        yield
    except DebugFormatNotElfError as exc:
        raise RunInputError.parameter(
            f"debug.format: {exc.debug_format} (.abicheck.yml) is only "
            f"supported for ELF binaries, but the {exc.side} input is "
            f"{exc.binary_fmt.upper()}."
        ) from exc
    except ManifestOperandError as exc:
        raise RunInputError.usage(str(exc)) from exc


def normalize_operand(path: Path, notify: Notify) -> tuple[Path, str | None]:
    """*path*'s binary format, following a resolvable GNU ld linker script.

    When *path* is a script naming a real shared library, that library and
    *its* format are returned, with a one-line note.
    """
    from ..extraction import detect_binary_format, resolve_linker_script

    fmt = detect_binary_format(path)
    if fmt is not None:
        return path, fmt
    target, is_ld = resolve_linker_script(path)
    if is_ld and target is not None and target.resolve() != path.resolve():
        notify(
            f"Note: '{path}' is a GNU ld linker script; following its "
            f"INPUT()/GROUP() directive to '{target}'."
        )
        return target, detect_binary_format(target)
    return path, fmt


def warn_ignored_header_inputs(
    request: PairSessionRequest,
    old_is_binary: bool,
    new_is_binary: bool,
    headers: tuple[Path, ...],
    old_headers: tuple[Path, ...],
    new_headers: tuple[Path, ...],
    notify: Notify,
) -> None:
    """Name the extraction-only inputs a snapshot-vs-snapshot pair ignores."""
    if old_is_binary or new_is_binary:
        return
    hdr = request.headers
    flag_pairs: list[tuple[tuple[Path, ...], str]] = [
        (headers, "-H/--header"),
        (old_headers, "--header old="),
        (new_headers, "--header new="),
        (hdr.includes, "-I/--include"),
        (hdr.old_includes, "--include old="),
        (hdr.new_includes, "--include new="),
    ]
    ignored = [label for value, label in flag_pairs if value]
    if ignored:
        notify(f"Warning: {', '.join(ignored)} ignored when both inputs are snapshots.")


def warn_ignored_development_packages(
    old_package: Path | None, new_package: Path | None, notify: Notify
) -> None:
    """Name each development package a single-pair compare cannot read.

    A ``-H/--header`` operand that is a development package is consumed only
    by the release fan-out, so it would otherwise drop out with nothing but
    a "no headers provided" note. One warning per distinct package, naming
    the side(s) it was given for.
    """
    sides_by_package: dict[Path, list[str]] = {}
    for side, package in (("old", old_package), ("new", new_package)):
        if package is not None:
            sides_by_package.setdefault(package, []).append(side)
    for package, sides in sides_by_package.items():
        scope = "both sides" if len(sides) == 2 else f"{sides[0]} side"
        notify(
            f"Warning: development package '{package}' ignored for the {scope}: a "
            "single-pair compare does not read headers from a development "
            "package. Extract it and pass its include directory with -H, or "
            "compare the release directories."
        )


def note_debug_resolution(
    old_input: Path, new_input: Path, request: PairSessionRequest, notify: Notify
) -> None:
    """Name each side's resolved detached debug artifact (debug roots / debuginfod)."""
    from ..extraction import detect_binary_format, resolve_debug_info

    ev = request.evidence
    debuginfod, url = ev.debug.debuginfod, ev.debug.debuginfod_url
    if not (ev.old_debug_roots or ev.new_debug_roots or debuginfod):
        return
    for label, binary, roots in (
        ("old", old_input, ev.old_debug_roots),
        ("new", new_input, ev.new_debug_roots),
    ):
        if detect_binary_format(binary) is None or not (roots or debuginfod):
            continue
        artifact = resolve_debug_info(
            binary,
            debug_roots=list(roots) or None,
            enable_debuginfod=debuginfod,
            debuginfod_urls=[url] if url else None,
        )
        if artifact:
            notify(f"Debug info ({label}): {artifact.source}")


def pair_request(
    request: PairSessionRequest,
    old_input: Path,
    new_input: Path,
    old_h: list[Path],
    new_h: list[Path],
    old_inc: list[Path],
    new_inc: list[Path],
    *,
    header_backend: str,
    side_compile_context: CompileContext | None,
    debug_format: str | None,
    changed_paths: tuple[str, ...],
) -> CompareRequest:
    """The pair's :class:`CompareRequest`, as the typed API would state it.

    ``include_dependencies`` filters both sides the way ``dump`` does by
    default, so ``dump old.so -o base.json`` then ``compare base.json new.so``
    compare consistently. A per-side header backend rides on that side's own
    ``CompileContext.frontend``, which outranks the both-sides
    *header_backend* (the shared context's frontend is already neutralised).
    A ``--dump-manifest`` side declares its own ``public_header_dirs``, so
    ``scope.public_header_dirs`` reaches only a manifest-less side.
    """
    from ..ownership_request import project_ownership_request
    from ..public_header_boundary import project_config_public_header_dirs
    from ..request_inputs import InputSpec
    from ..service_requests import CompareRequest as _CompareRequest

    hdr, ev, c, cfg = (
        request.headers,
        request.evidence,
        request.compile,
        request.project,
    )

    def _side_compile(backend: str | None) -> CompileContext | None:
        if backend is None:
            return side_compile_context
        from ...compile_context import CompileContext as _CompileContext

        base = side_compile_context or _CompileContext()
        return dataclasses.replace(base, frontend=backend)

    config_header_dirs = tuple(project_config_public_header_dirs(cfg.document) or ())
    ownership = project_ownership_request(cfg.document, cfg.path)
    pdb = ev.debug.pdb_path

    def _spec(
        path: Path,
        headers: list[Path],
        includes: list[Path],
        version: str,
        debug_roots: tuple[Path, ...],
        manifest: Any,
        backend: str | None,
    ) -> InputSpec:
        return InputSpec(
            path=path,
            headers=tuple(headers),
            exclude_headers=tuple(hdr.exclude_headers),
            includes=tuple(includes),
            version=version,
            pdb=pdb,
            debug_roots=tuple(debug_roots),
            include_dependencies=ev.include_dependencies,
            dump_manifest=manifest,
            compile=_side_compile(backend),
            public_header_dirs=() if manifest is not None else config_header_dirs,
            ownership=ownership,
        )

    return _CompareRequest(
        old=_spec(
            old_input,
            old_h,
            old_inc,
            request.operands.old_version,
            ev.old_debug_roots,
            hdr.old_dump_manifest,
            c.old_header_backend,
        ),
        new=_spec(
            new_input,
            new_h,
            new_inc,
            request.operands.new_version,
            ev.new_debug_roots,
            hdr.new_dump_manifest,
            c.new_header_backend,
        ),
        lang=c.lang,
        lang_explicit=c.lang_explicit,
        frontend=header_backend,
        dwarf_only=ev.debug.dwarf_only,
        debug_format=debug_format,
        include_labels=tuple((hdr.include_labels or {}).items()),
        follow_dependencies=ev.follow_deps,
        dependency_search_paths=tuple(ev.search_paths),
        ld_library_path=ev.ld_library_path,
        enable_debuginfod=ev.debug.debuginfod,
        debuginfod_url=ev.debug.debuginfod_url,
        changed_paths=changed_paths,
    )


def resolve_pair(
    request: CompareRequest,
    prepare: Callable[[CompareRequest], CompareRequest],
    notify: Notify,
) -> tuple[CompareRequest, ResolvedComparePair]:
    """*request* as *prepare* states it, and both of its sides resolved.

    The same resolution the typed Python API and the MCP ``abi_compare``
    tool run (ADR-055 D1). ``allow_parallel=False``: no two *threads*; two
    live binaries still resolve in two concurrent child processes (the
    ``balanced`` profile), so their progress notes may interleave.
    """
    from .resolve import resolve_compare_request

    try:
        request = prepare(request)
        pair = resolve_compare_request(request, notify=notify, allow_parallel=False)
    except (ValidationError, PlanningError) as exc:  # PlanningError: ADR-063 Phase 4
        raise RunInputError.usage(str(exc)) from exc
    except SnapshotError as exc:
        raise RunInputError.operational(str(exc)) from exc
    return request, pair
