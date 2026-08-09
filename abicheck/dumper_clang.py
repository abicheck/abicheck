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
"Extension: clang as an alternative L2 frontend"; surfaced by a real-world
UXL field run).

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
import os
import re
import shlex
import shutil
from pathlib import Path
from typing import Any

from .diff_cxx_rules import itanium_scope_components
from .errors import AstContextMissingError, SnapshotError
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


def _is_intel_sycl_driver(path: str) -> bool:
    """True if *path* is Intel's oneAPI DPC++/C++ compiler (icx/icpx/dpcpp[-cl]).

    These are the only clang-family drivers known to implement
    ``-fsycl-host-only``/``-fsycl-device-only`` (Intel's SYCL
    single-compilation-pass flags): stock upstream clang accepts a bare
    ``-fsycl`` and parses it fine as a single pass, but hard-rejects both
    flags with "unknown argument" (Codex review, PR #643: verified against a
    real clang 17/18 install). Deliberately narrower than
    :func:`_is_clang_family_binary` (which also matches plain
    "clang"/"clang++") — widening this to that check would make
    :func:`abicheck.dumper._needs_sycl_host_only` append a flag stock clang
    cannot parse, breaking a ``--gcc-path clang`` + ``-fsycl`` combination
    that previously worked.
    """
    return Path(path).stem.lower() in _CLANG_FAMILY_ALIAS_NAMES


def _is_dpcpp_family_binary(path: str) -> bool:
    """Whether *path* is specifically Intel's DPC++-capable compiler
    (``icx``/``icpx``/``dpcpp``/``dpcpp-cl``) — narrower than
    :func:`_is_clang_family_binary`, which also matches plain ``clang``/
    ``clang++`` (not SYCL/``-fsycl``-capable). Used to decide whether a
    clang header-AST invocation should add ``-fsycl -v`` and route its
    output through :mod:`abicheck.sycl_context`'s multi-document decoder
    (ADR-050 D5, G32 Phase D) instead of the plain single-document path.

    Same underlying name set as :func:`_is_intel_sycl_driver` (PR #643) --
    kept as a separate function since the two checks serve distinct
    call sites (single-pass host-only collapsing vs. multi-pass host/device
    selection) with independent docstrings/callers, not because the
    underlying test differs.
    """
    return Path(path).stem.lower() in _CLANG_FAMILY_ALIAS_NAMES


#: Intel's legacy, now-deprecated "dpcpp"/"dpcpp-cl" driver names predate the
#: unified icx/icpx compiler and its explicit ``-fsycl`` opt-in: "dpcpp" was
#: historically the SYCL-specific entry point (Intel's own migration guidance
#: frames it as "switch to the C++ driver with -fsycl", implying dpcpp itself
#: never needed the flag), so invoking one of these two names can enable SYCL
#: even with no ``-fsycl``/``-fno-sycl`` token at all (Codex review, PR #643,
#: round 8). Not independently confirmed in the current open-source
#: intel/llvm driver sources (which show ``-fsycl`` defaulting to off
#: regardless of argv0) — this may be packaging-layer behavior specific to
#: Intel's binary distribution rather than the public driver code. Guarded
#: for anyway: the cost of wrongly treating a plain "dpcpp" invocation as
#: SYCL-enabled is at most an unused ``-fsycl-host-only`` (clang warns on an
#: unused flag, it does not hard-fail), while the cost of not guarding, if
#: the premise holds, is silently reintroducing the exact double-JSON-document
#: failure this whole fix exists to prevent for a chunk of the driver family.
_SYCL_DEFAULT_ON_DRIVER_NAMES = frozenset({"dpcpp", "dpcpp-cl"})


def _dpcpp_defaults_sycl_on(cc_bin: str) -> bool:
    """True if *cc_bin* is one of Intel's SYCL-implied legacy driver names."""
    return Path(cc_bin).stem.lower() in _SYCL_DEFAULT_ON_DRIVER_NAMES


