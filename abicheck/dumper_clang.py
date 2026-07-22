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

"""``clang -ast-dump=json`` → ABI model parser (the alternative L2 backend).

A sibling to :mod:`abicheck.dumper_castxml` that produces the **same**
``AbiSnapshot`` fields (functions, variables, types, enums, typedefs, constants)
from a ``clang -ast-dump=json`` tree instead of castxml XML, so a clang-only host
can still run the header-aware L2 layer — public-surface scoping and the
ADR-035 D4 cross-source checks that depend on header provenance (ADR-003,
"Extension: clang as an alternative L2 frontend"; surfaced by the UXL field run,
``validation/uxl-scan-levels-timing-2026-06.md`` P1).

:class:`_ClangAstParser` mirrors :class:`abicheck.dumper_castxml._CastxmlParser`'s
public method surface exactly, so the two are interchangeable producers behind
the :mod:`abicheck.dumper` backend selector and act as a parity oracle for each
other (the same pattern as the DWARF↔castxml and libabigail/ABICC parity gates).

**Coverage vs. castxml.** clang's JSON AST is a *syntactic* dump: it does not
compute record layout, so a clang-derived ``RecordType`` carries field
names/types, bases, and access but **not** ``size_bits`` / ``offset_bits`` /
vtable slots (those stay ``None``/empty — the layout detectors skip an
unknown-vs-unknown comparison, and DWARF (L1) remains the layout authority).
Everything the source-API and public-surface-scoping detectors need —
signatures, ``noexcept``/``const``/``explicit`` qualifiers, enum values,
typedef targets, public constant values — is produced. This is the documented
"partial L2" trade-off: clang where castxml is absent or chokes, castxml for
full layout.

The same gap applies to a plain ``Variable``'s *natural* type alignment:
:func:`_clang_var_alignment_bits` only reads an explicit ``AlignedAttr``
override, never a computed one (contrast
:meth:`abicheck.dumper_castxml._CastxmlParser._type_alignment_bits`, which
castxml's real compiler-computed ``align`` attribute makes possible). Under
``--artifact-variant release-headers`` on a clang-only host this leaves
``diff_platform_elf_symbols._check_object_alignment_reduced`` without
declared-alignment corroboration for the overwhelming majority of exported
globals, so it can still false-positive ``exported_object_alignment_reduced``
on a purely additive change (the case61_var_added scenario) — a real, known,
tracked gap (see
``tests/test_clang_header_backend_integration.py::test_clang_backend_still_false_positives_case61_alignment_risk``),
not something a small patch can close: it would need clang to compute
``alignof`` from scratch (target ABI rules for builtins, pointers, typedefs,
arrays) rather than reading a value the AST dump already carries.

The parser is pure (no subprocess): it consumes an already-parsed JSON dict, so
every emit path is unit-testable without clang installed. Shelling out to clang
lives in :func:`abicheck.dumper._clang_header_dump`.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any

from .errors import SnapshotError
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
from .provenance import (
    build_public_set,
    classify_origin,
    header_from_location,
)


def _clang_available(clang_bin: str = "clang") -> bool:
    return shutil.which(clang_bin) is not None


#: Non-"clang"-spelled binary names that are still clang-driver-compatible (accept
#: ``-Xclang``/``-ast-dump=json`` directly): Intel's oneAPI DPC++/C++ compiler
#: (``icx``/``icpx``, and its older ``dpcpp``/``dpcpp-cl`` aliases — all four are
#: the same clang-based binary under different names/symlinks in Intel's package,
#: confirmed via ``__clang_major__``/``-Xclang -ast-dump=json`` against a real
#: install). Without this, ``--gcc-path .../icpx`` is silently ignored (the
#: substring check below only matches "clang") and falls back to plain "clang" on
#: PATH — a *different* compiler than the one the real build used, so the wrong
#: toolchain's headers/predefined macros get parsed. This does not attempt
#: general vendor-fork detection (e.g. Apple clang already spells "clang"); it is
#: narrowly the known non-"clang"-named forks that are otherwise indistinguishable
#: from a real GCC binary by name alone.
_CLANG_FAMILY_ALIAS_NAMES = frozenset({"icx", "icpx", "dpcpp", "dpcpp-cl"})


def _is_clang_family_binary(path: str) -> bool:
    stem = Path(path).stem.lower()
    return "clang" in stem or stem in _CLANG_FAMILY_ALIAS_NAMES


def _resolve_clang_bin(
    compiler: str,
    gcc_path: str | None,
    gcc_prefix: str | None,
) -> str:
    """Resolve the clang executable to run, raising if it is not on ``PATH``.

    ``--gcc-path`` is honored only when it points at a clang(-family) binary
    (castxml emulates a GCC/G++ binary, which can't take clang-only flags);
    ``--gcc-prefix`` maps to the prefixed clang driver.
    """
    clang_bin: str | None = None
    if gcc_path and _is_clang_family_binary(gcc_path):
        clang_bin = gcc_path
    elif gcc_prefix:
        clang_bin = (
            f"{gcc_prefix}clang++"
            if compiler in ("c++", "g++", "clang++")
            else f"{gcc_prefix}clang"
        )
    if not clang_bin:
        clang_bin = "clang++" if compiler in ("c++", "g++", "clang++") else "clang"
    if not _clang_available(clang_bin):
        raise SnapshotError(
            f"{clang_bin} not found in PATH. The clang header backend needs clang/clang++ "
            "installed (apt install clang, brew install llvm, or conda install -c conda-forge "
            "clang). Or use the castxml frontend (--ast-frontend castxml)."
        )
    return clang_bin


#: Clang AST node kinds for the function-like declarations we emit. Includes the
#: C++ special members so a public constructor/destructor/conversion change is
#: captured, mirroring castxml's ``Constructor``/``Destructor``/``Converter``.
_FUNCTION_NODE_KINDS = frozenset(
    {
        "FunctionDecl",
        "CXXMethodDecl",
        "CXXConstructorDecl",
        "CXXDestructorDecl",
        "CXXConversionDecl",
    }
)
#: Decl contexts we descend into, tracking the enclosing scope name so a
#: namespace/class-qualified constant key is built (``ns::C::kLimit``).
_SCOPE_NODE_KINDS = frozenset(
    {"NamespaceDecl", "CXXRecordDecl", "RecordDecl", "LinkageSpecDecl"}
)
#: Literal node kinds whose ``value`` is a stable, human-meaningful constant.
_LITERAL_NODE_KINDS = frozenset(
    {
        "IntegerLiteral",
        "FloatingLiteral",
        "CharacterLiteral",
        "StringLiteral",
        "CXXBoolLiteralExpr",
        "FixedPointLiteral",
    }
)
#: Single-child wrapper expressions to descend through before reading a literal.
_WRAPPER_EXPR_KINDS = frozenset(
    {
        "ImplicitCastExpr",
        "CStyleCastExpr",
        "CXXStaticCastExpr",
        "ConstantExpr",
        "ExprWithCleanups",
        "ParenExpr",
        "CXXFunctionalCastExpr",
        "MaterializeTemporaryExpr",
    }
)
#: Pseudo-files clang attributes builtin / command-line declarations to.
_BUILTIN_FILES = frozenset(
    {"<built-in>", "<builtin>", "<command line>", "<scratch space>"}
)


def _pointer_depth(type_str: str) -> int:
    """Best-effort pointer nesting depth from a written type spelling.

    castxml computes this from the type graph; on the clang path we count
    top-level ``*`` tokens in the ``qualType`` spelling (``const char *`` → 1,
    ``int **`` → 2), ignoring any inside template/array brackets. Stable for the
    pointer-depth-change detector even though it is a spelling heuristic.
    """
    depth = 0
    bracket = 0
    for ch in type_str:
        if ch in "<[(":
            bracket += 1
        elif ch in ">])":
            bracket = max(0, bracket - 1)
        elif ch == "*" and bracket == 0:
            depth += 1
    return depth


def _return_type(qualtype: str) -> str:
    """The return type spelling of a function ``qualType`` (``ret (params)…``).

    Scans for the first ``(`` at bracket depth 0 — the start of the parameter
    list — and returns everything before it. Function-pointer return types (rare)
    degrade to the whole spelling; ordinary returns are exact.
    """
    bracket = 0
    for idx, ch in enumerate(qualtype):
        if ch in "<[":
            bracket += 1
        elif ch in ">]":
            bracket = max(0, bracket - 1)
        elif ch == "(" and bracket == 0:
            return qualtype[:idx].strip()
    return qualtype.strip()


def _is_noexcept_qualifier(quals: str) -> bool:
    """Whether a function's trailing qualifiers denote a *non-throwing* spec.

    A bare ``noexcept`` (and ``noexcept(true)`` / ``noexcept(1)``) is
    non-throwing; ``noexcept(false)`` / ``noexcept(0)`` is *throwing* and must
    not be treated as ``noexcept`` — since C++17 the exception specification is
    part of the function type, so conflating the two would hide a real ABI break
    (CodeRabbit review). A dependent ``noexcept(expr)`` keeps its conservative
    "non-throwing" reading (the spelling is all the header AST exposes).
    """
    m = re.search(r"\bnoexcept(?:\s*\(([^)]*)\))?", quals)
    if m is None:
        return False
    expr = m.group(1)
    if expr is None:
        return True
    return expr.strip() not in ("false", "0")


#: clang attribute node kinds → normalized contract-attribute tokens (matching
#: the castxml spellings so cross-frontend snapshots stay comparable).
_CLANG_ATTR_TOKENS: dict[str, str] = {
    "NoReturnAttr": "noreturn",
    "C11NoReturnAttr": "noreturn",
    "NonNullAttr": "nonnull",
    "ReturnsNonNullAttr": "returns_nonnull",
    "RestrictAttr": "malloc",
    "FormatAttr": "format",
    "FormatArgAttr": "format_arg",
    "AllocSizeAttr": "alloc_size",
    "AllocAlignAttr": "alloc_align",
    "WarnUnusedResultAttr": "warn_unused_result",
    "SentinelAttr": "sentinel",
    "CDeclAttr": "cdecl",
    "StdCallAttr": "stdcall",
    "FastCallAttr": "fastcall",
    "ThisCallAttr": "thiscall",
    "VectorCallAttr": "vectorcall",
    "MSABIAttr": "ms_abi",
    "SysVABIAttr": "sysv_abi",
    "RegparmAttr": "regparm",
}


def _clang_attr_arg_tokens(child: dict[str, Any]) -> list[str]:
    """Ordered ABI-significant argument scalars of a clang attribute node.

    clang ``-ast-dump=json`` nests an argument-bearing attribute's operands as
    ``ConstantExpr`` / ``IntegerLiteral`` / ``StringLiteral`` children carrying
    an evaluated ``value``. Collect those scalars in document order so the
    normalized token keeps the same arguments castxml preserves — otherwise
    ``nonnull(1)`` → ``nonnull(2)``, ``format(printf,1,2)`` → ``format(printf,2,3)``
    or ``regparm(2)`` → ``regparm(3)`` would collapse to identical bare tokens
    and the contract / calling-convention detectors would never fire (and the
    two frontends would disagree). Once a node yields a ``value`` we do not
    descend into it — clang wraps a literal inside its ``ConstantExpr`` with the
    same value, so recursing would double-count it.
    """
    args: list[str] = []

    def _walk(nodes: Any) -> None:
        for sub in nodes or []:
            if not isinstance(sub, dict):
                continue
            value = sub.get("value")
            if isinstance(value, bool):
                # JSON booleans are ints in Python; skip — not an ABI arg.
                _walk(sub.get("inner", []))
            elif isinstance(value, int):
                args.append(str(value))
            elif isinstance(value, str) and value:
                # StringLiteral values arrive quoted (e.g. "printf"); strip them
                # so the token matches castxml's bare-identifier spelling.
                args.append(value.strip('"'))
            else:
                _walk(sub.get("inner", []))

    _walk(child.get("inner", []))
    return args


def _clang_contract_attributes(node: dict[str, Any]) -> list[str]:
    """Normalized contract/calling-convention attributes of a decl node.

    Argument-bearing attributes keep their operands in the token
    (``nonnull(1)``, ``format(printf,1,2)``), matching the castxml frontend, so
    an argument-only change is still a detectable contract change.
    """
    tokens: set[str] = set()
    for child in node.get("inner", []) or []:
        if not isinstance(child, dict):
            continue
        token = _CLANG_ATTR_TOKENS.get(str(child.get("kind", "")))
        if token:
            arg_tokens = _clang_attr_arg_tokens(child)
            if arg_tokens:
                token = f"{token}({','.join(arg_tokens)})"
            tokens.add(token)
    return sorted(tokens)


def _clang_exception_spec(quals: str) -> str:
    """The dynamic exception-specification spelling from trailing qualifiers.

    ``""`` when the function has no ``throw(...)`` spec (noexcept is handled
    separately by :func:`_is_noexcept_qualifier`).
    """
    m = re.search(r"\bthrow\s*\(([^)]*)\)", quals)
    if m is None:
        return ""
    inner = ", ".join(p.strip() for p in m.group(1).split(",") if p.strip())
    return f"throw({inner})"


def _clang_record_is_final(node: dict[str, Any]) -> bool:
    """Whether a ``CXXRecordDecl`` carries the ``final`` class-virt-specifier.

    Unlike castxml (which exposes ``final`` as a plain XML attribute), clang's
    ``-ast-dump=json`` signals it as a child ``FinalAttr`` node under
    ``"inner"`` rather than a boolean field on the record itself — there is no
    ``node["final"]`` key to read.
    """
    return any(
        isinstance(child, dict) and child.get("kind") == "FinalAttr"
        for child in node.get("inner", []) or []
    )


def _clang_var_alignment_bits(node: dict[str, Any]) -> int | None:
    """Explicit alignment (bits) from an AlignedAttr, when evaluable.

    No fallback to the variable's *natural* type alignment exists here —
    unlike ``dumper_castxml._CastxmlParser._type_alignment_bits``, which
    reads a real compiler-computed ``align`` attribute, clang's
    ``-ast-dump=json`` never exposes computed alignment for a plain type at
    all (see this module's docstring). Returning ``None`` for an
    unattributed variable is correct given that constraint, not a bug: a
    guessed alignment (from a hardcoded builtin/pointer/target-ABI table)
    risks being silently wrong, which is worse than the honest "no
    corroboration" this leaves for
    ``diff_platform_elf_symbols._check_object_alignment_reduced``.
    """
    for child in node.get("inner", []) or []:
        if not isinstance(child, dict) or child.get("kind") != "AlignedAttr":
            continue
        stack: list[Any] = list(child.get("inner", []) or [])
        while stack:
            sub = stack.pop()
            if not isinstance(sub, dict):
                continue
            value = sub.get("value")
            if isinstance(value, int):
                return value * 8
            if isinstance(value, str) and value.isdigit():
                return int(value) * 8
            stack.extend(sub.get("inner", []) or [])
    return None


def _function_qualifiers(qualtype: str) -> str:
    """The trailing cv/ref/exception qualifiers after a function's parameter list.

    Returns the substring after the matching ``)`` of the top-level parameter
    list — e.g. ``" const noexcept"`` for ``int (int) const noexcept`` — so the
    caller can detect ``const``/``volatile``/``noexcept`` and the ref-qualifier.
    """
    bracket = 0
    start = -1
    for idx, ch in enumerate(qualtype):
        if ch in "<[":
            bracket += 1
        elif ch in ">]":
            bracket = max(0, bracket - 1)
        elif ch == "(" and bracket == 0 and start == -1:
            start = idx
            bracket += 1
            # consume the parameter-list parentheses
            depth = 1
            j = idx + 1
            while j < len(qualtype) and depth:
                if qualtype[j] == "(":
                    depth += 1
                elif qualtype[j] == ")":
                    depth -= 1
                j += 1
            return qualtype[j:]
    return ""


class _ClangAstParser:
    """Parse a ``clang -ast-dump=json`` tree into ABI model objects.

    Drop-in alternative to :class:`abicheck.dumper_castxml._CastxmlParser`: the
    same six ``parse_*`` methods, the same model types, the same exported-symbol
    visibility resolution and public-header constant scoping. A single pre-order
    walk (in ``__init__``) categorizes the public declarations; the ``parse_*``
    methods are cheap transforms over that cached walk.
    """

    def __init__(
        self,
        root: dict[str, Any],
        exported_dynamic: set[str],
        exported_static: set[str],
        public_header_paths: list[str] | None = None,
        public_dir_paths: list[str] | None = None,
    ) -> None:
        self._root = root
        self._exported_dynamic = exported_dynamic
        self._exported_static = exported_static
        (
            self._pub_header_segs,
            self._pub_dir_segs,
            self._have_public_set,
        ) = build_public_set(public_header_paths, public_dir_paths)
        # Categorized decls from the single walk: each entry is the raw node plus
        # the scope/file/extern-C context needed to build the model object.
        self._functions: list[_Decl] = []
        self._variables: list[_Decl] = []
        self._records: list[_Decl] = []
        self._enums: list[_Decl] = []
        self._typedefs: list[_Decl] = []
        self._walk(
            root,
            scope=(),
            current_file="",
            access="public",
            extern_c=False,
            in_friend=False,
        )

    # ── traversal ────────────────────────────────────────────────────────────

    def _walk(
        self,
        node: dict[str, Any],
        *,
        scope: tuple[str, ...],
        current_file: str,
        access: str,
        extern_c: bool,
        in_friend: bool,
        in_template: bool = False,
    ) -> str:
        """Pre-order walk that categorizes public decls, threading the sticky file.

        clang omits a node's ``loc.file`` when it is unchanged from the previous
        node in source order, so the last file seen in a child's *subtree* must
        flow to the next sibling. Returns the last file seen anywhere below
        *node* so the caller can thread it forward.
        """
        if not isinstance(node, dict):
            return current_file
        file = _node_file(node, current_file)
        kind = node.get("kind")
        name = node.get("name") or ""

        if not node.get("isImplicit"):
            self._categorize(
                node, kind, name, scope, file, access, extern_c, in_friend, in_template
            )

        # A function/method body is not an ABI declaration surface: its
        # parameters and defaults are read straight off the function node in
        # parse_functions(), so descending into the CompoundStmt would only risk
        # categorizing block-scope locals (a plain `int x;` with no storageClass)
        # as ABI variables/constants. Stop here (Codex/CodeRabbit review).
        if kind in _FUNCTION_NODE_KINDS:
            return file

        # A record body's children inherit the tag's default access until an
        # AccessSpecDecl switches it; namespaces/linkage-specs impose none.
        child_extern_c = extern_c or (
            kind == "LinkageSpecDecl" and node.get("language") == "C"
        )
        child_scope = (*scope, name) if kind in _SCOPE_NODE_KINDS and name else scope
        running = (
            _default_record_access(node)
            if kind in ("CXXRecordDecl", "RecordDecl")
            else "public"
        )
        # A ``friend`` declaration injects its function into the enclosing
        # namespace but reachable only via ADL ("hidden friend"); mark the
        # subtree so parse_functions can flag it (matches castxml's
        # ``befriending`` link). Friends never define a new scope.
        child_in_friend = in_friend or kind == "FriendDecl"
        # The template pattern's own CXXRecordDecl body (e.g. `template<typename T>
        # struct Foo { T value; };`) is otherwise indistinguishable from an
        # ordinary record: same kind, same bare name, no template-argument
        # suffix. Its field *names*/*types* are still real public surface (a
        # field added/removed from the pattern is a real API change regardless
        # of instantiation), so it is still emitted as a RecordType — but it
        # has no fixed *layout* for any one instantiation, so a plain-name
        # DWARF match against it (e.g. layout backfill) would attach an
        # unrelated type's or instantiation's real layout — silent corruption
        # (Codex review). Mark the whole subtree so RecordType.is_template_pattern
        # is set and the backfill matcher can skip it specifically.
        child_in_template = in_template or kind in (
            "ClassTemplateDecl",
            "ClassTemplatePartialSpecializationDecl",
        )
        for child in node.get("inner", []) or []:
            if not isinstance(child, dict):
                continue
            if child.get("kind") == "AccessSpecDecl":
                running = child.get("access", running)
                continue
            file = self._walk(
                child,
                scope=child_scope,
                current_file=file,
                access=child.get("access", running),
                extern_c=child_extern_c,
                in_friend=child_in_friend,
                in_template=child_in_template,
            )
        return file

    def _categorize(
        self,
        node: dict[str, Any],
        kind: str | None,
        name: str,
        scope: tuple[str, ...],
        file: str,
        access: str,
        extern_c: bool,
        in_friend: bool,
        in_template: bool = False,
    ) -> None:
        entry = _Decl(
            node=node,
            scope=scope,
            file=file,
            access=access,
            extern_c=extern_c,
            in_friend=in_friend,
            in_template=in_template,
        )
        if kind in _FUNCTION_NODE_KINDS and name:
            self._functions.append(entry)
        elif kind == "VarDecl" and name:
            self._variables.append(entry)
        elif kind in ("CXXRecordDecl", "RecordDecl"):
            # Anonymous records (name="") are kept too: a ``typedef struct {…}
            # Foo;`` emits an unnamed RecordDecl that carries the fields, recovered
            # under the typedef name in parse_types (Codex/CodeRabbit review).
            self._records.append(entry)
        elif kind == "EnumDecl":
            # Anonymous enums are kept too: a ``typedef enum {…} Foo;`` emits an
            # unnamed EnumDecl that carries the enumerators, recovered under the
            # typedef name in parse_enums.
            self._enums.append(entry)
        elif kind in ("TypedefDecl", "TypeAliasDecl") and name:
            self._typedefs.append(entry)

    # ── shared helpers ───────────────────────────────────────────────────────

    def _visibility(self, mangled: str, name: str = "") -> Visibility:
        """Resolve API visibility from the binary's exported-symbol tables.

        Identical policy to the castxml parser so a clang- and a castxml-derived
        snapshot classify the same declaration the same way.

        Mach-O quirk: clang's ``mangledName`` carries the platform global-symbol
        prefix (``__ZN3lib3addEii`` on macOS), but ``_dump_macho`` strips the
        single leading underscore off the export set to match castxml's
        prefix-free names. So each mangled candidate is matched both as-is (ELF)
        **and** with one leading underscore removed (Mach-O), trying the as-is
        form first so an ELF Itanium ``_Z…`` name never spuriously matches the
        stripped variant.
        """
        for cand in self._symbol_candidates(mangled):
            if cand in self._exported_dynamic:
                return Visibility.PUBLIC
        if name and name in self._exported_dynamic:
            return Visibility.PUBLIC
        for cand in self._symbol_candidates(mangled):
            if cand in self._exported_static:
                return Visibility.ELF_ONLY
        if name and name in self._exported_static:
            return Visibility.ELF_ONLY
        return Visibility.HIDDEN

    @staticmethod
    def _symbol_candidates(mangled: str) -> tuple[str, ...]:
        """The mangled name plus, on a leading underscore, its de-prefixed form."""
        if not mangled:
            return ()
        if mangled.startswith("_"):
            return (mangled, mangled[1:])
        return (mangled,)

    @staticmethod
    def _access_level(access: str) -> AccessLevel:
        if access == "protected":
            return AccessLevel.PROTECTED
        if access == "private":
            return AccessLevel.PRIVATE
        return AccessLevel.PUBLIC

    @staticmethod
    def _source_location(entry: _Decl) -> str | None:
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
        line = _node_line(entry.node)
        return f"{entry.file}:{line}" if line else entry.file

    def _qualified(self, entry: _Decl) -> str:
        name = entry.node.get("name", "")
        return "::".join([*entry.scope, name]) if entry.scope else name

    # ── parse_* (mirror _CastxmlParser's public surface) ─────────────────────

    def parse_functions(self) -> list[Function]:
        funcs: list[Function] = []
        for entry in self._functions:
            node = entry.node
            if _is_builtin_file(entry.file):
                continue
            name = str(node.get("name", ""))
            if not name:
                continue
            qualtype = _qualtype(node)
            mangled = str(node.get("mangledName", "")) or name
            quals = _function_qualifiers(qualtype)
            ret_type = _return_type(qualtype) or "void"
            params = [
                Param(
                    name=str(p.get("name", "")),
                    type=_qualtype(p),
                    pointer_depth=_pointer_depth(_qualtype(p)),
                    # Preserve the actual default-argument value (so a changed
                    # default fires PARAM_DEFAULT_VALUE_CHANGED); fall back to a
                    # bare presence marker when the value can't be evaluated.
                    default=(_initializer_value(p) or "default")
                    if _param_has_default(p)
                    else None,
                )
                for p in node.get("inner", []) or []
                if isinstance(p, dict) and p.get("kind") == "ParmVarDecl"
            ]
            kind = node.get("kind")
            is_explicit: bool | None
            if kind in ("CXXConstructorDecl", "CXXConversionDecl"):
                is_explicit = bool(node.get("explicit"))
            else:
                is_explicit = None
            if "&&" in quals:
                ref_qualifier = "&&"
            elif re.search(r"(?<!&)&(?!&)", quals):
                ref_qualifier = "&"
            else:
                ref_qualifier = ""
            funcs.append(
                Function(
                    name=name,
                    mangled=mangled,
                    return_type=ret_type,
                    params=params,
                    visibility=self._visibility(str(node.get("mangledName", "")), name),
                    is_virtual=bool(node.get("virtual")),
                    is_noexcept=_is_noexcept_qualifier(quals),
                    # An ``extern "C"`` linkage spec is authoritative; fall back
                    # to the mangled==name heuristic for a plain C-mode parse
                    # (no LinkageSpecDecl, but C-linkage names equal their symbol).
                    is_extern_c=entry.extern_c or mangled == name,
                    vtable_index=None,
                    source_location=self._source_location(entry),
                    is_static=node.get("storageClass") == "static",
                    is_const=bool(re.search(r"\bconst\b", quals)),
                    is_volatile=bool(re.search(r"\bvolatile\b", quals)),
                    is_pure_virtual=bool(node.get("pure")),
                    is_deleted=bool(node.get("explicitlyDeleted")),
                    is_inline=bool(node.get("inline")),
                    access=self._access_level(entry.access),
                    return_pointer_depth=_pointer_depth(ret_type),
                    ref_qualifier=ref_qualifier,
                    is_explicit=is_explicit,
                    is_hidden_friend=entry.in_friend,
                    # ``entry.scope`` is the enclosing-class scope path at the
                    # point ``in_friend`` first became True (the FriendDecl's
                    # own scope, since FriendDecl never pushes a scope level) —
                    # i.e. exactly the befriending class, mirroring castxml's
                    # ``befriending`` attribute resolution.
                    hidden_friend_owner=(
                        "::".join(entry.scope)
                        if entry.in_friend and entry.scope
                        else None
                    ),
                    # clang stamps "variadic": true on FunctionDecl; the
                    # qualtype spelling ("void (int, ...)") is the fallback.
                    is_variadic=bool(node.get("variadic")) or "..." in qualtype,
                    contract_attributes=_clang_contract_attributes(node),
                    exception_spec=_clang_exception_spec(quals),
                )
            )
        return funcs

    def parse_variables(self) -> list[Variable]:
        variables: list[Variable] = []
        for entry in self._variables:
            node = entry.node
            if _is_builtin_file(entry.file):
                continue
            # Skip block-scope locals: only namespace/global-scope and static
            # member variables denote an ABI surface (a local VarDecl is reached
            # only via a function body, which we do not descend, so this is
            # defensive).
            if node.get("storageClass") in ("auto", "register"):
                continue
            name = str(node.get("name", ""))
            mangled = str(node.get("mangledName", "")) or name
            if not mangled:
                continue
            type_name = _qualtype(node)
            variables.append(
                Variable(
                    name=name,
                    mangled=mangled,
                    type=type_name,
                    visibility=self._visibility(mangled, name),
                    is_const=bool(node.get("constexpr"))
                    or bool(re.search(r"\bconst\b", type_name)),
                    source_location=self._source_location(entry),
                    alignment_bits=_clang_var_alignment_bits(node),
                )
            )
        return variables

    def parse_constants(self) -> dict[str, str]:
        """Public ``const``/``constexpr`` constant *values* (mirrors castxml).

        A namespace-scope ``const``/``constexpr`` emits no exported symbol, so it
        is invisible to L0/L1 — only the header tier sees a value change. Scoped
        to the public-header surface via provenance; empty when no public set was
        supplied (provenance is opt-in).
        """
        if not self._have_public_set:
            return {}
        out: dict[str, str] = {}
        for entry in self._variables:
            node = entry.node
            if _is_builtin_file(entry.file):
                continue
            if entry.access in ("private", "protected"):
                continue
            type_name = _qualtype(node)
            is_const = bool(node.get("constexpr")) or bool(
                re.search(r"\bconst\b", type_name)
            )
            if not is_const:
                continue
            value = _initializer_value(node)
            if value is None:
                continue
            if not self._decl_is_public(entry):
                continue
            out[self._qualified(entry)] = value
        return out

    def _decl_is_public(self, entry: _Decl) -> bool:
        sh = header_from_location(self._source_location(entry))
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
        # Map each anonymous record's clang id → the typedef name that aliases it
        # (``typedef struct {…} Foo;``), so the unnamed record is emitted as
        # ``Foo`` with its fields intact rather than dropped (mirrors castxml's
        # ``typedef_name_for`` alias handling).
        anon_names = self._anon_typedef_names()
        types: list[RecordType] = []
        for entry in self._records:
            node = entry.node
            if _is_builtin_file(entry.file):
                continue
            name = str(node.get("name", ""))
            if not name:
                name = anon_names.get(str(node.get("id", "")), "")
                if not name:
                    continue  # a truly anonymous record (e.g. an inline union member)
            if name.startswith("__"):
                continue
            # Only definitions carry meaningful members; a forward declaration
            # (no body) would emit an empty record and create a false ODR/empty
            # signal, so skip it (matches the castxml `incomplete`/no-members guard).
            if not _is_record_definition(node):
                continue
            types.append(self._build_record(entry, override_name=name))
        return types

    def _anon_typedef_names(self) -> dict[str, str]:
        """``{anonymous-record-id: typedef-name}`` from the collected typedefs."""
        out: dict[str, str] = {}
        for entry in self._typedefs:
            tname = str(entry.node.get("name", ""))
            if not tname:
                continue
            rid = _owned_tag_id(entry.node)
            if rid:
                out.setdefault(rid, tname)
        return out

    def _build_record(self, entry: _Decl, override_name: str = "") -> RecordType:
        node = entry.node
        kind = (
            "union"
            if node.get("tagUsed") == "union"
            else ("struct" if node.get("tagUsed") == "struct" else "class")
        )
        fields = self._parse_fields(node)
        bases, virtual_bases, base_access = _parse_bases(node)
        injected = _anonymous_member_names(node)
        own_name = override_name or str(node.get("name", ""))
        return RecordType(
            name=own_name,
            kind=kind,
            # Namespace/enclosing-class-qualified spelling, set only when it
            # actually differs from the bare name (mirrors castxml's own
            # RecordType.qualified_name convention) -- without this, ANY
            # namespaced/nested clang-parsed type had qualified_name=None, so
            # a lookup keyed on the tool's own fully-qualified
            # getQualifiedNameAsString() spelling (e.g. "ns::Foo") fell back
            # to the bare "Foo" and never matched (Codex review, G28 Phase 4).
            qualified_name=(
                "::".join([*entry.scope, own_name]) if entry.scope else None
            ),
            # clang's JSON AST does not compute layout — size/align/offsets are
            # left None so the layout detectors skip an unknown-vs-unknown
            # comparison (DWARF remains the layout authority on this host).
            size_bits=None,
            alignment_bits=None,
            fields=fields,
            bases=bases,
            virtual_bases=virtual_bases,
            vtable=[],
            is_union=kind == "union",
            is_opaque=False,
            is_final=_clang_record_is_final(node),
            is_template_pattern=entry.in_template,
            # True only when *every* field came from the anonymous-aggregate
            # flatten, not merely "at least one did" (Codex review): a mixed
            # record like `struct Foo { union { int i; }; int tag; };` would
            # otherwise report the flag for `tag` too, letting the DWARF
            # layout-backfill exact-match branch trust an unrelated empty
            # DWARF candidate for a field (`tag`) the flag was never meant to
            # vouch for.
            has_anonymous_aggregate_fields=bool(injected)
            and all(f.name in injected for f in fields),
            source_location=self._source_location(entry),
        )

    def _parse_fields(self, node: dict[str, Any]) -> list[TypeField]:
        # Members injected from an anonymous struct/union are referenced by
        # ``IndirectFieldDecl`` siblings; collect their names so the anonymous
        # record's FieldDecls can be flattened up into this record (and so a
        # typedef'd anonymous record, which has no IndirectFieldDecl, is not).
        injected = _anonymous_member_names(node)
        return self._collect_fields(node, _default_record_access(node), injected)

    def _collect_fields(
        self,
        node: dict[str, Any],
        running: str,
        injected: set[str],
        *,
        nested: bool = False,
    ) -> list[TypeField]:
        fields: list[TypeField] = []
        for child in node.get("inner", []) or []:
            if not isinstance(child, dict):
                continue
            kind = child.get("kind")
            if kind == "AccessSpecDecl":
                running = child.get("access", running)
                continue
            if kind in ("RecordDecl", "CXXRecordDecl") and not child.get("name"):
                # Anonymous struct/union member: its public members live directly
                # in the enclosing record's namespace, so flatten them here. Keep
                # only the injected names to avoid pulling in a typedef'd
                # anonymous record's fields.
                fields.extend(
                    self._collect_fields(child, running, injected, nested=True)
                )
                continue
            if kind != "FieldDecl":
                continue
            fname = str(child.get("name", ""))
            if not fname:
                continue
            if nested and fname not in injected:
                # A nested unnamed record contributes only the members that an
                # IndirectFieldDecl injected (anonymous aggregate); a typedef'd
                # anonymous record injects nothing, so its fields are dropped.
                continue
            fields.append(self._make_field(child, child.get("access", running)))
        return fields

    def _make_field(self, child: dict[str, Any], access: str) -> TypeField:
        ftype = _qualtype(child)
        cv_type = _field_own_cv_source(_desugared_qualtype(child))
        bits, is_bitfield = _bitfield_width(child)
        return TypeField(
            name=str(child.get("name", "")),
            type=ftype,
            offset_bits=None,
            is_bitfield=is_bitfield,
            bitfield_bits=bits,
            is_const=bool(re.search(r"\bconst\b", cv_type)),
            is_volatile=bool(re.search(r"\bvolatile\b", cv_type)),
            is_mutable=bool(child.get("mutable")),
            access=self._access_level(access),
        )

    def parse_enums(self) -> list[EnumType]:
        enums: list[EnumType] = []
        typedef_names_by_enum_id: dict[str, str] = {}
        for entry in self._typedefs:
            node = entry.node
            if _is_builtin_file(entry.file):
                continue
            typedef_name = str(node.get("name", ""))
            if not typedef_name:
                continue
            for child in node.get("inner", []) or []:
                if not isinstance(child, dict):
                    continue
                owned = child.get("ownedTagDecl") or {}
                if owned.get("kind") == "EnumDecl" and owned.get("id"):
                    typedef_names_by_enum_id[str(owned["id"])] = typedef_name

        for entry in self._enums:
            node = entry.node
            if _is_builtin_file(entry.file):
                continue
            name = str(node.get("name", "")) or typedef_names_by_enum_id.get(
                str(node.get("id", "")), ""
            )
            if not name or name.startswith("__"):
                continue
            members: list[EnumMember] = []
            # C/C++ enumerator values auto-increment from the previous one
            # (starting at 0) unless an explicit initializer overrides them;
            # clang's JSON only carries the value on an explicit ConstantExpr, so
            # reconstruct the implicit ones here.
            next_value = 0
            for child in node.get("inner", []) or []:
                if (
                    not isinstance(child, dict)
                    or child.get("kind") != "EnumConstantDecl"
                ):
                    continue
                explicit = _enum_constant_value(child)
                value = explicit if explicit is not None else next_value
                members.append(EnumMember(name=str(child.get("name", "")), value=value))
                next_value = value + 1
            enums.append(
                EnumType(
                    name=name,
                    members=members,
                    underlying_type=_enum_underlying(node),
                    source_location=self._source_location(entry),
                    # See RecordType.qualified_name (_build_record) for why
                    # this is only set when it differs from the bare name.
                    qualified_name=(
                        "::".join([*entry.scope, name]) if entry.scope else None
                    ),
                )
            )
        return enums

    def parse_typedefs(self) -> dict[str, str]:
        typedefs: dict[str, str] = {}
        for entry in self._typedefs:
            node = entry.node
            if _is_builtin_file(entry.file):
                continue
            name = str(node.get("name", ""))
            if not name:
                continue
            underlying = _typedef_underlying(node)
            typedefs[name] = underlying or "?"
        return typedefs


# ─── pure node helpers (module-level so they are unit-testable on their own) ──


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
    ) -> None:
        self.node = node
        self.scope = scope
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


def _qualtype(node: dict[str, Any]) -> str:
    type_obj = node.get("type")
    if isinstance(type_obj, dict):
        return str(type_obj.get("qualType", ""))
    return ""


def _desugared_qualtype(node: dict[str, Any]) -> str:
    """The fully-desugared type spelling, when clang provides one.

    A field declared through a typedef to a cv-qualified type
    (``typedef const int T; struct S { T x; };``) renders ``qualType`` as
    the bare alias ``"T"`` — the real ``"const int"`` is only visible via
    the separate ``desugaredQualType`` key clang emits precisely when a
    type alias needs unwrapping. A plain (non-aliased) field carries no
    ``desugaredQualType`` key at all (confirmed empirically), so falling
    back to ``qualType`` is exact, not merely a guess, for every other
    case. Used only for the const/volatile regex check below — the
    field's own displayed ``type`` spelling stays the sugared form users
    actually wrote (Codex review, PR #582: mirrors dumper_castxml's
    Typedef-indirection walk for the identical reason — a regex on the
    display spelling alone misses a qualifier hidden behind an alias).
    """
    type_obj = node.get("type")
    if isinstance(type_obj, dict):
        desugared = type_obj.get("desugaredQualType")
        if isinstance(desugared, str) and desugared:
            return desugared
        return str(type_obj.get("qualType", ""))
    return ""


def _last_top_level_ptr_end(type_str: str) -> int:
    """Index just past the last depth-0 ``*`` in *type_str*, or -1 if none.

    A ``*`` nested inside a template argument list, function-parameter
    list, or array subscript doesn't count — the value itself isn't a
    pointer at that syntactic position. Depth tracking mirrors
    ``name_classification._has_top_level_ptr_or_ref``.
    """
    depth = 0
    last = -1
    for i, ch in enumerate(type_str):
        if ch in "<([":
            depth += 1
        elif ch in ">)]":
            depth = max(0, depth - 1)
        elif ch == "*" and depth == 0:
            last = i + 1
    return last


def _field_own_cv_source(desugared: str) -> str:
    """Substring of *desugared* that describes the FIELD's OWN const/
    volatile qualifier, as opposed to its pointee's.

    A pointer typedef's desugared spelling puts a POINTEE qualifier before
    the ``*`` (``const int *`` — pointer to const int, the pointer itself
    is NOT const) and the pointer VALUE's own qualifier as a suffix after
    it, with no space (``int *const`` — confirmed against real clang
    output). Scanning the whole string for ``const``/``volatile`` (as an
    earlier version of ``_make_field`` did) misread the pointee's
    qualifier as the field's own, so a field typed through
    ``typedef const int *P;`` was wrongly marked ``is_const=True`` even
    though ``P`` itself is a plain, non-const pointer (Codex review, PR
    #582 — a pointer-typedef sibling of the scalar-typedef case
    ``_desugared_qualtype`` already handles). A non-pointer type has no
    such ambiguity — the whole spelling describes the field itself.
    """
    end = _last_top_level_ptr_end(desugared)
    return desugared[end:] if end >= 0 else desugared


def _node_file(node: dict[str, Any], current: str) -> str:
    """The declaring file for *node*, honoring clang's sticky ``loc.file``."""
    loc = node.get("loc")
    if isinstance(loc, dict):
        f = loc.get("file")
        if isinstance(f, str) and f:
            return f
        for sub in ("expansionLoc", "spellingLoc"):
            s = loc.get(sub)
            if isinstance(s, dict):
                sf = s.get("file")
                if isinstance(sf, str) and sf:
                    return sf
    return current


def _node_line(node: dict[str, Any]) -> int:
    loc = node.get("loc")
    if isinstance(loc, dict):
        line = loc.get("line")
        if isinstance(line, int):
            return line
        # Mirror _node_file's macro/expansion fallback so a decl whose file comes
        # from expansionLoc/spellingLoc gets its line from the same place.
        for sub in ("expansionLoc", "spellingLoc"):
            s = loc.get(sub)
            if isinstance(s, dict) and isinstance(s.get("line"), int):
                return int(s["line"])
    return 0


def _is_builtin_file(file: str) -> bool:
    return file in _BUILTIN_FILES


def _default_record_access(node: dict[str, Any]) -> str:
    """Default member access before any ``AccessSpecDecl`` (``class`` → private)."""
    return "private" if node.get("tagUsed") == "class" else "public"


def _is_record_definition(node: dict[str, Any]) -> bool:
    """Whether a record node is a definition (has a body) vs. a forward decl."""
    if node.get("completeDefinition"):
        return True
    return any(
        isinstance(c, dict)
        and c.get("kind") in ("FieldDecl", "AccessSpecDecl", "CXXMethodDecl")
        for c in node.get("inner", []) or []
    )


def _param_has_default(param: dict[str, Any]) -> bool:
    """Whether a ``ParmVarDecl`` carries a default argument.

    clang flags it either with ``"init": "c"`` or by nesting the default-value
    expression as the parameter's lone ``inner`` child.
    """
    if param.get("init"):
        return True
    return any(
        isinstance(c, dict) and not str(c.get("kind", "")).endswith(("Attr", "Comment"))
        for c in param.get("inner", []) or []
    )


def _evaluated_int_value(node: dict[str, Any]) -> int | None:
    """The integer value of an expression node, ``None`` when not constant-int.

    clang records a fully-evaluated constant on the ``ConstantExpr`` *wrapper*
    itself (``value``), so a folded expression like ``1 << 3`` or ``-1`` carries
    its value there while its children (a ``BinaryOperator``/``UnaryOperator``)
    do not. Read the wrapper's value first, then fall back to the unwrapped leaf
    literal — otherwise such bitfield widths / enum values would be lost (Codex/
    CodeRabbit review).
    """
    for candidate in (node, _unwrap_expr(node)):
        if not isinstance(candidate, dict):
            continue
        val = candidate.get("value")
        if val is not None:
            try:
                return int(str(val), 0)
            except ValueError:
                continue
    return None


def _bitfield_width(field: dict[str, Any]) -> tuple[int | None, bool]:
    """``(width, is_bitfield)`` for a ``FieldDecl`` (width from its inner expr)."""
    if not field.get("isBitfield"):
        return None, False
    for child in field.get("inner", []) or []:
        if isinstance(child, dict):
            return _evaluated_int_value(child), True
    return None, True


def _anonymous_member_names(node: dict[str, Any]) -> set[str]:
    """Names injected into *node* from anonymous struct/union members.

    clang emits an ``IndirectFieldDecl`` for every member that an anonymous
    aggregate injects into its enclosing record; their names mark exactly which
    of the anonymous record's fields belong to this record's surface.
    """
    names: set[str] = set()
    for child in node.get("inner", []) or []:
        if isinstance(child, dict) and child.get("kind") == "IndirectFieldDecl":
            name = child.get("name")
            if name:
                names.add(str(name))
    return names


def _parse_bases(node: dict[str, Any]) -> tuple[list[str], list[str], dict[str, str]]:
    """Direct base names, virtual base names, and base→access from a record node.

    clang emits base specifiers as a ``bases`` array on the ``CXXRecordDecl``
    definition; each entry carries the base ``type.qualType``, its ``access``,
    and an ``isVirtual`` flag. Absent on a non-polymorphic C ``RecordDecl``.
    """
    bases: list[str] = []
    virtual_bases: list[str] = []
    access: dict[str, str] = {}
    for b in node.get("bases", []) or []:
        if not isinstance(b, dict):
            continue
        type_obj = b.get("type")
        bname = str(type_obj.get("qualType", "")) if isinstance(type_obj, dict) else ""
        if not bname:
            continue
        if b.get("isVirtual"):
            virtual_bases.append(bname)
        else:
            bases.append(bname)
        access[bname] = str(b.get("access", "public"))
    return bases, virtual_bases, access


def _enum_underlying(node: dict[str, Any]) -> str:
    """The enum's fixed underlying type spelling, defaulting to ``int``."""
    fixed = node.get("fixedUnderlyingType")
    if isinstance(fixed, dict) and fixed.get("qualType"):
        return str(fixed["qualType"])
    return "int"


def _enum_constant_value(node: dict[str, Any]) -> int | None:
    """The explicit value of an ``EnumConstantDecl``, or ``None`` if implicit."""
    for child in node.get("inner", []) or []:
        if not isinstance(child, dict):
            continue
        value = _evaluated_int_value(child)
        if value is not None:
            return value
    return None


def _unwrap_expr(node: dict[str, Any]) -> dict[str, Any]:
    """Descend through single-child wrapper expressions (casts, ConstantExpr…)."""
    cur = node
    while isinstance(cur, dict) and cur.get("kind") in _WRAPPER_EXPR_KINDS:
        inner = [c for c in cur.get("inner", []) or [] if isinstance(c, dict)]
        if len(inner) != 1:
            break
        cur = inner[0]
    return cur


def _initializer_value(node: dict[str, Any]) -> str | None:
    """A stable value string for a variable's initializer, or ``None`` if absent.

    A lone literal (after stripping wrapper casts) keeps its human-readable value
    (``42``); any compound initializer is reduced to a short deterministic
    fingerprint so two different compound expressions compare unequal while the
    same one is stable across builds. Mirrors the castxml ``init`` value as a
    same-backend comparison key (cross-backend constant *values* are not
    expected to match — the snapshots are still per-backend parity oracles for
    presence/scope).
    """
    init = _init_expr(node)
    if init is None:
        return None
    core = _unwrap_expr(init)
    if core.get("kind") in _LITERAL_NODE_KINDS and "value" in core:
        return str(core["value"])
    return _expr_fingerprint(init)


def _init_expr(node: dict[str, Any]) -> dict[str, Any] | None:
    """The initializer expression child of a Var/Field decl, or ``None``."""
    candidates = [
        c
        for c in node.get("inner", []) or []
        if isinstance(c, dict)
        and not str(c.get("kind", "")).endswith(("Decl", "Attr", "Comment"))
    ]
    return candidates[-1] if candidates else None


def _expr_fingerprint(node: dict[str, Any]) -> str:
    """A short, build-stable structural fingerprint of an expression subtree."""
    blob = json.dumps(_canonical_expr(node), sort_keys=True).encode("utf-8")
    return "expr:" + hashlib.sha256(blob).hexdigest()[:16]


def _canonical_expr(node: Any) -> Any:
    """Reduce an expression node to a structural form (drop ids/locations)."""
    if not isinstance(node, dict):
        return node
    out: dict[str, Any] = {}
    for key in ("kind", "value", "opcode", "name", "castKind"):
        if key in node:
            out[key] = node[key]
    type_obj = node.get("type")
    if isinstance(type_obj, dict) and "qualType" in type_obj:
        out["type"] = type_obj["qualType"]
    inner = node.get("inner")
    if isinstance(inner, list):
        out["inner"] = [_canonical_expr(c) for c in inner]
    return out


def _owned_tag_id(typedef_node: dict[str, Any]) -> str:
    """The clang id of an anonymous tag a typedef *owns*, or ``""``.

    For ``typedef struct {…} Foo;`` clang nests an ``ElaboratedType`` under the
    ``TypedefDecl`` whose ``ownedTagDecl`` points at the unnamed ``RecordDecl``
    that holds the fields. Returns that record's ``id`` so parse_types can emit
    the otherwise-anonymous record under the typedef name.
    """

    def _scan(node: Any) -> str:
        if not isinstance(node, dict):
            return ""
        owned = node.get("ownedTagDecl")
        if isinstance(owned, dict) and isinstance(owned.get("id"), str):
            return str(owned["id"])
        for child in node.get("inner", []) or []:
            found = _scan(child)
            if found:
                return found
        return ""

    return _scan(typedef_node)


def _typedef_underlying(node: dict[str, Any]) -> str:
    """The written underlying type of a typedef/alias (``qualType``, then sugar)."""
    type_obj = node.get("type")
    if not isinstance(type_obj, dict):
        return ""
    return str(type_obj.get("qualType") or type_obj.get("desugaredQualType") or "")
