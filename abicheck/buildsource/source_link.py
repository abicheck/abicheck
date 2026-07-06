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

"""Source ABI linker (ADR-030 D5).

Folds per-TU :class:`SourceAbiTu` dumps into one per-library
:class:`SourceAbiSurface`, linking source declarations against the library's
exported binary symbols (from L0) and public-header set — the same conceptual
flow as Android's ``header-abi-linker`` (ADR-030 references), without adopting
its unstable intermediate formats.

Linking is cheap relative to parsing, so it is recomputed rather than cached
(ADR-030 D8); only the per-TU dumps are cached.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from .source_abi import SourceAbiSurface, SourceAbiTu, SourceEntity

#: C++ Itanium ctor/dtor "ABI clone" tags. The compiler emits *several* object
#: symbols for one source ctor/dtor — ``C1`` (complete), ``C2`` (base), ``C3``
#: (allocating); ``D0`` (deleting), ``D1`` (complete), ``D2`` (base) — plus GCC's
#: non-standard unified ``C4``/``D4`` marker seen in DWARF linkage names. A source
#: extractor sees ONE ``Decl`` and emits one mangled name (usually ``C1``/``D1``,
#: or ``C4``/``D4`` from DWARF). An exact-string match therefore attributes only
#: *one* clone to the declaration and reports the siblings as "exported symbol
#: with no source decl" — a systematic under-count for every class with exported
#: ctors/dtors. Folding the tag to a canonical marker lets the single declaration
#: claim all of its clone symbols (ADR-030 D5 symbol linking).
_CTOR_DTOR_TAGS = frozenset({"C1", "C2", "C3", "C4", "D0", "D1", "D2", "D4"})


def _skip_e_terminated(symbol: str, i: int) -> int:
    """Return the index just past the ``E`` that closes the ``I``/``N`` at *i*.

    Balances every nested ``E``-terminated production — ``I`` (template-args),
    ``N`` (nested-name), and ``F`` (function type, e.g. a ``A<void(int)>`` arg
    mangled ``FviE``) — and consumes length-prefixed ``<source-name>`` components
    *wholesale* so their interior characters (which can include ``I``/``N``/``F``/
    ``E``) never miscount. Without this, a nested type inside a template argument
    (the ``NSt7__cxx11…E`` of ``std::vector<std::string>``, or the ``F…E`` of a
    function-typed argument) would close the balance early and the real ctor tag
    that follows would be missed (Codex review).

    Best-effort: on any unrecognized production it advances one character, so an
    exotic tail can only cause a *missed* fold — never a wrong one, preserving
    the no-false-fold guarantee.
    """
    n = len(symbol)
    depth = 1  # symbol[i] is the opener
    i += 1
    while i < n and depth:
        c = symbol[i]
        if c == "L":
            # <expr-primary> literal := L <type> <value> E. Its VALUE is raw
            # digits (a non-type template parameter, e.g. `Fixed<3>` → `Li3E`),
            # NOT a length-prefixed source-name — so consume the literal to its
            # own matching E flatly (digits are literal chars here), only
            # balancing any nested I/N/F/L in its type. Without this the digit
            # would be misread as a length and swallow the trailing ctor tag
            # (Codex review).
            ldepth = 1
            i += 1
            while i < n and ldepth:
                d = symbol[i]
                if d in "INFL":
                    ldepth += 1
                elif d == "E":
                    ldepth -= 1
                i += 1
            continue
        if c.isdigit():  # <source-name> — consume by its declared length
            j = i
            while j < n and symbol[j].isdigit():
                j += 1
            i = j + int(symbol[i:j])
            continue
        if c in "INF":
            depth += 1
        elif c == "E":
            depth -= 1
        i += 1
    return i


#: Cheap pre-filter: a symbol can only carry a ctor/dtor special name if it holds
#: one of these substrings. Lets the demangler backstop skip the ~all symbols that
#: are obviously not ctors/dtors (no fork/parse) before paying for a demangle.
_CTOR_DTOR_SUBSTR = tuple(t + "E" for t in _CTOR_DTOR_TAGS)


def _ctor_dtor_canonical(symbol: str) -> str:
    """Fold a genuine Itanium ``<ctor-dtor-name>`` to a single canonical marker.

    Primary path is the fast, dependency-free structural parser
    (:func:`_ctor_dtor_structural`). When it cannot fold a symbol that *looks*
    like it carries a ctor/dtor tag — an exotic Itanium production the hand-parser
    doesn't model — fall back to a **demangler**-derived key (abicheck's demangler
    is a full Itanium parser, so it collapses every C1/C2/C3 and D0/D1/D2 clone to
    the same demangled ``Class::Class()`` / ``Class::~Class()`` form). The backstop
    is best-effort: if no demangler is available the structural result stands (a
    safe *missed* fold, never a wrong one). Both the export index and the decl
    side run through this one function, so their keys stay in the same space.
    """
    folded = _ctor_dtor_structural(symbol)
    if folded != symbol:
        return folded
    if any(sub in symbol for sub in _CTOR_DTOR_SUBSTR):
        return _ctor_dtor_demangle_fallback(symbol)
    return symbol


def _ctor_dtor_demangle_fallback(symbol: str) -> str:
    """Demangler-derived canonical key for a ctor/dtor the parser couldn't fold.

    Returns a ``"ctordtor:<demangled>"`` key when *symbol* demangles to a
    constructor (``Name::Name(``) or destructor (``Name::~Name(``) — the demangled
    form already omits the C1/C2/D0/D1 variant number, so every clone maps to one
    key. Returns *symbol* unchanged when a demangler is unavailable or the symbol
    is not actually a ctor/dtor, keeping exact-match semantics.
    """
    try:
        from ..demangle import demangle as _demangle

        # Normalize the Mach-O `__Z` prefix first (the demangler only accepts `_Z`).
        demangled = _demangle(_norm_itanium(symbol))
    except Exception:  # noqa: BLE001 - demangling is a best-effort backstop
        return symbol
    if not demangled or "(" not in demangled:
        return symbol
    qualified = demangled.split("(", 1)[0].rstrip()
    # Strip a trailing cv/ref qualifier list is unnecessary here (we cut at "(").
    parts = qualified.rsplit("::", 1)
    if len(parts) != 2:
        return symbol
    scope, name = parts
    cls = scope.rsplit("::", 1)[-1]
    # Constructor: leaf name == class name (ignoring template args on either).
    base_cls = cls.split("<", 1)[0]
    base_name = name.split("<", 1)[0]
    if base_name == base_cls or base_name == "~" + base_cls:
        return f"ctordtor:{qualified}"
    return symbol


def _ctor_dtor_structural(symbol: str) -> str:
    """Fold a genuine Itanium ``<ctor-dtor-name>`` by structural parse (no deps).

    ``_ZN3FooC1Ev``/``_ZN3FooC2Ev``/``_ZN3FooC4Ev`` fold to one key (and likewise
    the ``D0``/``D1``/``D2``/``D4`` destructor variants), so a single source
    ctor/dtor declaration claims all of its exported ABI clones.

    Only a *genuine* special name is folded — one that sits at a ``<nested-name>``
    component boundary, right after the class ``<source-name>``. A ``C1``/``D0``
    that is merely the tail of a length-prefixed **ordinary identifier** (a
    function literally named ``AC1`` mangles as ``_ZN1N3AC1Ev``) must NOT fold,
    or two unrelated symbols (``AC1``/``AC2``) would collide and one would be
    dropped from ``symbols_without_decl`` (Codex review). To tell them apart the
    nested name is parsed component-by-component: length-prefixed identifiers are
    consumed *wholesale* (by their declared length), so their interior characters
    — including any ``C1``/``D0`` — never reach the tag test. A no-op for any name
    without a genuine tag, so non-ctor/dtor symbols keep exact-match semantics.
    """
    # Mach-O/Darwin prefixes every Itanium symbol with an extra leading
    # underscore (`__ZN1AC1Ev`). NORMALIZE it away for the canonical key — do NOT
    # restore it — because the two producers spell it differently: the binary
    # export table (`macho_metadata`) already strips one underscore (`_ZN…`) while
    # the Clang plugin emits the raw `__ZN…` mangled name. Restoring the prefix
    # would keep those in different key spaces and the clones would never match on
    # macOS Flow-C (Codex review). A folded ctor/dtor key is only a *grouping*
    # key, so normalizing the spelling is safe; the actual matched symbols keep
    # their real names. Non-ctor symbols still fall through to `return original`
    # (their original spelling) for exact matching.
    original = symbol
    body = symbol
    if body[:3] == "__Z":
        body = body[1:]
    # A ctor/dtor is always a class member → a nested name. Non-nested symbols
    # (plain ``_Z…``, vtables/typeinfo ``_ZTV``/``_ZTI``, data) have none.
    if not body.startswith("_ZN"):
        return original
    symbol = body
    i, n = 3, len(symbol)
    # Leading CV-/ref-qualifiers on the implicit object parameter.
    while i < n and symbol[i] in "rVKRO":
        i += 1
    # ``boundary`` = we are at a clean prefix-component boundary (the previous
    # token was a fully-consumed source-name / substitution / template-arg list),
    # so a leading ``C``/``D`` here can only be a ctor/dtor special name — never
    # the middle of an identifier.
    boundary = False
    while i < n:
        c = symbol[i]
        if c == "E":  # end of the nested-name
            break
        if c.isdigit():  # <source-name> := <len> <identifier> — consume wholesale
            j = i
            while j < n and symbol[j].isdigit():
                j += 1
            i = j + int(symbol[i:j])
            boundary = True
            continue
        if c == "I":  # <template-args> := I … E (skip the balanced, nested run)
            i = _skip_e_terminated(symbol, i)
            boundary = True
            continue
        if c == "S":  # <substitution> := S_ | S<id>_ | S[abisod]
            if i + 1 < n and symbol[i + 1] in "atbsiod":
                i += 2
            else:
                i += 1
                while i < n and symbol[i] != "_":
                    i += 1
                i += 1  # consume the trailing '_'
            boundary = True
            continue
        if boundary and c in "CD" and symbol[i : i + 2] in _CTOR_DTOR_TAGS:
            # Normalized (prefix-stripped) canonical key — unifies `__ZN`/`_ZN`.
            return symbol[:i] + c + "@" + symbol[i + 2 :]
        # Unknown production: advance without claiming a boundary, so a later
        # C1/D0 reached only by char-skip is never mistaken for a special name.
        boundary = False
        i += 1
    # No fold: return the ORIGINAL symbol (with its prefix) so non-ctor names keep
    # exact-match semantics against the export set.
    return original


def _norm_itanium(symbol: str) -> str:
    """Strip the Mach-O leading underscore so ``__ZN…`` and ``_ZN…`` share a key.

    Mach-O/Darwin prefixes every Itanium symbol with an extra ``_``, so the Clang
    facts plugin records a method as ``__ZN1A3fooEv`` while ``macho_metadata``
    strips one underscore off the export table to ``_ZN1A3fooEv``. Ordinary (non
    ctor/dtor) names take the no-fold path in :func:`_ctor_dtor_structural` and are
    returned verbatim, so without normalizing here the two spellings would never
    exact-match and every plain C++ API on macOS would land in the unmatched sets
    (Codex review). Only the *matching key* is normalized; the real exported name
    is preserved so ``symbols_without_decl`` still subtracts the true spelling.
    """
    return symbol[1:] if symbol.startswith("__Z") else symbol


def _build_export_index(exported: set[str]) -> dict[str, list[str]]:
    """Index ctor/dtor canonical forms → the concrete exported clone symbols.

    Only names whose canonical form *differs* (i.e. actual ctor/dtor symbols) are
    indexed, so the map stays small and a non-ctor symbol can never collide into
    it — those still match via the normalized exact index (:func:`_build_exact_index`).
    """
    index: dict[str, list[str]] = {}
    for sym in exported:
        canon = _ctor_dtor_canonical(sym)
        if canon != sym:
            index.setdefault(canon, []).append(sym)
    return index


def _build_exact_index(exported: set[str]) -> dict[str, str]:
    """Map each export's Mach-O-normalized key → its real (as-exported) spelling.

    So an ordinary decl the plugin recorded as ``__ZN1A3fooEv`` resolves to the
    binary's ``_ZN1A3fooEv`` at the exact tier. Iterated in sorted order and the
    canonical ``_Z`` spelling wins any (practically impossible) collision where a
    binary lists both ``_ZN…`` and ``__ZN…`` for one entity, so the result is
    deterministic and returns a name that is genuinely in ``exported``.
    """
    index: dict[str, str] = {}
    for sym in sorted(exported):
        norm = _norm_itanium(sym)
        if norm not in index or sym == norm:
            index[norm] = sym
    return index


def _match_export(
    export_sym: str,
    exported: set[str],
    ctor_dtor_index: dict[str, list[str]],
    exact_index: dict[str, str],
) -> tuple[str, list[str]]:
    """Resolve a decl's export name to ``(primary_symbol, all_clone_symbols)``.

    Exact match (via the Mach-O-normalized :func:`_build_exact_index`, so a
    plugin-emitted ``__ZN…`` name resolves to the binary's ``_ZN…`` export) wins;
    its ctor/dtor siblings (if any were also exported) are folded in so none is
    orphaned. When there is no exact hit but the name is a ctor/dtor, it is matched
    against the canonical index so a decl mangled as ``C1``/``D1`` (or the DWARF
    ``C4``/``D4``) still claims the ``C2``/``D2``/… clones the binary actually
    exports. Returns ``("", [])`` when nothing matches.
    """
    if not export_sym:
        return "", []
    canon = _ctor_dtor_canonical(export_sym)
    clones = ctor_dtor_index.get(canon)
    real = exact_index.get(_norm_itanium(export_sym))
    if real is not None:
        if clones:
            return real, sorted(set(clones) | {real})
        return real, [real]
    if clones:
        variants = sorted(clones)
        return variants[0], variants
    return "", []


def _is_synthesized_symbol(symbol: str) -> bool:
    """Whether *symbol* is a compiler-*synthesized* export that belongs to a type
    or a function rather than a free declaration — a vtable/VTT/typeinfo/typeinfo-
    name/thunk (``_ZT…``) or a guard variable (``_ZGV…``), optionally Mach-O
    ``__``-prefixed. These never match a source decl by name, so they must be
    attributed to their owner or they orphan into ``symbols_without_decl``."""
    s = symbol[1:] if symbol.startswith("__Z") else symbol
    return s.startswith("_ZT") or s.startswith("_ZGV")


#: demangled-prefix → (finding kind, owner is a "type" or "func").
_SYNTHESIZED_PREFIXES: tuple[tuple[str, str, str], ...] = (
    ("vtable for ", "vtable", "type"),
    ("VTT for ", "VTT", "type"),
    ("construction vtable for ", "construction-vtable", "type"),
    ("typeinfo for ", "typeinfo", "type"),
    ("typeinfo name for ", "typeinfo-name", "type"),
    ("non-virtual thunk to ", "thunk", "func"),
    ("virtual thunk to ", "thunk", "func"),
    ("covariant return thunk to ", "thunk", "func"),
    ("guard variable for ", "guard", "func"),
)

_TBB_MALLOC_PROXY_MARKER = "__TBB_malloc_proxy"
_TBB_MALLOC_PROXY_C_SYMBOLS = frozenset(
    {
        "__libc_calloc",
        "__libc_free",
        "__libc_malloc",
        "__libc_memalign",
        "__libc_pvalloc",
        "__libc_realloc",
        "__libc_valloc",
        "aligned_alloc",
        "calloc",
        "free",
        "mallinfo",
        "malloc",
        "malloc_usable_size",
        "mallopt",
        "memalign",
        "posix_memalign",
        "pvalloc",
        "realloc",
        "valloc",
    }
)
_TBB_MALLOC_PROXY_CPP_SYMBOLS = frozenset(
    {
        "_ZdaPv",
        "_ZdaPvRKSt9nothrow_t",
        "_ZdlPv",
        "_ZdlPvRKSt9nothrow_t",
        "_Znam",
        "_ZnamRKSt9nothrow_t",
        "_Znwm",
        "_ZnwmRKSt9nothrow_t",
    }
)


def _strip_call_signature(name: str) -> str:
    """Drop a demangled function's trailing parameter list, keeping its name.

    ``ns::Widget::foo()`` → ``ns::Widget::foo``; ``ns::f(int, char)`` → ``ns::f``.
    A naive ``split("(", 1)[0]`` would turn ``D::operator()()`` into ``D::operator``
    — orphaning call-operator (functor) thunks, whose owning decl is spelled
    ``D::operator()`` (Codex review). Instead the *trailing* balanced parenthesis
    group (the parameter list) is removed by matching the final ``)`` back to its
    opener right-to-left, so the ``()`` that is part of ``operator()`` is preserved.
    Best-effort: a name with no ``)`` is returned stripped of surrounding space.
    """
    close = name.rfind(")")
    if close == -1:
        return name.strip()
    depth = 0
    i = close
    while i >= 0:
        c = name[i]
        if c == ")":
            depth += 1
        elif c == "(":
            depth -= 1
            if depth == 0:
                return name[:i].strip()
        i -= 1
    return name.strip()


def _thunk_target_mangled(symbol: str) -> str | None:
    """Extract the underlying target symbol a thunk adjusts to, or ``None``.

    Itanium thunk special names embed the full mangling of the function they
    forward to after one or two ``<call-offset>`` fields:

      * ``_ZTh<nv-offset>_<encoding>``          non-virtual (``h``)
      * ``_ZTv<v-offset>_<vcall-offset>_<encoding>``  virtual (``v``)
      * ``_ZTc<call-offset><call-offset><encoding>``  covariant (``c``)

    where a ``<call-offset>`` is ``h<number>_`` or ``v<number>_<number>_`` and a
    ``<number>`` is ``[n]<digits>``. Returning ``_Z<encoding>`` (Mach-O-normalized
    in) yields the *overload-specific* target symbol, so a thunk is attributed to
    the exact overload it forwards to rather than any same-named decl (Codex
    review). Best-effort: ``None`` on any shape it doesn't recognize.
    """
    s = _norm_itanium(symbol)
    if not s.startswith("_ZT"):
        return None
    n = len(s)
    i = 3
    if i >= n or s[i] not in "hvc":
        return None
    kind = s[i]

    def read_number(j: int) -> int | None:
        if j < n and s[j] == "n":
            j += 1
        k = j
        while k < n and s[k].isdigit():
            k += 1
        return k if k > j else None

    def read_call_offset(j: int) -> int | None:
        # h <number> _   |   v <number> _ <number> _
        if j >= n or s[j] not in "hv":
            return None
        virtual = s[j] == "v"
        j2 = read_number(j + 1)
        if j2 is None or j2 >= n or s[j2] != "_":
            return None
        j2 += 1
        if virtual:
            j3 = read_number(j2)
            if j3 is None or j3 >= n or s[j3] != "_":
                return None
            j2 = j3 + 1
        return j2

    if kind == "c":  # covariant: two call-offsets
        j = read_call_offset(i + 1)
        if j is not None:
            j = read_call_offset(j)
    else:  # h / v begin their own call-offset at i
        j = read_call_offset(i)
    if j is None or j >= n:
        return None
    return "_Z" + s[j:]


def _synthesized_target(demangled: str) -> tuple[str, str, str] | None:
    """Parse a demangled synthesized symbol into ``(kind, target, owner_kind)``.

    ``"vtable for ns::Widget"`` → ``("vtable", "ns::Widget", "type")``;
    ``"non-virtual thunk to ns::Widget::f()"`` → ``("thunk", "ns::Widget::f()",
    "func")``. Returns ``None`` for anything not recognized.
    """
    for prefix, kind, owner in _SYNTHESIZED_PREFIXES:
        if demangled.startswith(prefix):
            return kind, demangled[len(prefix) :].strip(), owner
    return None


def _split_qualified_scope(name: str) -> tuple[str, str]:
    """Split ``ns::Owner<T>::member`` at the last top-level ``::``.

    Template arguments may contain their own qualified names, so a plain
    ``rsplit("::", 1)`` can split inside ``Owner<std::vector<int>>``. This helper
    tracks angle-bracket depth and only accepts scope separators at depth zero.
    """
    depth = 0
    last = -1
    i = 0
    while i < len(name) - 1:
        c = name[i]
        if c == "<":
            depth += 1
        elif c == ">" and depth:
            depth -= 1
        elif c == ":" and name[i + 1] == ":" and depth == 0:
            last = i
            i += 1
        i += 1
    if last < 0:
        return "", name
    return name[:last], name[last + 2 :]


def _drop_demangled_return_type(name: str) -> str:
    """Remove a leading return type from a demangled qualified function name.

    Demanglers spell free/template functions as ``Ret ns::f(...)`` but ctors and
    most methods have no return prefix. The matcher needs the qualified name
    only. A space before the first top-level ``::`` indicates such a prefix;
    keeping the token after that space preserves names like ``ns::C::operator=``.
    """
    first_scope = name.find("::")
    if first_scope <= 0:
        return name
    prefix_end = name.rfind(" ", 0, first_scope)
    return name[prefix_end + 1 :] if prefix_end >= 0 else name


def _template_source_owner_index(surface: SourceAbiSurface) -> dict[str, str]:
    """Template-erased owner key -> stable source owner label.

    Includes class/function template declarations from the template bucket and
    declaration/type buckets. When both ``Box`` and ``Box<T>`` are present for one
    erased key, prefer the bare template owner for readability; both describe the
    same source pattern.
    """
    owners: dict[str, set[str]] = {}
    for bucket in (
        surface.reachable_declarations,
        surface.reachable_templates,
        surface.reachable_types,
    ):
        for entity in bucket:
            if not entity.qualified_name:
                continue
            qname = _strip_call_signature(entity.qualified_name)
            key = _template_pattern_key(qname)
            if not key and entity.kind == "template":
                key = f"{qname}<>"
            if key:
                owners.setdefault(key, set()).add(entity.qualified_name)
    return {
        key: sorted(values, key=lambda v: ("<" in v, len(v), v))[0]
        for key, values in owners.items()
    }


def _template_special_member_owner_key(demangled: str) -> str:
    """Return the erased class-template owner key for ctor/dtor/operator= exports.

    A oneDAL source replay often records only a class template pattern
    (``compute_input<Task>`` or ``compute_input``), while the binary exports
    concrete special members (ctors/dtors/assignment) for
    ``compute_input<task::compute>``. Unlike arbitrary methods such as
    ``set_data()``, special members are unambiguously owned by the class template
    itself, so they can be attributed when that owner pattern is present.
    """
    qname = _drop_demangled_return_type(_strip_call_signature(demangled))
    owner, leaf = _split_qualified_scope(qname)
    if not owner or "<" not in owner:
        return ""
    _, owner_leaf = _split_qualified_scope(owner)
    class_base = owner_leaf.split("<", 1)[0]
    leaf_base = leaf.split("<", 1)[0]
    if (
        leaf_base == class_base
        or leaf_base == f"~{class_base}"
        or leaf.startswith("operator=")
    ):
        return _template_pattern_key(owner)
    return ""


def _attribute_synthesized_exports(
    surface: SourceAbiSurface, unmatched: set[str]
) -> dict[str, tuple[str, str]]:
    """Attribute exported vtable/typeinfo/RTTI/thunk/guard symbols to the public
    type or function they belong to (ADR-030 D5 symbol linking).

    Such symbols are emitted *for* a type (`_ZTV`/`_ZTI`/`_ZTS`/`_ZTT`) or a
    method (thunks, guard variables), never as a free declaration, so exact-name
    matching always left them in ``symbols_without_decl`` — inflating the
    "exported but no source decl" count for every polymorphic public class. This
    demangles each still-unmatched synthesized symbol and, when its owning type or
    function is present on the public surface, records it as attributed. Best
    effort: a no-op when no demangler is available (the orphans simply remain, as
    before), so it can only *improve* matching, never regress it.
    """
    candidates = [s for s in unmatched if _is_synthesized_symbol(s)]
    if not candidates:
        return {}
    try:
        from ..demangle import demangle as _demangle
    except Exception:  # noqa: BLE001 - attribution is a best-effort enhancement
        return {}
    type_names = {t.qualified_name for t in surface.reachable_types if t.qualified_name}
    func_names = {
        d.qualified_name for d in surface.reachable_declarations if d.qualified_name
    }
    # Overload-specific index: a decl's real mangled name → its qualified name, so a
    # thunk is attributed to the EXACT overload it forwards to (Codex review). Keyed
    # Mach-O-normalized to line up with `_thunk_target_mangled`'s `_Z…` output.
    mangled_to_qname = {
        _norm_itanium(d.mangled_name): (d.qualified_name or d.mangled_name)
        for d in surface.reachable_declarations
        if d.mangled_name
    }

    def _owner_present(name: str, public: set[str]) -> bool:
        # Exact match, OR a base match ONLY when the *unspecialized* name is
        # itself a public entity. A base match derived from another specialization
        # (e.g. `ns::A<int>` present, `ns::A<char>` not) must NOT attribute the
        # other specialization's RTTI — that would hide an exported, unchecked
        # specialization (Codex review). So require the bare base in `public`.
        if name in public:
            return True
        base = name.split("<", 1)[0]
        return base != name and base in public

    attributed: dict[str, tuple[str, str]] = {}
    for sym in candidates:
        try:
            demangled = _demangle(sym)
        except Exception:  # noqa: BLE001
            continue
        if not demangled:
            continue
        parsed = _synthesized_target(demangled)
        if parsed is None:
            continue
        kind, target, owner = parsed
        if owner == "type":
            if _owner_present(target, type_names):
                attributed[sym] = (kind, target)
        elif kind == "thunk":
            # A thunk carries the FULL mangling of the function it forwards to, so
            # attribute to the exact overload rather than any same-named decl: a
            # thunk for `D::foo(double)` (absent from the surface) must NOT be
            # attributed to a public `D::foo(int)` (Codex review). Fall through to
            # no attribution when the target overload is not a public decl.
            tgt = _thunk_target_mangled(sym)
            qn = mangled_to_qname.get(tgt) if tgt else None
            if qn is not None:
                attributed[sym] = (kind, qn)
        else:  # guard variable — attribute to the enclosing function by name
            fname = _strip_call_signature(target)
            if _owner_present(fname, func_names):
                attributed[sym] = (kind, fname)
    return attributed


def _demangled_rematch(
    reachable_declarations: list[SourceEntity],
    mapping: dict[str, str],
    matched: set[str],
    exported: set[str],
) -> dict[str, str]:
    """Second-tier match by *demangled identity* for decls exact-matching missed.

    A source extractor's mangled name can differ *textually* from the binary's
    export for the same entity — most commonly a missing/extra ABI tag
    (``[abi:cxx11]``), a substitution-form difference, or minor vendor mangling
    drift — so the exact/ctor-fold tiers leave the decl in ``decls_without_symbol``
    even though the export is right there. This demangles both sides and matches a
    still-unmatched decl to a still-unmatched export **only when the demangled
    forms are equal and the export is unique** for that form (so an overload set,
    whose members demangle distinctly, can never cross-match). Best-effort: a
    no-op without a demangler. Returns ``{identity: export}`` for the new matches
    and updates *mapping*/*matched* in place.
    """
    unmatched_exports = [e for e in exported if e not in matched]
    unmatched_decls = [
        e
        for e in reachable_declarations
        if e.identity() and not mapping.get(e.identity()) and e.mangled_name
    ]
    if not unmatched_exports or not unmatched_decls:
        return {}
    try:
        from ..demangle import demangle as _demangle
    except Exception:  # noqa: BLE001 - best-effort second tier
        return {}

    def _dem(sym: str) -> str | None:
        # Normalize the Mach-O leading underscore first: the shared demangler only
        # accepts `_Z…`, so a raw `__ZN…` plugin name would otherwise fail to
        # demangle and never rematch (Codex review).
        try:
            return _demangle(_norm_itanium(sym))
        except Exception:  # noqa: BLE001
            return None

    # Demangled form → exports; keep only forms that map to exactly one export so
    # a match is never ambiguous.
    by_demangled: dict[str, list[str]] = {}
    for exp_sym in unmatched_exports:
        exp_dem = _dem(exp_sym)
        if exp_dem:
            by_demangled.setdefault(exp_dem, []).append(exp_sym)
    unique = {d: syms[0] for d, syms in by_demangled.items() if len(syms) == 1}
    new_matches: dict[str, str] = {}
    for decl in unmatched_decls:
        decl_dem = _dem(decl.mangled_name)
        if not decl_dem:
            continue
        exp = unique.get(decl_dem)
        if exp and exp not in matched:
            mapping[decl.identity()] = exp
            matched.add(exp)
            new_matches[decl.identity()] = exp
            del unique[decl_dem]  # consume so two decls can't claim the same export
    return new_matches


def _template_pattern_key(name: str) -> str:
    """Return a template-argument-erased key for a demangled/source name.

    ``ns::Box<T>::get`` and ``ns::Box<int>::get`` both become
    ``ns::Box<>::get``. The key is deliberately conservative: only real balanced
    ``<...>`` groups are erased, so ``operator<`` without a closing template list
    is left alone. Whitespace is ignored because demanglers vary in how they
    spell template argument separators.
    """
    s = "".join((name or "").split())
    if "<" not in s or ">" not in s:
        return ""
    out: list[str] = []
    depth = 0
    saw_template = False
    for i, c in enumerate(s):
        if (
            c == "<"
            and depth == 0
            and s[max(0, i - len("operator")) : i] == "operator"
        ):
            out.append(c)
            continue
        if c == "<":
            if depth == 0:
                out.append("<>")
                saw_template = True
            depth += 1
            continue
        if c == ">" and depth:
            depth -= 1
            continue
        if depth == 0:
            out.append(c)
    return "".join(out) if saw_template and depth == 0 else ""


def _template_instantiation_attribution(
    surface: SourceAbiSurface,
    reachable_declarations: list[SourceEntity],
    matched: set[str],
    exported: set[str],
) -> dict[str, str]:
    """Attribute concrete template-instantiation exports to one public pattern.

    A Clang/CastXML source pass often records the public declaration as a template
    pattern (``Box<T>::get`` or ``foo<T>``), while the binary exports concrete
    instantiations (``Box<int>::get()`` / ``foo<double>(double)``). Exact mangled
    matching cannot connect those strings. This tier demangles still-unmatched
    exports, erases template arguments on both sides, and attributes an export
    only when exactly one public declaration owns that erased pattern. Multiple
    exports may map to the same unique pattern (many instantiations of one public
    template); multiple source declarations for one pattern are skipped to avoid
    hiding overloads.
    """
    candidates = [s for s in exported if s not in matched]
    if not candidates:
        return {}
    try:
        from ..demangle import demangle as _demangle
    except Exception:  # pragma: no cover  # noqa: BLE001 - optional demangler
        return {}

    source_owners = _template_source_owner_index(surface)
    decls_by_key: dict[str, list[SourceEntity]] = {}
    for decl in reachable_declarations:
        if not decl.qualified_name:
            continue
        key = _template_pattern_key(_strip_call_signature(decl.qualified_name))
        if key:
            decls_by_key.setdefault(key, []).append(decl)
    unique_decl = {
        key: decls[0]
        for key, decls in decls_by_key.items()
        if len({d.identity() for d in decls}) == 1
    }
    if not unique_decl and not source_owners:
        return {}

    attributed: dict[str, str] = {}
    for sym in candidates:
        try:
            demangled = _demangle(_norm_itanium(sym))
        except Exception:  # noqa: BLE001
            continue
        key = _template_pattern_key(_strip_call_signature(demangled or ""))
        owner = unique_decl.get(key)
        if owner is not None:
            attributed[sym] = owner.qualified_name
            continue
        special_owner = source_owners.get(
            _template_special_member_owner_key(demangled or "")
        )
        if special_owner is not None:
            attributed[sym] = special_owner
    return attributed


def _attribute_allocator_interposer_exports(
    exported: set[str], unmatched: set[str]
) -> dict[str, str]:
    """Attribute oneTBB malloc proxy interposer exports to their proxy marker.

    ``libtbbmalloc_proxy`` intentionally exports allocator interposition entry
    points such as ``malloc``, ``free``, global ``operator new/delete`` and
    ``__libc_*`` hooks. These are not public-header declarations in the source
    surface, so source linking used to report every proxy hook as an orphan. Only
    enable this narrow classification when the library exports the explicit
    ``__TBB_malloc_proxy`` marker, which keeps ordinary libraries from hiding
    unrelated C/runtime symbols.
    """
    if _TBB_MALLOC_PROXY_MARKER not in exported:
        return {}
    attributed: dict[str, str] = {}
    for sym in unmatched:
        if (
            sym == _TBB_MALLOC_PROXY_MARKER
            or sym in _TBB_MALLOC_PROXY_C_SYMBOLS
            or _norm_itanium(sym) in _TBB_MALLOC_PROXY_CPP_SYMBOLS
        ):
            attributed[sym] = _TBB_MALLOC_PROXY_MARKER
    return attributed


def _classify_non_public_exports(
    unmatched: set[str], *, library: str = "", surface: SourceAbiSurface | None = None
) -> dict[str, str]:
    """Classify exported symbols that are not public-source declarations.

    This is an accounting tier, not source matching: the symbol is still not
    mapped to a public declaration. It separates dependency/internal/own exports
    from genuinely unclassified gaps so real-world reports can reach "fully
    accounted" coverage without pretending that stdlib/TBB/private implementation
    symbols are part of the project's public source API.
    """
    if not unmatched:
        return {}
    library_l = library.lower()
    source_roots = _source_namespace_roots(surface) if surface is not None else set()
    classified: dict[str, str] = {}
    for sym in sorted(unmatched):
        demangled = _best_effort_demangle(sym)
        if _is_stdlib_export(sym, demangled):
            classified[sym] = "dependency:stdlib"
        elif _is_tbb_export(sym, demangled) and "tbb" not in library_l:
            classified[sym] = "dependency:tbb"
        elif _is_internal_export(sym, demangled):
            classified[sym] = "internal_or_private_export"
        elif library and _belongs_to_source_namespace(demangled, source_roots):
            classified[sym] = "own_export_without_public_source_decl"
        elif library and source_roots and _is_cpp_export_without_public_decl(demangled):
            classified[sym] = "cpp_export_without_public_source_decl"
        elif library and source_roots and _is_c_export_without_public_decl(sym):
            classified[sym] = "c_export_without_public_source_decl"
    return classified


def _best_effort_demangle(sym: str) -> str:
    if not _looks_like_cpp_export(sym):
        return ""
    try:
        from ..demangle import demangle as _demangle

        return _demangle(_norm_itanium(sym)) or ""
    except Exception:  # noqa: BLE001 - classification is best-effort
        return ""


def _looks_like_cpp_export(sym: str) -> bool:
    return sym.startswith("_Z") or sym.startswith("__Z")


def _looks_like_c_export(sym: str) -> bool:
    return bool(sym) and not _looks_like_cpp_export(sym)


def _strip_synthesized_descriptor(demangled: str) -> str:
    """Strip a leading RTTI/vtable/thunk/guard descriptor so the *underlying*
    entity name can be origin-classified.

    A compiler-synthesized export demangles with a human descriptor prefix —
    ``"typeinfo for std::__detail::_AnyMatcher<...>"``, ``"guard variable for
    std::..."`` — so a naive ``startswith("std::")`` origin test misses it and the
    symbol lands in the generic ``cpp_export_without_public_source_decl`` bucket
    instead of ``dependency:stdlib``. Stripping the descriptor (``"typeinfo for
    std::..."`` -> ``"std::..."``) lets the stdlib/TBB origin checks see through
    the RTTI wrapper. A plain declaration (no descriptor) is returned unchanged.
    """
    for prefix, _kind, _owner in _SYNTHESIZED_PREFIXES:
        if demangled.startswith(prefix):
            return demangled[len(prefix) :]
    return demangled


def _is_stdlib_export(sym: str, demangled: str) -> bool:
    base = _strip_synthesized_descriptor(demangled)
    return (
        base.startswith("std::")
        or base.startswith("__gnu_cxx::")
        or sym.startswith(
            (
                "_ZSt",
                "_ZNSt",
                "_ZNKSt",
                "_ZTVSt",
                "_ZTISt",
                "_ZTSSt",
                # RTTI/VTT for *nested* std types mangle as ``_ZT?NSt...`` (the
                # top-level ``_ZT?St...`` forms above only catch ``std::`` at the
                # outermost level); these back up the demangled check above when
                # the demangler is unavailable. Guard variables have too many
                # mangled shapes (``_ZGVN...``/``_ZGVZN...``/``_ZGVZNK...``) to
                # enumerate reliably, so they ride the demangled ``guard variable
                # for std::...`` path above.
                "_ZTVNSt",
                "_ZTINSt",
                "_ZTSNSt",
                "_ZTTNSt",
                "_ZN9__gnu_cxx",
                "_ZNK9__gnu_cxx",
                "_ZTVN9__gnu_cxx",
                "_ZTIN9__gnu_cxx",
                "_ZTSN9__gnu_cxx",
            )
        )
    )


def _is_tbb_export(sym: str, demangled: str) -> bool:
    base = _strip_synthesized_descriptor(demangled)
    return base.startswith("tbb::") or sym.startswith(
        ("_ZN3tbb", "_ZTVN3tbb", "_ZTIN3tbb", "_ZTSN3tbb", "_ZTTN3tbb")
    )


def _is_internal_export(sym: str, demangled: str) -> bool:
    if sym.startswith(("_", "__")) and not _looks_like_cpp_export(sym):
        return True
    if not demangled:
        return False
    qualified = _strip_call_signature(demangled)
    leaf = qualified.rsplit("::", 1)[-1]
    return (
        "::detail::" in qualified
        or "::internal::" in qualified
        or leaf.endswith("_impl")
        or leaf.startswith("_daal_")
        or leaf.startswith("_")
    )


def _is_cpp_export_without_public_decl(demangled: str) -> bool:
    """Best-effort bucket for C++ exports with no public source declaration.

    This is only used when the linked surface has project namespace evidence and a
    concrete library name. It keeps accidental/private C++ exports visible as a
    separate reason without pretending they matched a public declaration.
    """
    if not demangled:
        return False
    qualified = _strip_call_signature(demangled)
    return bool(qualified)


def _is_c_export_without_public_decl(sym: str) -> bool:
    """Best-effort bucket for C exports with no public source declaration."""
    return _looks_like_c_export(sym) and bool(sym)


def _source_namespace_roots(surface: SourceAbiSurface | None) -> set[str]:
    if surface is None:
        return set()
    roots: set[str] = set()
    for bucket in surface.reachable_buckets().values():
        for entity in bucket:
            name = entity.qualified_name
            if not name or "::" not in name:
                continue
            root = name.split("::", 1)[0]
            if root not in {"std", "__gnu_cxx"}:
                roots.add(root)
    return roots


def _belongs_to_source_namespace(demangled: str, roots: set[str]) -> bool:
    if not demangled or "::" not in demangled or not roots:
        return False
    return demangled.split("::", 1)[0] in roots


#: Entity kinds routed to each reachable bucket of the linked surface (D5).
_TYPE_KINDS = frozenset({"record", "enum", "typedef", "union"})
_MACRO_KINDS = frozenset({"macro"})
_TEMPLATE_KINDS = frozenset({"template"})
_INLINE_KINDS = frozenset({"inline"})
#: Everything else (function/method/variable/constexpr) is a declaration.

#: Visibility values that put an entity on the public source surface.
_PUBLIC_VISIBILITY = frozenset({"public_header", "generated"})


def _is_public(entity: SourceEntity) -> bool:
    """Whether an entity belongs to the public source surface (D5 roots).

    An entity is public when it is API-relevant and either declared in a public
    (or generated public) header, or its origin marks it as a public header.
    """
    if not entity.api_relevant:
        return False
    if entity.visibility in _PUBLIC_VISIBILITY:
        return True
    loc = entity.source_location
    return bool(loc and loc.origin in ("PUBLIC_HEADER", "GENERATED"))


def link_source_abi(
    tus: Iterable[SourceAbiTu],
    *,
    exported_symbols: Iterable[str] = (),
    library: str = "",
    target_id: str = "",
    forced_public: Iterable[str] = (),
) -> SourceAbiSurface:
    """Link per-TU dumps into one library source ABI surface (ADR-030 D5).

    ``exported_symbols`` are the L0 dynamic exports (mangled names). A public
    source declaration that maps to one of them is shipped; one that does not is
    recorded under ``unmatched.decls_without_symbol`` and mapped to ``""`` so the
    diff can later flag a lost mapping (``source_decl_binary_symbol_mismatch``).
    ``forced_public`` names declarations the policy forces onto the surface even
    without a public-header origin.
    """
    exported = set(exported_symbols)
    forced = set(forced_public)
    surface = SourceAbiSurface(library=library, target_id=target_id)
    surface.roots["exported_symbols"] = sorted(exported)
    surface.roots["forced_public"] = sorted(forced)

    state = _LinkState()
    state.export_index = _build_export_index(exported)
    state.exact_index = _build_exact_index(exported)
    for tu in tus:
        for header in tu.public_header_roots:
            surface.mappings["public_header_to_target"][header] = (
                tu.target_id or target_id
            )
        for entity in tu.all_entities():
            if not (_is_public(entity) or entity.qualified_name in forced):
                continue
            state.public_decl_ids.append(entity.id)
            _route_entity(entity, surface, state, exported)

    surface.roots["public_header_declarations"] = sorted(set(state.public_decl_ids))
    # Second-tier: rescue decls whose mangled name differs textually from the
    # export (ABI-tag / substitution drift) via demangled identity.
    _demangled_rematch(
        surface.reachable_declarations,
        state.decl_to_symbol,
        state.matched_symbols,
        exported,
    )
    surface.mappings["source_decl_to_binary_symbol"] = dict(
        sorted(state.decl_to_symbol.items())
    )
    surface.odr_conflicts = state.odr_conflicts

    # Attribute compiler-synthesized exports (vtable/typeinfo/thunk/guard) to their
    # owning public type/function so they are not miscounted as "exported but no
    # source decl" (ADR-030 D5). These are matched to a *type/function*, not a
    # free decl, so they are tracked separately from decl matches.
    decl_matched = set(state.matched_symbols)
    synthesized = _attribute_synthesized_exports(surface, exported - decl_matched)
    if synthesized:
        surface.mappings["synthesized_symbol_to_owner"] = {
            sym: {"kind": kind, "owner": owner}
            for sym, (kind, owner) in sorted(synthesized.items())
        }
    template_instantiations = _template_instantiation_attribution(
        surface,
        surface.reachable_declarations,
        decl_matched | set(synthesized),
        exported,
    )
    if template_instantiations:
        surface.mappings["template_instantiation_symbol_to_decl"] = dict(
            sorted(template_instantiations.items())
        )
    allocator_interposers = _attribute_allocator_interposer_exports(
        exported, exported - decl_matched - set(synthesized) - set(template_instantiations)
    )
    if allocator_interposers:
        surface.mappings["allocator_interposer_symbol_to_owner"] = dict(
            sorted(allocator_interposers.items())
        )
    non_public = _classify_non_public_exports(
        exported
        - decl_matched
        - set(synthesized)
        - set(template_instantiations)
        - set(allocator_interposers),
        library=library,
        surface=surface,
    )
    if non_public:
        surface.mappings["non_public_symbol_to_reason"] = dict(
            sorted(non_public.items())
        )
    all_matched = (
        decl_matched
        | set(synthesized)
        | set(template_instantiations)
        | set(allocator_interposers)
        | set(non_public)
    )

    surface.unmatched["symbols_without_decl"] = sorted(exported - all_matched)
    surface.unmatched["decls_without_symbol"] = sorted(
        state.identity_to_qname.get(key, key)
        for key, sym in state.decl_to_symbol.items()
        if not sym
    )
    surface.coverage = {
        "reachable_declarations": len(surface.reachable_declarations),
        "reachable_types": len(surface.reachable_types),
        "reachable_macros": len(surface.reachable_macros),
        "reachable_templates": len(surface.reachable_templates),
        "reachable_inline_bodies": len(surface.reachable_inline_bodies),
        "exported_symbols": len(exported),
        # Honest breakdown of the export denominator (ADR-030 D5): decl matches vs
        # synthesized (RTTI/vtable/thunk) attributions vs the genuine remainder.
        "matched_symbols": len(decl_matched),
        "synthesized_symbols_matched": len(synthesized),
        "template_instantiation_symbols_matched": len(template_instantiations),
        "allocator_interposer_symbols_matched": len(allocator_interposers),
        "non_public_symbols_classified": len(non_public),
        "unmatched_symbols": len(exported) - len(all_matched),
        "odr_conflicts": len(state.odr_conflicts),
    }
    return surface


def relink_surface_exports(
    surface: SourceAbiSurface, exported_symbols: Iterable[str]
) -> SourceAbiSurface:
    """Re-derive a linked surface's L0-export mapping against a new export set.

    The parallel-baseline ``merge`` flow links the source surface with no binary
    present, so its ``source_decl_to_binary_symbol`` mapping is all-misses and the
    provenance/mapping checks are inert. Given the binary side's exported symbols,
    recompute ``roots['exported_symbols']`` and the decl→symbol mapping in place
    from the already-recorded public declarations — using exactly the same rule
    as :func:`link_source_abi` (``mangled_name or qualified_name`` matched against
    the export set), so the result is identical to what ``dump <binary> --sources``
    would have produced and introduces no new behaviour. Mutates and returns
    *surface*.
    """
    exported = set(exported_symbols)
    surface.roots["exported_symbols"] = sorted(exported)
    export_index = _build_export_index(exported)
    exact_index = _build_exact_index(exported)
    mapping: dict[str, str] = {}
    matched: set[str] = set()
    # identity -> display name, so the recomputed decls_without_symbol carries the
    # same qualified-name labels the original link produced rather than raw keys.
    identity_to_qname: dict[str, str] = {}
    for entity in surface.reachable_declarations:
        key = entity.identity()
        if not key:
            continue
        identity_to_qname[key] = entity.qualified_name or key
        export_sym = entity.mangled_name or entity.qualified_name
        primary, variants = _match_export(
            export_sym, exported, export_index, exact_index
        )
        if primary:
            mapping[key] = primary
            matched.update(variants)
        else:
            mapping.setdefault(key, "")
    # Second-tier demangled-identity rematch (ABI-tag / substitution drift).
    _demangled_rematch(surface.reachable_declarations, mapping, matched, exported)
    surface.mappings["source_decl_to_binary_symbol"] = dict(sorted(mapping.items()))

    # Attribute compiler-synthesized exports (vtable/typeinfo/thunk/guard) to their
    # owning public type/function — same as link_source_abi, so the merge/relink
    # flow (used by `merge` on a plugin/wrapper pack) reports the same honest
    # counts as `dump <binary> --sources`.
    synthesized = _attribute_synthesized_exports(surface, exported - matched)
    # Assign UNCONDITIONALLY (empty dict when nothing was attributed): a relink runs
    # against a possibly different export set, so a surface that previously recorded
    # synthesized owners but has none under the new exports must have the stale map
    # cleared — else the serialized L4 surface keeps claiming ownership of vtables/
    # typeinfo the new binary never exports, contradicting the recomputed coverage
    # and symbols_without_decl (Codex review).
    surface.mappings["synthesized_symbol_to_owner"] = {
        sym: {"kind": kind, "owner": owner}
        for sym, (kind, owner) in sorted(synthesized.items())
    }
    template_instantiations = _template_instantiation_attribution(
        surface,
        surface.reachable_declarations,
        matched | set(synthesized),
        exported,
    )
    surface.mappings["template_instantiation_symbol_to_decl"] = dict(
        sorted(template_instantiations.items())
    )
    allocator_interposers = _attribute_allocator_interposer_exports(
        exported, exported - matched - set(synthesized) - set(template_instantiations)
    )
    surface.mappings["allocator_interposer_symbol_to_owner"] = dict(
        sorted(allocator_interposers.items())
    )
    non_public = _classify_non_public_exports(
        exported
        - matched
        - set(synthesized)
        - set(template_instantiations)
        - set(allocator_interposers),
        library=surface.library,
        surface=surface,
    )
    surface.mappings["non_public_symbol_to_reason"] = dict(sorted(non_public.items()))
    all_matched = (
        matched | set(synthesized) | set(template_instantiations) | set(allocator_interposers)
        | set(non_public)
    )

    surface.unmatched["symbols_without_decl"] = sorted(exported - all_matched)
    # Recompute decls_without_symbol from the new mapping: declarations that now
    # resolve to an export must drop out of the unmatched list, or the merged
    # surface would serialize contradictory facts (mapping says foo->foo while
    # decls_without_symbol still reports foo as unmatched).
    surface.unmatched["decls_without_symbol"] = sorted(
        identity_to_qname.get(key, key) for key, sym in mapping.items() if not sym
    )
    if isinstance(surface.coverage, dict):
        surface.coverage["exported_symbols"] = len(exported)
        surface.coverage["matched_symbols"] = len(matched)
        surface.coverage["synthesized_symbols_matched"] = len(synthesized)
        surface.coverage["template_instantiation_symbols_matched"] = len(
            template_instantiations
        )
        surface.coverage["allocator_interposer_symbols_matched"] = len(
            allocator_interposers
        )
        surface.coverage["non_public_symbols_classified"] = len(non_public)
        surface.coverage["unmatched_symbols"] = len(exported) - len(all_matched)
    return surface


@dataclass
class _LinkState:
    """Mutable accumulators threaded through the per-entity routing helpers."""

    decl_to_symbol: dict[str, str] = field(
        default_factory=dict
    )  # identity -> symbol ("" if none)
    identity_to_qname: dict[str, str] = field(
        default_factory=dict
    )  # identity -> qualified_name
    # (qualified_name, declaring header) -> type_hash, for ODR detection. The
    # declaring header is part of the key because castxml reports a bare type
    # name (namespace lives in the XML `context`), so a::Widget and b::Widget
    # would otherwise collide into a false odr_source_conflict.
    type_by_name: dict[tuple[str, str], str] = field(default_factory=dict)
    odr_conflicts: list[dict[str, str]] = field(default_factory=list)
    public_decl_ids: list[str] = field(default_factory=list)
    matched_symbols: set[str] = field(default_factory=set)
    #: ctor/dtor canonical form -> exported clone symbols (see _build_export_index)
    export_index: dict[str, list[str]] = field(default_factory=dict)
    #: Mach-O-normalized exact key -> real exported spelling (see _build_exact_index)
    exact_index: dict[str, str] = field(default_factory=dict)


def _route_entity(
    entity: SourceEntity,
    surface: SourceAbiSurface,
    state: _LinkState,
    exported: set[str],
) -> None:
    """Place one public entity into the right reachable bucket of the surface."""
    if entity.kind in _TYPE_KINDS:
        _route_type(entity, surface, state)
    elif entity.kind in _MACRO_KINDS:
        surface.reachable_macros.append(entity)
    elif entity.kind in _TEMPLATE_KINDS:
        surface.reachable_templates.append(entity)
    elif entity.kind in _INLINE_KINDS:
        surface.reachable_inline_bodies.append(entity)
    else:
        _route_declaration(entity, surface, state, exported)


def _route_type(
    entity: SourceEntity, surface: SourceAbiSurface, state: _LinkState
) -> None:
    """Record a type entity and detect same-name/different-hash ODR conflicts (D5)."""
    surface.reachable_types.append(entity)
    if not entity.qualified_name:
        return
    # Typedefs are kept out of the ODR / source_type_to_debug_type path: a common
    # C self-alias `typedef struct Foo Foo;` (and anonymous-struct typedefs) shares
    # its `(qualified_name, header)` with the `record` the same header defines, so
    # routing the typedef here would collide with that record and emit a spurious
    # odr_source_conflict on an unchanged header (Codex review). Typedef target
    # changes are still surfaced by source_diff._diff_typedefs, which keys by
    # entity identity, so dropping them from the ODR/type-mapping path loses no
    # detection.
    if entity.kind == "typedef":
        return
    # Key ODR detection by (name, declaring header) so same-named types in
    # different namespaces/headers (a::Widget vs b::Widget, which castxml emits
    # with the bare name) don't conflate into a false odr_source_conflict. A
    # genuine ODR conflict (one type, two TUs, divergent definitions) shares
    # both name and header, so it still fires.
    header = entity.source_location.path if entity.source_location else ""
    key = (entity.qualified_name, header)
    prev = state.type_by_name.get(key)
    if prev is not None and prev != entity.type_hash:
        state.odr_conflicts.append(
            {
                "qualified_name": entity.qualified_name,
                # The declaring header is part of the conflict's identity (ODR is
                # keyed by (name, header) above), so the diff can tell a new
                # conflict for a same-named type in a *different* header apart
                # from one already present elsewhere.
                "header": header,
                "old_type_hash": prev,
                "new_type_hash": entity.type_hash,
            }
        )
    else:
        state.type_by_name[key] = entity.type_hash
    surface.mappings["source_type_to_debug_type"][entity.qualified_name] = (
        entity.type_hash
    )


def _route_declaration(
    entity: SourceEntity,
    surface: SourceAbiSurface,
    state: _LinkState,
    exported: set[str],
) -> None:
    """Record a declaration and map it to its exported binary symbol (D5).

    Keyed by the entity's stable identity (mangled name when present), not the
    bare qualified name, so C++ overloads sharing one name (f(int) vs f(double))
    keep independent mappings. The exported symbol is the mangled name for C++ or
    the plain qualified name for C / extern "C" decls whose extractor leaves
    mangled_name empty — matching on either avoids false "unmatched" evidence.
    """
    surface.reachable_declarations.append(entity)
    key = entity.identity()
    if not key:
        return
    state.identity_to_qname[key] = entity.qualified_name or key
    export_sym = entity.mangled_name or entity.qualified_name
    primary, variants = _match_export(
        export_sym, exported, state.export_index, state.exact_index
    )
    if primary:
        state.decl_to_symbol[key] = primary
        state.matched_symbols.update(variants)
    else:
        state.decl_to_symbol.setdefault(key, "")
