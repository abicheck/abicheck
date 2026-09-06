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

"""ADR-063 Track T4 ("Dump request contract"), item 1's second still-open
clause: a real *execution* variant for a binary-less
(``InputSpec.path is None``) :class:`~abicheck.api_types.DumpRequest`.

Before this change, :func:`~abicheck.service_dump_pipeline.execute_dump_request`
raised ``ValidationError`` unconditionally for this shape even though
:func:`~abicheck.service_dump_pipeline.resolve_dump_request` has always fully
resolved it -- the only pipeline that could actually produce the snapshot was
:func:`~abicheck.cli_buildsource.dump_source_only`'s own CLI-only path,
embedding L3-L5 evidence inline with no typed request at all.

These tests state the general contract (not just one hand-picked example):
a source-only :class:`~abicheck.api_types.DumpRequest`, executed through
:func:`~abicheck.service_dump_pipeline.execute_dump_request`, produces a
snapshot with the same L3 evidence -- and the same "no evidence at all is a
usage error" / "an unreached requested depth floor is a hard error"
behavior -- that :func:`~abicheck.cli_buildsource.dump_source_only` produces
for the equivalent CLI inputs.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from abicheck.api_types import DumpRequest, InputSpec
from abicheck.errors import ValidationError


def _source_tree_with_compile_db(tmp_path: Path) -> Path:
    """A minimal source tree carrying its own ``compile_commands.json`` --
    enough for L3 (build) evidence with no compiler/castxml/clang needed
    (mirrors ``tests/test_build_source_cli.py::test_dump_source_only_no_binary``,
    which this file's parity test is a typed-API counterpart to)."""
    tree = tmp_path / "src"
    tree.mkdir()
    cdb = [
        {
            "directory": str(tree),
            "file": "foo.cpp",
            "arguments": ["c++", "-std=c++17", "-c", "foo.cpp"],
        }
    ]
    (tree / "compile_commands.json").write_text(json.dumps(cdb))
    (tree / "foo.cpp").write_text("void foo() {}\n")
    return tree


class TestExecuteDumpRequestSourceOnlyMatchesDumpSourceOnly:
    """Parity: the typed pipeline and ``dump_source_only``'s own CLI pipeline
    must agree on the snapshot they produce for equivalent inputs."""

    def test_l3_evidence_matches_across_both_pipelines(self, tmp_path):
        from abicheck.cli_buildsource import dump_source_only
        from abicheck.serialization import load_snapshot
        from abicheck.service_dump_pipeline import (
            execute_dump_request,
            resolve_dump_request,
        )

        tree = _source_tree_with_compile_db(tmp_path)

        # The typed pipeline.
        request = DumpRequest(input=InputSpec(path=None, sources=tree))
        typed_result = execute_dump_request(resolve_dump_request(request))
        typed_snap = typed_result.snapshot

        # dump_source_only's own CLI pipeline, over the identical tree.
        legacy_out = tmp_path / "legacy.abi.json"
        dump_source_only(
            sources=tree,
            build_info=None,
            version="",
            output=legacy_out,
            build_config=None,
            git_tag=None,
            build_id=None,
            no_git=True,
        )
        legacy_snap = load_snapshot(legacy_out)

        # Neither carries a native artifact.
        assert typed_snap.elf is None
        assert typed_snap.pe is None
        assert typed_snap.macho is None
        assert legacy_snap.elf is None

        # Both name the snapshot after the source tree.
        assert typed_snap.library == legacy_snap.library == tree.name

        # Both embedded the identical L3 build evidence.
        assert typed_snap.build_source is not None
        assert legacy_snap.build_source is not None
        typed_units = typed_snap.build_source.build_evidence.compile_units
        legacy_units = legacy_snap.build_source.build_evidence.compile_units
        assert len(typed_units) == len(legacy_units) == 1
        assert typed_units[0].source == legacy_units[0].source

        # Both agree on the achieved evidence depth (whatever it resolves to
        # in this environment -- "source" when L4 replay was attempted,
        # "build" when no clang is available for it to attempt at all --
        # the point is that the two pipelines never disagree, not which
        # value it happens to be here).
        from abicheck.evidence_depth import gated_source_label

        legacy_effective_depth = gated_source_label(
            legacy_snap.build_source, legacy_snap
        )
        assert typed_result.effective_depth == legacy_effective_depth

    def test_build_info_only_also_embeds_l3(self, tmp_path):
        """The same parity holds for ``--build-info`` alone (no ``--sources``),
        the other half of ``dump_source_only``'s own precondition
        (``sources is None and build_info is None`` is the only rejected
        combination)."""
        from abicheck.service_dump_pipeline import (
            execute_dump_request,
            resolve_dump_request,
        )

        tree = _source_tree_with_compile_db(tmp_path)
        cdb_path = tree / "compile_commands.json"

        request = DumpRequest(input=InputSpec(path=None, build_info=cdb_path))
        result = execute_dump_request(resolve_dump_request(request))
        snap = result.snapshot
        assert snap.build_source is not None
        assert len(snap.build_source.build_evidence.compile_units) == 1
        # Named after build_info when sources is absent (dump_source_only's
        # own naming rule).
        assert snap.library == cdb_path.name


class TestExecuteDumpRequestSourceOnlyErrorContract:
    def test_no_sources_or_build_info_is_a_validation_error(self, tmp_path):
        """A source-only request with neither ``sources`` nor ``build_info``
        set is not expressible through ``DumpRequest.validate()`` (which
        `resolve_dump_request` already calls and which rejects the path-less
        shape with no evidence at all) -- but a caller resolving one via a
        `dump_manifest`-only path-less request (a shape `validate()` *does*
        allow, since a manifest counts as declared evidence too) reaches
        `execute_dump_request` with neither `sources` nor `build_info`,
        which is exactly the gap this guard closes: rather than silently
        embedding nothing, it fails the same way `dump_source_only`'s own
        "dump requires a binary ... or --sources/--build-info" precondition
        does."""
        from abicheck.service_dump_pipeline import (
            execute_dump_request,
            resolve_dump_request,
        )

        tree = _source_tree_with_compile_db(tmp_path)
        request = DumpRequest(input=InputSpec(path=None, sources=tree))
        resolved = resolve_dump_request(request)
        # Simulate the one path-less shape `validate()` allows through with
        # no `sources`/`build_info` at all: strip them back off the already
        # -resolved request (a real `dump_manifest`-only request would reach
        # this exact state without needing this stripping step -- see this
        # function's own docstring).
        stripped_input = dataclasses.replace(
            resolved.request.input, sources=None, build_info=None
        )
        stripped_resolved = dataclasses.replace(
            resolved,
            request=dataclasses.replace(resolved.request, input=stripped_input),
        )

        with pytest.raises(ValidationError, match="sources and/or build_info"):
            execute_dump_request(stripped_resolved)

    def test_unreached_requested_depth_floor_raises(self, tmp_path):
        """An explicit ``--depth build`` that the source-only run can't
        reach (no compile database anywhere in the tree, nothing to infer a
        build system from) is a hard ``ValidationError`` -- the identical
        floor `execute_dump_request`'s binary path enforces via
        `enforce_requested_depth`, not a silent weaker snapshot."""
        from abicheck.service_dump_pipeline import (
            execute_dump_request,
            resolve_dump_request,
        )

        tree = tmp_path / "empty_src"
        tree.mkdir()
        (tree / "foo.cpp").write_text("void foo() {}\n")

        request = DumpRequest(input=InputSpec(path=None, sources=tree), depth="build")
        resolved = resolve_dump_request(request)
        with pytest.raises(ValidationError, match="depth"):
            execute_dump_request(resolved)
