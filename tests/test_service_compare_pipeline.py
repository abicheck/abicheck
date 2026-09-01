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

"""Unit tests for ``service_compare_pipeline``'s ``resolve_sides_sequentially``.

Split out of tests/test_service_unit.py (which was already past the
architecture gate's 1200-line test-file cap) rather than grown further —
one class, moved verbatim, matching the file's own debt-ledger target
("tests/unit-or-contract ownership matching production migration").
"""

from __future__ import annotations

import pytest

from abicheck.api_types import CompareRequest, InputSpec
from abicheck.model import AbiSnapshot


class TestResolveSidesSequentially:
    """ADR-050 D6 / G32 Phase E, generalised by ADR-055 D1.

    A manifest-driven dump sizes its per-TU worker pool from a live
    ``MemAvailable`` reading, so two starting concurrently size two full pools
    off the same reading and jointly overcommit. That guard used to be
    implicit — the native ``compare`` CLI simply resolved sequentially, and
    ``run_compare_request`` was documented as unable to reach a manifest at
    all. ``InputSpec.dump_manifest`` made that documentation stale: the typed
    path could reach a manifest *and* resolved concurrently. Now that both
    front ends share one resolution, the guard is explicit and lives with it.
    """

    def _request(self, tmp_path, *, old_manifest=None, new_manifest=None):
        return CompareRequest(
            old=InputSpec(path=tmp_path / "old.so", dump_manifest=old_manifest),
            new=InputSpec(path=tmp_path / "new.so", dump_manifest=new_manifest),
        )

    def test_plain_pair_may_resolve_concurrently(self, tmp_path, monkeypatch):
        from abicheck.service import resolve_sides_sequentially

        monkeypatch.delenv("ABICHECK_PARALLEL_EXTRACTION", raising=False)
        assert resolve_sides_sequentially(self._request(tmp_path)) is False

    @pytest.mark.parametrize("side", ["old", "new"])
    def test_a_dump_manifest_on_either_side_forces_sequential(
        self, tmp_path, monkeypatch, side
    ):
        from types import SimpleNamespace

        from abicheck.service import resolve_sides_sequentially

        monkeypatch.delenv("ABICHECK_PARALLEL_EXTRACTION", raising=False)
        manifest = SimpleNamespace(translation_units=[])
        request = self._request(tmp_path, **{f"{side}_manifest": manifest})
        assert resolve_sides_sequentially(request) is True

    @pytest.mark.parametrize("value", ["0", "false", "no", "NO", " 0 "])
    def test_env_opt_out_forces_sequential(self, tmp_path, monkeypatch, value):
        from abicheck.service import resolve_sides_sequentially

        monkeypatch.setenv("ABICHECK_PARALLEL_EXTRACTION", value)
        assert resolve_sides_sequentially(self._request(tmp_path)) is True

    def test_manifest_request_really_resolves_one_side_at_a_time(
        self, tmp_path, monkeypatch
    ):
        """The behavioural half: not just the predicate, but the resolution.

        Without the guard this is exactly the double-pool-sizing case — two
        manifest dumps in a ``ThreadPoolExecutor``, overlapping in time.
        """
        import time
        from types import SimpleNamespace

        from abicheck import service as service_mod
        from abicheck.service import resolve_compare_request

        monkeypatch.delenv("ABICHECK_PARALLEL_EXTRACTION", raising=False)
        spans: list[tuple[str, float, float]] = []

        def _fake_resolve(path, headers, includes, version, lang, **kwargs):
            start = time.monotonic()
            time.sleep(0.05)
            spans.append((version, start, time.monotonic()))
            return AbiSnapshot(library="libtest", version=version)

        monkeypatch.setattr(service_mod, "resolve_input", _fake_resolve)
        old_p = tmp_path / "old.so"
        new_p = tmp_path / "new.so"
        old_p.write_bytes(b"\x7fELF" + b"\x00" * 200)
        new_p.write_bytes(b"\x7fELF" + b"\x00" * 200)
        manifest = SimpleNamespace(translation_units=[])
        resolve_compare_request(
            CompareRequest(
                old=InputSpec(path=old_p, version="old", dump_manifest=manifest),
                new=InputSpec(path=new_p, version="new", dump_manifest=manifest),
            )
        )
        assert len(spans) == 2
        (_old_v, _old_start, old_end), (_new_v, new_start, _new_end) = spans
        assert new_start >= old_end
