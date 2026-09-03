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
    # `execute_dump_request` never calls `resolve_dependency_scope` itself
    # (that step is `_write_snapshot_output`'s own, dump-CLI-only concern —
    # see the module docstring) -- ground truth for the assertion below,
    # so a flip from "full" is genuine evidence that step ran on this
    # already-embedded snapshot, not a value it already carried in.
    assert snap.dependency_scope == "full"

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

    from abicheck.serialization import load_snapshot_document

    payload = load_snapshot_document(out_path)
    provenance = payload["dump_provenance"]
    assert provenance["requested_depth"] == "source"
    assert provenance["effective_depth"] == "source"
    assert provenance["degraded"] is False

    # The real L3/L4/L5 evidence from the resolve-time embed survived into
    # the written snapshot -- not silently dropped or replaced with an
    # empty/no-op pack by anything downstream of the guard. Checking mere
    # pack *presence* is not enough: this fixture's own docstring records
    # that even a headers-only run (no sources/build_info at all) produces
    # a non-null pack whose L3/L4 layers are NOT_COLLECTED. Checking only
    # the manifest's own precomputed coverage *labels* is not enough
    # either (Codex review, fresh evidence): `BuildSourcePack` serializes
    # its manifest independently of `build_evidence`/`source_abi`/
    # `source_graph`, so a regression that drops the real per-layer
    # payload while leaving those labels stale would still pass a
    # coverage-only check. Assert one representative, real fact out of
    # each layer's own serialized payload instead.
    build_source = payload["build_source"]
    coverage = {
        row["layer"]: row["status"] for row in build_source["manifest"]["coverage"]
    }
    assert coverage["L3_build"] == "present"
    assert coverage["L4_source_abi"] == "present"

    # The dependency-scope resolution step (`resolve_dependency_scope`,
    # `--include-system-declarations`'s off-by-default counterpart) is the
    # other half of this prerequisite's own name -- and it is genuinely
    # independent of the embed guard above: nothing before this call flips
    # `dependency_scope` off "full" (asserted above, straight off
    # `execute_dump_request`'s own result), so this is real evidence the
    # step ran on an already-embedded snapshot, not a value carried in
    # from resolution (Codex review, fresh evidence -- an earlier revision
    # of this test asserted only provenance/build_source facts, none of
    # which `resolve_dependency_scope` touches, so it would have passed
    # unchanged even with that call removed entirely).
    assert payload["dependency_scope"] == "filtered"

    # L3: the real compile unit this side's build evidence collected.
    compile_units = build_source["build_evidence"]["compile_units"]
    assert len(compile_units) == 1
    assert compile_units[0]["standard"] == "c++17"

    # L4: the source declaration actually linked to the binary's real
    # exported symbol -- not just that *a* mapping dict is present.
    mapping = build_source["source_abi"]["mappings"]["source_decl_to_binary_symbol"]
    assert mapping.get("_ZNK6Widget3sumEv") == "_ZNK6Widget3sumEv"

    # L5: the source graph carries real nodes, not an empty/degraded stub.
    assert len(build_source["source_graph"]["nodes"]) > 0


