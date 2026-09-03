# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Which symbols a snapshot actually exports.

ADR-061 Phase 3: moved out of ``cli_buildsource_merge`` so engine code can ask
this question without importing the CLI layer. ``embed_build_source`` needs it
to seed L4 decl->symbol linking, and that was the last CLI dependency keeping
it there.

Note ``crosscheck_base`` documents a rule that "matches" this one; unifying
the two is a separate slice, not folded in here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..model import AbiSnapshot


def exported_symbols_from_snapshot(snap: AbiSnapshot) -> tuple[str, ...]:
    """Exported (mangled) symbol names already parsed into *snap* — no re-dump.

    Used to plumb L0 exports into inline source replay (A1) for the
    ``dump <binary> --sources`` flow. Empty for a source-only snapshot.

    The authoritative export set is the platform **dynamic symbol table**
    (``elf.symbols`` / ``pe.exports`` / ``macho.exports``), which lists every
    exported symbol as its raw linker name. When one is present it is used
    **alone**: the modeled ``functions``/``variables`` lists are a *narrower*,
    DWARF-shaped view that (a) covers only a fraction of the exports — feeding
    only those collapsed symbol matching to a handful of hits (the plugin/
    ``merge`` regression) — and (b) can carry non-ABI ctor/dtor linkage tags
    (GCC's unified ``C4``/``D4``) that are **not** real exports; unioning them in
    would let a source decl mangled ``C4`` exact-match a phantom and inflate
    ``exported_symbols``/``matched_symbols`` with a name the binary never exported
    (Codex review). The modeled mangled names are therefore only a *fallback* for
    backends that expose no raw table at all (a source-only snapshot, or a format
    whose export table did not parse).
    """
    raw: set[str] = set()
    have_raw_table = False
    elf = getattr(snap, "elf", None)
    if elf is not None:
        have_raw_table = True
        # Only DEFAULT-versioned ELF exports enter the relink set. A name that
        # exists *solely* as a non-default version alias (`foo@VER` with no
        # `foo@@VER`) cannot be linked against by an unversioned consumer, so
        # including it would let the L4 mapping mark a header decl backed only by
        # such an alias as "exported" — and the crosscheck's two-way reconciliation
        # would then wrongly suppress the `public_not_exported` finding the consumer
        # would actually hit as an undefined symbol (Codex review). Mirrors
        # `crosscheck._exported_symbol_names`. `is_default` is True for unversioned
        # symbols, so plain (non-versioned) libraries are unaffected.
        raw |= {
            s.name
            for s in getattr(elf, "symbols", ())
            if getattr(s, "name", "") and getattr(s, "is_default", True)
        }
    pe = getattr(snap, "pe", None)
    if pe is not None:
        have_raw_table = True
        raw |= {e.name for e in getattr(pe, "exports", ()) if getattr(e, "name", "")}
    macho = getattr(snap, "macho", None)
    if macho is not None:
        have_raw_table = True
        raw |= {e.name for e in getattr(macho, "exports", ()) if getattr(e, "name", "")}
    raw.discard("")
    if have_raw_table:
        # A parsed platform table is authoritative EVEN WHEN EMPTY — a hidden-only
        # library genuinely exports nothing, so its DWARF-modeled `functions` are
        # *not* exports and must not be relinked as if they were (Codex review).
        return tuple(sorted(raw))
    # No platform table parsed at all (a source-only snapshot): the modeled
    # mangled names are the only available fallback.
    syms = {fn.mangled for fn in snap.functions if fn.mangled}
    syms |= {v.mangled for v in snap.variables if getattr(v, "mangled", "")}
    syms.discard("")
    return tuple(sorted(syms))
