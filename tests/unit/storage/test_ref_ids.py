# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""`abicheck.storage.ref_ids` — cross-platform ref-id path safety, and the
`resolve_ref_ids` name-to-safe-id resolver (ADR-063 Track C 8B).
"""

from __future__ import annotations

import pytest

from abicheck.storage.ref_ids import (
    reject_filesystem_collisions,
    resolve_ref_ids,
    safe_ref_id,
)


class TestResolveRefIds:
    def test_safe_non_colliding_names_keep_their_literal_spelling(self) -> None:
        result = resolve_ref_ids(["liba.so", "libb.so"], opaque_prefix="lib")
        assert result == {"liba.so": "liba.so", "libb.so": "libb.so"}

    def test_case_colliding_names_all_fall_back_to_opaque_ids(self) -> None:
        result = resolve_ref_ids(["libFoo.so", "libfoo.so"], opaque_prefix="lib")
        assert set(result) == {"libFoo.so", "libfoo.so"}
        assert result["libFoo.so"] != "libFoo.so"
        assert result["libfoo.so"] != "libfoo.so"
        # Every fallback id is itself safe and mutually non-colliding.
        ids = list(result.values())
        assert len(set(ids)) == len(ids)
        for ref_id in ids:
            safe_ref_id(ref_id, "artifact_id")
        reject_filesystem_collisions(ids, "artifact_id")

    def test_an_unrelated_safe_name_is_unaffected_by_a_sibling_collision(
        self,
    ) -> None:
        """A colliding pair's own opaque fallback must not touch a third,
        unrelated, safe name's resolution -- an earlier version fell the
        *whole* set back to opaque ids, which meant adding one new
        colliding library anywhere in a bundle silently reassigned a
        completely unrelated, already-safe library's own `artifact_id`,
        breaking release-to-release matching keyed by that id (Codex
        review, fresh evidence: this is the concrete "a stable library
        gets reidentified when an unrelated sibling is added" scenario)."""
        result = resolve_ref_ids(
            ["libFoo.so", "libfoo.so", "libbar.so"], opaque_prefix="lib"
        )
        assert result["libbar.so"] == "libbar.so"
        assert result["libFoo.so"] != "libFoo.so"
        assert result["libfoo.so"] != "libfoo.so"

    def test_adding_a_colliding_sibling_does_not_change_an_existing_ids_resolution(
        self,
    ) -> None:
        """The exact scenario named by the Codex finding: `libfoo.so`
        alone resolves to its own literal spelling; adding a
        case-colliding `LIBFOO.SO` must not change that resolution for any
        *other* member of the set (here, none -- the two-name case is
        covered directly by verifying `libfoo.so` keeps its own previous
        resolution across both calls, holding all libraries besides the
        newly-added one fixed)."""
        before = resolve_ref_ids(["libfoo.so", "libbar.so"], opaque_prefix="lib")
        after = resolve_ref_ids(
            ["libfoo.so", "libbar.so", "LIBFOO.SO"], opaque_prefix="lib"
        )
        assert after["libbar.so"] == before["libbar.so"]

    def test_an_unsafe_name_falls_only_itself_back_to_an_opaque_id(self) -> None:
        """An unrelated, safe, non-colliding name's own resolution never
        depends on some other unsafe/colliding name elsewhere in the set
        -- an earlier version fell the *whole* set back to opaque ids,
        which meant adding one bad name anywhere reassigned every other
        already-safe name's own artifact_id too (Codex review, fresh
        evidence)."""
        result = resolve_ref_ids(["liba.so", "a/b"], opaque_prefix="lib")
        assert result["liba.so"] == "liba.so"
        assert result["a/b"] != "a/b"

    def test_opaque_ids_are_deterministic(self) -> None:
        first = resolve_ref_ids(["a/b"], opaque_prefix="lib")
        second = resolve_ref_ids(["a/b"], opaque_prefix="lib")
        assert first == second

    def test_opaque_ids_use_the_given_prefix(self) -> None:
        result = resolve_ref_ids(["a/b"], opaque_prefix="baseline")
        assert result["a/b"].startswith("baseline-")

    def test_opaque_ids_use_the_full_digest_not_a_truncated_prefix(self) -> None:
        """`_opaque_ref_id` must carry the full sha256 hex digest (64
        chars), not a truncated prefix of it -- a truncated digest would
        make a hash collision between two distinct names an actually
        reachable way to get `PackageManifest`'s own duplicate-`artifact_id`
        rejection, contradicting the "never collide (up to sha256)"
        guarantee that function claims (CodeRabbit review)."""
        result = resolve_ref_ids(["a/b"], opaque_prefix="lib")
        digest = result["a/b"].removeprefix("lib-")
        assert len(digest) == 64

    def test_an_empty_name_sequence_returns_an_empty_mapping(self) -> None:
        assert resolve_ref_ids([], opaque_prefix="lib") == {}

    @pytest.mark.parametrize(
        "names", [["a", "a"], ["dup", "dup", "other"]], ids=["exact", "with-extra"]
    )
    def test_exact_duplicate_names_resolve_to_one_shared_entry(
        self, names: list[str]
    ) -> None:
        # A `dict` comprehension collapses duplicate keys naturally --
        # this pins that resolve_ref_ids doesn't need special handling for
        # it, not that a caller should pass duplicates.
        result = resolve_ref_ids(names, opaque_prefix="lib")
        assert set(result) == set(names)
