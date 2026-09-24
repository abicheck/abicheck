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

Itanium-mangled names only, in either the plain ELF ``_Z...`` spelling or
the Mach-O ``__Z...`` spelling clang's own ``mangledName`` carries on macOS
(see ``_canonical_mangled`` below) -- MSVC-decorated names (``?run@Foo@@
QEAAXXZ``) are never demangled anywhere in this module (Codex review on
PR #874, fresh evidence): both ``cxxfilt`` (a binding to libstdc++'s
``__cxa_demangle``) and the GNU ``c++filt`` fallback support only the
Itanium ABI's mangling grammar -- confirmed against real ``c++filt``
(GNU Binutils), whose own ``-s {none,auto,gnu-v3,java,gnat,dlang,rust}``
format list has no MSVC/Microsoft entry at all. There is no equivalent
lightweight, cross-platform tool this codebase already depends on: real
MSVC demangling needs either the Windows-only ``undname``/``dbghelp.dll``
(part of the MSVC toolchain, not installable standalone on Linux/macOS CI)
or a third-party pure-Python MSVC demangler package -- a new runtime
dependency this deliberately lightweight tool has no other reason to carry
(see AGENTS.md's "Don't add dependencies without strong justification").
Consequence: a PE/COFF report (HTML or otherwise) for an MSVC-built C++
library shows every exported symbol in its raw decorated form regardless
of ``--demangle``/the HTML default -- confirmed by reading
``pdb_parser.py``/``pdb_metadata.py`` too: the PDB pipeline extracts
already-demangled *type*/struct/field names straight from CodeView debug
records (those are never mangled to begin with), but nothing in this
codebase demangles a PE export table's own decorated *symbol* names. Not
attempted here.
"""

from __future__ import annotations

import functools
import logging
import re
import subprocess
from typing import Any

_log = logging.getLogger(__name__)

# Whether we have already warned about demangling being unavailable.
_warned_no_demangler = False

# Set once a subprocess.run() call proves the `c++filt` binary itself isn't
# installed (FileNotFoundError). Unlike a timeout or a non-zero exit -- both
# of which the existing FAIL-caching comment below deliberately treats as
# possibly transient/input-specific and worth retrying -- a missing binary
# won't reappear mid-process, so there is no reason to keep re-attempting the
# same doomed subprocess launch for every subsequent demangle()/demangle_
# batch() call in this process's lifetime (Codex review, fresh evidence: a
# large HTML report with no demangler installed re-launched a fresh, doomed
# subprocess pair per row instead of degrading once).
_cppfilt_binary_confirmed_missing = False

# Set once an `import cxxfilt` inside demangle() raises ImportError -- proof
# the package itself isn't installed, as distinct from the package being
# installed but failing (or declining) to demangle one particular symbol.
# Only *this* flag (or `_cxxfilt_import_confirmed_broken` below) combined with
# `_cppfilt_binary_confirmed_missing` means "no demangler available at all";
# either backend being merely unable to handle one malformed/foreign-ABI
# symbol is the normal, expected outcome for plenty of real symbols and must
# never be conflated with both tools being absent (the bug this flag exists
# to fix: the user-facing "no cxxfilt package and no c++filt binary" warning
# used to fire on ANY single-symbol demangle failure, including with a fully
# working c++filt installed).
_cxxfilt_import_confirmed_missing = False

# Set once `import cxxfilt` raises something other than ImportError (e.g. an
# OSError/RuntimeError from a broken native dependency at module-init time)
# -- the package is *installed* but not usable, which is a different fact
# from `_cxxfilt_import_confirmed_missing` above and must be worded
# differently in the warning below (Codex review, fresh evidence: the
# broad `except Exception` needed to preserve the c++filt fallback for this
# case -- see `demangle()` -- must not also make the "no cxxfilt package"
# wording fire for a package that is, in fact, installed).
_cxxfilt_import_confirmed_broken = False


def _is_itanium_mangled(symbol: str, *, accept_macho_prefix: bool = False) -> bool:
    """True for a plain ELF ``_Z...`` name, or its Mach-O ``__Z...`` spelling
    when *accept_macho_prefix* is set.

    clang's own ``mangledName`` carries the platform global-symbol prefix on
    macOS (``__ZN3lib3addEii``, see ``dumper_clang.py``'s ``_visibility``
    docstring for the same quirk handled on the symbol-matching side), so a
    Mach-O ``Function.mangled``/``Change.symbol`` can carry either spelling.

    ``accept_macho_prefix`` defaults *off*: unlike the unambiguous single-
    underscore ``_Z...`` form, a bare ``__Z...`` string is only *probably*
    Mach-O-prefixed Itanium mangling -- a literal ELF export coincidentally
    named that way (e.g. a hand-written assembler alias) is possible, and
    this module is called for correctness-critical symbol matching
    (``debian_symbols.py``'s Debian `.symbols` file generation,
    ``dwarf_snapshot.py``, ``appcompat.py``) as well as for pure display
    (Codex review, fresh evidence). Only the report-rendering entry points
    (:func:`demangle_text`/:func:`prewarm_demangle_batch`, used solely by
    the HTML/Markdown reporters) opt in -- every other caller keeps the
    strict, unambiguous pre-Mach-O-fix behavior."""
    if symbol.startswith("_Z"):
        return True
    return accept_macho_prefix and symbol.startswith("__Z")


def _canonical_mangled(symbol: str) -> str:
    """Strip the Mach-O global-symbol prefix, if present, to the plain
    Itanium ``_Z...`` spelling cxxfilt/c++filt actually expect -- neither
    backend recognizes the doubled-underscore form (Codex review, fresh
    evidence: matching a ``__Z...`` token whole and demangling it unstripped
    fails silently, and matching only its ``_Z...`` suffix instead leaves
    the extra leading underscore glued onto the demangled result, e.g.
    ``_Foo::bar()`` instead of ``Foo::bar()``)."""
    return symbol[1:] if symbol.startswith("__Z") else symbol


@functools.lru_cache(maxsize=16384)
def demangle(symbol: str, *, accept_macho_prefix: bool = False) -> str | None:
    """Demangle a single Itanium C++ symbol. Returns *None* if not C++.

    Tries ``cxxfilt`` (Python binding to ``__cxa_demangle``) first, then
    falls back to the ``c++filt`` command-line tool. Accepts the plain ELF
    ``_Z...`` spelling always; the Mach-O ``__Z...`` spelling (see
    :func:`_canonical_mangled`) only when *accept_macho_prefix* is set --
    see :func:`_is_itanium_mangled`'s docstring for why that isn't the
    default. This gate runs before any cache lookup, so a symbol a
    permissive caller already cached OK/FAIL is never surfaced to a
    stricter caller that wouldn't itself have accepted it.
    """
    if not symbol or not _is_itanium_mangled(
        symbol, accept_macho_prefix=accept_macho_prefix
    ):
        return None
    # Reuse a warmed batch cache so a single demangle never re-forks `c++filt`
    # for a name a prior demangle_batch() already resolved (or proved
    # non-demangleable). On large ELF-only C++ libs the rename gate warms this
    # once, turning ~N per-name subprocesses into one batched call (field-eval P11).
    if symbol in _BATCH_CACHE_OK:
        return _BATCH_CACHE_OK[symbol]
    if symbol in _BATCH_CACHE_FAIL:
        return None
    canonical = _canonical_mangled(symbol)
    global _cxxfilt_import_confirmed_missing  # noqa: PLW0603
    global _cxxfilt_import_confirmed_broken  # noqa: PLW0603
    try:
        import cxxfilt
    except ModuleNotFoundError as exc:
        # A bare `ImportError` is not proof that `cxxfilt` itself is the
        # missing module: cxxfilt could be installed and importable, but
        # itself `import` a dependency that isn't -- that also raises
        # `ImportError` (its `ModuleNotFoundError` subclass, specifically),
        # naming the *dependency*, not `cxxfilt`, in `exc.name` (Codex
        # review, fresh evidence, third round). Only a `ModuleNotFoundError`
        # whose `.name` actually is "cxxfilt" proves the package itself is
        # absent; anything else means cxxfilt is installed but broken.
        if exc.name == "cxxfilt":
            _cxxfilt_import_confirmed_missing = True
        else:
            _cxxfilt_import_confirmed_broken = True
    except Exception:  # noqa: BLE001
        # Not narrowed to ImportError alone: an installed cxxfilt module can
        # also fail to *import* for a reason other than "package not
        # installed" (e.g. an OSError/RuntimeError from a broken native
        # dependency at module-init time) -- the prior implementation caught
        # all import-time exceptions here and fell through to the c++filt
        # fallback, and `_batch_phase2_cxxfilt()` still does the same (Codex
        # review, fresh evidence: narrowing this to ImportError let such an
        # exception escape uncaught, aborting demangle() even when c++filt
        # itself works fine). Recorded separately from
        # `_cxxfilt_import_confirmed_missing`: the package IS installed here,
        # just broken, and the warning below must say so accurately rather
        # than falsely claiming "no cxxfilt package" (Codex review, fresh
        # evidence, second round).
        _cxxfilt_import_confirmed_broken = True
    else:
        try:
            out = str(cxxfilt.demangle(canonical))
            # Some cxxfilt/__cxa_demangle versions return the input unchanged
            # on failure rather than raising -- for a malformed `__Z...` token
            # that echo is the canonical single-underscore form, which must be
            # compared against `canonical`, not treated as a real demangling
            # (Codex review, fresh evidence -- the batch cxxfilt path already
            # guards this identically).
            if out != canonical:
                return out
        except Exception:  # noqa: BLE001
            # cxxfilt is installed and imported fine; it just couldn't
            # demangle *this* symbol (malformed/foreign-ABI mangled name).
            # That is the normal, expected outcome for plenty of real
            # symbols -- log at debug level only, never the user-facing
            # "demangler unavailable" warning below, which is reserved for
            # both backends being confirmed absent.
            _log.debug("cxxfilt demangling failed for %s", symbol)
    global _cppfilt_binary_confirmed_missing  # noqa: PLW0603
    if not _cppfilt_binary_confirmed_missing:
        for cmd in _cppfilt_single_commands(canonical):
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if result.returncode == 0:
                    out = result.stdout.strip()
                    # Compare against the canonical input, not the original
                    # (possibly Mach-O-prefixed) symbol -- c++filt echoes back
                    # exactly what it was fed on failure, so for a malformed
                    # `__Z...` token that echo is the canonical single-
                    # underscore form, which never equals `symbol` and would
                    # be misread as a real demangling (Codex review).
                    if out and out != canonical:
                        return out
                    _log.debug(
                        "c++filt echoed %s back unchanged (or empty) for %s",
                        cmd[0],
                        symbol,
                    )
                else:
                    _log.debug(
                        "c++filt exited %d demangling %s", result.returncode, symbol
                    )
            except FileNotFoundError:
                _cppfilt_binary_confirmed_missing = True
                break
            except (subprocess.TimeoutExpired, OSError) as exc:
                _log.debug("c++filt failed demangling %s: %s", symbol, exc)

    # Only warn "demangler unavailable" when BOTH backends are confirmed
    # unusable in this environment. A working c++filt/cxxfilt that simply
    # couldn't demangle this one symbol (malformed input, a non-Itanium
    # mangled-looking token, a foreign ABI) is not "unavailable" and must
    # stay silent here -- the bug this fixes: the warning used to fire on
    # ANY per-symbol demangle failure regardless of tool presence, so a
    # report where c++filt genuinely worked for other symbols could still
    # falsely claim "no cxxfilt package and no c++filt binary". The wording
    # itself must also stay accurate to *which* cxxfilt fact is true (Codex
    # review, fresh evidence, second round): "no cxxfilt package" only when
    # the import genuinely failed with ImportError, never when the package
    # is installed but broken (`_cxxfilt_import_confirmed_broken`) --
    # claiming a broken package is a missing one is itself a false
    # diagnostic, just a different one from the bug this function fixes.
    cxxfilt_confirmed_unusable = (
        _cxxfilt_import_confirmed_missing or _cxxfilt_import_confirmed_broken
    )
    if cxxfilt_confirmed_unusable and _cppfilt_binary_confirmed_missing:
        global _warned_no_demangler  # noqa: PLW0603
        if not _warned_no_demangler:
            cxxfilt_state = (
                "no cxxfilt package"
                if _cxxfilt_import_confirmed_missing
                else "cxxfilt failed to initialize"
            )
            _log.warning(
                "C++ demangling unavailable (%s and no c++filt binary); "
                "DWARF export matching and appcompat symbol matching may be "
                "incomplete",
                cxxfilt_state,
            )
            _warned_no_demangler = True
    return None


# Process-wide cache for demangle_batch. Two mappings so a symbol that
# was passed once and known *not* to be demangleable is not re-queried
# on subsequent calls. Bounded to avoid unbounded growth on long-lived
# servers; the bound is intentionally large because the typical
# working-set is a few thousand symbols per ABI snapshot.
#
# ``_BATCH_CACHE_FAIL`` is a ``dict`` used as an ordered set (the values are
# always ``None``) rather than a ``set``: eviction below needs a *stable
# oldest entry*, which a set cannot offer. Membership tests read the same
# either way, which is all any caller does with it.
_BATCH_CACHE_OK: dict[str, str] = {}
_BATCH_CACHE_FAIL: dict[str, None] = {}
_BATCH_CACHE_MAX = 65536


def _evict_oldest(cache: dict[str, Any]) -> None:
    """Make room for one entry by dropping the oldest, never by clearing.

    Both caches previously cleared themselves wholesale on reaching the
    bound, so recording one symbol at the limit discarded 65,536 resolved
    names (verified: 65,536 successes, insert one more, one entry left).
    A demangling working set larger than the bound therefore didn't
    degrade -- it fell off a cliff and re-forked ``c++filt`` for the whole
    set, repeatedly. Dropping a single insertion-oldest entry keeps the
    cache full and makes the steady state FIFO, which is the right
    approximation here: a comparison walks a snapshot's symbols roughly
    once, so recency predicts reuse better than nothing and an exact LRU
    would cost a reordering on every hit for no measured gain.
    """
    for oldest in cache:
        del cache[oldest]
        return


def _batch_cache_record_ok(mangled: str, demangled: str) -> None:
    if mangled not in _BATCH_CACHE_OK and len(_BATCH_CACHE_OK) >= _BATCH_CACHE_MAX:
        _evict_oldest(_BATCH_CACHE_OK)
    _BATCH_CACHE_OK[mangled] = demangled


def _batch_cache_record_fail(mangled: str) -> None:
    if mangled not in _BATCH_CACHE_FAIL and len(_BATCH_CACHE_FAIL) >= _BATCH_CACHE_MAX:
        _evict_oldest(_BATCH_CACHE_FAIL)
    _BATCH_CACHE_FAIL[mangled] = None


def _batch_phase1_cache(cpp_syms: list[str]) -> tuple[dict[str, str], list[str]]:
    """Return (already-resolved, uncached) from the process-wide cache.

    The *uncached* list is **deduplicated**, first-occurrence order kept.
    Without that, a caller passing one symbol N times in a single call paid
    for it N times: phase 2 loops over this list and does not re-consult
    the cache it just populated, and phase 3 feeds it straight to
    ``c++filt``'s stdin. Measured on a real 1,500-export DSO whose names
    were passed 24 times in one batch: 36,000 names submitted to
    ``c++filt`` instead of 1,500, 0.0794s vs 0.0116s, identical output
    mapping. Deduplicating here rather than in ``demangle_batch`` keeps it
    in the one place that already decides what still needs resolving, so
    every phase downstream inherits it.
    """
    result: dict[str, str] = {}
    uncached: list[str] = []
    seen: set[str] = set()
    for s in cpp_syms:
        if s in _BATCH_CACHE_OK:
            result[s] = _BATCH_CACHE_OK[s]
        elif s in _BATCH_CACHE_FAIL:
            pass  # known non-demangleable; skip silently
        elif s not in seen:
            seen.add(s)
            uncached.append(s)
    return result, uncached


def _batch_phase2_cxxfilt(uncached: list[str], result: dict[str, str]) -> list[str]:
    """Try in-process cxxfilt for *uncached* symbols; return still-remaining list."""
    remaining: list[str] = []
    try:
        import cxxfilt

        for s in uncached:
            try:
                canonical = _canonical_mangled(s)
                d = cxxfilt.demangle(canonical)
                # Compare against the canonical input, not the original
                # (possibly Mach-O-prefixed) symbol -- some cxxfilt/
                # __cxa_demangle versions return the input unchanged on
                # failure rather than raising, and for a `__Z...` token
                # that echo is the canonical single-underscore form,
                # which never equals `s` and would be misread as a real
                # demangling (Codex review).
                if d and d != canonical:
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


def _cppfilt_single_commands(symbol: str) -> tuple[list[str], ...]:
    return (["c++filt", symbol], ["c++filt", "--no-strip-underscore", symbol])


def _cppfilt_batch_commands() -> tuple[list[str], ...]:
    return (["c++filt"], ["c++filt", "--no-strip-underscore"])


def _batch_phase3_cppfilt(remaining: list[str], result: dict[str, str]) -> None:
    """Fall back to a single batched ``c++filt`` subprocess call."""
    global _cppfilt_binary_confirmed_missing  # noqa: PLW0603
    unresolved = list(remaining)
    any_cppfilt_succeeded = False
    success_set: set[str] = set()
    if not _cppfilt_binary_confirmed_missing:
        for cmd in _cppfilt_batch_commands():
            if not unresolved:
                break
            success_set = set()
            canonical_inputs = [_canonical_mangled(s) for s in unresolved]
            try:
                proc = subprocess.run(
                    cmd,
                    input="\n".join(canonical_inputs),
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if proc.returncode != 0:
                    continue
                any_cppfilt_succeeded = True
                lines = proc.stdout.strip().split("\n")
                for mangled, canonical, demangled in zip(
                    unresolved, canonical_inputs, lines
                ):
                    # Compare against the *canonical* input, not the original
                    # (possibly Mach-O-prefixed) one -- c++filt echoes back
                    # exactly what it was fed on failure, so for a malformed
                    # `__Z...` token that echo is the canonical single-
                    # underscore form, which never equals the double-underscore
                    # `mangled` key and would be misread as a real demangling
                    # (Codex review, fresh evidence).
                    if demangled and demangled != canonical:
                        result[mangled] = demangled
                        _batch_cache_record_ok(mangled, demangled)
                        success_set.add(mangled)
            except FileNotFoundError:
                _cppfilt_binary_confirmed_missing = True
                break
            except (subprocess.TimeoutExpired, OSError):
                continue
            unresolved = [s for s in unresolved if s not in success_set]
    # Only cache permanent FAILs when c++filt actually ran to completion
    # (returncode 0). If the binary is missing, timed out, raised OSError,
    # or returned non-zero, leave the symbols un-cached so a future call
    # (e.g. after c++filt becomes available) can retry them.
    if any_cppfilt_succeeded:
        for s in unresolved:
            if s not in success_set:
                _batch_cache_record_fail(s)


def demangle_batch(
    symbols: list[str], *, accept_macho_prefix: bool = False
) -> dict[str, str]:
    """Demangle a batch of symbols efficiently using a single ``c++filt`` call.

    Returns a mapping from mangled → demangled for symbols that were
    successfully demangled. Non-C++ symbols are excluded from the result.
    ``accept_macho_prefix`` mirrors :func:`demangle`'s -- see
    :func:`_is_itanium_mangled`'s docstring for why it defaults off, and why
    gating it here, before any cache lookup, is what keeps a permissive
    caller's cached result from leaking into a stricter caller's answer.

    Memoised per-process via module-level caches so that callers which
    repeatedly demangle the same (or overlapping) symbol sets — common
    when several detectors each call ``demangle_batch`` with their own
    slice of a snapshot — do not pay the subprocess cost more than once
    per unique symbol.
    """
    cpp_syms = [
        s
        for s in symbols
        if s and _is_itanium_mangled(s, accept_macho_prefix=accept_macho_prefix)
    ]
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


def demangle_one_batched(symbol: str) -> str | None:
    """``demangle_batch([symbol]).get(symbol)``, without the batch overhead
    when the answer is already cached.

    For a caller that asks one name at a time after the names were batched
    up front (``compare.template_surface.qualified_declaration_name`` made
    ~225k single-name ``demangle_batch`` calls over ~37k distinct names).
    Same gate first (a Mach-O ``__Z`` spelling is never answered from a
    permissive caller's entry), same cache, and the cache has no recency to
    update on a hit, so the answer is identical by construction.
    """
    if not symbol or not _is_itanium_mangled(symbol):
        return None
    hit = _BATCH_CACHE_OK.get(symbol)
    if hit is not None:
        return hit
    if symbol in _BATCH_CACHE_FAIL:
        return None
    return demangle_batch([symbol]).get(symbol)


def _reset_demangle_batch_cache() -> None:
    """Test helper — clear the process-wide cache."""
    global _cppfilt_binary_confirmed_missing  # noqa: PLW0603
    global _cxxfilt_import_confirmed_missing  # noqa: PLW0603
    global _cxxfilt_import_confirmed_broken  # noqa: PLW0603
    global _warned_no_demangler  # noqa: PLW0603
    _BATCH_CACHE_OK.clear()
    _BATCH_CACHE_FAIL.clear()
    _cppfilt_binary_confirmed_missing = False
    _cxxfilt_import_confirmed_missing = False
    _cxxfilt_import_confirmed_broken = False
    _warned_no_demangler = False


def strip_signature(demangled: str) -> str:
    """Strip a demangled C++ signature down to its qualified name.

    ``"ns::detail::api(int) const"`` → ``"ns::detail::api"``. Pure string
    operation on an already-demangled string (no subprocess call) — keeps
    the full namespace/class qualification, unlike :func:`base_name`, which
    also peels off everything but the leaf segment for display.
    """
    paren = demangled.find("(")
    return (demangled[:paren] if paren != -1 else demangled).strip()


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
# ``_{1,2}Z`` (not just ``_Z``) so the whole Mach-O ``__Z...`` token is
# captured and replaced as one span -- matching only its ``_Z...`` suffix
# left the extra leading underscore glued onto the demangled text (Codex
# review, fresh evidence: ``__ZN3Foo3barEv`` rendered as ``_Foo::bar()``).
# The leading `(?<!...)` is a *left* token boundary, and it is load-bearing
# rather than defensive: without it the scan happily starts mid-identifier, so
# a legitimate C or assembler export that merely contains a mangled-looking
# suffix -- `my_Z3foov` -- matched `_Z3foov` inside itself and rendered as
# `myfoo() [_Z3foov]`, with the real symbol `my_Z3foov` appearing nowhere in
# the output and unrecoverable from it (Codex review, PR #1284; reproduced
# directly). This slice retires `--view no-demangle` and makes every human
# format demangle, which is what turned a latent corruption into an
# unavoidable one and makes it this PR's to fix. The right edge needs no such
# guard: `[A-Za-z0-9_$]+` is greedy, so a trailing run is swallowed into the
# token and simply fails to demangle, leaving the text untouched.
_MANGLED_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_$.])_{1,2}Z[A-Za-z0-9_$]+(?:\.[A-Za-z0-9_$]+)*"
)


def extract_mangled_tokens(text: str) -> set[str]:
    """Return every Itanium-mangled symbol token embedded in *text*."""
    return set(_MANGLED_TOKEN_RE.findall(text))


def prewarm_demangle_batch(
    objs: list[object], attrs: tuple[str, ...] = ("symbol", "description")
) -> None:
    """Pre-warm :func:`demangle_batch`'s process-wide cache from *attrs* of
    every object in *objs* (e.g. one HTML report's whole change list).

    Without this, a caller rendering many rows one at a time via
    :func:`demangle_text` pays a fresh ``c++filt`` subprocess per row once
    the fast in-process ``cxxfilt`` package isn't installed; one upfront
    batched call here makes every later per-row call a pure cache hit.

    Used only by report-rendering callers (``html_report.py``/
    ``appcompat_html.py``), so it warms with ``accept_macho_prefix=True`` --
    matching :func:`demangle_text`'s own default; see
    :func:`_is_itanium_mangled`'s docstring for why that default doesn't
    extend to :func:`demangle`/:func:`demangle_batch` themselves.
    """
    tokens: set[str] = set()
    for obj in objs:
        for attr in attrs:
            tokens |= extract_mangled_tokens(str(getattr(obj, attr, "") or ""))
    if tokens:
        demangle_batch(sorted(tokens), accept_macho_prefix=True)


def prewarm_demangle_from_json_value(value: object) -> None:
    """Pre-warm :func:`demangle_batch`'s process-wide cache by scanning
    every string reachable inside a JSON-shaped *value* -- a dict/list/tuple
    tree of scalars, e.g. a :class:`~abicheck.report.document.ReportDocument`'s
    ``to_mapping()`` -- for embedded mangled tokens.

    :func:`prewarm_demangle_batch` needs typed objects with named attributes
    (``obj.symbol``, ``obj.description``); a caller holding only a document's
    already-JSON-shaped mapping has no such objects, and re-typing every
    field the document happens to carry would drift out of sync with the
    document's own schema as it grows. Walking the tree instead stays
    correct by construction: any string field a document adds later is
    covered automatically, with no second list of attribute names to keep in
    sync (Codex review: ``render_html_document`` -- the first
    ``ReportDocument`` projection whose whole-document render entry point
    can run standalone, with no compute-side prewarm ever having run in this
    process -- rendered a 1,000-row document via ``c++filt`` subprocess per
    row instead of one batched call).
    """
    tokens: set[str] = set()

    def walk(v: object) -> None:
        if isinstance(v, str):
            tokens.update(extract_mangled_tokens(v))
        elif isinstance(v, dict):
            for item in v.values():
                walk(item)
        elif isinstance(v, (list, tuple)):
            for item in v:
                walk(item)

    walk(value)
    if tokens:
        demangle_batch(sorted(tokens), accept_macho_prefix=True)


def demangle_text(text: str, *, escape_table_pipes: bool = False) -> str:
    """Demangle every Itanium-mangled symbol token embedded in *text*.

    Tokens that are not valid C++ mangled names, or that cannot be demangled
    because no demangler is available, are left unchanged. Intended for
    human-facing report output only — machine formats (JSON/SARIF/JUnit) keep
    the raw mangled symbols so downstream tooling can match on them (and,
    since plan slice 7o, carry the demangled name too, in a separate
    ``demangled_symbol`` field).

    **Every successfully demangled token keeps its exact mangled spelling**,
    appended in brackets: ``_ZN3Foo3barEi`` renders as
    ``Foo::bar(int) [_ZN3Foo3barEi]``. That is what let slice 7o retire the
    ``--view demangle``/``no-demangle`` decision entirely: the only reason
    to ask for the mangled form was that demangling *replaced* it, leaving
    nothing to paste into ``nm``/``objdump``, a suppression rule's selector,
    or a bug report. Both names are now always present, so human output
    demangles automatically for every human format and nothing is lost.

    Resolves a Mach-O ``__Z...`` token (``accept_macho_prefix=True``) since
    this function has no other, correctness-critical caller to put at risk
    of misreading a coincidentally-``__Z``-prefixed literal ELF symbol --
    see :func:`_is_itanium_mangled`'s docstring.
    """
    tokens = extract_mangled_tokens(text)
    if not tokens:
        return text
    mapping = demangle_batch(sorted(tokens), accept_macho_prefix=True)

    def _repl(m: re.Match[str]) -> str:
        tok = m.group(0)
        # Idempotence: this function annotates as `name [tok]`, so a token
        # already sitting inside those brackets has been annotated already and
        # must be left alone. Without this a second pass produced
        # `bar() [bar() [_Z3barv]]` -- which is exactly what happened when a
        # renderer's own `demangle` default was flipped while the CLI still
        # demangled at its own boundary (Codex review, PR #1284). Cheap, and
        # it makes the double application harmless rather than merely
        # unlikely.
        start, end = m.start(), m.end()
        if (
            start > 0
            and text[start - 1] == "["
            and end < len(text)
            and text[end] == "]"
        ):
            return tok
        demangled = mapping.get(tok)
        if not demangled or demangled == tok:
            return tok
        if escape_table_pipes:
            # A demangled name can *contain* a pipe -- `_ZN3FooorERKS_` is
            # `Foo::operator|(Foo const&)` -- and this substitution runs over
            # an already-rendered document, so a caller that escaped its cells
            # beforehand cannot have escaped a delimiter this pass introduces
            # (CodeRabbit review, PR #1284). Escaping here is the only point
            # that can: GFM renders `\|` as a literal pipe, so a bullet or
            # paragraph reads identically while a table row keeps its columns.
            demangled = demangled.replace("|", "\\|")
        return f"{demangled} [{tok}]"

    return _MANGLED_TOKEN_RE.sub(_repl, text)
