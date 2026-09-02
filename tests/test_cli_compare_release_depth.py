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

"""D1/D3: ``compare --depth`` against a directory/package (release) operand.

Before this fix, ``--depth`` was rejected wholesale for a directory/package
compare (alongside ``--sources``/``--build-info``/``--dump-manifest``) with a
message whose own reasoning ("the per-library fan-out does not collect
inline build/source evidence") only actually applies to ``build``/``source``:

* ``--depth binary`` requests *less* evidence than the fan-out already
  collects by default -- there is nothing about it the fan-out can't
  provide, so it must be accepted and forwarded to every pair (this closes
  D3 too: a baseline dumped with an explicit ``--depth binary`` and a
  release-fan-out PR side that could not pin the same floor could silently
  diverge in evidence richness with no warning).
* ``--depth headers`` is still rejected -- the fan-out has no per-library
  evidence-*floor* enforcement -- but must get its own, distinct message
  rather than being lumped in with build/source's "no inline evidence"
  reasoning, which doesn't apply to it (the fan-out already resolves
  per-pair header evidence via ``-H``/``--include-dir``).
* ``--depth build``/``--depth source`` keep being rejected for the original
  reason.
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.serialization import snapshot_to_json

# ── helpers (mirrors test_compare_release_annotations.py's own) ────────────


def _snap(version: str, library: str = "libfoo.so") -> AbiSnapshot:
    return AbiSnapshot(
        library=library,
        version=version,
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


def _make_release_dirs(tmp_path: Path) -> tuple[Path, Path]:
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    old_dir.mkdir()
    new_dir.mkdir()
    _write_snap(old_dir / "libfoo.json", _snap("1.0"))
    _write_snap(new_dir / "libfoo.json", _snap("1.0"))
    return old_dir, new_dir


def _invoke(*args: str) -> tuple[int, str]:
    from abicheck.cli import main

    result = CliRunner().invoke(main, list(args))
    return result.exit_code, result.output


class TestDepthBinaryAcceptedForReleaseCompare:
    """D1: ``--depth binary`` is an explicit assertion the release fan-out
    can always honour -- it must not be rejected, and must actually reach
    each per-library comparison (D3: so a release PR side can pin the same
    evidence floor a baseline was dumped under)."""

    def test_depth_binary_does_not_raise_usage_error(self, tmp_path: Path) -> None:
        old_dir, new_dir = _make_release_dirs(tmp_path)
        code, out = _invoke(
            "compare",
            str(old_dir),
            str(new_dir),
            "--depth",
            "binary",
            "--jobs",
            "1",
            "--format",
            "json",
        )
        assert code == 0, out
        assert "not supported for directory/package" not in out

    def test_depth_binary_is_forwarded_to_every_library_pair(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Proves the value actually reaches ``service.run_compare`` for
        each pair, not just that the CLI stopped rejecting it (mirrors
        ``test_cli_compare_bundle_facts_rejections.py``'s identical
        ``test_depth_binary_clears_headers`` pattern for the sibling
        --old-bundle-facts dispatcher)."""
        import abicheck.service as service

        old_dir, new_dir = _make_release_dirs(tmp_path)
        captured: list[object] = []
        real_run_compare = service.run_compare

        def _capturing_run_compare(*args: object, **kwargs: object) -> object:
            captured.append(kwargs.get("depth"))
            return real_run_compare(*args, **kwargs)

        monkeypatch.setattr(service, "run_compare", _capturing_run_compare)

        code, out = _invoke(
            "compare",
            str(old_dir),
            str(new_dir),
            "--depth",
            "binary",
            "--jobs",
            "1",
            "--format",
            "json",
        )
        assert code == 0, out
        assert captured == ["binary"]

    def test_depth_binary_reported_per_library(self, tmp_path: Path) -> None:
        old_dir, new_dir = _make_release_dirs(tmp_path)
        code, out = _invoke(
            "compare",
            str(old_dir),
            str(new_dir),
            "--depth",
            "binary",
            "--jobs",
            "1",
            "--format",
            "json",
        )
        assert code == 0, out
        data = json.loads(out)
        [lib] = data["libraries"]
        assert lib["verdict"] == "NO_CHANGE"


class TestDepthHeadersRejectedDistinctlyForReleaseCompare:
    """D1: ``--depth headers`` is still rejected on this path, but its
    message must not claim the "no inline build/source evidence" reasoning
    that only applies to build/source -- the fan-out already resolves
    per-pair header evidence; what's missing is floor enforcement."""

    def test_depth_headers_is_rejected(self, tmp_path: Path) -> None:
        old_dir, new_dir = _make_release_dirs(tmp_path)
        code, out = _invoke(
            "compare",
            str(old_dir),
            str(new_dir),
            "--depth",
            "headers",
        )
        assert code == 64
        assert "--depth headers" in out

    def test_depth_headers_message_is_distinct_from_build_source(
        self, tmp_path: Path
    ) -> None:
        old_dir, new_dir = _make_release_dirs(tmp_path)
        code, out = _invoke(
            "compare",
            str(old_dir),
            str(new_dir),
            "--depth",
            "headers",
        )
        assert code == 64
        assert "does not collect inline build/source evidence" not in out
        assert "evidence floor" in out


class TestDepthBuildSourceStillRejectedForReleaseCompare:
    """Unchanged behaviour (regression coverage for the refactor): build and
    source both keep the original "no inline evidence" message."""

    def test_depth_build_is_rejected(self, tmp_path: Path) -> None:
        old_dir, new_dir = _make_release_dirs(tmp_path)
        code, out = _invoke(
            "compare",
            str(old_dir),
            str(new_dir),
            "--depth",
            "build",
        )
        assert code == 64
        assert "--depth build" in out
        assert "does not collect inline build/source evidence" in out

    def test_depth_source_is_rejected(self, tmp_path: Path) -> None:
        old_dir, new_dir = _make_release_dirs(tmp_path)
        code, out = _invoke(
            "compare",
            str(old_dir),
            str(new_dir),
            "--depth",
            "source",
        )
        assert code == 64
        assert "--depth source" in out
        assert "does not collect inline build/source evidence" in out

    def test_sources_still_rejected_alongside_depth_refactor(
        self, tmp_path: Path
    ) -> None:
        """Regression guard: splitting --depth out of
        _EVIDENCE_SET_INPUT_FLAGS must not accidentally stop rejecting the
        flags that stayed in it."""
        old_dir, new_dir = _make_release_dirs(tmp_path)
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        code, out = _invoke(
            "compare",
            str(old_dir),
            str(new_dir),
            "--sources",
            f"new={src_dir}",
        )
        assert code == 64
        assert "--sources" in out
        assert "does not collect inline build/source evidence" in out