def _user_explicitly_disabled_sycl(tokens: list[str]) -> bool:
    """True if *tokens* (the caller's own ``--gcc-options``/``--gcc-option``
    tokens -- never anything abicheck itself appends) end with an explicit
    ``-fno-sycl`` with no later ``-fsycl`` to re-enable it: i.e. the caller
    explicitly asked for a non-SYCL compile (Codex review, P2: ADR-050 D5's
    ``dumper._clang_header_dump`` unconditionally forces ``-fsycl`` onto
    every DPC++-capable invocation via ``dpcpp_multi_context``, which would
    otherwise silently override this last-flag-wins signal).

    Distinct from :func:`_dpcpp_defaults_sycl_on`/its use in
    :func:`_needs_sycl_host_only`'s "is SYCL effectively enabled" question
    (which decides whether an *already* ``-fsycl``'d build should collapse
    to one pass) -- here, silence (no ``-fsycl``/``-fno-sycl`` at all) is
    NOT an opt-out; only an explicit trailing ``-fno-sycl`` is.
    """
    disabled = False
    for tok in tokens:
        if tok == "-fsycl":
            disabled = False
        elif tok == "-fno-sycl":
            disabled = True
    return disabled


def _resolve_dpcpp_multi_context(
    clang_bin: str,
    frontend_context: str,
    gcc_options: str | None,
    gcc_option_tokens: tuple[str, ...],
) -> bool:
    """Validate *frontend_context* against *clang_bin* and the caller's own
    SYCL-enable/disable tokens, returning whether
    :func:`abicheck.dumper._clang_header_dump` should engage the multi-pass
    SYCL decode path for this invocation.

    Raises :class:`abicheck.errors.AstContextMissingError` when
    *frontend_context* requests a non-host context but either *clang_bin*
    isn't DPC++-capable (:func:`_is_dpcpp_family_binary`), or the caller's own
    ``gcc_options``/``gcc_option_tokens`` explicitly disable SYCL
    (:func:`_user_explicitly_disabled_sycl`) -- neither case is silently
    resolved (Codex review, P2).
    """
    is_dpcpp = _is_dpcpp_family_binary(clang_bin)
    if frontend_context != "host" and not is_dpcpp:
        raise AstContextMissingError(
            f"--frontend-context {frontend_context!r} requires a DPC++-capable "
            f"compiler (icx/icpx/dpcpp/dpcpp-cl); {clang_bin!r} is a plain "
            "clang/gcc invocation with no device AST context to select."
        )
    user_tokens = (
        shlex.split(gcc_options, posix=os.name != "nt") if gcc_options else []
    ) + list(gcc_option_tokens)
    sycl_explicitly_off = is_dpcpp and _user_explicitly_disabled_sycl(user_tokens)
    if frontend_context != "host" and sycl_explicitly_off:
        raise AstContextMissingError(
            f"--frontend-context {frontend_context!r} requires SYCL to be "
            "enabled, but the given --gcc-options/--gcc-option explicitly "
            "disable it (-fno-sycl) -- remove -fno-sycl or drop "
            "--frontend-context device."
        )
    return is_dpcpp and not sycl_explicitly_off


