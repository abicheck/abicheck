"""Union and bit-field operators with exact, configuration-aware expectations.

Each operator edits one ``c_layout`` fixture (``model/fragments``) or
rebuilds the seed with other flags. Its ground truth is a ``FindingAssertion``
(``model/exact``) computed per configuration from the independent layout
model (``model/layout_model``), never from a checker:

- **storage-layout rows**: for every fixture record, a ``TYPE_SIZE_CHANGED``
  / ``TYPE_ALIGNMENT_CHANGED`` row when the model's size or alignment moves,
  a ``TYPE_FIELD_OFFSET_CHANGED`` row per named member whose bit offset
  moves, and a ``FIELD_BITFIELD_CHANGED`` row per width change;
- **declared-member rows** (unions): ``UNION_FIELD_ADDED`` /
  ``UNION_FIELD_REMOVED`` / ``UNION_FIELD_TYPE_CHANGED`` from the two
  declarations, kept apart from the storage rows: equal ``sizeof``/``alignof``
  never implies an unchanged declaration, and a changed declaration never
  implies a moved byte.

The assertion's scope is every fixture record the edit touches and every kind
above, so an unchanged control (``*_rebuild_optimized``) is a required absence
of in-scope rows, and a correct verdict cannot hide a wrong member, value or
multiplicity. Alignment rows need a header dump: gcc and clang write no
``DW_AT_alignment`` for a record without ``alignas``, so below headers an
expected alignment row is *unknown* (the cell is evidence-limited), never
passed. The model covers SysV x86-64 gcc/clang at default packing and
``-fpack-struct=1``; every other configuration is not applicable, with the
reason, rather than given a guessed expectation.

Verdict bounds follow the catalog's default verdict of the rows the
configuration observes, the project's documented policy for each kind.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from abiforge.checkers.abicheck import default_verdict
from abiforge.model.config import Config, Flags
from abiforge.model.exact import (
    Bits,
    ExactRow,
    FindingAssertion,
    FindingScope,
    Width,
)
from abiforge.model.expect import VERDICT_RANK
from abiforge.model.fragments import (
    BITFIELD_WIDTHS,
    LAYOUT_FIXTURES,
    Fixture,
    bitfield_record,
    envelope,
    payload,
    role_text,
)
from abiforge.model.layout_model import Layout, Member, Record, Unsupported, layout
from abiforge.model.opkit import EditOp, Roles, _set, _with
from abiforge.operators.platform import _X86_HOSTS

__all__ = ["FRAGMENT_OPERATORS", "MODELLED_STATES", "expected_rows"]

#: Every record state an operator builds (old fixtures and new edits), for the
#: model's validation against compiler probes and raw DWARF.
MODELLED_STATES: list[tuple[str, tuple[Record, ...]]] = []

_FX = {fx.id: fx for fx in LAYOUT_FIXTURES}
_GATE = "lf.gate"

SIZE = "TYPE_SIZE_CHANGED"
ALIGN = "TYPE_ALIGNMENT_CHANGED"
OFFSET = "TYPE_FIELD_OFFSET_CHANGED"
WIDTH = "FIELD_BITFIELD_CHANGED"
U_ADD = "UNION_FIELD_ADDED"
U_DEL = "UNION_FIELD_REMOVED"
U_TYPE = "UNION_FIELD_TYPE_CHANGED"
#: Kinds a fixture record's declared members could be reported under: in
#: scope so a spurious one (a barrier read as a member, say) is caught.
_MEMBER_NOISE = frozenset(
    {
        "TYPE_FIELD_ADDED",
        "TYPE_FIELD_ADDED_COMPATIBLE",
        "TYPE_FIELD_REMOVED",
        "TYPE_FIELD_TYPE_CHANGED",
    }
)
SCOPE_KINDS = (
    frozenset({SIZE, ALIGN, OFFSET, WIDTH, U_ADD, U_DEL, U_TYPE}) | _MEMBER_NOISE
)
#: Kinds whose values are storage layout (as opposed to declarations).
_STORAGE_KINDS = frozenset({SIZE, ALIGN, OFFSET})
#: The atoms that record a rebuild flag (DWARF's producer string, build data).
_BUILD_RECORD = frozenset({"D", "BD"})
#: The evidence an alignment row needs (see the module docstring).
_KIND_NEEDS = {ALIGN: frozenset({"H"})}


def _model_gap(config: Config, flags: Flags) -> str | None:
    """Why the layout model does not cover *config* built with *flags*, or None."""
    if config.producer.family not in ("gcc", "clang"):
        return f"the layout model covers gcc and clang, not {config.producer.family}"
    if config.platform != ("linux", "elf"):
        return f"the layout model covers SysV ELF Linux, not {config.platform}"
    if flags.bits != 64:
        return "the layout model covers LP64 (-m64) only"
    if config.producer.arch != "x86_64":
        return f"the layout model covers x86-64 only, not {config.producer.arch}"
    if flags.pack not in (None, 1):
        return f"the layout model covers default packing and -fpack-struct=1, not {flags.pack}"
    return None


def _layouts(
    records: tuple[Record, ...], pack: int | None, producer: str
) -> dict[str, Layout]:
    known: dict[str, Layout] = {}
    out: dict[str, Layout] = {}
    for r in records:
        lay = layout(r, pack=pack, producer=producer, known=known)
        known[r.spelling] = lay
        out[r.name] = lay
    return out


def _spelling(m: Member) -> str:
    """A member's declared type as the checker spells it (``char[9]``)."""
    base = m.ctype.removeprefix("union ")
    return f"{base}[{m.extent}]" if m.extent is not None else base


