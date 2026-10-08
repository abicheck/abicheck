"""Top-level ``restrict`` in a clang parameter spelling.

Clang's ``qualType`` keeps a top-level ``restrict`` in the parameter's type
spelling; ``restrict`` is not part of the function's type (it is recorded as
``Param.is_restrict``), so the spelling must not carry it -- otherwise a
restrict-only change reads as a BREAKING ``func_params_changed``.
"""

from __future__ import annotations

_RESTRICT_TOKENS = frozenset({"restrict", "__restrict", "__restrict__"})


def without_top_level_restrict(spelling: str) -> str:
    """*spelling* minus a ``restrict`` on its outermost pointer.

    Only the qualifiers after the last top-level ``*`` are touched, so a
    restrict on a pointee (``float *restrict *``) or inside a nested
    function-pointer parameter list stays part of the type.
    """
    depth = 0
    last_star = -1
    for i, ch in enumerate(spelling):
        if ch in "(<[":
            depth += 1
        elif ch in ")>]":
            depth -= 1
        elif ch == "*" and depth == 0:
            last_star = i
    if last_star == -1:
        return spelling
    tail = spelling[last_star + 1 :].split()
    if not tail or any(t not in _RESTRICT_TOKENS | {"const", "volatile"} for t in tail):
        return spelling
    kept = [t for t in tail if t not in _RESTRICT_TOKENS]
    head = spelling[: last_star + 1]
    return head + " ".join(kept)
