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

Per ADR-061 D9, ``_Decl`` (the categorized-node-plus-walk-context type every
entity kind's parsing already received as a parameter) and the
built-in-file/qualtype/source-location/deprecation-message primitives more
than one entity kind reads now live in
:mod:`abicheck.extract.headers.clang.context`, with enum parsing built on
top of it in :mod:`abicheck.extract.headers.clang.enums`. Every name below
with a counterpart there is a thin delegating wrapper, kept so every
existing internal and external caller (tests included) that still reads
this module's private surface directly keeps resolving.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

from ._compiler_options import split_gcc_options

# Re-exported (not just referenced) so the historical
# ``dumper_clang._clang_contract_attributes`` import path tests use keeps
# resolving, even though the real call site moved to
# ``extract.headers.clang.functions``.
from .dumper_clang_attributes import _clang_contract_attributes  # noqa: F401
from .dumper_clang_expr import (  # noqa: F401  (some re-exported for tests)
    _SCOPE_NODE_KINDS,
    _WRAPPER_EXPR_KINDS,
    _canonical_expr,
    _expr_fingerprint,
    _field_initializer_value,
    _index_decl_id_qualified_names,
    _init_expr,
    _initializer_value,
    _normalize_qual_type,
    _specialization_scope_key,
    _unwrap_expr,
)

# Split out to keep this module under the 2000-line hard cap; imported (not
# just referenced) so the historical ``dumper_clang._name`` import paths that
# tests and sibling modules already use keep resolving.
from .dumper_clang_qualifiers import (  # noqa: F401  (compatibility re-exports)
    _OVERRIDE_ELIGIBLE_KINDS,
    _clang_method_is_override,
    _clang_param_is_restrict,
    _clang_param_is_va_list,
    _clang_record_is_abstract,
    _clang_record_type_traits,
    _desugared_qualtype,
    _field_own_cv_source,
    _last_top_level_ptr_end,
    _record_kind,
    _reduce_opaque_kind_set,
)
from .dumper_clang_vtable import (
    _index_template_param_defaults,
    _index_template_param_kinds,
    _index_template_param_names,
    _is_record_definition,
    _specialization_spelling,
    build_vtable as _build_clang_vtable,
)
from .errors import AstContextMissingError, SnapshotError
from .extract.headers.clang import (
    context as _clang_context,
    enums as _clang_enums,
    functions as _clang_functions,
)
from .model import (
    AccessLevel,
    EnumType,
    Fact,
    Function,
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
#: install). Without this, ``--compiler .../icpx`` is silently ignored (the
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
    cannot parse, breaking a ``--compiler clang`` + ``-fsycl`` combination
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
    """True if *tokens* (the caller's own ``--compiler-option``
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
    user_tokens = (split_gcc_options(gcc_options) if gcc_options else []) + list(
        gcc_option_tokens
    )
    sycl_explicitly_off = is_dpcpp and _user_explicitly_disabled_sycl(user_tokens)
    if frontend_context != "host" and sycl_explicitly_off:
        raise AstContextMissingError(
            f"--frontend-context {frontend_context!r} requires SYCL to be "
            "enabled, but the given --compiler-option explicitly "
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
    turn a working ``--compiler clang`` + ``-fsycl`` parse into a failure.

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
    stripping it a packaged ``--compiler clang-cl-20`` would not be
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
    should invoke, from the same ``--compiler``/``--compiler-prefix`` override a
    dump's ``CompileContext`` already carries for the L2 header AST —
    instead of hardcoding a generic ``clang``/``clang++`` regardless of what
    toolchain the caller actually pointed at.

    Mirrors :func:`_resolve_clang_bin`'s two override cases (``--compiler``
    only when it is actually a GNU-mode clang-family binary — castxml/GCC
    binaries can't take clang-only flags; ``--compiler-prefix`` maps to the
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
    (e.g. ``--compiler icpx``, which accepts icx/icpx-only flags) always
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

    ``--compiler`` is honored only when it points at a clang(-family) binary
    (castxml emulates a GCC/G++ binary, which can't take clang-only flags);
    ``--compiler-prefix`` maps to the prefixed clang driver.
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
#: Pseudo-files clang attributes builtin / command-line declarations to.
#: Single source of truth is now ``extract.headers.clang.context.BUILTIN_FILES``;
#: kept as a module attribute of the same name for any external reader of it.
_BUILTIN_FILES = _clang_context.BUILTIN_FILES


def _pointer_depth(type_str: str) -> int:
    """See ``extract.headers.clang.functions._pointer_depth`` (the canonical
    implementation this delegates to) for the full contract."""
    return _clang_functions._pointer_depth(type_str)


def _return_type(qualtype: str) -> str:
    """See ``extract.headers.clang.functions._return_type`` (the canonical
    implementation this delegates to) for the full contract."""
    return _clang_functions._return_type(qualtype)


def _is_noexcept_qualifier(quals: str) -> bool:
    """See ``extract.headers.clang.functions._is_noexcept_qualifier`` (the
    canonical implementation this delegates to) for the full contract."""
    return _clang_functions._is_noexcept_qualifier(quals)


def _clang_exception_spec(quals: str) -> str:
    """See ``extract.headers.clang.functions._clang_exception_spec`` (the
    canonical implementation this delegates to) for the full contract."""
    return _clang_functions._clang_exception_spec(quals)


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
    """Deprecation message for *node*, or ``None`` if not deprecated.

    See ``extract.headers.clang.context.clang_deprecated_message`` (the
    canonical implementation this delegates to) for the full contract.
    """
    return _clang_context.clang_deprecated_message(node)


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
    """See ``extract.headers.clang.functions._function_qualifiers`` (the
    canonical implementation this delegates to) for the full contract."""
    return _clang_functions._function_qualifiers(qualtype)


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
        target_triple: str | None = None,
    ) -> None:
        self._root = root
        # May be unavailable for synthetic/unit ASTs or an unprobeable
        # compiler.  In that case attribute spelling remains evidence rather
        # than being normalized against an assumed host ABI.
        self._target_triple = target_triple
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
        # Computed eagerly (not lazily like the id-qualified-names cache
        # above) because `_walk` itself -- run immediately below, during
        # __init__ -- needs these to correctly scope a specialization's own
        # members (see the `ClassTemplateSpecializationDecl` branch below);
        # a per-call lazy build wouldn't help since the first call IS during
        # this walk. Cheap: one extra whole-AST pass each, the same shape
        # `_id_index()` already pays lazily for a different purpose.
        self._template_param_kinds_by_qualname = _index_template_param_kinds(root)
        self._template_param_defaults_by_qualname = _index_template_param_defaults(root)
        self._template_param_names_by_qualname = _index_template_param_names(root)
        # Lazily-built, memoized record/specialization/vtable indices shared
        # between record-entity parsing (_build_record's base-lookup, still
        # in this module) and function-entity parsing
        # (extract.headers.clang.functions.parse_functions's is_virtual
        # override recovery). Constructed here (referencing self._records,
        # still empty) so its first real read -- after _walk below populates
        # self._records -- sees the fully-categorized list; see
        # RecordVtableIndex's own docstring for the full "why" of each cache.
        self._record_vtable_index = _clang_context.RecordVtableIndex(
            root,
            self._records,
            self._template_param_kinds_by_qualname,
            self._template_param_defaults_by_qualname,
            self._template_param_names_by_qualname,
        )
        self._walk(
            root,
            scope=(),
            lookup_scope=(),
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
        lookup_scope: tuple[str, ...],
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

        *lookup_scope* is a SEPARATE scope tuple from *scope*, growing only
        through `_SCOPE_NODE_KINDS` (never through a `ClassTemplateSpecial
        izationDecl`'s own spelling) -- exactly the convention `_index_
        template_param_kinds`/`_index_template_param_defaults`/`_index_
        template_param_names` use for their own qualname keys (Codex
        review, fresh evidence, second round: see the identical *lookup_
        scope* split in `dumper_clang_vtable.build_specialization_index`
        for the full empirical reasoning -- those three index functions
        register a NESTED template's own `ClassTemplateDecl` under its
        natural, unspelled scope, confirmed empirically to differ from
        *scope*'s own spelled qualname the moment a specialization ancestor
        is involved. Using *scope* for this lookup silently missed every
        such entry, degrading a nested specialization's own member back to
        the SAME owner-mismatch false positive the `ClassTemplateSpecial
        izationDecl` branch below was originally built to fix).
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
        if kind in _SCOPE_NODE_KINDS and name:
            child_scope = (*scope, name)
            child_lookup_scope = (*lookup_scope, name)
        elif kind == "ClassTemplateSpecializationDecl" and name:
            # A concrete template specialization's own members (e.g. `A<int>
            # ::f`) are otherwise scoped as if declared at the SAME level as
            # the specialization itself (`ClassTemplateSpecializationDecl`
            # is deliberately not in `_SCOPE_NODE_KINDS` -- it isn't an
            # ordinary namespace/class/linkage-spec scope) -- so a member's
            # `entry.scope` silently dropped the owning specialization
            # entirely (Codex review, fresh evidence: this is what let
            # `_specialization_record_index`'s base-lookup fix resolve the
            # base's vtable correctly while `owner_class_of`'s mangled-name
            # fallback still couldn't match it against `RecordType.bases`
            # -- see `parse_functions`'s own comment on this). Reuses
            # `_specialization_spelling` (already built for that base-lookup
            # index) so both consumers agree on the exact same spelling --
            # INCLUDING the same `param_kinds`/`param_defaults` context, not
            # just the bare function call: an earlier version of this branch
            # passed `None` for both, which produced the UNTRIMMED
            # `"A<double, int>"` form for a specialization using a defaulted
            # template argument while `_base_lookup_index()`'s own call
            # (through `D.bases`) correctly trimmed it to `"A<double>"` --
            # the same qualname mismatch the specialization-owner fix
            # itself was built to prevent, just for a different reason
            # (Codex review, fresh evidence: confirmed end-to-end that this
            # reintroduced a false `TYPE_VTABLE_CHANGED` for exactly the
            # no-keyword-override-through-a-defaulted-specialization-base
            # scenario the base-lookup fix was meant to make work). Falls
            # back to the unscoped behavior (bare `name`, degrading the SAME
            # way an unresolvable base already degrades elsewhere in this
            # module) when the spelling can't be reconstructed.
            template_qualname = (
                "::".join((*lookup_scope, name)) if lookup_scope else name
            )
            spelling = _specialization_spelling(
                node,
                name,
                self._template_param_kinds_by_qualname.get(template_qualname),
                self._template_param_defaults_by_qualname.get(template_qualname),
                self._template_param_names_by_qualname.get(template_qualname),
            )
            child_scope = (*scope, spelling) if spelling else scope
            child_lookup_scope = lookup_scope
        else:
            child_scope = scope
            child_lookup_scope = lookup_scope
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
                lookup_scope=child_lookup_scope,
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

        See ``extract.headers.clang.context.visibility`` (the canonical
        implementation this delegates to) for the full contract.
        """
        return _clang_context.visibility(
            self._exported_dynamic, self._exported_static, mangled, name
        )

    @staticmethod
    def _symbol_candidates(mangled: str) -> tuple[str, ...]:
        """See ``extract.headers.clang.context.symbol_candidates``."""
        return _clang_context.symbol_candidates(mangled)

    @staticmethod
    def _access_level(access: str) -> AccessLevel:
        """See ``extract.headers.clang.context.access_level``."""
        return _clang_context.access_level(access)

    @staticmethod
    def _source_location(entry: _Decl) -> str | None:
        """``file:line`` for a decl, or the bare file when clang omits the line.

        See ``extract.headers.clang.context.source_location`` (the canonical
        implementation this delegates to) for the full contract.
        """
        return _clang_context.source_location(entry)

    def _qualified(self, entry: _Decl) -> str:
        """See ``extract.headers.clang.context.qualified_name``."""
        return _clang_context.qualified_name(entry)

    def _id_index(self) -> dict[str, str]:
        """Lazily-built, memoized :func:`_index_decl_id_qualified_names`
        over this parser's own AST root — computed at most once per parse."""
        if self._decl_id_qualified_names is None:
            self._decl_id_qualified_names = _index_decl_id_qualified_names(self._root)
        return self._decl_id_qualified_names

    def _record_index(self) -> dict[str, dict[str, Any]]:
        """See ``extract.headers.clang.context.RecordVtableIndex.record_index``
        (the canonical implementation this delegates to) for the full
        contract, and ``dumper_clang_vtable.build_vtable``'s base-lookup
        recursion.

        A forward declaration (``struct A;``) and its later complete
        definition (``struct A { ... };``) share the same qualname and both
        land in ``self._records`` -- confirmed with a real clang build that
        clang emits BOTH `CXXRecordDecl` nodes for exactly this shape, the
        forward one carrying neither `completeDefinition` nor any member
        children. See ``RecordVtableIndex.record_index`` (that canonical
        implementation's own docstring) for the full forward-decl-vs-
        definition tiebreak this delegates to.
        """
        return self._record_vtable_index.record_index()

    def _specialization_record_index(self) -> dict[str, dict[str, Any]]:
        """See ``extract.headers.clang.context.RecordVtableIndex.
        specialization_record_index`` (the canonical implementation this
        delegates to) for the full contract.
        """
        return self._record_vtable_index.specialization_record_index()

    def _base_lookup_index(self) -> dict[str, dict[str, Any]]:
        """See ``extract.headers.clang.context.RecordVtableIndex.
        base_lookup_index`` (the canonical implementation this delegates to)
        for the full contract.
        """
        return self._record_vtable_index.base_lookup_index()

    def _virtual_mangled_names(self) -> frozenset[str]:
        """See ``extract.headers.clang.context.RecordVtableIndex.
        virtual_mangled_names`` (the canonical implementation this delegates
        to) for the full contract.
        """
        return self._record_vtable_index.virtual_mangled_names()

    # ── parse_* (mirror _CastxmlParser's public surface) ─────────────────────

    def parse_functions(self) -> list[Function]:
        """See ``extract.headers.clang.functions.parse_functions`` (the
        canonical implementation this delegates to) for the full contract.

        ``default_value`` is passed as a bound-method reference (matching
        the ``self._id_index`` bound-method-reference convention already
        used for param/field-default extraction elsewhere in this class):
        the real evaluator lives in ``dumper_clang_expr.py``, which imports
        ``diff_cxx_rules`` and so cannot be imported from the
        ``extract``-classified ``functions.py`` module directly.
        """
        return _clang_functions.parse_functions(
            self._functions,
            exported_dynamic=self._exported_dynamic,
            exported_static=self._exported_static,
            virtual_mangled_names=self._virtual_mangled_names(),
            target_triple=self._target_triple,
            default_value=lambda p: _initializer_value(p, self._id_index),
        )

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
        anon_names = self._anon_typedef_names()
        best: dict[str, tuple[_Decl, str]] = {}
        order: list[str] = []
        deprecated: dict[str, str] = {}
        opaque_kind_sets: dict[str, set[str]] = {}  # raw kinds of non-def redecls
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
            identity = "::".join([*entry.scope, name]) if entry.scope else name
            if (msg := _clang_deprecated_message(node)) is not None:  # most recent wins
                deprecated[identity] = msg
            if not (node_is_def := _is_record_definition(node)):
                opaque_kind_sets.setdefault(identity, set()).add(_record_kind(node))
            if (existing := best.get(identity)) is None:
                best[identity] = (entry, name)
                order.append(identity)
                continue
            if _is_record_definition(existing[0].node):
                continue
            node_pub = self._decl_is_public(entry)
            if node_is_def or (node_pub and not self._decl_is_public(existing[0])):
                best[identity] = (entry, name)
        return [
            self._build_record(
                (rec := best[identity])[0],
                override_name=rec[1],
                is_opaque=not _is_record_definition(rec[0].node),
                dep_msg=deprecated.get(identity),
                override_kind=_reduce_opaque_kind_set(opaque_kind_sets.get(identity)),
            )
            for identity in order
        ]

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

    def _build_record(
        self,
        entry: _Decl,
        override_name: str = "",
        is_opaque: bool = False,
        dep_msg: str | None = None,
        override_kind: str | None = None,
    ) -> RecordType:
        node = entry.node
        kind = override_kind if is_opaque and override_kind else _record_kind(node)
        own_name = override_name or str(node.get("name", ""))
        deprecated = dep_msg if dep_msg is not None else _clang_deprecated_message(node)
        if is_opaque:
            # Mirrors dumper_castxml.py's `incomplete="1"` branch.
            return RecordType(
                name=own_name,
                kind=kind,
                qualified_name=(
                    "::".join([*entry.scope, own_name]) if entry.scope else None
                ),
                size_bits=None,
                alignment_bits=None,
                fields=[],
                bases=[],
                virtual_bases=[],
                vtable=[],
                vptr_offset_bits=None,
                is_union=kind == "union",
                is_opaque=is_opaque,
                is_final=_clang_record_is_final(node),
                is_standard_layout=None,
                is_trivially_copyable=None,
                is_template_pattern=entry.in_template,
                has_anonymous_aggregate_fields=False,
                source_location=self._source_location(entry),
                deprecated=deprecated,
                # Empty lists are the parse's own answer -- matches dumper_castxml.py's opaque-record Fact stance.
                bases_fact=Fact.present([]),
                virtual_bases_fact=Fact.present([]),
                vtable_fact=Fact.present([]),
                vptr_offset_bits_fact=Fact.partial(
                    None
                ),  # heuristic field (see below), partial even here
            )
        fields = self._parse_fields(node)
        bases, virtual_bases, _base_access = _parse_bases(node)
        injected = _anonymous_member_names(node)
        is_standard_layout, is_trivially_copyable = _clang_record_type_traits(node)
        # G31 Phase C: reconstruct the vtable (and, from it, the same
        # 0-if-polymorphic vptr_offset_bits heuristic castxml already uses)
        # via dumper_clang_vtable's own signature-matching walk -- see that
        # module's docstring for why this can't be a simple `node.get
        # ("virtual")` check the way castxml's real semantic analysis allows.
        # Keyed by the SAME qualname _base_qualnames'/_record_index's own
        # lookups use, not `qualified_name` (which is None for a top-level
        # record) -- an anonymous-record's `override_name` never appears in
        # a `bases` array, so using it here (rather than the node's own bare
        # "") is what lets it still resolve if ever referenced as a base.
        own_qualname = "::".join([*entry.scope, own_name]) if entry.scope else own_name
        vtable = _build_clang_vtable(own_qualname, self._base_lookup_index())
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
            vtable=vtable,
            # Same convention as dumper_castxml.py: polymorphic (non-empty
            # vtable) -> vtable pointer at offset 0 (the Itanium ABI's
            # primary-base-at-offset-0 rule); None (unknown) otherwise. Real
            # multi-inheritance secondary-vtable placement is still not
            # tracked by either backend -- see the G31 plan doc.
            vptr_offset_bits=0 if vtable else None,
            is_union=kind == "union",
            is_opaque=is_opaque,
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
            deprecated=deprecated,
            # G31 Phase C backend audit -- see _clang_record_is_abstract.
            is_abstract=_clang_record_is_abstract(node),
            # Stated explicitly -- this parse genuinely resolved these. vptr_offset_bits_fact is `partial`, not `present`: 0-if-vtable-else-None is the Itanium primary-base heuristic, not a real offset read (matches vptr_offset_bits's own PARTIAL row).
            bases_fact=Fact.present(bases),
            virtual_bases_fact=Fact.present(virtual_bases),
            vtable_fact=Fact.present(vtable),
            vptr_offset_bits_fact=Fact.partial(0 if vtable else None),
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
            # self._id_index (uncalled -- a bound-method reference, matching
            # _IdIndexProvider's lazy contract) is only actually invoked deep
            # inside _canonical_expr, and only for a real referencedDecl --
            # never for an ordinary literal default or a field with no
            # initializer at all (Codex review, two rounds: passing the
            # CALLED self._id_index() built the whole-AST index eagerly for
            # every field regardless; gating only on hasInClassInitializer
            # still left every LITERAL default, e.g. `int timeout = 30;`,
            # paying for it too, since that never reaches the referencedDecl
            # branch either). The hasInClassInitializer ternary itself stays,
            # to skip the function call entirely for a field with no
            # initializer.
            default=(
                _field_initializer_value(child, self._id_index)
                if child.get("hasInClassInitializer")
                else None
            ),
            deprecated=_clang_deprecated_message(child),
        )

    def parse_enums(self) -> list[EnumType]:
        return _clang_enums.parse_enums(
            self._typedefs, self._enums, _evaluated_int_value
        )

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

    def parse_typedefs_qualified(self) -> dict[str, str]:
        """Same alias -> underlying-type mapping as :meth:`parse_typedefs`,
        keyed by the fully namespace/class-qualified name instead of the
        bare local name -- see ``AbiSnapshot.typedefs_qualified``'s
        docstring for why ``parse_typedefs`` cannot be relied on alone for
        a member typedef whose bare spelling collides across classes.
        """
        typedefs: dict[str, str] = {}
        for entry in self._typedefs:
            node = entry.node
            if _is_builtin_file(entry.file):
                continue
            name = str(node.get("name", ""))
            if not name:
                continue
            underlying = _typedef_underlying(node)
            typedefs[self._qualified(entry)] = underlying or "?"
        return typedefs


# ─── pure node helpers (module-level so they are unit-testable on their own) ──


# ``_Decl`` now lives in ``extract.headers.clang.context`` (ADR-061 D9
# "context.py" — the one shared type every entity-parsing module in that
# package receives). Re-exported under its old name so the many existing
# ``from abicheck.dumper_clang import _Decl`` call sites (production and
# tests alike) keep resolving.
_Decl = _clang_context._Decl


def _qualtype(node: dict[str, Any]) -> str:
    """A declaration's own ``type.qualType`` spelling.

    See ``extract.headers.clang.context.qualtype`` (the canonical
    implementation this delegates to) for the full contract.
    """
    return _clang_context.qualtype(node)


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
    """See ``extract.headers.clang.context.node_line``."""
    return _clang_context.node_line(node)


def _is_builtin_file(file: str) -> bool:
    return _clang_context.is_builtin_file(file)


def _default_record_access(node: dict[str, Any]) -> str:
    """Default member access before any ``AccessSpecDecl`` (``class`` → private)."""
    return "private" if node.get("tagUsed") == "class" else "public"


def _param_has_default(param: dict[str, Any]) -> bool:
    """See ``extract.headers.clang.functions._param_has_default`` (the
    canonical implementation this delegates to) for the full contract."""
    return _clang_functions._param_has_default(param)


def _evaluated_int_value(node: dict[str, Any]) -> int | None:
    """The integer value of an expression node, ``None`` when not constant-int.

    clang records a fully-evaluated constant on the ``ConstantExpr`` *wrapper*
    itself (``value``), so a folded expression like ``1 << 3`` or ``-1`` carries
    its value there while its children (a ``BinaryOperator``/``UnaryOperator``)
    do not. Read the wrapper's value first, then fall back to the unwrapped leaf
    literal — otherwise such bitfield widths / enum values would be lost (Codex/
    CodeRabbit review).

    Checking only the *original* node and the *fully*-unwrapped leaf (what an
    earlier revision did) misses a ``value`` folded onto an *intermediate*
    wrapper: an enumerator initialized from a sibling enumerator (``tag_x =
    tag_a``) wraps a ``ConstantExpr`` — carrying the real, evaluated ``value``
    (e.g. ``"2"``) — around a ``DeclRefExpr`` naming ``tag_a``, and
    ``_unwrap_expr`` keeps descending through ``ConstantExpr`` (a registered
    wrapper kind) straight into that ``DeclRefExpr``, which has no ``value``
    of its own. The fully-unwrapped leaf alone therefore reported ``None`` —
    read as "implicit" — silently discarding the real value and mis-numbering
    every enumerator after it. Walk the same single-child-wrapper chain
    ``_unwrap_expr`` follows, but check every node passed through, not just
    the endpoints.

    Deliberately NOT delegated to ``extract.headers.clang.context``: this
    walk depends on ``_WRAPPER_EXPR_KINDS`` (``dumper_clang_expr.py``),
    which itself imports ``diff_cxx_rules`` (classified ``compare``) for
    ``itanium_scope_components`` — moving this function into the
    ``extract``-classified package would create a real ``extract -> compare``
    edge. ``extract.headers.clang.enums.parse_enums`` instead takes this
    function as an explicit parameter (see that module's own docstring).
    """
    cur: Any = node
    while isinstance(cur, dict):
        val = cur.get("value")
        if val is not None:
            try:
                return int(str(val), 0)
            except ValueError:
                pass
        if cur.get("kind") not in _WRAPPER_EXPR_KINDS:
            break
        inner = (
            [c for c in raw if isinstance(c, dict)]
            if isinstance(raw := cur.get("inner"), list)
            else []
        )
        if len(inner) != 1:
            break
        cur = inner[0]
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


# ``_enum_underlying``/``_enum_constant_value`` moved to
# ``extract.headers.clang.enums`` with ``parse_enums`` itself (ADR-061 D9);
# re-exported under their old names for any external reader of them.
_enum_underlying = _clang_enums._enum_underlying
_enum_constant_value = _clang_enums._enum_constant_value


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