def expected_rows(
    old: tuple[Record, ...],
    new: tuple[Record, ...],
    *,
    old_pack: int | None,
    new_pack: int | None,
    producer: str,
) -> tuple[ExactRow, ...]:
    """The rows a report must hold for the *old* -> *new* record edit."""
    lo = _layouts(old, old_pack, producer)
    ln = _layouts(new, new_pack, producer)
    rows: list[ExactRow] = []
    new_by_name = {r.name: r for r in new}
    for r_old in old:
        r_new = new_by_name.get(r_old.name)
        if r_new is None:
            raise Unsupported(f"{r_old.name} is removed: not a fragment edit")
        a, b = lo[r_old.name], ln[r_old.name]
        if a.size != b.size:
            rows.append(ExactRow(SIZE, r_old.name, None, Bits(a.size), Bits(b.size)))
        if a.align != b.align:
            rows.append(ExactRow(ALIGN, r_old.name, None, Bits(a.align), Bits(b.align)))
        if r_old.tag == "struct":
            for name, off in a.offsets.items():
                if name in b.offsets and b.offsets[name] != off:
                    rows.append(
                        ExactRow(
                            OFFSET, r_old.name, name, Bits(off), Bits(b.offsets[name])
                        )
                    )
            for name, w in a.widths.items():
                if name in b.widths and b.widths[name] != w:
                    rows.append(
                        ExactRow(
                            WIDTH, r_old.name, name, Width(w), Width(b.widths[name])
                        )
                    )
            continue
        mo = {m.name: m for m in r_old.members if m.name}
        mn = {m.name: m for m in r_new.members if m.name}
        for name, m in mo.items():
            if name not in mn:
                rows.append(ExactRow(U_DEL, r_old.name, name, _spelling(m), None))
            elif _spelling(m) != _spelling(mn[name]):
                rows.append(
                    ExactRow(
                        U_TYPE, r_old.name, name, _spelling(m), _spelling(mn[name])
                    )
                )
        for name, m in mn.items():
            if name not in mo:
                rows.append(ExactRow(U_ADD, r_old.name, name, None, _spelling(m)))
    return tuple(rows)


def _max_verdict(kinds: set[str]) -> str:
    best = "NO_CHANGE"
    for k in kinds:
        v = default_verdict(k) or "BREAKING"
        if VERDICT_RANK[v] > VERDICT_RANK[best]:
            best = v
    return best


def _ceiling(verdict: str, has_rows: bool) -> str | None:
    """The verdict ceiling: none for a break; ``COMPATIBLE`` for an edit with
    no row at all (a control or an absorbed change); the risk ceiling for a
    compatible addition. An edit whose only rows this evidence cannot see
    keeps no ceiling: the truth is still a change."""
    if VERDICT_RANK[verdict] >= VERDICT_RANK["API_BREAK"]:
        return None
    if verdict == "NO_CHANGE":
        return None if has_rows else "COMPATIBLE"
    return "COMPATIBLE_WITH_RISK"


