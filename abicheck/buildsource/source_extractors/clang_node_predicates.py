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
"""Node classification for the clang AST walk: the kind-set constants, the
public-surface classification context (``_ClassifyContext``), the
source-location helper, and the pure per-node predicates that decide whether
a clang AST node becomes an ABI entity. Pure: no subprocess."""

from __future__ import annotations

from typing import Any

from ..build_evidence import CompileUnit
from ..source_abi import (
    SourceLocation,
)
from ._argv import (
    split_public_roots,
)
from .clang_command import dump_linkage_context
from .clang_nodes import (
    _entity_names,
    _mangled,
    _signature,
    _signature_desugared,
)
from .clang_public_roots import _file_segments, _matches_exact_public_header

#: clang extractor schema/behaviour version, recorded in the dump provenance and
#: folded into the per-TU cache key (ADR-030 D8). Bump on ANY recipe change so a
#: stale ``--cache-dir`` never reuses an old dump. 0.8: also folds in the
#: resolved compiler override (Codex review). 0.9: the reconstructed command now
#: appends ``-fsycl-host-only`` for a SYCL-enabled Intel oneAPI invocation
#: (Codex review) -- a pre-0.9 cache entry for the same TU was built without
#: that flag and must not be reused as-is. 0.10/0.11: a real build's own
#: recorded ``-fsycl-host-only``/``-fsycl-device-only`` (Intel-only SYCL
#: pass-selector flags carried through via ``abi_relevant_flags``) on a
#: non-Intel invoked binary now degrades that one TU (a clear
#: SourceExtractionError) instead of failing on the raw "unknown argument".
#: 0.10 first shipped this only for ``-fsycl-device-only``, silently
#: STRIPPING a recorded ``-fsycl-host-only`` instead -- confirmed against a
#: real clang install (Codex review) that this was itself wrong: stock
#: clang's bare ``-fsycl`` defines ``__SYCL_DEVICE_ONLY__`` (device context
#: by default), so dropping just the host-only selector silently replayed
#: an explicitly host-pinned TU as device code. 0.11 raises for both
#: selectors symmetrically; a bare, unselected ``-fsycl`` (ambiguous, no
#: explicit pin either way) is left carried through as-is, matching the L2
#: header-AST backend's own established "accepted approximation" for that
#: shape (see ``needs_sycl_host_only``'s own docstring). 0.12:
#: ``compile_invocation._carry_abi_relevant_flags`` (shared by castxml/clang) no longer
#: dedups a repeated flag WITHIN ``abi_relevant_flags`` itself -- a real,
#: layered build config can legitimately record the same toggle-style flag
#: more than once (e.g. ``-fno-sycl -fsycl -fno-sycl``), and the old dedup
#: silently dropped every repeat, corrupting last-flag-wins state for any
#: such pair, not just SYCL (Codex review). 0.13: ``_stamp_fact_set_and_
#: coverage`` now stamps a real ``compiler_family`` (``"intel-llvm"`` for
#: Intel's oneAPI fork, ``"clang"`` unchanged otherwise) instead of always
#: the ``default_fact_set`` default -- a cache entry produced under 0.12 or
#: earlier carries the stale ``"clang"`` label baked into its persisted
#: ``fact_set`` and, on a cache HIT, is returned as-is by
#: ``source_replay._replay_cache_lookup`` without ever re-running
#: ``_stamp_fact_set_and_coverage`` (Codex review, PR #756: a persistent
#: ``--source-abi-cache-dir``/``ABICHECK_L4_CACHE_DIR`` reused across an
#: abicheck upgrade would otherwise silently keep serving the old label
#: forever, since ``compute_tu_cache_key`` doesn't fold ``compiler_family``
#: in on its own -- only the extractor version invalidates it).


#: AST node kinds clang emits for the entities we fingerprint. Includes the C++
#: special members (constructor/destructor/conversion) so a change to a public
#: ``Widget(int n = 1)`` default, or an inline constructor body edit, is detected
#: — not just ordinary functions/methods (Codex review #339, P2).
_FUNCTION_NODE_KINDS = frozenset(
    {
        "FunctionDecl",
        "CXXMethodDecl",
        "CXXConstructorDecl",
        "CXXDestructorDecl",
        "CXXConversionDecl",
    }
)


