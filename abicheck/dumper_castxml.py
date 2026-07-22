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

"""castxml XML → ABI model parser.

Split from ``dumper.py`` to keep that module under the AI-readiness file-size
soft cap. Re-exported from ``abicheck.dumper`` so existing imports of
``_CastxmlParser``, ``_parse_vtable_index``, and ``_vt_sort_key`` from
``abicheck.dumper`` keep working.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from xml.etree.ElementTree import (
    Element,  # type annotation only; parsing uses defusedxml
)

from .model import (
    AccessLevel,
    EnumMember,
    EnumType,
    Function,
    Param,
    RecordType,
    ScopeOrigin,
    TypeField,
    Variable,
    Visibility,
)
from .provenance import build_public_set, classify_origin, header_from_location

#: Base names of semantic contract / calling-convention attributes worth
#: diffing. castxml passes GNU attributes through its compound ``attributes``
#: string, optionally prefixed (``gnu:nonnull(1)``); arguments are kept as
#: part of the normalized token so ``nonnull(1)`` → ``nonnull(2)`` is a change.
_CONTRACT_ATTRIBUTE_BASES = frozenset(
    {
        "noreturn",
        "nonnull",
        "returns_nonnull",
        "malloc",
        "format",
        "format_arg",
        "alloc_size",
        "alloc_align",
        "warn_unused_result",
        "sentinel",
        # calling-convention selections — a flip is an ABI change on the
        # affected targets, reported via the contract-attribute kinds.
        "cdecl",
        "stdcall",
        "fastcall",
        "thiscall",
        "regparm",
        "ms_abi",
        "sysv_abi",
        "vectorcall",
    }
)


def _extract_contract_attributes(attributes: str) -> list[str]:
    """Filter a castxml ``attributes`` string down to contract attributes.

    Returns normalized, sorted tokens with any ``gnu:``/``gnu::`` namespace
    prefix stripped and argument lists preserved (``nonnull(1)``). Tokens not
    in the known contract set (``noexcept``, ``final``, …) are ignored.
    """
    tokens: set[str] = set()
    for raw in attributes.split():
        token = raw
        for prefix in ("gnu::", "gnu:", "__"):
            if token.startswith(prefix):
                token = token[len(prefix) :]
        token = token.strip("_")
        base = token.split("(", 1)[0]
        if base in _CONTRACT_ATTRIBUTE_BASES:
            tokens.add(token)
    return sorted(tokens)


def _deprecation_marker(el: Element) -> str | None:
    """Deprecation message for *el*, or ``None`` if not deprecated.

    castxml's ``GetDeclAttributes`` (``Output.cxx``) always adds a bare
    ``"deprecated"`` token to the compound ``attributes`` string when
    ``DeprecatedAttr`` is present, but only emits the dedicated
    ``deprecation="..."`` XML attribute when the attribute carries a
    non-empty message. A BARE ``[[deprecated]]``/
    ``__attribute__((deprecated))`` (no message) therefore has NO
    ``deprecation`` attribute at all — reading only ``el.get("deprecation")``
    missed every messageless deprecation (Codex review, PR #582, confirmed
    against castxml's own source). Falls back to ``""`` (deprecated, no
    message) when the bare token is present in ``attributes`` instead.
    """
    msg = el.get("deprecation")
    if msg is not None:
        return msg
    if re.search(r"\bdeprecated\b", el.get("attributes", "")):
        return ""
    return None


def _parse_vtable_index(vi_str: str | None) -> int | None:
    """Parse vtable_index attribute, returning None for missing/invalid values."""
    if vi_str is None:
        return None
    stripped = vi_str.lstrip("-")
    return int(vi_str) if stripped.isdigit() else None


def _vt_sort_key(item: tuple[int | None, str]) -> tuple[int, int]:
    vi, _ = item
    return (0, vi) if vi is not None else (1, 0)


# Itanium <nested-name> ::= N [<CV-qualifiers: r/V/K>] [<ref-qualifier: R|O>] …
# At this position an uppercase R/O is unambiguous: prefix components start
# with a digit (source-name), S (substitution), T (template param), or a
# lowercase operator code — never a bare R/O.
_MANGLED_REF_QUAL = re.compile(r"^_ZN[rVK]*([RO])")


def _ref_qualifier_from_mangled(mangled: str) -> str:
    """Recover a member function's &/&& ref-qualifier from its Itanium mangling."""
    m = _MANGLED_REF_QUAL.match(mangled)
    if m is None:
        return ""
    return "&" if m.group(1) == "R" else "&&"


_MANGLED_SOURCE_NAME = re.compile(r"\d+")


def _mangled_name_is_local_linkage(mangled: str) -> bool:
    """Detect the Itanium ``<local-name>``/internal-linkage marker: a bare
    ``L`` immediately before the final component's length-prefixed
    source-name (e.g. ``_ZN5mylibL12hidden_constE`` for a non-``extern``
    namespace-scope ``const``/``constexpr`` variable).

    Parses the length-prefixed identifier chain component-by-component
    (jumping exactly ``length`` characters per source-name) rather than
    substring-matching for a literal ``L`` — a namespace or class name that
    merely *ends* in the letter ``L`` (e.g. ``MODEL``) is consumed as a whole
    source-name and never mistaken for the marker, since the parser always
    re-synchronizes on the next length-prefix digit run rather than rescanning
    already-consumed identifier characters.

    Returns ``False`` (not detected as local) on anything this simple
    single-source-name walker doesn't recognize (templates, operators, …) —
    a safe default, since the caller only uses this to rule OUT a public-CPO
    fallback, not to affirmatively hide something.
    """
    if not mangled.startswith("_Z"):
        return False
    i = 2
    n = len(mangled)
    if i < n and mangled[i] == "N":
        i += 1
    while i < n:
        local = mangled[i] == "L"
        if local:
            i += 1
        m = _MANGLED_SOURCE_NAME.match(mangled, i)
        if not m:
            return False
        length = int(m.group())
        i = m.end() + length
        if i > n:
            return False
        if local:
            return True
        if i < n and mangled[i] == "E":
            return False
    return False


#: Prefix marking a snapshot key synthesized for a constructor overload whose
#: real mangled name castxml omitted (see ``_CastxmlParser._function_mangled_name``).
#: It is intentionally not a real ABI symbol, only a stable per-overload
#: identity — ``diff_symbols._public_functions()`` reads this to exempt such
#: entries from its ELF-export-set narrowing, which they could never pass (the
#: key has no real exported symbol to match).
SYNTHETIC_CTOR_KEY_PREFIX = "__abicheck_ctor__"


def is_synthetic_ctor_key(key: str) -> bool:
    """Whether *key* is a castxml constructor-overload synthetic identity."""
    return key.startswith(SYNTHETIC_CTOR_KEY_PREFIX)


#: Marker for a snapshot key synthesized for a destructor whose real mangled
#: name castxml omitted (see ``_CastxmlParser._function_display_name`` and
#: ``_function_mangled_name``'s ``return name`` fallback). A class has at
#: most one destructor, so — unlike constructors — no per-overload prefix is
#: needed: the synthesized "~ClassName" display name is itself already a
#: stable, unique identity. It is intentionally not a real ABI symbol (a real
#: Itanium destructor mangling always starts with ``_Z``, never ``~``), only
#: a stable key — ``diff_symbols._public_functions()`` reads this the same
#: way it already does :data:`SYNTHETIC_CTOR_KEY_PREFIX`/
#: :func:`is_synthetic_ctor_key`, to exempt such entries from its
#: ELF-export-set narrowing, which they could never pass. Without this, a
#: real virtual destructor's PUBLIC visibility (``_ctor_or_dtor_visibility``)
#: was necessary but not sufficient: it would still be silently dropped
#: before reaching the diff whenever ELF metadata is present (Codex review,
#: PR #582 — found after the destructor-visibility fix, via the same Phase 2
#: parity gate).
_SYNTHETIC_DTOR_KEY_PREFIX = "~"


def is_synthetic_dtor_key(key: str) -> bool:
    """Whether *key* is a castxml destructor synthetic identity."""
    return key.startswith(_SYNTHETIC_DTOR_KEY_PREFIX)


