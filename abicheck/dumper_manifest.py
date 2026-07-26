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

"""ADR-050 D3/D4 (G32 Phase B/C) — the per-TU header-AST invocation loop and
the real compatible merge across translation units.

Lives in a sibling module, not inline in ``dumper.py``, because ``dumper.py``
has essentially no headroom left before the AI-readiness file-size gate's
2000-line hard cap (see ``AGENTS.md``'s "Files that are large" and this
phase's own G32 plan entry) — the same split precedent ``dumper_castxml.py``/
``dumper_clang.py`` already set for per-backend logic.

**Dependency direction**: this module never imports from ``dumper.py`` at
module level, even though its whole purpose is to be called *from*
``dumper.py``'s manifest-driven ``dump()`` path (wired in a later G32 task,
not this one). Importing ``dumper._header_ast_parser`` directly here would
create ``dumper -> dumper_manifest -> dumper``, an import cycle. Instead,
the caller (i.e. ``dumper.py``, once wired) injects
``dumper._header_ast_parser`` as the ``header_ast_parser`` callable
parameter below -- the exact same "pass the function in rather than import
it" technique ``dumper_hybrid.run_hybrid_dump(dump_fn, ...)`` already uses
for the identical reason (see that module's own docstring).

**Merge scope**: ``TuFragment``/``MergedTuFragments``/``entity_key`` live in
the leaf module :mod:`abicheck.tu_fragment` (not here) so that both this
module and :mod:`abicheck.tu_merge` -- which implements the real
compatible-merge lattice (ADR-050 D4: union provenance, keep the richer
declaration when two TUs' declarations are ODR-compatible; reject with
:class:`abicheck.errors.TuMergeError` otherwise) -- can depend on those
shapes without ``dumper_manifest -> tu_merge -> dumper_manifest`` forming an
import cycle. ``merge_tu_fragments`` below is :func:`abicheck.tu_merge.merge_fragments`
re-exported under its original Phase B name, kept as a stable public alias
of the same callable now that its placeholder ("any repeat is an error")
implementation has been replaced by the real one.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .dump_manifest import DumpManifest, IncludeEntry, TranslationUnit
from .dumper_clang import _ClangAstParser
from .dumper_toolchain import (
    _parser_ast_fallback_reason,
    _parser_ast_supported,
    _parser_ast_toolchain,
    _parser_ast_unsupported_reasons,
)
from .model import EnumType, Function, RecordType, Variable
from .tu_fragment import (
    MergedTuFragments as MergedTuFragments,
    TuFragment as TuFragment,
    entity_key as entity_key,
)
from .tu_merge import merge_fragments as merge_tu_fragments

if TYPE_CHECKING:
    from .dumper_castxml import _CastxmlParser

log = logging.getLogger(__name__)

#: Signature of ``dumper._header_ast_parser`` -- injected by the caller
#: rather than imported, see this module's own docstring.
HeaderAstParserFn = Callable[..., "_CastxmlParser | _ClangAstParser"]

# Re-exported for backward compatibility: TuFragment/MergedTuFragments/
# entity_key used to be defined here (G32 Phase B) and moved to the leaf
# module abicheck.tu_fragment (G32 Phase C) so both this module and
# abicheck.tu_merge could depend on them without an import cycle -- see this
# module's own docstring. merge_tu_fragments is now a direct alias of
# abicheck.tu_merge.merge_fragments, the real compatible-merge
# implementation (ADR-050 D4), under its original Phase B name.


def run_tu_fragment(
    tu: TranslationUnit,
    *,
    header_ast_parser: HeaderAstParserFn,
    backend: str,
    compiler: str,
    gcc_path: str | None,
    gcc_prefix: str | None,
    gcc_options: str | None,
    gcc_option_tokens: tuple[str, ...],
    sysroot: Path | None,
    nostdinc: bool,
    lang: str | None,
    exported_dynamic: set[str],
    exported_static: set[str],
    public_header_paths: list[str],
    public_dir_paths: list[str],
    extra_hash_dirs: tuple[Path, ...] = (),
) -> TuFragment:
    """Run one castxml/clang invocation for *tu* via *header_ast_parser*
    (``dumper._header_ast_parser``, injected -- see this module's own
    docstring) and normalize its output into a :class:`TuFragment`.

    *tu*'s own ``forced_includes`` become the parser's ``headers`` and its
    own ``includes`` (just the resolved paths -- ``project_owned`` only
    matters for :func:`abicheck.comparability.compute_extraction_contract`'s
    ownership classification, not for the parse itself) become
    ``extra_includes``. *public_header_paths*/*public_dir_paths* are the
    manifest's own base-profile provenance inputs (``roots`` +
    ``public_header_paths``/``public_header_dirs``), passed identically to
    every TU's call -- not derived per-TU from *tu*'s own
    ``forced_includes`` -- since a TU may force-include a private support
    header alongside a public one (:mod:`abicheck.dump_manifest`'s own
    docstring), so only the manifest's declared ``roots`` are the actual
    public surface, regardless of which TU happens to force-include them.
    """
    parser = header_ast_parser(
        list(tu.forced_includes),
        [entry.path for entry in tu.includes],
        backend=backend,
        compiler=compiler,
        gcc_path=gcc_path,
        gcc_prefix=gcc_prefix,
        gcc_options=gcc_options,
        gcc_option_tokens=gcc_option_tokens,
        sysroot=sysroot,
        nostdinc=nostdinc,
        lang=lang,
        exported_dynamic=exported_dynamic,
        exported_static=exported_static,
        public_header_paths=public_header_paths,
        public_dir_paths=public_dir_paths,
        extra_hash_dirs=extra_hash_dirs,
    )
    return TuFragment(
        tu_name=tu.name,
        functions=tuple(parser.parse_functions()),
        variables=tuple(parser.parse_variables()),
        types=tuple(parser.parse_types()),
        enums=tuple(parser.parse_enums()),
        typedefs=parser.parse_typedefs(),
        constants=parser.parse_constants(),
        ast_producer="clang" if isinstance(parser, _ClangAstParser) else "castxml",
        ast_toolchain=_parser_ast_toolchain(parser),
        ast_fallback_reason=_parser_ast_fallback_reason(parser),
        ast_toolchain_supported=_parser_ast_supported(parser),
        ast_toolchain_unsupported_reasons=tuple(
            _parser_ast_unsupported_reasons(parser)
        ),
    )


def run_tu_loop(
    tus: Sequence[TranslationUnit],
    *,
    header_ast_parser: HeaderAstParserFn,
    roots: Sequence[Path],
    public_header_paths: Sequence[Path] = (),
    public_header_dirs: Sequence[Path] = (),
    backend: str,
    compiler: str,
    gcc_path: str | None = None,
    gcc_prefix: str | None = None,
    gcc_options: str | None = None,
    gcc_option_tokens: tuple[str, ...] = (),
    sysroot: Path | None = None,
    nostdinc: bool = False,
    lang: str | None = None,
    exported_dynamic: set[str],
    exported_static: set[str],
    extra_hash_dirs: tuple[Path, ...] = (),
) -> MergedTuFragments:
    """Run every TU in *tus* (one castxml/clang invocation each) and merge
    the results via :func:`merge_tu_fragments` -- ADR-050 D3's "one
    castxml/clang invocation per TU... instead of today's single
    aggregate-then-parse call".

    A TU with ``required=False`` whose invocation raises degrades to a
    logged diagnostic instead of failing the whole loop; a ``required=True``
    TU's failure (the default) propagates and fails the whole manifest dump,
    matching D3's "A required TU's compile failure is a hard extraction
    failure for the whole snapshot... an optional TU's failure degrades to a
    diagnostic". Parse-time already guarantees ``contributes_to_abi=True``
    implies ``required=True`` (:mod:`abicheck.dump_manifest`), so an optional
    TU's silently-skipped failure can never drop an entity the merged
    snapshot claims to speak for.

    **Two deliberately different "public" sets are threaded through this
    function** (Codex review, PR #635 round 14) -- conflating them was a
    real bug: *resolved_public_paths*/*resolved_public_dirs* (``roots`` +
    *public_header_paths*/*public_header_dirs*) feed each TU's own
    :func:`run_tu_fragment` call, where they scope constant extraction the
    same roots-are-always-public way the legacy CLI's ``-H``/``--header``
    already does (see :class:`abicheck.dumper_castxml._CastxmlParser`'s own
    docstring) -- but :func:`merge_tu_fragments` gets only the manifest's
    *explicit* ``public_header_paths``/``public_header_dirs`` (``roots``
    excluded), because that is what the *later*, authoritative
    ``apply_provenance`` call in ``dumper.py`` classifies origin against
    too (:mod:`abicheck.dump_manifest`'s own docstring: ``roots`` is a
    *scope* declaration, D1's ``scope_fingerprint`` input, not an ADR-015
    provenance input -- "a manifest with public headers but no separate
    provenance fields behaves exactly like a legacy dump -H foo.h
    invocation with no --public-header", i.e. every declaration stays
    ``UNKNOWN``). Passing the roots-augmented set into the merge step
    instead would let :func:`abicheck.tu_merge._more_public_of` treat a
    root-only declaration as public during the merge, keep *that* TU's
    ``source_location`` as the merged entity's representative, and then
    have the later ``apply_provenance`` call -- which never considered
    ``roots`` public to begin with -- classify the very same declaration
    as private/unknown, hiding a real public API change even though the
    identical declaration also reached this manifest through a genuinely
    public header in another TU.
    """
    resolved_public_paths = [str(r) for r in roots] + [
        str(p) for p in public_header_paths
    ]
    resolved_public_dirs = [str(d) for d in public_header_dirs]
    explicit_public_paths = [str(p) for p in public_header_paths]
    explicit_public_dirs = [str(d) for d in public_header_dirs]

    fragments: list[TuFragment] = []
    for tu in tus:
        try:
            fragments.append(
                run_tu_fragment(
                    tu,
                    header_ast_parser=header_ast_parser,
                    backend=backend,
                    compiler=compiler,
                    gcc_path=gcc_path,
                    gcc_prefix=gcc_prefix,
                    gcc_options=gcc_options,
                    gcc_option_tokens=gcc_option_tokens,
                    sysroot=sysroot,
                    nostdinc=nostdinc,
                    lang=lang,
                    exported_dynamic=exported_dynamic,
                    exported_static=exported_static,
                    public_header_paths=resolved_public_paths,
                    public_dir_paths=resolved_public_dirs,
                    extra_hash_dirs=extra_hash_dirs,
                )
            )
        except Exception:
            if tu.required:
                raise
            log.warning(
                "Optional translation unit %r failed to extract; skipping "
                "(required=false). Its declarations are absent from this "
                "snapshot.",
                tu.name,
                exc_info=True,
            )

    return merge_tu_fragments(
        fragments,
        public_header_paths=explicit_public_paths,
        public_header_dirs=explicit_public_dirs,
    )


@dataclass(frozen=True)
class ElfHeaderAstResult:
    """The single result shape :func:`resolve_header_ast_result` returns for
    both the legacy single-header path and a real manifest -- everything a
    format handler's snapshot-assembly step needs, so it never has to know
    which of the two actually ran.
    """

    functions: tuple[Function, ...]
    variables: tuple[Variable, ...]
    types: tuple[RecordType, ...]
    enums: tuple[EnumType, ...]
    typedefs: dict[str, str]
    constants: dict[str, str]
    ast_producer: str
    ast_toolchain: dict[str, str]
    ast_fallback_reason: str | None
    ast_toolchain_supported: bool | None
    ast_toolchain_unsupported_reasons: tuple[str, ...]
    is_clang: bool
    provenance_headers: tuple[Path, ...]


def resolve_header_ast_result(
    *,
    dump_manifest: DumpManifest | None,
    headers: list[Path],
    extra_includes: list[Path],
    header_ast_parser: HeaderAstParserFn,
    backend: str,
    compiler: str,
    gcc_path: str | None,
    gcc_prefix: str | None,
    gcc_options: str | None,
    gcc_option_tokens: tuple[str, ...],
    sysroot: Path | None,
    nostdinc: bool,
    lang: str | None,
    exported_dynamic: set[str],
    exported_static: set[str],
    public_headers: list[Path] | None,
    public_header_dirs: list[Path] | None,
    extra_hash_dirs: tuple[Path, ...] = (),
) -> ElfHeaderAstResult:
    """Run the header-AST parse for one dump -- manifest-driven (one
    invocation per TU, merged) when *dump_manifest* is given, otherwise the
    legacy single-TU path -- and return one common result shape either way.

    The legacy branch is a real, one-TU call into :func:`run_tu_fragment`
    (a synthetic ``legacy-main`` :class:`~abicheck.dump_manifest.TranslationUnit`
    built from *headers*/*extra_includes*), not a second, parallel
    implementation of the parse-and-normalize step -- ADR-050 D3's "the
    existing single-TU code path becomes the manifest path's one-TU special
    case". Its *public_header_paths* input is *headers* itself plus
    *public_headers* (matching the legacy CLI's own "the headers you pass
    are always public" rule) since there is no manifest ``roots`` field to
    read it from instead.

    This function -- not the per-format ``dumper.py`` builders -- is where
    the legacy-vs-manifest branch lives, so each builder (``_dump_elf``
    today; ``_dump_pe``/``_dump_macho`` once they support manifests) needs
    only one call site instead of duplicating the branch inline (``dumper.py``
    has no line-count headroom for that -- see this module's own docstring).
    """
    if dump_manifest is not None:
        merged = run_tu_loop(
            dump_manifest.translation_units,
            header_ast_parser=header_ast_parser,
            roots=dump_manifest.roots,
            public_header_paths=dump_manifest.public_header_paths,
            public_header_dirs=dump_manifest.public_header_dirs,
            backend=backend,
            compiler=compiler,
            gcc_path=gcc_path,
            gcc_prefix=gcc_prefix,
            gcc_options=gcc_options,
            gcc_option_tokens=gcc_option_tokens,
            sysroot=sysroot,
            nostdinc=nostdinc,
            lang=lang,
            exported_dynamic=exported_dynamic,
            exported_static=exported_static,
            extra_hash_dirs=extra_hash_dirs,
        )
        provenance_headers = tuple(dump_manifest.roots)
    else:
        legacy_tu = TranslationUnit(
            name="legacy-main",
            forced_includes=tuple(headers),
            includes=tuple(IncludeEntry(path=p) for p in extra_includes),
        )
        fragment = run_tu_fragment(
            legacy_tu,
            header_ast_parser=header_ast_parser,
            backend=backend,
            compiler=compiler,
            gcc_path=gcc_path,
            gcc_prefix=gcc_prefix,
            gcc_options=gcc_options,
            gcc_option_tokens=gcc_option_tokens,
            sysroot=sysroot,
            nostdinc=nostdinc,
            lang=lang,
            exported_dynamic=exported_dynamic,
            exported_static=exported_static,
            public_header_paths=[str(h) for h in headers]
            + [str(h) for h in (public_headers or [])],
            public_dir_paths=[str(d) for d in (public_header_dirs or [])],
            extra_hash_dirs=extra_hash_dirs,
        )
        merged = MergedTuFragments(
            functions=fragment.functions,
            variables=fragment.variables,
            types=fragment.types,
            enums=fragment.enums,
            typedefs=fragment.typedefs,
            constants=fragment.constants,
            ast_producer=fragment.ast_producer,
            ast_toolchain=fragment.ast_toolchain,
            ast_fallback_reason=fragment.ast_fallback_reason,
            ast_toolchain_supported=fragment.ast_toolchain_supported,
            ast_toolchain_unsupported_reasons=fragment.ast_toolchain_unsupported_reasons,
        )
        provenance_headers = tuple(headers)

    return ElfHeaderAstResult(
        functions=merged.functions,
        variables=merged.variables,
        types=merged.types,
        enums=merged.enums,
        typedefs=merged.typedefs,
        constants=merged.constants,
        ast_producer=merged.ast_producer,
        ast_toolchain=merged.ast_toolchain,
        ast_fallback_reason=merged.ast_fallback_reason,
        ast_toolchain_supported=merged.ast_toolchain_supported,
        ast_toolchain_unsupported_reasons=merged.ast_toolchain_unsupported_reasons,
        is_clang=merged.ast_producer == "clang",
        provenance_headers=provenance_headers,
    )
