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

"""How the release public-surface section renders and serializes.

Its sibling ``tests/test_release_public_surface.py`` owns what the section
*states*; this file owns the branches that decide how it is written down --
the optional keys each ``to_dict`` omits rather than nulls, and the Markdown
renderer's per-shape paths (an unresolved side, a missing-export list,
coverage warnings, a folded product-level finding).

Split into its own module for the ordinary reason: that file is at the
1200-line test maximum. But the subject is a real one, not a leftover. These
are the paths where a wrong branch produces a *wrong document* rather than a
crash -- a side reported as resolvable when it is not, a coverage gap that
renders as silence -- and they were reached only through the `integration`
lane's real-toolchain runs, which the coverage lane does not run. That is
exactly the shape of gap this repository's `codecov/patch` gate exists to
catch, and it caught it here.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from abicheck.compare.bundle_export_index import build_bundle_export_index
from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.model.release_surface import (
    PublicObligation,
    ReleasePublicSurface,
    unresolved_surface,
)
from abicheck.policy.release_contract_reconciliation import (
    reconcile_release,
    reconcile_side,
)
from abicheck.report.release_public_surface import (
    SharedFinding,
    compute_release_public_surface,
    render_release_public_surface_markdown,
)


@dataclass
class _Member:
    elf: ElfMetadata | None


def _member(*exports: str) -> _Member:
    return _Member(
        elf=ElfMetadata(
            soname="",
            needed=[],
            symbols=[ElfSymbol(name=n, is_default=True) for n in exports],
            imports=[],
        )
    )


def _surface(*symbols: str, side: str = "new") -> ReleasePublicSurface:
    return ReleasePublicSurface(
        acquisition_key="k",
        side=side,
        obligations=tuple(
            PublicObligation(symbol=s, name=s, entity="function") for s in symbols
        ),
        declared_symbols=frozenset(symbols),
    )


class TestOptionalKeysAreOmittedNotNulled:
    """Every optional key follows the repository's additive convention: a
    key that states nothing is absent, never `null` -- so a consumer can
    feature-detect it instead of distinguishing "absent" from "present and
    null"."""

    def test_a_shared_finding_omits_the_values_it_has_none_of(self) -> None:
        bare = SharedFinding(
            kind="type_size_changed",
            symbol="Cfg",
            old_value=None,
            new_value=None,
            description="grew",
            source_location=None,
            affected_libraries=("libA.so",),
        ).to_dict()
        assert set(bare) == {
            "kind",
            "symbol",
            "description",
            "affected_libraries",
        }

    def test_a_shared_finding_carries_the_values_it_has(self) -> None:
        full = SharedFinding(
            kind="type_size_changed",
            symbol="Cfg",
            old_value="32",
            new_value="64",
            description="grew",
            source_location="product.h:2",
            affected_libraries=("libA.so", "libB.so"),
        ).to_dict()
        assert full["old_value"] == "32"
        assert full["new_value"] == "64"
        assert full["source_location"] == "product.h:2"

    def test_a_complete_export_index_omits_the_failure_keys(self) -> None:
        index = build_bundle_export_index("new", {"libA.so": _member("api_a")})
        record = index.to_dict()
        assert record["complete"] is True
        assert "members_without_exports" not in record
        assert "failed_members" not in record

    def test_an_incomplete_index_names_both_kinds_of_gap(self) -> None:
        index = build_bundle_export_index(
            "new",
            {"libA.so": _member("api_a"), "libB.so": _Member(elf=None)},
            failed_members={"libC.so": "dump failed"},
        )
        record = index.to_dict()
        assert record["complete"] is False
        assert record["members_without_exports"] == ["libB.so"]
        assert record["failed_members"] == {"libC.so": "dump failed"}
        reason = index.incompleteness_reason() or ""
        assert "libC.so" in reason
        assert "libB.so" in reason

    def test_the_index_never_lists_every_symbol(self) -> None:
        """A real product carries hundreds of thousands of exports; the
        summary states counts, which is what keeps the document off the
        O(surface x members) curve."""
        index = build_bundle_export_index(
            "new", {"libA.so": _member("api_a", "api_b", "api_c")}
        )
        record = index.to_dict()
        assert record["exported_symbols"] == 3
        assert "api_a" not in repr(record)

    def test_a_reconciliation_without_a_baseline_omits_the_old_side(self) -> None:
        result = reconcile_release(
            _surface("api_a"),
            build_bundle_export_index("new", {"libA.so": _member("api_a")}),
        )
        record = result.to_dict()
        assert "old" not in record
        assert "coverage_warnings" not in record
        assert record["new"]["side"] == "new"

    def test_a_two_sided_reconciliation_records_both(self) -> None:
        result = reconcile_release(
            _surface("api_a"),
            build_bundle_export_index("new", {"libA.so": _member("api_a")}),
            old_surface=_surface("api_a", side="old"),
            old_index=build_bundle_export_index("old", {"libA.so": _member("api_a")}),
        )
        record = result.to_dict()
        assert record["old"]["side"] == "old"
        assert record["new"]["side"] == "new"

    def test_an_incomplete_side_states_its_reason(self) -> None:
        record = reconcile_side(
            _surface("api_a", "api_b"),
            build_bundle_export_index(
                "new",
                {"libA.so": _member("api_a")},
                failed_members={"libB.so": "boom"},
            ),
        ).to_dict()
        assert record["coverage_complete"] is False
        assert "boom" in str(record["coverage_reason"])

    def test_a_complete_side_states_no_reason(self) -> None:
        record = reconcile_side(
            _surface("api_a"),
            build_bundle_export_index("new", {"libA.so": _member("api_a")}),
        ).to_dict()
        assert record["coverage_complete"] is True
        assert "coverage_reason" not in record

    def test_an_unresolvable_surface_reconciles_to_a_stated_record(self) -> None:
        """Not an exception and not a clean zero: the side is recorded as
        unresolvable, with its reason, and judges nothing."""
        record = reconcile_side(
            unresolved_surface(acquisition_key="k", side="new", reason="no headers"),
            build_bundle_export_index("new", {"libA.so": _member("api_a")}),
        ).to_dict()
        assert record["surface_resolvable"] is False
        assert record["public_declarations_with_export_obligation"] == 0
        assert record["missing_from_bundle"] == []
        assert record["coverage_reason"] == "no headers"


