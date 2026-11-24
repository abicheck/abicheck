# SPDX-License-Identifier: Apache-2.0

"""Model an unchanged ELF consumer in two deployment roots for release reports."""

from pathlib import Path

from ..binder import SymbolBinding
from ..errors import ReleaseOperandUsageError
from ..stack_checker import check_single_env
from .elf_dependency_resolver import DependencyGraph


def consumer_copies(old: Path, new: Path, client: Path) -> tuple[Path, Path]:
    """Locate the same client inside the two deployment images."""
    source = client.resolve()
    if not old.is_dir() or not new.is_dir() or not source.is_relative_to(old.resolve()):
        raise ReleaseOperandUsageError(
            "--used-by is not supported for directory/package comparisons unless "
            "the client is inside the old deployment directory and both operands are directories"
        )
    candidate = (new / source.relative_to(old.resolve())).resolve()
    if (
        not candidate.is_relative_to(new.resolve())
        or not candidate.is_file()
        or not source.is_file()
    ):
        raise ReleaseOperandUsageError(
            "--used-by needs a client inside both deployment roots"
        )
    with source.open("rb") as before, candidate.open("rb") as after:
        if before.read(4) != b"\x7fELF" or after.read(4) != b"\x7fELF":
            raise ReleaseOperandUsageError("--used-by deployment client must be ELF")
        while chunk := before.read(1 << 20):
            if chunk != after.read(len(chunk)):
                break
        else:
            if not after.read(1):
                return source, candidate
    raise ReleaseOperandUsageError("Both roots must contain the identical old consumer")


def release_consumer_binding(
    old: Path,
    new: Path,
    client: Path,
    ld_library_path: str = "",
    search_paths: list[Path] | None = None,
) -> dict[str, object]:
    copies = consumer_copies(old, new, client)
    baseline, result = [
        check_single_env(
            path,
            search_paths=search_paths,
            sysroot=root.resolve(),
            ld_library_path=ld_library_path,
        )
        for path, root in zip(copies, (old, new))
    ]

    def imports(
        bindings: list[SymbolBinding], root: Path, graph: DependencyGraph
    ) -> dict[tuple[str, str], dict[str, str | None]]:
        return {
            (b.symbol, b.version): {
                "status": b.status.value,
                "provider": b.provider,
                "search_reason": graph.nodes[b.provider].resolution_reason
                if b.provider is not None and b.provider in graph.nodes
                else None,
            }
            for b in bindings
            if b.consumer == str(root)
        }

    before = imports(baseline.bindings_candidate, copies[0], baseline.candidate_graph)
    after = imports(result.bindings_candidate, copies[1], result.candidate_graph)
    return {
        "client": str(copies[0].relative_to(old.resolve())),
        "loadability": result.loadability.value,
        "unresolved_dependencies": result.candidate_graph.unresolved,
        "imports": [
            {
                "symbol": name,
                "version": version,
                "old": before.get((name, version)),
                "new": after.get((name, version)),
            }
            for name, version in sorted(before.keys() | after.keys())
        ],
    }


def release_consumer_bindings(
    old: Path,
    new: Path,
    clients: tuple[Path, ...],
    ld_library_path: str,
    search_paths: tuple[Path, ...],
) -> dict[str, object] | None:
    bindings = [
        release_consumer_binding(old, new, client, ld_library_path, list(search_paths))
        for client in dict.fromkeys(clients)
    ]
    if not bindings:
        return None
    if len(bindings) > 1:
        bindings[0]["additional_consumers"] = bindings[1:]
    return bindings[0]
