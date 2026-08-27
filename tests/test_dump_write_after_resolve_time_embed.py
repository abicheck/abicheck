# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""CLI cleanup phase two, PR 3A: is ``_write_snapshot_output``'s own
provenance/``--inputs``/depth-gate sequence actually safe to receive a
snapshot that was already embedded at *resolve* time, the way
``execute_dump_request`` does it, rather than embedded by
``_write_snapshot_output`` itself?

The plan doc (``docs/contribute/plans/cli-cleanup-phase-two.md``, PR 3A/3C)
names this as a still-open prerequisite for routing the real ``dump`` CLI
run onto ``execute_dump_request`` -- "the write-time provenance/`--inputs`/
depth-gate sequence in `cli_buildsource._write_snapshot_output` [needs] to
be reordered around a resolve-time embed". ``build_source_already_satisfies``
(PR 3A blocker 5, sub-issue 3) already stops a *second* embed from running,
tested against a hand-stubbed ``BuildSourcePack`` in
``tests/test_dump_embed_idempotence.py`` -- but nothing exercises the
*rest* of ``_write_snapshot_output``'s sequence (the G21.7 warning, the
depth gate, the dependency-scope resolution, the provenance fold) against a
snapshot that arrived pre-embedded through the *real* typed pipeline,
end to end.

This module answers that question directly, using ``--ast-frontend clang``
(the same substitute this whole test suite already uses everywhere castxml
is unavailable) so the header parse itself never invokes castxml. This
module is still ``integration``-marked, though, and
``tests/conftest.py``'s ``_integration_skip_reason`` gates every
``integration``-marked test on ``shutil.which("castxml")`` alone -- it
cannot tell that a given test never calls it -- so *some* castxml binary
must still be discoverable on ``PATH`` for this module to run at all, even
though the test itself is castxml-free. Build a ``DumpRequest``, run it
through the real
``resolve_dump_request``/``execute_dump_request`` split (exactly the shape
a migrated ``perform_elf_dump`` would produce), and hand the resulting,
already-embedded snapshot to ``_write_snapshot_output`` with an *explicit*
``--depth`` — the one CLI-only check
(``check_requested_depth_satisfied``/``DumpDepthNotSatisfiedError``) that
``execute_dump_request``'s own ``enforce_requested_depth`` does not
replace, since the two raise different exception types for different
callers.

Both depth checks are provably not independent implementations that could
disagree -- ``check_requested_depth_satisfied`` (``cli_dump_helpers.py``)
uses ``_DEPTH_RANK = DEPTH_RANK`` and ``_gated_source_label`` (a documented
compatibility alias for ``evidence_depth.gated_source_label``), the exact
same shared primitives ``enforce_requested_depth``
(``workflows/artifact/execute.py``) uses -- so running both in sequence is
redundant, not risky. This module is what turns that reading of the code
into a real, executed assertion instead of an argument.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not sys.platform.startswith("linux"),
        reason="ELF/Linux-scoped repro (real g++-compiled .so + compile_commands.json)",
    ),
]

_HAVE_GXX = shutil.which("g++") is not None
_HAVE_CLANG = shutil.which("clang") is not None
_SKIP_REASON = "needs a real g++ and clang toolchain"


def _build_library(tmp_path: Path) -> tuple[Path, Path, Path]:
    """A tiny real C++ library plus a matching ``compile_commands.json``.

    Deliberately local rather than imported from a sibling module -- see
    ``tests/test_dump_embed_idempotence.py``'s identical builder for the
    convention this follows.
    """
    header = tmp_path / "widget.h"
    header.write_text(
        "#pragma once\nstruct Widget { int x; int y; int sum() const; };\n",
        encoding="utf-8",
    )
    src = tmp_path / "widget.cpp"
    src.write_text(
        '#include "widget.h"\nint Widget::sum() const { return x + y; }\n',
        encoding="utf-8",
    )
    so_path = tmp_path / "libwidget.so"
    subprocess.run(
        ["g++", "-std=c++17", "-shared", "-fPIC", "-o", str(so_path), str(src)],
        check=True,
        capture_output=True,
    )
    compile_db = tmp_path / "compile_commands.json"
    compile_db.write_text(
        json.dumps(
            [
                {
                    "directory": str(tmp_path),
                    "arguments": [
                        "g++",
                        "-std=c++17",
                        "-fPIC",
                        "-c",
                        str(src),
                        "-o",
                        "widget.o",
                    ],
                    "file": str(src),
                }
            ]
        ),
        encoding="utf-8",
    )
    return so_path, header, compile_db


