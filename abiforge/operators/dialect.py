"""Edit operators for the ``cxx_dialect`` seed: the same source rebuilt under a newer C++ standard.

Neither edit changes a line of the library's source. Only ``-std=`` changes
(the ``build.cflags`` role, appended after the configuration's own
``-std=``, so the later one wins). Each standard revision changes the type
of a declaration the seed already has, so the exported name changes and an
old consumer's reference to the old name no longer resolves:

- ``P0012R1`` (C++17) makes a ``noexcept`` exception specification part of
  the function type. ``void (*)() noexcept`` mangles as ``PDoFvvE`` instead
  of ``PFvvE``, so ``dl_register`` gets a new name.
- ``P0482R6`` (C++20) makes ``u8"x"[0]`` a ``const char8_t&`` instead of a
  ``const char&``, so ``dl_take``'s parameter type changes (``RKc`` ->
  ``RKDu``).

``dl_keep`` takes an ``int`` under every revision. It is the control: a
report that blames it, or that blames the declaration the *other* operator
changes, attributes the break to the wrong entity. The ground truth is the
Itanium mangling rules for the two papers, measured in
``research/standard-space/cpp.csv`` (GCC 14.2, Linux x86-64).
"""

from __future__ import annotations

from abiforge.model.config import StdRange
from abiforge.model.finding import EntityClaim, EntityRef
from abiforge.model.opkit import Edit, EditOp, Roles, _set
from abiforge.model.seeds import RoleText

__all__ = ["DIALECT_OPERATORS"]

#: What an old consumer of a renamed function sees: its old name is no longer
#: exported. A binary or DWARF dump pairs the two names and reports the
#: parameter change (or the removal). A header dump parses the unchanged
#: header at the configuration's *old* standard, so it still declares the old
#: name on the new side: "declared, no longer exported" (an export lost, which
#: ``FUNC_VISIBILITY_CHANGED`` reports) is the true reading of what it was
#: given. Naming the rename there needs the new build's ``-std=`` (L3 build
#: data), which a header dump does not have.
_RENAMED = frozenset({"FUNC_PARAMS_CHANGED", "FUNC_REMOVED", "FUNC_VISIBILITY_CHANGED"})


def _std(flag: str) -> Edit:
    def edit(r: Roles) -> dict[str, RoleText]:
        return _set(r, "build.cflags", flag)

    return edit


def _renamed(name: str, symbol: str, why: str) -> EntityClaim:
    return EntityClaim(EntityRef(name, symbol), required=True, kinds=_RENAMED, why=why)


def _untouched(name: str, why: str) -> EntityClaim:
    return EntityClaim(EntityRef(name), required=False, why=why)


_KEEP = _untouched("dl_keep", "dl_keep takes an int under every C++ revision")

DIALECT_OPERATORS: tuple[EditOp, ...] = (
    EditOp(
        "dialect_cxx17_noexcept_in_function_type",
        ("dl.gate", "build.cflags"),
        _std("-std=c++17"),
        "the library is rebuilt as C++17 from identical source: noexcept "
        "joins the callback parameter's type (P0012R1), dl_register's "
        "mangled name changes and an old consumer's reference to it is "
        "undefined",
        any_of=(_RENAMED,),
        touches=("build.cflags",),
        standards=(
            StdRange(
                "c++",
                "c++11",
                "ISO/IEC 14882:2011 [except.spec] (noexcept, not part of the type)",
                last="c++14",
                end_cite="P0012R1 (part of the function type from C++17)",
            ),
        ),
        claims=(
            _renamed(
                "dl_register",
                "_Z11dl_registerPFvvE",
                "the C++14 name of dl_register is no longer defined",
            ),
            _untouched("dl_take", "a u8 literal's element is char until C++20"),
            _KEEP,
        ),
    ),
    EditOp(
        "dialect_cxx20_char8_t_parameter",
        ("dl.gate", "build.cflags"),
        _std("-std=c++20"),
        'the library is rebuilt as C++20 from identical source: u8"x"[0] '
        "becomes a const char8_t& (P0482R6), dl_take's mangled name changes "
        "and an old consumer's reference to it is undefined",
        any_of=(_RENAMED,),
        touches=("build.cflags",),
        standards=(
            StdRange(
                "c++",
                "c++17",
                "P0012R1 (C++17: the callback's noexcept is already in its type)",
                last="c++17",
                end_cite="P0482R6 (char8_t from C++20)",
            ),
        ),
        claims=(
            _renamed(
                "dl_take",
                "_Z7dl_takeRKc",
                "the C++17 name of dl_take is no longer defined",
            ),
            _untouched("dl_register", "its type is the same in C++17 and C++20"),
            _KEEP,
        ),
    ),
    EditOp(
        "dialect_rebuild_cxx23_control",
        ("dl.gate", "build.cflags"),
        _std("-std=c++23"),
        "the library is rebuilt as C++23 from identical source on a C++20 or "
        "newer baseline: no declaration's type differs between these "
        "revisions (dl_take is already char8_t, dl_register's noexcept is "
        "already in its type), so every exported name and every consumer "
        "result stays the same",
        verdict_min="NO_CHANGE",
        verdict_max="COMPATIBLE",
        touches=("build.cflags",),
        standards=(
            StdRange(
                "c++",
                "c++20",
                "P0482R6 (char8_t from C++20; nothing these declarations use "
                "changes type after it)",
            ),
        ),
        claims=(
            _untouched("dl_register", "its type is the same in C++20 and later"),
            _untouched("dl_take", "its type is the same in C++20 and later"),
            _KEEP,
        ),
    ),
)
