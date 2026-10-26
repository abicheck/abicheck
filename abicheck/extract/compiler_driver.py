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

"""Compiler-driver resolution: *which clang to run for this compiler and flags,
and in which SYCL mode*.

This is the single owner of driver-family identity for every clang-driven
caller -- the L2 header AST backend, the L4/L5 source extractors, the S2
preprocessor pre-scan, the dump cache's tool identity, and the frontend
preflight. Name-based driver recognition (``clang``/``clang++``, Intel's
``icx``/``icpx``/``dpcpp``/``dpcpp-cl``, CL-style ``clang-cl``) and the
``-fsycl`` / ``-fno-sycl`` / ``-fsycl-host-only`` decisions that depend on it
are answered here and nowhere else.

Public interface (everything else in this module is private):

- :func:`resolve_clang_bin` -- the executable a clang header dump runs;
  raises :class:`abicheck.errors.SnapshotError` when it is not on ``PATH``.
- :func:`resolve_source_frontend_clang_bin` -- the same question for the
  build-context-aware source frontends, which degrade instead of raising.
- :func:`clang_bin_is_explicitly_configured` / :func:`is_default_clang_bin` --
  whether a resolved driver came from an explicit ``--compiler`` /
  ``--compiler-prefix`` override or is the plain host default.
- :func:`resolve_dpcpp_acquisition` -- the ``(multi_context, direct_host)``
  SYCL acquisition mode for one DPC++ request.
- :func:`needs_sycl_host_only` -- whether a driver invocation must be pinned
  to one host-side pass.
- :func:`is_clang_family_binary`, :func:`is_intel_sycl_driver`,
  :func:`is_dpcpp_family_binary`, :func:`is_cl_style_driver_name`,
  :func:`default_clang_bin_name`, :func:`clang_available` -- the driver-family
  predicates and availability probe those decisions are built from.

Callers import these names from here; no other module re-exports them.

Dependency note: :func:`_driver_host_only_is_single_pass` consults the
single-pass probe in :mod:`abicheck.extract.dpcpp_jobs` lazily, at call time,
so importing this module never pulls the replay planner in.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from .._compiler_options import split_gcc_options
from ..errors import AstContextMissingError, SnapshotError

#: Environment variables the compiler reads extra include directories from.
#: They are searched after the ``-I``/``-isystem`` roots, so they change a
#: parse as much as a flag does. Keyed by the dump cache and witnessed by the
#: header include disk cache.
ENV_INCLUDE_VARS: tuple[str, ...] = ("CPATH", "C_INCLUDE_PATH", "CPLUS_INCLUDE_PATH")

__all__ = [
    "clang_available",
    "clang_bin_is_explicitly_configured",
    "default_clang_bin_name",
    "is_cl_style_driver_name",
    "is_clang_family_binary",
    "is_default_clang_bin",
    "is_dpcpp_family_binary",
    "is_intel_sycl_driver",
    "needs_sycl_host_only",
    "resolve_clang_bin",
    "resolve_dpcpp_acquisition",
    "resolve_source_frontend_clang_bin",
]


def clang_available(clang_bin: str = "clang") -> bool:
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


def is_clang_family_binary(path: str) -> bool:
    stem = Path(path).stem.lower()
    return "clang" in stem or stem in _CLANG_FAMILY_ALIAS_NAMES


def is_intel_sycl_driver(path: str) -> bool:
    """True if *path* is Intel's oneAPI DPC++/C++ compiler (icx/icpx/dpcpp[-cl]).

    These are the only clang-family drivers known to implement
    ``-fsycl-host-only``/``-fsycl-device-only`` (Intel's SYCL
    single-compilation-pass flags): stock upstream clang accepts a bare
    ``-fsycl`` and parses it fine as a single pass, but hard-rejects both
    flags with "unknown argument" (Codex review, PR #643: verified against a
    real clang 17/18 install). Deliberately narrower than
    :func:`is_clang_family_binary` (which also matches plain
    "clang"/"clang++") — widening this to that check would make
    :func:`needs_sycl_host_only` append a flag stock clang
    cannot parse, breaking a ``--compiler clang`` + ``-fsycl`` combination
    that previously worked.
    """
    return Path(path).stem.lower() in _CLANG_FAMILY_ALIAS_NAMES


def is_dpcpp_family_binary(path: str) -> bool:
    """Whether *path* is specifically Intel's DPC++-capable compiler
    (``icx``/``icpx``/``dpcpp``/``dpcpp-cl``) — narrower than
    :func:`is_clang_family_binary`, which also matches plain ``clang``/
    ``clang++`` (not SYCL/``-fsycl``-capable). Used to decide whether a
    clang header-AST invocation should add ``-fsycl -v`` and route its
    output through :mod:`abicheck.sycl_context`'s multi-document decoder
    (ADR-050 D5, G32 Phase D) instead of the plain single-document path.

    Same underlying name set as :func:`is_intel_sycl_driver` (PR #643) --
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
    ``extract.headers.clang.backend.clang_header_dump`` unconditionally forces ``-fsycl`` onto
    every DPC++-capable invocation via ``dpcpp_multi_context``, which would
    otherwise silently override this last-flag-wins signal).

    Distinct from :func:`_dpcpp_defaults_sycl_on`/its use in
    :func:`needs_sycl_host_only`'s "is SYCL effectively enabled" question
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
    :func:`abicheck.extract.headers.clang.backend.clang_header_dump` should engage the multi-pass
    SYCL decode path for this invocation.

    Raises :class:`abicheck.errors.AstContextMissingError` when
    *frontend_context* requests a non-host context but either *clang_bin*
    isn't DPC++-capable (:func:`is_dpcpp_family_binary`), or the caller's own
    ``gcc_options``/``gcc_option_tokens`` explicitly disable SYCL
    (:func:`_user_explicitly_disabled_sycl`) -- neither case is silently
    resolved (Codex review, P2).
    """
    is_dpcpp = is_dpcpp_family_binary(clang_bin)
    if frontend_context != "host" and not is_dpcpp:
        raise AstContextMissingError(
            f"compile.frontend_context {frontend_context!r} requires a DPC++-capable "
            f"compiler (icx/icpx/dpcpp/dpcpp-cl); {clang_bin!r} is a plain "
            "clang/gcc invocation with no device AST context to select."
        )
    user_tokens = (split_gcc_options(gcc_options) if gcc_options else []) + list(
        gcc_option_tokens
    )
    sycl_explicitly_off = is_dpcpp and _user_explicitly_disabled_sycl(user_tokens)
    if frontend_context != "host" and sycl_explicitly_off:
        raise AstContextMissingError(
            f"compile.frontend_context {frontend_context!r} requires SYCL to be "
            "enabled, but the configured compile.options explicitly "
            "disable it (-fno-sycl) -- remove -fno-sycl or drop "
            "compile.frontend_context: device."
        )
    return is_dpcpp and not sycl_explicitly_off


def resolve_dpcpp_acquisition(
    clang_bin: str,
    frontend_context: str,
    gcc_options: str | None,
    gcc_option_tokens: tuple[str, ...],
) -> tuple[bool, bool]:
    """Return ``(multi_context, direct_host)`` for one DPC++ request."""
    multi = _resolve_dpcpp_multi_context(
        clang_bin, frontend_context, gcc_options, gcc_option_tokens
    )
    tokens = (split_gcc_options(gcc_options) if gcc_options else []) + list(
        gcc_option_tokens
    )
    direct_host = (
        multi
        and frontend_context == "host"
        and "-fsycl-device-only" not in tokens
        and _driver_host_only_is_single_pass(clang_bin, tuple(tokens))
    )
    return multi and not direct_host, direct_host


def _driver_host_only_is_single_pass(clang_bin: str, tokens: tuple[str, ...]) -> bool:
    """See :func:`abicheck.extract.dpcpp_jobs.host_only_request_usable`."""
    from .dpcpp_jobs import executable_revision, host_only_request_usable

    return host_only_request_usable(clang_bin, executable_revision(clang_bin), tokens)


def needs_sycl_host_only(cc_bin: str, tokens: list[str]) -> bool:
    """True if *tokens* enable SYCL on a driver that needs a pinned single pass.

    A bare ``-fsycl`` makes Intel's oneAPI DPC++/C++ driver (icx/icpx/dpcpp[-cl],
    :func:`is_intel_sycl_driver`) run *two* separate ``-cc1`` passes for one
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
    caller (:func:`abicheck.extract.headers.ast_config._build_clang_header_command`) when an
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
    if not is_intel_sycl_driver(cc_bin):
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


