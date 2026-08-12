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

"""Tests for ``abicheck.buildsource.baseline_publish`` (G30 P1.6, ADR-047
§6/§10) -- deriving ``actions/baseline``'s ``libraries`` JSON input from a
``build-output.json``, and the ``accepted-main`` Actions-cache key helpers.
"""

from __future__ import annotations

from pathlib import Path

from abicheck.buildsource.baseline_publish import (
    DEFAULT_ACCEPTED_MAIN_KEY_PREFIX,
    accepted_main_cache_key,
    accepted_main_cache_restore_prefix,
    derive_baseline_libraries,
)
from abicheck.buildsource.build_output import BuildOutput, BuildOutputTarget


def _write(root: Path, rel: str, content: bytes = b"binary-content") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


class TestDeriveBaselineLibraries:
    def test_single_standalone_target(self, tmp_path: Path) -> None:
        _write(tmp_path, "artifacts/libfoo.so")
        bo = BuildOutput(
            targets=[
                BuildOutputTarget(id="libfoo", binary="artifacts/libfoo.so"),
            ]
        )
        report = derive_baseline_libraries(bo, tmp_path)
        assert report.ok, report.errors
        assert report.entries == [
            {"name": "libfoo", "artifact": str(tmp_path / "artifacts/libfoo.so")}
        ]

    def test_bundle_member_sets_stage_binary(self, tmp_path: Path) -> None:
        _write(tmp_path, "artifacts/libfoo.so")
        bo = BuildOutput(
            targets=[
                BuildOutputTarget(
                    id="libfoo", binary="artifacts/libfoo.so", bundle="pvxs-release"
                ),
            ]
        )
        report = derive_baseline_libraries(bo, tmp_path)
        assert report.ok, report.errors
        assert report.entries[0]["stage_binary"] is True

    def test_non_bundle_target_has_no_stage_binary_key(self, tmp_path: Path) -> None:
        _write(tmp_path, "artifacts/libfoo.so")
        bo = BuildOutput(
            targets=[BuildOutputTarget(id="libfoo", binary="artifacts/libfoo.so")]
        )
        report = derive_baseline_libraries(bo, tmp_path)
        assert "stage_binary" not in report.entries[0]

    def test_header_roots_resolved_and_joined(self, tmp_path: Path) -> None:
        _write(tmp_path, "artifacts/libfoo.so")
        (tmp_path / "include").mkdir()
        (tmp_path / "generated-include").mkdir()
        bo = BuildOutput(
            targets=[
                BuildOutputTarget(
                    id="libfoo",
                    binary="artifacts/libfoo.so",
                    public_header_roots=["include"],
                    generated_header_roots=["generated-include"],
                ),
            ]
        )
        report = derive_baseline_libraries(bo, tmp_path)
        assert report.ok, report.errors
        header = report.entries[0]["header"]
        assert str(tmp_path / "include") in header
        assert str(tmp_path / "generated-include") in header

    def test_missing_binary_is_reported_not_raised(self, tmp_path: Path) -> None:
        bo = BuildOutput(
            targets=[BuildOutputTarget(id="libfoo", binary="artifacts/libfoo.so")]
        )
        report = derive_baseline_libraries(bo, tmp_path)
        assert not report.ok
        assert not report.entries
        assert "does not exist" in report.errors[0]

    def test_empty_binary_field_is_reported(self, tmp_path: Path) -> None:
        bo = BuildOutput(targets=[BuildOutputTarget(id="libfoo", binary="")])
        report = derive_baseline_libraries(bo, tmp_path)
        assert not report.ok
        assert "no binary declared" in report.errors[0]

    def test_missing_target_id_is_reported(self, tmp_path: Path) -> None:
        bo = BuildOutput(targets=[BuildOutputTarget(id="", binary="a.so")])
        report = derive_baseline_libraries(bo, tmp_path)
        assert not report.ok
        assert "no id set" in report.errors[0]

    def test_absolute_binary_path_is_rejected(self, tmp_path: Path) -> None:
        bo = BuildOutput(targets=[BuildOutputTarget(id="libfoo", binary="/etc/passwd")])
        report = derive_baseline_libraries(bo, tmp_path)
        assert not report.ok
        assert "escapes" in report.errors[0] or "absolute" in report.errors[0]

    def test_escaping_binary_path_is_rejected(self, tmp_path: Path) -> None:
        bo = BuildOutput(
            targets=[BuildOutputTarget(id="libfoo", binary="../../etc/passwd")]
        )
        report = derive_baseline_libraries(bo, tmp_path)
        assert not report.ok

    def test_escaping_header_root_is_reported_but_does_not_abort_other_targets(
        self, tmp_path: Path
    ) -> None:
        _write(tmp_path, "artifacts/libfoo.so")
        _write(tmp_path, "artifacts/libbar.so")
        bo = BuildOutput(
            targets=[
                BuildOutputTarget(
                    id="libfoo",
                    binary="artifacts/libfoo.so",
                    public_header_roots=["../../etc"],
                ),
                BuildOutputTarget(id="libbar", binary="artifacts/libbar.so"),
            ]
        )
        report = derive_baseline_libraries(bo, tmp_path)
        assert not report.ok
        assert any("header root" in e for e in report.errors)
        # libfoo still gets an entry (minus the bad header), and libbar --
        # entirely unaffected by libfoo's problem -- is unaffected.
        names = {e["name"] for e in report.entries}
        assert names == {"libfoo", "libbar"}
        libfoo_entry = next(e for e in report.entries if e["name"] == "libfoo")
        assert "header" not in libfoo_entry

    def test_no_targets_is_reported(self, tmp_path: Path) -> None:
        report = derive_baseline_libraries(BuildOutput(targets=[]), tmp_path)
        assert not report.ok
        assert "no targets" in report.errors[0]

    def test_multiple_targets_preserve_declaration_order(self, tmp_path: Path) -> None:
        _write(tmp_path, "artifacts/libb.so")
        _write(tmp_path, "artifacts/liba.so")
        bo = BuildOutput(
            targets=[
                BuildOutputTarget(id="libb", binary="artifacts/libb.so"),
                BuildOutputTarget(id="liba", binary="artifacts/liba.so"),
            ]
        )
        report = derive_baseline_libraries(bo, tmp_path)
        assert report.ok, report.errors
        assert [e["name"] for e in report.entries] == ["libb", "liba"]

    def test_to_dict_round_trips_ok_and_lists(self, tmp_path: Path) -> None:
        report = derive_baseline_libraries(BuildOutput(targets=[]), tmp_path)
        d = report.to_dict()
        assert d["ok"] is False
        assert d["entries"] == []
        assert d["errors"]