def _needs_sycl_host_only(cc_bin: str, tokens: list[str]) -> bool:
    """True if *tokens* enable SYCL on a driver that needs a pinned single pass.

    A bare ``-fsycl`` makes Intel's oneAPI DPC++/C++ driver (icx/icpx/dpcpp[-cl],
    :func:`_is_intel_sycl_driver`) run *two* separate ``-cc1`` passes for one
    compile -- a device-side pass and a host-side pass, each with its own
    ``-Xclang -ast-dump=json``, writing a complete JSON document to the same
    stdout stream back-to-back with no separator. ``json.load()`` in
    :func:`abicheck.dumper_clang_errors._parse_clang_ast_result` parses only
    the first document and raises on the leftover bytes ("Extra data"). The
    device-side AST is also the wrong evidence even if it parsed: it
    describes SPIR-V kernel code that never becomes part of a host ``.so``'s
    exported symbols. ``-fsycl-host-only`` collapses the compile back to a
    single host-side pass, which is what actually links into the scanned
    binary. Skipped when the caller already pinned a single pass explicitly
    (``-fsycl-host-only``/``-fsycl-device-only``). Also skipped by the one
    caller (:func:`abicheck.dumper._build_clang_header_command`) when an
    explicit multi-context request (``dpcpp_multi_context``, ADR-050 D5) is
    in play -- that request must never be silently collapsed back to a
    single pass by this function's own default-case behavior.

    Gated on *cc_bin* being specifically an Intel oneAPI driver, not any
    clang-family binary: stock upstream clang accepts a bare ``-fsycl`` but
    does not split into two passes and hard-rejects both
    ``-fsycl-host-only``/``-fsycl-device-only`` as "unknown argument"
    (Codex review, PR #643) -- appending the flag unconditionally would
    turn a working ``--gcc-path clang`` + ``-fsycl`` parse into a failure.

    A plain ``"-fsycl" in tokens`` membership check is not enough: the
    driver applies ``-fsycl``/``-fno-sycl`` last-flag-wins (confirmed with
    ``clang++ -fsycl -fno-sycl -###``, one ordinary host ``-cc1``, no
    device pass), so ``--gcc-options "-fsycl -fno-sycl"`` has SYCL
    disabled overall and must not get the flag appended either (Codex
    review, PR #643, round 5) -- the *last* occurrence of either flag
    decides the effective state, scanned below.

    The initial state is not always "off": Intel's legacy "dpcpp"/
    "dpcpp-cl" driver names imply SYCL is already on with no ``-fsycl``
    token at all (:func:`_dpcpp_defaults_sycl_on`, Codex review, PR #643,
    round 8) -- an explicit ``-fno-sycl`` still overrides that default.
    """
    if not _is_intel_sycl_driver(cc_bin):
        return False
    if "-fsycl-host-only" in tokens or "-fsycl-device-only" in tokens:
        return False
    sycl_enabled = _dpcpp_defaults_sycl_on(cc_bin)
    for tok in tokens:
        if tok == "-fsycl":
            sycl_enabled = True
        elif tok == "-fno-sycl":
            sycl_enabled = False
    return sycl_enabled


def _is_cl_style_driver_name(path: str) -> bool:
    """True for a CL-compatible driver name (``clang-cl``, Intel's ``dpcpp-cl``).

    A CL-style driver parses MSVC-shaped flags (``/E``, ``/d1PP``) instead of
    GNU-shaped ones, so it must never be selected as the binary for a
    GNU-flag-only invocation (the S2 preprocessor pre-scan, L4 source-ABI
    replay) — a CL driver can silently accept flags like ``-dM``/``-M`` as
    ordinary compile input and "succeed" without producing the expected
    output. Narrow, name-only heuristic: any stem ending in ``-cl`` (covers
    ``clang-cl``/``clang-cl.exe`` and ``dpcpp-cl``), consistent with the
    equally name-only :func:`_is_clang_family_binary` this complements.

    Strips a trailing numeric version suffix first (``clang-cl-20`` ->
    ``clang-cl``) -- LLVM/Debian packaging commonly ships a versioned
    executable alongside (or instead of) the unversioned name; without
    stripping it a packaged ``--gcc-path clang-cl-20`` would not be
    recognized as CL-style and would wrongly reach the GNU-only S2
    pre-scan, which silently ignores its unknown ``-dM``/``-M`` flags
    (Codex review) instead of being excluded and falling back to plain
    ``clang++``.
    """
    stem = Path(path).stem.lower()
    return re.sub(r"-\d+(?:\.\d+)*$", "", stem).endswith("-cl")


