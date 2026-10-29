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
    """

    reason: str
    rewrites: tuple[tuple[str, str], ...] = ()
    extent_shift: int = 0


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
    if spellings >= EXTRACTOR_GENERATION:
        return document, generations, previous, tuple(notices)
    migrated, spelling_notices = _migrate_spellings(document, spellings)
    notices.extend(spelling_notices)
    migrated, array_notices = migrate_castxml_array_counts(migrated, spellings)
    notices.extend(array_notices)
    return migrated, generations, EXTRACTOR_GENERATION, tuple(notices)