class TestAcceptedMainCacheKeys:
    def test_default_key_prefix(self) -> None:
        assert DEFAULT_ACCEPTED_MAIN_KEY_PREFIX == "abicheck-baseline-main"

    def test_cache_key_format(self) -> None:
        assert (
            accepted_main_cache_key(
                "abicheck-baseline-main", "linux-x86_64-gcc", "abc123"
            )
            == "abicheck-baseline-main-linux-x86_64-gcc-abc123"
        )

    def test_restore_prefix_format(self) -> None:
        assert (
            accepted_main_cache_restore_prefix(
                "abicheck-baseline-main", "linux-x86_64-gcc"
            )
            == "abicheck-baseline-main-linux-x86_64-gcc-"
        )

    def test_restore_prefix_is_a_prefix_of_the_cache_key(self) -> None:
        key = accepted_main_cache_key("p", "prof", "sha1")
        prefix = accepted_main_cache_restore_prefix("p", "prof")
        assert key.startswith(prefix)

    def test_two_consecutive_head_shas_produce_distinct_keys_sharing_one_prefix(
        self,
    ) -> None:
        # The behavior ADR-047 section 10 requires: two update-main-baseline.yml
        # runs at different head_shas must write two distinct, independently
        # resolvable cache entries, both discoverable via the same
        # restore-keys prefix -- a single stable key across refreshes would
        # silently stop updating after the first write.
        key_prefix, profile_id = "abicheck-baseline-main", "linux-x86_64-gcc"
        first = accepted_main_cache_key(key_prefix, profile_id, "sha-one")
        second = accepted_main_cache_key(key_prefix, profile_id, "sha-two")
        prefix = accepted_main_cache_restore_prefix(key_prefix, profile_id)
        assert first != second
        assert first.startswith(prefix)
        assert second.startswith(prefix)

    def test_generation_is_folded_into_the_key(self) -> None:
        assert (
            accepted_main_cache_key(
                "abicheck-baseline-main",
                "linux-x86_64-gcc",
                "abc123",
                generation=3,
            )
            == "abicheck-baseline-main-g3-linux-x86_64-gcc-abc123"
        )

    def test_generation_is_folded_into_the_restore_prefix(self) -> None:
        assert (
            accepted_main_cache_restore_prefix(
                "abicheck-baseline-main", "linux-x86_64-gcc", generation=3
            )
            == "abicheck-baseline-main-g3-linux-x86_64-gcc-"
        )

    def test_no_generation_matches_the_unfolded_key(self) -> None:
        # generation=None (the default) must be a complete no-op -- byte-
        # identical to never having passed it at all, so every pre-existing
        # caller's key format is unaffected.
        assert accepted_main_cache_key(
            "abicheck-baseline-main", "linux-x86_64-gcc", "abc123"
        ) == accepted_main_cache_key(
            "abicheck-baseline-main",
            "linux-x86_64-gcc",
            "abc123",
            generation=None,
        )

    def test_different_generations_produce_disjoint_prefixes(self) -> None:
        # Two different scanner-compatibility generations must never share
        # one cache-key namespace -- the whole point of folding generation
        # into the prefix rather than only the head_sha-unique suffix.
        key_prefix, profile_id = "abicheck-baseline-main", "linux-x86_64-gcc"
        g2_key = accepted_main_cache_key(key_prefix, profile_id, "sha1", generation=2)
        g3_prefix = accepted_main_cache_restore_prefix(
            key_prefix, profile_id, generation=3
        )
        assert not g2_key.startswith(g3_prefix)