@pytest.mark.skipif(not (_HAVE_GXX and _HAVE_CLANG), reason=_SKIP_REASON)
def test_write_snapshot_output_accepts_a_resolve_time_embedded_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exact shape a migrated ``perform_elf_dump`` would hand to
    ``_write_snapshot_output``: a snapshot already embedded and already
    depth-floor-checked by ``execute_dump_request``, an explicit
    ``--depth source`` given again (as the real CLI always does), and
    ``sources``/``build_info`` forwarded again too (as the real CLI call
    site does today, unconditionally, regardless of which path produced
    the snapshot).

    Asserts every step ``_write_snapshot_output`` still performs after the
    embed guard skips its own embed call:

    * the guard genuinely skips a second embed (spied), not just "the
      pack looks non-empty" — the same property
      ``test_dump_embed_idempotence.py`` pins against a stub, now pinned
      against the real typed pipeline's own output;
    * the depth gate does not raise, even though *this* call never ran
      the embed that reached "source" depth;
    * the provenance fold reports ``effective_depth == "source"`` and
      ``degraded is False`` — computed from ``snap.build_source`` alone,
      so it does not care when that pack was populated;
    * the written JSON round-trips and still carries the real L3/L4
      evidence the resolve-time embed produced (not an empty pack).
    """
    from abicheck.api_types import DumpRequest, InputSpec
    from abicheck.cli_buildsource import _write_snapshot_output
    from abicheck.compile_context import CompileContext
    from abicheck.service_dump_pipeline import (
        execute_dump_request,
        resolve_dump_request,
    )

    so_path, header, compile_db = _build_library(tmp_path)

    request = DumpRequest(
        input=InputSpec(
            path=so_path,
            headers=(header,),
            sources=tmp_path,
            build_info=compile_db,
            compile=CompileContext(frontend="clang"),
        ),
        depth="source",
    )
    resolved = resolve_dump_request(request)
    result = execute_dump_request(resolved)
    snap = result.snapshot

    # Ground truth: the resolve-time embed really did reach "source" depth,
    # and the depth floor already passed inside execute_dump_request itself
    # (it would have raised ValidationError otherwise) -- so anything this
    # test observes below is about _write_snapshot_output's own behaviour,
    # not about whether the fixture reached the depth it claims to.
    assert snap.build_source is not None
    assert result.effective_depth == "source"

    import abicheck.cli_buildsource as cli_buildsource

    embed_calls: list[Any] = []
    monkeypatch.setattr(
        cli_buildsource,
        "embed_build_source",
        lambda *a, **kw: embed_calls.append((a, kw)),
    )

    out_path = tmp_path / "out.json"
    _write_snapshot_output(
        snap,
        out_path,
        build_info=compile_db,
        sources=tmp_path,
        collect_mode="source-target",
        depth="source",
        header_roots=(header,),
    )

    # No second embed -- the exact property PR 3A blocker 5 sub-issue 3
    # exists to guarantee, now exercised against a real, non-stubbed pack.
    assert embed_calls == []

    payload = json.loads(out_path.read_text(encoding="utf-8"))
    provenance = payload["dump_provenance"]
    assert provenance["requested_depth"] == "source"
    assert provenance["effective_depth"] == "source"
    assert provenance["degraded"] is False

    # The real L3/L4/L5 evidence from the resolve-time embed survived into
    # the written snapshot -- not silently dropped or replaced with an
    # empty/no-op pack by anything downstream of the guard. Checking mere
    # pack *presence* is not enough: this fixture's own docstring records
    # that even a headers-only run (no sources/build_info at all) produces
    # a non-null pack whose L3/L4 layers are NOT_COLLECTED -- so the real
    # assertion is that every layer this specific request actually
    # requested (L3 build, L4 source-ABI replay) reports "present", not
    # merely that a `build_source` key exists.
    coverage = {
        row["layer"]: row["status"]
        for row in payload["build_source"]["manifest"]["coverage"]
    }
    assert coverage["L3_build"] == "present"
    assert coverage["L4_source_abi"] == "present"


@pytest.mark.skipif(not (_HAVE_GXX and _HAVE_CLANG), reason=_SKIP_REASON)
def test_write_snapshot_output_still_raises_for_a_genuinely_unreached_depth(
    tmp_path: Path,
) -> None:
    """The redundant depth check is not merely harmless -- it is still a
    real gate. A resolve-time result that never parsed any headers at all
    (no ``-H``, no ``--sources``/``--build-info`` -- ``depth="binary"``, a
    pure ELF-only dump) must still be rejected by ``_write_snapshot_output``
    when the CLI's own explicit ``--depth source`` is passed through
    unchanged, exactly as it would be if the embed had never run at all.

    Deliberately *not* a headers-only, no-sources shape (an earlier revision
    of this test used one): the header-graph attach pass alone already
    populates ``snap.build_source`` with a real (if L3/L4-empty) pack the
    moment any header is parsed, an independent discovery worth recording
    here rather than only in the commit that found it -- so "no build/source
    evidence" needs the parse itself to never run, not just L3/L4 to be
    empty.
    """
    from abicheck.api_types import DumpRequest, InputSpec
    from abicheck.cli_buildsource import _write_snapshot_output
    from abicheck.cli_dump_helpers import DumpDepthNotSatisfiedError
    from abicheck.service_dump_pipeline import (
        execute_dump_request,
        resolve_dump_request,
    )

    so_path, _header, _compile_db = _build_library(tmp_path)

    request = DumpRequest(input=InputSpec(path=so_path), depth="binary")
    resolved = resolve_dump_request(request)
    result = execute_dump_request(resolved)
    snap = result.snapshot
    assert snap.build_source is None

    with pytest.raises(DumpDepthNotSatisfiedError):
        _write_snapshot_output(
            snap,
            tmp_path / "out.json",
            depth="source",
        )
