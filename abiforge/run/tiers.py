"""Which corpus cells run when: PR, nightly and weekly tiers.

- **PR** (``pr_cells``): a fixed, deterministic handful of cells, one per
  axis value worth guarding (each flag, each evidence level, a cross-family
  consumer, a feature-gated standard), so a PR pays seconds, not minutes;
  plus ``pr_consumer_cells``: a breaking and a compatible edit per (build
  family, seed) of a small pairwise array over (build family, consumer
  family, consumer standard), each library pair checked against all of its
  consumers, and one dual-ABI cell whose infeasible pair must be reported.
- **nightly** (``nightly_cells``): a seeded *pairwise covering array* over
  every axis, including the (operator, seed) cell itself and the consumer
  axes (toolchain, standard, flags): every pair of axis values appears in at
  least one row. Each row's build is checked against a consumer built like
  it *and* the drawn consumer. The seed comes from
  ``ABICHECK_EDIT_CORPUS_SEED`` (default 0), so a run is reproducible from
  its seed.
- **weekly** (``weekly_cells``): every applicable operator under the
  newest selected toolchain of each family, with the exotic settings the
  nightly does not draw (``-m32``, newest standards, -fabi-version,
  ``_GLIBCXX_USE_CXX11_ABI=0``, mismatched consumer flags) and, on the base
  build, the full consumer cross: every selected consumer toolchain at the
  build's, the oldest and the newest standard it accepts.

Toolchains come from ``build_toolchains()`` / ``consumer_toolchains()``
(``ABICHECK_EDIT_CORPUS_TOOLCHAIN`` / ``..._CONSUMER_TOOLCHAIN``); a selected
name that is not installed raises at generation, before any cell runs.

Tiers select cells; they never decide outcomes. A row whose toolchain is not
installed still appears and is reported unavailable.

Coverage is computed over the drawn rows. A row is then *repaired* where it
would be meaningless (the standard raised to one the operator's features need,
a rebuild's target flag reset when the row already has it); pairs that are
infeasible by construction (a gcc-only flag on clang, ``-fpack-struct`` with
the ``std::string`` seed) are not covered by any runnable cell.
"""

from __future__ import annotations

import itertools
import os
import random
from collections.abc import Sequence
from dataclasses import dataclass, replace

from abiforge.model.config import (
    EVIDENCE,
    HEADER_BACKENDS,
    STANDARDS,
    Config,
    Consumer,
    Flags,
    Standard,
    Toolchain,
    default_config,
    has_feature,
)
from abiforge.model.seeds import SEED_BY_NAME, Seed
from abiforge.operators import OP_BY_NAME, EditOp, matrix
from abiforge.run.toolchains import (
    CONSUMER_TOOLCHAIN_ENV,
    TOOLCHAIN_ENV,
    build_toolchains,
    consumer_toolchains,
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
    ("rebuild_against_libcxx", "cxx_stdlib", "clang", {}),
    ("method_noexcept_add", "cxx_classes", "gcc", {}),
    ("concept_constraint_added", "cxx_classes", "clang", {"std": "c++20"}),
    ("rebuild_optimized", "c_core", "gcc", {}),
    # c_elf / cxx_more: a link-level edit from symbols alone, a C type edit
    # under DWARF, a C++ hierarchy edit under headers.
    ("version_script_dropped", "c_elf", "clang", {"evidence": "binary"}),
    ("union_member_retyped", "c_elf", "gcc", {"evidence": "dwarf"}),
    ("bases_reordered", "cxx_more", "gcc", {}),
    # c_elf_alt / cxx_alt: a reverse link edit from symbols alone, an
    # internal type reached through a public one under headers.
    ("executable_stack_removed", "c_elf_alt", "gcc", {"evidence": "binary"}),
    ("internal_type_grew", "cxx_alt", "clang", {}),
)


def _newest(family: str, toolchains: Sequence[Toolchain]) -> Toolchain | None:
    of = [t for t in toolchains if t.family == family]
    return of[-1] if of else None


def _toolchain(family: str) -> Toolchain | None:
    return _newest(family, build_toolchains())


def _missing(family: str, role: str) -> str:
    """Why no *role* toolchain of *family* is available: deselected or absent.

    Consumers follow ``CONSUMER_TOOLCHAIN_ENV`` when it is set, else the build
    selection (``consumer_toolchains``), so the reason names the variable
    that actually excluded the family.
    """
    names = (
        [CONSUMER_TOOLCHAIN_ENV, TOOLCHAIN_ENV]
        if role == "consumer"
        else [TOOLCHAIN_ENV]
    )
    for var in names:
        if os.environ.get(var, "").strip():
            return f"no selected {role} {family} toolchain ({var})"
    return f"no {role} {family} toolchain installed"


