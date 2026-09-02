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

"""Shared parser state for the clang header-AST backend (ADR-061 D9).

Owns the one type every entity-parsing module in this package receives —
:class:`_Decl`, a categorized AST node plus its walk context (scope, file,
access, extern-C/friend/template flags) — and the small set of
node-inspection primitives more than one entity kind's parsing reads:
built-in-origin filtering, a declaration's own type spelling, its source
location, its deprecation message, member access level, exported-symbol
visibility, and a decl's own qualified-name spelling. None of these open the
AST document or drive traversal themselves; ``dumper_clang.py``'s ``_walk``
still does that, populating the categorized ``_Decl`` lists this package's
entity modules (``enums.py``, ``functions.py``) read.

Also owns :class:`RecordVtableIndex` — the lazily-built, memoized record/
specialization/vtable indices ``dumper_clang.py`` used to carry as four
separate ``self._*`` caches. Read by BOTH function-entity parsing
(``functions.py``'s ``is_virtual`` override recovery, via
``virtual_mangled_names()``) and record-entity parsing (base-class vtable
lookup in ``dumper_clang.py``'s still-unmigrated ``_build_record``, via
``base_lookup_index()`` directly) — exactly the "read by more than one
entity kind" rule this module states above, so it lives here rather than in
``functions.py``.

Deliberately excludes ``dumper_clang._evaluated_int_value`` and
``dumper_clang_expr._initializer_value`` — see the comment above where the
former would otherwise live for why moving either here would recreate a
real ``extract -> compare`` layering violation (both live in
``dumper_clang_expr.py``, which imports ``diff_cxx_rules`` for
``itanium_scope_components``). ``functions.py::parse_functions`` takes its
own default-value evaluator as an explicit parameter for the same reason
``enums.py::parse_enums`` takes ``evaluate_int``.
"""

from __future__ import annotations

from typing import Any

from ....dumper_clang_vtable import build_vtable, is_record_definition
from ....model import AccessLevel, ScopeOrigin, Visibility
from ....model.identity import ScopePath
from ....name_classification import strip_anonymous_type_location
from ....provenance import classify_origin, header_from_location
from .templates import build_specialization_index

#: Pseudo-files clang attributes builtin / command-line declarations to.
BUILTIN_FILES = frozenset(
    {"<built-in>", "<builtin>", "<command line>", "<scratch space>"}
)


class _Decl:
    """A categorized clang AST decl node plus its walk context.

    ``__slots__`` keeps the per-decl overhead low on large headers.
    """

    __slots__ = (
        "access",
        "extern_c",
        "file",
        "in_friend",
        "in_template",
        "node",
        "scope",
        "scope_path",
        "template_param_kinds",
        "template_type_param_names",
    )

    def __init__(
        self,
        node: dict[str, Any],
        scope: tuple[str, ...],
        file: str,
        access: str,
        extern_c: bool = False,
        in_friend: bool = False,
        in_template: bool = False,
        scope_path: ScopePath = (),
        template_param_kinds: tuple[str, ...] = (),
        template_type_param_names: tuple[str, ...] = (),
    ) -> None:
        self.node = node
        self.scope = scope
        # The same containing scopes as ``scope``, as typed
        # ``model.identity`` segments recorded at the point each scope was
        # entered (ADR-063 Phase 2). Purely additive parser-internal state:
        # ``scope`` remains what every existing consumer reads and what every
        # ``qualified_name`` is built from, and nothing constructs an
        # ``EntityId`` from this yet. Defaults to ``()`` so the call sites
        # that build a ``_Decl`` directly (tests, sibling entity modules)
        # need no change.
        self.scope_path = scope_path
        self.file = file
        self.access = access
        # True when the decl sits inside an ``extern "C"`` linkage spec — an
        # authoritative C-linkage signal that beats the mangled==name heuristic.
        self.extern_c = extern_c
        # True when the decl is reached through a ``friend`` declaration: the
        # function is ADL-only ("hidden friend") and the diff treats it apart
        # from the ordinary public surface.
        self.in_friend = in_friend
        # True when the decl is the pattern body of a class template (e.g. the
        # CXXRecordDecl inside a ClassTemplateDecl): same kind and bare name as
        # an ordinary record, but its members reference dependent template-
        # parameter types with no fixed layout for any one instantiation. Kept
        # as a RecordType (its field *names*/*types* are still real public
        # surface — case17_template_abi's field-added detection relies on it)
        # but flagged so a name-based match (e.g. DWARF layout backfill)
        # never treats it as an ordinary concrete type (Codex review).
        self.in_template = in_template
        # The immediate enclosing FunctionTemplateDecl's own parameter-KIND
        # signature (ADR-063 Phase 2; see
        # extract.headers.clang.templates.function_template_param_kinds),
        # or () for a non-template declaration. Set only on the direct
        # FunctionDecl/CXXMethodDecl child of a FunctionTemplateDecl -- never
        # inherited further down -- since only that one declaration needs a
        # discriminator no ordinary parameter list or mangled name provides.
        self.template_param_kinds = template_param_kinds
        # The same enclosing FunctionTemplateDecl's own TOP-LEVEL type/
        # template-template parameter NAMES (companion to
        # template_param_kinds above; see
        # extract.headers.clang.functions.function_template_type_param_names),
        # used to canonicalize a dependent ORDINARY parameter type against a
        # pure template-parameter rename the identical way template_param_kinds'
        # own non-type-parameter entries already are.
        self.template_type_param_names = template_type_param_names


