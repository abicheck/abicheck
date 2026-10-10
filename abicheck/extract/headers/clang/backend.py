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

"""The clang header-AST backend (lane B, stage B2a).

:class:`ClangBackend` runs ``clang -Xclang -ast-dump=json`` over a header
set and returns the :class:`~abicheck.dumper_clang._ClangAstParser` built
from its AST -- the IR-fragment producer
:func:`~abicheck.extract.header_ast_fields.parse_header_ast_fields` reads.
:func:`clang_header_dump` is the cached, coordinated, self-healing tool run
it wraps, moved verbatim from ``extract.headers.clang.backend.clang_header_dump``.

The process runner is injected: ``ClangBackend(runner=...)`` hands its
runner to :func:`clang_header_dump`, which uses it for every clang
invocation instead of a module-level name a test would have to patch.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from ...._compiler_options import (
    effective_driver_mode_is_cl as _effective_driver_mode_is_cl,
    explicit_target_triple as _explicit_target_triple,
    forwarded_driver_mode_token as _forwarded_driver_mode_token,
    forwards_response_file as _forwards_response_file,
)
from ....dumper_ast_config import (
    _build_clang_header_command,
    _cache_key,
    clang_aggregate_text,
)
from ....dumper_ast_config_cpp20 import _detect_cpp20_headers
from ....dumper_clang import (
    _ClangAstParser,
    _is_cl_style_driver_name,
    _is_default_clang_bin,
    _resolve_clang_bin,
    _resolve_dpcpp_acquisition,
    clang_bin_is_explicitly_configured as _clang_bin_is_explicitly_configured,
)
from ....dumper_clang_errors import (
    _is_missing_cpp_stdlib_header_error,
    _parse_clang_ast_result,
    run_clang_ast,
)
from ....dumper_sysinc import _resolve_clang_system_includes
from ....dumper_toolchain import (
    _configured_target_triple,
    _resolve_force_cpp,
    _stamp_ast_parser,
    _tool_identity,
)
from ....errors import SnapshotError
from ....storage.header_ast_cache import (
    _cache_path,
    ast_acquisition_active,
    load_cached_ast,
    resolve_request_memoization,
    run_ast_acquisition_offering_entry,
    store_cached_ast,
)
from ...path_aliases import absolutize_include_roots
from ..backend import HeaderParseRequest
from .error_header_retry import retry_excluding_error_headers
from .locations import materialize_locations

# The orchestrator's logger name, kept from before the move so callers and
# tests capturing "abicheck.dumper" logging see no change.
log = logging.getLogger("abicheck.dumper")

#: Runs one clang command (``cmd``, ``timeout``, ``on_created``) and returns
#: the completed process; the default checks the scan deadline first.
ClangAstRunner = Callable[..., subprocess.CompletedProcess[str]]


def resolve_clang_langmode(
    lang: str | None,
    headers: list[Path],
    clang_bin: str,
    gcc_options: str | None = None,
    gcc_option_tokens: tuple[str, ...] = (),
    exported_symbols: frozenset[str] = frozenset(),
) -> tuple[bool, bool, bool, str]:
    """Return ``(force_cpp, force_cpp20, explicit_c_request, cc_id)`` for the TU.

    ``explicit_c_request`` records whether C was *explicitly* requested
    (``--lang c``) vs auto-detected — both leave ``force_cpp`` False, but the
    C→C++ self-heal treats them differently (warning vs debug; Codex review).

    ``exported_symbols``: see :func:`_resolve_force_cpp`'s own docstring —
    the binary's already-observed export table, checked as C++ evidence when
    the header content alone gives no signal.
    """
    force_cpp = _resolve_force_cpp(
        lang, headers, gcc_options, gcc_option_tokens, exported_symbols
    )
    force_cpp20 = force_cpp and _detect_cpp20_headers(headers)
    explicit_c_request = bool(lang) and not force_cpp
    cc_id = "msvc" if Path(clang_bin).name.lower() in ("cl", "cl.exe") else "gnu"
    return force_cpp, force_cpp20, explicit_c_request, cc_id


def _log_c_to_cpp_selfheal(explicit_c_request: bool) -> None:
    """Log the C→C++ self-heal at the right level for how C was chosen."""
    if explicit_c_request:
        # Explicit compile.lang: c that needs the C++ stdlib: keep the self-heal visible
        # — the result is C++ ABI evidence, not the C requested (Codex review).
        log.warning(
            "clang was asked for C (compile.lang: c) but the header(s) require the "
            "C++ standard library; self-healing to C++ mode. The result is "
            "C++ ABI evidence — set compile.lang: c++ to make this explicit, or "
            "verify you intended a C library."
        )
    else:
        log.debug(
            "clang auto-detected C for a pure-#include umbrella header (no "
            "inline C++ syntax to key on), then self-healed to C++ after a "
            "missing C++ standard header — an unambiguous C++ signal. The "
            "result is unaffected; set compile.lang: c++ to skip the C probe."
        )


def clang_header_dump(
    headers: list[Path],
    extra_includes: list[Path],
    compiler: str = "c++",
    *,
    gcc_path: str | None = None,
    gcc_prefix: str | None = None,
    gcc_options: str | None = None,
    gcc_option_tokens: tuple[str, ...] = (),
    sysroot: Path | None = None,
    nostdinc: bool = False,
    lang: str | None = None,
    extra_hash_dirs: tuple[Path, ...] = (),
    frontend_context: str = "host",
    memoize: bool | None = None,
    pruning_header_roots: tuple[str, ...] | None = None,
    exported_symbols: frozenset[str] = frozenset(),
    _coordinated: bool = False,
    _expected_acquisition_key: str | None = None,
    run_ast: ClangAstRunner = run_clang_ast,
) -> tuple[dict[str, Any], str | None, bool]:
    """Run clang over *headers* and return ``(root, resolved_kind, resolved_force_cpp)``.

    *run_ast* runs one clang command and returns the completed process
    (default :func:`~abicheck.dumper_clang_errors.run_clang_ast`: the scan
    deadline check, then the :func:`abicheck.deadline.run_bounded` run). It
    is injected, not patched: :class:`ClangBackend` passes its own runner.

    ``resolved_force_cpp`` is the mode that actually produced *root* -- the
    post-retry ``True`` when a C-mode parse self-healed into C++, else
    ``force_cpp`` (Codex review: the provenance probe must not re-derive a
    stale guess once this already resolved the real answer).

    ``frontend_context`` selects the host or device DPC++ evidence.
    """
    # The acquisition key folds the ``-I`` spelling in, so the spelling is
    # canonicalized here -- the one choke point every caller reaches --
    # rather than trusted to each caller: a relative root on one caller and
    # an absolute one on another made two keys (and two full parses) for
    # one header set. Idempotent for absolute roots.
    extra_includes = absolutize_include_roots(list(extra_includes))
    clang_bin = _resolve_clang_bin(compiler, gcc_path, gcc_prefix)
    dpcpp_multi_context, dpcpp_host_context = _resolve_dpcpp_acquisition(
        clang_bin, frontend_context, gcc_options, gcc_option_tokens
    )
    force_cpp, force_cpp20, explicit_c_request, cc_id = resolve_clang_langmode(
        lang,
        headers,
        clang_bin,
        gcc_options,
        gcc_option_tokens,
        exported_symbols,
    )

    def _resolve_sysinc(*, force_cpp: bool) -> tuple[str, ...]:
        return _resolve_clang_system_includes(
            compiler,
            gcc_path=gcc_path,
            gcc_prefix=gcc_prefix,
            sysroot=sysroot,
            nostdinc=nostdinc,
            force_cpp=force_cpp,
            gcc_options=gcc_options,
            gcc_option_tokens=gcc_option_tokens,
        )

    system_includes = _resolve_sysinc(force_cpp=force_cpp)
    cpp_system_includes = (
        system_includes if force_cpp else _resolve_sysinc(force_cpp=True)
    )
    frontend_identity = _tool_identity(clang_bin)
    compiler_identity = frontend_identity

    def _make_key(fcpp: bool, fcpp20: bool, sysinc: tuple[str, ...]) -> str:
        return _cache_key(
            headers,
            extra_includes,
            clang_bin,
            gcc_path=gcc_path,
            gcc_prefix=gcc_prefix,
            gcc_options=gcc_options,
            gcc_option_tokens=gcc_option_tokens,
            sysroot=sysroot,
            nostdinc=nostdinc,
            lang=lang,
            backend="clang",
            system_includes=sysinc,
            extra_hash_dirs=extra_hash_dirs,
            frontend_identity=frontend_identity,
            compiler_identity=compiler_identity,
            force_cpp=fcpp,
            force_cpp20=fcpp20,
            frontend_context=frontend_context,
            invocation_tool=(
                clang_bin,
                cc_id,
                str(dpcpp_multi_context),
                str(dpcpp_host_context),
            ),
        )

    key = _make_key(
        force_cpp,
        force_cpp20,
        system_includes if force_cpp else (*system_includes, *cpp_system_includes),
    )
    if _expected_acquisition_key is not None and key != _expected_acquisition_key:
        raise SnapshotError("header inputs changed before clang acquisition started")
    resolved_kind = (
        frontend_context if dpcpp_multi_context or dpcpp_host_context else None
    )
    cached = _cache_path(key, backend="clang")

    def _cpp_retry_mode() -> tuple[bool, bool, tuple[str, ...]]:  # C->C++ self-heal
        return True, _detect_cpp20_headers(headers), cpp_system_includes

    def _entry_path(acquired: tuple[Any, str | None, bool]) -> Path:  # its cache entry
        retry_key = None if acquired[2] == force_cpp else _make_key(*_cpp_retry_mode())
        return cached if retry_key is None else _cache_path(retry_key, backend="clang")

    if not _coordinated and ast_acquisition_active():
        # A warm disk hit is an acquisition too, so it is read on the
        # `_coordinated=True` re-entry below, never ahead of this call --
        # `run_ast_acquisition`'s own docstring has the rule.
        return run_ast_acquisition_offering_entry(
            "clang",
            key,
            _entry_path,
            lambda: clang_header_dump(
                headers,
                extra_includes,
                compiler,
                gcc_path=gcc_path,
                gcc_prefix=gcc_prefix,
                gcc_options=gcc_options,
                gcc_option_tokens=gcc_option_tokens,
                sysroot=sysroot,
                nostdinc=nostdinc,
                lang=lang,
                extra_hash_dirs=extra_hash_dirs,
                frontend_context=frontend_context,
                memoize=memoize,
                pruning_header_roots=pruning_header_roots,
                exported_symbols=exported_symbols,
                _coordinated=True,
                _expected_acquisition_key=key,
                run_ast=run_ast,
            ),
        )

    _memoize = resolve_request_memoization(memoize)
    _cached_result = load_cached_ast(
        key, "clang", cached, memoize=_memoize, on_disk_load=materialize_locations
    )
    if _cached_result is not None:
        return cast("dict[str, Any]", _cached_result), resolved_kind, force_cpp

    agg_ext = ".hpp" if force_cpp else ".h"
    with tempfile.NamedTemporaryFile(suffix=agg_ext, mode="w", delete=False) as agg:
        agg_path = Path(agg.name)
    active_headers = list(headers)

    def _write_agg(hdrs: list[Path]) -> None:
        agg_path.write_text(clang_aggregate_text(hdrs), encoding="utf-8")

    _write_agg(active_headers)

    _ast_paths: list[Path] = []  # each attempt's AST, cleaned up in `finally` below

    def _run_clang(
        fcpp: bool, fcpp20: bool, sysinc: tuple[str, ...]
    ) -> subprocess.CompletedProcess[str]:
        cmd = _build_clang_header_command(
            clang_bin,
            cc_id,
            extra_includes,
            agg_path,
            sysroot=sysroot,
            nostdinc=nostdinc,
            gcc_options=gcc_options,
            gcc_option_tokens=gcc_option_tokens,
            force_cpp=fcpp,
            force_cpp20=fcpp20,
            system_includes=sysinc,
            dpcpp_multi_context=dpcpp_multi_context,
            dpcpp_host_context=dpcpp_host_context,
        )
        try:
            return run_ast(cmd, timeout=120, on_created=_ast_paths.append)
        except subprocess.TimeoutExpired as exc:
            raise SnapshotError(
                "clang timed out after 120 seconds parsing the header(s). The header "
                "may contain syntax that causes the frontend to hang. The clang "
                "process (and any child processes) has been terminated."
            ) from exc

    try:
        result = _run_clang(force_cpp, force_cpp20, system_includes)
        # C→C++ self-heal: a pure-``#include`` umbrella header (e.g. oneTBB's
        # ``oneapi/tbb.h``) picks C mode, then ``#include <cstddef>`` fails — a
        # missing C++ *standard* header is an unambiguous "this is C++" signal, so
        # retry once in C++ mode with the pre-resolved C++ system includes. Skipped
        # when already C++ or the failure is anything but a missing C++ stdlib header.
        if (
            result.returncode != 0
            and not force_cpp
            and _is_missing_cpp_stdlib_header_error(result.stderr or "")
        ):
            _log_c_to_cpp_selfheal(explicit_c_request)
            cur_fcpp, cur_fcpp20, cur_sysinc = _cpp_retry_mode()
            result = _run_clang(cur_fcpp, cur_fcpp20, cur_sysinc)
        else:
            cur_fcpp, cur_fcpp20, cur_sysinc = force_cpp, force_cpp20, system_includes
        result = retry_excluding_error_headers(
            result=result,
            run_clang=lambda: _run_clang(cur_fcpp, cur_fcpp20, cur_sysinc),
            write_agg=_write_agg,
            agg_path=agg_path,
            active_headers=active_headers,
        )
        identities_stable = _tool_identity(clang_bin) == frontend_identity
        if not identities_stable:
            log.warning(
                "AST toolchain changed during clang execution; skipping cache write"
            )
        # Write under the mode that ACTUALLY produced `result`, not the
        # stale pre-retry `key`/`cached` (see this function's own docstring).
        write_key = (
            key
            if cur_fcpp == force_cpp
            else _make_key(cur_fcpp, cur_fcpp20, cur_sysinc)
        )
        write_cached = (
            cached if cur_fcpp == force_cpp else _cache_path(write_key, backend="clang")
        )
        stability_includes = (
            (system_includes if force_cpp else (*system_includes, *cpp_system_includes))
            if cur_fcpp == force_cpp
            else cur_sysinc
        )
        if _make_key(cur_fcpp, cur_fcpp20, stability_includes) != write_key:
            raise SnapshotError(
                "header inputs changed while clang was acquiring L2 evidence; "
                "discarding the unstable result"
            )
        root = _parse_clang_ast_result(
            result,
            write_cached,
            _ast_paths[-1],
            cache_write=identities_stable,
            dpcpp_capable=dpcpp_multi_context,
            frontend_context=frontend_context,
            header_roots=pruning_header_roots
            if pruning_header_roots is not None
            else tuple(str(h) for h in headers),
        )
        if identities_stable and _memoize:
            # Kept under the ORIGINAL `key`, not `write_key` (Codex review, P1):
            # the memo is a one-shot HANDOFF to `_attach_header_graph`'s own
            # follow-up call, which independently recomputes this same
            # pre-retry key -- storing under `write_key` would miss that
            # lookup, repeating both clang attempts and leaking the handed-off
            # tree in this thread's slot forever. Safe: that consumer discards
            # `resolved_force_cpp`, unlike the disk path keyed correctly above.
            store_cached_ast(key, "clang", root)
        return root, resolved_kind, cur_fcpp
    finally:
        agg_path.unlink(missing_ok=True)
        for _p in _ast_paths:
            _p.unlink(missing_ok=True)


class ClangBackend:
    """The ``clang`` :class:`~abicheck.extract.headers.backend.HeaderAstBackend`.

    *runner* runs one clang command and returns the completed process; the
    default is :func:`~abicheck.dumper_clang_errors.run_clang_ast`.
    """

    name = "clang"

    def __init__(self, runner: ClangAstRunner = run_clang_ast) -> None:
        self.runner = runner

    def parse(
        self, request: HeaderParseRequest, *, fallback_reason: str | None = None
    ) -> _ClangAstParser:
        """Run clang for *request* and return its stamped AST parser.

        *fallback_reason* records why castxml was not used, when this run
        is the auto-fallback from a failed castxml attempt.
        """
        r = request
        clang_bin = _resolve_clang_bin(r.compiler, r.gcc_path, r.gcc_prefix)
        ast_root, resolved_kind, resolved_force_cpp = clang_header_dump(
            r.headers,
            r.extra_includes,
            compiler=r.compiler,
            gcc_path=r.gcc_path,
            gcc_prefix=r.gcc_prefix,
            gcc_options=r.gcc_options,
            gcc_option_tokens=r.gcc_option_tokens,
            sysroot=r.sysroot,
            nostdinc=r.nostdinc,
            lang=r.lang,
            extra_hash_dirs=r.extra_hash_dirs,
            frontend_context=r.frontend_context,
            pruning_header_roots=r.pruning_header_roots
            if r.pruning_header_roots is not None
            else tuple(r.public_header_paths + r.public_dir_paths),
            exported_symbols=frozenset(r.exported_dynamic | r.exported_static),
            run_ast=self.runner,
        )
        is_cl_mode = _effective_driver_mode_is_cl(
            _is_cl_style_driver_name(clang_bin), r.gcc_options, r.gcc_option_tokens
        )
        _target_known = not _forwards_response_file(r.gcc_options, r.gcc_option_tokens)
        _bare_reprobe_args = _forwarded_driver_mode_token(
            r.gcc_options, r.gcc_option_tokens
        )

        def _bare_reprobe() -> str | None:
            return (
                _configured_target_triple(None, _bare_reprobe_args, clang_bin)
                if _target_known
                else None
            )

        _guess_ok = (
            _target_known
            and _is_default_clang_bin(clang_bin, r.compiler)
            and not _clang_bin_is_explicitly_configured(r.gcc_path, r.gcc_prefix)
        )
        target_triple = _configured_target_triple(
            r.gcc_options, r.gcc_option_tokens, clang_bin
        ) or (
            (
                _explicit_target_triple(
                    r.gcc_options, r.gcc_option_tokens, cl_style=True
                )
                or _bare_reprobe()
            )
            if is_cl_mode
            else _explicit_target_triple(r.gcc_options, r.gcc_option_tokens)
            or _bare_reprobe()
            or (sys.platform if _guess_ok else None)
        )
        parser = _ClangAstParser(
            ast_root,
            r.exported_dynamic,
            r.exported_static,
            public_header_paths=r.public_header_paths,
            public_dir_paths=r.public_dir_paths,
            target_triple=target_triple,
            no_binary_evidence=r.no_binary_evidence,
            is_cxx=resolved_force_cpp,
        )
        setattr(
            parser,
            "_abicheck_neutral_factory",
            lambda: _ClangAstParser(
                ast_root,
                set(),
                set(),
                public_header_paths=r.public_header_paths,
                public_dir_paths=r.public_dir_paths,
                target_triple=target_triple,
                no_binary_evidence=False,
                is_cxx=resolved_force_cpp,
            ),
        )
        stamped = cast(
            _ClangAstParser,
            _stamp_ast_parser(
                parser,
                producer="clang",
                executable=clang_bin,
                compiler=r.compiler,
                gcc_path=r.gcc_path,
                gcc_prefix=r.gcc_prefix,
                fallback_reason=fallback_reason,
                resolved_compiler=None,
                resolved_force_cpp=resolved_force_cpp,
                gcc_options=r.gcc_options,
                gcc_option_tokens=r.gcc_option_tokens,
            ),
        )
        setattr(stamped, "_abicheck_frontend_context_kind", resolved_kind)
        return stamped
