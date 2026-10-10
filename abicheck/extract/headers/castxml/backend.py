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

"""The castxml header-AST backend (lane B, stage B2b).

:class:`CastxmlBackend` runs CastXML over a header set and returns the
:class:`~abicheck.dumper_castxml._CastxmlParser` built from its XML -- the
IR-fragment producer
:func:`~abicheck.extract.header_ast_fields.parse_header_ast_fields` reads.
:func:`castxml_dump` is the cached, coordinated, C-to-C++-retrying tool run
it wraps, moved verbatim from ``dumper._castxml_dump``.

The process runner and the scan-deadline check are injected:
``CastxmlBackend(runner=..., check_deadline=...)`` hands both to
:func:`castxml_dump`. A castxml run failure surfaces as
:class:`CastxmlRunError`, so the dump's castxml-to-clang fallback policy (in
``dumper._header_ast_parser``) can tell a failed run apart from a failure
after it.
"""

from __future__ import annotations

import functools
import logging
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import cast
from xml.etree.ElementTree import Element, tostring

from ....dumper_ast_config import (
    _build_castxml_command,
    _cache_key,
    _resolve_compiler_binary,
)
from ....dumper_ast_config_cpp20 import _detect_cpp20_headers
from ....dumper_castxml import _CastxmlParser
from ....errors import SnapshotError, UnsupportedCastxmlVersionError
from ....extract.headers.castxml.policy import evaluate_castxml_version
from ....extract.headers.castxml.probe import (
    _castxml_cpp_retry_allowed,
    _validate_castxml_output,
    castxml_dump_excluding_unparseable,
    check_scan_deadline,
    record_unparseable_headers,
    run_castxml,
)
from ....extract.headers.toolchain import (
    _allow_unsupported_castxml_enabled,
    _resolve_force_cpp,
    _resolve_selected_tool,
    _stamp_ast_parser,
    _tool_identity,
    _tool_identity_metadata,
)
from ....storage.atomic_file import atomic_write as _atomic_write
from ....storage.cache_integrity import record_digest
from ....storage.header_ast_cache import (
    _cache_path,
    ast_acquisition_active,
    read_cached_castxml as _read_castxml_cache,
    run_ast_acquisition,
)
from ...castxml_header_compat import write_castxml_aggregate
from ...path_aliases import absolutize_include_roots
from ..backend import HeaderParseRequest
from .calling_convention import calling_conventions_in
from .macro_table import attach_macro_table, resolve_macro_table

# The orchestrator's logger name, kept from before the move.
log = logging.getLogger("abicheck.dumper")

#: Runs one castxml command (``cmd``, ``timeout``) and returns the completed
#: process with captured text output.
CastxmlRunner = Callable[..., subprocess.CompletedProcess[str]]


class CastxmlRunError(SnapshotError):
    """The castxml run itself failed; :attr:`original` is the run's error.

    A :class:`SnapshotError`, so a caller that only catches that keeps
    working; the dump's fallback policy catches this narrower type to decide
    whether clang may take over.
    """

    def __init__(self, original: SnapshotError) -> None:
        super().__init__(str(original))
        self.original = original


def _resolve_gated_castxml_bin(castxml_bin: str | None) -> str:
    """Resolve the castxml executable and fail closed on an out-of-policy build.

    The version gate (``castxml_policy``) runs *before* any header is parsed. An
    out-of-policy build (notably the legacy PyPI ``castxml`` distribution) is
    rejected unless the caller explicitly opted in via
    ``ABICHECK_ALLOW_UNSUPPORTED_CASTXML``. Skipped when the executable itself
    could not even be resolved/probed (``"error"`` key) — that is a different,
    pre-existing failure mode (missing/unreadable binary) that the actual castxml
    invocation reports precisely; this gate only judges a version it could
    actually observe.
    """
    try:
        resolved = castxml_bin or _resolve_selected_tool("castxml")
    except OSError as exc:
        raise SnapshotError(
            "castxml not found in PATH. Install with: apt install castxml, "
            "brew install castxml, conda install -c conda-forge castxml, "
            "or choco install castxml (Windows); then ensure castxml is in PATH. "
            "On a clang-only host, set compile.frontend: clang in .abicheck.yml "
            "(or ABICHECK_AST_FRONTEND=clang) to use the clang JSON-AST backend "
            "instead — note it does not carry record size/alignment/offset "
            "layout, so layout-only breaks need castxml or debug info (L1)."
        ) from exc
    meta = _tool_identity_metadata(resolved)
    if "error" not in meta:
        check = evaluate_castxml_version(meta.get("version", ""))
        if not check.supported and not _allow_unsupported_castxml_enabled():
            raise UnsupportedCastxmlVersionError(check.message(found_at=resolved))
    return resolved


