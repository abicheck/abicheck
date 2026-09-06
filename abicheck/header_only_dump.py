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

"""``build_header_only_snapshot`` -- the real header-AST execution for a
binary-less dump (workstream F S1, "Header-only comparison",
``docs/contribute/plans/vision-api-abi-evolution.md``).

Runs the exact same L2 parse-and-normalize machinery a binary dump's own
header-AST pass uses (:func:`abicheck.dumper_manifest.
resolve_header_ast_result`, the identical function :func:`abicheck.dumper.
_dump_elf` calls), with an empty observed-export set on both sides -- there
is no binary here to have exported anything from -- and projects the result
into a plain :class:`~abicheck.model.AbiSnapshot` with no ELF/PE/Mach-O
metadata at all.

**Why this is its own flat module, not a function in
``workflows/artifact/execute_header_only.py``**: ``dumper.py``/
``dumper_manifest.py`` are both classified `unclassified` in
``architecture/modules.yaml`` (no layer legacy_paths entry -- ``dumper.py``
has its own inbound/outbound edges that open a real cross-layer cycle if
reclassified into any layer, confirmed by trying it during this
workstream), and a *migrated* ADR-061 package file (anything under
``abicheck/workflows/``) importing an unclassified module is a hard
``unclassified-import`` architecture-gate error, while a flat module is
exempt from that check regardless of classification
(``scripts/check_architecture.py``'s ``migrated_source`` gate). This module
is named outside the frozen ``dumper_`` root family on purpose (that family
accepts no new siblings) and is registered in ``modules.yaml``'s
``public_root_surfaces`` -- the documented, sanctioned way for a migrated
package to import one specific flat, otherwise-unclassified module -- so
``workflows/artifact/execute_header_only.py`` can reach ``dumper``/
``dumper_manifest`` through this one seam instead of directly.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .compile_context import CompileContext
    from .dump_manifest import DumpManifest
    from .model import AbiSnapshot

__all__ = ["build_header_only_snapshot"]


def build_header_only_snapshot(
    *,
    library_hint: Path | None,
    version: str,
    headers: list[Path],
    extra_includes: list[Path],
    dump_manifest: DumpManifest | None,
    backend: str,
    compile: CompileContext | None,
    lang: str,
    lang_explicit: bool,
    public_headers: list[Path],
    public_header_dirs: list[Path],
    frontend_context: str = "host",
) -> AbiSnapshot:
    """Parse *headers*/*dump_manifest* alone into a binary-less
    :class:`~abicheck.model.AbiSnapshot`.

    *library_hint* names the resulting snapshot (mirrors
    ``dump_source_only``'s own naming rule for its ``sources``/
    ``build_info`` hint) -- typically the first resolved header path.
    *compile* carries the L2 cross-toolchain/AST-frontend override
    (``InputSpec.compile``); ``None`` uses every ``resolve_header_ast_result``
    default. *lang*/*lang_explicit* mirror ``ResolvedDumpRequest``'s own
    pair: *lang* is passed through only when *lang_explicit* is True, the
    same rule :func:`abicheck.workflows.input_resolution.resolve_input`
    documents for its own identically-named parameter.

    The returned snapshot always has ``platform=None`` (no ELF/PE/Mach-O
    metadata), ``from_headers=True``, and ``header_only=True`` -- see
    ``AbiSnapshot.header_only``'s own docstring and
    ``policy.header_only_capabilities`` for what that tier structurally
    cannot carry.
    """
    from .dumper import _ast_compile_provenance, _header_ast_parser
    from .dumper_manifest import resolve_header_ast_result
    from .model import AbiSnapshot

    gcc_path = compile.gcc_path if compile is not None else None
    gcc_prefix = compile.gcc_prefix if compile is not None else None
    gcc_options = compile.gcc_options if compile is not None else None
    gcc_option_tokens = compile.gcc_option_tokens if compile is not None else ()
    sysroot = compile.sysroot if compile is not None else None
    nostdinc = compile.nostdinc if compile is not None else False

    ast_result = resolve_header_ast_result(
        dump_manifest=dump_manifest,
        headers=headers,
        extra_includes=extra_includes,
        header_ast_parser=_header_ast_parser,
        backend=backend,
        compiler="c++",
        gcc_path=gcc_path,
        gcc_prefix=gcc_prefix,
        gcc_options=gcc_options,
        gcc_option_tokens=gcc_option_tokens,
        sysroot=sysroot,
        nostdinc=nostdinc,
        lang=lang if lang_explicit else None,
        # No binary observed anywhere: every mangled spelling this parse
        # produces is the frontend's own guess, never a linker-confirmed
        # export (see policy.header_only_capabilities).
        exported_dynamic=set(),
        exported_static=set(),
        public_headers=public_headers,
        public_header_dirs=public_header_dirs,
        frontend_context=frontend_context,
        # There is no binary here at all -- exported_dynamic/exported_static
        # above are unconditionally empty, so "not found in either set"
        # cannot mean "confirmed not exported" the way it does for an
        # ordinary binary dump. See visibility()'s own docstring.
        no_binary_evidence=True,
    )

    return AbiSnapshot(
        library=library_hint.name if library_hint is not None else "headers",
        version=version,
        functions=list(ast_result.functions),
        variables=list(ast_result.variables),
        types=list(ast_result.types),
        enums=list(ast_result.enums),
        typedefs=ast_result.typedefs,
        typedefs_qualified=ast_result.typedefs_qualified,
        constants=ast_result.constants,
        typedef_entity_ids=ast_result.typedef_entity_ids,
        constant_entity_ids=ast_result.constant_entity_ids,
        semantic_ir=ast_result.semantic_ir,
        from_headers=True,
        header_only=True,
        ast_producer=ast_result.ast_producer,
        ast_toolchain=ast_result.ast_toolchain,
        ast_fallback_reason=ast_result.ast_fallback_reason,
        ast_toolchain_supported=ast_result.ast_toolchain_supported,
        ast_toolchain_unsupported_reasons=list(
            ast_result.ast_toolchain_unsupported_reasons
        ),
        frontend_context_kind=ast_result.frontend_context_kind,
        platform=None,
        **_ast_compile_provenance(
            list(ast_result.provenance_headers),
            gcc_options,
            gcc_option_tokens,
            sysroot,
            ast_toolchain=ast_result.ast_toolchain,
            lang=lang,
        ),
    )
