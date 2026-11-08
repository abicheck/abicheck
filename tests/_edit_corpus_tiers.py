"""Which corpus cells run when: PR, nightly and weekly tiers.

- **PR** (``pr_cells``): a fixed, deterministic handful of cells, one per
  axis value worth guarding (each flag, each evidence level, a cross-family
  consumer, a feature-gated standard), so a PR pays seconds, not minutes.
- **nightly** (``nightly_cells``): a seeded *pairwise covering array* over
  every axis, including the (operator, seed) cell itself: every pair of axis
  values appears in at least one row. The seed comes from
  ``ABICHECK_EDIT_CORPUS_SEED`` (default 0), so a run is reproducible from
  its seed.
- **weekly** (``weekly_cells``): every applicable operator under the
  newest installed toolchain of each family, with the exotic settings the
  nightly does not draw (``-m32``, newest standards, -fabi-version,
  ``_GLIBCXX_USE_CXX11_ABI=0``, a mismatched consumer).

Tiers select cells; they never decide outcomes. A row whose toolchain is not
installed still appears and is reported unavailable.
"""

from __future__ import annotations

import itertools
import os
import random
from collections.abc import Sequence
from dataclasses import dataclass, replace

from tests._edit_corpus_config import (
    EVIDENCE,
    STANDARDS,
    Config,
    Flags,
    Standard,
    Toolchain,
    default_config,
    has_feature,
)
from tests._edit_corpus_operators import OP_BY_NAME, EditOp, matrix
from tests._edit_corpus_seeds import SEED_BY_NAME, Seed
from tests._edit_corpus_toolchains import (
    discover,
    newest,
    std_flag,
    std_flag_for_driver,
)


@dataclass(frozen=True)
class Cell:
    op: EditOp
    seed: Seed
    config: Config

    @property
    def id(self) -> str:
        return f"{self.op.name}-{self.seed.name}-{self.config.id}"


def stds(lang: str) -> tuple[Standard, ...]:
    return tuple(s for s in STANDARDS if s.lang == lang)


# --- PR tier ----------------------------------------------------------------

# (op, seed, producer family, config changes). One guard per axis.
_PR: tuple[tuple[str, str, str, dict[str, object]], ...] = (
    ("struct_append_field", "c_core", "gcc", {}),
    ("virtual_reorder", "cxx_classes", "clang", {}),
    ("enum_append_wide_value", "c_core", "gcc", {"flags": Flags(short_enums=True)}),
    ("struct_array_shrink_in_padding", "c_core", "clang", {"flags": Flags(pack=1)}),
    (
        "func_lose_default_visibility",
        "c_core",
        "gcc",
        {"flags": Flags(visibility="hidden")},
    ),
    ("struct_append_field", "c_core", "gcc", {"evidence": "binary"}),
    ("enum_change_value", "c_core", "gcc", {"evidence": "dwarf"}),
    ("func_param_type_change", "cxx_classes", "gcc", {"evidence": "binary"}),
    ("no_unique_address_added", "cxx_classes", "gcc", {"std": "c++20"}),
    ("enum_underlying_narrow", "c_core", "clang", {"std": "c23"}),
    ("enum_change_value", "c_core", "gcc", {"std": "c89", "consumer": "clang"}),
    ("rebuild_cxx11_abi_off", "cxx_stdlib", "gcc", {}),
    ("method_noexcept_add", "cxx_classes", "gcc", {}),
    ("concept_constraint_added", "cxx_classes", "clang", {"std": "c++20"}),
    ("rebuild_optimized", "c_core", "gcc", {}),
)


def _toolchain(family: str) -> Toolchain | None:
    return newest(family)


def pr_cells() -> list[Cell | tuple[str, str]]:
    """The PR tier; a ``(cell id, reason)`` pair where a toolchain is missing."""
    out: list[Cell | tuple[str, str]] = []
    for op_name, seed_name, family, changes in _PR:
        seed = SEED_BY_NAME[seed_name]
        tc = _toolchain(family)
        if tc is None:
            out.append(
                (f"{op_name}-{seed_name}-{family}", f"no {family} toolchain installed")
            )
            continue
        cfg = default_config(seed.lang, tc)
        for key, value in changes.items():
            if key == "std":
                cfg = replace(cfg, std=next(s for s in STANDARDS if s.name == value))
            elif key == "consumer":
                other = _toolchain(str(value))
                if other is None:
                    out.append(
                        (
                            f"{op_name}-{seed_name}-{family}",
                            f"no {value} consumer toolchain",
                        )
                    )
                    break
                cfg = replace(cfg, consumer=other)
            else:
                cfg = replace(cfg, **{key: value})
        else:
            out.append(Cell(OP_BY_NAME[op_name], seed, cfg))
    return out


# --- pairwise covering array -----------------------------------------------

_STD_SLOTS = 4  # low .. high, mapped onto each language's standards


def _factors(toolchains: Sequence[Toolchain]) -> dict[str, list[object]]:
    cells = [f"{op.name}@{seed.name}" for op, seed in matrix()]
    tc_ids = [t.id for t in toolchains] or ["none"]
    return {
        "cell": cells,
        "producer": tc_ids,
        "consumer": ["same", "other"],
        "std": list(range(_STD_SLOTS)),
        "opt": ["O0", "O2"],
        "visibility": ["default", "hidden"],
        "short_enums": [False, True],
        "pack": [None, 1],
        "cxx11_abi": [None, 0],
        "abi_version": [None, 11],
        "evidence": list(EVIDENCE),
    }


