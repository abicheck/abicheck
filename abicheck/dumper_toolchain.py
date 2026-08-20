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
import os
import shutil
import signal
import stat as stat_module
import subprocess
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any, TypedDict

from ._compiler_options import has_explicit_std, split_gcc_options
from .buildsource.redaction import DEFAULT_REDACTION
from .dumper_ast_config_cpp20 import _detect_cpp20_headers


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

    A *probed* default (``standard`` starting with the ``"probed:"`` prefix
    :func:`_probe_default_language_standard` produces — see its own
    docstring) already carries the literal macro assignment it observed, so
    it is read straight out of that string rather than looked up in
    :data:`_CPLUSPLUS_MACRO_BY_EDITION`, which only maps a real ``-std=``
    edition spelling."""
    if not standard:
        return None
    if standard.startswith(_PROBED_STANDARD_PREFIX):
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
    """``gcc_option_tokens`` followed by a shlex-split ``gcc_options`` string
    (snapshot provenance, schema v15) — the one place both forwarded-option
    spellings are merged into a single ordered token list, shared by every
    caller below so the split behavior can't drift between them (see
    :func:`abicheck._compiler_options.split_gcc_options` for its platform
    dispatch on ``os.name``)."""
    tokens = list(gcc_option_tokens)
    if gcc_options:
        tokens.extend(split_gcc_options(gcc_options))
    return tokens


def _extract_explicit_std_value(
    gcc_options: str | None, gcc_option_tokens: tuple[str, ...]
) -> str | None:
    """Pull the literal value out of an explicit ``-std=``/``--std=``/``/std:``
    token, or ``None`` if none is present (snapshot provenance, schema v15)."""
    tokens = _combined_option_tokens(gcc_options, gcc_option_tokens)
    for token in tokens:
        t = token[1:] if token.startswith("--") else token
        if t.startswith("-std="):
            return t[len("-std=") :]
        if t.lower().startswith("/std:"):
            return t[len("/std:") :]
    return None


#: Prefix distinguishing a *probed* default standard (never asserted by the
#: caller — see :func:`_probe_default_language_standard`) from a real,
#: explicit ``-std=``/``/std:`` spelling or the ``force_cpp20`` literal
#: ``"gnu++20"`` -- so a probed value can never collide with, or be mistaken
#: for, a user-given one.
_PROBED_STANDARD_PREFIX = "probed:"


def _probe_language_mode(lang: str | None) -> str:
    """``"c"`` or ``"c++"`` for :func:`_probe_default_language_standard`,
    mirroring the same ``lang == "c"`` convention used throughout this
    module (and ``dumper.py``'s own ``compiler = "cc" if lang == "c" else
    "c++"``) — any other value (``None``, ``"c++"``, ...) means C++."""
    return "c" if (lang or "").strip().lower() == "c" else "c++"


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


def _resolve_standard_provenance(
    headers: list[Path],
    gcc_options: str | None,
    gcc_option_tokens: tuple[str, ...],
    *,
    probe_compiler_bin: str | None = None,
    probe_lang_mode: str | None = None,
) -> str | None:
    """Best-effort resolved C/C++ standard for snapshot provenance (schema
    v15, P1 toolchain-profile audit).

    Mirrors the exact decision ``dumper.py``'s castxml/clang command builders
    already make (``has_explicit_std`` gates the requires/concept heuristic)
    without needing a second return value threaded through them, since it is
    a pure function of the same inputs: an explicit ``-std=``/``--std=``/
    ``/std:`` value is recorded verbatim; otherwise, if
    ``_detect_cpp20_headers`` would force C++20, ``"gnu++20"`` is recorded
    (the exact flag ``force_cpp20`` adds); otherwise, when *probe_compiler_bin*
    is given, :func:`_probe_default_language_standard` is asked what that
    resolved compiler's own unpinned default actually is (best-effort, never
    guessed at from nothing); otherwise ``None``.
    """
    if has_explicit_std(gcc_options, gcc_option_tokens):
        return _extract_explicit_std_value(gcc_options, gcc_option_tokens)
    if headers and _detect_cpp20_headers(headers):
        return "gnu++20"
    if probe_compiler_bin:
        probed = _probe_default_language_standard(
            probe_compiler_bin, probe_lang_mode or "c++"
        )
        if probed is not None:
            return probed
    return None


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
        probe_lang_mode=_probe_language_mode(lang),
    )
    args = _combined_option_tokens(gcc_options, gcc_option_tokens)
    return {
        "ast_resolved_standard": resolved_standard,
        "ast_cplusplus_macro": _cplusplus_macro_for_standard(resolved_standard),
        "ast_compile_args": tuple(DEFAULT_REDACTION.argv(args)),
        "ast_sysroot": DEFAULT_REDACTION.path(str(sysroot)) if sysroot else None,
    }
