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

"""Versioned-symbol-scheme recogniser (field-eval P08).

Libraries like **ICU** embed the major version in *every* exported symbol name
(``u_strlen_75`` → ``u_strlen_78``). A routine, source-compatible upgrade then
reads as a wall of `func_removed` + `func_added` even though the API barely
changed (16 k changes for ICU 75→78 in the field evaluation).

This recogniser is **advisory and additive** (ADR-028 authority rule): it never
removes or downgrades an artifact-proven break. When most removed function
symbols reappear as added symbols differing *only* by a numeric version token, it
emits a single ``versioned_symbol_scheme_detected`` finding (RISK) that explains
the churn and points at the library's versioning convention. The individual
``func_removed`` / ``func_added`` findings (and the BREAKING verdict they carry)
are left untouched — flipping the verdict is a deliberate, opt-in preset, not
something this heuristic does on its own.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from .checker_policy import ChangeKind
from .demangle import demangle, demangle_batch

if TYPE_CHECKING:
    from .checker_types import Change

#: Collapse every digit run to a placeholder so two names that differ only by a
#: version number share a normalized form (``u_strlen_75`` ~ ``u_strlen_78``).
_DIGITS = re.compile(r"\d+")

#: An inline-namespace **version token** in a *demangled* C++ name: a namespace
#: component ending in ``_<digits>`` immediately before ``::`` — ``icu_75::``,
#: ``lts_20240722::``, libc++ ``__1::``. The mandatory ``_`` before the digits is
#: what separates a real version stamp from a CamelCase class like ``Sha256::``
#: (no underscore), so crypto-style names never false-pair.
_NS_VER = re.compile(r"([A-Za-z_]\w*_\d+)(?=::)")
_TOKEN_STEM = re.compile(r"_\d+$")

#: Don't fire on a couple of coincidental renames — require a real, library-wide
#: pattern: an absolute floor *and* a majority of the removed surface.
_MIN_PAIRS = 3
_MIN_FRACTION = 0.6
#: A C++ inline-namespace token must be carried by this fraction of one side's
#: mangled symbols to count as the library-wide version stamp.
_TOKEN_MAJORITY = 0.5

_REMOVED_KINDS = (ChangeKind.FUNC_REMOVED, ChangeKind.FUNC_REMOVED_ELF_ONLY,
                  ChangeKind.VAR_REMOVED)
_ADDED_KINDS = (ChangeKind.FUNC_ADDED, ChangeKind.VAR_ADDED)


def _normalize(name: str) -> str:
    return _DIGITS.sub("#", name)


def _cpp_key(dem: str, tok: str) -> str:
    """Normalize a demangled name through its inline-namespace version token."""
    return dem.replace(tok, _TOKEN_STEM.sub("_#", tok)) if dem else ""


def _dominant_ns_token(demangled: list[str]) -> str | None:
    """Return the inline-namespace version token (e.g. ``icu_75``) carried by a
    majority of *demangled* names, or ``None`` when no single token dominates."""
    if not demangled:
        return None
    counts: dict[str, int] = {}
    for d in demangled:
        for tok in set(_NS_VER.findall(d)):
            counts[tok] = counts.get(tok, 0) + 1
    if not counts:
        return None
    tok, n = max(counts.items(), key=lambda kv: kv[1])
    return tok if n >= _TOKEN_MAJORITY * len(demangled) else None


def _cpp_tokens(removed: list[Change], added: list[Change]) -> tuple[str, str] | None:
    """Return ``(old_token, new_token)`` for a library-wide C++ inline-namespace
    version stamp (ICU ``icu_75``→``icu_78``), or ``None``. Warms the demangle
    cache for all mangled candidates in one batched call."""
    r_cpp = [c.symbol for c in removed if c.symbol.startswith("_Z")]
    a_cpp = [c.symbol for c in added if c.symbol.startswith("_Z")]
    if len(r_cpp) < _MIN_PAIRS or not a_cpp:
        return None
    demangle_batch([*r_cpp, *a_cpp])  # one batched warm
    r_dem = [d for d in (demangle(s) or "" for s in r_cpp) if d]
    a_dem = [d for d in (demangle(s) or "" for s in a_cpp) if d]
    if not r_dem or not a_dem:
        return None  # no demangler → can't normalize mangled names
    r_tok = _dominant_ns_token(r_dem)
    a_tok = _dominant_ns_token(a_dem)
    if not r_tok or not a_tok or r_tok == a_tok:
        return None
    if _TOKEN_STEM.sub("", r_tok) != _TOKEN_STEM.sub("", a_tok):
        return None  # different stems → not one version bump
    return r_tok, a_tok


def _scheme_key(name: str, tokens: tuple[str, str] | None) -> str | None:
    """Side-agnostic version-normalized key for a symbol, or ``None`` when the
    name is not part of a versioned scheme.

    C names → collapse every digit run. Mangled C++ names → demangle and replace
    whichever inline-namespace token (old or new) appears with the shared stem,
    so ``icu_75::Foo`` and ``icu_78::Foo`` map to the same key.
    """
    if not name.startswith("_Z"):
        return _normalize(name) if _DIGITS.search(name) else None
    if tokens is None:
        return None
    dem = demangle(name) or ""
    if not dem:
        return None
    r_tok, a_tok = tokens
    if r_tok not in dem and a_tok not in dem:
        return None
    return _cpp_key(_cpp_key(dem, r_tok), a_tok)


def analyze_versioned_scheme(changes: list[Change]) -> tuple[Change | None, list[Change]]:
    """Analyze removed/added churn for a versioned-symbol scheme.

    Handles two shapes: the **C-style** suffix (``u_strlen_75``→``u_strlen_78``,
    blunt digit collapse) and the **mangled C++ inline-namespace** stamp
    (``icu_75::``→``icu_78::``, found via demangling + a dominant-token majority).

    Returns ``(advisory, matched)`` where *advisory* is the single
    ``versioned_symbol_scheme_detected`` finding (or ``None``) and *matched* is
    the removed **and** added ``Change`` objects forming the version-rename pairs
    — the inputs the opt-in collapse preset reclassifies as compatible. Pure
    except for an optional batched demangle of mangled candidates.
    """
    from .checker_types import Change

    all_removed = [c for c in changes if c.kind in _REMOVED_KINDS]
    all_added = [c for c in changes if c.kind in _ADDED_KINDS]
    likely_n = sum(1 for c in changes if c.kind is ChangeKind.FUNC_LIKELY_RENAMED)
    # Nothing to work with unless removed↔added pairing or rename findings could
    # plausibly reach the floor.
    if (len(all_removed) < _MIN_PAIRS or not all_added) and likely_n < _MIN_PAIRS:
        return None, []

    tokens = _cpp_tokens(all_removed, all_added)

    # Pair removed↔added (funcs and vars) by their side-agnostic version key.
    added_by_key: dict[str, list[Change]] = {}
    for a in all_added:
        k = _scheme_key(a.symbol, tokens)
        if k is not None:
            added_by_key.setdefault(k, []).append(a)

    matched_removed: list[Change] = []
    matched_added: list[Change] = []
    seen_added: set[int] = set()
    eligible = 0
    for r in all_removed:
        k = _scheme_key(r.symbol, tokens)
        if k is None:
            continue
        eligible += 1
        cands = [a for a in added_by_key.get(k, []) if a.symbol != r.symbol]
        if not cands:
            continue
        matched_removed.append(r)
        for a in cands:
            if id(a) not in seen_added:
                seen_added.add(id(a))
                matched_added.append(a)

    # func_likely_renamed already encodes a pair (old_value→new_value); collapse
    # the ones that are version-renames under the same scheme.
    matched_renamed: list[Change] = []
    for c in changes:
        if c.kind is ChangeKind.FUNC_LIKELY_RENAMED and c.old_value and c.new_value:
            ko = _scheme_key(c.old_value, tokens)
            kn = _scheme_key(c.new_value, tokens)
            if ko is not None and ko == kn and c.old_value != c.new_value:
                matched_renamed.append(c)

    pairs = len(matched_removed) + len(matched_renamed)
    eligible += len(matched_renamed)
    if pairs < _MIN_PAIRS or eligible == 0 or pairs < _MIN_FRACTION * eligible:
        return None, []

    advisory = Change(
        kind=ChangeKind.VERSIONED_SYMBOL_SCHEME_DETECTED,
        symbol="<library>",
        description=(
            f"{pairs} of {eligible} versioned symbols are renamed between releases, "
            "differing only by a version token (versioned-symbol scheme: a C suffix "
            "like ICU 'u_strlen_75'->'_78', or a C++ inline-namespace stamp like "
            "'icu_75::'->'icu_78::'). The large churn is likely a library-wide "
            "rename, not independent API changes."
        ),
        old_value=f"{eligible} versioned symbols",
        new_value=f"{pairs} version-renamed",
    )
    return advisory, matched_removed + matched_added + matched_renamed


def detect_versioned_symbol_scheme(changes: list[Change]) -> Change | None:
    """Return one advisory ``Change`` if the removed/added churn is a versioned
    scheme, else ``None`` (back-compat wrapper over :func:`analyze_versioned_scheme`)."""
    return analyze_versioned_scheme(changes)[0]
