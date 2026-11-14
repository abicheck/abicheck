"""Edit operators only dependency resolution can judge.

A shared library may leave an imported symbol undefined: the static linker
allows it (no ``-z defs``), and the dynamic loader looks for a provider in
the process. Whether one exists is not a fact about the library alone but
about its DT_NEEDED closure, so a checker sees it only when it resolves that
closure (``abicheck dump --follow-deps``). Every operator here needs a
``follow_deps`` config (``Config.follow_deps``).

Ground truth, from the loader: the seed's library resolves every import from
libc, so it is self-contained; the edit adds a call to a function nothing in
the closure defines. An application linked against the old library has no
copy of that symbol either, so the library no longer loads with immediate
binding and fails at the first call with lazy binding -- a break for every
existing consumer, not a risk.
"""

from __future__ import annotations

from abiforge.model.config import Config
from abiforge.model.opkit import Edit, EditOp, _set

__all__ = ["DEPENDENCY_OPERATORS", "needs_follow_deps"]


def needs_follow_deps(config: Config) -> str | None:
    """Applicability: the cell must resolve each side's DT_NEEDED closure."""
    if config.objfmt != "elf":
        return "DT_NEEDED closure resolution reads ELF dynamic sections"
    if config.follow_deps:
        return None
    return (
        "whether an import has a provider is a fact about the DT_NEEDED "
        "closure; needs a follow_deps config"
    )


def _extra(header: str, source: str) -> Edit:
    return lambda r: _set(r, "elf.extra", header, source)


DEPENDENCY_OPERATORS: tuple[EditOp, ...] = (
    EditOp(
        "import_left_unprovided",
        ("elf.extra",),
        _extra(
            "int kdep_use(int a);",
            "int kdep_missing(int a);\nint kdep_use(int a) { return kdep_missing(a) + 1; }",
        ),
        "kdep_use calls kdep_missing, which no library in the closure defines: "
        "the library links, but no existing process can load it with immediate "
        "binding",
        expected=frozenset({"IMPORTED_SYMBOL_UNPROVIDED"}),
        verdict_min="BREAKING",
        precondition=needs_follow_deps,
        touches=("elf.extra",),
        min_evidence="binary",
    ),
)