def pr_cells() -> list[Cell | tuple[str, str]]:
    """The PR tier; a ``(cell id, reason)`` pair where a toolchain is missing."""
    out: list[Cell | tuple[str, str]] = []
    for op_name, seed_name, family, changes in _PR:
        seed = SEED_BY_NAME[seed_name]
        tc = _toolchain(family)
        if tc is None:
            out.append((f"{op_name}-{seed_name}-{family}", _missing(family, "build")))
            continue
        cfg = default_config(seed.lang, tc)
        for key, value in changes.items():
            if key == "std":
                cfg = replace(cfg, std=next(s for s in STANDARDS if s.name == value))
            elif key == "consumer":
                other = _newest(str(value), consumer_toolchains())
                if other is None:
                    out.append(
                        (
                            f"{op_name}-{seed_name}-{family}",
                            _missing(str(value), "consumer"),
                        )
                    )
                    break
                cfg = replace(cfg, consumers=(Consumer(toolchain=other),))
            else:
                cfg = replace(cfg, **{key: value})
        else:
            out.append(Cell(OP_BY_NAME[op_name], seed, cfg))
    return out


# --- PR tier: build x consumer pairs -----------------------------------------

# seed -> (an edit that must break some consumer, one that must break none).
_PR_CARRIERS: dict[str, tuple[str, str]] = {
    "c_core": ("struct_append_field", "func_add"),
    "cxx_classes": ("virtual_reorder", "enum_class_append"),
}
_PR_CONSUMER_FACTORS: dict[str, list[object]] = {
    "build": ["gcc", "clang"],
    "consumer": ["same", "other"],
    "std": ["same", "oldest", "newest"],
    "seed": sorted(_PR_CARRIERS),
}
# A cell whose second consumer is on the other side of libstdc++'s dual ABI:
# that pair must be reported infeasible, never run and never passed.
_PR_DUAL_ABI = ("func_param_append", "cxx_stdlib", "gcc")


def pr_consumer_rows() -> list[dict[str, object]]:
    """The PR tier's pairwise array over (build family, consumer family, standard, seed)."""
    return pairwise(_PR_CONSUMER_FACTORS, seed=0)


def consumer_std(op: EditOp, lang: str, which: str, tc: Toolchain) -> Standard | None:
    """The *oldest*/*newest* standard of *lang* that *tc* accepts and that has
    every feature *op*'s edited header needs; None for ``same``."""
    if which == "same":
        return None
    options = [
        s
        for s in stds(lang)
        if all(has_feature(s, f) for f in op.features) and std_flag(tc, s) is not None
    ]
    if not options:
        return None
    return options[0] if which == "oldest" else options[-1]


def _pr_consumer_groups() -> dict[tuple[str, str], list[tuple[str, str]]]:
    groups: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for row in pr_consumer_rows():
        key = (str(row["build"]), str(row["seed"]))
        groups.setdefault(key, []).append((str(row["consumer"]), str(row["std"])))
    return dict(sorted(groups.items()))


def pr_consumer_ids() -> list[str]:
    """Stable test ids of ``pr_consumer_cells``, computed without probing a compiler."""
    ids = [
        f"{op}-{seed}-{family}-consumers"
        for family, seed in _pr_consumer_groups()
        for op in _PR_CARRIERS[seed]
    ]
    op_name, seed_name, family = _PR_DUAL_ABI
    return [*ids, f"{op_name}-{seed_name}-{family}-dual-abi"]


def pr_consumer_cells() -> list[Cell | tuple[str, str]]:
    """One library pair per (build family, seed), checked against all its consumers."""
    out: list[Cell | tuple[str, str]] = []
    users = consumer_toolchains()
    for (family, seed_name), specs in _pr_consumer_groups().items():
        seed = SEED_BY_NAME[seed_name]
        tc = _toolchain(family)
        for op_name in _PR_CARRIERS[seed_name]:
            ident = f"{op_name}-{seed_name}-{family}-consumers"
            if tc is None:
                out.append((ident, _missing(family, "build")))
                continue
            op = OP_BY_NAME[op_name]
            consumers: list[Consumer] = [Consumer()]
            missing = None
            for who, which in specs:
                fam = family if who == "same" else _other_family(family)
                user = _newest(fam, users)
                if user is None:
                    missing = _missing(fam, "consumer")
                    break
                std = consumer_std(op, seed.lang, which, user)
                consumers.append(
                    Consumer(toolchain=None if user == tc else user, std=std)
                )
            if missing:
                out.append((ident, missing))
                continue
            cfg = replace(default_config(seed.lang, tc), consumers=tuple(consumers))
            out.append(Cell(op, seed, cfg))
    op_name, seed_name, family = _PR_DUAL_ABI
    tc = _toolchain(family)
    if tc is None:
        out.append(
            (f"{op_name}-{seed_name}-{family}-dual-abi", _missing(family, "build"))
        )
    else:
        seed = SEED_BY_NAME[seed_name]
        cfg = replace(
            default_config(seed.lang, tc),
            consumers=(Consumer(), Consumer(flags=Flags(cxx11_abi=0))),
        )
        out.append(Cell(OP_BY_NAME[op_name], seed, cfg))
    return out