_TEMPLATE_NODE_KINDS = frozenset({"FunctionTemplateDecl", "ClassTemplateDecl"})


#: Decl contexts we descend into to reach members/nested decls, tracking the
#: enclosing scope name so a member's qualified name is built (``ns::Cls::f``).
_SCOPE_NODE_KINDS = frozenset(
    {"NamespaceDecl", "CXXRecordDecl", "ClassTemplateDecl", "LinkageSpecDecl"}
)


class _ClassifyContext:
    """Public-surface classification for clang file paths (ADR-024 / ADR-030)."""

    def __init__(
        self,
        public_header_roots: list[str],
        *,
        target_triple: str | None = None,
        is_cxx: bool = False,
    ) -> None:
        from ...extract.declaration_provenance import build_public_set

        # The triple and language the AST dump ran under: they decide the
        # Darwin-aware C-linkage rule for every declaration's linker name.
        self.target_triple = target_triple
        self.is_cxx = is_cxx

        # A public root may be a *directory* (`--headers include/`). Feeding it to
        # build_public_set as a header file would never match a decl under it
        # (`include` vs `include/api.h`), dropping the whole public include tree;
        # split file roots from directory roots first (Codex review #339, P2).
        file_roots, dir_roots = split_public_roots(public_header_roots)
        self.exact_header_segs = [_file_segments(root) for root in file_roots]
        self.exact_header_segs = [seg for seg in self.exact_header_segs if seg]
        _, self.dir_segs, self.have_set = build_public_set([], dir_roots)
        self.have_set = self.have_set or bool(self.exact_header_segs)

    @classmethod
    def for_unit(
        cls, public_header_roots: list[str], compile_unit: CompileUnit
    ) -> _ClassifyContext:
        """The context of one compile unit's AST dump: the triple and language the
        dump ran under decide the Darwin-aware C-linkage rule."""
        target_triple, is_cxx = dump_linkage_context(compile_unit)
        return cls(public_header_roots, target_triple=target_triple, is_cxx=is_cxx)

    def entity_names(
        self, name: str, mangled: str, node: dict[str, Any]
    ) -> dict[str, str]:
        """A declaration's ``names``, linker identity included."""
        return _entity_names(
            name, mangled, node, self.target_triple, is_cxx=self.is_cxx
        )

    def classify(self, file: str) -> tuple[str, str, bool]:
        """Return ``(visibility, origin_label, api_relevant)`` for a file.

        Mirrors the castxml extractor: a header that is both public and generated
        stays public but is marked ``GENERATED`` (so ``generated_header_changed``
        owns it); a generated *private* header (not in the public set) is demoted
        off the public surface.
        """
        from ...extract.declaration_provenance import (
            classify_origin,
            is_generated_header,
        )
        from ...model import ScopeOrigin

        if _matches_exact_public_header(file, self.exact_header_segs):
            if is_generated_header(file):
                return "generated", "GENERATED", True
            return "public_header", "PUBLIC_HEADER", True
        origin = classify_origin(file, [], self.dir_segs, have_public_set=self.have_set)
        if origin == ScopeOrigin.PUBLIC_HEADER and is_generated_header(file):
            return "generated", "GENERATED", True
        if origin == ScopeOrigin.PUBLIC_HEADER:
            return "public_header", "PUBLIC_HEADER", True
        if origin == ScopeOrigin.GENERATED:
            return "private_header", "PRIVATE_HEADER", False
        return "unknown", "UNKNOWN", False


def _location(file: str, line: int, origin_label: str) -> SourceLocation:
    return SourceLocation(path=file, line=line, origin=origin_label)


#: C++ access specifiers that hide a member from consumers. A private/protected
#: member cannot be called or its inline body relied on, so it must stay off the
#: L4 public surface even when declared in a public header (Codex review #339,
#: P2). ``""``/``"none"``/``"public"`` mean "no restriction" (free functions,
#: namespace-scope decls, public members).
_NON_PUBLIC_ACCESS = frozenset({"private", "protected"})


def _is_accessible(access: str) -> bool:
    """Whether a decl with this C++ member-access is reachable by consumers."""
    return access not in _NON_PUBLIC_ACCESS


