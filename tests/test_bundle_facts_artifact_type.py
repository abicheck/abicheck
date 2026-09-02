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

"""``BundleFacts``' plain-JSON ``artifact_type`` discriminator (CLI cleanup
phase two, PR I prerequisite) -- a strong, explicit self-describing marker,
distinct from the shape-based heuristic it supersedes.

Split out of ``tests/test_bundle_facts.py`` (a ``debt.yaml``-tracked,
no-growth test module -- new coverage goes in a sibling file instead of
growing it further), mirroring that file's own small ``ElfMetadata``
fixture style. See ``tests/test_bundle_facts_archive.py``'s own
``TestBundleFactsArchiveArtifactTypeDiscriminator`` for the G40 archive
container's separate, required marker.
"""

from __future__ import annotations

import pytest

from abicheck.bundle_facts import (
    BUNDLE_FACTS_ARTIFACT_TYPE,
    BUNDLE_FACTS_SCHEMA_VERSION,
    BundleFacts,
    capture_bundle_facts,
)
from abicheck.bundle_facts_serialization import looks_like_bundle_facts_document
from abicheck.elf_metadata import ElfImport, ElfMetadata, ElfSymbol
from abicheck.model import AbiSnapshot
from abicheck.serialization import bundle_facts_from_dict, bundle_facts_to_dict


def _meta(
    *,
    soname: str = "",
    needed: list[str] | None = None,
    exports: list[str] | None = None,
    imports: list[str] | None = None,
) -> ElfMetadata:
    syms = [ElfSymbol(name=name, visibility="default") for name in exports or []]
    imps = [ElfImport(name=name) for name in imports or []]
    return ElfMetadata(
        soname=soname or "", needed=needed or [], symbols=syms, imports=imps
    )


def _old_metadata() -> dict[str, ElfMetadata]:
    return {
        "libcore.so": _meta(soname="libcore.so", exports=["core_mul", "core_add"]),
        "libalgo.so": _meta(
            soname="libalgo.so",
            needed=["libcore.so"],
            imports=["core_mul"],
        ),
    }


def _per_library_snapshots(metadata: dict[str, ElfMetadata]) -> dict[str, AbiSnapshot]:
    return {
        name: AbiSnapshot(library=name, version="old", elf=meta)
        for name, meta in metadata.items()
    }


