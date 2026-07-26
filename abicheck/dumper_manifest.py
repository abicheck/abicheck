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

"""ADR-050 D3 (G32 Phase B) — the per-TU header-AST invocation loop and the
minimal placeholder merge across translation units.

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

**Merge scope, this phase only**: :func:`merge_tu_fragments` is
*deliberately* the "no conflicts possible" placeholder the ADR's Phase B
scope calls for -- concatenate every TU fragment's entities and raise
:class:`abicheck.errors.SnapshotError` the moment the same ``entity_key``
(see :func:`entity_key`) appears in more than one fragment, regardless of
whether the two declarations would actually be compatible (e.g. a forward
declaration in one TU and its full definition in another). Real
compatible-merge handling -- union provenance, keep the richer declaration
when the two sides are ODR-compatible -- is ADR-050 D4 (G32 Phase C's
``tu_merge.py``/``TuMergeError``), not this module; this phase ships
deliberately strict so an accepted-but-wrong merge never silently produces a
snapshot with entities dropped or overwritten.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
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
from .errors import SnapshotError
from .model import EnumType, Function, RecordType, Variable

if TYPE_CHECKING:
    from .dumper_castxml import _CastxmlParser

log = logging.getLogger(__name__)

#: Signature of ``dumper._header_ast_parser`` -- injected by the caller
#: rather than imported, see this module's own docstring.
HeaderAstParserFn = Callable[..., "_CastxmlParser | _ClangAstParser"]


@dataclass(frozen=True)
class TuFragment:
    """One translation unit's own header-AST parse, normalized to plain
    model entities (not raw AST) -- ADR-050 D3's "each producing a
    normalized ``TuFragment``".

    ``ast_producer``/``ast_toolchain``/``ast_fallback_reason``/
    ``ast_toolchain_supported``/``ast_toolchain_unsupported_reasons`` mirror
    the same per-parser provenance fields ``dumper._dump_elf``/``_dump_pe``/
    ``_dump_macho`` already stamp onto a single-TU ``AbiSnapshot`` -- kept
    per-fragment here (not just on the merged result) since a future
    heterogeneous-toolchain diagnostic (D4's ``HETEROGENEOUS_ABI_CONTEXT``)
    needs each TU's own value to compare, even though D3's own parse-time
    rule already forces one compiler/target per manifest today.
    """

    tu_name: str
    functions: tuple[Function, ...] = ()
    variables: tuple[Variable, ...] = ()
    types: tuple[RecordType, ...] = ()
    enums: tuple[EnumType, ...] = ()
    typedefs: dict[str, str] = field(default_factory=dict)
    constants: dict[str, str] = field(default_factory=dict)
    ast_producer: str = "castxml"
    ast_toolchain: dict[str, str] = field(default_factory=dict)
    ast_fallback_reason: str | None = None
    ast_toolchain_supported: bool | None = None
    ast_toolchain_unsupported_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class MergedTuFragments:
    """The placeholder-merge result across every contributing TU's
    :class:`TuFragment` -- concatenated entity lists/dicts, plus one
    representative fragment's AST provenance (see :func:`merge_tu_fragments`
    for why using any single contributing fragment's provenance is correct,
    not just convenient, under D3's own single-compiler-per-manifest rule).
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


def entity_key(kind: str, name: str) -> tuple[str, str]:
    """The cross-TU identity a duplicate is detected against.

    Deliberately just ``(kind, name)`` -- for a :class:`Function`/
    :class:`Variable`, *name* is its mangled linker symbol (already
    excludes return type for every C++ mangling scheme this repo targets,
    and equals the plain name for C, which has no mangling); for a
    :class:`RecordType`/:class:`EnumType`/a typedef/a constant, *name* is
    the model's own (already possibly namespace-qualified) ``name``/dict
    key. ADR-050 D4's own text is explicit that ``entity_key`` "deliberately
    excludes return type" for exactly this reason -- folding it in would
    turn a same-TU return-type edit into an unrelated add+remove pair
    instead of one detected change, so this helper never looks at
    ``return_type``/``type`` at all.
    """
    return (kind, name)


def _fragment_entity_keys(fragment: TuFragment) -> list[tuple[str, str]]:
    keys: list[tuple[str, str]] = []
    keys.extend(entity_key("function", fn.mangled) for fn in fragment.functions)
    keys.extend(entity_key("variable", var.mangled) for var in fragment.variables)
    keys.extend(entity_key("type", rt.name) for rt in fragment.types)
    keys.extend(entity_key("enum", en.name) for en in fragment.enums)
    keys.extend(entity_key("typedef", name) for name in fragment.typedefs)
    keys.extend(entity_key("constant", name) for name in fragment.constants)
    return keys


def merge_tu_fragments(fragments: Sequence[TuFragment]) -> MergedTuFragments:
    """Concatenate *fragments* into one :class:`MergedTuFragments`, raising
    :class:`abicheck.errors.SnapshotError` the moment the same
    :func:`entity_key` is produced by more than one fragment.

    This is ADR-050 D3's own explicitly-scoped placeholder ("Phase B ships
    with a minimal 'no conflicts possible' merge... as a placeholder,
    replaced by Phase C's real compatible-merge lattice") -- it does not
    attempt to tell an ODR-safe pair (forward declaration + full definition
    across two TUs) apart from a genuine conflict; *any* repeat is an error
    here, on purpose, until D4's ``tu_merge.py`` lands.
    """
    functions: list[Function] = []
    variables: list[Variable] = []
    types: list[RecordType] = []
    enums: list[EnumType] = []
    typedefs: dict[str, str] = {}
    constants: dict[str, str] = {}
    seen: dict[tuple[str, str], str] = {}

    for fragment in fragments:
        # Checked against `seen` from *earlier* fragments only -- a single
        # TU's own parser output can legitimately repeat a key (e.g. two
        # destructors both falling back to castxml's synthesized no-mangled-
        # name marker within the same TU, already tolerated by today's
        # flat single-TU dump); only a repeat *introduced by a later TU*
        # is this placeholder's concern.
        fragment_keys = _fragment_entity_keys(fragment)
        for key in fragment_keys:
            if key in seen:
                kind, name = key
                raise SnapshotError(
                    f"translation unit {fragment.tu_name!r} redeclares {kind} "
                    f"{name!r}, already produced by translation unit "
                    f"{seen[key]!r} -- ADR-050 Phase B does not yet merge "
                    "compatible cross-TU redeclarations (forward declaration "
                    "+ definition, etc.); that lands in Phase C's tu_merge.py "
                    "(ADR-050 D4). Until then, the same declaration may only "
                    "be reachable from one contributing translation unit."
                )
        for key in fragment_keys:
            seen[key] = fragment.tu_name
        functions.extend(fragment.functions)
        variables.extend(fragment.variables)
        types.extend(fragment.types)
        enums.extend(fragment.enums)
        typedefs.update(fragment.typedefs)
        constants.update(fragment.constants)

    if not fragments:
        return MergedTuFragments(
            functions=(),
            variables=(),
            types=(),
            enums=(),
            typedefs={},
            constants={},
            ast_producer="castxml",
            ast_toolchain={},
            ast_fallback_reason=None,
            ast_toolchain_supported=None,
            ast_toolchain_unsupported_reasons=(),
        )

    # Any contributing fragment's AST provenance is representative: ADR-050
    # D3 rejects a manifest declaring different compilers/target triples
    # across TUs at parse time (dump_manifest.py -- compiler/target are
    # base-profile-only fields), so every fragment here was produced by the
    # same toolchain by construction.
    representative = fragments[0]
    return MergedTuFragments(
        functions=tuple(functions),
        variables=tuple(variables),
        types=tuple(types),
        enums=tuple(enums),
        typedefs=typedefs,
        constants=constants,
        ast_producer=representative.ast_producer,
        ast_toolchain=representative.ast_toolchain,
        ast_fallback_reason=representative.ast_fallback_reason,
        ast_toolchain_supported=representative.ast_toolchain_supported,
        ast_toolchain_unsupported_reasons=representative.ast_toolchain_unsupported_reasons,
    )


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
    """
    resolved_public_paths = [str(r) for r in roots] + [
        str(p) for p in public_header_paths
    ]
    resolved_public_dirs = [str(d) for d in public_header_dirs]

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

    return merge_tu_fragments(fragments)


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