@dataclass(frozen=True)
class _Plan:
    """One operator's old/new records and the new side's flags."""

    fixture: Fixture
    new_records: tuple[Record, ...]
    rebuild: Callable[[Flags], Flags] | None = None
    carrier: frozenset[str] = frozenset()

    def exact(self, config: Config) -> Mapping[str, object]:
        new_flags = self.rebuild(config.flags) if self.rebuild else config.flags
        rows = expected_rows(
            self.fixture.records,
            self.new_records,
            old_pack=config.flags.pack,
            new_pack=new_flags.pack,
            producer=config.producer.family,
        )
        atoms = config.evidence_set.atoms
        seen = {r.kind for r in rows if _KIND_NEEDS.get(r.kind, frozenset()) <= atoms}
        verdict = _max_verdict(seen)
        assertion = FindingAssertion(
            FindingScope(SCOPE_KINDS, frozenset(r.name for r in self.fixture.records)),
            rows,
            why=f"{self.fixture.id} layout and declared members",
            kind_needs=_KIND_NEEDS,
        )
        recorded = bool(atoms & _BUILD_RECORD)
        if (
            config.flags.pack is not None or new_flags.pack is not None
        ) and not recorded:
            # -fpack-struct is in neither the binary nor the distributed
            # header: a header dump without DWARF or build data reads the
            # declarations at default packing, so the storage values this
            # build really has are unknown here. Declared members and widths
            # are header facts and stay judged.
            assertion = assertion.without_kinds(
                _STORAGE_KINDS,
                f"the build's -fpack-struct is unrecorded at {config.evidence_label}",
            )
            seen -= _STORAGE_KINDS
            verdict = _max_verdict(seen)
        if self.carrier and not (atoms & self.carrier):
            # A build-flag edit the configuration keeps no record of: every
            # row is an obligation nothing here can check.
            assertion = assertion.without_kinds(
                SCOPE_KINDS,
                f"the rebuild flag is recorded only by {sorted(self.carrier)}, "
                f"absent at {config.evidence_label}",
            )
            seen = set()
            verdict = "NO_CHANGE"
        # Rows exist but this evidence can judge none of them: the edit is
        # unobservable here (a consumer break is not a false clean), and the
        # assertion's unknown keeps the cell from reading as a pass.
        observable = not (rows and not assertion.rows)
        return {
            "observable": observable,
            "expected": frozenset(seen),
            "any_of": (),
            "verdict_min": verdict,
            "verdict_max": _ceiling(verdict, bool(rows)),
            "assertions": (assertion,),
        }

    def precondition(self, config: Config) -> str | None:
        flags = self.rebuild(config.flags) if self.rebuild else config.flags
        return _model_gap(config, config.flags) or _model_gap(config, flags)


def _all_kinds(plan: _Plan) -> frozenset[str]:
    """Every kind the plan's rows can take over the modelled configurations
    (the static view coverage accounting reads)."""
    out: set[str] = set()
    for pack in (None, 1):
        for producer in ("gcc", "clang"):
            new_pack = plan.rebuild(Flags(pack=pack)).pack if plan.rebuild else pack
            rows = expected_rows(
                plan.fixture.records,
                plan.new_records,
                old_pack=pack,
                new_pack=new_pack,
                producer=producer,
            )
            out |= {r.kind for r in rows}
    return frozenset(out)


def _op(
    name: str,
    fid: str,
    new_records: tuple[Record, ...] | None,
    why: str,
    *,
    construct: str,
    rebuild: Callable[[Flags], Flags] | None = None,
    carrier: frozenset[str] = frozenset(),
) -> EditOp:
    fx = _FX[fid]
    plan = _Plan(
        fx, new_records if new_records is not None else fx.records, rebuild, carrier
    )

    def edit(r: Roles) -> dict[str, tuple[str, str]]:
        if new_records is None:
            return {}
        return _set(r, fx.role, *role_text(fx.id, new_records))

    MODELLED_STATES.append((f"{name}:old", fx.records))
    if new_records is not None:
        MODELLED_STATES.append((f"{name}:new", new_records))
    kinds = _all_kinds(plan)
    return EditOp(
        name,
        (_GATE, fx.role),
        edit,
        why,
        any_of=(kinds,) if kinds else (),
        verdict_min="NO_CHANGE",
        # An edit with no row anywhere (a control) declares its ceiling
        # statically too; ``exact`` sets the per-config bounds.
        verdict_max=None if kinds else "COMPATIBLE",
        rebuild=rebuild,
        carrier=carrier,
        precondition=plan.precondition,
        exact=plan.exact,
        constructs=(construct,),
        touches=(fx.role,),
        # The model is x86-64 SysV; another host CPU makes the cell unavailable.
        host_arch=_X86_HOSTS,
    )