class TestBundleFactsArtifactTypeDiscriminator:
    def test_artifact_type_round_trips(self) -> None:
        facts = capture_bundle_facts(_per_library_snapshots(_old_metadata()))
        d = bundle_facts_to_dict(facts)
        assert d["artifact_type"] == BUNDLE_FACTS_ARTIFACT_TYPE
        assert bundle_facts_from_dict(d).artifact_type == BUNDLE_FACTS_ARTIFACT_TYPE

    def test_artifact_type_is_not_a_constructor_parameter(self) -> None:
        # Codex review, fresh evidence: init=False makes the marker an
        # invariant a caller cannot break by construction -- a hand-built
        # BundleFacts(artifact_type="other") would otherwise write a
        # document bundle_facts_from_dict rejects (or, worse, one the
        # archive writer silently overwrites with the real marker, so the
        # two writers would disagree about what they just wrote).
        with pytest.raises(TypeError):
            BundleFacts(artifact_type="something-else")  # type: ignore[call-arg]

    def test_serialization_ignores_a_post_construction_mutation(self) -> None:
        # Codex review, fresh evidence: init=False only blocks constructor
        # injection -- the dataclass isn't frozen, so plain attribute
        # assignment after construction still works. bundle_facts_to_dict
        # must write the real constant regardless, matching how
        # write_bundle_facts_archive already writes its own marker, rather
        # than serializing a document bundle_facts_from_dict would reject.
        facts = capture_bundle_facts(_per_library_snapshots(_old_metadata()))
        facts.artifact_type = "something-else"
        d = bundle_facts_to_dict(facts)
        assert d["artifact_type"] == BUNDLE_FACTS_ARTIFACT_TYPE
        assert bundle_facts_from_dict(d).artifact_type == BUNDLE_FACTS_ARTIFACT_TYPE

    def test_rewriting_a_loaded_v1_document_stamps_the_current_schema_version(
        self,
    ) -> None:
        # Codex review, fresh evidence: bundle_facts_to_dict() always writes
        # the v2+-only artifact_type key, so a round-tripped v1 document
        # must not still declare schema_version 1 while carrying it -- that
        # combination is exactly the malformed, self-contradictory shape
        # schema_version 2's own introduction was meant to make impossible.
        facts = capture_bundle_facts(_per_library_snapshots(_old_metadata()))
        d = bundle_facts_to_dict(facts)
        del d["artifact_type"]
        d["schema_version"] = 1
        loaded = bundle_facts_from_dict(d)
        assert loaded.schema_version == 1  # preserved on the in-memory object

        rewritten = bundle_facts_to_dict(loaded)
        assert rewritten["schema_version"] == BUNDLE_FACTS_SCHEMA_VERSION
        assert rewritten["artifact_type"] == BUNDLE_FACTS_ARTIFACT_TYPE

    def test_missing_artifact_type_defaults_to_current_on_a_true_v1_document(
        self,
    ) -> None:
        # A true v1 document (schema_version 1) predates this marker
        # entirely -- it must still load, and get the current artifact_type
        # assigned.
        facts = capture_bundle_facts(_per_library_snapshots(_old_metadata()))
        d = bundle_facts_to_dict(facts)
        del d["artifact_type"]
        d["schema_version"] = 1
        assert bundle_facts_from_dict(d).artifact_type == BUNDLE_FACTS_ARTIFACT_TYPE

    def test_missing_artifact_type_accepts_a_string_encoded_v1_version(
        self,
    ) -> None:
        # Codex review, fresh evidence: the old reader normalized
        # schema_version via int(...) before comparing, so a v1 document
        # spelling it as the string "1" (still exactly what int("1") == 1
        # accepts) must keep loading, not be rejected by a raw,
        # unnormalized comparison against the missing marker.
        facts = capture_bundle_facts(_per_library_snapshots(_old_metadata()))
        d = bundle_facts_to_dict(facts)
        del d["artifact_type"]
        d["schema_version"] = "1"
        assert bundle_facts_from_dict(d).artifact_type == BUNDLE_FACTS_ARTIFACT_TYPE

    def test_missing_artifact_type_defaults_when_schema_version_is_absent_too(
        self,
    ) -> None:
        # No schema_version key at all is the same "predates the marker"
        # case as an explicit 1 -- bundle_facts_from_dict already defaults
        # a missing schema_version to the current one for other purposes,
        # but that must not by itself make a missing marker look legacy
        # any less than an explicit "1" does.
        facts = capture_bundle_facts(_per_library_snapshots(_old_metadata()))
        d = bundle_facts_to_dict(facts)
        del d["artifact_type"]
        del d["schema_version"]
        assert bundle_facts_from_dict(d).artifact_type == BUNDLE_FACTS_ARTIFACT_TYPE

    def test_missing_artifact_type_is_rejected_on_schema_version_2(self) -> None:
        # Codex review, fresh evidence: schema_version 2 is exactly where
        # the marker became mandatory -- a document declaring it but
        # omitting artifact_type is malformed, not legacy, and must not
        # silently pass through the v1 shape-fallback/default.
        facts = capture_bundle_facts(_per_library_snapshots(_old_metadata()))
        d = bundle_facts_to_dict(facts)
        assert d["schema_version"] == 2
        del d["artifact_type"]

        with pytest.raises(ValueError, match="artifact_type"):
            bundle_facts_from_dict(d)

    def test_mismatched_artifact_type_is_rejected(self) -> None:
        facts = capture_bundle_facts(_per_library_snapshots(_old_metadata()))
        d = bundle_facts_to_dict(facts)
        d["artifact_type"] = "something-else"

        with pytest.raises(ValueError, match="artifact_type"):
            bundle_facts_from_dict(d)

    def test_correct_artifact_type_is_rejected_on_schema_version_1(self) -> None:
        # Codex review, fresh evidence: even the *correct* marker is
        # self-contradictory on a document explicitly declaring
        # schema_version 1 -- artifact_type was added in schema_version 2,
        # so no genuinely-v1 document could ever carry it. No real writer
        # produces this combination; reaching it means a malformed or
        # hand-edited document.
        facts = capture_bundle_facts(_per_library_snapshots(_old_metadata()))
        d = bundle_facts_to_dict(facts)
        assert d["artifact_type"] == BUNDLE_FACTS_ARTIFACT_TYPE
        d["schema_version"] = 1

        with pytest.raises(ValueError, match="schema_version 1"):
            bundle_facts_from_dict(d)


