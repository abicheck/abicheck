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

"""Shared C++ name demangling utilities.

Used by dwarf_snapshot.py (FIX-B) and appcompat.py (FIX-A Part 3) for
cross-format symbol matching.
"""

from __future__ import annotations

import functools
import logging
import re
import subprocess

_log = logging.getLogger(__name__)

# Whether we have already warned about demangling being unavailable.
_warned_no_demangler = False


@functools.lru_cache(maxsize=16384)
def demangle(symbol: str) -> str | None:
    """Demangle a single Itanium C++ symbol. Returns *None* if not C++.

    Tries ``cxxfilt`` (Python binding to ``__cxa_demangle``) first, then
    falls back to the ``c++filt`` command-line tool.
    """
    if not symbol or not symbol.startswith("_Z"):
        return None
    # Reuse a warmed batch cache so a single demangle never re-forks `c++filt`
    # for a name a prior demangle_batch() already resolved (or proved
    # non-demangleable). On large ELF-only C++ libs the rename gate warms this
    # once, turning ~N per-name subprocesses into one batched call (field-eval P11).
    if symbol in _BATCH_CACHE_OK:
        return _BATCH_CACHE_OK[symbol]
    if symbol in _BATCH_CACHE_FAIL:
        return None
    try:
        import cxxfilt
        return str(cxxfilt.demangle(symbol))
    except Exception:  # noqa: BLE001
        _log.debug("cxxfilt demangling failed for %s", symbol)
    try:
        result = subprocess.run(
            ["c++filt", symbol],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            out = result.stdout.strip()
            if out and out != symbol:
                return out
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    global _warned_no_demangler  # noqa: PLW0603
    if not _warned_no_demangler:
        _log.warning(
            "C++ demangling unavailable (no cxxfilt package and no c++filt binary); "
            "DWARF export matching and appcompat symbol matching may be incomplete"
        )
        _warned_no_demangler = True
    return None


# Process-wide cache for demangle_batch. Two dicts so a symbol that
# was passed once and known *not* to be demangleable is not re-queried
# on subsequent calls. Bounded to avoid unbounded growth on long-lived
# servers; the bound is intentionally large because the typical
# working-set is a few thousand symbols per ABI snapshot.
_BATCH_CACHE_OK: dict[str, str] = {}
_BATCH_CACHE_FAIL: set[str] = set()
_BATCH_CACHE_MAX = 65536


def _batch_cache_record_ok(mangled: str, demangled: str) -> None:
    if len(_BATCH_CACHE_OK) >= _BATCH_CACHE_MAX:
        _BATCH_CACHE_OK.clear()
    _BATCH_CACHE_OK[mangled] = demangled


def _batch_cache_record_fail(mangled: str) -> None:
    if len(_BATCH_CACHE_FAIL) >= _BATCH_CACHE_MAX:
        _BATCH_CACHE_FAIL.clear()
    _BATCH_CACHE_FAIL.add(mangled)


def _batch_phase1_cache(cpp_syms: list[str]) -> tuple[dict[str, str], list[str]]:
    """Return (already-resolved, uncached) from the process-wide cache."""
    result: dict[str, str] = {}
    uncached: list[str] = []
    for s in cpp_syms:
        if s in _BATCH_CACHE_OK:
            result[s] = _BATCH_CACHE_OK[s]
        elif s in _BATCH_CACHE_FAIL:
            pass  # known non-demangleable; skip silently
        else:
            uncached.append(s)
    return result, uncached


def _batch_phase2_cxxfilt(uncached: list[str], result: dict[str, str]) -> list[str]:
    """Try in-process cxxfilt for *uncached* symbols; return still-remaining list."""
    remaining: list[str] = []
    try:
        import cxxfilt
        for s in uncached:
            try:
                d = cxxfilt.demangle(s)
                if d and d != s:
                    result[s] = d
                    _batch_cache_record_ok(s, d)
                else:
                    remaining.append(s)
            except Exception:  # noqa: BLE001
                remaining.append(s)
    except Exception:  # noqa: BLE001
        _log.debug("cxxfilt import or initialisation failed; falling back to c++filt")
        remaining = list(uncached)
    return remaining


def _batch_phase3_cppfilt(remaining: list[str], result: dict[str, str]) -> None:
    """Fall back to a single batched ``c++filt`` subprocess call."""
    success_set: set[str] = set()
    cppfilt_succeeded = False
    try:
        proc = subprocess.run(
            ["c++filt"],
            input="\n".join(remaining),
            capture_output=True, text=True, timeout=30,
        )
        if proc.returncode == 0:
            cppfilt_succeeded = True
            lines = proc.stdout.strip().split("\n")
            for mangled, demangled in zip(remaining, lines):
                if demangled and demangled != mangled:
                    result[mangled] = demangled
                    _batch_cache_record_ok(mangled, demangled)
                    success_set.add(mangled)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    # Only cache permanent FAILs when c++filt actually ran to completion
    # (returncode 0). If the binary is missing, timed out, raised OSError,
    # or returned non-zero, leave the symbols un-cached so a future call
    # (e.g. after c++filt becomes available) can retry them.
    if cppfilt_succeeded:
        for s in remaining:
            if s not in success_set:
                _batch_cache_record_fail(s)


def demangle_batch(symbols: list[str]) -> dict[str, str]:
    """Demangle a batch of symbols efficiently using a single ``c++filt`` call.

    Returns a mapping from mangled → demangled for symbols that were
    successfully demangled. Non-C++ symbols are excluded from the result.

    Memoised per-process via module-level caches so that callers which
    repeatedly demangle the same (or overlapping) symbol sets — common
    when several detectors each call ``demangle_batch`` with their own
    slice of a snapshot — do not pay the subprocess cost more than once
    per unique symbol.
    """
    cpp_syms = [s for s in symbols if s and s.startswith("_Z")]
    if not cpp_syms:
        return {}

    # Phase 1 — serve from the process-wide cache (both hit and miss).
    result, uncached = _batch_phase1_cache(cpp_syms)
    if not uncached:
        return result

    # Phase 2 — try cxxfilt (in-process, fastest) for the uncached set.
    remaining = _batch_phase2_cxxfilt(uncached, result)

    # Phase 3 — fall back to a single batched c++filt call.
    if remaining:
        _batch_phase3_cppfilt(remaining, result)

    return result


def _reset_demangle_batch_cache() -> None:
    """Test helper — clear the process-wide cache."""
    _BATCH_CACHE_OK.clear()
    _BATCH_CACHE_FAIL.clear()


def base_name(symbol: str) -> str:
    """Extract the unqualified function name from a symbol (best-effort).

    Known limitations: ``operator<<``, ``operator()``, and templates with
    ``::`` inside angle brackets may be parsed incorrectly. Only used for
    display, not for matching.

    Examples::

        "_ZNK6Widget8getValueEv" → "getValue"
        "Widget::getValue() const" → "getValue"
        "add" → "add"
    """
    demangled = demangle(symbol) or symbol
    paren = demangled.find("(")
    prefix = demangled[:paren] if paren != -1 else demangled
    parts = prefix.rsplit("::", 1)
    return parts[-1].strip()


# Itanium-mangled tokens use only this restricted alphabet, so we can find them
# inside free-form report text (descriptions, additions lists, leaked-symbol
# messages) without disturbing surrounding prose. ``.``-separated suffixes
# (GCC clone markers like ``.cold`` / ``.part.0``) are matched only when
# followed by more name characters, so a trailing sentence period is not eaten.
_MANGLED_TOKEN_RE = re.compile(r"_Z[A-Za-z0-9_$]+(?:\.[A-Za-z0-9_$]+)*")


def demangle_text(text: str) -> str:
    """Demangle every Itanium-mangled symbol token embedded in *text*.

    Tokens that are not valid C++ mangled names, or that cannot be demangled
    because no demangler is available, are left unchanged. Intended for
    human-facing report output only — machine formats (JSON/SARIF/JUnit) keep
    the raw mangled symbols so downstream tooling can match on them.
    """
    tokens = set(_MANGLED_TOKEN_RE.findall(text))
    if not tokens:
        return text
    mapping = demangle_batch(sorted(tokens))

    def _repl(m: re.Match[str]) -> str:
        tok = m.group(0)
        demangled = mapping.get(tok)
        return demangled if demangled and demangled != tok else tok

    return _MANGLED_TOKEN_RE.sub(_repl, text)
