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
    try:
        import cxxfilt

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
            except FileNotFoundError:
                _cppfilt_binary_confirmed_missing = True
                break
            except (subprocess.TimeoutExpired, OSError):
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


def _reset_demangle_batch_cache() -> None:
    """Test helper — clear the process-wide cache."""
    global _cppfilt_binary_confirmed_missing  # noqa: PLW0603
    _BATCH_CACHE_OK.clear()
    _BATCH_CACHE_FAIL.clear()
    _cppfilt_binary_confirmed_missing = False


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
_MANGLED_TOKEN_RE = re.compile(r"_{1,2}Z[A-Za-z0-9_$]+(?:\.[A-Za-z0-9_$]+)*")


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


def demangle_text(text: str) -> str:
    """Demangle every Itanium-mangled symbol token embedded in *text*.

    Tokens that are not valid C++ mangled names, or that cannot be demangled
    because no demangler is available, are left unchanged. Intended for
    human-facing report output only — machine formats (JSON/SARIF/JUnit) keep
    the raw mangled symbols so downstream tooling can match on them.

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
        demangled = mapping.get(tok)
        return demangled if demangled and demangled != tok else tok

    return _MANGLED_TOKEN_RE.sub(_repl, text)
