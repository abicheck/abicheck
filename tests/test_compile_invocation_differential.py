"""Differential pin for the compile-invocation model (parent plan P1; L4/L2 plan Phase 3).

The compile-command argv that L4 replay and the L5 AST passes run is built
from a ``CompileUnit`` by ``clang_command``. Phase 3 moves that construction
onto one parsed invocation model. The model must not change behaviour, so this
module renders the clang AST-dump and macro argv (and the replay directory) for
every unit of a representative compile database, and compares the result with
a golden file recorded **before** the refactor.

The corpus goes through the production adapter (``CompileDbAdapter``), so the
``CompileUnit`` fields are the ones a real ``dump --sources`` run produces, not
hand-built fixtures. It covers GNU and clang drivers, clang-cl, ``-I``,
``-isystem``, ``-iquote``, ``-idirafter``, ``-D``/``-U``, ``-include``,
``-std`` (including a conflicting pair), ``-x`` language forcing, a response
file, a relative directory with an ``env -C`` prefix, a cleared environment,
``--sysroot``/``--target``/``-isysroot``, a code-loading option, and the
SYCL host-only rules (accepted under Intel, a clean error under stock clang).

To regenerate after a deliberate, reviewed change: run
``regenerate_golden()`` from this module and review the diff.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from abicheck.buildsource.adapters.compile_db import CompileDbAdapter
from abicheck.buildsource.source_extractors.base import SourceExtractionError
from abicheck.buildsource.source_extractors.castxml import build_castxml_command
from abicheck.buildsource.source_extractors.clang_command import (
    build_clang_command,
    build_clang_macro_command,
    compile_unit_invocation,
)

_DATA = Path(__file__).parent / "data" / "compile_invocation"
_CORPUS = _DATA / "compile_commands.json"
_GOLDEN = _DATA / "clang_argv_golden.json"

#: The three AST-dump drivers a unit is replayed under: the default ``clang``,
#: an explicit ``clang++``, and ``icpx`` (Intel's SYCL driver, which exercises
#: the host-only append). All reach ``clang_command`` with the same
#: ``compiler_binary=None``, so only the driver token differs.
_CLANG_BINS = ("clang", "clang++", "icpx")


def _render_unit(cu: Any) -> dict[str, Any]:
    source, run_directory = compile_unit_invocation(cu)
    row: dict[str, Any] = {
        "source": str(source),
        "run_directory": str(run_directory),
        "castxml": build_castxml_command(cu, source, Path("out.xml")),
    }
    for clang_bin in _CLANG_BINS:
        try:
            row[f"ast[{clang_bin}]"] = build_clang_command(
                cu, source, clang_bin=clang_bin
            )
            row[f"macro[{clang_bin}]"] = build_clang_macro_command(
                cu, source, clang_bin=clang_bin
            )
        except SourceExtractionError as exc:
            row[f"error[{clang_bin}]"] = f"SourceExtractionError: {exc}"
    return row


def render_corpus() -> dict[str, dict[str, Any]]:
    """Render every compile unit of the corpus, keyed by its ``source`` spelling."""
    evidence = CompileDbAdapter(_CORPUS).collect()
    rows: dict[str, dict[str, Any]] = {}
    for cu in evidence.compile_units:
        rows[cu.source] = _render_unit(cu)
    return rows


def regenerate_golden() -> None:
    """Rewrite the golden file from the current code. Review the diff before committing."""
    _GOLDEN.write_text(
        json.dumps(render_corpus(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def test_corpus_covers_every_unit_the_plan_names() -> None:
    rows = render_corpus()
    assert len(rows) == 16
    assert all("ast[clang]" in row or "error[clang]" in row for row in rows.values())


def test_rendered_clang_argv_matches_pre_change_golden() -> None:
    golden = json.loads(_GOLDEN.read_text(encoding="utf-8"))
    assert render_corpus() == golden
