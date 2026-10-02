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

"""Tests for `_compare_release_parallel`'s dedup-scope propagation.

Split out of `test_compare_release.py` (a debt.yaml `no_growth`-tracked
module) purely to keep that file under the AI-readiness `file-size` gate's
2000-line hard cap -- this class is fully self-contained (its own
`_member_context` helper, no shared module-level fixtures from the parent
file) and was the newest/most independently-addable block at the time of
the split.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.cli_compare_release import _compare_release_parallel
from abicheck.cli_compare_release_pairwise import (
    ReleaseMemberContext,
    release_parent_request,
)


class TestParallelFanOutDedupPropagation:
    """`_compare_release_parallel` -- the ThreadPoolExecutor path `--jobs 0`
    (the CLI default, auto-detecting CPU count) actually dispatches to for
    any multi-library release -- must propagate an active `policy_file.
    dedup_validate_overrides_warnings()` scope into each worker thread
    (Codex review: a `ContextVar` set in the calling thread is not
    automatically visible to a new `ThreadPoolExecutor` worker thread)."""

    def _member_context(
        self, tmp_path: Path, pf: Path, keys: tuple[str, ...] = ("a", "b")
    ) -> ReleaseMemberContext:
        old_map = {k: tmp_path / f"{k}_old" for k in keys}
        return ReleaseMemberContext(
            request=release_parent_request(
                old_version="1.0",
                new_version="2.0",
                policy_file_path=pf,
                include_dependencies=True,
            ),
            old_map=old_map,
            new_map={k: tmp_path / f"{k}_new" for k in keys},
            resolve_debug_info=lambda _o, _n: None,
        )

    def test_dedup_scope_propagates_into_worker_threads(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
    ) -> None:
        import logging

        from abicheck.policy_file import dedup_validate_overrides_warnings
        from abicheck.service import load_suppression_and_policy

        pf = tmp_path / "policy.yaml"
        pf.write_text("base_policy: strict_abi\noverrides:\n  func_removed: ignore\n")

        def _fake_compare_one_library(key: str, *_args: object) -> dict[str, object]:
            # Every worker loads the exact same policy file, mirroring what
            # the real per-library comparison does through service.run_compare.
            load_suppression_and_policy(None, policy_file_path=pf)
            return {"library": f"{key}.so", "verdict": "NO_CHANGE"}

        monkeypatch.setattr(
            "abicheck.cli_compare_release_pairwise._compare_one_library",
            _fake_compare_one_library,
        )

        ctx = self._member_context(tmp_path, pf)
        with caplog.at_level(logging.WARNING, logger="abicheck.service"):
            with dedup_validate_overrides_warnings():
                results = _compare_release_parallel(
                    ["a", "b"], ctx, ctx.old_map, max_workers=4
                )
        assert len(results) == 2
        # Deduped across both worker threads -- not one warning per library.
        assert caplog.text.count("HIGH RISK") == 1

    def test_without_dedup_scope_each_worker_still_warns(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
    ) -> None:
        """Outside any dedup scope, behaviour is unchanged: every worker's
        own load still warns independently."""
        import logging

        from abicheck.service import load_suppression_and_policy

        pf = tmp_path / "policy.yaml"
        pf.write_text("base_policy: strict_abi\noverrides:\n  func_removed: ignore\n")

        def _fake_compare_one_library(key: str, *_args: object) -> dict[str, object]:
            load_suppression_and_policy(None, policy_file_path=pf)
            return {"library": f"{key}.so", "verdict": "NO_CHANGE"}

        monkeypatch.setattr(
            "abicheck.cli_compare_release_pairwise._compare_one_library",
            _fake_compare_one_library,
        )

        ctx = self._member_context(tmp_path, pf)
        with caplog.at_level(logging.WARNING, logger="abicheck.service"):
            results = _compare_release_parallel(
                ["a", "b"], ctx, ctx.old_map, max_workers=4
            )
        assert len(results) == 2
        assert caplog.text.count("HIGH RISK") == 2

    def test_dedup_scope_survives_many_concurrent_workers(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
    ) -> None:
        """Stress the check-then-add race directly: many workers, all
        starting on the identical first load, all racing to be first past
        `dedup_validate_overrides_warnings()`'s dedup set -- exactly the
        shape that reproduced the race this test guards against before
        `policy_file._warning_dedup_lock` was added (Codex review: the
        initial fix propagated the dedup scope into worker threads but left
        the shared set's check-then-add unguarded, reproducing on ~100% of
        real runs with as few as 2 concurrent workers)."""
        import logging

        from abicheck.policy_file import dedup_validate_overrides_warnings
        from abicheck.service import load_suppression_and_policy

        pf = tmp_path / "policy.yaml"
        pf.write_text("base_policy: strict_abi\noverrides:\n  func_removed: ignore\n")

        def _fake_compare_one_library(key: str, *_args: object) -> dict[str, object]:
            load_suppression_and_policy(None, policy_file_path=pf)
            return {"library": f"{key}.so", "verdict": "NO_CHANGE"}

        monkeypatch.setattr(
            "abicheck.cli_compare_release_pairwise._compare_one_library",
            _fake_compare_one_library,
        )

        keys = tuple(f"lib{i}" for i in range(40))
        ctx = self._member_context(tmp_path, pf, keys)
        with caplog.at_level(logging.WARNING, logger="abicheck.service"):
            with dedup_validate_overrides_warnings():
                results = _compare_release_parallel(
                    list(keys), ctx, ctx.old_map, max_workers=16
                )
        assert len(results) == 40
        assert caplog.text.count("HIGH RISK") == 1
