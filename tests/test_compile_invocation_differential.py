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


# ---------------------------------------------------------------------------
# L2 header-AST argv (``extract.headers.ast_config._build_clang_header_command``)
#
# The L2 header backend has no ``CompileUnit``: its include, sysroot and
# pass-through inputs arrive as the header-level arguments below. These rows pin
# the argv those inputs produce, recorded before the header renderer moved onto
# the compile-invocation model. Every case must stay byte-identical.
# ---------------------------------------------------------------------------

_HEADER_GOLDEN = _DATA / "clang_header_argv_golden.json"
_HEADER_AGG = Path("<abicheck-aggregate>.h")

#: Each case is the keyword arguments of ``_build_clang_header_command`` (other
#: than the fixed driver, include list and aggregate path, which are passed
#: positionally). Chosen to cover the include-dedup rule, the ``-isystem``
#: ordering, sysroot/nostdinc, C vs C++ language selection, SYCL host/multi
#: context, and the clang-cl driver.
_HEADER_CASES: dict[str, dict[str, Any]] = {
    "plain-c": {
        "clang_bin": "clang",
        "cc_id": "gnu",
        "extra_includes": ["/src/include"],
    },
    "cxx20-forced": {
        "clang_bin": "clang++",
        "cc_id": "gnu",
        "extra_includes": ["/src/include"],
        "force_cpp": True,
        "force_cpp20": True,
    },
    "cxx-explicit-std": {
        "clang_bin": "clang++",
        "cc_id": "gnu",
        "gcc_options": "-std=c++17",
        "force_cpp": True,
        "force_cpp20": True,
    },
    "gcc-option-duplicates-include": {
        "clang_bin": "clang",
        "cc_id": "gnu",
        "extra_includes": ["/src/include"],
        "gcc_option_tokens": ("-I", "/src/include", "-DFOO=1"),
    },
    "gcc-option-joined-include-and-iquote": {
        "clang_bin": "clang",
        "cc_id": "gnu",
        "extra_includes": ["/src/include"],
        "gcc_option_tokens": ("-I/src/include", "-iquote", "/q", "-iquote/q2"),
    },
    "sysroot-nostdinc-system-includes": {
        "clang_bin": "clang",
        "cc_id": "gnu",
        "sysroot": "/sdk",
        "nostdinc": True,
        "system_includes": ("/usr/include",),
    },
    "system-includes-with-user-isystem": {
        "clang_bin": "clang",
        "cc_id": "gnu",
        "gcc_option_tokens": ("-isystem", "/usr/include"),
        "system_includes": ("/usr/include", "/opt/inc"),
    },
    "intel-sycl-host-context": {
        "clang_bin": "icpx",
        "cc_id": "gnu",
        "dpcpp_host_context": True,
    },
    "intel-sycl-multi-context": {
        "clang_bin": "icpx",
        "cc_id": "gnu",
        "dpcpp_multi_context": True,
    },
    "stock-clang-sycl-host-context": {
        "clang_bin": "clang++",
        "cc_id": "gnu",
        "dpcpp_host_context": True,
    },
    "clang-cl-msvc": {
        "clang_bin": "clang-cl",
        "cc_id": "msvc",
        "extra_includes": ["C:/inc"],
    },
}


def _render_header_case(kwargs: dict[str, Any]) -> list[str]:
    from abicheck.extract.headers.ast_config import _build_clang_header_command

    args = dict(kwargs)
    clang_bin = args.pop("clang_bin")
    cc_id = args.pop("cc_id")
    extra_includes = [Path(p) for p in args.pop("extra_includes", [])]
    if "sysroot" in args:
        args["sysroot"] = Path(args["sysroot"])
    return _build_clang_header_command(
        clang_bin, cc_id, extra_includes, _HEADER_AGG, **args
    )


def render_header_corpus() -> dict[str, list[str]]:
    """Render every L2 header-AST case, keyed by its case name."""
    return {name: _render_header_case(kwargs) for name, kwargs in _HEADER_CASES.items()}


def regenerate_header_golden() -> None:
    """Rewrite the L2 header golden from the current code. Review the diff before committing."""
    _HEADER_GOLDEN.write_text(
        json.dumps(render_header_corpus(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def test_header_argv_matches_pre_change_golden() -> None:
    golden = json.loads(_HEADER_GOLDEN.read_text(encoding="utf-8"))
    assert render_header_corpus() == golden
