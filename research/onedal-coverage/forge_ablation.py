"""Report catalogue-wide class ablation and bit-field witness retention as JSON.

Run from the repository root with PYTHONPATH=. python research/onedal-coverage/forge_ablation.py.
"""

import json
from collections import defaultdict

from abiforge.model import reduction
from abiforge.model.seeds import SEEDS
from abiforge.model.space import (
    edit_class,
    edit_classes_without_operator,
    op_constructs,
)
from abiforge.operators import OP_BY_NAME


def catalogue():
    owners = defaultdict(list)
    classes = {}
    for name, op in sorted(OP_BY_NAME.items()):
        classes[name] = [f"{c}/{edit_class(op, c)}" for c in op_constructs(op)]
        for key in classes[name]:
            owners[key].append(name)
    return {
        "represented_classes": len(owners),
        "unrepresented_split_classes": edit_classes_without_operator(),
        "operators": {
            name: {
                "classes_lost_on_removal": [k for k in keys if len(owners[k]) == 1],
                "other_operators_per_class": {
                    k: [other for other in owners[k] if other != name] for k in keys
                },
            }
            for name, keys in classes.items()
        },
    }


def equal_source_edits():
    """Find identical role changes on every applicable seed, excluding no-source edits."""
    groups = defaultdict(list)
    for name, op in sorted(OP_BY_NAME.items()):
        changes = []
        for seed in SEEDS:
            if op.applies_to(seed):
                delta = tuple(
                    sorted(
                        (k, v)
                        for k, v in op.edit(seed.roles).items()
                        if seed.roles[k] != v
                    )
                )
                changes.append((seed.name, delta))
        if any(delta for _, delta in changes):
            groups[tuple(changes)].append(name)
    return [names for names in groups.values() if len(names) > 1]


def bitfields():
    # Keep each configuration separate. A mutant observed with one compiler
    # cannot discharge a witness for the other compiler.
    footprints = {}
    for name, transition in reduction.SELECTED.items():
        observations = set()
        for cfg in reduction.CONFIGS:
            kind, shape = reduction.signature(transition, cfg)
            observations.add((str(cfg), kind, tuple(sorted(shape))))
            baseline = reduction.rows(transition, cfg)
            for fault, model in reduction.MUTANTS.items():
                if baseline != reduction.rows(transition, cfg, model):
                    observations.add((str(cfg), "fault", fault))
        footprints[name] = observations
    target = set.union(*footprints.values())
    owners = {
        item: sorted(n for n, items in footprints.items() if item in items)
        for item in target
    }
    best = list(footprints)

    def search(remaining, chosen):
        nonlocal best
        if not remaining:
            if len(chosen) < len(best):
                best = chosen
            return
        if len(chosen) + 1 >= len(best):
            return
        # Every cover must include an owner of this outstanding observation.
        options = min(
            (owners[item] for item in remaining), key=lambda group: (len(group), group)
        )
        for name in options:
            search(remaining - footprints[name], [*chosen, name])

    search(target, [])
    assert set.union(*(footprints[n] for n in best)) == target
    return {
        "selected": len(footprints),
        "observations": len(target),
        "minimum_representatives": sorted(best),
        "individually_indispensable": sorted(
            {group[0] for group in owners.values() if len(group) == 1}
        ),
    }


if __name__ == "__main__":
    print(
        json.dumps(
            {
                "catalogue": catalogue(),
                "equal_source_edits": equal_source_edits(),
                "bitfields": bitfields(),
            },
            indent=2,
        )
    )