def _strip_trailing_version_suffix(stem: str) -> str:
    """Strip a trailing numeric version suffix (``clang-cl-20`` ->
    ``clang-cl``, ``clang-18`` -> ``clang``) from an already-lowercased
    binary stem.

    LLVM/Debian packaging commonly ships a versioned executable alongside
    (or instead of) the unversioned name -- shared by every name-only
    driver-identity heuristic in this module (:func:`is_cl_style_driver_name`,
    :func:`is_default_clang_bin`) so a packaged ``clang-18``/``clang-cl-20``
    is recognized the same way its unversioned name would be.
    """
    return re.sub(r"-\d+(?:\.\d+)*$", "", stem)


def is_cl_style_driver_name(path: str) -> bool:
    """True for a CL-compatible driver name (``clang-cl``, Intel's ``dpcpp-cl``).

    A CL-style driver parses MSVC-shaped flags (``/E``, ``/d1PP``) instead of
    GNU-shaped ones, so it must never be selected as the binary for a
    GNU-flag-only invocation (the S2 preprocessor pre-scan, L4 source-ABI
    replay) — a CL driver can silently accept flags like ``-dM``/``-M`` as
    ordinary compile input and "succeed" without producing the expected
    output. Narrow, name-only heuristic: any stem ending in ``-cl`` (covers
    ``clang-cl``/``clang-cl.exe`` and ``dpcpp-cl``), consistent with the
    equally name-only :func:`is_clang_family_binary` this complements.

    Strips a trailing numeric version suffix first (``clang-cl-20`` ->
    ``clang-cl``) -- without it a packaged ``--compiler clang-cl-20`` would
    not be recognized as CL-style and would wrongly reach the GNU-only S2
    pre-scan, which silently ignores its unknown ``-dM``/``-M`` flags
    (Codex review) instead of being excluded and falling back to plain
    ``clang++``.
    """
    stem = Path(path).stem.lower()
    return _strip_trailing_version_suffix(stem).endswith("-cl")


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

    Mirrors :func:`resolve_clang_bin`'s two override cases (``--compiler``
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
    :func:`is_cl_style_driver_name`) via *gcc_path* — correct for the S2
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
        and is_clang_family_binary(gcc_path)
        and (not exclude_cl_style or not is_cl_style_driver_name(gcc_path))
    ):
        return gcc_path
    if gcc_prefix:
        prefixed = f"{gcc_prefix}{fallback}"
        if shutil.which(prefixed):
            return prefixed
    return fallback