def _write_castxml_cache(
    cached: Path,
    out_xml: Path,
    *,
    castxml_bin: str,
    cc_bin: str,
    frontend_identity: str,
    compiler_identity: str,
) -> None:
    """Persist a fresh castxml XML dump, unless the toolchain moved underneath us.

    The cache key encodes the frontend/compiler identities observed *before* the
    run; if either changed while castxml was executing, the produced XML no
    longer describes that key, so the write is skipped rather than poisoning the
    cache. A cache write that fails on I/O is a warning, never an error — the
    dump itself already succeeded.
    """
    if (
        _tool_identity(castxml_bin) != frontend_identity
        or _tool_identity(cc_bin) != compiler_identity
    ):
        log.warning(
            "AST toolchain changed during CastXML execution; skipping cache write"
        )
        return
    try:
        _atomic_write(cached, out_xml.read_bytes())
        record_digest(cached)  # so the first read is already verified
    except OSError as exc:
        log.warning("Could not write castxml AST cache %s: %s", cached, exc)


def castxml_dump(
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
    castxml_bin: str | None = None,
    _selected_tool_out: list[str] | None = None,
    _selected_meta_out: list[tuple[str, bool]] | None = None,
    exported_symbols: frozenset[str] = frozenset(),
    _coordinated: bool = False,
    _expected_acquisition_key: str | None = None,
    run: CastxmlRunner = run_castxml,
    check_deadline: Callable[[], None] = check_scan_deadline,
) -> Element:
    """Run CastXML on *headers* and return its parsed XML root.

    *run* runs one castxml command (default
    :func:`~abicheck.extract.headers.castxml.probe.run_castxml`: the scan-deadline
    check, then a bounded run) and *check_deadline* is the scan-deadline
    check between attempts. Both are injected, not patched:
    :class:`CastxmlBackend` passes its own.
    """
    # One ``-I`` spelling per root for the acquisition key; see
    # ``clang_header_dump``.
    extra_includes = absolutize_include_roots(list(extra_includes))
    castxml_bin = _resolve_gated_castxml_bin(castxml_bin)
    if _selected_tool_out is not None:
        _selected_tool_out.append(castxml_bin)

    force_cpp = _resolve_force_cpp(
        lang, headers, gcc_options, gcc_option_tokens, exported_symbols
    )
    force_cpp20 = force_cpp and _detect_cpp20_headers(headers)
    resolved_compiler = compiler
    if not force_cpp and not gcc_path and not gcc_prefix:
        resolved_compiler = {
            "c++": "cc",
            "g++": "gcc",
            "clang++": "clang",
        }.get(compiler, compiler)
    cc_bin, cc_id = _resolve_compiler_binary(resolved_compiler, gcc_path, gcc_prefix)
    cc_bin = shutil.which(cc_bin) or cc_bin
    frontend_identity = _tool_identity(castxml_bin)
    compiler_identity = _tool_identity(cc_bin)

    def _make_key() -> str:
        return _cache_key(
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
            frontend_identity=frontend_identity,
            compiler_identity=compiler_identity,
            force_cpp=force_cpp,
            force_cpp20=force_cpp20,
            invocation_tool=(cc_bin, cc_id, castxml_bin),
        )

    key = _make_key()
    if _expected_acquisition_key is not None and key != _expected_acquisition_key:
        raise SnapshotError("header inputs changed before CastXML acquisition started")
    cached = _cache_path(key)
    if not _coordinated and ast_acquisition_active():
        # Same ordering rule (and reason) as the clang backend above; waiters
        # also get the producer's `(resolved_compiler, force_cpp)` selection.

        def _produce() -> tuple[Element, tuple[str, bool]]:
            produced_meta: list[tuple[str, bool]] = []
            produced = castxml_dump(
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
                castxml_bin=castxml_bin,
                _selected_meta_out=produced_meta,
                exported_symbols=exported_symbols,
                _coordinated=True,
                _expected_acquisition_key=key,
                run=run,
                check_deadline=check_deadline,
            )
            return produced, produced_meta[-1]

        root, selected_meta = run_ast_acquisition("castxml", key, _produce)
        if _selected_meta_out is not None:
            _selected_meta_out.append(selected_meta)
        return root

    if cached.exists():
        check_deadline()
        _cached_root = _read_castxml_cache(cached)
        if _cached_root is not None:
            check_deadline()
            if _selected_meta_out is not None:
                _selected_meta_out.append((resolved_compiler, force_cpp))
            return _cached_root

    with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as tmp:
        out_xml = Path(tmp.name)

    final_force_cpp = force_cpp
    try:
        try:
            root = _run_castxml_attempt(
                cc_bin,
                cc_id,
                headers,
                extra_includes,
                out_xml,
                sysroot=sysroot,
                nostdinc=nostdinc,
                gcc_options=gcc_options,
                gcc_option_tokens=gcc_option_tokens,
                force_cpp=force_cpp,
                castxml_bin=castxml_bin,
                run=run,
            )
        except SnapshotError as primary:
            if not _castxml_cpp_retry_allowed(
                primary, force_cpp=force_cpp, headers=headers
            ):
                raise
            log.warning(
                "castxml failed to parse the header(s) under compile.lang: c; the header "
                "contains C++-only constructs (class / namespace / template), so "
                "retrying in C++ mode. Set compile.lang: c++ in .abicheck.yml to "
                "select this directly and silence this warning."
            )
            try:
                root = _run_castxml_attempt(
                    cc_bin,
                    cc_id,
                    headers,
                    extra_includes,
                    out_xml,
                    sysroot=sysroot,
                    nostdinc=nostdinc,
                    gcc_options=gcc_options,
                    gcc_option_tokens=gcc_option_tokens,
                    force_cpp=True,
                    castxml_bin=castxml_bin,
                    run=run,
                )
                final_force_cpp = True
            except SnapshotError as retry_exc:
                # Both modes failed — surface the originally requested C-mode
                # error (and its hint), not the fallback's, so the diagnostic
                # matches what the user asked for. Mark it as a failed
                # language-mode retry and carry the retry's diagnostics, so
                # the unparseable-header fallback can still attribute it to
                # a header instead of treating it as a toolchain failure.
                primary.language_retry_failed = True
                primary.attribution_stderr = getattr(retry_exc, "stderr", None) or str(
                    retry_exc
                )
                raise primary from None
        if final_force_cpp == force_cpp:
            if _make_key() != key:
                raise SnapshotError(
                    "header inputs changed while CastXML was acquiring L2 "
                    "evidence; discarding the unstable result"
                )
            _write_castxml_cache(
                cached,
                out_xml,
                castxml_bin=castxml_bin,
                cc_bin=cc_bin,
                frontend_identity=frontend_identity,
                compiler_identity=compiler_identity,
            )
        check_deadline()
        if _selected_meta_out is not None:
            _selected_meta_out.append((resolved_compiler, final_force_cpp))
        return root
    finally:
        out_xml.unlink(missing_ok=True)


