# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""ADR-063 5B: header-fact scenarios that must go through the load path.

The detectors read each declaration's ``FactStatus``; for a stored document
that status is derived on load (``storage.fact_backfill``), so "which
evidence can this document be trusted for" is exercised by loading one.
"""

from __future__ import annotations

from abicheck.checker import ChangeKind, compare
from abicheck.dumper_hybrid import merge_snapshots
from abicheck.fact_provenance import type_fact_key
from abicheck.model import AbiSnapshot, RecordType, TypeField
from tests.test_dumper_hybrid import _snap


def _kinds(r):
    return {c.kind for c in r.changes}


class TestLegacyLoadedHeaderFacts:
    def test_legacy_bare_keyed_hybrid_baseline_still_detects_transition(self):
        """End-to-end regression for the exact scenario Codex flagged: a
        `--ast-frontend hybrid` baseline persisted BEFORE the provenance-key
        qualification fix has real provenance recorded under the former
        bare key. Comparing it against a freshly-merged snapshot must still
        detect a genuine deprecated transition, not silently suppress it."""
        from abicheck.checker import ChangeKind, compare
        from abicheck.serialization import snapshot_from_dict
        from abicheck.storage.fact_schema_versions import (
            _MIN_SCHEMA_VERSION_FOR_DEPRECATION_FACTS,
        )

        # A document persisted by the pre-fix merge code, loaded the way a
        # real baseline is (ADR-063 5B: the detector reads the fact's status,
        # so the scenario must go through the load-time correction that
        # derives it): real castxml-sourced provenance, under the bare key,
        # and a legacy `deprecated: null` with no `deprecated_fact`.
        old_legacy_hybrid = snapshot_from_dict(
            {
                "library": "libfoo.so",
                "version": "1.0",
                "schema_version": _MIN_SCHEMA_VERSION_FOR_DEPRECATION_FACTS - 1,
                "ast_producer": "hybrid",
                "from_headers": True,
                "functions": [],
                "variables": [],
                "types": [
                    {
                        "name": "Foo",
                        "qualified_name": "ns::Foo",
                        "qualified_name_fact": {
                            "status": "present",
                            "value": "ns::Foo",
                        },
                        "kind": "class",
                        "deprecated": None,
                    }
                ],
                "enums": [],
                "typedefs": {},
                "fact_provenance": {type_fact_key("Foo", "deprecated"): "castxml"},
            }
        )

        new_foo = RecordType(
            name="Foo", qualified_name="ns::Foo", kind="class", deprecated="use Bar"
        )
        new_merged = merge_snapshots(
            _snap(types=[new_foo], ast_producer="castxml"),
            _snap(ast_producer="clang"),
        )

        result = compare(old_legacy_hybrid, new_merged)
        assert ChangeKind.TYPE_DEPRECATED_ADDED in {c.kind for c in result.changes}

    def test_gated_on_a_genuinely_unknown_producer(self):
        """The real remaining false-positive-avoidance case: an
        ast_producer that isn't a confirmed backend at all (e.g. a legacy
        pre-provenance baseline) must still decline to compare."""
        t_old = RecordType(
            name="Cfg",
            kind="struct",
            size_bits=32,
            fields=[TypeField("count", "int", 0, deprecated="msg")],
        )
        t_new = RecordType(
            name="Cfg",
            kind="struct",
            size_bits=32,
            fields=[TypeField("count", "int", 0, deprecated=None)],
        )
        old = AbiSnapshot(
            library="libtest.so.1",
            version="1.0",
            types=[t_old],
            from_headers=True,
            ast_producer="castxml",
        )
        new = AbiSnapshot(
            library="libtest.so.1",
            version="2.0",
            types=[t_new],
            from_headers=True,
            ast_producer="some_future_tool",
        )
        # ADR-063 5B: an unrecognized producer is corrected on load
        # (storage.fact_backfill), so the scenario goes through the load.
        from tests._legacy_snapshot_doc import load_as_legacy

        r = compare(load_as_legacy(old), load_as_legacy(new))
        assert ChangeKind.FIELD_DEPRECATED_REMOVED not in _kinds(r)