def _other_family(family: str) -> str:
    return "clang" if family == "gcc" else "gcc"


# --- pairwise covering array -----------------------------------------------

# Standard slots, mapped onto each language's list so that every standard of
# both languages is some slot's value (C: 5 standards, C++: 6).
_STD_SLOTS = max(sum(1 for s in STANDARDS if s.lang == lang) for lang in ("c", "c++"))


def _m32_buildable(toolchains: Sequence[Toolchain]) -> bool:
    from abiforge.run.toolchains import _flags_build

    return any(_flags_build(t, "c", Flags(bits=32)) is None for t in toolchains)


def _factors(
    toolchains: Sequence[Toolchain], users: Sequence[Toolchain] | None = None
) -> dict[str, list[object]]:
    """Every nightly axis. A value the host cannot build still appears and its
    cells are reported unavailable; -m32 is drawn only where some installed
    toolchain links it (otherwise every such row would be skipped).

    *toolchains* build libraries, *users* (default: the same) build consumers.
    """
    cells = [f"{op.name}@{seed.name}" for op, seed in matrix()]
    tc_ids = [t.id for t in toolchains] or ["none"]
    user_ids = [t.id for t in (toolchains if users is None else users)] or ["none"]
    return {
        "cell": cells,
        "producer": tc_ids,
        "consumer": ["same", *user_ids],
        "consumer_std": ["same", "oldest", "newest"],
        "std": list(range(_STD_SLOTS)),
        "opt": ["O0", "O2"],
        "visibility": ["default", "hidden"],
        "bits": [64, 32] if _m32_buildable(toolchains) else [64],
        "short_enums": [False, True],
        "pack": [None, 1],
        "cxx11_abi": [None, 0],
        "abi_version": [None, 11],
        "consumer_flags": ["same", "flip_short_enums"],
        "evidence": list(EVIDENCE),
        # Read only when evidence is headers (Config normalizes it otherwise).
        "header_backend": list(HEADER_BACKENDS),
    }


