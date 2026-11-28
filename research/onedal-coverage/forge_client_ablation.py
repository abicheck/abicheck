"""Run Forge's frozen consumers and ablate consumer shapes for each registered seed."""

import argparse
import itertools
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory

from abiforge.model.config import default_config
from abiforge.model.seeds import SEEDS
from abiforge.operators import OP_BY_NAME
from abiforge.run.consumers import CONSUMERS
from abiforge.run.exec import _run, build_consumers
from abiforge.run.harness import CompileError, build_library, label_toolchain


def minimum_covers(matrix, shapes):
    detected = [set(hits) for hits in matrix.values() if hits]
    for size in range(len(shapes) + 1):
        covers = [
            group
            for group in itertools.combinations(sorted(shapes), size)
            if all(hits.intersection(group) for hits in detected)
        ]
        if covers:
            return covers
    raise AssertionError("the full consumer set must cover every observed break")


def measure(seed):
    compiler = label_toolchain("gcc")
    config = default_config(seed.lang, compiler)
    matrix, skipped, unstable = {}, {}, {}
    with TemporaryDirectory() as temp:
        root = Path(temp)
        old = build_library(seed, config, root / "old")
        clients = build_consumers(seed, config.consumer_set[0], old, root / "clients")
        for name, op in sorted(OP_BY_NAME.items()):
            if not op.applies_to(seed):
                continue
            reason = op.requires(config)
            if reason:
                skipped[name] = reason
                continue
            try:
                new = build_library(
                    seed,
                    op.new_side_config(config),
                    root / name,
                    overrides=op.edit(seed.roles),
                    flags=op.new_flags(config.flags),
                )
            except CompileError as exc:
                skipped[name] = str(exc).replace(temp, "WORK")[-800:]
                continue
            hits = []
            for shape, exe in clients.exes.items():
                control = clients.baseline[shape].key()
                trials = [_run(exe, new.dir) for _ in range(2)]
                if (
                    any(run.rc == -999 for run in trials)
                    or len({run.key() != control for run in trials}) != 1
                ):
                    unstable.setdefault(name, []).append(shape)
                elif trials[0].key() != control:
                    hits.append(shape)
            matrix[name] = sorted(hits)
    return seed.name, {
        "tested": matrix,
        "unavailable": skipped,
        "inconclusive": unstable,
        "minimum_covers": minimum_covers(matrix, clients.exes),
        "shapes": sorted(clients.exes),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    seeds = [seed for seed in SEEDS if seed.name in CONSUMERS]
    with ThreadPoolExecutor(max_workers=4) as pool:
        report = dict(pool.map(measure, seeds))
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
