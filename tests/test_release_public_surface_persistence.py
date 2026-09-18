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

"""How a release's one public contract persists, and refuses to half-persist.

The value round-trip, the ``BundleFacts`` schema-version discipline, and the
second (archive) container. One subject: a stored product baseline must
either carry the contract its members were reconciled against or refuse to
open -- never quietly drop it, which would leave those members reloaded
against no contract at all and bring back the per-member Cartesian product
on a *stored* comparison.

Split out of ``tests/test_release_public_surface.py`` (a distinct subject,
and that file reached the 1200-line test maximum).
"""

from __future__ import annotations

import json

import pytest

from abicheck.model.release_surface import (
    PublicObligation,
    ReleasePublicSurface,
    unresolved_surface,
)


def _surface(*symbols: str, side: str = "new", key: str = "k") -> ReleasePublicSurface:
    return ReleasePublicSurface(
        acquisition_key=key,
        side=side,
        obligations=tuple(
            PublicObligation(symbol=s, name=s, entity="function") for s in symbols
        ),
        declared_symbols=frozenset(symbols),
    )


class TestSurfaceRoundTrip:
    """The surface survives persistence unchanged (scenario 15's value half)."""

    def test_to_dict_from_dict_is_lossless(self) -> None:
        surface = ReleasePublicSurface(
            acquisition_key="key",
            side="old",
            obligations=(
                PublicObligation("s1", "n1", "function", "h.h:3"),
                PublicObligation("s2", "n2", "variable"),
            ),
            declared_symbols=frozenset({"s1", "s2", "s3"}),
            header_count=2,
            type_names=("Cfg",),
        )
        assert ReleasePublicSurface.from_dict(surface.to_dict()) == surface

    def test_an_unresolved_surface_round_trips_with_its_reason(self) -> None:
        surface = unresolved_surface(
            acquisition_key="k", side="new", reason="acquisition failed: boom"
        )
        back = ReleasePublicSurface.from_dict(surface.to_dict())
        assert back.resolvable is False
        assert back.unresolved_reason == "acquisition failed: boom"

    def test_a_malformed_obligation_list_does_not_crash_a_reader(self) -> None:
        back = ReleasePublicSurface.from_dict(
            {"acquisition_key": "k", "side": "new", "obligations": ["not a mapping"]}
        )
        assert back.obligations == ()