def _default_member_access(record: dict[str, Any]) -> str:
    """Default member access for a record's body before any ``AccessSpecDecl``.

    ``class`` defaults to private; ``struct``/``union`` default to public
    (clang records this as ``tagUsed``). Determines the access of members that
    appear before the first explicit ``public:``/``private:`` section.
    """
    return "private" if record.get("tagUsed") == "class" else "public"


def _is_template_node(kind: str | None, name: str) -> bool:
    """Return ``True`` when this AST node is a named template declaration.

    Template bodies are fingerprinted whole; callers must skip descent into
    the templated pattern to avoid re-emitting the inner FunctionDecl/Record.
    """
    return kind in _TEMPLATE_NODE_KINDS and bool(name)


def _is_function_node(kind: str | None, name: str, accessible: bool) -> bool:
    """Return ``True`` for a named, accessible function/method node."""
    return kind in _FUNCTION_NODE_KINDS and bool(name) and accessible


def _is_constexpr_var_node(
    kind: str | None, name: str, node: dict[str, Any], accessible: bool
) -> bool:
    """Return ``True`` for a named, accessible ``constexpr`` variable node."""
    return (
        kind == "VarDecl" and bool(name) and bool(node.get("constexpr")) and accessible
    )


#: Decl containers whose direct ``VarDecl`` children have linkage (become real
#: symbols). A ``VarDecl`` reached anywhere else in the walk — inside a function
#: body's ``CompoundStmt``/``DeclStmt`` — is a stack local with no symbol and is
#: never an ABI variable (``TranslationUnitDecl`` is the walk root's own kind).
_VARIABLE_SCOPE_KINDS = frozenset(
    {"TranslationUnitDecl", "NamespaceDecl", "CXXRecordDecl", "LinkageSpecDecl"}
)


def _mangled_has_internal_linkage(mangled: str) -> bool:
    """Return ``True`` when an Itanium *mangled* name marks internal linkage.

    clang has already done the linkage analysis and encoded it two ways, both
    handled here:

    * the GCC/clang seniority marker ``L`` prefixing the entity's own
      ``<unqualified-name>`` — a namespace/file-scope ``static`` *or* a
      namespace-scope ``const`` without ``extern`` (``_ZN2nsL7g_constE``,
      ``_ZL1xE``);
    * an **anonymous-namespace** component ``_GLOBAL__N_`` (``namespace { int x; }``
      mangles as ``_ZN12_GLOBAL__N_11xE``) — internal linkage with *no* ``L``
      marker (Codex review).

    Such an entity never appears in the dynamic symbol table, so emitting it as a
    ``variable`` would populate ``decls_without_symbol`` and risk a spurious
    ``source_binary_provenance_mismatch`` against the correct binary. This parses
    by Itanium length prefixes — so an ``L`` *inside* a source name (a namespace
    literally ending in ``L``) is never miscounted — and bails to ``False``
    (external, keep) on any exotic production, so a real export is never dropped.
    """
    m = mangled
    if not m.startswith("_Z"):
        return False
    # Anonymous namespace: a reserved compiler component, so a plain substring
    # test is unambiguous (a user cannot name an entity `_GLOBAL__N_`).
    if "_GLOBAL__N_" in m:
        return True
    i = 2
    n = len(m)
    if i < n and m[i] == "N":  # nested-name: N [CV/ref] <prefix> <name> E
        i += 1
        while i < n and m[i] in "rVKO":  # cv-qualifiers / ref-qualifiers
            i += 1
    while i < n:
        c = m[i]
        if c == "E":
            break
        if c == "L":  # seniority marker before a <source-name> ⇒ internal linkage
            return i + 1 < n and m[i + 1].isdigit()
        if c.isdigit():  # <source-name> ::= <length> <identifier> — skip wholesale
            j = i
            length = 0
            while j < n and m[j].isdigit():
                length = length * 10 + (ord(m[j]) - 48)
                j += 1
                if length > n - j:  # malformed length ⇒ bail safe
                    return False
            i = j + length
            continue
        return False  # template/substitution/other production ⇒ treat as external
    return False


