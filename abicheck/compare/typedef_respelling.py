"""Typedef respellings a stale Itanium mangled name vouches for.

A parameter respelled from a typedef to the canonical type it names (or back)
under an unchanged ``_Z`` key is the same type: the mangled name encodes the
canonical type, so header spellings alone must not report it as
``FUNC_PARAMS_CHANGED`` (catalog case95). Owned here so
``function_signature`` keeps one responsibility.
"""

from __future__ import annotations

_BUILTIN_TYPE_WORDS = frozenset(
    {
        "void",
        "bool",
        "char",
        "wchar_t",
        "char8_t",
        "char16_t",
        "char32_t",
        "short",
        "int",
        "long",
        "signed",
        "unsigned",
        "float",
        "double",
        "__int128",
        "_Bool",
        "auto",
    }
)


def _core_leaf(spelling: str) -> str | None:
    """The unqualified type name *spelling* names once cv and declarator
    sigils are stripped, or None when it is not a single name."""
    core = spelling.replace("*", " ").replace("&", " ").split()
    core = [t for t in core if t not in ("const", "volatile")]
    if len(core) != 1:
        return None
    return core[0].rsplit("::", 1)[-1]


def typedef_like(spelling: str) -> bool:
    """Whether *spelling* names a (possibly qualified) user type -- a typedef
    candidate -- rather than a builtin, once cv and declarator sigils are
    stripped."""
    leaf = _core_leaf(spelling)
    return leaf is not None and leaf.isidentifier() and leaf not in _BUILTIN_TYPE_WORDS


def _respells(typedef_side: str, encoded: str) -> bool:
    """Whether *typedef_side* can be another name for *encoded*: a user type
    name that is not the encoded type's own name (``Cfg`` for an encoded
    ``Cfg*`` is a pointer-level change, not a respelling)."""
    return typedef_like(typedef_side) and _core_leaf(typedef_side) != _core_leaf(
        encoded
    )


def _mangled_param_types(mangled: str) -> tuple[str, ...] | None:
    """The parameter types an Itanium mangled name encodes (canonical,
    scope-unqualified), or ``None`` for a name that encodes none."""
    from ..demangle import demangle_one_batched
    from ..model.special_member_identity import demangled_params

    text = demangle_one_batched(mangled)
    return demangled_params(text) if text else None


def _encoded_form(spelling: str) -> str:
    """*spelling* in the comparison form ``demangled_params`` yields."""
    from ..model.special_member_identity import demangled_params

    return (demangled_params(f"f({spelling})") or ("",))[0]


def respelled_typedef_positions(
    mangled: str, o_types: tuple[str, ...], n_types: tuple[str, ...]
) -> frozenset[int]:
    """Positions whose spelling changed only by naming a typedef differently.

    An Itanium mangled name encodes each parameter's *canonical* type, so for
    a pair matched under the same ``_Z`` key a position where one side spells
    exactly the encoded type and the other spells a typedef-like name is the
    same type, respelled -- the common case being a typedef replaced by what
    it names: ``size_type n`` -> ``std::size_t n`` (catalog case95), which
    header spellings alone would otherwise report as FUNC_PARAMS_CHANGED, a
    BREAKING calling-convention claim for a change the binary does not even
    contain. Only positions the mangled name itself vouches for are
    excused; ``extern "C"`` names encode no parameters and excuse nothing.
    """
    encoded = _mangled_param_types(mangled)
    if encoded is None or not (len(encoded) == len(o_types) == len(n_types)):
        return frozenset()
    explained = set()
    for i, (enc, a, b) in enumerate(zip(encoded, o_types, n_types)):
        if _encoded_form(a) == enc and _respells(b, enc):
            explained.add(i)
        elif _encoded_form(b) == enc and _respells(a, enc):
            explained.add(i)
    return frozenset(explained)


def all_respelled(
    mangled: str,
    o_types: tuple[str, ...],
    n_types: tuple[str, ...],
    differing: list[int],
) -> bool:
    """Whether every *differing* position is a typedef respelling."""
    # Demangling costs a c++filt spawn on a cache miss: only ask the mangled
    # name when every differing position is a candidate (a side spells a
    # user type name).
    return all(
        typedef_like(o_types[i]) or typedef_like(n_types[i]) for i in differing
    ) and set(differing) <= respelled_typedef_positions(mangled, o_types, n_types)
