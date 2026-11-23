"""Model an unchanged ELF consumer in two deployment roots for release reports."""

from filecmp import cmp
from pathlib import Path

import click

from ...binder import SymbolBinding
from ...stack_checker import check_stack


def release_consumer_binding(old: Path, new: Path, client: Path) -> dict[str, object]:
    if (
        client.is_absolute()
        or ".." in client.parts
        or not all(r.is_dir() for r in (old, new))
    ):
        raise click.UsageError(
            "--release-consumer needs two deployment directories and a root-relative ELF path"
        )
    copies = [(r / client).resolve() for r in (old, new)]
    for root, path in zip((old, new), copies):
        if not path.is_relative_to(root.resolve()) or not path.is_file():
            raise click.UsageError(
                f"Consumer {client} must exist inside both deployment roots"
            )
        with path.open("rb") as stream:
            if stream.read(4) != b"\x7fELF":
                raise click.UsageError(f"Consumer {client} must be ELF")
    if not cmp(*copies, shallow=False):
        raise click.UsageError("Both roots must contain the identical old consumer")
    result = check_stack(client, old.resolve(), new.resolve())

    def imports(
        bindings: list[SymbolBinding], root: Path
    ) -> dict[tuple[str, str], dict[str, str | None]]:
        return {
            (b.symbol, b.version): {"status": b.status.value, "provider": b.provider}
            for b in bindings
            if b.consumer == str(root)
        }

    before = imports(result.bindings_baseline, copies[0])
    after = imports(result.bindings_candidate, copies[1])
    return {
        "client": str(client),
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


def current_consumer_binding() -> dict[str, object] | None:
    context = click.get_current_context(silent=True)
    return context.meta.get("release_consumer_binding") if context else None
