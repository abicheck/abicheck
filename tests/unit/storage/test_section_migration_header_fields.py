# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""A section migration keeps the ADR-062 D14 header fields.

``migrate_section_dto`` rebuilds a section at the current version. Its header
carriers -- ``must_understand`` (a name that blocks analyses this build does not
understand) and ``extensions`` (optional values that round-trip unchanged) --
belong to the stored section, not to its payload, so a migration must keep them.
Bug class ``storage.migration_drops_header_fields``.
"""

from __future__ import annotations

from abicheck.storage.dto import (
    BINARY_SECTION_KIND,
    SECTION_SCHEMA_VERSIONS,
    SectionDTO,
    migrate_section_dto,
)


def test_a_v1_binary_migration_keeps_must_understand_and_extensions() -> None:
    stored = SectionDTO(
        section_kind=BINARY_SECTION_KIND,
        section_schema_version=1,
        payload={"exports": []},
        must_understand=("vendor.required",),
        extensions={"vendor.fact": 7},
    )
    migrated = migrate_section_dto(stored)
    assert (
        migrated.section_schema_version == SECTION_SCHEMA_VERSIONS[BINARY_SECTION_KIND]
    )
    assert migrated.must_understand == ("vendor.required",)
    assert dict(migrated.extensions) == {"vendor.fact": 7}


def test_a_current_section_is_returned_unchanged() -> None:
    current = SectionDTO(
        section_kind=BINARY_SECTION_KIND,
        section_schema_version=SECTION_SCHEMA_VERSIONS[BINARY_SECTION_KIND],
        payload={"exports": []},
        must_understand=("vendor.required",),
    )
    assert migrate_section_dto(current) is current
