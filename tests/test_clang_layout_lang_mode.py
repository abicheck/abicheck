# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""The clang layout pass follows the primary parse's C/C++ decision.

Bug class: a secondary pass over the same headers re-derives the language
from header text alone (a ``.h`` suffix reads as C) although the primary
parse already resolved C++ from evidence the pass lacks (the export table's
mangled names). Over C++ headers a C-mode ``-fdump-record-layouts`` dumps
nothing, so on a binary without DWARF every record kept ``size_bits=None``
and a public record's growth compared clean.

The oracle is the real compiler's ``sizeof`` (``tests/_compiler_sizeof.py``,
object-format neutral), not the layout reader itself.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from _compiler_sizeof import compiler_sizeof

from abicheck.extract.headers.clang.layout_tool import run_layout_tool

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("clang++") is None, reason="needs clang++"),
]

_CPP_ONLY = {
    "namespace": "namespace ns {{ struct R {{ {body} }}; }}\n",
    "class-access": "namespace ns {{ class R {{ public: {body} }}; }}\n",
    "inline-ns": "namespace ns {{ inline namespace v1 {{ struct R {{ {body} }}; }} }}\n",
}
_BODIES = ("int a;", "int a; long b;", "char c[3]; short s;", "double d; char e;")


def _headers(root: Path, private: str) -> Path:
    """``api.h`` whose only C++ is in the private header it includes -- the
    shape the header-text guess cannot see, so the decision has to come from
    the primary parse (as in ``test_contract_type_identities_integration``)."""
    (root / "priv.h").write_text(private, encoding="utf-8")
    api = root / "api.h"
    api.write_text('#include "priv.h"\n', encoding="utf-8")
    return api


def _probe(header: Path, mode: str | None) -> list[dict] | None:
    return run_layout_tool(
        None,
        [header],
        [],
        compiler="c++",
        lang=None,
        probe_names=["ns::R"],
        resolved_lang_mode=mode,
    )


@pytest.mark.parametrize("body", _BODIES)
@pytest.mark.parametrize("shape", sorted(_CPP_ONLY))
def test_cpp_decision_is_followed_for_a_dot_h_header(
    tmp_path: Path, shape: str, body: str
) -> None:
    header = _headers(tmp_path, _CPP_ONLY[shape].format(body=body))
    records = _probe(header, "c++")
    assert records, "the C++ decision must reach the layout pass"
    sizes = {r["size_bits"] for r in records if r.get("qualified_name") == "ns::R"}
    oracle = compiler_sizeof("clang++", "c++", header, "ns::R", workdir=tmp_path)
    assert sizes == {oracle * 8}


@pytest.mark.parametrize("shape", sorted(_CPP_ONLY))
def test_without_a_decision_the_dot_h_guess_stays_c(tmp_path: Path, shape: str) -> None:
    # Negative control: the fix follows a recorded decision; it does not
    # promote every header to C++ on its own.
    header = _headers(tmp_path, _CPP_ONLY[shape].format(body="int a;"))
    assert _probe(header, None) is None
    assert _probe(header, "c") is None