def is_builtin_file(file: str) -> bool:
    return file in BUILTIN_FILES


def qualtype(node: dict[str, Any]) -> str:
    """A declaration's own ``type.qualType`` spelling -- the single choke
    point every field/param/variable/function type string in this module is
    built from (`_parse_fields`, `_parse_functions`'s own signature and
    param loop, `parse_variables`, `parse_constants`).

    Stripped via :func:`strip_anonymous_type_location`: verified against
    real Clang 18 (``-ast-dump=json``) that a lambda closure type embedded in
    a type spelling -- e.g. a class-template specialization instantiated
    with a lambda argument, ``Guard<decltype([]{})>`` -- prints its
    ``qualType`` as ``"(lambda at <path>:<line>:<col>)"`` (Clang's
    TypePrinter, the same diagnostic-style spelling castxml's own XML `name`
    attribute uses, confirmed on a `FieldDecl` whose declared type IS the
    lambda type parameter). Left unstripped, that absolute, checkout-
    dependent path leaks into `TypeField.type`/`Param.type`/`Variable.type`/
    `Function.return_type`, so two checkouts of the identical, unchanged
    declaration would produce two different type spellings and could
    manufacture a spurious finding on the field/param/variable/function
    carrying it -- the same class of bug `dumper_castxml.py`'s own
    `strip_anonymous_type_location` calls guard against for its `name`/
    `qualified_name` fields, just reached through this backend's type-string
    printer rather than its declaration-name attribute (which, unlike
    castxml's, never itself embeds a location -- confirmed empirically: a
    template specialization's own `name` node stays the bare template name,
    e.g. ``"Guard"``, never ``"Guard<(lambda at ...)>"``).
    """
    type_obj = node.get("type")
    if isinstance(type_obj, dict):
        return strip_anonymous_type_location(str(type_obj.get("qualType", "")))
    return ""


def node_line(node: dict[str, Any]) -> int:
    loc = node.get("loc")
    if isinstance(loc, dict):
        line = loc.get("line")
        if isinstance(line, int):
            return line
        # Mirror `dumper_clang._node_file`'s macro/expansion fallback so a decl
        # whose file comes from expansionLoc/spellingLoc gets its line from
        # the same place.
        for sub in ("expansionLoc", "spellingLoc"):
            s = loc.get(sub)
            if isinstance(s, dict) and isinstance(s.get("line"), int):
                return int(s["line"])
    return 0


def source_location(entry: _Decl) -> str | None:
    """``file:line`` for a decl, or the bare file when clang omits the line.

    clang makes ``loc.line`` sticky just like ``loc.file`` — a declaration
    nested on the same source line as its parent (e.g. a ``static constexpr``
    member of a one-line ``struct``) often carries the inherited file but no
    ``line``. Dropping the whole location there would strip provenance and
    make ``_decl_is_public`` discard an otherwise-public constant/type, so
    the file is kept (``header_from_location`` tolerates a path with no
    ``:line`` suffix). Returns ``None`` only when there is no file at all.
    """
    if not entry.file:
        return None
    line = node_line(entry.node)
    return f"{entry.file}:{line}" if line else entry.file