class TestBundleFactsSchema:
    """Schema-version discipline and prior-schema loading (scenario 15)."""

    @staticmethod
    def _facts(surface: ReleasePublicSurface | None):
        from abicheck.model.bundle_facts import BundleFacts

        return BundleFacts(per_library_snapshots={}, public_surface=surface)

    def test_a_document_without_a_surface_keeps_its_old_version(self) -> None:
        from abicheck.storage.bundle_facts_codec import bundle_facts_to_dict

        doc = bundle_facts_to_dict(self._facts(None))
        assert doc["schema_version"] == 2
        assert "public_surface" not in doc

    def test_a_document_with_a_surface_declares_version_4(self) -> None:
        from abicheck.storage.bundle_facts_codec import bundle_facts_to_dict

        doc = bundle_facts_to_dict(self._facts(_surface("api_a", side="old")))
        assert doc["schema_version"] == 4

    def test_the_document_round_trips(self) -> None:
        from abicheck.storage.bundle_facts_codec import (
            bundle_facts_from_dict,
            bundle_facts_to_dict,
        )

        doc = bundle_facts_to_dict(self._facts(_surface("api_a", side="old")))
        assert bundle_facts_to_dict(bundle_facts_from_dict(doc)) == doc

    def test_a_prior_schema_document_still_loads(self) -> None:
        from abicheck.storage.bundle_facts_codec import bundle_facts_from_dict

        facts = bundle_facts_from_dict(
            {
                "artifact_type": "abicheck.bundle-facts",
                "schema_version": 2,
                "per_library_snapshots": {},
            }
        )
        assert facts.public_surface is None

    def test_a_surface_under_a_prior_version_is_refused(self) -> None:
        """No silent reinterpretation: a reader that cannot honor the block
        must refuse the document rather than drop the product contract and
        reconcile the members against nothing."""
        from abicheck.storage.bundle_facts_codec import bundle_facts_from_dict

        with pytest.raises(ValueError, match="public_surface"):
            bundle_facts_from_dict(
                {
                    "artifact_type": "abicheck.bundle-facts",
                    "schema_version": 3,
                    "per_library_snapshots": {},
                    "public_surface": {"acquisition_key": "k", "side": "old"},
                }
            )

    def test_the_project_snapshot_importer_refuses_a_v4_document(self) -> None:
        """Fail closed, don't drop the contract. The ``ProjectSnapshot``
        import adapter has no composition section for ``public_surface``, so
        it refuses a schema-4 document rather than importing one whose
        recorded contract it would silently discard -- which would leave the
        imported members reconciled against nothing."""
        from abicheck.errors import IncompatibleSnapshotSchemaError
        from abicheck.model.bundle_facts import PUBLIC_SURFACE_SCHEMA_VERSION
        from abicheck.storage.import_bundle_facts import (
            _BUNDLE_FACTS_SCHEMA_VERSION,
        )

        assert _BUNDLE_FACTS_SCHEMA_VERSION < PUBLIC_SURFACE_SCHEMA_VERSION
        from abicheck.serialization import SCHEMA_VERSION
        from abicheck.storage import InMemoryObjectStore
        from abicheck.storage.import_bundle_facts import import_bundle_facts

        with pytest.raises(IncompatibleSnapshotSchemaError, match="newer than"):
            import_bundle_facts(
                {
                    "artifact_type": "abicheck.bundle-facts",
                    "schema_version": PUBLIC_SURFACE_SCHEMA_VERSION,
                    "per_library_snapshots": {},
                    "public_surface": {"acquisition_key": "k", "side": "old"},
                },
                store=InMemoryObjectStore(),
                max_known_schema_version=SCHEMA_VERSION,
            )

    def test_a_wrong_shaped_surface_is_refused(self) -> None:
        from abicheck.storage.bundle_facts_codec import bundle_facts_from_dict

        with pytest.raises(ValueError, match="must be a mapping"):
            bundle_facts_from_dict(
                {
                    "artifact_type": "abicheck.bundle-facts",
                    "schema_version": 4,
                    "per_library_snapshots": {},
                    "public_surface": ["nope"],
                }
            )


class TestTheArchiveContainerCarriesTheContractToo:
    """`save_bundle_facts(format="archive")` is a second container, and a
    document that declares schema 4 must really carry the block -- otherwise
    it claims a contract it silently dropped, and its members reload
    reconciled against nothing."""

    def _facts(self, surface):
        from abicheck.model.bundle_facts import BundleFacts

        return BundleFacts(per_library_snapshots={}, public_surface=surface)

    def test_the_surface_round_trips_through_the_archive(self, tmp_path) -> None:
        from abicheck.serialization import load_bundle_facts, save_bundle_facts

        surface = ReleasePublicSurface(
            acquisition_key="key",
            side="old",
            obligations=(PublicObligation("api_a", "api_a", "function"),),
            declared_symbols=frozenset({"api_a"}),
        )
        path = tmp_path / "facts.zip"
        save_bundle_facts(self._facts(surface), path, format="archive")
        back = load_bundle_facts(path)
        assert back.public_surface is not None
        assert back.public_surface.acquisition_key == "key"
        assert [o.symbol for o in back.public_surface.obligations] == ["api_a"]

    def test_an_archive_without_a_surface_stays_at_its_own_version(
        self, tmp_path
    ) -> None:
        from abicheck.serialization import load_bundle_facts, save_bundle_facts

        path = tmp_path / "facts.zip"
        save_bundle_facts(self._facts(None), path, format="archive")
        assert load_bundle_facts(path).public_surface is None