def _build_library_with_flow2_symbol(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Like :func:`_build_library`, plus one more exported symbol
    (``extern "C" int helper()``) that is defined only in the ``.cpp`` TU and
    declared in no header the resolve-time embed ever parses -- so the
    resolve-time L4 surface (seeded from ``widget.h`` alone) has no way to
    see it. A Flow-2 pack is the only thing that can supply source-level
    facts for it, which is what makes this fixture prove the *combination*
    rather than merely that the resolve-time facts survived unchanged.
    """
    header = tmp_path / "widget.h"
    header.write_text(
        "#pragma once\nstruct Widget { int x; int y; int sum() const; };\n",
        encoding="utf-8",
    )
    src = tmp_path / "widget.cpp"
    src.write_text(
        '#include "widget.h"\n'
        "int Widget::sum() const { return x + y; }\n"
        'extern "C" int helper() { return 42; }\n',
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
def test_write_snapshot_output_folds_a_flow2_inputs_pack_onto_a_resolve_time_embedded_snapshot(
    tmp_path: Path,
) -> None:
    """The other still-open half of PR 3A's reordering prerequisite (the
    plan doc's own words): does ``_write_snapshot_output``'s Flow-2
    ``--inputs`` fold (``embed_inputs_pack``) behave correctly when the
    snapshot it receives already carries a *resolve-time*-embedded
    ``build_source`` (produced by ``execute_dump_request``), rather than one
    ``_write_snapshot_output`` embedded itself?

    ``embed_inputs_pack`` calls ``_combine_packs(snap.build_source,
    ingested.build_source)`` -- i.e. the resolve-time-embedded pack is
    always ``_combine_packs``'s *first* argument (``bi_pack``), the ingested
    Flow-2 pack its *second* (``src_pack``). Per ``_combine_packs``'s own
    documented per-layer priority, that is NOT a per-fact merge: L3
    (``build_evidence``) prefers ``bi_pack`` first, so the resolve-time
    embed's own L3 facts win; L4/L5 (``source_abi``/``source_graph``)
    prefer ``src_pack`` first, so a Flow-2 pack supplying real L4 facts
    *replaces* the resolve-time embed's own L4 surface wholesale rather
    than merging with it -- consistent with a Flow-2 pack's whole design
    (a build's own wrapper-emitted, per-TU-authoritative facts, meant to
    supersede a redundant inline replay, not be unioned with it).

    This is not new behaviour to verify -- ``_combine_packs``'s per-layer
    priority is already covered directly (``test_merge_support.py`` and
    friends). What is specifically untested, and what this test closes, is
    whether *this exact combination* still holds when ``bi_pack`` is a
    snapshot produced by the *typed* pipeline
    (``execute_dump_request``) rather than one ``_write_snapshot_output``
    embedded itself -- the one shape this whole module exists to exercise.
    Proven with a Flow-2 pack supplying a declaration for a real exported
    symbol (``helper``) the resolve-time embed's own header-seeded L4
    surface has no way to see (it is declared in no header at all): the
    written snapshot must link ``helper`` (the Flow-2 replacement took
    effect) while still carrying the resolve-time embed's own real L3
    compile-unit facts (the one layer Flow-2 did not supply here, so
    ``bi_pack`` -- the resolve-time embed -- must still win it).

    L5 is **not** independently preserved in this scenario, and the test
    below proves that directly rather than assuming it (Codex review,
    PR #917: an earlier revision asserted only that the combined graph was
    non-empty, which passed regardless of which pack's graph won).
    ``ingest_inputs_pack`` builds ``source_abi``/``source_graph`` together
    from the same ``tus`` list whenever any TU is supplied -- a Flow-2 pack
    that replaces L4 (the whole point of this fixture) therefore always
    supplies a real, non-empty L5 graph too, and ``_combine_packs`` prefers
    ``src_pack`` for L5 exactly as it does for L4. So the resolve-time
    embed's own graph (17 nodes here, including a ``sum()`` declaration
    node) is replaced wholesale by Flow-2's own graph (built purely from
    ``tu``, containing only ``helper``), the same way its L4 surface is --
    confirmed below by asserting the ``sum()`` node is absent and the
    ``helper`` node is present in the final graph, not merely that the
    graph is non-empty.
    """
    from abicheck.api_types import DumpRequest, InputSpec
    from abicheck.buildsource import SourceAbiTu, SourceEntity, SourceLocation
    from abicheck.buildsource.inputs_emit import write_inputs_pack
    from abicheck.cli_buildsource import _write_snapshot_output
    from abicheck.compile_context import CompileContext
    from abicheck.service_dump_pipeline import (
        execute_dump_request,
        resolve_dump_request,
    )

    so_path, header, compile_db = _build_library_with_flow2_symbol(tmp_path)

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
    assert snap.build_source is not None

    # Ground truth: the resolve-time embed's own L4 surface, seeded only
    # from widget.h, has no way to have linked `helper` -- it is declared
    # in no header this run ever parsed. Anything below linking `helper`
    # is therefore genuinely attributable to the Flow-2 fold.
    assert snap.build_source.source_abi is not None
    pre_mapping = snap.build_source.source_abi.mappings.get(
        "source_decl_to_binary_symbol", {}
    )
    assert "helper" not in pre_mapping

    # Ground truth for the L5 check below: the resolve-time embed's own
    # real source graph, captured *before* the Flow-2 fold, so the later
    # assertion proves a specific node from *this* graph is gone rather
    # than merely asserting a property that would hold for any non-empty
    # graph. Derived from the graph itself (not a hardcoded mangled name)
    # so this stays robust to a differing Itanium mangling; asserting its
    # own presence here pins that the fixture still produces a `sum()`
    # declaration node to look for at all.
    assert snap.build_source.source_graph is not None
    pre_sum_node_ids = {
        n.id
        for n in snap.build_source.source_graph.nodes
        if n.kind == "source_decl" and "sum" in n.id
    }
    assert pre_sum_node_ids, "fixture no longer produces a sum() decl node"

    flow2_root = tmp_path / "abicheck_inputs"
    tu = SourceAbiTu(
        tu_id="cu://widget.cpp#cfg:flow2",
        target_id="target://libwidget",
        source="widget.cpp",
        public_header_roots=["widget.cpp"],
        functions=[
            SourceEntity(
                id="decl://helper",
                kind="function",
                qualified_name="helper",
                mangled_name="helper",
                signature_hash="sig-helper",
                source_location=SourceLocation(
                    path="widget.cpp", line=3, origin="PUBLIC_HEADER"
                ),
                visibility="public_header",
            )
        ],
    )
    write_inputs_pack(flow2_root, library="libwidget", tus=[tu])

    out_path = tmp_path / "out.json"
    _write_snapshot_output(
        snap,
        out_path,
        build_info=compile_db,
        sources=tmp_path,
        collect_mode="source-target",
        depth="source",
        header_roots=(header,),
        inputs_pack=flow2_root,
    )

    from abicheck.serialization import load_snapshot_document

    payload = load_snapshot_document(out_path)
    build_source = payload["build_source"]

    # L4: the Flow-2 pack's own facts won this layer wholesale (by design --
    # see the docstring above), so `helper` -- invisible to the resolve-time
    # embed -- is now linked.
    mapping = build_source["source_abi"]["mappings"]["source_decl_to_binary_symbol"]
    assert mapping.get("helper") == "helper"
    # And genuinely wholesale, not a merge: the resolve-time embed's own L4
    # fact for `sum()` did NOT survive the combination -- pinning the
    # documented per-layer priority itself, not just the positive case.
    assert "_ZNK6Widget3sumEv" not in mapping

    # L3: Flow-2 supplied no compile_db in this fixture, so `bi_pack` (the
    # resolve-time embed) must still win this layer -- its real compile
    # unit survives the combination untouched.
    compile_units = build_source["build_evidence"]["compile_units"]
    assert len(compile_units) == 1
    assert compile_units[0]["standard"] == "c++17"

    # L5: Flow-2's own graph (built alongside its L4 surface from the same
    # `tus`, per `ingest_inputs_pack`) wins this layer too -- confirmed
    # directly rather than by a non-empty check alone (Codex review,
    # PR #917: a Flow-2 pack that supplies any TU always supplies a
    # non-empty graph too, so `len(nodes) > 0` would pass whichever pack's
    # graph won). The resolve-time embed's own `sum()` decl node(s),
    # captured as ground truth above, must be gone; the Flow-2-only
    # `helper` decl node must be present.
    graph_node_ids = {n["id"] for n in build_source["source_graph"]["nodes"]}
    assert graph_node_ids.isdisjoint(pre_sum_node_ids)
    assert "decl://helper" in graph_node_ids


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