def resolve_source_frontend_clang_bin(
    gcc_path: str | None,
    gcc_prefix: str | None,
    *,
    fallback: str = "clang",
    exclude_cl_style: bool = True,
) -> str:
    """Resolve the clang binary a build-context-aware source frontend (the S2
    preprocessor pre-scan, L4 source-ABI replay/``embed_build_source``)
    should invoke, from the same ``--gcc-path``/``--gcc-prefix`` override a
    dump's ``CompileContext`` already carries for the L2 header AST —
    instead of hardcoding a generic ``clang``/``clang++`` regardless of what
    toolchain the caller actually pointed at.

    Mirrors :func:`_resolve_clang_bin`'s two override cases (``--gcc-path``
    only when it is actually a GNU-mode clang-family binary — castxml/GCC
    binaries can't take clang-only flags; ``--gcc-prefix`` maps to the
    prefixed clang driver, but only when that specific prefixed binary is
    actually on PATH — a documented GCC cross-toolchain prefix is not
    evidence a same-prefixed Clang exists, and guessing wrong would silently
    downgrade an already-working plain fallback to a "not found" skip),
    without that function's raise-on-missing for the explicit-binary case:
    these callers already degrade gracefully (an availability check, a
    coverage/skip row) when the resolved binary isn't on PATH, so this stays
    a pure resolver.

    *exclude_cl_style* (default ``True``, matching the original S2-only
    behavior) rejects a CL-mode driver (``clang-cl``, ``dpcpp-cl``,
    :func:`_is_cl_style_driver_name`) via *gcc_path* — correct for the S2
    preprocessor pre-scan, which always shells out with fixed GNU-mode flags
    (``-E -dM``, ``-M``) a CL-mode driver can't take. L4 source-ABI replay
    (``ClangSourceExtractor``) is different: it already detects a CL compile
    unit and re-drives the same binary with ``--driver-mode=cl``, so
    excluding it here would silently fall back to a plain, non-CL ``clang``
    that can't parse the real (e.g. Intel DPC++ ``dpcpp-cl``) build context —
    L4 callers pass ``exclude_cl_style=False``.

    Without this function, a dump/scan driven by a non-default toolchain
    (e.g. ``--gcc-path icpx``, which accepts icx/icpx-only flags) always
    shelled out to a plain ``clang``/``clang++`` for L4/S2 instead, failing
    every invocation and silently degrading source-ABI coverage even though
    the real build's own compiler was resolvable right here.
    """
    if (
        gcc_path
        and _is_clang_family_binary(gcc_path)
        and (not exclude_cl_style or not _is_cl_style_driver_name(gcc_path))
    ):
        return gcc_path
    if gcc_prefix:
        prefixed = f"{gcc_prefix}{fallback}"
        if shutil.which(prefixed):
            return prefixed
    return fallback


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


def _clang_deprecated_message(node: dict[str, Any]) -> str | None:
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