def default_clang_bin_name(compiler: str) -> str:
    """The plain, unconfigured host binary name :func:`resolve_clang_bin`
    falls back to when neither ``gcc_path`` nor ``gcc_prefix`` resolves to
    something more specific (including when ``gcc_path`` names a non-
    clang-family binary ``resolve_clang_bin`` ignores outright).

    Exists as its own function so a caller can compare a resolved
    ``clang_bin`` against this to tell "genuinely explicit/cross compiler"
    from "the plain host default was used regardless of what was
    requested" -- e.g. ``dumper._run_clang``'s `sys.platform` guess, which
    is only safe for the latter (Codex review, fresh evidence: a `gcc_path`
    naming a non-clang binary is silently ignored by `resolve_clang_bin`,
    so its mere presence does not mean a cross-compiler actually ran).
    """
    return "clang++" if compiler in ("c++", "g++", "clang++") else "clang"


def is_default_clang_bin(clang_bin: str, compiler: str) -> bool:
    """Whether ``clang_bin`` (an already-resolved compiler executable) IS
    the plain, unconfigured host default -- by invocation BASENAME, which
    is what actually determines Clang's behavior, not real executable
    identity and not raw path spelling.

    Real Clang derives its own default target from ``argv[0]``: a
    target-prefixed symlink to the exact same binary as plain ``clang``
    (e.g. ``aarch64-apple-darwin-clang``, a real, documented cross-
    toolchain wrapper shape) reports that PREFIXED target from
    ``-print-target-triple``, not the host's -- so an earlier revision of
    this check, which resolved both sides through `PATH`/symlinks and
    compared real executable IDENTITY, wrongly treated that symlink as
    "the plain default" merely because it happened to point at the same
    file (Codex review, fresh evidence, correcting that revision). An
    absolute path to the plain binary (``/usr/bin/clang``) is still
    correctly recognized here, since its basename is unaffected by the
    directory portion of the path -- only a genuinely different
    invocation NAME changes Clang's own target-selection behavior.

    Also strips a trailing numeric version suffix (:func:`_strip_trailing_version_suffix`,
    ``clang-18`` -> ``clang``) before
    comparing -- a native, versioned Clang driver `resolve_clang_bin`
    genuinely runs (LLVM/Debian packaging commonly ships one) is still
    the plain host default in every way that matters here (Codex review,
    fresh evidence): its own basename dispatch behaves identically to the
    unversioned name once the version suffix -- which carries no target
    information -- is set aside.
    """
    stem = _strip_trailing_version_suffix(Path(clang_bin).stem.lower())
    return stem == default_clang_bin_name(compiler)


