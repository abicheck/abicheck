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
