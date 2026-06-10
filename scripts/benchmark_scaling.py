#!/usr/bin/env python3
"""Scaling benchmark for the abicheck comparison pipeline.

Real libraries can be large: ``libonedal_core.so`` exports ~10,550 functions.
The snapshot/``dump`` step scales fine (~5 s for that library); ``compare`` used
to blow up super-linearly on the post-processing detectors (surface scoping,
affected-symbol enrichment, namespace demangling, fingerprint rename matching).
Those paths are now fixed (see ``docs/development/performance.md``), and this
harness guards against regressions *without* needing a real binary, castxml, or
a compiler: it synthesises ``AbiSnapshot`` pairs of increasing size that
exercise each formerly-expensive path, times :func:`abicheck.checker.compare`,
and reports an empirical scaling exponent so a regression (or an improvement)
shows up as a single number.

It is intentionally **flexible**: by default it only measures and prints, so it
is safe to run unconditionally in CI as an informational job. Pass
``--max-seconds`` and/or ``--max-exponent`` to turn it into a gate once the
known bottlenecks are addressed and a stable budget exists.

Scenarios
---------
``add_remove``   Cheap baseline — functions added/removed, no type churn. This
                 is what ``tests/test_performance.py`` already covers; it stays
                 near-linear and is the control group.
``type_churn``   Every function takes a changed struct by pointer, so the
                 affected-symbol enrichment and opaque-type filters must relate
                 each type change back to the functions that use it. This is the
                 realistic hot path for a header-aware compare.
``elf_namespace`` ELF-only style: functions carry mangled (``_Z...``) names with
                 no qualified ``name``, forcing the namespace detectors to
                 demangle. Mirrors comparing stripped real libraries. Requires a
                 demangler (``c++filt`` / ``cxxfilt``); skipped if unavailable.

Usage
-----
    python3 scripts/benchmark_scaling.py
    python3 scripts/benchmark_scaling.py --scenario type_churn --sizes 1000 2000 4000
    python3 scripts/benchmark_scaling.py --json-out reports/scaling.json
    # Gating mode (opt-in):
    python3 scripts/benchmark_scaling.py --scenario type_churn --max-seconds 30
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent
if str(REPO_DIR) not in sys.path:
    sys.path.insert(0, str(REPO_DIR))

from abicheck.checker import compare  # noqa: E402
from abicheck.elf_metadata import (  # noqa: E402
    ElfMetadata,
    ElfSymbol,
    SymbolBinding,
    SymbolType,
)
from abicheck.model import (  # noqa: E402
    AbiSnapshot,
    Function,
    Param,
    RecordType,
    TypeField,
    Variable,
    Visibility,
)

DEFAULT_SIZES = (500, 1000, 2000, 4000)


# ── Snapshot builders (one per scenario) ──────────────────────────────────────
def _build_add_remove(n_funcs: int) -> tuple[AbiSnapshot, AbiSnapshot]:
    """Half the functions are removed, an equal number added. No type churn."""
    old_funcs = [
        Function(
            name=f"func_{i}",
            mangled=f"_Z6func_{i}v",
            return_type="int",
            visibility=Visibility.PUBLIC,
        )
        for i in range(n_funcs)
    ]
    new_funcs = old_funcs[: n_funcs // 2] + [
        Function(
            name=f"newfn_{i}",
            mangled=f"_Z6newfn_{i}v",
            return_type="int",
            visibility=Visibility.PUBLIC,
        )
        for i in range(n_funcs // 2)
    ]
    old = AbiSnapshot(library="libscale.so", version="1.0", functions=old_funcs)
    new = AbiSnapshot(library="libscale.so", version="2.0", functions=new_funcs)
    return old, new


def _build_type_churn(n_funcs: int) -> tuple[AbiSnapshot, AbiSnapshot]:
    """Every function takes a struct by pointer; every struct grows a field.

    Forces the affected-symbol enrichment and opaque/pointer-only filters to
    relate each of the ``n_types`` changed types back to the functions that
    reference it — the O(functions x types) path.
    """
    n_types = max(50, n_funcs // 20)
    types_old, types_new = [], []
    for i in range(n_types):
        base = [
            TypeField(name="a", type="int", offset_bits=0),
            TypeField(name="b", type="int", offset_bits=32),
        ]
        grown = base + [TypeField(name="c", type="int", offset_bits=64)]
        types_old.append(
            RecordType(name=f"Type_{i}", kind="struct", size_bits=64, fields=base)
        )
        types_new.append(
            RecordType(name=f"Type_{i}", kind="struct", size_bits=96, fields=grown)
        )
    funcs = []
    for i in range(n_funcs):
        t = f"Type_{i % n_types}"
        funcs.append(
            Function(
                name=f"use_{t}_{i}",
                mangled=f"_Z4use_{i}P{t}",
                return_type="int",
                params=[Param(name="p", type=f"{t} *")],
                visibility=Visibility.PUBLIC,
            )
        )
    old = AbiSnapshot(
        library="libscale.so", version="1.0", functions=list(funcs), types=types_old
    )
    new = AbiSnapshot(
        library="libscale.so", version="2.0", functions=list(funcs), types=types_new
    )
    return old, new


def _build_elf_namespace(n_funcs: int) -> tuple[AbiSnapshot, AbiSnapshot]:
    """ELF-only style: mangled names, no qualified ``name`` — forces demangling.

    Half the functions live in an ``experimental`` namespace so the namespace
    pattern detectors actually run. ``name`` is set equal to ``mangled`` to
    emulate a stripped library where only the mangled symbol is known.
    """

    def mangled(ns: str, i: int) -> str:
        # Valid Itanium nested-name mangling: _ZN<len>ns<len>leafEi, e.g.
        # _ZN12experimental5fn123Ei -> experimental::fn123(int). The index must
        # be inside the encoded identifier (and counted in its length) or the
        # name is invalid and c++filt leaves it unchanged, so the namespace
        # detectors would never see the `experimental` segment.
        leaf = f"fn{i}"
        return f"_ZN{len(ns)}{ns}{len(leaf)}{leaf}Ei"

    old_funcs, new_funcs = [], []
    for i in range(n_funcs):
        ns = "experimental" if i % 2 == 0 else "stablelib"
        m = mangled(ns, i)
        old_funcs.append(
            Function(name=m, mangled=m, return_type="int", visibility=Visibility.PUBLIC)
        )
        # New side keeps the same symbols plus a few removals to trigger work.
        if i % 17 != 0:
            new_funcs.append(
                Function(
                    name=m, mangled=m, return_type="int", visibility=Visibility.PUBLIC
                )
            )
    old = AbiSnapshot(library="libscale.so", version="1.0", functions=old_funcs)
    new = AbiSnapshot(library="libscale.so", version="2.0", functions=new_funcs)
    return old, new


def _build_var_churn(n_vars: int) -> tuple[AbiSnapshot, AbiSnapshot]:
    """Many public variables that all change type.

    Isolates the public-surface classification path, which recomputes set
    unions per change — quadratic in the number of findings regardless of
    types or functions.
    """
    old = AbiSnapshot(
        library="libscale.so",
        version="1.0",
        variables=[
            Variable(
                name=f"v{i}", mangled=f"v{i}", type="int", visibility=Visibility.PUBLIC
            )
            for i in range(n_vars)
        ],
    )
    new = AbiSnapshot(
        library="libscale.so",
        version="2.0",
        variables=[
            Variable(
                name=f"v{i}", mangled=f"v{i}", type="long", visibility=Visibility.PUBLIC
            )
            for i in range(n_vars)
        ],
    )
    return old, new


def _build_rename_churn(n_funcs: int) -> tuple[AbiSnapshot, AbiSnapshot]:
    """Stripped (ELF-only) library where every symbol is renamed.

    Old and new export disjoint, similarly-sized function symbols, so the
    fingerprint rename matcher's size gate admits the whole cross-product and
    the O(removed x added) name-similarity pass dominates — even when (as here)
    it ultimately reports no confident rename.
    """

    def elf(prefix: str) -> ElfMetadata:
        return ElfMetadata(
            soname="libscale.so",
            symbols=[
                ElfSymbol(
                    name=f"_Z3{prefix}v{i}",
                    binding=SymbolBinding.GLOBAL,
                    sym_type=SymbolType.FUNC,
                    # Spread sizes the way a real stripped library does, so the
                    # size-bucketed rename matcher behaves realistically (a few
                    # symbols per size) rather than all-collide in one bucket.
                    size=16 + (i * 7) % 4096,
                )
                for i in range(n_funcs)
            ],
        )

    old = AbiSnapshot(
        library="libscale.so", version="1.0", elf=elf("old"), elf_only_mode=True
    )
    new = AbiSnapshot(
        library="libscale.so", version="2.0", elf=elf("new"), elf_only_mode=True
    )
    return old, new


def _build_nested_types(n_types: int) -> tuple[AbiSnapshot, AbiSnapshot]:
    """Chain of embedded types (each embeds the previous) — every one changes.

    Stresses the transitive type-ancestor closure in affected-symbol
    enrichment: each type's ancestor set is rescanned against all functions and
    the per-ancestor function lists accumulate, so cost grows super-quadratically
    with the depth of the embedding graph. Capped at a small size by design.
    """
    types_old, types_new = [], []
    for i in range(n_types):
        prev = f"Type_{i - 1}" if i else "int"
        base = [
            TypeField(name="inner", type=prev, offset_bits=0),
            TypeField(name="a", type="int", offset_bits=64),
        ]
        grown = base + [TypeField(name="b", type="int", offset_bits=96)]
        types_old.append(
            RecordType(name=f"Type_{i}", kind="struct", size_bits=128, fields=base)
        )
        types_new.append(
            RecordType(name=f"Type_{i}", kind="struct", size_bits=160, fields=grown)
        )
    funcs = [
        Function(
            name=f"f_{i}",
            mangled=f"_Z3f_{i}P6Type_{i % n_types}",
            return_type="int",
            params=[Param(name="p", type=f"Type_{i % n_types} *")],
            visibility=Visibility.PUBLIC,
        )
        for i in range(n_types)
    ]
    old = AbiSnapshot(
        library="libscale.so", version="1.0", functions=list(funcs), types=types_old
    )
    new = AbiSnapshot(
        library="libscale.so", version="2.0", functions=list(funcs), types=types_new
    )
    return old, new


@dataclass
class Scenario:
    build: Callable[[int], tuple[AbiSnapshot, AbiSnapshot]]
    # Per-scenario default sweep. Some scenarios are intentionally pathological
    # (known super-linear paths) and must use a smaller sweep than the linear
    # control scenarios. An explicit ``--sizes`` overrides this.
    sizes: tuple[int, ...] = DEFAULT_SIZES
    # Hard safety cap: sizes above this are skipped even if requested via
    # ``--sizes``, so a known-pathological scenario can't be made to hang.
    max_size: int = 1_000_000
    # True if the scenario only does meaningful work with a demangler present.
    needs_demangler: bool = False


SCENARIOS: dict[str, Scenario] = {
    "add_remove": Scenario(_build_add_remove),
    "type_churn": Scenario(_build_type_churn),
    "elf_namespace": Scenario(_build_elf_namespace, needs_demangler=True),
    "var_churn": Scenario(_build_var_churn),
    # Quadratic paths — keep the sweeps small so a default run stays bounded.
    "rename_churn": Scenario(
        _build_rename_churn, sizes=(250, 500, 1000), max_size=1200
    ),
    "nested_types": Scenario(_build_nested_types, sizes=(100, 200, 400), max_size=500),
}


# ── Measurement ───────────────────────────────────────────────────────────────
@dataclass
class Point:
    size: int
    seconds: float
    changes: int


def _has_demangler() -> bool:
    if shutil.which("c++filt"):
        return True
    try:
        import cxxfilt  # noqa: F401

        return True
    except Exception:  # noqa: BLE001
        return False


def measure(scenario: str, sizes: list[int], repeat: int) -> list[Point]:
    spec = SCENARIOS[scenario]
    points: list[Point] = []
    for n in sizes:
        if n > spec.max_size:
            continue
        old, new = spec.build(n)
        best = math.inf
        changes = 0
        for _ in range(repeat):
            t0 = time.monotonic()
            result = compare(old, new)
            dt = time.monotonic() - t0
            best = min(best, dt)
            changes = len(result.changes)
        points.append(Point(size=n, seconds=round(best, 4), changes=changes))
    return points


def scaling_exponent(points: list[Point]) -> float | None:
    """Least-squares slope of log(seconds) vs log(size).

    ~1.0 means linear, ~2.0 means quadratic. Returns None if there are fewer
    than two usable (positive-time) points.
    """
    pts = [(math.log(p.size), math.log(p.seconds)) for p in points if p.seconds > 0]
    if len(pts) < 2:
        return None
    n = len(pts)
    sx = sum(x for x, _ in pts)
    sy = sum(y for _, y in pts)
    sxx = sum(x * x for x, _ in pts)
    sxy = sum(x * y for x, y in pts)
    denom = n * sxx - sx * sx
    if denom == 0:
        return None
    return (n * sxy - sx * sy) / denom


def tail_exponent(points: list[Point]) -> float | None:
    """Local log-log slope between the two largest sizes.

    Fixed per-run costs (imports, demangler warm-up) flatten the full-range
    least-squares fit at small sizes, hiding super-linear growth. The slope
    between the two largest points is a cleaner asymptotic signal, so the
    optional ``--max-exponent`` gate keys off this value.
    """
    usable = sorted((p for p in points if p.seconds > 0), key=lambda p: p.size)
    if len(usable) < 2:
        return None
    a, b = usable[-2], usable[-1]
    if a.size == b.size or a.seconds <= 0 or b.seconds <= 0:
        return None
    return math.log(b.seconds / a.seconds) / math.log(b.size / a.size)


# ── Reporting ─────────────────────────────────────────────────────────────────
def _classify(exponent: float | None) -> str:
    if exponent is None:
        return "n/a"
    if exponent < 1.3:
        return "linear"
    if exponent < 1.7:
        return "super-linear"
    return "≈quadratic+"


def _print_table(
    scenario: str, points: list[Point], exponent: float | None, tail: float | None
) -> None:
    print(f"\nScenario: {scenario}")
    print(f"  {'size':>8} {'changes':>9} {'seconds':>10} {'us/change':>11}")
    for p in points:
        per = (p.seconds / p.changes * 1e6) if p.changes else float("nan")
        print(f"  {p.size:>8} {p.changes:>9} {p.seconds:>10.3f} {per:>11.1f}")
    if exponent is not None:
        print(
            f"  full-range exponent (log-log fit): {exponent:.2f}  [{_classify(exponent)}]"
        )
    if tail is not None:
        print(f"  tail exponent (largest two sizes): {tail:.2f}  [{_classify(tail)}]")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--scenario",
        choices=[*SCENARIOS, "all"],
        default="all",
        help="Workload to run (default: all available)",
    )
    p.add_argument(
        "--sizes",
        type=int,
        nargs="+",
        default=None,
        help="Sizes to sweep, overriding each scenario's tuned default "
        f"(linear scenarios default to {' '.join(map(str, DEFAULT_SIZES))})",
    )
    p.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="Repetitions per size; the fastest run is kept (default: 1)",
    )
    p.add_argument(
        "--json-out",
        type=Path,
        default=None,
        help="Write the full result set as JSON to this path",
    )
    p.add_argument(
        "--max-seconds",
        type=float,
        default=None,
        help="GATE: fail if any single comparison exceeds this many seconds",
    )
    p.add_argument(
        "--max-exponent",
        type=float,
        default=None,
        help="GATE: fail if the log-log scaling exponent exceeds this value",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    scenarios = list(SCENARIOS) if args.scenario == "all" else [args.scenario]
    report: dict[str, object] = {
        "schema": "abicheck-scaling/1.0",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "sizes": args.sizes,
        "repeat": args.repeat,
        "scenarios": {},
    }
    failures: list[str] = []

    for scenario in scenarios:
        spec = SCENARIOS[scenario]
        if spec.needs_demangler and not _has_demangler():
            print(
                f"\nScenario: {scenario}  SKIP (no c++filt/cxxfilt demangler available)"
            )
            continue
        sizes = args.sizes if args.sizes is not None else list(spec.sizes)
        points = measure(scenario, sizes, args.repeat)
        if not points:
            print(
                f"\nScenario: {scenario}  SKIP (all requested sizes exceed its cap "
                f"of {SCENARIOS[scenario].max_size})"
            )
            continue
        exponent = scaling_exponent(points)
        tail = tail_exponent(points)
        _print_table(scenario, points, exponent, tail)
        report["scenarios"][scenario] = {  # type: ignore[index]
            "points": [asdict(p) for p in points],
            "exponent": exponent,
            "tail_exponent": tail,
        }

        if args.max_seconds is not None:
            worst = max(points, key=lambda p: p.seconds)
            if worst.seconds > args.max_seconds:
                failures.append(
                    f"{scenario}: {worst.seconds:.2f}s at size={worst.size} "
                    f"exceeds --max-seconds={args.max_seconds}"
                )
        if (
            args.max_exponent is not None
            and tail is not None
            and tail > args.max_exponent
        ):
            failures.append(
                f"{scenario}: tail scaling exponent {tail:.2f} "
                f"exceeds --max-exponent={args.max_exponent}"
            )

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, indent=2))
        print(f"\nWrote {args.json_out}")

    if failures:
        print("\nPERFORMANCE GATE FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
