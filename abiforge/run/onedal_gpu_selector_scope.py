# SPDX-License-Identifier: Apache-2.0
"""Check scoped parameter-selector removals against packaged oneDAL frontends."""

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from abiforge.checkers.abicheck import consumer_missing_symbol_probe

from .onedal_export_surface import validate_release_labels
from .onedal_selector_relocations import audit, image


def build(directory, names):
    directory.mkdir()
    provider = directory / "libselectors.so.1"
    source = "\n".join(f"void {name}(void) {{}}" for name in sorted(names))
    subprocess.run(
        [
            "gcc",
            "-x",
            "c",
            "-",
            "-shared",
            "-fPIC",
            "-Wl,-soname,libselectors.so.1",
            "-o",
            provider,
        ],
        input=source,
        text=True,
        check=True,
    )
    return provider


def build_consumer(work, baseline, names):
    declarations = [f"extern void {name}(void);" for name in sorted(names)]
    calls = [f"    {name}();" for name in sorted(names)]
    source = work / "consumer.c"
    source.write_text(
        "\n".join([*declarations, "int main(void) {", *calls, "    return 0;", "}"])
    )
    exe = work / "consumer"
    subprocess.run(["gcc", source, baseline, "-o", exe], check=True)
    return exe


def check_loader(exe, provider, missing):
    env = {
        "LC_ALL": "C",
        "LD_BIND_NOW": "1",
        "LD_LIBRARY_PATH": str(provider.parent.resolve()),
    }
    result = subprocess.run([exe.resolve()], env=env, capture_output=True, text=True)
    if missing:
        symbol = result.stderr.strip().rsplit("undefined symbol: ", 1)[-1]
        if result.returncode != 127 or symbol not in missing:
            raise ValueError(
                f"unexpected loader failure with {provider}: {result.stderr}"
            )
    elif result.returncode:
        raise ValueError(f"loader control failed with {provider}: {result.stderr}")
    return result.returncode


def run(prefix, work, device="gpu"):
    records = audit(prefix, device)
    if device == "gpu":
        frontend = image(prefix, "libonedal_dpc")
        control = image(prefix, "libonedal")
        if records[control.name]["relocation_types_by_selector"]:
            raise ValueError(f"unexpected GPU selector reference in {control}")
        return compare(frontend, work, records[frontend.name], control=control)
    rows = {}
    for stem in ("libonedal", "libonedal_dpc"):
        frontend = image(prefix, stem)
        directory = work / stem
        directory.mkdir()
        rows[frontend.name] = compare(frontend, directory, records[frontend.name])
    return rows


def compare(frontend, work, evidence, *, control=None):
    used = set(evidence["relocation_types_by_selector"])
    unused = set(evidence["selectors_without_named_relocation"])
    all_names = used | unused
    baseline = build(work / "baseline", all_names)
    consumer = build_consumer(work, baseline, used)
    check_loader(consumer, baseline, set())
    predict_missing = consumer_missing_symbol_probe(frontend, baseline)
    rows = {}
    for case, removed in (("referenced", used), ("unreferenced", unused)):
        candidate = build(work / case, all_names - removed)
        report = candidate.parent / "report.json"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "abicheck",
                "compare",
                baseline,
                candidate,
                "--used-by",
                frontend,
                *(["--used-by", control] if control else []),
                "-o",
                f"json={report}",
            ]
        )
        data = json.loads(report.read_text())
        consumers = {row["app"]: row for row in data["used_by"]}
        scoped = consumers[str(frontend)]
        missing = sorted(removed & used)
        if (
            result.returncode != 4
            or data["verdict"] != "BREAKING"
            or {
                c["symbol"]
                for c in data["changes"]
                if c["kind"].startswith("func_removed")
            }
            != removed
            or scoped["required_symbol_count"] != len(used)
            or sorted(scoped["missing_symbols"]) != missing
            or scoped["verdict"] != ("BREAKING" if missing else "COMPATIBLE")
        ):
            raise ValueError(f"unexpected {case} verdict: {scoped}")
        rows[case] = {
            "fixture_loader_exit": check_loader(consumer, candidate, missing),
            "removed_exports": len(removed),
            "library_verdict": data["verdict"],
            "consumer_verdict": scoped["verdict"],
            "required_symbol_count": len(used),
            "missing_symbols": missing,
        }
        individual = {}
        predicted_missing = []
        for index, name in enumerate(sorted(removed)):
            single = build(candidate.parent / f"single-{index}", all_names - {name})
            individual[name] = check_loader(consumer, single, used & {name})
            prediction = predict_missing(single)
            expected = [name] if individual[name] == 127 else []
            if prediction != expected:
                raise ValueError(
                    f"consumer prediction differs for {name}: {prediction}"
                )
            predicted_missing.extend(prediction)
        rows[case]["single_removal_predicted_missing"] = predicted_missing
        rows[case]["single_removal_loader_exits"] = individual
        if control:
            unaffected = consumers[str(control)]
            if (
                unaffected["verdict"] != "NO_CHANGE"
                or unaffected["required_symbol_count"] != 0
                or unaffected["relevant_change_count"] != 0
                or unaffected["missing_symbols"]
                or unaffected["missing_versions"]
            ):
                raise ValueError(f"unexpected zero-import verdict: {unaffected}")
            rows[case]["zero_import_control"] = {**unaffected, "app": control.name}
    return rows


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prefixes", nargs="+", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--device", choices=("cpu", "gpu"), default="gpu")
    args = parser.parse_args()
    validate_release_labels(args.prefixes)
    result = {}
    for prefix in args.prefixes:
        with tempfile.TemporaryDirectory() as tmp:
            result[prefix.name] = run(prefix, Path(tmp), args.device)
    args.out.write_text(json.dumps(result, indent=2) + "\n")
