"""Entity-attributed layout edits: one record mutation, judged per use of the type.

The same mutation -- ``struct S`` gains a field between its two members, so
its size grows and ``b`` moves -- is a different ABI fact depending on how the
public contract exposes ``S`` (``model/seeds.py``: ``c_opaque``, ``c_byval``):

- **B** ``private_record_grew`` (``c_opaque``): ``S`` lives behind an opaque
  handle the library allocates and frees; no client sees its size or
  offsets. Compatible: the report may note the internal change (permitted
  diagnostics), but must not attribute a public ABI break to ``S`` or to the
  handle functions.
- **C** ``public_record_grew`` (``c_byval``): ``S`` is defined in the header
  and passed by value; the execution oracle's layout probe measures the size
  and offset change independently and the ``value`` consumer observes it.
  Breaking, attributed to ``S``.
- **A+B** ``func_removed_and_private_record_grew`` (``c_opaque``): ``opq_f``
  is removed *and* B happens. The release is truly BREAKING because of
  ``opq_f``; a report that also blames ``S`` for a public break is a false
  attribution even though its verdict is right.

Each operator also pins a witness (``run.witness``): ``opq_g``/``bv_g``,
whose builtin-only prototype no type edit can reach, confirmed in both
libraries by ``nm``.
"""

from __future__ import annotations

from abiforge.model.config import Config
from abiforge.model.expect import Rule
from abiforge.model.finding import EntityClaim, EntityRef
from abiforge.model.opkit import _REMOVED, EditOp, Roles, _merge, _set
from abiforge.model.seeds import RoleText

#: The one layout mutation of this family.
GROWN_S = "struct S { int a; int c; int b; };"

#: What a layout break of ``S`` is reported as (either names the change).
_S_LAYOUT = frozenset({"TYPE_SIZE_CHANGED", "TYPE_FIELD_OFFSET_CHANGED"})

#: The opaque handle's API: it reaches ``S`` only through a pointer the
#: library owns, so no layout change of ``S`` breaks it.
_HANDLE_API = ("s_new", "s_sum", "s_free")


def _no_public_break(name: str, why: str) -> EntityClaim:
    # Only a public header shows S is not part of the contract: from DWARF or
    # symbols alone, S reached by pointer may be a client-allocated public
    # record (``c_core``'s ``fill_rec``), so the claim is undecided there.
    return EntityClaim(EntityRef(name), required=False, why=why, needs=_H)


_H = frozenset({"H"})


def _no_header(c: Config) -> bool:
    return "H" not in c.evidence_set.atoms


#: Without a public header, a grown record reached by pointer may be public:
#: no ceiling can be ground truth, and the cell is reported undecided.
_PRIVACY_UNSEEN = Rule(
    _no_header,
    {"verdict_max": None},
    "without a public header the evidence cannot tell an opaque record from "
    "a public one reached by pointer",
)


_S_PRIVATE = (
    _no_public_break(
        "S",
        "S is defined only in the library's source and reached through an "
        "opaque handle: its layout is not part of the public contract",
    ),
    *(
        _no_public_break(
            fn,
            "the handle API passes S only by a library-owned pointer",
        )
        for fn in _HANDLE_API
    ),
)


def _grow_private(r: Roles) -> dict[str, RoleText]:
    return _set(r, "opq.s", source=GROWN_S)


def _grow_public(r: Roles) -> dict[str, RoleText]:
    return _set(r, "bv.s", header=GROWN_S)


def _grow_private_drop_f(r: Roles) -> dict[str, RoleText]:
    return _merge(_grow_private(r), _set(r, "opq.f", "", ""))


ATTRIBUTION_OPERATORS: tuple[EditOp, ...] = (
    EditOp(
        "private_record_grew",
        ("opq.gate", "opq.s"),
        _grow_private,
        "S grows behind an opaque handle the library allocates and frees: "
        "an old client creates, uses and frees it with the new library unchanged",
        verdict_min="NO_CHANGE",
        verdict_max="COMPATIBLE_WITH_RISK",
        touches=("opq.s",),
        rules=(_PRIVACY_UNSEEN,),
        claims=_S_PRIVATE,
        witnesses=1,
    ),
    EditOp(
        "public_record_grew",
        ("bv.gate", "bv.s"),
        _grow_public,
        "S is defined in the header and passed by value: its size grows and "
        "S::b moves, which a client's stack storage and by-value call depend on",
        any_of=(_S_LAYOUT,),
        touches=("bv.s",),
        claims=(
            EntityClaim(
                EntityRef("S"),
                required=True,
                kinds=_S_LAYOUT,
                why="the edit changes S's size and S::b's offset",
            ),
        ),
        witnesses=1,
    ),
    EditOp(
        "func_removed_and_private_record_grew",
        ("opq.gate", "opq.s", "opq.f"),
        _grow_private_drop_f,
        "opq_f is removed (a real binary break) while S grows behind its "
        "opaque handle (no break): the break belongs to opq_f alone",
        expected=frozenset({"FUNC_REMOVED"}),
        touches=("opq.s", "opq.f"),
        rules=(
            Rule(
                lambda c: c.evidence == "binary",
                {"expected": frozenset({"FUNC_REMOVED_ELF_ONLY"})},
                "from symbols alone the removal is an export-table removal",
            ),
        ),
        claims=(
            EntityClaim(
                EntityRef("opq_f"),
                required=True,
                kinds=_REMOVED,
                why="the edit deletes opq_f",
            ),
            *_S_PRIVATE,
        ),
        witnesses=1,
    ),
)