class _CastxmlParser:
    """Parse castxml XML into ABI model objects."""

    def __init__(
        self,
        root: Element,
        exported_dynamic: set[str],
        exported_static: set[str],
        public_header_paths: list[str] | None = None,
        public_dir_paths: list[str] | None = None,
    ):
        self._root = root
        self._exported_dynamic = exported_dynamic
        self._exported_static = exported_static
        # Public-header surface used to scope constant extraction
        # (parse_constants). Seeded from the parsed headers (-H/--header) plus
        # any explicit --public-header / --public-header-dir inputs, and matched
        # with the same provenance segment logic used elsewhere — so constants
        # reached via an umbrella header or a public include dir are kept, while
        # transitively-included system/private-header constants are excluded.
        # Empty → constant extraction is skipped (provenance is opt-in).
        (self._pub_header_segs, self._pub_dir_segs, self._have_public_set) = (
            build_public_set(
                public_header_paths,
                public_dir_paths,
            )
        )
        self._id_map: dict[str, Element] = {}
        self._virtual_methods_by_class: dict[str, list[Element]] = {}
        self._source_lines_cache: dict[str, list[str]] = {}
        # Tag-grouped elements populated by the single pass in _build_id_map()
        # below, so parse_functions()/parse_types()/etc. below don't each
        # re-scan every top-level element themselves.
        self._function_els: list[Element] = []
        self._variable_els: list[Element] = []
        self._record_els: list[Element] = []
        self._enum_els: list[Element] = []
        self._typedef_els: list[Element] = []
        # Per-id memoization for the recursive type-graph resolvers below;
        # safe since the XML tree is immutable for this parser instance.
        self._type_name_cache: dict[str, str] = {}
        self._pointer_depth_cache: dict[str, int] = {}
        # method element id -> canonical vtable-slot key, resolved through any
        # `overrides` chain. Populated lazily by _collect_virtual_methods(); see
        # its docstring for why this is needed alongside vtable_index.
        self._vtable_slot_root: dict[str, int | str] = {}
        # method element id -> any ADDITIONAL slot keys beyond the primary one
        # in _vtable_slot_root, for a method that itself overrides more than
        # one base slot (non-virtual multiple inheritance). A further-derived
        # override referencing this id by `overrides` must propagate to every
        # one of these, not just the primary -- see _collect_virtual_methods.
        self._vtable_slot_extra_roots: dict[str, list[int | str]] = {}
        self._build_id_map()

    def _build_id_map(self) -> None:
        # Single pass building the id map, the virtual-method index, and the
        # tag-grouped element lists parse_functions()/parse_types()/etc. use.
        for el in self._root:
            eid = el.get("id")
            if eid:
                self._id_map[eid] = el
            tag = el.tag
            if tag in ("Method", "Destructor") and el.get("virtual") == "1":
                ctx = el.get("context")
                if ctx:
                    self._virtual_methods_by_class.setdefault(ctx, []).append(el)
            if tag in self._FUNCTION_TAGS:
                self._function_els.append(el)
            elif tag == "Variable":
                self._variable_els.append(el)
            elif tag in ("Struct", "Class", "Union"):
                self._record_els.append(el)
            elif tag == "Enumeration":
                self._enum_els.append(el)
            elif tag == "Typedef":
                self._typedef_els.append(el)

    def _resolve(self, id_: str) -> Element | None:
        return self._id_map.get(id_)

    def _source_line_has_explicit(
        self,
        loc_el: Element | None,
        declaration_el: Element | None = None,
    ) -> bool | None:
        """Fallback for castxml Converter nodes that omit explicit="1"."""
        if loc_el is not None:
            file_id = loc_el.get("file", "")
            line_raw = loc_el.get("line", "")
        elif declaration_el is not None:
            file_id = declaration_el.get("file", "")
            line_raw = declaration_el.get("line", "")
        else:
            return None
        file_el = self._id_map.get(file_id)
        if file_el is None:
            return None
        fname = file_el.get("name", "")
        if not fname or not line_raw:
            return None
        try:
            line_no = int(line_raw)
            lines = self._source_lines_cache.get(fname)
            if lines is None:
                lines = Path(fname).read_text(encoding="utf-8").splitlines()
                self._source_lines_cache[fname] = lines
        except (OSError, UnicodeDecodeError, ValueError, IndexError):
            return None
        # CastXML can point a split conversion operator at the ``operator``
        # line, while the ``explicit`` keyword is on the preceding line.
        start = max(0, line_no - 4)
        window_parts: list[str] = []
        for line in lines[start : min(len(lines), line_no + 5)]:
            window_parts.append(line.strip())
            if line_no - 1 <= start + len(window_parts) - 1 and (
                ";" in line or "{" in line
            ):
                break
        window = " ".join(window_parts)
        operator_match = re.search(r"\boperator\b", window)
        if operator_match is None:
            return False
        prefix = window[: operator_match.start()]
        declaration_start = max(prefix.rfind(";"), prefix.rfind("{"), prefix.rfind("}"))
        return bool(re.search(r"\bexplicit\b", prefix[declaration_start + 1 :]))

    def _type_name(self, id_: str, depth: int = 0) -> str:
        # Memoized by id alone (not depth): the same type id is commonly
        # resolved from thousands of call sites on a large ABI surface. A
        # depth-capped ("?") result is never cached, so reaching the same id
        # again within budget still resolves it properly.
        cached = self._type_name_cache.get(id_)
        if cached is not None:
            return cached
        result = self._type_name_uncached(id_, depth)
        if depth <= 10:
            self._type_name_cache[id_] = result
        return result

    def _type_name_uncached(self, id_: str, depth: int = 0) -> str:
        if depth > 10:
            return "?"
        el = self._resolve(id_)
        if el is None:
            return "?"
        tag = el.tag
        if tag in ("FundamentalType", "Enumeration"):
            return el.get("name", "?")
        if tag == "PointerType":
            return self._type_name(el.get("type", ""), depth + 1) + "*"
        if tag == "ReferenceType":
            return self._type_name(el.get("type", ""), depth + 1) + "&"
        if tag == "RValueReferenceType":
            return self._type_name(el.get("type", ""), depth + 1) + "&&"
        if tag == "CvQualifiedType":
            inner_id = el.get("type", "")
            base = self._type_name(inner_id, depth + 1)
            # castxml's CvQualifiedType also carries `volatile`; only `const`
            # was read here previously, so a volatile-qualified type's name
            # silently dropped it instead of just missing a dedicated
            # attribute (unlike the genuinely-unmodelable Atomic case below).
            # Order matches the "const volatile" spelling convention already
            # used by the DWARF backend's own qualifier stripping
            # (dwarf_snapshot._strip_type_decorators).
            #
            # Deliberately NOT `restrict` here (Codex review, PR #582):
            # unlike const/volatile — which are real signature-level
            # qualifiers on a pointee position and participate in mangling —
            # `restrict` has zero ABI/mangling effect and is already tracked
            # as its own compatible-classified fact (Param.is_restrict /
            # PARAM_RESTRICT_CHANGED, populated in _parse_function_params
            # via _resolve_cv_restrict below). Folding it into the generic
            # type-name spelling would make a restrict-only parameter change
            # look like an ordinary type mismatch and misfire the BREAKING
            # ``FUNC_PARAMS_CHANGED`` generic-type-diff path instead of the
            # dedicated compatible one.
            quals = [
                q
                for q, attr in (("const", "const"), ("volatile", "volatile"))
                if el.get(attr) == "1"
            ]
            if not quals:
                return base
            qual_str = " ".join(quals)
            # A CvQualifiedType directly wrapping a Pointer/Reference type
            # qualifies the pointer/reference VALUE itself (`int *
            # volatile`), not what it points to (`volatile int *`) — two
            # genuinely different declarations that a plain prefix always
            # collapsed to the identical spelling (G28 "known, deferred
            # limitation": confirmed via CodeRabbit review, PR #582). Render
            # the value-qualifier as a suffix instead, matching the "T *
            # const" convention cv_qualifiers_only_differ/
            # canonicalize_type_name already treat as canonical for this
            # case. A pointee-position qualifier (`const int *` —
            # PointerType wrapping CvQualifiedType) is unaffected: this
            # branch never sees it, since it fires from the CvQualifiedType
            # side, not the PointerType side. Deliberately NOT extended
            # through Typedef/ElaboratedType aliasing — see
            # _cv_qualifies_pointer_value's docstring (Codex review): the
            # clang backend takes clang's own `qualType` spelling verbatim,
            # which does not relocate a qualifier through an alias either,
            # so doing so here would newly diverge from clang on that case.
            if self._cv_qualifies_pointer_value(inner_id):
                return f"{base} {qual_str}"
            return f"{qual_str} {base}"
        if tag == "ElaboratedType":
            # castxml wraps an elaborated-type-specifier (`struct Foo`, `union
            # Foo`, `enum Foo` used directly rather than via a typedef) in an
            # ElaboratedType node with no `name` attribute of its own — resolve
            # through to the real underlying type instead of falling through to
            # the `tag` fallback below (which would literally return
            # "ElaboratedType").
            return self._type_name(el.get("type", ""), depth + 1)
        if tag in ("Struct", "Class", "Union"):
            return el.get("name", "?")
        if tag == "Typedef":
            return el.get("name", "?")
        if tag == "ArrayType":
            max_ = el.get("max", "")
            base = self._type_name(el.get("type", ""), depth + 1)
            return f"{base}[{max_}]" if max_ else f"{base}[]"
        if tag == "Unimplemented" and el.get("type_class") == "Atomic":
            # castxml cannot model C11 _Atomic: it emits a bare Unimplemented
            # node with no `type` reference to the wrapped type at all, so the
            # inner type name can't be recovered here. Spell it "_Atomic" (not
            # the literal tag name) so diff_atomic.py's _has_atomic() can still
            # detect the qualifier being added/removed on this slot.
            return "_Atomic"
        return el.get("name", tag)

    def _cv_qualifies_pointer_value(self, type_id: str) -> bool:
        """True if a ``CvQualifiedType`` wrapping *type_id* qualifies a
        pointer/reference VALUE rather than pointee data.

        Deliberately does NOT follow ``Typedef``/``ElaboratedType`` aliasing
        (Codex review): the clang backend's type spelling is clang's own
        ``qualType`` pretty-print, taken verbatim rather than re-derived —
        and clang's printer does not "see through" a typedef to relocate a
        qualifier after an implicit, textually-absent ``*`` either. For
        ``typedef int *IntPtr; volatile IntPtr x;``, clang spells it
        ``"volatile IntPtr"`` (prefix), not ``"IntPtr volatile"``. Following
        the typedef here to detect the aliased pointer and render a suffix
        would make castxml diverge from clang specifically on this case,
        even though both agreed (by prefixing) before this qualifier-suffix
        fix existed. Since the alias name itself carries no visible ``*``/
        ``&`` to move a qualifier around, there is no real prefix-vs-suffix
        ambiguity to resolve for it anyway (unlike a directly-spelled
        pointer) — only a DIRECT wrap is unambiguous and worth fixing.
        """
        el = self._resolve(type_id)
        if el is None:
            return False
        return el.tag in ("PointerType", "ReferenceType", "RValueReferenceType")

    def _type_alignment_bits(self, id_: str, depth: int = 0) -> int | None:
        """Natural (computed) alignment in bits for a type id, if castxml exposes it.

        Distinct from a Variable's own ``align`` attribute (an *explicit*
        alignas/``__attribute__((aligned))`` override on the declaration —
        see ``parse_variables``): this walks through cv-qualifiers, typedefs,
        elaborated-type wrappers, and array types to the underlying type node
        and reads its own ``align``, which castxml always populates with the
        compiler's actual computed alignment (the same attribute
        ``_build_record_type`` already trusts unconditionally for
        structs/unions/classes). ``ArrayType`` carries no ``align``/``size``
        of its own (confirmed empirically: an array's alignment is always its
        element type's) — recursing into its ``type`` is required, not just
        an optimization, or every exported array global would silently fall
        back to the same address-derived false-positive risk this method
        exists to close for scalars. Used as declared-alignment corroboration
        evidence for a plain variable with no explicit override, so
        ``_check_object_alignment_reduced`` isn't left with two ``None``s
        (and therefore no corroboration at all) for the overwhelming majority
        of exported globals that never carry an explicit alignment
        attribute.
        """
        if depth > 10 or not id_:
            return None
        el = self._resolve(id_)
        if el is None:
            return None
        if el.tag in ("CvQualifiedType", "Typedef", "ElaboratedType", "ArrayType"):
            return self._type_alignment_bits(el.get("type", ""), depth + 1)
        return self._optional_int_attr(el, "align")

    def _resolve_cv_restrict(self, id_: str, depth: int = 0) -> tuple[bool, bool, bool]:
        """Whether *id_*'s own (top-level) qualification is const/volatile/restrict.

        Walks the real XML type chain rather than pattern-matching the
        rendered ``_type_name`` spelling: a field or parameter declared
        through a ``Typedef`` whose target is itself cv-qualified (``typedef
        const int T; struct S { T x; };``) renders as the bare alias name
        ("T"), so a regex over the spelling can never see the qualifier
        behind it (Codex review, PR #582). ``ElaboratedType`` is followed for
        the same reason ``_type_name`` follows it. A further ``CvQualifiedType``
        or ``Typedef`` reached *through* one already-seen ``CvQualifiedType``
        combines in (rare, but e.g. two typedefs each adding one qualifier);
        any other tag (``PointerType`` chief among them) stops the walk so a
        *pointee*'s qualification is never attributed to the pointer/field
        itself — ``const int *`` is a non-const pointer to const int, not a
        const pointer.
        """
        if depth > 20 or not id_:
            return (False, False, False)
        el = self._resolve(id_)
        if el is None:
            return (False, False, False)
        if el.tag == "CvQualifiedType":
            const = el.get("const") == "1"
            volatile = el.get("volatile") == "1"
            restrict = el.get("restrict") == "1"
            inner_const, inner_volatile, inner_restrict = self._resolve_cv_restrict(
                el.get("type", ""), depth + 1
            )
            return (
                const or inner_const,
                volatile or inner_volatile,
                restrict or inner_restrict,
            )
        if el.tag in ("Typedef", "ElaboratedType"):
            return self._resolve_cv_restrict(el.get("type", ""), depth + 1)
        return (False, False, False)

    def _is_global_scope(self, el: Any) -> bool:
        """True if *el*'s immediate lexical context is the root ``::``
        namespace — i.e. not nested in any namespace or class.

        Every function-like element carries a ``context`` id; the file-level
        root ``Namespace`` element is the one with no ``context`` of its own
        (``name="::"``). A missing/unresolvable ``context`` is treated as
        global too (conservative default matching this method's callers,
        which only need to positively rule out namespace/class nesting).
        """
        ctx_id = el.get("context", "")
        if not ctx_id:
            return True
        ctx = self._resolve(ctx_id)
        if ctx is None:
            return True
        return ctx.tag == "Namespace" and not ctx.get("context")

    def _qualified_type_name(self, el: Any, leaf_name: str | None = None) -> str | None:
        """Namespace/enclosing-class-qualified name for a Struct/Class/Union
        element, or ``None`` if it's already at global scope (or a cycle /
        depth cap was hit).

        Walks castxml's ``context`` chain — each Struct/Class/Union/Namespace
        element points at its lexical parent via ``context`` — prepending each
        ancestor's name, stopping at the root ``Namespace`` (``name="::"``,
        which itself carries no ``context``). Used only where a real namespace
        path is required (internal-leak detection, SYCL-queue param matching);
        ``RecordType.name`` itself stays bare (see its docstring in model.py).
        """
        segments: list[str] = []
        seen_ids: set[str] = set()
        cur = el
        for _ in range(16):
            ctx_id = cur.get("context", "")
            if not ctx_id or ctx_id in seen_ids:
                break
            seen_ids.add(ctx_id)
            parent = self._resolve(ctx_id)
            if parent is None:
                break
            if parent.tag == "Namespace":
                pname = parent.get("name", "")
                if pname and pname != "::":
                    segments.append(pname)
                cur = parent
                continue
            if parent.tag in ("Struct", "Class", "Union"):
                pname = parent.get("name", "")
                if pname:
                    segments.append(pname)
                cur = parent
                continue
            break
        leaf = leaf_name if leaf_name is not None else el.get("name", "")
        if not segments or not leaf:
            return None
        segments.reverse()
        return "::".join([*segments, leaf])

    def _pointer_depth(self, id_: str, depth: int = 0) -> int:
        """Count pointer nesting depth: T=0, T*=1, T**=2, etc."""
        # Memoized by id alone, same rationale/safety as _type_name above.
        cached = self._pointer_depth_cache.get(id_)
        if cached is not None:
            return cached
        result = self._pointer_depth_uncached(id_, depth)
        if depth <= 10:
            self._pointer_depth_cache[id_] = result
        return result

    def _pointer_depth_uncached(self, id_: str, depth: int = 0) -> int:
        if depth > 10:
            return 0
        el = self._resolve(id_)
        if el is None:
            return 0
        if el.tag == "PointerType":
            return 1 + self._pointer_depth(el.get("type", ""), depth + 1)
        if el.tag in ("CvQualifiedType", "Typedef"):
            return self._pointer_depth(el.get("type", ""), depth + 1)
        return 0

    @staticmethod
    def _access_level(el: Element) -> AccessLevel:
        """Map castxml 'access' attribute to AccessLevel enum."""
        raw = el.get("access", "public")
        if raw == "protected":
            return AccessLevel.PROTECTED
        if raw == "private":
            return AccessLevel.PRIVATE
        return AccessLevel.PUBLIC

    def _visibility(self, mangled: str, name: str = "") -> Visibility:
        """Determine visibility based on ELF symbol tables."""
        # Check dynamic symbols (.dynsym) — truly exported
        if mangled and mangled in self._exported_dynamic:
            return Visibility.PUBLIC
        if name and name in self._exported_dynamic:
            return Visibility.PUBLIC
        # Check all symbols (.symtab) — present in ELF but not exported
        if mangled and mangled in self._exported_static:
            return Visibility.ELF_ONLY
        if name and name in self._exported_static:
            return Visibility.ELF_ONLY
        return Visibility.HIDDEN

    def _ctor_or_dtor_visibility(
        self,
        raw_mangled: str,
        name: str,
        access: AccessLevel,
        is_deleted: bool,
        is_artificial: bool,
    ) -> Visibility:
        """Visibility for a Constructor or Destructor element, with a
        source-access fallback.

        ``_visibility()`` is an ELF-symbol-table lookup: it needs a real
        mangled name to check. When castxml omits the mangled name for a
        user-declared, overloaded constructor (a documented castxml gap —
        see :func:`_function_mangled_name`'s synthesis comment), the ELF
        lookup can never match *any* overload of that constructor — the
        class's bare name never appears as its own exported symbol (Itanium
        mangling always applies to constructors), so every such overload
        would silently classify HIDDEN regardless of whether it is genuinely
        callable from outside the library. That hid both a removed public
        constructor overload (case78: FUNC_REMOVED never fired for
        ``task_arena(attach_mode_t)``) and an added one (case111: FUNC_ADDED
        never fired for the new ``std::function<int()>`` overload) behind
        `_public_functions()`'s PUBLIC/ELF_ONLY filter.

        castxml ALSO omits the mangled name for every ``<Destructor>``
        (never just user-declared/overloaded ones — a class has at most one
        destructor, so there's no overload-collision risk the way there is
        for constructors), so the exact same problem applies there: a
        removed or added virtual destructor would silently classify HIDDEN
        (Phase 2 castxml↔clang parity gate, PR #582 — discovered by
        comparing real castxml/clang dumps of a multiple/virtual-inheritance
        hierarchy: clang correctly reports a base's virtual destructor as
        PUBLIC while castxml reported it HIDDEN).

        Falls back to the real ELF lookup first (it stays authoritative
        whenever it can actually resolve something); only when that lookup
        has no mangled name to work with does a public, non-deleted,
        **user-declared** (``is_artificial`` false) constructor/destructor
        default to PUBLIC — the same "declared public in a public header,
        without contrary evidence" principle already used for source-graph
        public-surface classification
        (:data:`abicheck.buildsource.source_graph.PUBLIC_VISIBILITIES`).
        Compiler-generated implicit constructors/destructors (marked
        ``artificial="1"``) are excluded: they have no source declaration of
        their own to compare across versions, so promoting them would treat
        every trivial aggregate's synthesized ctor/dtor as a churny "added"/
        "removed" API surface instead of staying silent like the clang
        header backend already does for them.
        """
        resolved = self._visibility(raw_mangled, name)
        if raw_mangled:
            return resolved  # a real name was checked — trust a negative too
        if resolved is not Visibility.HIDDEN:
            return resolved  # matched via the bare name (e.g. C linkage)
        if access == AccessLevel.PUBLIC and not is_deleted and not is_artificial:
            return Visibility.PUBLIC
        return Visibility.HIDDEN

    def _variable_visibility(self, el: Element, mangled: str, name: str) -> Visibility:
        """Visibility for a namespace-scope Variable element, with a
        no-symbol-emitted fallback for genuine customisation point objects.

        A real CPO (``inline constexpr __sort_fn sort{};``) has external
        linkage but, when never ODR-used, the compiler emits **no** symbol
        for it at all — not even a local one — so ``_visibility()``'s ELF
        lookup correctly finds nothing and defaults to HIDDEN. That hid a
        CPO's own kind-changed finding: ``detect_cpo_kind_changed``
        (diff_templates.py) requires ``visibility == PUBLIC`` to consider a
        variable at all (case88).

        Falls back to PUBLIC only when castxml's own attributes rule out
        internal linkage: no ``static="1"`` (an explicit C++ ``static``), no
        anonymous-namespace mangling marker (``_GLOBAL__N_1``), and no
        Itanium local-linkage marker (a namespace-scope ``const``/
        ``constexpr`` variable with no ``extern`` — internal linkage by
        default, mangled with an ``L`` marker rather than exported) — the
        same "declared public, without contrary evidence" principle already
        applied to constructors/destructors (:meth:`_ctor_or_dtor_visibility`).
        """
        vis = self._visibility(mangled, name)
        if vis is not Visibility.HIDDEN:
            return vis
        if (
            el.get("static") == "1"
            or "_GLOBAL__N_1" in mangled
            or _mangled_name_is_local_linkage(mangled)
        ):
            return Visibility.HIDDEN  # genuine internal linkage, not just unexported
        return Visibility.PUBLIC

    def _is_builtin_element(self, el: Element) -> bool:
        """Return True if element originates from a compiler built-in pseudo-file.

        Real castxml output: elements carry a ``file`` attribute (e.g. ``file="f0"``)
        pointing directly to a ``File`` element in the id-map — NOT via a separate
        ``Location`` element.  The compound ``location`` attribute (``"f0:0"``) is
        informational only and is NOT a map key.

        Known built-in file names emitted by castxml:
        - ``<builtin>``       (clang/castxml built-in declarations)
        - ``<built-in>``      (older castxml / GCC)
        - ``<command-line>``  (preprocessor command-line defines)
        """
        file_id = el.get("file", "")
        if not file_id:
            return False
        file_el = self._id_map.get(file_id)
        if file_el is None:
            return False
        fname = file_el.get("name", "")
        return fname in ("<builtin>", "<built-in>", "<command-line>")

    def _build_hidden_friend_ids(self) -> dict[str, str]:
        """Map function ids to the qualified name of their befriending class.

        castxml emits an in-class ``friend`` declaration as a separate
        ``Function`` / ``Method`` / ``OperatorFunction`` element at namespace
        scope, and records the link from the class via a ``befriending``
        attribute on the ``Class`` / ``Struct`` element — a whitespace-
        separated list of ids. We resolve those ids so we can mark the
        corresponding ``Function`` objects as hidden friends downstream, and
        also record *which class* befriended each one (``hidden_friend_owner``)
        so surface classification can key demotion off the owner's header
        origin instead of unconditionally retaining every hidden-friend finding
        regardless of whether the owner lives in a system/private header.
        """
        owner_by_id: dict[str, str] = {}
        for el in self._record_els:
            if el.tag not in ("Class", "Struct"):
                continue
            befriending = el.get("befriending", "")
            if not befriending:
                continue
            owner_name = self._qualified_name(el)
            for fid in befriending.split():
                if fid:
                    owner_by_id[fid] = owner_name
        return owner_by_id

    # castxml emits non-member operator overloads as <OperatorFunction>
    # (e.g. `bool operator==(const Foo&, const Foo&)` at namespace scope,
    # including hidden friends declared inside a class body).
    _FUNCTION_TAGS: tuple[str, ...] = (
        "Function",
        "Method",
        "Constructor",
        "Destructor",
        "Converter",
        "OperatorFunction",
        "OperatorMethod",
    )

    def parse_functions(self) -> list[Function]:
        funcs: list[Function] = []
        hidden_friend_owner_by_id = self._build_hidden_friend_ids()
        for el in self._function_els:
            func = self._parse_function_element(el, hidden_friend_owner_by_id)
            if func is not None:
                funcs.append(func)
        return funcs

    def _function_display_name(self, el: Element) -> str:
        """Resolve a function element's display name, synthesizing/normalizing operator forms."""
        # castxml emits user-defined conversion operators as <Converter>
        # rather than <Method>. They carry mangled names (unlike
        # constructors), `const`/`virtual`/`explicit` qualifiers, and an
        # implicit empty name (which we synthesize as `operator <T>`).
        name = el.get("name", "")
        if not name and el.tag == "Converter":
            # Synthesize a stable display name for conversion operators.
            ret_id = el.get("returns", "")
            ret_type_for_name = self._type_name(ret_id) if ret_id else "?"
            name = f"operator {ret_type_for_name}"
        if name and el.tag == "Destructor":
            # castxml's <Destructor name="..."> is the bare CLASS name (e.g.
            # "Base1"), identical to its own Constructor's — unlike clang's
            # `-ast-dump=json`, which already names a CXXDestructorDecl
            # "~Base1" (confirmed against a live clang 18 dump; Phase 2
            # parity gate, PR #582). Synthesizing the same "~ClassName" form
            # here both matches clang's convention and gives
            # _function_mangled_name's no-mangled-name fallback (`return
            # name`) a key that can never collide with the class's own
            # constructor/type entries.
            name = f"~{name}"
        # castxml emits operator name as the bare symbol (e.g. "==", "+").
        # Normalize to the canonical "operator==" form for readability and
        # to match how the rest of the pipeline (and human reports)
        # refer to operator overloads.
        if (
            name
            and el.tag in ("OperatorFunction", "OperatorMethod")
            and not name.startswith("operator")
        ):
            name = f"operator{name}"
        return name

    def _ctor_param_identity_type(self, type_id: str) -> str:
        """Type spelling for a synthesized constructor identity key: like
        ``_type_name``, but with at most one OUTERMOST ``CvQualifiedType``
        layer removed.

        A top-level cv-qualifier — one directly wrapping the parameter's own
        type, whether that type is by-value (``volatile int``) or a pointer
        VALUE itself (``int * volatile``, i.e. ``CvQualifiedType`` directly
        wrapping ``PointerType``) — participates in neither real Itanium
        mangling nor overload identity, so it must not change the
        synthesized key either (Codex review, PR #582). A POINTEE-position
        qualifier (``const int *`` — ``PointerType`` wrapping
        ``CvQualifiedType``) is NOT touched: that one genuinely does
        distinguish two overloads and would mangle differently, so it must
        keep contributing to the key. This can't be done by pattern-matching
        the rendered ``_type_name`` string (both cases can render
        identically, e.g. ``"volatile int*"`` for either a volatile pointer
        VALUE or a pointer to volatile int) — only the real XML structure
        tells them apart: only strip when the type id itself resolves
        directly to a ``CvQualifiedType`` element.
        """
        el = self._resolve(type_id)
        if el is not None and el.tag == "CvQualifiedType":
            return self._type_name(el.get("type", ""))
        return self._type_name(type_id)

    def _parse_function_params(
        self, el: Element
    ) -> tuple[list[Param], bool, list[str]]:
        """Collect a function element's parameters, whether it is
        C-variadic, and each parameter's ctor-identity-key type spelling
        (mirrors ``params`` positionally; see ``_ctor_param_identity_type``).
        """
        params: list[Param] = []
        ctor_identity_types: list[str] = []
        is_variadic = False
        for arg in el:
            if arg.tag == "Argument":
                p_name = arg.get("name", "")
                p_type_id = arg.get("type", "")
                p_type = self._type_name(p_type_id)
                p_depth = self._pointer_depth(p_type_id)
                _, _, p_restrict = self._resolve_cv_restrict(p_type_id)
                # castxml emits default="<expr>" on Arguments that carry a
                # default value. Removing/changing a default is a source-API
                # (and silent-behaviour) concern even though the mangled name
                # is unchanged; capture it so the param_defaults detector can
                # fire. Absent attribute → None (no default).
                params.append(
                    Param(
                        name=p_name,
                        type=p_type,
                        pointer_depth=p_depth,
                        default=arg.get("default"),
                        # restrict has no ABI/mangling effect (unlike
                        # const/volatile) — tracked as its own compatible-
                        # classified fact via the dedicated param_restrict
                        # detector rather than folded into `type` (see
                        # _type_name's CvQualifiedType handling above).
                        is_restrict=p_restrict,
                    )
                )
                ctor_identity_types.append(self._ctor_param_identity_type(p_type_id))
            elif arg.tag == "Ellipsis":
                # Trailing C ellipsis (...) — the function is variadic.
                is_variadic = True
        return params, is_variadic, ctor_identity_types

    def _enclosing_class_qualified_name(self, el: Element) -> str:
        """Fully-qualified (``ns::Outer::Class``) name of the class/struct/
        union enclosing a Constructor/Destructor element *el*.

        Distinct from calling ``_qualified_name(el)`` directly on *el*: a
        Constructor/Destructor's own bare ``name`` attribute already equals
        the class's own leaf name, so walking from *el* itself would count
        that leaf twice (``Foo::Foo`` instead of ``ns::Foo``). Walking from
        *el*'s ``context`` (the class element) instead starts one level up,
        at the class's own name.
        """
        class_el = self._resolve(el.get("context", ""))
        if class_el is None:
            return el.get("name", "")
        return self._qualified_name(class_el)

    @staticmethod
    def _function_mangled_name(
        el: Element,
        name: str,
        ctor_identity_types: list[str],
        raw_mangled: str,
        qualified_scope: str = "",
    ) -> str:
        """Pick the snapshot key for a function: mangled name, ctor synthesis, or plain name."""
        if raw_mangled:
            return raw_mangled
        if el.tag == "Constructor":
            # CastXML may omit constructor mangled names even for public
            # user-declared overloaded constructors.  Using the bare class
            # name would collapse all overloads in AbiSnapshot.function_map,
            # hiding constructor additions such as case111.  Synthesize a
            # deterministic internal identity from the display name and
            # normalized parameter types; it is intentionally not an ABI
            # symbol, only a stable snapshot key for source-level overloads.
            # ctor_identity_types (not the raw Param.type strings) drops a
            # TOP-LEVEL cv qualifier the same way real Itanium mangling
            # would — see _ctor_param_identity_type's docstring: without it,
            # a layout-neutral declaration change like ``Widget(int)`` ->
            # ``Widget(volatile int)`` (by-value) or ``Widget(int*)`` ->
            # ``Widget(int* volatile)`` (the pointer VALUE itself, not its
            # pointee) produced two different synthetic keys, so the diff
            # engine saw a removed + added constructor instead of the same
            # overload reaching the cv-neutral param comparison (Codex
            # review, PR #582).
            #
            # Use the fully-qualified enclosing class name (falling back to
            # the bare *name* only if it couldn't be resolved), not just the
            # bare class name: two public classes with the same leaf name in
            # different namespaces (``ns1::Foo``/``ns2::Foo``) would
            # otherwise synthesize the identical key, silently colliding in
            # ``AbiSnapshot.function_map`` — one class's constructor
            # additions/removals then went undetected, "first-wins" (Codex
            # review, PR #582). A non-namespaced class's qualified name is
            # just its bare name, so this is a no-op for the common case.
            scope = qualified_scope or name
            param_sig = ",".join(ctor_identity_types)
            return f"{SYNTHETIC_CTOR_KEY_PREFIX}{scope}({param_sig})"
        if el.tag == "Destructor" and qualified_scope:
            # Same namespace-collision reasoning as above, applied to the
            # destructor's synthesized "~ClassName" key: qualify it as
            # "~ns::Class" instead of bare "~Class". The leading "~" is
            # preserved (is_synthetic_dtor_key() checks for it), and a
            # non-namespaced class again collapses to the pre-existing
            # "~Class" form.
            return f"~{qualified_scope}"
        return name  # C functions: use plain name

    def _function_source_location(
        self, el: Element
    ) -> tuple[str | None, Element | None]:
        """Resolve a function element's ``file:line`` source location and Location element."""
        # CastXML may store source location two ways:
        #   1. Directly as ``file``/``line`` attributes on the declaration
        #      element (modern compound-attribute form).
        #   2. As ``location="loc1"`` referencing a separate ``Location``
        #      element in the id map (legacy form).
        # Try direct attrs first, then fall back to the id-map lookup so
        # both formats are supported without losing source_location info.
        file_id = el.get("file", "")
        line = el.get("line", "")
        loc_el: Element | None = None
        if not (file_id and line):
            loc_id = el.get("location", "")
            loc_el = self._id_map.get(loc_id) if loc_id else None
            if loc_el is not None:
                file_id = loc_el.get("file", "")
                line = loc_el.get("line", "")
        file_el = self._id_map.get(file_id) if file_id else None
        fname = file_el.get("name", "") if file_el is not None else ""
        source_loc = f"{fname}:{line}" if fname and line else None
        return source_loc, loc_el

    def _function_is_explicit(self, el: Element, loc_el: Element | None) -> bool | None:
        """Determine the tri-state `explicit` specifier for a function element."""
        # castxml emits explicit="1" on Constructor / Method elements that
        # carry the `explicit` specifier. Tri-state: only Constructor /
        # Method tags can be explicit; for plain Function / Destructor the
        # attribute is conceptually N/A and we leave is_explicit=None so
        # the diff does not produce spurious findings.
        if el.tag in ("Constructor", "Method"):
            return el.get("explicit") == "1"
        if el.tag == "Converter":
            return (
                el.get("explicit") == "1"
                if el.get("explicit") is not None
                else self._source_line_has_explicit(loc_el, el)
            )
        return None

    @staticmethod
    def _function_ref_qualifier(el: Element, mangled: str) -> str:
        """Derive the &/&& ref-qualifier from the refqual attribute or the mangling."""
        # C++ ref-qualifier: newer castxml emits refqual="lvalue"/"rvalue",
        # but released versions (≤0.6.x) omit the attribute entirely, so
        # fall back to the Itanium mangling — the qualifier is encoded as
        # R (&) / O (&&) right after the CV-qualifiers in <nested-name>.
        refqual_raw = el.get("refqual", "")
        return {"lvalue": "&", "rvalue": "&&"}.get(
            refqual_raw, ""
        ) or _ref_qualifier_from_mangled(mangled)

    def _function_exception_spec(self, el: Element) -> str:
        """Render a function element's dynamic exception specification, if any."""
        # Dynamic exception specification: castxml emits throw="" for
        # `throw()` and a space-separated type-id list for `throw(T...)`.
        # Absent attribute = no dynamic spec (captured as ""), keeping the
        # tri-state None for dumpers that cannot know.
        throw_attr = el.get("throw")
        if throw_attr is None:
            return ""
        if not throw_attr.strip():
            return "throw()"
        thrown = ", ".join(self._type_name(tid) for tid in throw_attr.split())
        return f"throw({thrown})"

    def _parse_function_element(
        self, el: Element, hidden_friend_owner_by_id: dict[str, str]
    ) -> Function | None:
        """Build a Function from a castxml function-like element, or None if filtered."""
        name = self._function_display_name(el)
        if not name:
            return None
        # Skip compiler built-ins and command-line synthetic declarations
        if self._is_builtin_element(el):
            return None
        raw_mangled = el.get("mangled", "")
        ret_id = el.get("returns", "")
        ret_type = self._type_name(ret_id) if ret_id else "void"
        ret_ptr_depth = self._pointer_depth(ret_id) if ret_id else 0

        params, is_variadic, ctor_identity_types = self._parse_function_params(el)
        qualified_scope = (
            self._enclosing_class_qualified_name(el)
            if el.tag in ("Constructor", "Destructor")
            else ""
        )
        mangled = self._function_mangled_name(
            el, name, ctor_identity_types, raw_mangled, qualified_scope
        )

        # Real ELF export evidence overrides castxml's language-mode guess:
        # castxml ALWAYS emits a pseudo-Itanium `mangled` attribute, even for
        # a plain C function parsed in ambiguous/C++ mode (confirmed
        # empirically — the "C functions: use plain name" fallback in
        # _function_mangled_name is otherwise dead code, since raw_mangled is
        # never actually empty). When that guessed mangling matches no real
        # exported symbol at all while the function's bare declared name
        # *is* a real export, that's strong, low-false-positive-risk
        # evidence the function actually has C linkage — a genuine C++
        # function's real compiled export would essentially never coincide
        # with its bare unqualified name. Use the bare name as the
        # canonical symbol identity instead (case141).
        #
        # Restricted to global-scope functions (context is the root ``::``
        # namespace): ``name`` is always castxml's bare leaf identifier —
        # for a *namespaced* C++ function (``ns::foo``), the same bare
        # leaf could coincidentally match an unrelated, genuinely-exported
        # plain C ``foo``, which would wrongly rewrite the namespaced
        # function's identity onto that unrelated export instead. A real
        # (possibly extern "C") function this override is meant to recover
        # is always declared at global scope.
        if (
            el.tag == "Function"
            and mangled.startswith("_Z")
            and mangled not in self._exported_dynamic
            and name in self._exported_dynamic
            and self._is_global_scope(el)
        ):
            mangled = name
            is_extern_c_override = True
        else:
            is_extern_c_override = False

        is_virtual = el.get("virtual") == "1"
        noexcept_re = re.search(r"noexcept", el.get("attributes", ""))
        vtable_index = (
            _parse_vtable_index(el.get("vtable_index")) if is_virtual else None
        )

        # Detect extern "C": explicit extern attribute OR no mangled name (C linkage)
        is_extern_c = (
            el.get("extern") == "1"
            or (
                not raw_mangled and el.tag == "Function"
            )  # C functions have no mangled name
            or is_extern_c_override
        )

        source_loc, loc_el = self._function_source_location(el)
        access = self._access_level(el)
        is_deleted = el.get("deleted") == "1"
        visibility = (
            self._ctor_or_dtor_visibility(
                raw_mangled, name, access, is_deleted, el.get("artificial") == "1"
            )
            if el.tag in ("Constructor", "Destructor")
            else self._visibility(raw_mangled, name)
        )

        return Function(
            name=name,
            mangled=mangled,
            return_type=ret_type,
            params=params,
            visibility=visibility,
            is_virtual=is_virtual,
            is_noexcept=bool(noexcept_re),
            is_extern_c=is_extern_c,
            vtable_index=vtable_index,
            source_location=source_loc,
            is_static=el.get("static") == "1",
            is_const=el.get("const") == "1",
            is_volatile=el.get("volatile") == "1",
            is_pure_virtual=el.get("pure_virtual") == "1",
            is_deleted=is_deleted,
            # castxml emits inline="1" for inline functions/methods
            is_inline=el.get("inline") == "1",
            access=access,
            return_pointer_depth=ret_ptr_depth,
            ref_qualifier=self._function_ref_qualifier(el, mangled),
            is_explicit=self._function_is_explicit(el, loc_el),
            # Hidden-friend marker: castxml records the link via the
            # ``befriending`` attribute on the class element. We resolved
            # the referenced ids upfront and check membership here.
            is_hidden_friend=el.get("id", "") in hidden_friend_owner_by_id,
            hidden_friend_owner=hidden_friend_owner_by_id.get(el.get("id", "")),
            is_variadic=is_variadic,
            # Semantic contract / calling-convention attributes, filtered from
            # the compound ``attributes`` string (same channel as noexcept).
            contract_attributes=_extract_contract_attributes(el.get("attributes", "")),
            exception_spec=self._function_exception_spec(el),
            # See _deprecation_marker for why this isn't a plain
            # el.get("deprecation") read.
            deprecated=_deprecation_marker(el),
            # Explicit C++11 `override` specifier: castxml has no dedicated
            # boolean for it (distinct from `overrides`, the id-reference
            # list used for vtable-slot dedup) — the `override` token is
            # embedded in the same compound `attributes` string as
            # `noexcept`/`final`. Only member-function forms that can
            # actually be virtual may carry it; a free function/operator or
            # a constructor never can, so those stay None (not merely
            # False) rather than asserting a fact that's not applicable.
            is_override=(
                bool(re.search(r"\boverride\b", el.get("attributes", "")))
                if el.tag in ("Method", "Destructor", "Converter", "OperatorMethod")
                else None
            ),
        )

    def parse_variables(self) -> list[Variable]:
        variables = []
        for el in self._variable_els:
            name = el.get("name", "")
            # C-mode castxml does not emit a mangled attribute for C-linkage variables
            # (C has no name mangling); fall back to plain name as the symbol key,
            # mirroring the same pattern in parse_functions().
            mangled = el.get("mangled", "") or name
            if not mangled:
                continue
            # Real ELF export evidence overrides castxml's language-mode guess
            # — the same "case141" fallback already applied to functions
            # above (_parse_function_element): castxml ALWAYS emits a
            # pseudo-Itanium `mangled` attribute for a Variable too, even
            # when the header is actually a plain C API compiled with a C
            # linkage that never mangles at all (confirmed empirically —
            # Phase 2 castxml↔clang parity gate, PR #582: a `.c`-compiled
            # `extern int g;` got a bogus `_Z1g`-style key from castxml
            # while clang correctly reported the real bare-name export).
            # Restricted to global scope for the same reason as the function
            # override: a namespaced C++ variable's bare leaf could
            # coincidentally match an unrelated global export.
            if (
                mangled.startswith("_Z")
                and mangled not in self._exported_dynamic
                and mangled not in self._exported_static
                and name in (self._exported_dynamic | self._exported_static)
                and self._is_global_scope(el)
            ):
                mangled = name
            # Skip compiler built-ins and command-line synthetic declarations
            if self._is_builtin_element(el):
                continue
            type_name = self._type_name(el.get("type", ""))
            # Use castxml structured attribute first; fall back to word-boundary
            # regex on type_name to avoid false positives on names like
            # "constructor_t", "const_iterator", "myconstant".
            is_const = el.get("const") == "1" or bool(
                re.search(r"\bconst\b", type_name)
            )
            vis = self._variable_visibility(el, mangled, name)
            variables.append(
                Variable(
                    name=name,
                    mangled=mangled,
                    type=type_name,
                    visibility=vis,
                    is_const=is_const,
                    source_location=self._source_location(el),
                    # Explicit alignas/aligned override when castxml emits an
                    # ``align`` attribute on the Variable itself; falls back to
                    # the type's own natural (computed) alignment when there is
                    # no explicit override, so a plain scalar/aggregate global —
                    # the common case — still carries real declared-alignment
                    # evidence instead of leaving this None. See
                    # _type_alignment_bits.
                    alignment_bits=self._optional_int_attr(el, "align")
                    or self._type_alignment_bits(el.get("type", "")),
                    # See RecordType.deprecated for the message-text convention.
                    deprecated=_deprecation_marker(el),
                )
            )
        return variables

    def parse_constants(self) -> dict[str, str]:
        """Extract ``const`` / ``constexpr`` constant *values* declared in the
        provided public headers.

        These have a compile-time initializer (castxml emits ``init="..."``) and
        their value is baked into every consumer that ``#include``s the header —
        so a value change is a real source/ABI compatibility hazard. Yet a
        namespace-scope ``const``/``constexpr`` has internal linkage and emits no
        exported symbol, so it is invisible to DWARF/object comparison; only the
        header (castxml) tier can see it.

        Scoped to the public-header surface via provenance: a constant is kept
        only when its declaring header classifies as ``PUBLIC_HEADER`` (the
        parsed ``-H`` headers, plus any ``--public-header``/``--public-header-dir``
        inputs — so constants reached through an umbrella header or a public
        include dir are captured, while transitively-included system/private
        headers are excluded). Returns ``name -> value``; empty when no public
        header set is available (e.g. DWARF/symbols-only mode).
        """
        return {name: init for name, init, _ in self._iter_public_constants()}

    def parse_constant_headers(self) -> dict[str, str]:
        """Map each public constant's qualified name to its declaring header path.

        Same public-header scoping and key qualification as
        :meth:`parse_constants` (they share one filtering pass, so the maps never
        disagree). The L4 source-ABI extractor uses this to mark constants from a
        *generated* public header as ``GENERATED`` — otherwise a constant removed
        from a generated config header produces no L4 finding (the value-change
        case is already covered). The L2 snapshot path does not call this.
        """
        return {name: header for name, _, header in self._iter_public_constants()}

    def _iter_public_constants(self) -> list[tuple[str, str, str]]:
        """Return ``(qualified_name, init_value, declaring_header)`` for every
        public ``const``/``constexpr`` — the single source of truth shared by
        :meth:`parse_constants` and :meth:`parse_constant_headers`.
        """
        if not self._have_public_set:
            return []
        out: list[tuple[str, str, str]] = []
        for el in self._variable_els:
            init = el.get("init")
            if not init:
                continue
            if self._is_builtin_element(el):
                continue
            name = el.get("name", "")
            if not name:
                continue
            # Skip private/protected class-scope members: a consumer cannot
            # name them, so a value change to such an implementation detail is
            # not an API contract change. (Namespace-scope constants carry no
            # `access` attribute, so they pass through as public.)
            if el.get("access") in ("private", "protected"):
                continue
            # Only const / constexpr: the initializer is a baked-in contract.
            # (constexpr implies const, so this captures both.)
            type_name = self._type_name(el.get("type", ""))
            is_const = el.get("const") == "1" or bool(
                re.search(r"\bconst\b", type_name)
            )
            if not is_const:
                continue
            if not self._decl_is_public(el):
                continue
            # Qualify the key with its namespace/class context so that
            # constants sharing an unqualified name in different scopes
            # (``A::kLimit`` vs ``B::kLimit``) don't alias and overwrite each
            # other — which would mask or misreport a CONSTANT_CHANGED.
            out.append(
                (
                    self._qualified_name(el),
                    init,
                    header_from_location(self._source_location(el)) or "",
                )
            )
        return out

    def _qualified_name(self, el: Any) -> str:
        """Build a namespace/class-qualified name by walking ``context``.

        ``A::kLimit`` for a constant in namespace ``A``; ``C::kLimit`` for a
        static data member of ``C``; the bare name for a global. Stops at the
        global namespace (castxml name ``"::"``).
        """
        parts = [el.get("name", "")]
        ctx_id = el.get("context", "")
        seen: set[str] = set()
        while ctx_id and ctx_id not in seen:
            seen.add(ctx_id)
            ctx = self._id_map.get(ctx_id)
            if ctx is None:
                break
            cname = ctx.get("name", "")
            if cname and cname != "::":
                parts.append(cname)
            ctx_id = ctx.get("context", "")
        return "::".join(reversed(parts))

    def _decl_is_public(self, el: Any) -> bool:
        """True if *el*'s declaring header classifies as a public header.

        Uses the shared provenance segment matcher (suffix/basename/public-dir
        containment), so build-prefixed paths and umbrella-included public
        headers match while system/private headers do not.
        """
        sh = header_from_location(self._source_location(el))
        if not sh:
            return False
        return (
            classify_origin(
                sh,
                self._pub_header_segs,
                self._pub_dir_segs,
                have_public_set=self._have_public_set,
            )
            == ScopeOrigin.PUBLIC_HEADER
        )

    def parse_types(self) -> list[RecordType]:
        # Build reverse mapping: struct/union ID → typedef name for anonymous types.
        # This allows us to include `typedef struct { ... } Foo;` where the struct
        # itself is anonymous (name="") but reachable via the typedef.
        typedef_name_for: dict[str, str] = {}
        for el in self._typedef_els:
            td_name = el.get("name", "")
            if not td_name:
                continue
            target_id = el.get("type", "")
            target_el = self._resolve(target_id)
            # Follow through ElaboratedType / CvQualifiedType wrappers
            # that castxml may insert between Typedef and the actual Struct.
            while target_el is not None and target_el.tag in (
                "ElaboratedType",
                "CvQualifiedType",
            ):
                target_id = target_el.get("type", "")
                target_el = self._resolve(target_id)
            if target_el is not None and target_el.tag in ("Struct", "Class", "Union"):
                target_name = target_el.get("name", "")
                if not target_name:
                    # Anonymous struct/union with a typedef alias — record it.
                    # Use the struct's own id as key (may differ from the
                    # Typedef's type attr when ElaboratedType is involved).
                    struct_id = target_el.get("id", "")
                    if struct_id:
                        typedef_name_for[struct_id] = td_name

        types = []
        for el in self._record_els:
            if self._is_public_record_type(el):
                types.append(self._build_record_type(el))
            else:
                # self._record_els is already pre-filtered to Struct/Class/
                # Union (see _build_id_map), so this is every record type
                # _is_public_record_type rejected. Check if it's an
                # anonymous struct reachable via typedef.
                eid = el.get("id", "")
                override_name = typedef_name_for.get(eid)
                if override_name and not self._is_builtin_element(el):
                    types.append(
                        self._build_record_type(el, override_name=override_name)
                    )
        return types

    def _is_public_record_type(self, el: Any) -> bool:
        if el.tag not in ("Struct", "Class", "Union"):
            return False
        name = el.get("name", "")
        if not name or el.get("artificial") == "1":
            return False
        if name.startswith("__"):
            return False
        # Skip compiler built-ins and command-line synthetic types
        if self._is_builtin_element(el):
            return False
        return True

    def _build_record_type(
        self, el: Any, override_name: str | None = None
    ) -> RecordType:
        name = override_name or el.get("name", "")
        is_opaque = el.get("incomplete") == "1"
        vtable = [] if is_opaque else self._build_vtable(el.get("id", ""))
        # Best-effort layout descriptor (layout-closure work). Direct (non-virtual)
        # base subobject offsets from each ``<Base offset=...>``; the unit only has
        # to be consistent across snapshots for change detection, and it is.
        base_offsets: dict[str, int] = {}
        if not is_opaque:
            for b in el:
                if b.tag == "Base" and b.get("virtual") != "1":
                    off = self._optional_int_attr(b, "offset")
                    if off is not None:
                        base_offsets[self._type_name(b.get("type", ""))] = off
        # is_standard_layout / is_trivially_copyable / data_size_bits are left
        # None: "not polymorphic and no virtual bases" is not a sound
        # standard-layout signal (a mixed-access class is already non-standard-
        # layout, so the heuristic would flip True→False on gaining a virtual and
        # emit a spurious STANDARD_LAYOUT_LOST), and CastXML doesn't expose the
        # trivially-copyable trait directly (Codex review #345).
        return RecordType(
            name=name,
            kind=el.tag.lower(),
            size_bits=self._optional_int_attr(el, "size"),
            alignment_bits=self._optional_int_attr(el, "align"),
            fields=[] if is_opaque else self._parse_record_fields(el),
            bases=[]
            if is_opaque
            else [
                self._type_name(b.get("type", ""))
                for b in el
                if b.tag == "Base" and b.get("virtual") != "1"
            ],
            virtual_bases=[]
            if is_opaque
            else [
                self._type_name(b.get("type", ""))
                for b in el
                if b.tag == "Base" and b.get("virtual") == "1"
            ],
            vtable=vtable,
            is_union=el.tag == "Union",
            is_opaque=is_opaque,
            # Polymorphic (non-empty vtable) → vtable pointer at offset 0; None
            # when non-polymorphic so the diff can tell "gained a vptr" apart.
            vptr_offset_bits=0 if vtable else None,
            base_offsets=base_offsets,
            qualified_name=self._qualified_type_name(el, leaf_name=name),
            # castxml records the `final` class-key specifier as a `final`
            # token inside the compound ``attributes`` string (e.g.
            # ``attributes="final"``), the same channel used for noexcept.
            # Header mode always knows the answer, so this is a concrete bool
            # (never None on the castxml path); DWARF/symbols mode leaves the
            # model default of None since the binary carries no `final` info.
            is_final=bool(re.search(r"\bfinal\b", el.get("attributes", ""))),
            source_location=self._source_location(el),
            # castxml's `abstract="1"` marks a class/struct with at least one
            # pure virtual function (cannot be instantiated). Header mode
            # always knows the answer for a complete type, matching the
            # `is_final` convention above; left None for an opaque/incomplete
            # record (no member list to have judged it from).
            is_abstract=None if is_opaque else el.get("abstract") == "1",
            # `[[deprecated("msg")]]` -> the message text verbatim; a bare
            # `[[deprecated]]` with no message -> "" (see _deprecation_marker:
            # castxml only emits the `deprecation` XML attribute when there
            # IS a message, so a bare marker must be read from the
            # compound `attributes` string instead); not deprecated -> None.
            deprecated=_deprecation_marker(el),
        )

    def _source_location(self, el: Any) -> str | None:
        """Resolve a declaration's ``file:line`` source location.

        Mirrors the function-parsing path: castxml emits the location either
        directly as ``file``/``line`` attributes or as a ``location`` id
        referencing a ``Location`` element. Returns ``None`` when neither is
        present. Used to populate provenance (``source_header``/``origin``)
        on records, variables, and enums (ADR-015 v6).
        """
        file_id = el.get("file", "")
        line = el.get("line", "")
        if not (file_id and line):
            loc_id = el.get("location", "")
            loc_el = self._id_map.get(loc_id) if loc_id else None
            if loc_el is not None:
                file_id = loc_el.get("file", "")
                line = loc_el.get("line", "")
        file_el = self._id_map.get(file_id) if file_id else None
        fname = file_el.get("name", "") if file_el is not None else ""
        return f"{fname}:{line}" if fname and line else None

    def _optional_int_attr(self, el: Any, attr: str) -> int | None:
        raw = el.get(attr)
        return int(raw) if raw and raw.isdigit() else None

    def _parse_record_fields(self, el: Any) -> list[TypeField]:
        """Parse struct/class/union fields.

        castxml uses two layouts depending on version / output mode:
        - Inline children: ``<Struct><Field .../></Struct>``
        - Members attribute: ``<Struct members="_14 _15 _16 ..."/>`` (IDs resolved via id_map)

        We support both: first scan inline children, then fall back to the
        ``members`` attribute so we never miss fields in either format.
        """
        fields: list[TypeField] = []

        # Collect Field elements: inline children first
        field_elements: list[Any] = [c for c in el if c.tag == "Field"]

        # Fallback: resolve via space-separated "members" attribute
        if not field_elements:
            for mid in el.get("members", "").split():
                member_el = self._id_map.get(mid)
                if member_el is not None and member_el.tag == "Field":
                    field_elements.append(member_el)

        for child in field_elements:
            child_name = child.get("name", "")
            if not child_name:
                # Anonymous struct/union member — flatten its fields into parent
                fields.extend(self._expand_anonymous_field(child))
                continue
            bitfield_bits, is_bitfield = self._parse_bitfield_bits(child.get("bits"))
            field_type_id = child.get("type", "")
            field_type = self._type_name(field_type_id)
            # Resolved from the real XML type chain (following through any
            # Typedef indirection), not a regex over `field_type`: a field
            # declared through a typedef to a cv-qualified type (`typedef
            # const int T; struct S { T x; };`) renders as the bare alias
            # name ("T"), which a spelling-based regex could never see
            # through (Codex review, PR #582).
            field_const, field_volatile, _ = self._resolve_cv_restrict(field_type_id)
            fields.append(
                TypeField(
                    name=child_name,
                    type=field_type,
                    offset_bits=self._optional_int_attr(child, "offset"),
                    is_bitfield=is_bitfield,
                    bitfield_bits=bitfield_bits,
                    is_const=field_const,
                    is_volatile=field_volatile,
                    # castxml's Field element carries its own `mutable="1"`
                    # attribute (fixed xs:int, per castxml.xsd) rather than
                    # deriving it from the referenced type like const/volatile.
                    is_mutable=child.get("mutable") == "1",
                    access=self._access_level(child),
                    # Default member initializer expression, verbatim
                    # (castxml's Field ``init`` attribute — the same channel
                    # already used for Variable/constant initializers).
                    default=child.get("init"),
                    # See RecordType.deprecated for the message-text convention.
                    deprecated=_deprecation_marker(child),
                )
            )
        return fields

    def _expand_anonymous_field(
        self, field_el: Any, _depth: int = 0, _outer_offset: int = 0
    ) -> list[TypeField]:
        """Flatten anonymous struct/union field into the parent's field list.

        In castxml output, anonymous unions/structs inside a struct appear as
        ``Field`` elements with ``name=""`` pointing to a ``Union`` or ``Struct``
        element.  We inline their named fields at the correct offset to prevent
        false ``TYPE_FIELD_REMOVED`` reports when a named field moves into an
        anonymous union (issue #58).

        ``_depth`` guards against malformed/cyclic XML (max nesting: 16).
        ``_outer_offset`` carries the accumulated offset from outer anonymous
        members so doubly-nested fields get correct absolute ``offset_bits``.
        """
        if _depth > 16:
            return []
        type_id = field_el.get("type", "")
        type_el = self._resolve(type_id)
        if type_el is None or type_el.tag not in ("Union", "Struct"):
            return []

        this_offset = _outer_offset + (self._optional_int_attr(field_el, "offset") or 0)
        result: list[TypeField] = []

        # Collect inner Field elements (inline children or members attribute)
        inner_fields: list[Any] = [c for c in type_el if c.tag == "Field"]
        if not inner_fields:
            for mid in type_el.get("members", "").split():
                member_el = self._id_map.get(mid)
                if member_el is not None and member_el.tag == "Field":
                    inner_fields.append(member_el)

        for inner in inner_fields:
            inner_name = inner.get("name", "")
            if not inner_name:
                # Doubly-nested anonymous member — recurse, passing accumulated offset
                result.extend(
                    self._expand_anonymous_field(
                        inner,
                        _depth + 1,
                        _outer_offset=this_offset,
                    )
                )
                continue
            inner_offset = self._optional_int_attr(inner, "offset") or 0
            bitfield_bits, is_bitfield = self._parse_bitfield_bits(inner.get("bits"))
            inner_type_id = inner.get("type", "")
            inner_type = self._type_name(inner_type_id)
            inner_const, inner_volatile, _ = self._resolve_cv_restrict(inner_type_id)
            result.append(
                TypeField(
                    name=inner_name,
                    type=inner_type,
                    offset_bits=this_offset + inner_offset,
                    is_bitfield=is_bitfield,
                    bitfield_bits=bitfield_bits,
                    is_const=inner_const,
                    is_volatile=inner_volatile,
                    is_mutable=inner.get("mutable") == "1",
                    access=self._access_level(inner),
                    # Same channel as the direct-field path in
                    # _parse_record_fields — a field inside an anonymous
                    # struct/union must not lose its initializer/deprecation
                    # just because it was flattened (Codex review, PR #582).
                    default=inner.get("init"),
                    deprecated=_deprecation_marker(inner),
                )
            )
        return result

    @staticmethod
    def _parse_bitfield_bits(bits_raw: str | None) -> tuple[int | None, bool]:
        try:
            bitfield_bits = int(bits_raw) if bits_raw is not None else None
        except ValueError:
            return (None, False)
        return (bitfield_bits, bitfield_bits is not None)

    def _build_vtable(self, class_id: str) -> list[str]:
        slots = self._collect_virtual_methods(class_id)
        ordered = sorted(slots.values(), key=_vt_sort_key)
        return [name for _, name in ordered]

    def _collect_virtual_methods(
        self,
        cid: str,
        seen: set[str] | None = None,
    ) -> dict[int | str, tuple[int | None, str]]:
        """Ordered mapping of *canonical vtable-slot key* -> ``(vtable_index, mangled)``.

        Keyed so a derived override replaces its base's entry **in place**
        rather than appending a duplicate: dict re-assignment to an existing
        key keeps that key's original insertion position (Python dict
        semantics), so a reused slot stays where the base declared it while a
        genuinely new virtual still appends at the end.

        ``vtable_index`` is the preferred slot identity when castxml emits it
        (unchanged from prior behavior). But that attribute is not always
        present — this castxml/Clang build may track no slot indices at all —
        and without it, a same-signature override (which reuses its base's
        slot per the Itanium ABI) has no other signal tying it to the base
        entry it replaces, so it was appended as a spurious extra slot,
        growing the reconstructed vtable by one entry it never actually
        gained (case185's false-positive ``type_vtable_changed``: a
        `Derived::paint(int) override` reusing `Base::paint(int)`'s slot read
        as vtable growth instead of a compatible rename in place).
        castxml's ``overrides`` attribute — the id of the method declaration
        this one overrides — is the fallback signal: resolved (through
        ``_vtable_slot_root``, to survive multi-level override chains where
        ``overrides`` points at an intermediate override rather than the
        slot's original declarer) to the same key the overridden entry was
        stored under, so the override replaces it instead of duplicating it.
        """
        if seen is None:
            seen = set()
        if cid in seen:
            return {}
        seen.add(cid)
        class_el = self._id_map.get(cid)
        if class_el is None:
            return {}

        slots: dict[int | str, tuple[int | None, str]] = {}
        for base in class_el:
            if base.tag != "Base":
                continue
            base_type_el = self._resolve(base.get("type", ""))
            if base_type_el is not None:
                slots.update(
                    self._collect_virtual_methods(base_type_el.get("id", ""), seen)
                )

        for method_el in self._virtual_methods_by_class.get(cid, []):
            mangled_name = method_el.get("mangled", "")
            if not mangled_name and method_el.tag == "Destructor":
                # castxml <Destructor> elements carry no mangled attribute.
                # Without a fallback every virtual destructor is silently
                # dropped from the vtable, which makes each polymorphic type
                # look like it lacks a destructor slot (false
                # POLYMORPHIC_TYPE_NON_VIRTUAL_DTOR). The name attribute is
                # the class name, so "~Name" is a stable, per-class entry.
                name = method_el.get("name", "")
                mangled_name = f"~{name}" if name else ""
            if not mangled_name:
                continue
            idx = _parse_vtable_index(method_el.get("vtable_index"))
            mid = method_el.get("id", "")
            overrides_id = method_el.get("overrides")
            key: int | str
            extra_keys: list[int | str] = []
            if overrides_id:
                # An override always reuses whatever slot its base declaration
                # landed under -- checked BEFORE falling back to this
                # declaration's own vtable_index. Preferring a fresh idx here
                # instead would miss the reverse mixed-index direction: a base
                # that lacks vtable_index (so its slot is keyed by its own
                # string id) but is overridden by a declaration that DOES
                # carry an index would otherwise open a new int-keyed slot
                # instead of collapsing onto the base's string-keyed one.
                #
                # castxml can list more than one overridden declaration as a
                # whitespace-separated id list when a single override
                # simultaneously covers more than one base-class branch (e.g.
                # non-virtual multiple inheritance -- Derived : Base1, Base2 --
                # where one final overrider satisfies both Base1::foo() and
                # Base2::foo()). Each resolved id is a genuinely distinct
                # position in the object's real vtable-group layout (typically
                # an adjusting thunk for all but one) -- an exact lookup of the
                # raw composite string never matches _vtable_slot_root, so
                # resolve each id: the first resolved slot becomes this
                # entry's own key, and every OTHER resolved slot keeps its own
                # key and prior sort position (extra_keys, applied below)
                # with only its content updated to this override, rather than
                # collapsing them into one entry -- which would under-report
                # the vtable's true size -- or leaving them with stale
                # pre-override content.
                #
                # A resolved id can itself carry extra roots from an earlier
                # multi-slot override (a further-derived override referencing
                # an intermediate override's id by `overrides` must propagate
                # to every slot that intermediate one touched, not just its
                # primary) -- both _vtable_slot_root and
                # _vtable_slot_extra_roots are consulted per id below.
                resolved_keys: list[int | str] = []
                for oid in overrides_id.split():
                    candidates: list[int | str] = []
                    primary = self._vtable_slot_root.get(oid)
                    if primary is not None:
                        candidates.append(primary)
                    candidates.extend(self._vtable_slot_extra_roots.get(oid, ()))
                    for candidate in candidates:
                        if candidate not in resolved_keys:
                            resolved_keys.append(candidate)
                if resolved_keys:
                    key = resolved_keys[0]
                    extra_keys = resolved_keys[1:]
                else:
                    key = overrides_id
                if isinstance(key, int):
                    # Consistently-indexed lineage: adopt the resolved index
                    # for sorting when this declaration has none of its own,
                    # so _build_vtable's final _vt_sort_key sort places it at
                    # the inherited position instead of the unindexed tail
                    # (which would silently reorder it past any indexed
                    # sibling slot declared after this one, an apparent
                    # "vtable reordered" that never actually happened).
                    if idx is None:
                        idx = key
                else:
                    # Unindexed lineage (key is a string): a fresh
                    # vtable_index on THIS declaration has no verified
                    # relationship to sibling unindexed slots' true positions
                    # (e.g. Base has unindexed foo then bar; Derived overrides
                    # bar with its own vtable_index="1" -- that "1" doesn't
                    # mean "after foo", it's not comparable to foo's unknown
                    # position at all), so it must not be trusted for
                    # cross-slot ordering. Discard it and let _vt_sort_key
                    # treat this slot as unindexed, preserving its original
                    # discovery-order position.
                    idx = None
            elif idx is not None:
                key = idx
            else:
                key = mid or mangled_name
            if mid:
                # Record the *actual* slot key (int index or str id) this method
                # landed under, not just a self-reference -- a downstream override
                # in a mixed indexed/unindexed chain (e.g. Base has vtable_index,
                # Mid overrides it losing the index, Derived overrides Mid via
                # `overrides="Mid's id"`) must still resolve back to the int index
                # Base's slot is keyed by, or it would append instead of replace.
                self._vtable_slot_root[mid] = key
                if extra_keys:
                    # This id itself touches more than one slot -- a further-
                    # derived override referencing it by `overrides` must
                    # propagate to all of them (see the resolution loop above).
                    self._vtable_slot_extra_roots[mid] = list(extra_keys)
            slots[key] = (idx, mangled_name)
            for extra_key in extra_keys:
                prev_idx, _ = slots.get(extra_key, (None, ""))
                slots[extra_key] = (prev_idx, mangled_name)

        return slots

    def parse_enums(self) -> list[EnumType]:
        enums = []
        for el in self._enum_els:
            name = el.get("name", "")
            if not name or name.startswith("__"):
                continue
            if self._is_builtin_element(el):
                continue
            members = []
            for child in el:
                if child.tag == "EnumValue":
                    m_name = child.get("name", "")
                    m_val_str = child.get("init", "0")
                    try:
                        # base=0 auto-detects 0x.../0o.../0b... prefixes and signs
                        # so common C/C++ initializers like 0x10 don't silently
                        # collapse to 0.
                        m_val = int(m_val_str, 0)
                    except ValueError:
                        m_val = 0
                    members.append(EnumMember(name=m_name, value=m_val))
            enums.append(
                EnumType(
                    name=name,
                    members=members,
                    source_location=self._source_location(el),
                    # castxml's `scoped="1"` marks a C++11 `enum class`/`enum
                    # struct` (as opposed to a plain C-style enum). Header
                    # mode always knows the answer, so this is a concrete
                    # bool (never None on the castxml path).
                    is_scoped=el.get("scoped") == "1",
                    # See RecordType.deprecated for the message-text convention.
                    deprecated=_deprecation_marker(el),
                    # See RecordType.qualified_name for the bare-vs-qualified
                    # name convention this mirrors.
                    qualified_name=self._qualified_type_name(el, leaf_name=name),
                )
            )
        return enums

    def _underlying_type_name(self, id_: str, depth: int = 0) -> str:
        """Follow typedef chains to the concrete base type name."""
        if depth > 20:
            return "?"
        el = self._resolve(id_)
        if el is None:
            return "?"
        if el.tag == "Typedef":
            return self._underlying_type_name(el.get("type", ""), depth + 1)
        return self._type_name(id_)

    def parse_typedefs(self) -> dict[str, str]:
        typedefs: dict[str, str] = {}
        for el in self._typedef_els:
            name = el.get("name", "")
            if not name:
                continue
            if self._is_builtin_element(el):
                continue
            type_id = el.get("type", "")
            # Flatten typedef chains: alias → alias2 → int  stored as  alias → int
            underlying = self._underlying_type_name(type_id) if type_id else "?"
            typedefs[name] = underlying
        return typedefs

    def _iter_public_typedefs(self) -> list[tuple[str, str, str]]:
        """``(qualified_name, underlying_type, declaring_header)`` for every
        *public-header* typedef — the provenance-scoped source of truth shared by
        :meth:`parse_public_typedefs` and :meth:`parse_public_typedef_headers`.

        Unlike :meth:`parse_typedefs` (unscoped, used by the L2 snapshot), this is
        filtered to the public surface so the L4 extractor does not pull
        private/system aliases onto the linked source surface (ADR-030 #3).
        """
        if not self._have_public_set:
            return []
        out: list[tuple[str, str, str]] = []
        for el in self._typedef_els:
            name = el.get("name", "")
            if not name:
                continue
            if self._is_builtin_element(el):
                continue
            if el.get("access") in ("private", "protected"):
                continue
            if not self._decl_is_public(el):
                continue
            type_id = el.get("type", "")
            underlying = self._underlying_type_name(type_id) if type_id else "?"
            out.append(
                (
                    self._qualified_name(el),
                    underlying,
                    header_from_location(self._source_location(el)) or "",
                )
            )
        return out

    def parse_public_typedefs(self) -> dict[str, str]:
        """Public-header typedef aliases ``qualified_name → underlying type`` (ADR-030 #3)."""
        return {name: target for name, target, _ in self._iter_public_typedefs()}

    def parse_public_typedef_headers(self) -> dict[str, str]:
        """Public typedef qualified name → declaring header (provenance, ADR-030 #3)."""
        return {name: header for name, _, header in self._iter_public_typedefs()}