class TestTheMarkdownRender:
    """The renderer formats and decides nothing, so each shape it can be
    handed must come out saying the same thing the struct does."""

    def _terms(self, **kw):
        return compute_release_public_surface(**kw)

    def test_a_missing_export_is_listed_with_its_evolution_state(self) -> None:
        result = reconcile_release(
            _surface("api_a", "gone"),
            build_bundle_export_index("new", {"libA.so": _member("api_a")}),
            old_surface=_surface("api_a", "gone", side="old"),
            old_index=build_bundle_export_index(
                "old", {"libA.so": _member("api_a", "gone")}
            ),
        )
        text = render_release_public_surface_markdown(
            self._terms(reconciliation=result)
        )
        assert "Declarations no bundle member exports" in text
        assert "`gone`" in text
        assert "(introduced)" in text

    def test_an_unresolved_side_says_so_instead_of_reporting_zeros(self) -> None:
        result = reconcile_release(
            unresolved_surface(acquisition_key="k", side="new", reason="no headers"),
            build_bundle_export_index("new", {"libA.so": _member("api_a")}),
        )
        text = render_release_public_surface_markdown(
            self._terms(reconciliation=result)
        )
        assert "public surface unresolved" in text
        assert "no headers" in text

    def test_a_coverage_gap_is_rendered_not_swallowed(self) -> None:
        result = reconcile_release(
            _surface("api_a", "api_b"),
            build_bundle_export_index(
                "new",
                {"libA.so": _member("api_a")},
                failed_members={"libB.so": "dump failed"},
            ),
            old_surface=_surface("api_a", "api_b", side="old"),
            old_index=build_bundle_export_index(
                "old", {"libA.so": _member("api_a"), "libB.so": _member("api_b")}
            ),
        )
        text = render_release_public_surface_markdown(
            self._terms(reconciliation=result)
        )
        assert "### Coverage" in text
        assert "incomplete" in text

    def test_a_folded_finding_names_every_affected_library(self) -> None:
        text = render_release_public_surface_markdown(
            self._terms(
                reconciliation=reconcile_release(
                    _surface("api_a"),
                    build_bundle_export_index("new", {"libA.so": _member("api_a")}),
                ),
                shared_findings=(
                    SharedFinding(
                        kind="type_size_changed",
                        symbol="Cfg",
                        old_value=None,
                        new_value=None,
                        description="Size changed: Cfg",
                        source_location=None,
                        affected_libraries=("libA.so", "libB.so"),
                    ),
                ),
            )
        )
        assert "Product-level findings (reported once, not per library)" in text
        assert "affects: libA.so, libB.so" in text

    def test_a_fold_with_no_reconciliation_still_renders(self) -> None:
        """The fold runs even when the surface stage did not, so the section
        must render from shared findings alone."""
        text = render_release_public_surface_markdown(
            self._terms(
                reconciliation=None,
                shared_findings=(
                    SharedFinding(
                        kind="type_size_changed",
                        symbol="Cfg",
                        old_value=None,
                        new_value=None,
                        description="Size changed: Cfg",
                        source_location=None,
                        affected_libraries=("libA.so", "libB.so"),
                    ),
                ),
            )
        )
        assert "Cfg" in text

    def test_nothing_to_state_renders_nothing(self) -> None:
        assert (
            render_release_public_surface_markdown(self._terms(reconciliation=None))
            == ""
        )

    def test_the_acquisition_count_is_stated(self) -> None:
        text = render_release_public_surface_markdown(
            self._terms(
                reconciliation=reconcile_release(
                    _surface("api_a"),
                    build_bundle_export_index("new", {"libA.so": _member("api_a")}),
                ),
                acquisition={"acquisitions": 2, "reuses": 1, "keys": []},
            )
        )
        assert "Header acquisitions: 2 (reused 1 time(s))." in text