class TestLooksLikeBundleFactsDocument:
    """Two-tier classification: an explicit ``artifact_type`` key is trusted
    outright (in both the match and mismatch directions) -- shape-based
    fallback applies only to a true v1 document (``schema_version`` absent,
    or normalizing -- via the same ``int(...)`` coercion the reader applies
    -- to exactly ``1``); a document *explicitly* declaring ``schema_version``
    2+ with no marker gets neither tier (Codex review, fresh evidence)."""

    def test_true_for_a_document_with_the_correct_marker(self) -> None:
        assert looks_like_bundle_facts_document(
            {"artifact_type": BUNDLE_FACTS_ARTIFACT_TYPE}
        )

    def test_false_for_a_wrong_marker_even_with_bundle_facts_shape(self) -> None:
        # The explicit marker is trusted outright -- a wrong marker is
        # rejected even though the rest of the document is shaped exactly
        # like real bundle facts, proving there is no shape fallback once
        # the key is present.
        assert not looks_like_bundle_facts_document(
            {
                "artifact_type": "something-else",
                "per_library_snapshots": {},
            }
        )

    def test_true_for_a_legacy_v1_document_with_no_marker_key(self) -> None:
        assert looks_like_bundle_facts_document(
            {"schema_version": 1, "per_library_snapshots": {}}
        )

    def test_false_for_an_explicit_null_schema_version(self) -> None:
        # Codex review, fresh evidence: an explicit `"schema_version": null`
        # is not the same as the key being absent -- bundle_facts_from_dict
        # would raise on int(None), so the classifier must not route it
        # through the "absent, therefore legacy" branch either.
        assert not looks_like_bundle_facts_document(
            {"schema_version": None, "per_library_snapshots": {}}
        )

    def test_true_for_a_string_encoded_v1_version(self) -> None:
        # Codex review, fresh evidence: must classify identically to the
        # bare-int-1 case above, matching bundle_facts_from_dict's own
        # int(...) normalization.
        assert looks_like_bundle_facts_document(
            {"schema_version": "1", "per_library_snapshots": {}}
        )

    def test_true_when_schema_version_is_absent_too(self) -> None:
        assert looks_like_bundle_facts_document({"per_library_snapshots": {}})

    def test_false_for_schema_version_2_with_no_marker_key(self) -> None:
        # The exact bypass Codex flagged: a document declaring the current
        # schema_version but omitting the now-mandatory marker must not
        # fall through to the v1-only shape fallback just because it also
        # happens to carry a per_library_snapshots-shaped key.
        assert not looks_like_bundle_facts_document(
            {"schema_version": 2, "per_library_snapshots": {}}
        )

    def test_false_for_a_non_dict_input(self) -> None:
        assert not looks_like_bundle_facts_document(["not", "a", "dict"])
        assert not looks_like_bundle_facts_document(None)

    def test_false_for_a_dict_with_neither_key(self) -> None:
        assert not looks_like_bundle_facts_document({"schema_version": 2})

    def test_false_for_a_non_coercible_schema_version(self) -> None:
        # A pure classifier over untrusted, already-decoded JSON must not
        # raise for a schema_version that int(...) can't parse -- it's
        # simply not eligible for the v1 shape fallback.
        assert not looks_like_bundle_facts_document(
            {"schema_version": ["not", "coercible"], "per_library_snapshots": {}}
        )
        assert not looks_like_bundle_facts_document(
            {"schema_version": "not-a-number", "per_library_snapshots": {}}
        )

    def test_false_for_an_overflowing_schema_version(self) -> None:
        # Codex review, fresh evidence: a JSON exponent like 1e999 decodes
        # to float inf (json.loads has no integer overflow limit for
        # floats), and int(inf) raises OverflowError rather than
        # TypeError/ValueError -- an exception class the classifier's
        # except clause didn't originally catch, which would crash a
        # future operand dispatcher on malformed input instead of routing
        # it to normal validation.
        assert not looks_like_bundle_facts_document(
            {"schema_version": float("inf"), "per_library_snapshots": {}}
        )