def clang_deprecated_message(node: dict[str, Any]) -> str | None:
    """Deprecation message for *node*, or ``None`` if not deprecated (G31
    Phase C schema-completeness audit) — the direct-clang backend's
    counterpart to ``dumper_castxml._deprecation_marker``, matching its exact
    three-way convention (message text / ``""`` for a bare, messageless
    ``[[deprecated]]`` / ``None`` for not deprecated) so the two backends'
    ``Function.deprecated``/``Variable.deprecated``/``TypeField.deprecated``/
    ``RecordType.deprecated``/``EnumType.deprecated`` agree.

    Verified against real ``clang -ast-dump=json`` output (Clang 18) before
    wiring this up: unlike castxml (a compound ``attributes`` string plus a
    separate ``deprecation="..."`` XML attribute only for a non-empty
    message), clang emits a ``DeprecatedAttr`` child node under the
    declaration's own ``"inner"`` list — present for both the bare and
    messaged forms, with an optional ``message`` string key present *only*
    for the messaged form (confirmed empirically: a bare ``[[deprecated]]``'s
    ``DeprecatedAttr`` node carries no ``message`` key at all, not an empty
    string).
    """
    for child in node.get("inner", []) or []:
        if isinstance(child, dict) and child.get("kind") == "DeprecatedAttr":
            return str(child.get("message", ""))
    return None


def access_level(access: str) -> AccessLevel:
    """Tri-state member access from a decl's own walk-context access string.

    Read by function-entity parsing (``functions.py``) and record-entity
    field parsing (``dumper_clang.py``'s still-unmigrated ``_parse_fields``)
    alike — a "more than one entity kind" helper per this module's own rule.
    """
    if access == "protected":
        return AccessLevel.PROTECTED
    if access == "private":
        return AccessLevel.PRIVATE
    return AccessLevel.PUBLIC


def default_record_access(node: dict[str, Any]) -> str:
    """Default member access before any ``AccessSpecDecl`` (``class`` ->
    private). Read by both the shared ``_walk`` traversal (``dumper_clang.py``)
    and record-entity field parsing (``records.py``) -- a second, previously
    undetected copy of this predicate is exactly the divergence risk this
    module's own "more than one entity kind" rule exists to close (Codex
    review, PR #940)."""
    return "private" if node.get("tagUsed") == "class" else "public"


def symbol_candidates(mangled: str) -> tuple[str, ...]:
    """The mangled name plus, on a leading underscore, its de-prefixed form.

    See :func:`visibility`'s docstring for the Mach-O prefix-stripping
    reasoning this supports.
    """
    if not mangled:
        return ()
    if mangled.startswith("_"):
        return (mangled, mangled[1:])
    return (mangled,)


#: OS-component names (lowercased) that mark a Darwin/Apple target --
#: checked against each ``-``-separated triple COMPONENT, not the raw
#: string, and via ``startswith`` rather than exact equality, since the OS
#: component routinely carries a trailing version suffix (``"macosx13.0"``,
#: ``"ios15.0"``, ``"darwin20.6.0"``). Deliberately independent of the
#: VENDOR component ("apple") -- see :func:`is_darwin_target`'s own
#: docstring for the confirmed, real triple this distinction matters for.
_DARWIN_OS_NAMES = ("darwin", "macos", "ios", "tvos", "watchos")


