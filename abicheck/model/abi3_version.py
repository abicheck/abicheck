# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""ABI3 version-string parsing -- a value, not a classification.

Split out of ``stable_abi.py`` on Codex review of PR #994. That module mixes
two things: parsing an ``abi3``/``cp3x`` version string into a tuple (here),
and *classifying* a symbol against the CPython stable-ABI policy
(``classify``, ``is_private_symbol``, ``is_nonstable_cpython_import`` -- still
there). A first revision classified the whole module ``model`` so a frontend
could reach the parser, which would have made the classification algorithm an
inward model dependency for every future consumer. Only the parser belongs in
``model``; ``stable_abi.py`` re-exports both names, so every existing caller
is unaffected.
"""

from __future__ import annotations

from ..stable_abi_data import STABLE_ABI_SYMBOLS

#: Highest minor the vendored stable-ABI data knows about.
_MAX_KNOWN_MINOR: int = max(minor for _major, minor in STABLE_ABI_SYMBOLS.values())

#: Headroom above the vendored data version for accepting an ``--abi3`` floor. A
#: floor can legitimately target a CPython *newer* than the vendored data (e.g.
#: ``--abi3 3.16`` while the data is 3.15) — such a module simply uses only
#: symbols the data already knows, so it audits cleanly. We accept a generous
#: margin of future minors so real/near-future interpreters are never rejected,
#: while still catching implausible typos (``3.99``, ``3.999``) that would
#: otherwise sort above every vendored symbol and silently suppress all
#: ``ABOVE_FLOOR`` violations. This separates *typo rejection* from the
#: vendored-data ceiling (a refresh raises both automatically).
_MAX_ABI3_MINOR: int = _MAX_KNOWN_MINOR + 10


def parse_abi3_version(text: str) -> tuple[int, int] | None:
    """Parse an ``--abi3`` argument like ``"3.9"`` / ``"3"`` into a tuple.

    Returns ``None`` when *text* is not a valid ``Py_LIMITED_API`` floor. Only
    the documented ``3`` / ``3.x`` forms are accepted — there is no Limited API
    outside the CPython 3 line, so a non-3 major (a mistyped ``39`` for ``3.9``,
    or ``4``) is rejected rather than silently treated as an unreachably-high
    floor that would suppress every ``ABOVE_FLOOR`` violation.

    The bare-major form ``"3"`` is the documented ``Py_LIMITED_API=3`` spelling,
    which CPython treats as the **3.2** Stable-ABI baseline (the Limited API did
    not exist before 3.2). We therefore normalise ``3`` — and any ``3.0``/``3.1``
    — to ``(3, 2)`` so ordinary stable symbols (``PyList_New`` etc., floor 3.2)
    are not wrongly reported as above-floor.

    A floor may target a CPython newer than the vendored data (``--abi3 3.16``
    while the data is 3.15) — that is accepted, since such a module only uses
    symbols the data already knows. Only an *implausible* minor beyond a generous
    future margin (:data:`_MAX_ABI3_MINOR`, e.g. ``3.99``) is rejected: it would
    sort above every vendored symbol, silently suppressing all ``ABOVE_FLOOR``
    violations and letting a CI typo certify a wheel that targets a much lower
    floor.
    """
    parts = text.strip().split(".")
    if len(parts) > 2:
        # Reject `3.9.1` / trailing junk — only `3` or `3.x` are valid floors.
        return None
    try:
        major = int(parts[0])
        minor = int(parts[1]) if len(parts) > 1 else 0
    except (ValueError, IndexError):
        return None
    if major != 3:
        # No Limited API outside the CPython 3 line (rejects `39`, `4`, `2.7`).
        return None
    if minor > _MAX_ABI3_MINOR:
        # Implausible floor (e.g. `3.99`) — a typo, not a real/near-future
        # interpreter. Reject rather than certify against a floor no interpreter
        # provides and the vendored data cannot audit.
        return None
    # Py_LIMITED_API=3 (or 3.0/3.1) → the 3.2 Limited-API baseline.
    minor = max(minor, 2)
    return (major, minor)


def format_version(v: tuple[int, int]) -> str:
    """Render a ``(major, minor)`` version tuple as ``"3.9"``."""
    return f"{v[0]}.{v[1]}"
