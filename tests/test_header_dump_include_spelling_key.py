# SPDX-License-Identifier: Apache-2.0
"""Bug class: one header set, two ``-I`` spellings, two acquisition keys.

A relative ``-I`` root on one caller and an absolute one on another gave the
same header parse two AST-cache/acquisition keys, so a compare parsed each
side twice and the header-graph attach fell back to streaming the whole AST
off disk (#1402's release-surface absolutization reached the primary pass
but not ``service_header_graph_attach``). The invariant is stated at the
choke point both parsers share, for *every* spelling of a root, so the next
caller that forgets to absolutize cannot split the key again.
"""

from __future__ import annotations

import itertools
import os
import shutil
from pathlib import Path

import pytest

from abicheck import dumper
from abicheck.extract.headers.clang import backend as clang_backend


class _KeyCaptured(Exception):
    pass


def _spellings(root: Path, cwd: Path) -> list[Path]:
    rel = Path(os.path.relpath(root, cwd))
    return [
        root,
        rel,
        Path(".") / rel,
        rel / "sub" / "..",
        Path("..") / cwd.name / rel,
    ]


def _key_for(monkeypatch: pytest.MonkeyPatch, owner, fn, headers, includes) -> str:
    seen: list[str] = []

    def _capture(key: str, *a, **k):
        seen.append(key)
        raise _KeyCaptured

    monkeypatch.setattr(owner, "_cache_path", _capture)
    with pytest.raises(_KeyCaptured):
        fn(headers, includes, "c++")
    return seen[0]


@pytest.mark.parametrize(
    "owner,fn_name,tool",
    [
        (clang_backend, "clang_header_dump", "clang++"),
        (dumper, "_castxml_dump", "castxml"),
    ],
)
def test_acquisition_key_is_independent_of_include_spelling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    owner: object,
    fn_name: str,
    tool: str,
) -> None:
    if shutil.which(tool) is None:
        pytest.skip(f"{tool} not available")
    if fn_name == "_castxml_dump":
        try:
            dumper._resolve_gated_castxml_bin(None)
        except Exception as exc:  # outside the version policy
            pytest.skip(f"castxml rejected by version policy: {exc}")
    fn = getattr(owner, fn_name)
    work = tmp_path / "work"
    roots = [work / "include", work / "include" / "svs" / "lib"]
    for r in roots:
        (r / "sub").mkdir(parents=True)
    header = roots[1] / "api.h"
    header.write_text("int f(void);\n")
    monkeypatch.chdir(work)

    oracle = _key_for(monkeypatch, owner, fn, [header], list(roots))
    variants = list(itertools.product(*(_spellings(r, work) for r in roots)))
    assert len(variants) == 25
    keys = {_key_for(monkeypatch, owner, fn, [header], list(v)) for v in variants}
    assert keys == {oracle}

    # Vacuity guard: the key must still distinguish a genuinely different root.
    other = work / "other"
    other.mkdir()
    assert _key_for(monkeypatch, owner, fn, [header], [roots[0], other]) != oracle
