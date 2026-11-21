"""A finite reduction certificate for the bit-field edits of ``operators/fragments``.

The question: do the few bit-field edits the generator runs stand for every
edit of the same shape, *for a named projection*? This module answers it
exhaustively over a stated finite domain, against the independent layout
model (``model/layout_model``), and reports where the answer is no.

**Domain** (``dense_domain``): the ``bitfield_record`` shape (``a : w``,
``b : 16``, ``c : 3``, an ordinary ``tail``, an optional ``unsigned : 0``
barrier ``before`` or ``after`` b). A state is ``(w, barrier)`` with ``w`` in
1..32. A transition is a width change at a fixed barrier, or a barrier
inserted or removed at a fixed width. Each transition is taken under every
configuration in ``CONFIGS`` (default packing and ``-fpack-struct=1``, gcc
and clang).

**Projection** (``signature``): the *shape* of the exact rows an assertion
judges -- which members' offsets move, whether ``a``'s width changes,
whether the size and the alignment change -- not their values. Two
transitions with the same signature under one configuration are one class.
A projection is part of the claim: classes under it say nothing about
values, calling convention, consumer code, or any record outside the shape.

**Certificate** (``certify``): per configuration, the classes the dense
domain reaches and the classes the selected edits reach; a class no
selected edit reaches is *uncovered*, listed with its smallest witness.

**Fault retention** (``retention``): a projection can be covered while a
wrong layout rule still passes the selected edits. Each ``MUTANTS`` entry is
a plausible rule error expressed as an input transform of the real model
(a barrier ignored, the gcc/clang barrier rule swapped, packing ignored or
forced). A suite *detects* a mutant when some transition and configuration
in it gives different exact rows (values included) from the true model.
Dense and selected detection are reported apart. Detecting the four mutants
measures nothing about faults outside them.

Everything here is the model's own arithmetic. ``tests/test_abiforge_layout_model``
ties the model to compilers; ``python -m abiforge.run.fragment_campaign``
ties the selected edits to the checker. This module ties neither.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace

from abiforge.model.fragments import bitfield_record
from abiforge.model.layout_model import Layout, Record, layout

__all__ = [
    "BARRIERS",
    "CONFIGS",
    "MUTANTS",
    "SELECTED",
    "Cfg",
    "State",
    "Transition",
    "certify",
    "dense_domain",
    "retention",
    "rows",
    "signature",
]

BARRIERS = ("none", "before", "after")
WIDTHS = range(1, 33)


@dataclass(frozen=True, order=True)
class State:
    width: int
    barrier: str

    def record(self) -> Record:
        return bitfield_record("cert", self.width, self.barrier)


@dataclass(frozen=True, order=True)
class Transition:
    old: State
    new: State

    @property
    def kind(self) -> str:
        return "width" if self.old.barrier == self.new.barrier else "barrier"

    def __str__(self) -> str:
        o, n = self.old, self.new
        return f"({o.width},{o.barrier})->({n.width},{n.barrier})"


@dataclass(frozen=True, order=True)
class Cfg:
    pack: int | None
    producer: str

    def __str__(self) -> str:
        return f"{self.producer}/pack={self.pack or 'default'}"


CONFIGS: tuple[Cfg, ...] = tuple(
    Cfg(pack, producer) for pack in (None, 1) for producer in ("gcc", "clang")
)


def _t(w0: int, b0: str, w1: int, b1: str) -> Transition:
    return Transition(State(w0, b0), State(w1, b1))


#: The transitions ``operators/fragments`` runs, by operator name.
#: ``tests/test_abiforge_reduction`` checks each against the operator's
#: fixture and edit, so the table cannot drift from the generator.
SELECTED: dict[str, Transition] = {
    "bitfield_width_1_to_7": _t(1, "none", 7, "none"),
    "bitfield_width_7_to_8": _t(7, "none", 8, "none"),
    "bitfield_width_8_to_9": _t(8, "none", 9, "none"),
    "bitfield_width_9_to_15": _t(9, "none", 15, "none"),
    "bitfield_width_15_to_16": _t(15, "none", 16, "none"),
    "bitfield_width_16_to_17": _t(16, "none", 17, "none"),
    "bitfield_width_17_to_31": _t(17, "none", 31, "none"),
    "bitfield_width_31_to_32": _t(31, "none", 32, "none"),
    "bitfield_width_17_to_16_shrink": _t(17, "none", 16, "none"),
    "bitfield_width_16_to_17_behind_barrier": _t(16, "before", 17, "before"),
    "bitfield_barrier_inserted_before_7": _t(7, "none", 7, "before"),
    "bitfield_barrier_inserted_before_17": _t(17, "none", 17, "before"),
    "bitfield_barrier_inserted_after_17": _t(17, "none", 17, "after"),
    "bitfield_barrier_removed_after_17": _t(17, "after", 17, "none"),
    "bitfield_barrier_removed_before_7": _t(7, "before", 7, "none"),
    "bitfield_barrier_removed_after_6": _t(6, "after", 6, "none"),
    "bitfield_barrier_removed_before_1": _t(1, "before", 1, "none"),
    "bitfield_barrier_removed_after_14": _t(14, "after", 14, "none"),
    "bitfield_barrier_removed_after_16": _t(16, "after", 16, "none"),
    "bitfield_barrier_removed_before_30": _t(30, "before", 30, "none"),
    "bitfield_width_1_to_2_after_barrier": _t(1, "after", 2, "after"),
    "bitfield_width_1_to_2_before_barrier": _t(1, "before", 2, "before"),
}


def dense_domain() -> Iterator[Transition]:
    """Every width change at a fixed barrier and every barrier insertion or
    removal at a fixed width (``before``<->``after`` is two edits, not one)."""
    for b in BARRIERS:
        for w0 in WIDTHS:
            for w1 in WIDTHS:
                if w0 != w1:
                    yield _t(w0, b, w1, b)
    for w in WIDTHS:
        for b in ("before", "after"):
            yield _t(w, "none", w, b)
            yield _t(w, b, w, "none")


Model = Callable[[Record, Cfg], Layout]


def _true(record: Record, cfg: Cfg) -> Layout:
    return layout(record, pack=cfg.pack, producer=cfg.producer)


Row = tuple[str, str, int, int]


def rows(t: Transition, cfg: Cfg, model: Model = _true) -> frozenset[Row]:
    """The exact rows (kind, member, old, new) the model predicts for *t*."""
    lo, ln = model(t.old.record(), cfg), model(t.new.record(), cfg)
    out: set[Row] = set()
    for m, off in lo.offsets.items():
        if ln.offsets[m] != off:
            out.add(("offset", m, off, ln.offsets[m]))
    for m, w in lo.widths.items():
        if ln.widths[m] != w:
            out.add(("width", m, w, ln.widths[m]))
    if lo.size != ln.size:
        out.add(("size", "", lo.size, ln.size))
    if lo.align != ln.align:
        out.add(("align", "", lo.align, ln.align))
    return frozenset(out)


def signature(t: Transition, cfg: Cfg) -> tuple[str, frozenset[tuple[str, str]]]:
    """*t*'s class under the row-shape projection: its edit kind and which
    (row kind, member) pairs it produces, without their values."""
    return t.kind, frozenset((k, m) for k, m, _, _ in rows(t, cfg))


def _describe(sig: tuple[str, frozenset[tuple[str, str]]]) -> str:
    kind, shape = sig
    body = ",".join(f"{k}:{m}" if m else k for k, m in sorted(shape)) or "nothing"
    return f"{kind}[{body}]"


def certify(
    selected: dict[str, Transition] | None = None,
) -> dict[str, object]:
    """Per configuration: the classes reached densely and by *selected*,
    and each uncovered class with its smallest witness transition."""
    chosen = SELECTED if selected is None else selected
    per: dict[str, object] = {}
    dense_n = 0
    for cfg in CONFIGS:
        classes: dict[tuple[str, frozenset[tuple[str, str]]], list[Transition]] = {}
        for t in dense_domain():
            classes.setdefault(signature(t, cfg), []).append(t)
            dense_n += 1
        hit = {signature(t, cfg) for t in chosen.values()}
        uncovered = {
            _describe(sig): str(min(ts))
            for sig, ts in classes.items()
            if sig not in hit
        }
        per[str(cfg)] = {
            "classes": len(classes),
            "covered": len(classes) - len(uncovered),
            "uncovered": dict(sorted(uncovered.items())),
        }
    return {
        "projection": "row shape: edit kind x {(row kind, member)}; values excluded",
        "dense_transitions": dense_n,
        "selected_transitions": len(chosen) * len(CONFIGS),
        "configs": per,
    }


def _drop_barriers(r: Record) -> Record:
    return replace(r, members=tuple(m for m in r.members if m.name is not None))


_SWAP = {"gcc": "clang", "clang": "gcc"}

#: Plausible layout-rule errors, each an input transform of the true model.
MUTANTS: dict[str, Model] = {
    # A barrier with no member DIE read as no member at all.
    "barrier_ignored": lambda r, c: _true(_drop_barriers(r), c),
    # The #pragma pack result transferred to -fpack-struct (or the reverse):
    # the error the research campaign's own fixture would have made.
    "barrier_rule_swapped": lambda r, c: _true(r, Cfg(c.pack, _SWAP[c.producer])),
    "pack_ignored": lambda r, c: _true(r, Cfg(None, c.producer)),
    "pack_forced": lambda r, c: _true(r, Cfg(1, c.producer)),
}


def retention(selected: dict[str, Transition] | None = None) -> dict[str, object]:
    """Per mutant: how many (transition, config) cells tell it from the true
    model, over the dense domain and over *selected*."""
    chosen = SELECTED if selected is None else selected
    out: dict[str, object] = {}
    for name, mutant in MUTANTS.items():
        dense = sum(
            rows(t, c) != rows(t, c, mutant) for t in dense_domain() for c in CONFIGS
        )
        sel = sum(
            rows(t, c) != rows(t, c, mutant) for t in chosen.values() for c in CONFIGS
        )
        out[name] = {"dense_detecting_cells": dense, "selected_detecting_cells": sel}
    return out