def _is_top_level_const(qual: str) -> bool:
    """Return ``True`` when a type spelling is const-qualified at the top level.

    Top-level const (``const int``, ``ns::Foo const``, ``int *const``) marks the
    *object* itself const — which at namespace scope without ``extern`` gives
    internal linkage in C++. An **array** is const iff its element type is const,
    so it is reduced to that element type (``const int[2]`` / ``const char[8]``
    are internal; an array of mutable pointers ``const char *[2]`` is not).
    Pointer/reference-to-const (``const char *``) is NOT top-level const (the
    pointer object is mutable, external), so a leading ``const`` only counts when
    the reduced element type is not a pointer.
    """
    q = qual.strip().rstrip("&").strip()
    # Array: reduce to the element type (spelled before the first ``[``).
    bracket = q.find("[")
    if bracket != -1:
        q = q[:bracket].strip()
    # Trailing ``const`` must be a standalone token, not the tail of an identifier
    # (a type named ``almost_const`` is not const-qualified). ``int *const`` /
    # ``ns::Foo const`` qualify; the char before ``const`` is then a non-identifier
    # character (space / ``*``) or the string is exactly ``const``.
    if q.endswith("const") and (len(q) == 5 or not (q[-6].isalnum() or q[-6] == "_")):
        return True
    return q.startswith("const ") and "*" not in q


def _is_variable_node(
    kind: str | None,
    name: str,
    node: dict[str, Any],
    accessible: bool,
    enclosing_kind: str | None,
    ctx: _ClassifyContext,
) -> bool:
    """Return ``True`` for a named, accessible **externally-linked** data variable.

    These are the globals and static data members that become exported ``OBJECT``
    symbols (e.g. ``llvm::raw_ostream::RED``), so capturing them lets a binary
    data export map back to a source declaration (ADR-030 D4). ``constexpr``
    variables are handled by ``_emit_constexpr`` and excluded here.

    A block-scope local (enclosing kind is a statement, not a decl container) has
    no linkage and is dropped. Internal-linkage variables never appear in the
    dynamic symbol table, so they are dropped two ways: (1) the mangled-name
    marker — a C++ *namespace/file-scope* ``static``, a namespace-scope ``const``
    without ``extern``, or an anonymous-namespace variable all carry it; and
    (2) an explicit ``storageClass == "static"`` filter for the case clang gives
    **no** mangled name (a C / ``extern "C"`` file-scope ``static``, whose
    ``mangledName`` is absent or equals ``name``), which (1) cannot see. A
    ``static`` **data member** (enclosing ``CXXRecordDecl``) has external linkage
    and is kept by both. The mangled name is clang's own linkage verdict, so this
    stays authoritative without re-deriving C++ linkage rules.
    """
    if kind != "VarDecl" or not name or not accessible:
        return False
    if node.get("constexpr"):
        return False
    if enclosing_kind not in _VARIABLE_SCOPE_KINDS:
        return False
    mangled = _mangled(node, ctx.target_triple, is_cxx=ctx.is_cxx)
    if _mangled_has_internal_linkage(mangled):
        return False
    # C / extern "C" file-scope static: no mangled name carries the marker, so
    # filter on storageClass directly. A static data member is external, so keep
    # it (its enclosing kind is a record).
    if node.get("storageClass") == "static" and enclosing_kind != "CXXRecordDecl":
        return False
    # MSVC / clang-cl mangling (`?name@...`) has no Itanium internal-linkage
    # marker, so a namespace-scope top-level `const` without `extern` — internal
    # linkage in C++ — would slip through the marker check. Fall back to the
    # language rule from the type (Codex review). A static data member is
    # external (enclosing record), and `extern` overrides, so both are excluded.
    if (
        mangled.startswith("?")
        and enclosing_kind != "CXXRecordDecl"
        and node.get("storageClass") != "extern"
        and not node.get("inline")
        and (
            _is_top_level_const(_signature(node))
            or _is_top_level_const(_signature_desugared(node))
        )
    ):
        return False
    return True


def _is_type_node(kind: str | None, name: str, accessible: bool) -> bool:
    """Return ``True`` for a named, accessible record or enum declaration."""
    return kind in ("CXXRecordDecl", "EnumDecl") and bool(name) and accessible


def _is_typedef_node(kind: str | None, name: str, accessible: bool) -> bool:
    """Return ``True`` for a named, accessible typedef or type-alias declaration."""
    return kind in ("TypedefDecl", "TypeAliasDecl") and bool(name) and accessible


def _is_concept_node(kind: str | None, name: str, accessible: bool) -> bool:
    """Return ``True`` for a named, accessible C++20 concept declaration."""
    return kind == "ConceptDecl" and bool(name) and accessible