def resolve_clang_bin(
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
    if gcc_path and is_clang_family_binary(gcc_path):
        clang_bin = gcc_path
    elif gcc_prefix:
        clang_bin = (
            f"{gcc_prefix}clang++"
            if compiler in ("c++", "g++", "clang++")
            else f"{gcc_prefix}clang"
        )
    if not clang_bin:
        clang_bin = default_clang_bin_name(compiler)
    if not clang_available(clang_bin):
        raise SnapshotError(
            f"{clang_bin} not found in PATH. The clang header backend needs clang/clang++ "
            "installed (apt install clang, brew install llvm, or conda install -c conda-forge "
            "clang). Or use the castxml frontend (compile.frontend: castxml in "
            ".abicheck.yml, or ABICHECK_AST_FRONTEND=castxml)."
        )
    return clang_bin


def clang_bin_is_explicitly_configured(
    gcc_path: str | None, gcc_prefix: str | None
) -> bool:
    """Whether :func:`resolve_clang_bin` took an explicit ``--compiler``/
    ``--compiler-prefix`` branch for these inputs, rather than falling
    through to the plain, unconfigured host default.

    Mirrors that function's own adoption condition instead of re-deriving
    one from the resolved `clang_bin` string, since a resolved binary's
    BASENAME can coincide with the plain default name even when it was
    genuinely explicitly configured -- a ``--compiler`` wrapper installed
    at a path whose basename happens to be plain ``clang``/``clang++``
    (Codex review, fresh evidence: such a wrapper can produce a real AST
    while not implementing ``-print-target-triple`` at all, failing both
    the option-bearing probe and the bare re-probe; treating it as the
    native host default purely because its basename matches would then
    let :func:`dumper._run_clang`'s last-resort ``sys.platform`` guess
    substitute the wrong platform for an explicitly cross-targeting
    wrapper). Callers gate that guess on this being ``False`` in addition
    to :func:`is_default_clang_bin`'s own basename check -- the two
    answer different questions (real identity vs. explicit provenance)
    and neither alone is sufficient.
    """
    return bool(gcc_path and is_clang_family_binary(gcc_path)) or bool(gcc_prefix)
