# SPDX-License-Identifier: Apache-2.0
"""Run each frozen parameter client on all supplied Linux release packages."""

import argparse
import json
import re
import subprocess
import tempfile
from collections import Counter
from functools import cache
from pathlib import Path

from abiforge.checkers.abicheck import (
    consumer_missing_symbol_probe,
    elf_release_surface,
)

from .onedal_covariance_parameters import CAMPAIGNS, build_client
from .onedal_elf_extraction import dynamic_symbols, version_requirements
from .onedal_export_surface import symbol_names, validate_release_labels
from .onedal_selector_relocations import image


def run(prefixes, work, *, import_profiles=None):
    validate_release_labels(prefixes)

    @cache
    def packaged_tags(path):
        requirements, _ = version_requirements(path)
        return {(lib, tag) for lib, tags in requirements.items() for tag in tags}

    candidate_exports = cache(symbol_names)
    matrix = {}
    for sdk in prefixes:
        soname = image(sdk, "libonedal_parameters").name
        major = int(soname.rsplit(".", 1)[1])
        clients = {}
        for algorithm in CAMPAIGNS:
            exe = build_client(sdk, work, algorithm, major)
            tags = subprocess.check_output(["readelf", "-dW", exe], text=True)
            expected = re.findall(r"\(NEEDED\).*?\[([^\]]+)\]", tags)
            surface = elf_release_surface(exe)
            if import_profiles is not None:
                profile = tuple(sorted(entry for entry in surface.imports if entry[2]))
                import_profiles.setdefault(profile, []).append(
                    f"{sdk.name}/{algorithm}"
                )
            direct = surface.needed
            requirements, indices = version_requirements(exe)
            _, imports = dynamic_symbols(exe, indices)
            if imports != Counter(surface.imports):
                raise ValueError(
                    f"ELF import names, bindings or versions differ: {exe}"
                )
            if requirements != {
                lib: sorted(tags) for lib, tags in surface.versions_required.items()
            }:
                raise ValueError(f"ELF version requirements differ: {exe}")
            if direct != expected:
                raise ValueError(f"DT_NEEDED differs for {exe}: {direct} vs {expected}")
            needed = [name for name in direct if name.startswith("libonedal")]
            if soname not in needed:
                raise ValueError(f"missing direct dependency on {soname} in {exe}")
            runs = {}
            bindings_by_release = {}
            for runtime in prefixes:
                lib = runtime.resolve() / "lib"
                missing = [name for name in needed if not (lib / name).is_file()]
                env = {"LC_ALL": "C", "LD_BIND_NOW": "1", "LD_LIBRARY_PATH": str(lib)}
                if not missing:
                    env["LD_DEBUG"] = "bindings"
                result = subprocess.run([exe], env=env, capture_output=True, text=True)
                if missing:
                    diagnostic = f"{missing[0]}: cannot open shared object file"
                    if result.returncode != 127 or diagnostic not in result.stderr:
                        raise ValueError(
                            f"unexpected loader outcome: {runtime}, {exe}, {result.stderr}"
                        )
                elif result.returncode:
                    raise ValueError(
                        f"client failed on {runtime}: {exe}, {result.returncode}"
                    )
                else:
                    pattern = (
                        rf"binding file {re.escape(str(exe))} \[0\] to {re.escape(str(lib))}/"
                        r"(libonedal[^/ ]+) \[0\]: normal symbol `([^']+)'"
                    )
                    imported_names = {entry[0] for entry in surface.imports}
                    bindings = sorted(
                        {
                            (dll, name)
                            for dll, name in re.findall(pattern, result.stderr)
                            if name in imported_names
                        }
                    )
                    if not bindings:
                        raise ValueError(
                            f"no direct oneDAL import bindings for {exe} on {runtime}"
                        )
                    bindings_by_release[runtime.name] = bindings
                runs[runtime.name] = {
                    "exit": result.returncode,
                    "absent_dependencies": missing,
                }
            bindings = bindings_by_release[sdk.name]
            if any(observed != bindings for observed in bindings_by_release.values()):
                raise ValueError(
                    f"oneDAL import bindings changed across same-major runs for {exe}"
                )
            frontend = image(sdk, "libonedal")
            frontend_names = {name for provider, name in bindings if provider == frontend.name}
            predict_missing = consumer_missing_symbol_probe(exe, frontend)
            missing_by_candidate = {}
            for runtime in prefixes:
                candidate = image(runtime, "libonedal")
                missing_names = predict_missing(candidate)
                if set(missing_names) != frontend_names - candidate_exports(candidate):
                    raise ValueError(f"missing frontend imports differ: {exe}, {candidate}")
                missing_by_candidate[runtime.name] = missing_names
            packaged = [name for name in direct if (sdk / "lib" / name).is_file()]
            dependency_tags = set().union(
                *(packaged_tags(sdk / "lib" / name) for name in packaged)
            )
            extra_imports = sorted(
                (provider, version, name)
                for name, _, version, provider in surface.imports
                if version and (provider, version) not in dependency_tags
            )
            clients[algorithm] = {
                "missing_frontend_imports_by_candidate": missing_by_candidate,
                "direct_onedal_bindings": bindings,
                "compared_package_libraries": packaged,
                "imports_with_client_only_version_tags": extra_imports,
                "version_requirements": requirements,
                "versioned_import_count": sum(
                    1 for _, _, version, _ in surface.imports if version
                ),
                "direct_dependencies": direct,
                "needed": needed,
                "runs": runs,
            }
        matrix[sdk.name] = clients
    return matrix


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prefixes", nargs="+", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--import-profiles",
        type=Path,
        help="group clients with identical versioned imports",
    )
    args = parser.parse_args()
    profiles = {} if args.import_profiles else None
    with tempfile.TemporaryDirectory() as tmp:
        result = run(args.prefixes, Path(tmp), import_profiles=profiles)
    if profiles is not None:
        rows = [
            {"clients": sorted(clients), "imports": entries}
            for entries, clients in sorted(profiles.items())
        ]
        args.import_profiles.write_text(json.dumps(rows, indent=2) + "\n")
    args.out.write_text(json.dumps(result, indent=2) + "\n")