def _u(fid: str, *members: Member, embedded: bool = True) -> tuple[Record, ...]:
    u = payload(fid, *members)
    return (u, envelope(fid)) if embedded else (u,)


def _c(n: int) -> Member:
    return Member("c", "char", extent=n)


_U64 = Member("bits", "uint64_t")
_DBL = Member("bits", "double")
_REAL = Member("real", "double")


_UNION_OPS = (
    _op(
        "union_member_retyped_same_size",
        "uretype",
        _u("uretype", _c(8), _DBL),
        "bits: uint64_t -> double: same size and alignment, so no byte moves, "
        "but the member's declared type changes",
        construct="union",
    ),
    _op(
        "union_member_retyped_same_size_back",
        "uretype_back",
        _u("uretype_back", _c(8), _U64),
        "bits: double -> uint64_t, the reverse retype",
        construct="union",
    ),
    _op(
        "union_member_added_same_layout",
        "uadd",
        _u("uadd", _c(8), _U64, _REAL),
        "a double member joins char[8] and uint64_t: size and alignment unchanged",
        construct="union",
    ),
    _op(
        "union_member_raises_alignment",
        "ualign_add",
        _u("ualign_add", _c(8), _U64),
        "adding uint64_t beside char[8] raises the unpacked union alignment "
        "from 1 to 8 without growing the union; its enclosing record grows",
        construct="union",
    ),
    _op(
        "union_member_lowers_alignment",
        "ualign_remove",
        _u("ualign_remove", _c(8)),
        "removing the sole uint64_t lowers the unpacked union alignment "
        "from 8 to 1 and moves both the union and the following word",
        construct="union",
    ),
    _op(
        "union_member_removed_same_layout",
        "uremove",
        _u("uremove", _c(8), _U64),
        "the double member leaves: size and alignment unchanged",
        construct="union",
    ),
    *(
        _op(
            f"union_char_extent_{a}_to_{b}",
            f"uext{a}",
            _u(f"uext{a}", _c(b), _U64),
            f"c: char[{a}] -> char[{b}] beside uint64_t: the union's size moves "
            "only when the array crosses the 8-byte alignment unit (unpacked), "
            "and the enclosing record's trailing member moves with it",
            construct="union",
        )
        for a, b in ((7, 8), (8, 9), (15, 16), (16, 17))
    ),
    *(
        _op(
            f"union_array_stride_{direction}",
            fid,
            (payload(fid, _c(n), _U64), envelope(fid, extent=3)),
            "three embedded union elements multiply a change in element stride "
            "at the trailing word; an extent change inside padding preserves its offset",
            construct="union",
        )
        for direction, fid, n in (
            ("grows", "ustride_grow", 9),
            ("shrinks", "ustride_shrink", 8),
            ("unchanged", "ustride_same", 8),
        )
    ),
    _op(
        "union_char_only_extent_8_to_9",
        "uext_char",
        _u("uext_char", _c(9)),
        "a char-only union grows 8 -> 9 bytes: alignment stays 1",
        construct="union",
    ),
    _op(
        "union_rebuild_packed",
        "upack",
        None,
        "-fpack-struct=1 drops the union's alignment to 1 and moves it and the "
        "trailing member inside the enclosing record",
        construct="union",
        rebuild=_with(pack=1),
        carrier=_BUILD_RECORD,
    ),
    _op(
        "union_rebuild_packed_alignment_only",
        "ualign",
        None,
        "-fpack-struct=1 on a union of char[8] and uint64_t: size stays 8, "
        "only alignof drops 8 -> 1 (an alignment-only fixture)",
        construct="union",
        rebuild=_with(pack=1),
        carrier=_BUILD_RECORD,
    ),
    _op(
        "union_rebuild_optimized",
        "uctl",
        None,
        "-O2 rebuild of an unchanged union: a control, no in-scope row",
        construct="compile_option",
        rebuild=_with(opt="O2"),
    ),
)