def is_darwin_target(target_triple: str | None) -> bool:
    """Whether *target_triple* names a Darwin (macOS/iOS/...) target.

    Used to gate a leading-underscore de-prefixing decision that changes
    an *identity* determination (extern-"C" recognition), not merely an
    export-table membership test the way :func:`symbol_candidates`'s own
    unconditional tolerant match does (Codex review, sixteenth round,
    fresh evidence): on a NON-Darwin target, ``raw_mangled ==
    "_" + name`` is not a linker-decoration artifact at all -- it is
    exactly what a real, explicit ``asm("_foo")`` label or a genuinely
    underscore-prefixed real mangled name looks like, and reinterpreting
    that as C linkage would discard a real, distinct identity (castxml
    still carries a real ``("mangled", "_foo")`` for the same
    declaration, so treating it as ``("extern_c",)`` on clang's side
    would newly DISAGREE with castxml instead of agreeing with it). Only
    Darwin's linker actually prepends this underscore as pure platform
    decoration with no identity content of its own, so only there is
    stripping it safe.

    **Checks the triple's OS component, not merely an ``"apple"`` vendor
    (Codex review, nineteenth round, fresh evidence).** A target such as
    ``"x86_64-unknown-darwin"`` is a valid triple clang accepts and mangles
    exactly like a real Mach-O target (Darwin's OS-level linker behavior,
    not anything vendor-specific), but its vendor component is
    ``"unknown"``, not ``"apple"`` -- an ``"apple" in triple`` substring
    test missed it, so the de-prefix fallback was wrongly skipped and this
    normalizer fell back to a real, non-Darwin mangled identity for a
    genuinely plain-C declaration castxml still recognized as extern "C".
    Splitting on ``"-"`` and checking each COMPONENT (rather than a bare
    substring test over the whole triple) also avoids a false match from
    an unrelated component that merely happens to CONTAIN one of these
    tokens.
    """
    if not target_triple:
        return False
    components = target_triple.lower().split("-")
    return "apple" in components or any(
        component.startswith(_DARWIN_OS_NAMES) for component in components
    )


def visibility(
    exported_dynamic: set[str],
    exported_static: set[str],
    mangled: str,
    name: str = "",
) -> Visibility:
    """Resolve API visibility from the binary's exported-symbol tables.

    Identical policy to the castxml parser (``castxml.location.visibility``)
    so a clang- and a castxml-derived snapshot classify the same declaration
    the same way.

    Mach-O quirk: clang's ``mangledName`` carries the platform global-symbol
    prefix (``__ZN3lib3addEii`` on macOS), but ``_dump_macho`` strips the
    single leading underscore off the export set to match castxml's
    prefix-free names. So each mangled candidate is matched both as-is (ELF)
    **and** with one leading underscore removed (Mach-O), trying the as-is
    form first so an ELF Itanium ``_Z…`` name never spuriously matches the
    stripped variant.

    Read by function-entity parsing (``functions.py``) and variable/constant
    parsing (``dumper_clang.py``'s still-unmigrated ``parse_variables``/
    ``parse_constants``) alike.
    """
    for cand in symbol_candidates(mangled):
        if cand in exported_dynamic:
            return Visibility.PUBLIC
    if name and name in exported_dynamic:
        return Visibility.PUBLIC
    for cand in symbol_candidates(mangled):
        if cand in exported_static:
            return Visibility.ELF_ONLY
    if name and name in exported_static:
        return Visibility.ELF_ONLY
    return Visibility.HIDDEN


def qualified_name(entry: _Decl) -> str:
    """``"::".join(scope + [name])`` for a decl — the same spelling
    ``RecordType.qualified_name``/vtable-lookup keys are built from.

    Read by function-entity parsing (specialization-method qualification),
    record-entity parsing (``RecordVtableIndex.record_index`` below), and
    constant/typedef parsing (``dumper_clang.py``'s still-unmigrated
    ``parse_constants``/``parse_typedefs_qualified``) alike.
    """
    name = entry.node.get("name", "")
    return "::".join([*entry.scope, name]) if entry.scope else name


def decl_is_public(
    entry: _Decl,
    pub_header_segs: list[tuple[str, ...]],
    pub_dir_segs: list[tuple[str, ...]],
    have_public_set: bool,
) -> bool:
    """True if *entry*'s declaring header classifies as a public header.

    Uses the shared provenance segment matcher (``classify_origin`` /
    ``header_from_location``, the same ones ``dumper_castxml.py``'s own
    ``decl_is_public`` — see ``extract.headers.castxml.location`` — reads),
    so build-prefixed paths and umbrella-included public headers match while
    system/private headers do not. Read by more than one entity kind's
    parsing on this backend too (constant parsing in ``dumper_clang.py``'s
    still-unmigrated ``parse_constants``, and record-entity parsing in
    ``extract.headers.clang.records``), same "more than one entity kind"
    rule this module's own docstring states for ``access_level``/
    ``visibility``/``qualified_name`` above. Takes the three
    ``provenance.build_public_set`` outputs explicitly rather than a
    wrapping context object -- clang's own convention (``functions.py``/
    ``enums.py``) is explicit parameters, not a second, competing context
    shape.
    """
    sh = header_from_location(source_location(entry))
    if not sh:
        return False
    return (
        classify_origin(
            sh, pub_header_segs, pub_dir_segs, have_public_set=have_public_set
        )
        == ScopeOrigin.PUBLIC_HEADER
    )