def _clang_record_type_traits(node: dict[str, Any]) -> tuple[bool | None, bool | None]:
    """``(is_standard_layout, is_trivially_copyable)`` from a record's own
    ``definitionData`` (G31 Phase C schema-completeness audit).

    Verified against real ``clang -ast-dump=json`` output (Clang 18) before
    wiring this up, following G28 Phase 1's discipline: a ``CXXRecordDecl``'s
    ``definitionData`` carries ``isStandardLayout``/``isTriviallyCopyable`` as
    boolean keys, but — confirmed empirically, not assumed from clang's own
    schema docs — clang's ``JSONNodeDumper`` only *emits* a ``definitionData``
    boolean key when the trait is ``true``; a record that does **not** have
    the trait has the key entirely absent rather than present with a literal
    ``false`` (e.g. a class with a private member is not standard-layout, and
    its ``definitionData`` has no ``isStandardLayout`` key at all, confirmed
    by direct comparison against a plain-public-members struct which does).
    So presence recovers ``True``, and absence — while ``definitionData``
    itself is present — recovers ``False``.

    A record with no ``definitionData`` at all yields ``(None, None)`` —
    "not collected", not "false" — matching this module's existing
    ``RecordType.is_standard_layout``/``is_trivially_copyable`` tri-state
    convention (see ``diff_layout.py``'s own True-vs-None handling, which
    only fires ``STANDARD_LAYOUT_LOST``/``TRIVIALLY_COPYABLE_LOST`` on an
    explicit ``True`` on one side, never treating "unknown" as a regression).
    This happens for two real cases, confirmed empirically: a plain C
    ``RecordDecl`` (these are C++-only type-trait concepts, so a C struct's
    node carries no ``definitionData`` key whatsoever — not "trivially true
    by default", genuinely absent), and an incomplete/forward-declared record
    (filtered out upstream by ``_is_record_definition`` before this is ever
    called, but kept conservative here too in case that guard's scope ever
    narrows).
    """
    definition_data = node.get("definitionData")
    if not isinstance(definition_data, dict):
        return None, None
    return (
        bool(definition_data.get("isStandardLayout", False)),
        bool(definition_data.get("isTriviallyCopyable", False)),
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
        # Built lazily (on first param-default/field-default extraction that
        # needs it, via _id_index()) -- a full extra tree walk isn't worth
        # paying on every dump when nothing in this TU has a referenced-decl
        # initializer to fingerprint.
        self._decl_id_qualified_names: dict[str, str] | None = None
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

    def _id_index(self) -> dict[str, str]:
        """Lazily-built, memoized :func:`_index_decl_id_qualified_names`
        over this parser's own AST root — computed at most once per parse."""
        if self._decl_id_qualified_names is None:
            self._decl_id_qualified_names = _index_decl_id_qualified_names(self._root)
        return self._decl_id_qualified_names

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
                    default=(_initializer_value(p, self._id_index()) or "default")
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
                    deprecated=_clang_deprecated_message(node),
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
                    deprecated=_clang_deprecated_message(node),
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
        is_standard_layout, is_trivially_copyable = _clang_record_type_traits(node)
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
            # G31 Phase C: unlike layout (size/align/offsets), these are
            # semantic type traits clang's AST computes independent of any
            # layout pass, and are genuinely absent from CastXML's own schema
            # (see dumper_castxml.py's own is_standard_layout/
            # is_trivially_copyable comment) — the direct-clang backend is
            # the one place these can actually be populated.
            is_standard_layout=is_standard_layout,
            is_trivially_copyable=is_trivially_copyable,
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
            deprecated=_clang_deprecated_message(node),
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
            # Gate the id-index build on hasInClassInitializer itself, not
            # just leave it to _field_initializer_value's own internal check
            # -- self._id_index() is a plain function-call ARGUMENT here, so
            # Python evaluates it eagerly regardless of whether child has an
            # initializer at all (Codex review, fresh evidence): the first
            # field processed in nearly every direct-clang dump was paying
            # the one-time whole-AST index walk even for an ordinary,
            # initializer-less field. Short-circuits via the ternary the
            # same way the sibling Param.default call site already does.
            default=(
                _field_initializer_value(child, self._id_index())
                if child.get("hasInClassInitializer")
                else None
            ),
            deprecated=_clang_deprecated_message(child),
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
                    # G31 Phase C: clang's EnumDecl carries a "scopedEnumTag"
                    # key ("class"/"struct") only for an `enum class`/`enum
                    # struct` -- absent (not merely false) for a plain C-style
                    # enum, confirmed against real clang -ast-dump=json output.
                    # Unlike is_standard_layout/is_trivially_copyable, a plain
                    # EnumDecl always has a definitive answer here (there is
                    # no "not collected" case for a real enum definition), so
                    # this is a concrete bool, never None, on this backend.
                    is_scoped="scopedEnumTag" in node,
                    deprecated=_clang_deprecated_message(node),
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


def _field_initializer_value(
    field: dict[str, Any], id_index: dict[str, str] | None = None
) -> str | None:
    """A ``TypeField.default`` value for a ``FieldDecl``, or ``None``.

    G31 Phase C: the last of that phase's fact-completeness list that the
    direct-clang backend genuinely can close (vptr *placement* it still
    cannot — clang's plain ``-ast-dump=json`` carries no secondary-vtable
    offsets without the optional ``ABICHECK_CLANG_LAYOUT_TOOL`` companion).

    Presence is taken from clang's own ``hasInClassInitializer`` flag rather
    than from "does this decl have a non-attribute ``inner`` child" the way
    :func:`_param_has_default` does, because a ``FieldDecl``'s ``inner`` list
    is overloaded: a **bitfield width** is nested there as a ``ConstantExpr``
    too. Verified against real Clang 18 output — ``int bf : 3;`` (no
    initializer at all) nests exactly one ``ConstantExpr`` child with
    ``value: "3"``, which the param-style "any non-attribute child" heuristic
    would read as a default member initializer of ``3``, fabricating an
    initializer for a field that has none. ``hasInClassInitializer`` is
    present-only-when-true (absent, never present-and-``false``, for both the
    plain and the bitfield case), matching the same convention
    ``scopedEnumTag`` uses.

    The width/initializer ordering is what makes reusing
    :func:`_initializer_value` safe for the combined form: ``int bfi : 3 = 2;``
    nests the width ``ConstantExpr`` *first* and the initializer second, and
    :func:`_init_expr` takes the last non-``Decl``/``Attr``/``Comment`` child
    — so the initializer wins. (A trailing ``[[deprecated]]`` is a
    ``DeprecatedAttr``, which that same filter already drops; confirmed
    against real output, where clang emits it *after* the initializer.)

    Value representation matches :func:`_initializer_value`'s contract, i.e.
    the same-backend-comparable one ``Param.default`` already uses on this
    backend: a bare literal keeps its readable value, anything compound
    reduces to a structural fingerprint. That is deliberately NOT
    cross-comparable with castxml's verbatim source expression — see
    ``diff_types._diff_field_default_initializer``'s same-producer gate.
    """
    if not field.get("hasInClassInitializer"):
        return None
    return _initializer_value(field, id_index)


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


def _initializer_value(
    node: dict[str, Any], id_index: dict[str, str] | None = None
) -> str | None:
    """A stable value string for a variable's initializer, or ``None`` if absent.

    A lone literal (after stripping wrapper casts) keeps its human-readable value
    (``42``); any compound initializer is reduced to a short deterministic
    fingerprint so two different compound expressions compare unequal while the
    same one is stable across builds. Mirrors the castxml ``init`` value as a
    same-backend comparison key (cross-backend constant *values* are not
    expected to match — the snapshots are still per-backend parity oracles for
    presence/scope).

    *id_index* (typically :func:`_index_decl_id_qualified_names` over the
    whole AST root) resolves a referenced declaration's scope, so ``a::VALUE``
    and ``b::VALUE`` fingerprint distinctly — see :func:`_canonical_expr`.
    """
    init = _init_expr(node)
    if init is None:
        return None
    core = _unwrap_expr(init)
    if core.get("kind") in _LITERAL_NODE_KINDS and "value" in core:
        return str(core["value"])
    return _expr_fingerprint(init, id_index)


def _init_expr(node: dict[str, Any]) -> dict[str, Any] | None:
    """The initializer expression child of a Var/Field decl, or ``None``."""
    candidates = [
        c
        for c in node.get("inner", []) or []
        if isinstance(c, dict)
        and not str(c.get("kind", "")).endswith(("Decl", "Attr", "Comment"))
    ]
    return candidates[-1] if candidates else None


def _expr_fingerprint(
    node: dict[str, Any], id_index: dict[str, str] | None = None
) -> str:
    """A short, build-stable structural fingerprint of an expression subtree."""
    blob = json.dumps(_canonical_expr(node, id_index), sort_keys=True).encode("utf-8")
    return "expr:" + hashlib.sha256(blob).hexdigest()[:16]


def _index_decl_id_qualified_names(root: dict[str, Any]) -> dict[str, str]:
    """Map every named declaration's clang ``id`` to its scope-qualified name.

    A single, dedicated pass over the WHOLE AST root (independent of
    :class:`_ClangAstParser`'s own categorizing walk, which only tracks the
    ABI-surface kinds it collects): a ``DeclRefExpr``'s ``referencedDecl``
    can name any declaration in the TU, so this needs to see everything.

    Feeds :func:`_canonical_expr`'s referenced-declaration fingerprinting: a
    ``referencedDecl`` stub is compact and carries only a bare, unqualified
    ``name`` (Codex review, fresh evidence: `a::VALUE`/`b::VALUE` share the
    byte-identical stub). Its own ``id`` IS unique but is a compile-time-only
    memory address, never stable across builds, so it is exchanged for this
    index's qualified-name string rather than hashed directly.

    Same namespace/class scope-tracking rule as
    ``_ClangAstParser._walk``/``_SCOPE_NODE_KINDS``, EXCEPT for
    ``ClassTemplateSpecializationDecl`` (Codex review, second round):
    distinct specializations of the same template (``A<int>`` vs.
    ``A<long>``) both expose only the bare primary-template name ``"A"``, no
    template-argument spelling at all on the node itself.
    ``_SCOPE_NODE_KINDS`` deliberately stays untouched (it also drives
    ``_ClangAstParser._walk``'s own public-surface qualified names, a much
    larger blast radius); this function special-cases the kind locally via
    :func:`_specialization_scope_key`.

    A redeclaration (e.g. a function declared then defined) shares its real
    entity's name, so the first sighting of a given ``id`` is kept rather
    than overwritten.
    """
    index: dict[str, str] = {}

    def walk(node: Any, scope: tuple[str, ...]) -> None:
        if not isinstance(node, dict):
            return
        kind = node.get("kind")
        name = node.get("name") or ""
        node_id = node.get("id")
        if isinstance(node_id, str) and name:
            index.setdefault(node_id, "::".join((*scope, name)) if scope else name)
        is_specialization = kind == "ClassTemplateSpecializationDecl"
        if is_specialization and name:
            # A representative member's own MANGLED name encodes the
            # template arguments (`_ZN1AIiE5VALUEE` vs `_ZN1AIlE5VALUEE` for
            # `VALUE`) and, unlike this node's own `id` (a compile-time
            # memory address), is build-stable -- so it disambiguates
            # without ever hashing an unstable value into the persisted
            # fingerprint _canonical_expr ultimately produces. Falls back to
            # the bare (collision-prone) name when no direct child carries
            # one at all -- a rarer, accepted degradation, the same
            # "conservative fallback over a wrong guess" convention this
            # whole module already follows elsewhere.
            disambiguator = _specialization_scope_key(node)
            scope_name = f"{name}#{disambiguator}" if disambiguator else name
        else:
            scope_name = name
        scope_forming = kind in _SCOPE_NODE_KINDS or is_specialization
        child_scope = (*scope, scope_name) if scope_forming and scope_name else scope
        for child in node.get("inner", []) or []:
            walk(child, child_scope)

    walk(root, ())
    return index


def _specialization_scope_key(node: dict[str, Any]) -> str:
    """A build-stable, MEMBER-ORDER-INDEPENDENT identity for a
    ``ClassTemplateSpecializationDecl``, or ``""`` when none can be derived.

    An earlier version used whichever direct child happened to be FIRST with
    a mangled name -- unstable to unrelated source edits (Codex review, fresh
    evidence: real Clang 17 output confirmed inserting `static constexpr int
    AAA` *before* an unchanged `VALUE` changed the disambiguator from
    `VALUE`'s own mangled name to `AAA`'s, silently perturbing every OTHER
    declaration referencing that `VALUE`, though nothing about it changed).

    Fixed via the SCOPE portion of a representative member's mangled name
    (:func:`diff_cxx_rules.itanium_scope_components`), e.g.
    ``_ZN1AIiE5VALUEE`` -> ``["AIiE", "VALUE"]``: dropping the trailing leaf
    leaves ``["AIiE"]``, identical regardless of which member of the SAME
    specialization contributed it. Tries every child until one parses (not
    just the first with a mangled name) -- a special member/operator's name
    is not parseable this way (returns ``None``, by that function's own
    contract), and stopping there would reopen the same instability.
    """
    for child in node.get("inner", []) or []:
        if not isinstance(child, dict):
            continue
        mangled = child.get("mangledName")
        if not isinstance(mangled, str) or not mangled:
            continue
        comps = itanium_scope_components(mangled)
        if comps and len(comps) > 1:
            return "::".join(comps[:-1])
    return ""


#: Matches clang's anonymous-tag-type spelling, e.g. ``"(unnamed enum at
#: t.hpp:1:1)"`` or ``"union (unnamed union at t.hpp:2:5)"`` -- the location
#: is the only volatile part; ``\1`` (the tag kind) is kept.
_ANON_TYPE_LOCATION_RE = re.compile(r"\(unnamed (\w+) at [^)]*\)")


def _normalize_qual_type(qual_type: str) -> str:
    """Strip the source location out of an anonymous-tag ``qualType`` before
    it's folded into a build-stable fingerprint.

    clang spells an anonymous enum/struct/union/class's type as ``"(unnamed
    <kind> at <file>:<line>:<col>)"`` -- an absolute path and line embedded
    right in the type string (Codex review, fresh evidence, verified against
    real Clang 17 output: parsing identical source from two checkout paths,
    or merely inserting a blank line before an anonymous `enum { VALUE = 3
    };`, produced two DIFFERENT `TypeField.default` fingerprints for an
    unrelated, unchanged initializer referencing it). The "unnamed <kind>"
    portion is kept (still distinguishes anonymous from named); only the
    location collapses to a fixed placeholder. A non-matching (the common,
    named-type) qualType passes through unchanged.
    """
    return _ANON_TYPE_LOCATION_RE.sub(r"(unnamed \1)", qual_type)


def _canonical_expr(node: Any, id_index: dict[str, str] | None = None) -> Any:
    """Reduce an expression node to a structural form (drop ids/locations)."""
    if not isinstance(node, dict):
        return node
    out: dict[str, Any] = {}
    for key in ("kind", "value", "opcode", "name", "castKind"):
        if key in node:
            out[key] = node[key]
    type_obj = node.get("type")
    if isinstance(type_obj, dict) and "qualType" in type_obj:
        out["type"] = _normalize_qual_type(type_obj["qualType"])
    # A UnaryExprOrTypeTraitExpr (sizeof/alignof/... applied to a TYPE, not
    # an expression) stores its operand EXCLUSIVELY in "argType" -- "type" is
    # just the trait's result type (always "unsigned long" for sizeof,
    # regardless of operand) (Codex review: `sizeof(int)` vs `sizeof(long
    # long)` fingerprinted identically without this). Other trait-expression
    # operand shapes are an unverified, narrower residual gap.
    arg_type_obj = node.get("argType")
    if isinstance(arg_type_obj, dict) and "qualType" in arg_type_obj:
        out["argType"] = _normalize_qual_type(arg_type_obj["qualType"])
    referenced = node.get("referencedDecl")
    if isinstance(referenced, dict):
        # A DeclRefExpr's own top-level keys never identify WHICH declaration
        # it names -- that lives only in this compact stub, previously
        # dropped entirely (Codex review: `int x = DEFAULT_A;` vs `int x =
        # DEFAULT_B;` fingerprinted identically without this -- affects both
        # TypeField.default and the pre-existing Param.default, which share
        # this helper). Its own "id" is a compile-time memory address, never
        # stable across builds, so only "kind"/"name"/"type" are kept.
        ref_out: dict[str, Any] = {}
        for key in ("kind", "name"):
            if key in referenced:
                ref_out[key] = referenced[key]
        ref_type = referenced.get("type")
        if isinstance(ref_type, dict) and "qualType" in ref_type:
            ref_out["type"] = _normalize_qual_type(ref_type["qualType"])
        # The bare "name" above collides across scopes (Codex review, second
        # round): `a::VALUE` vs `b::VALUE` share it. Resolve via id_index
        # when available -- falls back to bare-name-only (still better than
        # nothing) when the id isn't found, e.g. a builtin.
        ref_id = referenced.get("id")
        if id_index and isinstance(ref_id, str):
            qualified = id_index.get(ref_id)
            if qualified is not None:
                ref_out["qualified_name"] = qualified
        if ref_out:
            out["referencedDecl"] = ref_out
    inner = node.get("inner")
    if isinstance(inner, list):
        out["inner"] = [_canonical_expr(c, id_index) for c in inner]
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