def std_for_slot(lang: str, slot: int) -> Standard:
    options = stds(lang)
    return options[min(slot * len(options) // _STD_SLOTS, len(options) - 1)]


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
    idx = options.index(std_for_slot(lang, slot))
    floor = 0
    for feat in op.features:
        floor = max(
            floor, next((i for i, s in enumerate(options) if has_feature(s, feat)), 0)
        )
    idx = max(idx, floor)
    while idx > floor and std_flag(tc, options[idx]) is None:
        idx -= 1
    return options[idx]


def _repair_rebuild(op: EditOp, flags: Flags) -> Flags:
    """Reset a flag a rebuild edit would set to the value the row already has.

    A rebuild row drawn with its target flag already on (``rebuild_short_enums``
    with ``short_enums=True``) is a no-op the operator rejects, which would
    drop every pair that row covers. Resetting just that flag keeps the rest of
    the row, so those pairs stay covered by a runnable cell.
    """
    if op.rebuild is None:
        return flags
    base = Flags()
    sets = op.rebuild(base)  # what the rebuild changes, read off the defaults
    reset = {
        name: getattr(base, name)
        for name in Flags.__dataclass_fields__
        if getattr(sets, name) != getattr(base, name)
        and getattr(flags, name) == getattr(sets, name)
    }
    return replace(flags, **reset) if reset else flags  # type: ignore[arg-type]


def row_to_cell(
    row: dict[str, object],
    toolchains: Sequence[Toolchain],
    users: Sequence[Toolchain] | None = None,
) -> Cell | None:
    op_name, seed_name = str(row["cell"]).split("@")
    op, seed = OP_BY_NAME[op_name], SEED_BY_NAME[seed_name]
    by_id = {t.id: t for t in toolchains}
    tc = by_id.get(str(row["producer"]))
    if tc is None:
        return None
    user_by_id = {t.id: t for t in (toolchains if users is None else users)}
    consumer = user_by_id.get(str(row["consumer"]))
    if consumer == tc:
        consumer = None
    lang = seed.lang
    flags = Flags(
        opt=str(row["opt"]),
        visibility=str(row["visibility"]),
        bits=int(row["bits"]),  # type: ignore[call-overload]
        short_enums=bool(row["short_enums"]),
        pack=row["pack"],  # type: ignore[arg-type]
        cxx11_abi=row["cxx11_abi"] if lang == "c++" else None,  # type: ignore[arg-type]
        abi_version=row["abi_version"] if tc.family == "gcc" else None,  # type: ignore[arg-type]
    )
    flags = _repair_rebuild(op, flags)
    consumer_flags = None
    if row["consumer_flags"] == "flip_short_enums":
        consumer_flags = replace(
            flags,
            short_enums=not flags.short_enums,
            abi_version=flags.abi_version if (consumer or tc).family == "gcc" else None,
        )
    drawn = Consumer(
        toolchain=consumer,
        std=consumer_std(
            op, lang, str(row.get("consumer_std", "same")), consumer or tc
        ),
        flags=consumer_flags,
    )
    cfg = Config(
        std=_repair_std(op, lang, int(row["std"]), tc),  # type: ignore[call-overload]
        producer=tc,
        flags=flags,
        # The build's own consumer and the drawn one: one library pair, two checks.
        consumers=(Consumer(), drawn) if drawn != Consumer() else (),
        evidence=str(row["evidence"]),
        header_backend=str(row.get("header_backend", "castxml")),
    )
    return Cell(op, seed, cfg)


def nightly_seed() -> int:
    return int(os.environ.get("ABICHECK_EDIT_CORPUS_SEED", "0"))


def nightly_cells(seed: int | None = None) -> list[Cell]:
    toolchains, users = build_toolchains(), consumer_toolchains()
    rows = pairwise(
        _factors(toolchains, users), nightly_seed() if seed is None else seed
    )
    cells = [row_to_cell(r, toolchains, users) for r in rows]
    return shard([c for c in cells if c is not None])


def shard_spec() -> tuple[int, int]:
    """``ABICHECK_EDIT_CORPUS_SHARD=K/N`` (1-based); default 1/1."""
    raw = os.environ.get("ABICHECK_EDIT_CORPUS_SHARD", "1/1")
    k, n = (int(x) for x in raw.split("/"))
    if not 1 <= k <= n:
        raise ValueError(f"ABICHECK_EDIT_CORPUS_SHARD={raw!r}: need 1 <= K <= N")
    return k, n


def shard(cells: list[Cell]) -> list[Cell]:
    """This shard's cells: every N-th cell from K-1, so shards partition the list."""
    k, n = shard_spec()
    return cells[k - 1 :: n]


def enabled_tiers() -> frozenset[str]:
    """Tiers ``ABICHECK_EDIT_CORPUS_TIER`` (comma-separated) asks to generate."""
    raw = os.environ.get("ABICHECK_EDIT_CORPUS_TIER", "")
    return frozenset(t.strip() for t in raw.split(",") if t.strip())


# --- weekly -----------------------------------------------------------------


def weekly_consumers(
    op: EditOp, lang: str, tc: Toolchain, users: Sequence[Toolchain]
) -> tuple[Consumer, ...]:
    """The full consumer cross: every consumer toolchain at the build's standard,
    the oldest and the newest it accepts for *op*'s edited header."""
    out: list[Consumer] = [Consumer()]
    for user in users:
        for which in ("same", "oldest", "newest"):
            std = consumer_std(op, lang, which, user)
            out.append(Consumer(toolchain=None if user == tc else user, std=std))
    return tuple(dict.fromkeys(out))


def _weekly_configs(
    lang: str, tc: Toolchain, consumers: tuple[Consumer, ...] = ()
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
        replace(base, consumers=consumers),
        replace(base, flags=Flags(bits=32)),
        replace(base, flags=Flags(opt="O2", visibility="hidden")),
        replace(base, flags=Flags(short_enums=True, pack=1)),
        replace(base, consumers=(Consumer(flags=Flags(short_enums=True)),)),
    ]
    if tc.family == "gcc":
        out.append(replace(base, flags=Flags(abi_version=11)))
    if lang == "c++":
        out.append(replace(base, flags=Flags(cxx11_abi=0)))
    # The baseline once more per non-reference header backend: every operator
    # under clang and the native gcc plugin, not only where the draw lands.
    out.extend(replace(base, header_backend=b) for b in HEADER_BACKENDS[1:])
    return out


def weekly_cells() -> list[Cell]:
    cells: list[Cell] = []
    toolchains, users = build_toolchains(), consumer_toolchains()
    for fam in sorted({t.family for t in toolchains}):
        tc = _newest(fam, toolchains)
        assert tc is not None
        for op, seed in matrix():
            cross = weekly_consumers(op, seed.lang, tc, users)
            for cfg in _weekly_configs(seed.lang, tc, cross):
                cells.append(Cell(op, seed, cfg))
    return shard(cells)