class RecordVtableIndex:
    """Lazily-built, memoized record/specialization/vtable indices shared
    across the clang backend's entity-parsing modules.

    Four caches, each built at most once per parser instance and each
    depending only on the one before it: ``record_index()`` (ordinary
    ``CXXRecordDecl``/``RecordDecl`` nodes, keyed by qualified name, a
    complete definition always winning over a forward-declaration stub for
    the same key — see the method's own docstring for why),
    ``specialization_record_index()`` (the same shape over concrete
    ``ClassTemplateSpecializationDecl`` nodes, a different clang node kind
    ``record_index()`` never collects), ``base_lookup_index()`` (their
    merge, for :func:`dumper_clang_vtable.build_vtable`'s base-lookup
    recursion), and ``virtual_mangled_names()`` (every mangled name
    occupying a slot in ANY record's reconstructed vtable across the whole
    TU — recovers a signature-matched override with neither a `virtual` nor
    an `override` keyword, which clang's JSON gives no other signal for).

    Memoized rather than recomputed per call: ``dumper_clang.py``'s
    ``_build_record`` calls ``base_lookup_index()`` once per record, and
    ``virtual_mangled_names()`` needs the SAME merged index for the whole
    translation unit — an unmemoized version paid an O(records × index
    size) cost that rebuilding this class fixed once (CodeRabbit review,
    fresh evidence, from when this state lived directly on
    ``_ClangAstParser``).
    """

    def __init__(
        self,
        root: dict[str, Any],
        records: list[_Decl],
        template_param_kinds_by_qualname: dict[str, list[str | None]],
        template_param_defaults_by_qualname: dict[str, list[str | None]],
        template_param_names_by_qualname: dict[str, list[str | None]],
    ) -> None:
        self._root = root
        # Same list object `dumper_clang.py`'s `_walk` populates in place —
        # constructed before the walk runs, read only lazily afterward, the
        # same timing the four caches below already relied on when this
        # state lived directly on `_ClangAstParser`.
        self._records = records
        self._template_param_kinds_by_qualname = template_param_kinds_by_qualname
        self._template_param_defaults_by_qualname = template_param_defaults_by_qualname
        self._template_param_names_by_qualname = template_param_names_by_qualname
        self._record_by_qualname: dict[str, dict[str, Any]] | None = None
        self._specialization_by_qualname: dict[str, dict[str, Any]] | None = None
        self._base_lookup: dict[str, dict[str, Any]] | None = None
        self._virtual_mangled: frozenset[str] | None = None

    def record_index(self) -> dict[str, dict[str, Any]]:
        """Lazily-built ``qualified name -> node`` index over every parsed
        record, for :func:`dumper_clang_vtable.build_vtable`'s base-lookup
        recursion.

        A forward declaration (``struct A;``) and its later complete
        definition (``struct A { ... };``) share the same qualname and both
        land in ``self._records`` -- confirmed with a real clang build that
        clang emits BOTH `CXXRecordDecl` nodes for exactly this shape, the
        forward one carrying neither `completeDefinition` nor any member
        children. A plain first-registration-wins policy silently kept
        whichever was encountered first, which is the forward decl whenever
        one precedes the definition in source order -- the common style --
        losing every virtual method the real definition carries (Codex
        review, fresh evidence: `struct A; struct A { virtual void f(); };`
        resolved to an empty `vtable` for A and any of its derived classes).
        A complete definition always wins over a forward-declaration stub
        for the same qualname, regardless of which one was walked first;
        ties among non-definitions (there's at most one real forward decl
        in practice, but this stays defensive) keep the first seen.
        """
        if self._record_by_qualname is None:
            idx: dict[str, dict[str, Any]] = {}
            for entry in self._records:
                name = str(entry.node.get("name") or "")
                if not name:
                    continue
                qualname = qualified_name(entry)
                existing = idx.get(qualname)
                if existing is None or (
                    not is_record_definition(existing)
                    and is_record_definition(entry.node)
                ):
                    idx[qualname] = entry.node
            self._record_by_qualname = idx
        return self._record_by_qualname

    def specialization_record_index(self) -> dict[str, dict[str, Any]]:
        """Lazily-built, memoized :func:`.templates.build_specialization_index`
        over this parser's own AST root -- see that function's docstring for
        the full "why". Passes through the
        param-kinds/param-defaults indices already computed eagerly by
        ``dumper_clang.py``'s ``__init__`` (for ``_walk``'s own
        specialization-scoping use) instead of paying for a second
        whole-AST pass over each.
        """
        if self._specialization_by_qualname is None:
            self._specialization_by_qualname = build_specialization_index(
                self._root,
                self._template_param_kinds_by_qualname,
                self._template_param_defaults_by_qualname,
                self._template_param_names_by_qualname,
                is_record_definition=is_record_definition,
            )
        return self._specialization_by_qualname

    def base_lookup_index(self) -> dict[str, dict[str, Any]]:
        """Lazily-built, memoized merge of ``record_index()`` +
        ``specialization_record_index()``, for :func:`dumper_clang_vtable.
        build_vtable`'s base-lookup recursion.

        Safe to merge into one dict: an ordinary record's qualname never
        contains ``"<"``, so the two key spaces never collide. An ordinary
        record wins on the rare case both indexes somehow produced the same
        key (shouldn't occur given the above, but a plain record is always
        the more trustworthy of the two if it ever did).
        """
        if self._base_lookup is None:
            merged = dict(self.specialization_record_index())
            merged.update(self.record_index())
            self._base_lookup = merged
        return self._base_lookup

    def virtual_mangled_names(self) -> frozenset[str]:
        """Every mangled name occupying a slot in ANY record's reconstructed
        vtable, across the whole TU.

        The gap this closes (Codex review, fresh evidence, real end-to-end
        repro): :func:`dumper_clang_vtable.build_vtable` correctly
        recognizes a signature-matched override with no `virtual`/`override`
        keyword and replaces the inherited slot with the derived method's
        own mangled name -- but that knowledge lived only inside the vtable
        list itself. ``functions.py::parse_functions``'s own
        ``Function.is_virtual`` still reads clang's raw, keyword-only
        ``node.get("virtual")`` -- the exact signal this class exists to
        work around -- so ``diff_cxx_rules.vtable_slot_is_override_reuse()``
        (which requires both sides' ``Function.is_virtual`` to be ``True``
        before recognizing a slot as reused rather than changed) rejected
        the reuse and ``diff_types._diff_type_vtable`` emitted a spurious
        ``TYPE_VTABLE_CHANGED`` BREAKING finding for exactly the
        no-keyword-override case this class was built to recognize.

        Only ever WIDENS ``is_virtual`` from ``False`` to ``True`` (the
        caller still ORs this in, never overrides an already-``True``
        reading) -- purely additive, so it cannot suppress a real virtuality
        signal, only recover one clang's JSON otherwise drops silently.
        """
        if self._virtual_mangled is None:
            idx = self.base_lookup_index()
            names: set[str] = set()
            for qualname in idx:
                names.update(build_vtable(qualname, idx))
            self._virtual_mangled = frozenset(names)
        return self._virtual_mangled


# Deliberately NOT here: `dumper_clang._evaluated_int_value`. It walks
# clang's wrapper-expression chain via `_WRAPPER_EXPR_KINDS`
# (`dumper_clang_expr.py`), which itself imports `diff_cxx_rules`
# (classified `compare`) for `itanium_scope_components` — the exact
# "shared piece entangled with another layer" case `extract/AGENTS.md`
# names as the pattern to avoid rather than paper over with a new import.
# Moving `_evaluated_int_value` here would recreate that `extract -> compare`
# edge one module down. `enums.py.parse_enums` instead takes the evaluator
# as an explicit parameter, supplied by its one caller
# (`dumper_clang._ClangAstParser.parse_enums`, which already owns it) — the
# same "context is whatever the entity module actually needs, not
# whatever's convenient to import" principle this package's `context.py`
# modules apply everywhere else, just expressed as a parameter instead of
# a state field here since the value is a pure function, not parser state.
