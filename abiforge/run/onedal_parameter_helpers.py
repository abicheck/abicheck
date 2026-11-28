# SPDX-License-Identifier: Apache-2.0
"""Check non-selector imports shared with the packaged parameter providers."""

import argparse
import json
import tempfile
from pathlib import Path

from abiforge.checkers.abicheck import (
    consumer_missing_symbol_probe,
    consumer_required_names,
)

from .onedal_export_surface import symbol_names, validate_release_labels
from .onedal_gpu_selector_scope import build, build_consumer, check_loader
from .onedal_selector_relocations import image


def run(prefix, work, candidates=()):
    results = {}
    for suffix in ("", "_dpc"):
        provider = image(prefix, f"libonedal_parameters{suffix}")
        frontend = image(prefix, f"libonedal{suffix}")
        shared = symbol_names(provider) & symbol_names(frontend, imports=True)
        required = consumer_required_names(frontend, provider)
        if required != shared:
            raise ValueError(f"abicheck and nm disagree for {frontend}: {required ^ shared}")
        selectors = {s for s in shared if "_parameters_" in s and "EEclE" in s}
        helpers = shared - selectors
        if not helpers:
            raise ValueError(f"no parameter helper imports in {frontend}")
        directory = work / frontend.name
        directory.mkdir()
        baseline = build(directory / "baseline", helpers)
        exe = build_consumer(directory, baseline, helpers)
        predict = consumer_missing_symbol_probe(frontend, baseline)
        baseline_exit = check_loader(exe, baseline, set())
        rows = {}
        for index, name in enumerate(sorted(helpers)):
            candidate = build(directory / str(index), helpers - {name})
            missing = predict(candidate)
            if missing != [name]:
                raise ValueError(f"unexpected missing imports for {name}: {missing}")
            rows[name] = check_loader(exe, candidate, missing)
        replacements = {}
        missing_from = consumer_missing_symbol_probe(frontend, provider)
        for candidate in candidates:
            if candidate == prefix:
                continue
            replacement = image(candidate, f"libonedal_parameters{suffix}")
            missing = missing_from(replacement)
            if set(missing) != required - symbol_names(replacement):
                raise ValueError(f"candidate import coverage differs: {replacement}")
            replacements[candidate.name] = {
                "provider": replacement.name,
                "missing_symbols": missing,
            }
        results[frontend.name] = {
            "candidate_releases": replacements,
            "provider": provider.name,
            "required_names": sorted(required),
            "matched_imports": len(shared),
            "selector_imports": len(selectors),
            "baseline_loader_exit": baseline_exit,
            "helper_removal_loader_exits": rows,
        }
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prefixes", nargs="+", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    validate_release_labels(args.prefixes)
    report = {}
    for prefix in args.prefixes:
        with tempfile.TemporaryDirectory() as tmp:
            report[prefix.name] = run(prefix, Path(tmp), args.prefixes)
    args.out.write_text(json.dumps(report, indent=2) + "\n")
