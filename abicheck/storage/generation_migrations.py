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

"""Single-file snapshot generation stamp, drift notice and spelling migrations
(ADR-062 D2).

A single-file snapshot records the ``(extractor, resolver, source)``
generations of the build that *extracted its facts* under the top-level
``fact_generations`` key. An absent key reads as
:data:`~.versioning.UNSTATED_GENERATION` (a snapshot written before the stamp
existed), never as "current".

On load, the stated generations go through the same owner every package read
uses, :func:`~.versioning.reader_generation_compatibility`, and a drift (or an
unstated extractor generation) becomes a runtime ``load_notice`` that reaches
``DiffResult.coverage_warnings``.

An extractor-generation bump that changed how a type is *spelled* must also
say how to read the older spelling, or every baseline dumped before the bump
diffs a renamed spelling as a real change (field report: castxml's undeduced
``AutoType`` became ``auto`` at generation 6, and a stored ``AutoType&``
return compared against a fresh ``auto&`` read as ``func_return_changed``).
:data:`SPELLING_MIGRATIONS` holds one row per extractor generation: either the
spelling rewrites that generation introduced, or the reason it introduced
none. ``tests/test_snapshot_generation_migration.py`` fails when
``EXTRACTOR_GENERATION`` moves without a row. Each applied rewrite is
reported as a load notice (record before disposing, ADR-067), never applied
silently.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .castxml_array_migration import migrate_castxml_array_counts
from .castxml_base_offset_migration import migrate_castxml_base_offsets
from .castxml_noexcept_migration import migrate_castxml_noexcept
from .clang_vtable_base_migration import migrate_clang_vtable_bases
from .versioning import (
    EXTRACTOR_GENERATION,
    GENERATION_DRIFT_NOTICE_MARKER,
    UNSTATED_GENERATION,
    FactGenerations,
    StorageVersions,
    reader_generation_compatibility,
)

__all__ = [
    "FACT_GENERATIONS_KEY",
    "PLACEHOLDER_REASONS",
    "SPELLING_MIGRATIONS",
    "GenerationSpellingChange",
    "decode_fact_generations",
    "decode_spellings_generation",
    "encode_fact_generations",
    "erased_extents_restored_between",
    "extractor_generation_of",
    "pending_extent_shift",
    "is_placeholder_reason",
    "migrate_and_describe",
    "without_snapshot_generation_notices",
]

#: Top-level snapshot document key holding the producing build's generations.
FACT_GENERATIONS_KEY = "fact_generations"

#: Key inside :data:`FACT_GENERATIONS_KEY` naming the extractor generation
#: whose spellings the document already carries, written when a load migrated
#: an older document and it is saved again. The stamp itself is never
#: re-dated, so without this a re-saved baseline would be migrated twice
#: (harmless for a rename, wrong for a count shift).
SPELLINGS_KEY = "spellings"

#: Prefix of the drift/unstated notice a single-file snapshot's own stamp
#: raises, so a package reader whose manifest owns the generation can drop it.
_SNAPSHOT_GENERATION_NOTICE_PREFIX = "stored snapshot"


def without_snapshot_generation_notices(notices: tuple[str, ...]) -> tuple[str, ...]:
    """*notices* minus the per-document generation notices: a package reader
    states generations once, from its manifest (one package states one), and
    keeps only the spelling-migration notices the document load raised."""
    return tuple(
        n for n in notices if not n.startswith(_SNAPSHOT_GENERATION_NOTICE_PREFIX)
    )


@dataclass(frozen=True)
class GenerationSpellingChange:
    """What one extractor generation changed about stored type spellings.

    *rewrites* maps an old whole-word spelling to the spelling this
    generation writes instead; empty means the generation changed no
    spelling, and *reason* then says why (it must be non-empty either way).

    *extent_shift* is a respelling no load-time rewrite can apply soundly:
    this generation spells every array extent the older extractor wrote as
    ``N`` as ``N + extent_shift`` for some, but not all, arrays (a stored
    snapshot does not say which backend wrote an extent). Comparison demotes
    a finding whose spellings differ only by that shift instead.

    *restores_erased_extents* says this generation spells an array bound the
    older DWARF extractor erased (``char[8]`` where it wrote ``char[]``). A
    stored ``T[]`` does not say what the bound was, so no rewrite is sound;
    comparison demotes a finding whose spellings differ only by that erasure.
    """

    reason: str
    rewrites: tuple[tuple[str, str], ...] = ()
    extent_shift: int = 0
    #: This generation also declares a load-time rewrite of stored *facts*
    #: (not spellings), such as ``castxml_noexcept_migration.py``: a document
    #: from before it re-encodes to different bytes by design.
    migrates_facts: bool = False
    restores_erased_extents: bool = False


#: Every row's reason cites the commit that introduced the generation and what
#: an audit of that commit's extractor diff found. Rows without rewrites were
#: audited by reading the diff over the fingerprinted extractor scope
#: (``generation_fingerprints.json``) and confirmed empirically: the frozen
#: corpus under ``tests/fixtures/generations/``, dumped at each generation's
#: last main commit, spells every entity like a fresh dump once these
#: rewrites are applied (``tests/test_generation_corpus.py``).
#: ``scripts/check_ai_readiness.py``'s ``generation-corpus`` check refuses a
#: row whose reason is empty or a bare "no change" placeholder.
SPELLING_MIGRATIONS: Mapping[int, GenerationSpellingChange] = {
    1: GenerationSpellingChange(
        "85d745b9a (#1538): first stamped generation; the baseline every "
        "later row is measured from, so it has nothing to migrate"
    ),
    2: GenerationSpellingChange(
        "9d291e8df (#1589): header declaration-order witness, ELF "
        "symbol-versioned alias binding and DWARF advanced-extraction status "
        "are new facts; the lambda-closure respelling fix (d150dbdd0) is a "
        "compare-side filter -- no stored type spelling changed"
    ),
    3: GenerationSpellingChange(
        "27e6c848d (#1599): build_mode capture from detached debug and "
        "Mach-O and compiler-family switch bounds; the header-backend edits "
        "are import renames -- no stored type spelling changed"
    ),
    4: GenerationSpellingChange(
        "9245b872f (#1649): malformed DW_OP member locations read as unknown "
        "offsets instead of values; the castxml/clang edits are module moves "
        "-- offsets changed, no stored type spelling changed"
    ),
    5: GenerationSpellingChange(
        "362976812 (#1684): new castxml specialization_declared_fact on "
        "records -- a new fact, no stored type spelling changed"
    ),
    6: GenerationSpellingChange(
        "49529426d (#1698): castxml's undeduced AutoType placeholder is "
        "spelled 'auto' (extract/headers/castxml/type_resolution.py); the "
        "same-line specialization and clang instantiated_from edits add facts",
        (("AutoType", "auto"),),
    ),
    7: GenerationSpellingChange(
        "never on main: an intermediate bump inside #1712's branch (clang "
        "positional template defaults), folded into generation 9 by "
        "c23a9ad57 -- no snapshot was ever stamped 7"
    ),
    8: GenerationSpellingChange(
        "never on main: an intermediate bump inside #1712's branch (clang "
        "dependent-member defaults), folded into generation 9 by c23a9ad57 "
        "-- no snapshot was ever stamped 8"
    ),
    9: GenerationSpellingChange(
        "c23a9ad57 (#1712): class-template parameter defaults recorded as a "
        "new template_param_defaults fact (clang records, castxml header "
        "scan) -- previously absent, not respelled"
    ),
    10: GenerationSpellingChange(
        "1fc6c46c5 (#1720): ElfSymbol.origin_lib attributes std template "
        "local statics to the C++ runtime -- a symbol attribute, no stored "
        "type spelling changed"
    ),
    11: GenerationSpellingChange(
        "34c66d618 (#1745): clang records non-type and template-template "
        "parameter defaults (template_default_spelling.py) where the fact "
        "was absent before -- new values, not respelled ones"
    ),
    12: GenerationSpellingChange(
        "2be210ea9 (#1754): clang records function-template default "
        "arguments as a new fact -- no stored type spelling changed"
    ),
    13: GenerationSpellingChange(
        "126b3e759 (#1783): dump-time typedef retention rooted on header "
        "identifiers (flat_map_dependency_scope.py), so a dependency typedef "
        "can now be kept where it was dropped -- set membership, not spelling"
    ),
    14: GenerationSpellingChange(
        "dd03cb6a6 (#1785): DWARF vector types spell as 'T __attribute__((vector_size(N)))' instead "
        "of 'T[]' (dwarf_utils.array_type_info); a stored 'T[]' cannot say whether "
        "it was an array or a vector, so no rewrite is sound and a baseline "
        "holding a vector type is re-dumped"
    ),
    15: GenerationSpellingChange(
        "castxml vector types resolve to 'T __attribute__((vector_size(N)))' from the "
        "clang AST (a stored unresolved vector cannot be respelled, so a baseline "
        "holding one is re-dumped), and castxml arrays spell their element count "
        "(max + 1, was max): castxml_array_migration.py rewrites a castxml "
        "snapshot's stored 'T[N-1]' to 'T[N]' by provenance on load; for a "
        "snapshot whose producer is not recorded as castxml, comparison demotes "
        "the extent shift instead",
        extent_shift=1,
    ),
    16: GenerationSpellingChange(
        "castxml function types spell their full declarator, 'int (*)(int, int)', "
        "instead of the opaque 'FunctionType*' tag (extract/headers/castxml/"
        "type_resolution.py): the stored tag dropped the signature, so no rewrite "
        "is sound; policy/generation_drift_demotion.py demotes a stored tag "
        "against a fresh declarator, and a baseline holding callbacks is re-dumped"
    ),
    17: GenerationSpellingChange(
        "clang function-template patterns whose default argument cannot be spelled "
        "record template_param_defaults_fact as UNSUPPORTED (was absent); no type "
        "or name spelling changes, so no rewrite is needed -- an older clang "
        "snapshot simply names no gap for such a pattern"
    ),
    18: GenerationSpellingChange(
        "castxml collapses a same-key function group declared only in system "
        "headers outside a given public set, whose signatures (and exception "
        "specification) agree modulo cv -- glibc wcschr/strchr overloads, "
        "libstdc++ copy ctor beside its converting-template twin -- to its first "
        "declaration (duplicate_declarations.py): the dropped record was already "
        "shadowed first-wins by the index, so set membership shrinks and no "
        "stored type spelling is rewritten. A standalone mingw-w64 CRT "
        "(<prefix>/<arch>-w64-mingw32/include) now classifies as a system header "
        "(system_header_layout.py), so its declarations record origin "
        "system_header where they read unknown or private_header -- an origin "
        "value, not a type spelling"
    ),
    19: GenerationSpellingChange(
        "DWARF declarations record their declaring file when it is a header, and a dependency-internal presence finding no public declaration reaches is out of contract; no stored type spelling changed, only which declarations carry a location and which findings gate"
    ),
    20: GenerationSpellingChange(
        "clang records Function/Variable.explicit_instantiation_fact from a text "
        "AST pass (extract/headers/clang/explicit_instantiation.py); no existing "
        "type, name or fact spelling changes, so no rewrite is needed -- an older "
        "snapshot names no explicit form and reads as not collected"
    ),
    21: GenerationSpellingChange(
        "no stored type spelling changed in this generation: the header parse "
        "now uses the dialect and target flags the binary was built with"
    ),
    22: GenerationSpellingChange(
        "Mach-O LC_LOAD_WEAK_DYLIB install names are recorded as weak_dependent_libs, "
        "a new fact rather than a spelling: no stored type or declaration spelling "
        "changes, so no rewrite is needed and a stored snapshot reads as before"
    ),
    23: GenerationSpellingChange(
        "clang runs set template_default_header_scan when a parsed public header "
        "declares an alias or variable template with a default template argument "
        "(was None for every clang run); no type or name spelling changes, so no "
        "rewrite is needed -- an older clang snapshot simply names no "
        "alias/variable template-defaults gap"
    ),
    24: GenerationSpellingChange(
        "a header-graph call to a virtual method defined out of line is recorded "
        "call_kind virtual / resolution overapprox (was direct / exact) and so "
        "gains its VIRTUAL_CALL_MAY_DISPATCH_TO edges; graph edge attributes only, "
        "no type or declaration spelling changes, so no rewrite is needed"
    ),
    25: GenerationSpellingChange(
        "clang function EntityIds gain a ('requires', ...) constraint block for "
        "a requires-clause/type-constraint, and an out-of-line member/static "
        "data member definition takes its in-class declaration's EntityId; no "
        "type or name spelling changes, so no rewrite is needed -- an older "
        "clang snapshot keeps the colliding/split identities it recorded"
    ),
    26: GenerationSpellingChange(
        "castxml compiler emulation keeps a joined -xc/-xc++ language flag "
        "(only the separate -x c form was carried), so a dump whose compile "
        "flags spell the language joined now probes the emulated compiler in "
        "that language; a malformed clang AST 'inner' is refused instead of "
        "read as no children. No type or name spelling changes, so no rewrite "
        "is needed"
    ),
    27: GenerationSpellingChange(
        "castxml compiler emulation keeps a separate --target <triple> and "
        "-target <triple> (both copied as a pair for a clang-family emulated "
        "compiler, dropped for GCC), so a dump whose compile flags name the "
        "target as a separate operand now probes the emulated compiler for that "
        "target instead of the host; header ABI facts (pointer size, predefined "
        "macros) change for those dumps. No type or name spelling changes, so no "
        "rewrite is needed"
    ),
    28: GenerationSpellingChange(
        "an ELF, PE or Mach-O dump whose export table could not be read (zeroed "
        "section header table, no .dynsym, unnamed exported entries, a Mach-O "
        "dylib with no export trie or symbol table) is refused with an error "
        "instead of recording an empty export surface. Dumps that read their "
        "export table are unchanged; no type or name spelling changes, so no "
        "rewrite is needed (a baseline from an unreadable binary must be "
        "re-dumped, not rewritten)"
    ),
    29: GenerationSpellingChange(
        "function types spell their calling convention and full signature: a "
        "clang-written DW_AT_calling_convention on a DWARF subroutine type "
        "(ms_abi), the castxml FunctionType node joined from the clang AST for a "
        "field, typedef, parameter or return (castxml drops ms_abi/sysv_abi from "
        "its XML), and a CodeView LF_PROCEDURE now spell "
        "'void (*)(int) __attribute__((ms_abi))', and a DWARF or PDB procedure "
        "spells 'ret (*)(params)' where it spelled the opaque 'fn(...)'. The stored "
        "'fn(...)' cannot be rewritten to a signature, so no rewrite is sound; "
        "policy/generation_drift_demotion.py demotes a stored 'fn(...)' against a "
        "fresh declarator, and a convention present on one side only, when the "
        "generations differ, is demoted as drift. A baseline holding callbacks is "
        "re-dumped"
    ),
    30: GenerationSpellingChange(
        "a non-const variable declared inline with an arithmetic type "
        "(inline int x = 1;) records its initializer as its value on both "
        "frontends, and a const integral global the header declares without an "
        "initializer (extern const int k; defined in a .cpp) takes its value from "
        "the exported object's bytes. A header whose only C++ signal is an inline "
        "variable is parsed as C++, not rejected as C. A const variable template "
        "(template <class T> inline constexpr int vt = 1;) is joined from the clang "
        "AST onto the castxml root. A baseline from an earlier generation has no "
        "value for these variables, so a value change is not compared until it is "
        "re-dumped; no type or name spelling changes, so no rewrite is needed"
    ),
    31: GenerationSpellingChange(
        "castxml complex and bit-precise integer types (float _Complex, _BitInt(N)) "
        "and a pointer, reference, array or typedef chain around one resolve to the "
        "element type's clang spelling, where castxml left the one literal "
        "Unimplemented -- a spelling shared by every such type, so float _Complex -> "
        "double _Complex compared equal. A stored Unimplemented cannot say which "
        "type it was, so no rewrite is sound and a baseline holding one is re-dumped"
    ),
    32: GenerationSpellingChange(
        "the generation-32 ABI-fact change (model/abi_fundamental.py): DWARF "
        "(extract/dwarf_abi_fact.py) and castxml (extract/headers/castxml/"
        "abi_fundamental.py) record a fundamental slot's encoding class, size, "
        "alignment and vector lanes as type_abi_fact/return_type_abi_fact -- a "
        "new fact, not a spelling: no stored type spelling changes, so no rewrite "
        "is needed. The DWARF layout (DwarfMetadata.structs/struct_odr_conflicts "
        "FieldInfo) records the same fact per member as type_abi_fact, read from "
        "the member DIE, so the depth projection keeps it. A generation-31 or "
        "pre-v69 document carries no fact, so its slots keep the name-based verdict "
        "(an unset fact never proves two slots equal). A DWARF pointer declared restrict is now one level deep (it was "
        "zero: DW_TAG_restrict_type was not unwrapped), so a generation-31 DWARF "
        "baseline holding a restrict pointer parameter stores its pointer depth as "
        "zero and reads as a pointer-level change against a fresh dump; a re-dump "
        "is the fix for that baseline"
    ),
    33: GenerationSpellingChange(
        "the clang header frontend parses a Windows binary's headers for the C++ "
        "ABI its exports are mangled for (extract/headers/clang/abi_target.py): "
        "a MinGW (Itanium) DLL under a clang whose default is *-windows-msvc is "
        "parsed for *-windows-gnu, and an MSVC DLL under a MinGW-default clang for "
        "*-windows-msvc. Declarations that were mangled for the other ABI -- and "
        "so matched no export and were dropped as out of contract -- are now "
        "recorded. Only that Windows clang-frontend case changes; ELF, Mach-O, a "
        "C-only DLL and the castxml frontend record what they did. A stored "
        "generation-32 snapshot of such a DLL lacks those declarations, which no "
        "rewrite can supply, so it is re-dumped"
    ),
    34: GenerationSpellingChange(
        "no stored spelling changes: a PE dump whose located PDB is malformed "
        "(truncated, bad magic, corrupt stream directory, no TPI stream, a "
        "truncated supported TPI record or field-list sub-record) or whose "
        "struct/enum/toolchain extraction phase crashes now records the debug "
        "section as failed (pdb_metadata.PdbParseError) instead of present and "
        "empty or partial. A well-formed PDB records exactly what generation 33 "
        "did; a generation-33 snapshot of a malformed PDB carries an empty debug "
        "section no rewrite can mark failed, so it is re-dumped"
    ),
    35: GenerationSpellingChange(
        "no stored spelling changes: the direct-Clang header backend now reads "
        "a constructor's or conversion function's `explicit` specifier from its "
        "decl-specifier tokens (extract/headers/clang/explicit_specifier.py). "
        "Generation 34 recorded is_explicit=False with a PRESENT fact for every "
        "such declaration, because Clang's JSON never serializes the key; "
        "generation 35 records True, False, or an unknown (NOT_COLLECTED/"
        "UNSUPPORTED) fact. A generation-34 clang snapshot's False cannot be "
        "told apart from a real one, so it is re-dumped. A binary-implied "
        "-std= is also stamped as ast_toolchain language_standard_implied=1 "
        "so a binary-implied dialect difference can bound a comparison instead of refusing it; without the stamp the pair keeps the refusal. A C header whose only C++ signal is a guarded extern 'C' is now parsed in C mode when the build supplies a C -std= (generation 34 failed that parse outright, so it has no stored snapshot to drift)"
    ),
    36: GenerationSpellingChange(
        "no stored spelling changes: the direct-Clang header backend always "
        "selects the language (-x c / -x c++), also when the build supplies its "
        "own -std= (extract/headers/clang_header_argv.py). Generation 35 dropped "
        "-x whenever a -std= came from the build, so under the default clang++ "
        "driver a C build's header was read as C++ and failed ('-std=gnu11 not "
        "allowed with C++'): that parse has no stored snapshot to drift. A "
        "generation-35 clang snapshot of a C header without a build -std= was "
        "already parsed as C, so it records what generation 36 does"
    ),
    37: GenerationSpellingChange(
        "the direct-Clang header backend now records what it previously left "
        "empty or spelled as sugar: (1) a record's size, alignment, data size, "
        "vptr, base and field offsets from clang's own -fdump-record-layouts "
        "(extract/headers/clang/record_layout_dump.py) when DWARF did not fill "
        "them; (2) one record per explicitly instantiated or specialized class "
        "template, named by its spelled X<Args> (castxml's convention); (3) a "
        "field declared through an alias template by the alias target's type "
        "(castxml's spelling). A generation-36 clang snapshot lacks those facts, "
        "so comparing it with a generation-37 one would report the new records "
        "and fields as changes; it is re-dumped"
    ),
    38: GenerationSpellingChange(
        "no stored spelling changes: a -H directory now expands its headers in "
        "the header-order canonical order (resolved path as a string, so foo.h "
        "before foo/bar.h; header_utils.iter_directory_headers). Generation 37 "
        "sorted by pathlib.Path components (foo/bar.h first), so for a tree "
        "with a foo.h beside a foo/ directory the aggregate TU included the "
        "headers in a different order, header_sequence recorded that order, and "
        "the header-order witness re-dumped in canonical order. Types and "
        "declarations keep their spellings; only the declared order of such "
        "trees changes"
    ),
    39: GenerationSpellingChange(
        "no type or name spelling changes. (1) The castxml backend stores a base "
        "class's offset in bits, as DWARF and the clang layout pass do; it stored "
        "castxml's byte offset, so a castxml snapshot and a clang one disagreed on "
        "every nonzero base offset. castxml_base_offset_migration.py multiplies a "
        "stored castxml snapshot's base offsets by 8; a hybrid snapshot mixed both "
        "units and is re-dumped. (2) The clang record-layout pass "
        "(extract/headers/clang/record_layout_dump.py) now sizes an empty record "
        "and records an empty base's offset, gives the members of an unnamed "
        "member struct/union their offsets, probes C headers, and keeps a virtual "
        "base out of base_offsets (castxml and DWARF record none). A "
        "generation-38 clang snapshot lacks those facts or holds a virtual base's "
        "offset, which no rewrite can settle, so it is re-dumped"
    ),
    40: GenerationSpellingChange(
        "no stored spelling changes: the direct-Clang header backend resolves "
        "a base specifier by C++ name lookup from the scope that wrote it "
        "(extract/headers/clang/base_resolution.py), because clang 22+ writes "
        "a relative base (detail::I inside namespace lib) with no fully "
        "qualified desugaredQualType. A record whose base, at any depth, "
        "still does not resolve now records its vtable fact PARTIAL instead "
        "of PRESENT. bases keep their written spellings. A generation-39 "
        "clang snapshot may hold an empty or short PRESENT vtable for such a "
        "class; clang_vtable_base_migration.py lowers a pre-40 clang/hybrid "
        "vtable fact over bases to PARTIAL on load, and a re-dump restores it"
    ),
    41: GenerationSpellingChange(
        "castxml and clang runs set cxx_concept_header_scan when a parsed public "
        "header defines a C++20 concept or spells a requires-clause (was unset); "
        "no type or name spelling changes, so no rewrite is needed -- an older "
        "snapshot simply names no concept gap"
    ),
    42: GenerationSpellingChange(
        "clang runs set cxx_concept_constraints (the public headers' C++20 "
        "concepts and template constraints as conjunctive atom sets, "
        "extract/headers/clang/concept_constraints.py) where it was unset; no "
        "type or name spelling changes, so no rewrite is needed -- an older "
        "snapshot reads as not modelled and keeps its concept gap note"
    ),
    43: GenerationSpellingChange(
        "clang runs record Variable.alignment_bits for a builtin-typed variable "
        "(cv-qualified or not) as the compiler-probed natural alignment, as "
        "castxml's computed align already did (extract/builtin_abi."
        "natural_alignment_bits), and a parameter's pointer_depth counted "
        "through a typedef'd pointer as castxml does; no type or name spelling "
        "changes, so no "
        "rewrite is needed -- an older clang snapshot reads the alignment as "
        "not captured"
    ),
    44: GenerationSpellingChange(
        "PE/Mach-O header-scoped dumps keep the parsed header surface when no "
        "declaration's spelling matches an export (scope_fallback "
        "export-binding-unresolved replaces mangling-fallback), and bind a "
        "declaration to an export spelled in another mangling scheme "
        "(extract/header_export_binding): a bound declaration's mangled "
        "becomes the export's MSVC/Itanium/decorated spelling. ELF dumps and "
        "every type or name spelling are unchanged, so no rewrite is needed -- "
        "an older PE/Mach-O snapshot keeps its export-table-only surface"
    ),
    45: GenerationSpellingChange(
        "DWARF arrays spell each dimension's bound (dwarf_utils.array_type_info: "
        "'char[8]', 'int[2][3]', was 'char[]' for every array; a flexible member "
        "keeps 'T[]') and an array without DW_AT_byte_size records its size as "
        "count times element size (was 0); DWARF-only snapshots record C++ "
        "member and namespace-scope functions defined outside their declaration "
        "(DW_AT_specification, extract/dwarf_member_functions), with the "
        "declaration's name, scope, return type and 'this' qualifiers (an "
        'extern "C" one by its plain name), and each virtual\'s vtable slot '
        "from DW_AT_vtable_elem_location (was 0 or unrecorded). A stored 'T[]' cannot say what bound it "
        "erased, so no rewrite is sound: comparison demotes a finding whose "
        "spellings differ only by that erasure (policy/generation_drift_demotion)",
        restores_erased_extents=True,
    ),
    46: GenerationSpellingChange(
        "no type or name spelling changes. castxml runs read a function's "
        "noexcept specification and the public headers' C++20 concept and "
        "template constraints from the clang AST of the same header "
        "(extract/headers/castxml/clang_semantics_join.py), where castxml "
        "stored is_noexcept=False for every function and no constraint table; "
        "a function whose header spells a specification the join could not "
        "read stores is_noexcept=None (not observed). "
        "castxml_noexcept_migration.py loads a pre-46 castxml snapshot's C++ "
        "is_noexcept=False as None, so an older baseline reports no "
        "func_noexcept_added for an unchanged noexcept function; its missing "
        "constraint table already reads as not modelled",
        migrates_facts=True,
    ),
    47: GenerationSpellingChange(
        "no type or name spelling changes. castxml and clang runs set "
        "cxx_template_defaults (the public headers' template-parameter "
        "defaults of every class, function, alias and variable template, read "
        "from the clang AST: extract/headers/clang/template_default_entries.py; "
        "castxml through castxml/clang_semantics_join.py) where it was unset; "
        "no rewrite is needed -- an older snapshot reads as not modelled, so a "
        "castxml side keeps its header-scan gap note and a clang side its "
        "alias/variable-template gap note"
    ),
    48: GenerationSpellingChange(
        "advanced DWARF records a by-value aggregate's triviality as 'unknown' "
        "when the unit carries only its declaration (clang's type homing of "
        "std::string), and takes a type's own DW_AT_calling_convention as the "
        "answer when present; the ELF origin of a std customization point "
        "specialized over builtins only (std::char_traits<char>) is the C++ "
        "runtime. No type or name spelling changes, so no rewrite is needed -- "
        "an older snapshot's declaration-only 'trivial' reads as before"
    ),
    49: GenerationSpellingChange(
        "an ELF dump records every compilation unit's stack-protector setting "
        "from its DW_AT_producer (the abicheck.cu_stack_protector extension "
        "fact), and a DWARF-derived variable records its DW_AT_alignment as "
        "alignment_bits (a nameless out-of-line definition, such as clang's "
        "for a C++ static data member, is read with the declaration it "
        "completes, so that variable is now recorded). No type or name spelling changes, so no rewrite is "
        "needed -- an older snapshot reads as no unit recorded (a lost canary "
        "keeps its RISK) and as no declared DWARF alignment"
    ),
    50: GenerationSpellingChange(
        "no type or name spelling changes. DWARF-only snapshots record an "
        "export-table-only record for each ABI-relevant export no DWARF "
        "declaration names (a .symver alias, an IFUNC, a TU built without -g; "
        "extract/dwarf_export_gaps.py), set Function.is_variadic from "
        "DW_TAG_unspecified_parameters (was unset), and the advanced DWARF "
        "toolchain flags include -f[no-][un]signed-char from DW_AT_producer; "
        "no rewrite is needed -- an older snapshot reads is_variadic as not "
        "recorded and simply lacks the added records and flags"
    ),
    51: GenerationSpellingChange(
        "every dump of a binary file records the SHA-256 of the binary's bytes "
        "(the abicheck.binary_sha256 extension fact on the binary section, "
        "hashed through a GNU ld linker script to its target like a live "
        "operand), so the identical-input exemption can prove a stored dump and "
        "a live binary one input. No type or name spelling changes, so no "
        "rewrite is needed "
        "-- an older snapshot reads as no recorded identity, which proves no "
        "identical-input exemption"
    ),
    52: GenerationSpellingChange(
        "Clang ordinary enum representations come from a compiler probe instead of assuming int; Clang parameter/return slots record scoped record/enum identities through aliases and namespace imports; extern-C at a noninitial column selects C++ mode; "
        "the built-in Clang layout pass follows the primary parse's recorded C/C++ mode, so a no-DWARF dump of C++ headers reached through a .h now carries record sizes. An older int cannot reveal the original representation and absent identities cannot be rebuilt from names: no sound load-time rewrite; re-dump old Clang baselines. Type spellings are unchanged."
    ),
}

#: Reasons that say nothing about what was audited; the bump gate refuses them.
PLACEHOLDER_REASONS = frozenset(
    {
        "",
        "tbd",
        "todo",
        "none",
        "n/a",
        "no change",
        "no spelling change",
        "no stored type spelling changed",
        "no stored type spelling changed in this generation",
        "bumped for a semantic change",
    }
)


def is_placeholder_reason(reason: str) -> bool:
    """Whether *reason* is empty or a bare "no change" placeholder."""
    return reason.strip().lower().rstrip(".") in PLACEHOLDER_REASONS


def extractor_generation_of(fact_generations: FactGenerations | None) -> int:
    """The extractor generation a snapshot's facts were extracted under:
    ``None`` (extracted by this process) reads as this build's
    ``EXTRACTOR_GENERATION``; an unstated stamp stays
    :data:`~.versioning.UNSTATED_GENERATION`."""
    if fact_generations is None:
        return EXTRACTOR_GENERATION
    return fact_generations[0]


def pending_extent_shift(old_generation: int, new_generation: int) -> int:
    """The array-extent shift the generations in (*old_generation*,
    *new_generation*] declared; 0 when none did or the order is reversed."""
    return sum(
        change.extent_shift
        for introduced, change in SPELLING_MIGRATIONS.items()
        if old_generation < introduced <= new_generation
    )


def erased_extents_restored_between(generation_a: int, generation_b: int) -> bool:
    """Whether a generation in between the two (either order) started
    spelling the array bounds an older DWARF extractor erased to ``[]``."""
    low, high = sorted((generation_a, generation_b))
    return any(
        change.restores_erased_extents
        for introduced, change in SPELLING_MIGRATIONS.items()
        if low < introduced <= high
    )


def encode_fact_generations(
    generations: FactGenerations, spellings_generation: int | None = None
) -> dict[str, int]:
    extractor, resolver, source = generations
    encoded = {"extractor": extractor, "resolver": resolver, "source": source}
    if spellings_generation is not None and spellings_generation > extractor:
        encoded[SPELLINGS_KEY] = spellings_generation
    return encoded


def decode_spellings_generation(document: Mapping[str, Any]) -> int | None:
    """The generation whose spellings *document* already carries, when a
    previous load migrated it; ``None`` when it was never migrated."""
    raw = document.get(FACT_GENERATIONS_KEY)
    value = raw.get(SPELLINGS_KEY) if isinstance(raw, Mapping) else None
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def decode_fact_generations(document: Mapping[str, Any]) -> FactGenerations:
    """The stated triple; any absent or malformed axis reads as unstated."""
    raw = document.get(FACT_GENERATIONS_KEY)
    if not isinstance(raw, Mapping):
        return (UNSTATED_GENERATION, UNSTATED_GENERATION, UNSTATED_GENERATION)
    versions = StorageVersions.from_dict(
        {
            "extractor_generation": raw.get("extractor"),
            "resolver_generation": raw.get("resolver"),
            "source_generation": raw.get("source"),
        }
    )
    return (
        versions.extractor_generation,
        versions.resolver_generation,
        versions.source_generation,
    )


def _pending_rewrites(extractor_generation: int) -> list[tuple[str, str]]:
    return [
        pair
        for generation in sorted(SPELLING_MIGRATIONS)
        if generation > extractor_generation
        for pair in SPELLING_MIGRATIONS[generation].rewrites
    ]


def _declared_type_names(document: Mapping[str, Any]) -> set[str]:
    names: set[str] = set()
    for key in ("types", "enums"):
        for entry in document.get(key) or ():
            if isinstance(entry, Mapping) and isinstance(entry.get("name"), str):
                names.add(entry["name"].rsplit("::", 1)[-1])
    typedefs = document.get("typedefs")
    if isinstance(typedefs, Mapping):
        names.update(str(k).rsplit("::", 1)[-1] for k in typedefs)
    return names


def _rewrite(
    value: Any, pattern: re.Pattern[str], table: dict[str, str], hits: list[int]
) -> Any:
    """*value* with every whole-word old spelling replaced; an unchanged
    subtree is returned as the same object, so a document with nothing to
    migrate is never copied."""
    if isinstance(value, str):
        if pattern.search(value) is None:
            return value
        hits[0] += 1
        return pattern.sub(lambda m: table[m.group(0)], value)
    if isinstance(value, dict):
        out: dict[Any, Any] | None = None
        for index, (key, item) in enumerate(value.items()):
            new_key = _rewrite(key, pattern, table, hits)
            new_item = _rewrite(item, pattern, table, hits)
            if out is None and (new_key is not key or new_item is not item):
                out = dict(list(value.items())[:index])
            if out is not None:
                out[new_key] = new_item
        return value if out is None else out
    if isinstance(value, list):
        items = [_rewrite(item, pattern, table, hits) for item in value]
        changed = any(a is not b for a, b in zip(items, value, strict=True))
        return items if changed else value
    return value


def _migrate_spellings(
    document: dict[str, Any], extractor_generation: int
) -> tuple[dict[str, Any], list[str]]:
    pending = _pending_rewrites(extractor_generation)
    if not pending:
        return document, []
    declared = _declared_type_names(document)
    notices: list[str] = []
    applicable: dict[str, str] = {}
    for old, new in pending:
        if old in declared:
            # A user type really named like the old placeholder: rewriting
            # would rename it, so keep it and say so.
            notices.append(
                f"spelling migration '{old}' -> '{new}' skipped: this snapshot "
                f"declares a type named '{old}'; re-dump the baseline"
            )
            continue
        applicable[old] = new
    if not applicable:
        return document, notices
    pattern = re.compile(
        r"\b(?:" + "|".join(re.escape(old) for old in applicable) + r")\b"
    )
    hits = [0]
    migrated = _rewrite(document, pattern, applicable, hits)
    if hits[0]:
        rules = ", ".join(f"'{o}' -> '{n}'" for o, n in applicable.items())
        notices.append(
            f"migrated {hits[0]} stored spelling(s) from an older extractor "
            f"generation ({rules}); re-dump the baseline to drop this migration"
        )
    return migrated, notices


def migrate_and_describe(
    document: dict[str, Any],
) -> tuple[dict[str, Any], FactGenerations, int | None, tuple[str, ...]]:
    """Read *document*'s generation stamp, apply the spelling migrations its
    extractor generation predates, and describe both as load notices.

    Returns the (possibly rewritten, never mutated in place) document, the
    stated generations, the generation whose spellings the returned document
    carries (``None`` when no migration was due), and the notices. Drift is decided by
    :func:`~.versioning.reader_generation_compatibility` -- the one owner
    every package read uses -- not by a second comparison here.
    """
    generations = decode_fact_generations(document)
    extractor, resolver, source = generations
    notices: list[str] = []
    compatibility = reader_generation_compatibility(
        StorageVersions(
            extractor_generation=extractor,
            resolver_generation=resolver,
            source_generation=source,
        )
    )
    if compatibility.semantics_differ:
        notices.append(f"{_SNAPSHOT_GENERATION_NOTICE_PREFIX}: {compatibility.reason}")
    elif extractor == UNSTATED_GENERATION:
        notices.append(
            f"{_SNAPSHOT_GENERATION_NOTICE_PREFIX} does not state its extractor "
            "generation (written "
            "before generation stamping), so it may have been "
            f"{GENERATION_DRIFT_NOTICE_MARKER} extractor semantics; "
            "re-dump the baseline"
        )
    previous = decode_spellings_generation(document)
    spellings = max(extractor, previous or extractor)
    if previous is not None and spellings > extractor:
        # Migrated by an earlier load and saved again: the spellings are
        # already rewritten, but the document still holds them, so say so
        # exactly as the first load did (a dry run that keeps only notices).
        notices.extend(migrate_castxml_array_counts(document, extractor)[1])
        notices.extend(migrate_castxml_base_offsets(document, extractor)[1])
        notices.extend(migrate_clang_vtable_bases(document, extractor)[1])
        notices.extend(migrate_castxml_noexcept(document, extractor)[1])
    if spellings >= EXTRACTOR_GENERATION:
        return document, generations, previous, tuple(notices)
    migrated, spelling_notices = _migrate_spellings(document, spellings)
    notices.extend(spelling_notices)
    migrated, array_notices = migrate_castxml_array_counts(migrated, spellings)
    notices.extend(array_notices)
    migrated, offset_notices = migrate_castxml_base_offsets(migrated, spellings)
    notices.extend(offset_notices)
    migrated, vtable_notices = migrate_clang_vtable_bases(migrated, spellings)
    notices.extend(vtable_notices)
    migrated, noexcept_notices = migrate_castxml_noexcept(migrated, spellings)
    notices.extend(noexcept_notices)
    return migrated, generations, EXTRACTOR_GENERATION, tuple(notices)
