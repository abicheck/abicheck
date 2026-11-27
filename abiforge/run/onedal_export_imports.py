# SPDX-License-Identifier: Apache-2.0
"""Link address relocations for every named export in pinned Linux x86-64 SDKs."""

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

from .onedal_export_surface import symbol_names


def link_imports(provider, directory):
    names = symbol_names(provider)
    if not all(name.isascii() and name.isidentifier() for name in names):
        raise ValueError(f"assembler probe needs plain symbol names: {provider}")
    source = directory / "references.s"
    source.write_text(
        '.section .data.rel.ro,"aw"\n'
        + "".join(f".quad {name}\n" for name in sorted(names))
        + '.section .note.GNU-stack,"",@progbits\n'
    )
    consumer = directory / "consumer.so"
    subprocess.run(
        [
            "gcc",
            "-shared",
            "-nostdlib",
            "-Wl,-z,defs,--allow-shlib-undefined",
            source,
            provider,
            "-o",
            consumer,
        ],
        check=True,
    )
    imports = symbol_names(consumer, imports=True)
    return {
        "exports": len(names),
        "matched_imports": len(names & imports),
        "without_import": sorted(names - imports),
        "unexpected_imports": sorted(imports - names),
    }


def audit(prefix, directory):
    providers = sorted({p.resolve() for p in (prefix / "lib").glob("libonedal*.so*")})
    if not providers:
        raise ValueError(f"missing oneDAL ELF images in {prefix}")
    return {path.name: link_imports(path, directory) for path in providers}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prefix", nargs="+", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as temp:
        reports = {p.name: audit(p, Path(temp)) for p in args.prefix}
    args.out.write_text(json.dumps(reports, sort_keys=True, indent=2) + "\n")
