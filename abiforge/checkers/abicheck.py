"""The abicheck adapter: the only abiforge module that imports abicheck.

abiforge's core (the space model, the edit operators, their declared ground
truth, the compiler/execution oracle and the coverage accounting) is written
from the C/C++ language and platform ABI rules, not from any checker. A
checker under test plugs in through an adapter that answers four questions:

* ``snapshot`` -- what the checker records for one built library;
* ``answer`` -- its verdict and finding kinds for an old/new pair;
* ``kind_catalog`` -- the finding vocabulary it can report (for coverage);
* ``header_backend_problem`` -- why one of its header backends cannot run here.

The operators' expectations are spelled in abicheck's ``ChangeKind``
vocabulary today; a second checker needs a translation of that vocabulary
(see ``abiforge/README.md`` "Checking other tools").

``tests/test_abiforge_boundaries.py`` fails when any other abiforge module
imports abicheck, or when abiforge imports ``tests`` or ``pytest``.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator, Sequence
from pathlib import Path

#: The verdict an adapter returns when the checker refuses to compare.
REFUSED = "REFUSED"

#: Run the checker's full public dump workflow instead of its library entry
#: point (record-layout enrichment included).
PUBLIC_DUMP_ENV = "ABICHECK_EDIT_CORPUS_PUBLIC_DUMP"


def snapshot(
    so: Path,
    headers: Sequence[Path],
    *,
    version: str,
    is_cxx: bool,
    backend: str,
    gcc_options: str | None,
) -> object:
    """abicheck's snapshot of one built library, made the way users get it."""
    from abicheck.compile_context import CompileContext
    from abicheck.dumper import dump

    headers = list(headers)
    lang = "c++" if is_cxx else "c"
    if os.environ.get(PUBLIC_DUMP_ENV):
        # The public dump workflow end to end (``run_dump``), record-layout
        # enrichment included.
        from abicheck.service import run_dump

        snap = run_dump(
            so,
            "elf",
            headers,
            version=version,
            lang=lang,
            lang_explicit=True,
            frontend=backend,
            compile=CompileContext(
                gcc_options=gcc_options if headers else None, frontend=backend
            ),
        )
    else:
        snap = dump(
            so,
            headers,
            version=version,
            compiler="c++" if is_cxx else "cc",
            lang=lang,
            header_backend=backend,
            gcc_options=gcc_options if headers else None,
            # ``abicheck dump -H`` tags each header it is given public
            # (ADR-015 provenance); the bare ``dump()`` only does when told,
            # and without it the cross-source checks (public-not-exported,
            # exported-not-public) never run.
            public_headers=headers or None,
        )
        if headers and backend == "clang":
            # The public dump workflow (``abicheck dump``/``compare``) enriches
            # a clang-backend snapshot with record layout after ``dump()``; the
            # bare ``dumper.dump()`` does not (known-gaps "`dumper.dump()` never
            # runs the clang record-layout pass"). Mirror the workflow, so the
            # corpus judges what users get.
            from abicheck.extract.headers.clang.layout_tool import (
                attach_clang_layout,
            )

            snap = attach_clang_layout(
                snap,
                headers,
                [],
                lang=lang,
                compile=CompileContext(gcc_options=gcc_options),
            )
    if headers:
        # ``abicheck dump -H`` takes the headers it parses as the library's
        # public-header set and classifies every declaration's origin against
        # it (``finish_provenance``); the bare ``dumper.dump()`` leaves origin
        # unknown, so a declared-but-unexported entry has no public-contract
        # fact and its gained export reads as a plain addition. Mirror it.
        from abicheck.workflows.snapshot_factory import finish_provenance

        snap = finish_provenance(snap, headers, None)
    return snap


def answer(old: object, new: object) -> tuple[str, frozenset[str]]:
    """(verdict name, ChangeKind names); ``REFUSED`` plus the error type on refusal.

    A refusal (``abicheck.errors.AbicheckError``, e.g. a profile mismatch) is
    abicheck's answer, not a harness failure: it is neither clean nor a break.
    """
    from abicheck.checker import compare
    from abicheck.errors import AbicheckError

    try:
        result = compare(old, new)  # type: ignore[arg-type]
    except AbicheckError as exc:
        return REFUSED, frozenset({type(exc).__name__})
    return result.verdict.name, frozenset(c.kind.name for c in result.changes)


def kind_names() -> tuple[str, ...]:
    """Every finding kind abicheck can report (``ChangeKind`` names)."""
    from abicheck.model.change_catalog.kinds import ChangeKind

    return tuple(k.name for k in ChangeKind)


def kind_catalog() -> Iterator[tuple[str, str, str]]:
    """(name, value, taxonomy module) for every ``ChangeKind``.

    The taxonomy module is the catalog file that declares the kind
    (``build``, ``platform``, ``symbols``, ``types``, ...), which the coverage
    report uses to place a kind in a scope bucket.
    """
    from abicheck.model.change_catalog import (
        build,
        disposition,
        platform,
        source,
        source_graph_risk,
        symbols,
        types,
    )
    from abicheck.model.change_catalog.kinds import ChangeKind

    modules = {
        "build": build.BUILD_ENTRIES,
        "disposition": disposition.DISPOSITION_ENTRIES,
        "platform": platform.PLATFORM_ENTRIES,
        "source": source.SOURCE_ENTRIES,
        "source_graph_risk": source_graph_risk.SOURCE_GRAPH_RISK_ENTRIES,
        "symbols": symbols.SYMBOLS_ENTRIES,
        "types": types.TYPES_ENTRIES,
    }
    module_of = {e.kind: mod for mod, entries in modules.items() for e in entries}
    for k in ChangeKind:
        yield k.name, k.value, module_of[k.value]


def header_backend_problem(backend: str, driver: str, lang: str) -> str | None:
    """Why abicheck's *backend* header backend cannot run on this host, or None.

    Only the native gcc plugin needs a probe here (it must be built for the
    installed gcc); the other backends are checked by the caller.
    """
    if backend != "gcc":
        return None
    from abicheck.errors import SnapshotError
    from abicheck.extract.headers.gcc.discovery import find_plugin

    try:
        find_plugin(driver, lang)
    except (SnapshotError, OSError, subprocess.SubprocessError) as exc:
        return f"gcc header backend: {str(exc).splitlines()[0]}"
    return None