def _width_op(a: int, b: int, fid: str) -> EditOp:
    return _op(
        f"bitfield_width_{a}_to_{b}" + ("_shrink" if b < a else ""),
        fid,
        (bitfield_record(fid, b, "none"),),
        f"a: {a} -> {b} bits before b:16, c:3 and an ordinary tail: unpacked, b "
        "moves only when a+b crosses the 32-bit unit (and sizeof may stay); "
        "packed, every later bit-field moves by the width delta",
        construct="struct",
    )


_BITFIELD_OPS = (
    *(
        _width_op(a, b, f"bw{a}")
        for a, b in zip(BITFIELD_WIDTHS, BITFIELD_WIDTHS[1:], strict=False)
    ),
    _width_op(17, 16, "bw17_shrink"),
    _op(
        "bitfield_width_16_to_17_behind_barrier",
        "bw16_before",
        (bitfield_record("bw16_before", 17, "before"),),
        "a: 16 -> 17 with a :0 barrier before b: unpacked the barrier already "
        "puts b at the next unit, so only a's width changes (absorbed)",
        construct="struct",
    ),
    _op(
        "bitfield_barrier_inserted_before_7",
        "bins7_before",
        (bitfield_record("bins7_before", 7, "before"),),
        "unsigned :0 inserted before b after a 7-bit a: b, c and tail move to "
        "the next unit (gcc -fpack-struct=1 ignores the barrier)",
        construct="struct",
    ),
    _op(
        "bitfield_barrier_inserted_before_17",
        "bins17_before",
        (bitfield_record("bins17_before", 17, "before"),),
        "unsigned :0 inserted before b after a 17-bit a: unpacked b already "
        "starts the next unit, so nothing moves",
        construct="struct",
    ),
    _op(
        "bitfield_barrier_inserted_after_17",
        "bins17_after",
        (bitfield_record("bins17_after", 17, "after"),),
        "unsigned :0 inserted after b: c and tail move to the next unit and "
        "sizeof grows 8 -> 12 (unpacked)",
        construct="struct",
    ),
    _op(
        "bitfield_barrier_removed_after_17",
        "brem17_after",
        (bitfield_record("brem17_after", 17, "none"),),
        "the :0 barrier after b is removed: c and tail move back",
        construct="struct",
    ),
    _op(
        "bitfield_barrier_removed_before_7",
        "brem7_before",
        (bitfield_record("brem7_before", 7, "none"),),
        "the :0 barrier before b is removed: b, c and tail move back",
        construct="struct",
    ),
    *(
        _op(
            f"bitfield_barrier_removed_{where}_{w}",
            f"brem{w}_{where}",
            (bitfield_record(f"brem{w}_{where}", w, "none"),),
            f"the :0 barrier {where} b is removed after a {w}-bit a: {why}",
            construct="struct",
        )
        for w, where, why in (
            (6, "after", "c moves back; unpacked tail too, clang packed size too"),
            (1, "before", "b, c, tail move back and sizeof shrinks"),
            (14, "after", "unpacked nothing moves; packed c moves back"),
            (16, "after", "b already ends on the unit: nothing moves"),
            (30, "before", "unpacked nothing moves; packed b and c move back"),
        )
    ),
    *(
        _op(
            f"bitfield_width_1_to_2_{where}_barrier",
            f"bw1_{where}",
            (bitfield_record(f"bw1_{where}", 2, where),),
            f"a: 1 -> 2 bits with a :0 barrier {where} b: {why}",
            construct="struct",
        )
        for where, why in (
            ("after", "b moves inside its unit, the barrier holds c and tail"),
            ("before", "the barrier holds b, c and tail: only a's width changes"),
        )
    ),
    _op(
        "bitfield_rebuild_packed_alignment_only",
        "balign",
        None,
        "-fpack-struct=1 on a:16, b:16: no bit moves and sizeof stays 4, only "
        "alignof drops 4 -> 1 (an alignment-only fixture)",
        construct="struct",
        rebuild=_with(pack=1),
        carrier=_BUILD_RECORD,
    ),
    _op(
        "bitfield_rebuild_optimized",
        "bctl",
        None,
        "-O2 rebuild of unchanged bit-fields: a control, no in-scope row",
        construct="compile_option",
        rebuild=_with(opt="O2"),
    ),
)

FRAGMENT_OPERATORS: tuple[EditOp, ...] = (*_UNION_OPS, *_BITFIELD_OPS)