def pairwise(
    factors: dict[str, list[object]], seed: int, candidates: int = 40
) -> list[dict[str, object]]:
    """A greedy seeded covering array: every pair of factor values in some row."""
    rng = random.Random(seed)
    names = list(factors)
    uncovered = {
        (a, va, b, vb)
        for a, b in itertools.combinations(names, 2)
        for va in factors[a]
        for vb in factors[b]
    }
    rows: list[dict[str, object]] = []
    while uncovered:
        # Anchor each row on one uncovered pair so every row makes progress.
        a, va, b, vb = (
            min(uncovered, key=repr)
            if not rows
            else rng.choice(sorted(uncovered, key=repr))
        )
        best, best_gain = None, -1
        for _ in range(candidates):
            row = {n: rng.choice(factors[n]) for n in names}
            row[a], row[b] = va, vb
            gain = sum(
                (x, row[x], y, row[y]) in uncovered
                for x, y in itertools.combinations(names, 2)
            )
            if gain > best_gain:
                best, best_gain = row, gain
        assert best is not None
        rows.append(best)
        uncovered -= {
            (x, best[x], y, best[y]) for x, y in itertools.combinations(names, 2)
        }
    return rows


def uncovered_pairs(
    factors: dict[str, list[object]], rows: list[dict[str, object]]
) -> set[tuple]:
    """Pairs no row covers (empty for a covering array). An independent check."""
    need = {
        (a, va, b, vb)
        for a, b in itertools.combinations(list(factors), 2)
        for va in factors[a]
        for vb in factors[b]
    }
    for row in rows:
        for a, b in itertools.combinations(list(factors), 2):
            need.discard((a, row[a], b, row[b]))
    return need


def _repair_std(op: EditOp, lang: str, slot: int, tc: Toolchain) -> Standard:
    """The row's standard, raised to the first one that has the op's features, and
    lowered to one the producer accepts."""
    options = stds(lang)
    idx = min(slot * len(options) // _STD_SLOTS, len(options) - 1)
    floor = 0
    for feat in op.features:
        floor = max(
            floor, next((i for i, s in enumerate(options) if has_feature(s, feat)), 0)
        )
    idx = max(idx, floor)
    while idx > floor and std_flag(tc, options[idx]) is None:
        idx -= 1
    return options[idx]


def row_to_cell(row: dict[str, object], toolchains: Sequence[Toolchain]) -> Cell | None:
    op_name, seed_name = str(row["cell"]).split("@")
    op, seed = OP_BY_NAME[op_name], SEED_BY_NAME[seed_name]
    by_id = {t.id: t for t in toolchains}
    tc = by_id.get(str(row["producer"]))
    if tc is None:
        return None
    others = [t for t in toolchains if t.family != tc.family]
    consumer = others[-1] if row["consumer"] == "other" and others else None
    lang = seed.lang
    flags = Flags(
        opt=str(row["opt"]),
        visibility=str(row["visibility"]),
        short_enums=bool(row["short_enums"]),
        pack=row["pack"],  # type: ignore[arg-type]
        cxx11_abi=row["cxx11_abi"] if lang == "c++" else None,  # type: ignore[arg-type]
        abi_version=row["abi_version"] if tc.family == "gcc" else None,  # type: ignore[arg-type]
    )
    cfg = Config(
        std=_repair_std(op, lang, int(row["std"]), tc),  # type: ignore[arg-type]
        producer=tc,
        flags=flags,
        consumer=consumer,
        evidence=str(row["evidence"]),
    )
    return Cell(op, seed, cfg)


def nightly_seed() -> int:
    return int(os.environ.get("ABICHECK_EDIT_CORPUS_SEED", "0"))


def nightly_cells(seed: int | None = None) -> list[Cell]:
    toolchains = discover()
    rows = pairwise(_factors(toolchains), nightly_seed() if seed is None else seed)
    cells = [row_to_cell(r, toolchains) for r in rows]
    return [c for c in cells if c is not None]


# --- weekly -----------------------------------------------------------------


def _weekly_configs(
    lang: str, tc: Toolchain, others: Sequence[Toolchain]
) -> list[Config]:
    # Newest standard both the producer and the header frontend's system
    # compiler accept (castxml emulates the system cc/c++).
    driver = "c++" if lang == "c++" else "cc"
    top = [
        s
        for s in stds(lang)
        if std_flag(tc, s) is not None and std_flag_for_driver(driver, s) is not None
    ]
    newest_std = top[-1] if top else stds(lang)[-1]
    base = Config(std=newest_std, producer=tc)
    out = [
        base,
        replace(base, flags=Flags(bits=32)),
        replace(base, flags=Flags(opt="O2", visibility="hidden")),
        replace(base, flags=Flags(short_enums=True, pack=1)),
        replace(base, consumer_flags=Flags(short_enums=True)),
    ]
    if tc.family == "gcc":
        out.append(replace(base, flags=Flags(abi_version=11)))
    if lang == "c++":
        out.append(replace(base, flags=Flags(cxx11_abi=0)))
    if others:
        out.append(replace(base, consumer=others[-1]))
    return out


def weekly_cells() -> list[Cell]:
    cells: list[Cell] = []
    families = sorted({t.family for t in discover()})
    for fam in families:
        tc = newest(fam)
        assert tc is not None
        others = [newest(f) for f in families if f != fam]
        for op, seed in matrix():
            for cfg in _weekly_configs(
                seed.lang, tc, [o for o in others if o is not None]
            ):
                cells.append(Cell(op, seed, cfg))
    return cells
