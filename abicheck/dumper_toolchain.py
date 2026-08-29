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

"""Small filesystem and AST-toolchain identity helpers for :mod:`dumper`."""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import signal
import stat as stat_module
import subprocess
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any, TypedDict

from ._compiler_options import has_explicit_cpp_std, has_explicit_std, split_gcc_options
from .buildsource.redaction import DEFAULT_REDACTION
from .dumper_ast_config import _detect_cpp_headers
from .dumper_ast_config_cpp20 import _detect_cpp20_headers

log = logging.getLogger(__name__)


def _safe_mtime(path: Path) -> tuple[float | None, bool]:
    """Return (mtime, SOURCE_DATE_EPOCH substitution), or (None, False)."""
    source_date_epoch = os.environ.get("SOURCE_DATE_EPOCH")
    if source_date_epoch:
        try:
            return float(int(source_date_epoch.strip())), True
        except (ValueError, OverflowError):
            pass
    try:
        return path.stat().st_mtime, False
    except OSError:
        return None, False


def _safe_size(path: Path) -> int | None:
    """Return path's byte size, or None when it cannot be stat'd."""
    try:
        return path.stat().st_size
    except OSError:
        return None


def _castxml_available() -> bool:
    return shutil.which("castxml") is not None


@lru_cache(maxsize=64)
def _executable_sha256(
    real_path: str,
    device: int,
    inode: int,
    mtime_ns: int,
    ctime_ns: int,
    size: int,
) -> str:
    """Hash one exact executable revision (stat fields invalidate memoization)."""
    del device, inode, mtime_ns, ctime_ns, size
    digest = hashlib.sha256()
    with Path(real_path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@lru_cache(maxsize=64)
def _tool_version_output(selected_path: str, digest: str) -> str:
    """Return bounded ``--version`` output for one exact executable revision."""
    del digest
    limit = 64 * 1024
    raw = bytearray()
    try:
        # Avoid preexec_fn: dumps can originate from a threaded service caller
        # paths, where Python documents it as unsafe. A parent-side reader caps
        # output and kills a noisy process without buffering an unbounded pipe.
        process = subprocess.Popen(
            [selected_path, "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=os.name == "posix",
        )
        assert process.stdout is not None
        stdout = process.stdout

        def _kill() -> None:
            try:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
            except OSError:
                pass

        def _read_capped() -> None:
            while chunk := stdout.read(8192):
                remaining = limit + 1 - len(raw)
                if remaining > 0:
                    raw.extend(chunk[:remaining])
                if len(raw) > limit:
                    _kill()
                    break

        reader = threading.Thread(target=_read_capped, daemon=True)
        reader.start()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _kill()
            process.wait()
            raise
        finally:
            reader.join(timeout=1)
            stdout.close()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"unavailable:{type(exc).__name__}:{exc}"
    truncated = len(raw) > limit
    text = raw[:limit].decode("utf-8", errors="replace")
    if truncated:
        text += "\n[truncated]"
    return "\n".join(line.rstrip() for line in text.splitlines() if line.strip())


@lru_cache(maxsize=64)
def _tool_target_triple(selected_path: str, digest: str) -> str | None:
    """Return ``<tool> -dumpmachine`` output for one exact executable
    revision, or ``None`` when the tool doesn't support the flag (e.g.
    castxml itself) or the probe fails. GCC/G++/Clang/Clang++ all accept
    ``-dumpmachine``; there is no MSVC equivalent (cl.exe is never the
    executable this is probed against — see the gnu/msvc dialect split in
    ``dumper._header_ast_parser``)."""
    del digest
    try:
        result = subprocess.run(
            [selected_path, "-dumpmachine"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
            text=True,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    triple = result.stdout.strip()
    return triple or None


def _resolved_tool(executable: str) -> tuple[str, Path, os.stat_result, str]:
    selected = shutil.which(executable)
    if selected is None:
        separators = tuple(sep for sep in (os.sep, os.altsep) if sep)
        if not Path(executable).is_absolute() and not any(
            sep in executable for sep in separators
        ):
            raise FileNotFoundError(f"tool not found on PATH: {executable}")
        selected = executable
    real = Path(selected).resolve(strict=True)
    stat = real.stat()
    if not stat_module.S_ISREG(stat.st_mode):
        raise OSError(f"resolved tool is not a regular file: {real}")
    digest = _executable_sha256(
        str(real),
        stat.st_dev,
        stat.st_ino,
        stat.st_mtime_ns,
        stat.st_ctime_ns,
        stat.st_size,
    )
    return selected, real, stat, digest


def _resolve_selected_tool(executable: str) -> str:
    """Return the exact executable selected now, rejecting missing bare names."""
    return _resolved_tool(executable)[0]


def _tool_identity(executable: str) -> str:
    """Identify the executable selected by PATH, including content SHA256."""
    selected = shutil.which(executable) or executable
    try:
        selected, real, stat, digest = _resolved_tool(executable)
    except OSError as exc:
        return f"selected={selected};unavailable={type(exc).__name__}:{exc}"
    version = _tool_version_output(selected, digest)
    return (
        f"selected={selected};realpath={real};mtime_ns={stat.st_mtime_ns};"
        f"size={stat.st_size};sha256={digest};version={version}"
    )


def _tool_identity_metadata(executable: str) -> dict[str, str]:
    """Machine-readable subset of :func:`_tool_identity` for provenance."""
    selected = shutil.which(executable) or executable
    try:
        selected, real, stat, digest = _resolved_tool(executable)
        version = _tool_version_output(selected, digest)
    except OSError as exc:
        return {"selected": selected, "error": f"{type(exc).__name__}: {exc}"}
    metadata = {
        "selected": selected,
        "realpath": str(real),
        "mtime_ns": str(stat.st_mtime_ns),
        "size": str(stat.st_size),
        "sha256": digest,
        "version": version,
    }
    triple = _tool_target_triple(selected, digest)
    if triple is not None:
        metadata["target_triple"] = triple
    return metadata


def _stamp_ast_parser(
    parser: Any,
    *,
    producer: str,
    executable: str,
    compiler: str,
    gcc_path: str | None,
    gcc_prefix: str | None,
    fallback_reason: str | None = None,
    resolved_compiler: str | None = None,
    resolved_force_cpp: bool | None = None,
    gcc_options: str | None = None,
    gcc_option_tokens: tuple[str, ...] = (),
) -> Any:
    """Attach the frontend/compiler provenance attributes to a built parser.

    Module-level (rather than a closure over ``dumper._header_ast_parser``) so
    the stamping rules are readable on their own; *compiler*/*gcc_path*/
    *gcc_prefix* are the enclosing call's toolchain selection, needed only to
    probe the host compiler behind a castxml dump. *resolved_compiler*, when
    given, overrides *compiler* for that resolution -- the force_cpp-aware
    spelling ``dumper._castxml_dump`` actually invoked (e.g. ``"cc"``), not
    the caller's original, unresolved request (Codex review; see that
    function's own ``_selected_meta_out`` docstring). *resolved_force_cpp*,
    when given, records either backend's real, post-retry C->C++ self-heal
    as ``metadata["resolved_lang_mode"]``.

    *gcc_options*/*gcc_option_tokens* (Codex review, PR #816 follow-up):
    stamps ``metadata["language_standard_explicit"]`` (``"1"``/``"0"``) with
    whether the caller gave an explicit ``-std=``/``/std:`` — real
    provenance ``comparability._language_standard_content_divergence_
    corroborated`` reads to tell a genuinely content-driven, auto-resolved
    ``language_standard`` apart from an explicit pin whose value happens to
    collide with an auto-resolved literal (e.g. ``-std=gnu11`` given
    without ``--lang``, which is otherwise indistinguishable from the
    unpinned forced-``gnu11`` default by string inspection alone). Stored
    on ``AbiSnapshot.ast_toolchain`` -- a free-form provenance dict, not a
    ``profile_fields``/``PROFILE_FIELD_KEYS`` entry -- specifically so a
    fresh snapshot compared against a legacy baseline that predates this
    field (which simply lacks the key) degrades to "no provenance
    available" rather than tripping ``_unexplained_profile_fields``'s
    unconditionally-fatal ``unknown_differing`` check.

    Moved here from ``dumper.py`` (unchanged logic) purely to stay under that
    module's AI-readiness file-size hard cap -- ``parser`` is typed ``Any``
    rather than ``_CastxmlParser | _ClangAstParser`` to avoid importing those
    two dumper.py-local classes here just for a type hint.

    Resolving against *resolved_compiler* (rather than the caller's
    original *compiler*) is a real correctness fix, but it has one known,
    documented interaction with a *legacy* baseline persisted before this
    fix existed -- see
    ``comparability._language_standard_probe_upgrade_corroborated``'s own
    docstring for the full account (a legacy dump's ``compiler_family``/
    ``compiler_version`` can appear to "change" against a fresh re-dump of
    the identical installation, purely because this fix corrected which
    binary's identity gets recorded).
    """
    from .castxml_policy import evaluate_castxml_version
    from .dumper_ast_config import _resolve_compiler_binary
    from .errors import SnapshotError

    executable_meta = _tool_identity_metadata(executable)
    metadata = {"producer": producer, **executable_meta}
    dialect: str | None
    if producer == "clang":
        # clang is both frontend and compiler here (mirrors
        # _resolve_clang_langmode's own cc_id derivation for the same
        # binary); same dialect test used there -- and since it is the same
        # binary, reuse the probe above rather than re-hashing and
        # re-running --version on it (CodeRabbit review).
        compiler_meta = executable_meta
        dialect = "msvc" if Path(executable).name.lower() in ("cl", "cl.exe") else "gnu"
    else:
        try:
            host_cc, dialect = _resolve_compiler_binary(
                resolved_compiler or compiler, gcc_path, gcc_prefix
            )
            compiler_meta = _tool_identity_metadata(host_cc)
        except SnapshotError as exc:
            metadata["compiler_error"] = str(exc)
            compiler_meta = {}
            dialect = None
    metadata.update({f"compiler_{key}": value for key, value in compiler_meta.items()})
    # ADR-050 D1: surface the ABI dialect (gnu/msvc) instead of
    # discarding it -- compute_extraction_contract's abi_dialect field
    # reads this key (Codex review, PR #624 follow-up).
    if dialect is not None:
        metadata["abi_dialect"] = dialect
    if resolved_force_cpp is not None:
        metadata["resolved_lang_mode"] = "c++" if resolved_force_cpp else "c"
    metadata["language_standard_explicit"] = (
        "1" if has_explicit_std(gcc_options, gcc_option_tokens) else "0"
    )
    setattr(parser, "_abicheck_ast_toolchain", metadata)
    setattr(parser, "_abicheck_ast_fallback_reason", fallback_reason)
    if producer == "castxml":
        check = evaluate_castxml_version(metadata.get("version", ""))
        setattr(parser, "_abicheck_ast_supported", check.supported)
        setattr(parser, "_abicheck_ast_unsupported_reasons", check.reasons)
        metadata.update(check.provenance_fields())
    return parser


def _compiler_family_from_toolchain(ast_toolchain: dict[str, str]) -> str | None:
    """Best-effort ADR-050 ``compiler_family`` label from the resolved host
    compiler binary (low-stakes: used for ``profile_fingerprint`` stability,
    not semantic parsing, so a reasonable guess is fine — Codex review,
    PR #624).

    Reads ``compiler_selected`` first, not the bare ``selected`` key: for a
    castxml-produced snapshot, ``selected`` names the castxml binary itself
    (e.g. ``/usr/bin/castxml``), never the host compiler whose family/ABI
    dialect actually matters here; ``compiler_selected`` is the resolved
    host cc (see ``dumper._header_ast_parser``'s ``_stamp_parser``). For a
    clang-produced snapshot the two keys already carry the same value
    (clang is both frontend and compiler), so the fallback is harmless.
    """
    path = ast_toolchain.get("compiler_selected") or ast_toolchain.get("selected") or ""
    name = Path(path).name.lower() if path else ""
    if not name:
        return None
    if "clang" in name:
        return "clang"
    if name in ("cl", "cl.exe"):
        return "msvc"
    if "gcc" in name or "g++" in name:
        return "gnu"
    return name


def _ast_fallback_enabled() -> bool:
    return os.environ.get("ABICHECK_ALLOW_AST_FALLBACK", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _allow_unsupported_castxml_enabled() -> bool:
    """Explicit opt-in override for the CastXML version gate
    (``castxml_policy``). Same convention as ``_ast_fallback_enabled`` — a
    hard failure by default, degraded only on deliberate request."""
    return os.environ.get("ABICHECK_ALLOW_UNSUPPORTED_CASTXML", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _auto_ast_fallback_eligible(backend: str) -> bool:
    """Whether this request is genuinely unpinned ``auto`` selection."""
    choice = (backend or "auto").strip().lower()
    env_pin = os.environ.get("ABICHECK_AST_FRONTEND", "").strip().lower()
    return choice == "auto" and env_pin not in {"castxml", "clang", "hybrid"}


def _parser_ast_toolchain(parser: Any) -> dict[str, str]:
    return dict(getattr(parser, "_abicheck_ast_toolchain", {}))


def _parser_ast_fallback_reason(parser: Any) -> str | None:
    value = getattr(parser, "_abicheck_ast_fallback_reason", None)
    return str(value) if value else None


def _parser_ast_supported(parser: Any) -> bool | None:
    value = getattr(parser, "_abicheck_ast_supported", None)
    return bool(value) if value is not None else None


def _parser_ast_unsupported_reasons(parser: Any) -> list[str]:
    return list(getattr(parser, "_abicheck_ast_unsupported_reasons", []) or [])


def _parser_frontend_context_kind(parser: Any) -> str | None:
    """ADR-050 D5, G32 Phase D: the resolved SYCL/DPC++ ``kind``
    (``"host"``/``"device"``) a DPC++-capable clang invocation's
    ``sycl_context`` selection resolved to, or ``None`` for an ordinary,
    non-DPC++ parse -- mirrors :func:`_parser_ast_toolchain`'s own
    ``getattr`` pattern for parser-stamped metadata."""
    value = getattr(parser, "_abicheck_frontend_context_kind", None)
    return str(value) if value else None


#: Standard-mandated ``__cplusplus`` literal for every C++ edition this
#: project's own force_cpp20 path or a user's explicit ``-std=`` can name.
#: Keyed on the edition token alone (the part after the final ``+``/``c++``),
#: so "c++17", "gnu++17", and "/std:c++17" all resolve the same way. Not
#: exhaustive of every -std= spelling a user could pass — an unrecognized
#: edition leaves the macro value unset (None) rather than guessing.
_CPLUSPLUS_MACRO_BY_EDITION: dict[str, str] = {
    "98": "199711L",
    "03": "199711L",
    "11": "201103L",
    "0x": "201103L",
    "14": "201402L",
    "1y": "201402L",
    "17": "201703L",
    "1z": "201703L",
    "20": "202002L",
    "2a": "202002L",
    "23": "202302L",
    "2b": "202302L",
}


def _cplusplus_macro_for_standard(standard: str | None) -> str | None:
    """Map a resolved ``-std=``/``/std:`` value to its standard-mandated
    ``__cplusplus`` literal, or ``None`` when unrecognized (snapshot
    provenance, schema v15).

    A *probed* default (``standard`` carrying the ``"probed:"`` marker
    :func:`_probe_default_language_standard` produces — see its own
    docstring) already carries the literal macro assignment it observed, so
    it is read straight out of that string rather than looked up in
    :data:`_CPLUSPLUS_MACRO_BY_EDITION`, which only maps a real ``-std=``
    edition spelling. The marker is checked by *containment*, not
    ``str.startswith`` (Codex review, fresh evidence): when ``lang`` is
    also given, :func:`abicheck._compiler_options.language_standard_field`
    prefixes the probed value with ``"c++:"``/``"c:"``
    (``"c++:probed:__cplusplus=201703L"``), so the marker no longer sits at
    position 0."""
    if not standard:
        return None
    if _PROBED_STANDARD_PREFIX in standard:
        _, _, assignment = standard.partition(_PROBED_STANDARD_PREFIX)
        macro, _, value = assignment.partition("=")
        return value or None if macro == "__cplusplus" else None
    edition = (
        standard.rsplit("+", 1)[-1].lower() if "+" in standard else standard.lower()
    )
    return _CPLUSPLUS_MACRO_BY_EDITION.get(edition)


def _combined_option_tokens(
    gcc_options: str | None, gcc_option_tokens: tuple[str, ...]
) -> list[str]:
    """``gcc_option_tokens`` then split ``gcc_options`` (schema v15's
    ``ast_compile_args``) -- reversed order, unlike ``_extract_explicit_std_value``."""
    tokens = list(gcc_option_tokens)
    if gcc_options:
        tokens.extend(split_gcc_options(gcc_options))
    return tokens


def _extract_explicit_std_value(
    gcc_options: str | None, gcc_option_tokens: tuple[str, ...]
) -> str | None:
    """Pull the literal ``-std=``/``--std=``/``/std:`` value, or ``None``
    (schema v15). Last-wins over real command order (``gcc_options`` then
    ``gcc_option_tokens``) -- unlike ``_combined_option_tokens``'s reversed
    order + first-match, wrong once ``gcc_options`` merges into ``gcc_option_tokens``."""
    tokens = list(split_gcc_options(gcc_options)) if gcc_options else []
    tokens += gcc_option_tokens
    value: str | None = None
    for token in tokens:
        t = token[1:] if token.startswith("--") else token
        if t.startswith("-std="):
            value = t[len("-std=") :]
        elif t.lower().startswith("/std:"):
            value = t[len("/std:") :]
    return value


#: Prefix distinguishing a *probed* default standard (never asserted by the
#: caller — see :func:`_probe_default_language_standard`) from a real,
#: explicit ``-std=``/``/std:`` spelling or the ``force_cpp20`` literal
#: ``"gnu++20"`` -- so a probed value can never collide with, or be mistaken
#: for, a user-given one.
_PROBED_STANDARD_PREFIX = "probed:"


def _resolve_force_cpp(
    lang: str | None,
    headers: list[Path],
    gcc_options: str | None,
    gcc_option_tokens: tuple[str, ...],
) -> bool:
    """Decide whether the TU is C++ when no ``lang`` was explicitly given.

    An explicit ``--lang c++``/``cpp`` always wins. Otherwise, C++20
    concept/requires syntax (including an abbreviated constrained parameter
    like ``void f(std::integral auto x);``, which needs no
    class/namespace/template keyword at all) is on its own sufficient proof
    the header is C++ — without this, a header whose only C++ signal is such
    syntax stayed auto-detected as C (Codex review). Shared by both the clang
    and castxml frontends so the auto-detection rule cannot drift between
    them.

    ``for_language_mode_decision=True`` (Codex review): a
    ``#if __cplusplus``/``#ifdef __cplusplus``-guarded C++20 construct
    must not by itself promote an auto-detected header to C++ mode — in C
    mode ``__cplusplus`` is undefined, so that guard's content is not
    actually reachable there, and forcing C++ purely because it exists
    would then turn an *active*, unguarded use of the same word as an
    ordinary C identifier elsewhere in the header into a reserved-word
    parse error once C++20 mode is wrongly forced.

    Relocated here from ``dumper.py`` (Codex review, abicheck-internal-bugs
    finding 2 follow-up): :func:`_probe_default_language_standard`'s own
    probe needs this exact decision — the raw ``lang`` argument alone is
    not enough, since an unspecified ``lang`` (the common case: ``dump``'s
    own CLI squashes its Click default back to ``None`` so auto-detection
    can run — see ``cli_dump_helpers.perform_elf_dump``'s ``lang_explicit``
    handling) can still auto-detect as either C or C++ depending on the
    header's own content, and probing the wrong language mode would record
    a ``language_standard`` that describes a dialect the real parse never
    actually used, weakening the comparability guard's precision (Codex
    review: "two compiler versions with the same C default but different
    C++ defaults will be rejected as non-comparable despite matching
    extraction contexts"). ``dumper.py`` re-exports this name unchanged for
    its own callers.
    """
    if lang:
        return bool(lang.upper() in ("C++", "CPP"))
    return (
        _detect_cpp_headers(headers)
        or _detect_cpp20_headers(headers, for_language_mode_decision=True)
        or has_explicit_cpp_std(gcc_options, gcc_option_tokens)
    )


@lru_cache(maxsize=32)
def _probe_default_language_standard(compiler_bin: str, lang_mode: str) -> str | None:
    """Best-effort: what C/C++ edition *compiler_bin* actually resolves to
    when invoked with **no** explicit ``-std=`` at all (ADR-050 D1/D2
    follow-up — closes the "profile_fingerprint guard doesn't fire without
    L3 build evidence" gap: two header-AST dumps of genuinely different
    toolchains/versions, neither given an explicit standard, previously both
    recorded ``ast_resolved_standard=None`` and so trivially matched on
    ``language_standard``, letting ``compare`` produce a real verdict for two
    snapshots extracted under incompatible dialects instead of refusing).

    Probes the compiler's own predefined-macro table the same way
    ``buildsource.source_extractors.clang._clang_compiler_family`` already
    does for compiler-family identification (``-E -dM``): the frontend's own
    unpinned default is a real, observable fact — GCC 9's default C++ dialect
    genuinely differs from Clang 18's — it just isn't *asserted* by any flag
    this project passed, so it wasn't previously recorded at all rather than
    guessed at.

    Returns a ``"probed:"``-prefixed literal macro assignment
    (``"probed:__cplusplus=201703L"`` / ``"probed:__STDC_VERSION__=201710L"``)
    rather than a canonical ``-std=`` spelling: the exact edition a given
    ``__cplusplus``/``__STDC_VERSION__`` value maps to is not always
    recoverable (a compiler can report a nonstandard literal for a
    still-experimental standard), but the raw value alone is already
    sufficient for this function's actual purpose — two probes disagreeing
    on it is real evidence of a dialect difference, and two probes agreeing
    is real evidence there isn't one, regardless of what the edition is
    conventionally called. A pre-C99 C compiler defines no
    ``__STDC_VERSION__`` at all, so that absence is itself recorded as a
    distinct value rather than falling through to ``None`` (which would
    read as "not probed" rather than "genuinely probed as C89/ANSI C").

    ``None`` on any failure (compiler not found, times out, or rejects
    ``-E -dM`` — e.g. an MSVC ``cl.exe``, which uses different flags
    entirely) — a probe that can't run is simply not evidence, the same
    fail-open convention every other best-effort toolchain probe in this
    codebase already uses. Cached per ``(compiler_bin, lang_mode)`` pair, the
    same cost shape as ``_clang_compiler_family``/``_clang_compiler_version``,
    so a whole dump session pays this at most once per distinct resolved
    compiler.
    """
    macro_name = "__STDC_VERSION__" if lang_mode == "c" else "__cplusplus"
    try:
        r = subprocess.run(
            [compiler_bin, "-E", "-dM", "-x", lang_mode, "-"],
            input="",
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    for line in r.stdout.splitlines():
        parts = line.split(None, 2)
        if len(parts) >= 2 and parts[0] == "#define" and parts[1] == macro_name:
            value = parts[2].strip() if len(parts) >= 3 else ""
            return f"{_PROBED_STANDARD_PREFIX}{macro_name}={value}"
    if lang_mode == "c":
        # No __STDC_VERSION__ at all is itself a real, distinct signal
        # (pre-C99 default), not a probe failure.
        return f"{_PROBED_STANDARD_PREFIX}{macro_name}=<absent>"
    return None


#: Both ``dumper_ast_config._build_castxml_command`` (gated on ``cc_id ==
#: "gnu"``) and ``_build_clang_header_command`` (unconditional)
#: unconditionally force this standard for an unpinned C/gnu-dialect parse --
#: so the real, parsed dialect is this fixed value, never the resolved
#: compiler's own naked default (Codex review, fresh evidence: GCC's naked
#: default is typically C17, but the AST is always generated as gnu11).
_FORCED_C_STANDARD = "gnu11"

#: Mirrors ``tu_merge._HETEROGENEOUS_LANG_MODE`` (not imported directly --
#: ``tu_merge.py`` already imports from this module's sibling ``dumper.py``
#: chain, and a reverse import would risk a cycle the AI-readiness gate
#: rejects; the two are kept in lockstep by
#: ``tests/test_tu_merge.py``/``tests/test_ast_compile_provenance.py``
#: asserting the literal string). Written into a merged multi-TU manifest's
#: ``resolved_lang_mode`` when the contributing TUs genuinely disagree on
#: it -- signals "cannot determine this at all," distinct from the field
#: simply being absent (a single-TU dump, or a fragment merge where every
#: TU agreed), which still falls back to the static re-derivation below.
_HETEROGENEOUS_LANG_MODE = "heterogeneous"


def _resolve_standard_provenance(
    headers: list[Path],
    gcc_options: str | None,
    gcc_option_tokens: tuple[str, ...],
    *,
    probe_compiler_bin: str | None = None,
    lang: str | None = None,
    resolved_lang_mode: str | None = None,
    abi_dialect: str | None = None,
) -> str | None:
    """Best-effort resolved C/C++ standard for snapshot provenance (schema
    v15, P1 toolchain-profile audit).

    Mirrors the exact decision ``dumper.py``'s castxml/clang command builders
    already make (``has_explicit_std`` gates the requires/concept heuristic)
    without needing a second return value threaded through them, since it is
    a pure function of the same inputs: an explicit ``-std=``/``--std=``/
    ``/std:`` value is recorded verbatim; otherwise, if
    ``_detect_cpp20_headers`` would force C++20, ``"gnu++20"`` is recorded
    (the exact flag ``force_cpp20`` adds); otherwise, for an unpinned
    C/gnu-dialect parse, :data:`_FORCED_C_STANDARD` is recorded directly --
    *never probed* -- since both header-AST command builders force it
    unconditionally, regardless of what the resolved compiler's own naked
    default actually is; otherwise, when *probe_compiler_bin* is given,
    :func:`_probe_default_language_standard` is asked what the resolved
    compiler's own unpinned default actually is (the remaining case: an
    unpinned C++ parse with no C++20 heuristic, or any MSVC-dialect parse,
    neither of which either command builder pins); otherwise ``None``.

    *lang* is resolved through :func:`_resolve_force_cpp` (the identical
    decision the real header-AST parse makes — never derived from *lang*
    alone, Codex review): an unspecified *lang* can still auto-detect as
    either C or C++ depending on *headers*' own content, and probing the
    wrong language mode would record a dialect the real parse never
    actually used. *resolved_lang_mode* (``"c"``/``"c++"``), when given,
    overrides that re-derivation entirely -- it is the language mode the
    header-AST parse actually settled on (e.g. after a C->C++ self-heal
    retry), which a static re-derivation from *lang*/*headers* alone cannot
    reconstruct (Codex review, fresh evidence).

    *resolved_lang_mode* may also be :data:`_HETEROGENEOUS_LANG_MODE` --
    ``tu_merge.merge_fragments``'s sentinel for "the contributing TUs of a
    merged manifest genuinely disagree on this." That is a stronger signal
    than "unknown": falling through to the static ``_resolve_force_cpp``
    re-derivation below (over the manifest's combined *public/declared*
    headers only) can be confidently *wrong*, not merely uninformed, for a
    TU whose C++-ness came from something invisible to those public
    headers (e.g. a private forced include). So this function returns
    ``None`` immediately for that sentinel, skipping both the forced-
    standard path and the probe path entirely, rather than guessing
    (Codex review, fresh evidence).
    """
    if has_explicit_std(gcc_options, gcc_option_tokens):
        return _extract_explicit_std_value(gcc_options, gcc_option_tokens)
    if headers and _detect_cpp20_headers(headers):
        return "gnu++20"
    if resolved_lang_mode == _HETEROGENEOUS_LANG_MODE:
        return None
    if probe_compiler_bin is None:
        # No resolved-compiler identity to probe *or* to report a forced
        # standard against (the dialect check below needs it too) --
        # preserves this function's exact prior no-``ast_toolchain`` behavior.
        return None
    final_is_cpp = (
        resolved_lang_mode == "c++"
        if resolved_lang_mode is not None
        else _resolve_force_cpp(lang, headers, gcc_options, gcc_option_tokens)
    )
    if not final_is_cpp and abi_dialect != "msvc":
        return _FORCED_C_STANDARD
    return _probe_default_language_standard(
        probe_compiler_bin, "c++" if final_is_cpp else "c"
    )


class _AstCompileProvenance(TypedDict):
    ast_resolved_standard: str | None
    ast_cplusplus_macro: str | None
    ast_compile_args: tuple[str, ...]
    ast_sysroot: str | None


def _ast_compile_provenance(
    headers: list[Path],
    gcc_options: str | None,
    gcc_option_tokens: tuple[str, ...],
    sysroot: Path | None,
    *,
    ast_toolchain: dict[str, str] | None = None,
    lang: str | None = None,
) -> _AstCompileProvenance:
    """Structured compile-context provenance kwargs for an ``AbiSnapshot``
    built from a header-AST parse (schema v15). A single call site shared by
    ``dumper.py``'s ELF/PE/Mach-O snapshot constructors so the four fields
    can never drift between them.

    ``ast_compile_args``/``ast_sysroot`` are redacted via the same
    :class:`~abicheck.buildsource.redaction.RedactionPolicy` every L3
    build-evidence adapter (``compile_db.py``, ``make.py``, ``bazel.py``,
    ``ninja.py``, ``cmake_file_api.py``) already applies before persisting a
    command line or path — a raw ``--compiler-option`` token can
    carry a secret-looking ``-DTOKEN=...`` define or an absolute home-prefixed
    path, and this is the one place such tokens reach a persisted snapshot
    without going through that established convention.

    ``ast_toolchain``/``lang`` (ADR-050 D1/D2 follow-up): the just-resolved
    header-AST toolchain identity (``dumper_toolchain._parser_ast_toolchain``'s
    result — the same dict :func:`_compiler_family_from_toolchain` already
    reads ``compiler_selected``/``selected`` from) and the caller's raw
    ``lang``, forwarded to :func:`_resolve_standard_provenance` so it can
    probe the resolved compiler's own unpinned default standard when no
    explicit one was given — see that function's own docstring. ``None``
    for either (the default) preserves the exact prior behavior: no probe
    attempted, ``ast_resolved_standard`` stays ``None`` for an unpinned dump.
    """
    resolved_standard = _resolve_standard_provenance(
        headers,
        gcc_options,
        gcc_option_tokens,
        probe_compiler_bin=(
            (ast_toolchain or {}).get("compiler_selected")
            or (ast_toolchain or {}).get("selected")
        ),
        lang=lang,
        resolved_lang_mode=(ast_toolchain or {}).get("resolved_lang_mode"),
        abi_dialect=(ast_toolchain or {}).get("abi_dialect"),
    )
    args = _combined_option_tokens(gcc_options, gcc_option_tokens)
    return {
        "ast_resolved_standard": resolved_standard,
        "ast_cplusplus_macro": _cplusplus_macro_for_standard(resolved_standard),
        "ast_compile_args": tuple(DEFAULT_REDACTION.argv(args)),
        "ast_sysroot": DEFAULT_REDACTION.path(str(sysroot)) if sysroot else None,
    }


def _configured_target_triple(
    gcc_options: str | None, gcc_option_tokens: tuple[str, ...], clang_bin: str
) -> str | None:
    """Return the target reported by configured Clang and its pass-through flags."""
    cmd = [
        clang_bin,
        *split_gcc_options(gcc_options or ""),
        *gcc_option_tokens,
        "-print-target-triple",
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, check=False, timeout=10
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("Could not probe effective Clang target: %s", exc)
        return None
    if result.returncode:
        log.warning("Could not probe effective Clang target: %s", result.stderr.strip())
        return None
    target = result.stdout.strip()
    return target or None
