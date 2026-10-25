# SPDX-License-Identifier: Apache-2.0
"""Harness H5 cell: the shared AST acquisition table equals no table at all.

Family F5 (``docs/contribute/plans/defect-family-harnesses.md`` § H5), for
bug class ``concurrency.caller_local_answer_published_to_shared_acquisition``.
A castxml header with a vector type is compared with the header graph on.
Two consumers then ask for one clang AST key in the same request: the
header-graph prefetch thread, whose ``derived_ast_scope`` takes a streamed
projection instead of the tree, and the castxml vector-spelling join, which
needs the tree. The table used to publish the prefetch's placeholder to the
join, and ``compare`` crashed in the forked side child with ``'object' object
has no attribute 'get'``.

The cell runs the real ``compare`` CLI with the table (the default) and in
reference mode (``ABICHECK_REFERENCE_MODE=1``: no table, every consumer
acquires its own AST) and requires the canonical JSON reports to be equal.
It forces the field order, with the join asking only once the prefetch has
settled, and proves each configuration ran the way it claims: a forked run
forked its sides, and an in-process run counts the derived offer taken.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from _family_f5_support import canonical_report, first_difference
from click.testing import CliRunner

from abicheck.cli import main

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(sys.platform != "linux", reason="gcc emits ELF on Linux only"),
    pytest.mark.skipif(
        not (shutil.which("castxml") and shutil.which("clang") and shutil.which("g++")),
        reason="needs castxml, clang and g++",
    ),
]

_HEADER = """#pragma once
namespace lib {
typedef %s V __attribute__((vector_size(32)));
struct State { V acc; int n; };
V scale(V v, V f);
void init(State* s);
}
"""
_SOURCE = """#include "api.h"
namespace lib {
V scale(V v, V f) { return v * f; }
void init(State* s) { s->acc = V{}; s->n = 0; }
}
"""


def _side(root: Path, element: str) -> tuple[Path, Path, Path]:
    """A library, its header and a one-entry compile database."""
    root.mkdir(parents=True)
    (root / "api.h").write_text(_HEADER % element)
    (root / "api.cpp").write_text(_SOURCE)
    lib = root / "libapi.so"
    cmd = ["g++", "-std=c++17", "-g", "-fPIC", "-w", "-Wno-psabi", "-c", "api.cpp"]
    built = subprocess.run(
        [*cmd[:-2], "-shared", "api.cpp", "-o", str(lib)],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert built.returncode == 0, built.stderr
    build = root / "build"
    build.mkdir()
    entry = {
        "directory": str(root),
        "file": str(root / "api.cpp"),
        "command": " ".join(cmd),
    }
    (build / "compile_commands.json").write_text(json.dumps([entry]))
    return lib, root / "api.h", build


def _force_join_after_prefetch(mp: pytest.MonkeyPatch, seen: dict[str, int]) -> None:
    """The field order, made deterministic: the vector join asks for the
    clang AST only once the header-graph prefetch has published its own.
    Installed before any fork, so a forked side child runs it too."""
    import abicheck.extract.headers.castxml.backend as castxml_backend
    import abicheck.workflows.dump.header_graph_prefetch as prefetch

    started: list[Any] = []
    real_prefetch = prefetch.prefetch_header_graph_ast
    real_join = castxml_backend.join_vector_spellings

    def recording_prefetch(*args: Any, **kwargs: Any) -> Any:
        handle = real_prefetch(*args, **kwargs)
        started.append(handle)
        return handle

    def join_after_prefetch(*args: Any, **kwargs: Any) -> Any:
        for handle in started:
            try:
                handle.future.result(timeout=300)
            except BaseException:  # noqa: BLE001 -- only its settling matters
                pass
        seen["joins_after_prefetch"] += bool(started)
        return real_join(*args, **kwargs)

    mp.setattr(prefetch, "prefetch_header_graph_ast", recording_prefetch)
    mp.setattr(castxml_backend, "join_vector_spellings", join_after_prefetch)


def _count_forks_and_offers(mp: pytest.MonkeyPatch, seen: dict[str, int]) -> None:
    import abicheck.dumper_clang_errors as clang_errors
    import abicheck.workflows.side_isolation as side_isolation

    real_fork = side_isolation.fork_child
    real_offer = clang_errors.offer_derived_ast_source

    def counting_fork(*args: Any, **kwargs: Any) -> Any:
        seen["forks"] += 1
        return real_fork(*args, **kwargs)

    def counting_offer(*args: Any, **kwargs: Any) -> Any:
        taken = real_offer(*args, **kwargs)
        seen["offers_taken"] += taken is not None
        return taken

    mp.setattr(side_isolation, "fork_child", counting_fork)
    mp.setattr(clang_errors, "offer_derived_ast_source", counting_offer)


def _compare(
    mp: pytest.MonkeyPatch, args: list[str], cache: Path, *, reference: bool
) -> tuple[int, str, dict[str, int]]:
    seen = {"forks": 0, "offers_taken": 0, "joins_after_prefetch": 0}
    with mp.context() as m:
        # A cold header cache, private to this configuration.
        m.setenv("XDG_CACHE_HOME", str(cache))
        # Stream the projection from even this small AST document: the size
        # gate is what kept every fixture off the path a real library takes.
        m.setenv("ABICHECK_HEADER_GRAPH_STREAM_MIN_MIB", "0")
        if reference:
            m.setenv("ABICHECK_REFERENCE_MODE", "1")
        else:
            m.delenv("ABICHECK_REFERENCE_MODE", raising=False)
        _force_join_after_prefetch(m, seen)
        _count_forks_and_offers(m, seen)
        result = CliRunner().invoke(main, ["compare", *args, "-o", "json=-"])
    assert result.exception is None or isinstance(result.exception, SystemExit), (
        result.exception,
        result.output[-3000:],
    )
    return result.exit_code, canonical_report(result.stdout), seen


@pytest.mark.parametrize("execution", ["forked-child", "in-process"])
def test_shared_acquisition_matches_reference_with_a_derived_consumer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, execution: str
) -> None:
    old_lib, old_h, old_build = _side(tmp_path / "old", "float")
    new_lib, new_h, new_build = _side(tmp_path / "new", "int")
    if execution == "in-process":
        monkeypatch.setenv("ABICHECK_SIDE_PROCESSES", "0")
    else:
        monkeypatch.delenv("ABICHECK_SIDE_PROCESSES", raising=False)
    args = [
        str(old_lib),
        str(new_lib),
        "--header",
        f"old={old_h}",
        "--header",
        f"new={new_h}",
        "--build-info",
        f"old={old_build}",
        "--build-info",
        f"new={new_build}",
    ]
    if execution == "forked-child":
        # `low-memory` resolves each side in its own forked child on Linux
        # whatever else runs in this process. The default profile forks two
        # concurrent children only when this process has a single thread
        # (`side_isolation.fork_is_safe`), which a pytest-xdist worker need
        # not, and then resolves in-process. The race under test is inside
        # the child either way: its prefetch thread against its castxml join.
        args += ["--performance-profile", "low-memory"]
    exit_opt, optimized, seen = _compare(
        monkeypatch, args, tmp_path / "cache-opt", reference=False
    )
    exit_ref, reference, _ = _compare(
        monkeypatch, args, tmp_path / "cache-ref", reference=True
    )
    if execution == "forked-child":
        assert seen["forks"] == 2, seen
    else:
        # Both sides here: the order was forced and the prefetch's scope took
        # the streamed projection -- the shape that used to be published.
        assert seen["forks"] == 0, seen
        assert seen["joins_after_prefetch"] == 2, seen
        assert seen["offers_taken"] == 2, seen
    # float x8 -> int x8: the same size, so only the joined vector spelling
    # sees it. Equal reports with a break prove the join read a real tree in
    # both configurations, not that both silently lost the vector.
    assert exit_opt == exit_ref == 4
    assert "vector_size(32)" in optimized
    assert optimized == reference, first_difference(optimized, reference)