class TestTheManifestDoesNotGrowWithTheContract:
    """The archive's `manifest.json` is capped (`DEFAULT_MAX_MANIFEST_BYTES`,
    64 MiB) and the writer raises rather than producing an archive that
    exceeds it. The public surface grows with the *product* -- one record per
    obligation plus every declared symbol and type name -- so writing it
    inline made a large release unable to write a bundle-facts archive at
    all, which is precisely the scale this block exists to serve.

    Asserted as a *ratio*, not against the real 64 MiB ceiling: a fixture
    large enough to breach that cap would be a 64 MiB test. The invariant is
    stronger than the threshold anyway -- the manifest must be O(1) in the
    surface's size, so no surface of any size can breach it.
    """

    def _facts(self, surface):
        from abicheck.model.bundle_facts import BundleFacts

        return BundleFacts(per_library_snapshots={}, public_surface=surface)

    def _manifest(self, tmp_path, surface, name: str):
        import json
        import zipfile

        from abicheck.serialization import save_bundle_facts

        path = tmp_path / name
        save_bundle_facts(self._facts(surface), path, format="archive")
        with zipfile.ZipFile(path) as zf:
            return json.loads(zf.read("manifest.json")), path

    def _big(self, count: int):
        symbols = tuple(f"api_{i:06d}" for i in range(count))
        return ReleasePublicSurface(
            acquisition_key="key",
            side="new",
            obligations=tuple(
                PublicObligation(symbol=s, name=s, entity="function") for s in symbols
            ),
            declared_symbols=frozenset(symbols),
            type_names=tuple(f"Type_{i:06d}" for i in range(count)),
            resolvable=True,
        )

    def test_the_manifest_stays_flat_as_the_surface_grows(self, tmp_path) -> None:
        import json

        small, _ = self._manifest(tmp_path, self._big(10), "small.zip")
        large, _ = self._manifest(tmp_path, self._big(4000), "large.zip")
        small_bytes = len(json.dumps(small))
        large_bytes = len(json.dumps(large))
        # 400x the surface. Inline, the manifest grew with it; by hash it
        # does not grow at all beyond the fixed-width digest.
        assert large_bytes - small_bytes < 64, (small_bytes, large_bytes)

    def test_the_manifest_carries_a_hash_not_the_payload(self, tmp_path) -> None:
        """The structural half: a reviewer reading the manifest must not
        find the contract's contents there, whatever its size."""
        manifest, _ = self._manifest(tmp_path, self._big(50), "m.zip")
        assert "public_surface" not in manifest
        blob = manifest["public_surface_blob"]
        assert isinstance(blob, str) and blob
        assert "api_000001" not in json.dumps(manifest)

    def test_a_large_surface_still_round_trips(self, tmp_path) -> None:
        """The vacuity guard on both assertions above: a writer that simply
        dropped the surface would pass them and lose the contract."""
        from abicheck.serialization import load_bundle_facts, save_bundle_facts

        surface = self._big(4000)
        path = tmp_path / "rt.zip"
        save_bundle_facts(self._facts(surface), path, format="archive")
        back = load_bundle_facts(path).public_surface
        assert back is not None
        assert len(back.obligations) == 4000
        assert back.declared_symbols == surface.declared_symbols
        assert back.type_names == surface.type_names

    def test_an_archive_with_no_surface_names_no_blob(self, tmp_path) -> None:
        manifest, _ = self._manifest(tmp_path, None, "none.zip")
        assert "public_surface_blob" not in manifest
        assert "public_surface" not in manifest

    @pytest.mark.parametrize("bad", [17, ["a"], {"h": 1}])
    def test_a_non_string_blob_marker_is_refused(self, tmp_path, bad) -> None:
        """An unvalidated non-string reaches the hash-keyed blob cache and
        raises a raw `TypeError` instead of this module's own error."""
        import json
        import zipfile

        from abicheck.serialization import load_bundle_facts, save_bundle_facts

        path = tmp_path / "bad.zip"
        save_bundle_facts(self._facts(self._big(3)), path, format="archive")
        with zipfile.ZipFile(path) as zf:
            members = {n: zf.read(n) for n in zf.namelist()}
        manifest = json.loads(members["manifest.json"])
        manifest["public_surface_blob"] = bad
        members["manifest.json"] = json.dumps(manifest).encode()
        with zipfile.ZipFile(path, "w") as zf:
            for name, payload in members.items():
                zf.writestr(name, payload)
        with pytest.raises((ValueError, Exception)) as excinfo:
            load_bundle_facts(path)
        assert "public_surface_blob" in str(excinfo.value)