def _run_castxml_attempt(
    cc_bin: str,
    cc_id: str,
    headers: list[Path],
    extra_includes: list[Path],
    out_xml: Path,
    *,
    sysroot: Path | None,
    nostdinc: bool,
    gcc_options: str | None,
    gcc_option_tokens: tuple[str, ...] = (),
    force_cpp: bool,
    castxml_bin: str = "castxml",
    run: CastxmlRunner = run_castxml,
) -> Element:
    """Run one castxml invocation in a fixed language mode and parse its output.

    Writes the aggregate ``#include`` header (``.h`` for C, ``.hpp`` for C++),
    builds and runs the castxml command, and validates the result. Raises
    :class:`SnapshotError` on a non-zero exit, a timeout, or empty/invalid XML —
    leaving *out_xml* in place on success so the caller can cache it. The agg
    header is always cleaned up. Factored out of :func:`_castxml_dump` so the
    C→C++ fallback (G16/A3) can re-run with a different mode without duplicating
    the run/validate plumbing.
    """
    # Detect C++20 concept / requires syntax — castxml's default standard
    # (typically C++17) rejects these, so we override it. Only in C++ mode.
    force_cpp20 = force_cpp and _detect_cpp20_headers(headers)
    agg_ext = ".hpp" if force_cpp else ".h"

    agg_path = write_castxml_aggregate(headers, agg_ext)  # rmtree'd in `finally`

    cmd = _build_castxml_command(
        cc_bin,
        cc_id,
        extra_includes,
        out_xml,
        agg_path,
        sysroot=sysroot,
        nostdinc=nostdinc,
        gcc_options=gcc_options,
        gcc_option_tokens=gcc_option_tokens,
        force_cpp=force_cpp,
        force_cpp20=force_cpp20,
        castxml_bin=castxml_bin,
    )

    try:
        try:
            result = run(cmd, timeout=120)
        except subprocess.TimeoutExpired as exc:
            stderr_snippet = ""
            if exc.stderr:
                text = (
                    exc.stderr
                    if isinstance(exc.stderr, str)
                    else exc.stderr.decode("utf-8", errors="replace")
                )
                stderr_snippet = f"\nPartial stderr: {text[:1000].strip()}"
            raise SnapshotError(
                f"castxml timed out after 120 seconds. The header file may contain "
                f"syntax that causes the compiler to hang. Check that the header "
                f"is valid and can be compiled with gcc/g++. The castxml process "
                f"(and any child processes) has been terminated.{stderr_snippet}"
            ) from exc
        root = _validate_castxml_output(
            result, out_xml, headers, force_cpp, castxml_bin=castxml_bin
        )
        _record_calling_convention_macros(root, cmd, out_xml, agg_path.parent, run)
        return root
    finally:
        shutil.rmtree(agg_path.parent, ignore_errors=True)


