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

"""P2 follow-up (Codex review, PR #1016): ``--bundle-facts-out``'s stranded
(removed-in-new) library resolver must honour ``--depth binary`` the same
way every matched pair does.

D1's own fix (``tests/test_cli_compare_release_depth.py``) threaded ``depth``
through to every *matched* pair in the release fan-out, but
``compare_release_cmd``'s ``_resolve_stranded_library`` closure -- used only
by ``write_bundle_facts_out()`` to resolve a library present in the old side
and absent from the new one -- called ``_resolve_input`` with the full
header/include set regardless of ``depth``. A ``--depth binary`` run's
``BundleFacts`` output therefore mixed binary-only snapshots for every
matched pair with a full L2 (header-evidence) snapshot for the stranded
member, silently reintroducing header/API findings the run explicitly asked
to exclude for a later stored-baseline comparison against that output.

ADR-063 D1's second named exception migrated ``_resolve_stranded_library``
off the direct ``cli_resolve._resolve_input()`` call onto the shared
``DumpRequest -> resolve_dump_request -> execute_dump_request`` pipeline
(dropping the hand-rolled ``is_binary_depth`` special-case this suite used to
pin) -- these tests now capture ``resolve_dump_request``'s own
``DumpRequest.input.headers``/``.includes`` instead of ``_resolve_input``'s
raw arguments, but assert the identical depth-aware contract.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.serialization import snapshot_to_json


def _snap(library: str = "libfoo.so") -> AbiSnapshot:
    return AbiSnapshot(
        library=library,
        version="1.0",
        functions=[
            Function(
                name="foo",
                mangled="_Z3foov",
                return_type="int",
                visibility=Visibility.PUBLIC,
            )
        ],
        from_headers=True,
    )


def _write_snap(path: Path, snap: AbiSnapshot) -> Path:
    path.write_text(snapshot_to_json(snap), encoding="utf-8")
    return path


def _invoke(*args: str) -> tuple[int, str]:
    result = CliRunner().invoke(main, list(args))
    return result.exit_code, result.output


class TestBundleFactsOutStrandedLibraryHonoursDepthBinary:
    def _setup(self, tmp_path: Path) -> tuple[Path, Path, Path, Path]:
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        snap = _snap(library="libfoo.so")
        _write_snap(old_dir / "libfoo.json", snap)
        _write_snap(new_dir / "libfoo.json", snap)
        # Old-only: stranded in the new side, so write_bundle_facts_out's
        # resolve_stranded_library callback is what resolves it.
        stranded = old_dir / "libbroken.so"
        stranded.write_bytes(b"\x7fELF" + b"\x00" * 100)
        header = tmp_path / "api.h"
        header.write_text("void f();\n")
        return old_dir, new_dir, stranded, header

    def _capture_stranded_resolve_input_args(
        self, monkeypatch: pytest.MonkeyPatch, stranded: Path
    ) -> dict[str, object]:
        """Capture the *effective* (post depth-resolution) header set.

        ``DumpRequest.input.headers`` itself always carries the caller's raw
        input unchanged -- clearing at ``depth=binary`` happens one layer
        further in, in evidence resolution (``ResolvedDumpRequest.headers`` /
        ``.public_headers``, the same effective fields the real dump/extract
        step reads) -- so this asserts on those, not the raw request.
        """
        captured: dict[str, object] = {}
        from abicheck.service_dump_pipeline import (
            resolve_dump_request as _real_resolve_dump_request,
        )

        def _fake_resolve_dump_request(request: object) -> object:
            resolved = _real_resolve_dump_request(request)  # type: ignore[arg-type]
            if request.input.path == stranded:  # type: ignore[attr-defined]
                captured["headers"] = list(resolved.headers)  # type: ignore[attr-defined]
                captured["includes"] = list(request.input.includes)  # type: ignore[attr-defined]
                captured["public_headers"] = list(resolved.public_headers)  # type: ignore[attr-defined]
            return resolved

        monkeypatch.setattr(
            "abicheck.service_dump_pipeline.resolve_dump_request",
            _fake_resolve_dump_request,
        )
        return captured

    def test_depth_binary_clears_headers_for_the_stranded_library(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        old_dir, new_dir, stranded, header = self._setup(tmp_path)
        captured = self._capture_stranded_resolve_input_args(monkeypatch, stranded)
        out_path = tmp_path / "old.bundlefacts.json"

        code, _ = _invoke(
            "compare",
            str(old_dir),
            str(new_dir),
            "-H",
            str(header),
            "--depth",
            "binary",
            "--bundle-facts-out",
            str(out_path),
        )

        assert code == 0
        assert captured["headers"] == []
        assert captured["includes"] == []

    def test_negative_control_headers_survive_without_depth_binary(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without ``--depth binary`` the stranded library still gets the
        full header set -- the new depth-aware branch must not affect any
        other ``--depth`` value or the no-flag default."""
        old_dir, new_dir, stranded, header = self._setup(tmp_path)
        captured = self._capture_stranded_resolve_input_args(monkeypatch, stranded)
        out_path = tmp_path / "old.bundlefacts.json"

        code, _ = _invoke(
            "compare",
            str(old_dir),
            str(new_dir),
            "-H",
            str(header),
            "--bundle-facts-out",
            str(out_path),
        )

        assert code == 0
        assert captured["headers"] == [header]