class TestNoReaderDiscardsTheContractSilently:
    """A default standing in for missing evidence is what these gates
    refuse (`storage/AGENTS.md`). The declared-version refusal only catches
    a document that *says* v4; one omitting `schema_version` defaults to the
    reader's own current version and slips past -- and the import adapter has
    no composition section for the block, so the contract would be dropped
    and the import would still succeed.
    """

    def _document(self, *, with_version: bool):
        doc = {
            "artifact_type": "abicheck.bundle-facts",
            "variant_fingerprint": "default",
            "per_library_snapshots": {},
            "public_surface": _surface("api_a", side="old").to_dict(),
        }
        if with_version:
            doc["schema_version"] = 4
        return doc

    def _import(self, doc, tmp_path):
        from abicheck.serialization import SCHEMA_VERSION
        from abicheck.storage import InMemoryObjectStore
        from abicheck.storage.import_bundle_facts import import_bundle_facts

        return import_bundle_facts(
            doc,
            store=InMemoryObjectStore(),
            max_known_schema_version=SCHEMA_VERSION,
        )

    def test_a_schema_less_document_carrying_a_contract_is_refused(
        self, tmp_path
    ) -> None:
        with pytest.raises((ValueError, Exception)) as excinfo:
            self._import(self._document(with_version=False), tmp_path)
        assert "public_surface" in str(excinfo.value)

    def test_a_declared_v4_document_is_refused_too(self, tmp_path) -> None:
        """The pre-existing path, asserted beside it: both spellings of the
        same document reach a refusal, not one of them."""
        with pytest.raises((ValueError, Exception)):
            self._import(self._document(with_version=True), tmp_path)

    def test_a_document_with_no_contract_still_imports(self, tmp_path) -> None:
        """The vacuity guard: the gate must refuse the block, not the
        document shape -- otherwise every ordinary import breaks."""
        doc = self._document(with_version=False)
        del doc["public_surface"]
        assert self._import(doc, tmp_path) is not None


class TestACaptureStampsTheVersionItsContentsNeed:
    """Each block names the version it needs. One "current" constant stamped
    4 on a degraded-only capture (`degraded_members` needs only 3) and left
    2 on a capture that really does carry a public surface.
    """

    def _capture(self, **kwargs):
        from abicheck.workflows.bundle_facts_capture import capture_bundle_facts

        return capture_bundle_facts(per_library_snapshots={}, **kwargs)

    def test_a_plain_capture_stays_at_the_base_version(self) -> None:
        assert self._capture().schema_version == 2

    def test_a_public_surface_capture_declares_four(self) -> None:
        facts = self._capture(public_surface=_surface("api_a"))
        assert facts.schema_version == 4

    def test_a_surface_capture_declares_four_with_degraded_members_too(self) -> None:
        """The higher of the two, not whichever branch is checked first."""
        facts = self._capture(public_surface=_surface("api_a"), degraded_members={})
        assert facts.schema_version == 4

    def test_the_in_memory_version_matches_what_is_persisted(self, tmp_path) -> None:
        """The invariant behind all three: the object a caller holds and the
        document written from it declare the same version, so a reader and
        an in-process consumer cannot disagree about what it carries."""
        from abicheck.model.bundle_facts import document_schema_version

        for facts in (
            self._capture(),
            self._capture(public_surface=_surface("api_a")),
        ):
            assert facts.schema_version == document_schema_version(facts)