def _record_calling_convention_macros(
    root: Element,
    cmd: list[str],
    out_xml: Path,
    work_dir: Path,
    run: CastxmlRunner,
) -> None:
    """Attach the compiler-resolved calling-convention macros to *root* and
    to *out_xml* (so the cached AST keeps them); see :mod:`.macro_table`.
    A failed preprocess run leaves both untouched."""
    table = resolve_macro_table(
        cmd, work_dir, run, lambda t: bool(calling_conventions_in(t))
    )
    if table is None:
        return
    extra = Element("_")
    attach_macro_table(extra, table)
    attach_macro_table(root, table)
    data = out_xml.read_bytes()
    close_at = data.rfind(b"</")
    if close_at == -1:
        return
    payload = b"".join(tostring(el) for el in extra)
    out_xml.write_bytes(data[:close_at] + payload + data[close_at:])


class CastxmlBackend:
    """The ``castxml`` :class:`~abicheck.extract.headers.backend.HeaderAstBackend`.

    *runner* runs one castxml command; *check_deadline* is the scan-deadline
    check between attempts. Defaults: ``dumper_castxml_probe.run_castxml``
    and ``check_scan_deadline``.
    """

    name = "castxml"

    def __init__(
        self,
        runner: CastxmlRunner = run_castxml,
        check_deadline: Callable[[], None] = check_scan_deadline,
    ) -> None:
        self.runner = runner
        self.check_deadline = check_deadline

    def parse(
        self, request: HeaderParseRequest, *, fallback_reason: str | None = None
    ) -> _CastxmlParser:
        """Run castxml for *request* and return its stamped parser.

        Raises :class:`CastxmlRunError` when the castxml run fails.
        """
        r = request
        selected_castxml: list[str] = []
        selected_meta: list[tuple[str, bool]] = []

        _dump = functools.partial(
            castxml_dump, run=self.runner, check_deadline=self.check_deadline
        )

        try:
            xml_root, unparseable = castxml_dump_excluding_unparseable(
                _dump,
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
                _selected_tool_out=selected_castxml,
                _selected_meta_out=selected_meta,
                exported_symbols=frozenset(r.exported_dynamic | r.exported_static),
            )
        except SnapshotError as exc:
            raise CastxmlRunError(exc) from exc
        parser = _CastxmlParser(
            xml_root,
            r.exported_dynamic,
            r.exported_static,
            public_header_paths=r.public_header_paths,
            public_dir_paths=r.public_dir_paths,
            no_binary_evidence=r.no_binary_evidence,
        )
        setattr(
            parser,
            "_abicheck_neutral_factory",
            lambda: _CastxmlParser(
                xml_root,
                set(),
                set(),
                public_header_paths=r.public_header_paths,
                public_dir_paths=r.public_dir_paths,
                no_binary_evidence=False,
            ),
        )
        meta = selected_meta[0] if selected_meta else (None, None)
        return cast(
            _CastxmlParser,
            record_unparseable_headers(
                _stamp_ast_parser(
                    parser,
                    producer="castxml",
                    executable=selected_castxml[0] if selected_castxml else "castxml",
                    compiler=r.compiler,
                    gcc_path=r.gcc_path,
                    gcc_prefix=r.gcc_prefix,
                    fallback_reason=fallback_reason,
                    resolved_compiler=meta[0],
                    resolved_force_cpp=meta[1],
                    gcc_options=r.gcc_options,
                    gcc_option_tokens=r.gcc_option_tokens,
                ),
                unparseable,
            ),
        )