class TestTheSurfaceValueRoundTrip:
    """`ReleasePublicSurface.from_dict` reads a persisted baseline, so it
    must tolerate the shapes a stored document can actually hold."""

    @pytest.mark.parametrize("value", ["not-a-number", None, {}, []])
    def test_a_malformed_header_count_reads_as_zero(self, value: object) -> None:
        back = ReleasePublicSurface.from_dict(
            {"acquisition_key": "k", "side": "new", "header_count": value}
        )
        assert back.header_count == 0

    def test_a_real_header_count_survives(self) -> None:
        back = ReleasePublicSurface.from_dict(
            {"acquisition_key": "k", "side": "new", "header_count": 3}
        )
        assert back.header_count == 3

    @pytest.mark.parametrize("value", ["oops", 7, None])
    def test_a_malformed_symbol_list_reads_as_empty(self, value: object) -> None:
        """A string is iterable, so a bare string must not decompose into
        characters -- the one shape that would silently produce a surface of
        single-letter symbols."""
        back = ReleasePublicSurface.from_dict(
            {"acquisition_key": "k", "side": "new", "declared_symbols": value}
        )
        assert back.declared_symbols == frozenset()

    def test_obligation_symbols_are_reachable_as_a_set(self) -> None:
        assert _surface("api_a", "api_b").obligation_symbols() == frozenset(
            {"api_a", "api_b"}
        )

    def test_an_identity_knows_whether_it_names_headers(self) -> None:
        from abicheck.model.release_surface import SurfaceAcquisitionIdentity

        assert SurfaceAcquisitionIdentity().has_headers() is False
        assert SurfaceAcquisitionIdentity(header_dirs=("/inc",)).has_headers() is True
        assert (
            SurfaceAcquisitionIdentity(header_files=("/inc/a.h",)).has_headers() is True
        )
