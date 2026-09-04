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

    def test_only_the_non_canonical_member_of_a_colliding_pair_goes_opaque(
        self,
    ) -> None:
        """Exactly one string per case/normalization fold class is its own
        canonical fold (`libfoo.so`, already lowercase); that one keeps its
        literal spelling unconditionally, and only the other, non-canonical
        spelling (`libFoo.so`) falls back to an opaque id -- resolving the
        pair's own membership-dependence the whole-set-fallback design (and
        its per-call-collision-detection successor) both still had (Codex
        review, fresh evidence: see `resolve_ref_ids`'s own docstring for
        the two prior falsified designs)."""
        result = resolve_ref_ids(["libFoo.so", "libfoo.so"], opaque_prefix="lib")
        assert result["libfoo.so"] == "libfoo.so"
        assert result["libFoo.so"] != "libFoo.so"
        # The fallback id is itself safe and doesn't collide with the
        # literal one it sits alongside.
        ids = list(result.values())
        assert len(set(ids)) == len(ids)
        for ref_id in ids:
            safe_ref_id(ref_id, "artifact_id")
        reject_filesystem_collisions(ids, "artifact_id")

    def test_an_unrelated_safe_name_is_unaffected_by_a_sibling_collision(
        self,
    ) -> None:
        """A colliding pair's own opaque fallback must not touch a third,
        unrelated, safe name's resolution."""
        result = resolve_ref_ids(
            ["libFoo.so", "libfoo.so", "libbar.so"], opaque_prefix="lib"
        )
        assert result["libbar.so"] == "libbar.so"
        assert result["libfoo.so"] == "libfoo.so"
        assert result["libFoo.so"] != "libFoo.so"

    def test_adding_a_colliding_sibling_never_changes_any_existing_resolution(
        self,
    ) -> None:
        """The exact scenario named by the Codex finding: `libfoo.so`
        alone resolves to its own literal spelling; adding a
        case-colliding `LIBFOO.SO` must not change that resolution for
        `libfoo.so` itself, nor for any other member of the set."""
        before = resolve_ref_ids(["libfoo.so", "libbar.so"], opaque_prefix="lib")
        after = resolve_ref_ids(
            ["libfoo.so", "libbar.so", "LIBFOO.SO"], opaque_prefix="lib"
        )
        assert after["libfoo.so"] == before["libfoo.so"]
        assert after["libbar.so"] == before["libbar.so"]

    def test_a_non_canonical_but_otherwise_safe_name_alone_goes_opaque(self) -> None:
        """`resolve_ref_ids` decides purely from each name's own spelling,
        never from what else is in the call -- a non-canonical name (here,
        with no colliding sibling at all) still gets an opaque id, since
        its own canonical fold (`"libfoo.so"`) is a *different* string it
        does not itself spell."""
        result = resolve_ref_ids(["libFoo.so"], opaque_prefix="lib")
        assert result["libFoo.so"] != "libFoo.so"

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

    def test_a_literal_name_shaped_like_an_opaque_id_never_keeps_its_spelling(
        self,
    ) -> None:
        """A literal name that already has the exact shape `_opaque_ref_id`
        produces (`lib-<64 hex chars>`) must not keep that spelling: a
        second, genuinely different name could independently hash to the
        identical opaque id, and `PackageManifest` would then reject an
        otherwise-valid import as a duplicate `artifact_id` (Codex review,
        fresh evidence -- the literal-id and opaque-id namespaces were not
        disjoint)."""
        opaque_shaped_literal = "lib-" + "a" * 64
        result = resolve_ref_ids([opaque_shaped_literal], opaque_prefix="lib")
        assert result[opaque_shaped_literal] != opaque_shaped_literal

    def test_an_empty_name_sequence_returns_an_empty_mapping(self) -> None:
        assert resolve_ref_ids([], opaque_prefix="lib") == {}

    def test_rejects_an_unsafe_opaque_prefix(self) -> None:
        """A caller-supplied `opaque_prefix` that is itself unsafe would
        make `_opaque_ref_id`'s own output fail `safe_ref_id`, silently
        breaking the "a name this function cannot make safe always has a
        working opaque fallback" guarantee this function's own docstring
        makes (CodeRabbit review)."""
        with pytest.raises(ValueError, match="opaque_prefix"):
            resolve_ref_ids(["a"], opaque_prefix="a/b")

    def test_rejects_an_overlong_opaque_prefix(self) -> None:
        with pytest.raises(ValueError, match="opaque_prefix"):
            resolve_ref_ids(["a"], opaque_prefix="x" * 300)

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
